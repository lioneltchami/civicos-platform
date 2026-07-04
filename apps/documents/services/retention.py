"""
Document Management Building Block — Retention service.

Handles the full document disposal lifecycle:

  1. schedule_expiry()        — set expires_at and retain_until at creation time
  2. soft_delete()            — set deleted_at, update scan_status=DELETED
  3. hard_delete()            — irreversible: null _storage_key, delete S3 object
                                 (Document row is RETAINED for audit trail)
  4. mark_purpose_fulfilled() — transitory records: soft-delete when purpose met
  5. apply_legal_hold()       — set legal_hold=True (blocks all disposal)
  6. release_legal_hold()     — set legal_hold=False (re-enables disposal)
  7. purge_expired_tokens()   — hard-delete expired/used DocumentAccessTokens

Disposal invariants (MUST be preserved):
  - legal_hold=True is an ABSOLUTE block. No disposal may proceed while True.
  - Hard deletion is IRREVERSIBLE: S3 object deleted + storage key nulled.
    The Document row is RETAINED per spec §11.2 for the audit trail.
    No recovery after hard deletion. 30-day grace period exists for this reason.
  - Privacy Act s.6(1): retain_until must be in the past before disposal.
  - soft_delete() fires document_soft_deleted signal.
  - Hard delete fires document_hard_deleted signal BEFORE nulling storage so
    receivers can log the event; the Document PK is still valid in the DB row.
  - PIPEDA: audit entries for disposal use document.pk only
    (no filename, no storage_key, no uploader PII).

Governing law: PIPEDA clause 4.5.3 & 4.7.5, Privacy Act s.6(1), LAC DA #2016/001,
NIST SP 800-88 (irreversibility of disposal), TBS SPIN 2023-06-13.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_event

if TYPE_CHECKING:
    from django.contrib.auth import get_user_model

    from apps.documents.models import Document

    User = get_user_model()

logger = logging.getLogger(__name__)

# Default hard-delete grace period in days.
# Documents are soft-deleted first; hard deletion happens after this period.
_DEFAULT_HARD_DELETE_GRACE_DAYS: int = 30


def schedule_expiry(
    *,
    document: "Document",
) -> None:
    """
    Set expires_at and retain_until on a newly created Document.

    Called immediately after Document creation in validate_upload_request().

    Calculation rules:
      retain_until = (created_at + min_retention_days).date()
                     — set for ALL documents (including transitory) if min > 0.
                     — Privacy Act s.6(1): mandatory minimum retention floor.

      expires_at   = created_at + max_retention_days
                     — set ONLY for non-transitory documents.
                     — For transitory records: expires_at stays None and is set
                       later by mark_purpose_fulfilled() when purpose is met.
                       Setting it at creation would be premature — transitory records
                       are destroyed when purpose is fulfilled, not on a calendar date.

    Transitory record reasoning (LAC DA #2016/001):
      Transitory records have no fixed calendar expiry. They are destroyed when
      the purpose for which they were created has been fulfilled. Scheduling
      expires_at at creation would trigger disposal before the purpose is complete.
      The service layer that marks purpose-fulfilled sets expires_at = now().

    Args:
        document: The newly created Document (already saved to DB with created_at set).

    Saves:
        Updates document.expires_at and document.retain_until in place using
        update_fields to avoid overwriting other fields set by the caller.
    """
    category = document.category
    created_at = document.created_at  # timezone-aware datetime

    # ── retain_until: Privacy Act s.6(1) minimum retention floor ─────────────
    # Convert datetime to date for the DateField.
    # Set for ALL documents (transitory or not) when min_retention_days > 0.
    # Even transitory records occasionally have minimum retention requirements
    # (e.g., legally mandated 30-day retention for public interest records).
    retain_until = None
    if category.min_retention_days > 0:
        retain_until = (created_at + timedelta(days=category.min_retention_days)).date()

    # ── expires_at: calendar-based disposal date (non-transitory only) ────────
    # Transitory records: expires_at left as None — set by mark_purpose_fulfilled().
    expires_at = None
    if not category.is_transitory and category.max_retention_days > 0:
        expires_at = created_at + timedelta(days=category.max_retention_days)

    # ── Persist both fields atomically ────────────────────────────────────────
    # update_fields avoids race conditions with any other concurrent saves.
    # Both fields start as None in the DB; we only overwrite them here if they
    # should be set. If both are None (e.g. is_transitory AND min_retention=0)
    # the document will never appear in pending_disposal() — as intended.
    document.expires_at = expires_at
    document.retain_until = retain_until
    document.save(update_fields=["expires_at", "retain_until", "updated_at"])

    logger.debug(
        "schedule_expiry: doc pk=%s category=%r transitory=%s "
        "expires_at=%s retain_until=%s",
        document.pk,
        category.slug,
        category.is_transitory,
        expires_at,
        retain_until,
    )


def soft_delete(
    *,
    document: "Document",
    deleted_by: "User | None" = None,
    reason: str = "",
) -> "Document":
    """
    Soft-delete a document: set deleted_at=now(), scan_status=DELETED.

    Preconditions (enforced twice: pre-lock and under lock for TOCTOU safety):
      - document.legal_hold must be False (raises ValueError if True).
      - Document must not already be deleted (raises ValueError if deleted_at set).

    Side effects (in order):
      1. select_for_update() INSIDE atomic() — prevents concurrent deletions.
      2. Sets doc.deleted_at = timezone.now()
      3. Sets doc.scan_status = ScanStatus.DELETED
         CONTRACT: pending_hard_delete() queries for scan_status=DELETED.
         This MUST happen or the hard-delete Celery task silently skips forever.
      4. Sets doc.deleted_by = deleted_by (if provided)
      5. Sets doc.deletion_reason = reason
      6. Saves with update_fields (atomic, targeted)
      7. Fires document_soft_deleted signal via send_robust()
      8. Writes audit: AuditEventType.RECORD_DELETED
         PIPEDA: event_detail contains ONLY deletion_reason and deleted_by_pk —
         NO original_filename, NO storage_key, NO uploader email.

    Args:
        document:   Document to soft-delete.
        deleted_by: User who initiated deletion (None for system/Celery deletion).
        reason:     Staff-internal reason for deletion (e.g. "retention_expired").

    Returns:
        The updated Document instance (reflects saved state).

    Raises:
        ValueError:  document.legal_hold is True.
        ValueError:  Document is already soft-deleted.
    """
    from apps.audit.models import AuditEventType
    from apps.documents.models import Document
    from apps.documents.signals import document_soft_deleted

    # Pre-lock check — fast path to avoid lock contention on obvious cases.
    if document.legal_hold:
        raise ValueError(
            f"Document {document.pk} is on legal hold and cannot be deleted. "
            "Remove the legal hold before attempting disposal."
        )
    if document.deleted_at is not None:
        raise ValueError(
            f"Document {document.pk} is already soft-deleted "
            f"(deleted_at={document.deleted_at.isoformat()})."
        )

    now = timezone.now()

    with transaction.atomic():
        # Re-fetch under lock (TOCTOU guard: another process may have set
        # legal_hold=True or deleted_at between the pre-check and here).
        doc = Document.objects.select_for_update().get(pk=document.pk)

        if doc.legal_hold:
            raise ValueError(
                f"Document {doc.pk} is on legal hold and cannot be deleted. "
                "Remove the legal hold before attempting disposal."
            )
        if doc.deleted_at is not None:
            raise ValueError(
                f"Document {doc.pk} is already soft-deleted "
                f"(deleted_at={doc.deleted_at.isoformat()})."
            )

        doc.deleted_at = now
        doc.scan_status = Document.ScanStatus.DELETED  # CONTRACT: required for pending_hard_delete()
        doc.deleted_by = deleted_by
        doc.deletion_reason = reason
        doc.save(
            update_fields=[
                "deleted_at",
                "scan_status",
                "deleted_by",
                "deletion_reason",
                "updated_at",
            ]
        )

    # Update the caller's in-memory instance to reflect the saved state.
    # Prevents callers from working with stale field values after this call.
    document.deleted_at = doc.deleted_at
    document.scan_status = doc.scan_status
    document.deleted_by = doc.deleted_by
    document.deletion_reason = doc.deletion_reason

    # Fire signal outside the transaction (send_robust never raises).
    # PIPEDA: kwargs contain ONLY document_pk and deleted_by_id — no PII.
    document_soft_deleted.send_robust(
        sender=Document,
        document_pk=str(document.pk),
        deleted_by_id=deleted_by.pk if deleted_by else None,
    )

    # Audit log — PIPEDA: NO original_filename, NO storage_key in event_detail.
    record_event(
        event_type=AuditEventType.RECORD_DELETED,
        actor_id=str(deleted_by.pk) if deleted_by else None,
        resource_type="documents.Document",
        resource_id=str(document.pk),
        event_detail={
            "deletion_reason": reason or "retention_expired",
            "deleted_by_pk": deleted_by.pk if deleted_by else None,
        },
    )

    logger.info(
        "soft_delete: Document pk=%r soft-deleted. reason=%r deleted_by_pk=%r",
        str(document.pk),
        reason or "retention_expired",
        deleted_by.pk if deleted_by else None,
    )

    return document


def hard_delete(
    *,
    document: "Document",
) -> None:
    """
    Irreversibly hard-delete a document from storage.

    Per spec §11.2 and the audit trail requirement: the Document DB row is
    RETAINED. Only the S3 object is deleted and the _storage_key field is
    nulled out. This is intentional — the row provides the permanent audit
    trail (who uploaded, what category, scan result, disposal timestamp).

    Ordering:
      1. Fire document_hard_deleted signal (BEFORE storage deletion — last chance
         to log while document data is still available).
      2. Write audit entry (RECORD_PURGED) — before irreversible storage ops.
      3. Delete S3/storage object at document.storage_key (IRREVERSIBLE).
         If this fails: log error; do NOT null the storage key or update scan_status.
         The task scheduler will retry on the next run.
      4. Null out _storage_key field ("") — reference is no longer valid.

    Preconditions:
      - document.legal_hold must be False.
      - document.deleted_at must be set (soft-delete must have occurred).
      - document.scan_status must be DELETED (set by soft_delete — see contract).
      - document.deleted_at must be at least _DEFAULT_HARD_DELETE_GRACE_DAYS ago.

    Args:
        document: The soft-deleted Document to hard-delete.

    Raises:
        ValueError:  Preconditions not met (legal hold, not soft-deleted, etc.)

    PIPEDA:
      - signal kwargs contain ONLY document_pk and category_slug.
      - audit event_detail contains ONLY cleared_at timestamp.
      - NO original_filename, NO storage_key value, NO uploader PII in any log.

    Governing law: PIPEDA clause 4.7.5, NIST SP 800-88, spec §11.2.
    """
    from apps.audit.models import AuditEventType
    from apps.documents.models import Document
    from apps.documents.signals import document_hard_deleted

    # ── Precondition checks ───────────────────────────────────────────────────
    if document.legal_hold:
        raise ValueError(
            f"Document {document.pk} is on legal hold and cannot be hard-deleted."
        )
    if document.deleted_at is None:
        raise ValueError(
            f"Document {document.pk} has not been soft-deleted. "
            "soft_delete() must be called before hard_delete()."
        )
    if document.scan_status != Document.ScanStatus.DELETED:
        raise ValueError(
            f"Document {document.pk} scan_status is {document.scan_status!r}; "
            "must be DELETED (set by soft_delete()) for hard deletion."
        )

    grace_cutoff = timezone.now() - timedelta(days=_DEFAULT_HARD_DELETE_GRACE_DAYS)
    if document.deleted_at > grace_cutoff:
        raise ValueError(
            f"Document {document.pk} was soft-deleted less than "
            f"{_DEFAULT_HARD_DELETE_GRACE_DAYS} days ago "
            f"(deleted_at={document.deleted_at.isoformat()}). "
            "Grace period has not elapsed."
        )

    doc_pk = str(document.pk)
    # Capture category_slug before any potential state change — safe since
    # category is PROTECT FK and will not disappear.
    try:
        category_slug = document.category.slug
    except Exception:
        category_slug = "unknown"

    # Capture the current storage key before nulling it.
    storage_key = document.storage_key

    # ── Step 1: Fire signal BEFORE deletion (last chance to log) ─────────────
    # send_robust never raises; all receivers are called even if some fail.
    # PIPEDA: kwargs contain ONLY document_pk and category_slug.
    document_hard_deleted.send_robust(
        sender=Document,
        document_pk=doc_pk,
        category_slug=category_slug,
    )

    # ── Step 2: Write audit entry BEFORE irreversible storage operations ──────
    # If the storage delete fails, the audit entry is still there as evidence.
    record_event(
        event_type=AuditEventType.RECORD_PURGED,
        resource_type="documents.Document",
        resource_id=doc_pk,
        event_detail={
            "cleared_at": timezone.now().isoformat(),
            # PIPEDA: NO original_filename, NO storage_key value, NO uploader PII
        },
    )

    # ── Step 3: Delete S3/storage object (IRREVERSIBLE) ───────────────────────
    # Fail-safe: if storage deletion fails, do NOT null the storage key.
    # The document will re-appear in pending_hard_delete() on the next run
    # because scan_status=DELETED and deleted_at <= cutoff still holds.
    if storage_key:
        try:
            default_storage.delete(storage_key)
        except Exception:
            logger.exception(
                "hard_delete: storage deletion failed for doc pk=%r. "
                "Storage key NOT nulled. Task will retry on next scheduled run. "
                "Exception type logged above.",
                doc_pk,
            )
            # Do NOT null the storage key — the file may still be there.
            # Do NOT delete the DB row — the document is still accessible to staff.
            return
    else:
        # storage_key already empty — storage already cleared (idempotent path).
        logger.info(
            "hard_delete: doc pk=%r has empty storage_key; skipping storage delete.",
            doc_pk,
        )

    # ── Step 4: Null out _storage_key field ───────────────────────────────────
    # Use bulk update (no save() call) to avoid triggering model signals.
    # **{"_storage_key": ""} syntax required because _ prefix in keyword args
    # is valid Python but unusual — the dict form is more explicit about intent.
    Document.objects.filter(pk=document.pk).update(**{"_storage_key": ""})

    # Update the in-memory instance to reflect the nulled key.
    document._storage_key = ""

    logger.info(
        "hard_delete: Document pk=%r storage cleared and storage_key nulled. "
        "DB row retained for audit trail per spec §11.2.",
        doc_pk,
    )


def mark_purpose_fulfilled(
    *,
    document: "Document",
    actor: "User",
) -> "Document":
    """
    For transitory documents: trigger soft-delete once purpose is fulfilled.

    Transitory records (LAC Disposition Authorization #2016/001) are documents
    created for a specific purpose that have no fixed calendar expiry — they are
    destroyed once the purpose for which they were created is met.

    Example: PIPEDA Data Export packages — delivered to the citizen, then
    destroyed. Setting a calendar expires_at at creation would be premature
    (what if the export takes days to generate?).

    This function:
      1. Validates the category is transitory (raises ValueError if not).
      2. Sets expires_at = now() on the document (purpose is fulfilled NOW).
      3. Calls soft_delete() with reason="transitory_purpose_fulfilled".

    Per spec §11.4, this replaces the direct soft_delete call for transitory docs.

    Args:
        document: The transitory Document to dispose.
        actor:    The User performing the action (for audit trail).

    Returns:
        The soft-deleted Document instance.

    Raises:
        ValueError: document.category.is_transitory is False.
        ValueError: document.legal_hold is True (propagated from soft_delete).
        ValueError: document is already soft-deleted.
    """
    if not document.category.is_transitory:
        raise ValueError(
            f"mark_purpose_fulfilled() called on non-transitory document {document.pk}. "
            f"Category {document.category.slug!r} has is_transitory=False. "
            "Only transitory categories (LAC DA #2016/001) may be disposed via this function."
        )

    # Mark the expires_at before soft_delete so pending_disposal() reflects the
    # correct state if the document is ever un-deleted or re-queried.
    # This is a belt-and-suspenders step — soft_delete sets scan_status=DELETED
    # which already removes the doc from active queries.
    now = timezone.now()
    from apps.documents.models import Document

    Document.objects.filter(pk=document.pk).update(expires_at=now)
    document.expires_at = now

    logger.info(
        "mark_purpose_fulfilled: doc pk=%r transitory purpose fulfilled; "
        "initiating soft_delete. actor_pk=%r",
        str(document.pk),
        actor.pk,
    )

    return soft_delete(
        document=document,
        deleted_by=actor,
        reason="transitory_purpose_fulfilled",
    )


def apply_legal_hold(
    *,
    document: "Document",
    set_by: "User",
    reason: str,
) -> "Document":
    """
    Apply a legal hold to a document, blocking all automated disposal.

    Legal holds represent ATIP requests, OPC complaints, or litigation holds.
    Per TBS guidance: "ATIP and litigation holds supersede all normal schedules."

    Permission required: documents.manage_legal_hold
    (Typically granted only to Privacy Officers and legal counsel.)

    Side effects (in order):
      1. Permission check (PermissionDenied if set_by lacks manage_legal_hold).
      2. Pre-lock check (ValueError if already on hold).
      3. select_for_update() INSIDE atomic() (TOCTOU guard).
      4. Sets legal_hold=True, legal_hold_reason, legal_hold_set_by.
      5. Saves with update_fields.
      6. Fires document_legal_hold_changed signal (legal_hold=True).
      7. Writes audit: AuditEventType.STATUS_CHANGED.

    Args:
        document: The Document to hold.
        set_by:   User applying the hold (must have manage_legal_hold permission).
        reason:   Non-PII description of why the hold was applied (staff-internal).

    Returns:
        The updated Document instance (legal_hold=True).

    Raises:
        PermissionDenied: set_by lacks documents.manage_legal_hold permission.
        ValueError:       Document is already on legal hold.
    """
    from apps.audit.models import AuditEventType
    from apps.documents.models import Document
    from apps.documents.signals import document_legal_hold_changed

    if not set_by.has_perm("documents.manage_legal_hold"):
        raise PermissionDenied(
            f"User pk={set_by.pk} does not have documents.manage_legal_hold permission."
        )

    # Pre-lock check.
    if document.legal_hold:
        raise ValueError(
            f"Document {document.pk} is already on legal hold. "
            "Release the existing hold before reapplying."
        )

    with transaction.atomic():
        doc = Document.objects.select_for_update().get(pk=document.pk)

        if doc.legal_hold:
            raise ValueError(
                f"Document {doc.pk} is already on legal hold (concurrent race)."
            )

        doc.legal_hold = True
        doc.legal_hold_reason = reason
        doc.legal_hold_set_by = set_by
        doc.save(
            update_fields=[
                "legal_hold",
                "legal_hold_reason",
                "legal_hold_set_by",
                "updated_at",
            ]
        )

    # Update caller's in-memory instance.
    document.legal_hold = True
    document.legal_hold_reason = reason
    document.legal_hold_set_by = set_by

    # Fire signal outside transaction (send_robust never raises).
    # PIPEDA: kwargs contain ONLY document_pk, legal_hold flag, set_by_id (int).
    document_legal_hold_changed.send_robust(
        sender=Document,
        document_pk=str(document.pk),
        legal_hold=True,
        set_by_id=set_by.pk,
    )

    # Audit — PIPEDA: NO original_filename, NO storage_key.
    record_event(
        event_type=AuditEventType.STATUS_CHANGED,
        actor_id=str(set_by.pk),
        resource_type="documents.Document",
        resource_id=str(document.pk),
        event_detail={
            "legal_hold": True,
            "reason": reason,
        },
    )

    logger.info(
        "apply_legal_hold: Document pk=%r legal hold applied. set_by_pk=%r",
        str(document.pk),
        set_by.pk,
    )

    return document


def release_legal_hold(
    *,
    document: "Document",
    released_by: "User",
) -> "Document":
    """
    Release a legal hold, re-enabling automated disposal per the retention schedule.

    When a legal hold is lifted, the normal retention schedule resumes from the
    current date (TBS guidance). The document will appear in pending_disposal()
    if its expires_at and retain_until are already in the past.

    Permission required: documents.manage_legal_hold

    Side effects (in order):
      1. Permission check (PermissionDenied if released_by lacks manage_legal_hold).
      2. Pre-lock check (ValueError if not on hold).
      3. select_for_update() INSIDE atomic() (TOCTOU guard).
      4. Sets legal_hold=False.
      5. Saves with update_fields (does NOT clear legal_hold_reason — preserved
         for audit trail — or legal_hold_set_by for the same reason).
      6. Fires document_legal_hold_changed signal (legal_hold=False).
      7. Writes audit: AuditEventType.STATUS_CHANGED.

    Args:
        document:     The Document whose hold is to be released.
        released_by:  User releasing the hold (must have manage_legal_hold).

    Returns:
        The updated Document instance (legal_hold=False).

    Raises:
        PermissionDenied: released_by lacks documents.manage_legal_hold permission.
        ValueError:       Document is not on legal hold.
    """
    from apps.audit.models import AuditEventType
    from apps.documents.models import Document
    from apps.documents.signals import document_legal_hold_changed

    if not released_by.has_perm("documents.manage_legal_hold"):
        raise PermissionDenied(
            f"User pk={released_by.pk} does not have documents.manage_legal_hold permission."
        )

    # Pre-lock check.
    if not document.legal_hold:
        raise ValueError(
            f"Document {document.pk} is not on legal hold. Nothing to release."
        )

    with transaction.atomic():
        doc = Document.objects.select_for_update().get(pk=document.pk)

        if not doc.legal_hold:
            raise ValueError(
                f"Document {doc.pk} is not on legal hold (concurrent race)."
            )

        doc.legal_hold = False
        # NOTE: legal_hold_reason and legal_hold_set_by are preserved for audit
        # trail — they document WHO set the hold and WHY, even after release.
        doc.save(update_fields=["legal_hold", "updated_at"])

    # Update caller's in-memory instance.
    document.legal_hold = False

    # Fire signal outside transaction (send_robust never raises).
    document_legal_hold_changed.send_robust(
        sender=Document,
        document_pk=str(document.pk),
        legal_hold=False,
        set_by_id=released_by.pk,
    )

    # Audit.
    record_event(
        event_type=AuditEventType.STATUS_CHANGED,
        actor_id=str(released_by.pk),
        resource_type="documents.Document",
        resource_id=str(document.pk),
        event_detail={
            "legal_hold": False,
        },
    )

    logger.info(
        "release_legal_hold: Document pk=%r legal hold released. released_by_pk=%r",
        str(document.pk),
        released_by.pk,
    )

    return document


def purge_expired_tokens(*, dry_run: bool = False) -> int:
    """
    Hard-delete expired and used DocumentAccessTokens.

    Called daily by Celery Beat via ``run_purge_expired_tokens`` in tasks.py.

    A token is purgeable when either condition holds:
      - ``expires_at <= now``   : TTL has elapsed; token can no longer be validated.
      - ``used_at is not None`` : Token has already been consumed (single-use).

    Both conditions are purged together in a single DELETE query.
    There is no grace period — expired/used tokens contain no recovery value:
      - Expired tokens cannot be redeemed (is_valid=False).
      - Used tokens can only be redeemed once; used_at being set means the download
        has already occurred.

    PIPEDA: Tokens store a masked IP address. Purging expired tokens is required
    by PIPEDA data-minimisation principles: we must not retain personal data
    beyond the purpose for which it was collected.

    Args:
        dry_run: If True, returns the count that WOULD be deleted without
                 actually deleting. Useful for monitoring/alerting queries.

    Returns:
        Number of tokens deleted (or would-be-deleted if dry_run=True).
    """
    from django.db.models import Q

    from apps.documents.models import DocumentAccessToken

    now = timezone.now()

    # Purgeable = expired OR already used.
    # The two conditions are independent — we want to clean up both in one pass:
    #   - Expired tokens: cannnot be redeemed regardless of used_at.
    #   - Used tokens: single-use semantics; the download is complete.
    qs = DocumentAccessToken.objects.filter(
        Q(expires_at__lte=now) | Q(used_at__isnull=False)
    )

    if dry_run:
        count = qs.count()
        logger.info(
            "purge_expired_tokens (dry_run): %d token(s) would be deleted.",
            count,
        )
        return count

    _, deleted_counts = qs.delete()
    # Django's bulk delete returns a dict like:
    #   {"documents.DocumentAccessToken": 17, ...}
    # Use .get() with the full app_label.ModelName key; fall back to 0.
    count = deleted_counts.get("documents.DocumentAccessToken", 0)

    logger.info("purge_expired_tokens: deleted %d expired/used token(s).", count)
    return count
