"""
Wave 4: Document Management BB — Retention service + Beat schedule tests.

Covers:
  - soft_delete()             — happy path, legal hold block, already-deleted guard,
                                TOCTOU (legal hold set between pre-check and lock),
                                signal dispatch, audit log, PIPEDA invariants
  - hard_delete()             — happy path (nulls _storage_key, retains DB row),
                                NIST SP 800-88 fail-safe (storage error → no DB change),
                                precondition checks, signal fired BEFORE storage delete,
                                PIPEDA invariants
  - apply_legal_hold()        — happy path, permission guard, TOCTOU, signal
  - release_legal_hold()      — happy path, permission guard, not-on-hold guard
  - mark_purpose_fulfilled()  — transitory only, delegates to soft_delete
  - run_disposal_schedule     — Celery task: soft-deletes pending_disposal() docs,
                                skips legal holds + already-deleted, returns counts
  - run_hard_delete_schedule  — Celery task: hard-deletes pending_hard_delete() docs,
                                returns counts
  - notify_expiring_documents — Celery task: sends email notifications, handles
                                template errors and missing users gracefully
  - create_beat_schedule()    — registers exactly 5 PeriodicTask entries (idempotent)
  - PIPEDA invariants         — no original_filename / storage_key in audit event_detail,
                                no PII in signal kwargs, no PII in log messages

Privacy invariants enforced throughout:
  - audit event_detail NEVER contains original_filename or storage_key
  - signal kwargs contain ONLY pk / category slug / bool / int
  - PIPEDA clause 4.7.5: hard delete nulls _storage_key; DB row retained for audit trail
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock, call, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.documents.models import Document, DocumentCategory
from apps.documents.services.retention import (
    apply_legal_hold,
    hard_delete,
    mark_purpose_fulfilled,
    release_legal_hold,
    soft_delete,
)

User = get_user_model()

# ─────────────────────────────────────────────────────────────────────────────
# Test helpers / factories
# ─────────────────────────────────────────────────────────────────────────────


def _slug():
    return f"w4-{uuid.uuid4().hex[:8]}"


def make_category(**kwargs) -> DocumentCategory:
    defaults = {
        "name_en": "Wave 4 Category",
        "name_fr": "Catégorie Wave 4",
        "slug": _slug(),
        "security_classification": DocumentCategory.SecurityClassification.PROTECTED_B,
        "allowed_mime_types": [],
        "max_size_bytes": 0,
        "min_retention_days": 730,
        "max_retention_days": 2555,
        "is_transitory": False,
    }
    defaults.update(kwargs)
    return DocumentCategory.objects.create(**defaults)


def make_user(**kwargs) -> User:
    uid = uuid.uuid4().hex[:8]
    defaults = {"email": f"w4-{uid}@example.com", "password": "hunter2"}
    defaults.update(kwargs)
    return User.objects.create_user(**defaults)


def make_document(
    category: DocumentCategory,
    user: User,
    *,
    scan_status=Document.ScanStatus.ACTIVE,
    deleted_at=None,
    legal_hold: bool = False,
    expires_at=None,
    retain_until=None,
    is_latest_version: bool = True,
) -> Document:
    doc = Document.objects.create(
        category=category,
        uploaded_by=user,
        original_filename="secret_name_never_in_audit.pdf",
        _storage_key=f"documents/quarantine/{uuid.uuid4().hex}/{uuid.uuid4().hex}.bin",
        mime_type="application/pdf",
        size_bytes=12345,
        scan_status=scan_status,
        security_classification=category.security_classification,
        legal_hold=legal_hold,
        expires_at=expires_at,
        retain_until=retain_until,
        deleted_at=deleted_at,
        is_latest_version=is_latest_version,
    )
    return doc


def make_active_doc(category, user, **kwargs) -> Document:
    return make_document(
        category, user, scan_status=Document.ScanStatus.ACTIVE, **kwargs
    )


def make_soft_deleted_doc(category, user, grace_days_ago=31) -> Document:
    """Create a document that is past the soft-delete grace period."""
    past = timezone.now() - timedelta(days=grace_days_ago)
    doc = make_document(
        category,
        user,
        scan_status=Document.ScanStatus.DELETED,
        deleted_at=past,
    )
    return doc


# ─────────────────────────────────────────────────────────────────────────────
# soft_delete() tests
# ─────────────────────────────────────────────────────────────────────────────


class SoftDeleteHappyPathTests(TestCase):
    def setUp(self):
        self.category = make_category()
        self.user = make_user()
        self.actor = make_user()
        self.doc = make_active_doc(self.category, self.user)

    def test_sets_deleted_at(self):
        """deleted_at is set to a current datetime after soft_delete."""
        before = timezone.now()
        soft_delete(document=self.doc, deleted_by=self.actor, reason="test")
        self.doc.refresh_from_db()
        self.assertIsNotNone(self.doc.deleted_at)
        self.assertGreaterEqual(self.doc.deleted_at, before)

    def test_sets_scan_status_deleted(self):
        """scan_status becomes DELETED — CONTRACT required by pending_hard_delete()."""
        soft_delete(document=self.doc, deleted_by=self.actor, reason="test")
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.scan_status, Document.ScanStatus.DELETED)

    def test_sets_deleted_by(self):
        """deleted_by FK points to the actor who performed the deletion."""
        soft_delete(document=self.doc, deleted_by=self.actor, reason="test")
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.deleted_by_id, self.actor.pk)

    def test_sets_deletion_reason(self):
        """deletion_reason is persisted."""
        soft_delete(document=self.doc, deleted_by=self.actor, reason="retention_expired")
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.deletion_reason, "retention_expired")

    def test_system_deletion_no_deleted_by(self):
        """System (Celery) deletions pass deleted_by=None — no error."""
        soft_delete(document=self.doc, deleted_by=None, reason="retention_expired")
        self.doc.refresh_from_db()
        self.assertIsNone(self.doc.deleted_by)
        self.assertEqual(self.doc.scan_status, Document.ScanStatus.DELETED)

    def test_returns_document_instance(self):
        """Return value is the updated Document instance."""
        result = soft_delete(document=self.doc, deleted_by=self.actor, reason="test")
        self.assertIsInstance(result, Document)
        self.assertEqual(result.pk, self.doc.pk)

    def test_in_memory_instance_updated(self):
        """The caller's in-memory document instance is updated after soft_delete."""
        soft_delete(document=self.doc, deleted_by=self.actor, reason="test")
        # Should NOT need refresh_from_db to see the change
        self.assertIsNotNone(self.doc.deleted_at)
        self.assertEqual(self.doc.scan_status, Document.ScanStatus.DELETED)


