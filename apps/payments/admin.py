"""
Django admin registration for the Payments building block.

Access control policy:
  PaymentAuditEntry       — no add, no change, no delete (system-created log)
  OfficialDonationReceipt — no add, no change, no delete (cancel via code)
  Payment                 — no add, no delete (created by gateway)
  Refund                  — no add, no delete (created by gateway)
  WebhookEvent            — no add, no delete (created by gateway)
  TenantPaymentConfig     — stripe keys and webhook secret are read-only; row cannot be deleted

Security:
  - No PII (payer/donor email, legal name) in list_display or search_fields
  - pdf_path never in any fieldset, list_display, or readonly_fields
  - Payer/donor FKs represented by .pk only
  - webhook_endpoint_secret never shown in plain text (masked display only)
  - gateway_payment_method_id not exposed in admin (Stripe reusable token)
"""
from django.contrib import admin

from .models import (
    CharitySettings,
    Donation,
    DonationCampaign,
    FeeSchedule,
    OfficialDonationReceipt,
    Payment,
    PaymentAuditEntry,
    PaymentIntent,
    RecurringGiftPlan,
    Refund,
    ServiceFeePayment,
    TaxRate,
    TenantPaymentConfig,
    WebhookEvent,
)


# ---------------------------------------------------------------------------
# PaymentIntent
# ---------------------------------------------------------------------------

@admin.register(PaymentIntent)
class PaymentIntentAdmin(admin.ModelAdmin):
    list_display = [
        "reference",
        "payer_pk",
        "purpose",
        "status",
        "gateway",
        "amount",
        "currency",
        "created_at",
    ]
    list_filter = ["status", "purpose", "gateway", "currency"]
    search_fields = ["reference", "gateway_intent_id"]
    readonly_fields = [
        "id",
        "reference",
        "idempotency_key",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]

    @admin.display(description="Payer PK", ordering="payer_id")
    def payer_pk(self, obj):
        return obj.payer_id


# ---------------------------------------------------------------------------
# RefundInline — shown on the Payment change page
# ---------------------------------------------------------------------------

class RefundInline(admin.TabularInline):
    model = Refund
    fields = ("amount", "reason", "gateway_refund_id", "authorized_by", "refunded_at")
    readonly_fields = ("amount", "reason", "gateway_refund_id", "authorized_by", "refunded_at")
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# Payment
# ---------------------------------------------------------------------------

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = [
        "gateway_charge_id",
        "intent_reference",
        "payment_method_type",
        "card_brand",
        "amount_paid",
        "processor_fee",
        "net_amount",
        "paid_at",
        "refund_link",
    ]
    list_filter = ["payment_method_type", "card_brand"]
    list_select_related = ["intent"]
    search_fields = ["gateway_charge_id", "intent__reference"]
    readonly_fields = [
        "id",
        "intent",
        "gateway_charge_id",
        "amount_paid",
        "processor_fee",
        "net_amount",
        "payment_method_type",
        "card_last_four",
        "card_brand",
        "paid_at",
        "created_at",
        "updated_at",
    ]
    ordering = ["-paid_at"]
    inlines = [RefundInline]

    @admin.display(description="Intent Reference", ordering="intent__reference")
    def intent_reference(self, obj):
        return obj.intent.reference

    @admin.display(description="Refund")
    def refund_link(self, obj):
        from django.utils.html import format_html
        from django.urls import reverse
        url = reverse("payments:refund_create", kwargs={"payment_pk": obj.pk})
        return format_html('<a href="{}">Issue Refund</a>', url)

    def has_add_permission(self, request):
        return False  # Created by gateway receiver only

    def has_delete_permission(self, request, obj=None):
        return False  # Financial records are permanent


# ---------------------------------------------------------------------------
# Refund
# ---------------------------------------------------------------------------

@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = [
        "gateway_refund_id",
        "payment_charge_id",
        "amount",
        "reason",
        "authorized_by_pk",
        "refunded_at",
    ]
    list_filter = ["reason"]
    list_select_related = ["payment", "authorized_by"]
    search_fields = ["gateway_refund_id", "payment__gateway_charge_id"]
    readonly_fields = [
        "id",
        "payment",
        "amount",
        "reason",
        "gateway_refund_id",
        "refunded_at",
        "authorized_by",
        "created_at",
        "updated_at",
    ]
    ordering = ["-refunded_at"]

    @admin.display(description="Payment Charge ID", ordering="payment__gateway_charge_id")
    def payment_charge_id(self, obj):
        return obj.payment.gateway_charge_id

    @admin.display(description="Authorized By PK", ordering="authorized_by_id")
    def authorized_by_pk(self, obj):
        return obj.authorized_by_id

    def has_add_permission(self, request):
        return False  # Created by gateway receiver only

    def has_delete_permission(self, request, obj=None):
        return False  # Financial records are permanent


# ---------------------------------------------------------------------------
# WebhookEvent
# ---------------------------------------------------------------------------

