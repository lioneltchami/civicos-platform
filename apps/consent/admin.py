"""
Django admin registration for the Consent & Privacy building block.

Access control:
  ConsentAuditEntry  — read-only (append-only model)
  ConsentCategory    — full CRUD
  ConsentRecord      — read-only (citizens control their own records)
  DataExportRequest  — staff can update status and notes only
"""
from django.contrib import admin

from .models import ConsentAuditEntry, ConsentCategory, ConsentRecord, DataExportRequest


@admin.register(ConsentCategory)
class ConsentCategoryAdmin(admin.ModelAdmin):
    list_display = ["slug", "name_en", "lawful_basis", "is_required", "is_active", "sort_order"]
    list_filter = ["is_required", "is_active", "lawful_basis"]
    prepopulated_fields = {"slug": ["name_en"]}
    ordering = ["sort_order", "slug"]


@admin.register(ConsentRecord)
class ConsentRecordAdmin(admin.ModelAdmin):
    list_display = ["citizen_pk", "category", "status", "granted_at", "withdrawn_at", "source"]
    list_filter = ["status", "source", "category"]
    readonly_fields = [
        "citizen",
        "category",
        "status",
        "granted_at",
        "withdrawn_at",
        "actor_ip",
        "source",
        "consent_version",
        "created_at",
        "updated_at",
    ]
    search_fields = ["citizen__pk"]  # never email — PIPEDA

    @admin.display(description="Citizen PK")
    def citizen_pk(self, obj):
        return obj.citizen_id

    def has_add_permission(self, request):
        return False  # citizens control their own consent

    def has_delete_permission(self, request, obj=None):
        return False  # consent records are permanent audit evidence


@admin.register(DataExportRequest)
class DataExportRequestAdmin(admin.ModelAdmin):
    list_display = ["citizen_pk", "status", "format", "requested_at", "processed_at", "expires_at"]
    list_filter = ["status", "format"]
    search_fields = ["citizen__pk"]  # never email — PIPEDA
    readonly_fields = [
        "citizen",
        "download_token",
        "requested_at",
        "processed_at",
        "expires_at",
        "document",
    ]
    fields = [
        "citizen",
        "status",
        "format",
        "notes",
        "download_token",
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
        return False  # PIPEDA — cannot delete export request records


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
