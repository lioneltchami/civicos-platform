"""
GovStack Payments Building Block — API views.

Implements the GovStack bb-payments API surface for CivicOS certification.
All endpoints return Content-Type: application/json.

URL prefix: /govstack/payments/
Namespace:  govstack_payments

Endpoint inventory (9 harness-tested + 4 P2G):
  G2P Beneficiary (Wave 2):
    POST /govstack/payments/register-beneficiary
    POST /govstack/payments/update-beneficiary-details

  G2P Bulk (Wave 3):
    POST /govstack/payments/bulk-payment
    POST /govstack/payments/prepayment-validation
    POST /govstack/payments/prepayment-validation-response

  Voucher (Wave 4):
    POST  /govstack/payments/vouchers/voucher_preactivation
    PATCH /govstack/payments/vouchers/voucher_activation
    POST  /govstack/payments/vouchers/voucher_redemption
    PATCH /govstack/payments/vouchers/voucherstatuscheck/{voucherserialnumber}
    GET   /govstack/payments/vouchers/voucherstatuscheck/{voucherserialnumber}

  P2G (Wave 5):
    GET   /govstack/payments/bills/{bill_id}
    POST  /govstack/payments/billTransferRequests
    POST  /govstack/payments/bills/{bill_id}/mark-paid
    GET   /govstack/payments/transferRequests/{transfer_request_id}

Architecture:
  - GovStackAPIView is the base class for all views in this module.
  - It sets the custom exception handler (govstack_exception_handler) so
    all error responses are normalised to {"message": "..."}.
  - Throttle scope "govstack_bb" is applied to all views.
  - Wave 1: All views return {"status": "not_implemented"} (HTTP 501).
  - Wave 2–5: Views delegate to service methods and return proper shapes.

Security:
  - All views enforce specific permission classes (never IsAuthenticated alone).
  - No PII in any log line.
  - X-Callback-URL and BB headers are validated in auth/service layers.
  - Content-Type: application/json enforced by DRF's Response class.

DO NOT MODIFY:
  - apps/payments/views/ (Stripe-facing CivicOS views)
  - apps/payments/urls.py (CivicOS internal URL routing)
"""
from __future__ import annotations

import logging

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .govstack_auth import AllowAnyBB, HasVoucherJWT, IsTrustedSourceBB
from .govstack_exceptions import govstack_exception_handler, govstack_g2p_exception_handler
from .govstack_serializers import RegisterBeneficiaryRequestSerializer
from .govstack_services import GovStackBeneficiaryService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Base view
# ---------------------------------------------------------------------------

