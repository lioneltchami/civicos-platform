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

from .govstack_models import (
    BulkPaymentBatch,
    CreditInstruction,
    GovStackBeneficiary,
    GovStackBill,
    GovStackBillPayment,
    GovStackPaymentAuditEntry,
    GovStackRegisteredBB,
    GovStackVoucher,
    PrepaymentValidationRequest,
)
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
    list_display = [  # noqa: RUF012
        "reference",
        "payer_pk",
        "purpose",
        "status",
        "gateway",
        "amount",
        "currency",
        "created_at",
    ]
    list_filter = ["status", "purpose", "gateway", "currency"]  # noqa: RUF012
    search_fields = ["reference", "gateway_intent_id"]  # noqa: RUF012
    readonly_fields = [  # noqa: RUF012
        "id",
        "reference",
        "idempotency_key",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]  # noqa: RUF012

    @admin.display(description="Payer PK", ordering="payer_id")
    def payer_pk(self, obj):  # noqa: ANN001, ANN201
        return obj.payer_id

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        # Financial records must not be mutated via admin — gateway is sole writer.
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        # PaymentIntents are permanent financial records; deletion cascades to
        # Payment, Donation, and OfficialDonationReceipt rows (CRA audit risk).
        return False


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

    def has_add_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False


# ---------------------------------------------------------------------------
# Payment
# ---------------------------------------------------------------------------


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    # Note: "refund_link" is added dynamically in get_list_display() so it only
    # appears for users who hold the payments.add_refund permission.  Users who
    # can view payments but cannot issue refunds must not see the link — it would
    # render a clickable URL that returns 403 (confusing UX, minor info-disclosure).
    list_display = [  # noqa: RUF012
        "gateway_charge_id",
        "intent_reference",
        "payment_method_type",
        "card_brand",
        "amount_paid",
        "processor_fee",
        "net_amount",
        "paid_at",
    ]
    list_filter = ["payment_method_type", "card_brand"]  # noqa: RUF012
    list_select_related = ["intent"]  # noqa: RUF012
    search_fields = ["gateway_charge_id", "intent__reference"]  # noqa: RUF012
    readonly_fields = [  # noqa: RUF012
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
    ordering = ["-paid_at"]  # noqa: RUF012
    inlines = [RefundInline]  # noqa: RUF012

    def get_list_display(self, request):  # noqa: ANN001, ANN201
        """Append refund_link only for users who may issue refunds (payments.add_refund)."""
        columns = list(super().get_list_display(request))
        if request.user.has_perm("payments.add_refund"):
            columns.append("refund_link")
        return columns

    @admin.display(description="Intent Reference", ordering="intent__reference")
    def intent_reference(self, obj):  # noqa: ANN001, ANN201
        return obj.intent.reference

    @admin.display(description="Refund")
    def refund_link(self, obj):  # noqa: ANN001, ANN201
        from django.urls import reverse
        from django.utils.html import format_html

        url = reverse("payments:refund_create", kwargs={"payment_pk": obj.pk})
        return format_html('<a href="{}">Issue Refund</a>', url)

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        return False  # Created by gateway receiver only

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False  # Financial records are permanent


# ---------------------------------------------------------------------------
# Refund
# ---------------------------------------------------------------------------


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = [  # noqa: RUF012
        "gateway_refund_id",
        "payment_charge_id",
        "amount",
        "reason",
        "authorized_by_pk",
        "refunded_at",
    ]
    list_filter = ["reason"]  # noqa: RUF012
    list_select_related = ["payment", "authorized_by"]  # noqa: RUF012
    search_fields = ["gateway_refund_id", "payment__gateway_charge_id"]  # noqa: RUF012
    readonly_fields = [  # noqa: RUF012
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
    ordering = ["-refunded_at"]  # noqa: RUF012

    @admin.display(description="Payment Charge ID", ordering="payment__gateway_charge_id")
    def payment_charge_id(self, obj):  # noqa: ANN001, ANN201
        return obj.payment.gateway_charge_id

    @admin.display(description="Authorized By PK", ordering="authorized_by_id")
    def authorized_by_pk(self, obj):  # noqa: ANN001, ANN201
        return obj.authorized_by_id

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        return False  # Created by gateway receiver only

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False  # Financial records are permanent


# ---------------------------------------------------------------------------
# WebhookEvent
# ---------------------------------------------------------------------------


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = [  # noqa: RUF012
        "gateway_event_id",
        "gateway",
        "event_type",
        "signature_verified",
        "processed",
        "retry_count",
        "created_at",
    ]
    list_filter = ["gateway", "signature_verified", "processed"]  # noqa: RUF012
    search_fields = [  # noqa: RUF012
        "gateway_event_id",
        "event_type",
    ]  # payload excluded: LIKE scan is slow + payload may contain PII
    readonly_fields = [  # noqa: RUF012
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
    ordering = ["-created_at"]  # noqa: RUF012

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        return False  # Created by webhook endpoint only

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False  # Audit evidence


# ---------------------------------------------------------------------------
# PaymentAuditEntry
# ---------------------------------------------------------------------------


@admin.register(PaymentAuditEntry)
class PaymentAuditEntryAdmin(admin.ModelAdmin):
    list_display = [  # noqa: RUF012
        "action",
        "actor_pk",
        "payment_intent_reference",
        "actor_ip",
        "created_at",
    ]
    list_filter = ["action"]  # noqa: RUF012
    list_select_related = ["payment_intent", "actor"]  # noqa: RUF012
    search_fields = ["payment_intent__reference"]  # noqa: RUF012
    readonly_fields = [  # noqa: RUF012
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
    ordering = ["-created_at"]  # noqa: RUF012

    @admin.display(description="Actor PK", ordering="actor_id")
    def actor_pk(self, obj):  # noqa: ANN001, ANN201
        return obj.actor_id

    @admin.display(description="Intent Reference", ordering="payment_intent__reference")
    def payment_intent_reference(self, obj):  # noqa: ANN001, ANN201
        return obj.payment_intent.reference if obj.payment_intent_id else "-"

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        return False  # System-created only

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False  # Immutable

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False  # Immutable


# ---------------------------------------------------------------------------
# TenantPaymentConfig
# ---------------------------------------------------------------------------


@admin.register(TenantPaymentConfig)
class TenantPaymentConfigAdmin(admin.ModelAdmin):
    list_display = ["id", "use_connect", "is_test_mode", "updated_at"]  # noqa: RUF012
    readonly_fields = [  # noqa: RUF012
        "id",
        "stripe_publishable_key",
        "stripe_connect_account_id",
        "webhook_secret_display",
        "created_at",
        "updated_at",
    ]
    fieldsets = [  # noqa: RUF012
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

    def webhook_secret_display(self, obj) -> str:  # noqa: ANN001
        if obj.webhook_endpoint_secret:
            return f"{'*' * 8} (set — {len(obj.webhook_endpoint_secret)} chars)"
        return "⚠ Not configured"

    webhook_secret_display.short_description = "Webhook signing secret"

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        # is_test_mode controls whether live or test Stripe keys are used.
        # Flipping it via admin could route real donor money to a test account.
        # Changes require a code deploy + environment variable rotation.
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False


# ---------------------------------------------------------------------------
# CharitySettings
# ---------------------------------------------------------------------------


@admin.register(CharitySettings)
class CharitySettingsAdmin(admin.ModelAdmin):
    list_display = [  # noqa: RUF012
        "charity_legal_name",
        "charity_registration_number",
        "charity_province",
        "is_active",
    ]
    list_filter = ["is_active", "charity_province"]  # noqa: RUF012
    readonly_fields = ["id", "created_at", "updated_at"]  # noqa: RUF012
    fieldsets = [  # noqa: RUF012
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

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        # CharitySettings rows are referenced by issued CRA receipts.
        # Deletion would corrupt the audit trail.
        return False


# ---------------------------------------------------------------------------
# FeeSchedule
# ---------------------------------------------------------------------------


@admin.register(FeeSchedule)
class FeeScheduleAdmin(admin.ModelAdmin):
    list_display = [  # noqa: RUF012
        "fee_code",
        "service_type",
        "province",
        "amount",
        "is_taxable",
        "effective_date",
        "expiry_date",
        "is_active",
    ]
    list_filter = ["province", "is_taxable", "is_active", "service_type"]  # noqa: RUF012
    search_fields = ["fee_code", "description_en"]  # noqa: RUF012
    readonly_fields = ["id", "created_at", "updated_at"]  # noqa: RUF012
    ordering = ["fee_code", "-effective_date"]  # noqa: RUF012

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        # FeeSchedule rows are referenced by historical ServiceFeePayment records.
        # Deletion would break audit trail references.
        return False


# ---------------------------------------------------------------------------
# TaxRate
# ---------------------------------------------------------------------------


@admin.register(TaxRate)
class TaxRateAdmin(admin.ModelAdmin):
    list_display = [  # noqa: RUF012
        "province",
        "tax_name_en",
        "federal_rate",
        "provincial_rate",
        "combined_rate",
        "effective_date",
    ]
    search_fields = ["province", "tax_name_en"]  # noqa: RUF012
    readonly_fields = ["id", "created_at", "updated_at"]  # noqa: RUF012
    ordering = ["province"]  # noqa: RUF012

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        # TaxRate rows are embedded in historical receipts by snapshot (tax_rate_applied).
        # Deleting the source row would break backwards reconciliation.
        return False


# ---------------------------------------------------------------------------
# ServiceFeePayment
# ---------------------------------------------------------------------------


@admin.register(ServiceFeePayment)
class ServiceFeePaymentAdmin(admin.ModelAdmin):
    list_display = [  # noqa: RUF012
        "fee_code",
        "service_request_id",
        "base_amount",
        "tax_amount",
        "tax_rate_applied",
        "created_at",
    ]
    search_fields = ["fee_code"]  # noqa: RUF012
    readonly_fields = [  # noqa: RUF012
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

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False


# ---------------------------------------------------------------------------
# DonationCampaign
# ---------------------------------------------------------------------------


@admin.register(DonationCampaign)
class DonationCampaignAdmin(admin.ModelAdmin):
    list_display = [  # noqa: RUF012
        "slug",
        "name_en",
        "goal_amount",
        "start_date",
        "end_date",
        "is_active",
        "sort_order",
    ]
    list_filter = ["is_active"]  # noqa: RUF012
    search_fields = ["slug", "name_en"]  # noqa: RUF012
    prepopulated_fields = {"slug": ["name_en"]}  # noqa: RUF012
    readonly_fields = ["id", "created_at", "updated_at"]  # noqa: RUF012

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        # DonationCampaign rows are referenced by Donation records (FK).
        # Deletion would cascade or orphan donation history.
        return False


# ---------------------------------------------------------------------------
# Donation
# ---------------------------------------------------------------------------


@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    # NOTE: donor_name_snapshot and donor_address_snapshot are PII fields (CRA receipt requirement).
    # Access restricted to staff with payments.view_donation permission.
    # These fields are NOT searchable (not in search_fields) to prevent PII in URL params.
    list_display = [  # noqa: RUF012
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
    list_filter = ["status", "is_recurring", "is_anonymous", "dedication_type"]  # noqa: RUF012
    list_select_related = ["campaign", "recurring_plan"]  # noqa: RUF012
    search_fields = ["payment_intent__reference"]  # noqa: RUF012
    readonly_fields = [  # noqa: RUF012
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
    ordering = ["-created_at"]  # noqa: RUF012

    @admin.display(description="Donor PK", ordering="donor_id")
    def donor_pk(self, obj):  # noqa: ANN001, ANN201
        return obj.donor_id

    def has_view_permission(self, request, obj=None):  # noqa: ANN001, ANN201
        # PIPEDA: donor_name_snapshot and donor_address_snapshot are personal information.
        # Require explicit payments.view_donation permission beyond basic staff status.
        return request.user.is_superuser or request.user.has_perm("payments.view_donation")

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        # Donation records are financial audit evidence — immutable after creation.
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False


# ---------------------------------------------------------------------------
# RecurringGiftPlan
# ---------------------------------------------------------------------------


@admin.register(RecurringGiftPlan)
class RecurringGiftPlanAdmin(admin.ModelAdmin):
    list_display = [  # noqa: RUF012
        "gateway_subscription_id",
        "donor_pk",
        "campaign",
        "amount",
        "frequency",
        "status",
        "next_charge_date",
        "created_at",
    ]
    list_filter = ["status", "frequency"]  # noqa: RUF012
    search_fields = ["gateway_subscription_id"]  # noqa: RUF012
    readonly_fields = [  # noqa: RUF012
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
    ordering = ["-created_at"]  # noqa: RUF012

    @admin.display(description="Donor PK", ordering="donor_id")
    def donor_pk(self, obj):  # noqa: ANN001, ANN201
        return obj.donor_id

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False


# ---------------------------------------------------------------------------
# OfficialDonationReceipt
# ---------------------------------------------------------------------------


@admin.register(OfficialDonationReceipt)
class OfficialDonationReceiptAdmin(admin.ModelAdmin):
    # list_display contains only non-PII fields.
    # donor_legal_name, donor_address_*, and donor PII are restricted to the
    # detail view which is gated by has_view_permission.
    list_display = [  # noqa: RUF012
        "serial_number",
        "status",
        "receipt_date",
        "eligible_amount",
        "is_annual_consolidated",
        "email_sent",
    ]
    list_filter = ["status", "is_annual_consolidated", "donor_province"]  # noqa: RUF012
    # donor_legal_name excluded from search_fields — PIPEDA: name in URL/logs is a privacy violation.  # noqa: E501
    search_fields = ["serial_number", "charity_registration_number"]  # noqa: RUF012
    # pdf_path is intentionally excluded from readonly_fields, fieldsets, and list_display.
    # PII fields (donor_legal_name, donor_address_*) are in readonly_fields for the
    # detail view only; the detail view is gated by has_view_permission below.
    readonly_fields = [  # noqa: RUF012
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
        "email_sent",
        "created_at",
        "updated_at",
    ]
    ordering = ["-issued_at"]  # noqa: RUF012

    def has_view_permission(self, request, obj=None):  # noqa: ANN001, ANN201
        # Superusers always have access; other staff need the explicit permission.
        return request.user.is_superuser or request.user.has_perm(
            "payments.view_officialdonationreceipt"
        )

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        return False  # Issued programmatically only

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False  # Cancel via receipt.cancel() in code, never via admin

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False  # CRA records are permanent


# ===========================================================================
# GovStack Payments BB Admin
# ===========================================================================
# These models support the GovStack bb-payments certification layer.
# All are read-only in admin (no add, no change, no delete on most).
# payee_functional_id and financial_address are NOT in list_display or
# search_fields — they are PII / sensitive and must not appear in logs.
# ===========================================================================


@admin.register(GovStackBeneficiary)
class GovStackBeneficiaryAdmin(admin.ModelAdmin):
    list_display = [  # noqa: RUF012
        "pk",
        "source_bb_id",
        "payment_modality",
        "financial_address_status",
        "is_active",
        "created_at",
        "updated_at",
    ]
    list_filter = ["is_active", "payment_modality"]  # noqa: RUF012
    # payee_functional_id intentionally excluded from search_fields —
    # it is a government-assigned functional ID and should not appear in URL params.
    search_fields = ["source_bb_id", "registering_institution_id"]  # noqa: RUF012
    readonly_fields = [  # noqa: RUF012
        "id",
        "source_bb_id",
        "payment_modality",
        "financial_address_status",
        "registering_institution_id",
        "is_active",
        "created_at",
        "updated_at",
    ]
    # payee_functional_id and financial_address are excluded from fieldsets below.
    fieldsets = [  # noqa: RUF012
        (
            "Identity",
            {
                "fields": [
                    "id",
                    "source_bb_id",
                    "registering_institution_id",
                    "is_active",
                ],
            },
        ),
        (
            "Payment Details",
            {
                "fields": ["payment_modality", "financial_address_status"],
                "description": (
                    "PayeeFunctionalID and FinancialAddress are excluded from this view. "
                    "FinancialAddress is Fernet-encrypted at rest. "
                    "These fields must not appear in admin UIs per the GovStack security policy."
                ),
            },
        ),
        (
            "Timestamps",
            {"fields": ["created_at", "updated_at"], "classes": ["collapse"]},
        ),
    ]
    ordering = ["-created_at"]  # noqa: RUF012

    @admin.display(description="Financial Address")
    def financial_address_status(self, obj) -> str:  # noqa: ANN001
        return "✓ Set" if obj.financial_address else "✗ Not set"

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        return False  # Registered via GovStack API only

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False  # Updated via GovStack API only

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False  # Beneficiary records are maintained via the API


@admin.register(BulkPaymentBatch)
class BulkPaymentBatchAdmin(admin.ModelAdmin):
    list_display = [  # noqa: RUF012
        "batch_id",
        "source_bb_id",
        "status",
        "total_amount",
        "completed_amount",
        "failed_amount",
        "created_at",
    ]
    list_filter = ["status"]  # noqa: RUF012
    search_fields = ["batch_id", "request_id", "source_bb_id"]  # noqa: RUF012
    readonly_fields = [  # noqa: RUF012
        "id",
        "request_id",
        "source_bb_id",
        "batch_id",
        "status",
        "correlation_id",
        "total_amount",
        "completed_amount",
        "failed_amount",
        "result_generated_at",
        "note",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]  # noqa: RUF012

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False  # Status updated by Celery tasks only

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False  # Financial records are permanent


class CreditInstructionInline(admin.TabularInline):
    model = CreditInstruction
    fields = ["instruction_id", "amount", "currency", "status", "failure_reason"]  # noqa: RUF012
    readonly_fields = ["instruction_id", "amount", "currency", "status", "failure_reason"]  # noqa: RUF012
    extra = 0
    can_delete = False
    show_change_link = False

    def has_add_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False


@admin.register(CreditInstruction)
class CreditInstructionAdmin(admin.ModelAdmin):
    list_display = [  # noqa: RUF012
        "instruction_id",
        "batch_batch_id",
        "amount",
        "currency",
        "status",
        "created_at",
    ]
    list_filter = ["status", "currency"]  # noqa: RUF012
    search_fields = ["instruction_id", "batch__batch_id"]  # noqa: RUF012
    list_select_related = ["batch"]  # noqa: RUF012
    readonly_fields = [  # noqa: RUF012
        "id",
        "batch",
        "instruction_id",
        "amount",
        "currency",
        "narration",
        "status",
        "failure_reason",
        "created_at",
        "updated_at",
    ]
    # payee_functional_id intentionally excluded.
    ordering = ["-created_at"]  # noqa: RUF012

    @admin.display(description="Batch ID", ordering="batch__batch_id")
    def batch_batch_id(self, obj) -> str:  # noqa: ANN001
        return obj.batch.batch_id

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False


@admin.register(PrepaymentValidationRequest)
class PrepaymentValidationRequestAdmin(admin.ModelAdmin):
    list_display = [  # noqa: RUF012
        "request_id",
        "batch_id",
        "status",
        "beneficiary_found",
        "financial_address_valid",
        "created_at",
    ]
    list_filter = ["status", "beneficiary_found", "financial_address_valid"]  # noqa: RUF012
    search_fields = ["request_id", "batch_id", "source_bb_id"]  # noqa: RUF012
    readonly_fields = [  # noqa: RUF012
        "id",
        "request_id",
        "source_bb_id",
        "batch_id",
        "instruction_id",
        "amount",
        "currency",
        "narration",
        "status",
        "beneficiary_found",
        "financial_address_valid",
        "created_at",
        "updated_at",
    ]
    # payee_functional_id intentionally excluded.
    ordering = ["-created_at"]  # noqa: RUF012

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False


@admin.register(GovStackVoucher)
class GovStackVoucherAdmin(admin.ModelAdmin):
    list_display = [  # noqa: RUF012
        "serial_number",
        "status",
        "amount",
        "currency",
        "group_code",
        "issuing_bb",
        "redeemed_at",
        "created_at",
    ]
    list_filter = ["status", "currency", "group_code", "issuing_bb"]  # noqa: RUF012
    # payee_functional_id intentionally excluded from search_fields.
    search_fields = ["serial_number", "group_code", "issuing_bb", "redemption_transaction_id"]  # noqa: RUF012
    readonly_fields = [  # noqa: RUF012
        "id",
        "serial_number",
        "voucher_secret_status",
        "amount",
        "currency",
        "group_code",
        "status",
        "issuing_bb",
        "registering_institution_id",
        "batch_id",
        "expiry_date",
        "redeemed_by_agent_id",
        "redeemed_merchant_name",
        "redeemed_merchant_bank_details",
        "redeemed_merchant_voucher_group",
        "redeemed_at",
        "redemption_transaction_id",
        "created_at",
        "updated_at",
    ]
    # payee_functional_id and voucher_secret excluded from fieldsets.
    fieldsets = [  # noqa: RUF012
        (
            "Voucher Identity",
            {
                "fields": [
                    "id",
                    "serial_number",
                    "voucher_secret_status",
                    "status",
                    "issuing_bb",
                    "registering_institution_id",
                ],
            },
        ),
        (
            "Value",
            {
                "fields": ["amount", "currency", "group_code", "expiry_date"],
            },
        ),
        (
            "Redemption",
            {
                "fields": [
                    "redeemed_by_agent_id",
                    "redeemed_merchant_name",
                    "redeemed_merchant_bank_details",
                    "redeemed_merchant_voucher_group",
                    "redeemed_at",
                    "redemption_transaction_id",
                ],
                "classes": ["collapse"],
            },
        ),
        (
            "Tracking",
            {
                "fields": ["batch_id", "created_at", "updated_at"],
                "classes": ["collapse"],
                "description": (
                    "payee_functional_id is excluded from this view per GovStack security policy."
                ),
            },
        ),
    ]
    ordering = ["-created_at"]  # noqa: RUF012

    @admin.display(description="Voucher Secret")
    def voucher_secret_status(self, obj) -> str:  # noqa: ANN001
        return "✓ Set (encrypted)" if obj.voucher_secret else "✗ Not set"

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        return False  # Created via GovStack API only

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False  # Status transitions via GovStack API only

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False  # Voucher records are permanent


@admin.register(GovStackPaymentAuditEntry)
class GovStackPaymentAuditEntryAdmin(admin.ModelAdmin):
    list_display = [  # noqa: RUF012
        "action",
        "actor_bb_id",
        "object_type",
        "object_pk",
        "request_id",
        "timestamp",
    ]
    list_filter = ["action", "object_type"]  # noqa: RUF012
    search_fields = ["action", "actor_bb_id", "object_pk", "request_id"]  # noqa: RUF012
    readonly_fields = [  # noqa: RUF012
        "id",
        "action",
        "actor_bb_id",
        "object_type",
        "object_pk",
        "request_id",
        "details",
        "timestamp",
    ]
    ordering = ["-timestamp"]  # noqa: RUF012

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        return False  # System-created only

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False  # Immutable: append-only log

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False  # Permanent: cannot be deleted


# ===========================================================================
# P2G — Bill Payments (Wave 5)
# ===========================================================================


@admin.register(GovStackBill)
class GovStackBillAdmin(admin.ModelAdmin):
    """
    GovStackBill admin.

    Bills are created by staff (import or direct admin entry) and read by the
    P2G API.  Admin allows add and change (status can be corrected by staff),
    but delete is blocked once a bill has GovStackBillPayment records attached
    (enforced by PROTECT FK on GovStackBillPayment.bill).
    """

    list_display = [  # noqa: RUF012
        "bill_id",
        "status",
        "amount",
        "currency",
        "due_date",
        "created_at",
    ]
    list_filter = ["status", "currency"]  # noqa: RUF012
    search_fields = ["bill_id", "description", "correlation_id"]  # noqa: RUF012
    # bill_id is always read-only — it is a stable external identifier referenced
    # by downstream systems, audit trails, and the P2G API URL path parameter.
    # Changing it after creation would silently break any cached reference held by
    # Source BBs and would orphan any GovStackBillPayment audit entries that link
    # back to this bill via its external identifier.
    # amount and currency are editable so staff can correct data-entry errors on
    # unpaid bills, but bill_id must never change once the bill is published.
    readonly_fields = ["id", "bill_id", "created_at", "updated_at"]  # noqa: RUF012
    ordering = ["-created_at"]  # noqa: RUF012

    fieldsets = (
        (
            "Bill Details",
            {
                "fields": (
                    "id",
                    "bill_id",
                    "amount",
                    "currency",
                    "description",
                    "status",
                    "due_date",
                    "correlation_id",
                ),
            },
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        # Deleting a bill that has payments attached is blocked at the DB layer
        # (PROTECT FK), but we also block it in admin to give a clear message
        # before the DELETE is even attempted.
        if obj is not None and obj.payments.exists():
            return False
        return True

    def delete_view(self, request, object_id, extra_context=None):  # noqa: ANN001, ANN201
        """
        Override to catch ProtectedError from a TOCTOU race condition.

        has_delete_permission() blocks deletion when payments exist, but
        there is a window between the permission check and the SQL DELETE
        where a concurrent POST /billTransferRequests could create a payment
        against the same bill.  Without this override, that race causes an
        unhandled ProtectedError → Django admin renders a 500.

        This override catches ProtectedError and redirects to the change page
        with a user-readable error message instead.
        """
        from django.contrib import messages
        from django.db.models import ProtectedError
        from django.http import HttpResponseRedirect
        from django.urls import reverse

        try:
            return super().delete_view(request, object_id, extra_context)
        except ProtectedError:
            obj = self.get_object(request, object_id)
            label = str(obj) if obj else object_id
            self.message_user(
                request,
                (
                    f'Bill "{label}" cannot be deleted because it has associated '
                    "payment records. Remove or reassign the payments first."
                ),
                level=messages.ERROR,
            )
            return HttpResponseRedirect(
                reverse(
                    f"admin:{self.opts.app_label}_{self.opts.model_name}_change",
                    args=[object_id],
                )
            )


@admin.register(GovStackBillPayment)
class GovStackBillPaymentAdmin(admin.ModelAdmin):
    """
    GovStackBillPayment admin (read-only).

    Payment records are created by the P2G API (POST /billTransferRequests)
    and must not be modified or deleted after creation.  The admin provides
    a read-only audit view only.
    """

    list_display = [  # noqa: RUF012
        "request_id",
        "bill",
        "status",
        "amount",
        "currency",
        "created_at",
    ]
    list_filter = ["status", "currency"]  # noqa: RUF012
    search_fields = ["request_id", "payment_reference_id", "correlation_id"]  # noqa: RUF012
    readonly_fields = [  # noqa: RUF012
        "id",
        "request_id",
        "bill",
        "bill_inquiry_request_id",
        "payment_reference_id",
        "correlation_id",
        "payer_fi_id",
        "platform_tenant_id",
        "amount",
        "currency",
        "status",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]  # noqa: RUF012

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        return False  # Created via P2G API only

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False  # Payment records are immutable

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False  # Payment records are permanent


# ===========================================================================
# BB Whitelist (GAP-4)
# ===========================================================================


@admin.register(GovStackRegisteredBB)
class GovStackRegisteredBBAdmin(admin.ModelAdmin):
    """
    GovStackRegisteredBB admin — manages the whitelist of Building Blocks
    authorised to call this BB's G2P endpoints when
    GOVSTACK_REQUIRE_REGISTERED_BB=True.

    Operators can:
      - Add new BB entries before enabling GOVSTACK_REQUIRE_REGISTERED_BB=True.
      - Deactivate BBs by unchecking is_active (no data loss).
      - View the bb_id and description.
      - Set allowed_platform_tenant_ids to opt a registered BB into P2G
        tenant-registry binding (certifiability-audit fix, Round 2 — HIGH
        finding): once non-empty, GovStackAPIView._validate_platform_tenant_id()
        rejects any X-Platform-TenantId this BB declares that isn't in the
        list. Leave empty (the default) for unrestricted, back-compat
        behaviour.

    bb_id is immutable after creation (it is the lookup key used in HTTP
    headers — changing it would silently break the calling BB).
    Delete is disabled; use is_active=False for suspension instead so the
    audit trail of when the BB was first registered is preserved.

    No PII is stored here — bb_id is an infrastructure identifier only.
    """

    list_display = [  # noqa: RUF012
        "bb_id",
        "is_active",
        "role",
        "description_short",
        "created_at",
        "updated_at",
    ]
    list_filter = ["is_active", "role"]  # noqa: RUF012
    search_fields = ["bb_id", "description"]  # noqa: RUF012
    ordering = ["bb_id"]  # noqa: RUF012
    # allowed_platform_tenant_ids intentionally NOT in list_display — it's a
    # JSON list, unsuited to the list view; it is editable via the change
    # form's fieldset below (certifiability-audit fix, Round 2 — HIGH
    # finding: opt-in tenant-registry binding for P2G endpoints).

    def get_readonly_fields(self, request, obj=None):  # noqa: ANN001, ANN201
        # Timestamps are always auto-set — show as read-only on both forms.
        # bb_id is immutable after creation (it's the lookup key in HTTP headers;
        # renaming it would silently break the calling BB).
        # On the ADD form (obj=None) bb_id must remain editable so operators can
        # set it. A class-level readonly_fields would hide the input on the ADD
        # form, making the admin incapable of creating new rows.
        base = ["created_at", "updated_at"]
        if obj is not None:
            # Change form: lock bb_id to prevent renaming.
            return [*base, "bb_id"]
        return base

    fieldsets = [  # noqa: RUF012
        (
            None,
            {
                "fields": ["bb_id", "description", "is_active", "role"],
            },
        ),
        (
            "P2G Tenant Scoping",
            {
                "fields": ["allowed_platform_tenant_ids"],
                "description": (
                    "Opt-in tenant-registry binding for P2G endpoints "
                    "(certifiability-audit fix). Leave empty for unrestricted "
                    "(back-compat) behaviour — only a non-empty list of "
                    "X-Platform-TenantId values enforces binding for this BB."
                ),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ["created_at", "updated_at"],
                "classes": ["collapse"],
            },
        ),
    ]

    @admin.display(description="Description")
    def description_short(self, obj) -> str:  # noqa: ANN001
        """Truncate long descriptions to keep list_display readable."""
        return (obj.description[:60] + "…") if len(obj.description) > 60 else obj.description

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        # Deletion is blocked — use is_active=False to suspend a BB.
        # This preserves the record of when the BB was first registered.
        return False
