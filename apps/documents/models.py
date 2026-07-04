"""
Document Management Building Block — Models.

Data model for controlled, PIPEDA-compliant document storage.

Key privacy invariants (enforced throughout this BB):
  - `_storage_key` (db_column="storage_key") is NEVER exposed in templates,
    serializers, admin list displays, API responses, or log messages.
    Access it only through `document.storage_key` property in the service layer,
    which is the designated internal accessor.
  - `original_filename` is NEVER written to audit event_detail (may contain PII
    such as a person's name in the filename). Log only document.pk.
  - `uploaded_by` identity is NEVER included in quarantine notifications.
  - Citizens receive 404 (not 403) for non-owned document PKs (IDOR prevention).
  - `legal_hold=True` blocks ALL automated disposal unconditionally.

Governing law: PIPEDA clause 4.5.3 & 4.7.5, Privacy Act s.6(1) & s.6(3),
TBS SPIN 2023-06-13, LAC Disposition Authorization #2016/001.
"""

import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel


# ─────────────────────────────────────────────────────────────────────────────
# QuerySet
# ─────────────────────────────────────────────────────────────────────────────


class DocumentQuerySet(models.QuerySet):
    """
    Custom queryset for Document.

    All service-layer queries should start from one of these methods to ensure
    correct filtering semantics (especially around soft-delete and scan status).
    """

    def active(self):
        """
        Documents that are scanned clean and not soft-deleted.

        Only ACTIVE documents may be downloaded by any user.
        PIPEDA: citizens must never access QUARANTINED or PENDING_UPLOAD files.
        """
        return self.filter(
            scan_status=Document.ScanStatus.ACTIVE,
            deleted_at__isnull=True,
        )

    def latest_versions(self):
        """
        Only the most-recent version of each document chain.

        Combine with .active() for the typical "show current documents" view.
        """
        return self.filter(is_latest_version=True)

    def pending_disposal(self):
        """
        Documents eligible for soft-delete disposal scheduling.

        Criteria:
          - expires_at has passed (max_retention_days exceeded)
          - retain_until has passed (Privacy Act s.6(1) minimum retention met)
          - No legal hold
          - Not already soft-deleted

        Both expires_at AND retain_until must be in the past before disposal
        is permitted. Privacy Act s.6(1) mandates minimum 2-year retention for
        administrative purpose records; retain_until encodes that floor date.

        NULL values for expires_at or retain_until are auto-excluded by the SQL
        <= comparison (NULL comparisons evaluate to UNKNOWN / false in SQL).

        Celery Beat runs this daily to flag records for disposal.
        """
        from django.utils import timezone

        now = timezone.now()
        return self.filter(
            expires_at__lte=now,
            retain_until__lte=now.date(),  # DateField — compare with date, not datetime
            legal_hold=False,
            deleted_at__isnull=True,
        )

    def pending_hard_delete(self, grace_days: int = 30):
        """
        Soft-deleted documents past the 30-day grace period — eligible for
        irreversible hard deletion (S3 delete + DB row delete).

        NIST SP 800-88 / OPC guidance: hard deletion must be irreversible.
        The grace period allows recovery of accidentally deleted documents.
        legal_hold=True is an absolute block even after grace period.
        """
        from datetime import timedelta

        from django.utils import timezone

        cutoff = timezone.now() - timedelta(days=grace_days)
        return self.filter(
            deleted_at__lte=cutoff,
            scan_status=Document.ScanStatus.DELETED,
            legal_hold=False,  # safety guard: legal hold blocks hard delete too
        )


# ─────────────────────────────────────────────────────────────────────────────
# DocumentCategory
# ─────────────────────────────────────────────────────────────────────────────