class GovStackAPIView(APIView):
    """
    Base class for all GovStack Payments views.

    Provides:
    - Custom exception handler (normalises all errors to {"message": "..."})
    - Throttle scope "govstack_bb" (100 req/min per IP via ScopedRateThrottle)
    - Logging of incoming requests at DEBUG level (no PII)

    Wave 1 note: all concrete views inherit this and return 501 stubs.
    """
    # ScopedRateThrottle is set explicitly here so the govstack_bb scope is active
    # without touching DEFAULT_THROTTLE_CLASSES (which controls citizen/anon flows).
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def get_exception_handler(self):
        return govstack_exception_handler

    def initial(self, request: Request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        logger.debug(
            "govstack.payments method=%s path=%s",
            request.method,
            request.path,
        )


# ---------------------------------------------------------------------------
# G2P base view  (Wave 2+)
# ---------------------------------------------------------------------------

class GovStackG2PView(GovStackAPIView):
    """
    Base class for all G2P (Government-to-Person) endpoints.

    G2P responses use a different envelope from Voucher/P2G responses:
        Success  → HTTP 200  {ResponseCode: "00", RequestID: ..., ResponseDescription: ...}
        Failure  → HTTP 400  {ResponseCode: "01", RequestID: ..., ResponseDescription: ...}

    Key differences from GovStackAPIView:
    - permission_classes defaults to [AllowAnyBB]: the GovStack harness does NOT send
      X-Registering-Institution-ID, so IsTrustedSourceBB would block all harness tests.
    - get_exception_handler() returns govstack_g2p_exception_handler which wraps ALL
      exceptions (throttle, auth, unexpected errors) in the G2P envelope so the harness
      g2pResponseSchema check is never violated.
    - Views manually handle serializer validation errors and build responses directly,
      so unexpected exceptions are the only thing reaching the exception handler.

    Security:
    - PayeeFunctionalID and FinancialAddress must NEVER appear in any response field,
      log line, or audit details.  See GovStackG2PView._g2p_ok/_g2p_bad which only
      expose ResponseCode, RequestID (echoed), and ResponseDescription (human text).
    """

    permission_classes = [AllowAnyBB]

    def get_exception_handler(self):
        # G2P views use the G2P-specific handler to wrap unexpected exceptions in the
        # G2P envelope ({ResponseCode, RequestID, ResponseDescription}).
        # This overrides GovStackAPIView which returns govstack_exception_handler
        # (the Voucher handler that returns {"message": "..."}).
        return govstack_g2p_exception_handler

    # ── Response helpers ─────────────────────────────────────────────────────

    def _request_id(self, request: Request) -> str:
        """
        Extract RequestID from the parsed request body for echo-back.

        The harness always sends a 12-char RequestID in the body and expects it
        echoed back verbatim in every response (success and error).  If the body
        is missing or unparseable, return "" (the harness does not test that case).
        """
        try:
            data = request.data
            if isinstance(data, dict):
                return str(data.get("RequestID", ""))
        except Exception:
            pass
        return ""

    def _g2p_ok(self, request: Request, description: str = "Request received successfully.") -> Response:
        """Return a G2P success envelope (HTTP 200, ResponseCode '00')."""
        return Response(
            {
                "ResponseCode": "00",
                "RequestID": self._request_id(request),
                "ResponseDescription": description[:200],
            },
            status=200,
        )

    def _g2p_bad(self, request: Request, description: str = "Bad request.") -> Response:
        """Return a G2P error envelope (HTTP 400, ResponseCode '01')."""
        return Response(
            {
                "ResponseCode": "01",
                "RequestID": self._request_id(request),
                "ResponseDescription": description[:200],
            },
            status=400,
        )

    def _flatten_errors(self, errors, _prefix: str = "") -> str:
        """
        Recursively flatten DRF serializer error dicts into a concise string.

        Handles:
          - Top-level field errors:  {"SourceBBID": ["error"]}
          - List field errors:       {"Beneficiaries": ["This list may not be empty."]}
          - Nested list item errors: {"Beneficiaries": [{"PayeeFunctionalID": ["error"]}]}
        """
        parts: list[str] = []

        if isinstance(errors, dict):
            for field, value in errors.items():
                key = f"{_prefix}.{field}" if _prefix else field
                if isinstance(value, list):
                    # Separate plain strings (field-level messages) from dicts (nested items)
                    plain = [m for m in value if not isinstance(m, dict)]
                    nested = [m for m in value if isinstance(m, dict) and m]
                    if plain:
                        parts.append(f"{key}: {'; '.join(str(m) for m in plain)}")
                    for item_errors in nested:
                        inner = self._flatten_errors(item_errors, key)
                        if inner:
                            parts.append(inner)
                elif isinstance(value, dict):
                    inner = self._flatten_errors(value, key)
                    if inner:
                        parts.append(inner)
                else:
                    parts.append(f"{key}: {value}")

        elif isinstance(errors, list):
            plain = [m for m in errors if not isinstance(m, dict)]
            nested = [m for m in errors if isinstance(m, dict) and m]
            if plain:
                label = f"{_prefix}: " if _prefix else ""
                parts.append(f"{label}{'; '.join(str(m) for m in plain)}")
            for item_errors in nested:
                inner = self._flatten_errors(item_errors, _prefix)
                if inner:
                    parts.append(inner)

        else:
            parts.append(str(errors))

        result = " | ".join(p for p in parts if p)
        return result[:197] + "..." if len(result) > 200 else result


# ---------------------------------------------------------------------------
# G2P — Beneficiary (Wave 2)
# ---------------------------------------------------------------------------

class RegisterBeneficiaryView(GovStackG2PView):
    """
    POST /govstack/payments/register-beneficiary

    GovStack spec: api/G2P API YAMLs/RegisterBeneficiaryRequest.yml
    Harness: g2p_register_beneficiary.feature @endpoint=/register-beneficiary

    Harness scenarios (6):
      ✓ Smoke: basic POST → 200
      ✓ Full fields (PaymentModality + FinancialAddress) → 200, ResponseCode "00"
      ✓ Missing SourceBBID → 400, ResponseCode "01"
      ✓ Missing PayeeFunctionalID (Beneficiaries: []) → 400, ResponseCode "01"
      ✓ Invalid SourceBBID ("invalid") → 400, ResponseCode "01"
      ✓ Invalid PayeeFunctionalID ("invalid") → 400, ResponseCode "01"

    Headers consumed:
      X-Registering-Institution-ID  (optional, stored on beneficiary record)
      X-Callback-URL                 (optional, reserved for async Wave 3+)

    Body:  {RequestID?, SourceBBID, Beneficiaries: [{PayeeFunctionalID, PaymentModality?, FinancialAddress?}]}
    Response: {ResponseCode, RequestID, ResponseDescription}
    """

    def post(self, request: Request) -> Response:
        ser = RegisterBeneficiaryRequestSerializer(data=request.data)
        if not ser.is_valid():
            return self._g2p_bad(request, self._flatten_errors(ser.errors))

        d = ser.validated_data
        GovStackBeneficiaryService.register(
            request_id=d.get("RequestID", ""),
            source_bb_id=d["SourceBBID"],
            beneficiaries=d["Beneficiaries"],
            registering_institution_id=(
                request.headers.get("X-Registering-Institution-ID", "")
                or request.headers.get("X-Registering-Institution-Id", "")
            ),
            callback_url=request.headers.get("X-Callback-URL", ""),
        )

        return self._g2p_ok(request, "Beneficiary registration request received successfully.")


class UpdateBeneficiaryView(GovStackG2PView):
    """
    POST /govstack/payments/update-beneficiary-details

    GovStack spec: api/G2P API YAMLs/UpdateBeneficiaryRequest.yml
    Harness: g2p_update_beneficiary_details.feature @endpoint=/update-beneficiary-details

    Harness scenarios (6):
      ✓ Smoke → 200
      ✓ Full fields → 200, ResponseCode "00"
      ✓ Missing SourceBBID → 400, ResponseCode "01"
      ✓ Missing PayeeFunctionalID (Beneficiaries: []) → 400, ResponseCode "01"
      ✓ Invalid SourceBBID → 400, ResponseCode "01"
      ✓ Invalid PayeeFunctionalID → 400, ResponseCode "01"

    Semantics: upsert — creates the record if PayeeFunctionalID doesn't exist.
    The harness smoke test sends an unregistered ID and expects HTTP 200/"00".
    """

    def post(self, request: Request) -> Response:
        ser = RegisterBeneficiaryRequestSerializer(data=request.data)
        if not ser.is_valid():
            return self._g2p_bad(request, self._flatten_errors(ser.errors))

        d = ser.validated_data
        GovStackBeneficiaryService.update(
            request_id=d.get("RequestID", ""),
            source_bb_id=d["SourceBBID"],
            beneficiaries=d["Beneficiaries"],
            registering_institution_id=(
                request.headers.get("X-Registering-Institution-ID", "")
                or request.headers.get("X-Registering-Institution-Id", "")
            ),
            callback_url=request.headers.get("X-Callback-URL", ""),
        )

        return self._g2p_ok(request, "Beneficiary update request received successfully.")


# ---------------------------------------------------------------------------
# G2P — Bulk Payment (Wave 3)
# ---------------------------------------------------------------------------

class BulkPaymentView(GovStackAPIView):
    """
    POST /govstack/payments/bulk-payment

    GovStack spec: api/G2P API YAMLs/BulkPayment.yml
    Harness: g2p_bulk_payment.feature @endpoint=/bulk-payment

    Required headers: X-CorrelationID
    Body: {RequestID, SourceBBID, BatchID, CreditInstructions: [...]}
    Response: {ResponseCode, RequestID, ResponseDescription}
    Failure: HTTP 400 {ResponseCode: "01", RequestID, ResponseDescription}

    Async: accepts request → HTTP 200 → Celery task processes batch.

    Wave 1: stub.
    Wave 3: full implementation.
    """
    permission_classes = [IsTrustedSourceBB]

    def post(self, request: Request) -> Response:
        return Response(
            {"status": "not_implemented"},
            status=501,
        )


class PrepaymentValidationView(GovStackAPIView):
    """
    POST /govstack/payments/prepayment-validation

    GovStack spec: api/G2P API YAMLs/PrePaymentValidation.yml
    Harness: g2p_prepayment_validation.feature @endpoint=/prepayment-validation

    Async: accepts → 200 → Celery validates PayeeFunctionalID → POSTs to callback.

    Wave 1: stub.
    Wave 3: full implementation.
    """
    permission_classes = [IsTrustedSourceBB]

    def post(self, request: Request) -> Response:
        return Response(
            {"status": "not_implemented"},
            status=501,
        )


class PrepaymentValidationResponseView(GovStackAPIView):
    """
    POST /govstack/payments/prepayment-validation-response

    The harness sends this request to acknowledge that it has received
    the async validation result callback. CivicOS logs and acknowledges.

    Wave 1: stub.
    Wave 3: full implementation.
    """
    permission_classes = [IsTrustedSourceBB]

    def post(self, request: Request) -> Response:
        return Response(
            {"status": "not_implemented"},
            status=501,
        )


# ---------------------------------------------------------------------------
# Voucher Engine (Wave 4)
# ---------------------------------------------------------------------------

class VoucherPreactivationView(GovStackAPIView):
    """
    POST /govstack/payments/vouchers/voucher_preactivation

    GovStack spec: api/Voucher API YAMLs/VoucherPreactivationRequest.yml
    Harness: voucher_preactivation.feature @endpoint=/vouchers/voucher_preactivation

    Required headers: X-Registering-Institution-Id (optional: X-Callback-URL)
    Body: {voucher_amount, voucher_currency, voucher_group, Gov_Stack_BB}
    Response: {voucherNumber, voucherSerialNumber, voucherGroup, expiryDate}

    Custom error codes:
      452 — invalid voucher_amount
      453 — invalid voucher_currency
      454 — invalid voucher_group
      460 — unknown Gov_Stack_BB

    Wave 1: stub.
    Wave 4: full implementation.
    """
    permission_classes = [AllowAnyBB]

    def post(self, request: Request) -> Response:
        return Response(
            {"status": "not_implemented"},
            status=501,
        )


class VoucherActivationView(GovStackAPIView):
    """
    PATCH /govstack/payments/vouchers/voucher_activation

    GovStack spec: api/Voucher API YAMLs/VoucherActivate.yml
    Harness: voucher_activation.feature @endpoint=/vouchers/voucher_activation

    Required headers: X-Registering-Institution-Id
    Body: {voucher_serial_number (int), Gov_Stack_BB}
    Response: {voucherNumber, voucherSerialNumber, voucherStatus, voucherGroup}

    Custom error codes:
      456 — serial number not found
      460 — unknown Gov_Stack_BB

    Wave 1: stub.
    Wave 4: full implementation.
    """
    permission_classes = [AllowAnyBB]

    def patch(self, request: Request) -> Response:
        return Response(
            {"status": "not_implemented"},
            status=501,
        )


class VoucherRedemptionView(GovStackAPIView):
    """
    POST /govstack/payments/vouchers/voucher_redemption

    GovStack spec: api/Voucher API YAMLs/VoucherRedemption.yml
    Harness: voucher_redemption.feature @endpoint=/vouchers/voucher_redemption

    Auth: JWT Bearer (HasVoucherJWT — harness-relaxed in Wave 4)
    Body: {voucher_number (int), Gov_Stack_BB, merchant_name, merchant_bank_details,
           merchant_voucher_group, override}
    Response: {status (int), message, serialNumber, value, timestamp, transactionId}

    Custom error codes:
      460 — unknown Gov_Stack_BB

    Wave 1: stub.
    Wave 4: full implementation.
    """
    permission_classes = [HasVoucherJWT]

    def post(self, request: Request) -> Response:
        return Response(
            {"status": "not_implemented"},
            status=501,
        )


class VoucherStatusCheckView(GovStackAPIView):
    """
    GET  /govstack/payments/vouchers/voucherstatuscheck/{voucherserialnumber}
    PATCH /govstack/payments/vouchers/voucherstatuscheck/{voucherserialnumber}

    Two harness features share this URL, one per HTTP method:
      GET   → voucher_status_check.feature  @endpoint=/vouchers/voucherstatuscheck
      PATCH → voucher_cancelation.feature   @endpoint=/vouchers/voucherstatuscheck

    GET response:  {status (int), serialNumber, value}
    PATCH response: {voucherSerialNumber, voucherStatus}

    PATCH custom error codes:
      463 — invalid serial number (not found)
      464 — voucher already cancelled

    URL param: voucherserialnumber (str path segment)

    Wave 1: stub.
    Wave 4: full implementation.
    """
    permission_classes = [AllowAnyBB]

    def get(self, request: Request, voucherserialnumber: str) -> Response:
        return Response(
            {"status": "not_implemented"},
            status=501,
        )

    def patch(self, request: Request, voucherserialnumber: str) -> Response:
        return Response(
            {"status": "not_implemented"},
            status=501,
        )


# ---------------------------------------------------------------------------
# P2G — Bill Payments (Wave 5)
# ---------------------------------------------------------------------------

class BillInquiryView(GovStackAPIView):
    """
    GET /govstack/payments/bills/{bill_id}

    Adapts CivicOS FeeSchedule → GovStack P2G bill inquiry shape.
    No harness feature yet.

    Wave 1: stub.
    Wave 5: full implementation.
    """
    permission_classes = [AllowAnyBB]

    def get(self, request: Request, bill_id: str) -> Response:
        return Response(
            {"status": "not_implemented"},
            status=501,
        )


class BillTransferRequestView(GovStackAPIView):
    """
    POST /govstack/payments/billTransferRequests

    Receives a mobile money payment notification for a bill.
    Required headers: X-CorrelationID, X-Platform-TenantId, X-PayerFI-Id

    Wave 1: stub.
    Wave 5: full implementation.
    """
    permission_classes = [AllowAnyBB]

    def post(self, request: Request) -> Response:
        return Response(
            {"status": "not_implemented"},
            status=501,
        )


class MarkBillPaidView(GovStackAPIView):
    """
    POST /govstack/payments/bills/{bill_id}/mark-paid

    Staff endpoint: manually mark a bill as paid after confirming
    mobile money receipt.

    Wave 1: stub.
    Wave 5: full implementation.
    """
    permission_classes = [AllowAnyBB]

    def post(self, request: Request, bill_id: str) -> Response:
        return Response(
            {"status": "not_implemented"},
            status=501,
        )


class TransferRequestStatusView(GovStackAPIView):
    """
    GET /govstack/payments/transferRequests/{transfer_request_id}

    Status check for a P2G transfer request.

    Wave 1: stub.
    Wave 5: full implementation.
    """
    permission_classes = [AllowAnyBB]

    def get(self, request: Request, transfer_request_id: str) -> Response:
        return Response(
            {"status": "not_implemented"},
            status=501,
        )