class SoftDeleteLegalHoldTests(TestCase):
    """Legal hold blocks soft_delete unconditionally."""

    def setUp(self):
        self.category = make_category()
        self.user = make_user()
        self.actor = make_user()
        self.doc = make_active_doc(self.category, self.user, legal_hold=True)

    def test_raises_value_error_pre_lock(self):
        """Raises ValueError if legal_hold is True (pre-lock fast path)."""
        with self.assertRaises(ValueError):
            soft_delete(document=self.doc, deleted_by=self.actor)

    def test_doc_not_modified_after_legal_hold_rejection(self):
        """No fields are modified when the legal hold check fails."""
        try:
            soft_delete(document=self.doc, deleted_by=self.actor)
        except ValueError:
            pass
        self.doc.refresh_from_db()
        self.assertIsNone(self.doc.deleted_at)
        self.assertNotEqual(self.doc.scan_status, Document.ScanStatus.DELETED)

    def test_legal_hold_set_concurrently_raises_under_lock(self):
        """
        TOCTOU guard: if legal_hold is set in the DB between the pre-check
        (which reads the in-memory instance) and the select_for_update() re-read,
        the under-lock re-check raises ValueError — preventing the deletion.

        Mechanism: soft_delete() does a pre-check on the in-memory document
        (legal_hold=False → passes), then inside atomic() calls
        Document.objects.select_for_update().get() which reads the CURRENT DB state.
        By updating the DB directly (simulating a concurrent process) before
        calling soft_delete(), the under-lock read sees legal_hold=True.
        """
        # Start with no legal hold. Pre-check will pass (in-memory value is False).
        doc = make_active_doc(self.category, self.user, legal_hold=False)

        # Directly set legal_hold=True in the DB — simulating a concurrent process.
        # The in-memory 'doc.legal_hold' is still False (not refreshed).
        Document.objects.filter(pk=doc.pk).update(legal_hold=True)

        # soft_delete() pre-check reads doc.legal_hold (False in-memory → passes).
        # The under-lock select_for_update().get() reads fresh from DB (True → raises).
        with self.assertRaises(ValueError):
            soft_delete(document=doc, deleted_by=self.actor)

        # Verify the document was NOT soft-deleted.
        doc.refresh_from_db()
        self.assertIsNone(doc.deleted_at)


class SoftDeleteAlreadyDeletedTests(TestCase):
    """Documents already soft-deleted cannot be soft-deleted again."""

    def setUp(self):
        self.category = make_category()
        self.user = make_user()
        self.actor = make_user()
        self.doc = make_soft_deleted_doc(self.category, self.user)

    def test_raises_value_error_if_already_deleted(self):
        with self.assertRaises(ValueError):
            soft_delete(document=self.doc, deleted_by=self.actor)


class SoftDeleteSignalTests(TestCase):
    """document_soft_deleted signal is fired with correct kwargs."""

    def setUp(self):
        self.category = make_category()
        self.user = make_user()
        self.actor = make_user()
        self.doc = make_active_doc(self.category, self.user)

    def test_signal_fired_with_correct_kwargs(self):
        """document_soft_deleted.send_robust fires with document_pk and deleted_by_id."""
        from apps.documents.signals import document_soft_deleted

        received = []
        document_soft_deleted.connect(lambda sender, **kw: received.append(kw), weak=False)
        try:
            # H-2 fix: signal fires via transaction.on_commit(); use
            # captureOnCommitCallbacks(execute=True) so it fires in TestCase.
            with self.captureOnCommitCallbacks(execute=True):
                soft_delete(document=self.doc, deleted_by=self.actor, reason="test")
        finally:
            document_soft_deleted.disconnect()

        self.assertEqual(len(received), 1)
        kw = received[0]
        self.assertEqual(kw["document_pk"], str(self.doc.pk))
        self.assertEqual(kw["deleted_by_id"], self.actor.pk)

    def test_signal_fired_with_none_deleted_by(self):
        """System deletion: deleted_by_id is None in signal kwargs."""
        from apps.documents.signals import document_soft_deleted

        received = []
        document_soft_deleted.connect(lambda sender, **kw: received.append(kw), weak=False)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                soft_delete(document=self.doc, deleted_by=None, reason="system")
        finally:
            document_soft_deleted.disconnect()

        self.assertEqual(received[0]["deleted_by_id"], None)

    def test_signal_kwargs_no_pii(self):
        """
        PIPEDA: signal kwargs contain ONLY document_pk and deleted_by_id (int).
        No original_filename, no storage_key, no email address.
        """
        from apps.documents.signals import document_soft_deleted

        received = []
        document_soft_deleted.connect(lambda sender, **kw: received.append(kw), weak=False)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                soft_delete(document=self.doc, deleted_by=self.actor, reason="test")
        finally:
            document_soft_deleted.disconnect()

        kw = received[0]
        # Only these keys are permitted.
        allowed_keys = {"document_pk", "deleted_by_id", "signal", "sender"}
        self.assertTrue(kw.keys() <= allowed_keys, f"Unexpected keys: {kw.keys() - allowed_keys}")
        self.assertNotIn("original_filename", kw)
        self.assertNotIn("storage_key", kw)


class SoftDeleteAuditTests(TestCase):
    """soft_delete() writes a RECORD_DELETED audit entry."""

    def setUp(self):
        self.category = make_category()
        self.user = make_user()
        self.actor = make_user()
        self.doc = make_active_doc(self.category, self.user)

    def test_audit_entry_written(self):
        with patch("apps.documents.services.retention.record_event") as mock_record:
            soft_delete(document=self.doc, deleted_by=self.actor, reason="audit_test")

        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args.kwargs
        from apps.audit.models import AuditEventType
        self.assertEqual(call_kwargs["event_type"], AuditEventType.RECORD_DELETED)
        self.assertEqual(call_kwargs["resource_type"], "documents.Document")
        self.assertEqual(call_kwargs["resource_id"], str(self.doc.pk))
        self.assertEqual(call_kwargs["actor_id"], str(self.actor.pk))

    def test_audit_event_detail_no_pii(self):
        """
        PIPEDA: event_detail MUST NOT contain original_filename or storage_key.
        Only deletion_reason and deleted_by_pk are permitted.
        """
        with patch("apps.documents.services.retention.record_event") as mock_record:
            soft_delete(document=self.doc, deleted_by=self.actor, reason="test_reason")

        event_detail = mock_record.call_args.kwargs.get("event_detail", {})
        self.assertNotIn("original_filename", event_detail)
        self.assertNotIn("storage_key", event_detail)
        self.assertIn("deletion_reason", event_detail)

    def test_audit_actor_id_is_pk_not_email(self):
        """PIPEDA: actor_id is str(user.pk), NEVER email address."""
        with patch("apps.documents.services.retention.record_event") as mock_record:
            soft_delete(document=self.doc, deleted_by=self.actor, reason="test")

        actor_id = mock_record.call_args.kwargs.get("actor_id")
        self.assertEqual(actor_id, str(self.actor.pk))
        self.assertNotIn("@", actor_id)  # Must not be email


# ─────────────────────────────────────────────────────────────────────────────
# hard_delete() tests
# ─────────────────────────────────────────────────────────────────────────────


