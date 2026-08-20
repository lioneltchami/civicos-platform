"""
GovStack Payments Building Block — data models.

These models support the GovStack bb-payments API certification layer.
They are ENTIRELY SEPARATE from the CivicOS-internal Payments models
(Stripe, PaymentIntent, Donations, etc.) in models.py.

Security invariants:
- financial_address is Fernet-encrypted at rest (EncryptedCharField).
- voucher_secret is Fernet-encrypted at rest (EncryptedCharField).
- payee_functional_id and financial_address MUST NEVER appear in any log
  line, HTTP response body, or error message.
- voucher_secret MUST NEVER appear in any HTTP response body or log line.
- currency accepts any ISO 4217 code — NOT restricted to CAD (unlike models.py).
- All monetary values: DecimalField (never FloatField).
- GovStackPaymentAuditEntry is append-only: save() after creation and
  delete() are blocked at the model level.

Architecture:
- URL prefix: /govstack/payments/
- Namespace:  govstack_payments
- These models are NOT related to any existing payments model.
"""
from __future__ import annotations

import secrets
import uuid
from decimal import Decimal

from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import EncryptedCharField
from apps.core.models import TimestampedModel


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

_ISO4217_VALIDATOR = RegexValidator(
    regex=r"^[A-Z]{3}$",
    message="Currency must be a 3-letter ISO 4217 code (e.g. USD, AED, CAD).",
)

_BB_ID_VALIDATOR = RegexValidator(
    regex=r"^[a-zA-Z0-9\-]{1,20}$",
    message="BB ID must be 1–20 alphanumeric or hyphen characters.",
)

# G2P-specific validator: lowercase hex + hyphens only, matching the serializer's
# _G2P_UUID_RE pattern.  Applied to GovStackBeneficiary.payee_functional_id so that
# Django admin and any code that calls full_clean() enforces the same constraint as
# the serializer layer.
#
# The broad _BB_ID_VALIDATOR is intentionally kept for other BB-ID fields (source_bb_id
# on Wave 3+ models, Voucher Gov_Stack_BB fields) whose serializers accept uppercase IDs.
#
# Known permissive boundary: pure-hyphen strings like "---" also match (hyphens are valid
# UUID separator chars).  If a future spec version tightens this, change to:
#   r"^[0-9a-f][0-9a-f\-]{0,19}$"
_G2P_UUID_VALIDATOR = RegexValidator(
    regex=r"^[0-9a-f\-]{1,20}$",
    message=(
        "Payee Functional ID must be 1–20 lowercase hex characters and hyphens "
        "(e.g. '2ba5ed20-0f42-4eff-8'). Uppercase letters are not permitted."
    ),
)

# Exactly 12 alphanumeric-or-hyphen chars — matches the live GovStack
# g2pResponseSchema.RequestID constraint (minLength: 12, maxLength: 12) in
# GovStackWorkingGroup/bb-payments' helpers.js. max_length on the CharField
# below stays at 16 (not narrowed to 12): this validator is the actual length
# gate, and 16 leaves headroom without requiring a migration.
_REQUEST_ID_VALIDATOR = RegexValidator(
    regex=r"^[a-zA-Z0-9\-]{12}$",
    message="RequestID must be exactly 12 alphanumeric or hyphen characters.",
)


def _generate_voucher_serial() -> str:
    """
    Generate a unique 18-digit numeric serial number for a GovStack voucher.

    The GovStack harness's own JSON schema (test/openAPI/features/support/helpers/
    helpers.js) requires both `voucher_number` and `voucher_serial_number` to be
    strings of 16-25 characters. This codebase's own request-side serializers
    (VoucherActivationRequestSerializer.voucher_serial_number and
    VoucherRedemptionRequestSerializer.voucher_number, govstack_serializers.py)
    cap length at max_length=20, so 18 digits sits comfortably inside both
    constraints (16 <= 18 <= 20 <= 25).

    The value MUST remain purely numeric (digits only): _is_numeric_voucher_number()
    (govstack_services.py) relies on int() parsing succeeding for legitimate
    vouchers and failing for the harness's literal "notAnumber" fixture (HTTP 461).

    Uniqueness is enforced by the DB unique constraint on GovStackVoucher.serial_number.
    In tests, patch this function to inject deterministic values (e.g. '5550', '6004').
    """
    # 100_000_000_000_000_000-999_999_999_999_999_999: exactly 18 digits, never
    # starts with 0 (str() of a Python int never truncates or pads leading zeros).
    return str(secrets.randbelow(9 * 10**17) + 10**17)


# ---------------------------------------------------------------------------
# GovStackBeneficiary  (G2P ID Mapper)
# ---------------------------------------------------------------------------

