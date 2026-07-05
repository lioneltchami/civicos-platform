"""
Document Management Building Block — Upload service.

Handles the multi-step upload pipeline:
  1. validate_upload_request() — validate intent, create Document in PENDING_UPLOAD state,
                                  return presigned POST data for direct browser→S3 upload.
  2. confirm_upload()          — verify file exists at quarantine key, run magic-byte and
                                  ZIP-bomb validation, advance to SCANNING, dispatch ClamAV task.

Defence-in-depth per OWASP File Upload Cheat Sheet (all 8 layers):
  Layer 1: Category permission check (_user_may_upload_to_category)
  Layer 2: Size check (category.max_size_bytes OR global DOCUMENT_MAX_*_UPLOAD_BYTES)
  Layer 3: Extension allowlist (not blocklist — _ALLOWED_EXTENSIONS)
  Layer 4: MIME type allowlist (client Content-Type — untrusted, secondary check only)
  Layer 5: Magic-byte validation (python-magic / libmagic) — in confirm_upload()
  Layer 6: ZIP bomb detection (CVE-2024-0450) — in confirm_upload(), docx/xlsx only
  Layer 7: Storage key randomisation (UUID-based, never filename-derived)
  Layer 8: ClamAV virus scan — async Celery task dispatched from confirm_upload()

PIPEDA constraints (must be preserved by ALL callers):
  - original_filename is stored in DB but NEVER used as the storage path.
  - original_filename is NEVER written to audit event_detail (may contain PII).
  - storage_key is NEVER returned to clients. Only doc_id (UUID) is returned.
  - Audit log entries use only doc.pk, category_slug, mime_type, size_bytes.
  - Citizens receive Http404 (not 403) for non-owned document PKs (IDOR prevention).
  - select_for_update() MUST be called inside atomic() before any status check.

Governing law: PIPEDA clause 4.7.5, OWASP File Upload Cheat Sheet, CVE-2024-0450.
"""

from __future__ import annotations

import datetime
import io
import logging
import os
import uuid
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    from django.contrib.auth import get_user_model

    from apps.documents.models import Document, DocumentCategory

    User = get_user_model()

logger = logging.getLogger(__name__)

# python-magic (libmagic) — optional in dev, required in production.
# Imported at module level so tests can patch `apps.documents.services.upload.magic`.
# If not installed, magic is None; _validate_magic_bytes() handles this gracefully.
try:
    import magic
except ImportError:
    magic = None  # type: ignore[assignment]

# ── Extension allowlist ───────────────────────────────────────────────────────
# Only file extensions in this set are accepted, regardless of any MIME claim.
# This is an allowlist — anything not here is rejected.
# Note: "txt" is permitted for plain-text supporting evidence.
_ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {
        "pdf",
        "jpg",
        "jpeg",
        "png",
        "webp",
        "docx",
        "xlsx",
        "csv",
        "txt",
    }
)

# Extensions that trigger ZIP-bomb validation (CVE-2024-0450).
# .docx and .xlsx are ZIP containers — both entry count and compression ratio
# must be checked. Bare .zip is NOT in _ALLOWED_EXTENSIONS and will be rejected
# at the extension-allowlist stage before reaching this check.
_ZIP_FAMILY: frozenset[str] = frozenset({"docx", "xlsx"})

# Valid storage key prefixes. Any other value is rejected by _make_storage_key()
# to prevent path-injection attacks.
_VALID_PREFIXES: frozenset[str] = frozenset({"quarantine", "active", "deleted"})

# MIME types that are always acceptable for ZIP-family validation
# (detected by python-magic for .docx / .xlsx files).
_ZIP_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/zip",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-office",
        "application/octet-stream",  # python-magic fallback for some zip variants
    }
)

# Number of bytes read from storage for magic-byte identification.
_MAGIC_BYTE_READ_LENGTH: int = 8192

