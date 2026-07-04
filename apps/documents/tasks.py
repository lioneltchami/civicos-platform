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
  - scan_document                 — dev bypass + ClamAV integration
  - cleanup_stale_pending_uploads — purge docs stuck in PENDING_UPLOAD

Wave 3 tasks:
  - ClamAV integration wired into scan_document (replaces Wave 2 stub)
  - run_purge_expired_tokens      — daily Celery Beat: delete expired access tokens

Wave 4 tasks:
  - run_disposal_schedule         — daily Celery Beat: soft-delete expired docs
  - run_hard_delete_schedule      — daily Celery Beat: hard-delete past grace period
  - notify_expiring_documents     — daily Celery Beat: notify citizens of expiring docs
  - create_beat_schedule()        — idempotent module-level function; called from migration
                                     0004 to register all PeriodicTask entries.

Beat schedule (all times UTC):
  01:00 — run_purge_expired_tokens       (Wave 3)
  02:00 — run_disposal_schedule          (Wave 4)
  03:00 — run_hard_delete_schedule       (Wave 4)
  04:00 — cleanup_stale_pending_uploads  (Wave 2)
  08:00 — notify_expiring_documents      (Wave 4)
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import Task, shared_task
from django.db import transaction
from django.utils import timezone

from apps.notifications.services import send_email_notification

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
    # H-9: explicit name prevents Beat task name breakage on module refactoring.
    name="apps.documents.tasks.scan_document",
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
    # H-9: explicit name prevents Beat task name breakage on module refactoring.
    name="apps.documents.tasks.cleanup_stale_pending_uploads",
)
def cleanup_stale_pending_uploads(self) -> int:
    """
    Delete Document records stuck in PENDING_UPLOAD beyond the presigned URL TTL.

    Scheduled by Celery Beat to run daily at 04:00 UTC.

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
# Wave 4: Retention disposal tasks
# ─────────────────────────────────────────────────────────────────────────────


@shared_task(
    bind=True,
    queue="documents",
    acks_late=True,
    reject_on_worker_lost=True,
    # Retry on transient DB errors. Documents that fail individually are
    # logged and skipped — the task does NOT abort the whole batch.
    max_retries=3,
    default_retry_delay=60,
    # H-9: explicit name prevents Beat task name breakage on module refactoring.
    name="apps.documents.tasks.run_disposal_schedule",
)
def run_disposal_schedule(self) -> dict:
    """
    Celery Beat task: soft-delete all documents past their max retention date.

    Runs daily at 02:00 UTC via django_celery_beat PeriodicTask.

    Algorithm:
      1. Fetch the list of PKs matching pending_disposal() — evaluated once.
         Using PKs (not a live queryset) prevents "cursor expired" / lazy
         evaluation issues when iterating across a long batch.
      2. For each pk, call retention.soft_delete() inside its own try/except.
         Errors per document are logged and skipped — one failure must NOT
         abort the rest of the batch (a legal hold set mid-run would cause
         ValueError; that is expected and must not fail the whole task).
      3. Return a summary dict for Celery task result inspection.

    Idempotency:
      soft_delete() raises ValueError if the document is already soft-deleted.
      The per-doc try/except handles this gracefully — safe to re-run.

    PIPEDA:
      - Log only document_pk (UUID), not original_filename, storage_key, or
        uploader email.
      - Reason written to audit log is "retention_expired" (not filename).

    Returns:
        {
            "total_eligible": int,   # docs in pending_disposal()
            "soft_deleted": int,     # successfully disposed
            "skipped": int,          # expected skips: legal holds, already deleted
            "errors": int,           # M-6: unexpected exceptions (DB/service errors)
        }
    """
    from apps.documents.models import Document
    from apps.documents.services.retention import soft_delete

    # Materialise PKs upfront — avoids cursor timeout for large result sets and
    # ensures we iterate a fixed snapshot (not a live queryset that changes as
    # we soft-delete rows and they drop out of pending_disposal()).
    eligible_pks = list(
        Document.objects.pending_disposal().values_list("pk", flat=True)
    )
    total = len(eligible_pks)
    soft_deleted_count = 0
    skipped_count = 0
    errors_count = 0  # M-6: unexpected failures — separate from expected skips

    # H-4: batch-fetch in chunks of 500 to eliminate N+1 queries
    # (one SELECT per pk replaced by one SELECT per chunk of 500).
    # soft_delete() re-fetches under select_for_update() inside its own atomic(),
    # so the pre-fetched doc is used for outer loop efficiency only.
    _CHUNK = 500
    for _start in range(0, len(eligible_pks), _CHUNK):
        chunk = eligible_pks[_start : _start + _CHUNK]
        doc_map = {
            d.pk: d
            for d in Document.objects.filter(pk__in=chunk).select_related("category")
        }
        for doc_pk in chunk:
            doc = doc_map.get(doc_pk)
            if doc is None:
                # Deleted by a concurrent task or manual action between the
                # snapshot and this iteration step. Skip silently.
                skipped_count += 1
                continue

            try:
                soft_delete(document=doc, deleted_by=None, reason="retention_expired")
                soft_deleted_count += 1
            except ValueError as exc:
                # Expected skip: already deleted, legal hold set between snapshot & now.
                logger.info(
                    "run_disposal_schedule: skipped doc pk=%r — %s",
                    str(doc_pk),
                    type(exc).__name__,
                )
                skipped_count += 1
            except Exception:
                # M-6: unexpected error — counted separately so monitoring dashboards
                # can distinguish DB/service failures from intentional skips.
                logger.exception(
                    "run_disposal_schedule: unexpected error for doc pk=%r; skipping.",
                    str(doc_pk),
                )
                errors_count += 1

    logger.info(
        "run_disposal_schedule: total_eligible=%d soft_deleted=%d skipped=%d errors=%d",
        total,
        soft_deleted_count,
        skipped_count,
        errors_count,
    )

    return {
        "total_eligible": total,
        "soft_deleted": soft_deleted_count,
        "skipped": skipped_count,
        "errors": errors_count,
    }


@shared_task(
    bind=True,
    queue="documents",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
    default_retry_delay=60,
    # H-9: explicit name prevents Beat task name breakage on module refactoring.
    name="apps.documents.tasks.run_hard_delete_schedule",
)
def run_hard_delete_schedule(self) -> dict:
    """
    Celery Beat task: irreversibly hard-delete soft-deleted documents past
    the 30-day grace period.

    Runs daily at 03:00 UTC via django_celery_beat PeriodicTask.

    Per spec §11.2 and NIST SP 800-88: hard deletion is irreversible.
    The S3 object is deleted and the _storage_key field is nulled out.
    The Document DB row is RETAINED for the audit trail — the row provides
    the permanent record of what was uploaded, by whom, and when.

    Algorithm:
      1. Fetch PKs matching pending_hard_delete(grace_days=30) upfront.
      2. For each pk, call retention.hard_delete() inside its own try/except.
         If storage deletion fails, hard_delete() returns without nulling the
         key (fail-safe — the document re-appears in the next batch run).
      3. Return a summary dict.

    Idempotency:
      - pending_hard_delete() checks scan_status=DELETED + deleted_at <= cutoff.
      - hard_delete() raises ValueError if preconditions are not met.
        A document with an empty _storage_key re-appears in the queryset
        (storage_key empty but scan_status still DELETED + deleted_at still set),
        so hard_delete() is called again — it skips the S3 delete (empty key),
        nulls the key again (no-op), and returns. Fully idempotent.

    PIPEDA:
      - Log only document_pk (UUID), not storage_key or uploader email.

    Returns:
        {
            "total_eligible": int,
            "hard_deleted": int,
            "skipped": int,          # expected skips: legal holds, wrong status
            "errors": int,           # M-6: unexpected exceptions (S3/DB errors)
        }
    """
    from apps.documents.models import Document
    from apps.documents.services.retention import hard_delete

    eligible_pks = list(
        Document.objects.pending_hard_delete().values_list("pk", flat=True)
    )
    total = len(eligible_pks)
    hard_deleted_count = 0
    skipped_count = 0
    errors_count = 0  # M-6: unexpected failures — separate from expected skips

    # H-4: batch-fetch in chunks of 500 to eliminate N+1 queries.
    # hard_delete() re-fetches under select_for_update() inside its own atomic(),
    # so this pre-fetch is for loop efficiency only.
    _CHUNK = 500
    for _start in range(0, len(eligible_pks), _CHUNK):
        chunk = eligible_pks[_start : _start + _CHUNK]
        doc_map = {
            d.pk: d
            for d in Document.objects.filter(pk__in=chunk).select_related("category")
        }
        for doc_pk in chunk:
            doc = doc_map.get(doc_pk)
            if doc is None:
                skipped_count += 1
                continue

            try:
                hard_delete(document=doc)
                hard_deleted_count += 1
            except ValueError as exc:
                # Expected skip: legal hold, not soft-deleted, grace not elapsed.
                logger.info(
                    "run_hard_delete_schedule: skipped doc pk=%r — %s",
                    str(doc_pk),
                    type(exc).__name__,
                )
                skipped_count += 1
            except Exception:
                # M-6: unexpected error — counted separately so monitoring dashboards
                # can distinguish S3/DB failures from intentional skips.
                logger.exception(
                    "run_hard_delete_schedule: unexpected error for doc pk=%r; skipping.",
                    str(doc_pk),
                )
                errors_count += 1

    logger.info(
        "run_hard_delete_schedule: total_eligible=%d hard_deleted=%d skipped=%d errors=%d",
        total,
        hard_deleted_count,
        skipped_count,
        errors_count,
    )

    return {
        "total_eligible": total,
        "hard_deleted": hard_deleted_count,
        "skipped": skipped_count,
        "errors": errors_count,
    }


@shared_task(
    bind=True,
    queue="documents",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
    default_retry_delay=60,
    # H-9: explicit name prevents Beat task name breakage on module refactoring.
    name="apps.documents.tasks.notify_expiring_documents",
)
def notify_expiring_documents(self, days_before: int = 7) -> dict:
    """
    Celery Beat task: notify citizens whose documents expire within ``days_before`` days.

    Runs daily at 08:00 UTC via django_celery_beat PeriodicTask.

    A document is "expiring soon" when:
      - expires_at is in the window [now, now + days_before]
      - scan_status = ACTIVE   (only clean documents are meaningful to notify on)
      - deleted_at IS NULL     (not soft-deleted)
      - legal_hold = False     (legal holds prevent disposal — no notification needed)
      - is_latest_version = True (do not spam for every version of a document chain)

    Notification:
      - Sends an email via notifications.services.send_email_notification() to
        the document's uploaded_by user.
      - Uses subject_key="document_expiring_soon" — requires the corresponding
        email templates to exist (see apps/notifications/templates/notifications/email/).
      - PIPEDA: context contains ONLY category display name and expires_at date.
        No original_filename, no storage_key.
      - Notification failures (template missing → returns False) are logged and
        skipped — they do NOT abort the batch or re-queue the task.
      - SMTP failures raise → the task retries up to max_retries.

    Args:
        days_before: Notify for documents expiring within this many days.
                     Default 7 (one week). Configurable for testing.

    Returns:
        {
            "total_eligible": int,   # docs in the expiry window
            "notified": int,         # successfully emailed
            "skipped": int,          # template error, missing user, or send failure
        }
    """
    from apps.documents.models import Document

    now = timezone.now()
    window_end = now + timedelta(days=days_before)

    # Materialise PKs upfront for the same reason as run_disposal_schedule:
    # avoids cursor timeout and ensures a fixed snapshot.
    eligible = list(
        Document.objects.filter(
            scan_status=Document.ScanStatus.ACTIVE,
            deleted_at__isnull=True,
            legal_hold=False,
            is_latest_version=True,
            expires_at__gte=now,
            expires_at__lte=window_end,
        )
        .select_related("uploaded_by", "category")
        .values_list("pk", "uploaded_by_id", "expires_at", "category__name_en")
    )

    total = len(eligible)
    notified_count = 0
    skipped_count = 0

    for doc_pk, uploaded_by_id, expires_at, category_name in eligible:
        try:
            # Fetch the User to pass to send_email_notification.
            # Using get_user_model() here keeps the app decoupled from the
            # concrete User model import path.
            from django.contrib.auth import get_user_model
            User = get_user_model()
            try:
                recipient = User.objects.get(pk=uploaded_by_id)
            except User.DoesNotExist:
                # User account deleted between snapshot and now.
                logger.warning(
                    "notify_expiring_documents: user pk=%r not found for doc pk=%r; skipping.",
                    uploaded_by_id,
                    str(doc_pk),
                )
                skipped_count += 1
                continue

            # PIPEDA: context contains ONLY category name and expiry date —
            # NO original_filename, NO storage_key, NO uploader PII.
            # H-5 fix: compute actual remaining days, not the task parameter.
            # H-6 fix: use timezone.localdate() to convert expires_at to the server's
            # configured TIME_ZONE — prevents showing the wrong date to citizens
            # in Atlantic/Pacific timezones when the UTC date crosses midnight.
            _expires_date = timezone.localdate(expires_at)
            context = {
                "category_name": category_name or "Document",
                "expires_at": _expires_date,
                "days_remaining": max(0, (_expires_date - timezone.localdate()).days),
            }

            success = send_email_notification(
                recipient=recipient,
                subject_key="document_expiring_soon",
                context=context,
            )

            if success:
                notified_count += 1
                # C-6 fix: do NOT log uploaded_by_id — user PK is PII under PIPEDA.
                # Module invariant: "PII is NEVER written to log messages."
                logger.debug(
                    "notify_expiring_documents: notification sent for doc pk=%r.",
                    str(doc_pk),
                )
            else:
                # Template rendering failed (non-retryable misconfiguration).
                logger.error(
                    "notify_expiring_documents: send_email_notification returned False "
                    "for doc pk=%r (template missing or render error). Skipping.",
                    str(doc_pk),
                )
                skipped_count += 1

        except Exception:
            # C-5 fix: per-document SMTP/send failure — log and skip this document.
            # DO NOT re-raise: retrying the whole batch would re-send notifications
            # to every citizen who already received one on this run (duplicate emails).
            # The document will be retried automatically on tomorrow's scheduled run
            # if it still falls within the expiry window.
            # PIPEDA: log only doc_pk — not user email or uploader identity.
            logger.exception(
                "notify_expiring_documents: send failure for doc pk=%r; "
                "skipping (will retry on next scheduled run).",
                str(doc_pk),
            )
            skipped_count += 1

    logger.info(
        "notify_expiring_documents: total_eligible=%d notified=%d skipped=%d",
        total,
        notified_count,
        skipped_count,
    )

    return {
        "total_eligible": total,
        "notified": notified_count,
        "skipped": skipped_count,
    }


@shared_task(
    bind=True,
    queue="documents",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=0,
    # H-9: explicit name prevents Beat task name breakage on module refactoring.
    name="apps.documents.tasks.run_purge_expired_tokens",
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


# ─────────────────────────────────────────────────────────────────────────────
# Beat schedule registration
# ─────────────────────────────────────────────────────────────────────────────


def create_beat_schedule() -> None:
    """
    Idempotently register all Document BB PeriodicTask entries with
    django_celery_beat's DatabaseScheduler.

    Called from migration 0004 via:
        migrations.RunPython(lambda apps, schema_editor: create_beat_schedule())

    Using get_or_create() makes this safe to re-run at any time (e.g. after
    deployment, after a Beat flush, or manually via the shell).

    Beat schedule (all times UTC):
      01:00 — run_purge_expired_tokens       — daily; delete expired/used tokens
      02:00 — run_disposal_schedule          — daily; soft-delete retention-expired docs
      03:00 — run_hard_delete_schedule       — daily; irreversible S3 + storage_key null
      04:00 — cleanup_stale_pending_uploads  — daily; remove stuck PENDING_UPLOAD rows
      08:00 — notify_expiring_documents      — daily; email citizens about upcoming expiry

    Off-peak hours are chosen deliberately:
      - 01:00–04:00 window: low citizen traffic; destructive operations run when the
        system is least loaded and any DB performance impact is minimal.
      - 08:00: notification window — citizens are typically online in the morning,
        making the 7-day expiry warning actionable.

    Why daily? Daily runs are fine for retention policy enforcement (documents
    don't need to be disposed of within minutes of their expiry date). The 30-day
    grace period for hard deletion provides ample margin.

    Why not every minute? Frequent runs would hammer the DB and risk thundering-herd
    problems if the task takes a long time on a large dataset.
    """
    from django_celery_beat.models import CrontabSchedule, PeriodicTask

    # Shared defaults for all entries — task name prefix is the app label.
    # The "name" is the human-readable PeriodicTask.name, used in the admin.
    # The "task" is the fully-qualified Celery task name (module.function).

    tasks = [
        {
            "name": "documents: purge-expired-tokens",
            "task": "apps.documents.tasks.run_purge_expired_tokens",
            "minute": "0",
            "hour": "1",
        },
        {
            "name": "documents: run-disposal-schedule",
            "task": "apps.documents.tasks.run_disposal_schedule",
            "minute": "0",
            "hour": "2",
        },
        {
            "name": "documents: run-hard-delete-schedule",
            "task": "apps.documents.tasks.run_hard_delete_schedule",
            "minute": "0",
            "hour": "3",
        },
        {
            "name": "documents: cleanup-stale-pending-uploads",
            "task": "apps.documents.tasks.cleanup_stale_pending_uploads",
            "minute": "0",
            "hour": "4",
        },
        {
            "name": "documents: notify-expiring-documents",
            "task": "apps.documents.tasks.notify_expiring_documents",
            "minute": "0",
            "hour": "8",
        },
    ]

    for entry in tasks:
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=entry["minute"],
            hour=entry["hour"],
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            # H-8: timezone MUST be in the lookup fields, not defaults={}.
            # If it were in defaults only, a pre-existing crontab with the same
            # time but timezone="America/Toronto" would be silently reused —
            # Beat tasks would run in the wrong timezone (TBS SPIN 2023: UTC required).
            timezone="UTC",
        )

        periodic_task, created = PeriodicTask.objects.get_or_create(
            name=entry["name"],
            defaults={
                "task": entry["task"],
                "crontab": schedule,
                "enabled": True,
            },
        )

        if not created:
            # Existing task: ensure the crontab, task name, and enabled flag are
            # correct.  Without updating enabled here, a manually-disabled task
            # remains disabled after redeploy — Beat silently stops running the
            # task and operators have no visibility (M-4 fix).
            updated = False
            if periodic_task.task != entry["task"]:
                periodic_task.task = entry["task"]
                updated = True
            if periodic_task.crontab_id != schedule.pk:
                periodic_task.crontab = schedule
                updated = True
            if not periodic_task.enabled:
                # M-4: re-enable any task disabled by an admin; deployment is
                # authoritative for which tasks must be running.
                periodic_task.enabled = True
                updated = True
            if updated:
                periodic_task.save(update_fields=["task", "crontab", "enabled"])

        logger.debug(
            "create_beat_schedule: %s task=%r schedule=%r:%r created=%s",
            entry["name"],
            entry["task"],
            entry["hour"],
            entry["minute"],
            created,
        )
