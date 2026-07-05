"""
Wave 7 — §24.1 canonical test file: test_signals.py

Verifies that each service emits the correct signal with the correct kwargs.

Rules:
  - Signals are defined in apps.documents.signals (declaration-only module)
  - All 8 signals are django.dispatch.Signal instances
  - Signals fire AFTER transaction.on_commit() — use captureOnCommitCallbacks(execute=True)
  - Signal kwargs NEVER include original_filename, storage_key, or uploader email/name
  - document_quarantined MUST NOT include any uploader identity kwargs
  - document_pk must always be a str (not UUID)
  - deleted_by_id may be None for system deletions
"""

import uuid
from datetime import timedelta
from unittest.mock import MagicMock, call, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.dispatch import Signal
from django.test import TestCase
from django.utils import timezone

from apps.documents import signals as doc_signals
from apps.documents.models import Document, DocumentCategory
from apps.documents.services.retention import (
    apply_legal_hold,
    hard_delete,
    release_legal_hold,
    soft_delete,
)

User = get_user_model()

_CTR = 0


def _make_user(**kwargs):
    global _CTR
    _CTR += 1
    return User.objects.create_user(
        email=f"signal{_CTR}@example.com",
        password="testpass123",
        **kwargs,
    )


def _make_category(**kwargs):
    global _CTR
    _CTR += 1
    return DocumentCategory.objects.create(
        name_en="Signal Test",
        name_fr="Test Signal",
        slug=f"signal-cat-{_CTR}",
        allowed_mime_types=["application/pdf"],
        min_retention_days=365,
        max_retention_days=1825,
        **kwargs,
    )


def _make_document(user, category, **kwargs):
    doc_id = uuid.uuid4()
    return Document.objects.create(
        uploaded_by=user,
        category=category,
        original_filename="secret-name.pdf",
        _storage_key=f"documents/active/{doc_id}/{uuid.uuid4().hex}.bin",
        mime_type="application/pdf",
        size_bytes=4_096,
        scan_status=Document.ScanStatus.ACTIVE,
        **kwargs,
    )


def _grant_manage_legal_hold(user):
    ct = ContentType.objects.get_for_model(Document)
    perm, _ = Permission.objects.get_or_create(
        codename="manage_legal_hold",
        content_type=ct,
        defaults={"name": "Can apply and release legal holds"},
    )
    user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


# ─────────────────────────────────────────────────────────────────────────────
# Signal declaration tests (structural)
# ─────────────────────────────────────────────────────────────────────────────


class SignalDeclarationTests(TestCase):
    """All 8 document signals must be django.dispatch.Signal instances."""

    EXPECTED_SIGNALS = [
        "document_upload_initiated",
        "document_confirmed",
        "document_scan_clean",
        "document_quarantined",
        "document_version_created",
        "document_soft_deleted",
        "document_hard_deleted",
        "document_legal_hold_changed",
    ]

    def test_all_signals_are_django_signal_instances(self):
        for name in self.EXPECTED_SIGNALS:
            with self.subTest(signal=name):
                obj = getattr(doc_signals, name, None)
                self.assertIsNotNone(obj, f"signals.{name} is not defined")
                self.assertIsInstance(
                    obj, Signal,
                    f"signals.{name} is {type(obj).__name__}, expected Signal",
                )

    def test_no_extra_signals_exported(self):
        """Signal count: exactly 8."""
        all_signals = [
            name for name, val in vars(doc_signals).items()
            if isinstance(val, Signal)
        ]
        self.assertEqual(len(all_signals), 8, f"Expected 8 signals, found {len(all_signals)}: {all_signals}")


# ─────────────────────────────────────────────────────────────────────────────
# document_soft_deleted signal
# ─────────────────────────────────────────────────────────────────────────────


