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
    document: Document,
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
        "schedule_expiry: doc pk=%s category=%r transitory=%s " "expires_at=%s retain_until=%s",
        document.pk,
        category.slug,
        category.is_transitory,
        expires_at,
        retain_until,
    )


def soft_delete(
    *,
    document: Document,
    deleted_by: User | None = None,
    reason: str = "",
) -> Document:
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
        doc.scan_status = (
            Document.ScanStatus.DELETED
        )  # CONTRACT: required for pending_hard_delete()
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

        # H-2: audit INSIDE atomic — state change and audit commit together.
        # A crash between the atomic exit and a post-block record_event() call
        # would leave a committed deletion with no audit trail (PIPEDA violation).
        # PIPEDA: NO original_filename, NO storage_key in event_detail.
        record_event(
            event_type=AuditEventType.RECORD_DELETED,
            actor_id=str(deleted_by.pk) if deleted_by else None,
            resource_type="documents.Document",
            resource_id=str(doc.pk),
            event_detail={
                "deletion_reason": reason or "retention_expired",
                "deleted_by_pk": deleted_by.pk if deleted_by else None,
            },
        )

        # H-2: signal via on_commit — fires only after transaction commits, never
        # if the transaction rolls back.  H-3: send_robust() return values are
        # inspected and receiver exceptions logged at ERROR level.
        # PIPEDA: kwargs contain ONLY document_pk and deleted_by_id — no PII.
        _doc_pk_s = str(doc.pk)
        _deleted_by_pk = deleted_by.pk if deleted_by else None

        def _fire_soft_deleted(
            _pk=_doc_pk_s,  # noqa: ANN001
            _dbpk=_deleted_by_pk,  # noqa: ANN001
        ) -> None:
            results = document_soft_deleted.send_robust(
                sender=Document,
                document_pk=_pk,
                deleted_by_id=_dbpk,
            )
            for _receiver, _response in results:
                if isinstance(_response, Exception):
                    logger.error(
                        "soft_delete: signal receiver %r raised: %r",
                        _receiver,
                        _response,
                    )

        transaction.on_commit(_fire_soft_deleted)

    # Update the caller's in-memory instance to reflect the saved state.
    # Prevents callers from working with stale field values after this call.
    document.deleted_at = doc.deleted_at
    document.scan_status = doc.scan_status
    document.deleted_by = doc.deleted_by
    document.deletion_reason = doc.deletion_reason

    logger.info(
        "soft_delete: Document pk=%r soft-deleted. reason=%r deleted_by_pk=%r",
        str(document.pk),
        reason or "retention_expired",
        deleted_by.pk if deleted_by else None,
    )

    return document


