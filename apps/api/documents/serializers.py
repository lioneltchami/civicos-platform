"""
apps/api/documents/serializers.py
==================================
DRF serializers for the Document Management BB REST API.

Three serializers cover all eight spec §18 endpoints:

  DocumentSerializer              — read-only; renders a Document instance for
                                    citizen and staff GET responses.
  DocumentUploadRequestSerializer — write; validates POST /request-upload/ body.
  DocumentAttachmentSerializer    — read-only; nested in the attached-list endpoint.

Security invariants enforced here
──────────────────────────────────
• ``_storage_key`` / ``storage_key`` are NEVER included in Meta.fields or any
  SerializerMethodField — they must never leave the service layer.
• ``scan_engine_result`` is hidden for citizens and unprivileged staff. It is
  returned only when ``request.user.has_perm("documents.view_quarantined")``
  is True. All other callers receive ``null``.
• ``uploaded_by_id`` is the UUID primary key only — never email or display name.
• ``original_filename`` is included in the document representation because it is
  needed for download UI, but it is gated behind ownership / coordinator checks
  at the view layer. It must NEVER appear in audit event_detail (enforced by the
  service layer, not here).

These serializers are pure presentation layer. All business logic lives in
apps/documents/services/*.
"""

from __future__ import annotations

import logging

from rest_framework import serializers

from apps.documents.models import Document, DocumentAttachment

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# DocumentSerializer (read-only)
# ─────────────────────────────────────────────────────────────────────────────


class DocumentSerializer(serializers.ModelSerializer):
    """
    Read-only JSON representation of a Document instance.

    Designed for citizen self-service and staff coordinator views.

    Security notes
    ──────────────
    • ``storage_key`` and ``_storage_key`` are NEVER in this serializer.
    • ``scan_engine_result`` is gated by ``documents.view_quarantined``
      permission — returns ``null`` for all other users.
    • ``uploaded_by_id`` is UUID pk only, never email.
    • Deleted documents (``deleted_at`` set) are excluded by the view layer
      before serialization; this serializer does not re-check.
    """

    # Expose pk as 'doc_id' (UUID string) so the client-facing field name is
    # stable even if the model's internal pk strategy changes.
    doc_id = serializers.UUIDField(source="pk", read_only=True)

    # Slug of the owning category — stable identifier for API consumers.
    category_slug = serializers.SlugRelatedField(
        source="category",
        slug_field="slug",
        read_only=True,
    )

    # Localised category name driven by Accept-Language / LANGUAGE_CODE.
    category_name = serializers.SerializerMethodField()

    # Human-readable display strings alongside the machine codes.
    scan_status_display = serializers.CharField(
        source="get_scan_status_display",
        read_only=True,
    )
    security_classification_display = serializers.CharField(
        source="get_security_classification_display",
        read_only=True,
    )

    # PK only — NEVER email, username, or display name.
    uploaded_by_id = serializers.UUIDField(
        source="uploaded_by.pk",
        read_only=True,
    )

    # Alias for legal_hold bool — more descriptive in JSON.
    is_on_legal_hold = serializers.BooleanField(
        source="legal_hold",
        read_only=True,
    )

    # Gated field — staff with view_quarantined perm only.
    scan_engine_result = serializers.SerializerMethodField()

    class Meta:
        model = Document
        # IMPORTANT: 'storage_key' and '_storage_key' must NEVER appear here.
        fields = [
            "doc_id",
            "category_slug",
            "category_name",
            "original_filename",
            "mime_type",
            "size_bytes",
            "scan_status",
            "scan_status_display",
            "version_number",
            "is_latest_version",
            "security_classification",
            "security_classification_display",
            "uploaded_by_id",
            "is_on_legal_hold",
            "scan_engine_result",
            "description",
            "expires_at",
            "retain_until",
            "created_at",
            "updated_at",
        ]
        # All fields are read-only — this serializer is never used for writes.
        read_only_fields = fields

    # ── SerializerMethodField implementations ─────────────────────────────────

    def get_category_name(self, obj: Document) -> str:
        """
        Return category name in the request language (en/fr).

        Falls back to English if no request context is available or if the
        language is not French. The field is never null because both name_en
        and name_fr are required on DocumentCategory.
        """
        request = self.context.get("request")
        lang = getattr(request, "LANGUAGE_CODE", "en") if request else "en"
        return obj.category.name_fr if lang.startswith("fr") else obj.category.name_en

    def get_scan_engine_result(self, obj: Document) -> str | None:
        """
        Return the ClamAV raw result string iff the requesting user holds the
        ``documents.view_quarantined`` permission.

        Returns ``None`` (serialised as JSON ``null``) for:
        - Citizens
        - Staff without the quarantine permission
        - Any request where the permission cannot be determined (no request context)

        This is the sole PIPEDA enforcement point for scan_engine_result in the
        DRF API layer. Never add scan_engine_result to Meta.fields directly.
        """
        request = self.context.get("request")
        if request is None:
            return None
        if request.user.has_perm("documents.view_quarantined"):
            # Return the raw result (may be empty string — normalise to None).
            return obj.scan_engine_result or None
        return None