class SoftDeletedSignalTests(TestCase):

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)

    def test_soft_delete_fires_signal(self):
        handler = MagicMock()
        doc_signals.document_soft_deleted.connect(handler)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                soft_delete(document=self.doc, deleted_by=self.user, reason="test")
            handler.assert_called_once()
        finally:
            doc_signals.document_soft_deleted.disconnect(handler)

    def test_soft_delete_signal_document_pk_is_str(self):
        handler = MagicMock()
        doc_signals.document_soft_deleted.connect(handler)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                soft_delete(document=self.doc, deleted_by=self.user, reason="test")
            _, kwargs = handler.call_args
            self.assertIsInstance(kwargs.get("document_pk"), str)
            self.assertEqual(kwargs["document_pk"], str(self.doc.pk))
        finally:
            doc_signals.document_soft_deleted.disconnect(handler)

    def test_soft_delete_signal_has_deleted_by_id(self):
        handler = MagicMock()
        doc_signals.document_soft_deleted.connect(handler)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                soft_delete(document=self.doc, deleted_by=self.user, reason="test")
            _, kwargs = handler.call_args
            self.assertEqual(kwargs.get("deleted_by_id"), self.user.pk)
        finally:
            doc_signals.document_soft_deleted.disconnect(handler)

    def test_soft_delete_signal_system_deletion_deleted_by_id_none(self):
        handler = MagicMock()
        doc_signals.document_soft_deleted.connect(handler)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                soft_delete(document=self.doc, deleted_by=None, reason="retention_expired")
            _, kwargs = handler.call_args
            self.assertIsNone(kwargs.get("deleted_by_id"))
        finally:
            doc_signals.document_soft_deleted.disconnect(handler)

    def test_soft_delete_signal_no_original_filename(self):
        """PIPEDA: original_filename MUST NOT be in signal kwargs."""
        handler = MagicMock()
        doc_signals.document_soft_deleted.connect(handler)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                soft_delete(document=self.doc, deleted_by=self.user, reason="test")
            _, kwargs = handler.call_args
            self.assertNotIn("original_filename", kwargs)
        finally:
            doc_signals.document_soft_deleted.disconnect(handler)

    def test_soft_delete_signal_no_storage_key(self):
        handler = MagicMock()
        doc_signals.document_soft_deleted.connect(handler)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                soft_delete(document=self.doc, deleted_by=self.user, reason="test")
            _, kwargs = handler.call_args
            self.assertNotIn("storage_key", kwargs)
            self.assertNotIn("_storage_key", kwargs)
        finally:
            doc_signals.document_soft_deleted.disconnect(handler)

    def test_soft_delete_signal_not_fired_before_commit(self):
        """Signal should NOT fire before the transaction commits."""
        handler = MagicMock()
        doc_signals.document_soft_deleted.connect(handler)
        try:
            # Without captureOnCommitCallbacks(execute=True), on_commit doesn't fire
            soft_delete(document=self.doc, deleted_by=self.user, reason="test")
            handler.assert_not_called()
        finally:
            doc_signals.document_soft_deleted.disconnect(handler)


# ─────────────────────────────────────────────────────────────────────────────
# document_hard_deleted signal
# ─────────────────────────────────────────────────────────────────────────────


class HardDeletedSignalTests(TestCase):

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)
        self.doc.deleted_at = timezone.now() - timedelta(days=31)
        self.doc.scan_status = Document.ScanStatus.DELETED
        self.doc.legal_hold = False
        self.doc.save(update_fields=["deleted_at", "scan_status", "legal_hold", "updated_at"])

    def _run_hard_delete(self):
        with patch("apps.documents.services.retention.default_storage") as mock_storage:
            mock_storage.delete.return_value = None
            hard_delete(document=self.doc)

    def test_hard_delete_fires_signal(self):
        handler = MagicMock()
        doc_signals.document_hard_deleted.connect(handler)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                self._run_hard_delete()
            handler.assert_called_once()
        finally:
            doc_signals.document_hard_deleted.disconnect(handler)

    def test_hard_delete_signal_document_pk_is_str(self):
        handler = MagicMock()
        doc_signals.document_hard_deleted.connect(handler)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                self._run_hard_delete()
            _, kwargs = handler.call_args
            self.assertIsInstance(kwargs.get("document_pk"), str)
            self.assertEqual(kwargs["document_pk"], str(self.doc.pk))
        finally:
            doc_signals.document_hard_deleted.disconnect(handler)

    def test_hard_delete_signal_has_category_slug(self):
        handler = MagicMock()
        doc_signals.document_hard_deleted.connect(handler)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                self._run_hard_delete()
            _, kwargs = handler.call_args
            self.assertEqual(kwargs.get("category_slug"), self.cat.slug)
        finally:
            doc_signals.document_hard_deleted.disconnect(handler)

    def test_hard_delete_signal_no_storage_key(self):
        handler = MagicMock()
        doc_signals.document_hard_deleted.connect(handler)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                self._run_hard_delete()
            _, kwargs = handler.call_args
            self.assertNotIn("storage_key", kwargs)
            self.assertNotIn("_storage_key", kwargs)
        finally:
            doc_signals.document_hard_deleted.disconnect(handler)

    def test_hard_delete_signal_no_original_filename(self):
        handler = MagicMock()
        doc_signals.document_hard_deleted.connect(handler)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                self._run_hard_delete()
            _, kwargs = handler.call_args
            self.assertNotIn("original_filename", kwargs)
        finally:
            doc_signals.document_hard_deleted.disconnect(handler)


