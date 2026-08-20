"""
Payments building block — data models.

Security invariants:
- No raw PANs, no CVVs — card_last_four (4 chars) only.
- All monetary values: DecimalField (never FloatField).
- PaymentAuditEntry and OfficialDonationReceipt are append-only.
- document._storage_key is NEVER in any HTTP response header, URL, log line, or template context.
- Currency always CAD.
- PII never written to application logs.
"""
from __future__ import annotations

import secrets
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from django.contrib.contenttypes.fields import GenericRelation

from apps.core.fields import EncryptedCharField, _get_fernet  # noqa: F401
from apps.core.models import TimestampedModel


def _generate_payment_reference() -> str:
    """Generate a collision-safe payment reference. 16 hex chars = 2^64 address space."""
    return secrets.token_hex(8)


# ---------------------------------------------------------------------------
# Module-level constants reused across models
# ---------------------------------------------------------------------------

GATEWAY_STRIPE = "stripe"
GATEWAY_MONERIS = "moneris"
GATEWAY_MANUAL = "manual"       # offline / manual payments (honoraria, bank transfers)
GATEWAY_CHOICES = [
    (GATEWAY_STRIPE, "Stripe"),
    (GATEWAY_MONERIS, "Moneris"),
    (GATEWAY_MANUAL, "Manual / Offline"),
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
    GATEWAY_MANUAL = GATEWAY_MANUAL
    GATEWAY_CHOICES = GATEWAY_CHOICES

    # Purpose constants
    PURPOSE_SERVICE_FEE = "service_fee"
    PURPOSE_DONATION = "donation"
    PURPOSE_FINE = "fine"
    PURPOSE_HONORARIUM = "honorarium"

    PURPOSE_CHOICES = [
        (PURPOSE_SERVICE_FEE, "Service Fee"),
        (PURPOSE_DONATION, "Donation"),
        (PURPOSE_FINE, "Fine / Penalty"),
        (PURPOSE_HONORARIUM, "Volunteer Honorarium"),
    ]

    # Allowed status transitions
    ALLOWED_TRANSITIONS = {
        STATUS_PENDING: [STATUS_PROCESSING, STATUS_CANCELLED, STATUS_FAILED],
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
        default=_generate_payment_reference,
        editable=False,
        db_index=True,
        help_text="Unique payment reference (16-char hex). Collision-safe: 2^64 address space.",
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
        db_index=True,
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
        indexes = [
            models.Index(fields=["payer", "status"], name="payments_intent_payer_status"),
            models.Index(fields=["status"], name="payments_intent_status"),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gt=0),
                name="payments_intent_amount_positive",
            ),
            models.CheckConstraint(
                check=models.Q(tax_amount__gte=0),
                name="payments_intent_tax_nonneg",
            ),
        ]
        # Custom permissions used by the Analytics & Reporting BB.
        # Defined here (payments app) so that permission_required strings
        # "payments.view_financialreport" etc. resolve correctly.
        permissions = [
            ("view_financialreport",  "Can view financial reports"),
            ("export_financialreport", "Can export financial reports"),
            ("view_donationreport",   "Can view donation & CRA reports"),
            ("export_donationreport", "Can export donation & CRA reports"),
            ("view_operationalreport",  "Can view operational reports"),
            ("export_operationalreport", "Can export operational reports"),
        ]

    def __str__(self) -> str:
        return self.reference or str(self.id)

    def save(self, *args, **kwargs):
        if not self.id:
            self.id = uuid.uuid4()
        # reference is populated via default=_generate_payment_reference; no fallback needed.
        super().save(*args, **kwargs)

    def transition(self, new_status: str) -> None:
        """
        Advance the status machine to new_status.
        Raises ValueError if the transition is not permitted.
        """
        from django.db import transaction
        with transaction.atomic():
            # Re-fetch from DB with row lock
            refreshed = (
                self.__class__.objects.select_for_update().get(pk=self.pk)
            )
            allowed = self.ALLOWED_TRANSITIONS.get(refreshed.status, [])
            if new_status not in allowed:
                raise ValueError(
                    f"Cannot transition PaymentIntent from "
                    f"'{refreshed.status}' to '{new_status}'."
                )
            refreshed.status = new_status
            refreshed.save(update_fields=["status", "updated_at"])
            # Keep in-memory object consistent
            self.status = new_status


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
    CARD_BRAND_DISCOVER = "discover"
    CARD_BRAND_JCB = "jcb"
    CARD_BRAND_DINERS = "diners"
    CARD_BRAND_UNIONPAY = "unionpay"
    CARD_BRAND_OTHER = "other"
    CARD_BRAND_CHOICES = [
        (CARD_BRAND_VISA, "Visa"),
        (CARD_BRAND_MASTERCARD, "Mastercard"),
        (CARD_BRAND_AMEX, "Amex"),
        (CARD_BRAND_INTERAC, "Interac"),
        (CARD_BRAND_DISCOVER, "Discover"),
        (CARD_BRAND_JCB, "JCB"),
        (CARD_BRAND_DINERS, "Diners Club"),
        (CARD_BRAND_UNIONPAY, "UnionPay"),
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
        editable=False,  # computed from amount_paid - processor_fee in save()
        verbose_name=_("Net Amount"),
        help_text=_("Net amount after processor fees. Computed automatically."),
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
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount_paid__gte=0),
                name="payments_payment_amount_paid_non_negative",
            ),
            models.CheckConstraint(
                check=models.Q(processor_fee__gte=0),
                name="payments_payment_processor_fee_non_negative",
            ),
            models.CheckConstraint(
                check=models.Q(net_amount__gte=0),
                name="payments_payment_net_amount_non_negative",
            ),
            models.CheckConstraint(
                check=models.Q(processor_fee__lte=models.F("amount_paid")),
                name="payments_payment_processor_fee_lte_amount_paid",
            ),
        ]

    def __str__(self) -> str:
        return f"Payment {self.gateway_charge_id}"

    def save(self, *args, **kwargs):
        self.net_amount = self.amount_paid - self.processor_fee
        super().save(*args, **kwargs)


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

    GATEWAY_STATUS_PENDING = "pending"
    GATEWAY_STATUS_SUCCEEDED = "succeeded"
    GATEWAY_STATUS_FAILED = "failed"
    GATEWAY_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("succeeded", "Succeeded"),
        ("failed", "Failed"),
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
    gateway_status = models.CharField(
        max_length=20,
        choices=GATEWAY_STATUS_CHOICES,
        default=GATEWAY_STATUS_PENDING,
        db_index=True,
        verbose_name=_("Gateway Status"),
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

    def clean(self) -> None:
        """
        M-D: Enforce cross-row refund cap at the ORM level.

        A CheckConstraint can enforce amount > 0 per row, but cannot enforce
        the cross-row invariant (sum of refunds ≤ payment.amount_paid). We do
        that here via clean(), which is called by ModelForm validation and
        admin. The view layer also enforces this via _compute_already_refunded().
        """
        super().clean()
        from django.core.exceptions import ValidationError

        if self.amount is not None and self.amount <= Decimal("0.00"):
            raise ValidationError({"amount": _("Refund amount must be greater than zero.")})

        if self.amount is not None and self.payment_id is not None:
            already_refunded = (
                Refund.objects.filter(payment_id=self.payment_id)
                .exclude(pk=self.pk)
                .exclude(gateway_status=self.GATEWAY_STATUS_FAILED)
                .aggregate(total=models.Sum("amount"))["total"]
                or Decimal("0.00")
            )
            try:
                max_refundable = self.payment.amount_paid - already_refunded
            except Exception:
                max_refundable = None

            if max_refundable is not None and self.amount > max_refundable:
                raise ValidationError(
                    {
                        "amount": _(
                            f"Refund amount ({self.amount}) exceeds maximum "
                            f"refundable ({max_refundable})."
                        )
                    }
                )

    class Meta:
        ordering = ["-refunded_at"]
        verbose_name = _("Refund")
        verbose_name_plural = _("Refunds")
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gt=0),
                name="payments_refund_amount_positive",
            ),
        ]

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
        indexes = [
            models.Index(fields=["processed", "gateway"], name="payments_webhook_proc_gw"),
            models.Index(fields=["event_type"], name="payments_wh_event_type_idx"),
        ]

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
    details = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("Details"),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Payment Audit Entry")
        verbose_name_plural = _("Payment Audit Entries")
        indexes = [
            models.Index(fields=["payment_intent"], name="payments_audit_intent"),
            models.Index(fields=["created_at"], name="payments_audit_timestamp"),
        ]
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
        return f"{self.action} @ {self.created_at}"

    def _mask_ip(self, ip):
        if not ip:
            return ip
        try:
            import ipaddress
            parsed = ipaddress.ip_address(ip)
            if parsed.version == 4:
                parts = ip.split(".")
                return ".".join(parts[:3] + ["0"])
            else:  # IPv6
                # Zero the last 80 bits (last 5 groups of 4 hex digits)
                packed = parsed.packed
                masked = packed[:6] + b"\x00" * 10
                return str(ipaddress.IPv6Address(masked))
        except ValueError:
            return ""  # Invalid IP — store empty rather than PII

    def save(self, *args, **kwargs):
        # PaymentAuditEntry is append-only: always INSERT, never UPDATE.
        # Use self._state.adding (set by Django to True for unsaved instances,
        # False after the first successful save) rather than SELECT EXISTS to
        # determine whether this is a new record.  This avoids the spurious
        # SELECT EXISTS round-trip that Django otherwise issues when pk is
        # already set (common with UUID primary keys whose default assigns the
        # pk in Python before the first save call).
        self.actor_ip = self._mask_ip(self.actor_ip)
        if not self._state.adding:
            raise ValueError("PaymentAuditEntry is append-only and cannot be modified.")
        kwargs.setdefault("force_insert", True)
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
    webhook_endpoint_secret = EncryptedCharField(
        max_length=255,
        blank=True,
        verbose_name=_("Webhook Endpoint Secret"),
        help_text=(
            "Stripe webhook signing secret (whsec_...). "
            "Stored encrypted at rest (Fernet/AES-128-CBC). "
            "Changing this requires rotating the Stripe webhook signing secret and redeploying."
        ),
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

    def save(self, *args, **kwargs):
        self.pk = _SINGLETON_PK
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls) -> "TenantPaymentConfig":
        """Return the singleton config row, creating it if it does not exist."""
        from django.db import IntegrityError
        try:
            obj, _ = cls.objects.get_or_create(pk=_SINGLETON_PK)
        except IntegrityError:
            obj = cls.objects.get(pk=_SINGLETON_PK)
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
        validators=[
            RegexValidator(
                r"^\d{9} RR \d{4}$",
                "Must match CRA format: 123456789 RR 0001",
            )
        ],
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
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"],
                condition=models.Q(is_active=True),
                name="payments_charitysettings_one_active",
            ),
        ]

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
        constraints = [
            models.UniqueConstraint(
                fields=["fee_code", "province", "effective_date"],
                name="payments_feeschedule_code_province_date_unique",
            ),
            models.CheckConstraint(
                check=models.Q(amount__gte=0),
                name="payments_feeschedule_amount_nonneg",
            ),
            models.CheckConstraint(
                check=models.Q(expiry_date__isnull=True) | models.Q(expiry_date__gt=models.F("effective_date")),
                name="payments_feeschedule_dates_valid",
            ),
        ]

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
        max_digits=7,
        decimal_places=5,
        verbose_name=_("Federal Rate"),
        help_text="e.g. 0.0500 for 5% GST.",
    )
    provincial_rate = models.DecimalField(
        max_digits=7,
        decimal_places=5,
        verbose_name=_("Provincial Rate"),
        help_text="e.g. 0.0800 for 8% PST.",
    )
    combined_rate = models.DecimalField(
        max_digits=7,
        decimal_places=5,
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
        max_digits=7,
        decimal_places=5,
        default=Decimal("0.00000"),
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

    # Documents BB — optional confirmation documents (e.g. payment receipts, fee waivers).
    # GenericRelation does not require a migration on this model; the FK lives on
    # DocumentAttachment. Spec §16.4.
    document_attachments = GenericRelation(
        "documents.DocumentAttachment",
        content_type_field="content_type",
        object_id_field="object_id",
        related_query_name="service_fee_payment",
    )

    class Meta:
        verbose_name = _("Service Fee Payment")
        verbose_name_plural = _("Service Fee Payments")
        constraints = [
            # M-E: base_amount and tax_amount must be non-negative
            models.CheckConstraint(
                check=models.Q(base_amount__gte=Decimal("0.00")),
                name="payments_servicefeepayment_base_amount_nonneg",
            ),
            models.CheckConstraint(
                check=models.Q(tax_amount__gte=Decimal("0.00")),
                name="payments_servicefeepayment_tax_amount_nonneg",
            ),
        ]

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
        indexes = [
            models.Index(fields=["donor", "status"], name="payments_plan_donor_status"),
            models.Index(fields=["next_charge_date", "status"], name="payments_plan_charge_date_st"),
        ]

    def __str__(self) -> str:
        return f"RecurringPlan {self.gateway_subscription_id} ({self.status})"


# ---------------------------------------------------------------------------
# Donation
# ---------------------------------------------------------------------------

class DonationQuerySet(models.QuerySet):
    _FINANCIAL_FIELDS = frozenset({"amount", "advantage_amount", "eligible_amount"})

    def update(self, **kwargs):
        blocked = self._FINANCIAL_FIELDS & set(kwargs)
        if blocked:
            raise ValueError(
                f"Cannot bulk-update financial fields on Donation: {blocked}. "
                f"Use instance.save() to trigger eligible_amount recomputation."
            )
        return super().update(**kwargs)


class DonationManager(models.Manager):
    def get_queryset(self):
        return DonationQuerySet(self.model, using=self._db)


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

    objects = DonationManager()

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
        indexes = [
            models.Index(fields=["donor"], name="payments_donation_donor"),
            models.Index(fields=["campaign", "status"], name="payments_donation_camp_status"),
            models.Index(fields=["status"], name="payments_donation_status"),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gt=0),
                name="payments_donation_amount_positive",
            ),
            models.CheckConstraint(
                check=models.Q(eligible_amount__gte=0),
                name="payments_donation_eligible_nonneg",
            ),
            models.CheckConstraint(
                check=models.Q(advantage_amount__lte=models.F("amount")),
                name="payments_donation_advantage_lte_amount",
            ),
            models.CheckConstraint(
                check=~models.Q(donor_name_snapshot=""),
                name="payments_donation_donor_name_required",
            ),
            models.CheckConstraint(
                check=~models.Q(donor_address_snapshot=""),
                name="payments_donation_donor_address_required",
            ),
        ]

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
    Only status, cancellation_reason, superseded_by, and document may change.
    Cancel and re-issue to correct errors.
    The document FK references the Documents BB record for the receipt PDF and
    MUST NEVER be exposed in any API, template output, or admin list_display.
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
        "receipt_date",
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
        validators=[
            RegexValidator(
                regex=r"^\d{4}-\d{6}$",
                message="Serial number must be in YYYY-NNNNNN format (e.g. 2024-000001).",
            )
        ],
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
    email_sent = models.BooleanField(
        default=False,
        help_text=(
            "Set to True once the CRA receipt email has been delivered. "
            "Guards against duplicate delivery on Celery retry races."
        ),
    )

    # Documents BB: replaces pdf_path CharField.
    # Phase 1: FK added (null=True); Phase 2: backfilled via migrate_existing_files;
    # Phase 3: pdf_path dropped. Use document FK for all new code.
    document = models.OneToOneField(
        "documents.Document",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="donation_receipt",
        verbose_name=_("Receipt document (Documents BB)"),
        help_text=_(
            "FK to the Documents BB record for this receipt PDF. "
            "Replaces the deprecated pdf_path CharField after migration."
        ),
    )

    class Meta:
        verbose_name = _("Official Donation Receipt")
        verbose_name_plural = _("Official Donation Receipts")
        ordering = ["-issued_at"]
        indexes = [
            models.Index(fields=["donation", "status"], name="payments_receipt_don_status"),
            models.Index(fields=["is_annual_consolidated", "receipt_date"], name="payments_receipt_annual_date"),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(eligible_amount__gte=0),
                name="payments_receipt_eligible_nonneg",
            ),
            models.CheckConstraint(
                check=models.Q(advantage_amount__gte=0),
                name="payments_receipt_advantage_nonneg",
            ),
            # CRA IT-110R3: advantage_amount must never exceed eligible_amount.
            # eligible_amount = amount - advantage_amount, so advantage > eligible
            # would imply a negative net donation — invalid for tax receipt purposes.
            # Python-level validation alone is insufficient; a direct DB insert or
            # ORM bypass could create an invalid receipt without this constraint.
            models.CheckConstraint(
                check=models.Q(advantage_amount__lte=models.F("eligible_amount")),
                name="payments_receipt_advantage_lte_eligible",
            ),
            # Prevents two issued receipts for the same donation (anchor FK).
            # For annual consolidated receipts the anchor is the first donation in the
            # tax year for that donor — so each donor gets at most one issued receipt
            # per annual run even under concurrent worker execution.
            models.UniqueConstraint(
                fields=["donation"],
                condition=models.Q(status="issued"),
                name="payments_receipt_unique_issued_per_donation",
            ),
        ]

    def __str__(self) -> str:
        return f"Receipt {self.serial_number} ({self.status})"

    @property
    def has_pdf(self) -> bool:
        """True if a PDF document has been stored for this receipt."""
        return bool(self.document_id)

    def save(self, *args, **kwargs):
        if not self.serial_number:
            from django.db import connection
            from django.utils.timezone import localtime
            year = localtime(timezone.now()).year
            with connection.cursor() as cursor:
                cursor.execute("SELECT nextval('payments_receipt_serial_seq')")
                seq = cursor.fetchone()[0]
            # L-2: A global DB sequence exposes approximate receipt issuance rate —
            # a donor with serial numbers 2026-000042 and 2026-000045 can infer
            # ~3 other receipts were issued between theirs. This is an accepted
            # trade-off: sequential serials are CRA-auditable and tamper-evident
            # (no gaps = no deleted receipts). For a privacy-first redesign,
            # replace with a random opaque token while keeping a separate
            # monotonic internal counter for CRA audit purposes.
            self.serial_number = f"{year}-{str(seq).zfill(6)}"

        if self.receipt_date and self.donation_date and self.receipt_date < self.donation_date:
            raise ValueError(
                f"receipt_date ({self.receipt_date}) cannot be before "
                f"donation_date ({self.donation_date})."
            )

        if self.pk:
            try:
                original = self.__class__._default_manager.using(self._state.db).get(pk=self.pk)
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
        from django.db import transaction
        now = timezone.now()
        with transaction.atomic():
            updated = self.__class__._base_manager.filter(
                pk=self.pk, status=self.RECEIPT_STATUS_ISSUED
            ).update(
                status=self.RECEIPT_STATUS_CANCELLED,
                cancellation_reason=reason,
                superseded_by_id=superseded_by.pk if superseded_by else None,
                updated_at=now,
            )
            if not updated:
                raise ValueError(
                    f"Receipt {self.serial_number} cannot be cancelled — "
                    f"current status is not 'issued'."
                )
            self.status = self.RECEIPT_STATUS_CANCELLED
            self.cancellation_reason = reason
            self.updated_at = now
            if superseded_by:
                self.superseded_by = superseded_by

    def mark_superseded(self, new_receipt: "OfficialDonationReceipt") -> None:
        """Mark this receipt as superseded by a corrected re-issue."""
        from django.db import transaction
        now = timezone.now()
        with transaction.atomic():
            updated = self.__class__._base_manager.filter(
                pk=self.pk, status=self.RECEIPT_STATUS_ISSUED
            ).update(
                status=self.RECEIPT_STATUS_SUPERSEDED,
                superseded_by_id=new_receipt.pk,
                updated_at=now,
            )
            if not updated:
                raise ValueError(
                    f"Receipt {self.serial_number} cannot be superseded — "
                    f"current status is not 'issued'."
                )
            self.status = self.RECEIPT_STATUS_SUPERSEDED
            self.superseded_by = new_receipt
            self.updated_at = now


