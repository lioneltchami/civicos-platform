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
    GovStackPaymentAuditEntry,
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

    def has_change_permission(self, request, obj=None):
        # Financial records must not be mutated via admin — gateway is sole writer.
        return False

    def has_delete_permission(self, request, obj=None):
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

    def has_add_permission(self, request, obj=None):
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
    list_display = [
        "gateway_charge_id",
        "intent_reference",
        "payment_method_type",
        "card_brand",
        "amount_paid",
        "processor_fee",
        "net_amount",
        "paid_at",
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

    def get_list_display(self, request):
        """Append refund_link only for users who may issue refunds (payments.add_refund)."""
        columns = list(super().get_list_display(request))
        if request.user.has_perm("payments.add_refund"):
            columns.append("refund_link")
        return columns

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
    search_fields = ["gateway_event_id", "event_type"]  # payload excluded: LIKE scan is slow + payload may contain PII
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

    def has_change_permission(self, request, obj=None):
        # is_test_mode controls whether live or test Stripe keys are used.
        # Flipping it via admin could route real donor money to a test account.
        # Changes require a code deploy + environment variable rotation.
        return False

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

    def has_delete_permission(self, request, obj=None):
        # CharitySettings rows are referenced by issued CRA receipts.
        # Deletion would corrupt the audit trail.
        return False


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

    def has_delete_permission(self, request, obj=None):
        # FeeSchedule rows are referenced by historical ServiceFeePayment records.
        # Deletion would break audit trail references.
        return False


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

    def has_delete_permission(self, request, obj=None):
        # TaxRate rows are embedded in historical receipts by snapshot (tax_rate_applied).
        # Deleting the source row would break backwards reconciliation.
        return False


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

    def has_delete_permission(self, request, obj=None):
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

    def has_view_permission(self, request, obj=None):
        # PIPEDA: donor_name_snapshot and donor_address_snapshot are personal information.
        # Require explicit payments.view_donation permission beyond basic staff status.
        return request.user.is_superuser or request.user.has_perm("payments.view_donation")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # Donation records are financial audit evidence — immutable after creation.
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
    # list_display contains only non-PII fields.
    # donor_legal_name, donor_address_*, and donor PII are restricted to the
    # detail view which is gated by has_view_permission.
    list_display = [
        "serial_number",
        "status",
        "receipt_date",
        "eligible_amount",
        "is_annual_consolidated",
        "email_sent",
    ]
    list_filter = ["status", "is_annual_consolidated", "donor_province"]
    # donor_legal_name excluded from search_fields — PIPEDA: name in URL/logs is a privacy violation.
    search_fields = ["serial_number", "charity_registration_number"]
    # pdf_path is intentionally excluded from readonly_fields, fieldsets, and list_display.
    # PII fields (donor_legal_name, donor_address_*) are in readonly_fields for the
    # detail view only; the detail view is gated by has_view_permission below.
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
        "email_sent",
        "created_at",
        "updated_at",
    ]
    ordering = ["-issued_at"]

    def has_view_permission(self, request, obj=None):
        # Superusers always have access; other staff need the explicit permission.
        return request.user.is_superuser or request.user.has_perm(
            "payments.view_officialdonationreceipt"
        )

    def has_add_permission(self, request):
        return False  # Issued programmatically only

    def has_change_permission(self, request, obj=None):
        return False  # Cancel via receipt.cancel() in code, never via admin

    def has_delete_permission(self, request, obj=None):
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
    list_display = [
        "pk",
        "source_bb_id",
        "payment_modality",
        "financial_address_status",
        "is_active",
        "created_at",
        "updated_at",
    ]
    list_filter = ["is_active", "payment_modality"]
    # payee_functional_id intentionally excluded from search_fields —
    # it is a government-assigned functional ID and should not appear in URL params.
    search_fields = ["source_bb_id", "registering_institution_id"]
    readonly_fields = [
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
    fieldsets = [
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
    ordering = ["-created_at"]

    @admin.display(description="Financial Address")
    def financial_address_status(self, obj) -> str:
        return "✓ Set" if obj.financial_address else "✗ Not set"

    def has_add_permission(self, request) -> bool:
        return False  # Registered via GovStack API only

    def has_change_permission(self, request, obj=None) -> bool:
        return False  # Updated via GovStack API only

    def has_delete_permission(self, request, obj=None) -> bool:
        return False  # Beneficiary records are maintained via the API


@admin.register(BulkPaymentBatch)
class BulkPaymentBatchAdmin(admin.ModelAdmin):
    list_display = [
        "batch_id",
        "source_bb_id",
        "status",
        "total_amount",
        "completed_amount",
        "failed_amount",
        "created_at",
    ]
    list_filter = ["status"]
    search_fields = ["batch_id", "request_id", "source_bb_id"]
    readonly_fields = [
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
    ordering = ["-created_at"]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False  # Status updated by Celery tasks only

    def has_delete_permission(self, request, obj=None) -> bool:
        return False  # Financial records are permanent


class CreditInstructionInline(admin.TabularInline):
    model = CreditInstruction
    fields = ["instruction_id", "amount", "currency", "status", "failure_reason"]
    readonly_fields = ["instruction_id", "amount", "currency", "status", "failure_reason"]
    extra = 0
    can_delete = False
    show_change_link = False

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(CreditInstruction)
class CreditInstructionAdmin(admin.ModelAdmin):
    list_display = [
        "instruction_id",
        "batch_batch_id",
        "amount",
        "currency",
        "status",
        "created_at",
    ]
    list_filter = ["status", "currency"]
    search_fields = ["instruction_id", "batch__batch_id"]
    list_select_related = ["batch"]
    readonly_fields = [
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
    ordering = ["-created_at"]

    @admin.display(description="Batch ID", ordering="batch__batch_id")
    def batch_batch_id(self, obj) -> str:
        return obj.batch.batch_id

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(PrepaymentValidationRequest)
class PrepaymentValidationRequestAdmin(admin.ModelAdmin):
    list_display = [
        "request_id",
        "batch_id",
        "status",
        "beneficiary_found",
        "financial_address_valid",
        "created_at",
    ]
    list_filter = ["status", "beneficiary_found", "financial_address_valid"]
    search_fields = ["request_id", "batch_id", "source_bb_id"]
    readonly_fields = [
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
    ordering = ["-created_at"]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(GovStackVoucher)
class GovStackVoucherAdmin(admin.ModelAdmin):
    list_display = [
        "serial_number",
        "status",
        "amount",
        "currency",
        "group_code",
        "issuing_bb",
        "redeemed_at",
        "created_at",
    ]
    list_filter = ["status", "currency", "group_code", "issuing_bb"]
    # payee_functional_id intentionally excluded from search_fields.
    search_fields = ["serial_number", "group_code", "issuing_bb", "redemption_transaction_id"]
    readonly_fields = [
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
    fieldsets = [
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
    ordering = ["-created_at"]

    @admin.display(description="Voucher Secret")
    def voucher_secret_status(self, obj) -> str:
        return "✓ Set (encrypted)" if obj.voucher_secret else "✗ Not set"

    def has_add_permission(self, request) -> bool:
        return False  # Created via GovStack API only

    def has_change_permission(self, request, obj=None) -> bool:
        return False  # Status transitions via GovStack API only

    def has_delete_permission(self, request, obj=None) -> bool:
        return False  # Voucher records are permanent


@admin.register(GovStackPaymentAuditEntry)
class GovStackPaymentAuditEntryAdmin(admin.ModelAdmin):
    list_display = [
        "action",
        "actor_bb_id",
        "object_type",
        "object_pk",
        "request_id",
        "timestamp",
    ]
    list_filter = ["action", "object_type"]
    search_fields = ["action", "actor_bb_id", "object_pk", "request_id"]
    readonly_fields = [
        "id",
        "action",
        "actor_bb_id",
        "object_type",
        "object_pk",
        "request_id",
        "details",
        "timestamp",
    ]
    ordering = ["-timestamp"]

    def has_add_permission(self, request) -> bool:
        return False  # System-created only

    def has_change_permission(self, request, obj=None) -> bool:
        return False  # Immutable: append-only log

    def has_delete_permission(self, request, obj=None) -> bool:
        return False  # Permanent: cannot be deleted
