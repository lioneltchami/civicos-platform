"""
Tests for apps.documents.tasks — Wave 2.

Coverage:
  - scan_document: dev bypass, idempotency, document-not-found, signal dispatch,
    ClamAV-required branch raises NotImplementedError
  - cleanup_stale_pending_uploads: deletes stale PENDING_UPLOAD rows,
    keeps recent rows, keeps non-PENDING_UPLOAD rows, returns count

All tests use override_settings to control CIVICOS configuration.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import ANY, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.documents.models import Document, DocumentCategory
from apps.documents.services.upload import _make_storage_key
from apps.documents.tasks import cleanup_stale_pending_uploads, scan_document

User = get_user_model()

CIVICOS_DEV = {
    "CLAMAV_HOST": "",
    "CLAMAV_PORT": 3310,
    "CLAMAV_REQUIRED": False,
    "DOCUMENT_PRESIGNED_POST_TTL_SECONDS": 900,
    "DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES": 10 * 1024 * 1024,
    "DOCUMENT_MAX_STAFF_UPLOAD_BYTES": 50 * 1024 * 1024,
    "DOCUMENT_ZIP_MAX_ENTRIES": 1000,
    "DOCUMENT_ZIP_MAX_RATIO": 100,
}

CIVICOS_PROD = {
    **CIVICOS_DEV,
    "CLAMAV_HOST": "clamav.internal",
    "CLAMAV_REQUIRED": True,
}


def make_user() -> User:
    uid = uuid.uuid4().hex[:8]
    return User.objects.create_user(
        email=f"task-{uid}@example.com",
        password="hunter2",
    )


def make_category(**kwargs) -> DocumentCategory:
    defaults = {
        "name_en": "Task Test Category",
        "name_fr": "Catégorie de test",
        "slug": f"task-cat-{uuid.uuid4().hex[:6]}",
        "security_classification": DocumentCategory.SecurityClassification.PROTECTED_B,
        "allowed_mime_types": [],
        "max_size_bytes": 0,
        "min_retention_days": 730,
        "max_retention_days": 2555,
        "is_transitory": False,
    }
    defaults.update(kwargs)
    return DocumentCategory.objects.create(**defaults)


def make_document(
    user: User,
    category: DocumentCategory,
    scan_status: str = Document.ScanStatus.SCANNING,
    created_at_override=None,
) -> Document:
    doc = Document.objects.create(
        category=category,
        uploaded_by=user,
        original_filename="test.pdf",
        _storage_key=_make_storage_key(str(uuid.uuid4()), prefix="quarantine"),
        mime_type="application/pdf",
        size_bytes=1024,
        scan_status=scan_status,
        security_classification=DocumentCategory.SecurityClassification.PROTECTED_B,
    )
    if created_at_override is not None:
        # Use queryset update to bypass auto_now_add
        Document.objects.filter(pk=doc.pk).update(created_at=created_at_override)
        doc.refresh_from_db()
    return doc


# ─────────────────────────────────────────────────────────────────────────────
# scan_document
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=CIVICOS_DEV, DEBUG=True)
class ScanDocumentDevBypassTests(TestCase):
    """
    Tests for scan_document() in dev mode (CLAMAV_HOST="" + CLAMAV_REQUIRED=False).
    """

    def setUp(self):
        self.user = make_user()
        self.category = make_category()

    def test_dev_bypass_marks_document_active(self):
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        scan_document.run(str(doc.pk))  # T-9: use .run() to exercise the bound task instance
        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.ACTIVE)

    def test_dev_bypass_sets_scan_completed_at(self):
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        before = timezone.now()
        scan_document.run(str(doc.pk))  # T-9
        doc.refresh_from_db()
        self.assertIsNotNone(doc.scan_completed_at)
        self.assertGreaterEqual(doc.scan_completed_at, before)

    def test_dev_bypass_sets_scan_engine_result_to_dev_bypass(self):
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        scan_document.run(str(doc.pk))  # T-9
        doc.refresh_from_db()
        self.assertEqual(doc.scan_engine_result, "DEV_BYPASS")

    def test_dev_bypass_fires_document_scan_clean_signal(self):
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        received = []

        from apps.documents.signals import document_scan_clean
        # weak=False: lambdas have no strong reference and are GC'd by default.
        handler = lambda sender, **kw: received.append(kw)  # noqa: E731
        document_scan_clean.connect(handler, weak=False)
        try:
            scan_document.run(str(doc.pk))  # T-9
        finally:
            document_scan_clean.disconnect(handler)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["document_pk"], str(doc.pk))

    def test_dev_bypass_signal_no_uploader_pii(self):
        """PIPEDA: scan_clean signal must contain only document_pk — no uploader identity."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        received = []

        from apps.documents.signals import document_scan_clean
        # weak=False prevents GC of the anonymous handler before the signal fires.
        handler = lambda sender, **kw: received.append(kw)  # noqa: E731
        document_scan_clean.connect(handler, weak=False)
        try:
            scan_document.run(str(doc.pk))  # T-9
        finally:
            document_scan_clean.disconnect(handler)

        signal_kwargs = received[0]
        # Positive assertion: exact key set — no unexpected keys can sneak in
        self.assertEqual(
            set(signal_kwargs.keys()),
            {"document_pk", "signal"},
            "scan_clean signal must contain exactly {document_pk, signal} — no extra keys",
        )
        # Negative assertion: explicit PII key names must be absent
        forbidden_pii_keys = {"email", "username", "original_filename", "storage_key"}
        found_pii = forbidden_pii_keys & set(signal_kwargs.keys())
        self.assertFalse(
            found_pii,
            f"PIPEDA: PII keys must not appear in scan_clean signal: {found_pii}",
        )

    # ── Idempotency ───────────────────────────────────────────────────────────

    def test_idempotent_already_active(self):
        """If document is already ACTIVE (from a concurrent worker), task is a no-op."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.ACTIVE)
        original_completed_at = doc.scan_completed_at

        scan_document.run(str(doc.pk))  # T-9
        doc.refresh_from_db()

        # Status should be unchanged
        self.assertEqual(doc.scan_status, Document.ScanStatus.ACTIVE)
        self.assertEqual(doc.scan_completed_at, original_completed_at)

    def test_idempotent_already_quarantined(self):
        """T-7: Status must be unchanged AND signal must NOT fire on QUARANTINED path."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.QUARANTINED)
        received = []

        from apps.documents.signals import document_scan_clean
        handler = lambda sender, **kw: received.append(kw)  # noqa: E731
        document_scan_clean.connect(handler, weak=False)
        try:
            scan_document.run(str(doc.pk))  # T-9
        finally:
            document_scan_clean.disconnect(handler)

        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.QUARANTINED)
        self.assertEqual(
            len(received), 0,
            "document_scan_clean must NOT fire when document is already QUARANTINED",
        )

    def test_idempotent_already_deleted(self):
        """T-7: Status must be unchanged AND signal must NOT fire on DELETED path."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.DELETED)
        received = []

        from apps.documents.signals import document_scan_clean
        handler = lambda sender, **kw: received.append(kw)  # noqa: E731
        document_scan_clean.connect(handler, weak=False)
        try:
            scan_document.run(str(doc.pk))  # T-9
        finally:
            document_scan_clean.disconnect(handler)

        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.DELETED)
        self.assertEqual(
            len(received), 0,
            "document_scan_clean must NOT fire when document is already DELETED",
        )

    def test_idempotent_does_not_fire_signal_when_already_active(self):
        """Signal must NOT fire if we skipped the transition (idempotency path)."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.ACTIVE)
        received = []

        from apps.documents.signals import document_scan_clean
        # weak=False: prevents GC of the anonymous handler before signal assertion.
        handler = lambda sender, **kw: received.append(kw)  # noqa: E731
        document_scan_clean.connect(handler, weak=False)
        try:
            scan_document.run(str(doc.pk))  # T-9
        finally:
            document_scan_clean.disconnect(handler)

        self.assertEqual(len(received), 0)

    @override_settings(CIVICOS=CIVICOS_DEV, DEBUG=False, TESTING=False)
    def test_dev_bypass_raises_in_production(self):
        """
        C-4: The DEV_BYPASS path must raise RuntimeError when triggered outside
        a DEBUG or TESTING environment. An empty CLAMAV_HOST with
        CLAMAV_REQUIRED=False in production would mark every document ACTIVE
        without any virus scan — this guard prevents silent misconfiguration.
        """
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        with self.assertRaises(RuntimeError) as ctx:
            scan_document.run(str(doc.pk))  # T-9
        self.assertIn("DEV_BYPASS", str(ctx.exception))
        # Document must remain in SCANNING — no status change occurred
        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.SCANNING)

    # ── Document not found ────────────────────────────────────────────────────

    def test_document_not_found_triggers_retry(self):
        """
        T-9 / M-6: When the document is not found, scan_document must call
        self.retry() — NOT silently return (old T-9 bug) or raise DoesNotExist.

        We patch self.retry() to intercept the call without triggering the eager
        retry loop. With CELERY_TASK_ALWAYS_EAGER=True, an unpatched self.retry()
        inside .run() would synchronously re-invoke the task body up to max_retries=5,
        exhausting retries and raising MaxRetriesExceededError instead of Retry.
        Patching isolates the core M-6 invariant: "retry must be called with
        countdown=5 (replica-lag backoff)" from Celery's eager-execution machinery.
        """
        from celery.exceptions import Retry

        fake_pk = str(uuid.uuid4())
        with patch.object(scan_document, "retry", side_effect=Retry()) as mock_retry:
            with self.assertRaises(Retry):
                scan_document.run(fake_pk)

        mock_retry.assert_called_once()
        _, retry_kwargs = mock_retry.call_args
        # H2 fix: DoesNotExist retries now use exponential backoff (2**retries)*30.
        # At retries=0 (first attempt), countdown = (2**0)*30 = 30 seconds.
        self.assertEqual(
            retry_kwargs.get("countdown"),
            30,
            "H2: retry countdown must be (2**retries)*30=30s on first DoesNotExist",
        )


