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
from .govstack_exceptions import govstack_exception_handler

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
# G2P — Beneficiary (Wave 2)
# ---------------------------------------------------------------------------

class RegisterBeneficiaryView(GovStackAPIView):
    """
    POST /govstack/payments/register-beneficiary

    GovStack spec: api/G2P API YAMLs/RegisterBeneficiaryRequest.yml
    Harness: g2p_register_beneficiary.feature @endpoint=/register-beneficiary

    Required headers: X-Callback-URL, X-Registering-Institution-ID
    Body: {RequestID, SourceBBID, Beneficiaries: [{PayeeFunctionalID, ...}]}
    Response: {ResponseCode, RequestID, ResponseDescription}

    Wave 1: stub.
    Wave 2: full implementation.
    """
    permission_classes = [IsTrustedSourceBB]

    def post(self, request: Request) -> Response:
        return Response(
            {"status": "not_implemented"},
            status=501,
        )


class UpdateBeneficiaryView(GovStackAPIView):
    """
    POST /govstack/payments/update-beneficiary-details

    GovStack spec: api/G2P API YAMLs/UpdateBeneficiaryRequest.yml
    Harness: g2p_update_beneficiary_details.feature @endpoint=/update-beneficiary-details

    Required headers: X-Callback-URL, X-Registering-Institution-ID
    Body: {RequestID, SourceBBID, Beneficiaries: [{PayeeFunctionalID, ...}]}
    Response: {ResponseCode, RequestID, ResponseDescription}

    Wave 1: stub.
    Wave 2: full implementation.
    """
    permission_classes = [IsTrustedSourceBB]

    def post(self, request: Request) -> Response:
        return Response(
            {"status": "not_implemented"},
            status=501,
        )


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
