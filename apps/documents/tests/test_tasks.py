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
from unittest.mock import patch

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


@override_settings(CIVICOS=CIVICOS_DEV)
class ScanDocumentDevBypassTests(TestCase):
    """
    Tests for scan_document() in dev mode (CLAMAV_HOST="" + CLAMAV_REQUIRED=False).
    """

    def setUp(self):
        self.user = make_user()
        self.category = make_category()

    def test_dev_bypass_marks_document_active(self):
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        scan_document(str(doc.pk))
        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.ACTIVE)

    def test_dev_bypass_sets_scan_completed_at(self):
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        before = timezone.now()
        scan_document(str(doc.pk))
        doc.refresh_from_db()
        self.assertIsNotNone(doc.scan_completed_at)
        self.assertGreaterEqual(doc.scan_completed_at, before)

    def test_dev_bypass_sets_scan_engine_result_to_dev_bypass(self):
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        scan_document(str(doc.pk))
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
            scan_document(str(doc.pk))
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
            scan_document(str(doc.pk))
        finally:
            document_scan_clean.disconnect(handler)

        kwargs_str = str(received[0])
        self.assertNotIn("email", kwargs_str)
        self.assertNotIn("username", kwargs_str)
        self.assertNotIn("original_filename", kwargs_str)

    # ── Idempotency ───────────────────────────────────────────────────────────

    def test_idempotent_already_active(self):
        """If document is already ACTIVE (from a concurrent worker), task is a no-op."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.ACTIVE)
        original_completed_at = doc.scan_completed_at

        scan_document(str(doc.pk))
        doc.refresh_from_db()

        # Status should be unchanged
        self.assertEqual(doc.scan_status, Document.ScanStatus.ACTIVE)
        self.assertEqual(doc.scan_completed_at, original_completed_at)

    def test_idempotent_already_quarantined(self):
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.QUARANTINED)
        scan_document(str(doc.pk))
        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.QUARANTINED)

    def test_idempotent_already_deleted(self):
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.DELETED)
        scan_document(str(doc.pk))
        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.DELETED)

    def test_idempotent_does_not_fire_signal_when_already_active(self):
        """Signal must NOT fire if we skipped the transition (idempotency path)."""
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.ACTIVE)
        received = []

        from apps.documents.signals import document_scan_clean
        # weak=False: prevents GC of the anonymous handler before signal assertion.
        handler = lambda sender, **kw: received.append(kw)  # noqa: E731
        document_scan_clean.connect(handler, weak=False)
        try:
            scan_document(str(doc.pk))
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
            scan_document(str(doc.pk))
        self.assertIn("DEV_BYPASS", str(ctx.exception))
        # Document must remain in SCANNING — no status change occurred
        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.SCANNING)

    # ── Document not found ────────────────────────────────────────────────────

    def test_document_not_found_logs_warning_and_returns(self):
        """
        If the document does not exist (race condition), the helper logs
        a warning and returns without error. The task itself does not raise.
        """
        # Use a random UUID that has no corresponding DB row
        fake_pk = str(uuid.uuid4())
        # Should not raise — the helper catches DoesNotExist and logs
        scan_document(fake_pk)


@override_settings(CIVICOS=CIVICOS_PROD)
class ScanDocumentProdTests(TestCase):
    """
    Tests for scan_document() in production mode (CLAMAV_REQUIRED=True).
    The full ClamAV implementation is in Wave 3; until then, the task raises.
    """

    def setUp(self):
        self.user = make_user()
        self.category = make_category()

    def test_clamav_required_raises_not_implemented(self):
        """
        When ClamAV is configured (prod), scan_document must raise
        NotImplementedError rather than silently bypassing the scan.
        This ensures the dev bypass path can never be activated in production.
        """
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        with self.assertRaises(NotImplementedError):
            scan_document(str(doc.pk))

    def test_clamav_required_does_not_mark_active(self):
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.SCANNING)
        try:
            scan_document(str(doc.pk))
        except NotImplementedError:
            pass
        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.SCANNING)


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
        count = cleanup_stale_pending_uploads()
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
        count = cleanup_stale_pending_uploads()
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
        cleanup_stale_pending_uploads()
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())

    def test_keeps_active_documents_regardless_of_age(self):
        """ACTIVE documents must never be deleted by the cleanup task."""
        doc = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.ACTIVE,
            created_at_override=self._stale_time(),
        )
        cleanup_stale_pending_uploads()
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())

    def test_keeps_quarantined_documents(self):
        doc = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.QUARANTINED,
            created_at_override=self._stale_time(),
        )
        cleanup_stale_pending_uploads()
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())

    def test_returns_zero_when_nothing_to_clean(self):
        count = cleanup_stale_pending_uploads()
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
        count = cleanup_stale_pending_uploads()
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
        count = cleanup_stale_pending_uploads()
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
        count = cleanup_stale_pending_uploads()
        self.assertEqual(count, 1)
        self.assertFalse(Document.objects.filter(pk=old_doc.pk).exists())
        self.assertTrue(Document.objects.filter(pk=recent_doc.pk).exists())
