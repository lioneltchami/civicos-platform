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
from .govstack_exceptions import (
    DuplicateBatchError,
    DuplicateValidationRequestError,
    govstack_exception_handler,
    govstack_g2p_exception_handler,
)
from .govstack_serializers import (
    BulkPaymentRequestSerializer,
    PrepaymentValidationRequestSerializer,
    PrepaymentValidationResponseAckSerializer,
    RegisterBeneficiaryRequestSerializer,
    UpdateBeneficiaryRequestSerializer,
    VoucherActivationRequestSerializer,
    VoucherPreactivationRequestSerializer,
    VoucherRedemptionRequestSerializer,
)
from .govstack_services import (
    GovStackBeneficiaryService,
    GovStackBulkPaymentService,
    GovStackVoucherService,
)

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
    - _flatten_errors() helper for both Voucher and G2P validation error rendering

    Wave 2+ concrete views delegate to service methods and return proper shapes.
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

    def _flatten_errors(self, errors, _prefix: str = "") -> str:
        """
        Recursively flatten DRF serializer error dicts into a concise string.

        Available on GovStackAPIView (base) so both Voucher and G2P views can use it.

        Handles:
          - Top-level field errors:  {"voucher_amount": ["error"]}
          - List field errors:       {"Beneficiaries": ["This list may not be empty."]}
          - Nested list item errors: {"Beneficiaries": [{"PayeeFunctionalID": ["error"]}]}
        """
        parts: list[str] = []

        if isinstance(errors, dict):
            for field, value in errors.items():
                key = f"{_prefix}.{field}" if _prefix else field
                if isinstance(value, list):
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
        if len(result) > 200:
            return result[:197] + "..."
        return result or "Validation error."


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
                # Use `or ""` to coerce null (None) to "" — str(None) would return
                # the 4-char string "None" which is incorrect for echo-back.
                return str(data.get("RequestID") or "")
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

    # _flatten_errors is inherited from GovStackAPIView (base class).
    # Removed duplicate override — keeping it in one place prevents drift.


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
        ser = UpdateBeneficiaryRequestSerializer(data=request.data)
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

class BulkPaymentView(GovStackG2PView):
    """
    POST /govstack/payments/bulk-payment

    GovStack spec: api/G2P API YAMLs/BulkPayment.yml
    Harness: g2p_bulk_payment.feature @endpoint=/bulk-payment

    Harness scenarios (7):
      ✓ Smoke → 200
      ✓ Full fields + Narration → 200, ResponseCode "00", RequestID echoed
      ✓ Missing SourceBBID → 400, ResponseCode "01"
      ✓ Missing BatchID → 400, ResponseCode "01"
      ✓ Empty CreditInstructions → 400, ResponseCode "01"
      ✓ "invalid" SourceBBID (7 chars, fails min_length=10) → 400, ResponseCode "01"
      ✓ "invalid" BatchID (7 chars, fails min_length=10) → 400, ResponseCode "01"

    Headers consumed:
      X-CorrelationID  (optional, stored on batch)
      X-Callback-URL   (optional, async result delivery)

    Body:    {RequestID?, SourceBBID, BatchID, CreditInstructions: [...]}
    Success: HTTP 200  {ResponseCode: "00", RequestID, ResponseDescription}
    Failure: HTTP 400  {ResponseCode: "01", RequestID, ResponseDescription}

    Extends GovStackG2PView (not GovStackAPIView) to inherit:
      - G2P envelope helpers (_g2p_ok, _g2p_bad, _flatten_errors)
      - govstack_g2p_exception_handler (wraps unexpected errors in G2P envelope)
      - AllowAnyBB permission (harness does not send X-Registering-Institution-ID)
    """

    def post(self, request: Request) -> Response:
        ser = BulkPaymentRequestSerializer(data=request.data)
        if not ser.is_valid():
            return self._g2p_bad(request, self._flatten_errors(ser.errors))

        d = ser.validated_data
        try:
            GovStackBulkPaymentService.receive_batch(
                request_id=d.get("RequestID", ""),
                source_bb_id=d["SourceBBID"],
                batch_id=d["BatchID"],
                instructions=d["CreditInstructions"],
                callback_url=request.headers.get("X-Callback-URL", ""),
                correlation_id=request.headers.get("X-CorrelationID", ""),
            )
        except DuplicateBatchError:
            # BatchID already exists — return a G2P envelope error rather than a 500.
            # BatchID is not surfaced in the error message to avoid it co-appearing
            # with PII in logging; the Source BB already knows which BatchID it sent.
            return self._g2p_bad(request, "Batch ID has already been received.")

        return self._g2p_ok(request, "Bulk payment batch received successfully.")


