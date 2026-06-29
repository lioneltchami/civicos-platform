"""
Payments building block — data models.

Security invariants:
- No raw PANs, no CVVs — card_last_four (4 chars) only.
- All monetary values: DecimalField (never FloatField).
- PaymentAuditEntry and OfficialDonationReceipt are append-only.
- pdf_path is stored but never exposed in API, templates, or admin lists.
- Currency always CAD.
- PII never written to application logs.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimestampedModel


# ---------------------------------------------------------------------------
# Module-level constants reused across models
# ---------------------------------------------------------------------------

GATEWAY_STRIPE = "stripe"
GATEWAY_MONERIS = "moneris"
GATEWAY_CHOICES = [
    (GATEWAY_STRIPE, "Stripe"),
    (GATEWAY_MONERIS, "Moneris"),
]


# ---------------------------------------------------------------------------
# PaymentIntent
# ---------------------------------------------------------------------------

class PaymentIntent(TimestampedModel):
    """
    Represents a citizen's intention to pay before the gateway processes it.
    Drives status-machine transitions; all downstream models link back here.
    """

    # Status constants
    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_REFUNDED = "refunded"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_REFUNDED, "Refunded"),
    ]

    # Gateway constants
    GATEWAY_STRIPE = GATEWAY_STRIPE
    GATEWAY_MONERIS = GATEWAY_MONERIS
    GATEWAY_CHOICES = GATEWAY_CHOICES

    # Purpose constants
    PURPOSE_SERVICE_FEE = "service_fee"
    PURPOSE_DONATION = "donation"
    PURPOSE_FINE = "fine"

    PURPOSE_CHOICES = [
        (PURPOSE_SERVICE_FEE, "Service Fee"),
        (PURPOSE_DONATION, "Donation"),
        (PURPOSE_FINE, "Fine / Penalty"),
    ]

    # Allowed status transitions
    ALLOWED_TRANSITIONS = {
        STATUS_PENDING: [STATUS_PROCESSING, STATUS_CANCELLED],
        STATUS_PROCESSING: [STATUS_COMPLETED, STATUS_FAILED],
        STATUS_COMPLETED: [STATUS_REFUNDED],
        STATUS_FAILED: [],
        STATUS_CANCELLED: [],
        STATUS_REFUNDED: [],
    }

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    reference = models.CharField(
        max_length=20,
        unique=True,
        blank=True,
        help_text="Human-readable reference: PMT-YYYY-NNNNNN. Auto-generated on save.",
    )
    payer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="payment_intents",
        verbose_name=_("Payer"),
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name=_("Amount"),
    )
    tax_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name=_("Tax Amount"),
    )
    currency = models.CharField(
        max_length=3,
        default="CAD",
        verbose_name=_("Currency"),
    )
    purpose = models.CharField(
        max_length=30,
        choices=PURPOSE_CHOICES,
        verbose_name=_("Purpose"),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name=_("Status"),
    )
    gateway = models.CharField(
        max_length=20,
        choices=GATEWAY_CHOICES,
        default=GATEWAY_STRIPE,
        verbose_name=_("Payment Gateway"),
    )
    gateway_intent_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Gateway Intent ID"),
        help_text="ID returned by the payment gateway (e.g. Stripe PaymentIntent ID).",
    )
    idempotency_key = models.UUIDField(
        unique=True,
        default=uuid.uuid4,
        verbose_name=_("Idempotency Key"),
        help_text="Prevents duplicate charges on retry.",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("Metadata"),
        help_text="Arbitrary key-value data for gateway or app context.",
    )
    failure_reason = models.CharField(
        max_length=500,
        blank=True,
        verbose_name=_("Failure Reason"),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Payment Intent")
        verbose_name_plural = _("Payment Intents")

    def __str__(self) -> str:
        return self.reference or str(self.id)

    def save(self, *args, **kwargs):
        if not self.reference:
            # id is already set by default=uuid.uuid4 before save()
            self.reference = (
                f"PMT-{timezone.now().year}-"
                f"{str(self.id.int % 1_000_000).zfill(6)}"
            )
        super().save(*args, **kwargs)

    def transition(self, new_status: str) -> None:
        """
        Advance the status machine to new_status.
        Raises ValueError if the transition is not permitted.
        """
        allowed = self.ALLOWED_TRANSITIONS.get(self.status, [])
        if new_status not in allowed:
            raise ValueError(
                f"Cannot transition PaymentIntent from '{self.status}' to '{new_status}'."
            )
        self.status = new_status
        self.save(update_fields=["status", "updated_at"])


# ---------------------------------------------------------------------------
# Payment
# ---------------------------------------------------------------------------

class Payment(TimestampedModel):
    """
    The confirmed, captured payment record produced after a gateway charge succeeds.
    One-to-one with a PaymentIntent.
    """

    PAYMENT_METHOD_CARD = "card"
    PAYMENT_METHOD_BANK = "bank_transfer"
    PAYMENT_METHOD_CHOICES = [
        (PAYMENT_METHOD_CARD, "Card"),
        (PAYMENT_METHOD_BANK, "Bank Transfer"),
    ]

    CARD_BRAND_VISA = "visa"
    CARD_BRAND_MASTERCARD = "mastercard"
    CARD_BRAND_AMEX = "amex"
    CARD_BRAND_INTERAC = "interac"
    CARD_BRAND_OTHER = "other"
    CARD_BRAND_CHOICES = [
        (CARD_BRAND_VISA, "Visa"),
        (CARD_BRAND_MASTERCARD, "Mastercard"),
        (CARD_BRAND_AMEX, "Amex"),
        (CARD_BRAND_INTERAC, "Interac"),
        (CARD_BRAND_OTHER, "Other"),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    intent = models.OneToOneField(
        PaymentIntent,
        on_delete=models.PROTECT,
        related_name="payment",
        verbose_name=_("Payment Intent"),
    )
    gateway_charge_id = models.CharField(
        max_length=255,
        unique=True,
        verbose_name=_("Gateway Charge ID"),
        help_text="Charge/transaction ID returned by the gateway.",
    )
    amount_paid = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name=_("Amount Paid"),
    )
    processor_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name=_("Processor Fee"),
        help_text="Gateway processing fee deducted from gross amount.",
    )
    net_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name=_("Net Amount"),
        help_text="Amount after processor fee: amount_paid - processor_fee.",
    )
    payment_method_type = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        verbose_name=_("Payment Method Type"),
    )
    card_last_four = models.CharField(
        max_length=4,
        blank=True,
        null=True,
        verbose_name=_("Card Last Four"),
        help_text="Last 4 digits of the card used. Never store full PAN.",
    )
    card_brand = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        choices=CARD_BRAND_CHOICES,
        verbose_name=_("Card Brand"),
    )
    paid_at = models.DateTimeField(
        verbose_name=_("Paid At"),
        db_index=True,
    )

    class Meta:
        ordering = ["-paid_at"]
        verbose_name = _("Payment")
        verbose_name_plural = _("Payments")

    def __str__(self) -> str:
        return f"Payment {self.gateway_charge_id}"


# ---------------------------------------------------------------------------
# Refund
# ---------------------------------------------------------------------------

class Refund(TimestampedModel):
    """
    A partial or full refund against a completed Payment.
    Authorized staff must be recorded on every refund (audit requirement).
    """

    REASON_DUPLICATE = "duplicate"
    REASON_FRAUDULENT = "fraudulent"
    REASON_CUSTOMER = "requested_by_customer"
    REASON_NOT_RENDERED = "service_not_rendered"

    REASON_CHOICES = [
        (REASON_DUPLICATE, "Duplicate"),
        (REASON_FRAUDULENT, "Fraudulent"),
        (REASON_CUSTOMER, "Requested by Customer"),
        (REASON_NOT_RENDERED, "Service Not Rendered"),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name="refunds",
        verbose_name=_("Payment"),
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name=_("Refund Amount"),
    )
    reason = models.CharField(
        max_length=30,
        choices=REASON_CHOICES,
        verbose_name=_("Reason"),
    )
    gateway_refund_id = models.CharField(
        max_length=255,
        unique=True,
        verbose_name=_("Gateway Refund ID"),
    )
    refunded_at = models.DateTimeField(
        verbose_name=_("Refunded At"),
        db_index=True,
    )
    authorized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="authorized_refunds",
        verbose_name=_("Authorized By"),
    )
    notes = models.TextField(
        blank=True,
        verbose_name=_("Notes"),
    )

    class Meta:
        ordering = ["-refunded_at"]
        verbose_name = _("Refund")
        verbose_name_plural = _("Refunds")

    def __str__(self) -> str:
        return f"Refund {self.gateway_refund_id}"


# ---------------------------------------------------------------------------
# WebhookEvent
# ---------------------------------------------------------------------------

class WebhookEvent(TimestampedModel):
    """
    Raw inbound webhook payloads from payment gateways.
    Stored before processing so no event is ever lost.
    signature_verified must be True before payload is acted upon.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    gateway = models.CharField(
        max_length=20,
        choices=GATEWAY_CHOICES,
        verbose_name=_("Gateway"),
    )
    event_type = models.CharField(
        max_length=100,
        verbose_name=_("Event Type"),
        help_text="e.g. payment_intent.succeeded",
    )
    gateway_event_id = models.CharField(
        max_length=255,
        unique=True,
        verbose_name=_("Gateway Event ID"),
        help_text="Idempotency key from gateway — prevents double-processing.",
    )
    payload = models.JSONField(
        verbose_name=_("Payload"),
        help_text="Full event payload as received from gateway.",
    )
    signature_verified = models.BooleanField(
        default=False,
        verbose_name=_("Signature Verified"),
        help_text="Only process events where signature has been cryptographically verified.",
    )
    processed = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name=_("Processed"),
    )
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Processed At"),
    )
    error = models.TextField(
        blank=True,
        verbose_name=_("Error"),
        help_text="Last processing error, if any.",
    )
    retry_count = models.IntegerField(
        default=0,
        verbose_name=_("Retry Count"),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Webhook Event")
        verbose_name_plural = _("Webhook Events")

    def __str__(self) -> str:
        return f"{self.gateway}:{self.event_type} ({self.gateway_event_id})"


# ---------------------------------------------------------------------------
# PaymentAuditEntry — APPEND-ONLY
# ---------------------------------------------------------------------------

class PaymentAuditEntryQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("PaymentAuditEntry is append-only and cannot be updated.")

    def delete(self):
        raise ValueError("PaymentAuditEntry is append-only and cannot be deleted.")


class PaymentAuditEntryManager(models.Manager):
    def get_queryset(self):
        return PaymentAuditEntryQuerySet(self.model, using=self._db)


class PaymentAuditEntry(TimestampedModel):
    """
    Immutable audit log for all payment-related events.
    Records may never be updated or deleted. Enforced at model, queryset,
    and admin levels.
    """

    ACTION_CHOICES = [
        ("intent_created", "Intent Created"),
        ("intent_processing", "Intent Processing"),
        ("payment_completed", "Payment Completed"),
        ("payment_failed", "Payment Failed"),
        ("payment_cancelled", "Payment Cancelled"),
        ("refund_requested", "Refund Requested"),
        ("refund_completed", "Refund Completed"),
        ("refund_failed", "Refund Failed"),
        ("donation_created", "Donation Created"),
        ("receipt_issued", "Receipt Issued"),
        ("receipt_cancelled", "Receipt Cancelled"),
        ("receipt_superseded", "Receipt Superseded"),
        ("recurring_plan_created", "Recurring Plan Created"),
        ("recurring_plan_paused", "Recurring Plan Paused"),
        ("recurring_plan_cancelled", "Recurring Plan Cancelled"),
        ("webhook_received", "Webhook Received"),
        ("webhook_processed", "Webhook Processed"),
        ("webhook_failed", "Webhook Failed"),
    ]

    objects = PaymentAuditEntryManager()

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payment_audit_entries",
        verbose_name=_("Actor"),
        help_text="Staff or citizen who triggered the action, if applicable.",
    )
    action = models.CharField(
        max_length=30,
        choices=ACTION_CHOICES,
        db_index=True,
        verbose_name=_("Action"),
    )
    payment_intent = models.ForeignKey(
        PaymentIntent,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_entries",
        verbose_name=_("Payment Intent"),
    )
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_entries",
        verbose_name=_("Payment"),
    )
    refund = models.ForeignKey(
        Refund,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_entries",
        verbose_name=_("Refund"),
    )
    actor_ip = models.CharField(
        max_length=45,
        blank=True,
        verbose_name=_("Actor IP"),
        help_text="Always masked before storage. Never log full IP with PII.",
    )
    timestamp = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name=_("Timestamp"),
    )
    details = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("Details"),
    )

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = _("Payment Audit Entry")
        verbose_name_plural = _("Payment Audit Entries")
        constraints = [
            models.CheckConstraint(
                check=models.Q(
                    action__in=[
                        "intent_created",
                        "intent_processing",
                        "payment_completed",
                        "payment_failed",
                        "payment_cancelled",
                        "refund_requested",
                        "refund_completed",
                        "refund_failed",
                        "donation_created",
                        "receipt_issued",
                        "receipt_cancelled",
                        "receipt_superseded",
                        "recurring_plan_created",
                        "recurring_plan_paused",
                        "recurring_plan_cancelled",
                        "webhook_received",
                        "webhook_processed",
                        "webhook_failed",
                    ]
                ),
                name="payments_audit_action_valid",
            )
        ]

    def __str__(self) -> str:
        return f"{self.action} @ {self.timestamp}"

    def save(self, *args, **kwargs):
        if self.pk and self.__class__.objects.filter(pk=self.pk).exists():
            raise ValueError(
                "PaymentAuditEntry is append-only and cannot be modified."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("PaymentAuditEntry is append-only and cannot be deleted.")


# ---------------------------------------------------------------------------
# TenantPaymentConfig — singleton
# ---------------------------------------------------------------------------

_SINGLETON_PK = uuid.UUID("00000000-0000-0000-0000-000000000001")


class TenantPaymentConfig(TimestampedModel):
    """
    Per-tenant Stripe/Moneris configuration.
    One row exists per deployment. Use get_solo() to fetch or create it.
    webhook_endpoint_secret is stored but never exposed in API or template output.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    use_connect = models.BooleanField(
        default=False,
        verbose_name=_("Use Stripe Connect"),
        help_text="True = Stripe Connect multi-tenant mode.",
    )
    stripe_publishable_key = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Stripe Publishable Key"),
    )
    stripe_connect_account_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Stripe Connect Account ID"),
        help_text="Stripe platform account ID (Connect mode).",
    )
    webhook_endpoint_secret = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Webhook Endpoint Secret"),
        help_text="Changing this requires rotating the Stripe webhook signing secret and redeploying.",
    )
    is_test_mode = models.BooleanField(
        default=True,
        verbose_name=_("Test Mode"),
        help_text="Use Stripe test keys. Must be False in production.",
    )

    class Meta:
        verbose_name = _("Tenant Payment Config")
        verbose_name_plural = _("Tenant Payment Config")

    def __str__(self) -> str:
        mode = "TEST" if self.is_test_mode else "LIVE"
        return f"TenantPaymentConfig ({mode})"

    @classmethod
    def get_solo(cls) -> "TenantPaymentConfig":
        """Return the singleton config row, creating it if it does not exist."""
        obj, _ = cls.objects.get_or_create(pk=_SINGLETON_PK)
        return obj


# ---------------------------------------------------------------------------
# CharitySettings
# ---------------------------------------------------------------------------

class CharitySettings(TimestampedModel):
    """
    CRA-compliant charity registration details.
    Used to populate official donation receipts.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    charity_legal_name = models.CharField(
        max_length=255,
        verbose_name=_("Charity Legal Name"),
    )
    charity_registration_number = models.CharField(
        max_length=20,
        verbose_name=_("CRA Registration Number"),
        help_text="Format: 123456789 RR 0001",
    )
    charity_address_line1 = models.CharField(
        max_length=255,
        verbose_name=_("Address Line 1"),
    )
    charity_city = models.CharField(
        max_length=100,
        verbose_name=_("City"),
    )
    charity_province = models.CharField(
        max_length=2,
        verbose_name=_("Province"),
        help_text="ISO 3166-2 province code (e.g. ON, QC).",
    )
    charity_postal_code = models.CharField(
        max_length=10,
        verbose_name=_("Postal Code"),
    )
    place_of_issue = models.CharField(
        max_length=100,
        verbose_name=_("Place of Issue"),
        help_text="City where receipts are issued (CRA requirement).",
    )
    authorized_signatory_name = models.CharField(
        max_length=255,
        verbose_name=_("Authorized Signatory Name"),
    )
    authorized_signatory_title = models.CharField(
        max_length=255,
        verbose_name=_("Authorized Signatory Title"),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("Is Active"),
    )
    name_en = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Display Name (English)"),
    )
    name_fr = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Display Name (French)"),
    )

    class Meta:
        verbose_name = _("Charity Settings")
        verbose_name_plural = _("Charity Settings")

    def __str__(self) -> str:
        return self.charity_legal_name

    def get_name(self) -> str:
        from django.utils.translation import get_language
        if get_language() and get_language().startswith("fr"):
            return self.name_fr or self.name_en
        return self.name_en or self.charity_legal_name