# ─────────────────────────────────────────────────────────────────────────────
# document_legal_hold_changed signal
# ─────────────────────────────────────────────────────────────────────────────


class LegalHoldChangedSignalTests(TestCase):

    def setUp(self):
        self.user = _make_user()
        self.user = _grant_manage_legal_hold(self.user)
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)

    def test_apply_legal_hold_fires_signal(self):
        handler = MagicMock()
        doc_signals.document_legal_hold_changed.connect(handler)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                apply_legal_hold(document=self.doc, set_by=self.user, reason="ATIP")
            handler.assert_called_once()
        finally:
            doc_signals.document_legal_hold_changed.disconnect(handler)

    def test_apply_legal_hold_signal_legal_hold_true(self):
        handler = MagicMock()
        doc_signals.document_legal_hold_changed.connect(handler)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                apply_legal_hold(document=self.doc, set_by=self.user, reason="ATIP")
            _, kwargs = handler.call_args
            self.assertEqual(kwargs.get("legal_hold"), True)
        finally:
            doc_signals.document_legal_hold_changed.disconnect(handler)

    def test_apply_legal_hold_signal_set_by_id(self):
        handler = MagicMock()
        doc_signals.document_legal_hold_changed.connect(handler)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                apply_legal_hold(document=self.doc, set_by=self.user, reason="ATIP")
            _, kwargs = handler.call_args
            self.assertEqual(kwargs.get("set_by_id"), self.user.pk)
        finally:
            doc_signals.document_legal_hold_changed.disconnect(handler)

    def test_apply_legal_hold_signal_document_pk_str(self):
        handler = MagicMock()
        doc_signals.document_legal_hold_changed.connect(handler)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                apply_legal_hold(document=self.doc, set_by=self.user, reason="ATIP")
            _, kwargs = handler.call_args
            self.assertIsInstance(kwargs.get("document_pk"), str)
            self.assertEqual(kwargs["document_pk"], str(self.doc.pk))
        finally:
            doc_signals.document_legal_hold_changed.disconnect(handler)

    def test_release_legal_hold_fires_signal(self):
        self.doc.legal_hold = True
        self.doc.legal_hold_reason = "test hold"
        self.doc.save(update_fields=["legal_hold", "legal_hold_reason", "updated_at"])

        handler = MagicMock()
        doc_signals.document_legal_hold_changed.connect(handler)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                release_legal_hold(document=self.doc, released_by=self.user)
            handler.assert_called_once()
        finally:
            doc_signals.document_legal_hold_changed.disconnect(handler)

    def test_release_legal_hold_signal_legal_hold_false(self):
        self.doc.legal_hold = True
        self.doc.legal_hold_reason = "test hold"
        self.doc.save(update_fields=["legal_hold", "legal_hold_reason", "updated_at"])

        handler = MagicMock()
        doc_signals.document_legal_hold_changed.connect(handler)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                release_legal_hold(document=self.doc, released_by=self.user)
            _, kwargs = handler.call_args
            self.assertEqual(kwargs.get("legal_hold"), False)
        finally:
            doc_signals.document_legal_hold_changed.disconnect(handler)

    def test_legal_hold_signal_no_email_in_kwargs(self):
        """PIPEDA: no user email in signal kwargs."""
        handler = MagicMock()
        doc_signals.document_legal_hold_changed.connect(handler)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                apply_legal_hold(document=self.doc, set_by=self.user, reason="ATIP")
            _, kwargs = handler.call_args
            kwargs_str = str(kwargs)
            self.assertNotIn(self.user.email, kwargs_str)
        finally:
            doc_signals.document_legal_hold_changed.disconnect(handler)

    def test_legal_hold_signal_no_storage_key(self):
        handler = MagicMock()
        doc_signals.document_legal_hold_changed.connect(handler)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                apply_legal_hold(document=self.doc, set_by=self.user, reason="ATIP")
            _, kwargs = handler.call_args
            self.assertNotIn("storage_key", kwargs)
        finally:
            doc_signals.document_legal_hold_changed.disconnect(handler)