class GovStackBeneficiary(TimestampedModel):
    """
    ID Mapper entry for a G2P beneficiary.

    Maps PayeeFunctionalID (government-assigned functional identity)
    to a FinancialAddress (bank account, IBAN, mobile money wallet, etc.).

    GovStack spec: api/G2P API YAMLs/RegisterBeneficiaryRequest.yml

    Security:
    - financial_address is EncryptedCharField — stored as Fernet ciphertext.
    - payee_functional_id is NEVER in any log line. Use str(obj.pk) in logs.
    - financial_address is NEVER in any HTTP response or log line.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    payee_functional_id = models.CharField(
        max_length=20,
        unique=True,  # unique already creates an index; db_index=True is redundant
        validators=[_G2P_UUID_VALIDATOR],
        verbose_name=_("Payee Functional ID"),
        help_text=_(
            "Government-assigned functional identity for this beneficiary. "
            "Must be 1–20 lowercase hex chars and hyphens per GovStack G2P spec. "
            "NEVER write to logs."
        ),
    )
    payment_modality = models.CharField(
        max_length=2,
        blank=True,
        verbose_name=_("Payment Modality"),
        help_text=_(
            "Two-digit code: 01=bank account, 02=mobile money, "
            "03=voucher, 04=proxy. Optional."
        ),
    )
    financial_address = EncryptedCharField(
        max_length=512,
        blank=True,
        verbose_name=_("Financial Address"),
        help_text=_(
            "Bank account / IBAN / mobile money number. "
            "Stored Fernet-encrypted. NEVER in any HTTP response or log."
        ),
    )
    source_bb_id = models.CharField(
        max_length=20,
        db_index=True,
        # G2P SourceBBID values flow through _validate_g2p_id in the serializer
        # (lowercase hex + hyphens only), so the model validator matches that constraint.
        # Wave 3+ models (BulkPaymentBatch, etc.) keep _BB_ID_VALIDATOR on their
        # source_bb_id fields because those serializers accept uppercase SourceBBIDs.
        validators=[_G2P_UUID_VALIDATOR],
        verbose_name=_("Source BB ID"),
        help_text=_("SourceBBID of the registering Building Block. Must be lowercase hex + hyphens."),
    )
    registering_institution_id = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_("Registering Institution ID"),
        help_text=_("X-Registering-Institution-ID header value at registration time."),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("Active"),
    )

    class Meta:
        verbose_name = _("GovStack Beneficiary")
        verbose_name_plural = _("GovStack Beneficiaries")
        indexes = [
            models.Index(
                fields=["source_bb_id", "payee_functional_id"],
                name="gs_ben_sourcebb_payee_idx",
            ),
        ]

    def __str__(self) -> str:
        # DO NOT include payee_functional_id here — it would appear in admin
        # list views and potentially in log lines.
        return f"Beneficiary {self.pk} [{self.source_bb_id}]"


# ---------------------------------------------------------------------------
# BulkPaymentBatch  (G2P Bulk Disbursement)
# ---------------------------------------------------------------------------

class BulkPaymentBatch(TimestampedModel):
    """
    A batch of credit instructions from a Source BB to the Payments BB.

    GovStack spec: api/G2P API YAMLs/BulkPayment.yml
    Harness feature: test/openAPI/features/g2p_bulk_payment.feature

    Tracks processing status across all CreditInstructions in the batch.
    After acceptance (HTTP 200), a Celery task processes instructions async.
    """

    STATUS_RECEIVED = "received"
    STATUS_VALIDATING = "validating"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_PARTIAL = "partial"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_RECEIVED, _("Received")),
        (STATUS_VALIDATING, _("Validating")),
        (STATUS_PROCESSING, _("Processing")),
        (STATUS_COMPLETED, _("Completed")),
        (STATUS_PARTIAL, _("Partially Completed")),
        (STATUS_FAILED, _("Failed")),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    request_id = models.CharField(
        max_length=16,
        db_index=True,
        validators=[_REQUEST_ID_VALIDATOR],
        verbose_name=_("Request ID"),
        help_text=_("RequestID from Source BB. Exactly 12 alphanumeric/hyphen chars per the live GovStack spec."),
    )
    source_bb_id = models.CharField(
        max_length=20,
        db_index=True,
        verbose_name=_("Source BB ID"),
        help_text=_("SourceBBID. Pattern: [a-zA-Z0-9]{10} per harness."),
    )
    batch_id = models.CharField(
        max_length=20,
        unique=True,  # unique already creates an index
        verbose_name=_("Batch ID"),
        help_text=_("BatchID from Source BB. Must be globally unique."),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_RECEIVED,
        db_index=True,
        verbose_name=_("Status"),
    )
    callback_url = models.URLField(
        max_length=500,
        blank=True,
        verbose_name=_("Callback URL"),
        help_text=_("X-Callback-URL header. CivicOS POSTs async results here."),
    )
    correlation_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Correlation ID"),
        # max_length=100: standard UUIDs are 36 chars; some systems use longer IDs.
        # The old max_length=12 caused DataError whenever a Source BB sent a UUID-format
        # correlation ID (e.g. "550e8400-e29b-41d4-a716-446655440000" = 36 chars).
        help_text=_("X-CorrelationID header. Max 100 chars (accommodates UUIDs and longer IDs)."),
    )
    total_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("Total Amount"),
        help_text=_("Sum of all instruction amounts. Computed on receipt."),
    )
    completed_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name=_("Completed Amount"),
    )
    failed_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name=_("Failed Amount"),
    )
    result_generated_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Result Generated At"),
        help_text=_("Timestamp when Celery task completed processing."),
    )
    note = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Note"),
        help_text=_("Internal note for partial/failed batches."),
    )

    class Meta:
        verbose_name = _("Bulk Payment Batch")
        verbose_name_plural = _("Bulk Payment Batches")
        indexes = [
            models.Index(fields=["status", "created_at"], name="gs_batch_status_created_idx"),
        ]

    def __str__(self) -> str:
        return f"Batch {self.batch_id} [{self.status}]"


# ---------------------------------------------------------------------------
# CreditInstruction  (one line item in a BulkPaymentBatch)
# ---------------------------------------------------------------------------

class CreditInstruction(TimestampedModel):
    """
    A single credit instruction within a BulkPaymentBatch.

    GovStack spec: CreditInstructions[] in BulkPayment.yml

    Security: payee_functional_id must NEVER appear in logs. Use pk in logs.
    """

    STATUS_PENDING = "pending"
    STATUS_VALIDATED = "validated"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending")),
        (STATUS_VALIDATED, _("Validated")),
        (STATUS_COMPLETED, _("Completed")),
        (STATUS_FAILED, _("Failed")),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    batch = models.ForeignKey(
        BulkPaymentBatch,
        on_delete=models.PROTECT,
        related_name="instructions",
        verbose_name=_("Batch"),
    )
    instruction_id = models.CharField(
        max_length=16,
        db_index=True,
        verbose_name=_("Instruction ID"),
        help_text=_("InstructionID. Max 16 chars. Unique within batch."),
    )
    payee_functional_id = models.CharField(
        max_length=20,
        verbose_name=_("Payee Functional ID"),
        help_text=_(
            "Maps to GovStackBeneficiary.payee_functional_id. "
            "NEVER write to any log line."
        ),
    )
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name=_("Amount"),
    )
    currency = models.CharField(
        max_length=3,
        validators=[_ISO4217_VALIDATOR],
        verbose_name=_("Currency"),
        help_text=_("ISO 4217 3-letter code. Multi-currency — NOT restricted to CAD."),
    )
    narration = models.CharField(
        max_length=50,
        blank=True,
        verbose_name=_("Narration"),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name=_("Status"),
    )
    failure_reason = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Failure Reason"),
    )

    class Meta:
        verbose_name = _("Credit Instruction")
        verbose_name_plural = _("Credit Instructions")
        unique_together = [("batch", "instruction_id")]
        indexes = [
            models.Index(fields=["batch", "status"], name="gs_instr_batch_status_idx"),
        ]

    def __str__(self) -> str:
        # self.batch_id is the FK UUID — use self.batch.batch_id to get the human-readable ID,
        # but that triggers a DB query. Use the pk instead to keep __str__ query-free.
        return f"Instruction {self.instruction_id} [{self.status}] (batch pk={self.batch_id})"


# ---------------------------------------------------------------------------
# PrepaymentValidationRequest
# ---------------------------------------------------------------------------

class PrepaymentValidationRequest(TimestampedModel):
    """
    Pre-payment validation — validates PayeeFunctionalIDs before bulk disbursement.

    GovStack spec: api/G2P API YAMLs/PrePaymentValidation.yml
    Harness features:
      - g2p_prepayment_validation.feature
      - (prepayment-validation-response is the harness's acknowledgement step)

    Async: CivicOS accepts the request, returns 200 immediately, then POSTs
    the validation result to X-Callback-URL via a Celery task.
    """

    STATUS_PENDING = "pending"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending")),
        (STATUS_COMPLETED, _("Completed")),
        (STATUS_FAILED, _("Failed")),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    request_id = models.CharField(
        max_length=16,
        unique=True,  # unique already creates an index
        validators=[_REQUEST_ID_VALIDATOR],
        verbose_name=_("Request ID"),
        help_text=_("RequestID from Source BB. Exactly 12 chars per live schema."),
    )
    source_bb_id = models.CharField(
        max_length=20,
        verbose_name=_("Source BB ID"),
    )
    batch_id = models.CharField(
        max_length=20,
        db_index=True,
        verbose_name=_("Batch ID"),
    )
    instruction_id = models.CharField(
        max_length=20,
        verbose_name=_("Instruction ID"),
    )
    payee_functional_id = models.CharField(
        max_length=20,
        verbose_name=_("Payee Functional ID"),
        help_text=_("NEVER write to any log line."),
    )
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name=_("Amount"),
    )
    currency = models.CharField(
        max_length=3,
        validators=[_ISO4217_VALIDATOR],
        verbose_name=_("Currency"),
    )
    narration = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Narration"),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name=_("Status"),
    )
    beneficiary_found = models.BooleanField(
        null=True,
        verbose_name=_("Beneficiary Found"),
        help_text=_("True if PayeeFunctionalID resolved in the ID Mapper."),
    )
    financial_address_valid = models.BooleanField(
        null=True,
        verbose_name=_("Financial Address Valid"),
        help_text=_("True if FinancialAddress is present and non-empty."),
    )
    callback_url = models.URLField(
        max_length=500,
        blank=True,
        verbose_name=_("Callback URL"),
    )

    class Meta:
        verbose_name = _("Prepayment Validation Request")
        verbose_name_plural = _("Prepayment Validation Requests")
        indexes = [
            models.Index(fields=["batch_id", "status"], name="gs_prepay_batch_status_idx"),
        ]

    def __str__(self) -> str:
        return f"Prepayment {self.request_id} [{self.status}]"


class PrepaymentExecution(models.Model):
    """Internal admission record for one validated prepayment execution."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    validation_request = models.OneToOneField(
        PrepaymentValidationRequest,
        on_delete=models.PROTECT,
        related_name="execution",
    )
    execution_key = models.CharField(max_length=160, unique=True)
    admitted_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _("Prepayment Execution")
        verbose_name_plural = _("Prepayment Executions")

    def __str__(self) -> str:
        return f"Prepayment execution {self.validation_request_id}"