@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = [
        "gateway_event_id",
        "gateway",
        "event_type",
        "signature_verified",
        "processed",
        "retry_count",
        "created_at",
    ]
    list_filter = ["gateway", "signature_verified", "processed"]
    search_fields = ["gateway_event_id", "event_type", "payload"]  # payload LIKE scan — consider GIN index at scale
    readonly_fields = [
        "id",
        "gateway",
        "event_type",
        "gateway_event_id",
        "payload",
        "signature_verified",
        "processed",
        "processed_at",
        "error",
        "retry_count",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]

    def has_add_permission(self, request):
        return False  # Created by webhook endpoint only

    def has_delete_permission(self, request, obj=None):
        return False  # Audit evidence


# ---------------------------------------------------------------------------
# PaymentAuditEntry
# ---------------------------------------------------------------------------

@admin.register(PaymentAuditEntry)
class PaymentAuditEntryAdmin(admin.ModelAdmin):
    list_display = [
        "action",
        "actor_pk",
        "payment_intent_reference",
        "actor_ip",
        "created_at",
    ]
    list_filter = ["action"]
    list_select_related = ["payment_intent", "actor"]
    search_fields = ["payment_intent__reference"]
    readonly_fields = [
        "id",
        "actor",
        "action",
        "payment_intent",
        "payment",
        "refund",
        "actor_ip",
        "details",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]

    @admin.display(description="Actor PK", ordering="actor_id")
    def actor_pk(self, obj):
        return obj.actor_id

    @admin.display(description="Intent Reference", ordering="payment_intent__reference")
    def payment_intent_reference(self, obj):
        return obj.payment_intent.reference if obj.payment_intent_id else "-"

    def has_add_permission(self, request):
        return False  # System-created only

    def has_change_permission(self, request, obj=None):
        return False  # Immutable

    def has_delete_permission(self, request, obj=None):
        return False  # Immutable


# ---------------------------------------------------------------------------
# TenantPaymentConfig
# ---------------------------------------------------------------------------

@admin.register(TenantPaymentConfig)
class TenantPaymentConfigAdmin(admin.ModelAdmin):
    list_display = ["id", "use_connect", "is_test_mode", "updated_at"]
    readonly_fields = [
        "id",
        "stripe_publishable_key",
        "stripe_connect_account_id",
        "webhook_secret_display",
        "created_at",
        "updated_at",
    ]
    fieldsets = [
        (
            "Mode",
            {
                "fields": ["use_connect", "is_test_mode"],
            },
        ),
        (
            "Stripe Keys",
            {
                "fields": [
                    "stripe_publishable_key",
                    "stripe_connect_account_id",
                ],
                "description": (
                    "Stripe key fields are read-only in the admin. "
                    "Changes must be made via environment variables and redeployment."
                ),
            },
        ),
        (
            "Webhook",
            {
                "fields": ["webhook_secret_display"],
                "description": (
                    "Webhook signing secret is masked for security. "
                    "Changing this requires rotating the Stripe webhook signing secret "
                    "and redeploying."
                ),
            },
        ),
        (
            "Metadata",
            {
                "fields": ["id", "created_at", "updated_at"],
                "classes": ["collapse"],
            },
        ),
    ]

    def webhook_secret_display(self, obj):
        if obj.webhook_endpoint_secret:
            return f"{'*' * 8} (set — {len(obj.webhook_endpoint_secret)} chars)"
        return "⚠ Not configured"
    webhook_secret_display.short_description = "Webhook signing secret"

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# CharitySettings
# ---------------------------------------------------------------------------