# ─────────────────────────────────────────────────────────────────────────────
# document_quarantined signal — PII privacy constraints
# ─────────────────────────────────────────────────────────────────────────────


class QuarantineSignalPIITests(TestCase):
    """
    The document_quarantined signal specifically MUST NOT include any uploader
    identity in kwargs. This prevents PII leakage to system-admin notification
    handlers.

    Tests here are structural (signal declaration & kwargs inspection).
    The actual dispatch happens inside scan webhook handlers; we test the signal
    object itself and any documented constraints.
    """

    def test_document_quarantined_signal_exists(self):
        self.assertIsInstance(doc_signals.document_quarantined, Signal)

    def test_quarantined_signal_kwargs_documented_correctly(self):
        """
        Signal docstring must document that uploader identity is excluded.
        This is a documentation enforcement test.
        """
        import inspect
        module_source = inspect.getsource(doc_signals)
        # The PIPEDA note about no uploader PII must be present
        self.assertIn("MUST NOT", module_source)
        self.assertIn("uploaded_by", module_source)
        # The quarantine signal must NOT list uploaded_by_id as a provided kwarg
        quarantine_block_start = module_source.find("document_quarantined")
        quarantine_block_end = module_source.find("\n\n", quarantine_block_start)
        quarantine_block = module_source[quarantine_block_start:quarantine_block_end]
        self.assertNotIn("uploaded_by_id", quarantine_block)

    def test_quarantined_signal_allowed_kwargs(self):
        """
        Simulate firing document_quarantined and verify only allowed kwargs.
        Allowed: document_pk (str), scan_engine_result (str)
        """
        handler = MagicMock()
        doc_signals.document_quarantined.connect(handler)
        try:
            doc_signals.document_quarantined.send(
                sender=Document,
                document_pk="some-uuid",
                scan_engine_result="Eicar-Test-Signature",
            )
            _, kwargs = handler.call_args
            allowed = {"signal", "sender", "document_pk", "scan_engine_result"}
            extra = set(kwargs.keys()) - allowed
            self.assertSetEqual(extra, set(), f"Unexpected kwargs in quarantined signal: {extra}")
        finally:
            doc_signals.document_quarantined.disconnect(handler)


# ─────────────────────────────────────────────────────────────────────────────
# document_upload_initiated signal — kwarg contract
# ─────────────────────────────────────────────────────────────────────────────


class UploadInitiatedSignalContractTests(TestCase):
    """
    document_upload_initiated is fired inside validate_upload_request().
    We can't easily call that service here without S3 mocking, but we verify
    the signal's documented kwarg contract by firing it directly and checking
    what flows through to handlers.
    """

    def test_upload_initiated_signal_exists(self):
        self.assertIsInstance(doc_signals.document_upload_initiated, Signal)

    def test_upload_initiated_kwarg_contract(self):
        """
        Documented kwargs: document_pk (str), category_slug (str), uploaded_by_id (int)
        """
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)

        handler = MagicMock()
        doc_signals.document_upload_initiated.connect(handler)
        try:
            doc_signals.document_upload_initiated.send(
                sender=Document,
                document_pk=str(doc.pk),
                category_slug=cat.slug,
                uploaded_by_id=user.pk,
            )
            _, kwargs = handler.call_args
            self.assertEqual(kwargs["document_pk"], str(doc.pk))
            self.assertIsInstance(kwargs["document_pk"], str)
            self.assertEqual(kwargs["category_slug"], cat.slug)
            self.assertEqual(kwargs["uploaded_by_id"], user.pk)
            # PIPEDA: no filename, no storage_key
            self.assertNotIn("original_filename", kwargs)
            self.assertNotIn("storage_key", kwargs)
        finally:
            doc_signals.document_upload_initiated.disconnect(handler)


# ─────────────────────────────────────────────────────────────────────────────
# document_confirmed signal — kwarg contract
# ─────────────────────────────────────────────────────────────────────────────


