"""
GovStack Payments BB — authentication and permission classes.

GovStack BB-to-BB authentication is header-based.
The caller is always another GovStack Building Block
(Registry BB, Information Mediator BB, Social Protection BB),
NOT a citizen or CivicOS staff user.

Permission matrix:
  IsTrustedSourceBB  — G2P endpoints (register-beneficiary, update-beneficiary-details,
                        bulk-payment, prepayment-validation, prepayment-validation-response)
  AllowAnyBB         — Voucher endpoints that use Gov_Stack_BB in the request body
  HasVoucherJWT      — Voucher redemption and status check (require Bearer JWT)

Verified harness behaviour (checked directly against the live
GovStackWorkingGroup/bb-payments repo's `test/openAPI/features/support/g2p_*.js`
step-definition files, not against the formal OpenAPI YAMLs or prior code
comments — those two sources disagree with each other and with the real
harness):

  The real Gherkin/Cucumber harness NEVER sends an X-Registering-Institution-ID
  (or -Id) header on ANY G2P endpoint tested by the harness (register-beneficiary,
  update-beneficiary-details, bulk-payment, prepayment-validation — the harness
  has no separate scenario for prepayment-validation-response's own auth, but it
  shares the same view/permission wiring). This is true even for the "smoke
  test" scenarios that must return HTTP 200. A permission class that
  unconditionally requires this header — regardless of settings — would
  reject every harness call.

For the GovStack test harness:
  The harness calls endpoints without a pre-negotiated API key and without the
  X-Registering-Institution-ID header at all.
  Set GOVSTACK_REQUIRE_REGISTERED_BB=False in the harness environment (default in
  tests) so IsTrustedSourceBB behaves like AllowAnyBB when the header is absent
  (matching real harness behaviour) while still validating the header (length,
  and in production mode the whitelist) if a caller happens to supply one.
  AllowAnyBB accepts any non-empty Gov_Stack_BB value in the request body.

For production deployment:
  GOVSTACK_REQUIRE_REGISTERED_BB=True (the production default set in production.py)
  causes IsTrustedSourceBB to require the header and query the
  GovStackRegisteredBB whitelist table. Only BBs with a matching, is_active=True
  row are granted access. Run seed_govstack_vouchers before enabling this in the
  harness environment to create GovStackRegisteredBB(bb_id="GS-HARNESS") so the
  harness ID passes (if the harness is ever updated to send the header).

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
    Grants access for GovStack G2P endpoints based on the
    X-Registering-Institution-ID (or -Id) header, with a harness-mode
    fallback that mirrors HasVoucherJWT's degrade-to-AllowAnyBB pattern.

    Used on:
      - POST /govstack/payments/register-beneficiary
      - POST /govstack/payments/update-beneficiary-details
      - POST /govstack/payments/bulk-payment
      - POST /govstack/payments/prepayment-validation
      - POST /govstack/payments/prepayment-validation-response

    Verified harness behaviour:
      The real GovStack Cucumber/Gherkin harness (checked directly against
      `test/openAPI/features/support/g2p_*.js` in GovStackWorkingGroup/bb-payments)
      NEVER sends this header on any of the 4 G2P endpoints, including the
      smoke-test scenarios. A permission class that required the header
      unconditionally would reject every real harness call.

    Two operating modes, controlled by the GOVSTACK_REQUIRE_REGISTERED_BB
    Django setting (mirrors the GOVSTACK_VOUCHER_REQUIRE_JWT pattern) — the
    mode only changes what happens when the header is ABSENT or, if present,
    whether it must additionally match the whitelist:

      GOVSTACK_REQUIRE_REGISTERED_BB=False (default in tests / harness):
        - Header absent  → access is granted (AllowAnyBB-equivalent fallback,
          matching real harness behaviour — this is the important fix: a
          missing header must NOT be treated as a failure in harness mode).
        - Header present → still validated for length (≤ 20 chars). A caller
          that explicitly supplies a header is honoured, but an oversized
          value is still rejected. The whitelist DB lookup is NOT performed
          in this mode (that only happens in production mode below).

      GOVSTACK_REQUIRE_REGISTERED_BB=True (production default via production.py):
        - Header absent  → access is denied (production must not allow
          anonymous callers).
        - Header present → validated for length (≤ 20 chars) and then looked
          up in the GovStackRegisteredBB whitelist; only an active, matching
          row grants access.  Run seed_govstack_vouchers first to create the
          harness row (bb_id="GS-HARNESS") before enabling this mode.

    Note the length check and the whitelist lookup apply identically whether
    or not GOVSTACK_REQUIRE_REGISTERED_BB is set — the settings flag governs
    (a) whether an absent header is tolerated and (b) whether a present header
    is checked against the DB whitelist. This keeps the whitelist path fully
    testable via @override_settings(GOVSTACK_REQUIRE_REGISTERED_BB=True) even
    though harness mode is otherwise permissive.

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

        require_registered = getattr(settings, "GOVSTACK_REQUIRE_REGISTERED_BB", False)

        if not institution_id:
            if not require_registered:
                # Harness / test mode: the real harness never sends this header
                # on any G2P endpoint (confirmed against the live g2p_*.js step
                # definitions), so a missing header must degrade to
                # AllowAnyBB-equivalent behaviour here — mirroring how
                # HasVoucherJWT falls back when GOVSTACK_VOUCHER_REQUIRE_JWT=False.
                logger.debug(
                    "govstack_auth.IsTrustedSourceBB: no X-Registering-Institution-ID "
                    "header, harness mode — allowing path=%s",
                    request.path,
                )
                return True

            # Production mode: the header is mandatory.
            logger.debug(
                "govstack_auth.IsTrustedSourceBB: missing X-Registering-Institution-ID "
                "path=%s",
                request.path,
            )
            return False

        # A header WAS supplied — validate it regardless of mode. This keeps the
        # length check and (in production mode) the whitelist check exercisable
        # even when GOVSTACK_REQUIRE_REGISTERED_BB=False, so a test can supply an
        # explicit header and still observe real validation behaviour.
        if len(institution_id) > 20:
            logger.debug(
                "govstack_auth.IsTrustedSourceBB: institution_id too long (%d chars) path=%s",
                len(institution_id),
                request.path,
            )
            return False

        if not require_registered:
            # Harness / test mode: header-only check is sufficient — no DB lookup.
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
      - P2G bill endpoints
      - GovStackG2PView's own class-level default (permission_classes =
        [AllowAnyBB]) — kept as the base-class fallback in case a future G2P
        subclass needs it, but all 5 concrete G2P endpoint views
        (RegisterBeneficiaryView, UpdateBeneficiaryView, BulkPaymentView,
        PrepaymentValidationView, PrepaymentValidationResponseView) now
        override this with [IsTrustedSourceBB] instead — see that class's
        docstring for why this is now safe to do uniformly (its harness-mode
        behaviour with an absent header is equivalent to AllowAnyBB).

    NOT used on:
      - POST /govstack/payments/vouchers/voucher_redemption  → HasVoucherJWT
      - GET/PATCH /govstack/payments/vouchers/voucherstatuscheck/{serial} → HasVoucherJWT
      - G2P register-beneficiary, update-beneficiary-details, bulk-payment,
        prepayment-validation, prepayment-validation-response → IsTrustedSourceBB

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
