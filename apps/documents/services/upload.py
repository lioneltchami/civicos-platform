"""
Document Management Building Block — Upload service.

Handles the multi-step upload pipeline:
  1. validate_upload_request() — validate intent, return presigned POST data
  2. confirm_upload()          — verify file exists, dispatch ClamAV scan task

All validation is defense-in-depth per OWASP File Upload Cheat Sheet:
  Layer 1: Category permission and intent check
  Layer 2: Size check (cap per category or global setting)
  Layer 3: Extension allowlist (not blocklist)
  Layer 4: MIME type allowlist (not client Content-Type — that is untrusted)
  Layer 5: Magic-byte validation (python-magic / libmagic) — in confirm_upload()
  Layer 6: ZIP bomb detection (CVE-2024-0450) — in confirm_upload()
  Layer 7: Storage key randomisation (UUID-based, never filename-derived)
  Layer 8: ClamAV virus scan — async task dispatched from confirm_upload()

PIPEDA constraints (must be preserved by all callers):
  - original_filename is stored in DB but NEVER used as the storage path.
  - original_filename is NEVER written to audit event_detail.
  - storage_key is NEVER returned to clients. Only doc_id (UUID) is returned.
  - Audit log entries for upload events use doc.pk as resource_id only.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    from django.contrib.auth import get_user_model

    from apps.documents.models import Document, DocumentCategory

    User = get_user_model()

logger = logging.getLogger(__name__)

# Allowlisted file extensions (lowercase, without leading dot).
# This is an allowlist — not present here → rejected regardless of MIME type.
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
# .docx and .xlsx are ZIP containers — they must be checked for entry count and
# compression ratio. Bare .zip is NOT in _ALLOWED_EXTENSIONS so it will be
# rejected at the extension-allowlist stage before reaching this check.
# If .zip support is added to _ALLOWED_EXTENSIONS in future, add it here too.
_ZIP_FAMILY: frozenset[str] = frozenset({"docx", "xlsx"})


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

    Args:
        user:              The authenticated user performing the upload.
        category_slug:     DocumentCategory slug (e.g. 'service-request-evidence').
        original_filename: Client-supplied filename (stored in DB, never used as path).
        mime_type:         Client-supplied MIME type (validated against allowlist).
        size_bytes:        Client-supplied file size (validated against cap).

    Returns:
        {
            "doc_id":        str  — UUID of the created Document record,
            "upload_url":    str  — presigned POST URL (S3) or direct upload URL (dev),
            "upload_fields": dict — fields to include in the multipart POST,
            "expires_at":    str  — ISO-8601 timestamp when presigned URL expires,
        }

    Raises:
        PermissionDenied:  User is not allowed to upload to this category.
        ValidationError:   File fails size, extension, or MIME type validation.
        DocumentCategory.DoesNotExist: category_slug not found.

    STUB: Full implementation in Wave 2. This signature is fixed.
    """
    # [Wave 2] Implementation:
    # 1. Fetch category (raises DoesNotExist if not found — caller converts to 404)
    # 2. Check _user_may_upload_to_category(user, category)
    # 3. Validate size_bytes
    # 4. Validate extension from original_filename
    # 5. Validate mime_type against category.allowed_mime_types or global setting
    # 6. Generate storage_key = _make_storage_key(doc_uuid, prefix="quarantine")
    # 7. Create Document(scan_status=PENDING_UPLOAD, _storage_key=storage_key, ...)
    # 8. Generate presigned POST via boto3 (dev: return simple upload URL)
    # 9. Fire document_upload_initiated signal
    # 10. Return {doc_id, upload_url, upload_fields, expires_at}
    raise NotImplementedError("upload.validate_upload_request — implemented in Wave 2")


def confirm_upload(
    *,
    user: "User",
    doc_id: str,
) -> "Document":
    """
    Confirm that a browser upload completed and dispatch the ClamAV scan task.

    Called by the API view after the browser has POSTed to S3.
    Verifies the file exists at the quarantine key, runs magic-byte validation,
    updates scan_status to SCANNING, and dispatches scan_document.delay().

    Args:
        user:   The authenticated user (must be the uploader).
        doc_id: UUID of the Document to confirm.

    Returns:
        The updated Document instance (scan_status=SCANNING).

    Raises:
        Http404:           doc_id not found, OR user is not the uploader (IDOR
                           prevention: 404 — not 403 — so existence is not confirmed).
        ValidationError:   magic-byte or ZIP-bomb validation failure.

    STUB: Full implementation in Wave 2. This signature is fixed.
    """
    # [Wave 2] Implementation:
    # 1. Fetch doc with select_for_update() inside atomic()
    # 2. Verify doc.uploaded_by == user; raise Http404 if not (IDOR: 404 not 403)
    # 3. Verify doc.scan_status == PENDING_UPLOAD (idempotency guard)
    # 4. Verify file exists at quarantine key (boto3 head_object or local stat)
    # 5. Stream first 8 KB → python-magic → validate mime_type
    # 6. If ZIP family: validate entry count and compression ratio (CVE-2024-0450)
    # 7. Update doc.scan_status = SCANNING
    # 8. Dispatch scan_document.apply_async(args=[str(doc.pk)], countdown=2)
    # 9. Write audit: AuditEventType.RECORD_CREATED, resource_type="documents.Document"
    # 10. Fire document_confirmed signal
    # 11. Return updated doc
    raise NotImplementedError("upload.confirm_upload — implemented in Wave 2")


_VALID_PREFIXES: frozenset[str] = frozenset({"quarantine", "active", "deleted"})


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
                  guard: an arbitrary prefix could create keys outside the
                  expected namespace).

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

    Business rules (Wave 2 will wire these to permissions):
      - Superusers: always allowed.
      - Staff with documents.upload_document: allowed to any category.
      - Regular users (citizens): allowed only to categories that are
        not staff-only (all current categories are accessible to citizens).

    STUB: Full implementation in Wave 2.
    """
    if user.is_superuser:
        return True
    if user.has_perm("documents.upload_document"):
        return True
    # Citizens can upload to any non-staff-only category.
    # Wave 2 will add a staff_only flag to DocumentCategory.
    return user.is_authenticated