@admin.register(CharitySettings)
class CharitySettingsAdmin(admin.ModelAdmin):
    list_display = [
        "charity_legal_name",
        "charity_registration_number",
        "charity_province",
        "is_active",
    ]
    list_filter = ["is_active", "charity_province"]
    readonly_fields = ["id", "created_at", "updated_at"]
    fieldsets = [
        (
            "Charity Information",
            {
                "fields": [
                    "charity_legal_name",
                    "charity_registration_number",
                    "charity_address_line1",
                    "charity_city",
                    "charity_province",
                    "charity_postal_code",
                ],
            },
        ),
        (
            "Receipt Details",
            {
                "fields": [
                    "place_of_issue",
                    "authorized_signatory_name",
                    "authorized_signatory_title",
                ],
            },
        ),
        (
            "Display",
            {
                "fields": ["name_en", "name_fr", "is_active"],
            },
        ),
        (
            "Metadata",
            {
                "fields": ["id", "created_at", "updated_at"],
                "classes": ["collapse"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# FeeSchedule
# ---------------------------------------------------------------------------

@admin.register(FeeSchedule)
class FeeScheduleAdmin(admin.ModelAdmin):
    list_display = [
        "fee_code",
        "service_type",
        "province",
        "amount",
        "is_taxable",
        "effective_date",
        "expiry_date",
        "is_active",
    ]
    list_filter = ["province", "is_taxable", "is_active", "service_type"]
    search_fields = ["fee_code", "description_en"]
    readonly_fields = ["id", "created_at", "updated_at"]
    ordering = ["fee_code", "-effective_date"]


# ---------------------------------------------------------------------------
# TaxRate
# ---------------------------------------------------------------------------

@admin.register(TaxRate)
class TaxRateAdmin(admin.ModelAdmin):
    list_display = [
        "province",
        "tax_name_en",
        "federal_rate",
        "provincial_rate",
        "combined_rate",
        "effective_date",
    ]
    search_fields = ["province", "tax_name_en"]
    readonly_fields = ["id", "created_at", "updated_at"]
    ordering = ["province"]


# ---------------------------------------------------------------------------
# ServiceFeePayment
# ---------------------------------------------------------------------------

@admin.register(ServiceFeePayment)
class ServiceFeePaymentAdmin(admin.ModelAdmin):
    list_display = [
        "fee_code",
        "service_request_id",
        "base_amount",
        "tax_amount",
        "tax_rate_applied",
        "created_at",
    ]
    search_fields = ["fee_code"]
    readonly_fields = [
        "id",
        "payment_intent",
        "service_request_id",
        "fee_code",
        "base_amount",
        "tax_amount",
        "tax_rate_applied",
        "description_en",
        "description_fr",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# DonationCampaign
# ---------------------------------------------------------------------------

@admin.register(DonationCampaign)
class DonationCampaignAdmin(admin.ModelAdmin):
    list_display = [
        "slug",
        "name_en",
        "goal_amount",
        "start_date",
        "end_date",
        "is_active",
        "sort_order",
    ]
    list_filter = ["is_active"]
    search_fields = ["slug", "name_en"]
    prepopulated_fields = {"slug": ["name_en"]}
    readonly_fields = ["id", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# Donation
# ---------------------------------------------------------------------------

@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    # NOTE: donor_name_snapshot and donor_address_snapshot are PII fields (CRA receipt requirement).
    # Access restricted to staff with payments.view_donation permission.
    # These fields are NOT searchable (not in search_fields) to prevent PII in URL params.
    list_display = [
        "donor_pk",
        "campaign",
        "amount",
        "eligible_amount",
        "advantage_amount",
        "is_recurring",
        "is_anonymous",
        "status",
        "created_at",
    ]
    list_filter = ["status", "is_recurring", "is_anonymous", "dedication_type"]
    list_select_related = ["campaign", "recurring_plan"]
    search_fields = ["payment_intent__reference"]
    readonly_fields = [
        "id",
        "payment_intent",
        "donor",
        "campaign",
        "amount",
        "advantage_amount",
        "advantage_description",
        "eligible_amount",
        "is_recurring",
        "recurring_plan",
        "dedication_type",
        "dedication_name",
        "is_anonymous",
        "status",
        "donor_name_snapshot",
        "donor_address_snapshot",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]

    @admin.display(description="Donor PK", ordering="donor_id")
    def donor_pk(self, obj):
        return obj.donor_id

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# RecurringGiftPlan
# ---------------------------------------------------------------------------

@admin.register(RecurringGiftPlan)
class RecurringGiftPlanAdmin(admin.ModelAdmin):
    list_display = [
        "gateway_subscription_id",
        "donor_pk",
        "campaign",
        "amount",
        "frequency",
        "status",
        "next_charge_date",
        "created_at",
    ]
    list_filter = ["status", "frequency"]
    search_fields = ["gateway_subscription_id"]
    readonly_fields = [
        "id",
        "donor",
        "campaign",
        "amount",
        "frequency",
        "next_charge_date",
        "gateway_subscription_id",
        # gateway_payment_method_id intentionally excluded — reusable Stripe token;
        # exposing it broadens the attack surface unnecessarily.
        "status",
        "cancelled_at",
        "cancellation_reason",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]

    @admin.display(description="Donor PK", ordering="donor_id")
    def donor_pk(self, obj):
        return obj.donor_id

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# OfficialDonationReceipt
# ---------------------------------------------------------------------------

@admin.register(OfficialDonationReceipt)
class OfficialDonationReceiptAdmin(admin.ModelAdmin):
    list_display = [
        "serial_number",
        "status",
        "receipt_date",
        "eligible_amount",
        "advantage_amount",
        "is_annual_consolidated",
        "issued_at",
    ]
    list_filter = ["status", "is_annual_consolidated", "donor_province"]
    # donor_legal_name excluded from search_fields — PIPEDA: name in URL/logs is a privacy violation.
    search_fields = ["serial_number", "charity_registration_number"]
    # pdf_path is intentionally excluded from readonly_fields, fieldsets, and list_display
    readonly_fields = [
        "id",
        "donation",
        "serial_number",
        "status",
        "superseded_by",
        "cancellation_reason",
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
        "issued_at",
        "is_annual_consolidated",
        "created_at",
        "updated_at",
    ]
    ordering = ["-issued_at"]

    def has_add_permission(self, request):
        return False  # Issued programmatically only

    def has_change_permission(self, request, obj=None):
        return False  # Cancel via receipt.cancel() in code, never via admin

    def has_delete_permission(self, request, obj=None):
        return False  # CRA records are permanent
