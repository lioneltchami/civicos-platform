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
  IsTrustedPayerFI   — P2G endpoints (bill inquiry, bill transfer request).
                        Mode-gated like IsTrustedSourceBB: header optional in
                        harness mode, required + whitelist-checked in
                        production (GOVSTACK_REQUIRE_REGISTERED_PAYER_FI).
  RequirePayerFI     — MarkBillPaidView only. Fail-closed variant of
                        IsTrustedPayerFI: the X-PayerFI-Id header (or accepted
                        variant) is ALWAYS required, in every settings mode,
                        because this endpoint mutates real bill state, has
                        zero harness coverage to protect, and carries no
                        idempotency key of its own.
  IsTrustedBiller    — TransferRequestStatusView only. A fresh fetch of the
                        live rtpStatusUpdateRequest.yml spec confirmed this
                        endpoint requires X-billerId (required: true,
                        maxLength: 20) and has NO PayerFI header of any kind —
                        it was previously (incorrectly) wired to
                        IsTrustedPayerFI/X-PayerFI-Id. Mode-gated the same way
                        as IsTrustedPayerFI, reusing the
                        GOVSTACK_REQUIRE_REGISTERED_PAYER_FI flag (the
                        existing P2G-wide flag) rather than inventing a new
                        one for a single endpoint.

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

Not in this file — X-Platform-TenantId (tenant scoping):
  The 4 P2G views also validate a SEPARATE header, X-Platform-TenantId (or
  Platform-TenantId), via GovStackAPIView._validate_platform_tenant_id() in
  govstack_views.py — NOT a permission class, and NOT defined here. That
  header answers "which tenant's data" (a request-validation concern, HTTP
  400 on failure), whereas every permission class in this file answers "who
  is calling" (an auth concern, HTTP 401 on failure). The two are
  deliberately kept separate rather than stacking two permission classes per
  view for two different questions. See that method's docstring for the full
  design rationale and live-spec citations.