class DocumentCategory(models.Model):
    """
    Defines a type of document with its retention policy and security classification.

    Created by system admins (superusers), not end users.

    Examples:
      slug="service-request-evidence"  — citizen supporting documents
      slug="volunteer-certification"    — CRC / VSC certificates
      slug="cra-t4a-slip"              — CRA T4A (Protected B)
      slug="donation-receipt-pdf"      — official donation receipts
      slug="pipeda-data-export"        — PIPEDA export packages (transitory)

    Retention semantics (Privacy Act s.6(1) + PIPEDA):
      - min_retention_days: earliest disposal date after last administrative use
      - max_retention_days: latest disposal date (hard cap, unless legal hold)
      - is_transitory: True → destroy once purpose fulfilled (LAC DA #2016/001)
    """

    class SecurityClassification(models.TextChoices):
        UNCLASSIFIED = "unclassified", _("Unclassified")
        PROTECTED_A = "protected_a", _("Protected A")
        PROTECTED_B = "protected_b", _("Protected B")

    # ── Identity ─────────────────────────────────────────────────────────────

    name_en = models.CharField(
        max_length=200,
        verbose_name=_("Name (English)"),
    )
    name_fr = models.CharField(
        max_length=200,
        verbose_name=_("Name (French)"),
    )
    slug = models.SlugField(
        unique=True,
        verbose_name=_("Slug"),
        help_text=_(
            "Machine-readable identifier. Used in service-layer lookups. "
            "Example: 'service-request-evidence'"
        ),
    )
    description_en = models.TextField(
        blank=True,
        verbose_name=_("Description (English)"),
    )
    description_fr = models.TextField(
        blank=True,
        verbose_name=_("Description (French)"),
    )

    # ── Security Classification ───────────────────────────────────────────────

    security_classification = models.CharField(
        max_length=20,
        choices=SecurityClassification.choices,
        default=SecurityClassification.PROTECTED_B,
        verbose_name=_("Security classification"),
        help_text=_(
            "TBS SPIN 2023: aggregated citizen service delivery data defaults to "
            "Protected B. Lowering to Protected A requires Privacy Officer approval."
        ),
    )

    # ── Allowed MIME types and size ───────────────────────────────────────────

    allowed_mime_types = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("Allowed MIME types"),
        help_text=_(
            "JSON list of allowed MIME types for uploads in this category. "
            'Empty list means use the global CIVICOS["ALLOWED_UPLOAD_MIME_TYPES"] setting.'
        ),
    )
    max_size_bytes = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Max file size (bytes)"),
        help_text=_(
            "Maximum upload size in bytes for this category. "
            '0 means use the global CIVICOS["MAX_UPLOAD_SIZE"] setting.'
        ),
    )

    # ── Retention ─────────────────────────────────────────────────────────────

    min_retention_days = models.PositiveIntegerField(
        default=730,  # 2 years — Privacy Act s.6(1) minimum for administrative records
        verbose_name=_("Minimum retention (days)"),
        help_text=_(
            "Minimum days to retain after last administrative use. "
            "Privacy Act s.6(1) mandates at least 2 years (730 days) for "
            "administrative purpose records."
        ),
    )
    max_retention_days = models.PositiveIntegerField(
        default=2555,  # 7 years — typical GC operational record retention
        verbose_name=_("Maximum retention (days)"),
        help_text=_(
            "Maximum days to retain. After this, the document is scheduled "
            "for disposal unless a legal hold is active."
        ),
    )
    is_transitory = models.BooleanField(
        default=False,
        verbose_name=_("Transitory record"),
        help_text=_(
            "If True, documents in this category are destroyed once their "
            "purpose is fulfilled, regardless of min_retention_days — "
            "unless a legal hold is active. "
            "Ref: LAC Disposition Authorization #2016/001."
        ),
    )

    # ── Timestamps ────────────────────────────────────────────────────────────

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Document category")
        verbose_name_plural = _("Document categories")
        ordering = ["name_en"]

    def __str__(self) -> str:
        return self.slug

    def clean(self) -> None:
        if self.min_retention_days > self.max_retention_days:
            raise ValidationError(
                _(
                    "Minimum retention (%(min)d days) must not exceed "
                    "maximum retention (%(max)d days)."
                )
                % {"min": self.min_retention_days, "max": self.max_retention_days}
            )


# ─────────────────────────────────────────────────────────────────────────────
# Document
# ─────────────────────────────────────────────────────────────────────────────