# ---------------------------------------------------------------------------
# GovStackVoucher
# ---------------------------------------------------------------------------

class GovStackVoucher(TimestampedModel):
    """
    A GovStack digital voucher for goods/services redemption.

    GovStack spec: api/Voucher API YAMLs/
    Harness features (5):
      - voucher_preactivation.feature
      - voucher_activation.feature
      - voucher_redemption.feature
      - voucher_cancelation.feature
      - voucher_status_check.feature

    Status state machine (see ALLOWED_TRANSITIONS):
      PREACTIVATED → ACTIVATED → CONSUMED (terminal)
                   ↘ CANCELLED (terminal)
               ↗ ACTIVATED again from BLOCKED / SUSPENDED
      BLOCKED  → ACTIVATED | CANCELLED
      SUSPENDED → ACTIVATED | CANCELLED

    NOT_PREACTIVATED is the initial state before any action; normal flow
    creates vouchers directly in PREACTIVATED (pre-activation endpoint).

    Security:
    - voucher_secret is EncryptedCharField — NEVER in any HTTP response or log.
    - serial_number is the public identifier (safe to expose in responses).
    """

    # ── Status string constants ──────────────────────────────────────────────
    STATUS_NOT_PREACTIVATED = "not_preactivated"
    STATUS_PREACTIVATED = "preactivated"
    STATUS_ACTIVATED = "activated"
    STATUS_CONSUMED = "consumed"
    STATUS_BLOCKED = "blocked"
    STATUS_SUSPENDED = "suspended"
    STATUS_CANCELLED = "cancelled"
    STATUS_PURGED = "purged"

    STATUS_CHOICES = [
        (STATUS_NOT_PREACTIVATED, _("Not Preactivated")),
        (STATUS_PREACTIVATED, _("Preactivated")),
        (STATUS_ACTIVATED, _("Activated")),
        (STATUS_CONSUMED, _("Consumed")),
        (STATUS_BLOCKED, _("Blocked")),
        (STATUS_SUSPENDED, _("Suspended")),
        (STATUS_CANCELLED, _("Cancelled")),
        (STATUS_PURGED, _("Purged")),
    ]

    # ── Integer status codes for the GET /voucherstatuscheck response ────────
    # Used in: GET /vouchers/voucherstatuscheck/{serial} → {"status": <int>}
    STATUS_INT_MAP: dict[str, int] = {
        STATUS_NOT_PREACTIVATED: 0,
        STATUS_PREACTIVATED: 1,
        STATUS_ACTIVATED: 2,
        STATUS_CONSUMED: 3,
        STATUS_BLOCKED: 4,
        STATUS_SUSPENDED: 5,
        STATUS_CANCELLED: 6,
        STATUS_PURGED: 7,
    }
    STATUS_ERROR_INT: int = 9  # Returned when status lookup fails

    # ── Valid state machine transitions ─────────────────────────────────────
    ALLOWED_TRANSITIONS: dict[str, list[str]] = {
        # ── GovStack spec artifact ────────────────────────────────────────────
        # STATUS_NOT_PREACTIVATED → PREACTIVATED is defined by the GovStack
        # Voucher spec as the "initial issuance" transition.
        # In this implementation, GovStackVoucherService.preactivate() creates
        # vouchers directly in STATUS_PREACTIVATED (the model default), so this
        # entry is never exercised at runtime — no voucher is ever persisted in
        # the NOT_PREACTIVATED state.
        # Kept for spec completeness and to keep transition_to() exhaustive
        # (it would raise ValueError for any unmapped from-state, so every
        # spec-defined state must appear as a key here).
        STATUS_NOT_PREACTIVATED: [STATUS_PREACTIVATED],

        # ── Active lifecycle ──────────────────────────────────────────────────
        STATUS_PREACTIVATED: [STATUS_ACTIVATED, STATUS_CANCELLED],
        STATUS_ACTIVATED: [STATUS_CONSUMED, STATUS_BLOCKED, STATUS_SUSPENDED, STATUS_CANCELLED],
        STATUS_BLOCKED: [STATUS_ACTIVATED, STATUS_CANCELLED],
        STATUS_SUSPENDED: [STATUS_ACTIVATED, STATUS_CANCELLED],

        # ── Terminal states (no outbound transitions) ─────────────────────────
        STATUS_CONSUMED: [],
        STATUS_CANCELLED: [],
        STATUS_PURGED: [],
    }

    # ── Fields ───────────────────────────────────────────────────────────────

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    serial_number = models.CharField(
        max_length=20,
        unique=True,  # unique already creates an index
        verbose_name=_("Serial Number"),
        help_text=_(
            "Public voucher serial number. Assigned on pre-activation. "
            "Safe to expose in API responses."
        ),
    )
    voucher_secret = EncryptedCharField(
        max_length=512,
        blank=True,
        verbose_name=_("Voucher Secret"),
        help_text=_(
            "Secret number for redemption validation. Stored Fernet-encrypted. "
            "MUST NEVER appear in any HTTP response or log line."
        ),
    )
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name=_("Amount"),
    )
    currency = models.CharField(
        max_length=3,
        validators=[_ISO4217_VALIDATOR],
        verbose_name=_("Currency"),
        help_text=_("ISO 4217 3-letter code. NOT restricted to CAD."),
    )
    group_code = models.CharField(
        max_length=50,
        db_index=True,
        verbose_name=_("Group Code"),
        help_text=_("Voucher group / program code. e.g. 'Payment Voucher'."),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PREACTIVATED,
        db_index=True,
        verbose_name=_("Status"),
    )
    issuing_bb = models.CharField(
        max_length=50,
        db_index=True,
        verbose_name=_("Issuing BB"),
        help_text=_("Gov_Stack_BB identifier that requested pre-activation."),
    )
    registering_institution_id = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_("Registering Institution ID"),
        help_text=_("X-Registering-Institution-Id header at pre-activation time."),
    )
    batch_id = models.CharField(
        max_length=12,
        blank=True,
        verbose_name=_("Batch ID"),
    )
    payee_functional_id = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_("Payee Functional ID"),
        help_text=_("Beneficiary this voucher was issued to (if applicable). Never log."),
    )
    callback_url = models.URLField(
        max_length=500,
        blank=True,
        verbose_name=_("Callback URL"),
    )
    expiry_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Expiry Date"),
        help_text=_("Voucher expiry. Default: now + GOVSTACK_VOUCHER_EXPIRY_DAYS (90)."),
    )

    # ── Redemption fields (populated on CONSUMED transition) ─────────────────
    redeemed_by_agent_id = models.CharField(
        max_length=10,
        blank=True,
        verbose_name=_("Redeemed By Agent ID"),
    )
    redeemed_merchant_name = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Redeemed Merchant Name"),
    )
    redeemed_merchant_bank_details = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Redeemed Merchant Bank Details"),
    )
    redeemed_merchant_voucher_group = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Redeemed Merchant Voucher Group"),
    )
    redeemed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Redeemed At"),
    )
    redemption_transaction_id = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_("Redemption Transaction ID"),
    )

    class Meta:
        verbose_name = _("GovStack Voucher")
        verbose_name_plural = _("GovStack Vouchers")
        indexes = [
            models.Index(fields=["status", "group_code"], name="gs_voucher_status_group_idx"),
            models.Index(fields=["issuing_bb", "status"], name="gs_voucher_bb_status_idx"),
        ]

    def __str__(self) -> str:
        return f"Voucher {self.serial_number} [{self.status}]"

    # ── State machine ────────────────────────────────────────────────────────

    def transition_to(self, new_status: str) -> None:
        """
        Validate and apply a status transition.

        Raises ValueError if the transition is not permitted.
        Does NOT save — callers must call .save() or .save(update_fields=["status", ...]).

        Usage:
            voucher.transition_to(GovStackVoucher.STATUS_ACTIVATED)
            voucher.save(update_fields=["status"])
        """
        allowed = self.ALLOWED_TRANSITIONS.get(self.status, [])
        if new_status not in allowed:
            raise ValueError(
                f"Invalid voucher transition: {self.status!r} → {new_status!r}. "
                f"Allowed transitions from {self.status!r}: {allowed}."
            )
        self.status = new_status

    @property
    def status_int(self) -> int:
        """
        Integer status code for the GET /voucherstatuscheck response.

        Returns STATUS_ERROR_INT (9) if the status string is unrecognised.
        """
        return self.STATUS_INT_MAP.get(self.status, self.STATUS_ERROR_INT)

    @property
    def is_terminal(self) -> bool:
        """True if no further transitions are possible."""
        return self.status in (
            self.STATUS_CONSUMED,
            self.STATUS_CANCELLED,
            self.STATUS_PURGED,
        )


