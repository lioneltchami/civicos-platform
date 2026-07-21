# Generated manually — Wave 5: GovStack P2G Bill Payments models.
#
# Adds two new models:
#   - GovStackBill: a government bill that can be paid via the P2G API.
#   - GovStackBillPayment: a transfer request / payment record (idempotency key = request_id).
#
# The GovStackPaymentAuditEntry.action choices list was also extended with
# bill_payment_requested and bill_paid.  Those new action strings do NOT
# require a migration because:
#
#   Django's `choices=` parameter on a CharField is pure Python/ORM metadata.
#   It generates NO database-level constraint.  In PostgreSQL, VARCHAR columns
#   have no implicit CHECK constraint limiting values to the choices list unless
#   you explicitly add one (e.g. via models.CheckConstraint or a PostgreSQL ENUM
#   type).  In SQLite, the same applies — no constraint is emitted.  Adding a
#   new choices value therefore only changes Python validation behaviour; no DDL
#   statement is needed and no migration is required.
#
#   If you ever migrate a field to a PostgreSQL ENUM type or add a CheckConstraint
#   that enumerates the allowed values, you WILL need a migration for any new
#   choices — but the default CharField approach does not require one.

import django.core.validators
import django.db.models.deletion
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0019_widen_bulk_correlation_id"),
    ]

    operations = [
        # ── GovStackBill ──────────────────────────────────────────────────────
        migrations.CreateModel(
            name="GovStackBill",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Created At"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Updated At"),
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
                    "bill_id",
                    models.CharField(
                        help_text=(
                            "Government-assigned bill identifier. Used as the {bill_id} "
                            "URL parameter. Safe to expose in API responses."
                        ),
                        max_length=100,
                        unique=True,
                        verbose_name="Bill ID",
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
                    "description",
                    models.CharField(
                        blank=True,
                        help_text=(
                            "Human-readable description of the bill "
                            "(e.g. 'Passport Application Fee')."
                        ),
                        max_length=500,
                        verbose_name="Description",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("unpaid", "Unpaid"),
                            ("paid", "Paid"),
                            ("overdue", "Overdue"),
                            ("cancelled", "Cancelled"),
                        ],
                        db_index=True,
                        default="unpaid",
                        max_length=20,
                        verbose_name="Status",
                    ),
                ),
                (
                    "due_date",
                    models.DateField(
                        blank=True,
                        help_text="Date by which the bill must be paid. Optional.",
                        null=True,
                        verbose_name="Due Date",
                    ),
                ),
                (
                    "correlation_id",
                    models.CharField(
                        blank=True,
                        help_text="Optional cross-system correlation identifier for this bill.",
                        max_length=100,
                        verbose_name="Correlation ID",
                    ),
                ),
            ],
            options={
                "verbose_name": "GovStack Bill",
                "verbose_name_plural": "GovStack Bills",
                "indexes": [
                    models.Index(
                        fields=["status", "due_date"],
                        name="gs_bill_status_due_idx",
                    )
                ],
                "constraints": [
                    models.CheckConstraint(
                        check=models.Q(amount__gt=0),
                        name="gs_bill_amount_positive",
                    )
                ],
            },
        ),
        # ── GovStackBillPayment ───────────────────────────────────────────────
        migrations.CreateModel(
            name="GovStackBillPayment",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Created At"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Updated At"),
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
                        help_text=(
                            "Caller-supplied idempotency key. Duplicate request_ids return "
                            "HTTP 400 instead of creating duplicate payment records."
                        ),
                        max_length=100,
                        unique=True,
                        verbose_name="Request ID",
                    ),
                ),
                (
                    "bill",
                    models.ForeignKey(
                        help_text="",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="payments",
                        to="payments.govstackbill",
                        verbose_name="Bill",
                    ),
                ),
                (
                    "bill_inquiry_request_id",
                    models.CharField(
                        blank=True,
                        help_text="Request ID from a prior GET /bills/{billId} inquiry (optional).",
                        max_length=100,
                        verbose_name="Bill Inquiry Request ID",
                    ),
                ),
                (
                    "payment_reference_id",
                    models.CharField(
                        blank=True,
                        help_text="Mobile money / financial network payment reference (optional).",
                        max_length=100,
                        verbose_name="Payment Reference ID",
                    ),
                ),
                (
                    "correlation_id",
                    models.CharField(
                        blank=True,
                        help_text="X-CorrelationID header value for cross-system tracing.",
                        max_length=100,
                        verbose_name="Correlation ID",
                    ),
                ),
                (
                    "payer_fi_id",
                    models.CharField(
                        blank=True,
                        help_text="X-PayerFI-Id header: financial institution that originated the payment.",
                        max_length=100,
                        verbose_name="Payer FI ID",
                    ),
                ),
                (
                    "platform_tenant_id",
                    models.CharField(
                        blank=True,
                        help_text="X-Platform-TenantId header value.",
                        max_length=100,
                        verbose_name="Platform Tenant ID",
                    ),
                ),
                (
                    "amount",
                    models.DecimalField(
                        decimal_places=2,
                        help_text="Snapshotted from GovStackBill.amount at payment time.",
                        max_digits=14,
                        verbose_name="Amount",
                    ),
                ),
                (
                    "currency",
                    models.CharField(
                        help_text="Snapshotted from GovStackBill.currency at payment time.",
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
            ],
            options={
                "verbose_name": "GovStack Bill Payment",
                "verbose_name_plural": "GovStack Bill Payments",
                "indexes": [
                    models.Index(
                        fields=["bill", "status"],
                        name="gs_billpay_bill_status_idx",
                    ),
                    models.Index(
                        fields=["status", "created_at"],
                        name="gs_billpay_status_created_idx",
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        check=models.Q(amount__gt=0),
                        name="gs_billpay_amount_positive",
                    )
                ],
            },
        ),
    ]