"""

from __future__ import annotations

import logging

from django.conf import settings
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class _HeaderWhitelistBBPermission(BasePermission):
    """
    Shared base for header-based, whitelist-checked BB permission classes.

    Generalizes the pattern originally written for IsTrustedSourceBB so it
    can be reused for IsTrustedPayerFI / RequirePayerFI (the P2G caller-identity
    permission classes) without duplicating the header-lookup / length-check /
    whitelist-lookup logic. Subclasses configure behaviour entirely through
    class attributes — no method overrides should be necessary for a normal
    new header/flag pairing.

    Class attributes subclasses MUST set:
      header_names   — tuple of header name variants to check, in priority
                        order (first non-empty value wins). Lookups are via
                        Django's HttpHeaders, which is already
                        case-insensitive, so variants are only needed here
                        for genuinely different header names (e.g. with vs.
                        without an "X-" prefix), not for letter-casing.
      settings_flag  — name of the Django setting that gates strict/whitelist
                        mode (mirrors GOVSTACK_REQUIRE_REGISTERED_BB).
      message        — DRF permission denial message.

    Class attributes subclasses MAY override:
      require_header_always — if True, an absent/empty header is ALWAYS
                        denied, regardless of the settings_flag value. Used
                        by RequirePayerFI for MarkBillPaidView's fail-closed
                        requirement. Defaults to False (the IsTrustedSourceBB /
                        IsTrustedPayerFI mode-gated behaviour).
      max_length     — maximum accepted header length. Defaults to 20,
                        matching both X-Registering-Institution-ID's existing
                        limit and X-PayerFI-Id's `maxLength: 20` in the live
                        GovStack P2G spec.

    Two operating modes, controlled by `settings_flag` — the mode only
    changes what happens when the header is ABSENT (unless
    require_header_always=True, which always denies on absence) or, if
    present, whether it must additionally match the whitelist:

      <settings_flag>=False (default in tests / harness):
        - Header absent  → access is granted (AllowAnyBB-equivalent fallback),
          UNLESS require_header_always=True, in which case access is denied.
        - Header present → still validated for length (≤ max_length chars).
          The whitelist DB lookup is NOT performed in this mode.

      <settings_flag>=True (production default via production.py):
        - Header absent  → access is denied (production must not allow
          anonymous callers). This is also the outcome when
          require_header_always=True, in any mode.
        - Header present → validated for length (≤ max_length chars) and then
          looked up in the GovStackRegisteredBB whitelist; only an active,
          matching row grants access.

    Note the length check and the whitelist lookup apply identically whether
    or not `settings_flag` is set — the settings flag governs (a) whether an
    absent header is tolerated and (b) whether a present header is checked
    against the DB whitelist. This keeps the whitelist path fully testable
    via @override_settings(<settings_flag>=True) even though harness mode is
    otherwise permissive.

    Failure response: HTTP 401 (DRF returns NotAuthenticated for anonymous
    callers that fail a permission check, since authentication was not
    attempted).
    """

    header_names: tuple[str, ...] = ()
    settings_flag: str = ""
    message = "Missing or invalid caller-identity header."
    require_header_always = False
    max_length = 20

    def _extract_header(self, request: Request) -> str:
        for header_name in self.header_names:
            value = request.headers.get(header_name, "").strip()
            if value:
                return value
        return ""

    def has_permission(self, request: Request, view: APIView) -> bool:
        caller_id = self._extract_header(request)
        require_registered = getattr(settings, self.settings_flag, False)

        if not caller_id:
            if self.require_header_always:
                # Fail-closed variant (e.g. RequirePayerFI): a missing header
                # is ALWAYS denied, regardless of settings_flag.
                logger.debug(
                    "govstack_auth.%s: missing header (fail-closed) path=%s",
                    type(self).__name__,
                    request.path,
                )
                return False

            if not require_registered:
                # Harness / test mode: a missing header degrades to
                # AllowAnyBB-equivalent behaviour — mirroring how
                # HasVoucherJWT falls back when its own flag is False.
                logger.debug(
                    "govstack_auth.%s: no header present, harness mode — " "allowing path=%s",
                    type(self).__name__,
                    request.path,
                )
                return True

            # Production mode: the header is mandatory.
            logger.debug(
                "govstack_auth.%s: missing header path=%s",
                type(self).__name__,
                request.path,
            )
            return False

        # A header WAS supplied — validate it regardless of mode. This keeps
        # the length check and (in production mode) the whitelist check
        # exercisable even when settings_flag is False, so a test can supply
        # an explicit header and still observe real validation behaviour.
        if len(caller_id) > self.max_length:
            logger.debug(
                "govstack_auth.%s: caller id too long (%d chars) path=%s",
                type(self).__name__,
                len(caller_id),
                request.path,
            )
            return False

        if not require_registered:
            # Harness / test mode: header-only check is sufficient — no DB lookup.
            # caller_id is truthy here (the "not caller_id" branch above already
            # returned for the empty case), so it is safe to stash as the
            # resolved caller identity for downstream throttling/logging.
            request.META["_gs_payer_identity"] = caller_id
            return True

        # Production mode (or require_header_always): validate against the
        # GovStackRegisteredBB whitelist. Lazy import avoids circular import
        # issues at module load time and keeps this file importable before
        # the app registry is fully initialised.
        from apps.payments.govstack_models import GovStackRegisteredBB

        granted = GovStackRegisteredBB.objects.filter(
            bb_id=caller_id,
            is_active=True,
        ).exists()

        if not granted:
            logger.warning(
                "govstack_auth.%s: caller_id=%r not in whitelist or inactive path=%s",
                type(self).__name__,
                caller_id,
                request.path,
            )
        else:
            # Access GRANTED with a known, whitelisted caller_id — stash it so
            # a throttle class can key on caller identity instead of client IP.
            # Uses the exact key name "_gs_payer_identity" (mirrors
            # apps.appointments.govstack_auth.GovStackSchedulerAuth's
            # "_gs_requestor_id" pattern). Never stashed when caller_id is
            # falsy — see the harness-mode absent-header branch above, which
            # grants access with no identity and must not stash "".
            request.META["_gs_payer_identity"] = caller_id

        return granted


class IsTrustedSourceBB(_HeaderWhitelistBBPermission):
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
    Django setting (mirrors the GOVSTACK_VOUCHER_REQUIRE_JWT pattern) — see
    the shared logic and full mode documentation on
    _HeaderWhitelistBBPermission, this class's base.

    The harness does not explicitly test the failure status code here.
    """

    header_names = ("X-Registering-Institution-ID", "X-Registering-Institution-Id")
    settings_flag = "GOVSTACK_REQUIRE_REGISTERED_BB"
    message = "Missing or invalid X-Registering-Institution-ID header."


class IsTrustedPayerFI(_HeaderWhitelistBBPermission):
    """
    Grants access for GovStack P2G endpoints based on the X-PayerFI-Id
    header (accepting the X-PayerFI-ID and PayerFI-Id spelling variants seen
    across the upstream P2G YAMLs), with the same mode-gated harness fallback
    as IsTrustedSourceBB.

    Used on:
      - GET  /govstack/payments/bills/{bill_id}
      - POST /govstack/payments/billTransferRequests

    NOT used on:
      - POST /govstack/payments/bills/{bill_id}/mark-paid → RequirePayerFI
        (fail-closed variant — see that class's docstring)
      - GET  /govstack/payments/transferRequests/{transfer_request_id} →
        IsTrustedBiller (certifiability-audit fix — a fresh fetch of the live
        rtpStatusUpdateRequest.yml spec confirmed this endpoint requires
        X-billerId, not X-PayerFI-Id, and has no PayerFI header of any kind.
        This view was previously (incorrectly) wired to IsTrustedPayerFI.
        See IsTrustedBiller's docstring below.)

    Real spec vs. harness:
      The live GovStack P2G YAMLs (`api/P2G API YAMLs/`) declare a `security`
      scheme keyed on X-CorrelationID, which is a correlation identifier, not
      a credential. The genuine caller-identity field is X-PayerFI-Id
      (`billPaymentRequest.yml`, required, maxLength: 20) — inconsistently
      spelled `PayerFI-Id` and `X-Payer FI-ID` (a literal typo) elsewhere in
      the same YAML set. This codebase standardizes on accepting
      X-PayerFI-Id, X-PayerFI-ID, and PayerFI-Id.

      There is currently zero P2G harness coverage (no bill/p2g/transferRequest
      reference anywhere in `test/openAPI/features/`), so — unlike
      IsTrustedSourceBB — this class's harness-mode behaviour is not being
      validated against a real Cucumber harness; it exists purely to close
      the "zero caller-identity validation in every environment" gap while
      preserving today's harness-mode permissiveness for these 3 endpoints.

    Two operating modes, controlled by the GOVSTACK_REQUIRE_REGISTERED_PAYER_FI
    Django setting — see the shared logic and full mode documentation on
    _HeaderWhitelistBBPermission, this class's base. Kept as its own flag
    (rather than reusing GOVSTACK_REQUIRE_REGISTERED_BB) so Payer-FI
    enforcement can be rolled out independently of G2P/voucher enforcement.
    """

    header_names = ("X-PayerFI-Id", "X-PayerFI-ID", "PayerFI-Id")
    settings_flag = "GOVSTACK_REQUIRE_REGISTERED_PAYER_FI"
    message = "Missing or invalid X-PayerFI-Id header."


