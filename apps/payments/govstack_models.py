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

_REQUEST_ID_VALIDATOR = RegexValidator(
    regex=r"^[a-zA-Z0-9\-]{1,16}$",
    message="RequestID must be 1–16 alphanumeric or hyphen characters.",
)


def _generate_voucher_serial() -> str:
    """
    Generate a unique 6-digit numeric serial number for a GovStack voucher.

    Uniqueness is enforced by the DB unique constraint on GovStackVoucher.serial_number.
    In tests, patch this function to inject deterministic values (e.g. '5550', '6004').
    """
    # 100000–999999: 6 digits, never starts with 0.
    return str(secrets.randbelow(900_000) + 100_000)


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
        validators=[_BB_ID_VALIDATOR],
        verbose_name=_("Payee Functional ID"),
        help_text=_(
            "Government-assigned functional identity for this beneficiary. "
            "Max 20 chars per GovStack spec. NEVER write to logs."
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
        validators=[_BB_ID_VALIDATOR],
        verbose_name=_("Source BB ID"),
        help_text=_("SourceBBID of the registering Building Block."),
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
        help_text=_("RequestID from Source BB. Max 16 chars."),
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
        max_length=12,
        blank=True,
        verbose_name=_("Correlation ID"),
        help_text=_("X-CorrelationID header."),
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
        verbose_name=_("Request ID"),
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
        STATUS_NOT_PREACTIVATED: [STATUS_PREACTIVATED],
        STATUS_PREACTIVATED: [STATUS_ACTIVATED, STATUS_CANCELLED],
        STATUS_ACTIVATED: [STATUS_CONSUMED, STATUS_BLOCKED, STATUS_SUSPENDED, STATUS_CANCELLED],
        STATUS_BLOCKED: [STATUS_ACTIVATED, STATUS_CANCELLED],
        STATUS_SUSPENDED: [STATUS_ACTIVATED, STATUS_CANCELLED],
        STATUS_CONSUMED: [],   # terminal
        STATUS_CANCELLED: [],  # terminal
        STATUS_PURGED: [],     # terminal
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
