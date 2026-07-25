"""
GovStack Scheduler BB — authentication and permission classes.

GovStack Scheduler BB-to-BB authentication uses query parameters rather
than headers. Every call from a GovStack Building Block carries:
  - requestor_id   : identifies the requesting BB or actor
  - request_token  : authenticates the requestor (validated against GovStackRegisteredBB)

This contrasts with the Payments BB pattern (X-Registering-Institution-ID header).

Auth classes in this module are used on all /govstack/scheduler/ endpoints.
They are SEPARATE from CivicOS citizen auth (JWT / session / allauth).

Operating modes:

  GOVSTACK_SCHEDULER_REQUIRE_TOKEN=False (default — harness / development):
    Any non-empty requestor_id + request_token passes authentication.
    The GovStackRegisteredBB table is NOT consulted.
    Use this mode when running the GovStack certification harness before
    seeding the whitelist table.

  GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True (production):
    request_token must match an active GovStackRegisteredBB row (bb_id=request_token).
    requestor_id must be non-empty and at most 100 chars.
    Set this in production.py alongside GOVSTACK_REQUIRE_REGISTERED_BB.

  If both query parameters are absent, GovStackSchedulerAuth returns None so
  that DRF falls through to other authentication backends (e.g. simplejwt for
  admin access). This preserves the standard CivicOS auth stack for non-BB callers.

Permission classes:

  GovStackSchedulerPermission  — base gate; requires request.auth == "govstack_scheduler"
  GovStackSchedulerRolePermission — enforces minimum actor role per endpoint

Actor roles (set on request.META["_gs_actor_role"] by the view layer):
  "admin"      — entities, resources, affiliations, all logs
  "organizer"  — events, alert schedules, messages, appointments
  "resource"   — own availability queries
  "subscriber" — own appointments only

Role hierarchy (lowest to highest privilege):
  subscriber → resource → organizer → admin
"""
from __future__ import annotations

import logging

from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

# Role ordering — higher index means more privilege.
_ROLE_ORDER: list[str] = ["subscriber", "resource", "organizer", "admin"]


def _role_rank(role: str) -> int:
    """Return the numeric rank of a GovStack actor role (higher = more privilege)."""
    try:
        return _ROLE_ORDER.index(role)
    except ValueError:
        return -1


class GovStackSchedulerAuth(BaseAuthentication):
    """
    DRF authentication class for GovStack Scheduler BB endpoints.

    Reads requestor_id and request_token from request.query_params.
    Validates request_token against the GovStackRegisteredBB whitelist
    when GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True (production mode).

    Two operating modes, controlled by the GOVSTACK_SCHEDULER_REQUIRE_TOKEN
    Django setting (mirrors the GOVSTACK_REQUIRE_REGISTERED_BB pattern):

      GOVSTACK_SCHEDULER_REQUIRE_TOKEN=False (default in tests / harness):
        Query param presence is sufficient — any non-empty requestor_id and
        request_token pass authentication without a DB lookup.

      GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True (production default via production.py):
        DB lookup — request_token must match a GovStackRegisteredBB row with
        is_active=True.  Run seed_govstack_vouchers first to create the
        harness row (bb_id="GS-HARNESS") before enabling this mode.

    Return value on success: (None, "govstack_scheduler")
      - user is None because GovStack BB-to-BB calls are not tied to a Django user;
        the actor is identified by requestor_id only.
      - auth string "govstack_scheduler" allows GovStackSchedulerPermission to gate
        access to GovStack endpoints.

    Returns None (falls through) when both params are absent, allowing other DRF
    authentication backends (simplejwt) to handle non-BB callers.

    Raises AuthenticationFailed when:
      - Only one of the two params is present (partial credential)
      - requestor_id exceeds 100 characters (in any mode)
      - Token is present but fails whitelist validation (production mode only)
    """

    def authenticate(self, request: Request):
        requestor_id: str = request.query_params.get("requestor_id", "").strip()
        request_token: str = request.query_params.get("request_token", "").strip()

        # Both params absent → fall through to next authentication backend.
        if not requestor_id and not request_token:
            return None

        # Partial credentials — one present, one missing.
        if not requestor_id:
            raise AuthenticationFailed(
                "requestor_id query parameter is required when request_token is supplied."
            )
        if not request_token:
            raise AuthenticationFailed(
                "request_token query parameter is required when requestor_id is supplied."
            )

        # requestor_id length guard — enforced in all modes.
        if len(requestor_id) > 100:
            logger.debug(
                "govstack_scheduler_auth: requestor_id too long (%d chars) path=%s",
                len(requestor_id),
                request.path,
            )
            raise AuthenticationFailed("requestor_id must be 100 characters or fewer.")

        # Mode switch — same pattern as IsTrustedSourceBB / GOVSTACK_REQUIRE_REGISTERED_BB.
        require_token = getattr(settings, "GOVSTACK_SCHEDULER_REQUIRE_TOKEN", False)

        if require_token:
            # Production mode: validate request_token against the GovStackRegisteredBB table.
            # Lazy import avoids circular import issues at module load time and keeps
            # this file importable before the app registry is fully initialised.
            from apps.payments.govstack_models import GovStackRegisteredBB  # noqa: PLC0415

            if not GovStackRegisteredBB.objects.filter(
                bb_id=request_token,
                is_active=True,
            ).exists():
                logger.warning(
                    "govstack_scheduler_auth: request_token=[REDACTED] not in whitelist "
                    "or inactive requestor_id=%r path=%s",
                    requestor_id,
                    request.path,
                )
                raise AuthenticationFailed(
                    "request_token is invalid or the requesting BB is not registered."
                )

        # Store actor identity on the request for downstream permission checks and logging.
        # Using META avoids mutating the DRF request object's public attributes.
        # NOTE: request_token is intentionally NOT stored — it is a credential and must
        # not appear in debug pages, middleware logs, or the DRF request inspector.
        request.META["_gs_requestor_id"] = requestor_id

        logger.debug(
            "govstack_scheduler_auth: authenticated requestor_id=%r path=%s require_token=%s",
            requestor_id,
            request.path,
            require_token,
        )

        # user=None (no Django user linked to BB-to-BB calls), auth="govstack_scheduler"
        return (None, "govstack_scheduler")

    def authenticate_header(self, request: Request) -> str:
        """
        Return WWW-Authenticate header value for unauthenticated 401 responses.

        GovStack Scheduler uses query params, not the Authorization header.
        This string is informational only — the spec expects 401 with a clear
        message rather than a specific WWW-Authenticate scheme.
        """
        return 'GovStackScheduler realm="requestor_id+request_token query params"'