# ---------------------------------------------------------------------------
# FeeSchedule
# ---------------------------------------------------------------------------

class FeeSchedule(TimestampedModel):
    """
    Versioned fee schedule for government services.
    Fees are snapshotted on ServiceFeePayment at time of payment — never
    computed from the live schedule retroactively.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    service_type = models.CharField(
        max_length=100,
        verbose_name=_("Service Type"),
        help_text="Matches service request service_type slug.",
    )
    fee_code = models.CharField(
        max_length=50,
        unique=True,
        verbose_name=_("Fee Code"),
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name=_("Amount"),
    )
    is_taxable = models.BooleanField(
        default=False,
        verbose_name=_("Is Taxable"),
    )
    province = models.CharField(
        max_length=2,
        verbose_name=_("Province"),
        help_text="ISO 3166-2 province code.",
    )
    description_en = models.CharField(
        max_length=255,
        verbose_name=_("Description (English)"),
    )
    description_fr = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Description (French)"),
    )
    effective_date = models.DateField(
        verbose_name=_("Effective Date"),
    )
    expiry_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("Expiry Date"),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("Is Active"),
    )

    class Meta:
        verbose_name = _("Fee Schedule")
        verbose_name_plural = _("Fee Schedules")
        ordering = ["fee_code", "-effective_date"]

    def __str__(self) -> str:
        return f"{self.fee_code} ({self.province}) — ${self.amount}"

    @classmethod
    def get_current(cls, fee_code: str, province: str, as_of=None) -> "FeeSchedule | None":
        """Return the currently active fee for the given fee_code and province."""
        if as_of is None:
            as_of = timezone.now().date()
        qs = cls.objects.filter(
            fee_code=fee_code,
            province=province,
            is_active=True,
            effective_date__lte=as_of,
        ).filter(
            models.Q(expiry_date__isnull=True) | models.Q(expiry_date__gte=as_of)
        ).order_by("-effective_date")
        return qs.first()

    def get_description(self) -> str:
        from django.utils.translation import get_language
        if get_language() and get_language().startswith("fr"):
            return self.description_fr or self.description_en
        return self.description_en


# ---------------------------------------------------------------------------
# TaxRate
# ---------------------------------------------------------------------------

class TaxRate(TimestampedModel):
    """
    Canadian provincial/territorial tax rates.
    One row per province (unique). Use get_for_province() for lookups.
    Seeded via: python manage.py seed_tax_rates
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    province = models.CharField(
        max_length=2,
        unique=True,
        verbose_name=_("Province"),
        help_text="ISO 3166-2 province/territory code.",
    )
    federal_rate = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        verbose_name=_("Federal Rate"),
        help_text="e.g. 0.0500 for 5% GST.",
    )
    provincial_rate = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        verbose_name=_("Provincial Rate"),
        help_text="e.g. 0.0800 for 8% PST.",
    )
    combined_rate = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        verbose_name=_("Combined Rate"),
        help_text="Total tax rate applied to taxable fees.",
    )
    tax_name_en = models.CharField(
        max_length=50,
        verbose_name=_("Tax Name (English)"),
        help_text="e.g. 'HST', 'GST + PST'.",
    )
    tax_name_fr = models.CharField(
        max_length=50,
        verbose_name=_("Tax Name (French)"),
    )
    effective_date = models.DateField(
        verbose_name=_("Effective Date"),
    )

    class Meta:
        verbose_name = _("Tax Rate")
        verbose_name_plural = _("Tax Rates")
        ordering = ["province"]

    def __str__(self) -> str:
        return f"{self.province} — {self.tax_name_en} ({self.combined_rate * 100:.2f}%)"

    @classmethod
    def get_for_province(cls, province: str) -> "TaxRate | None":
        return cls.objects.filter(province=province).order_by("-effective_date").first()

    def get_tax_name(self) -> str:
        from django.utils.translation import get_language
        if get_language() and get_language().startswith("fr"):
            return self.tax_name_fr or self.tax_name_en
        return self.tax_name_en


