"""
Wave 3 ClamAV integration test suite — apps.documents.tasks.

Coverage:
  - ClamAVCleanPathTests:      clean scan → ACTIVE, signals, idempotency
  - ClamAVInfectedPathTests:   infected scan → QUARANTINED, storage delete, signals
  - ClamAVRetryTests:          ConnectionError / IOError → retry with exponential backoff
  - ClamAVDocumentNotFoundTests: missing doc during lock → retry
  - QuarantineOnScanFailureTests (Wave 3 extension): full signal + PII assertions
  - ScanDocumentHelperTests:   direct unit tests for _scan_with_clamav,
                                _mark_document_active_clamav,
                                _mark_document_quarantined_clamav

Design invariants enforced:
  - All task invocations use scan_document.run(str(doc.pk)) — bypasses Celery
    eager-mode retry loops while still executing the bound task instance body.
  - Signal handlers use weak=False — lambdas hold no strong reference by default
    and would be GC'd before the signal fires.
  - PIPEDA: no PII (email, original_filename, storage_key) in signals or results.

Settings:
  - CIVICOS_CLAMAV mirrors production ClamAV settings with CLAMAV_HOST set.
  - override_settings(CIVICOS=CIVICOS_CLAMAV) activates the Wave 3 code path.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import ANY, MagicMock, patch

from celery.exceptions import Retry
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.documents.models import Document, DocumentCategory
from apps.documents.services.upload import _make_storage_key
from apps.documents.tasks import (
    _mark_document_active_clamav,
    _mark_document_quarantined_clamav,
    _quarantine_on_scan_failure,
    _scan_with_clamav,
    scan_document,
)

User = get_user_model()

# ─────────────────────────────────────────────────────────────────────────────
# Shared settings fixtures
# ─────────────────────────────────────────────────────────────────────────────

CIVICOS_CLAMAV = {
    "CLAMAV_HOST": "clamav.internal",
    "CLAMAV_PORT": 3310,
    "CLAMAV_TIMEOUT": 30,
    "CLAMAV_REQUIRED": True,
    "ALLOWED_UPLOAD_MIME_TYPES": ["application/pdf"],
    "DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES": 10 * 1024 * 1024,
    "DOCUMENT_MAX_STAFF_UPLOAD_BYTES": 50 * 1024 * 1024,
    "DOCUMENT_PRESIGNED_POST_TTL_SECONDS": 900,
    "DOCUMENT_ZIP_MAX_ENTRIES": 1000,
    "DOCUMENT_ZIP_MAX_RATIO": 100,
}

# ─────────────────────────────────────────────────────────────────────────────
# Test helpers
# ─────────────────────────────────────────────────────────────────────────────


def make_user() -> User:
    uid = uuid.uuid4().hex[:8]
    return User.objects.create_user(
        email=f"task-{uid}@example.com",
        password="hunter2",
    )


def make_category(**kwargs) -> DocumentCategory:
    defaults = {
        "name_en": "Test",
        "name_fr": "Test",
        "slug": f"cat-{uuid.uuid4().hex[:6]}",
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
) -> Document:
    return Document.objects.create(
        category=category,
        uploaded_by=user,
        original_filename="test.pdf",
        _storage_key=_make_storage_key(str(uuid.uuid4()), prefix="quarantine"),
        mime_type="application/pdf",
        size_bytes=1024,
        scan_status=scan_status,
        security_classification=DocumentCategory.SecurityClassification.PROTECTED_B,
    )


def _write_fake_file(storage_key: str) -> None:
    """
    Write a small dummy file at MEDIA_ROOT/<storage_key> so that
    default_storage.open(storage_key) succeeds without mocking.
    The test settings use FileSystemStorage with a temp MEDIA_ROOT.
    """
    from django.conf import settings as django_settings

    full_path = os.path.join(django_settings.MEDIA_ROOT, storage_key)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "wb") as fh:
        fh.write(b"fake pdf bytes")


# ─────────────────────────────────────────────────────────────────────────────
# 1. ClamAVCleanPathTests
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=CIVICOS_CLAMAV)
class ClamAVCleanPathTests(TestCase):
    """
    Tests for the happy path: ClamAV returns clean → ACTIVE transition.

    _scan_with_clamav is patched to return "OK" so these tests exercise only
    the scan_document orchestration layer and the _mark_document_active_clamav
    helper, not the pyclamd socket interaction.
    """

    def setUp(self):
        self.user = make_user()
        self.category = make_category()

    def _run_clean_scan(self, doc: Document) -> None:
        """Invoke scan_document with _scan_with_clamav stubbed to return OK."""
        with patch("apps.documents.tasks._scan_with_clamav", return_value="OK"):
            scan_document.run(str(doc.pk))

    # ── Status and field assertions ───────────────────────────────────────────

    def test_clean_scan_marks_document_active(self):
        """Clean scan must transition the document to ACTIVE."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        self._run_clean_scan(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.ACTIVE)

    def test_clean_scan_sets_scan_engine_result_ok(self):
        """scan_engine_result must be exactly 'OK' after a clean ClamAV scan."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        self._run_clean_scan(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.scan_engine_result, "OK")

    def test_clean_scan_sets_scan_completed_at(self):
        """scan_completed_at must be stamped at the time of the clean scan."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        before = timezone.now()
        self._run_clean_scan(doc)
        doc.refresh_from_db()
        self.assertIsNotNone(doc.scan_completed_at)
        self.assertGreaterEqual(doc.scan_completed_at, before)

    # ── Signal assertions ─────────────────────────────────────────────────────

    def test_clean_scan_fires_document_scan_clean_signal(self):
        """document_scan_clean must fire exactly once with the correct document_pk."""
        from apps.documents.signals import document_scan_clean

        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        received = []
        handler = lambda sender, **kw: received.append(kw)  # noqa: E731
        document_scan_clean.connect(handler, weak=False)
        try:
            self._run_clean_scan(doc)
        finally:
            document_scan_clean.disconnect(handler)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["document_pk"], str(doc.pk))

    def test_clean_scan_signal_no_pii_in_kwargs(self):
        """
        PIPEDA: document_scan_clean kwargs must contain ONLY document_pk (and the
        signal sentinel). No filename, email, storage_key, or uploader identity.
        """
        from apps.documents.signals import document_scan_clean

        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        received = []
        handler = lambda sender, **kw: received.append(kw)  # noqa: E731
        document_scan_clean.connect(handler, weak=False)
        try:
            self._run_clean_scan(doc)
        finally:
            document_scan_clean.disconnect(handler)

        self.assertEqual(len(received), 1)
        signal_kwargs = received[0]
        # Exact allowed keys (Django adds "signal" automatically)
        self.assertEqual(
            set(signal_kwargs.keys()),
            {"document_pk", "signal"},
            "PIPEDA: document_scan_clean signal must contain exactly "
            "{document_pk, signal} — no extra keys.",
        )
        # Explicit PII key names must be absent
        forbidden = {"email", "username", "original_filename", "storage_key", "uploaded_by"}
        found_pii = forbidden & set(signal_kwargs.keys())
        self.assertFalse(
            found_pii,
            f"PIPEDA: PII keys must not appear in document_scan_clean signal: {found_pii}",
        )

    def test_clean_scan_does_not_fire_quarantined_signal(self):
        """document_quarantined must NOT fire on the clean path."""
        from apps.documents.signals import document_quarantined

        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        quarantine_received = []
        handler = lambda sender, **kw: quarantine_received.append(kw)  # noqa: E731
        document_quarantined.connect(handler, weak=False)
        try:
            self._run_clean_scan(doc)
        finally:
            document_quarantined.disconnect(handler)

        self.assertEqual(
            len(quarantine_received),
            0,
            "document_quarantined must NOT fire when scan result is clean.",
        )

    # ── Idempotency ───────────────────────────────────────────────────────────

    def test_clean_scan_idempotent_already_active(self):
        """
        If the document is already ACTIVE (processed by a concurrent worker),
        the task must be a no-op: status unchanged, no signal fired.
        """
        from apps.documents.signals import document_scan_clean

        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.ACTIVE)
        original_completed_at = doc.scan_completed_at

        received = []
        handler = lambda sender, **kw: received.append(kw)  # noqa: E731
        document_scan_clean.connect(handler, weak=False)
        try:
            self._run_clean_scan(doc)
        finally:
            document_scan_clean.disconnect(handler)

        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.ACTIVE)
        self.assertEqual(doc.scan_completed_at, original_completed_at)
        self.assertEqual(
            len(received),
            0,
            "document_scan_clean must NOT fire on the idempotent ACTIVE path.",
        )

    def test_clean_scan_idempotent_already_quarantined(self):
        """
        If the document is already QUARANTINED (e.g. another worker ran first),
        the task must be a no-op: status unchanged, neither signal fired.
        """
        from apps.documents.signals import document_scan_clean, document_quarantined

        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.QUARANTINED)

        clean_received = []
        quarantine_received = []
        clean_handler = lambda sender, **kw: clean_received.append(kw)  # noqa: E731
        q_handler = lambda sender, **kw: quarantine_received.append(kw)  # noqa: E731
        document_scan_clean.connect(clean_handler, weak=False)
        document_quarantined.connect(q_handler, weak=False)
        try:
            self._run_clean_scan(doc)
        finally:
            document_scan_clean.disconnect(clean_handler)
            document_quarantined.disconnect(q_handler)

        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.QUARANTINED)
        self.assertEqual(len(clean_received), 0)
        self.assertEqual(len(quarantine_received), 0)