# ---------------------------------------------------------------------------
# GovStackPaymentAuditEntry  (append-only)
# ---------------------------------------------------------------------------

class _AuditEntryQuerySet(models.QuerySet):
    """
    Custom QuerySet for GovStackPaymentAuditEntry.

    Overrides `.delete()` to block bulk deletion via the ORM.  Django's
    standard QuerySet.delete() calls SQL DELETE directly without invoking the
    model's delete() method, so bulk deletes must be blocked here.

    This is a separate class (not inlined as a Manager) so it can be used with
    `.as_manager()` and still be patchable in tests.
    """

    def delete(self):
        raise PermissionError(
            "GovStackPaymentAuditEntry records are permanent and cannot be deleted."
        )

    def update(self, **kwargs):
        """
        Block bulk update to enforce append-only semantics.

        Django's QuerySet.update() issues a raw SQL UPDATE bypassing the model's
        save() override. Without this guard, any caller could do:
            GovStackPaymentAuditEntry.objects.filter(...).update(actor_bb_id="tampered")
        …and silently corrupt the audit trail.
        """
        raise PermissionError(
            "GovStackPaymentAuditEntry records are append-only and cannot be modified."
        )


class GovStackPaymentAuditEntry(TimestampedModel):
    """
    Append-only audit log for all GovStack Payments BB events.

    IMPORTANT: save() after initial creation and delete() are BLOCKED at the
    model level to enforce append-only semantics.  Bulk deletion via the
    queryset is also blocked by _AuditEntryQuerySet.delete().

    All action constants are declared here so callers don't use bare strings.

    Security:
    - details JSON must NEVER contain payee_functional_id, financial_address,
      or voucher_secret.
    - object_pk should be str(model.pk) or a non-PII identifier.
    """

    # Custom manager: blocks bulk deletion via queryset.delete().
    objects = _AuditEntryQuerySet.as_manager()

    # ── Action constants ────────────────────────────────────────────────────
    ACTION_BENEFICIARY_REGISTERED = "beneficiary_registered"
    ACTION_BENEFICIARY_UPDATED = "beneficiary_updated"
    ACTION_BATCH_RECEIVED = "batch_received"
    ACTION_BATCH_COMPLETED = "batch_completed"
    ACTION_BATCH_PARTIAL = "batch_partial"
    ACTION_BATCH_FAILED = "batch_failed"
    ACTION_INSTRUCTION_COMPLETED = "instruction_completed"
    ACTION_INSTRUCTION_FAILED = "instruction_failed"
    ACTION_VALIDATION_REQUESTED = "validation_requested"
    ACTION_VALIDATION_COMPLETED = "validation_completed"
    ACTION_VOUCHER_PREACTIVATED = "voucher_preactivated"
    ACTION_VOUCHER_ACTIVATED = "voucher_activated"
    ACTION_VOUCHER_REDEEMED = "voucher_redeemed"
    ACTION_VOUCHER_CANCELLED = "voucher_cancelled"
    # P2G — Bill Payments (Wave 5)
    ACTION_BILL_PAYMENT_REQUESTED = "bill_payment_requested"
    ACTION_BILL_PAID = "bill_paid"
    # Item 02 — failure remediation. Every value represents non-PII lifecycle
    # metadata only and is persisted through the append-only audit model.
    ACTION_PAYMENT_ATTEMPT_CREATED = "payment_attempt_created"
    ACTION_PAYMENT_OUTCOME_RECORDED = "payment_outcome_recorded"
    ACTION_PAYMENT_RETRY_SCHEDULED = "payment_retry_scheduled"
    ACTION_PAYMENT_UNCERTAIN = "payment_uncertain"
    ACTION_CALLBACK_QUEUED = "callback_queued"
    ACTION_CALLBACK_DELIVERED = "callback_delivered"
    ACTION_CALLBACK_DEAD_LETTERED = "callback_dead_lettered"
    ACTION_RECONCILIATION_RECORDED = "reconciliation_recorded"
    ACTION_PAYMENT_REVIEW_REQUIRED = "payment_review_required"

    ACTION_CHOICES = [
        (ACTION_BENEFICIARY_REGISTERED, _("Beneficiary Registered")),
        (ACTION_BENEFICIARY_UPDATED, _("Beneficiary Updated")),
        (ACTION_BATCH_RECEIVED, _("Batch Received")),
        (ACTION_BATCH_COMPLETED, _("Batch Completed")),
        (ACTION_BATCH_PARTIAL, _("Batch Partially Completed")),
        (ACTION_BATCH_FAILED, _("Batch Failed")),
        (ACTION_INSTRUCTION_COMPLETED, _("Instruction Completed")),
        (ACTION_INSTRUCTION_FAILED, _("Instruction Failed")),
        (ACTION_VALIDATION_REQUESTED, _("Validation Requested")),
        (ACTION_VALIDATION_COMPLETED, _("Validation Completed")),
        (ACTION_VOUCHER_PREACTIVATED, _("Voucher Preactivated")),
        (ACTION_VOUCHER_ACTIVATED, _("Voucher Activated")),
        (ACTION_VOUCHER_REDEEMED, _("Voucher Redeemed")),
        (ACTION_VOUCHER_CANCELLED, _("Voucher Cancelled")),
        # Wave 5
        (ACTION_BILL_PAYMENT_REQUESTED, _("Bill Payment Requested")),
        (ACTION_BILL_PAID, _("Bill Paid")),
        (ACTION_PAYMENT_ATTEMPT_CREATED, _("Payment Attempt Created")),
        (ACTION_PAYMENT_OUTCOME_RECORDED, _("Payment Outcome Recorded")),
        (ACTION_PAYMENT_RETRY_SCHEDULED, _("Payment Retry Scheduled")),
        (ACTION_PAYMENT_UNCERTAIN, _("Payment Outcome Uncertain")),
        (ACTION_CALLBACK_QUEUED, _("Callback Queued")),
        (ACTION_CALLBACK_DELIVERED, _("Callback Delivered")),
        (ACTION_CALLBACK_DEAD_LETTERED, _("Callback Dead-Lettered")),
        (ACTION_RECONCILIATION_RECORDED, _("Reconciliation Recorded")),
        (ACTION_PAYMENT_REVIEW_REQUIRED, _("Payment Review Required")),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    action = models.CharField(
        max_length=50,
        choices=ACTION_CHOICES,
        db_index=True,
        verbose_name=_("Action"),
    )
    actor_bb_id = models.CharField(
        max_length=50,
        blank=True,
        db_index=True,
        verbose_name=_("Actor BB ID"),
        help_text=_("SourceBBID or Gov_Stack_BB that triggered the action."),
    )
    object_type = models.CharField(
        max_length=50,
        db_index=True,
        verbose_name=_("Object Type"),
        help_text=_("e.g. 'beneficiary', 'batch', 'instruction', 'voucher'"),
    )
    object_pk = models.CharField(
        max_length=100,
        verbose_name=_("Object PK"),
        help_text=_(
            "PK of the affected object. Use str(obj.pk) or a non-PII identifier. "
            "NEVER include payee_functional_id or financial_address."
        ),
    )
    request_id = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_("Request ID"),
        help_text=_("RequestID echoed from the triggering request."),
    )
    details = models.JSONField(
        default=dict,
        verbose_name=_("Details"),
        help_text=_(
            "Non-PII metadata about the action. "
            "Must NEVER contain payee_functional_id, financial_address, or voucher_secret."
        ),
    )
    timestamp = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name=_("Timestamp"),
    )

    class Meta:
        verbose_name = _("GovStack Payment Audit Entry")
        verbose_name_plural = _("GovStack Payment Audit Entries")
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["action", "timestamp"], name="gs_audit_action_ts_idx"),
            models.Index(fields=["object_type", "object_pk"], name="gs_audit_obj_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.action} / {self.object_type}:{self.object_pk} @ {self.timestamp:%Y-%m-%d %H:%M:%S}"

    # ── Append-only enforcement ──────────────────────────────────────────────

    def save(self, *args, **kwargs) -> None:  # type: ignore[override]
        """Block updates. GovStackPaymentAuditEntry is append-only."""
        if self.pk and GovStackPaymentAuditEntry.objects.filter(pk=self.pk).exists():
            raise PermissionError(
                "GovStackPaymentAuditEntry records are append-only and cannot be modified."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):  # type: ignore[override]
        """Block deletion. GovStackPaymentAuditEntry records are permanent."""
        raise PermissionError(
            "GovStackPaymentAuditEntry records are permanent and cannot be deleted."
        )


# ---------------------------------------------------------------------------
# GovStackBill  (P2G — Person to Government)
# ---------------------------------------------------------------------------

class GovStackBill(TimestampedModel):
    """
    A government bill / fee that a citizen can pay via the P2G API.

    GovStack spec: api/P2G API YAMLs/
    Harness: no P2G harness features in current certification cycle.

    Bills are created by government staff (admin or import jobs), not via the
    P2G API itself.  The P2G API only reads bills and records payment requests.

    Status state machine:
      UNPAID → PAID (via POST /billTransferRequests or POST /bills/{id}/mark-paid)
      UNPAID → OVERDUE (via scheduled task — not Wave 5 scope)
      Any non-terminal → CANCELLED (admin action only)
      PAID and CANCELLED are terminal.

    Security:
    - No citizen PII stored on this model (bill_id is a government-assigned ID,
      not a citizen identifier).
    - correlation_id and payer_fi_id on GovStackBillPayment may identify a
      financial institution (not a citizen), so they are omitted from audit details.
    """

    STATUS_UNPAID = "unpaid"
    STATUS_PAID = "paid"
    STATUS_OVERDUE = "overdue"
    # NOTE: STATUS_CANCELLED = "cancelled" is the same string value as
    # GovStackVoucher.STATUS_CANCELLED.  This is intentional — both use the
    # GovStack-spec status string "cancelled" — but callers MUST always qualify
    # the constant with the model class name (e.g. GovStackBill.STATUS_CANCELLED,
    # not a bare import).  Never do `from govstack_models import *` or use an
    # unqualified STATUS_CANCELLED reference that could shadow one model with the
    # other.
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_UNPAID, _("Unpaid")),
        (STATUS_PAID, _("Paid")),
        (STATUS_OVERDUE, _("Overdue")),
        (STATUS_CANCELLED, _("Cancelled")),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    bill_id = models.CharField(
        max_length=100,
        db_index=True,  # no longer globally unique — see gs_bill_tenant_billid_uniq below
        verbose_name=_("Bill ID"),
        help_text=_(
            "Government-assigned bill identifier. Used as the {bill_id} URL parameter. "
            "Safe to expose in API responses. Uniqueness is enforced PER TENANT "
            "(see Meta.constraints' gs_bill_tenant_billid_uniq), not globally — "
            "see that constraint's docstring note for the certifiability-audit "
            "rationale (Round 2, MEDIUM finding)."
        ),
    )
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name=_("Amount"),
    )
    currency = models.CharField(
        max_length=3,
        validators=[_ISO4217_VALIDATOR],
        verbose_name=_("Currency"),
        help_text=_("ISO 4217 3-letter code. NOT restricted to CAD."),
    )
    description = models.CharField(
        max_length=500,
        blank=True,
        verbose_name=_("Description"),
        help_text=_("Human-readable description of the bill (e.g. 'Passport Application Fee')."),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_UNPAID,
        db_index=True,
        verbose_name=_("Status"),
    )
    due_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("Due Date"),
        help_text=_("Date by which the bill must be paid. Optional."),
    )
    correlation_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Correlation ID"),
        help_text=_("Optional cross-system correlation identifier for this bill."),
    )
    platform_tenant_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Platform Tenant ID"),
        help_text=_(
            "X-Platform-TenantId header value recorded when this bill was "
            "created/imported by government staff. Empty string means the bill "
            "predates tenant scoping or was created without a declared tenant "
            "(tolerated — see GovStackP2GService for how this is used to scope "
            "reads/writes only when a caller-supplied tenant id is present)."
        ),
    )

    class Meta:
        verbose_name = _("GovStack Bill")
        verbose_name_plural = _("GovStack Bills")
        indexes = [
            models.Index(fields=["status", "due_date"], name="gs_bill_status_due_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gt=0),
                name="gs_bill_amount_positive",
            ),
            # Certifiability-audit fix (Round 2 — MEDIUM finding): bill_id was
            # globally unique=True, but tenant-scoping (added in a prior
            # round) means two DIFFERENT tenants sharing one deployment could
            # coincidentally collide on the same bill_id string, incorrectly
            # rejecting the second tenant's legitimate bill as a duplicate of
            # the first's. Scoping uniqueness to (platform_tenant_id, bill_id)
            # instead preserves today's behaviour for blank-tenant records
            # (platform_tenant_id="" — two blank-tenant bills still can't
            # collide with each other, matching pre-fix behaviour exactly)
            # while allowing two different tenants to legitimately reuse the
            # same bill_id string.
            models.UniqueConstraint(
                fields=["platform_tenant_id", "bill_id"],
                name="gs_bill_tenant_billid_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"Bill {self.bill_id} [{self.status}]"


# ---------------------------------------------------------------------------
# GovStackBillPayment  (P2G Transfer Request)
# ---------------------------------------------------------------------------

class GovStackBillPayment(TimestampedModel):
    """
    A P2G bill payment record — the result of a POST /billTransferRequests call.

    GovStack spec: api/P2G API YAMLs/BillTransferRequest.yml
    Corresponds to the GovStack "Transfer Request" concept.

    Each record is identified by request_id (supplied by the caller) which
    acts as an idempotency key — duplicate request_ids return HTTP 400
    (DuplicateBillPaymentError) rather than creating duplicate records.

    Amounts and currency are snapshotted from GovStackBill at payment time so
    the payment record remains accurate even if the bill's fee is later updated.

    Security:
    - payer_fi_id identifies the financial institution (not the citizen) and
      is stored as plain text.  It is NOT included in audit details because:
      (a) it is potentially identifiable infrastructure metadata, and
      (b) it is already stored on this model for direct lookup.
    - No citizen PII is stored on this model.
    """

    STATUS_PENDING = "pending"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending")),
        (STATUS_COMPLETED, _("Completed")),
        (STATUS_FAILED, _("Failed")),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    request_id = models.CharField(
        max_length=100,
        db_index=True,  # no longer globally unique — see gs_billpayment_tenant_requestid_uniq below
        verbose_name=_("Request ID"),
        help_text=_(
            "Caller-supplied idempotency key. Duplicate request_ids return HTTP 400 "
            "instead of creating duplicate payment records. Uniqueness is enforced "
            "PER TENANT (see Meta.constraints' gs_billpayment_tenant_requestid_uniq), "
            "not globally — see that constraint's docstring note for the "
            "certifiability-audit rationale (Round 2, MEDIUM finding)."
        ),
    )
    bill = models.ForeignKey(
        GovStackBill,
        on_delete=models.PROTECT,  # PROTECT: cannot delete a bill that has payment records
        related_name="payments",
        verbose_name=_("Bill"),
    )
    bill_inquiry_request_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Bill Inquiry Request ID"),
        help_text=_("Request ID from a prior GET /bills/{billId} inquiry (optional)."),
    )
    payment_reference_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Payment Reference ID"),
        help_text=_("Mobile money / financial network payment reference (optional)."),
    )
    correlation_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Correlation ID"),
        help_text=_("X-CorrelationID header value for cross-system tracing."),
    )
    payer_fi_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Payer FI ID"),
        help_text=_("X-PayerFI-Id header: financial institution that originated the payment."),
    )
    platform_tenant_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Platform Tenant ID"),
        help_text=_("X-Platform-TenantId header value."),
    )
    # Snapshot of bill values at payment time.
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name=_("Amount"),
        help_text=_("Snapshotted from GovStackBill.amount at payment time."),
    )
    currency = models.CharField(
        max_length=3,
        validators=[_ISO4217_VALIDATOR],
        verbose_name=_("Currency"),
        help_text=_("Snapshotted from GovStackBill.currency at payment time."),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name=_("Status"),
    )

    class Meta:
        verbose_name = _("GovStack Bill Payment")
        verbose_name_plural = _("GovStack Bill Payments")
        indexes = [
            models.Index(fields=["bill", "status"], name="gs_billpay_bill_status_idx"),
            models.Index(fields=["status", "created_at"], name="gs_billpay_status_created_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gt=0),
                name="gs_billpay_amount_positive",
            ),
            # Certifiability-audit fix (Round 2 — MEDIUM finding): request_id
            # was globally unique=True — same rationale as
            # GovStackBill.gs_bill_tenant_billid_uniq above. Scoping to
            # (platform_tenant_id, request_id) preserves today's behaviour for
            # blank-tenant records while letting two different tenants
            # legitimately reuse the same request_id idempotency-key string.
            models.UniqueConstraint(
                fields=["platform_tenant_id", "request_id"],
                name="gs_billpayment_tenant_requestid_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"BillPayment {self.request_id} [{self.status}] (bill={self.bill_id})"


# ---------------------------------------------------------------------------
# GovStackRegisteredBB  (BB Whitelist — GAP-4)
# ---------------------------------------------------------------------------

class GovStackRegisteredBB(TimestampedModel):
    """
    Registry of GovStack Building Blocks authorised to call this BB's endpoints.

    Used by IsTrustedSourceBB.has_permission() when
    GOVSTACK_REQUIRE_REGISTERED_BB=True (the production default).

    Each row maps a bb_id (the exact string sent in the
    X-Registering-Institution-ID request header) to an active/inactive flag.
    Rows with is_active=False are silently rejected — this supports
    temporary suspension of a BB's access without deleting audit history.

    Harness setup:
      seed_govstack_vouchers creates GovStackRegisteredBB(bb_id="GS-HARNESS")
      so the harness institution ID passes after the table is populated.

    Production setup:
      Add a row per registered Building Block via Django admin before enabling
      GOVSTACK_REQUIRE_REGISTERED_BB=True in the environment.

    Security:
      bb_id is validated against _BB_ID_VALIDATOR (1–20 alphanumeric/hyphen chars)
      at both the model and DB layer (unique constraint).
      No PII is stored here — bb_id is an infrastructure identifier, not a citizen ID.
    """

    bb_id = models.CharField(
        max_length=20,
        unique=True,
        validators=[_BB_ID_VALIDATOR],
        verbose_name=_("BB Identifier"),
        help_text=_(
            "Must match the X-Registering-Institution-ID header value sent by the BB. "
            "1–20 alphanumeric or hyphen characters. Case-sensitive."
        ),
    )
    description = models.TextField(
        blank=True,
        verbose_name=_("Description"),
        help_text=_("Human-readable description of this Building Block (optional)."),
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name=_("Active"),
        help_text=_(
            "Inactive BBs are rejected by IsTrustedSourceBB even if their bb_id "
            "is present in the table. Use this to suspend access without deleting records."
        ),
    )
    role = models.CharField(
        max_length=20,
        choices=[
            ("resource", _("Resource")),
            ("organizer", _("Organizer")),
            ("admin", _("Admin")),
        ],
        default="organizer",
        db_index=True,
        verbose_name=_("Scheduler Role"),
        help_text=_(
            "Maximum GovStack Scheduler BB actor role this registered BB may act as "
            "(subscriber → resource → organizer → admin). Consulted only by the "
            "Scheduler BB's GovStackSchedulerAuth when GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True. "
            "Does not apply to citizen/subscriber-tier calls, which require a CivicOS "
            "citizen access token (JWT) instead of a bb_id role."
        ),
    )
    allowed_platform_tenant_ids = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("Allowed Platform Tenant IDs"),
        help_text=_(
            "List of X-Platform-TenantId values this registered BB may declare "
            "on P2G calls. An EMPTY list means unrestricted (back-compat default "
            "for BBs not yet assigned tenant restrictions) — only a non-empty "
            "list enforces binding."
        ),
    )
    # Design note (certifiability-audit fix, Round 2 — HIGH finding): this
    # field is a MINIMAL, OPT-IN tenant-registry binding, not a full
    # multi-tenant data-isolation redesign. A fresh, adversarial re-audit
    # found that X-Platform-TenantId tenant-scoping (added in a prior round)
    # was a self-asserted claim with no verification that the calling,
    # whitelisted BB was actually entitled to declare a given tenant ID — a
    # caller who simply knew or guessed another tenant's ID string got full
    # cross-tenant read/write access. Defaulting this field to "deny
    # everything" (i.e. requiring every BB to have a non-empty allow-list
    # before any P2G call succeeds) was deliberately rejected: there is no
    # existing tenant-registry data to migrate, and a hard-deny default would
    # break every current caller with zero migration path. The empty-list
    # default is unrestricted (identical to today's behaviour) so operators
    # can adopt this opt-in hardening per-BB, at their own pace, once they
    # know which tenant IDs a given BB is entitled to declare. See
    # apps.payments.govstack_views.GovStackAPIView._validate_platform_tenant_id()
    # for the enforcement logic.

    class Meta:
        verbose_name = _("GovStack Registered BB")
        verbose_name_plural = _("GovStack Registered BBs")
        ordering = ["bb_id"]

    def __str__(self) -> str:
        status = "active" if self.is_active else "inactive"
        return f"{self.bb_id} ({status})"


# ---------------------------------------------------------------------------
# Failure-remediation lifecycle (Item 02)
# ---------------------------------------------------------------------------
class ProviderRegistration(TimestampedModel):
    """Durable, redacted provider configuration selected by tenant and operation."""
    tenant_id = models.CharField(max_length=100, db_index=True)
    operation = models.CharField(max_length=30)
    provider_name = models.CharField(max_length=80)
    # Factory metadata is deliberately distinct from provider_name: workers may
    # resolve only a reviewed allowlisted factory key and schema version.
    factory_key = models.CharField(max_length=80, default="", db_index=True)
    schema_version = models.PositiveIntegerField(default=0)
    configuration_version = models.CharField(max_length=80)
    configuration = models.JSONField(default=dict)
    active_from = models.DateTimeField(null=True, blank=True)
    active_until = models.DateTimeField(null=True, blank=True)
    audit_metadata = models.JSONField(default=dict)
    active = models.BooleanField(default=True, db_index=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "operation"], name="gs_provider_registration_scope_uniq")]
        indexes = [models.Index(fields=["tenant_id", "operation", "active"], name="gs_provider_reg_lookup_idx")]


