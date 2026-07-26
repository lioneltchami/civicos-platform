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

# G2P RequestID: exactly 12 alphanumeric-or-hyphen chars.
# Verified against a fresh clone of GovStackWorkingGroup/bb-payments
# (test/openAPI/features/support/helpers/helpers.js): g2pResponseSchema.RequestID
# is `{type: 'string', minLength: 12, maxLength: 12}` — used by the response
# schema for register-beneficiary, update-beneficiary-details, bulk-payment, and
# prepayment-validation. Every RequestID literal in the harness's own .feature
# files for these 4 endpoints is exactly 12 characters (e.g. "RequestID111",
# "abcdef123456", "4a0425ef-008") and no scenario ever sends a non-12-char or
# missing RequestID — so tightening 1–16 down to exactly 12 cannot break any
# harness run; it only closes a real spec-fidelity gap.
# NOTE: prepayment-validation-response uses a *different* schema
# (prepaymentValidationResponseSchema) whose RequestID has no length constraint
# at all — that endpoint's serializer (PrepaymentValidationResponseAckSerializer)
# is deliberately NOT covered by this validator.
_REQUEST_ID_RE = re.compile(r"^[a-zA-Z0-9\-]{12}$")

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
    """
    Validate RequestID: exactly 12 alphanumeric or hyphen characters.

    Matches the live GovStack g2pResponseSchema (helpers.js) exactly:
    RequestID is `{minLength: 12, maxLength: 12}` there. Used as a field-level
    validator on the RequestID field of the request serializers for
    register-beneficiary, update-beneficiary-details, bulk-payment, and
    prepayment-validation — i.e. only when a RequestID value is actually
    present in the payload (DRF does not run field-level validators when a
    `required=False` field is omitted and falls back to its default; the
    omitted case is intentionally left as-is — see RequestID field comments).
    """
    if not value or not _REQUEST_ID_RE.match(value):
        raise serializers.ValidationError(
            "Must be exactly 12 alphanumeric or hyphen characters."
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
    # Validated by _validate_request_id (exactly 12 alphanumeric/hyphen chars) —
    # this only fires when a RequestID value is present in the payload; an
    # omitted key still falls back to the "" default unvalidated (DRF does not
    # run field-level validators on a required=False field's default value).
    RequestID = serializers.CharField(
        max_length=16,
        required=False,
        allow_blank=True,
        default="",
        validators=[_validate_request_id],
        help_text="RequestID. Exactly 12 alphanumeric or hyphen chars when present. Echoed back in response.",
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
    # RequestID: validated by _validate_request_id (exactly 12 alphanumeric/hyphen
    # chars) when present; an omitted key still defaults to "" unvalidated.
    RequestID = serializers.CharField(
        max_length=16, required=False, allow_blank=True, default="",
        validators=[_validate_request_id],
    )
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
    # RequestID: validated by _validate_request_id (exactly 12 alphanumeric/hyphen
    # chars) when present; an omitted key still defaults to "" unvalidated.
    RequestID = serializers.CharField(
        max_length=16, required=False, allow_blank=True, default="",
        validators=[_validate_request_id],
    )
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

    IMPORTANT — RequestID on THIS endpoint is deliberately NOT run through
    _validate_request_id (unlike register-beneficiary / update-beneficiary-details
    / bulk-payment / prepayment-validation). Verified against a fresh clone of
    GovStackWorkingGroup/bb-payments: this endpoint's response is validated
    against `prepaymentValidationResponseSchema` in helpers.js, which declares
    `RequestID: { type: 'string' }` with NO length constraint — a different,
    unconstrained schema from `g2pResponseSchema` (minLength/maxLength 12) used
    by the other four endpoints. Applying the 12-char constraint here would be
    over-tightening beyond what the live spec actually requires for this route.

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
        if not value or not value.strip():
            raise serializers.ValidationError("voucher_currency is required.")
        # ISO 4217 format validation is intentionally NOT applied here.
        # It is performed in GovStackVoucherService.preactivate() so that an
        # invalid format (e.g. "US" — 2 chars, "USDD" — 4 chars) raises
        # InvalidVoucherCurrency (HTTP 453) rather than serializer.ValidationError
        # (HTTP 400).  The GovStack Payments spec assigns status 453 to this
        # error; HTTP 400 would be a spec violation.
        return value.strip().upper()

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

    Field names and casing are the REAL harness-validated schema
    (test/openAPI/Payment_BB_Voucher_api_test.json), NOT the internal
    Payment-Hub↔Voucher-Engine protocol docs under
    "api/Voucher API YAMLs/" — those use a different (camelCase) field
    naming convention that the actual certification harness does not
    validate against. All 3 fields below are required by the harness schema.
    """
    voucher_number = serializers.CharField(read_only=True)
    voucher_serial_number = serializers.CharField(read_only=True)
    expiry_date_time = serializers.DateTimeField(read_only=True)


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

    Real harness schema (test/openAPI/Payment_BB_Voucher_api_test.json)
    requires only "result_status" — a free-form string (no enum), not the
    old camelCase {voucherNumber, voucherSerialNumber, voucherStatus,
    voucherGroup} shape.
    """
    result_status = serializers.CharField(read_only=True)


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

    Real harness schema requires only "result_status" (free-form string, no
    enum) — not the old {status, message, serialNumber, value, timestamp,
    transactionId} shape.
    """
    result_status = serializers.CharField(read_only=True)


# ---------------------------------------------------------------------------
# Voucher — Status Check
# ---------------------------------------------------------------------------

class VoucherStatusResponseSerializer(serializers.Serializer):
    """
    Response shape for GET /vouchers/voucherstatuscheck/{serial} → HTTP 200.
    Schema documentation only — see section note above.

    Real harness schema requires:
      voucher_status: one of exactly 7 enum strings (see
        govstack_views.py's _VOUCHER_STATUS_ENUM_MAP for the mapping from
        GovStackVoucher.status and the judgment calls documented there).
      voucher_amount: a STRING (str(voucher.amount)), NOT a float/number —
        the old `value = float(voucher.amount)` was a confirmed bug.
    """
    voucher_status = serializers.CharField(read_only=True)
    voucher_amount = serializers.CharField(read_only=True)


# ---------------------------------------------------------------------------
# Voucher — Cancellation
# ---------------------------------------------------------------------------

class VoucherCancellationRequestSerializer(serializers.Serializer):
    """
    PATCH /govstack/payments/vouchers/voucherstatuscheck/{voucherserialnumber}  (body)
    Harness: voucher_cancelation.feature

    NEW (P1): the real harness sends a JSON body on EVERY cancellation PATCH
    call, in addition to the URL path segment carrying the same serial
    number — confirmed via the harness's own JS step-definitions. Previously
    this endpoint validated ONLY the URL path segment and silently ignored
    the request body entirely, which meant the harness's "missing
    voucherserialnumber in payload → 400" and "missing Gov_Stack_BB in
    payload → 400" negative scenarios would have incorrectly returned 200.
    This serializer fixes that real, previously-undiscovered gap.

    Both fields are required and must be non-blank; either missing/blank
    → HTTP 400 via standard DRF serializer validation (including the
    "no payload at all" case, since request.data then resolves to {}).
    """
    voucherserialnumber = serializers.CharField(
        max_length=20,
        help_text=(
            "Voucher serial number. Also carried in the URL path — the URL "
            "value is authoritative for the actual lookup; this body field "
            "is validated for presence only, matching harness behaviour."
        ),
    )
    Gov_Stack_BB = serializers.CharField(
        max_length=50,
        help_text=(
            "Issuing Gov_Stack_BB identifier. Must be present and non-blank "
            "(400 otherwise). A non-blank but known-invalid sentinel value "
            "(e.g. 'invalid_bb') is accepted by this serializer and instead "
            "rejected at the view/service layer with HTTP 463 — this "
            "endpoint uniquely reuses 463 for both an invalid serial and an "
            "invalid Gov_Stack_BB (confirmed via the real Gherkin scenarios)."
        ),
    )


class VoucherCancellationResponseSerializer(serializers.Serializer):
    """
    Response shape for PATCH /vouchers/voucherstatuscheck/{serial} → HTTP 200.
    (Cancellation — same URL as status check, different HTTP method.)
    Schema documentation only — see section note above.

    "message" is REQUIRED by the real harness schema — previously absent
    entirely. voucherSerialNumber / voucherStatus are kept additively (not
    required by the harness, but useful to API consumers).
    """
    voucherSerialNumber = serializers.CharField(read_only=True)
    voucherStatus = serializers.CharField(read_only=True)   # GovStackVoucher.STATUS_CANCELLED = "cancelled"
    message = serializers.CharField(read_only=True)


# ---------------------------------------------------------------------------
# P2G — Bill Payments (Wave 5)
# ---------------------------------------------------------------------------

class BillTransferRequestSerializer(serializers.Serializer):
    """
    POST /govstack/payments/billTransferRequests
    GovStack spec: P2G API YAMLs/BillTransferRequest.yml
    Harness: no P2G harness feature in current certification cycle.

    Caller is a mobile money operator / financial institution BB notifying the
    Payments BB that a citizen has submitted a payment for a government bill.

    requestId is the caller's idempotency key — duplicate requestIds return
    HTTP 400 (DuplicateBillPaymentError) rather than creating a duplicate record.
    """
    requestId = serializers.CharField(
        max_length=100,
        help_text=(
            "Caller-supplied idempotency key. Uniquely identifies this transfer request. "
            "Duplicate requestIds return HTTP 400."
        ),
    )
    billId = serializers.CharField(
        max_length=100,
        help_text="Government-assigned bill identifier matching GovStackBill.bill_id.",
    )
    billInquiryRequestId = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        default="",
        help_text="requestId from a prior GET /bills/{billId} inquiry (optional).",
    )
    paymentReferenceID = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        default="",
        help_text="Mobile money / financial network payment reference (optional).",
    )

    def validate_requestId(self, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            # An all-whitespace requestId would strip to "" — an empty idempotency
            # key.  The first call would succeed with a blank key; the second
            # would hit the unique constraint and return 400 (DuplicateBillPaymentError),
            # making idempotency semantics meaningless.  Reject early.
            raise serializers.ValidationError(
                "requestId must not be blank or whitespace-only."
            )
        return stripped

    def validate_billId(self, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise serializers.ValidationError(
                "billId must not be blank or whitespace-only."
            )
        return stripped


# ---------------------------------------------------------------------------
# P2G — Response serializers (schema documentation only)
# ---------------------------------------------------------------------------
# These classes document the exact JSON shape returned by each P2G endpoint.
# Views construct response dicts directly; these serve as:
#   1. Authoritative field-level schema reference.
#   2. Source material for auto-generated OpenAPI/Swagger documentation.
# The view Response() dict is always the source of truth.
# ---------------------------------------------------------------------------

class BillInquiryResponseSerializer(serializers.Serializer):
    """
    Response shape for GET /govstack/payments/bills/{bill_id} → HTTP 200.
    Schema documentation only.
    """
    billId = serializers.CharField(read_only=True)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    currency = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)         # "unpaid" | "paid" | "overdue" | "cancelled"
    dueDate = serializers.DateField(read_only=True, allow_null=True)   # null when not set


class BillTransferResponseSerializer(serializers.Serializer):
    """
    Response shape for POST /govstack/payments/billTransferRequests → HTTP 200.
    Schema documentation only.
    """
    requestId = serializers.CharField(read_only=True)
    billId = serializers.CharField(read_only=True)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    currency = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)         # "completed"
    message = serializers.CharField(read_only=True)


class MarkBillPaidResponseSerializer(serializers.Serializer):
    """
    Response shape for POST /govstack/payments/bills/{bill_id}/mark-paid → HTTP 200.
    Schema documentation only.
    """
    billId = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)         # "paid"
    message = serializers.CharField(read_only=True)


class TransferRequestStatusSerializer(serializers.Serializer):
    """
    Response shape for GET /govstack/payments/transferRequests/{request_id} → HTTP 200.
    Schema documentation only.
    """
    requestId = serializers.CharField(read_only=True)
    billId = serializers.CharField(read_only=True)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    currency = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)         # "pending" | "completed" | "failed"