# ─────────────────────────────────────────────────────────────────────────────
# DocumentUploadRequestSerializer (write-only)
# ─────────────────────────────────────────────────────────────────────────────


class DocumentUploadRequestSerializer(serializers.Serializer):
    """
    Validates the POST /api/v1/documents/request-upload/ request body.

    This serializer performs *early* input validation before the request reaches
    the service layer (apps.documents.services.upload.validate_upload_request).
    The service performs the full 8-layer OWASP upload validation; this
    serializer handles field-level type and format checks only.

    Fields
    ──────
    category_slug    — must reference an existing DocumentCategory.
    original_filename — for display purposes only; never stored as a path.
    mime_type        — MIME type header claimed by the client; the service
                       re-validates with magic bytes.
    size_bytes       — declared file size; must be ≥ 1 and ≤ configured maximum.
    """

    category_slug = serializers.SlugField(max_length=100)
    original_filename = serializers.CharField(
        max_length=255,
        help_text="Display filename only; not used as a storage path.",
    )
    mime_type = serializers.CharField(max_length=100)
    size_bytes = serializers.IntegerField(
        min_value=1,
        help_text="Declared file size in bytes (must be ≥ 1).",
    )

    def validate_category_slug(self, value: str) -> str:
        """Confirm the slug maps to an existing DocumentCategory."""
        from apps.documents.models import DocumentCategory

        if not DocumentCategory.objects.filter(slug=value).exists():
            raise serializers.ValidationError(
                f"Unknown document category: '{value}'."
            )
        return value

    def validate_size_bytes(self, value: int) -> int:
        """
        Reject files that exceed the configured citizen upload limit.

        The cap is read from settings.CIVICOS["DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES"].
        Falls back to 10 MiB if the setting is absent. The service layer also
        checks per-category limits; this is the global ceiling only.
        """
        from django.conf import settings

        civicos = getattr(settings, "CIVICOS", {})
        max_bytes = civicos.get(
            "DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES",
            10 * 1024 * 1024,  # 10 MiB default
        )
        if value > max_bytes:
            raise serializers.ValidationError(
                f"File size {value:,} bytes exceeds the maximum of "
                f"{max_bytes:,} bytes."
            )
        return value


# ─────────────────────────────────────────────────────────────────────────────
# DocumentAttachmentSerializer (read-only)
# ─────────────────────────────────────────────────────────────────────────────


class DocumentAttachmentSerializer(serializers.ModelSerializer):
    """
    Read-only JSON representation of a DocumentAttachment.

    Used by the staff-only GET /api/v1/documents/?attached_to=... endpoint.
    The nested ``document`` field uses DocumentSerializer and therefore
    inherits all its PIPEDA security constraints (no storage_key, etc.).
    """

    attachment_id = serializers.UUIDField(source="pk", read_only=True)

    # Full document representation — PIPEDA invariants apply transitively.
    document = DocumentSerializer(read_only=True)

    # Content type as "app_label.model" (e.g. "portal.servicerequest").
    attached_to_type = serializers.SerializerMethodField()

    # The concrete PK of the linked object (may be UUID string or integer string).
    attached_to_id = serializers.CharField(source="object_id", read_only=True)

    class Meta:
        model = DocumentAttachment
        fields = [
            "attachment_id",
            "document",
            "attached_to_type",
            "attached_to_id",
            "attachment_role",
            "note",
            "created_at",
        ]
        read_only_fields = fields

    def get_attached_to_type(self, obj: DocumentAttachment) -> str:
        """
        Return the content type as 'app_label.model'.

        Example: 'portal.servicerequest'
        """
        return f"{obj.content_type.app_label}.{obj.content_type.model}"