# ---------------------------------------------------------------------------
# ServiceFeePayment
# ---------------------------------------------------------------------------

class ServiceFeePayment(TimestampedModel):
    """
    Links a PaymentIntent to a portal ServiceRequest.
    Fee amounts are snapshotted at payment time — never recomputed from live schedule.
    service_request_id is a plain UUIDField (no FK) to avoid cross-app coupling.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    payment_intent = models.OneToOneField(
        PaymentIntent,
        on_delete=models.PROTECT,
        related_name="service_fee_payment",
        verbose_name=_("Payment Intent"),
    )
    service_request_id = models.UUIDField(
        verbose_name=_("Service Request ID"),
        help_text="Portal ServiceRequest PK — no FK to avoid cross-app coupling.",
    )
    fee_code = models.CharField(
        max_length=50,
        verbose_name=_("Fee Code"),
        help_text="Snapshot of FeeSchedule.fee_code at time of payment.",
    )
    base_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name=_("Base Amount"),
    )
    tax_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name=_("Tax Amount"),
    )
    tax_rate_applied = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        default=Decimal("0.0000"),
        verbose_name=_("Tax Rate Applied"),
        help_text="Snapshot of TaxRate.combined_rate at time of payment.",
    )
    description_en = models.CharField(
        max_length=255,
        verbose_name=_("Description (English)"),
        help_text="Snapshot of fee description (EN).",
    )
    description_fr = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Description (French)"),
        help_text="Snapshot of fee description (FR).",
    )

    class Meta:
        verbose_name = _("Service Fee Payment")
        verbose_name_plural = _("Service Fee Payments")

    def __str__(self) -> str:
        return f"ServiceFee {self.fee_code} for request {self.service_request_id}"


# ---------------------------------------------------------------------------
# DonationCampaign
# ---------------------------------------------------------------------------

class DonationCampaign(TimestampedModel):
    """
    A fundraising campaign. Donations are linked to campaigns.
    OQ-8: advantage_amount is the default FMV of any benefit given to donors.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    slug = models.SlugField(
        unique=True,
        verbose_name=_("Slug"),
    )
    name_en = models.CharField(
        max_length=255,
        verbose_name=_("Name (English)"),
    )
    name_fr = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Name (French)"),
    )
    description_en = models.TextField(
        blank=True,
        verbose_name=_("Description (English)"),
    )
    description_fr = models.TextField(
        blank=True,
        verbose_name=_("Description (French)"),
    )
    goal_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("Goal Amount"),
    )
    start_date = models.DateField(
        verbose_name=_("Start Date"),
    )
    end_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("End Date"),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("Is Active"),
    )
    sort_order = models.IntegerField(
        default=0,
        verbose_name=_("Sort Order"),
    )
    advantage_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name=_("Default Advantage Amount"),
        help_text=(
            "Default advantage amount for donations to this campaign "
            "(FMV of benefit received by donor). OQ-8."
        ),
    )
    advantage_description_en = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Advantage Description (English)"),
    )
    advantage_description_fr = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Advantage Description (French)"),
    )

    class Meta:
        verbose_name = _("Donation Campaign")
        verbose_name_plural = _("Donation Campaigns")
        ordering = ["sort_order", "name_en"]

    def __str__(self) -> str:
        return self.name_en

    def get_name(self) -> str:
        from django.utils.translation import get_language
        if get_language() and get_language().startswith("fr"):
            return self.name_fr or self.name_en
        return self.name_en

    def get_description(self) -> str:
        from django.utils.translation import get_language
        if get_language() and get_language().startswith("fr"):
            return self.description_fr or self.description_en
        return self.description_en

    def get_advantage_description(self) -> str:
        from django.utils.translation import get_language
        if get_language() and get_language().startswith("fr"):
            return self.advantage_description_fr or self.advantage_description_en
        return self.advantage_description_en