class HardDeleteHappyPathTests(TestCase):
    """
    hard_delete() nulls _storage_key and deletes the S3 object.
    The DB row is RETAINED (spec §11.2, PIPEDA audit trail).
    """

    def setUp(self):
        self.category = make_category()
        self.user = make_user()
        self.doc = make_soft_deleted_doc(self.category, self.user, grace_days_ago=31)

    def test_storage_key_nulled(self):
        """After hard_delete(), _storage_key is set to empty string."""
        with patch("apps.documents.services.retention.default_storage") as mock_storage:
            mock_storage.delete.return_value = None
            hard_delete(document=self.doc)

        self.doc.refresh_from_db()
        self.assertEqual(self.doc.storage_key, "")

    def test_db_row_retained(self):
        """
        PIPEDA / spec §11.2: the Document DB row MUST be retained for the
        audit trail. hard_delete() must NOT delete the DB row.
        """
        with patch("apps.documents.services.retention.default_storage") as mock_storage:
            mock_storage.delete.return_value = None
            hard_delete(document=self.doc)

        # Row must still exist.
        self.assertTrue(Document.objects.filter(pk=self.doc.pk).exists())

    def test_storage_delete_called_with_correct_key(self):
        """S3/storage object is deleted at the correct storage key."""
        expected_key = self.doc.storage_key
        with patch("apps.documents.services.retention.default_storage") as mock_storage:
            mock_storage.delete.return_value = None
            hard_delete(document=self.doc)
        mock_storage.delete.assert_called_once_with(expected_key)

    def test_in_memory_storage_key_cleared(self):
        """The in-memory document instance's _storage_key is cleared."""
        with patch("apps.documents.services.retention.default_storage") as mock_storage:
            mock_storage.delete.return_value = None
            hard_delete(document=self.doc)
        self.assertEqual(self.doc._storage_key, "")


class HardDeleteFailSafeTests(TestCase):
    """
    NIST SP 800-88 fail-safe: if S3 deletion fails, the storage key is NOT
    nulled and the DB row is NOT changed. The task will retry on the next run.
    """

    def setUp(self):
        self.category = make_category()
        self.user = make_user()
        self.doc = make_soft_deleted_doc(self.category, self.user, grace_days_ago=31)
        self._original_storage_key = self.doc.storage_key

    def test_storage_key_not_nulled_on_storage_error(self):
        """If S3 delete raises, _storage_key is preserved."""
        with patch("apps.documents.services.retention.default_storage") as mock_storage:
            mock_storage.delete.side_effect = Exception("S3 unreachable")
            hard_delete(document=self.doc)

        self.doc.refresh_from_db()
        self.assertEqual(self.doc.storage_key, self._original_storage_key)

    def test_scan_status_not_changed_on_storage_error(self):
        """If S3 delete raises, scan_status remains DELETED (not changed)."""
        with patch("apps.documents.services.retention.default_storage") as mock_storage:
            mock_storage.delete.side_effect = Exception("S3 unreachable")
            hard_delete(document=self.doc)

        self.doc.refresh_from_db()
        self.assertEqual(self.doc.scan_status, Document.ScanStatus.DELETED)

    def test_empty_storage_key_skips_storage_delete(self):
        """If storage_key is already empty, storage.delete is not called."""
        # Simulate previously-cleared storage key.
        Document.objects.filter(pk=self.doc.pk).update(**{"_storage_key": ""})
        self.doc._storage_key = ""

        with patch("apps.documents.services.retention.default_storage") as mock_storage:
            hard_delete(document=self.doc)
        mock_storage.delete.assert_not_called()

        # Row still exists.
        self.assertTrue(Document.objects.filter(pk=self.doc.pk).exists())


class HardDeletePreconditionTests(TestCase):
    """hard_delete() enforces all preconditions before touching storage."""

    def setUp(self):
        self.category = make_category()
        self.user = make_user()

    def test_raises_if_legal_hold(self):
        """Legal hold is an absolute block on hard deletion."""
        doc = make_document(
            self.category,
            self.user,
            scan_status=Document.ScanStatus.DELETED,
            deleted_at=timezone.now() - timedelta(days=35),
            legal_hold=True,
        )
        with self.assertRaises(ValueError):
            hard_delete(document=doc)

    def test_raises_if_not_soft_deleted(self):
        """Document must be soft-deleted (deleted_at set) before hard deletion."""
        doc = make_active_doc(self.category, self.user)
        with self.assertRaises(ValueError):
            hard_delete(document=doc)

    def test_raises_if_scan_status_not_deleted(self):
        """
        scan_status must be DELETED (set by soft_delete).
        If this check fails, the CONTRACT between soft_delete and pending_hard_delete
        is broken.
        """
        past = timezone.now() - timedelta(days=35)
        doc = make_document(
            self.category,
            self.user,
            scan_status=Document.ScanStatus.ACTIVE,  # Wrong — should be DELETED
            deleted_at=past,
        )
        with self.assertRaises(ValueError):
            hard_delete(document=doc)

    def test_raises_if_grace_period_not_elapsed(self):
        """Cannot hard-delete within the 30-day grace period."""
        recent = timezone.now() - timedelta(days=5)  # Only 5 days ago
        doc = make_document(
            self.category,
            self.user,
            scan_status=Document.ScanStatus.DELETED,
            deleted_at=recent,
        )
        with self.assertRaises(ValueError):
            hard_delete(document=doc)


class HardDeleteSignalTests(TestCase):
    """document_hard_deleted signal fires BEFORE storage deletion."""

    def setUp(self):
        self.category = make_category()
        self.user = make_user()
        self.doc = make_soft_deleted_doc(self.category, self.user, grace_days_ago=31)

    def test_signal_fired_after_storage_delete(self):
        """
        C-2 fix: Signal MUST fire AFTER confirmed S3 deletion — not before.
        Firing before would produce a false signal if S3 then fails: the DB row
        says deleted but the file is still on S3 (false security posture).
        The signal fires after the conditional DB update commits PURGED status.
        """
        call_order = []

        from apps.documents.signals import document_hard_deleted

        def receiver(sender, **kw):
            call_order.append("signal")

        document_hard_deleted.connect(receiver)
        try:
            with patch("apps.documents.services.retention.default_storage") as mock_storage:
                def delete_side_effect(key):
                    call_order.append("storage_delete")
                mock_storage.delete.side_effect = delete_side_effect
                hard_delete(document=self.doc)
        finally:
            document_hard_deleted.disconnect(receiver)

        # storage_delete MUST appear before signal in the call order (C-2 fix).
        self.assertIn("storage_delete", call_order)
        self.assertIn("signal", call_order)
        self.assertLess(call_order.index("storage_delete"), call_order.index("signal"))

    def test_signal_kwargs(self):
        """document_hard_deleted fires with document_pk and category_slug."""
        from apps.documents.signals import document_hard_deleted

        received = []
        document_hard_deleted.connect(lambda sender, **kw: received.append(kw), weak=False)
        try:
            with patch("apps.documents.services.retention.default_storage"):
                hard_delete(document=self.doc)
        finally:
            document_hard_deleted.disconnect()

        self.assertEqual(len(received), 1)
        kw = received[0]
        self.assertEqual(kw["document_pk"], str(self.doc.pk))
        self.assertEqual(kw["category_slug"], self.category.slug)

    def test_signal_kwargs_no_storage_key(self):
        """
        PIPEDA: document_hard_deleted signal kwargs MUST NOT include
        storage_key — that is an internal implementation detail.
        """
        from apps.documents.signals import document_hard_deleted

        received = []
        document_hard_deleted.connect(lambda sender, **kw: received.append(kw), weak=False)
        try:
            with patch("apps.documents.services.retention.default_storage"):
                hard_delete(document=self.doc)
        finally:
            document_hard_deleted.disconnect()

        kw = received[0]
        self.assertNotIn("storage_key", kw)
        self.assertNotIn("original_filename", kw)


