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
  - scan_document             — dev bypass + ClamAV integration
  - cleanup_stale_pending_uploads — purge docs stuck in PENDING_UPLOAD

Wave 3 tasks:
  - ClamAV integration wired into scan_document (replaces Wave 2 stub)
  - run_purge_expired_tokens  — daily Celery Beat: delete expired access tokens

Wave 5 tasks (stubs, signatures fixed):
  - run_disposal_schedule     — daily Celery Beat: soft-delete expired docs
  - run_hard_delete_schedule  — daily Celery Beat: hard-delete past grace period
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import Task, shared_task
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Private sentinel exception — distinguishes storage read failures (permanent)
# from ClamAV/network failures (transient). Using a custom class avoids the
# Python 3 OSError hierarchy problem where ConnectionError is a subclass of
# IOError/OSError, which would otherwise cause `except IOError` to incorrectly
# catch ClamAV daemon connection errors and quarantine instead of retrying.
# ─────────────────────────────────────────────────────────────────────────────


class _StorageReadError(Exception):
    """
    Internal sentinel: the document file cannot be opened from storage.

    Raised by ``_scan_with_clamav`` when ``default_storage.open()`` fails.
    Caught in ``scan_document`` to trigger immediate quarantine rather than
    exhausting the retry budget on a permanently-missing file.

    NOT a subclass of OSError/IOError so it is not confused with
    pyclamd's ConnectionError (which is an OSError subclass).
    """


def _quarantine_on_scan_failure(doc_pk: str, exc: BaseException) -> None:
    """
    Transition a document from SCANNING → QUARANTINED after scan failure.

    Called by ``_ScanDocumentTask.on_failure()`` when ``scan_document``
    exhausts all retries (``MaxRetriesExceededError``) or raises an
    unhandled exception. Documents left in SCANNING indefinitely are a
    security gap — no virus scan ran but the document appears in-progress
    forever. Forcing QUARANTINED surfaces the failure to admins and blocks
    citizen access until manual review.

    PIPEDA: no PII in logs or signal kwargs. Only doc_pk and exception
    type name are recorded.
    """
    from apps.documents.models import Document
    from apps.documents.signals import document_quarantined

    try:
        with transaction.atomic():
            try:
                doc = Document.objects.select_for_update().get(pk=doc_pk)
            except Document.DoesNotExist:
                logger.warning(
                    "_quarantine_on_scan_failure: doc pk=%r not found; "
                    "may have been deleted by cleanup task.",
                    doc_pk,
                )
                return

            if doc.scan_status != Document.ScanStatus.SCANNING:
                # Already transitioned by another code path (e.g. concurrent retry).
                return

            # Unwrap MaxRetriesExceededError to get the root cause exception type.
            from celery.exceptions import MaxRetriesExceededError as _MaxRetriesExceededError
            if isinstance(exc, _MaxRetriesExceededError) and exc.__cause__ is not None:
                exc = exc.__cause__
            scan_engine_result = f"SCAN_FAILURE:{type(exc).__name__}"
            doc.scan_status = Document.ScanStatus.QUARANTINED
            doc.scan_engine_result = scan_engine_result
            doc.scan_completed_at = timezone.now()
            doc.save(
                update_fields=[
                    "scan_status",
                    "scan_engine_result",
                    "scan_completed_at",
                    "updated_at",
                ]
            )

        document_quarantined.send_robust(
            sender=Document,
            document_pk=str(doc_pk),
            scan_engine_result=scan_engine_result,
        )
        logger.error(
            "scan_document: scan failed after exhausting retries for doc pk=%r; "
            "transitioned to QUARANTINED. Exception type: %s",
            doc_pk,
            type(exc).__name__,
        )
    except Exception:
        logger.exception(
            "_quarantine_on_scan_failure: on_failure quarantine transition "
            "itself failed for doc pk=%r",
            doc_pk,
        )


class _ScanDocumentTask(Task):
    """
    Custom Celery Task base class for ``scan_document``.

    Adds an ``on_failure`` hook that transitions the document from
    SCANNING → QUARANTINED when all retries are exhausted, preventing
    documents from being permanently stuck in the SCANNING state.
    """

    def on_failure(
        self,
        exc: BaseException,
        task_id: str,
        args: tuple,
        kwargs: dict,
        einfo: object,
    ) -> None:
        """Quarantine the document when scan_document exhausts all retries."""
        doc_pk: str | None = args[0] if args else kwargs.get("doc_pk")
        if doc_pk:
            _quarantine_on_scan_failure(doc_pk=doc_pk, exc=exc)
        super().on_failure(exc, task_id, args, kwargs, einfo)


# ─────────────────────────────────────────────────────────────────────────────
# Wave 2: scan_document
# ─────────────────────────────────────────────────────────────────────────────