# ---------------------------------------------------------------------------
# RecurringGiftPlan — defined before Donation (forward reference)
# ---------------------------------------------------------------------------

FREQUENCY_MONTHLY = "monthly"
FREQUENCY_QUARTERLY = "quarterly"
FREQUENCY_ANNUALLY = "annually"
FREQUENCY_CHOICES = [
    (FREQUENCY_MONTHLY, "Monthly"),
    (FREQUENCY_QUARTERLY, "Quarterly"),
    (FREQUENCY_ANNUALLY, "Annually"),
]

PLAN_STATUS_ACTIVE = "active"
PLAN_STATUS_PAUSED = "paused"
PLAN_STATUS_CANCELLED = "cancelled"
PLAN_STATUS_CHOICES = [
    (PLAN_STATUS_ACTIVE, "Active"),
    (PLAN_STATUS_PAUSED, "Paused"),
    (PLAN_STATUS_CANCELLED, "Cancelled"),
]


class RecurringGiftPlan(TimestampedModel):
    """
    A citizen's recurring donation subscription.
    gateway_subscription_id ties it to a Stripe Subscription or Moneris equivalent.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    donor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recurring_plans",
        verbose_name=_("Donor"),
    )
    campaign = models.ForeignKey(
        DonationCampaign,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="recurring_plans",
        verbose_name=_("Campaign"),
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name=_("Amount per Period"),
    )
    frequency = models.CharField(
        max_length=20,
        choices=FREQUENCY_CHOICES,
        verbose_name=_("Frequency"),
    )
    next_charge_date = models.DateField(
        verbose_name=_("Next Charge Date"),
    )
    gateway_subscription_id = models.CharField(
        max_length=255,
        unique=True,
        verbose_name=_("Gateway Subscription ID"),
    )
    gateway_payment_method_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Gateway Payment Method ID"),
    )
    status = models.CharField(
        max_length=20,
        choices=PLAN_STATUS_CHOICES,
        default=PLAN_STATUS_ACTIVE,
        db_index=True,
        verbose_name=_("Status"),
    )
    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Cancelled At"),
    )
    cancellation_reason = models.CharField(
        max_length=500,
        blank=True,
        verbose_name=_("Cancellation Reason"),
    )

    class Meta:
        verbose_name = _("Recurring Gift Plan")
        verbose_name_plural = _("Recurring Gift Plans")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"RecurringPlan {self.gateway_subscription_id} ({self.status})"


# ---------------------------------------------------------------------------
# Donation
# ---------------------------------------------------------------------------

DONATION_STATUS_PENDING = "pending"
DONATION_STATUS_COMPLETED = "completed"
DONATION_STATUS_FAILED = "failed"
DONATION_STATUS_REFUNDED = "refunded"
DONATION_STATUS_CHOICES = [
    (DONATION_STATUS_PENDING, "Pending"),
    (DONATION_STATUS_COMPLETED, "Completed"),
    (DONATION_STATUS_FAILED, "Failed"),
    (DONATION_STATUS_REFUNDED, "Refunded"),
]

DEDICATION_IN_MEMORY = "in_memory_of"
DEDICATION_IN_HONOUR = "in_honour_of"
DEDICATION_TYPE_CHOICES = [
    (DEDICATION_IN_MEMORY, "In Memory Of"),
    (DEDICATION_IN_HONOUR, "In Honour Of"),
]


class Donation(TimestampedModel):
    """
    A single donation event linked to a PaymentIntent.
    eligible_amount = amount - advantage_amount (CRA receipt rule).
    donor_name_snapshot and donor_address_snapshot satisfy CRA s.3500 requirements.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    payment_intent = models.ForeignKey(
        PaymentIntent,
        on_delete=models.PROTECT,
        related_name="donations",
        verbose_name=_("Payment Intent"),
    )
    donor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="donations",
        verbose_name=_("Donor"),
    )
    campaign = models.ForeignKey(
        DonationCampaign,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="donations",
        verbose_name=_("Campaign"),
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name=_("Donation Amount"),
    )
    advantage_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name=_("Advantage Amount"),
        help_text="FMV of benefit received by donor (OQ-8). Reduces eligible tax credit.",
    )
    advantage_description = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Advantage Description"),
    )
    eligible_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name=_("Eligible Amount"),
        help_text="amount - advantage_amount. Computed and stored on save().",
    )
    is_recurring = models.BooleanField(
        default=False,
        verbose_name=_("Is Recurring"),
    )
    recurring_plan = models.ForeignKey(
        RecurringGiftPlan,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="donations",
        verbose_name=_("Recurring Plan"),
    )
    dedication_type = models.CharField(
        max_length=20,
        choices=DEDICATION_TYPE_CHOICES,
        null=True,
        blank=True,
        verbose_name=_("Dedication Type"),
    )
    dedication_name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Dedication Name"),
    )
    is_anonymous = models.BooleanField(
        default=False,
        verbose_name=_("Is Anonymous"),
    )
    status = models.CharField(
        max_length=20,
        choices=DONATION_STATUS_CHOICES,
        default=DONATION_STATUS_PENDING,
        db_index=True,
        verbose_name=_("Status"),
    )
    donor_name_snapshot = models.CharField(
        max_length=255,
        verbose_name=_("Donor Legal Name (Snapshot)"),
        help_text="Legal name at time of donation — CRA requirement.",
    )
    donor_address_snapshot = models.TextField(
        verbose_name=_("Donor Address (Snapshot)"),
        help_text="Full mailing address at time of donation — CRA requirement.",
    )

    class Meta:
        verbose_name = _("Donation")
        verbose_name_plural = _("Donations")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Donation ${self.amount} ({self.status})"

    def save(self, *args, **kwargs):
        # CRA rule: eligible amount must never be negative
        self.eligible_amount = max(
            Decimal("0.00"),
            self.amount - self.advantage_amount,
        )
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# OfficialDonationReceipt — APPEND-ONLY with status transition exception
# ---------------------------------------------------------------------------

class OfficialDonationReceiptQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("OfficialDonationReceipt records cannot be bulk-updated.")

    def delete(self):
        raise ValueError("OfficialDonationReceipt records cannot be deleted.")


class OfficialDonationReceiptManager(models.Manager):
    def get_queryset(self):
        return OfficialDonationReceiptQuerySet(self.model, using=self._db)


class OfficialDonationReceipt(TimestampedModel):
    """
    CRA-compliant official donation tax receipt.
    Append-only: core financial fields are immutable after issue.
    Only status, cancellation_reason, superseded_by, and pdf_path may change.
    Cancel and re-issue to correct errors.
    pdf_path is stored internally and MUST NEVER be exposed in any API,
    template output, or admin list_display.
    """

    RECEIPT_STATUS_ISSUED = "issued"
    RECEIPT_STATUS_CANCELLED = "cancelled"
    RECEIPT_STATUS_SUPERSEDED = "superseded"

    RECEIPT_STATUS_CHOICES = [
        (RECEIPT_STATUS_ISSUED, "Issued"),
        (RECEIPT_STATUS_CANCELLED, "Cancelled"),
        (RECEIPT_STATUS_SUPERSEDED, "Superseded"),
    ]

    # Fields that must never change after first save
    _IMMUTABLE_FIELDS = frozenset({
        "donation_id",
        "serial_number",
        "donor_legal_name",
        "donor_address_line1",
        "donor_city",
        "donor_province",
        "donor_postal_code",
        "donation_date",
        "eligible_amount",
        "advantage_amount",
        "advantage_description",
        "charity_legal_name",
        "charity_registration_number",
        "charity_address",
        "place_of_issue",
        "authorized_signatory_name",
        "authorized_signatory_title",
        "is_annual_consolidated",
    })

    objects = OfficialDonationReceiptManager()

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    donation = models.ForeignKey(
        Donation,
        on_delete=models.PROTECT,
        related_name="receipts",
        verbose_name=_("Donation"),
    )
    serial_number = models.CharField(
        max_length=20,
        unique=True,
        verbose_name=_("Serial Number"),
        help_text="YYYY-NNNNNN format, generated from DB sequence payments_receipt_serial_seq.",
    )
    status = models.CharField(
        max_length=20,
        choices=RECEIPT_STATUS_CHOICES,
        default=RECEIPT_STATUS_ISSUED,
        db_index=True,
        verbose_name=_("Status"),
    )
    superseded_by = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="supersedes",
        verbose_name=_("Superseded By"),
    )
    cancellation_reason = models.CharField(
        max_length=500,
        blank=True,
        verbose_name=_("Cancellation Reason"),
    )
    # Donor snapshot fields (CRA requirement)
    donor_legal_name = models.CharField(
        max_length=255,
        verbose_name=_("Donor Legal Name"),
    )
    donor_address_line1 = models.CharField(
        max_length=255,
        verbose_name=_("Donor Address Line 1"),
    )
    donor_city = models.CharField(
        max_length=100,
        verbose_name=_("Donor City"),
    )
    donor_province = models.CharField(
        max_length=2,
        verbose_name=_("Donor Province"),
    )
    donor_postal_code = models.CharField(
        max_length=10,
        verbose_name=_("Donor Postal Code"),
    )
    donation_date = models.DateField(
        verbose_name=_("Donation Date"),
    )
    receipt_date = models.DateField(
        verbose_name=_("Receipt Date"),
    )
    eligible_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name=_("Eligible Amount"),
    )
    advantage_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name=_("Advantage Amount"),
    )
    advantage_description = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Advantage Description"),
    )
    # Charity snapshot fields (CRA requirement)
    charity_legal_name = models.CharField(
        max_length=255,
        verbose_name=_("Charity Legal Name"),
    )
    charity_registration_number = models.CharField(
        max_length=20,
        verbose_name=_("CRA Registration Number"),
    )
    charity_address = models.TextField(
        verbose_name=_("Charity Address"),
    )
    place_of_issue = models.CharField(
        max_length=100,
        verbose_name=_("Place of Issue"),
    )
    authorized_signatory_name = models.CharField(
        max_length=255,
        verbose_name=_("Authorized Signatory Name"),
    )
    authorized_signatory_title = models.CharField(
        max_length=255,
        verbose_name=_("Authorized Signatory Title"),
    )
    pdf_path = models.CharField(
        max_length=500,
        blank=True,
        verbose_name=_("PDF Path"),
        help_text=(
            "Internal storage path — NEVER expose in API, templates, or admin list_display."
        ),
    )
    issued_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("Issued At"),
        db_index=True,
    )
    is_annual_consolidated = models.BooleanField(
        default=False,
        verbose_name=_("Is Annual Consolidated"),
        help_text="True for year-end consolidated receipts that supersede monthly receipts.",
    )

    class Meta:
        verbose_name = _("Official Donation Receipt")
        verbose_name_plural = _("Official Donation Receipts")
        ordering = ["-issued_at"]

    def __str__(self) -> str:
        return f"Receipt {self.serial_number} ({self.status})"

    def save(self, *args, **kwargs):
        if self.pk:
            try:
                original = self.__class__.objects.get(pk=self.pk)
            except self.__class__.DoesNotExist:
                pass  # Brand-new record — allow
            else:
                changed_immutable = [
                    f for f in self._IMMUTABLE_FIELDS
                    if getattr(self, f) != getattr(original, f)
                ]
                if changed_immutable:
                    raise ValueError(
                        f"OfficialDonationReceipt is append-only. "
                        f"Cannot change: {', '.join(sorted(changed_immutable))}. "
                        f"Cancel and issue a new receipt to correct errors."
                    )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(
            "OfficialDonationReceipt cannot be deleted. Cancel it and issue a new one."
        )

    def cancel(self, reason: str, superseded_by: "OfficialDonationReceipt | None" = None) -> None:
        """Cancel this receipt. Only 'issued' receipts can be cancelled."""
        if self.status != self.RECEIPT_STATUS_ISSUED:
            raise ValueError(
                f"Only 'issued' receipts can be cancelled. Current status: {self.status}"
            )
        self.status = self.RECEIPT_STATUS_CANCELLED
        self.cancellation_reason = reason
        if superseded_by is not None:
            self.superseded_by = superseded_by
        self.save(update_fields=["status", "cancellation_reason", "superseded_by", "updated_at"])

    def mark_superseded(self, new_receipt: "OfficialDonationReceipt") -> None:
        """Mark this receipt as superseded by a corrected re-issue."""
        if self.status != self.RECEIPT_STATUS_ISSUED:
            raise ValueError(
                f"Only 'issued' receipts can be superseded. Current status: {self.status}"
            )
        self.status = self.RECEIPT_STATUS_SUPERSEDED
        self.superseded_by = new_receipt
        self.save(update_fields=["status", "superseded_by", "updated_at"])
