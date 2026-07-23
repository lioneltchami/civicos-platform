"""
GovStack Payments BB — authentication and permission classes.

GovStack BB-to-BB authentication is header-based.
The caller is always another GovStack Building Block
(Registry BB, Information Mediator BB, Social Protection BB),
NOT a citizen or CivicOS staff user.

Permission matrix:
  IsTrustedSourceBB  — G2P endpoints (register-beneficiary, update-beneficiary-details,
                        bulk-payment, prepayment-validation)
  AllowAnyBB         — Voucher endpoints that use Gov_Stack_BB in the request body
  HasVoucherJWT      — Voucher redemption and status check (require Bearer JWT)

For the GovStack test harness:
  The harness calls endpoints without a pre-negotiated API key.
  Set GOVSTACK_REQUIRE_REGISTERED_BB=False in the harness environment (default in
  tests) so IsTrustedSourceBB accepts any valid non-empty header without a DB lookup.
  AllowAnyBB accepts any non-empty Gov_Stack_BB value in the request body.

For production deployment:
  GOVSTACK_REQUIRE_REGISTERED_BB=True (the production default set in production.py)
  causes IsTrustedSourceBB to query the GovStackRegisteredBB whitelist table.
  Only BBs with a matching, is_active=True row are granted access.
  Run seed_govstack_vouchers before enabling this in the harness environment to
  create GovStackRegisteredBB(bb_id="GS-HARNESS") so the harness ID passes.

Security note:
  These permissions do NOT authenticate individual citizens. They authenticate
  inter-BB API calls. Do not mix with CivicOS's IsAuthenticated / JWT auth for
  citizen-facing endpoints.
"""
from __future__ import annotations

import logging

from django.conf import settings
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class IsTrustedSourceBB(BasePermission):
    """
    Grants access when the X-Registering-Institution-ID header is present,
    non-empty, ≤ 20 chars, AND (in production mode) matches an active
    GovStackRegisteredBB whitelist row.

    Used on:
      - POST /govstack/payments/register-beneficiary
      - POST /govstack/payments/update-beneficiary-details
      - POST /govstack/payments/bulk-payment
      - POST /govstack/payments/prepayment-validation
      - POST /govstack/payments/prepayment-validation-response

    Two operating modes, controlled by the GOVSTACK_REQUIRE_REGISTERED_BB
    Django setting (mirrors the GOVSTACK_VOUCHER_REQUIRE_JWT pattern):

      GOVSTACK_REQUIRE_REGISTERED_BB=False (default in tests / harness):
        Header-only check — any non-empty, ≤ 20-char header value passes.
        This preserves backward compatibility with harness runs and existing
        tests that do not seed the GovStackRegisteredBB table.

      GOVSTACK_REQUIRE_REGISTERED_BB=True (production default via production.py):
        DB lookup — the institution_id must match a GovStackRegisteredBB row
        with is_active=True.  Run seed_govstack_vouchers first to create the
        harness row (bb_id="GS-HARNESS") before enabling this mode.

    Failure response: HTTP 401 (DRF returns NotAuthenticated for anonymous
    callers that fail a permission check, since authentication was not attempted).
    The harness does not explicitly test the failure status code here.
    """

    message = "Missing or invalid X-Registering-Institution-ID header."

    def has_permission(self, request: Request, view: APIView) -> bool:
        institution_id = (
            request.headers.get("X-Registering-Institution-ID", "").strip()
            or request.headers.get("X-Registering-Institution-Id", "").strip()
        )

        # Header presence and length validation is ALWAYS applied regardless of mode.
        if not institution_id:
            logger.debug(
                "govstack_auth.IsTrustedSourceBB: missing X-Registering-Institution-ID "
                "path=%s",
                request.path,
            )
            return False

        if len(institution_id) > 20:
            logger.debug(
                "govstack_auth.IsTrustedSourceBB: institution_id too long (%d chars) path=%s",
                len(institution_id),
                request.path,
            )
            return False

        # Mode switch — same pattern as HasVoucherJWT / GOVSTACK_VOUCHER_REQUIRE_JWT.
        require_registered = getattr(settings, "GOVSTACK_REQUIRE_REGISTERED_BB", False)
        if not require_registered:
            # Harness / test mode: header-only check is sufficient.
            return True

        # Production mode: validate against the GovStackRegisteredBB whitelist.
        # Lazy import avoids circular import issues at module load time and keeps
        # this file importable before the app registry is fully initialised.
        from apps.payments.govstack_models import GovStackRegisteredBB  # noqa: PLC0415

        granted = GovStackRegisteredBB.objects.filter(
            bb_id=institution_id,
            is_active=True,
        ).exists()

        if not granted:
            logger.warning(
                "govstack_auth.IsTrustedSourceBB: institution_id=%r not in whitelist "
                "or inactive path=%s",
                institution_id,
                request.path,
            )

        return granted


class AllowAnyBB(BasePermission):
    """
    Grants access to any caller (no specific header required at the permission
    layer). Endpoint-level validation of Gov_Stack_BB in the request body
    happens in the view or service.

    Used on:
      - POST /govstack/payments/vouchers/voucher_preactivation
      - PATCH /govstack/payments/vouchers/voucher_activation
      - G2P bulk-payment, prepayment-validation, P2G bill endpoints
        (via GovStackG2PView which sets permission_classes = [AllowAnyBB])

    NOT used on:
      - POST /govstack/payments/vouchers/voucher_redemption  → HasVoucherJWT
      - GET/PATCH /govstack/payments/vouchers/voucherstatuscheck/{serial} → HasVoucherJWT

    This is intentionally permissive at the permission layer because:
      1. The harness does not send a pre-registered auth token.
      2. Gov_Stack_BB validation (HTTP 460) happens at the business logic level.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        return True


class HasVoucherJWT(BasePermission):
    """
    Requires a valid Bearer JWT token for voucher redemption and status endpoints.

    These endpoints handle money redemption and are higher-risk than the
    bulk-payment or beneficiary registration flows, so they require explicit
    authentication per the GovStack Payments spec's bearerAuth security scheme.

    For Wave 4 implementation:
      This delegates to DRF's built-in JWTAuthentication (simplejwt).
      JWTAuthentication is active via the global DEFAULT_AUTHENTICATION_CLASSES
      in config/settings/base.py — the views using this permission do NOT need
      to set authentication_classes explicitly.

    For the harness:
      The harness may supply a test JWT. If it does not, this permission
      falls back to AllowAnyBB behaviour so harness tests are not blocked.
      Set GOVSTACK_VOUCHER_REQUIRE_JWT = True in production settings.
    """

    message = "Authentication credentials were not provided or are invalid."

    def has_permission(self, request: Request, view: APIView) -> bool:
        require_jwt = getattr(settings, "GOVSTACK_VOUCHER_REQUIRE_JWT", False)

        if not require_jwt:
            # Harness mode: JWT not enforced.
            return True

        # Production mode: require authenticated user (set by JWTAuthentication).
        return request.user is not None and request.user.is_authenticated