class HardDeleteAuditTests(TestCase):
    """hard_delete() writes a RECORD_PURGED audit entry."""

    def setUp(self):
        self.category = make_category()
        self.user = make_user()
        self.doc = make_soft_deleted_doc(self.category, self.user, grace_days_ago=31)

    def test_audit_event_type_is_record_purged(self):
        with patch("apps.documents.services.retention.record_event") as mock_record:
            with patch("apps.documents.services.retention.default_storage"):
                hard_delete(document=self.doc)

        from apps.audit.models import AuditEventType
        call_kwargs = mock_record.call_args.kwargs
        self.assertEqual(call_kwargs["event_type"], AuditEventType.RECORD_PURGED)

    def test_audit_event_detail_no_pii(self):
        """
        PIPEDA: event_detail MUST NOT contain original_filename or storage_key.
        Only cleared_at timestamp is permitted.
        """
        with patch("apps.documents.services.retention.record_event") as mock_record:
            with patch("apps.documents.services.retention.default_storage"):
                hard_delete(document=self.doc)

        event_detail = mock_record.call_args.kwargs.get("event_detail", {})
        self.assertNotIn("original_filename", event_detail)
        self.assertNotIn("storage_key", event_detail)
        self.assertIn("cleared_at", event_detail)

    def test_audit_written_after_storage_delete(self):
        """
        C-2 fix: Audit MUST be written AFTER confirmed S3 deletion — not before.
        Writing audit before S3 delete creates a false RECORD_PURGED immutable
        entry if S3 then fails: the audit chain says "purged" but the file is
        still on S3 (falsified immutable record). Post-deletion ordering is correct.
        """
        call_order = []

        def audit_side_effect(**kw):
            call_order.append("audit")

        with patch("apps.documents.services.retention.record_event", side_effect=audit_side_effect):
            with patch("apps.documents.services.retention.default_storage") as mock_storage:
                def delete_side_effect(key):
                    call_order.append("storage_delete")
                mock_storage.delete.side_effect = delete_side_effect
                hard_delete(document=self.doc)

        # storage_delete MUST appear before audit in the call order (C-2 fix).
        self.assertIn("storage_delete", call_order)
        self.assertIn("audit", call_order)
        self.assertLess(call_order.index("storage_delete"), call_order.index("audit"))


# ─────────────────────────────────────────────────────────────────────────────
# apply_legal_hold() tests
# ─────────────────────────────────────────────────────────────────────────────


class ApplyLegalHoldTests(TestCase):

    def setUp(self):
        self.category = make_category()
        self.user = make_user()
        self.doc = make_active_doc(self.category, self.user)

    def _make_staff_user_with_perm(self) -> User:
        from django.contrib.auth.models import Permission
        staff = make_user()
        perm = Permission.objects.get(
            content_type__app_label="documents",
            codename="manage_legal_hold",
        )
        staff.user_permissions.add(perm)
        # Refresh permission cache.
        return User.objects.get(pk=staff.pk)

    def test_apply_sets_legal_hold_true(self):
        staff = self._make_staff_user_with_perm()
        apply_legal_hold(document=self.doc, set_by=staff, reason="ATIP request")
        self.doc.refresh_from_db()
        self.assertTrue(self.doc.legal_hold)

    def test_apply_sets_legal_hold_reason(self):
        staff = self._make_staff_user_with_perm()
        apply_legal_hold(document=self.doc, set_by=staff, reason="litigation hold")
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.legal_hold_reason, "litigation hold")

    def test_apply_sets_legal_hold_set_by(self):
        staff = self._make_staff_user_with_perm()
        apply_legal_hold(document=self.doc, set_by=staff, reason="test")
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.legal_hold_set_by_id, staff.pk)

    def test_raises_permission_denied_without_perm(self):
        """Users without manage_legal_hold raise PermissionDenied."""
        user_no_perm = make_user()
        with self.assertRaises(PermissionDenied):
            apply_legal_hold(document=self.doc, set_by=user_no_perm, reason="test")

    def test_raises_value_error_if_already_on_hold(self):
        """Cannot apply a hold to a document already on hold."""
        staff = self._make_staff_user_with_perm()
        apply_legal_hold(document=self.doc, set_by=staff, reason="first")
        self.doc.refresh_from_db()
        with self.assertRaises(ValueError):
            apply_legal_hold(document=self.doc, set_by=staff, reason="second")

    def test_signal_fired_with_legal_hold_true(self):
        from apps.documents.signals import document_legal_hold_changed

        received = []
        document_legal_hold_changed.connect(lambda sender, **kw: received.append(kw), weak=False)
        staff = self._make_staff_user_with_perm()
        try:
            # H-2 fix: signal fires via transaction.on_commit(); use
            # captureOnCommitCallbacks(execute=True) so it fires in TestCase.
            with self.captureOnCommitCallbacks(execute=True):
                apply_legal_hold(document=self.doc, set_by=staff, reason="test")
        finally:
            document_legal_hold_changed.disconnect()

        self.assertEqual(len(received), 1)
        kw = received[0]
        self.assertEqual(kw["document_pk"], str(self.doc.pk))
        self.assertTrue(kw["legal_hold"])
        self.assertEqual(kw["set_by_id"], staff.pk)

    def test_audit_written_with_legal_hold_applied_event(self):
        """
        C-4 fix: apply_legal_hold() MUST use LEGAL_HOLD_APPLIED — not the generic
        STATUS_CHANGED. This enables precise audit queries for legal hold events
        without scanning all status change entries.
        """
        staff = self._make_staff_user_with_perm()
        with patch("apps.documents.services.retention.record_event") as mock_record:
            apply_legal_hold(document=self.doc, set_by=staff, reason="ATIP")

        from apps.audit.models import AuditEventType
        call_kwargs = mock_record.call_args.kwargs
        self.assertEqual(call_kwargs["event_type"], AuditEventType.LEGAL_HOLD_APPLIED)
        self.assertEqual(call_kwargs["actor_id"], str(staff.pk))
        self.assertNotIn("@", call_kwargs["actor_id"])  # No PII
        # event_detail must include reason and set_by_pk for forensic audit trail.
        detail = call_kwargs.get("event_detail", {})
        self.assertTrue(detail.get("legal_hold"))
        self.assertIn("reason", detail)
        self.assertIn("set_by_pk", detail)

    def test_in_memory_instance_updated(self):
        """Caller's in-memory doc is updated — no refresh_from_db needed."""
        staff = self._make_staff_user_with_perm()
        apply_legal_hold(document=self.doc, set_by=staff, reason="test")
        self.assertTrue(self.doc.legal_hold)


