"""
Document Management Building Block — Celery tasks.

Tasks in this module are routed to the ``documents`` Celery queue via
the CELERY_TASK_ROUTES setting:
    "apps.documents.tasks.*": {"queue": "documents"}

Task design invariants:
  - All tasks use ``acks_late=True`` and ``reject_on_worker_lost=True`` to
    guarantee at-least-once delivery. Tasks MUST be idempotent.
  - ``select_for_update()`` is used inside ``atomic()`` before any status
    check to prevent race conditions under concurrent workers.
  - PII is NEVER written to task arguments or log messages.
    Use document.pk (UUID str) only. Never log email, original_filename, or
    storage_key.
  - Celery task exceptions are logged at ERROR level; they do NOT propagate
    PII to Sentry/logging sinks.

Wave 2 tasks:
  - scan_document             — dev bypass + Wave 3 ClamAV stub
  - cleanup_stale_pending_uploads — purge docs stuck in PENDING_UPLOAD

Wave 3 tasks (stubs, signatures fixed):
  - (ClamAV integration wired into scan_document)

Wave 5 tasks (stubs, signatures fixed):
  - run_disposal_schedule     — daily Celery Beat: soft-delete expired docs
  - run_hard_delete_schedule  — daily Celery Beat: hard-delete past grace period
  - purge_expired_tokens      — daily Celery Beat: delete expired access tokens
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Wave 2: scan_document
# ─────────────────────────────────────────────────────────────────────────────


@shared_task(
    bind=True,
    max_retries=5,
    default_retry_delay=30,
    queue="documents",
    # at-least-once delivery: ack only after the task body returns without error
    acks_late=True,
    # if the worker process is lost mid-task (OOM kill, SIGKILL), requeue the task
    reject_on_worker_lost=True,
)
def scan_document(self, doc_pk: str) -> None:
    """
    Run a ClamAV virus scan on a newly uploaded document.

    The task is dispatched by confirm_upload() inside an on_commit() hook,
    guaranteeing the DB row is committed before the worker picks it up.

    Status lifecycle:
      PENDING_UPLOAD → SCANNING (set by confirm_upload before dispatch)
      SCANNING → ACTIVE        (set here if clean / dev bypass)
      SCANNING → QUARANTINED   (set here if threat detected — Wave 3)

    Idempotency:
      If doc.scan_status is not SCANNING when this task runs (e.g. already
      processed by a concurrent worker on retry), the task returns immediately
      without re-scanning.

    Dev bypass (CLAMAV_HOST="" AND CLAMAV_REQUIRED=False):
      In development, ClamAV is typically not running. When both conditions
      hold, the task marks the document ACTIVE immediately with
      scan_engine_result="DEV_BYPASS" so the development cycle is not blocked.
      This bypass path is NEVER active in production (where CLAMAV_REQUIRED=True
      or CLAMAV_HOST is set).

    PIPEDA:
      - event_detail in the THREAT_DETECTED audit entry contains ONLY doc pk.
      - Quarantine signal kwargs contain ONLY doc pk and scan_engine_result.
      - No uploader identity, no original_filename, no storage_key in any log.

    Args:
        doc_pk: UUID string of the Document to scan.

    Raises:
        Document.DoesNotExist:  Retried up to max_retries if not found (may
                                not yet be visible across DB replicas).
        NotImplementedError:    Full ClamAV implementation deferred to Wave 3.
    """
    from django.conf import settings

    from apps.documents.models import Document
    from apps.documents.signals import document_quarantined, document_scan_clean

    civicos: dict = getattr(settings, "CIVICOS", {})
    clamav_required: bool = civicos.get("CLAMAV_REQUIRED", False)
    clamav_host: str = civicos.get("CLAMAV_HOST", "")

    # ── Dev bypass: no ClamAV configured and not required ────────────────────
    if not clamav_host and not clamav_required:
        _mark_document_active_dev_bypass(doc_pk=doc_pk)
        return

    # ── ClamAV required but not yet implemented (Wave 3) ─────────────────────
    # In production, CLAMAV_REQUIRED=True and CLAMAV_HOST is set; this branch
    # will be replaced by the full ClamAV integration in Wave 3.
    # Until then, we raise to prevent accidentally shipping a no-scan path.
    raise NotImplementedError(
        "scan_document — full ClamAV integration implemented in Wave 3. "
        "Set CLAMAV_REQUIRED=False and CLAMAV_HOST='' for development use."
    )


def _mark_document_active_dev_bypass(doc_pk: str) -> None:
    """
    Mark a document ACTIVE without running ClamAV (development only).

    This function is extracted for testability. It MUST NOT be called in
    production (where CLAMAV_REQUIRED=True).

    Uses select_for_update() inside atomic() for safe status transition.
    Idempotent: if already past SCANNING status, returns without action.
    """
    from apps.documents.models import Document
    from apps.documents.signals import document_scan_clean

    with transaction.atomic():
        try:
            doc = Document.objects.select_for_update().get(pk=doc_pk)
        except Document.DoesNotExist:
            logger.warning(
                "scan_document (dev bypass): Document pk=%r not found; "
                "may be a race — will not retry from this helper.",
                doc_pk,
            )
            return

        # Idempotency guard: only transition from SCANNING → ACTIVE
        if doc.scan_status != Document.ScanStatus.SCANNING:
            logger.info(
                "scan_document (dev bypass): doc pk=%r already in status %r; "
                "skipping (idempotent).",
                doc_pk,
                doc.scan_status,
            )
            return

        doc.scan_status = Document.ScanStatus.ACTIVE
        doc.scan_completed_at = timezone.now()
        doc.scan_engine_result = "DEV_BYPASS"
        doc.save(
            update_fields=[
                "scan_status",
                "scan_completed_at",
                "scan_engine_result",
                "updated_at",
            ]
        )

    # Fire signal outside the lock (send_robust never raises).
    # PIPEDA: kwargs contain only doc.pk — no uploader identity.
    document_scan_clean.send_robust(
        sender=Document,
        document_pk=str(doc_pk),
    )

    logger.info(
        "scan_document (dev bypass): doc pk=%r marked ACTIVE.",
        doc_pk,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Wave 2: cleanup_stale_pending_uploads
# ─────────────────────────────────────────────────────────────────────────────

# Documents stuck in PENDING_UPLOAD for longer than this are considered
# abandoned (browser closed, presigned URL expired without a POST, etc.).
# 15 minutes == presigned POST TTL (DOCUMENT_PRESIGNED_POST_TTL_SECONDS=900).
# We add a 5-minute grace margin, so 20 minutes total.
_STALE_PENDING_MINUTES: int = 20


@shared_task(
    bind=True,
    queue="documents",
    acks_late=True,
    reject_on_worker_lost=True,
    # No retries — this is a periodic cleanup. If it fails, the next run
    # (scheduled by Celery Beat) will clean up.
    max_retries=0,
)
def cleanup_stale_pending_uploads(self) -> int:
    """
    Delete Document records stuck in PENDING_UPLOAD beyond the presigned URL TTL.

    Scheduled by Celery Beat to run every 30 minutes.

    A document in PENDING_UPLOAD state means:
      - validate_upload_request() was called and the Document row was created.
      - The browser never POSTed the file (tab closed, error, presigned URL
        expired), OR confirm_upload() was never called.

    After DOCUMENT_PRESIGNED_POST_TTL_SECONDS (900s = 15 min) + a 5-minute grace
    margin, the row is safe to delete — the presigned URL is expired and the
    S3 object (if any) will be purged by S3 lifecycle rules.

    This task performs a HARD DELETE of the Document row (not a soft-delete),
    because the document was never confirmed and never had valid content.
    No file bytes were written by the service layer to the storage key
    (the browser was responsible for the upload, and it never completed).
    No audit log entry is written for stale abandoned uploads because no
    data transfer occurred — there is nothing to audit.

    PIPEDA: No PII is logged. Only counts are reported.

    Returns:
        Number of stale Document rows deleted.
    """
    from django.conf import settings

    from apps.documents.models import Document

    civicos: dict = getattr(settings, "CIVICOS", {})
    ttl_seconds: int = civicos.get("DOCUMENT_PRESIGNED_POST_TTL_SECONDS", 900)
    # Add a 5-minute grace margin beyond the presigned URL TTL
    grace_seconds = ttl_seconds + (5 * 60)
    cutoff = timezone.now() - timedelta(seconds=grace_seconds)

    qs = Document.objects.filter(
        scan_status=Document.ScanStatus.PENDING_UPLOAD,
        created_at__lte=cutoff,
    )

    count, _ = qs.delete()

    if count:
        logger.info(
            "cleanup_stale_pending_uploads: deleted %d stale PENDING_UPLOAD records "
            "(older than %d seconds).",
            count,
            grace_seconds,
        )
    else:
        logger.debug(
            "cleanup_stale_pending_uploads: no stale records found.",
        )

    return count


# ─────────────────────────────────────────────────────────────────────────────
# Wave 5 stubs — signatures fixed; implementations deferred
# ─────────────────────────────────────────────────────────────────────────────


@shared_task(
    bind=True,
    queue="documents",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
    default_retry_delay=60,
)
def run_disposal_schedule(self) -> None:
    """
    Celery Beat task: soft-delete all documents past their max retention date.

    Runs daily. Finds all documents matching DocumentQuerySet.pending_disposal()
    (expires_at AND retain_until both in the past, no legal hold, not already
    deleted) and calls retention.soft_delete() for each.

    STUB: Full implementation in Wave 5.
    """
    raise NotImplementedError("run_disposal_schedule — implemented in Wave 5")


@shared_task(
    bind=True,
    queue="documents",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
    default_retry_delay=60,
)
def run_hard_delete_schedule(self) -> None:
    """
    Celery Beat task: irreversibly hard-delete soft-deleted documents past
    the 30-day grace period.

    Finds all documents matching DocumentQuerySet.pending_hard_delete() and
    calls retention.hard_delete() for each.

    NIST SP 800-88: hard deletion must be irreversible. S3 delete occurs
    BEFORE DB row delete. If S3 delete fails, DB row is preserved.

    STUB: Full implementation in Wave 5.
    """
    raise NotImplementedError("run_hard_delete_schedule — implemented in Wave 5")


@shared_task(
    bind=True,
    queue="documents",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=0,
)
def run_purge_expired_tokens(self) -> None:
    """
    Celery Beat task: hard-delete expired and used DocumentAccessTokens.

    Tokens have a 5-minute TTL. After expiry or single-use, they are purgeable
    immediately. There is no grace period.

    STUB: Full implementation in Wave 5.
    """
    raise NotImplementedError("run_purge_expired_tokens — implemented in Wave 5")