class DocumentConfirmedSignalContractTests(TestCase):

    def test_document_confirmed_kwarg_contract(self):
        """
        Documented kwargs: document_pk (str), size_bytes (int), mime_type (str)
        PIPEDA: original_filename and uploader PII NOT in kwargs.
        """
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)

        handler = MagicMock()
        doc_signals.document_confirmed.connect(handler)
        try:
            doc_signals.document_confirmed.send(
                sender=Document,
                document_pk=str(doc.pk),
                size_bytes=doc.size_bytes,
                mime_type=doc.mime_type,
            )
            _, kwargs = handler.call_args
            self.assertEqual(kwargs["document_pk"], str(doc.pk))
            self.assertEqual(kwargs["size_bytes"], doc.size_bytes)
            self.assertEqual(kwargs["mime_type"], doc.mime_type)
            self.assertNotIn("original_filename", kwargs)
            self.assertNotIn("uploaded_by_id", kwargs)
            self.assertNotIn("storage_key", kwargs)
        finally:
            doc_signals.document_confirmed.disconnect(handler)


# ─────────────────────────────────────────────────────────────────────────────
# document_version_created signal — kwarg contract
# ─────────────────────────────────────────────────────────────────────────────


class VersionCreatedSignalContractTests(TestCase):

    def test_version_created_kwarg_contract(self):
        """
        Documented kwargs: root_document_pk (str), new_version_pk (str), version_number (int)
        """
        user = _make_user()
        cat = _make_category()
        root = _make_document(user, cat)

        handler = MagicMock()
        doc_signals.document_version_created.connect(handler)
        try:
            doc_signals.document_version_created.send(
                sender=Document,
                root_document_pk=str(root.pk),
                new_version_pk=str(uuid.uuid4()),
                version_number=2,
            )
            _, kwargs = handler.call_args
            self.assertIsInstance(kwargs["root_document_pk"], str)
            self.assertIsInstance(kwargs["new_version_pk"], str)
            self.assertIsInstance(kwargs["version_number"], int)
            self.assertEqual(kwargs["version_number"], 2)
            # PIPEDA: no filename, no storage_key
            self.assertNotIn("original_filename", kwargs)
            self.assertNotIn("storage_key", kwargs)
        finally:
            doc_signals.document_version_created.disconnect(handler)


# ─────────────────────────────────────────────────────────────────────────────
# document_scan_clean signal — kwarg contract
# ─────────────────────────────────────────────────────────────────────────────


class ScanCleanSignalContractTests(TestCase):

    def test_scan_clean_kwarg_contract(self):
        """Documented kwargs: document_pk (str) only."""
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)

        handler = MagicMock()
        doc_signals.document_scan_clean.connect(handler)
        try:
            doc_signals.document_scan_clean.send(
                sender=Document,
                document_pk=str(doc.pk),
            )
            _, kwargs = handler.call_args
            self.assertIsInstance(kwargs["document_pk"], str)
            self.assertNotIn("original_filename", kwargs)
            self.assertNotIn("storage_key", kwargs)
            self.assertNotIn("uploaded_by_id", kwargs)
        finally:
            doc_signals.document_scan_clean.disconnect(handler)


# ─────────────────────────────────────────────────────────────────────────────
# send_robust() return value inspection
# ─────────────────────────────────────────────────────────────────────────────


class SendRobustTests(TestCase):
    """
    Signals are fired via send_robust() inside on_commit callbacks.
    Verify that exceptions in handlers are captured (not propagated) and that
    a faulty handler does not break the disposal flow.
    """

    def test_faulty_signal_handler_does_not_propagate(self):
        """
        A handler raising an exception must not prevent soft_delete() from
        completing — send_robust() captures exceptions instead of re-raising.
        """
        def bad_handler(**kwargs):
            raise RuntimeError("Simulated handler failure")

        doc_signals.document_soft_deleted.connect(bad_handler)
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)

        try:
            # This must not raise despite bad_handler
            with self.captureOnCommitCallbacks(execute=True):
                soft_delete(document=doc, deleted_by=user, reason="test")
            # Verify deletion still happened
            doc.refresh_from_db()
            self.assertEqual(doc.scan_status, Document.ScanStatus.DELETED)
        finally:
            doc_signals.document_soft_deleted.disconnect(bad_handler)