@override_settings(CIVICOS=CIVICOS_PROD)
class ScanDocumentProdTests(TestCase):
    """
    Tests for scan_document() in production mode (CLAMAV_HOST set, CLAMAV_REQUIRED=True).

    Wave 3: the full ClamAV path is implemented. These tests verify that:
      - The ClamAV code path is entered (not the dev bypass).
      - A clean scan result marks the document ACTIVE.
      - An infected result marks the document QUARANTINED.
      - A connection failure triggers a Celery retry (not NotImplementedError).
      - The document remains in SCANNING on retry (not silently marked ACTIVE).
    """

    def setUp(self):
        self.user = make_user()
        self.category = make_category()

    def test_clean_clamav_scan_marks_document_active(self):
        """
        Production ClamAV path: mocked clean scan → ACTIVE.
        _scan_with_clamav is patched to return "OK" so no real socket call occurs.
        """
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        with patch("apps.documents.tasks._scan_with_clamav", return_value="OK"):
            scan_document.run(str(doc.pk))  # T-9
        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.ACTIVE)

    def test_infected_clamav_scan_marks_document_quarantined(self):
        """
        Production ClamAV path: mocked infected scan → QUARANTINED.
        """
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        with patch(
            "apps.documents.tasks._scan_with_clamav",
            return_value="Eicar-Test-Signature",
        ):
            scan_document.run(str(doc.pk))  # T-9
        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.QUARANTINED)
        self.assertEqual(doc.scan_engine_result, "FOUND: Eicar-Test-Signature")

    def test_clamav_connection_error_triggers_retry_not_active(self):
        """
        Connection failure must trigger Celery retry; document stays in SCANNING
        (must NOT be marked ACTIVE — that would bypass virus scanning).

        When scan_document is called via .run() (called_directly=True), Celery's
        self.retry(exc=<ConnectionError>) re-raises the original exception rather
        than celery.exceptions.Retry.  This is documented Celery behaviour:
        task.py::raise_with_context raises exc when exc is provided.
        The important invariant is that scan_status remains SCANNING (not ACTIVE).
        """
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        with patch(
            "apps.documents.tasks._scan_with_clamav",
            side_effect=ConnectionError("clamd unreachable"),
        ):
            # Under called_directly=True, self.retry(exc=<err>) re-raises the
            # original ConnectionError, not celery.exceptions.Retry.
            with self.assertRaises(ConnectionError):
                scan_document.run(str(doc.pk))  # T-9

        # Critical invariant: ClamAV failure must NEVER mark a document ACTIVE.
        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.SCANNING)

    def test_clamav_path_does_not_use_dev_bypass(self):
        """
        When CLAMAV_HOST is set, scan_document must NOT use the dev bypass
        (scan_engine_result='DEV_BYPASS'). Either a real scan or retry must occur.
        """
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        with patch("apps.documents.tasks._scan_with_clamav", return_value="OK"):
            scan_document.run(str(doc.pk))  # T-9
        doc.refresh_from_db()
        self.assertNotEqual(doc.scan_engine_result, "DEV_BYPASS")