# ─────────────────────────────────────────────────────────────────────────────
# release_legal_hold() tests
# ─────────────────────────────────────────────────────────────────────────────


class ReleaseLegalHoldTests(TestCase):

    def setUp(self):
        self.category = make_category()
        self.user = make_user()
        self.doc = make_active_doc(self.category, self.user, legal_hold=True)

    def _make_staff_user_with_perm(self) -> User:
        from django.contrib.auth.models import Permission
        staff = make_user()
        perm = Permission.objects.get(
            content_type__app_label="documents",
            codename="manage_legal_hold",
        )
        staff.user_permissions.add(perm)
        return User.objects.get(pk=staff.pk)

    def test_release_sets_legal_hold_false(self):
        staff = self._make_staff_user_with_perm()
        release_legal_hold(document=self.doc, released_by=staff)
        self.doc.refresh_from_db()
        self.assertFalse(self.doc.legal_hold)

    def test_legal_hold_reason_preserved_for_audit(self):
        """legal_hold_reason is NOT cleared on release — preserved for audit trail."""
        Document.objects.filter(pk=self.doc.pk).update(legal_hold_reason="ATIP request 2026")
        staff = self._make_staff_user_with_perm()
        release_legal_hold(document=self.doc, released_by=staff)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.legal_hold_reason, "ATIP request 2026")

    def test_raises_permission_denied_without_perm(self):
        user_no_perm = make_user()
        with self.assertRaises(PermissionDenied):
            release_legal_hold(document=self.doc, released_by=user_no_perm)

    def test_raises_value_error_if_not_on_hold(self):
        """Cannot release a hold that is not active."""
        doc_no_hold = make_active_doc(self.category, self.user, legal_hold=False)
        staff = self._make_staff_user_with_perm()
        with self.assertRaises(ValueError):
            release_legal_hold(document=doc_no_hold, released_by=staff)

    def test_signal_fired_with_legal_hold_false(self):
        from apps.documents.signals import document_legal_hold_changed

        received = []
        document_legal_hold_changed.connect(lambda sender, **kw: received.append(kw), weak=False)
        staff = self._make_staff_user_with_perm()
        try:
            # H-2 fix: signal fires via transaction.on_commit(); use
            # captureOnCommitCallbacks(execute=True) so it fires in TestCase.
            with self.captureOnCommitCallbacks(execute=True):
                release_legal_hold(document=self.doc, released_by=staff)
        finally:
            document_legal_hold_changed.disconnect()

        self.assertEqual(len(received), 1)
        self.assertFalse(received[0]["legal_hold"])
        self.assertEqual(received[0]["set_by_id"], staff.pk)

    def test_audit_written(self):
        """
        C-4 fix: release_legal_hold() MUST use LEGAL_HOLD_RELEASED — not the
        generic STATUS_CHANGED. This enables precise forensic audit queries.
        """
        staff = self._make_staff_user_with_perm()
        with patch("apps.documents.services.retention.record_event") as mock_record:
            release_legal_hold(document=self.doc, released_by=staff)

        from apps.audit.models import AuditEventType
        call_kwargs = mock_record.call_args.kwargs
        self.assertEqual(call_kwargs["event_type"], AuditEventType.LEGAL_HOLD_RELEASED)
        # event_detail must include released_by_pk for forensic audit trail.
        detail = call_kwargs.get("event_detail", {})
        self.assertFalse(detail.get("legal_hold"))
        self.assertIn("released_by_pk", detail)

    def test_in_memory_instance_updated(self):
        """Caller's in-memory doc is updated."""
        staff = self._make_staff_user_with_perm()
        release_legal_hold(document=self.doc, released_by=staff)
        self.assertFalse(self.doc.legal_hold)


# ─────────────────────────────────────────────────────────────────────────────
# mark_purpose_fulfilled() tests
# ─────────────────────────────────────────────────────────────────────────────


class MarkPurposeFulfilledTests(TestCase):

    def setUp(self):
        self.transitory_category = make_category(is_transitory=True)
        self.non_transitory_category = make_category(is_transitory=False)
        self.user = make_user()
        self.actor = make_user()

    def test_soft_deletes_transitory_document(self):
        """mark_purpose_fulfilled() soft-deletes a transitory document."""
        doc = make_active_doc(self.transitory_category, self.user)
        result = mark_purpose_fulfilled(document=doc, actor=self.actor)
        doc.refresh_from_db()
        self.assertIsNotNone(doc.deleted_at)
        self.assertEqual(doc.scan_status, Document.ScanStatus.DELETED)

    def test_reason_is_transitory_purpose_fulfilled(self):
        """deletion_reason is 'transitory_purpose_fulfilled' (LAC DA #2016/001)."""
        doc = make_active_doc(self.transitory_category, self.user)
        mark_purpose_fulfilled(document=doc, actor=self.actor)
        doc.refresh_from_db()
        self.assertEqual(doc.deletion_reason, "transitory_purpose_fulfilled")

    def test_sets_expires_at_to_now(self):
        """expires_at is set to the current timestamp when purpose is fulfilled."""
        doc = make_active_doc(self.transitory_category, self.user)
        before = timezone.now()
        mark_purpose_fulfilled(document=doc, actor=self.actor)
        doc.refresh_from_db()
        self.assertIsNotNone(doc.expires_at)
        self.assertGreaterEqual(doc.expires_at, before)

    def test_raises_for_non_transitory_category(self):
        """mark_purpose_fulfilled() raises ValueError on non-transitory documents."""
        doc = make_active_doc(self.non_transitory_category, self.user)
        with self.assertRaises(ValueError):
            mark_purpose_fulfilled(document=doc, actor=self.actor)

    def test_raises_if_legal_hold(self):
        """Legal hold blocks mark_purpose_fulfilled() via soft_delete()."""
        doc = make_active_doc(self.transitory_category, self.user, legal_hold=True)
        with self.assertRaises(ValueError):
            mark_purpose_fulfilled(document=doc, actor=self.actor)

    def test_actor_set_as_deleted_by(self):
        """The actor is recorded as deleted_by in the DB."""
        doc = make_active_doc(self.transitory_category, self.user)
        mark_purpose_fulfilled(document=doc, actor=self.actor)
        doc.refresh_from_db()
        self.assertEqual(doc.deleted_by_id, self.actor.pk)