# ─────────────────────────────────────────────────────────────────────────────
# 2. ClamAVInfectedPathTests
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=CIVICOS_CLAMAV)
class ClamAVInfectedPathTests(TestCase):
    """
    Tests for the infected path: ClamAV returns a virus name → QUARANTINED transition.

    _scan_with_clamav is patched to return "Eicar-Test-Signature" so these tests
    exercise the _mark_document_quarantined_clamav helper and its side-effects
    (storage deletion, signal dispatch).
    """

    VIRUS_NAME = "Eicar-Test-Signature"

    def setUp(self):
        self.user = make_user()
        self.category = make_category()

    def _run_infected_scan(self, doc: Document) -> None:
        """Invoke scan_document with _scan_with_clamav stubbed to return a virus name."""
        with patch(
            "apps.documents.tasks._scan_with_clamav",
            return_value=self.VIRUS_NAME,
        ):
            # _mark_document_quarantined_clamav imports default_storage locally, so
            # we must patch at the django module level to intercept those local imports.
            with patch("django.core.files.storage.default_storage") as mock_storage:
                mock_storage.exists.return_value = True
                scan_document.run(str(doc.pk))
                self._mock_storage = mock_storage

    # ── Status and field assertions ───────────────────────────────────────────

    def test_infected_scan_marks_document_quarantined(self):
        """Infected scan must transition the document to QUARANTINED."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        self._run_infected_scan(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.QUARANTINED)

    def test_infected_scan_sets_scan_engine_result(self):
        """scan_engine_result must record 'FOUND: <VirusName>' for audit purposes."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        self._run_infected_scan(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.scan_engine_result, f"FOUND: {self.VIRUS_NAME}")

    def test_infected_scan_sets_scan_completed_at(self):
        """scan_completed_at must be stamped when the document is quarantined."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        before = timezone.now()
        self._run_infected_scan(doc)
        doc.refresh_from_db()
        self.assertIsNotNone(doc.scan_completed_at)
        self.assertGreaterEqual(doc.scan_completed_at, before)

    # ── Signal assertions ─────────────────────────────────────────────────────

    def test_infected_scan_fires_quarantined_signal(self):
        """document_quarantined must fire exactly once with document_pk and scan_engine_result."""
        from apps.documents.signals import document_quarantined

        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        received = []
        handler = lambda sender, **kw: received.append(kw)  # noqa: E731
        document_quarantined.connect(handler, weak=False)
        try:
            self._run_infected_scan(doc)
        finally:
            document_quarantined.disconnect(handler)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["document_pk"], str(doc.pk))
        self.assertEqual(received[0]["scan_engine_result"], f"FOUND: {self.VIRUS_NAME}")

    def test_infected_scan_signal_pipeda_no_uploader_identity(self):
        """
        PIPEDA: document_quarantined signal kwargs must NOT include uploader PII
        (email, original_filename, storage_key, uploaded_by).
        """
        from apps.documents.signals import document_quarantined

        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        received = []
        handler = lambda sender, **kw: received.append(kw)  # noqa: E731
        document_quarantined.connect(handler, weak=False)
        try:
            self._run_infected_scan(doc)
        finally:
            document_quarantined.disconnect(handler)

        self.assertEqual(len(received), 1)
        signal_kwargs = received[0]
        # Exact allowed keys
        self.assertEqual(
            set(signal_kwargs.keys()),
            {"document_pk", "scan_engine_result", "signal"},
            "PIPEDA: document_quarantined signal must contain exactly "
            "{document_pk, scan_engine_result, signal} — no extra keys.",
        )
        # Explicit PII key names must be absent
        forbidden = {"email", "username", "original_filename", "storage_key", "uploaded_by"}
        found_pii = forbidden & set(signal_kwargs.keys())
        self.assertFalse(
            found_pii,
            f"PIPEDA: PII keys must not appear in document_quarantined signal: {found_pii}",
        )

    def test_infected_scan_does_not_fire_scan_clean_signal(self):
        """document_scan_clean must NOT fire on the infected path."""
        from apps.documents.signals import document_scan_clean

        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        clean_received = []
        handler = lambda sender, **kw: clean_received.append(kw)  # noqa: E731
        document_scan_clean.connect(handler, weak=False)
        try:
            self._run_infected_scan(doc)
        finally:
            document_scan_clean.disconnect(handler)

        self.assertEqual(
            len(clean_received),
            0,
            "document_scan_clean must NOT fire when a virus is detected.",
        )

    # ── Storage deletion ──────────────────────────────────────────────────────

    def test_infected_scan_deletes_storage_object(self):
        """
        The infected file must be deleted from storage to prevent access.
        default_storage.delete must be called with the document's storage_key.
        """
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        storage_key = doc.storage_key

        with patch(
            "apps.documents.tasks._scan_with_clamav",
            return_value=self.VIRUS_NAME,
        ):
            # _mark_document_quarantined_clamav imports default_storage locally inside
            # the function body, so we patch at the django module level.
            with patch("django.core.files.storage.default_storage") as mock_storage:
                mock_storage.exists.return_value = True
                scan_document.run(str(doc.pk))

        mock_storage.exists.assert_called_once_with(storage_key)
        mock_storage.delete.assert_called_once_with(storage_key)

    def test_infected_scan_skips_delete_if_file_not_in_storage(self):
        """
        If default_storage.exists() returns False (file never written or already
        deleted), delete must NOT be called — no double-delete error.
        """
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)

        with patch(
            "apps.documents.tasks._scan_with_clamav",
            return_value=self.VIRUS_NAME,
        ):
            with patch("django.core.files.storage.default_storage") as mock_storage:
                mock_storage.exists.return_value = False
                scan_document.run(str(doc.pk))

        mock_storage.delete.assert_not_called()

    # ── Idempotency ───────────────────────────────────────────────────────────

    def test_infected_scan_idempotent_already_quarantined(self):
        """
        If the document is already QUARANTINED (processed by a concurrent worker),
        the task must be a no-op: status unchanged, no signal fired, no storage delete.
        """
        from apps.documents.signals import document_quarantined

        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.QUARANTINED)
        received = []
        handler = lambda sender, **kw: received.append(kw)  # noqa: E731
        document_quarantined.connect(handler, weak=False)
        try:
            with patch(
                "apps.documents.tasks._scan_with_clamav",
                return_value=self.VIRUS_NAME,
            ):
                with patch("django.core.files.storage.default_storage") as mock_storage:
                    mock_storage.exists.return_value = True
                    scan_document.run(str(doc.pk))
        finally:
            document_quarantined.disconnect(handler)

        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.QUARANTINED)
        # Signal must NOT fire: no status transition occurred
        self.assertEqual(
            len(received),
            0,
            "document_quarantined must NOT fire when already QUARANTINED (idempotent).",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. ClamAVRetryTests
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=CIVICOS_CLAMAV)
class ClamAVRetryTests(TestCase):
    """
    Tests for retry behaviour when ClamAV is unreachable or file read fails.

    With CELERY_TASK_ALWAYS_EAGER=True, an unpatched self.retry() inside .run()
    would synchronously re-invoke the task body up to max_retries=5, exhausting
    retries. We patch self.retry() to intercept the first retry call so we can
    assert the correct countdown without triggering the eager retry loop.
    """

    def setUp(self):
        self.user = make_user()
        self.category = make_category()

    def test_connection_error_triggers_retry(self):
        """
        When _scan_with_clamav raises ConnectionError (ClamAV daemon unreachable),
        scan_document must call self.retry() rather than propagating the exception.
        """
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)

        with patch(
            "apps.documents.tasks._scan_with_clamav",
            side_effect=ConnectionError("ClamAV daemon not reachable"),
        ):
            with patch.object(scan_document, "retry", side_effect=Retry()) as mock_retry:
                with self.assertRaises(Retry):
                    scan_document.run(str(doc.pk))

        mock_retry.assert_called_once()

    def test_retry_countdown_exponential_backoff_first_retry(self):
        """
        On the first retry (request.retries == 0), countdown must be (2**0) * 30 = 30.
        """
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)

        with patch(
            "apps.documents.tasks._scan_with_clamav",
            side_effect=ConnectionError("daemon down"),
        ):
            with patch.object(scan_document, "retry", side_effect=Retry()) as mock_retry:
                # Patch request.retries to 0 to simulate first retry
                with patch.object(scan_document.request, "retries", 0, create=True):
                    with self.assertRaises(Retry):
                        scan_document.run(str(doc.pk))

        _, retry_kwargs = mock_retry.call_args
        self.assertEqual(
            retry_kwargs.get("countdown"),
            30,
            "First retry countdown must be (2**0) * 30 = 30 seconds.",
        )

    def test_retry_countdown_exponential_backoff_second_retry(self):
        """
        On the second retry (request.retries == 1), countdown must be (2**1) * 30 = 60.
        """
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)

        with patch(
            "apps.documents.tasks._scan_with_clamav",
            side_effect=ConnectionError("daemon down"),
        ):
            with patch.object(scan_document, "retry", side_effect=Retry()) as mock_retry:
                with patch.object(scan_document.request, "retries", 1, create=True):
                    with self.assertRaises(Retry):
                        scan_document.run(str(doc.pk))

        _, retry_kwargs = mock_retry.call_args
        self.assertEqual(
            retry_kwargs.get("countdown"),
            60,
            "Second retry countdown must be (2**1) * 30 = 60 seconds.",
        )

    def test_retry_countdown_exponential_backoff_third_retry(self):
        """
        On the third retry (request.retries == 2), countdown must be (2**2) * 30 = 120.
        """
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)

        with patch(
            "apps.documents.tasks._scan_with_clamav",
            side_effect=ConnectionError("daemon down"),
        ):
            with patch.object(scan_document, "retry", side_effect=Retry()) as mock_retry:
                with patch.object(scan_document.request, "retries", 2, create=True):
                    with self.assertRaises(Retry):
                        scan_document.run(str(doc.pk))

        _, retry_kwargs = mock_retry.call_args
        self.assertEqual(
            retry_kwargs.get("countdown"),
            120,
            "Third retry countdown must be (2**2) * 30 = 120 seconds.",
        )

    def test_io_error_reading_file_triggers_retry(self):
        """
        If _scan_with_clamav raises IOError (file cannot be read from storage),
        the task must retry — same retry path as a ClamAV connection error.
        """
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)

        with patch(
            "apps.documents.tasks._scan_with_clamav",
            side_effect=IOError("S3 read failed"),
        ):
            with patch.object(scan_document, "retry", side_effect=Retry()) as mock_retry:
                with self.assertRaises(Retry):
                    scan_document.run(str(doc.pk))

        mock_retry.assert_called_once()

    def test_max_retries_exhausted_calls_quarantine_on_failure(self):
        """
        When on_failure() fires after max retries are exhausted, it must call
        _quarantine_on_scan_failure() with the correct doc_pk.

        We invoke on_failure() directly (as the Celery worker machinery would)
        and verify that _quarantine_on_scan_failure is delegated to.
        """
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)

        with patch("apps.documents.tasks._quarantine_on_scan_failure") as mock_quarantine:
            scan_document.on_failure(
                exc=ConnectionError("max retries exhausted"),
                task_id="test-task-id",
                args=(str(doc.pk),),
                kwargs={},
                einfo=None,
            )

        mock_quarantine.assert_called_once_with(
            doc_pk=str(doc.pk),
            exc=ANY,
        )

    def test_runtime_error_from_clamav_error_status_triggers_retry(self):
        """
        If _scan_with_clamav raises RuntimeError (ClamAV returned ERROR status),
        the task must retry rather than propagate the error.
        """
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)

        with patch(
            "apps.documents.tasks._scan_with_clamav",
            side_effect=RuntimeError("ClamAV scan ERROR: internal error"),
        ):
            with patch.object(scan_document, "retry", side_effect=Retry()) as mock_retry:
                with self.assertRaises(Retry):
                    scan_document.run(str(doc.pk))

        mock_retry.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 4. ClamAVDocumentNotFoundTests
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=CIVICOS_CLAMAV)
class ClamAVDocumentNotFoundTests(TestCase):
    """
    Tests for document-not-found during the initial lock acquisition step.

    In the ClamAV path, the document is fetched with select_for_update() inside
    an atomic block (Step 1). If Document.DoesNotExist is raised (DB replica lag
    or race condition), the task must retry — not raise DoesNotExist silently.
    """

    def setUp(self):
        self.user = make_user()
        self.category = make_category()

    def test_doc_not_found_in_clamav_path_triggers_retry(self):
        """
        If the document is not found during lock acquisition (Step 1),
        scan_document must call self.retry() with countdown=5 (replica-lag backoff).
        """
        fake_pk = str(uuid.uuid4())

        with patch.object(scan_document, "retry", side_effect=Retry()) as mock_retry:
            with self.assertRaises(Retry):
                scan_document.run(fake_pk)

        mock_retry.assert_called_once()
        _, retry_kwargs = mock_retry.call_args
        self.assertEqual(
            retry_kwargs.get("countdown"),
            5,
            "M-6: document-not-found retry must use countdown=5 for DB replica-lag backoff.",
        )

    def test_doc_not_found_does_not_fire_any_signal(self):
        """
        If the document is not found, neither document_scan_clean nor
        document_quarantined must fire (no transition occurred).
        """
        from apps.documents.signals import document_quarantined, document_scan_clean

        fake_pk = str(uuid.uuid4())
        clean_received = []
        quarantine_received = []
        clean_handler = lambda sender, **kw: clean_received.append(kw)  # noqa: E731
        q_handler = lambda sender, **kw: quarantine_received.append(kw)  # noqa: E731
        document_scan_clean.connect(clean_handler, weak=False)
        document_quarantined.connect(q_handler, weak=False)
        try:
            with patch.object(scan_document, "retry", side_effect=Retry()):
                try:
                    scan_document.run(fake_pk)
                except Retry:
                    pass
        finally:
            document_scan_clean.disconnect(clean_handler)
            document_quarantined.disconnect(q_handler)

        self.assertEqual(len(clean_received), 0)
        self.assertEqual(len(quarantine_received), 0)


# ─────────────────────────────────────────────────────────────────────────────
# 5. QuarantineOnScanFailureTests (Wave 3 extension)
# ─────────────────────────────────────────────────────────────────────────────


class QuarantineOnScanFailureWave3Tests(TestCase):
    """
    Additional Wave 3 tests for _quarantine_on_scan_failure.

    The Wave 2 QuarantineOnScanFailureTests in test_tasks.py cover:
      - SCANNING → QUARANTINED transition
      - scan_engine_result encoding (exception type only)
      - scan_completed_at stamping
      - document_quarantined signal firing
      - PIPEDA: no PII in signal kwargs
      - idempotency (doc already QUARANTINED)
      - doc not found does not raise
      - on_failure args/kwargs extraction

    These Wave 3 tests add coverage for the ClamAV-specific context and
    verify the PII contract specifically in the scan_engine_result value.
    """

    def setUp(self):
        self.user = make_user()
        self.category = make_category()

    def test_quarantine_on_scan_failure_marks_quarantined(self):
        """Direct call to _quarantine_on_scan_failure must transition SCANNING → QUARANTINED."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        _quarantine_on_scan_failure(doc_pk=str(doc.pk), exc=ConnectionError("timeout"))
        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.QUARANTINED)

    def test_quarantine_on_scan_failure_sets_scan_engine_result(self):
        """scan_engine_result must encode SCAN_FAILURE:<ExceptionType> — no PII."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        _quarantine_on_scan_failure(
            doc_pk=str(doc.pk),
            exc=ConnectionError("192.168.1.1:3310 refused"),
        )
        doc.refresh_from_db()
        self.assertEqual(doc.scan_engine_result, "SCAN_FAILURE:ConnectionError")

    def test_quarantine_on_scan_failure_no_pii_in_result(self):
        """
        PIPEDA: scan_engine_result must NOT contain the exception message
        (which might contain an IP address, hostname, or path).
        """
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        sensitive_message = "user@example.com refused connection"
        _quarantine_on_scan_failure(
            doc_pk=str(doc.pk),
            exc=ConnectionError(sensitive_message),
        )
        doc.refresh_from_db()
        self.assertNotIn(
            sensitive_message,
            doc.scan_engine_result,
            "PIPEDA: exception message (potential PII) must not appear in scan_engine_result.",
        )

    def test_quarantine_on_scan_failure_fires_signal(self):
        """document_quarantined must fire with document_pk and scan_engine_result."""
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
        self.assertEqual(received[0]["scan_engine_result"], "SCAN_FAILURE:RuntimeError")

    def test_quarantine_on_scan_failure_doc_not_found_does_not_raise(self):
        """If the document has been deleted, the helper must log and return gracefully."""
        fake_pk = str(uuid.uuid4())
        # Must not raise regardless of exception type
        _quarantine_on_scan_failure(doc_pk=fake_pk, exc=RuntimeError("err"))

    def test_quarantine_on_scan_failure_idempotent_already_quarantined(self):
        """If doc is already QUARANTINED, helper must not overwrite scan_engine_result."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.QUARANTINED)
        doc.scan_engine_result = "ORIGINAL_RESULT"
        doc.save(update_fields=["scan_engine_result", "updated_at"])

        _quarantine_on_scan_failure(doc_pk=str(doc.pk), exc=RuntimeError("err"))
        doc.refresh_from_db()

        self.assertEqual(doc.scan_status, Document.ScanStatus.QUARANTINED)
        self.assertEqual(
            doc.scan_engine_result,
            "ORIGINAL_RESULT",
            "Idempotency: scan_engine_result must not be overwritten when already QUARANTINED.",
        )

    def test_quarantine_on_scan_failure_signal_no_pii(self):
        """
        PIPEDA: document_quarantined signal from on_failure path must not include PII.
        Exact allowed keys: {document_pk, scan_engine_result, signal}.
        """
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
        signal_kwargs = received[0]
        self.assertEqual(
            set(signal_kwargs.keys()),
            {"document_pk", "scan_engine_result", "signal"},
        )
        forbidden = {"email", "username", "original_filename", "storage_key", "uploaded_by"}
        found_pii = forbidden & set(signal_kwargs.keys())
        self.assertFalse(found_pii, f"PIPEDA: PII found in quarantine signal: {found_pii}")