class RequirePayerFI(IsTrustedPayerFI):
    """
    Fail-closed variant of IsTrustedPayerFI, used ONLY on MarkBillPaidView.

    Used on:
      - POST /govstack/payments/bills/{bill_id}/mark-paid

    Unlike IsTrustedPayerFI (and every other permission class in this
    module), a missing/empty X-PayerFI-Id header (or accepted variant) is
    ALWAYS denied here — in every settings mode, including harness/test mode
    where GOVSTACK_REQUIRE_REGISTERED_PAYER_FI defaults to False. This
    deliberately does NOT degrade to AllowAnyBB-equivalent behaviour.

    Rationale (mirrors the design note in SPEC_GOVSTACK_PAYMENTS_BB.md §24.2):
      MarkBillPaidView mutates real bill state (UNPAID → PAID) outside of the
      normal POST /billTransferRequests flow, has zero harness coverage to
      protect (so there is no harness-compatibility reason to stay
      permissive), and carries no idempotency key of its own — unlike
      BillTransferRequestView, which is idempotency-keyed on requestId.

    When a header IS present, this class applies the exact same length
    (≤ 20 chars) and, when GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=True,
    whitelist checks as IsTrustedPayerFI — only the "header absent" branch
    differs.
    """

    require_header_always = True
    message = (
        "Missing or invalid X-PayerFI-Id header. This endpoint requires " "caller identification."
    )


class IsTrustedBiller(_HeaderWhitelistBBPermission):
    """
    Grants access for GET /transferRequests/{transferRequestId} based on the
    X-billerId header — confirmed via a fresh fetch of the live
    rtpStatusUpdateRequest.yml spec file, which requires X-billerId
    (required: true, maxLength: 20) and contains no PayerFI header of any
    kind. This endpoint was previously (incorrectly) wired to
    IsTrustedPayerFI/X-PayerFI-Id — see MASTER_BB_CERTIFIABILITY_REPORT.md
    "Payments BB" for the audit finding this fixes.

    Used on:
      - GET /govstack/payments/transferRequests/{transfer_request_id}

    Mode-gated identically to IsTrustedPayerFI (see
    _HeaderWhitelistBBPermission for the full mode breakdown): the header is
    optional in harness/test mode and required + whitelist-checked in
    production. Reuses GOVSTACK_REQUIRE_REGISTERED_PAYER_FI rather than
    introducing a new settings flag for a single endpoint — this is the same
    P2G-wide "is caller-identity enforcement turned on for P2G" toggle that
    IsTrustedPayerFI/RequirePayerFI already use, so enabling it in production
    hardens all 4 P2G views uniformly.

    There is currently zero P2G harness coverage for this endpoint, so — like
    IsTrustedPayerFI — this class's harness-mode behaviour is not being
    validated against a real Cucumber harness; it exists purely to align
    caller-identity enforcement with the live spec's actual required header.
    """

    header_names = ("X-billerId", "X-BillerId", "X-billerID")
    settings_flag = "GOVSTACK_REQUIRE_REGISTERED_PAYER_FI"  # reuse the existing P2G-wide flag rather than inventing a new one for a single endpoint  # noqa: E501
    message = "Missing or invalid X-billerId header."


class AllowAnyBB(BasePermission):
    """
    Grants access to any caller (no specific header required at the permission
    layer). Endpoint-level validation of Gov_Stack_BB in the request body
    happens in the view or service.

    Used on:
      - POST /govstack/payments/vouchers/voucher_preactivation
      - PATCH /govstack/payments/vouchers/voucher_activation
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
      - P2G bill inquiry, bill transfer request → IsTrustedPayerFI;
        P2G mark-bill-paid → RequirePayerFI; P2G transfer request status →
        IsTrustedBiller (Issue B fix — these 4 views previously used
        AllowAnyBB with zero caller-identity validation of any kind, in every
        settings mode including production)

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