# ─────────────────────────────────────────────────────────────────────────────
# Celery task: run_disposal_schedule
# ─────────────────────────────────────────────────────────────────────────────


class RunDisposalScheduleTests(TestCase):
    """
    run_disposal_schedule processes pending_disposal() documents.

    The task is called via .run() to bypass Celery's task machinery in tests
    (consistent with the project's established task testing pattern).
    """

    def setUp(self):
        from apps.documents.tasks import run_disposal_schedule
        self.task = run_disposal_schedule
        self.category = make_category(max_retention_days=10, min_retention_days=5)
        self.user = make_user()

    def _make_pending_disposal_doc(self) -> Document:
        """Create a document that is past BOTH expires_at and retain_until."""
        past_dt = timezone.now() - timedelta(days=1)
        past_date = (timezone.now() - timedelta(days=1)).date()
        return make_document(
            self.category,
            self.user,
            scan_status=Document.ScanStatus.ACTIVE,
            expires_at=past_dt,
            retain_until=past_date,
        )

    def test_soft_deletes_eligible_documents(self):
        """run_disposal_schedule soft-deletes documents in pending_disposal()."""
        doc = self._make_pending_disposal_doc()
        result = self.task.run()
        doc.refresh_from_db()
        self.assertIsNotNone(doc.deleted_at)
        self.assertEqual(doc.scan_status, Document.ScanStatus.DELETED)
        self.assertGreater(result["soft_deleted"], 0)

    def test_skips_legal_hold_documents(self):
        """Documents on legal hold are excluded by pending_disposal() before the loop.

        legal_hold=True is an ABSOLUTE block: pending_disposal() filters them out
        entirely, so total_eligible == 0 and deleted_at stays None.
        """
        doc = self._make_pending_disposal_doc()
        Document.objects.filter(pk=doc.pk).update(legal_hold=True)
        result = self.task.run()
        doc.refresh_from_db()
        # Legal-hold doc must NOT be touched.
        self.assertIsNone(doc.deleted_at)
        # It never entered the loop — total_eligible is 0, not counted as skipped.
        self.assertEqual(result["total_eligible"], 0)

    def test_skips_already_deleted_documents(self):
        """Documents already soft-deleted are skipped without error."""
        doc = self._make_pending_disposal_doc()
        # Soft-delete it manually to make it already-deleted.
        Document.objects.filter(pk=doc.pk).update(
            deleted_at=timezone.now() - timedelta(hours=1),
            scan_status=Document.ScanStatus.DELETED,
        )
        # Now run — should skip it gracefully.
        result = self.task.run()
        self.assertGreaterEqual(result["skipped"], 0)

    def test_returns_summary_dict(self):
        """Return value is a dict with total_eligible, soft_deleted, skipped."""
        result = self.task.run()
        self.assertIn("total_eligible", result)
        self.assertIn("soft_deleted", result)
        self.assertIn("skipped", result)

    def test_no_documents_returns_zero_counts(self):
        """No pending_disposal() documents → zero counts, no error."""
        result = self.task.run()
        self.assertEqual(result["total_eligible"], 0)
        self.assertEqual(result["soft_deleted"], 0)
        self.assertEqual(result["skipped"], 0)

    def test_task_decorator_properties(self):
        """Task must have acks_late=True and reject_on_worker_lost=True."""
        self.assertTrue(self.task.acks_late)
        self.assertTrue(self.task.reject_on_worker_lost)

    def test_task_queue(self):
        """Task is routed to the 'documents' queue."""
        self.assertEqual(self.task.queue, "documents")


# ─────────────────────────────────────────────────────────────────────────────
# Celery task: run_hard_delete_schedule
# ─────────────────────────────────────────────────────────────────────────────


class RunHardDeleteScheduleTests(TestCase):

    def setUp(self):
        from apps.documents.tasks import run_hard_delete_schedule
        self.task = run_hard_delete_schedule
        self.category = make_category()
        self.user = make_user()

    def test_nulls_storage_key_for_eligible_documents(self):
        """run_hard_delete_schedule nulls _storage_key for pending_hard_delete() docs."""
        doc = make_soft_deleted_doc(self.category, self.user, grace_days_ago=31)
        with patch("apps.documents.services.retention.default_storage") as mock_storage:
            mock_storage.delete.return_value = None
            result = self.task.run()

        doc.refresh_from_db()
        self.assertEqual(doc.storage_key, "")
        self.assertGreater(result["hard_deleted"], 0)

    def test_db_row_retained_after_hard_delete(self):
        """
        DB row is RETAINED after hard deletion — spec §11.2 audit trail requirement.
        """
        doc = make_soft_deleted_doc(self.category, self.user, grace_days_ago=31)
        with patch("apps.documents.services.retention.default_storage"):
            self.task.run()
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())

    def test_skips_docs_within_grace_period(self):
        """Documents soft-deleted < 30 days ago are not hard-deleted."""
        doc = make_soft_deleted_doc(self.category, self.user, grace_days_ago=5)
        with patch("apps.documents.services.retention.default_storage") as mock_storage:
            result = self.task.run()
        mock_storage.delete.assert_not_called()
        self.assertEqual(result["total_eligible"], 0)

    def test_skips_legal_hold_even_after_grace(self):
        """Legal hold blocks hard deletion even after grace period."""
        doc = make_soft_deleted_doc(self.category, self.user, grace_days_ago=31)
        Document.objects.filter(pk=doc.pk).update(legal_hold=True)
        result = self.task.run()
        self.assertEqual(result["total_eligible"], 0)

    def test_returns_summary_dict(self):
        result = self.task.run()
        self.assertIn("total_eligible", result)
        self.assertIn("hard_deleted", result)
        self.assertIn("skipped", result)

    def test_task_decorator_properties(self):
        self.assertTrue(self.task.acks_late)
        self.assertTrue(self.task.reject_on_worker_lost)


# ─────────────────────────────────────────────────────────────────────────────
# Celery task: notify_expiring_documents
# ─────────────────────────────────────────────────────────────────────────────


