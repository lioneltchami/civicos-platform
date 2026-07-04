"""
Document Management Building Block — Admin.

Admin interface for DocumentCategory, Document, DocumentAttachment,
and DocumentAccessToken.

Privacy constraints (MUST be enforced throughout this file):
  - `storage_key` (_storage_key) MUST NEVER appear in list_display,
    fieldsets, search_fields, or any admin response.
  - `original_filename` should only appear in readonly_fields for staff
    with `documents.view_document` permission. It is NEVER in list views
    (list_display) to avoid bulk PII exposure in admin index pages.
  - `scan_engine_result` is staff-internal; restrict to superusers.
  - `legal_hold_reason` and `deletion_reason` are staff-internal; never citizen-visible.
  - Document admin actions (soft-delete, legal hold) must check permissions explicitly.
  - Admin list views use `uploaded_by_id` (PK) not email — consistent with PIPEDA.
"""

import logging

from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import Document, DocumentAccessToken, DocumentAttachment, DocumentCategory

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# DocumentCategory
# ─────────────────────────────────────────────────────────────────────────────


@admin.register(DocumentCategory)
class DocumentCategoryAdmin(admin.ModelAdmin):
    list_display = [
        "slug",
        "name_en",
        "security_classification",
        "min_retention_days",
        "max_retention_days",
        "is_transitory",
        "created_at",
    ]
    list_filter = ["security_classification", "is_transitory"]
    search_fields = ["slug", "name_en", "name_fr"]
    ordering = ["name_en"]
    readonly_fields = ["created_at", "updated_at"]
    prepopulated_fields = {"slug": ("name_en",)}

    fieldsets = (
        (
            _("Identity"),
            {
                "fields": ("slug", "name_en", "name_fr", "description_en", "description_fr"),
            },
        ),
        (
            _("Security"),
            {
                "fields": ("security_classification",),
            },
        ),
        (
            _("Upload constraints"),
            {
                "fields": ("allowed_mime_types", "max_size_bytes"),
            },
        ),
        (
            _("Retention policy"),
            {
                "fields": (
                    "min_retention_days",
                    "max_retention_days",
                    "is_transitory",
                ),
                "description": _(
                    "Privacy Act s.6(1): minimum 730 days (2 years) for administrative records. "
                    "Transitory records are destroyed once purpose is fulfilled "
                    "(LAC Disposition Authorization #2016/001)."
                ),
            },
        ),
        (
            _("Timestamps"),
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Document
# ─────────────────────────────────────────────────────────────────────────────


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    """
    Admin for the Document model.

    PIPEDA constraints enforced:
      - `_storage_key` is excluded from all fieldsets. It does NOT appear in
        any admin view — not even for superusers.
      - `original_filename` is in readonly_fields only (never editable).
      - list_display uses `uploaded_by_id` (int PK), not `uploaded_by.email`.
      - `scan_engine_result` is only visible in the detail view; never in list.
    """

    # ── List view ─────────────────────────────────────────────────────────────

    list_display = [
        "pk_short",
        "category",
        "scan_status_badge",
        "security_classification",
        "version_number",
        "is_latest_version",
        "uploaded_by_id",   # PK only — PIPEDA: not email
        "size_bytes",
        "legal_hold",
        "created_at",
    ]
    list_filter = [
        "scan_status",
        "security_classification",
        "category",
        "legal_hold",
        "is_latest_version",
    ]
    search_fields = [
        "id",
        "category__slug",
        # Note: original_filename is intentionally NOT in search_fields
        # because it would expose PII in search result snippets.
    ]
    ordering = ["-created_at"]
    date_hierarchy = "created_at"

    # ── Detail view ───────────────────────────────────────────────────────────

    readonly_fields = [
        "id",
        "pk_short",
        "original_filename",  # readonly only; never editable
        "mime_type",
        "size_bytes",
        "scan_status",
        "scan_completed_at",
        "scan_engine_result",
        "version_number",
        "is_latest_version",
        "root_document",
        "expires_at",
        "retain_until",
        "last_admin_use_at",
        "deleted_at",
        "created_at",
        "updated_at",
        # storage_key is intentionally EXCLUDED — NEVER in admin
    ]

    fieldsets = (
        (
            _("Identity"),
            {
                "fields": ("id", "pk_short", "category", "uploaded_by"),
                "description": _("The uploaded_by field shows the user PK, not email (PIPEDA)."),
            },
        ),
        (
            _("File metadata"),
            {
                "fields": ("original_filename", "mime_type", "size_bytes"),
                "description": _(
                    "original_filename is stored for display only. "
                    "The actual storage path is never shown here (PIPEDA)."
                ),
            },
        ),
        (
            _("Scan status"),
            {
                "fields": ("scan_status", "scan_completed_at", "scan_engine_result"),
            },
        ),
        (
            _("Security classification"),
            {
                "fields": ("security_classification",),
            },
        ),
        (
            _("Versioning"),
            {
                "fields": ("version_number", "is_latest_version", "root_document"),
            },
        ),
        (
            _("Content"),
            {
                "fields": ("description",),
            },
        ),
        (
            _("Retention"),
            {
                "fields": ("expires_at", "retain_until", "last_admin_use_at"),
            },
        ),
        (
            _("Legal hold"),
            {
                "fields": ("legal_hold", "legal_hold_reason", "legal_hold_set_by"),
                "description": _(
                    "A legal hold supersedes all retention schedules. "
                    "Only set by Privacy Officer or legal counsel. "
                    "Requires documents.manage_legal_hold permission."
                ),
            },
        ),
        (
            _("Deletion"),
            {
                "fields": ("deleted_at", "deleted_by", "deletion_reason"),
                "classes": ("collapse",),
            },
        ),
        (
            _("Timestamps"),
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    # Document records are immutable through the admin interface.
    # All lifecycle operations (upload, legal hold, soft-delete, hard-delete)
    # MUST go through the service layer to enforce permissions and write audit logs.
    # Admin is a read-only forensics/observation tool for Document records.

    def has_add_permission(self, request) -> bool:
        # Creation must go through the upload pipeline (service layer).
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        # SECURITY: legal_hold, uploaded_by, security_classification, and other
        # lifecycle fields must only be modified via the service layer.
        # Editing via admin would bypass:
        #   - documents.manage_legal_hold permission check
        #   - AuditEventType.STATUS_CHANGED audit log write
        #   - document_legal_hold_changed signal
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        # Deletion must go through DocumentService.soft_delete() (service layer).
        return False

    # ── Custom display columns ─────────────────────────────────────────────────

    @admin.display(description=_("ID (short)"))
    def pk_short(self, obj) -> str:
        """Display the first 8 chars of the UUID for readability."""
        return str(obj.pk)[:8] if obj.pk else "—"

    @admin.display(description=_("Scan status"))
    def scan_status_badge(self, obj) -> str:
        colours = {
            Document.ScanStatus.PENDING_UPLOAD: "#888",
            Document.ScanStatus.SCANNING: "#e6a817",
            Document.ScanStatus.ACTIVE: "#2e7d32",
            Document.ScanStatus.QUARANTINED: "#c62828",
            Document.ScanStatus.DELETED: "#555",
        }
        colour = colours.get(obj.scan_status, "#888")
        label = obj.get_scan_status_display()
        return format_html(
            '<span style="color:{};font-weight:bold;">{}</span>',
            colour,
            label,
        )


# ─────────────────────────────────────────────────────────────────────────────
# DocumentAttachment
# ─────────────────────────────────────────────────────────────────────────────


@admin.register(DocumentAttachment)
class DocumentAttachmentAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "document",
        "content_type",
        "object_id",
        "attachment_role",
        "attached_by_id",  # PK only — PIPEDA
        "created_at",
    ]
    list_filter = ["content_type", "attachment_role"]
    search_fields = ["document__id", "object_id", "attachment_role"]
    ordering = ["-created_at"]
    raw_id_fields = ["document", "attached_by", "content_type"]
    readonly_fields = ["created_at", "updated_at"]

    def has_add_permission(self, request) -> bool:
        # Attachments must be created through the service layer.
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        # Attachments link documents to CivicOS records and form part of the
        # case record. Deletion via admin bypasses the service-layer audit trail.
        return False


# ─────────────────────────────────────────────────────────────────────────────
# DocumentAccessToken
# ─────────────────────────────────────────────────────────────────────────────


@admin.register(DocumentAccessToken)
class DocumentAccessTokenAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "document",
        "issued_to_id",   # PK only — PIPEDA
        "expires_at",
        "used_at",
        "is_valid_display",
        "created_at",
    ]
    list_filter = ["expires_at"]
    search_fields = ["document__id", "issued_to__id"]
    ordering = ["-created_at"]
    readonly_fields = [
        "id",
        "document",
        "issued_to",
        "token",
        "expires_at",
        "used_at",
        "ip_address",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        # Tokens are immutable; no field edits allowed.
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        # Tokens are audit evidence (who was issued a download token, when, from which IP).
        # Deleting them via admin would destroy the audit trail.
        return False

    @admin.display(description=_("Valid?"), boolean=True)
    def is_valid_display(self, obj) -> bool:
        return obj.is_valid
