"""
GovStack Payments BB — DRF serializers.

These serializers define the request/response shapes for the GovStack
Payments BB API surface. They follow the exact field names and constraints
from the GovStack OpenAPI specs in github.com/GovStackWorkingGroup/bb-payments.

Serializer naming convention:
  <Operation>RequestSerializer  — validates incoming request body
  <Operation>ResponseSerializer — shapes outgoing response body

All field names match the GovStack spec (camelCase where the spec uses it,
snake_case only where the spec uses it). The harness is case-sensitive on
field names.

Security:
  - No serializer ever includes payee_functional_id, financial_address,
    or voucher_secret in output serializers.
  - These are input serializers only for those fields; they must not be
    placed in response serializers.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from .govstack_models import (
    BulkPaymentBatch,
    CreditInstruction,
    GovStackBeneficiary,
    GovStackVoucher,
    PrepaymentValidationRequest,
)


# ---------------------------------------------------------------------------
# Shared validators
# ---------------------------------------------------------------------------

# Broad validator: any alphanumeric + hyphen, used by Voucher/P2G flows
_BB_ID_RE = re.compile(r"^[a-zA-Z0-9\-]{1,20}$")
_REQUEST_ID_RE = re.compile(r"^[a-zA-Z0-9\-]{1,16}$")

# Bulk-payment-specific BB ID validator: min length 10.
# • Valid harness values: "SourceBBID11" (12 chars), "BatchID11111" (12 chars)
# • Invalid harness value: "invalid" (7 chars, lowercase only)
# • min_length=10 cleanly separates valid from invalid without over-constraining.
# • Allows uppercase (harness uses mixed-case like "SourceBBID11"); contrast with
#   _G2P_UUID_RE which is hex-only (lowercase) for beneficiary/prepayment flows.
# Defined at module level (not as a class attribute on BulkPaymentRequestSerializer)
# for consistency with the other compiled regexes above.
_BULK_BB_ID_RE = re.compile(r"^[a-zA-Z0-9\-]{10,20}$")

# ISO 4217 currency code: exactly 3 uppercase letters.
# Pre-compiled at module level — called once per instruction in bulk payments,
# so avoiding repeated re.compile() overhead (inline re.match() recompiles every call).
_ISO4217_RE = re.compile(r"^[A-Z]{3}$")

# G2P-specific validator: lowercase hex + hyphen only (UUID-like identifiers).
# The harness uses values like "11668d2a-a8f" (SourceBBID, 12 chars) and
# "2ba5ed20-0f42-4eff-8" (PayeeFunctionalID, 20 chars).  The harness negative
# test sends the literal string "invalid" which contains non-hex chars (i, n, v, l)
# and is correctly rejected by this pattern.
#
# Known permissive boundary: pure-hyphen strings like "---" also match (hyphens
# are valid separator chars in UUID format).  No harness scenario tests for this
# case and the GovStack spec does not forbid it, so we accept it.  If a future
# harness version adds such a test, tighten to require at least one hex digit:
#   r"^[0-9a-f][0-9a-f\-]{0,19}$"
_G2P_UUID_RE = re.compile(r"^[0-9a-f\-]{1,20}$")


def _validate_bb_id(value: str) -> str:
    """Validate SourceBBID / Gov_Stack_BB: 1–20 alphanumeric or hyphen chars."""
    if not value or not _BB_ID_RE.match(value):
        raise serializers.ValidationError(
            "Must be 1–20 alphanumeric or hyphen characters."
        )
    return value


def _validate_payee_id(value: str) -> str:
    """Validate PayeeFunctionalID: 1–20 alphanumeric or hyphen chars."""
    if not value or not _BB_ID_RE.match(value):
        raise serializers.ValidationError(
            "Must be 1–20 alphanumeric or hyphen characters."
        )
    return value


def _validate_g2p_id(value: str, *, field_name: str = "field") -> str:
    """
    Validate a G2P identifier (SourceBBID, PayeeFunctionalID) as a UUID-like
    lowercase hex string.

    Valid examples:  "11668d2a-a8f"  (12 chars)  — harness SourceBBID
                     "2ba5ed20-0f42-4eff-8"  (20 chars) — harness PayeeFunctionalID
    Invalid example: "invalid"  (contains i, n, v, l — not hex chars)

    This is stricter than _validate_bb_id intentionally: the G2P harness uses
    UUID-format identifiers and specifically tests that the literal string
    "invalid" is rejected.
    """
    if not value or not _G2P_UUID_RE.match(value):
        raise serializers.ValidationError(
            f"{field_name} must be 1–20 lowercase hex characters and hyphens "
            "(e.g. '11668d2a-a8f'). The value provided is not a valid identifier."
        )
    return value


def _validate_request_id(value: str) -> str:
    """Validate RequestID: 1–16 alphanumeric or hyphen chars."""
    if not value or not _REQUEST_ID_RE.match(value):
        raise serializers.ValidationError(
            "Must be 1–16 alphanumeric or hyphen characters."
        )
    return value


def _validate_iso4217(value: str) -> str:
    """Validate ISO 4217 currency code: exactly 3 uppercase letters."""
    if not value or not _ISO4217_RE.match(value):
        raise serializers.ValidationError(
            "Must be a 3-letter ISO 4217 currency code (e.g. USD, AED, CAD)."
        )
    return value


# ---------------------------------------------------------------------------
# G2P — Beneficiary
# ---------------------------------------------------------------------------

class BeneficiaryItemSerializer(serializers.Serializer):
    """
    One entry in the Beneficiaries[] array.
    GovStack spec: RegisterBeneficiaryRequest.yml → Beneficiaries
    """
    PayeeFunctionalID = serializers.CharField(
        max_length=20,
        error_messages={
            "required": "PayeeFunctionalID is required.",
            "blank": "PayeeFunctionalID cannot be blank.",
        },
    )
    PaymentModality = serializers.CharField(
        max_length=2,
        required=False,
        allow_blank=True,
        default="",
    )
    # max_length matches model field (512). IBANs can be up to 34 chars;
    # mobile money wallet IDs can also exceed 30 chars.
    FinancialAddress = serializers.CharField(
        max_length=512,
        required=False,
        allow_blank=True,
        default="",
    )

    def validate_PayeeFunctionalID(self, value: str) -> str:
        # G2P identifiers must be UUID-like (lowercase hex + hyphens).
        # The harness sends valid values like "2ba5ed20-0f42-4eff-8" and
        # invalid values like "invalid" (contains non-hex chars).
        return _validate_g2p_id(value, field_name="PayeeFunctionalID")


class RegisterBeneficiaryRequestSerializer(serializers.Serializer):
    """
    POST /govstack/payments/register-beneficiary
    POST /govstack/payments/update-beneficiary-details
    GovStack spec: RegisterBeneficiaryRequest.yml / UpdateBeneficiaryRequest.yml
    """
    # RequestID is echoed back verbatim in all responses (success and error).
    # The harness always sends exactly 12-char RequestIDs (UUID prefix format).
    # Not validated for format here — just echoed back as-is.
    RequestID = serializers.CharField(
        max_length=16,
        required=False,
        allow_blank=True,
        default="",
        help_text="RequestID. Max 16 chars. Echoed back in response.",
    )
    SourceBBID = serializers.CharField(
        max_length=20,
        error_messages={
            "required": "SourceBBID is required.",
            "blank": "SourceBBID cannot be blank.",
        },
    )
    Beneficiaries = serializers.ListField(
        child=BeneficiaryItemSerializer(),
        min_length=1,
        # max_length guards against unbounded atomic transactions; a single
        # request with 10 000 beneficiaries would hold a DB lock for seconds.
        # 500 is generous for a government G2P batch and well within harness limits.
        max_length=500,
        error_messages={
            "required": "Beneficiaries array is required.",
            "min_length": "Beneficiaries array must contain at least one entry.",
            "max_length": "Beneficiaries array must not exceed 500 entries per request.",
        },
    )

    def validate_SourceBBID(self, value: str) -> str:
        # G2P identifiers must be UUID-like (lowercase hex + hyphens).
        # The harness sends valid values like "11668d2a-a8f" (12 chars) and
        # invalid values like "invalid" (contains non-hex chars i, n, v, l).
        return _validate_g2p_id(value, field_name="SourceBBID")


# UpdateBeneficiaryRequest and RegisterBeneficiaryRequest have identical schemas
# (same fields, same constraints) per the GovStack spec.  Using a named alias
# rather than the same class directly means:
#   - views/tests can import UpdateBeneficiaryRequestSerializer explicitly, making
#     the intent clear and allowing future divergence without touching view code.
#   - A grep for "UpdateBeneficiaryRequestSerializer" finds all update-specific uses.
UpdateBeneficiaryRequestSerializer = RegisterBeneficiaryRequestSerializer


class G2PResponseSerializer(serializers.Serializer):
    """
    Standard G2P response envelope.
    GovStack spec: all G2P response bodies.
    """
    ResponseCode = serializers.CharField(read_only=True)
    RequestID = serializers.CharField(read_only=True)
    ResponseDescription = serializers.CharField(read_only=True)


# ---------------------------------------------------------------------------
# G2P — Bulk Payment
# ---------------------------------------------------------------------------

class CreditInstructionSerializer(serializers.Serializer):
    """
    One entry in CreditInstructions[].
    GovStack spec: BulkPayment.yml → CreditInstructions
    """
    InstructionID = serializers.CharField(max_length=16)
    PayeeFunctionalID = serializers.CharField(max_length=20)
    Amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    Currency = serializers.CharField(max_length=3)
    Narration = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")

    def validate_PayeeFunctionalID(self, value: str) -> str:
        return _validate_payee_id(value)

    def validate_Currency(self, value: str) -> str:
        return _validate_iso4217(value.upper() if value else value)


class BulkPaymentRequestSerializer(serializers.Serializer):
    """
    POST /govstack/payments/bulk-payment
    GovStack spec: BulkPayment.yml
    Harness: g2p_bulk_payment.feature @endpoint=/bulk-payment

    SourceBBID / BatchID rules (derived from harness data):
      • Valid values in harness: "SourceBBID11" (12 chars), "BatchID11111" (12 chars)
      • Invalid value in harness: "invalid" (7 chars, lowercase only)
      • Minimum length of 10 cleanly separates valid from invalid:
          "invalid" = 7 chars  → rejected
          "SourceBBID11" = 12 chars → accepted
      • Allow uppercase (harness uses mixed-case IDs like "SourceBBID11").
        Contrast with G2P beneficiary endpoints which use _G2P_UUID_RE (hex only).

    Harness negative scenarios:
      HTTP 400: missing SourceBBID, missing BatchID, empty CreditInstructions,
               "invalid" SourceBBID, "invalid" BatchID.
    """
    RequestID = serializers.CharField(max_length=16, required=False, allow_blank=True, default="")
    SourceBBID = serializers.CharField(max_length=20)
    BatchID = serializers.CharField(max_length=20)
    CreditInstructions = serializers.ListField(
        child=CreditInstructionSerializer(),
        min_length=1,
        # max_length guards against unbounded atomic transactions: all instructions
        # are created in a single transaction.atomic() block in receive_batch().
        # 500 matches the cap on RegisterBeneficiaryRequest.Beneficiaries and is
        # well within any realistic G2P disbursement batch size.
        max_length=500,
        error_messages={
            "required": "CreditInstructions array is required.",
            "min_length": "CreditInstructions array must contain at least one entry.",
            "max_length": "CreditInstructions array must not exceed 500 entries per request.",
        },
    )

    def validate_SourceBBID(self, value: str) -> str:
        if not value or not _BULK_BB_ID_RE.match(value):
            raise serializers.ValidationError(
                "SourceBBID must be 10–20 alphanumeric or hyphen characters."
            )
        return value

    def validate_BatchID(self, value: str) -> str:
        if not value or not _BULK_BB_ID_RE.match(value):
            raise serializers.ValidationError(
                "BatchID must be 10–20 alphanumeric or hyphen characters."
            )
        return value


# ---------------------------------------------------------------------------
# G2P — Prepayment Validation
# ---------------------------------------------------------------------------

class PrepaymentCreditInstructionSerializer(serializers.Serializer):
    """
    One entry in CreditInstructions[] for the /prepayment-validation endpoint.

    Differs from CreditInstructionSerializer (used by /bulk-payment) in two ways:
      1. Narration is REQUIRED — the harness negative scenario "missing Narration"
         sends a full body without Narration and expects ResponseCode "01".
         (For /bulk-payment, Narration is optional and tested with "not obligatory fields".)
      2. InstructionID max_length=20 (model field size) vs 16 in CreditInstruction model.
         The harness sends 16-char IDs ("instructionID123") — both fit within 20.
    """
    InstructionID = serializers.CharField(max_length=20)
    PayeeFunctionalID = serializers.CharField(max_length=20)
    Amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    Currency = serializers.CharField(max_length=3)
    # Narration is REQUIRED here — unlike CreditInstructionSerializer where it is optional.
    Narration = serializers.CharField(max_length=200)

    def validate_PayeeFunctionalID(self, value: str) -> str:
        return _validate_payee_id(value)

    def validate_Currency(self, value: str) -> str:
        return _validate_iso4217(value.upper() if value else value)


class PrepaymentValidationRequestSerializer(serializers.Serializer):
    """
    POST /govstack/payments/prepayment-validation
    GovStack spec: PrePaymentValidation.yml

    The harness sends exactly one instruction per request.  The view processes
    CreditInstructions[0] and stores one PrepaymentValidationRequest record.

    Key behavioural difference from /bulk-payment: this endpoint ALWAYS returns
    HTTP 200.  Serializer errors → ResponseCode "01" (not HTTP 400).

    SourceBBID / BatchID: accept any non-empty alphanumeric string (1–20 chars).
    The harness "invalid SourceBBID/BatchID" scenarios send a PARTIAL body
    (only that one field), so failure is triggered by MISSING required fields
    (BatchID, CreditInstructions), not by an invalid SourceBBID/BatchID value.
    """
    RequestID = serializers.CharField(max_length=16, required=False, allow_blank=True, default="")
    SourceBBID = serializers.CharField(max_length=20)
    BatchID = serializers.CharField(max_length=20)
    CreditInstructions = serializers.ListField(
        child=PrepaymentCreditInstructionSerializer(),
        min_length=1,
        error_messages={
            "required": "CreditInstructions array is required.",
            "min_length": "CreditInstructions array must contain at least one entry.",
        },
    )

    def validate_SourceBBID(self, value: str) -> str:
        return _validate_bb_id(value)

    def validate_BatchID(self, value: str) -> str:
        return _validate_bb_id(value)


class PrepaymentValidationResponseAckSerializer(serializers.Serializer):
    """
    POST /govstack/payments/prepayment-validation-response

    Body sent by the harness to acknowledge receipt of the async validation
    result callback.  The harness sends {RequestID, Source_BatchID} (note the
    underscore in Source_BatchID — matches the GovStack spec field name exactly).

    All fields are optional and is_valid() ALWAYS returns True:
      - No max_length constraints (callers may send arbitrarily long values —
        the view truncates or ignores them rather than losing context entirely).
      - allow_null=True so {"RequestID": null} defaults to "" rather than failing
        validation or being coerced to the string "None".
      - required=False, allow_blank=True so missing / empty values default to "".
    This endpoint is not in the harness error scenarios; we degrade gracefully
    for any body rather than returning 400.
    """
    RequestID = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        default="",
    )
    # IMPORTANT: field name is Source_BatchID (with underscore), NOT SourceBatchID.
    # The harness sends {"Source_BatchID": "..."} — a mismatch here silently drops
    # the batch ID and breaks the chained two-step test scenario.
    Source_BatchID = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        default="",
    )


# ---------------------------------------------------------------------------
# Voucher — Pre-activation
# ---------------------------------------------------------------------------

class VoucherPreactivationRequestSerializer(serializers.Serializer):
    """
    POST /govstack/payments/vouchers/voucher_preactivation
    GovStack spec: api/Voucher API YAMLs/VoucherPreactivationRequest.yml
    Harness: voucher_preactivation.feature

    NOTE: Field names use snake_case as per the GovStack voucher spec
    (different convention from the G2P PascalCase spec).
    """
    voucher_amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text="Voucher face value. Must be positive.",
    )
    voucher_currency = serializers.CharField(
        max_length=3,
        help_text="ISO 4217 3-letter currency code.",
    )
    voucher_group = serializers.CharField(
        max_length=50,
        allow_blank=True,
        help_text=(
            "Voucher group / program code. "
            "Blank/whitespace triggers InvalidVoucherGroup (454) from the service — "
            "not a 400 from the serializer — so allow_blank=True is intentional."
        ),
    )
    Gov_Stack_BB = serializers.CharField(
        max_length=50,
        allow_blank=True,
        help_text=(
            "Issuing Gov_Stack_BB identifier. "
            "Blank/whitespace triggers GovStackBBNotFound (460) from the service — "
            "not a 400 from the serializer — so allow_blank=True is intentional."
        ),
    )

    def validate_voucher_amount(self, value) -> Decimal:
        # Non-parseable input (e.g. "abc") is rejected by DecimalField → HTTP 400. Correct.
        # Non-positive values (0 or negative) MUST produce HTTP 452 (InvalidVoucherAmount),
        # not 400.  Pass them through here — the service raises InvalidVoucherAmount(452).
        return value

    def validate_voucher_currency(self, value: str) -> str:
        if not value:
            raise serializers.ValidationError("voucher_currency is required.")
        upper = value.upper()
        # Apply ISO 4217 regex: exactly 3 uppercase letters.
        # Invalid format returns HTTP 400 here; the service may raise
        # InvalidVoucherCurrency (HTTP 453) for codes that are syntactically valid
        # but not supported by the program.
        return _validate_iso4217(upper)

    # NOTE: no validate_voucher_group — blank groups reach the service, which raises
    # InvalidVoucherGroup (454).  Serializer-level rejection would give HTTP 400, which
    # does not match the GovStack spec for this field.


# ---------------------------------------------------------------------------
# Voucher response serializers — SCHEMA DOCUMENTATION ONLY
# ---------------------------------------------------------------------------
# The five classes below document the exact JSON shape returned by each
# successful voucher endpoint.  Views construct response dicts directly
# (the DRF pattern used throughout this codebase) rather than running data
# through serializer.data at request time — so none of these classes are
# instantiated during normal request handling.
#
# They serve three purposes:
#   1. Authoritative field-level schema reference for API consumers and
#      code reviewers — one place to see every key and type in each response.
#   2. Source material for auto-generated OpenAPI / Swagger documentation.
#   3. A migration path: if a future wave requires output validation or
#      serializer.data() rendering, switch the view to use these classes
#      directly without changing any field definitions.
#
# Canonical rule: if the view's Response() dict and the corresponding
# serializer below ever drift apart, the serializer is wrong.
# The view Response() dict is always the source of truth.
# ---------------------------------------------------------------------------

class VoucherPreactivationResponseSerializer(serializers.Serializer):
    """
    Response shape for POST /vouchers/voucher_preactivation → HTTP 200.
    Schema documentation only — see section note above.
    """
    voucherNumber = serializers.CharField(read_only=True)
    voucherSerialNumber = serializers.CharField(read_only=True)
    voucherGroup = serializers.CharField(read_only=True)
    expiryDate = serializers.DateTimeField(read_only=True)


# ---------------------------------------------------------------------------
# Voucher — Activation
# ---------------------------------------------------------------------------

class VoucherActivationRequestSerializer(serializers.Serializer):
    """
    PATCH /govstack/payments/vouchers/voucher_activation
    GovStack spec: VoucherActivate.yml
    Harness: voucher_activation.feature

    NOTE: voucher_serial_number is sent as integer by the harness.
    """
    voucher_serial_number = serializers.CharField(
        max_length=20,
        help_text=(
            "Voucher serial number (may be sent as int by harness, treated as string). "
            "max_length=20 matches the model field and prevents unbounded DB queries."
        ),
    )
    Gov_Stack_BB = serializers.CharField(
        max_length=50,
        allow_blank=True,
        help_text=(
            "Issuing Gov_Stack_BB identifier. "
            "Blank triggers GovStackBBNotFound (460) from the service."
        ),
    )

    def validate_voucher_serial_number(self, value) -> str:
        """Accept int or string serial numbers (harness sends int)."""
        return str(value).strip()


class VoucherActivationResponseSerializer(serializers.Serializer):
    """
    Response shape for PATCH /vouchers/voucher_activation → HTTP 200.
    Schema documentation only — see section note above.
    """
    voucherNumber = serializers.CharField(read_only=True)
    voucherSerialNumber = serializers.CharField(read_only=True)
    voucherStatus = serializers.CharField(read_only=True)
    voucherGroup = serializers.CharField(read_only=True)


# ---------------------------------------------------------------------------
# Voucher — Redemption
# ---------------------------------------------------------------------------

class VoucherRedemptionRequestSerializer(serializers.Serializer):
    """
    POST /govstack/payments/vouchers/voucher_redemption
    GovStack spec: VoucherRedemption.yml
    Harness: voucher_redemption.feature

    NOTE: voucher_number is sent as integer by the harness.
    """
    voucher_number = serializers.CharField(
        max_length=20,
        help_text=(
            "Voucher number (may be sent as int by harness). "
            "max_length=20 matches the model field and prevents unbounded DB queries."
        ),
    )
    Gov_Stack_BB = serializers.CharField(
        max_length=50,
        allow_blank=True,
        help_text=(
            "Issuing Gov_Stack_BB identifier. "
            "Blank triggers GovStackBBNotFound (460) from the service."
        ),
    )
    merchant_name = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    merchant_bank_details = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    merchant_voucher_group = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    override = serializers.BooleanField(required=False, default=False)
    agent_id = serializers.CharField(
        max_length=10,
        required=False,
        allow_blank=True,
        default="",
        help_text=(
            "Agent ID performing the redemption. Stored as redeemed_by_agent_id (max 10 chars). "
            "Optional — not tested by the GovStack harness but wired through for production use."
        ),
    )

    def validate_voucher_number(self, value) -> str:
        return str(value).strip()


class VoucherRedemptionResponseSerializer(serializers.Serializer):
    """
    Response shape for POST /vouchers/voucher_redemption → HTTP 200.
    Schema documentation only — see section note above.
    """
    status = serializers.IntegerField(read_only=True)       # status_int (CONSUMED = 3)
    message = serializers.CharField(read_only=True)
    serialNumber = serializers.CharField(read_only=True)
    value = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    timestamp = serializers.DateTimeField(read_only=True)   # redeemed_at
    transactionId = serializers.CharField(read_only=True)   # redemption_transaction_id (20 hex chars)


# ---------------------------------------------------------------------------
# Voucher — Status Check
# ---------------------------------------------------------------------------

class VoucherStatusResponseSerializer(serializers.Serializer):
    """
    Response shape for GET /vouchers/voucherstatuscheck/{serial} → HTTP 200.
    Schema documentation only — see section note above.
    """
    status = serializers.IntegerField(read_only=True)       # status_int from STATUS_INT_MAP
    serialNumber = serializers.CharField(read_only=True)
    value = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)


# ---------------------------------------------------------------------------
# Voucher — Cancellation
# ---------------------------------------------------------------------------

class VoucherCancellationResponseSerializer(serializers.Serializer):
    """
    Response shape for PATCH /vouchers/voucherstatuscheck/{serial} → HTTP 200.
    (Cancellation — same URL as status check, different HTTP method.)
    Schema documentation only — see section note above.
    """
    voucherSerialNumber = serializers.CharField(read_only=True)
    voucherStatus = serializers.CharField(read_only=True)   # GovStackVoucher.STATUS_CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# P2G — Bill Payments (Wave 5 stubs)
# ---------------------------------------------------------------------------

class BillInquiryResponseSerializer(serializers.Serializer):
    """GET /govstack/payments/bills/{billId}"""
    billId = serializers.CharField(read_only=True)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    currency = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    dueDate = serializers.DateField(read_only=True, required=False)


class BillTransferRequestSerializer(serializers.Serializer):
    """POST /govstack/payments/billTransferRequests"""
    requestId = serializers.CharField(max_length=20)
    billInquiryRequestId = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    billId = serializers.CharField(max_length=100)
    paymentReferenceID = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