class NotifyExpiringDocumentsTests(TestCase):

    def setUp(self):
        from apps.documents.tasks import notify_expiring_documents
        self.task = notify_expiring_documents
        self.category = make_category()
        self.user = make_user()

    def _make_expiring_doc(self, days_from_now: int = 3) -> Document:
        """Create an ACTIVE, non-deleted, non-held document expiring soon."""
        expires_at = timezone.now() + timedelta(days=days_from_now)
        return make_document(
            self.category,
            self.user,
            scan_status=Document.ScanStatus.ACTIVE,
            expires_at=expires_at,
            legal_hold=False,
            is_latest_version=True,
        )

    def test_sends_notification_for_expiring_document(self):
        """Sends email for each document expiring within days_before."""
        doc = self._make_expiring_doc(days_from_now=3)
        with patch(
            "apps.documents.tasks.send_email_notification", return_value=True
        ) as mock_send:
            result = self.task.run(days_before=7)

        mock_send.assert_called_once()
        self.assertEqual(result["notified"], 1)
        self.assertEqual(result["total_eligible"], 1)

    def test_does_not_notify_deleted_documents(self):
        """Soft-deleted documents are excluded from the expiry notification."""
        past = timezone.now() - timedelta(days=5)
        make_document(
            self.category,
            self.user,
            scan_status=Document.ScanStatus.DELETED,
            deleted_at=past,
            expires_at=timezone.now() + timedelta(days=3),
        )
        with patch("apps.documents.tasks.send_email_notification", return_value=True) as mock_send:
            result = self.task.run(days_before=7)
        mock_send.assert_not_called()
        self.assertEqual(result["total_eligible"], 0)

    def test_does_not_notify_legal_hold_documents(self):
        """Legal-hold documents are not notified (disposal is blocked anyway)."""
        make_document(
            self.category,
            self.user,
            scan_status=Document.ScanStatus.ACTIVE,
            expires_at=timezone.now() + timedelta(days=3),
            legal_hold=True,
        )
        with patch("apps.documents.tasks.send_email_notification", return_value=True) as mock_send:
            result = self.task.run(days_before=7)
        mock_send.assert_not_called()
        self.assertEqual(result["total_eligible"], 0)

    def test_does_not_notify_non_latest_versions(self):
        """Only is_latest_version=True documents generate notifications."""
        make_document(
            self.category,
            self.user,
            scan_status=Document.ScanStatus.ACTIVE,
            expires_at=timezone.now() + timedelta(days=3),
            is_latest_version=False,
        )
        with patch("apps.documents.tasks.send_email_notification", return_value=True) as mock_send:
            result = self.task.run(days_before=7)
        mock_send.assert_not_called()
        self.assertEqual(result["total_eligible"], 0)

    def test_does_not_notify_quarantined_documents(self):
        """Only ACTIVE documents are notified."""
        make_document(
            self.category,
            self.user,
            scan_status=Document.ScanStatus.QUARANTINED,
            expires_at=timezone.now() + timedelta(days=3),
        )
        with patch("apps.documents.tasks.send_email_notification", return_value=True) as mock_send:
            result = self.task.run(days_before=7)
        mock_send.assert_not_called()

    def test_does_not_notify_already_expired_documents(self):
        """Documents already past expires_at are not in the notification window."""
        make_document(
            self.category,
            self.user,
            scan_status=Document.ScanStatus.ACTIVE,
            expires_at=timezone.now() - timedelta(days=1),  # Past
        )
        with patch("apps.documents.tasks.send_email_notification", return_value=True) as mock_send:
            result = self.task.run(days_before=7)
        mock_send.assert_not_called()

    def test_template_failure_counted_as_skipped(self):
        """
        If send_email_notification returns False (template missing), the
        document is counted as skipped and the task continues without aborting.
        """
        self._make_expiring_doc(days_from_now=3)
        with patch(
            "apps.documents.tasks.send_email_notification", return_value=False
        ):
            result = self.task.run(days_before=7)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["notified"], 0)

    def test_notification_context_contains_no_pii(self):
        """
        PIPEDA: the context dict passed to send_email_notification MUST NOT
        contain original_filename or storage_key.
        """
        self._make_expiring_doc(days_from_now=3)
        with patch(
            "apps.documents.tasks.send_email_notification", return_value=True
        ) as mock_send:
            self.task.run(days_before=7)

        call_kwargs = mock_send.call_args.kwargs
        context = call_kwargs.get("context", {})
        self.assertNotIn("original_filename", context)
        self.assertNotIn("storage_key", context)
        self.assertIn("category_name", context)
        self.assertIn("expires_at", context)

    def test_returns_summary_dict(self):
        result = self.task.run(days_before=7)
        self.assertIn("total_eligible", result)
        self.assertIn("notified", result)
        self.assertIn("skipped", result)

    def test_task_decorator_properties(self):
        self.assertTrue(self.task.acks_late)
        self.assertTrue(self.task.reject_on_worker_lost)

    def test_task_queue(self):
        self.assertEqual(self.task.queue, "documents")


# ─────────────────────────────────────────────────────────────────────────────
# create_beat_schedule() tests
# ─────────────────────────────────────────────────────────────────────────────


class CreateBeatScheduleTests(TestCase):
    """
    create_beat_schedule() registers exactly 5 PeriodicTask entries using
    django_celery_beat. Calls are idempotent — safe to call multiple times.
    """

    def setUp(self):
        from apps.documents.tasks import create_beat_schedule
        from django_celery_beat.models import CrontabSchedule, PeriodicTask

        # Migration 0004 already pre-populates the 5 "documents:" PeriodicTask
        # entries as part of the test DB setup. Delete them so each test starts
        # from a clean slate and can verify creation behaviour independently.
        PeriodicTask.objects.filter(name__startswith="documents:").delete()
        # CrontabSchedule rows may also be orphaned after PeriodicTask deletion;
        # deleting them prevents stale rows from interfering with get_or_create
        # lookups that use all schedule fields as lookup kwargs.
        CrontabSchedule.objects.filter(
            day_of_week="*", day_of_month="*", month_of_year="*",
            timezone="UTC", minute="0",
        ).delete()
        self.create_beat_schedule = create_beat_schedule

    def test_creates_five_periodic_tasks(self):
        """Exactly 5 PeriodicTask entries are created on first call."""
        from django_celery_beat.models import PeriodicTask
        self.assertEqual(
            PeriodicTask.objects.filter(name__startswith="documents:").count(), 0,
            "setUp should have cleared pre-existing documents: tasks",
        )
        self.create_beat_schedule()
        self.assertEqual(
            PeriodicTask.objects.filter(name__startswith="documents:").count(), 5,
        )

    def test_idempotent_second_call(self):
        """Calling create_beat_schedule() twice does not duplicate entries."""
        from django_celery_beat.models import PeriodicTask
        self.create_beat_schedule()
        count_after_first = PeriodicTask.objects.filter(name__startswith="documents:").count()
        self.create_beat_schedule()
        count_after_second = PeriodicTask.objects.filter(name__startswith="documents:").count()
        self.assertEqual(count_after_first, count_after_second)

    def test_purge_tokens_task_registered_at_01_00(self):
        """run_purge_expired_tokens is scheduled at 01:00 UTC."""
        self.create_beat_schedule()
        from django_celery_beat.models import PeriodicTask
        task = PeriodicTask.objects.get(name="documents: purge-expired-tokens")
        self.assertEqual(task.crontab.hour, "1")
        self.assertEqual(task.crontab.minute, "0")
        self.assertEqual(task.task, "apps.documents.tasks.run_purge_expired_tokens")

    def test_disposal_task_registered_at_02_00(self):
        """run_disposal_schedule is scheduled at 02:00 UTC."""
        self.create_beat_schedule()
        from django_celery_beat.models import PeriodicTask
        task = PeriodicTask.objects.get(name="documents: run-disposal-schedule")
        self.assertEqual(task.crontab.hour, "2")
        self.assertEqual(task.task, "apps.documents.tasks.run_disposal_schedule")

    def test_hard_delete_task_registered_at_03_00(self):
        """run_hard_delete_schedule is scheduled at 03:00 UTC."""
        self.create_beat_schedule()
        from django_celery_beat.models import PeriodicTask
        task = PeriodicTask.objects.get(name="documents: run-hard-delete-schedule")
        self.assertEqual(task.crontab.hour, "3")
        self.assertEqual(task.task, "apps.documents.tasks.run_hard_delete_schedule")

    def test_cleanup_task_registered_at_04_00(self):
        """cleanup_stale_pending_uploads is scheduled at 04:00 UTC."""
        self.create_beat_schedule()
        from django_celery_beat.models import PeriodicTask
        task = PeriodicTask.objects.get(name="documents: cleanup-stale-pending-uploads")
        self.assertEqual(task.crontab.hour, "4")
        self.assertEqual(task.task, "apps.documents.tasks.cleanup_stale_pending_uploads")

    def test_notify_task_registered_at_08_00(self):
        """notify_expiring_documents is scheduled at 08:00 UTC."""
        self.create_beat_schedule()
        from django_celery_beat.models import PeriodicTask
        task = PeriodicTask.objects.get(name="documents: notify-expiring-documents")
        self.assertEqual(task.crontab.hour, "8")
        self.assertEqual(task.task, "apps.documents.tasks.notify_expiring_documents")

    def test_all_tasks_enabled(self):
        """All registered PeriodicTask entries have enabled=True."""
        self.create_beat_schedule()
        from django_celery_beat.models import PeriodicTask
        tasks = PeriodicTask.objects.filter(name__startswith="documents:")
        for task in tasks:
            self.assertTrue(task.enabled, f"Task {task.name} is disabled")


