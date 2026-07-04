"""
Document Management Building Block — Retention service.

Handles the full document disposal lifecycle:

  1. schedule_expiry()   — set expires_at and retain_until at creation time
  2. soft_delete()       — set deleted_at, update scan_status=DELETED
  3. run_disposal()      — Celery Beat task: find expired docs, soft-delete them
  4. run_hard_delete()   — Celery Beat task: hard-delete after 30-day grace
  5. apply_legal_hold()  — set legal_hold=True (blocks all disposal)
  6. release_legal_hold()— set legal_hold=False (re-enables disposal)
  7. purge_expired_tokens() — hard-delete expired/used DocumentAccessTokens

Disposal invariants (MUST be preserved):
  - legal_hold=True is an ABSOLUTE block. No disposal may proceed while True.
  - Hard deletion is IRREVERSIBLE: S3 object deleted + DB row deleted.
    No recovery after hard deletion. 30-day grace period exists for this reason.
  - Privacy Act s.6(1): retain_until must be in the past before disposal.
  - soft_delete() fires document_soft_deleted signal.
  - Hard delete fires document_hard_deleted signal BEFORE row deletion so receivers
    can log the event; the Document PK must be included in signal kwargs.
  - PIPEDA: audit entries for disposal use document.pk only (no filename, no storage_key).

Governing law: PIPEDA clause 4.5.3 & 4.7.5, Privacy Act s.6(1), LAC DA #2016/001,
NIST SP 800-88 (irreversibility of disposal).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from django.utils import timezone

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

    Preconditions:
      - document.legal_hold must be False (raises ValueError if True).
      - Document must not already be deleted.
      - document.retain_until (Privacy Act s.6(1) minimum retention) must be in
        the past for automated (Celery) disposal. Staff-initiated deletion may
        override this floor with appropriate justification (raise ValueError
        if retain_until is still in future, unless caller passes override=True
        — Wave 5 will wire the override permission to documents.override_retention).

    Side effects:
      1. Sets document.deleted_at = timezone.now()
      2. Sets document.scan_status = ScanStatus.DELETED
         CONTRACT: pending_hard_delete() queries for scan_status=DELETED.
         This step MUST happen or the hard-delete Celery task will silently
         skip this document forever.
      3. Sets document.deleted_by = deleted_by (if provided)
      4. Sets document.deletion_reason = reason
      5. Saves with update_fields (atomic)
      6. Moves S3 object from active/ to deleted/ prefix (if in S3)
      7. Fires document_soft_deleted signal
      8. Writes audit: AuditEventType.RECORD_DELETED

    Args:
        document:   Document to soft-delete.
        deleted_by: User who initiated deletion (None for system/Celery deletion).
        reason:     Staff-internal reason for deletion.

    Returns:
        The updated Document instance.

    Raises:
        ValueError:  document.legal_hold is True.
        ValueError:  Document is already soft-deleted.

    STUB: Full implementation in Wave 5. This signature is fixed.
    """
    raise NotImplementedError("retention.soft_delete — implemented in Wave 5")


def hard_delete(
    *,
    document: "Document",
) -> None:
    """
    Irreversibly hard-delete a document from storage and the database.

    This is called by the Celery Beat task after the 30-day grace period.

    CRITICAL: This operation is IRREVERSIBLE. The S3 object is deleted before
    the DB row. If the S3 delete fails, the DB row is NOT deleted (fail-safe).

    Preconditions:
      - document.deleted_at must be set (soft-delete must have occurred).
      - document.scan_status must be DELETED (set by soft_delete() — see contract
        comment on soft_delete() side effect step 2).
      - document.legal_hold must be False.
      - document.deleted_at must be at least _DEFAULT_HARD_DELETE_GRACE_DAYS ago.

    Side effects:
      1. Fires document_hard_deleted signal (BEFORE deletion — last chance to log)
      2. Deletes S3 object at document.storage_key
      3. Deletes DocumentAttachment records for this document
      4. Hard-deletes the Document DB row
      - On S3 deletion failure: logs error, sets scan_status=QUARANTINED to flag
        for manual investigation; does NOT delete DB row.

    Args:
        document: The soft-deleted Document to hard-delete.

    Raises:
        ValueError:  Preconditions not met (legal hold, not soft-deleted, etc.)

    STUB: Full implementation in Wave 5. This signature is fixed.
    """
    raise NotImplementedError("retention.hard_delete — implemented in Wave 5")


def apply_legal_hold(
    *,
    document: "Document",
    set_by: "User",
    reason: str,
) -> "Document":
    """
    Apply a legal hold to a document, blocking all automated disposal.

    Requires `documents.manage_legal_hold` permission.
    Reverses any pending disposal that may have been scheduled.

    Args:
        document: The Document to hold.
        set_by:   User applying the hold (Privacy Officer or legal counsel).
        reason:   Non-PII description of why the hold was applied.

    Returns:
        The updated Document instance (legal_hold=True).

    Raises:
        PermissionDenied: set_by lacks documents.manage_legal_hold permission.
        ValueError:       Document is already on legal hold.

    Side effects:
      1. Sets document.legal_hold = True, document.legal_hold_reason = reason,
         document.legal_hold_set_by = set_by
      2. Saves with update_fields (atomic)
      3. Fires document_legal_hold_changed signal with
         kwargs: document_pk, legal_hold=True, set_by_id=set_by.pk
      4. Writes audit: AuditEventType.STATUS_CHANGED

    STUB: Full implementation in Wave 5. This signature is fixed.
    """
    raise NotImplementedError("retention.apply_legal_hold — implemented in Wave 5")


def release_legal_hold(
    *,
    document: "Document",
    released_by: "User",
) -> "Document":
    """
    Release a legal hold, re-enabling automated disposal per the retention schedule.

    Requires `documents.manage_legal_hold` permission.

    Args:
        document:     The Document to release.
        released_by:  User releasing the hold.

    Returns:
        The updated Document instance (legal_hold=False).

    Raises:
        PermissionDenied: released_by lacks documents.manage_legal_hold permission.
        ValueError:       Document is not on legal hold.

    Side effects:
      1. Clears document.legal_hold = False
      2. Saves with update_fields (atomic)
      3. Fires document_legal_hold_changed signal with
         kwargs: document_pk, legal_hold=False, set_by_id=released_by.pk
      4. Writes audit: AuditEventType.STATUS_CHANGED

    STUB: Full implementation in Wave 5. This signature is fixed.
    """
    raise NotImplementedError("retention.release_legal_hold — implemented in Wave 5")


def purge_expired_tokens(*, dry_run: bool = False) -> int:
    """
    Hard-delete expired and used DocumentAccessTokens.

    Called daily by Celery Beat. Tokens are short-lived (5 min TTL) so there
    is no grace period — expiry means immediately purgeable.

    Args:
        dry_run: If True, count records that would be deleted without deleting.

    Returns:
        Number of tokens deleted (or would-be-deleted in dry_run mode).

    STUB: Full implementation in Wave 5. This signature is fixed.
    """
    raise NotImplementedError("retention.purge_expired_tokens — implemented in Wave 5")
