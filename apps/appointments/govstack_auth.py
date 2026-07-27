"""
GovStack Scheduler BB — authentication and permission classes.

GovStack Scheduler BB-to-BB authentication uses query parameters rather
than headers. Every call from a GovStack Building Block carries:
  - requestor_id   : identifies the requesting BB (looked up as
                     GovStackRegisteredBB.bb_id — a PUBLIC, non-secret
                     infrastructure identifier; safe to log/display)
  - request_token  : authenticates the requestor — verified against a
                     SEPARATE, high-entropy, hashed secret stored in
                     GovStackBBCredential (apps.appointments.models),
                     never against bb_id itself

This contrasts with the Payments BB pattern (X-Registering-Institution-ID header).

Auth classes in this module are used on all /govstack/scheduler/ endpoints.
They are SEPARATE from CivicOS citizen auth (JWT / session / allauth).

SECURITY HISTORY (Finding #1, fixed):
  Earlier versions of this module validated request_token via
  ``GovStackRegisteredBB.objects.filter(bb_id=request_token, ...)`` — i.e.
  the "secret" token WAS the same bb_id value used elsewhere as a public
  identifier (Django admin display, plaintext logs, the Payments BB's
  ordinary SourceBBID request field). Anyone who learned a registered BB's
  bb_id could fully authenticate as that BB. This was a genuine
  auth-bypass-by-design, not just a spec-conformance gap. The fix
  introduces GovStackBBCredential: requestor_id now ONLY performs an
  identity lookup (bb_id, non-secret), and request_token is checked with
  ``check_token()`` against a hashed secret that is never equal to, or
  derived from, bb_id. See GovStackBBCredential's docstring for full detail
  and the generation flow (govstack_generate_bb_credential management
  command).

Operating modes:

  GOVSTACK_SCHEDULER_REQUIRE_TOKEN=False (default — harness / development):
    Any non-empty requestor_id + request_token passes authentication.
    Neither GovStackRegisteredBB nor GovStackBBCredential is consulted.
    Use this mode when running the GovStack certification harness before
    provisioning real credentials.

  GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True (production):
    requestor_id must resolve to an active GovStackRegisteredBB row, that
    row must have an associated GovStackBBCredential, and request_token
    must verify against that credential's hashed secret via check_token().
    requestor_id must also be non-empty and at most 100 chars.
    Set this in production.py alongside GOVSTACK_REQUIRE_REGISTERED_BB.

  If both query parameters are absent, GovStackSchedulerAuth returns None so
  that DRF falls through to other authentication backends (e.g. simplejwt for
  admin access). This preserves the standard CivicOS auth stack for non-BB callers.

Citizen self-booking decision (explicitly considered, see GovStackCitizenAuth
below): YES, citizen self-service endpoints still require the BB credential
gate. The calling system (e.g. CivicOS's own citizen portal) holds the
secret on the citizen's behalf, so the citizen never manages a credential —
this preserves defense-in-depth by verifying which system is calling, in
addition to who the citizen is, rather than trusting citizen JWTs alone.

Permission classes:

  GovStackSchedulerPermission  — base gate; requires request.auth in
                                  ("govstack_scheduler", "govstack_scheduler_subscriber")
  GovStackSchedulerRolePermission — enforces minimum actor role per endpoint

Actor roles (set on request.META["_gs_actor_role"] by the view layer):
  "admin"      — entities, resources, affiliations, all logs
  "organizer"  — events, alert schedules, messages, appointments
  "resource"   — own availability queries
  "subscriber" — own appointments only

Role hierarchy (lowest to highest privilege):
  subscriber → resource → organizer → admin

Resolved caller identity:
  GovStackSchedulerAuth.authenticate() always sets TWO request.META keys on
  success:
    _gs_requestor_id : the raw requestor_id query param (identity string,
                        not a trust signal on its own)
    _gs_resolved_role : the ACTUAL trust-tier role resolved for this caller —
                        either the calling GovStackRegisteredBB row's `role`
                        field (GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True) or
                        "admin" as a dev/harness bootstrapping convenience
                        (GOVSTACK_SCHEDULER_REQUIRE_TOKEN=False — see the
                        docstring on GovStackSchedulerAuth.authenticate()).
  GovStackSchedulerRolePermission enforces access using _gs_resolved_role,
  never a view's own self-declared gs_actor_role — a view's gs_actor_role
  attribute only describes the MINIMUM role required to reach that view, it
  is never treated as evidence of who the caller actually is.
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
        requestor_id must match an active GovStackRegisteredBB row (identity
        lookup only), AND request_token must verify against that BB's
        GovStackBBCredential hashed secret via check_token(). bb_id itself is
        NEVER accepted as the credential (see module docstring, Finding #1).
        Run govstack_generate_bb_credential to provision a credential for a
        registered BB before enabling this mode.

    Return value on success: (None, "govstack_scheduler")
      - user is None because GovStack BB-to-BB calls are not tied to a Django user;
        the actor is identified by requestor_id only.
      - auth string "govstack_scheduler" allows GovStackSchedulerPermission to gate
        access to GovStack endpoints.

    On every successful authentication, two values are stored on request.META
    for downstream permission checks and logging (never on the public DRF
    request attributes, to keep them out of the DRF request inspector):
      _gs_requestor_id  : the raw requestor_id string (identity, not trust).
      _gs_resolved_role : the caller's RESOLVED trust-tier role — this is the
        authoritative role signal consulted by GovStackSchedulerRolePermission.
        In GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True mode this is the matching
        GovStackRegisteredBB row's `role` field. In False mode (dev/harness
        bootstrapping — not a security boundary) it is always "admin", so
        that existing tests and harness runs that don't set up a
        GovStackRegisteredBB row keep passing unauthenticated-role-wise.

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
            # Production mode: requestor_id resolves an active GovStackRegisteredBB row
            # (identity lookup only — bb_id is a public, non-secret identifier). The
            # ACTUAL credential check is request_token verified against that BB's
            # separate GovStackBBCredential hashed secret via check_token(). This is
            # the Finding #1 fix: bb_id itself is NEVER accepted as a bearer credential.
            # Lazy imports avoid circular import issues at module load time and keep
            # this file importable before the app registry is fully initialised.
            from apps.appointments.models import GovStackBBCredential  # noqa: PLC0415
            from apps.payments.govstack_models import GovStackRegisteredBB  # noqa: PLC0415

            bb = GovStackRegisteredBB.objects.filter(
                bb_id=requestor_id,
                is_active=True,
            ).first()
            if bb is None:
                logger.warning(
                    "govstack_scheduler_auth: requestor_id=%r not in whitelist or inactive "
                    "path=%s",
                    requestor_id,
                    request.path,
                )
                raise AuthenticationFailed(
                    "requestor_id is invalid or the requesting BB is not registered."
                )

            credential = GovStackBBCredential.objects.filter(bb=bb).first()
            if credential is None or not credential.check_token(request_token):
                logger.warning(
                    "govstack_scheduler_auth: request_token=[REDACTED] failed verification "
                    "for requestor_id=%r path=%s",
                    requestor_id,
                    request.path,
                )
                raise AuthenticationFailed(
                    "request_token is invalid for the given requestor_id."
                )

            # Best-effort last_used_at bookkeeping — never let a logging/audit write
            # fail the authentication path itself.
            try:
                from django.utils import timezone as _tz  # noqa: PLC0415

                GovStackBBCredential.objects.filter(pk=credential.pk).update(
                    last_used_at=_tz.now()
                )
            except Exception:  # noqa: BLE001
                logger.debug(
                    "govstack_scheduler_auth: failed to update last_used_at for "
                    "credential pk=%s (non-fatal)",
                    credential.pk,
                )

            resolved_role = bb.role
        else:
            # Dev/harness mode is a bootstrapping convenience, not a security boundary
            # (see module docstring) — trust the caller at the highest tier so existing
            # tests that don't set up GovStackRegisteredBB rows keep passing. Real role
            # enforcement testing must use GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True with an
            # actual GovStackRegisteredBB row (mirrors the IsTrustedSourceBB test pattern
            # in apps/payments/tests/test_govstack_auth.py).
            resolved_role = "admin"

        # Store actor identity on the request for downstream permission checks and logging.
        # Using META avoids mutating the DRF request object's public attributes.
        # NOTE: request_token is intentionally NOT stored — it is a credential and must
        # not appear in debug pages, middleware logs, or the DRF request inspector.
        request.META["_gs_requestor_id"] = requestor_id
        request.META["_gs_resolved_role"] = resolved_role

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


class GovStackCitizenAuth(BaseAuthentication):
    """
    Composite authentication for GovStack Scheduler endpoints that support BOTH
    citizen self-service AND organizer/admin BB-to-BB access on behalf of a citizen
    (e.g. Appointment create/delete/list — see SPEC_APPOINTMENTS_BB_GOVSTACK.md §8).

    Always requires the same outer GovStack BB-to-BB trust gate as
    GovStackSchedulerAuth (requestor_id + request_token). On top of that:

      - If an `Authorization: Bearer <JWT>` header is ALSO present, it is validated
        as a CivicOS citizen access token via JWTAuthentication. An invalid or
        expired token is rejected outright (fails closed — never silently ignored,
        to avoid a caller believing they're authenticated as themselves when
        they're not). Staff users are rejected — this path is for citizens only.
        On success: request.user = the resolved citizen, request.auth =
        "govstack_scheduler_subscriber", and request.META["_gs_resolved_role"] =
        "subscriber". The resolved citizen's own pk is the ONLY identity the
        view/service layer may act on for this request.

      - If no Authorization header is present at all, falls back to BB-only trust,
        identical to GovStackSchedulerAuth: request.user = None, request.auth =
        "govstack_scheduler", and _gs_resolved_role set per GovStackSchedulerAuth's
        rules (organizer/admin BBs may then act on an arbitrary participant_id,
        enforced by the view/service layer requiring role >= "organizer" on this path).

    Returns None (falls through to other DRF authenticators) only when BOTH the
    BB credentials and the Authorization header are absent — same convention as
    GovStackSchedulerAuth.
    """

    def authenticate(self, request: Request):
        bb_auth = GovStackSchedulerAuth()
        bb_result = bb_auth.authenticate(request)
        if bb_result is None:
            return None

        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header:
            # BB-only path — identical outcome to GovStackSchedulerAuth.
            return bb_result

        from rest_framework_simplejwt.authentication import JWTAuthentication  # noqa: PLC0415

        jwt_result = JWTAuthentication().authenticate(request)
        if jwt_result is None:
            raise AuthenticationFailed(
                "The supplied Authorization header did not contain a valid citizen access token."
            )
        user, token = jwt_result
        if user.is_staff:
            raise AuthenticationFailed(
                "Subscriber-scoped GovStack operations require a citizen (non-staff) identity."
            )

        request.META["_gs_resolved_role"] = "subscriber"
        return (user, "govstack_scheduler_subscriber")

    def authenticate_header(self, request: Request) -> str:
        return 'GovStackScheduler realm="requestor_id+request_token query params, optional citizen Bearer JWT"'


class GovStackSchedulerPermission(BasePermission):
    """
    Base permission gate for all GovStack Scheduler BB endpoints.

    Grants access if and only if the request was authenticated by
    GovStackSchedulerAuth or GovStackCitizenAuth (i.e. request.auth is
    "govstack_scheduler" for a BB-to-BB call, or "govstack_scheduler_subscriber"
    for a citizen JWT-authenticated call).

    Apply this to all views under /govstack/scheduler/ to ensure that
    GovStack-specific endpoints are unreachable by standard CivicOS citizen
    session auth or unauthenticated requests.

    Usage:
        permission_classes = [GovStackSchedulerPermission]

    Note:
      Wave A stub views use the @api_view decorator with no permission_classes,
      so the stubs return 501 to both authenticated and unauthenticated callers.
      Concrete Wave B–G views must set this permission class explicitly.
    """

    message = "GovStack Scheduler authentication required (requestor_id + request_token)."

    def has_permission(self, request: Request, view: APIView) -> bool:
        return request.auth in ("govstack_scheduler", "govstack_scheduler_subscriber")


class GovStackSchedulerRolePermission(BasePermission):
    """
    Role-based permission helper for GovStack Scheduler BB endpoints.

    Enforces the endpoint's minimum required actor role against the CALLER'S
    resolved role — never against the view's own self-declared role attribute
    (a view's gs_actor_role only states what is required to reach it, it is
    never evidence of who is calling).

    Two cases:
      1. Citizen self-service (Tier 2): request.auth ==
         "govstack_scheduler_subscriber" (set by GovStackCitizenAuth after
         validating a citizen JWT). Always permitted at THIS layer — ownership
         of the specific resource being acted on (e.g. "is this appointment
         actually the caller's own?") is enforced in the service/view layer,
         not here, since a citizen may only ever act on their own data
         regardless of any BB-level role.
      2. BB-to-BB (Tier 1): request.auth == "govstack_scheduler". The caller's
         resolved role is read from request.META["_gs_resolved_role"], set by
         GovStackSchedulerAuth.authenticate() on every successful
         authentication. Compared against the endpoint's minimum required
         role — the view's class-level `gs_actor_role` attribute if present,
         else this permission class's own `min_role`.

    Subclass and override min_role to enforce a minimum role level:

        class AdminOnlyPermission(GovStackSchedulerRolePermission):
            min_role = "admin"

        class OrganizerPermission(GovStackSchedulerRolePermission):
            min_role = "organizer"

    Role hierarchy (lowest to highest privilege):
        subscriber → resource → organizer → admin
    """

    #: Minimum role required to access the endpoint. Override in subclasses.
    min_role: str = "subscriber"

    message = "Insufficient actor role for this GovStack Scheduler endpoint."

    def has_permission(self, request: Request, view: APIView) -> bool:
        # Citizen self-service (Tier 2) is always permitted at this layer — ownership
        # of the specific resource being acted on is enforced in the service/view layer,
        # not here, since a citizen may only ever act on their own data regardless of
        # any BB-level role.
        if request.auth == "govstack_scheduler_subscriber":
            return True

        if request.auth != "govstack_scheduler":
            return False

        required_role = getattr(view, "gs_actor_role", None) or self.min_role
        resolved_role = request.META.get("_gs_resolved_role", "")
        if not resolved_role:
            # GovStackSchedulerAuth always sets this on success — absence means a
            # bug upstream. Fail closed.
            return False
        return _role_rank(resolved_role) >= _role_rank(required_role)