@shared_task(
    bind=True,
    base=_ScanDocumentTask,  # M-7: on_failure → quarantine when retries exhausted
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
        # Production safety guard: an empty CLAMAV_HOST with CLAMAV_REQUIRED=False
        # outside of a DEBUG or TESTING environment means every document would be
        # marked ACTIVE with scan_engine_result="DEV_BYPASS" — no virus scan
        # would ever run. Fail loudly so operators fix the misconfiguration rather
        # than ship an unscanned-document pipeline to production silently.
        if not (settings.DEBUG or getattr(settings, "TESTING", False)):
            raise RuntimeError(
                "scan_document: DEV_BYPASS activated in a non-DEBUG, non-TESTING "
                "environment. Set CLAMAV_HOST and CLAMAV_REQUIRED=True for "
                "production, or ensure DEBUG=True / TESTING=True for development."
            )
        try:
            _mark_document_active_dev_bypass(doc_pk=doc_pk)
        except Document.DoesNotExist as exc:
            # M-6: Document not visible yet (DB replica lag). Retry with
            # exponential backoff so the task can find it once propagated.
            countdown = (2 ** self.request.retries) * 30
            logger.warning(
                "scan_document: doc pk=%r not found (possible replica lag); "
                "retry %d/%d in %ds.",
                doc_pk,
                self.request.retries + 1,
                self.max_retries,
                countdown,
            )
            raise self.retry(exc=exc, countdown=countdown)
        return

    # ── Wave 3: Full ClamAV integration ──────────────────────────────────────
    # Triggered when CLAMAV_HOST is set OR CLAMAV_REQUIRED=True.
    # In production: CLAMAV_REQUIRED=True and CLAMAV_HOST is set.
    # In dev with explicit ClamAV: CLAMAV_HOST set, CLAMAV_REQUIRED may be False.

    # Step 1: Quick idempotency check under lock (brief atomic block).
    # We capture storage_key here and release the lock before the ClamAV scan
    # (which may be slow). Holding a DB lock during network I/O would block
    # concurrent workers unnecessarily.
    try:
        with transaction.atomic():
            doc = Document.objects.select_for_update().get(pk=doc_pk)
            if doc.scan_status != Document.ScanStatus.SCANNING:
                logger.info(
                    "scan_document: doc pk=%r already in status %r; skipping (idempotent).",
                    doc_pk,
                    doc.scan_status,
                )
                return
            # Capture the storage key while we hold the row lock.
            storage_key = doc.storage_key
    except Document.DoesNotExist as exc:
        # DB replica lag: document not yet visible. Retry with backoff (M-6 pattern).
        countdown = (2 ** self.request.retries) * 30
        logger.warning(
            "scan_document: doc pk=%r not found (possible replica lag); "
            "retry %d/%d in %ds.",
            doc_pk,
            self.request.retries + 1,
            self.max_retries,
            countdown,
        )
        raise self.retry(exc=exc, countdown=countdown)

    # Step 2: Perform the ClamAV scan OUTSIDE any transaction.
    # Network I/O must never hold a DB lock: slow ClamAV scans would block
    # every other worker that needs to write to the Document table.
    try:
        scan_result = _scan_with_clamav(
            storage_key=storage_key,
            civicos=civicos,
        )
    except _StorageReadError as exc:
        # Permanent storage failure — quarantine immediately, do not exhaust retries.
        # Using _StorageReadError (not IOError) avoids catching pyclamd's
        # ConnectionError, which is also an OSError/IOError subclass in Python 3.
        logger.error(
            "scan_document: storage read failed for doc pk=%r; quarantining immediately. "
            "Exception type: %s",
            doc_pk,
            type(exc).__name__,
        )
        _mark_document_quarantined_clamav(
            doc_pk=doc_pk,
            virus_name=f"STORAGE_ERROR:{type(exc).__name__}",
        )
        return
    except Exception as exc:
        # Transient ClamAV or other failure — retry with exponential backoff.
        countdown = (2 ** self.request.retries) * 30
        logger.warning(
            "scan_document: ClamAV scan failed for doc pk=%r; "
            "retry %d/%d in %ds. Exception type: %s",
            doc_pk,
            self.request.retries + 1,
            self.max_retries,
            countdown,
            type(exc).__name__,
        )
        raise self.retry(exc=exc, countdown=countdown)

    # Step 3: Persist the scan result.
    try:
        if scan_result == "OK":
            _mark_document_active_clamav(doc_pk=doc_pk)
        else:
            _mark_document_quarantined_clamav(doc_pk=doc_pk, virus_name=scan_result)
    except Exception as exc:
        countdown = (2 ** self.request.retries) * 30
        logger.warning(
            "scan_document: failed to persist scan result for doc pk=%r; "
            "retry %d/%d in %ds. scan_result=%r Exception type: %s",
            doc_pk,
            self.request.retries + 1,
            self.max_retries,
            countdown,
            scan_result,
            type(exc).__name__,
        )
        raise self.retry(exc=exc, countdown=countdown)


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
            # M-6: Re-raise so the outer scan_document task can retry via
            # self.retry(). Swallowing DoesNotExist here would permanently
            # lose the document if the DB row isn't visible yet due to
            # replica lag (the docstring's documented retry rationale).
            logger.warning(
                "scan_document (dev bypass): Document pk=%r not found; "
                "re-raising so the task retry mechanism can handle replica lag.",
                doc_pk,
            )
            raise

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
# Wave 3: ClamAV integration helpers
# ─────────────────────────────────────────────────────────────────────────────


def _scan_with_clamav(*, storage_key: str, civicos: dict) -> str:
    """
    Stream a document from storage into ClamAV and return the scan verdict.

    Uses pyclamd's ``instream()`` protocol to stream the file directly into
    the clamd daemon without writing bytes to the local filesystem.

    Args:
        storage_key: The storage path of the document (quarantine prefix).
        civicos:     The CIVICOS settings dict (for host/port/timeout).

    Returns:
        ``"OK"``   — file is clean.
        Virus name — threat detected (e.g. ``"Eicar-Test-Signature"``).

    Raises:
        IOError:                   File cannot be read from storage.
        Exception (ConnectionError-family): ClamAV daemon is unreachable.
            The caller (scan_document) catches any exception and retries.

    PIPEDA: storage_key is NEVER logged. Only exception type is logged.
    """
    import pyclamd
    from django.core.files.storage import default_storage

    host: str = civicos.get("CLAMAV_HOST", "localhost")
    port: int = civicos.get("CLAMAV_PORT", 3310)
    timeout: int = civicos.get("CLAMAV_TIMEOUT", 30)

    # Connect to clamd network socket.
    # pyclamd.ConnectionError is raised here (or on the first scan call) if
    # the daemon is not reachable.
    cd = pyclamd.ClamdNetworkSocket(host=host, port=port, timeout=timeout)

    # Stream file directly from storage into ClamAV via the instream protocol.
    # django-storages S3 backend implements Django's file storage interface,
    # so default_storage.open() works transparently for both backends.
    # pyclamd.instream() accepts any file-like object and reads in 8192-byte chunks,
    # avoiding loading the entire file into memory.
    # pyclamd.instream() returns:
    #   None                               → clean file
    #   {"stream": ("FOUND", "VirusName")} → threat detected
    #   {"stream": ("ERROR", "message")}   → clamd error during scan
    # Open the storage file separately so we can distinguish storage errors
    # (permanent — missing/inaccessible file) from ClamAV errors (transient).
    # ConnectionError is a subclass of OSError in Python 3, so we cannot use
    # `except IOError` without also catching ClamAV connection failures.
    # _StorageReadError is a private non-OSError class used as the sentinel.
    try:
        fh = default_storage.open(storage_key)
    except Exception as exc:
        raise _StorageReadError(
            f"_scan_with_clamav: cannot open storage file: {type(exc).__name__}"
        ) from exc

    with fh:
        result = cd.instream(fh)

    if result is None:
        # Clean: clamd found no threat.
        return "OK"

    stream_val = result.get("stream")
    if stream_val is None or not isinstance(stream_val, tuple) or len(stream_val) != 2:
        raise RuntimeError(
            f"_scan_with_clamav: ClamAV returned unexpected result format: {result!r}"
        )
    status, detail = stream_val
    if status == "FOUND":
        return detail  # virus name string (e.g. "Eicar-Test-Signature")

    # "ERROR" status from clamd — treat as a transient failure so the task
    # retries rather than permanently quarantining a potentially clean file.
    raise RuntimeError(f"ClamAV scan ERROR: {detail}")


def _mark_document_active_clamav(*, doc_pk: str) -> None:
    """
    Transition a document from SCANNING → ACTIVE after a clean ClamAV scan.

    Mirrors ``_mark_document_active_dev_bypass()`` but records the real
    ClamAV verdict ("OK") as scan_engine_result.

    Uses select_for_update() inside atomic() for safe status transition.
    Idempotent: if the document is no longer in SCANNING state (e.g. a
    concurrent worker already processed it), returns without action.

    PIPEDA: fires document_scan_clean with document_pk only — no PII.
    """
    from apps.documents.models import Document
    from apps.documents.signals import document_scan_clean

    with transaction.atomic():
        try:
            doc = Document.objects.select_for_update().get(pk=doc_pk)
        except Document.DoesNotExist:
            logger.warning(
                "_mark_document_active_clamav: doc pk=%r not found; "
                "may have been deleted by cleanup task.",
                doc_pk,
            )
            return

        if doc.scan_status != Document.ScanStatus.SCANNING:
            logger.info(
                "_mark_document_active_clamav: doc pk=%r already in status %r; "
                "skipping (idempotent).",
                doc_pk,
                doc.scan_status,
            )
            return

        doc.scan_status = Document.ScanStatus.ACTIVE
        doc.scan_completed_at = timezone.now()
        doc.scan_engine_result = "OK"
        doc.save(
            update_fields=[
                "scan_status",
                "scan_completed_at",
                "scan_engine_result",
                "updated_at",
            ]
        )

    # Fire signal outside the lock (send_robust never raises).
    # PIPEDA: kwargs contain only doc.pk — no uploader identity, no filename.
    document_scan_clean.send_robust(
        sender=Document,
        document_pk=str(doc_pk),
    )

    logger.info(
        "scan_document (ClamAV): doc pk=%r marked ACTIVE (scan: OK).",
        doc_pk,
    )


def _mark_document_quarantined_clamav(*, doc_pk: str, virus_name: str) -> None:
    """
    Transition a document from SCANNING → QUARANTINED after a ClamAV threat detection.

    The document row is retained for the audit trail. The storage object
    (in the quarantine prefix) is deleted to prevent access.

    Uses select_for_update() inside atomic() for safe status transition.
    Idempotent: if the document is no longer in SCANNING state, returns without action.

    PIPEDA:
      - scan_engine_result records only the virus name from ClamAV.
      - document_quarantined signal kwargs: document_pk + scan_engine_result only.
      - No uploader PII, no original_filename, no storage_key in any log or signal.
    """
    from apps.documents.models import Document
    from apps.documents.signals import document_quarantined

    scan_engine_result = f"FOUND: {virus_name}"

    with transaction.atomic():
        try:
            doc = Document.objects.select_for_update().get(pk=doc_pk)
        except Document.DoesNotExist:
            logger.warning(
                "_mark_document_quarantined_clamav: doc pk=%r not found; "
                "may have been deleted by cleanup task.",
                doc_pk,
            )
            return

        if doc.scan_status != Document.ScanStatus.SCANNING:
            logger.info(
                "_mark_document_quarantined_clamav: doc pk=%r already in status %r; "
                "skipping (idempotent).",
                doc_pk,
                doc.scan_status,
            )
            return

        # Capture storage key before status change, for the S3 delete below.
        storage_key = doc.storage_key

        doc.scan_status = Document.ScanStatus.QUARANTINED
        doc.scan_completed_at = timezone.now()
        doc.scan_engine_result = scan_engine_result
        doc.save(
            update_fields=[
                "scan_status",
                "scan_completed_at",
                "scan_engine_result",
                "updated_at",
            ]
        )

    # Delete the file from quarantine storage (irreversible — the file is infected).
    # Performed OUTSIDE the transaction to avoid holding the DB lock during storage I/O.
    # If this fails, the document is still QUARANTINED and inaccessible to users;
    # the stale storage object will be purged by S3 lifecycle rules or a future
    # cleanup task.
    try:
        from django.core.files.storage import default_storage

        default_storage.delete(storage_key)
    except Exception:
        logger.exception(
            "_mark_document_quarantined_clamav: storage delete failed for doc pk=%r. "
            "Exception type logged above.",
            doc_pk,
        )

    # Fire signal outside the lock. send_robust never raises.
    # PIPEDA: kwargs contain ONLY document_pk and scan_engine_result.
    # NO uploader identity, NO original_filename.
    document_quarantined.send_robust(
        sender=Document,
        document_pk=str(doc_pk),
        scan_engine_result=scan_engine_result,
    )

    logger.error(
        "scan_document (ClamAV): doc pk=%r QUARANTINED. Threat detected. "
        "Exception type: FOUND (ClamAV result recorded in scan_engine_result).",
        doc_pk,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Wave 2: cleanup_stale_pending_uploads
# ─────────────────────────────────────────────────────────────────────────────

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
def run_purge_expired_tokens(self) -> int:
    """
    Celery Beat task: hard-delete expired and used DocumentAccessTokens.

    Tokens have a 5-minute TTL (CIVICOS['DOCUMENT_ACCESS_TOKEN_TTL_SECONDS']).
    Expired tokens and used tokens are eligible for deletion after a 24-hour
    buffer (per spec §19 — "expired > 24 hours ago") to allow debugging of
    any edge cases before permanent deletion.

    No grace period beyond 24 hours: tokens are intentionally short-lived and
    contain no data that requires retention.

    Returns:
        Number of tokens deleted.

    PIPEDA: no PII is logged — only a count is reported.
    """
    from apps.documents.services.retention import purge_expired_tokens

    count = purge_expired_tokens()
    if count:
        logger.info(
            "run_purge_expired_tokens: deleted %d expired/used DocumentAccessToken records.",
            count,
        )
    else:
        logger.debug("run_purge_expired_tokens: no expired tokens found.")
    return count