def hard_delete(
    *,
    document: Document,
) -> None:
    """
    Irreversibly hard-delete a document from storage.

    Per spec §11.2 and NIST SP 800-88: the Document DB row is RETAINED.
    Only the S3 object is deleted and the _storage_key field is nulled.
    After successful deletion scan_status is set to PURGED — the terminal
    lifecycle state — preventing re-processing on subsequent daily runs (C-3).

    Ordering (C-2 fix — audit/signal fire AFTER confirmed S3 deletion):
      1. Pre-lock fast-path checks (in-memory instance, for performance only).
      2. select_for_update() INSIDE atomic() — re-check ALL preconditions under
         lock (C-1 fix: TOCTOU guard against concurrent legal hold application).
      3. Capture storage_key; release DB lock.
      4. Delete S3/storage object (IRREVERSIBLE, outside lock).
         On failure: log; leave scan_status=DELETED; task retries next run.
      5. Conditional DB update: null _storage_key + set scan_status=PURGED.
         filter(scan_status=DELETED) prevents concurrent-worker duplicate audits.
      6. Write RECORD_PURGED audit entry (AFTER confirmed S3 deletion).
      7. Fire document_hard_deleted signal (AFTER confirmed S3 deletion).

    Preconditions (checked pre-lock AND re-checked under lock):
      - document.legal_hold must be False.
      - document.deleted_at must be set (soft-delete must have occurred).
      - document.scan_status must be DELETED.
      - document.deleted_at must be at least grace_days ago.

    Grace days: read from settings.CIVICOS['DOCUMENT_HARD_DELETE_GRACE_DAYS']
    (falls back to _DEFAULT_HARD_DELETE_GRACE_DAYS = 30 if not set).

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
    from django.conf import settings

    from apps.audit.models import AuditEventType
    from apps.documents.models import Document
    from apps.documents.signals import document_hard_deleted

    # H-1 fix: read grace_days from settings so the CIVICOS knob actually works.
    civicos: dict = getattr(settings, "CIVICOS", {})
    grace_days: int = civicos.get(
        "DOCUMENT_HARD_DELETE_GRACE_DAYS", _DEFAULT_HARD_DELETE_GRACE_DAYS
    )

    doc_pk = str(document.pk)

    # ── Pre-lock fast-path checks ─────────────────────────────────────────────
    # Using the caller's in-memory instance — for performance only.
    # All checks are repeated under lock below (the authoritative TOCTOU guard).
    if document.legal_hold:
        raise ValueError(f"Document {document.pk} is on legal hold and cannot be hard-deleted.")
    if document.deleted_at is None:
        raise ValueError(
            f"Document {document.pk} has not been soft-deleted. "
            "soft_delete() must be called before hard_delete()."
        )
    # Idempotent fast-path: already purged by a previous run.
    if document.scan_status == Document.ScanStatus.PURGED:
        logger.info(
            "hard_delete: doc pk=%r already PURGED (idempotent); returning.",
            doc_pk,
        )
        return
    if document.scan_status != Document.ScanStatus.DELETED:
        raise ValueError(
            f"Document {document.pk} scan_status is {document.scan_status!r}; "
            "must be DELETED (set by soft_delete()) for hard deletion."
        )

    # ── Step 1: Lock and re-check ALL preconditions under lock (C-1 fix) ──────
    # select_for_update() INSIDE atomic() is the TOCTOU guard. A concurrent
    # apply_legal_hold() that commits between the pre-check above and this lock
    # would otherwise be missed, allowing irreversible deletion of a legally-held
    # document. The lock closes that race completely.
    storage_key: str = ""
    category_slug: str = "unknown"

    with transaction.atomic():
        doc = Document.objects.select_for_update().get(pk=document.pk)

        # Idempotent: a concurrent worker already committed PURGED.
        if doc.scan_status == Document.ScanStatus.PURGED:
            logger.info(
                "hard_delete: doc pk=%r already PURGED (concurrent race); "
                "returning (idempotent).",
                doc_pk,
            )
            return

        # Re-check ALL preconditions against the locked DB row.
        if doc.legal_hold:
            raise ValueError(
                f"Document {doc.pk} is on legal hold (set concurrently). " "Hard deletion aborted."
            )
        if doc.deleted_at is None:
            raise ValueError(f"Document {doc.pk} has not been soft-deleted (concurrent race).")
        if doc.scan_status != Document.ScanStatus.DELETED:
            raise ValueError(
                f"Document {doc.pk} scan_status is {doc.scan_status!r} under lock; "
                "must be DELETED for hard deletion."
            )

        now = timezone.now()
        grace_cutoff = now - timedelta(days=grace_days)
        if doc.deleted_at > grace_cutoff:
            raise ValueError(
                f"Document {doc.pk} was soft-deleted less than {grace_days} days ago "
                f"(deleted_at={doc.deleted_at.isoformat()}). Grace period has not elapsed."
            )

        # Capture values needed outside the transaction (after lock release).
        storage_key = doc.storage_key
        try:
            category_slug = doc.category.slug
        except Exception:
            category_slug = "unknown"

    # Lock released. storage_key and category_slug captured safely.
    # legal_hold was confirmed False at lock-acquisition time.

    # ── Step 2: Delete S3/storage object (IRREVERSIBLE) ───────────────────────
    # OUTSIDE the transaction: network I/O must not hold a DB lock (performance).
    # C-2 fix: audit and signal fire AFTER confirmed S3 deletion — never before.
    cleared_at = timezone.now()
    if storage_key:
        try:
            default_storage.delete(storage_key)
        except Exception:
            logger.exception(
                "hard_delete: storage deletion FAILED for doc pk=%r. "
                "scan_status remains DELETED; task retries on next scheduled run. "
                "NO audit entry written — the S3 file may still exist.",
                doc_pk,
            )
            # Do NOT update DB, do NOT write audit, do NOT fire signal.
            # The document re-appears in pending_hard_delete() next run because
            # scan_status is still DELETED. Retry is automatic.
            return
    else:
        # storage_key already empty — storage cleared in a previous run.
        logger.info(
            "hard_delete: doc pk=%r has empty storage_key; " "skipping S3 delete (idempotent).",
            doc_pk,
        )

    # ── Step 3: Atomically null _storage_key + set scan_status=PURGED (C-3) ──
    # Wrapped in atomic() so the DB update and RECORD_PURGED audit commit together.
    # PIPEDA 4.5.3: a crash between the update and the audit would leave a purged
    # document with no audit trail — wrapping them in one transaction prevents that.
    # Conditional update on scan_status=DELETED (not just pk) prevents a concurrent
    # worker that also processed this document from writing a duplicate RECORD_PURGED
    # audit entry. rows_updated == 0 means another worker already committed PURGED.
    _doc_pk_s = doc_pk
    _cat_slug = category_slug

    with transaction.atomic():
        rows_updated = Document.objects.filter(
            pk=document.pk,
            scan_status=Document.ScanStatus.DELETED,
        ).update(**{"_storage_key": "", "scan_status": Document.ScanStatus.PURGED})

        # Update the caller's in-memory instance.
        document._storage_key = ""
        document.scan_status = Document.ScanStatus.PURGED

        if rows_updated == 0:
            # A concurrent worker already committed the PURGED state.
            # Skip audit and signal to prevent duplicate immutable entries.
            logger.info(
                "hard_delete: doc pk=%r PURGED state already committed by a concurrent "
                "worker; skipping audit and signal (idempotent).",
                doc_pk,
            )
            return

        # ── Step 4: Write audit INSIDE atomic (PIPEDA 4.5.3) ─────────────────
        # record_event() inside the same atomic() block so the DB state change
        # and the audit entry commit together. A crash between them would leave
        # the document purged with no audit trail — this prevents that gap.
        # PIPEDA: event_detail contains ONLY cleared_at — no storage_key, no PII.
        record_event(
            event_type=AuditEventType.RECORD_PURGED,
            resource_type="documents.Document",
            resource_id=doc_pk,
            event_detail={
                "cleared_at": cleared_at.isoformat(),
                # PIPEDA: NO original_filename, NO storage_key value, NO uploader PII.
            },
        )

        # ── Step 5: Fire signal via on_commit AFTER transaction commits ───────
        # Consistent with soft_delete / apply_legal_hold / release_legal_hold.
        # on_commit() ensures the signal fires only after the DB write is durable.
        # send_robust() never raises; inspect return values for receiver exceptions.
        # PIPEDA: kwargs contain ONLY document_pk and category_slug.
        def _fire_hard_deleted(
            _pk=_doc_pk_s,  # noqa: ANN001
            _slug=_cat_slug,  # noqa: ANN001
        ) -> None:
            results = document_hard_deleted.send_robust(
                sender=Document,
                document_pk=_pk,
                category_slug=_slug,
            )
            for _rcvr, _resp in results:
                if isinstance(_resp, Exception):
                    logger.error(
                        "hard_delete: signal receiver %r raised: %r",
                        _rcvr,
                        _resp,
                    )

        transaction.on_commit(_fire_hard_deleted)

    logger.info(
        "hard_delete: Document pk=%r storage cleared, scan_status=PURGED. "
        "DB row retained for audit trail per spec §11.2.",
        doc_pk,
    )


def mark_purpose_fulfilled(
    *,
    document: Document,
    actor: User,
) -> Document:
    """
    For transitory documents: perform soft-delete inline once purpose is fulfilled.

    Transitory records (LAC Disposition Authorization #2016/001) are documents
    created for a specific purpose that have no fixed calendar expiry — they are
    destroyed once the purpose for which they were created is met.

    Example: PIPEDA Data Export packages — delivered to the citizen, then
    destroyed. Setting a calendar expires_at at creation would be premature
    (what if the export takes days to generate?).

    This function performs the entire disposal inline inside a single
    transaction.atomic() + select_for_update() block (M-2 fix):
      1. Validates the category is transitory (raises ValueError if not).
      2. Acquires a SELECT FOR UPDATE lock on the document row.
      3. Re-validates legal_hold, deleted_at, and is_transitory under lock
         (TOCTOU guard — concurrent processes may have changed state).
      4. Sets expires_at, deleted_at, scan_status, deleted_by, and
         deletion_reason in one atomic save() with update_fields.
         (M-2: expires_at and the soft-delete fields are written together in
         a single save — there is no separate expires_at update step.)
      5. Writes a RECORD_DELETED audit entry inside the same atomic block
         (PIPEDA 4.5.3 — state change and audit trail commit together).
      6. Schedules document_soft_deleted signal via transaction.on_commit()
         (fires only after commit, never on rollback; send_robust() used).

    Per spec §11.4, this replaces the direct soft_delete() call for transitory
    docs. Merging both the expires_at update and the deletion into one continuous
    lock eliminates the TOCTOU race that existed when they used separate
    atomic() blocks.

    Args:
        document: The transitory Document to dispose.
        actor:    The User performing the action (for audit trail).

    Returns:
        The soft-deleted Document instance (reflects saved state).

    Raises:
        ValueError: document.category.is_transitory is False.
        ValueError: document.legal_hold is True (raised directly by this function).
        ValueError: document is already soft-deleted.
    """
    if not document.category.is_transitory:
        raise ValueError(
            f"mark_purpose_fulfilled() called on non-transitory document {document.pk}. "
            f"Category {document.category.slug!r} has is_transitory=False. "
            "Only transitory categories (LAC DA #2016/001) may be disposed via this function."
        )

    # ── Pre-lock fast-path (avoids unnecessary SELECT FOR UPDATE on obvious failures) ──
    # Mirrors soft_delete()'s pre-lock guards. The inside-lock guards below
    # re-verify these same conditions under the lock for TOCTOU safety.
    if document.legal_hold:
        raise ValueError(
            f"Document {document.pk} is on legal hold and cannot be disposed. "
            "Remove the legal hold before marking purpose fulfilled."
        )
    if document.deleted_at is not None:
        raise ValueError(
            f"Document {document.pk} is already soft-deleted "
            f"(deleted_at={document.deleted_at.isoformat()}). "
            "Cannot mark purpose fulfilled on an already-disposed document."
        )

    # M-2 fix: Hold a SINGLE select_for_update() lock across BOTH the expires_at
    # update AND the soft-delete (deleted_at + scan_status=DELETED + audit + signal).
    #
    # Previous code released the lock after updating expires_at, then called
    # soft_delete() which opened its OWN atomic()/select_for_update() block.
    # The gap between those two separate atomic blocks was a TOCTOU race: a
    # concurrent Celery worker running run_disposal_schedule() could observe
    # expires_at=now() and race to call soft_delete() in that gap, leaving the
    # subsequent soft-delete attempt to hit the "already deleted" guard. Merging
    # both operations into one continuous lock eliminates this window entirely —
    # no other process can observe an intermediate state where expires_at is set
    # but the document is not yet deleted.
    from apps.audit.models import AuditEventType
    from apps.documents.models import Document
    from apps.documents.signals import document_soft_deleted

    with transaction.atomic():
        doc = (
            Document.objects.select_related("category")
            .select_for_update(of=("self",))
            .get(pk=document.pk)
        )

        # Capture now AFTER acquiring the lock so the timestamp is consistent
        # with the locked DB row (eliminates cosmetic clock skew — LOW-4).
        now = timezone.now()

        # Re-validate under lock — concurrent process may have changed state.
        if doc.deleted_at is not None:
            raise ValueError(
                f"Document {doc.pk} is already soft-deleted "
                f"(deleted_at={doc.deleted_at.isoformat()}). "
                "Cannot mark purpose fulfilled on an already-disposed document."
            )
        if doc.legal_hold:
            raise ValueError(
                f"Document {doc.pk} is on legal hold and cannot be disposed. "
                "Remove the legal hold before marking purpose fulfilled."
            )
        if not doc.category.is_transitory:
            raise ValueError(
                f"mark_purpose_fulfilled() called on non-transitory document {doc.pk}. "
                f"Category {doc.category.slug!r} has is_transitory=False."
            )

        # Set expires_at AND perform the soft-delete in the same locked transaction.
        # This closes the TOCTOU window — no other process can observe expires_at=now()
        # and race to call soft_delete() before we complete the deletion ourselves.
        doc.expires_at = now
        doc.deleted_at = now
        doc.scan_status = (
            Document.ScanStatus.DELETED
        )  # CONTRACT: required for pending_hard_delete()
        doc.deleted_by = actor
        doc.deletion_reason = "transitory_purpose_fulfilled"
        doc.save(
            update_fields=[
                "expires_at",
                "deleted_at",
                "scan_status",
                "deleted_by",
                "deletion_reason",
                "updated_at",
            ]
        )

        # Audit: purpose fulfilled + soft-delete recorded atomically (PIPEDA 4.5.3).
        # PIPEDA: NO original_filename, NO storage_key in event_detail.
        record_event(
            event_type=AuditEventType.RECORD_DELETED,
            actor_id=str(actor.pk),
            resource_type="documents.Document",
            resource_id=str(doc.pk),
            event_detail={
                "deletion_reason": "transitory_purpose_fulfilled",
                "deleted_by_pk": actor.pk,
            },
        )

        # Signal via on_commit — fires only after this transaction commits, never
        # on rollback. send_robust() inspects receiver exceptions and logs at ERROR.
        # PIPEDA: kwargs contain ONLY document_pk and deleted_by_id — no PII.
        _doc_pk_s = str(doc.pk)
        _actor_pk = actor.pk

        def _fire_soft_deleted(
            _pk=_doc_pk_s,  # noqa: ANN001
            _dbpk=_actor_pk,  # noqa: ANN001
        ) -> None:
            results = document_soft_deleted.send_robust(
                sender=Document,
                document_pk=_pk,
                deleted_by_id=_dbpk,
            )
            for _receiver, _response in results:
                if isinstance(_response, Exception):
                    logger.error(
                        "mark_purpose_fulfilled: signal receiver %r raised: %r",
                        _receiver,
                        _response,
                    )

        transaction.on_commit(_fire_soft_deleted)

    # Update the caller's in-memory instance to reflect the saved state.
    document.expires_at = doc.expires_at
    document.deleted_at = doc.deleted_at
    document.scan_status = doc.scan_status
    document.deleted_by = doc.deleted_by
    document.deletion_reason = doc.deletion_reason

    logger.info(
        "mark_purpose_fulfilled: doc pk=%r transitory purpose fulfilled; "
        "soft-deleted within single lock. actor_pk=%r",
        str(document.pk),
        actor.pk,
    )

    return document


def apply_legal_hold(
    *,
    document: Document,
    set_by: User,
    reason: str,
) -> Document:
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
            raise ValueError(f"Document {doc.pk} is already on legal hold (concurrent race).")

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

        # H-2: audit INSIDE atomic — state change and audit commit together.
        # C-4 fix: use dedicated LEGAL_HOLD_APPLIED event type (not generic STATUS_CHANGED).
        # PIPEDA: event_detail contains only legal_hold flag, reason, and actor pk — no PII.
        record_event(
            event_type=AuditEventType.LEGAL_HOLD_APPLIED,
            actor_id=str(set_by.pk),
            resource_type="documents.Document",
            resource_id=str(doc.pk),
            event_detail={
                "legal_hold": True,
                "reason": reason,
                "set_by_pk": set_by.pk,
            },
        )

        # H-2: signal via on_commit — fires only if transaction commits.
        # H-3: send_robust() return values inspected; receiver exceptions logged.
        # PIPEDA: kwargs contain ONLY document_pk, legal_hold flag, set_by_id (int).
        _doc_pk_s = str(doc.pk)
        _set_by_pk = set_by.pk

        def _fire_legal_hold_applied(
            _pk=_doc_pk_s,  # noqa: ANN001
            _sbpk=_set_by_pk,  # noqa: ANN001
        ) -> None:
            results = document_legal_hold_changed.send_robust(
                sender=Document,
                document_pk=_pk,
                legal_hold=True,
                set_by_id=_sbpk,
            )
            for _receiver, _response in results:
                if isinstance(_response, Exception):
                    logger.error(
                        "apply_legal_hold: signal receiver %r raised: %r",
                        _receiver,
                        _response,
                    )

        transaction.on_commit(_fire_legal_hold_applied)

    # Update caller's in-memory instance.
    document.legal_hold = True
    document.legal_hold_reason = reason
    document.legal_hold_set_by = set_by

    logger.info(
        "apply_legal_hold: Document pk=%r legal hold applied. set_by_pk=%r",
        str(document.pk),
        set_by.pk,
    )

    return document


def release_legal_hold(
    *,
    document: Document,
    released_by: User,
    reason: str = "",
) -> Document:
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
      6. Writes audit: AuditEventType.LEGAL_HOLD_RELEASED.
      7. Fires document_legal_hold_changed signal (legal_hold=False).

    Args:
        document:     The Document whose hold is to be released.
        released_by:  User releasing the hold (must have manage_legal_hold).
        reason:       Optional non-PII description of why the hold was released
                      (e.g. "ATIP request closed"). Stored in audit event_detail.
                      Do NOT include personal information.

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
        raise ValueError(f"Document {document.pk} is not on legal hold. Nothing to release.")

    with transaction.atomic():
        doc = Document.objects.select_for_update().get(pk=document.pk)

        if not doc.legal_hold:
            raise ValueError(f"Document {doc.pk} is not on legal hold (concurrent race).")

        doc.legal_hold = False
        # NOTE: legal_hold_reason and legal_hold_set_by are preserved for audit
        # trail — they document WHO set the hold and WHY, even after release.
        doc.save(update_fields=["legal_hold", "updated_at"])

        # H-2: audit INSIDE atomic — state change and audit commit together.
        # C-4 fix: use dedicated LEGAL_HOLD_RELEASED event type (not generic STATUS_CHANGED).
        # PIPEDA: event_detail contains only legal_hold flag, actor pk, and reason — no PII.
        record_event(
            event_type=AuditEventType.LEGAL_HOLD_RELEASED,
            actor_id=str(released_by.pk),
            resource_type="documents.Document",
            resource_id=str(doc.pk),
            event_detail={
                "legal_hold": False,
                "released_by_pk": released_by.pk,
                "release_reason": reason or "",
            },
        )

        # H-2: signal via on_commit — fires only if transaction commits.
        # H-3: send_robust() return values inspected; receiver exceptions logged.
        _doc_pk_s = str(doc.pk)
        _released_by_pk = released_by.pk

        def _fire_legal_hold_released(
            _pk=_doc_pk_s,  # noqa: ANN001
            _rbpk=_released_by_pk,  # noqa: ANN001
        ) -> None:
            results = document_legal_hold_changed.send_robust(
                sender=Document,
                document_pk=_pk,
                legal_hold=False,
                set_by_id=_rbpk,
            )
            for _receiver, _response in results:
                if isinstance(_response, Exception):
                    logger.error(
                        "release_legal_hold: signal receiver %r raised: %r",
                        _receiver,
                        _response,
                    )

        transaction.on_commit(_fire_legal_hold_released)

    # Update caller's in-memory instance.
    document.legal_hold = False

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
    qs = DocumentAccessToken.objects.filter(Q(expires_at__lte=now) | Q(used_at__isnull=False))

    if dry_run:
        count = qs.count()
        logger.info(
            "purge_expired_tokens (dry_run): %d token(s) would be deleted.",
            count,
        )
        return count

    # Initialise count before the atomic block so it is always defined even if
    # qs.delete() raises and the block rolls back (prevents UnboundLocalError
    # in the logger.info() call below).
    count = 0

    # H-7: wrap delete + audit in atomic so they commit together.
    # If record_event() fails the delete is also rolled back — we must never
    # delete records without a corresponding audit trail (PIPEDA 4.5.3).
    with transaction.atomic():
        _, deleted_counts = qs.delete()
        # Django's bulk delete returns a dict like:
        #   {"documents.DocumentAccessToken": 17, ...}
        # Use .get() with the full app_label.ModelName key; fall back to 0.
        count = deleted_counts.get("documents.DocumentAccessToken", 0)

        # H-7: PIPEDA 4.5.3 requires a disposal event be logged after bulk deletion.
        # event_detail contains ONLY a count — no token values, no IP addresses.
        if count > 0:
            from apps.audit.models import AuditEventType

            record_event(
                event_type=AuditEventType.RECORD_PURGED,
                resource_type="documents.DocumentAccessToken",
                event_detail={
                    "count": count,
                    "reason": "token_expired_or_used",
                },
            )

    logger.info("purge_expired_tokens: deleted %d expired/used token(s).", count)
    return count