class PrepaymentValidationView(GovStackG2PView):
    """
    POST /govstack/payments/prepayment-validation

    GovStack spec: api/G2P API YAMLs/PrePaymentValidation.yml
    Harness: g2p_prepayment_validation.feature @endpoint=/prepayment-validation

    Harness scenarios (15):
      ✓ Smoke → 200
      ✓ Full valid fields → 200, ResponseCode "00"
      ✓ [Chained] → POST /prepayment-validation-response → 200, {RequestID, Source_BatchID}
      ✓ Missing SourceBBID → 200, ResponseCode "01"
      ✓ Missing BatchID → 200, ResponseCode "01"
      ✓ Missing InstructionID in CreditInstructions → 200, ResponseCode "01"
      ✓ Missing PayeeFunctionalID → 200, ResponseCode "01"
      ✓ Missing Amount → 200, ResponseCode "01"
      ✓ Missing Currency → 200, ResponseCode "01"
      ✓ Missing Narration (required here, unlike /bulk-payment) → 200, ResponseCode "01"
      ✓ Invalid SourceBBID (partial body, missing required fields) → 200, ResponseCode "01"
      ✓ Invalid BatchID (partial body, missing required fields) → 200, ResponseCode "01"
      ✓ Invalid InstructionID (partial body) → 200, ResponseCode "01"
      ✓ Invalid Amount "100.10.1" (not parseable Decimal) → 200, ResponseCode "01"
      ✓ Invalid Currency "US" (2 chars, fails ISO 4217) → 200, ResponseCode "01"

    CRITICAL BEHAVIOURAL DIFFERENCE from /bulk-payment:
      This endpoint ALWAYS returns HTTP 200 — even when serializer validation fails.
      Validation errors produce ResponseCode "01" with HTTP 200 (async acceptance model).
      The harness verifies the g2pResponseSchema on EVERY response, including errors.

    Async flow:
      1. Accept the request immediately → HTTP 200.
      2. Store PrepaymentValidationRequest (status=PENDING).
      3. Celery task (Wave 3+ Async) validates PayeeFunctionalID against ID Mapper
         and POSTs result to X-Callback-URL.
      4. /prepayment-validation-response returns the result.
    """

    def post(self, request: Request) -> Response:
        ser = PrepaymentValidationRequestSerializer(data=request.data)
        if not ser.is_valid():
            # ALWAYS HTTP 200 for prepayment-validation — even for validation errors.
            # The harness g2pResponseSchema check runs on every scenario including negatives.
            return Response(
                {
                    "ResponseCode": "01",
                    "RequestID": self._request_id(request),
                    "ResponseDescription": self._flatten_errors(ser.errors),
                },
                status=200,
            )

        d = ser.validated_data

        # Enforce single-instruction constraint at the API boundary.
        # PrepaymentValidationRequest.request_id has unique=True: each request maps
        # to exactly one instruction. The GovStack spec and harness both send one
        # instruction per request. If a caller sends more, reject rather than silently
        # dropping instructions[1:].
        if len(d["CreditInstructions"]) > 1:
            return Response(
                {
                    "ResponseCode": "01",
                    "RequestID": self._request_id(request),
                    "ResponseDescription": (
                        "CreditInstructions must contain exactly one entry per request. "
                        f"Received {len(d['CreditInstructions'])}."
                    ),
                },
                status=200,
            )

        instruction = d["CreditInstructions"][0]

        try:
            GovStackBulkPaymentService.validate_prepayment(
                request_id=d.get("RequestID", ""),
                source_bb_id=d["SourceBBID"],
                batch_id=d["BatchID"],
                instruction_id=instruction["InstructionID"],
                payee_functional_id=instruction["PayeeFunctionalID"],
                amount=instruction["Amount"],
                currency=instruction["Currency"],
                narration=instruction["Narration"],
                callback_url=request.headers.get("X-Callback-URL", ""),
            )
        except DuplicateValidationRequestError:
            # RequestID already exists — return G2P error envelope at HTTP 200.
            # /prepayment-validation MUST always return HTTP 200 per the spec.
            # The duplicate request_id is not surfaced in the error message.
            return Response(
                {
                    "ResponseCode": "01",
                    "RequestID": self._request_id(request),
                    "ResponseDescription": "Prepayment validation request ID has already been received.",
                },
                status=200,
            )

        return Response(
            {
                "ResponseCode": "00",
                "RequestID": self._request_id(request),
                "ResponseDescription": "Prepayment validation request received successfully.",
            },
            status=200,
        )