class PaymentAttempt(TimestampedModel):
    """Provider-neutral, durable execution record; local validation is not settlement."""
    STATUS_PENDING = "pending"
    STATUS_RETRYABLE = "retryable"
    STATUS_UNCERTAIN = "uncertain"
    STATUS_SETTLED = "settled"
    STATUS_REJECTED = "rejected"
    STATUS_REVIEW = "review"
    STATUS_DEAD_LETTER = "dead_letter"
    STATUS_CHOICES = [(s, s.replace('_', ' ').title()) for s in (
        STATUS_PENDING, STATUS_RETRYABLE, STATUS_UNCERTAIN, STATUS_SETTLED,
        STATUS_REJECTED, STATUS_REVIEW, STATUS_DEAD_LETTER)]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.CharField(max_length=100, blank=False, db_index=True)
    provider_registration = models.ForeignKey("ProviderRegistration", on_delete=models.PROTECT, null=True, blank=True, related_name="attempts")
    claim_token = models.CharField(max_length=128, blank=True)
    claim_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    claim_generation = models.PositiveIntegerField(default=0)
    claim_heartbeat_at = models.DateTimeField(null=True, blank=True)
    submission_intent = models.JSONField(default=dict)
    # Evidence from an accepted or ambiguous provider interaction. A reserved
    # command alone never establishes that external work may exist.
    recovery_evidence = models.JSONField(default=dict)

    request_id = models.CharField(max_length=100, db_index=True)
    operation = models.CharField(max_length=30, default="g2p")
    correlation_id = models.CharField(max_length=100, blank=True, db_index=True)
    source_bb_id = models.CharField(max_length=50, blank=True)
    provider_attempt_id = models.CharField(max_length=100, blank=True, db_index=True)
    external_transaction_id = models.CharField(max_length=100, blank=True, db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    failure_code = models.CharField(max_length=50, blank=True)
    failure_category = models.CharField(max_length=30, blank=True)
    retryable = models.BooleanField(default=False)
    attempt_count = models.PositiveIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True)
    payload_fingerprint = models.CharField(max_length=64)
    version = models.PositiveIntegerField(default=1)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "operation", "request_id"], name="gs_attempt_scope_request_uniq")]
        indexes = [models.Index(fields=["status", "next_retry_at"], name="gs_attempt_due_idx")]

