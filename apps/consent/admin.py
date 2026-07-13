"""
Django admin registration for the Consent & Privacy building block.

Access policy:
  ConsentAuditEntry  — fully read-only (append-only model)
  ConsentRevision    — fully read-only (append-only tamper-proof chain)
  ConsentCategory    — full CRUD (system configuration)
  ConsentRecord      — read-only (citizens control via portal; staff view only)
  ConsentSignature   — fully read-only (cryptographic evidence)
  ConsentPolicy      — full CRUD (GovStack Policy management)
  ConsentWebhook     — read/update (secret masked; no inline secret exposure)
  DataExportRequest  — limited update (status/notes only; no sensitive tokens shown)
"""
from django.contrib import admin
from django.utils.html import format_html

from .models import (
    ConsentAuditEntry,
    ConsentCategory,
    ConsentPolicy,
    ConsentRecord,
    ConsentRevision,
    ConsentSignature,
    ConsentWebhook,
    DataExportRequest,
)


# ---------------------------------------------------------------------------
# ConsentPolicy — GovStack Policy (full CRUD for staff)
# ---------------------------------------------------------------------------

@admin.register(ConsentPolicy)
class ConsentPolicyAdmin(admin.ModelAdmin):
    list_display = ["name", "version", "jurisdiction", "industry_sector", "created_at"]
    list_filter = ["jurisdiction", "industry_sector"]
    search_fields = ["name", "version"]
    readonly_fields = ["created_at", "updated_at"]
    ordering = ["-created_at"]


# ---------------------------------------------------------------------------
# ConsentCategory — GovStack DataAgreement
# ---------------------------------------------------------------------------

@admin.register(ConsentCategory)
class ConsentCategoryAdmin(admin.ModelAdmin):
    list_display = ["slug", "name_en", "lawful_basis", "is_required", "is_active", "sort_order"]
    list_filter = ["is_required", "is_active", "lawful_basis"]
    prepopulated_fields = {"slug": ["name_en"]}
    ordering = ["sort_order", "slug"]


# ---------------------------------------------------------------------------
# ConsentRevision — append-only tamper-proof chain (fully read-only)
# ---------------------------------------------------------------------------

@admin.register(ConsentRevision)
class ConsentRevisionAdmin(admin.ModelAdmin):
    list_display = ["schema_name", "object_id", "timestamp", "authorized_by_pk"]
    list_filter = ["schema_name"]
    search_fields = ["object_id"]
    readonly_fields = [
        "schema_name",
        "object_id",
        "serialized_snapshot",
        "serialized_hash",
        "predecessor_hash",
        "authorized_by_individual",
        "authorized_by_other",
        "timestamp",
    ]
    ordering = ["-timestamp"]

    @admin.display(description="Authorized By PK")
    def authorized_by_pk(self, obj):
        return obj.authorized_by_individual_id or obj.authorized_by_other or "—"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# ConsentRecord — read-only for staff; citizens manage via portal
# ---------------------------------------------------------------------------

@admin.register(ConsentRecord)
class ConsentRecordAdmin(admin.ModelAdmin):
    list_display = [
        "citizen_pk", "category", "status", "state", "is_current",
        "granted_at", "withdrawn_at", "source",
    ]
    list_filter = ["status", "state", "is_current", "source", "category"]
    readonly_fields = [
        "citizen",
        "category",
        "status",
        "state",
        "is_current",
        "granted_at",
        "withdrawn_at",
        "actor_ip",
        "source",
        "consent_version",
        "data_agreement_revision",
        "data_agreement_revision_hash",
        "created_at",
        "updated_at",
    ]
    search_fields = ["citizen__pk"]  # never email — PIPEDA

    @admin.display(description="Citizen PK")
    def citizen_pk(self, obj):
        return obj.citizen_id

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# ConsentSignature — cryptographic evidence (fully read-only)
# ---------------------------------------------------------------------------

@admin.register(ConsentSignature)
class ConsentSignatureAdmin(admin.ModelAdmin):
    list_display = ["consent_record", "verification_type", "timestamp"]
    list_filter = ["verification_type"]
    readonly_fields = [
        "consent_record",
        "verification_type",
        "verification_payload",
        "verification_payload_hash",
        "verification_signed_by",
        "timestamp",
        "data_agreement_revision_hash",
    ]
    ordering = ["-timestamp"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# ConsentWebhook — read/update; secret shown only as presence indicator
# ---------------------------------------------------------------------------

@admin.register(ConsentWebhook)
class ConsentWebhookAdmin(admin.ModelAdmin):
    list_display = [
        "payload_url", "content_type", "is_disabled", "secret_set_indicator",
        "created_at",
    ]
    list_filter = ["is_disabled", "content_type"]
    readonly_fields = ["secret_set_indicator", "created_at", "updated_at"]
    fields = [
        "payload_url",
        "content_type",
        "is_disabled",
        "subscribed_events",
        "signature_header",
        "skipped_headers",
        "secret_set_indicator",
        "created_at",
        "updated_at",
    ]
    # secret_key is intentionally excluded from all admin fields — use the API
    # to rotate secrets; the admin must never display or allow editing the raw value.

    @admin.display(description="Secret configured")
    def secret_set_indicator(self, obj):
        # Show presence/absence only — never the actual value
        if obj.secret_key:
            return format_html('<span style="color:green">&#10003; Set</span>')
        return format_html('<span style="color:red">&#10007; Not set</span>')

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# DataExportRequest — PIPEDA right-of-access tracking
# ---------------------------------------------------------------------------

@admin.register(DataExportRequest)
class DataExportRequestAdmin(admin.ModelAdmin):
    list_display = ["citizen_pk", "status", "format", "requested_at", "processed_at", "expires_at"]
    list_filter = ["status", "format"]
    search_fields = ["citizen__pk"]  # never email — PIPEDA
    readonly_fields = [
        "citizen",
        "requested_at",
        "processed_at",
        "expires_at",
        "document",
        # download_token is sensitive — never shown in admin; use the API download endpoint
    ]
    fields = [
        "citizen",
        "status",
        "format",
        "notes",
        "requested_at",
        "processed_at",
        "expires_at",
        "document",
    ]

    @admin.display(description="Citizen PK")
    def citizen_pk(self, obj):
        return obj.citizen_id

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# ConsentAuditEntry — immutable audit trail (fully read-only)
# ---------------------------------------------------------------------------

@admin.register(ConsentAuditEntry)
class ConsentAuditEntryAdmin(admin.ModelAdmin):
    list_display = ["citizen_pk", "actor_pk", "action", "category", "timestamp", "actor_ip"]
    list_filter = ["action"]
    search_fields = ["citizen__pk"]  # never email — PIPEDA
    readonly_fields = [
        "citizen",
        "actor",
        "action",
        "category",
        "export_request",
        "actor_ip",
        "timestamp",
        "details",
    ]

    @admin.display(description="Citizen PK")
    def citizen_pk(self, obj):
        return obj.citizen_id

    @admin.display(description="Actor PK")
    def actor_pk(self, obj):
        return obj.actor_id

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
