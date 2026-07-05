"""
Document Management Building Block — Versioning service.

Manages the document version chain: creating new versions of existing documents,
querying version history, and ensuring version chain integrity.

Version chain invariants (enforced by select_for_update in create_new_version):
  1. Exactly one Document per root chain has is_latest_version=True.
  2. root_document points to the version-1 Document (root_document=None).
  3. version_number is monotonically increasing within a chain.
  4. Versions are immutable once created; only the latest version is actionable.

PIPEDA constraints:
  - Previous versions are not deleted when a new version is created.
    They are retained per the category retention policy (older versions
    share the same expires_at as the root; legal_hold is inherited).
  - original_filename NEVER in audit event_detail.
  - storage_key NEVER logged or returned.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import connection, models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    from django.contrib.auth import get_user_model

    from apps.documents.models import Document

    User = get_user_model()

logger = logging.getLogger(__name__)


def create_new_version(
    *,
    user: "User",
    root_document: "Document",
    original_filename: str,
    mime_type: str,
    size_bytes: int,
    description: str = "",
) -> dict:
    """
    Create a new version of an existing document chain.

    Accepted as ``root_document``: any Document in the chain (root or child).
    This service resolves to the chain root automatically.

    The new version starts in PENDING_UPLOAD status (same as a fresh upload).
    The caller must call confirm_upload() after the file is uploaded to S3.

    Permission rules:
      1. Superusers: always allowed.
      2. Staff with ``documents.upload_staff_document``: allowed (any document).
      3. The original uploader (``chain_root.uploaded_by == user``) with
         ``documents.upload_document`` permission: allowed.
      4. Everyone else: PermissionDenied.

    Chain integrity guarantee:
      ``select_for_update()`` locks the entire version chain (root + all children)
      inside ``atomic()`` before any write. This prevents two concurrent requests
      from both finding version_number=N and creating duplicate version N+1 rows.

    PIPEDA constraints:
      - ``original_filename`` is NEVER in audit event_detail.
      - ``storage_key`` is NEVER returned to callers.
      - Audit entry records only: doc_pk, root_pk, version_number.

    Args:
        user:              Authenticated user performing the upload.
        root_document:     Any Document in the chain (root or child version).
                           Service resolves to the chain root automatically.
        original_filename: Client-supplied filename (stored in DB; never used as path).
        mime_type:         Client-supplied MIME type (validated against category allowlist).
        size_bytes:        Client-supplied file size (validated against cap).
        description:       Optional description for this version.

    Returns:
        Same structure as ``validate_upload_request()``::

            {
                "doc_id":        str   — UUID of the new Document version,
                "upload_url":    str   — presigned POST URL (or '' in dev),
                "upload_fields": dict  — fields for the multipart POST,
                "expires_at":    str   — ISO-8601 expiry of the presigned URL,
            }

        PIPEDA: ``storage_key`` is NEVER in the returned dict.

    Raises:
        PermissionDenied:      User is not authorised to version this document.
        ValidationError:       File fails size, extension, or MIME type validation.
    """
    from apps.audit.models import AuditEventType
    from apps.audit.services import record_event
    from apps.documents.models import Document
    from apps.documents.services.retention import schedule_expiry
    from apps.documents.services.upload import (
        _ALLOWED_EXTENSIONS,
        _civicos,
        _generate_presigned_post,
        _make_storage_key,
        _user_is_staff_uploader,
    )
    from apps.documents.signals import document_version_created
    from pathlib import Path

    # ── Resolve chain root ────────────────────────────────────────────────────
    # Caller may pass any version in the chain. We normalise to the chain root
    # (version 1, root_document=None) as the canonical anchor for all queries.
    if root_document.root_document_id is not None:
        # Caller passed a non-root version — resolve to the real root.
        # select_related("category") avoids an extra query when validating MIME.
        try:
            chain_root = Document.objects.select_related("category").get(
                pk=root_document.root_document_id
            )
        except Document.DoesNotExist:
            logger.error(
                "create_new_version: chain root not found for document pk=%s "
                "(root_document_id=%s). Chain invariant violation.",
                root_document.pk,
                root_document.root_document_id,
            )
            raise ValidationError(
                _("Could not resolve the document version chain. Please contact support.")
            )
    else:
        # root_document IS the chain root (version 1).
        # Re-fetch with select_related("category") if category is not already loaded.
        if "category" not in root_document.__dict__:
            try:
                chain_root = Document.objects.select_related("category").get(
                    pk=root_document.pk
                )
            except Document.DoesNotExist:
                raise ValidationError(
                    _("Document not found.")
                )
        else:
            chain_root = root_document

    # Guard: the resolved root must itself be a true root (root_document_id IS NULL).
    if chain_root.root_document_id is not None:
        logger.error(
            "create_new_version: resolved chain_root pk=%s still has "
            "root_document_id=%s set. Chain invariant violated — aborting.",
            chain_root.pk,
            chain_root.root_document_id,
        )
        raise ValidationError(
            _("Document version chain is corrupted. Contact support.")
        )

    # ── Soft-delete guard ─────────────────────────────────────────────────────
    if chain_root.deleted_at is not None:
        raise ValidationError(
            _("Cannot create a new version of a deleted document.")
        )

    # ── SQLite guard ──────────────────────────────────────────────────────────
    if connection.vendor == "sqlite":
        raise ImproperlyConfigured(
            "create_new_version requires SELECT FOR UPDATE support. "
            "SQLite is not supported for this service in production."
        )

    category = chain_root.category

    # ── Permission check ──────────────────────────────────────────────────────
    if not _user_may_version(user=user, chain_root=chain_root):
        raise PermissionDenied

    # ── Input validation (same rules as validate_upload_request) ─────────────
    # ── Size ─────────────────────────────────────────────────────────────────
    if size_bytes <= 0:
        raise ValidationError(_("File size must be greater than zero."))

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

    # ── Extension allowlist ───────────────────────────────────────────────────
    raw_suffix = Path(original_filename).suffix
    ext = raw_suffix.lstrip(".").lower()
    if not ext or ext not in _ALLOWED_EXTENSIONS:
        raise ValidationError(
            _("File type '.%(ext)s' is not permitted.")
            % {"ext": ext or "(none)"}
        )

    # ── MIME type check ───────────────────────────────────────────────────────
    allowed_mimes: list[str] = (
        category.allowed_mime_types
        if category.allowed_mime_types
        else _civicos().get("ALLOWED_UPLOAD_MIME_TYPES", [])
    )
    if not allowed_mimes:
        raise ImproperlyConfigured(
            f"DocumentCategory '{category.slug}' has no allowed_mime_types and "
            "CIVICOS['ALLOWED_UPLOAD_MIME_TYPES'] is empty."
        )
    if mime_type not in allowed_mimes:
        raise ValidationError(
            _("Content type '%(mime)s' is not permitted for this document category.")
            % {"mime": mime_type}
        )

    # ── Generate new UUID and storage key ─────────────────────────────────────
    # A fresh UUID for both the new Document PK and its quarantine storage path.
    # Storage key is completely independent of original_filename (OWASP layer 7).
    doc_uuid = uuid.uuid4()
    new_storage_key = _make_storage_key(str(doc_uuid), prefix="quarantine")

    # ── Atomic version-chain update ───────────────────────────────────────────
    # select_for_update() locks the ENTIRE chain (root + all child versions).
    # This prevents two concurrent create_new_version() calls from both reading
    # version_number=N and both creating duplicate version N+1 rows.
    #
    # Lock granularity: Q(pk=chain_root.pk) OR Q(root_document=chain_root)
    # covers the root itself and every child version pointing to it.
    with transaction.atomic():
        chain_qs = (
            Document.objects.select_for_update()
            .filter(
                models.Q(pk=chain_root.pk)
                | models.Q(root_document_id=chain_root.pk)
            )
            .order_by("-version_number")
        )
        chain_docs = list(chain_qs)

        if not chain_docs:
            # Chain invariant violation — the root must always exist.
            logger.error(
                "create_new_version: chain_root pk=%s not found during lock. "
                "Chain invariant violated.",
                chain_root.pk,
            )
            raise ValidationError(
                _("Could not resolve the document version chain. Please contact support.")
            )

        # ── Re-check soft-delete guard under lock (TOCTOU prevention) ────────
        # The pre-lock guard at line 164 is a fast path without a DB lock.
        # A concurrent soft_delete() between that check and this atomic() block
        # could delete the chain root after we read it but before we lock it.
        # Re-read chain_root from the locked result set to get the committed state.
        locked_root = next((d for d in chain_docs if d.pk == chain_root.pk), None)
        if locked_root is not None and locked_root.deleted_at is not None:
            raise ValidationError(
                _("Cannot create a new version of a deleted document.")
            )

        # Find the current latest version (invariant: exactly one per chain).
        current_latest_candidates = [d for d in chain_docs if d.is_latest_version]
        if len(current_latest_candidates) == 0:
            logger.error(
                "create_new_version: chain_root pk=%s has ZERO is_latest_version=True docs. "
                "Chain invariant violated — using highest version as repair. Manual audit required.",
                chain_root.pk,
            )
            current_latest = chain_docs[0]  # chain_docs is ordered by -version_number
        elif len(current_latest_candidates) > 1:
            logger.error(
                "create_new_version: chain_root pk=%s has %d is_latest_version=True docs. "
                "Chain invariant violated — using highest version_number as repair. Manual audit required.",
                chain_root.pk,
                len(current_latest_candidates),
            )
            current_latest = max(current_latest_candidates, key=lambda d: d.version_number)
        else:
            current_latest = current_latest_candidates[0]
        new_version_number = current_latest.version_number + 1

        # ── Swap is_latest_version ────────────────────────────────────────────
        # Mark the current latest as not-latest before creating the new version.
        # Both operations happen in the same atomic block, so there is never a
        # moment where zero or two versions are marked as latest.
        current_latest.is_latest_version = False
        current_latest.save(update_fields=["is_latest_version", "updated_at"])

        # ── Create the new version Document ───────────────────────────────────
        new_doc = Document.objects.create(
            id=doc_uuid,
            category=category,
            uploaded_by=user,
            original_filename=original_filename,   # stored in DB; NEVER used as path
            _storage_key=new_storage_key,           # NEVER returned to callers
            mime_type=mime_type,
            size_bytes=size_bytes,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
            security_classification=category.security_classification,
            version_number=new_version_number,
            root_document=chain_root,              # always points to version 1
            is_latest_version=True,
            description=description,
            # H-1: Propagate legal_hold from the locked chain root so that every
            # new version in a legally-held chain inherits the hold unconditionally.
            # TBS ATIP / litigation hold guidance: "holds supersede all normal schedules."
            legal_hold=locked_root.legal_hold,
            # Retention/legal-hold: set by schedule_expiry() immediately below.
        )
        # schedule_expiry() writes expires_at and retain_until atomically
        # within the same transaction, ensuring the new version enters the
        # disposal schedule from the moment it is created.
        schedule_expiry(document=new_doc)

        # ── Audit log (inside atomic — PIPEDA 4.5.3) ──────────────────────────
        # record_event() is inside atomic() so that an audit write failure does
        # NOT roll back the version creation (try/except swallows the exception).
        # PIPEDA 4.5.3: audit entries must be written atomically with the state
        # change they record.
        # PIPEDA constraints on event_detail:
        #   - NO original_filename (may contain PII)
        #   - NO storage_key (internal S3 path)
        # Only: root_document_pk, new_version_pk, version_number.
        try:
            record_event(
                event_type=AuditEventType.RECORD_CREATED,
                actor_id=str(user.pk),
                resource_type="documents.Document",
                resource_id=str(new_doc.pk),
                event_detail={
                    "root_document_pk": str(chain_root.pk),
                    "new_version_pk": str(new_doc.pk),
                    "version_number": new_version_number,
                    "category_slug": category.slug,
                    "mime_type": mime_type,
                    "size_bytes": size_bytes,
                    # original_filename deliberately excluded (PIPEDA)
                    # storage_key deliberately excluded (security)
                },
            )
        except Exception:
            # Audit failure must NEVER prevent the version creation.
            logger.exception(
                "create_new_version: audit write failed for new doc pk=%s; "
                "version creation unaffected.",
                new_doc.pk,
            )

    # ── Generate presigned upload URL (outside transaction) ───────────────────
    # Network I/O (boto3 S3) must NOT run inside a DB transaction.
    # If the presigned POST generation fails, we roll back:
    #   1. Restore is_latest_version=True on the previous version.
    #   2. Delete the newly created Document row.
    # This is best-effort: if the rollback itself fails, both Document rows
    # are orphaned but the DB constraint (exactly one is_latest_version=True)
    # will be violated. Log loudly so operators can investigate.

    # H-2: Build the success_action_redirect URL so S3 redirects the browser to
    # the confirm view after upload. Without this the new version stays in
    # PENDING_UPLOAD forever (same pattern as validate_upload_request in upload.py).
    # Lazy imports to match the file's existing pattern and avoid circular imports.
    from django.conf import settings as _settings
    from django.urls import reverse as _reverse

    _base_url = getattr(_settings, "SITE_URL", "").rstrip("/")
    if not _base_url:
        logger.warning(
            "create_new_version: SITE_URL is not configured — "
            "success_action_redirect will NOT be injected into the S3 presigned POST "
            "for new version doc pk=%s. "
            "Set SITE_URL in production settings so the browser is redirected to the "
            "confirm view after upload (otherwise documents stay in PENDING_UPLOAD).",
            new_doc.pk,
        )
    _confirm_path = _reverse("documents:upload-confirm", args=[str(new_doc.pk)])
    success_redirect_url: str | None = f"{_base_url}{_confirm_path}" if _base_url else None

    try:
        presigned = _generate_presigned_post(
            doc=new_doc,
            category=category,
            max_size=max_size,
            success_redirect_url=success_redirect_url,
        )
    except Exception:
        logger.error(
            "create_new_version: presigned POST generation failed for "
            "new doc pk=%s; rolling back version chain.",
            new_doc.pk,
        )
        try:
            with transaction.atomic():
                restored = Document.objects.filter(pk=current_latest.pk).update(
                    is_latest_version=True,
                    updated_at=timezone.now(),
                )
                if restored != 1:
                    logger.error(
                        "create_new_version: rollback could not restore is_latest_version "
                        "on pk=%s (rows updated=%d). Manual remediation required.",
                        current_latest.pk,
                        restored,
                    )
                new_doc.delete()
        except Exception:
            logger.error(
                "create_new_version: ROLLBACK FAILED — could not restore is_latest_version "
                "on pk=%s or delete new doc pk=%s. Chain root pk=%s has data integrity violation. "
                "Manual remediation required.",
                current_latest.pk,
                new_doc.pk,
                chain_root.pk,
            )
        raise

    # ── Fire signal ───────────────────────────────────────────────────────────
    # send_robust() ensures a bad receiver never propagates an exception here.
    # PIPEDA: kwargs contain only PKs and version_number — no PII.
    document_version_created.send_robust(
        sender=Document,
        root_document_pk=str(chain_root.pk),
        new_version_pk=str(new_doc.pk),
        version_number=new_version_number,
    )

    # PIPEDA: storage_key NEVER in the returned dict.
    return {
        "doc_id": str(new_doc.pk),
        "upload_url": presigned["url"],
        "upload_fields": presigned["fields"],
        "expires_at": presigned["expires_at"],
    }


def get_version_history(
    *,
    user: "User",
    root_document_pk: str,
) -> list["Document"]:
    """
    Return the full version chain for a document, ordered by version_number ascending.

    Only ACTIVE and SCANNING versions are included (QUARANTINED and DELETED
    are excluded from citizen views; staff with documents.coordinator_view_document see all).

    Args:
        user:             The authenticated user making the request.
        root_document_pk: The PK of the root document (version 1).

    Returns:
        List of Document instances, ordered by version_number ascending.

    Raises:
        PermissionDenied:      User is not authorised to view this document.
        Document.DoesNotExist: Not found (caller converts to 404).

    STUB: Full implementation in Wave 4. This signature is fixed.
    """
    raise NotImplementedError("versioning.get_version_history — implemented in Wave 4")


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _user_may_version(
    *,
    user: "User",
    chain_root: "Document",
) -> bool:
    """
    Return True if ``user`` is allowed to create a new version of this document chain.

    Permission hierarchy:
      1. Superusers: always allowed.
      2. Staff with ``documents.upload_staff_document``: allowed (any document).
      3. The original uploader (``chain_root.uploaded_by == user``) who has
         ``documents.upload_document``: allowed (citizen can re-version own docs).
      4. Everyone else: denied.

    Note: This check does NOT gate on scan_status. Versioning creates a new
    PENDING_UPLOAD document; it does not read the existing file content.
    """
    if not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    if user.has_perm("documents.upload_staff_document"):
        return True

    # Citizen uploader: must own the document AND have upload permission.
    # Citizens cannot re-version documents in staff-only categories.
    if (
        chain_root.uploaded_by_id == user.pk
        and user.has_perm("documents.upload_document")
        and not chain_root.category.staff_only
    ):
        return True

    return False