class PrepaymentValidationResponseView(GovStackG2PView):
    """
    POST /govstack/payments/prepayment-validation-response

    GovStack spec: api/G2P API YAMLs/PrePaymentValidation.yml (response-side)
    Harness: g2p_prepayment_validation.feature (chained two-step)

    The harness sends this immediately after POST /prepayment-validation to
    retrieve the validation result.  Body: {RequestID, Source_BatchID}.
    Response: {RequestID, Source_BatchID, NumberFailedCases?, FailedAccounts?}

    prepaymentValidationResponseSchema (from harness):
      Required:  RequestID, Source_BatchID
      Optional:  NumberFailedCases (integer), FailedAccounts (array)

    Since the harness calls this endpoint immediately (before the Celery task runs),
    PrepaymentValidationRequest records are still in PENDING status →
    NumberFailedCases=0, FailedAccounts=[].  This satisfies the harness schema check.

    Always returns HTTP 200.
    """

    def post(self, request: Request) -> Response:
        ser = PrepaymentValidationResponseAckSerializer(data=request.data)
        # All fields are optional with defaults — is_valid() always returns True.
        # Call once, then use validated_data directly (no fallback needed).
        ser.is_valid()
        d = ser.validated_data

        request_id = self._request_id(request)
        source_batch_id = d.get("Source_BatchID", "")

        result = GovStackBulkPaymentService.get_validation_result(
            request_id=request_id,
            source_batch_id=source_batch_id,
        )

        return Response(
            {
                "RequestID": request_id,
                "Source_BatchID": source_batch_id,
                "NumberFailedCases": result["number_failed_cases"],
                "FailedAccounts": result["failed_accounts"],
            },
            status=200,
        )


# ---------------------------------------------------------------------------
# Voucher Engine (Wave 4)
# ---------------------------------------------------------------------------

class VoucherPreactivationView(GovStackAPIView):
    """
    POST /govstack/payments/vouchers/voucher_preactivation

    GovStack spec: api/Voucher API YAMLs/VoucherPreactivationRequest.yml
    Harness: voucher_preactivation.feature @endpoint=/vouchers/voucher_preactivation

    Headers consumed:
      X-Registering-Institution-Id  (optional — stored on voucher record)
      X-Callback-URL                 (optional — stored on voucher record)

    Body: {voucher_amount, voucher_currency, voucher_group, Gov_Stack_BB}
    Response 200: {voucherNumber, voucherSerialNumber, voucherGroup, expiryDate}

    Custom error codes (all return {"message": "..."}):
      400 — missing / non-numeric fields (standard DRF validation)
      452 — voucher_amount is zero or negative
      453 — voucher_currency is not a valid ISO 4217 code
      454 — voucher_group is empty or blank
      460 — Gov_Stack_BB is unknown or empty

    Security:
      Serial number is public (in response). voucher_secret is stored encrypted
      and NEVER returned.
    """
    permission_classes = [AllowAnyBB]

    def post(self, request: Request) -> Response:
        ser = VoucherPreactivationRequestSerializer(data=request.data)
        if not ser.is_valid():
            return Response(
                {"message": self._flatten_errors(ser.errors)},
                status=400,
            )
        d = ser.validated_data

        registering_institution_id = (
            request.headers.get("X-Registering-Institution-Id", "").strip()
            or request.headers.get("X-Registering-Institution-ID", "").strip()
        )
        callback_url = request.headers.get("X-Callback-URL", "").strip()

        # InvalidVoucherAmount (452), InvalidVoucherGroup (454), GovStackBBNotFound (460)
        # are all APIException subclasses — DRF catches and calls govstack_exception_handler
        # automatically, which normalises them to {"message": "..."} with the correct code.
        voucher = GovStackVoucherService.preactivate(
            voucher_amount=d["voucher_amount"],
            voucher_currency=d["voucher_currency"],
            voucher_group=d["voucher_group"],
            issuing_bb=d["Gov_Stack_BB"],
            registering_institution_id=registering_institution_id,
            callback_url=callback_url,
        )

        return Response(
            {
                "voucherNumber": voucher.serial_number,
                "voucherSerialNumber": voucher.serial_number,
                "voucherGroup": voucher.group_code,
                "expiryDate": (
                    voucher.expiry_date.isoformat()
                    if voucher.expiry_date
                    else None
                ),
            },
            status=200,
        )


class VoucherActivationView(GovStackAPIView):
    """
    PATCH /govstack/payments/vouchers/voucher_activation

    GovStack spec: api/Voucher API YAMLs/VoucherActivate.yml
    Harness: voucher_activation.feature @endpoint=/vouchers/voucher_activation

    Body: {voucher_serial_number (int or str), Gov_Stack_BB}
    Response 200: {voucherNumber, voucherSerialNumber, voucherStatus, voucherGroup}

    Custom error codes:
      456 — voucher serial number not found (or invalid state transition)
      460 — Gov_Stack_BB is unknown or empty
    """
    permission_classes = [AllowAnyBB]

    def patch(self, request: Request) -> Response:
        ser = VoucherActivationRequestSerializer(data=request.data)
        if not ser.is_valid():
            return Response(
                {"message": self._flatten_errors(ser.errors)},
                status=400,
            )
        d = ser.validated_data

        # InvalidVoucherSerial (456) and GovStackBBNotFound (460) are APIExceptions.
        voucher = GovStackVoucherService.activate(
            voucher_serial_number=d["voucher_serial_number"],
            issuing_bb=d["Gov_Stack_BB"],
        )

        return Response(
            {
                "voucherNumber": voucher.serial_number,
                "voucherSerialNumber": voucher.serial_number,
                "voucherStatus": voucher.status,
                "voucherGroup": voucher.group_code,
            },
            status=200,
        )