# ─────────────────────────────────────────────────────────────────────────────
# manage_legal_hold permission exists in Document Meta
# ─────────────────────────────────────────────────────────────────────────────


class DocumentPermissionsTests(TestCase):
    """Verify the manage_legal_hold permission is declared in Document.Meta."""

    def test_manage_legal_hold_in_meta_permissions(self):
        """manage_legal_hold must exist in Document._meta.permissions."""
        codenames = [p[0] for p in Document._meta.permissions]
        self.assertIn("manage_legal_hold", codenames)

    def test_manage_legal_hold_in_db(self):
        """manage_legal_hold Permission object exists in the DB after migrations."""
        from django.contrib.auth.models import Permission
        self.assertTrue(
            Permission.objects.filter(
                content_type__app_label="documents",
                codename="manage_legal_hold",
            ).exists()
        )

    def test_upload_document_still_present(self):
        """Existing upload_document permission was not accidentally removed."""
        codenames = [p[0] for p in Document._meta.permissions]
        self.assertIn("upload_document", codenames)

    def test_upload_staff_document_still_present(self):
        """Existing upload_staff_document permission was not accidentally removed."""
        codenames = [p[0] for p in Document._meta.permissions]
        self.assertIn("upload_staff_document", codenames)


# ─────────────────────────────────────────────────────────────────────────────
# PIPEDA cross-cutting invariants
# ─────────────────────────────────────────────────────────────────────────────


class PipedaInvariantsTests(TestCase):
    """
    These tests enforce the PIPEDA invariants that apply across the entire
    Wave 4 implementation. They are the "never break these" tests.
    """

    def setUp(self):
        self.category = make_category()
        self.user = make_user()
        self.actor = make_user()

    def test_soft_delete_audit_no_filename(self):
        """
        PIPEDA clause 4.5.3: original_filename (which may contain a person's
        name, DOB, case number) must never appear in audit event_detail.
        """
        doc = make_active_doc(self.category, self.user)
        with patch("apps.documents.services.retention.record_event") as mock_record:
            soft_delete(document=doc, deleted_by=self.actor, reason="test")

        event_detail = mock_record.call_args.kwargs.get("event_detail", {})
        # Ensure the actual filename ("secret_name_never_in_audit.pdf") is absent.
        event_detail_str = str(event_detail)
        self.assertNotIn("secret_name_never_in_audit", event_detail_str)
        self.assertNotIn("original_filename", event_detail)

    def test_hard_delete_audit_no_storage_key(self):
        """
        PIPEDA: storage_key is an internal path that must never appear in
        audit entries (it could reveal storage structure or file identifiers).
        """
        doc = make_soft_deleted_doc(self.category, self.user, grace_days_ago=31)
        captured_storage_key = doc.storage_key  # capture before deletion

        with patch("apps.documents.services.retention.record_event") as mock_record:
            with patch("apps.documents.services.retention.default_storage"):
                hard_delete(document=doc)

        event_detail = mock_record.call_args.kwargs.get("event_detail", {})
        event_detail_str = str(event_detail)
        # The actual storage key value must not appear.
        self.assertNotIn(captured_storage_key, event_detail_str)
        self.assertNotIn("storage_key", event_detail)

    def test_legal_hold_audit_actor_id_is_int_not_email(self):
        """
        PIPEDA: actor identifiers in audit entries must be integer PKs,
        never email addresses (which are PII under PIPEDA clause 4.2).
        """
        from django.contrib.auth.models import Permission
        staff = make_user()
        perm = Permission.objects.get(
            content_type__app_label="documents",
            codename="manage_legal_hold",
        )
        staff.user_permissions.add(perm)
        staff = User.objects.get(pk=staff.pk)

        doc = make_active_doc(self.category, self.user)
        with patch("apps.documents.services.retention.record_event") as mock_record:
            apply_legal_hold(document=doc, set_by=staff, reason="test")

        actor_id = mock_record.call_args.kwargs.get("actor_id")
        # Must be a string representation of an integer, not an email.
        self.assertIsNotNone(actor_id)
        self.assertNotIn("@", actor_id)
        # Must be parseable as an integer.
        int(actor_id)  # Raises ValueError if it's not numeric

    def test_soft_delete_signal_no_filename(self):
        """
        document_soft_deleted signal kwargs must not include original_filename.
        Signals are consumed by receivers that may write to external logging systems.
        """
        from apps.documents.signals import document_soft_deleted

        doc = make_active_doc(self.category, self.user)
        received = []
        document_soft_deleted.connect(lambda sender, **kw: received.append(kw), weak=False)
        try:
            soft_delete(document=doc, deleted_by=self.actor, reason="test")
        finally:
            document_soft_deleted.disconnect()

        kw = received[0]
        kw_str = str(kw)
        self.assertNotIn("secret_name_never_in_audit", kw_str)
        self.assertNotIn("original_filename", kw)
        self.assertNotIn("storage_key", kw)