class PaymentExecutionIntent(TimestampedModel):
    """Append-only provider execution identity reserved before external I/O."""

    STATE_RESERVED = "reserved"
    STATE_CORRELATED = "correlated"
    STATE_CHOICES = [
        (STATE_RESERVED, "Reserved"),
        (STATE_CORRELATED, "Correlated"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.OneToOneField(
        PaymentAttempt,
        on_delete=models.PROTECT,
        related_name="execution_intent",
    )
    scope = models.CharField(max_length=100, db_index=True)
    operation = models.CharField(max_length=30)
    request_identity = models.CharField(max_length=100)
    payload_fingerprint = models.CharField(max_length=64)
    provider_correlation = models.CharField(max_length=160, blank=True, db_index=True)
    state = models.CharField(max_length=20, choices=STATE_CHOICES, default=STATE_RESERVED, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "operation", "request_identity"],
                name="gs_exec_intent_identity_uniq",
            )
        ]
        indexes = [
            models.Index(
                fields=["scope", "operation", "created_at"],
                name="gs_exec_intent_scope_idx",
            )
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            original = type(self).objects.get(pk=self.pk)
            immutable = (
                "attempt_id",
                "scope",
                "operation",
                "request_identity",
                "payload_fingerprint",
            )
            if any(getattr(original, field) != getattr(self, field) for field in immutable):
                raise ValueError("PaymentExecutionIntent canonical identity is immutable")
            if original.provider_correlation and self.provider_correlation != original.provider_correlation:
                raise ValueError("PaymentExecutionIntent provider correlation is immutable")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("PaymentExecutionIntent is append-only")


class CallbackDelivery(TimestampedModel):
    """Idempotent callback outbox ledger with bounded retry/dead-letter state."""
    STATUS_PENDING = "pending"; STATUS_DELIVERED = "delivered"; STATUS_RETRY = "retry"; STATUS_DEAD = "dead"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey(PaymentAttempt, on_delete=models.PROTECT, related_name="callbacks")
    callback_url = models.URLField(max_length=500)
    # Payloads are restricted to the existing non-PII callback contract fields.
    # The hash provides idempotent outbox uniqueness and is retained for replay.
    payload = models.JSONField(default=dict)
    payload_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=20, default=STATUS_PENDING, db_index=True)
    delivery_count = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_http_status = models.PositiveIntegerField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["attempt", "payload_hash"], name="gs_callback_attempt_payload_uniq")]