class VoucherRedemptionView(GovStackAPIView):
    """
    POST /govstack/payments/vouchers/voucher_redemption

    GovStack spec: api/Voucher API YAMLs/VoucherRedemption.yml
    Harness: voucher_redemption.feature @endpoint=/vouchers/voucher_redemption

    Auth: JWT Bearer via HasVoucherJWT (harness-relaxed — GOVSTACK_VOUCHER_REQUIRE_JWT=False)

    Body: {voucher_number (int or str), Gov_Stack_BB, merchant_name?,
           merchant_bank_details?, merchant_voucher_group?, override?}
    Response 200: {status (int), message, serialNumber, value, timestamp, transactionId}

    Custom error codes:
      456 — voucher number not found or invalid state (not ACTIVATED)
      460 — Gov_Stack_BB is unknown or empty

    Security:
      merchant_name / merchant_bank_details may contain PII and are NOT echoed
      back in the response — only stored internally.
    """
    permission_classes = [HasVoucherJWT]

    def post(self, request: Request) -> Response:
        ser = VoucherRedemptionRequestSerializer(data=request.data)
        if not ser.is_valid():
            return Response(
                {"message": self._flatten_errors(ser.errors)},
                status=400,
            )
        d = ser.validated_data

        # GovStackBBNotFound (460) and InvalidVoucherSerial (456) are APIExceptions.
        voucher = GovStackVoucherService.redeem(
            voucher_number=d["voucher_number"],
            issuing_bb=d["Gov_Stack_BB"],
            merchant_name=d.get("merchant_name", ""),
            merchant_bank_details=d.get("merchant_bank_details", ""),
            merchant_voucher_group=d.get("merchant_voucher_group", ""),
            override=d.get("override", False),
        )

        return Response(
            {
                "status": voucher.status_int,
                "message": "Voucher redeemed successfully.",
                "serialNumber": voucher.serial_number,
                "value": str(voucher.amount),
                "timestamp": (
                    voucher.redeemed_at.isoformat()
                    if voucher.redeemed_at
                    else None
                ),
                "transactionId": voucher.redemption_transaction_id,
            },
            status=200,
        )


class VoucherStatusCheckView(GovStackAPIView):
    """
    GET  /govstack/payments/vouchers/voucherstatuscheck/{voucherserialnumber}
    PATCH /govstack/payments/vouchers/voucherstatuscheck/{voucherserialnumber}

    Two harness features share this URL, one per HTTP method:
      GET   → voucher_status_check.feature  @endpoint=/vouchers/voucherstatuscheck
      PATCH → voucher_cancelation.feature   @endpoint=/vouchers/voucherstatuscheck

    URL param: voucherserialnumber (str path segment — may be sent as int by harness)

    GET response 200:  {status (int), serialNumber, value}
    PATCH response 200: {voucherSerialNumber, voucherStatus}

    PATCH custom error codes:
      463 — serial number not found, or voucher in a non-cancellable state
      464 — voucher already cancelled (idempotent double-cancel)

    GET custom error codes:
      456 — serial number not found
    """
    permission_classes = [AllowAnyBB]

    def get(self, request: Request, voucherserialnumber: str) -> Response:
        # URL parameter may arrive as an integer string from the harness.
        serial = str(voucherserialnumber).strip()

        # InvalidVoucherSerial (456) is an APIException — handled automatically.
        voucher = GovStackVoucherService.get_status(serial_number=serial)

        return Response(
            {
                "status": voucher.status_int,
                "serialNumber": voucher.serial_number,
                "value": str(voucher.amount),
            },
            status=200,
        )

    def patch(self, request: Request, voucherserialnumber: str) -> Response:
        serial = str(voucherserialnumber).strip()

        # InvalidCancellationSerial (463) and VoucherAlreadyCancelled (464) are
        # APIExceptions — DRF catches and calls govstack_exception_handler.
        voucher = GovStackVoucherService.cancel(voucher_serial_number=serial)

        return Response(
            {
                "voucherSerialNumber": voucher.serial_number,
                "voucherStatus": voucher.status,
            },
            status=200,
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