# ─────────────────────────────────────────────────────────────────────────────
# cleanup_stale_pending_uploads
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=CIVICOS_DEV)
class CleanupStalePendingUploadsTests(TestCase):
    """
    Tests for cleanup_stale_pending_uploads() Celery task.

    The stale threshold is DOCUMENT_PRESIGNED_POST_TTL_SECONDS (900s) + 5 min (300s)
    = 1200 seconds (20 minutes). Documents older than this in PENDING_UPLOAD state
    are considered abandoned and must be deleted.
    """

    def setUp(self):
        self.user = make_user()
        self.category = make_category()

    def _stale_time(self) -> timezone.datetime:
        """Return a timestamp older than the stale threshold (25 min ago)."""
        return timezone.now() - timedelta(seconds=1500)  # 25 minutes ago

    def _recent_time(self) -> timezone.datetime:
        """Return a timestamp within the stale threshold (5 min ago)."""
        return timezone.now() - timedelta(seconds=300)  # 5 minutes ago

    def test_deletes_stale_pending_upload_rows(self):
        """Documents in PENDING_UPLOAD older than threshold must be deleted."""
        doc = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
            created_at_override=self._stale_time(),
        )
        count = cleanup_stale_pending_uploads.run()  # T-9
        self.assertEqual(count, 1)
        self.assertFalse(Document.objects.filter(pk=doc.pk).exists())

    def test_keeps_recent_pending_upload_rows(self):
        """Documents in PENDING_UPLOAD within threshold must NOT be deleted."""
        doc = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
            created_at_override=self._recent_time(),
        )
        count = cleanup_stale_pending_uploads.run()  # T-9
        self.assertEqual(count, 0)
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())

    def test_keeps_scanning_documents_regardless_of_age(self):
        """SCANNING status is not PENDING_UPLOAD — must not be deleted."""
        doc = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.SCANNING,
            created_at_override=self._stale_time(),
        )
        cleanup_stale_pending_uploads.run()  # T-9
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())

    def test_keeps_active_documents_regardless_of_age(self):
        """ACTIVE documents must never be deleted by the cleanup task."""
        doc = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.ACTIVE,
            created_at_override=self._stale_time(),
        )
        cleanup_stale_pending_uploads.run()  # T-9
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())

    def test_keeps_quarantined_documents(self):
        doc = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.QUARANTINED,
            created_at_override=self._stale_time(),
        )
        cleanup_stale_pending_uploads.run()  # T-9
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())

    def test_returns_zero_when_nothing_to_clean(self):
        count = cleanup_stale_pending_uploads.run()  # T-9
        self.assertEqual(count, 0)

    def test_returns_count_of_deleted_rows(self):
        """Return value must equal the number of rows deleted."""
        for _ in range(3):
            make_document(
                self.user,
                self.category,
                scan_status=Document.ScanStatus.PENDING_UPLOAD,
                created_at_override=self._stale_time(),
            )
        count = cleanup_stale_pending_uploads.run()  # T-9
        self.assertEqual(count, 3)

    def test_mixed_stale_and_recent(self):
        """Only stale rows deleted; recent rows kept."""
        stale_doc = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
            created_at_override=self._stale_time(),
        )
        recent_doc = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
            created_at_override=self._recent_time(),
        )
        count = cleanup_stale_pending_uploads.run()  # T-9
        self.assertEqual(count, 1)
        self.assertFalse(Document.objects.filter(pk=stale_doc.pk).exists())
        self.assertTrue(Document.objects.filter(pk=recent_doc.pk).exists())

    @override_settings(CIVICOS={**CIVICOS_DEV, "DOCUMENT_PRESIGNED_POST_TTL_SECONDS": 60})
    def test_respects_custom_ttl_setting(self):
        """
        The stale threshold is derived from DOCUMENT_PRESIGNED_POST_TTL_SECONDS
        (60s + 300s grace = 360s = 6 minutes). A document 7 minutes old should
        be deleted.
        """
        # 7 minutes old → past threshold of 6 minutes
        old_doc = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
            created_at_override=timezone.now() - timedelta(seconds=420),
        )
        # 4 minutes old → within threshold
        recent_doc = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
            created_at_override=timezone.now() - timedelta(seconds=240),
        )
        count = cleanup_stale_pending_uploads.run()  # T-9
        self.assertEqual(count, 1)
        self.assertFalse(Document.objects.filter(pk=old_doc.pk).exists())
        self.assertTrue(Document.objects.filter(pk=recent_doc.pk).exists())