# Known S3 storage backend class paths (django-storages).
# M-5: Compare against an explicit set rather than fragile substring matching.
# "s3" in backend.lower() would match any backend with "s3" in its path,
# including future custom backends that happen to include "s3" as a substring
# without actually being S3. Use exact class paths instead.
_S3_BACKEND_PATHS: frozenset[str] = frozenset(
    {
        "storages.backends.s3boto3.S3Boto3Storage",  # django-storages >= 1.13
        "storages.backends.s3.S3Storage",            # django-storages <= 1.12 (legacy)
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Public service functions
# ─────────────────────────────────────────────────────────────────────────────


def validate_upload_request(
    *,
    user: "User",
    category_slug: str,
    original_filename: str,
    mime_type: str,
    size_bytes: int,
) -> dict:
    """
    Validate an upload intent and generate a presigned S3 POST URL.

    Called by the API view before any file bytes are transferred.
    Creates a Document record in PENDING_UPLOAD state.

    OWASP Layers 1–4 are applied here (category, size, extension, MIME header).
    OWASP Layers 5–8 are applied in confirm_upload() after the file is uploaded.

    Args:
        user:              The authenticated user performing the upload.
        category_slug:     DocumentCategory slug (e.g. 'service-request-evidence').
        original_filename: Client-supplied filename (stored in DB, NEVER used as path).
        mime_type:         Client-supplied MIME type (validated against allowlist).
        size_bytes:        Client-supplied file size (validated against cap).

    Returns:
        {
            "doc_id":        str  — UUID of the created Document record,
            "upload_url":    str  — presigned POST URL (S3) or direct upload URL (dev),
            "upload_fields": dict — fields to include in the multipart POST,
            "expires_at":    str  — ISO-8601 timestamp when the presigned URL expires,
        }
        PIPEDA: storage_key is NEVER in the returned dict.

    Raises:
        DocumentCategory.DoesNotExist: category_slug not found (caller converts to 404).
        PermissionDenied:              User is not allowed to upload to this category.
        ValidationError:               File fails size, extension, or MIME type validation.
    """
    from apps.documents.models import Document, DocumentCategory
    from apps.documents.services.retention import schedule_expiry
    from apps.documents.signals import document_upload_initiated

    # ── Layer 1: Category fetch + permission check ─────────────────────────────
    # Raises DocumentCategory.DoesNotExist → caller (view) converts to 404.
    category = DocumentCategory.objects.get(slug=category_slug)

    if not _user_may_upload_to_category(user, category):
        # PIPEDA: do NOT reveal why (could disclose category existence to unauthorised user)
        raise PermissionDenied

    # ── Layer 2: Size validation ───────────────────────────────────────────────
    if size_bytes <= 0:
        raise ValidationError(
            _("File size must be greater than zero.")
        )

    # Category-level override → global staff/citizen cap → hardcoded fallback.
    if category.max_size_bytes > 0:
        max_size = category.max_size_bytes
    elif _user_is_staff_uploader(user):
        max_size = _civicos().get("DOCUMENT_MAX_STAFF_UPLOAD_BYTES", 50 * 1024 * 1024)
    else:
        max_size = _civicos().get("DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES", 10 * 1024 * 1024)

    if size_bytes > max_size:
        raise ValidationError(
            _("File exceeds the maximum allowed size of %(max)s bytes.")
            % {"max": max_size}
        )

    # ── Layer 3: Extension allowlist ──────────────────────────────────────────
    # Use pathlib to extract the extension; strip the leading dot; lowercase.
    # A missing extension (e.g. filename "Makefile") also fails.
    raw_suffix = Path(original_filename).suffix  # includes "." e.g. ".pdf"
    ext = raw_suffix.lstrip(".").lower()
    if not ext or ext not in _ALLOWED_EXTENSIONS:
        raise ValidationError(
            _("File type '.%(ext)s' is not permitted.")
            % {"ext": ext or "(none)"}
        )

    # ── Layer 4: MIME type header check (untrusted — secondary) ───────────────
    # The authoritative check is Layer 5 (magic bytes) in confirm_upload().
    # This check filters obvious mismatches and bad actors early.
    allowed_mimes: list[str] = (
        category.allowed_mime_types
        if category.allowed_mime_types
        else _civicos().get("ALLOWED_UPLOAD_MIME_TYPES", [])
    )
    if not allowed_mimes:
        # Misconfiguration: an empty MIME list would silently reject every upload
        # for this category. Fail loudly so operators fix configuration rather
        # than wonder why no uploads succeed.
        raise ImproperlyConfigured(
            f"DocumentCategory '{category.slug}' has no allowed_mime_types and "
            "CIVICOS['ALLOWED_UPLOAD_MIME_TYPES'] is empty. Configure at least one "
            "permitted MIME type before accepting uploads for this category."
        )
    if mime_type not in allowed_mimes:
        raise ValidationError(
            _("Content type '%(mime)s' is not permitted for this document category.")
            % {"mime": mime_type}
        )

    # ── Layer 7: Generate randomised storage key ───────────────────────────────
    # Use a freshly generated UUID as both the Document PK and part of the key.
    # The storage key is completely independent of original_filename.
    doc_uuid = uuid.uuid4()
    storage_key = _make_storage_key(str(doc_uuid), prefix="quarantine")

    # ── Create Document + schedule retention dates (atomic) ───────────────────
    # Document.create() and schedule_expiry() must commit atomically.
    # A committed Document row without retention dates permanently escapes the
    # disposal schedule — a Privacy Act s.6(1) / PIPEDA data-minimisation
    # violation. The atomic block makes both succeed or both roll back.
    with transaction.atomic():
        doc = Document.objects.create(
            id=doc_uuid,
            category=category,
            uploaded_by=user,
            original_filename=original_filename,   # stored in DB; NEVER used as path
            _storage_key=storage_key,               # NEVER returned to clients
            mime_type=mime_type,
            size_bytes=size_bytes,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
            security_classification=category.security_classification,
            # version fields default: version_number=1, is_latest_version=True
        )
        schedule_expiry(document=doc)

    # ── Generate presigned POST (outside transaction — no network inside txn) ──
    # Network calls (boto3 S3) must not run inside a DB transaction: they can
    # be slow, and transaction hold time directly affects DB concurrency.
    # If the S3 call fails, the committed Document row is treated as an abandoned
    # PENDING_UPLOAD and purged by cleanup_stale_pending_uploads().
    # Best-effort immediate cleanup prevents accumulating zombie rows.

    # Build absolute confirm URL for S3 success_action_redirect.
    # SITE_URL must be set in production (e.g. "https://portal.example.gov.ca").
    # In dev/test it is typically empty; the redirect is skipped and confirm_upload()
    # must be called directly by the client (dev upload flow).
    _base_url = getattr(settings, "SITE_URL", "").rstrip("/")
    if not _base_url:
        logger.warning(
            "validate_upload_request: SITE_URL is not configured — "
            "success_action_redirect will NOT be injected into the S3 presigned POST. "
            "Set SITE_URL in production settings so the browser is redirected to the "
            "confirm view after upload (otherwise documents stay in PENDING_UPLOAD)."
        )
    _confirm_path = reverse("documents:upload-confirm", args=[str(doc.pk)])
    _confirm_url: str | None = f"{_base_url}{_confirm_path}" if _base_url else None

    try:
        presigned = _generate_presigned_post(
            doc=doc,
            category=category,
            max_size=max_size,
            success_redirect_url=_confirm_url,
        )
    except Exception:
        try:
            doc.delete()
        except Exception:  # noqa: BLE001
            logger.warning(
                "validate_upload_request: could not delete doc pk=%s after "
                "presigned POST failure; stale cleanup will handle it.",
                doc.pk,
            )
        raise

    # ── Fire signal ───────────────────────────────────────────────────────────
    # PIPEDA: kwargs contain only doc.pk, category.slug, user.pk (no email, no filename).
    # M-9: User.pk is BigAutoField (int). Cast explicitly to int so the type
    # contract in signals.py ("uploaded_by_id (int)") is always satisfied,
    # even if the User model is swapped for one with a UUID PK in the future —
    # in that case the int() cast will raise TypeError early, forcing an
    # explicit update of the signal contract rather than silent type mismatch.
    document_upload_initiated.send_robust(
        sender=Document,
        document_pk=str(doc.pk),
        category_slug=category.slug,
        uploaded_by_id=int(user.pk),
    )

    # PIPEDA: storage_key NEVER in the returned dict.
    return {
        "doc_id": str(doc.pk),
        "upload_url": presigned["url"],
        "upload_fields": presigned["fields"],
        "expires_at": presigned["expires_at"],
    }


def confirm_upload(
    *,
    user: "User",
    doc_id: str,
) -> "Document":
    """
    Confirm that a browser upload completed and dispatch the ClamAV scan task.

    Called by the API view after the browser has POSTed to S3 (or directly to
    Django in dev). Verifies the file exists at the quarantine key, runs
    magic-byte validation (Layer 5), ZIP-bomb detection (Layer 6), advances
    scan_status to SCANNING, and dispatches the scan_document Celery task.

    SECURITY INVARIANTS:
      - select_for_update() is called INSIDE atomic() before any status check.
      - IDOR: Http404 (not 403) if user is not the uploader. 403 confirms existence.
      - Idempotent: if already past PENDING_UPLOAD (e.g. double-submit), returns early.

    Args:
        user:   The authenticated user (MUST be the uploader).
        doc_id: UUID of the Document to confirm.

    Returns:
        The updated Document instance (scan_status=SCANNING).

    Raises:
        Http404:         doc_id not found OR user is not the uploader (IDOR: 404 not 403).
        ValidationError: File not found at quarantine key, magic-byte mismatch,
                         or ZIP-bomb detection triggered.
    """
    from apps.audit.models import AuditEventType
    from apps.audit.services import record_event
    from apps.documents.models import Document
    from apps.documents.signals import document_confirmed
    from apps.documents.tasks import scan_document

    # ── M-2: UUID format validation ────────────────────────────────────────────
    # A malformed doc_id (e.g. "abc", "'; DROP TABLE --") causes the UUID field
    # parser inside .get(pk=doc_id) to raise ValueError — an unhandled 500.
    # Catch it here and return 404, consistent with the IDOR prevention rule
    # (we never confirm whether a document with a given ID exists to the caller).
    try:
        uuid.UUID(doc_id)
    except (ValueError, AttributeError):
        raise Http404

    # ── select_for_update inside atomic — required before any status check ─────
    with transaction.atomic():
        try:
            doc = Document.objects.select_for_update().select_related("category").get(pk=doc_id)
        except Document.DoesNotExist:
            # IDOR prevention: 404 even for "not found" case
            raise Http404

        # ── IDOR guard: 404 not 403 ────────────────────────────────────────────
        if doc.uploaded_by_id != user.pk:
            # 403 would confirm the document exists with this ID. Use 404.
            raise Http404

        # ── Idempotency guard ──────────────────────────────────────────────────
        # If the client double-submits while the scan is running or already done,
        # return the current doc silently (safe: scan already dispatched).
        # If the document is QUARANTINED or DELETED it is permanently unavailable —
        # returning it silently would hide a security event from the caller.
        if doc.scan_status in (
            Document.ScanStatus.SCANNING,
            Document.ScanStatus.ACTIVE,
        ):
            return doc
        elif doc.scan_status in (
            Document.ScanStatus.QUARANTINED,
            Document.ScanStatus.DELETED,
        ):
            raise ValidationError(
                _("Document is not available for upload confirmation.")
            )
        # PENDING_UPLOAD: fall through to validation

        # ── Layer 4b: Verify file exists at quarantine storage key ────────────
        try:
            _verify_file_exists(doc.storage_key)
        except ValidationError:
            raise

        # ── Layer 5: Magic-byte validation ─────────────────────────────────────
        # Read the first N bytes from storage and run libmagic to detect real type.
        first_bytes = _read_first_bytes(doc.storage_key, length=_MAGIC_BYTE_READ_LENGTH)

        allowed_mimes: list[str] = (
            doc.category.allowed_mime_types
            if doc.category.allowed_mime_types
            else _civicos().get("ALLOWED_UPLOAD_MIME_TYPES", [])
        )
        if not allowed_mimes:
            # Misconfiguration guard: same check as in validate_upload_request.
            # Defends against a category being misconfigured between validate and
            # confirm (e.g. admin changed allowed_mime_types to [] after validation).
            raise ImproperlyConfigured(
                f"DocumentCategory for document pk={doc.pk} has no allowed_mime_types "
                "and CIVICOS['ALLOWED_UPLOAD_MIME_TYPES'] is empty."
            )
        # M-3: _validate_magic_bytes() now returns the authoritative MIME type
        # detected by libmagic (or None if the check was bypassed in dev).
        # Overwrite doc.mime_type with the verified value so the model stores
        # the magic-detected result, not the unverified client-supplied value.
        detected_mime: str | None = _validate_magic_bytes(
            first_bytes=first_bytes, allowed_mimes=allowed_mimes
        )
        if detected_mime is not None:
            # Authoritative: replace the client-supplied MIME stored at
            # validate_upload_request() time with the libmagic-detected value.
            doc.mime_type = detected_mime

        # ── Layer 6: ZIP bomb detection (CVE-2024-0450) ────────────────────────
        # The ZIP central directory is located at the END of the archive — the
        # 8 KB first_bytes used for magic detection is insufficient for any
        # real-world .docx or .xlsx file. Read the full file so zipfile can
        # locate the EOCD record and parse the central directory.
        ext = Path(doc.original_filename).suffix.lstrip(".").lower()
        if ext in _ZIP_FAMILY:
            zip_bytes = _read_full_file(doc.storage_key)
            _check_zip_bomb(zip_bytes)

        # ── Advance scan status ────────────────────────────────────────────────
        doc.scan_status = Document.ScanStatus.SCANNING
        # Always include mime_type in update_fields: if detected_mime is not None
        # it carries the authoritative value; if None (magic bypassed) Django
        # writes the existing client-supplied value unchanged — no regression.
        doc.save(update_fields=["scan_status", "mime_type", "updated_at"])

        # ── Audit INSIDE atomic — PIPEDA 4.5.3 requires atomicity ────────────────
        # record_event() MUST be inside atomic() so a failed audit write rolls
        # back the scan_status change. If we cannot audit the state change, we
        # must not commit it (PIPEDA 4.5.3: accountability requires auditability).
        # PIPEDA constraints on event_detail:
        #   - NO original_filename (may contain PII)
        #   - NO storage_key (internal S3 path)
        #   - NO uploader email or name
        try:
            record_event(
                event_type=AuditEventType.RECORD_CREATED,
                actor_id=str(user.pk),
                # actor_email intentionally omitted — see PIPEDA note above
                resource_type="documents.Document",
                resource_id=str(doc.pk),
                event_detail={
                    "category_slug": doc.category.slug,
                    "mime_type": doc.mime_type,
                    "size_bytes": doc.size_bytes,
                    # original_filename deliberately excluded (PIPEDA)
                },
            )
        except Exception:
            logger.exception(
                "confirm_upload: audit write failed for doc pk=%s; "
                "rolling back scan_status change (PIPEDA 4.5.3).",
                doc.pk,
            )
            # Re-raise so the atomic block rolls back — if we can't audit the
            # state change, we must not commit it (PIPEDA accountability invariant).
            raise

        # ── Dispatch ClamAV scan task on_commit ───────────────────────────────
        # on_commit ensures the DB row is flushed before the worker picks up the task.
        # The closure captures doc.pk by value so the lambda is safe after the
        # atomic block exits.
        _doc_pk_str = str(doc.pk)
        transaction.on_commit(
            lambda: scan_document.apply_async(args=[_doc_pk_str], countdown=2)
        )

    # ── Fire signal ───────────────────────────────────────────────────────────
    # Receivers may do lightweight work (e.g. write a notification row).
    # send_robust() ensures one bad receiver never breaks the pipeline.
    document_confirmed.send_robust(
        sender=Document,
        document_pk=str(doc.pk),
        size_bytes=doc.size_bytes,
        mime_type=doc.mime_type,
    )

    return doc


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_storage_key(doc_uuid: str, prefix: str = "quarantine") -> str:
    """
    Generate a randomised, UUID-based storage path.

    The key is completely independent of the user-supplied filename.
    OWASP: 'Creating a random string as a filename, such as generating a
    UUID/GUID, is essential.'

    Format: "documents/{prefix}/{doc_uuid}/{file_uuid}.bin"
    Prefixes: quarantine (pre-scan), active (clean), deleted (soft-deleted).

    Args:
        doc_uuid: The Document UUID (str).
        prefix:   Storage prefix — MUST be one of 'quarantine', 'active', or
                  'deleted'. Any other value raises ValueError (path-injection
                  guard: an arbitrary prefix could escape the expected namespace).

    Returns:
        A storage key string safe to pass to the storage backend.

    Raises:
        ValueError: prefix is not one of the allowed values.

    Example:
        "documents/active/3f2504e0-4f89-11d3-9a0c-0305e82c3301/a1b2c3d4.bin"
    """
    if prefix not in _VALID_PREFIXES:
        raise ValueError(
            f"Invalid storage prefix {prefix!r}. "
            f"Must be one of: {sorted(_VALID_PREFIXES)}"
        )
    file_uuid = uuid.uuid4().hex
    return f"documents/{prefix}/{doc_uuid}/{file_uuid}.bin"


def _user_may_upload_to_category(
    user: "User",
    category: "DocumentCategory",
) -> bool:
    """
    Return True if user is allowed to upload documents in this category.

    Permission hierarchy:
      1. Anonymous users: never permitted.
      2. Superusers: always permitted (all categories).
      3. Staff with documents.upload_staff_document: permitted to all categories
         (both citizen-accessible and staff-only).
      4. staff_only categories: block everyone not covered by rules 2–3.
      5. Staff or citizens with documents.upload_document: permitted to
         non-staff-only categories.
      6. Any authenticated user: permitted to non-staff-only categories.

    The staff_only check (rule 4) must come AFTER the superuser and
    upload_staff_document checks (rules 2–3) so that privileged staff are
    never accidentally blocked by the flag.
    """
    if not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    # Staff with broad document upload permission — may upload anywhere,
    # including staff-only categories.
    if user.has_perm("documents.upload_staff_document"):
        return True

    # Staff-only categories are blocked for everyone not covered above.
    if category.staff_only:
        return False

    # Non-staff-only category: any authenticated user with basic permission may upload.
    if user.has_perm("documents.upload_document"):
        return True

    # Authenticated citizen (no explicit permission required for public categories).
    return True


def _user_is_staff_uploader(user: "User") -> bool:
    """
    Return True if this user qualifies for the staff upload size limit.

    Staff users get a higher per-file cap (50 MB default vs 10 MB for citizens).
    """
    return (
        user.is_staff
        or user.is_superuser
        or user.has_perm("documents.upload_staff_document")
    )


def _civicos() -> dict:
    """Return the CIVICOS settings dict."""
    return getattr(settings, "CIVICOS", {})


def _get_bucket_name(storage_opts: dict) -> str:
    """
    Resolve the S3 bucket name from storage options or the legacy fallback setting.

    M-8: Direct access to ``settings.AWS_STORAGE_BUCKET_NAME`` raises
    ``AttributeError`` when the attribute is absent. Use ``getattr()`` and
    raise ``ImproperlyConfigured`` with a clear message when neither source
    provides a bucket name, rather than crashing with an opaque AttributeError
    deep inside an S3 helper.

    Args:
        storage_opts: The ``STORAGES['default']['OPTIONS']`` dict.

    Returns:
        The bucket name string.

    Raises:
        ImproperlyConfigured: Neither ``storage_opts['bucket_name']`` nor
            ``settings.AWS_STORAGE_BUCKET_NAME`` is set.
    """
    bucket_name: str | None = storage_opts.get("bucket_name") or getattr(
        settings, "AWS_STORAGE_BUCKET_NAME", None
    )
    if not bucket_name:
        raise ImproperlyConfigured(
            "S3 bucket name is not configured. Set either "
            "STORAGES['default']['OPTIONS']['bucket_name'] or "
            "AWS_STORAGE_BUCKET_NAME in your Django settings."
        )
    return bucket_name


def _is_s3_storage() -> bool:
    """
    Return True if the default storage backend is an S3 backend.

    M-5: Uses an explicit allowlist of known S3 backend class paths rather
    than substring matching ("s3" in backend.lower()). Substring matching
    is fragile — it matches any backend whose module path happens to contain
    "s3" (e.g. a custom backend named "my_s3_compat_layer.storage.Backend"
    would incorrectly match). Exact-set comparison is unambiguous.
    """
    backend = settings.STORAGES.get("default", {}).get("BACKEND", "")
    return backend in _S3_BACKEND_PATHS


def _generate_presigned_post(
    *,
    doc: "Document",
    category: "DocumentCategory",
    max_size: int,
    success_redirect_url: str | None = None,
) -> dict:
    """
    Generate a presigned POST URL for direct browser-to-S3 upload.

    In production (S3 backend): uses boto3 to generate a presigned POST.
    In dev (filesystem): returns a placeholder that the dev upload view handles.

    Args:
        doc:                  The Document record (for storage_key and mime_type).
        category:             The DocumentCategory (for allowed_mime_types).
        max_size:             Caller-resolved size cap in bytes — already accounts for
                              per-user (citizen vs staff) and category-level overrides.
                              Passed explicitly so the S3 presigned POST enforces the
                              SAME limit that Django already validated.
        success_redirect_url: Absolute URL S3 should redirect the browser to after a
                              successful upload (success_action_redirect field). If None,
                              the redirect is omitted (acceptable in dev/test).

    Returns:
        {
            "url":        str   — POST endpoint URL,
            "fields":     dict  — form fields to include in the multipart POST,
            "expires_at": str   — ISO-8601 expiry timestamp,
        }

    PIPEDA: The returned dict NEVER contains the storage_key — only
    the presigned URL and its fields.
    """
    civicos = _civicos()
    ttl_seconds: int = civicos.get("DOCUMENT_PRESIGNED_POST_TTL_SECONDS", 900)
    expires_at = timezone.now() + datetime.timedelta(seconds=ttl_seconds)
    expires_at_str = expires_at.isoformat()

    if _is_s3_storage():
        return _generate_s3_presigned_post(
            doc=doc,
            category=category,
            ttl_seconds=ttl_seconds,
            expires_at_str=expires_at_str,
            max_size=max_size,
            success_redirect_url=success_redirect_url,
        )
    else:
        return _generate_dev_upload_placeholder(expires_at_str=expires_at_str)


def _generate_s3_presigned_post(
    *,
    doc: "Document",
    category: "DocumentCategory",
    ttl_seconds: int,
    expires_at_str: str,
    max_size: int,
    success_redirect_url: str | None = None,
) -> dict:
    """
    Generate a boto3 presigned POST for direct browser→S3 upload.

    S3 enforces content-length-range and content-type constraints so that
    the browser cannot upload oversized or mistyped files even without
    going through Django.

    The presigned POST uploads to the quarantine prefix. The file will NOT
    be accessible until the ClamAV scan passes and the object is moved to
    the active prefix.

    Args:
        max_size:             Resolved size cap from validate_upload_request() — already
                              accounts for citizen vs staff cap and category override.
                              Using the caller-resolved value prevents the S3 policy from
                              accidentally applying the staff cap to citizen uploads.
        success_redirect_url: Absolute URL S3 redirects the browser to after a successful
                              upload. Injected as success_action_redirect so the confirm
                              view is reached and scan_status advances past PENDING_UPLOAD.
                              If None, the field is omitted (acceptable in dev/test).
    """
    import boto3
    from botocore.exceptions import ClientError

    storage_opts = settings.STORAGES.get("default", {}).get("OPTIONS", {})
    bucket_name: str = _get_bucket_name(storage_opts)
    region_name: str = storage_opts.get("region_name", "ca-central-1")
    kms_key_id: str = storage_opts.get("object_parameters", {}).get("SSEKMSKeyId", "")

    # Determine allowed MIME types for this category
    allowed_mimes: list[str] = (
        category.allowed_mime_types
        if category.allowed_mime_types
        else _civicos().get("ALLOWED_UPLOAD_MIME_TYPES", [])
    )

    # max_size is passed in from validate_upload_request() where it was resolved
    # per-user (citizen cap vs staff cap). Do NOT recompute here — recomputing
    # would silently apply the staff cap to all users, defeating the citizen limit.

    conditions: list = [
        # Enforce upload to the exact quarantine key (prevents key substitution)
        {"key": doc.storage_key},
        # Allow any content-type in the approved set
        ["content-length-range", 1, max_size],
    ]
    # Add content-type constraint if only one MIME type is allowed
    if len(allowed_mimes) == 1:
        conditions.append({"Content-Type": allowed_mimes[0]})

    # Server-side encryption — Protected B requires KMS
    if kms_key_id:
        conditions.append({"x-amz-server-side-encryption": "aws:kms"})
        conditions.append({"x-amz-server-side-encryption-aws-kms-key-id": kms_key_id})

    # Inject success_action_redirect so S3 redirects the browser to the confirm
    # view after the upload completes. Without this the browser has no redirect
    # target and confirm_upload() is never called — the Document stays in
    # PENDING_UPLOAD forever (Bug C-4).
    if success_redirect_url:
        conditions.append({"success_action_redirect": success_redirect_url})

    fields: dict[str, str] = {}
    if success_redirect_url:
        fields["success_action_redirect"] = success_redirect_url

    try:
        s3_client = boto3.client("s3", region_name=region_name)
        presigned = s3_client.generate_presigned_post(
            Bucket=bucket_name,
            Key=doc.storage_key,
            Fields=fields or None,
            Conditions=conditions,
            ExpiresIn=ttl_seconds,
        )
    except ClientError as exc:
        logger.error(
            "Failed to generate presigned POST for document pk=%s: %s",
            doc.pk,
            exc,
        )
        raise ValidationError(
            _("Could not generate an upload URL. Please try again.")
        ) from exc

    return {
        "url": presigned["url"],
        "fields": presigned["fields"],
        "expires_at": expires_at_str,
    }


def _generate_dev_upload_placeholder(*, expires_at_str: str) -> dict:
    """
    Return a dev-mode upload placeholder.

    In development (FileSystemStorage), the browser cannot POST directly
    to the local filesystem. The dev upload API view handles the file
    itself and writes it to the quarantine path. This function returns a
    placeholder that the dev client understands.

    The client should POST the file as a multipart form to:
        POST /api/v1/documents/{doc_id}/dev-upload/

    This endpoint is only available when DEBUG=True or TESTING=True.
    It is NOT wired in production.
    """
    return {
        "url": "",           # dev client uses the confirm-upload flow directly
        "fields": {},
        "expires_at": expires_at_str,
    }


def _verify_file_exists(storage_key: str) -> None:
    """
    Verify that the file was actually uploaded to the quarantine storage key.

    S3: uses boto3 head_object.
    Local filesystem (dev): checks that the file path exists.

    Raises:
        ValidationError: File is not found at the expected key.
    """
    if _is_s3_storage():
        _verify_s3_object_exists(storage_key)
    else:
        _verify_local_file_exists(storage_key)


def _verify_s3_object_exists(storage_key: str) -> None:
    """
    Check that an S3 object exists at the given key.

    Raises:
        ValidationError: Object not found (404) or access denied (403).
    """
    import boto3
    from botocore.exceptions import ClientError

    storage_opts = settings.STORAGES.get("default", {}).get("OPTIONS", {})
    bucket_name: str = _get_bucket_name(storage_opts)
    region_name: str = storage_opts.get("region_name", "ca-central-1")

    s3_client = boto3.client("s3", region_name=region_name)
    try:
        s3_client.head_object(Bucket=bucket_name, Key=storage_key)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("404", "NoSuchKey", "403", "Forbidden"):
            raise ValidationError(
                _("File was not found in storage. Please upload the file and try again.")
            ) from exc
        # Unexpected S3 error — propagate
        # PIPEDA: storage_key MUST NOT appear in logs. Log exception only.
        logger.error("Unexpected S3 error verifying object existence: %s", exc)
        raise ValidationError(
            _("Could not verify file upload. Please try again.")
        ) from exc


def _verify_local_file_exists(storage_key: str) -> None:
    """
    Check that a file exists at the local filesystem path for the given key.

    Path containment check: resolves both MEDIA_ROOT and the constructed path
    and asserts the result is inside MEDIA_ROOT. Prevents path traversal via
    a crafted storage_key (e.g. "../../etc/passwd").

    Raises:
        ValidationError: File path does not exist or is outside MEDIA_ROOT.
    """
    media_root = Path(settings.MEDIA_ROOT).resolve()
    local_path = (media_root / storage_key).resolve()
    if not local_path.is_relative_to(media_root):
        raise ValidationError(
            _("File was not found in storage. Please upload the file and try again.")
        )
    if not local_path.exists():
        raise ValidationError(
            _("File was not found in storage. Please upload the file and try again.")
        )


def _read_first_bytes(storage_key: str, *, length: int = 8192) -> bytes:
    """
    Read the first `length` bytes from the stored file.

    S3: uses a ranged GET request to avoid downloading the entire file.
    Local filesystem (dev): opens the file and reads directly.

    Returns:
        First `length` bytes of the file (may be fewer for very small files).

    Raises:
        ValidationError: File cannot be read.
    """
    if _is_s3_storage():
        return _read_s3_first_bytes(storage_key, length=length)
    else:
        return _read_local_first_bytes(storage_key, length=length)


def _read_s3_first_bytes(storage_key: str, *, length: int) -> bytes:
    """Read the first `length` bytes of an S3 object using a ranged GET."""
    import boto3
    from botocore.exceptions import ClientError

    storage_opts = settings.STORAGES.get("default", {}).get("OPTIONS", {})
    bucket_name: str = _get_bucket_name(storage_opts)
    region_name: str = storage_opts.get("region_name", "ca-central-1")

    s3_client = boto3.client("s3", region_name=region_name)
    try:
        resp = s3_client.get_object(
            Bucket=bucket_name,
            Key=storage_key,
            Range=f"bytes=0-{length - 1}",
        )
        return resp["Body"].read()
    except ClientError as exc:
        # PIPEDA: storage_key MUST NOT appear in logs. Log exception only.
        logger.error("Failed to read first bytes for magic-byte check: %s", exc)
        raise ValidationError(
            _("Could not read uploaded file for validation. Please try again.")
        ) from exc


def _read_local_first_bytes(storage_key: str, *, length: int) -> bytes:
    """Read the first `length` bytes from a local filesystem file.

    Path containment check prevents traversal via crafted storage_key values.
    """
    media_root = Path(settings.MEDIA_ROOT).resolve()
    local_path = (media_root / storage_key).resolve()
    if not local_path.is_relative_to(media_root):
        raise ValidationError(
            _("Could not read uploaded file for validation. Please try again.")
        )
    try:
        with open(local_path, "rb") as fh:
            return fh.read(length)
    except OSError as exc:
        logger.error(
            "Failed to read first bytes from local path=%r: %s",
            str(local_path),
            exc,
        )
        raise ValidationError(
            _("Could not read uploaded file for validation. Please try again.")
        ) from exc


def _read_full_file(storage_key: str) -> bytes:
    """
    Read the complete file content from storage.

    Required for ZIP bomb inspection: the ZIP central directory is at the END
    of the archive, so a partial (8 KB) read is never sufficient for any
    real-world .docx or .xlsx file.

    S3: performs a full GetObject (no Range header). Appropriate for files
    already size-capped at ≤50 MB by upload policy.
    Local filesystem (dev): reads the entire file.

    Returns:
        All bytes of the stored file.

    Raises:
        ValidationError: File cannot be read.
    """
    if _is_s3_storage():
        return _read_full_s3_file(storage_key)
    else:
        return _read_full_local_file(storage_key)


def _read_full_s3_file(storage_key: str) -> bytes:
    """Download the complete contents of an S3 object."""
    import boto3
    from botocore.exceptions import ClientError

    storage_opts = settings.STORAGES.get("default", {}).get("OPTIONS", {})
    bucket_name: str = _get_bucket_name(storage_opts)
    region_name: str = storage_opts.get("region_name", "ca-central-1")

    s3_client = boto3.client("s3", region_name=region_name)
    try:
        resp = s3_client.get_object(Bucket=bucket_name, Key=storage_key)
        return resp["Body"].read()
    except ClientError as exc:
        # PIPEDA: storage_key MUST NOT appear in logs. Log exception only.
        logger.error("Failed to read full S3 file for ZIP bomb check: %s", exc)
        raise ValidationError(
            _("Could not read uploaded file for validation. Please try again.")
        ) from exc


def _read_full_local_file(storage_key: str) -> bytes:
    """Read the complete contents of a local filesystem file.

    Path containment check prevents traversal via crafted storage_key values.
    """
    media_root = Path(settings.MEDIA_ROOT).resolve()
    local_path = (media_root / storage_key).resolve()
    if not local_path.is_relative_to(media_root):
        raise ValidationError(
            _("Could not read uploaded file for validation. Please try again.")
        )
    try:
        with open(local_path, "rb") as fh:
            return fh.read()
    except OSError as exc:
        logger.error(
            "Failed to read full local file for ZIP bomb check: path=%r, error=%s",
            str(local_path),
            exc,
        )
        raise ValidationError(
            _("Could not read uploaded file for validation. Please try again.")
        ) from exc


def _validate_magic_bytes(
    *, first_bytes: bytes, allowed_mimes: list[str]
) -> str | None:
    """
    Validate a file's actual content against the allowed MIME type list.

    Uses python-magic (libmagic) to detect the real MIME type from the
    file's magic bytes, independent of the filename or client-supplied
    Content-Type header.

    M-1: Availability of python-magic is governed by ``CIVICOS['MAGIC_BYTES_REQUIRED']``
    (default ``True``), not ``CLAMAV_REQUIRED``. The two are independent concerns:
    ClamAV scans for malware; magic bytes verify the declared file type. A dev
    environment without ClamAV but with python-magic installed should still run
    magic-byte validation. A dev environment without python-magic should only
    skip validation if the operator has explicitly opted out via
    ``MAGIC_BYTES_REQUIRED = False``.

    M-3: Returns the detected MIME type string so ``confirm_upload()`` can
    overwrite ``doc.mime_type`` with the authoritative magic-detected value,
    replacing the unverified client-supplied value stored at upload initiation.
    Returns ``None`` when the check is bypassed (magic unavailable + not required)
    so the caller knows not to overwrite the client-supplied value.

    `magic` is imported at module level (falls back to None if not installed).
    Tests patch `apps.documents.services.upload.magic` at the module level.

    Args:
        first_bytes:   Leading bytes of the file (at least ``_MAGIC_BYTE_READ_LENGTH``).
        allowed_mimes: MIME type allowlist from the DocumentCategory / CIVICOS config.

    Returns:
        The detected MIME type string on success, or ``None`` if the check was
        bypassed because python-magic is unavailable and ``MAGIC_BYTES_REQUIRED``
        is False.

    Raises:
        ImproperlyConfigured: ``allowed_mimes`` is empty (misconfiguration).
        ImproperlyConfigured: python-magic unavailable and ``MAGIC_BYTES_REQUIRED``
            is True (the default).
        ValidationError: Detected MIME type is not in ``allowed_mimes``.
        ValidationError: python-magic raised an unexpected error during detection.
    """
    if not allowed_mimes:
        # Guard: if the MIME allowlist is somehow empty here (misconfigured category
        # or CIVICOS setting changed between validate and confirm), raise rather than
        # silently accept every MIME type or reject all uploads cryptically.
        raise ImproperlyConfigured(
            "allowed_mimes is empty — magic-byte validation cannot proceed. "
            "Configure MIME types on the DocumentCategory or "
            "CIVICOS['ALLOWED_UPLOAD_MIME_TYPES']."
        )

    if magic is None:
        # M-1: Use MAGIC_BYTES_REQUIRED (default True), not CLAMAV_REQUIRED.
        # CLAMAV_REQUIRED and magic availability are independent concerns.
        magic_required: bool = _civicos().get("MAGIC_BYTES_REQUIRED", True)
        if magic_required:
            raise ImproperlyConfigured(
                "python-magic / libmagic is not installed and "
                "CIVICOS['MAGIC_BYTES_REQUIRED'] is True (the default). "
                "Install python-magic (`pip install python-magic` and the "
                "libmagic system library) or set "
                "CIVICOS['MAGIC_BYTES_REQUIRED'] = False to disable "
                "magic-byte validation in development environments."
            )
        logger.warning(
            "python-magic not available; skipping magic-byte validation "
            "(CIVICOS['MAGIC_BYTES_REQUIRED'] = False). "
            "Install python-magic for production use."
        )
        return None  # M-3: caller must not overwrite doc.mime_type

    try:
        detected_mime: str = magic.Magic(mime=True).from_buffer(first_bytes)
    except Exception as exc:  # noqa: BLE001
        logger.warning("magic-byte detection failed: %s", exc)
        raise ValidationError(
            _("Could not determine file type. Please ensure the file is not corrupted.")
        ) from exc

    if detected_mime not in allowed_mimes:
        logger.warning(
            "Magic-byte validation failed: detected=%r, allowed=%r",
            detected_mime,
            allowed_mimes,
        )
        raise ValidationError(
            _("File content does not match its declared type. "
              "Please ensure you are uploading a valid file.")
        )

    return detected_mime  # M-3: authoritative MIME from libmagic


def _check_zip_bomb(data: bytes) -> None:
    """
    Detect ZIP bomb attacks in .docx and .xlsx uploads (CVE-2024-0450).

    Must be called with the COMPLETE file bytes. The ZIP central directory is
    located at the END of the archive; a partial read (e.g. first 8 KB) is
    always insufficient for real-world .docx and .xlsx files. The caller in
    confirm_upload() uses _read_full_file() to guarantee complete data.

    Checks both:
      1. Total entry count > DOCUMENT_ZIP_MAX_ENTRIES (default: 1000)
      2. Any entry's uncompressed/compressed ratio > DOCUMENT_ZIP_MAX_RATIO (default: 100)

    A .docx or .xlsx that fails to parse as a valid ZIP archive is treated as
    corrupted and rejected — not silently accepted.

    Raises:
        ValidationError: Entry count or compression ratio limit exceeded.
        ValidationError: Archive is not a valid ZIP file (file is corrupted).
    """
    civicos = _civicos()
    max_entries: int = civicos.get("DOCUMENT_ZIP_MAX_ENTRIES", 1000)
    max_ratio: int = civicos.get("DOCUMENT_ZIP_MAX_RATIO", 100)

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            entries = zf.infolist()
    except zipfile.BadZipFile as exc:
        # A .docx/.xlsx that cannot be parsed as a valid ZIP archive is
        # corrupted or deliberately malformed. Reject it rather than silently
        # accepting unknown content.
        raise ValidationError(
            _("File is not a valid archive. "
              "Please ensure the file is not corrupted before uploading.")
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("ZIP bomb check failed unexpectedly: %s", exc)
        raise ValidationError(
            _("Could not read archive structure. Please ensure the file is not corrupted.")
        ) from exc

    # Guard 1: Entry count limit
    if len(entries) > max_entries:
        raise ValidationError(
            _("Archive contains too many entries (%(count)d). "
              "Maximum allowed is %(max)d.")
            % {"count": len(entries), "max": max_entries}
        )

    # Guard 2: Compression ratio limit
    for entry in entries:
        if entry.compress_size > 0:
            ratio = entry.file_size / entry.compress_size
            if ratio > max_ratio:
                raise ValidationError(
                    _("Archive compression ratio is suspicious (%(ratio).1f:1). "
                      "The file may be a ZIP bomb.")
                    % {"ratio": ratio}
                )
