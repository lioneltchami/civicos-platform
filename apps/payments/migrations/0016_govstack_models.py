# Generated 2026-07-19 — GovStack Payments BB (Wave 1)
#
# Creates the 6 GovStack Payments BB models:
#   - GovStackBeneficiary         (G2P ID Mapper)
#   - BulkPaymentBatch            (G2P bulk disbursement batch)
#   - CreditInstruction           (one line item per batch)
#   - PrepaymentValidationRequest (pre-disbursement validation)
#   - GovStackVoucher             (voucher lifecycle management)
#   - GovStackPaymentAuditEntry   (append-only audit log)
#
# These tables are ENTIRELY SEPARATE from the existing Payments tables.
# No foreign keys from these models to the existing payments models.
# No alterations to any existing table.
#
# Security reminder:
#   financial_address and voucher_secret use BinaryField storage
#   (EncryptedCharField subclasses BinaryField). Django migrations
#   generate BinaryField columns for these — that is correct.

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import uuid
from decimal import Decimal

from django.db import migrations, models

import apps.core.fields


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0015_alter_officialdonationreceipt_document"),
    ]

    operations = [
        # ── GovStackBeneficiary ──────────────────────────────────────────
        migrations.CreateModel(
            name="GovStackBeneficiary",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        db_index=True,
                        verbose_name="Created at",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Updated at"),
                ),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "payee_functional_id",
                    models.CharField(
                        db_index=True,
                        help_text="Government-assigned functional identity for this beneficiary. Max 20 chars per GovStack spec. NEVER write to logs.",
                        max_length=20,
                        unique=True,
                        validators=[
                            django.core.validators.RegexValidator(
                                message="BB ID must be 1–20 alphanumeric or hyphen characters.",
                                regex="^[a-zA-Z0-9\\-]{1,20}$",
                            )
                        ],
                        verbose_name="Payee Functional ID",
                    ),
                ),
                (
                    "payment_modality",
                    models.CharField(
                        blank=True,
                        help_text="Two-digit code: 01=bank account, 02=mobile money, 03=voucher, 04=proxy. Optional.",
                        max_length=2,
                        verbose_name="Payment Modality",
                    ),
                ),
                (
                    "financial_address",
                    apps.core.fields.EncryptedCharField(
                        blank=True,
                        editable=True,
                        help_text="Bank account / IBAN / mobile money number. Stored Fernet-encrypted. NEVER in any HTTP response or log.",
                        max_length=512,
                        verbose_name="Financial Address",
                    ),
                ),
                (
                    "source_bb_id",
                    models.CharField(
                        db_index=True,
                        help_text="SourceBBID of the registering Building Block.",
                        max_length=20,
                        validators=[
                            django.core.validators.RegexValidator(
                                message="BB ID must be 1–20 alphanumeric or hyphen characters.",
                                regex="^[a-zA-Z0-9\\-]{1,20}$",
                            )
                        ],
                        verbose_name="Source BB ID",
                    ),
                ),
                (
                    "registering_institution_id",
                    models.CharField(
                        blank=True,
                        help_text="X-Registering-Institution-ID header value at registration time.",
                        max_length=20,
                        verbose_name="Registering Institution ID",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(default=True, verbose_name="Active"),
                ),
            ],
            options={
                "verbose_name": "GovStack Beneficiary",
                "verbose_name_plural": "GovStack Beneficiaries",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="govstackbeneficiary",
            index=models.Index(
                fields=["source_bb_id", "payee_functional_id"],
                name="gs_ben_sourcebb_payee_idx",
            ),
        ),

        # ── BulkPaymentBatch ─────────────────────────────────────────────
        migrations.CreateModel(
            name="BulkPaymentBatch",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        db_index=True,
                        verbose_name="Created at",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Updated at"),
                ),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "request_id",
                    models.CharField(
                        db_index=True,
                        help_text="RequestID from Source BB. Max 16 chars.",
                        max_length=16,
                        validators=[
                            django.core.validators.RegexValidator(
                                message="RequestID must be 1–16 alphanumeric or hyphen characters.",
                                regex="^[a-zA-Z0-9\\-]{1,16}$",
                            )
                        ],
                        verbose_name="Request ID",
                    ),
                ),
                (
                    "source_bb_id",
                    models.CharField(
                        db_index=True,
                        help_text="SourceBBID. Pattern: [a-zA-Z0-9]{10} per harness.",
                        max_length=20,
                        verbose_name="Source BB ID",
                    ),
                ),
                (
                    "batch_id",
                    models.CharField(
                        db_index=True,
                        help_text="BatchID from Source BB. Must be globally unique.",
                        max_length=20,
                        unique=True,
                        verbose_name="Batch ID",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("received", "Received"),
                            ("validating", "Validating"),
                            ("processing", "Processing"),
                            ("completed", "Completed"),
                            ("partial", "Partially Completed"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="received",
                        max_length=20,
                        verbose_name="Status",
                    ),
                ),
                (
                    "callback_url",
                    models.URLField(
                        blank=True,
                        help_text="X-Callback-URL header. CivicOS POSTs async results here.",
                        max_length=500,
                        verbose_name="Callback URL",
                    ),
                ),
                (
                    "correlation_id",
                    models.CharField(
                        blank=True,
                        help_text="X-CorrelationID header.",
                        max_length=12,
                        verbose_name="Correlation ID",
                    ),
                ),
                (
                    "total_amount",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        help_text="Sum of all instruction amounts. Computed on receipt.",
                        max_digits=14,
                        null=True,
                        verbose_name="Total Amount",
                    ),
                ),
                (
                    "completed_amount",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=14,
                        verbose_name="Completed Amount",
                    ),
                ),
                (
                    "failed_amount",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=14,
                        verbose_name="Failed Amount",
                    ),
                ),
                (
                    "result_generated_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="Timestamp when Celery task completed processing.",
                        null=True,
                        verbose_name="Result Generated At",
                    ),
                ),
                (
                    "note",
                    models.CharField(
                        blank=True,
                        help_text="Internal note for partial/failed batches.",
                        max_length=200,
                        verbose_name="Note",
                    ),
                ),
            ],
            options={
                "verbose_name": "Bulk Payment Batch",
                "verbose_name_plural": "Bulk Payment Batches",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="bulkpaymentbatch",
            index=models.Index(
                fields=["status", "created_at"],
                name="gs_batch_status_created_idx",
            ),
        ),

        # ── CreditInstruction ────────────────────────────────────────────
        migrations.CreateModel(
            name="CreditInstruction",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        db_index=True,
                        verbose_name="Created at",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Updated at"),
                ),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="instructions",
                        to="payments.bulkpaymentbatch",
                        verbose_name="Batch",
                    ),
                ),
                (
                    "instruction_id",
                    models.CharField(
                        db_index=True,
                        help_text="InstructionID. Max 16 chars. Unique within batch.",
                        max_length=16,
                        verbose_name="Instruction ID",
                    ),
                ),
                (
                    "payee_functional_id",
                    models.CharField(
                        help_text="Maps to GovStackBeneficiary.payee_functional_id. NEVER write to any log line.",
                        max_length=20,
                        verbose_name="Payee Functional ID",
                    ),
                ),
                (
                    "amount",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=14,
                        verbose_name="Amount",
                    ),
                ),
                (
                    "currency",
                    models.CharField(
                        help_text="ISO 4217 3-letter code. Multi-currency — NOT restricted to CAD.",
                        max_length=3,
                        validators=[
                            django.core.validators.RegexValidator(
                                message="Currency must be a 3-letter ISO 4217 code (e.g. USD, AED, CAD).",
                                regex="^[A-Z]{3}$",
                            )
                        ],
                        verbose_name="Currency",
                    ),
                ),
                (
                    "narration",
                    models.CharField(
                        blank=True,
                        max_length=50,
                        verbose_name="Narration",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("validated", "Validated"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                        verbose_name="Status",
                    ),
                ),
                (
                    "failure_reason",
                    models.CharField(
                        blank=True,
                        max_length=200,
                        verbose_name="Failure Reason",
                    ),
                ),
            ],
            options={
                "verbose_name": "Credit Instruction",
                "verbose_name_plural": "Credit Instructions",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AlterUniqueTogether(
            name="creditinstruction",
            unique_together={("batch", "instruction_id")},
        ),
        migrations.AddIndex(
            model_name="creditinstruction",
            index=models.Index(
                fields=["batch", "status"],
                name="gs_instr_batch_status_idx",
            ),
        ),

        # ── PrepaymentValidationRequest ──────────────────────────────────
        migrations.CreateModel(
            name="PrepaymentValidationRequest",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        db_index=True,
                        verbose_name="Created at",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Updated at"),
                ),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "request_id",
                    models.CharField(
                        db_index=True,
                        max_length=16,
                        unique=True,
                        verbose_name="Request ID",
                    ),
                ),
                (
                    "source_bb_id",
                    models.CharField(max_length=20, verbose_name="Source BB ID"),
                ),
                (
                    "batch_id",
                    models.CharField(db_index=True, max_length=20, verbose_name="Batch ID"),
                ),
                (
                    "instruction_id",
                    models.CharField(max_length=20, verbose_name="Instruction ID"),
                ),
                (
                    "payee_functional_id",
                    models.CharField(
                        help_text="NEVER write to any log line.",
                        max_length=20,
                        verbose_name="Payee Functional ID",
                    ),
                ),
                (
                    "amount",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=14,
                        verbose_name="Amount",
                    ),
                ),
                (
                    "currency",
                    models.CharField(
                        max_length=3,
                        validators=[
                            django.core.validators.RegexValidator(
                                message="Currency must be a 3-letter ISO 4217 code (e.g. USD, AED, CAD).",
                                regex="^[A-Z]{3}$",
                            )
                        ],
                        verbose_name="Currency",
                    ),
                ),
                (
                    "narration",
                    models.CharField(
                        blank=True,
                        max_length=200,
                        verbose_name="Narration",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                        verbose_name="Status",
                    ),
                ),
                (
                    "beneficiary_found",
                    models.BooleanField(
                        help_text="True if PayeeFunctionalID resolved in the ID Mapper.",
                        null=True,
                        verbose_name="Beneficiary Found",
                    ),
                ),
                (
                    "financial_address_valid",
                    models.BooleanField(
                        help_text="True if FinancialAddress is present and non-empty.",
                        null=True,
                        verbose_name="Financial Address Valid",
                    ),
                ),
                (
                    "callback_url",
                    models.URLField(
                        blank=True,
                        max_length=500,
                        verbose_name="Callback URL",
                    ),
                ),
            ],
            options={
                "verbose_name": "Prepayment Validation Request",
                "verbose_name_plural": "Prepayment Validation Requests",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="prepaymentvalidationrequest",
            index=models.Index(
                fields=["batch_id", "status"],
                name="gs_prepay_batch_status_idx",
            ),
        ),

        # ── GovStackVoucher ──────────────────────────────────────────────
        migrations.CreateModel(
            name="GovStackVoucher",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        db_index=True,
                        verbose_name="Created at",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Updated at"),
                ),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "serial_number",
                    models.CharField(
                        db_index=True,
                        help_text="Public voucher serial number. Assigned on pre-activation. Safe to expose in API responses.",
                        max_length=20,
                        unique=True,
                        verbose_name="Serial Number",
                    ),
                ),
                (
                    "voucher_secret",
                    apps.core.fields.EncryptedCharField(
                        blank=True,
                        editable=True,
                        help_text="Secret number for redemption validation. Stored Fernet-encrypted. MUST NEVER appear in any HTTP response or log line.",
                        max_length=512,
                        verbose_name="Voucher Secret",
                    ),
                ),
                (
                    "amount",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=14,
                        verbose_name="Amount",
                    ),
                ),
                (
                    "currency",
                    models.CharField(
                        help_text="ISO 4217 3-letter code. NOT restricted to CAD.",
                        max_length=3,
                        validators=[
                            django.core.validators.RegexValidator(
                                message="Currency must be a 3-letter ISO 4217 code (e.g. USD, AED, CAD).",
                                regex="^[A-Z]{3}$",
                            )
                        ],
                        verbose_name="Currency",
                    ),
                ),
                (
                    "group_code",
                    models.CharField(
                        db_index=True,
                        help_text="Voucher group / program code. e.g. 'Payment Voucher'.",
                        max_length=50,
                        verbose_name="Group Code",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("not_preactivated", "Not Preactivated"),
                            ("preactivated", "Preactivated"),
                            ("activated", "Activated"),
                            ("consumed", "Consumed"),
                            ("blocked", "Blocked"),
                            ("suspended", "Suspended"),
                            ("cancelled", "Cancelled"),
                            ("purged", "Purged"),
                        ],
                        db_index=True,
                        default="preactivated",
                        max_length=20,
                        verbose_name="Status",
                    ),
                ),
                (
                    "issuing_bb",
                    models.CharField(
                        db_index=True,
                        help_text="Gov_Stack_BB identifier that requested pre-activation.",
                        max_length=50,
                        verbose_name="Issuing BB",
                    ),
                ),
                (
                    "registering_institution_id",
                    models.CharField(
                        blank=True,
                        help_text="X-Registering-Institution-Id header at pre-activation time.",
                        max_length=20,
                        verbose_name="Registering Institution ID",
                    ),
                ),
                (
                    "batch_id",
                    models.CharField(
                        blank=True,
                        max_length=12,
                        verbose_name="Batch ID",
                    ),
                ),
                (
                    "payee_functional_id",
                    models.CharField(
                        blank=True,
                        help_text="Beneficiary this voucher was issued to (if applicable). Never log.",
                        max_length=20,
                        verbose_name="Payee Functional ID",
                    ),
                ),
                (
                    "callback_url",
                    models.URLField(
                        blank=True,
                        max_length=500,
                        verbose_name="Callback URL",
                    ),
                ),
                (
                    "expiry_date",
                    models.DateTimeField(
                        blank=True,
                        help_text="Voucher expiry. Default: now + GOVSTACK_VOUCHER_EXPIRY_DAYS (90).",
                        null=True,
                        verbose_name="Expiry Date",
                    ),
                ),
                (
                    "redeemed_by_agent_id",
                    models.CharField(
                        blank=True,
                        max_length=10,
                        verbose_name="Redeemed By Agent ID",
                    ),
                ),
                (
                    "redeemed_merchant_name",
                    models.CharField(
                        blank=True,
                        max_length=200,
                        verbose_name="Redeemed Merchant Name",
                    ),
                ),
                (
                    "redeemed_merchant_bank_details",
                    models.CharField(
                        blank=True,
                        max_length=200,
                        verbose_name="Redeemed Merchant Bank Details",
                    ),
                ),
                (
                    "redeemed_merchant_voucher_group",
                    models.CharField(
                        blank=True,
                        max_length=100,
                        verbose_name="Redeemed Merchant Voucher Group",
                    ),
                ),
                (
                    "redeemed_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="Redeemed At",
                    ),
                ),
                (
                    "redemption_transaction_id",
                    models.CharField(
                        blank=True,
                        max_length=20,
                        verbose_name="Redemption Transaction ID",
                    ),
                ),
            ],
            options={
                "verbose_name": "GovStack Voucher",
                "verbose_name_plural": "GovStack Vouchers",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="govstackvoucher",
            index=models.Index(
                fields=["status", "group_code"],
                name="gs_voucher_status_group_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="govstackvoucher",
            index=models.Index(
                fields=["issuing_bb", "status"],
                name="gs_voucher_bb_status_idx",
            ),
        ),

        # ── GovStackPaymentAuditEntry ────────────────────────────────────
        migrations.CreateModel(
            name="GovStackPaymentAuditEntry",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        db_index=True,
                        verbose_name="Created at",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Updated at"),
                ),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("beneficiary_registered", "Beneficiary Registered"),
                            ("beneficiary_updated", "Beneficiary Updated"),
                            ("batch_received", "Batch Received"),
                            ("batch_completed", "Batch Completed"),
                            ("batch_partial", "Batch Partially Completed"),
                            ("batch_failed", "Batch Failed"),
                            ("instruction_completed", "Instruction Completed"),
                            ("instruction_failed", "Instruction Failed"),
                            ("validation_requested", "Validation Requested"),
                            ("validation_completed", "Validation Completed"),
                            ("voucher_preactivated", "Voucher Preactivated"),
                            ("voucher_activated", "Voucher Activated"),
                            ("voucher_redeemed", "Voucher Redeemed"),
                            ("voucher_cancelled", "Voucher Cancelled"),
                        ],
                        db_index=True,
                        max_length=50,
                        verbose_name="Action",
                    ),
                ),
                (
                    "actor_bb_id",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        help_text="SourceBBID or Gov_Stack_BB that triggered the action.",
                        max_length=50,
                        verbose_name="Actor BB ID",
                    ),
                ),
                (
                    "object_type",
                    models.CharField(
                        db_index=True,
                        help_text="e.g. 'beneficiary', 'batch', 'instruction', 'voucher'",
                        max_length=50,
                        verbose_name="Object Type",
                    ),
                ),
                (
                    "object_pk",
                    models.CharField(
                        help_text="PK of the affected object. Use str(obj.pk) or a non-PII identifier. NEVER include payee_functional_id or financial_address.",
                        max_length=100,
                        verbose_name="Object PK",
                    ),
                ),
                (
                    "request_id",
                    models.CharField(
                        blank=True,
                        help_text="RequestID echoed from the triggering request.",
                        max_length=20,
                        verbose_name="Request ID",
                    ),
                ),
                (
                    "details",
                    models.JSONField(
                        default=dict,
                        help_text="Non-PII metadata about the action. Must NEVER contain payee_functional_id, financial_address, or voucher_secret.",
                        verbose_name="Details",
                    ),
                ),
                (
                    "timestamp",
                    models.DateTimeField(
                        auto_now_add=True,
                        db_index=True,
                        verbose_name="Timestamp",
                    ),
                ),
            ],
            options={
                "verbose_name": "GovStack Payment Audit Entry",
                "verbose_name_plural": "GovStack Payment Audit Entries",
                "ordering": ["-timestamp"],
            },
        ),
        migrations.AddIndex(
            model_name="govstackpaymentauditentry",
            index=models.Index(
                fields=["action", "timestamp"],
                name="gs_audit_action_ts_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="govstackpaymentauditentry",
            index=models.Index(
                fields=["object_type", "object_pk"],
                name="gs_audit_obj_idx",
            ),
        ),
    ]