class Document(BaseModel):
    """
    A single controlled document record.

    UUID primary key (from BaseModel / UUIDModel) prevents sequential enumeration.

    PIPEDA constraints (MUST be enforced throughout this BB):
      - `_storage_key` (db_column="storage_key") MUST NEVER appear in any
        template, serializer, admin list display, API response, or log message.
        Access via the `storage_key` property in service-layer internals only.
      - `original_filename` MUST NEVER appear in audit event_detail payloads
        (it may contain a person's name, DOB, or other PII in the filename).
        Log only document.pk in audit entries.
      - `uploaded_by` PK MUST NEVER appear in quarantine notifications sent
        externally. Admin quarantine alerts contain only document.pk.

    Security classification: inherited from category at creation; may be
    overridden per-document by a superuser with `documents.manage_classification`
    permission. Citizens cannot see or change classification.

    Versioning: root_document=None → this is version 1. Subsequent versions
    point to the version-1 Document. Exactly one per chain has is_latest_version=True.
    """

    class ScanStatus(models.TextChoices):
        PENDING_UPLOAD = "pending_upload", _("Pending upload")
        SCANNING = "scanning", _("Scanning")
        ACTIVE = "active", _("Active")
        QUARANTINED = "quarantined", _("Quarantined — Infected")
        DELETED = "deleted", _("Deleted")

    # ── Core ──────────────────────────────────────────────────────────────────

    category = models.ForeignKey(
        DocumentCategory,
        on_delete=models.PROTECT,
        related_name="documents",
        verbose_name=_("Category"),
    )

    # Who uploaded — FK only. NEVER log email. Use uploaded_by_id (PK) in logs.
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_documents",
        verbose_name=_("Uploaded by"),
    )

    # ── File metadata ─────────────────────────────────────────────────────────

    # The user-provided filename at upload time. Stored in DB only.
    # NEVER used as the filesystem/storage path. NEVER in audit event_detail.
    original_filename = models.CharField(
        max_length=255,
        verbose_name=_("Original filename"),
        help_text=_(
            "User-supplied filename. Never used as the storage path. "
            "PIPEDA: never write this value to audit event_detail."
        ),
    )

    # OWASP: randomised UUID-based storage key — completely independent of filename.
    # Format: "documents/{prefix}/{doc_uuid}/{file_uuid}.bin"
    # Leading underscore signals "private field — access via property only".
    # db_column="storage_key" keeps the DB column name clean.
    _storage_key = models.CharField(
        db_column="storage_key",
        max_length=500,
        verbose_name=_("Storage key"),
        help_text=_(
            "Internal S3/filesystem path. NEVER expose in templates, serializers, "
            "admin, API responses, or log messages. Access via service layer only."
        ),
    )

    mime_type = models.CharField(
        max_length=100,
        verbose_name=_("MIME type"),
        help_text=_("Validated magic-byte result — not the client-supplied Content-Type header."),
    )
    size_bytes = models.PositiveBigIntegerField(
        verbose_name=_("File size (bytes)"),
    )

    # ── Scan status ───────────────────────────────────────────────────────────

    scan_status = models.CharField(
        max_length=20,
        choices=ScanStatus.choices,
        default=ScanStatus.PENDING_UPLOAD,
        db_index=True,
        verbose_name=_("Scan status"),
    )
    scan_completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Scan completed at"),
    )
    scan_engine_result = models.TextField(
        blank=True,
        verbose_name=_("Scan engine result"),
        help_text=_(
            "Raw result string from ClamAV (e.g. 'OK' or 'FOUND: Eicar-Test-Signature'). "
            "Stored for forensics; NEVER displayed to citizens."
        ),
    )

    # ── Security classification ───────────────────────────────────────────────

    security_classification = models.CharField(
        max_length=20,
        choices=DocumentCategory.SecurityClassification.choices,
        default=DocumentCategory.SecurityClassification.PROTECTED_B,
        verbose_name=_("Security classification"),
        help_text=_(
            "Inherits from category at creation. Overridable per-document "
            "by authorised admin only. Citizens cannot see or change this."
        ),
    )

    # ── Versioning ────────────────────────────────────────────────────────────

    version_number = models.PositiveSmallIntegerField(
        default=1,
        verbose_name=_("Version number"),
    )

    # Null means THIS document is version 1 of its chain.
    # Non-null points to the version-1 Document in the chain.
    root_document = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="versions",
        verbose_name=_("Root document"),
        help_text=_(
            "FK to version 1 of this document chain. "
            "Null if and only if this document IS version 1."
        ),
    )

    # Exactly one Document per root chain must have this True.
    # Enforced by DocumentService.create_new_version() using select_for_update().
    is_latest_version = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name=_("Is latest version"),
        help_text=_(
            "Exactly one Document per root chain must have this set to True. "
            "Managed exclusively by DocumentService.create_new_version()."
        ),
    )

    # ── Retention and disposal ────────────────────────────────────────────────

    # Set to created_at + category.max_retention_days at creation.
    # Celery Beat checks daily; disposal is triggered when expires_at AND
    # retain_until are both in the past.
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Expires at"),
        help_text=_(
            "When this document is eligible for disposal (max retention exceeded). "
            "Null = does not expire (e.g. legal hold or manual override)."
        ),
    )

    # Privacy Act s.6(1): must not dispose before this date.
    # Set to max(created_at + category.min_retention_days,
    #            last_admin_use_at + 730 days).
    # Updated whenever the document is used for an administrative purpose.
    retain_until = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("Retain until"),
        help_text=_(
            "Earliest date on which disposal is permitted. "
            "Set to max(created_at + min_retention_days, last_admin_use_at + 730 days). "
            "Updated whenever the document is used for an administrative purpose. "
            "Privacy Act s.6(1)."
        ),
    )

    last_admin_use_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Last administrative use at"),
        help_text=_(
            "Last timestamp this document was used for an administrative purpose "
            "(e.g. referenced in a staff decision). Drives retain_until calculation."
        ),
    )

    # ── Legal hold ────────────────────────────────────────────────────────────

    # ATIP / litigation hold — supersedes all retention schedules.
    # TBS: "ATIP and litigation holds supersede all normal schedules."
    legal_hold = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name=_("Legal hold"),
        help_text=_(
            "If True, this document is exempt from all scheduled disposal. "
            "Set only by Privacy Officer or legal counsel "
            "(requires documents.manage_legal_hold permission). "
            "Ref: TBS guidance on ATIP and litigation holds."
        ),
    )
    legal_hold_reason = models.TextField(
        blank=True,
        verbose_name=_("Legal hold reason"),
        help_text=_("Non-PII description of why the hold was applied. Staff-internal only."),
    )
    legal_hold_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="legal_hold_documents",
        verbose_name=_("Legal hold set by"),
    )

    # ── Soft delete ───────────────────────────────────────────────────────────

    # Soft-delete timestamp. Set by DocumentService.soft_delete().
    # Hard deletion (S3 + DB row) happens after 30-day grace period by Celery
    # task, unless legal_hold is True.
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Deleted at"),
    )
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="deleted_documents",
        verbose_name=_("Deleted by"),
    )
    deletion_reason = models.TextField(
        blank=True,
        verbose_name=_("Deletion reason"),
        help_text=_("Staff-provided reason for deletion. Never exposed to citizens."),
    )

    # ── Description ───────────────────────────────────────────────────────────

    description = models.TextField(
        blank=True,
        verbose_name=_("Description"),
        help_text=_(
            "Staff or citizen description of what this document contains. "
            "Must not contain sensitive PII — PII lives in the referenced records."
        ),
    )

    # ── Manager ───────────────────────────────────────────────────────────────

    objects = DocumentQuerySet.as_manager()

    class Meta:
        verbose_name = _("Document")
        verbose_name_plural = _("Documents")
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["scan_status", "deleted_at"],
                name="doc_scan_deleted_idx",
            ),
            models.Index(
                fields=["expires_at", "legal_hold", "deleted_at"],
                name="doc_expiry_hold_idx",
            ),
            models.Index(
                fields=["uploaded_by", "created_at"],
                name="doc_uploader_created_idx",
            ),
            models.Index(
                fields=["root_document", "is_latest_version"],
                name="doc_root_latest_idx",
            ),
        ]

    def __str__(self) -> str:
        # PIPEDA: do NOT include original_filename — may contain PII.
        # This string appears in admin logs, Sentry breadcrumbs, management output.
        return f"Document #{self.pk} [v{self.version_number}, {self.scan_status}]"

    @property
    def storage_key(self) -> str:
        """
        Internal accessor for the storage key.

        This property exists for the service-layer internals only.
        Callers should use DocumentService.generate_download_url() which:
          1. Validates permissions
          2. Writes the audit log entry
          3. Returns a short-lived signed URL (not the key itself)

        CRITICAL: Never expose `_storage_key` or this property's return value
        in templates, serializers, admin, or API responses.
        """
        return self._storage_key

    @property
    def is_deleted(self) -> bool:
        """True if this document has been soft-deleted."""
        return self.deleted_at is not None

    def clean(self) -> None:
        errors = {}

        if self.root_document_id is not None and str(self.root_document_id) == str(self.pk):
            errors["root_document"] = ValidationError(
                _("A document cannot be its own root document.")
            )

        if self.version_number < 1:
            errors["version_number"] = ValidationError(
                _("Version number must be at least 1.")
            )

        if self.root_document is None and self.version_number != 1:
            errors["version_number"] = ValidationError(
                _("A document with no root document must be version 1.")
            )

        if errors:
            raise ValidationError(errors)