# ─────────────────────────────────────────────────────────────────────────────
# T-8: Task decorator property tests
# ─────────────────────────────────────────────────────────────────────────────


class TaskDecoratorPropertyTests(TestCase):
    """
    T-8: Verify at-least-once delivery guarantees are declared on both tasks.

    ``acks_late=True`` ensures the broker does NOT ack the message until the
    task body returns successfully — a worker crash mid-execution causes the
    broker to redeliver the task.

    ``reject_on_worker_lost=True`` causes a NACK (not ACK) if the worker
    process disappears (OOM kill, SIGKILL), so the message is requeued rather
    than silently discarded.

    Both properties MUST be True on any task that mutates document state, to
    satisfy the at-least-once delivery guarantee required by the security
    model (a lost scan leaves the document in SCANNING forever — a security
    gap, because no virus check ran but the document appears in-progress).
    """

    def test_scan_document_acks_late(self):
        """T-8: scan_document must ack only after successful completion."""
        self.assertTrue(
            scan_document.acks_late,
            "scan_document.acks_late must be True for at-least-once delivery",
        )

    def test_scan_document_reject_on_worker_lost(self):
        """T-8: scan_document must NACK on worker crash so the message is requeued."""
        self.assertTrue(
            scan_document.reject_on_worker_lost,
            "scan_document.reject_on_worker_lost must be True",
        )

    def test_cleanup_stale_pending_uploads_acks_late(self):
        """T-8: cleanup task must ack only after the delete query succeeds."""
        self.assertTrue(
            cleanup_stale_pending_uploads.acks_late,
            "cleanup_stale_pending_uploads.acks_late must be True",
        )

    def test_cleanup_stale_pending_uploads_reject_on_worker_lost(self):
        """T-8: cleanup task must NACK on worker crash."""
        self.assertTrue(
            cleanup_stale_pending_uploads.reject_on_worker_lost,
            "cleanup_stale_pending_uploads.reject_on_worker_lost must be True",
        )