class GovStackSchedulerPermission(BasePermission):
    """
    Base permission gate for all GovStack Scheduler BB endpoints.

    Grants access if and only if the request was authenticated by
    GovStackSchedulerAuth (i.e. request.auth == "govstack_scheduler").

    Apply this to all views under /govstack/scheduler/ to ensure that
    GovStack-specific endpoints are unreachable by standard CivicOS citizen
    JWT tokens or unauthenticated requests.

    Usage:
        permission_classes = [GovStackSchedulerPermission]

    Note:
      Wave A stub views use the @api_view decorator with no permission_classes,
      so the stubs return 501 to both authenticated and unauthenticated callers.
      Concrete Wave B–G views must set this permission class explicitly.
    """

    message = "GovStack Scheduler authentication required (requestor_id + request_token)."

    def has_permission(self, request: Request, view: APIView) -> bool:
        return request.auth == "govstack_scheduler"


class GovStackSchedulerRolePermission(BasePermission):
    """
    Role-based permission helper for GovStack Scheduler BB endpoints.

    Reads the actor role from request.META.get("_gs_actor_role"), which is
    expected to be set by the view or an upstream layer after resolving the
    requestor_id to a known actor type.

    Subclass and override min_role to enforce a minimum role level:

        class AdminOnlyPermission(GovStackSchedulerRolePermission):
            min_role = "admin"

        class OrganizerPermission(GovStackSchedulerRolePermission):
            min_role = "organizer"

    Role hierarchy (lowest to highest privilege):
        subscriber → resource → organizer → admin

    Note:
      This class is a forward-compatible helper for Wave B–G view implementations.
      Wave A stubs return HTTP 501 and do not enforce role requirements.
      If _gs_actor_role is not set on the request (Wave A), this permission
      falls through to allow the stub to return its 501 response.
    """

    #: Minimum role required to access the endpoint. Override in subclasses.
    min_role: str = "subscriber"

    message = "Insufficient actor role for this GovStack Scheduler endpoint."

    def has_permission(self, request: Request, view: APIView) -> bool:
        # Must pass base GovStack auth first.
        if request.auth != "govstack_scheduler":
            return False

        actor_role = request.META.get("_gs_actor_role", "")
        if not actor_role:
            # Role not yet resolved. In DEBUG mode, raise ImproperlyConfigured if the
            # min_role requirement is above "subscriber" — this catches Wave B+ views
            # that forget to set _gs_actor_role. In production, fall through safely.
            if settings.DEBUG and _role_rank(self.min_role) > _role_rank("subscriber"):
                from django.core.exceptions import ImproperlyConfigured
                raise ImproperlyConfigured(
                    f"{self.__class__.__name__} requires request.META['_gs_actor_role'] "
                    f"to be set (min_role={self.min_role!r}). Set it in your view before "
                    "GovStackSchedulerRolePermission is evaluated."
                )
            return True

        return _role_rank(actor_role) >= _role_rank(self.min_role)