# ─────────────────────────────────────────────────────────────────────────────
# DocumentAttachment (generic linker)
# ─────────────────────────────────────────────────────────────────────────────


class DocumentAttachment(BaseModel):
    """
    Generic through-model linking a Document to any CivicOS object.

    Use this instead of adding a ForeignKey to Document on each target model.
    Supported target models include: ServiceRequest, WorkItem,
    VolunteerApplication, Honorarium, etc.

    Cardinality:
      - Many Documents may be attached to one content_object.
      - One Document may be attached to multiple content_objects.

    Example:
        DocumentAttachment.objects.create(
            document=cert_doc,
            content_object=application,
            attachment_role="volunteer_certification",
            attached_by=coordinator_user,
        )
    """

    document = models.ForeignKey(
        Document,
        on_delete=models.PROTECT,  # never cascade-delete a doc from an attachment
        related_name="attachments",
        verbose_name=_("Document"),
    )

    # Generic FK — points to any CivicOS model instance.
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        verbose_name=_("Content type"),
    )
    object_id = models.CharField(
        max_length=50,
        verbose_name=_("Object ID"),
        help_text=_("Supports UUID (36 chars) and integer PKs."),
    )
    content_object = GenericForeignKey("content_type", "object_id")

    # Discriminates between attachment roles on the same object.
    attachment_role = models.CharField(
        max_length=50,
        default="supporting_evidence",
        verbose_name=_("Attachment role"),
        help_text=_(
            "Role of this document on the linked object. "
            "Examples: 'supporting_evidence', 'decision_letter', "
            "'volunteer_certification', 'cra_t4a_slip'."
        ),
    )

    # Who performed the attachment
    attached_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="document_attachments",
        verbose_name=_("Attached by"),
    )
    note = models.TextField(
        blank=True,
        verbose_name=_("Note"),
        help_text=_("Optional staff note about why this document was attached. Not visible to citizens."),
    )

    class Meta:
        verbose_name = _("Document attachment")
        verbose_name_plural = _("Document attachments")
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["content_type", "object_id"],
                name="docattach_ct_obj_idx",
            ),
            models.Index(
                fields=["document", "content_type"],
                name="docattach_doc_ct_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"DocumentAttachment #{self.pk}: "
            f"doc={self.document_id} → {self.content_type}:{self.object_id}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# DocumentAccessToken
# ─────────────────────────────────────────────────────────────────────────────


def _generate_token() -> str:
    """Generate a 64-character cryptographically random hex token."""
    return uuid.uuid4().hex + uuid.uuid4().hex  # 64 hex chars = 256 bits


class DocumentAccessToken(BaseModel):
    """
    Single-use, time-limited token authorising one download of one document.

    Instead of exposing S3 presigned URLs directly (which can be forwarded
    and re-used by unintended recipients), we issue an opaque token. The
    download view validates it, writes an audit log entry, then:
      - For small files (≤ 1 MB): proxies the file as a Django response.
      - For large files (> 1 MB): generates a fresh short-lived presigned URL
        (TTL = CIVICOS['DOCUMENT_PRESIGNED_URL_TTL_SECONDS']) and returns HTTP 302.

    Tokens expire after CIVICOS['DOCUMENT_ACCESS_TOKEN_TTL_SECONDS'] (default: 300).
    Used tokens are immediately invalidated (used_at is set on first use).
    Expired/used tokens are purged by Celery Beat daily.

    IP address is stored for audit correlation, masked per CivicOS policy
    (last octet of IPv4 zeroed; last 80 bits of IPv6 masked).
    """

    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="access_tokens",
        verbose_name=_("Document"),
    )
    issued_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name=_("Issued to"),
    )

    token = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        default=_generate_token,
        verbose_name=_("Token"),
        help_text=_("Opaque 64-char hex token. Single-use; invalidated on first use."),
    )
    expires_at = models.DateTimeField(
        db_index=True,
        verbose_name=_("Expires at"),
    )
    used_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Used at"),
        help_text=_("Set on first (and only) use. Null means token has not been consumed."),
    )

    # Masked IP for audit correlation: IPv4 last octet zeroed (e.g. 192.168.1.0),
    # IPv6 last 80 bits masked. Never store full IP in this table.
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name=_("IP address (masked)"),
        help_text=_("Masked IP address at token issuance. Last octet zeroed for IPv4."),
    )

    class Meta:
        verbose_name = _("Document access token")
        verbose_name_plural = _("Document access tokens")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"DocumentAccessToken #{self.pk} [doc={self.document_id}]"

    @property
    def is_valid(self) -> bool:
        """True if the token has not been used and has not expired."""
        from django.utils import timezone

        return self.used_at is None and self.expires_at > timezone.now()