# ─────────────────────────────────────────────────────────────────────────────
# M-7: _quarantine_on_scan_failure + _ScanDocumentTask.on_failure
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=CIVICOS_DEV)
class QuarantineOnScanFailureTests(TestCase):
    """
    Tests for _quarantine_on_scan_failure() helper and the _ScanDocumentTask.on_failure()
    hook (M-7).

    on_failure() is triggered by the Celery worker machinery after all retries are
    exhausted — never by .run() directly. These tests exercise both the helper
    directly and the on_failure() hook via scan_document (which IS an instance of
    _ScanDocumentTask because base=_ScanDocumentTask is set in the decorator).

    PIPEDA: all assertions verify that no PII appears in signal kwargs or log
    messages (document_pk and exception type only).
    """

    def setUp(self):
        self.user = make_user()
        self.category = make_category()

    # ── _quarantine_on_scan_failure unit tests ────────────────────────────────

    def test_quarantine_transitions_scanning_to_quarantined(self):
        """M-7: SCANNING → QUARANTINED on scan failure."""
        from apps.documents.tasks import _quarantine_on_scan_failure

        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        _quarantine_on_scan_failure(doc_pk=str(doc.pk), exc=RuntimeError("scan error"))
        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.QUARANTINED)

    def test_quarantine_sets_scan_engine_result_from_exception_type(self):
        """scan_engine_result must encode exception type only — no PII."""
        from apps.documents.tasks import _quarantine_on_scan_failure

        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        _quarantine_on_scan_failure(doc_pk=str(doc.pk), exc=RuntimeError("sensitive message"))
        doc.refresh_from_db()
        self.assertIn("RuntimeError", doc.scan_engine_result)
        # Exception message (which might contain PII) must NOT be in the result
        self.assertNotIn("sensitive message", doc.scan_engine_result)

    def test_quarantine_sets_scan_completed_at(self):
        """scan_completed_at must be stamped when quarantine transition occurs."""
        from apps.documents.tasks import _quarantine_on_scan_failure

        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        before = timezone.now()
        _quarantine_on_scan_failure(doc_pk=str(doc.pk), exc=RuntimeError("err"))
        doc.refresh_from_db()
        self.assertIsNotNone(doc.scan_completed_at)
        self.assertGreaterEqual(doc.scan_completed_at, before)

    def test_quarantine_fires_document_quarantined_signal(self):
        """M-7: document_quarantined signal must fire on SCANNING → QUARANTINED."""
        from apps.documents.tasks import _quarantine_on_scan_failure
        from apps.documents.signals import document_quarantined

        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        received = []
        handler = lambda sender, **kw: received.append(kw)  # noqa: E731
        document_quarantined.connect(handler, weak=False)
        try:
            _quarantine_on_scan_failure(doc_pk=str(doc.pk), exc=RuntimeError("err"))
        finally:
            document_quarantined.disconnect(handler)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["document_pk"], str(doc.pk))
        # PIPEDA: no PII in signal kwargs
        forbidden = {"original_filename", "email", "uploaded_by", "storage_key"}
        self.assertFalse(forbidden & set(received[0].keys()))

    def test_quarantine_idempotent_if_not_scanning(self):
        """If doc is already QUARANTINED (race condition), must not re-transition."""
        from apps.documents.tasks import _quarantine_on_scan_failure

        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.QUARANTINED)
        doc.scan_engine_result = "ORIGINAL"
        doc.save(update_fields=["scan_engine_result", "updated_at"])

        _quarantine_on_scan_failure(doc_pk=str(doc.pk), exc=RuntimeError("err"))
        doc.refresh_from_db()

        # Status and result must be unchanged (idempotency guard)
        self.assertEqual(doc.scan_status, Document.ScanStatus.QUARANTINED)
        self.assertEqual(doc.scan_engine_result, "ORIGINAL")

    def test_quarantine_doc_not_found_does_not_raise(self):
        """If doc was concurrently deleted, must log and return without raising."""
        from apps.documents.tasks import _quarantine_on_scan_failure

        fake_pk = str(uuid.uuid4())
        # Must not raise — worker on_failure must always complete cleanly
        _quarantine_on_scan_failure(doc_pk=fake_pk, exc=RuntimeError("err"))

    # ── _ScanDocumentTask.on_failure hook tests ───────────────────────────────

    def test_on_failure_extracts_doc_pk_from_positional_args(self):
        """
        on_failure must call _quarantine_on_scan_failure when doc_pk is in args[0].
        The normal dispatch path uses positional args: apply_async(args=[pk]).
        """
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        with patch("apps.documents.tasks._quarantine_on_scan_failure") as mock_quarantine:
            scan_document.on_failure(
                exc=RuntimeError("test"),
                task_id="fake-task-id",
                args=(str(doc.pk),),
                kwargs={},
                einfo=None,
            )
        mock_quarantine.assert_called_once_with(doc_pk=str(doc.pk), exc=ANY)

    def test_on_failure_extracts_doc_pk_from_kwargs(self):
        """
        on_failure must also handle kwargs dispatch (args is empty tuple).
        Without the kwargs.get("doc_pk") fallback, quarantine is silently skipped
        when the task is dispatched with keyword args instead of positional args.
        """
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        with patch("apps.documents.tasks._quarantine_on_scan_failure") as mock_quarantine:
            scan_document.on_failure(
                exc=RuntimeError("test"),
                task_id="fake-task-id",
                args=(),
                kwargs={"doc_pk": str(doc.pk)},
                einfo=None,
            )
        mock_quarantine.assert_called_once_with(doc_pk=str(doc.pk), exc=ANY)

    def test_on_failure_empty_args_and_kwargs_does_not_call_quarantine(self):
        """If both args and kwargs carry no doc_pk, quarantine must not be attempted."""
        with patch("apps.documents.tasks._quarantine_on_scan_failure") as mock_quarantine:
            scan_document.on_failure(
                exc=RuntimeError("test"),
                task_id="fake-task-id",
                args=(),
                kwargs={},
                einfo=None,
            )
        mock_quarantine.assert_not_called()