class PaymentReconciliation(TimestampedModel):
    """Comparison of internal and provider/source-BB outcomes."""
    STATUS_MATCHED = "matched"; STATUS_MISMATCH = "mismatch"; STATUS_UNKNOWN = "unknown"; STATUS_RESOLVED = "resolved"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey(PaymentAttempt, on_delete=models.PROTECT, related_name="reconciliations")
    provider_status = models.CharField(max_length=30, blank=True)
    internal_status = models.CharField(max_length=30)
    source_bb_status = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=20, default=STATUS_UNKNOWN, db_index=True)
    external_transaction_id = models.CharField(max_length=100, blank=True)
    resolution_note = models.CharField(max_length=255, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        indexes = [models.Index(fields=["status", "created_at"], name="gs_recon_status_created_idx")]

class ProviderObservation(TimestampedModel):
    """Immutable exact-binding provider/source evidence; never local finality."""
    KIND_PROVIDER = "provider"
    KIND_SOURCE = "source"
    OUTCOME_SETTLED = "settled"
    OUTCOME_REJECTED = "rejected"
    OUTCOME_UNCERTAIN = "uncertain"
    OUTCOME_REVIEW = "manual_review"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey(PaymentAttempt, on_delete=models.PROTECT, related_name="observations")
    tenant_id = models.CharField(max_length=100, db_index=True)
    observation_kind = models.CharField(max_length=20)
    observation_id = models.CharField(max_length=160)
    provider_transaction_id = models.CharField(max_length=100, blank=True)
    event_id = models.CharField(max_length=160, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3)
    outcome = models.CharField(max_length=20)
    verified = models.BooleanField(default=False)
    verification_method = models.CharField(max_length=80, blank=True)
    binding_hash = models.CharField(max_length=64)
    accepted_finality = models.BooleanField(default=False, db_index=True)
    metadata = models.JSONField(default=dict)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["observation_kind", "observation_id"], name="gs_observation_kind_id_uniq"),
            models.UniqueConstraint(fields=["attempt", "accepted_finality"], condition=models.Q(accepted_finality=True), name="gs_one_accepted_finality"),
        ]
        indexes = [models.Index(fields=["tenant_id", "attempt", "created_at"], name="gs_obs_tenant_attempt_idx")]
    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("ProviderObservation is immutable")
        return super().save(*args, **kwargs)
    def delete(self, *args, **kwargs):
        raise ValueError("ProviderObservation is append-only")