# ─────────────────────────────────────────────────────────────────────────────
# 6. ScanDocumentHelperTests
# ─────────────────────────────────────────────────────────────────────────────


class ScanWithClamavTests(TestCase):
    """
    Direct unit tests for _scan_with_clamav().

    pyclamd is NOT installed in the test environment (it requires a running clamd
    daemon at build time).  We inject a MagicMock module into sys.modules so that
    ``import pyclamd`` inside _scan_with_clamav() returns our stub without actually
    importing the real library.  This is the correct pattern when the real module
    cannot be installed in CI.

    Each test builds a fresh ``mock_pyclamd`` MagicMock, configures its
    ``ClamdNetworkSocket`` attribute, then wraps the call under
    ``patch.dict(sys.modules, {"pyclamd": mock_pyclamd})``.
    """

    def _make_civicos(self) -> dict:
        return {
            "CLAMAV_HOST": "clamav.internal",
            "CLAMAV_PORT": 3310,
            "CLAMAV_TIMEOUT": 30,
        }

    def _mock_storage_open(self, mock_storage: MagicMock, data: bytes = b"fake bytes") -> None:
        """Configure mock_storage.open() as a context manager returning data."""
        mock_fh = MagicMock()
        mock_fh.read.return_value = data
        mock_storage.open.return_value.__enter__.return_value = mock_fh

    def test_scan_with_clamav_clean_returns_ok(self):
        """
        When cd.instream() returns None (clean), _scan_with_clamav must return 'OK'.
        """
        import sys

        civicos = self._make_civicos()

        mock_pyclamd = MagicMock()
        mock_cd = MagicMock()
        mock_cd.instream.return_value = None  # clean
        mock_pyclamd.ClamdNetworkSocket.return_value = mock_cd

        with patch.dict(sys.modules, {"pyclamd": mock_pyclamd}):
            with patch("django.core.files.storage.default_storage") as mock_storage:
                self._mock_storage_open(mock_storage)
                result = _scan_with_clamav(storage_key="quarantine/test.bin", civicos=civicos)

        self.assertEqual(result, "OK")

    def test_scan_with_clamav_infected_returns_virus_name(self):
        """
        When cd.instream() returns {"stream": ("FOUND", "Eicar-Test-Signature")},
        _scan_with_clamav must return the virus name string.
        """
        import sys

        civicos = self._make_civicos()

        mock_pyclamd = MagicMock()
        mock_cd = MagicMock()
        mock_cd.instream.return_value = {"stream": ("FOUND", "Eicar-Test-Signature")}
        mock_pyclamd.ClamdNetworkSocket.return_value = mock_cd

        with patch.dict(sys.modules, {"pyclamd": mock_pyclamd}):
            with patch("django.core.files.storage.default_storage") as mock_storage:
                self._mock_storage_open(mock_storage)
                result = _scan_with_clamav(storage_key="quarantine/test.bin", civicos=civicos)

        self.assertEqual(result, "Eicar-Test-Signature")

    def test_scan_with_clamav_clamd_error_status_raises_runtime(self):
        """
        When cd.instream() returns {"stream": ("ERROR", "...")}, _scan_with_clamav
        must raise RuntimeError so the caller retries rather than permanently
        quarantining a potentially clean file.
        """
        import sys

        civicos = self._make_civicos()

        mock_pyclamd = MagicMock()
        mock_cd = MagicMock()
        mock_cd.instream.return_value = {"stream": ("ERROR", "internal clamd error")}
        mock_pyclamd.ClamdNetworkSocket.return_value = mock_cd

        with patch.dict(sys.modules, {"pyclamd": mock_pyclamd}):
            with patch("django.core.files.storage.default_storage") as mock_storage:
                self._mock_storage_open(mock_storage)
                with self.assertRaises(RuntimeError) as ctx:
                    _scan_with_clamav(storage_key="quarantine/test.bin", civicos=civicos)

        self.assertIn("ClamAV scan ERROR", str(ctx.exception))

    def test_scan_with_clamav_file_read_error_raises_ioerror(self):
        """
        When default_storage.open() raises an exception, _scan_with_clamav must
        raise IOError so the caller can retry.
        """
        import sys

        civicos = self._make_civicos()

        mock_pyclamd = MagicMock()
        mock_pyclamd.ClamdNetworkSocket.return_value = MagicMock()

        with patch.dict(sys.modules, {"pyclamd": mock_pyclamd}):
            with patch("django.core.files.storage.default_storage") as mock_storage:
                mock_storage.open.side_effect = OSError("S3 read timeout")
                with self.assertRaises(IOError):
                    _scan_with_clamav(storage_key="quarantine/test.bin", civicos=civicos)

    def test_scan_with_clamav_passes_correct_host_port_timeout(self):
        """
        _scan_with_clamav must pass host, port, and timeout from civicos settings
        to pyclamd.ClamdNetworkSocket().
        """
        import sys

        civicos = {
            "CLAMAV_HOST": "clamav.example.com",
            "CLAMAV_PORT": 9999,
            "CLAMAV_TIMEOUT": 60,
        }

        mock_pyclamd = MagicMock()
        mock_cd = MagicMock()
        mock_cd.instream.return_value = None
        mock_pyclamd.ClamdNetworkSocket.return_value = mock_cd

        with patch.dict(sys.modules, {"pyclamd": mock_pyclamd}):
            with patch("django.core.files.storage.default_storage") as mock_storage:
                self._mock_storage_open(mock_storage)
                _scan_with_clamav(storage_key="quarantine/test.bin", civicos=civicos)

        mock_pyclamd.ClamdNetworkSocket.assert_called_once_with(
            host="clamav.example.com",
            port=9999,
            timeout=60,
        )

    def test_scan_with_clamav_streams_file_bytes_to_instream(self):
        """
        _scan_with_clamav must read the full file bytes and pass them to
        cd.instream() wrapped in a BytesIO object.
        """
        import io as _io
        import sys

        civicos = self._make_civicos()
        file_data = b"PDF file content here"

        mock_pyclamd = MagicMock()
        mock_cd = MagicMock()
        mock_cd.instream.return_value = None
        mock_pyclamd.ClamdNetworkSocket.return_value = mock_cd

        with patch.dict(sys.modules, {"pyclamd": mock_pyclamd}):
            with patch("django.core.files.storage.default_storage") as mock_storage:
                self._mock_storage_open(mock_storage, data=file_data)
                _scan_with_clamav(storage_key="quarantine/test.bin", civicos=civicos)

        # Verify instream was called once
        mock_cd.instream.assert_called_once()
        # The argument must be a BytesIO containing the file bytes
        call_args = mock_cd.instream.call_args[0]
        self.assertEqual(len(call_args), 1)
        byte_stream = call_args[0]
        self.assertIsInstance(byte_stream, _io.BytesIO)
        self.assertEqual(byte_stream.getvalue(), file_data)

    def test_scan_with_clamav_connection_error_propagates(self):
        """
        When pyclamd raises ConnectionError (daemon unreachable),
        _scan_with_clamav must let it propagate so scan_document retries.
        """
        import sys

        civicos = self._make_civicos()

        mock_pyclamd = MagicMock()
        mock_pyclamd.ClamdNetworkSocket.side_effect = ConnectionError("Connection refused")

        with patch.dict(sys.modules, {"pyclamd": mock_pyclamd}):
            with patch("django.core.files.storage.default_storage"):
                with self.assertRaises(ConnectionError):
                    _scan_with_clamav(storage_key="quarantine/test.bin", civicos=civicos)