# ---------------------------------------------------------------------------
# Item 02 blueprint command boundary
# ---------------------------------------------------------------------------
class PaymentCommand(TimestampedModel):
    """Immutable request command reserved before provider-executable work."""

    STATUS_RESERVED = "reserved"
    STATUS_DISPATCHED = "dispatched"
    STATUS_CHOICES = [
        (STATUS_RESERVED, "Reserved"),
        (STATUS_DISPATCHED, "Dispatched"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.CharField(max_length=100, db_index=True)
    caller_bb_id = models.CharField(max_length=20)
    operation = models.CharField(max_length=64)
    request_identity = models.CharField(max_length=255)
    fingerprint = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_RESERVED)
    attempt = models.OneToOneField(
        "payments.PaymentAttempt",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payment_command",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "operation", "request_identity"],
                name="payment_command_identity_uniq",
            )
        ]
        indexes = [
            models.Index(
                fields=["tenant_id", "operation", "created_at"],
                name="payment_command_scope_idx",
            )
        ]


class PaymentCommandOutbox(TimestampedModel):
    """One publishable post-commit handoff record for a PaymentCommand."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    command = models.OneToOneField(
        PaymentCommand,
        on_delete=models.PROTECT,
        related_name="outbox",
    )
    topic = models.CharField(max_length=120)
    payload = models.JSONField(default=dict)
    published_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True, db_index=True)
    acknowledgement_token = models.CharField(max_length=64, null=True, blank=True)
    acknowledgement_result = models.JSONField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["acknowledged_at", "created_at"],
                name="payment_outbox_due_idx",
            )
        ]
