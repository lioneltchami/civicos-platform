# Document Management Building Block — Specification

**Version:** 1.0  
**Date:** 2026-07-03  
**Status:** Approved for implementation  
**Author:** CivicOS Architecture Team  
**Classification:** Unclassified (this document); system handles up to Protected B

---

## Table of Contents

1. [Purpose and Scope](#1-purpose-and-scope)
2. [Governing Law and Standards](#2-governing-law-and-standards)
3. [Security Classification Framework](#3-security-classification-framework)
4. [Architecture Overview](#4-architecture-overview)
5. [Data Model](#5-data-model)
6. [Storage Strategy](#6-storage-strategy)
7. [Upload Pipeline](#7-upload-pipeline)
8. [Virus-Scan Pipeline](#8-virus-scan-pipeline)
9. [File Validation](#9-file-validation)
10. [Versioning](#10-versioning)
11. [Retention and Disposal Policy](#11-retention-and-disposal-policy)
12. [Access Control and Permissions](#12-access-control-and-permissions)
13. [Audit Trail](#13-audit-trail)
14. [Signals and Events](#14-signals-and-events)
15. [Notifications](#15-notifications)
16. [Integration Points with Existing BBs](#16-integration-points-with-existing-bbs)
17. [Admin Interface](#17-admin-interface)
18. [API Contract](#18-api-contract)
19. [Celery Tasks and Beat Schedule](#19-celery-tasks-and-beat-schedule)
20. [Templates and WCAG 2.1 AA](#20-templates-and-wcag-21-aa)
21. [Configuration and Settings](#21-configuration-and-settings)
22. [Dependencies](#22-dependencies)
23. [Migration Plan for Existing File Fields](#23-migration-plan-for-existing-file-fields)
24. [Test Strategy](#24-test-strategy)
25. [Implementation Waves](#25-implementation-waves)
26. [Open Questions / Deferred Items](#26-open-questions--deferred-items)

---

## 1. Purpose and Scope

### 1.1 Problem Statement

CivicOS currently has **no unified, controlled document storage layer**. File handling is scattered and inconsistent across building blocks:

| Location | Anti-pattern | Risk |
|---|---|---|
| `payments.OfficialDonationReceipt.pdf_path` | Raw `CharField(max_length=500)` storing filesystem path | No access logging, no retention enforcement, no virus scan, path leakage possible |
| `consent.DataExportRequest.storage_path` | Same raw-path anti-pattern | Same risks; PIPEDA export package with no controlled lifecycle |
| `volunteers.Certification.document` | Raw `FileField(upload_to=...)` | No versioning, no access log, no virus scan, no retention policy |
| `portal.ServiceRequest.submission_data` | Citizen-uploaded files stored as JSON blobs (base64 or paths) | Unstructured, unscannable, no per-file access control |
| `volunteers.Honorarium` (T4A PDFs) | `t4a_issued` flag exists but no document reference | Issued T4A PDFs have nowhere to be stored or tracked |

### 1.2 What This BB Provides

The Document Management BB introduces `apps/documents/` as a first-class CivicOS building block providing:

- **Controlled upload pipeline** — multi-layer validation, presigned S3 upload (server never handles raw bytes in prod), async virus scanning via ClamAV
- **Versioning** — immutable version records; latest-version access via service function
- **Security classification** — Protected A / Protected B tagging per document type
- **Retention policy enforcement** — configurable minimum/maximum retention per document category, legal-hold flag, automated expiry via Celery Beat
- **Universal FK integration** — generic attachment mechanism so any existing model can reference documents without a migration to the document model
- **Full audit trail** — every upload, download, version creation, deletion, and access-control change written to `apps.audit.AuditLogEntry` via the chain-of-custody hash chain
- **PIPEDA-compliant disposal** — soft-delete → 30-day grace → hard delete from storage; irreversible per OPC/NIST SP 800-88 guidance
- **WCAG 2.1 AA accessible UI** — bilingual (en/fr) upload interface with `aria-live` progress, `aria-invalid` error states, keyboard-accessible drag-and-drop fallback

### 1.3 Out of Scope (this version)

- CMS content documents (already handled by `wagtail.documents` / `cms.CustomDocument`)
- PDF content-disarm and reconstruct (CDR) — deferred to v1.1 (see §26)
- Officer-level document e-signing — deferred to a future Signatures BB
- Full-text indexing / OCR — deferred to a future Search BB
- Files larger than 50 MB — 10 MB citizen, 50 MB staff hard cap; larger files require out-of-band process

---

## 2. Governing Law and Standards

### 2.1 PIPEDA (Personal Information Protection and Electronic Documents Act, S.C. 2000, c. 5)

**Principle 5 — Limiting Use, Disclosure, and Retention (clause 4.5.3):**
> "Personal information that is no longer required to fulfil the identified purposes should be destroyed, erased, or made anonymous. Organizations shall develop guidelines and implement procedures to govern the destruction of personal information."

**Principle 7 — Safeguards (clause 4.7.5):**
> "Care shall be used in the disposal or destruction of personal information, to prevent unauthorized parties from gaining access to the information."

**Practical requirements for this BB:**
- Every document must have an identified purpose and a retention schedule attached to it at creation time.
- Automated disposal pipeline must be in place — manual deletion is insufficient.
- Disposal must be irreversible (overwrite, then delete from S3; no "just delete the pointer").
- Consent withdrawal stops future use but does not override legal retention minimums.

### 2.2 Privacy Act (R.S.C. 1985, c. P-21)

**Section 6(1) — Minimum retention for administrative purpose records:**  
Personal information used for an *administrative purpose* must be retained for **at least 2 years** after its last use in that purpose, to allow the individual a reasonable opportunity to access it.

**Section 6(3) — Disposal:**  
Institutions must dispose in accordance with regulations and TBS directives.

**Extended holds:**
- If an ATIP request is received → retain until 2 years after the institution's response
- If an OPC complaint is filed → retain until 2 years after the Commissioner's finding
- If Federal Court review → retain until 2 years after the court review is concluded
- Legal hold flag on the `Document` model supersedes the retention schedule

### 2.3 TBS Directive on Service and Digital (2021)

- Each institution must establish retention periods for information resources of business value.
- Transitory records: dispose when no longer needed for business purposes (LAC Disposition Authorization #2016/001).
- ATIP and litigation holds supersede all normal schedules — even transitory records cannot be destroyed after a request is received.

### 2.4 TBS Security Policy Implementation Notice (SPIN 2023-06-13)

> "The personal information of individuals that is used to deliver GC services and benefits should be categorized as no higher than **Protected B**. This categorization is not affected by the degree of aggregation or by where the information is stored, including in the cloud."

CivicOS documents must therefore be treated as minimum Protected B by default for aggregated citizen data.

### 2.5 OWASP File Upload Cheat Sheet (current)

Mandatory defence-in-depth layers:

1. Extension allowlist (not blocklist)
2. Content-Type header (untrusted — secondary check only)
3. Magic byte / file signature validation (`python-magic` / `libmagic`)
4. ZIP bomb detection (CVE-2024-0450, Medium, Sep 2024)
5. Virus scan (ClamAV / clamd)
6. Filename randomisation (UUID-based storage key, original name in DB only)
7. Storage path isolation (outside web root; never served directly)

### 2.6 WCAG 2.1 AA

All upload UI components must satisfy:

| SC | Level | Requirement |
|---|---|---|
| 1.1.1 | A | Non-text content alternatives |
| 1.3.1 | A | Info and Relationships (label association) |
| 1.4.3 | AA | Contrast ≥ 4.5:1 |
| 2.1.1 | A | Keyboard-accessible drag-and-drop fallback |
| 2.4.7 | AA | Visible focus indicator |
| 3.3.2 | A | Labels or instructions on all file inputs |
| 4.1.2 | A | Name, Role, Value via ARIA |
| 4.1.3 | AA | Status messages via `aria-live` without focus shift |

---

## 3. Security Classification Framework

Based on TBS Standard on Security Categorization (Directive on Security Management, Appendix J) and PSPC Levels of Security (updated 2024-12-31):

| Level | Meaning | CivicOS Examples |
|---|---|---|
| **Unclassified** | Public information; no injury if disclosed | Public CMS documents |
| **Protected A** | Injury to individual if compromised | Single citizen name + address |
| **Protected B** | Serious injury to individual if compromised | Financial records, medical info, aggregated service delivery data |
| **Protected C** | Extremely grave injury | Not in scope for this BB |

**Default classification for this BB:** `PROTECTED_B`

Every `DocumentCategory` carries a `security_classification` field. Staff may lower to `PROTECTED_A` only for specific categories approved by the Privacy Officer. Citizens cannot see or change classification.

**Personnel screening implication (informational — for deployment teams):**

| Info Level | Staff / Contractor Screening Required |
|---|---|
| Protected A/B | Reliability Status |
| Protected C | Enhanced Reliability Status |

---

## 4. Architecture Overview

```
┌────────────────────────────────────────────────────────────────────────────┐
│                          CITIZEN / STAFF BROWSER                           │
└────────────────────┬────────────────────────────────┬───────────────────────┘
                     │  1. Request presigned URL        │  5. Confirm upload
                     ▼                                  │
┌────────────────────────────────┐                      │
│     Django View                │                      │
│  DocumentRequestUploadView     │◄─────────────────────┘
│  (creates Document in          │
│   PENDING_UPLOAD state)        │  6. Set status=SCANNING
│  Returns: {presigned_url,      │     dispatch scan_document.delay(pk)
│            doc_id, fields}     │
└────────────┬───────────────────┘
             │  2. Presigned POST URL (boto3 generate_presigned_post)
             ▼
┌────────────────────────────────┐
│     Amazon S3                  │
│   /quarantine/ prefix          │◄── 3. Browser POSTs directly (no Django server)
└────────────┬───────────────────┘
             │  4. Browser callback to Django confirm endpoint
             │
┌────────────────────────────────┐
│     Celery Worker              │
│   scan_document(doc_pk)        │  7. Stream file from S3 → ClamAV daemon
│                                │     CLEAN → move to /active/, set status=ACTIVE
│                                │     INFECTED → delete from S3, status=QUARANTINED
│                                │     notify admin on quarantine
└────────────────────────────────┘

Storage prefixes:
  s3://bucket/documents/quarantine/{uuid}/{uuid}.bin   ← post-upload, pre-scan
  s3://bucket/documents/active/{uuid}/{uuid}.bin       ← clean, accessible
  s3://bucket/documents/deleted/{uuid}/{uuid}.bin      ← soft-deleted (30-day grace)

Django never handles raw file bytes in production.
Dev: local filesystem (MEDIA_ROOT/documents/).
```

---

## 5. Data Model

### 5.1 `DocumentCategory`

Configures retention policy and classification per document type.

```python
class DocumentCategory(models.Model):
    """
    Defines a type of document with its retention policy and security level.
    Created by system admins, not end users.

    Examples: "Service Request — Supporting Evidence",
              "Volunteer Certification", "CRA T4A Slip",
              "Donation Receipt PDF", "PIPEDA Data Export".
    """

    class SecurityClassification(models.TextChoices):
        UNCLASSIFIED = "unclassified", _("Unclassified")
        PROTECTED_A  = "protected_a",  _("Protected A")
        PROTECTED_B  = "protected_b",  _("Protected B")

    # Identity
    name_en        = models.CharField(max_length=200)
    name_fr        = models.CharField(max_length=200)
    slug           = models.SlugField(unique=True)  # e.g. "service-request-evidence"
    description_en = models.TextField(blank=True)
    description_fr = models.TextField(blank=True)

    # Classification
    security_classification = models.CharField(
        max_length=20,
        choices=SecurityClassification.choices,
        default=SecurityClassification.PROTECTED_B,
    )

    # Allowed content types for this category
    # JSON list, e.g. ["application/pdf", "image/jpeg", "image/png"]
    allowed_mime_types = models.JSONField(
        default=list,
        help_text="Empty list = use global CIVICOS['ALLOWED_UPLOAD_MIME_TYPES'] setting.",
    )
    max_size_bytes = models.PositiveIntegerField(
        default=0,
        help_text="0 = use global CIVICOS['MAX_UPLOAD_SIZE'] setting.",
    )

    # Retention
    # PIPEDA Principle 5 + Privacy Act s.6(1): both min and max required.
    min_retention_days = models.PositiveIntegerField(
        default=730,  # 2 years — Privacy Act s.6(1) minimum for administrative records
        help_text="Minimum days to retain after last administrative use. "
                  "Privacy Act s.6(1) mandates ≥ 2 years for administrative purpose records.",
    )
    max_retention_days = models.PositiveIntegerField(
        default=2555,  # 7 years — typical government operational record retention
        help_text="Maximum days to retain. After this, document is scheduled for disposal "
                  "unless a legal hold is active.",
    )
    # Some categories are transitory (destroy once purpose fulfilled, e.g. temp export packages)
    is_transitory = models.BooleanField(
        default=False,
        help_text="If True, documents in this category are destroyed once their "
                  "purpose is fulfilled, regardless of min_retention_days "
                  "(unless a legal hold is active). "
                  "Ref: LAC Disposition Authorization #2016/001.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = "Document Category"
        verbose_name_plural = "Document Categories"
        ordering            = ["name_en"]

    def __str__(self) -> str:
        return self.slug
```

### 5.2 `Document` (primary model)

```python
class Document(BaseModel):  # UUID PK + created_at + updated_at (from apps.core.models)
    """
    A single controlled document record.

    PIPEDA constraints:
    - `storage_key` must NEVER be exposed in any template, serializer, API response,
      or log. Expose only `pk` and `original_filename` to authorised parties.
    - `original_filename` must be stored separately from the actual storage key
      (storage key is always a UUID-based random path).
    - Access must be audited on every view/download via apps.audit.

    Security classification: inherited from category; can be overridden per document.
    """

    class ScanStatus(models.TextChoices):
        PENDING_UPLOAD = "pending_upload", _("Pending Upload")
        SCANNING       = "scanning",       _("Scanning")
        ACTIVE         = "active",         _("Active")
        QUARANTINED    = "quarantined",    _("Quarantined — Infected")
        DELETED        = "deleted",        _("Deleted")

    # ── Core fields ───────────────────────────────────────────────────────────

    category = models.ForeignKey(
        DocumentCategory,
        on_delete=models.PROTECT,
        related_name="documents",
    )

    # Who uploaded it (FK → AUTH_USER_MODEL; never log email — log pk only)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_documents",
    )

    # ── File metadata ─────────────────────────────────────────────────────────

    # The user-provided filename at upload time.
    # Stored in DB only — NEVER used as the filesystem path.
    original_filename = models.CharField(max_length=255)

    # OWASP: randomised storage key — UUIDv4-based path, independent of filename.
    # Format: "documents/active/{doc_uuid}/{file_uuid}.bin"
    # NEVER expose this value externally. Guarded by property with audit log.
    _storage_key = models.CharField(
        db_column="storage_key",
        max_length=500,
    )

    mime_type    = models.CharField(max_length=100)   # validated magic-byte result
    size_bytes   = models.PositiveBigIntegerField()

    # ── Scan status ───────────────────────────────────────────────────────────

    scan_status = models.CharField(
        max_length=20,
        choices=ScanStatus.choices,
        default=ScanStatus.PENDING_UPLOAD,
        db_index=True,
    )
    scan_completed_at  = models.DateTimeField(null=True, blank=True)
    scan_engine_result = models.TextField(
        blank=True,
        help_text="Raw result string from ClamAV (CLEAN / FOUND: <virus_name>). "
                  "Stored for forensics; never displayed to citizens.",
    )

    # ── Security classification ───────────────────────────────────────────────

    security_classification = models.CharField(
        max_length=20,
        choices=DocumentCategory.SecurityClassification.choices,
        default=DocumentCategory.SecurityClassification.PROTECTED_B,
        help_text="Defaults to category classification; can be overridden per-document "
                  "by an authorised admin.",
    )

    # ── Versioning ────────────────────────────────────────────────────────────

    version_number = models.PositiveSmallIntegerField(default=1)

    # Points to the first version in the chain; null means this IS the first version.
    root_document = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="versions",
        help_text="FK to version 1 of this document chain. "
                  "Null iff this document is version 1.",
    )

    is_latest_version = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Exactly one Document per root chain must have this True. "
                  "Enforced in DocumentService.create_new_version().",
    )

    # ── Retention / disposal ──────────────────────────────────────────────────

    # Set to created_at + category.max_retention_days at creation time.
    # Celery Beat task checks this daily.
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="When this document is eligible for disposal. "
                  "Null = does not expire (legal hold or manual override).",
    )

    # Privacy Act s.6(1): must not dispose before this date regardless of expires_at.
    retain_until = models.DateField(
        null=True,
        blank=True,
        help_text="Earliest date on which disposal is permitted. "
                  "Set to max(created_at + category.min_retention_days, last_used_for_admin_purpose + 730 days). "
                  "Updated whenever the document is used for an administrative purpose.",
    )

    last_admin_use_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last timestamp this document was used for an administrative purpose "
                  "(e.g. referenced in a staff decision). Drives retain_until calculation.",
    )

    # ATIP / litigation hold — supersedes all retention schedules.
    legal_hold = models.BooleanField(
        default=False,
        db_index=True,
        help_text="If True, this document is exempt from scheduled disposal. "
                  "Legal holds are set by the Privacy Officer or legal counsel. "
                  "Ref: TBS guidance — 'ATIP and litigation holds supersede all normal schedules.'",
    )
    legal_hold_reason = models.TextField(
        blank=True,
        help_text="Non-PII description of why legal hold was applied. "
                  "Coordinator-internal only.",
    )
    legal_hold_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="legal_hold_documents",
    )

    # ── Soft delete ───────────────────────────────────────────────────────────

    # Soft-delete timestamp. Set by DocumentService.soft_delete().
    # Hard deletion (from S3 + DB row) happens after 30-day grace period
    # by Celery task, unless legal_hold is True.
    deleted_at   = models.DateTimeField(null=True, blank=True, db_index=True)
    deleted_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="deleted_documents",
    )
    deletion_reason = models.TextField(
        blank=True,
        help_text="Staff-provided reason for deletion. Not exposed to citizens.",
    )

    # ── Purpose / description ─────────────────────────────────────────────────

    description = models.TextField(
        blank=True,
        help_text="Staff or citizen description of what this document contains. "
                  "Must never contain sensitive PII (stored in audit trail).",
    )

    objects = DocumentQuerySet.as_manager()  # see §5.5

    class Meta:
        verbose_name        = "Document"
        verbose_name_plural = "Documents"
        ordering            = ["-created_at"]
        indexes = [
            models.Index(fields=["scan_status", "deleted_at"]),
            models.Index(fields=["expires_at", "legal_hold", "deleted_at"]),
            models.Index(fields=["uploaded_by", "created_at"]),
            models.Index(fields=["root_document", "is_latest_version"]),
        ]

    def __str__(self) -> str:
        # PIPEDA: do not include original_filename (could contain PII) in __str__
        # used in admin logs, Sentry, management commands.
        return f"Document #{self.pk} [v{self.version_number}, {self.scan_status}]"

    @property
    def storage_key(self) -> str:
        """
        Access-guarded property. Callers should never use this directly;
        use DocumentService.generate_download_url() which also writes the audit log.
        This property exists only for the service layer internals.
        """
        return self._storage_key

    def clean(self) -> None:
        if self.root_document_id == self.pk:
            raise ValidationError("A document cannot be its own root.")
        if self.version_number < 1:
            raise ValidationError("version_number must be ≥ 1.")
        if self.root_document is None and self.version_number != 1:
            raise ValidationError("A document with no root must be version 1.")
```

### 5.3 `DocumentAttachment` (generic linker)

Rather than adding `document` FKs to every existing model (which requires migrations on each), use a through-model with a `GenericForeignKey` that lets any model attach documents without DB schema changes on the target model.

```python
class DocumentAttachment(BaseModel):
    """
    Generic through-model linking a Document to any CivicOS object
    (ServiceRequest, WorkItem, VolunteerApplication, Honorarium, etc.)

    Use this instead of adding a ForeignKey to Document on each target model.
    Specific FKs (e.g. Certification.document) are migrated directly only
    where a one-to-one relationship is semantically correct.

    Cardinality: many Documents may be attached to one content_object.
                 one Document may be attached to multiple content_objects
                 (e.g. a letter referenced by both ServiceRequest and WorkItem).
    """

    document = models.ForeignKey(
        Document,
        on_delete=models.PROTECT,  # never cascade-delete a doc from an attachment
        related_name="attachments",
    )

    # Generic FK: points to ServiceRequest, WorkItem, VolunteerApplication, etc.
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id    = models.CharField(max_length=50)  # supports UUIDs and integers
    content_object = GenericForeignKey("content_type", "object_id")

    # Discriminates between attachment roles on the same object.
    # E.g. on ServiceRequest: "supporting_evidence" vs "decision_letter"
    attachment_role = models.CharField(
        max_length=50,
        default="supporting_evidence",
        help_text="Role of this document on the linked object. "
                  "Choices depend on the linked model type.",
    )

    # Who attached it
    attached_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="document_attachments",
    )
    note = models.TextField(
        blank=True,
        help_text="Optional staff note about why this document was attached. "
                  "Not exposed to citizens.",
    )

    class Meta:
        verbose_name        = "Document Attachment"
        verbose_name_plural = "Document Attachments"
        ordering            = ["-created_at"]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
            models.Index(fields=["document", "content_type"]),
        ]

    def __str__(self) -> str:
        return f"DocumentAttachment #{self.pk}: doc={self.document_id} → {self.content_type}:{self.object_id}"
```

### 5.4 `DocumentAccessToken`

Replaces raw S3 signed URLs for download. Short-lived, single-use.

```python
class DocumentAccessToken(BaseModel):
    """
    Single-use, time-limited token authorising one download of one document.

    Instead of exposing S3 presigned URLs directly (which can be forwarded),
    we issue an opaque token. The download view validates it, writes an audit
    log entry, then proxies the file from S3 (small files) or redirects to a
    fresh short-lived presigned URL (large files).

    Tokens expire after CIVICOS['DOCUMENT_ACCESS_TOKEN_TTL_SECONDS'] (default: 300).
    Used tokens are immediately invalidated. Expired/used tokens are purged by
    Celery Beat daily.
    """

    document     = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="access_tokens")
    issued_to    = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    token        = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at   = models.DateTimeField(db_index=True)
    used_at      = models.DateTimeField(null=True, blank=True)
    ip_address   = models.GenericIPAddressField(null=True, blank=True)  # masked: last octet zeroed

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"DocumentAccessToken #{self.pk} [doc={self.document_id}]"

    @property
    def is_valid(self) -> bool:
        from django.utils import timezone
        return self.used_at is None and self.expires_at > timezone.now()
```

### 5.5 `DocumentQuerySet`

```python
class DocumentQuerySet(models.QuerySet):
    def active(self):
        """Documents that are scanned clean and not soft-deleted."""
        return self.filter(scan_status=Document.ScanStatus.ACTIVE, deleted_at__isnull=True)

    def latest_versions(self):
        """Only the most-recent version of each document chain."""
        return self.filter(is_latest_version=True)

    def pending_disposal(self):
        """Eligible for disposal: expired, not on legal hold, not deleted."""
        from django.utils import timezone
        return self.filter(
            expires_at__lte=timezone.now(),
            legal_hold=False,
            deleted_at__isnull=True,
        )

    def pending_hard_delete(self, grace_days: int = 30):
        """Soft-deleted more than grace_days ago; eligible for hard deletion."""
        from django.utils import timezone
        from datetime import timedelta
        cutoff = timezone.now() - timedelta(days=grace_days)
        return self.filter(deleted_at__lte=cutoff, scan_status=Document.ScanStatus.DELETED)
```

---

## 6. Storage Strategy

### 6.1 Dev (local filesystem)

```python
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
}
DOCUMENTS_STORAGE_PREFIX = "documents"  # MEDIA_ROOT/documents/
```

File layout:
```
MEDIA_ROOT/
  documents/
    quarantine/{doc_uuid}/{file_uuid}.bin
    active/{doc_uuid}/{file_uuid}.bin
    deleted/{doc_uuid}/{file_uuid}.bin
```

### 6.2 Production (S3 via django-storages 1.14.6)

```python
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name":      env("AWS_STORAGE_BUCKET_NAME"),
            "region_name":      env("AWS_S3_REGION_NAME", default="ca-central-1"),
            "signature_version": "s3v4",       # always explicit
            "default_acl":      None,          # private — NEVER public-read
            "querystring_auth": True,          # presigned URLs only
            "querystring_expire": 300,         # 5-minute signed URL TTL
            "object_parameters": {
                "ServerSideEncryption": "aws:kms",  # Protected B requirement
                "SSEKMSKeyId": env("AWS_KMS_KEY_ID", default=""),
            },
        },
    },
}
```

**KMS encryption:** Protected B documents must be encrypted at rest with a customer-managed KMS key (not the default AWS-managed key). This aligns with the SPIN 2023 requirement that aggregated citizen service delivery data be treated as Protected B including in the cloud.

### 6.3 Storage Key Generation

Storage key is generated at Document creation time:

```python
import uuid

def _make_storage_key(doc_uuid: str, prefix: str = "quarantine") -> str:
    """
    Returns a randomised storage path that is completely independent of
    the user-provided filename. The original filename is stored only in DB.
    OWASP: 'Creating a random string as a filename, such as generating a UUID/GUID, is essential.'
    """
    file_uuid = uuid.uuid4().hex
    return f"documents/{prefix}/{doc_uuid}/{file_uuid}.bin"
```

Django **never serves documents directly**. All access goes through `DocumentDownloadView` which validates the `DocumentAccessToken`, writes an audit log, then either:
- **(dev / small files ≤ 1 MB):** Streams from storage backend and serves as Django response
- **(prod / files > 1 MB):** Generates a fresh short-lived presigned URL (TTL = 300 s) and returns HTTP 302

---

## 7. Upload Pipeline

### 7.1 Citizen Upload Flow (production, S3)

```
Step 1 — POST /api/v1/documents/request-upload/
  Request:  { category_slug, original_filename, mime_type, size_bytes }
  Response: { doc_id, upload_url, upload_fields, expires_at }
  Side-effects:
    - Validate category, mime_type (allowlist), size_bytes (cap)
    - Validate extension (allowlist)
    - Create Document(scan_status=PENDING_UPLOAD)
    - Generate presigned POST via boto3 (NOT django-storages — no native support)
    - Return presigned fields to browser

Step 2 — Browser POSTs directly to S3 (quarantine prefix)
  Django is NOT in this data path.
  S3 enforces: content-length-range, content-type, key prefix.

Step 3 — POST /api/v1/documents/{doc_id}/confirm-upload/
  Request:  { doc_id }   (authenticated; same user as Step 1)
  Response: { doc_id, scan_status: "scanning" }
  Side-effects:
    - Verify file exists at quarantine key in S3 (head_object)
    - Validate magic bytes (stream first 8 KB from S3 → python-magic)
    - Validate ZIP bomb if extension is zip/docx/xlsx (check entry count + ratio)
    - Update Document(scan_status=SCANNING)
    - dispatch scan_document.apply_async(args=[doc_id], countdown=2)
    - Write audit: document_uploaded event

Step 4 — Celery task scan_document(doc_id)
  - Stream file from S3 quarantine prefix into ClamAV daemon via pyclamd
  - CLEAN:
      Move quarantine key → active key in S3 (copy + delete)
      Update Document(scan_status=ACTIVE, scan_completed_at=now)
      Fire document_scan_clean signal
      Send notification to uploader (if citizen): "Your document is ready"
  - INFECTED:
      Delete from S3 quarantine (irreversible)
      Update Document(scan_status=QUARANTINED)
      Fire document_quarantined signal
      Notify system admin (not citizen — avoid disclosing scan details)
      Write audit: document_quarantined event with engine result
```

### 7.2 Dev Upload Flow (local filesystem)

Same steps 1 and 3; step 2 is replaced by direct multipart upload to Django (standard Django file handling). `scan_document` task is still dispatched, but ClamAV availability is optional in dev — if `clamd` is not running, the task logs a warning and sets `scan_status=ACTIVE` directly (gated by `CIVICOS['CLAMAV_REQUIRED']` setting, default `False` in dev, `True` in prod).

### 7.3 Staff Upload Flow

Staff may upload documents (e.g. decision letters, T4A slips) through the backoffice interface. Same pipeline; higher allowed MIME type set (e.g. DOCX, XLSX in addition to PDF/images); size cap is 50 MB for staff vs 10 MB for citizens.

---

## 8. Virus-Scan Pipeline

### 8.1 ClamAV Integration

**Library:** `pyclamd` (direct clamd protocol interface — preferred over the dated `django-clamd` package)

**Daemon config (clamd.conf additions):**
```
# Stream files directly from S3 — set higher than default 25 MB for government use
StreamMaxLength 200M
MaxFileSize 200M
MaxScanSize 400M
```

**Task implementation pattern:**
```python
@shared_task(
    bind=True,
    max_retries=5,
    default_retry_delay=30,
    queue="documents",
    reject_on_worker_lost=True,
    acks_late=True,
)
def scan_document(self, doc_pk: str) -> None:
    """
    Stream document from storage into ClamAV and update scan_status.
    Retries with exponential backoff if ClamAV daemon is unavailable.
    PIPEDA: never log filename, uploader email, or storage_key.
    """
    try:
        document = Document.objects.select_for_update().get(pk=doc_pk)
        if document.scan_status != Document.ScanStatus.SCANNING:
            return  # idempotency guard

        result = _stream_to_clamav(document)  # raises ClamdError on daemon unavailable

        if result == "OK":
            _mark_clean(document)
        else:
            _mark_quarantined(document, result)

    except ClamdError as exc:
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 30)
```

### 8.2 Quarantine Handling

- Quarantined documents are **never accessible** by any user.
- S3 object is **immediately deleted** from quarantine prefix.
- Document row is retained (with `scan_status=QUARANTINED`) for the audit trail.
- System admin receives a notification with `document_pk` only (no filename, no uploader PII).
- Citizen receives a generic "Your document could not be processed; please try again or contact support" message — **no disclosure of virus detection**.

---

## 9. File Validation

All validation layers run in `DocumentService.validate_upload_request()` (step 1) and `DocumentService.validate_confirmed_upload()` (step 3):

### Layer 1: Category and Intent Check

```python
category = get_object_or_404(DocumentCategory, slug=category_slug)
if not _user_may_upload_to_category(user, category):
    raise PermissionDenied
```

### Layer 2: Size Check

```python
max_size = category.max_size_bytes or settings.CIVICOS["MAX_UPLOAD_SIZE"]
if size_bytes > max_size:
    raise ValidationError(_("File exceeds maximum size of %(max)s bytes.") % {"max": max_size})
if size_bytes <= 0:
    raise ValidationError(_("File size must be greater than zero."))
```

### Layer 3: Extension Allowlist

```python
allowed_exts = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".docx", ".xlsx", ".csv"}
ext = Path(original_filename).suffix.lower()
if ext not in allowed_exts:
    raise ValidationError(_("File type %(ext)s is not permitted.") % {"ext": ext})
```

### Layer 4: MIME Type Header Check (step 1 — untrusted, secondary only)

```python
allowed_mimes = category.allowed_mime_types or settings.CIVICOS["ALLOWED_UPLOAD_MIME_TYPES"]
if claimed_mime_type not in allowed_mimes:
    raise ValidationError(_("Content type %(mime)s is not permitted.") % {"mime": claimed_mime_type})
```

### Layer 5: Magic Byte Validation (step 3 — authoritative)

```python
import magic
first_bytes = _read_first_bytes_from_storage(storage_key, length=8192)
detected_mime = magic.Magic(mime=True).from_buffer(first_bytes)
if detected_mime not in allowed_mimes:
    raise ValidationError(_("File content does not match its declared type."))
```

### Layer 6: ZIP Bomb Detection (step 3 — for zip/docx/xlsx only)

```python
if detected_mime in ("application/zip", "application/vnd.openxmlformats-officedocument.*"):
    with zipfile.ZipFile(io.BytesIO(buffer)) as zf:
        entries = zf.infolist()
        if len(entries) > 1000:  # entry count limit
            raise ValidationError(_("Archive contains too many entries."))
        total_uncompressed = sum(e.file_size for e in entries)
        total_compressed   = sum(e.compress_size for e in entries)
        if total_compressed > 0 and (total_uncompressed / total_compressed) > 200:
            raise ValidationError(_("Archive compression ratio is suspicious."))
```

---

## 10. Versioning

### 10.1 Rules

- Every `Document` record is **immutable** once `scan_status=ACTIVE`. Files are never overwritten.
- Uploading a new version creates a **new Document record** with `root_document` pointing to version 1 and `version_number` incremented.
- Only **one version** per root chain may have `is_latest_version=True` at a time.
- Prior versions remain accessible to staff (for audit purposes) but are hidden from citizens.

### 10.2 Service Function

```python
def create_new_version(
    *,
    root_or_any_version: Document,
    uploaded_by: User,
    original_filename: str,
    mime_type: str,
    size_bytes: int,
) -> tuple[Document, str, dict]:
    """
    Returns (new_document, presigned_url, presigned_fields).
    Atomically sets is_latest_version=False on previous latest version.
    """
    with transaction.atomic():
        root = root_or_any_version.root_document or root_or_any_version
        prev_latest = Document.objects.select_for_update().filter(
            root_document=root, is_latest_version=True
        ).get()
        prev_latest.is_latest_version = False
        prev_latest.save(update_fields=["is_latest_version", "updated_at"])

        new_doc = Document.objects.create(
            category=root.category,
            uploaded_by=uploaded_by,
            original_filename=original_filename,
            _storage_key=_make_storage_key(str(uuid.uuid4()), "quarantine"),
            mime_type=mime_type,
            size_bytes=size_bytes,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
            root_document=root,
            version_number=prev_latest.version_number + 1,
            is_latest_version=True,
            security_classification=root.security_classification,
            expires_at=_compute_expires_at(root.category),
            retain_until=_compute_retain_until(root.category),
        )
        presigned = _generate_presigned_post(new_doc)
        return new_doc, presigned["url"], presigned["fields"]
```

---

## 11. Retention and Disposal Policy

### 11.1 Retention Schedule

| Document Category | min_retention_days | max_retention_days | is_transitory |
|---|---|---|---|
| Service Request — Supporting Evidence | 730 (2 yr) | 2555 (7 yr) | No |
| Service Request — Decision Letter | 730 (2 yr) | 2555 (7 yr) | No |
| Volunteer Application — Supporting Docs | 730 (2 yr) | 1825 (5 yr) | No |
| Volunteer Certification | 365 (1 yr) | 1825 (5 yr) | No |
| CRA T4A Slip | 2555 (7 yr) | 2555 (7 yr) | No |
| Donation Receipt PDF | 2555 (7 yr) | 2555 (7 yr) | No |
| PIPEDA Data Export Archive | 0 | 30 | Yes (transitory) |
| Staff Decision Memo | 730 (2 yr) | 3650 (10 yr) | No |

### 11.2 Disposal Lifecycle

```
Document created
    │
    ├── expires_at = created_at + category.max_retention_days
    ├── retain_until = created_at + category.min_retention_days
    │
    ▼
Celery Beat: expire_documents() (daily at 02:00 UTC)
    │  Find: expires_at ≤ now AND legal_hold=False AND deleted_at IS NULL
    │  Action: soft_delete(reason="retention_expired") for each
    │
    ▼ (30-day grace period)
Celery Beat: hard_delete_expired_documents() (daily at 03:00 UTC)
    │  Find: deleted_at ≤ now - 30 days AND scan_status=DELETED
    │  Action: for each:
    │    1. Delete S3 object (irreversible — per OPC/NIST SP 800-88 guidance)
    │    2. Null out _storage_key field (reference no longer valid)
    │    3. Write audit: document_hard_deleted
    │    4. Document row is RETAINED (for audit trail — never hard-delete the row)
    │
    ▼
Document row persists (audit trail preserved)
_storage_key is cleared; original_filename, created_at, category, scan_result all retained
```

### 11.3 Legal Hold

- Setting `legal_hold=True` **immediately** exempts the document from all automated disposal.
- `legal_hold` can only be set/unset by users with `documents.manage_legal_hold` permission.
- Every legal hold change is written to the audit log with `legal_hold_set_by`, `legal_hold_reason`, and a before/after state snapshot.
- When a legal hold is **lifted**, the normal retention schedule resumes from the current date.

### 11.4 Transitory Record Disposal

For `is_transitory=True` categories (e.g. PIPEDA export archives):

```python
# In DocumentService, called after the export is delivered to the citizen:
def mark_purpose_fulfilled(document: Document, actor: User) -> None:
    """
    For transitory documents, triggers disposal once purpose is fulfilled.
    Ref: LAC Disposition Authorization #2016/001.
    PIPEDA Principle 5: destroy once purpose fulfilled.
    """
    if not document.category.is_transitory:
        raise ValueError("mark_purpose_fulfilled called on non-transitory document.")
    with transaction.atomic():
        document.soft_delete(
            actor=actor,
            reason="transitory_purpose_fulfilled",
        )
```

---

## 12. Access Control and Permissions

### 12.1 Django Permissions

```python
class Meta:
    permissions = [
        ("view_document",           "Can view/download documents"),
        ("upload_document",         "Can upload new documents"),
        ("upload_staff_document",   "Can upload staff-only documents (e.g. decision letters)"),
        ("delete_document",         "Can soft-delete documents"),
        ("view_all_documents",      "Can view documents uploaded by other users (staff)"),
        ("view_quarantined",        "Can view quarantined document metadata (admin only)"),
        ("manage_legal_hold",       "Can set/unset legal hold on documents"),
        ("view_document_versions",  "Can view all versions of a document chain"),
        ("hard_delete_document",    "Can trigger hard deletion (system admin only)"),
    ]
```

### 12.2 Ownership Rules

- **Citizens:** may view and download their own documents only. May not view documents uploaded by other citizens. 404 (not 403) for non-owned PKs (IDOR prevention — 403 confirms existence).
- **Coordinators/Staff:** `view_all_documents` + `upload_staff_document` + `delete_document`. May view documents attached to cases they manage.
- **Admins:** Full access including `manage_legal_hold` and `view_quarantined`.
- **Scan status gate:** Citizens can only download documents with `scan_status=ACTIVE`. Staff with `view_quarantined` may view metadata of quarantined documents.

### 12.3 `DocumentAccessToken` Enforcement

Every download request requires a valid `DocumentAccessToken`:

1. View issues token (`GET /documents/{pk}/download/`) → 302 to token URL (`/documents/dl/{token}/`)
2. Token URL validates: not expired, not used, issued to current user, document is ACTIVE
3. On success: mark token used, write audit log, serve file
4. On failure: 404 (never 403, per IDOR rule)

---

## 13. Audit Trail

Every document-related event must call `apps.audit.services.record_event()`.

### 13.1 Mandatory Audit Events

| Event | `event_type` | `event_detail` keys | Notes |
|---|---|---|---|
| Upload confirmed (presign requested) | `data.created` | `category_slug`, `mime_type`, `size_bytes` | Never log `original_filename` |
| Scan complete — clean | `data.updated` | `scan_status: active` | |
| Scan complete — quarantined | `security.threat_detected` | `scan_status: quarantined`, `engine_result` | Never log uploader PII |
| Document downloaded | `data.viewed` | `doc_pk`, `version_number`, `token_pk` | Every download, every time |
| Version created | `data.updated` | `new_version_number`, `root_pk` | |
| Soft deleted | `data.deleted` | `deletion_reason`, `deleted_by_pk` | Never log filename |
| Hard deleted (storage cleared) | `data.purged` | `cleared_at` | Storage key nulled |
| Legal hold set | `data.updated` | `legal_hold: true`, `reason` | |
| Legal hold lifted | `data.updated` | `legal_hold: false` | |
| Access denied (non-owned PK) | `security.access_denied` | `requested_pk`, `requesting_user_pk` | |
| Retention scheduled disposal | `data.deleted` | `reason: retention_expired` | |

### 13.2 PIPEDA Constraints on Audit Data

- `original_filename` must **never** appear in any audit `event_detail` — it may contain PII (e.g. `john_smith_tax_return_2025.pdf`).
- `uploaded_by` email/name must **never** appear in logs — use `uploaded_by.pk` (integer) only.
- `storage_key` must **never** appear in audit records.
- `scan_engine_result` (contains virus name string) appears only in `document_quarantined` events, scoped to `view_quarantined` permission.

---

## 14. Signals and Events

Following the project convention (`noun_verb = Signal()`, dispatch via `.send_robust()`):

```python
# apps/documents/signals.py

document_uploaded    = Signal()  # sender=Document; kwargs: instance, uploaded_by
document_scan_clean  = Signal()  # sender=Document; kwargs: instance
document_quarantined = Signal()  # sender=Document; kwargs: instance, engine_result (never includes uploader PII)
document_downloaded  = Signal()  # sender=Document; kwargs: instance, downloaded_by, token
document_version_created = Signal()  # sender=Document; kwargs: instance (new version), previous_version
document_soft_deleted    = Signal()  # sender=Document; kwargs: instance, deleted_by
document_hard_deleted    = Signal()  # sender=Document; kwargs: instance (storage_key already nulled)
document_legal_hold_set  = Signal()  # sender=Document; kwargs: instance, set_by, hold_active
```

Receivers live in `apps/documents/receivers.py`, registered in `apps/documents/apps.py` `ready()`.

---

## 15. Notifications

Using the existing `apps.notifications.services.send_email_notification()` pattern:

| Trigger | subject_key | Recipient | Context keys |
|---|---|---|---|
| Document scan clean | `document_scan_clean` | Uploader (citizen) | `document_pk`, `category_name`, `portal_url` |
| Document quarantined | `document_quarantined_admin` | System admin(s) | `document_pk`, `category_slug` (NO uploader PII, NO filename) |
| Document expiring (7 days) | `document_expiring` | Uploader (citizen) | `document_pk`, `category_name`, `expires_at`, `portal_url` |
| Legal hold set | `document_legal_hold` | Privacy Officer | `document_pk`, `reason` |

Templates in `templates/notifications/email/document_*/` (HTML + TXT + subject variants, EN + FR).

---

## 16. Integration Points with Existing BBs

### 16.1 Portal BB (`apps.portal`)

**`ServiceRequest`** — two attachment roles via `DocumentAttachment`:

| Role | Who uploads | `attachment_role` |
|---|---|---|
| Supporting evidence submitted with request | Citizen | `"supporting_evidence"` |
| Decision letter / approval notice | Staff | `"decision_letter"` |

**Implementation:** Add `GenericRelation` to `ServiceRequest`:
```python
# portal/models.py addition
from django.contrib.contenttypes.fields import GenericRelation
class ServiceRequest(BaseModel):
    ...
    document_attachments = GenericRelation(
        "documents.DocumentAttachment",
        content_type_field="content_type",
        object_id_field="object_id",
    )
```

**`StatusUpdate`** — optional decision letter attachment via `DocumentAttachment`.

### 16.2 Workflows BB (`apps.workflows`)

**`WorkItem`** — staff attach evidence during review:
- `attachment_role = "staff_evidence"`

**`WorkItemComment`** — inline attachment alongside comment body:
- `attachment_role = "comment_attachment"`

### 16.3 Volunteers BB (`apps.volunteers`)

**`VolunteerApplication`** — supporting documents (references, ID, certifications requested during onboarding).

**`Certification`** — **direct migration** (not GenericFK):
```python
# Replace: document = FileField(upload_to="volunteers/certifications/")
# With:    document = ForeignKey("documents.Document", null=True, on_delete=PROTECT)
```
Migration 0012 adds the FK; 0013 backfills existing FileField values by creating Document records for each; 0014 drops the old FileField.

**`Honorarium`** — one-to-one for issued T4A slip:
```python
t4a_document = models.OneToOneField(
    "documents.Document",
    null=True,
    blank=True,
    on_delete=models.PROTECT,
    related_name="honorarium_t4a",
)
```

**`ScreeningRecord`** — chain-of-custody reference (not the criminal result itself):
```python
vsc_confirmation_doc = models.ForeignKey(
    "documents.Document",
    null=True,
    blank=True,
    on_delete=models.PROTECT,
    related_name="screening_vsc_confirmations",
    help_text="PIPEDA: stores reference to chain-of-custody confirmation document only. "
              "Must never reference or store the criminal check result.",
)
```

### 16.4 Payments BB (`apps.payments`)

**`OfficialDonationReceipt`** — **direct migration** from bare `pdf_path` CharField:
```python
# Replace: pdf_path = CharField(max_length=500)
# With:    document = OneToOneField("documents.Document", null=True, on_delete=PROTECT)
```
Migration: add FK; backfill by creating Document records from existing `pdf_path` values; null the old field; later migration drops it.

**`ServiceFeePayment`** — optional confirmation document:
```python
document_attachments = GenericRelation("documents.DocumentAttachment")
```

### 16.5 Consent BB (`apps.consent`)

**`DataExportRequest`** — **direct migration** from bare `storage_path` CharField:
```python
# Replace: storage_path = CharField(max_length=500)
# With:    document = OneToOneField("documents.Document", null=True, on_delete=PROTECT)
```
Category: `"pipeda-data-export"` with `is_transitory=True`, `max_retention_days=30`.

### 16.6 Reports BB (`apps.reports`)

Generated PDF reports (monthly operational summary, volunteer impact PDF):
- These are staff-generated output documents, not citizen uploads.
- Store via Document model with category `"system-generated-report"`.
- Coordinators download via `DocumentDownloadView` (with audit log), not via direct media URL.

---

## 17. Admin Interface

```python
@admin.register(DocumentCategory)
class DocumentCategoryAdmin(admin.ModelAdmin):
    list_display  = ["slug", "security_classification", "min_retention_days",
                     "max_retention_days", "is_transitory"]
    search_fields = ["slug", "name_en", "name_fr"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display  = ["pk", "category", "scan_status", "version_number",
                     "is_latest_version", "legal_hold", "created_at"]
    list_filter   = ["scan_status", "legal_hold", "category",
                     "security_classification"]
    search_fields = ["pk"]  # NO search on original_filename (PII) in list display
    readonly_fields = [
        "pk", "category", "uploaded_by", "original_filename",  # shown to admin
        "_storage_key",  # BLOCKED — see below
        "scan_status", "scan_completed_at", "scan_engine_result",
        "version_number", "root_document", "is_latest_version",
        "created_at", "updated_at",
    ]

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        # _storage_key and scan_engine_result gated by view_quarantined permission
        if not request.user.has_perm("documents.view_quarantined"):
            return [f for f in fields if f not in ("_storage_key", "scan_engine_result")]
        return fields

    # No delete action — use DocumentService.soft_delete() instead
    def has_delete_permission(self, request, obj=None):
        return False

    actions = ["action_set_legal_hold", "action_lift_legal_hold"]

    def action_set_legal_hold(self, request, queryset):
        if not request.user.has_perm("documents.manage_legal_hold"):
            self.message_user(request, "Permission denied.", level="error")
            return
        queryset.update(legal_hold=True, legal_hold_set_by=request.user)
    action_set_legal_hold.short_description = "Set legal hold"
```

---

## 18. API Contract

All endpoints under `/api/v1/documents/` (DRF, authenticated via JWT, same pattern as existing API BB).

### 18.1 Endpoints

```
POST   /api/v1/documents/request-upload/
    → Request presigned upload URL
    → Body: { category_slug, original_filename, mime_type, size_bytes }
    → 201: { doc_id, upload_url, upload_fields, expires_at }
    → 400: validation errors
    → 403: not permitted to upload to this category

POST   /api/v1/documents/{doc_id}/confirm-upload/
    → Confirm browser has uploaded to S3; triggers scan
    → 200: { doc_id, scan_status }
    → 400: file not found in quarantine, magic byte mismatch, ZIP bomb
    → 404: doc_id not found or not owned by current user

GET    /api/v1/documents/{doc_id}/
    → Document metadata (no storage_key, no signed URL)
    → 200: { doc_id, category, original_filename, mime_type, size_bytes,
              scan_status, version_number, is_latest_version, created_at }
    → 404: not found or not owned (IDOR: 404 not 403)

GET    /api/v1/documents/{doc_id}/download/
    → Issues a DocumentAccessToken; returns download URL
    → 200: { token_url, expires_at }
    → 404: not found, not owned, or not ACTIVE

GET    /api/v1/documents/dl/{token}/
    → Validates and redeems DocumentAccessToken; serves file
    → 302 (prod): redirect to fresh short-lived S3 presigned URL
    → 200 (dev): inline file stream
    → 404: invalid or expired token

GET    /api/v1/documents/?attached_to={content_type}&object_id={pk}
    → List documents attached to a given object (staff only)
    → 200: paginated list of document metadata

POST   /api/v1/documents/{doc_id}/attach/
    → Attach document to an object (staff only, upload_staff_document permission)
    → Body: { content_type, object_id, attachment_role, note }
    → 201: { attachment_id }

DELETE /api/v1/documents/{doc_id}/
    → Soft-delete (delete_document permission required)
    → Body: { reason }
    → 204: No content
    → 403: Legal hold active — cannot delete

GET    /api/v1/documents/{doc_id}/versions/
    → List all versions in chain (view_document_versions permission)
    → 200: list of version metadata
```

### 18.2 Serializers

- **`DocumentSerializer`** — read-only; never exposes `_storage_key`, `scan_engine_result` (except to `view_quarantined` users), `uploaded_by` email.
- **`DocumentUploadRequestSerializer`** — validates category_slug, mime_type allowlist, size_bytes cap.
- **`DocumentAttachmentSerializer`** — read-only list; exposes document metadata + role.

---

## 19. Celery Tasks and Beat Schedule

```python
# apps/documents/tasks.py

@shared_task(bind=True, max_retries=5, default_retry_delay=30,
             queue="documents", reject_on_worker_lost=True, acks_late=True)
def scan_document(self, doc_pk: str) -> None:
    """Virus-scan a document via ClamAV. Retry if daemon unavailable."""

@shared_task(queue="documents")
def expire_documents() -> dict:
    """Soft-delete documents past max_retention_days. Skips legal holds."""

@shared_task(queue="documents")
def hard_delete_expired_documents(grace_days: int = 30) -> dict:
    """
    Hard-delete (clear S3 + null storage_key) documents soft-deleted > grace_days ago.
    Irreversible per OPC / NIST SP 800-88.
    """

@shared_task(queue="documents")
def notify_expiring_documents(days_before: int = 7) -> dict:
    """Notify citizens whose documents expire in `days_before` days."""

@shared_task(queue="documents")
def purge_expired_access_tokens() -> int:
    """Hard-delete DocumentAccessToken rows that expired > 24 hours ago."""

@shared_task(queue="documents")
def cleanup_stale_pending_uploads(max_age_hours: int = 24) -> int:
    """
    Delete Document rows stuck in PENDING_UPLOAD state for > max_age_hours.
    These represent incomplete uploads where the browser never called confirm-upload.
    """
```

### Beat Schedule

```python
"expire-documents":              {"task": "apps.documents.tasks.expire_documents",
                                  "schedule": crontab(hour=2, minute=0)},
"hard-delete-expired-documents": {"task": "apps.documents.tasks.hard_delete_expired_documents",
                                  "schedule": crontab(hour=3, minute=0)},
"notify-expiring-documents":     {"task": "apps.documents.tasks.notify_expiring_documents",
                                  "schedule": crontab(hour=8, minute=0)},
"purge-access-tokens":           {"task": "apps.documents.tasks.purge_expired_access_tokens",
                                  "schedule": crontab(hour=1, minute=0)},
"cleanup-stale-pending-uploads": {"task": "apps.documents.tasks.cleanup_stale_pending_uploads",
                                  "schedule": crontab(hour=4, minute=0)},
```

---

## 20. Templates and WCAG 2.1 AA

### 20.1 Template Inventory

```
templates/documents/
  citizen/
    upload_form.html          ← citizen upload widget (shared partial)
    document_list.html        ← citizen view of their documents
    document_detail.html      ← single document metadata + download button
  staff/
    document_list.html        ← staff view with filtering + attach UI
    document_detail.html      ← full metadata including scan result (gated)
    attach_form.html          ← attach existing document to object
  partials/
    _document_card.html       ← reusable card (used in portal, volunteer views)
    _upload_progress.html     ← WCAG-compliant progress widget with aria-live
    _document_table.html      ← accessible table of attached documents
```

### 20.2 Accessible File Input Pattern

```html
{# WCAG 2.1 AA compliant file upload input #}
<div class="mb-3">
  <label for="id_document_upload" class="form-label">
    {% trans "Upload supporting document" %}
  </label>
  <input
    type="file"
    id="id_document_upload"
    name="document"
    class="form-control {% if form.document.errors %}is-invalid{% endif %}"
    accept=".pdf,.jpg,.jpeg,.png"
    aria-describedby="documentHelp {% if form.document.errors %}documentError{% endif %}"
    {% if form.document.errors %}aria-invalid="true"{% endif %}
  >
  <div id="documentHelp" class="form-text">
    {% blocktrans with max_mb=10 %}
      PDF, JPEG, or PNG only. Maximum {{ max_mb }} MB.
      Files are virus-scanned before becoming available.
    {% endblocktrans %}
  </div>
  {% if form.document.errors %}
  <div id="documentError" class="invalid-feedback" role="alert">
    {{ form.document.errors|join:", " }}
  </div>
  {% endif %}
</div>

{# WCAG 4.1.3: status messages via aria-live (no focus shift) #}
<div
  id="uploadStatus"
  aria-live="polite"
  aria-atomic="true"
  class="visually-hidden"
></div>
```

### 20.3 Drag-and-Drop Fallback (WCAG 2.1.1)

Drag-and-drop zone must always have a keyboard-accessible alternative (`<input type="file">`). The drop zone is an enhancement only.

### 20.4 Bilingual Requirements

All user-facing strings wrapped in `{% trans %}` / `{% blocktrans %}`. Email notification templates duplicated for `en` and `fr` subject keys. `original_filename` is user-provided and not translated; display it as-is using `|force_escape`.

---

## 21. Configuration and Settings

```python
# config/settings/base.py additions

CIVICOS = {
    ...
    # Document Management BB
    "MAX_UPLOAD_SIZE":              10 * 1024 * 1024,   # 10 MB citizen
    "MAX_STAFF_UPLOAD_SIZE":        50 * 1024 * 1024,   # 50 MB staff
    "ALLOWED_UPLOAD_MIME_TYPES": [
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/webp",
    ],
    "ALLOWED_STAFF_MIME_TYPES": [
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/webp",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # docx
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",        # xlsx
        "text/csv",
    ],
    "DOCUMENT_ACCESS_TOKEN_TTL_SECONDS":   300,    # 5 minutes
    "DOCUMENT_PRESIGNED_UPLOAD_TTL":       900,    # 15 minutes for S3 presigned POST
    "DOCUMENT_SOFT_DELETE_GRACE_DAYS":     30,     # days before hard deletion
    "DOCUMENT_EXPIRY_WARNING_DAYS":        7,      # days before notify citizen
    "CLAMAV_HOST":                         "localhost",
    "CLAMAV_PORT":                         3310,
    "CLAMAV_TIMEOUT":                      30,
    "CLAMAV_REQUIRED":                     False,  # True in production
    "DOCUMENT_STORAGE_PREFIX":             "documents",
    "DOCUMENT_QUARANTINE_PREFIX":          "documents/quarantine",
    "DOCUMENT_ACTIVE_PREFIX":              "documents/active",
    "DOCUMENT_DELETED_PREFIX":             "documents/deleted",
}
```

---

## 22. Dependencies

### New Python Packages

```
# requirements/base.txt additions
python-magic==0.4.27   # libmagic wrapper for magic-byte validation
pyclamd==0.4.1         # ClamAV clamd protocol client (preferred over django-clamd)
```

```
# requirements/production.txt (already present)
django-storages[s3]==1.14.6  # already installed; no version change needed
boto3>=1.34.0                # already installed; ensure >= 1.34 for s3v4 by default
```

### System Dependencies

```
# Docker / apt additions
clamav        # ClamAV engine
clamav-daemon # clamd daemon process
libmagic1     # shared library required by python-magic
```

### No New Django Apps

`wagtail.documents` is already installed and handles CMS documents via `cms.CustomDocument`. The new `apps.documents` is a **separate** app for transactional citizen/staff documents.

---

## 23. Migration Plan for Existing File Fields

Migrations must be in dependency order:

### Phase 1 — Add new fields (non-breaking)

```
volunteers/migrations/00XX_add_honorarium_t4a_document.py
    AddField: Honorarium.t4a_document → FK to documents.Document (null=True)

volunteers/migrations/00XX_add_certification_document_fk.py
    AddField: Certification.document_v2 → FK to documents.Document (null=True)

volunteers/migrations/00XX_add_screening_vsc_confirmation_doc.py
    AddField: ScreeningRecord.vsc_confirmation_doc → FK to documents.Document (null=True)

payments/migrations/00XX_add_donation_receipt_document_fk.py
    AddField: OfficialDonationReceipt.document → FK to documents.Document (null=True)

consent/migrations/00XX_add_data_export_request_document_fk.py
    AddField: DataExportRequest.document → FK to documents.Document (null=True)
```

### Phase 2 — Data migration (management command)

```
python manage.py migrate_existing_files
```

This management command:
1. Iterates each model with existing file data
2. Creates `DocumentCategory` records if absent
3. Creates `Document` records for each existing file (status=ACTIVE, skip virus scan for existing)
4. Writes the FK on the parent model
5. Writes an audit log entry for each migration

### Phase 3 — Make FKs non-null; drop old fields

```
volunteers/migrations/00XX_drop_certification_document_filefield.py
    RemoveField: Certification.document (old FileField)

payments/migrations/00XX_drop_donation_receipt_pdf_path.py
    RemoveField: OfficialDonationReceipt.pdf_path

consent/migrations/00XX_drop_data_export_request_storage_path.py
    RemoveField: DataExportRequest.storage_path
```

---

## 24. Test Strategy

### 24.1 Test File Inventory

```
apps/documents/tests/
  test_models.py                 # DocumentCategory constraints, Document.clean(),
                                 # QuerySet methods, __str__ PII safety
  test_services_upload.py        # Upload pipeline: presign, confirm, validation layers,
                                 # magic-byte rejection, ZIP bomb detection
  test_services_versioning.py    # create_new_version: atomic swap, version chain
  test_services_retention.py     # expire_documents, hard_delete, legal_hold exemption,
                                 # transitory disposal, retain_until enforcement
  test_tasks.py                  # scan_document: clean path, quarantine path,
                                 # ClamAV unavailable → retry, idempotency
  test_views_citizen.py          # Upload flow, download via token, IDOR (404 not 403),
                                 # scan_status gate (ACTIVE only)
  test_views_staff.py            # Staff upload, attach to objects, version list
  test_api.py                    # All API endpoints: auth, validation, error envelopes
  test_access_tokens.py          # Token TTL, single-use, expiry, cross-user isolation
  test_audit.py                  # Every event writes correct audit record;
                                 # no PII in event_detail
  test_signals.py                # All 8 signals fire with correct kwargs
  test_admin.py                  # storage_key/scan_engine_result gated by view_quarantined;
                                 # delete blocked; legal hold actions
  test_pipeda.py                 # Key invariants:
                                 #   1. storage_key never appears in any serializer
                                 #   2. original_filename never appears in audit detail
                                 #   3. Quarantine notification has no uploader PII
                                 #   4. IDOR: non-owned doc returns 404 not 403
                                 #   5. scan_status gate: PENDING/SCANNING/QUARANTINED → 404
                                 #   6. Legal hold blocks disposal
                                 #   7. Transitory doc disposed after mark_purpose_fulfilled
  test_wcag.py                   # Template rendering: aria-describedby present,
                                 # aria-invalid on error, aria-live on status region
  test_integration.py            # Full upload → scan → download flow (TransactionTestCase)
```

### 24.2 Critical Security Invariants (must never regress)

```python
# Always assert in test_pipeda.py:

def test_storage_key_never_in_api_response(self):
    """storage_key must never appear in any API response."""
    response = self.client.get(f"/api/v1/documents/{self.doc.pk}/")
    self.assertNotIn("storage_key", response.data)
    self.assertNotIn("_storage_key", response.data)
    self.assertNotIn(self.doc.storage_key, response.content.decode())

def test_original_filename_not_in_audit_detail(self):
    """original_filename must never appear in audit event_detail (may contain PII)."""
    # Download the document, triggering an audit entry
    ...
    entry = AuditLogEntry.objects.filter(resource_type="documents.Document").last()
    self.assertNotIn("original_filename", entry.event_detail)
    self.assertNotIn(self.doc.original_filename, str(entry.event_detail))

def test_idor_returns_404_not_403(self):
    """Non-owned document returns 404, not 403 (403 confirms existence)."""
    other_doc = _make_doc(user=self.other_user)
    response = self.client.get(f"/api/v1/documents/{other_doc.pk}/")
    self.assertEqual(response.status_code, 404)

def test_quarantine_notification_has_no_uploader_pii(self):
    """Admin notification for quarantined document must not include uploader identity."""
    ...
    ctx = mock_send.call_args.kwargs["context"]
    self.assertNotIn("uploader_email", ctx)
    self.assertNotIn("uploader_name", ctx)
    self.assertNotIn("original_filename", ctx)

def test_legal_hold_blocks_disposal(self):
    """A document on legal hold must not be soft-deleted by the retention task."""
    doc = _make_doc(legal_hold=True, expires_at=timezone.now() - timedelta(days=1))
    expire_documents()
    doc.refresh_from_db()
    self.assertIsNone(doc.deleted_at)

def test_scan_pending_blocks_download(self):
    """Citizens cannot download documents not yet scanned clean."""
    doc = _make_doc(scan_status=Document.ScanStatus.SCANNING)
    response = self.citizen_client.get(f"/api/v1/documents/{doc.pk}/download/")
    self.assertEqual(response.status_code, 404)
```

---

## 25. Implementation Waves

### Wave 1 — Core scaffold + model + service layer
- `apps/documents/` app scaffold: `apps.py`, `models.py`, `admin.py`, `signals.py`, `services/`
- `DocumentCategory`, `Document`, `DocumentAttachment`, `DocumentAccessToken` models
- Initial migration `0001_initial`
- `DocumentQuerySet` methods
- `DocumentService` skeleton (stubs for all service functions)
- Settings additions in `base.py`
- Wire `INSTALLED_APPS`

### Wave 2 — Upload pipeline + validation + virus scan
- `services/upload.py`: presign, confirm, magic-byte validation, ZIP bomb detection
- `tasks.py`: `scan_document`, `cleanup_stale_pending_uploads`
- ClamAV integration (`pyclamd`)
- `python-magic` integration
- Dev bypass (`CLAMAV_REQUIRED=False`)
- Celery queue routing for `documents` queue

### Wave 3 — Download, access tokens, versioning
- `services/download.py`: token issuance, redemption, proxy/redirect logic
- `services/versioning.py`: `create_new_version`, atomic `is_latest_version` swap
- `tasks.py`: `purge_expired_access_tokens`
- Audit log on every download

### Wave 4 — Retention and disposal
- `services/retention.py`: `expire_documents`, `hard_delete_expired_documents`, `mark_purpose_fulfilled`
- `tasks.py`: `expire_documents`, `hard_delete_expired_documents`, `notify_expiring_documents`
- Beat schedule wiring
- Legal hold management

### Wave 5 — Views, API, and templates
- Citizen views: upload, list, detail, download
- Staff views: list, detail, attach, version history
- DRF API endpoints
- All templates (WCAG 2.1 AA)
- Bilingual notification email templates

### Wave 6 — Integration migrations
- Migrations to add document FKs to existing models (`Certification`, `Honorarium`, `OfficialDonationReceipt`, `DataExportRequest`, `ScreeningRecord`)
- Management command `migrate_existing_files`
- `GenericRelation` additions to `ServiceRequest`, `WorkItem`, `WorkItemComment`

### Wave 7 — Test suite + adversarial review + commit
- Full test suite (all test files in §24.1)
- Adversarial review pass
- Fix findings
- Tag `v0.8.0`

### Wave 8 — DRF REST API (added 2026-07-23, closes DOC-GAPs 1–5)
- `apps/api/documents/` module: `serializers.py`, `views.py`, `urls.py`
- 9 view classes covering all 8 spec §18 endpoints
  - `DocumentRequestUploadView` — POST /api/v1/documents/request-upload/
  - `DocumentConfirmUploadView` — POST /api/v1/documents/{doc_id}/confirm-upload/
  - `DocumentDetailDeleteView` — GET + DELETE /api/v1/documents/{doc_id}/
  - `DocumentDownloadInitView` — GET /api/v1/documents/{doc_id}/download/
  - `DocumentTokenRedeemView` — GET /api/v1/documents/dl/{token}/
  - `DocumentAttachedListView` — GET /api/v1/documents/?attached_to=…
  - `DocumentAttachView` — POST /api/v1/documents/{doc_id}/attach/
  - `DocumentVersionsView` — GET /api/v1/documents/{doc_id}/versions/
- 3 serializers: `DocumentSerializer`, `DocumentUploadRequestSerializer`, `DocumentAttachmentSerializer`
- Wired into `apps/api/urls.py` under `documents/`
- Renamed old `test_api.py` → `test_views_http_contract.py` (HTML view tests)
- New `apps/documents/tests/test_api.py` — 62 DRF API tests covering all 9 view classes
- Fixed `apps/documents/tests/test_pipeda.py` §24.2 PIPEDA invariants (3 tests updated to use APIClient)
- Security: `storage_key` never in response; `scan_engine_result` gated; IDOR → 404; legal hold → 403; atomic single-use tokens
- **All 5 DOC-GAPs resolved. Document Management BB is now complete and certifiably production-ready.**

---

## 26. Open Questions / Deferred Items

| # | Item | Decision needed | Deferred to |
|---|---|---|---|
| 1 | **PDF CDR (Content Disarm and Reconstruct)** — strip embedded JS, external links, macros from PDFs via Ghostscript or `pikepdf`. OWASP recommends this for PDFs. | High-value, but adds Ghostscript system dep and ~1-2s per PDF. Recommend enabling in v1.1 once Ghostscript is confirmed on prod infra. | v1.1 |
| 2 | **Files > 10 MB for citizens** — some use-cases (e.g. large scanned documents) may exceed 10 MB. | Raise to 20 MB? Require out-of-band submission? | Privacy Officer decision |
| 3 | **Encrypted zip / password-protected PDF** — ClamAV cannot scan inside these; they must be rejected or flagged. | Reject outright (safest) or flag for manual staff review? | v1.1 |
| 4 | **Officer e-signing** — decision letters require a staff signature workflow. | Deferred to dedicated Signatures BB. | Future BB |
| 5 | **Full-text search / OCR** — index document contents for staff search. | Deferred to Search BB (Elasticsearch/OpenSearch). | Future BB |
| 6 | **VolunteerProfile.photo** — currently an `ImageField` with consent gating. Should it be migrated into the Document BB? | Recommend keeping as ImageField (different use-case: profile photo ≠ supporting document) but adding audit log on access. | v0.8.1 |
| 7 | **Multi-file upload** — allow citizens to upload multiple files in one request. | Wave 5 UI supports single file per request; batching is a UX enhancement. | v0.8.1 |
| 8 | **Virus definition update schedule** — `freshclam` must run on the ClamAV server; how often? | Standard is daily freshclam updates. Needs DevOps confirmation. | DevOps |
| 9 | **Protected C documents** — out of scope for this version. If needed, requires separate infrastructure, Enhanced Reliability screening, and EARB approval. | Cabinet-level decision. | Future |
| 10 | **Tenant isolation** — current CivicOS is single-tenant. If multi-tenancy is added, document visibility must be scoped per-tenant. | Not applicable now; design uses `uploaded_by` scoping which is tenant-safe if tenant FK is added to User. | Multi-tenancy BB |

---

*End of specification — Document Management BB v1.0*