class IdempotencyConflict(Exception):
    pass

class InvalidPaymentTransition(Exception):
    pass

class PaymentOutcome:
    """Stable adapter-neutral outcome vocabulary."""
    def __init__(self, status, code="", category="", retryable=False, provider_attempt_id="", external_transaction_id="", message=""):
        self.status, self.code, self.category, self.retryable = status, code, category, retryable
        self.provider_attempt_id, self.external_transaction_id, self.message = provider_attempt_id, external_transaction_id, message


PaymentAttempt.ALLOWED_TRANSITIONS = {
    PaymentAttempt.STATUS_PENDING: {PaymentAttempt.STATUS_RETRYABLE, PaymentAttempt.STATUS_UNCERTAIN, PaymentAttempt.STATUS_SETTLED, PaymentAttempt.STATUS_REJECTED, PaymentAttempt.STATUS_REVIEW},
    PaymentAttempt.STATUS_RETRYABLE: {PaymentAttempt.STATUS_PENDING, PaymentAttempt.STATUS_UNCERTAIN, PaymentAttempt.STATUS_SETTLED, PaymentAttempt.STATUS_REJECTED, PaymentAttempt.STATUS_REVIEW, PaymentAttempt.STATUS_DEAD_LETTER},
    PaymentAttempt.STATUS_UNCERTAIN: {PaymentAttempt.STATUS_UNCERTAIN, PaymentAttempt.STATUS_SETTLED, PaymentAttempt.STATUS_REJECTED, PaymentAttempt.STATUS_REVIEW, PaymentAttempt.STATUS_DEAD_LETTER},
    PaymentAttempt.STATUS_REVIEW: {PaymentAttempt.STATUS_PENDING, PaymentAttempt.STATUS_DEAD_LETTER},
    PaymentAttempt.STATUS_SETTLED: set(), PaymentAttempt.STATUS_REJECTED: set(), PaymentAttempt.STATUS_DEAD_LETTER: set(),
}

PaymentAttempt.is_terminal = property(lambda self: self.status in {PaymentAttempt.STATUS_SETTLED, PaymentAttempt.STATUS_REJECTED, PaymentAttempt.STATUS_DEAD_LETTER})

def _attempt_transition(self, new_status, **fields):
    if new_status not in PaymentAttempt.ALLOWED_TRANSITIONS.get(self.status, set()):
        raise InvalidPaymentTransition(f"invalid payment transition {self.status} -> {new_status}")
    self.status = new_status
    for key, value in fields.items(): setattr(self, key, value)
    self.version += 1
PaymentAttempt.transition_to = _attempt_transition
PaymentAttempt.__str__ = lambda self: f"PaymentAttempt {self.pk} [{self.status}]"

PaymentAttempt._meta.verbose_name = "Payment Attempt"
CallbackDelivery._meta.verbose_name = "Callback Delivery"
PaymentReconciliation._meta.verbose_name = "Payment Reconciliation"


class IdempotencyLedger(TimestampedModel):
    """Durable endpoint reservation and exact response replay record."""
    STATE_IN_PROGRESS = "in_progress"
    STATE_COMPLETE = "complete"
    STATE_CHOICES = [(STATE_IN_PROGRESS, "In progress"), (STATE_COMPLETE, "Complete")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.CharField(max_length=100)
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=255)
    key = models.CharField(max_length=255)
    fingerprint = models.CharField(max_length=64)
    state = models.CharField(max_length=20, choices=STATE_CHOICES, default=STATE_IN_PROGRESS)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    body = models.JSONField(default=dict)
    headers = models.JSONField(default=dict)
    completed_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "method", "path", "key"], name="gs_idem_tenant_method_path_key")]
        indexes = [models.Index(fields=["tenant_id", "created_at"], name="gs_idem_tenant_created_idx")]


class BatchLease(TimestampedModel):
    """Durable ownership token for batch workers; stale owners cannot renew or commit."""
    batch = models.OneToOneField(BulkPaymentBatch, on_delete=models.PROTECT, related_name="runtime_lease")
    owner_token = models.CharField(max_length=128)
    generation = models.PositiveIntegerField(default=1)
    expires_at = models.DateTimeField(db_index=True)
    class Meta:
        indexes = [models.Index(fields=["expires_at", "generation"], name="gs_batch_lease_due_idx")]

    def is_expired(self):
        from django.utils import timezone
        return self.expires_at <= timezone.now()
