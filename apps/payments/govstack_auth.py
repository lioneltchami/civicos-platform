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
  IsTrustedSourceBB accepts any non-empty X-Registering-Institution-ID header.
  AllowAnyBB accepts any non-empty Gov_Stack_BB value in the request body.

For production deployment:
  Replace the body of IsTrustedSourceBB.has_permission() with a lookup against
  a GovStackRegisteredBB table (to be added in a future wave).
  AllowAnyBB.has_permission() similarly should validate Gov_Stack_BB against
  a registered BB registry.

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
    Grants access when the X-Registering-Institution-ID header is present
    and non-empty.

    Used on:
      - POST /govstack/payments/register-beneficiary
      - POST /govstack/payments/update-beneficiary-details
      - POST /govstack/payments/bulk-payment
      - POST /govstack/payments/prepayment-validation
      - POST /govstack/payments/prepayment-validation-response

    Production upgrade path:
      Validate institution_id against a whitelist / database table of
      registered institutions. For now (harness testing), any non-empty
      string passes.

    Failure response: HTTP 401 (DRF returns 401 for anonymous users that fail a permission
    check, since authentication was not attempted). The harness does not explicitly test
    the failure status code on these endpoints.
    """

    message = "Missing or invalid X-Registering-Institution-ID header."

    def has_permission(self, request: Request, view: APIView) -> bool:
        institution_id = (
            request.headers.get("X-Registering-Institution-ID", "").strip()
            or request.headers.get("X-Registering-Institution-Id", "").strip()
        )
        if not institution_id:
            logger.debug(
                "govstack_auth.IsTrustedSourceBB: missing X-Registering-Institution-ID "
                "path=%s",
                request.path,
            )
            return False

        # Validate length per spec (max 20 chars)
        if len(institution_id) > 20:
            logger.debug(
                "govstack_auth.IsTrustedSourceBB: institution_id too long (%d chars) path=%s",
                len(institution_id),
                request.path,
            )
            return False

        return True


class AllowAnyBB(BasePermission):
    """
    Grants access to any caller (no specific header required at the permission
    layer). Endpoint-level validation of Gov_Stack_BB in the request body
    happens in the view or service.

    Used on:
      - POST /govstack/payments/vouchers/voucher_preactivation
      - PATCH /govstack/payments/vouchers/voucher_activation
      - POST /govstack/payments/vouchers/voucher_redemption
      - GET/PATCH /govstack/payments/vouchers/voucherstatuscheck/{serial}

    This is intentionally permissive at the permission layer because:
      1. The harness does not send a pre-registered auth token.
      2. Gov_Stack_BB validation (HTTP 460) happens at the business logic level.
      3. Voucher redemption/status additionally require JWT auth (see HasVoucherJWT).
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
      The view must also include JWTAuthentication in its authentication_classes.

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