class MarkDocumentActiveClamavTests(TestCase):
    """
    Direct unit tests for _mark_document_active_clamav().
    """

    def setUp(self):
        self.user = make_user()
        self.category = make_category()

    def test_mark_document_active_clamav_transitions_status(self):
        """SCANNING → ACTIVE transition must succeed."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        _mark_document_active_clamav(doc_pk=str(doc.pk))
        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.ACTIVE)

    def test_mark_document_active_clamav_sets_scan_engine_result_ok(self):
        """scan_engine_result must be set to 'OK'."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        _mark_document_active_clamav(doc_pk=str(doc.pk))
        doc.refresh_from_db()
        self.assertEqual(doc.scan_engine_result, "OK")

    def test_mark_document_active_clamav_stamps_scan_completed_at(self):
        """scan_completed_at must be set to approximately now."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        before = timezone.now()
        _mark_document_active_clamav(doc_pk=str(doc.pk))
        doc.refresh_from_db()
        self.assertIsNotNone(doc.scan_completed_at)
        self.assertGreaterEqual(doc.scan_completed_at, before)

    def test_mark_document_active_clamav_fires_document_scan_clean(self):
        """document_scan_clean signal must fire with document_pk."""
        from apps.documents.signals import document_scan_clean

        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        received = []
        handler = lambda sender, **kw: received.append(kw)  # noqa: E731
        document_scan_clean.connect(handler, weak=False)
        try:
            _mark_document_active_clamav(doc_pk=str(doc.pk))
        finally:
            document_scan_clean.disconnect(handler)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["document_pk"], str(doc.pk))

    def test_mark_document_active_clamav_idempotent_already_active(self):
        """
        If the document is already ACTIVE, the helper must return without
        modifying any fields and without firing the signal.
        """
        from apps.documents.signals import document_scan_clean

        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.ACTIVE)
        original_completed_at = doc.scan_completed_at

        received = []
        handler = lambda sender, **kw: received.append(kw)  # noqa: E731
        document_scan_clean.connect(handler, weak=False)
        try:
            _mark_document_active_clamav(doc_pk=str(doc.pk))
        finally:
            document_scan_clean.disconnect(handler)

        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.ACTIVE)
        self.assertEqual(doc.scan_completed_at, original_completed_at)
        self.assertEqual(len(received), 0)

    def test_mark_document_active_clamav_idempotent_already_quarantined(self):
        """If already QUARANTINED, must be a no-op (no status change, no signal)."""
        from apps.documents.signals import document_scan_clean

        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.QUARANTINED)

        received = []
        handler = lambda sender, **kw: received.append(kw)  # noqa: E731
        document_scan_clean.connect(handler, weak=False)
        try:
            _mark_document_active_clamav(doc_pk=str(doc.pk))
        finally:
            document_scan_clean.disconnect(handler)

        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.QUARANTINED)
        self.assertEqual(len(received), 0)

    def test_mark_document_active_clamav_doc_not_found_does_not_raise(self):
        """If the document was deleted, the helper must log and return without raising."""
        fake_pk = str(uuid.uuid4())
        # Must not raise
        _mark_document_active_clamav(doc_pk=fake_pk)


class MarkDocumentQuarantinedClamavTests(TestCase):
    """
    Direct unit tests for _mark_document_quarantined_clamav().
    """

    def setUp(self):
        self.user = make_user()
        self.category = make_category()

    def test_mark_document_quarantined_clamav_transitions_status(self):
        """SCANNING → QUARANTINED transition must succeed."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        with patch("django.core.files.storage.default_storage") as mock_storage:
            mock_storage.exists.return_value = False
            _mark_document_quarantined_clamav(
                doc_pk=str(doc.pk),
                virus_name="Eicar-Test-Signature",
            )
        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.QUARANTINED)

    def test_mark_document_quarantined_clamav_sets_scan_engine_result(self):
        """scan_engine_result must be 'FOUND: <VirusName>'."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        with patch("django.core.files.storage.default_storage") as mock_storage:
            mock_storage.exists.return_value = False
            _mark_document_quarantined_clamav(
                doc_pk=str(doc.pk),
                virus_name="Win.Trojan.Agent",
            )
        doc.refresh_from_db()
        self.assertEqual(doc.scan_engine_result, "FOUND: Win.Trojan.Agent")

    def test_mark_document_quarantined_clamav_stamps_scan_completed_at(self):
        """scan_completed_at must be stamped."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        before = timezone.now()
        with patch("django.core.files.storage.default_storage") as mock_storage:
            mock_storage.exists.return_value = False
            _mark_document_quarantined_clamav(
                doc_pk=str(doc.pk),
                virus_name="Eicar-Test-Signature",
            )
        doc.refresh_from_db()
        self.assertIsNotNone(doc.scan_completed_at)
        self.assertGreaterEqual(doc.scan_completed_at, before)

    def test_mark_document_quarantined_clamav_fires_document_quarantined_signal(self):
        """document_quarantined signal must fire with correct kwargs."""
        from apps.documents.signals import document_quarantined

        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        received = []
        handler = lambda sender, **kw: received.append(kw)  # noqa: E731
        document_quarantined.connect(handler, weak=False)
        try:
            with patch("django.core.files.storage.default_storage") as mock_storage:
                mock_storage.exists.return_value = False
                _mark_document_quarantined_clamav(
                    doc_pk=str(doc.pk),
                    virus_name="Eicar-Test-Signature",
                )
        finally:
            document_quarantined.disconnect(handler)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["document_pk"], str(doc.pk))
        self.assertEqual(received[0]["scan_engine_result"], "FOUND: Eicar-Test-Signature")

    def test_mark_document_quarantined_clamav_deletes_storage_object(self):
        """The infected file must be deleted from storage."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        storage_key = doc.storage_key

        with patch("django.core.files.storage.default_storage") as mock_storage:
            mock_storage.exists.return_value = True
            _mark_document_quarantined_clamav(
                doc_pk=str(doc.pk),
                virus_name="Eicar-Test-Signature",
            )

        mock_storage.exists.assert_called_once_with(storage_key)
        mock_storage.delete.assert_called_once_with(storage_key)

    def test_mark_document_quarantined_clamav_idempotent_already_quarantined(self):
        """
        If already QUARANTINED, must be a no-op: no field changes, no signal, no delete.
        """
        from apps.documents.signals import document_quarantined

        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.QUARANTINED)
        doc.scan_engine_result = "FOUND: AlreadySet"
        doc.save(update_fields=["scan_engine_result", "updated_at"])

        received = []
        handler = lambda sender, **kw: received.append(kw)  # noqa: E731
        document_quarantined.connect(handler, weak=False)
        try:
            with patch("django.core.files.storage.default_storage") as mock_storage:
                _mark_document_quarantined_clamav(
                    doc_pk=str(doc.pk),
                    virus_name="Eicar-Test-Signature",
                )
        finally:
            document_quarantined.disconnect(handler)

        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.QUARANTINED)
        self.assertEqual(doc.scan_engine_result, "FOUND: AlreadySet")
        self.assertEqual(
            len(received),
            0,
            "document_quarantined must NOT fire when already QUARANTINED (idempotent).",
        )

    def test_mark_document_quarantined_clamav_doc_not_found_does_not_raise(self):
        """If the document was concurrently deleted, helper must return without raising."""
        fake_pk = str(uuid.uuid4())
        with patch("django.core.files.storage.default_storage"):
            # Must not raise
            _mark_document_quarantined_clamav(
                doc_pk=fake_pk,
                virus_name="Eicar-Test-Signature",
            )

    def test_mark_document_quarantined_clamav_storage_delete_failure_does_not_raise(self):
        """
        If default_storage.delete() raises (transient S3 error), the helper must
        log the error and return without re-raising. The document is already
        QUARANTINED in the DB — storage cleanup is a secondary concern.
        """
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)

        with patch("django.core.files.storage.default_storage") as mock_storage:
            mock_storage.exists.return_value = True
            mock_storage.delete.side_effect = OSError("S3 delete timeout")
            # Must not raise
            _mark_document_quarantined_clamav(
                doc_pk=str(doc.pk),
                virus_name="Eicar-Test-Signature",
            )

        doc.refresh_from_db()
        # Despite the storage failure, the document must still be QUARANTINED in the DB
        self.assertEqual(doc.scan_status, Document.ScanStatus.QUARANTINED)
