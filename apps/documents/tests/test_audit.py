"""
Wave 7 — §24.1 canonical test file: test_audit.py

Verifies that every service function writes the correct AuditLogEntry.

Invariants (PIPEDA clause 4.5.3):
  - record_event() is called INSIDE transaction.atomic() (state change + audit commit together)
  - event_detail NEVER contains original_filename (PII risk)
  - event_detail NEVER contains storage_key (security risk)
  - event_detail NEVER contains uploader email or full name
  - AuditLogEntry uses .timestamp field (NOT .created_at)
  - actor_id = str(user.pk) for user actions; None for system actions

Services tested:
  - soft_delete()     → RECORD_DELETED
  - hard_delete()     → RECORD_PURGED
  - apply_legal_hold()   → LEGAL_HOLD_APPLIED
  - release_legal_hold() → LEGAL_HOLD_RELEASED
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone

from apps.audit.models import AuditEventType, AuditLogEntry
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
        email=f"audit{_CTR}@example.com",
        password="testpass123",
        **kwargs,
    )


def _make_category(**kwargs):
    global _CTR
    _CTR += 1
    return DocumentCategory.objects.create(
        name_en="Audit Test",
        name_fr="Test Audit",
        slug=f"audit-cat-{_CTR}",
        allowed_mime_types=["application/pdf"],
        min_retention_days=730,
        max_retention_days=2555,
        **kwargs,
    )


def _make_document(user, category, **kwargs):
    doc_id = uuid.uuid4()
    return Document.objects.create(
        uploaded_by=user,
        category=category,
        original_filename="audit-sensitive.pdf",
        _storage_key=f"documents/active/{doc_id}/{uuid.uuid4().hex}.bin",
        mime_type="application/pdf",
        size_bytes=8_192,
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


def _latest_audit(doc, event_type):
    return (
        AuditLogEntry.objects.filter(
            resource_type="documents.Document",
            resource_id=str(doc.pk),
            event_type=event_type,
        )
        .order_by("-timestamp")
        .first()
    )


# ─────────────────────────────────────────────────────────────────────────────
# soft_delete() audit tests
# ─────────────────────────────────────────────────────────────────────────────


class SoftDeleteAuditTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)

    def test_soft_delete_writes_record_deleted_audit(self):
        before = AuditLogEntry.objects.filter(
            resource_type="documents.Document",
            resource_id=str(self.doc.pk),
            event_type=AuditEventType.RECORD_DELETED,
        ).count()

        soft_delete(document=self.doc, deleted_by=self.user, reason="test_deletion")

        after = AuditLogEntry.objects.filter(
            resource_type="documents.Document",
            resource_id=str(self.doc.pk),
            event_type=AuditEventType.RECORD_DELETED,
        ).count()
        self.assertEqual(after, before + 1)

    def test_soft_delete_audit_has_no_original_filename(self):
        """PIPEDA: event_detail must NOT contain original_filename."""
        soft_delete(document=self.doc, deleted_by=self.user, reason="retention_expired")
        entry = _latest_audit(self.doc, AuditEventType.RECORD_DELETED)
        self.assertIsNotNone(entry)
        self.assertNotIn("original_filename", entry.event_detail)
        # Verify the value itself isn't hidden in a different key
        detail_str = str(entry.event_detail)
        self.assertNotIn(self.doc.original_filename, detail_str)

    def test_soft_delete_audit_has_no_storage_key(self):
        """PIPEDA: event_detail must NOT contain storage_key."""
        soft_delete(document=self.doc, deleted_by=self.user, reason="test")
        entry = _latest_audit(self.doc, AuditEventType.RECORD_DELETED)
        self.assertIsNotNone(entry)
        self.assertNotIn("storage_key", entry.event_detail)
        self.assertNotIn(self.doc._storage_key, str(entry.event_detail))

    def test_soft_delete_audit_has_deletion_reason(self):
        soft_delete(document=self.doc, deleted_by=self.user, reason="test_reason_123")
        entry = _latest_audit(self.doc, AuditEventType.RECORD_DELETED)
        self.assertIsNotNone(entry)
        self.assertIn("deletion_reason", entry.event_detail)
        self.assertEqual(entry.event_detail["deletion_reason"], "test_reason_123")

    def test_soft_delete_audit_actor_id_is_user_pk(self):
        soft_delete(document=self.doc, deleted_by=self.user, reason="test")
        entry = _latest_audit(self.doc, AuditEventType.RECORD_DELETED)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor_id, str(self.user.pk))

    def test_soft_delete_system_deletion_actor_id_none(self):
        """System/Celery deletion (deleted_by=None) → actor_id is None."""
        soft_delete(document=self.doc, deleted_by=None, reason="retention_expired")
        entry = _latest_audit(self.doc, AuditEventType.RECORD_DELETED)
        self.assertIsNotNone(entry)
        self.assertIsNone(entry.actor_id)

    def test_soft_delete_resource_type(self):
        soft_delete(document=self.doc, deleted_by=self.user, reason="test")
        entry = _latest_audit(self.doc, AuditEventType.RECORD_DELETED)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.resource_type, "documents.Document")

    def test_soft_delete_resource_id_is_doc_pk(self):
        soft_delete(document=self.doc, deleted_by=self.user, reason="test")
        entry = _latest_audit(self.doc, AuditEventType.RECORD_DELETED)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.resource_id, str(self.doc.pk))

    def test_soft_delete_audit_entry_has_timestamp(self):
        """AuditLogEntry uses .timestamp (NOT .created_at)."""
        soft_delete(document=self.doc, deleted_by=self.user, reason="test")
        entry = _latest_audit(self.doc, AuditEventType.RECORD_DELETED)
        self.assertIsNotNone(entry)
        self.assertIsNotNone(entry.timestamp)
        self.assertFalse(hasattr(entry, "created_at"))


# ─────────────────────────────────────────────────────────────────────────────
# hard_delete() audit tests
# ─────────────────────────────────────────────────────────────────────────────


class HardDeleteAuditTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)
        # Soft-delete first (required precondition)
        self.doc.deleted_at = timezone.now() - timedelta(days=31)
        self.doc.scan_status = Document.ScanStatus.DELETED
        self.doc.legal_hold = False
        self.doc.save(update_fields=["deleted_at", "scan_status", "legal_hold", "updated_at"])

    def _run_hard_delete(self):
        """Run hard_delete() with default_storage.delete mocked."""
        with patch("apps.documents.services.retention.default_storage") as mock_storage:
            mock_storage.delete.return_value = None
            hard_delete(document=self.doc)

    def test_hard_delete_writes_record_purged_audit(self):
        before = AuditLogEntry.objects.filter(
            resource_type="documents.Document",
            resource_id=str(self.doc.pk),
            event_type=AuditEventType.RECORD_PURGED,
        ).count()

        self._run_hard_delete()

        after = AuditLogEntry.objects.filter(
            resource_type="documents.Document",
            resource_id=str(self.doc.pk),
            event_type=AuditEventType.RECORD_PURGED,
        ).count()
        self.assertEqual(after, before + 1)

    def test_hard_delete_audit_has_cleared_at(self):
        self._run_hard_delete()
        entry = _latest_audit(self.doc, AuditEventType.RECORD_PURGED)
        self.assertIsNotNone(entry)
        self.assertIn("cleared_at", entry.event_detail)

    def test_hard_delete_audit_no_storage_key_value(self):
        """PIPEDA: the actual storage_key value must NOT appear in event_detail."""
        storage_key_value = self.doc._storage_key
        self._run_hard_delete()
        entry = _latest_audit(self.doc, AuditEventType.RECORD_PURGED)
        self.assertIsNotNone(entry)
        self.assertNotIn("storage_key", entry.event_detail)
        self.assertNotIn(storage_key_value, str(entry.event_detail))

    def test_hard_delete_audit_no_original_filename(self):
        self._run_hard_delete()
        entry = _latest_audit(self.doc, AuditEventType.RECORD_PURGED)
        self.assertIsNotNone(entry)
        self.assertNotIn("original_filename", entry.event_detail)

    def test_hard_delete_resource_type(self):
        self._run_hard_delete()
        entry = _latest_audit(self.doc, AuditEventType.RECORD_PURGED)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.resource_type, "documents.Document")

    def test_hard_delete_doc_status_set_to_purged(self):
        """After hard_delete(), the Document row has scan_status=PURGED."""
        self._run_hard_delete()
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.scan_status, Document.ScanStatus.PURGED)

    def test_hard_delete_storage_key_cleared(self):
        """After hard_delete(), _storage_key is empty string."""
        self._run_hard_delete()
        self.doc.refresh_from_db()
        self.assertEqual(self.doc._storage_key, "")

    def test_hard_delete_db_row_retained(self):
        """Per spec §11.2 / NIST SP 800-88: DB row is RETAINED after hard delete."""
        pk = self.doc.pk
        self._run_hard_delete()
        # Row must still exist
        self.assertTrue(Document.objects.filter(pk=pk).exists())


# ─────────────────────────────────────────────────────────────────────────────
# apply_legal_hold() audit tests
# ─────────────────────────────────────────────────────────────────────────────


class ApplyLegalHoldAuditTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.user = _grant_manage_legal_hold(self.user)
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)

    def test_apply_legal_hold_writes_audit(self):
        before = AuditLogEntry.objects.filter(
            resource_type="documents.Document",
            resource_id=str(self.doc.pk),
            event_type=AuditEventType.LEGAL_HOLD_APPLIED,
        ).count()

        apply_legal_hold(document=self.doc, set_by=self.user, reason="ATIP request #2026-001")

        after = AuditLogEntry.objects.filter(
            resource_type="documents.Document",
            resource_id=str(self.doc.pk),
            event_type=AuditEventType.LEGAL_HOLD_APPLIED,
        ).count()
        self.assertEqual(after, before + 1)

    def test_apply_legal_hold_audit_detail_has_legal_hold_true(self):
        apply_legal_hold(document=self.doc, set_by=self.user, reason="ATIP #2026")
        entry = _latest_audit(self.doc, AuditEventType.LEGAL_HOLD_APPLIED)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.event_detail.get("legal_hold"), True)

    def test_apply_legal_hold_audit_detail_has_reason(self):
        apply_legal_hold(document=self.doc, set_by=self.user, reason="litigation hold XYZ")
        entry = _latest_audit(self.doc, AuditEventType.LEGAL_HOLD_APPLIED)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.event_detail.get("reason"), "litigation hold XYZ")

    def test_apply_legal_hold_audit_actor_is_set_by_pk(self):
        apply_legal_hold(document=self.doc, set_by=self.user, reason="ATIP")
        entry = _latest_audit(self.doc, AuditEventType.LEGAL_HOLD_APPLIED)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor_id, str(self.user.pk))

    def test_apply_legal_hold_audit_no_email_in_detail(self):
        """PIPEDA: no user email in event_detail."""
        apply_legal_hold(document=self.doc, set_by=self.user, reason="ATIP")
        entry = _latest_audit(self.doc, AuditEventType.LEGAL_HOLD_APPLIED)
        self.assertIsNotNone(entry)
        self.assertNotIn(self.user.email, str(entry.event_detail))


# ─────────────────────────────────────────────────────────────────────────────
# release_legal_hold() audit tests
# ─────────────────────────────────────────────────────────────────────────────


class ReleaseLegalHoldAuditTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.user = _grant_manage_legal_hold(self.user)
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat, legal_hold=True, legal_hold_reason="test")

    def test_release_legal_hold_writes_audit(self):
        before = AuditLogEntry.objects.filter(
            resource_type="documents.Document",
            resource_id=str(self.doc.pk),
            event_type=AuditEventType.LEGAL_HOLD_RELEASED,
        ).count()

        release_legal_hold(document=self.doc, released_by=self.user, reason="ATIP closed")

        after = AuditLogEntry.objects.filter(
            resource_type="documents.Document",
            resource_id=str(self.doc.pk),
            event_type=AuditEventType.LEGAL_HOLD_RELEASED,
        ).count()
        self.assertEqual(after, before + 1)

    def test_release_legal_hold_audit_detail_has_legal_hold_false(self):
        release_legal_hold(document=self.doc, released_by=self.user)
        entry = _latest_audit(self.doc, AuditEventType.LEGAL_HOLD_RELEASED)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.event_detail.get("legal_hold"), False)

    def test_release_legal_hold_audit_actor_is_released_by_pk(self):
        release_legal_hold(document=self.doc, released_by=self.user)
        entry = _latest_audit(self.doc, AuditEventType.LEGAL_HOLD_RELEASED)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor_id, str(self.user.pk))

    def test_release_legal_hold_audit_no_storage_key(self):
        release_legal_hold(document=self.doc, released_by=self.user)
        entry = _latest_audit(self.doc, AuditEventType.LEGAL_HOLD_RELEASED)
        self.assertIsNotNone(entry)
        self.assertNotIn("storage_key", entry.event_detail)
        self.assertNotIn(self.doc._storage_key, str(entry.event_detail))


# ─────────────────────────────────────────────────────────────────────────────
# AuditLogEntry immutability tests
# ─────────────────────────────────────────────────────────────────────────────


class AuditLogEntryImmutabilityTests(TestCase):
    def test_audit_entry_cannot_be_updated(self):
        """AuditLogEntry.save() on existing record raises ValueError."""
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)
        soft_delete(document=doc, deleted_by=user, reason="test")
        entry = AuditLogEntry.objects.filter(
            resource_type="documents.Document",
            resource_id=str(doc.pk),
        ).first()
        self.assertIsNotNone(entry)
        entry.event_detail["tampered"] = True
        with self.assertRaises(ValueError):
            entry.save()

    def test_audit_entry_cannot_be_deleted(self):
        """AuditLogEntry.delete() raises ValueError."""
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)
        soft_delete(document=doc, deleted_by=user, reason="test")
        entry = AuditLogEntry.objects.filter(
            resource_type="documents.Document",
            resource_id=str(doc.pk),
        ).first()
        self.assertIsNotNone(entry)
        with self.assertRaises(ValueError):
            entry.delete()

    def test_audit_entry_has_entry_hash(self):
        """Every AuditLogEntry must have a non-empty entry_hash for tamper detection."""
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)
        soft_delete(document=doc, deleted_by=user, reason="test")
        entry = AuditLogEntry.objects.filter(
            resource_type="documents.Document",
            resource_id=str(doc.pk),
        ).first()
        self.assertIsNotNone(entry)
        self.assertTrue(entry.entry_hash)
        self.assertEqual(len(entry.entry_hash), 64)  # SHA-256 hex


# ─────────────────────────────────────────────────────────────────────────────
# PIPEDA atomicity test
# ─────────────────────────────────────────────────────────────────────────────


class AuditAtomicityTests(TestCase):
    """
    PIPEDA 4.5.3: state change and audit entry MUST commit together.

    We cannot easily simulate a crash mid-transaction in SQLite,
    but we can verify the audit entry is written in the same call
    (same DB save cycle) as the state change.
    """

    def test_soft_delete_audit_and_state_commit_together(self):
        """
        After soft_delete(), both the Document and the AuditLogEntry exist.
        Verifies atomicity at the observable level.
        """
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)

        soft_delete(document=doc, deleted_by=user, reason="test")

        # Both must exist in DB after the call
        doc.refresh_from_db()
        self.assertIsNotNone(doc.deleted_at)
        self.assertEqual(doc.scan_status, Document.ScanStatus.DELETED)

        entry = _latest_audit(doc, AuditEventType.RECORD_DELETED)
        self.assertIsNotNone(entry)

    def test_apply_legal_hold_audit_and_state_commit_together(self):
        user = _make_user()
        user = _grant_manage_legal_hold(user)
        cat = _make_category()
        doc = _make_document(user, cat)

        apply_legal_hold(document=doc, set_by=user, reason="ATIP")

        doc.refresh_from_db()
        self.assertTrue(doc.legal_hold)

        entry = _latest_audit(doc, AuditEventType.LEGAL_HOLD_APPLIED)
        self.assertIsNotNone(entry)

    def test_record_event_failure_rolls_back_document_state(self):
        """PIPEDA 4.5.3: if audit write fails, document state must roll back atomically."""
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)

        # Patch record_event to raise — simulating a DB failure mid-transaction.
        # record_event is imported at module level in retention.py, so patch it there.
        with patch(
            "apps.documents.services.retention.record_event",
            side_effect=RuntimeError("simulated DB failure"),
        ):
            with self.assertRaises(RuntimeError):
                soft_delete(document=doc, deleted_by=user, reason="test_atomicity")

        # Document state must be rolled back — save() and record_event() are in
        # the same atomic() block, so both must roll back together.
        doc.refresh_from_db()
        self.assertIsNone(
            doc.deleted_at,
            "Document deleted_at must be rolled back if audit record_event raises",
        )
        self.assertEqual(
            AuditLogEntry.objects.filter(resource_id=str(doc.pk)).count(),
            0,
            "No AuditLogEntry must exist if the atomic transaction rolls back",
        )

    def test_document_state_failure_rolls_back_audit_entry(self):
        """PIPEDA 4.5.3: if document save fails, audit entry must also roll back."""
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)

        # Patch Document.save to raise on the FIRST call inside the atomic block.
        # soft_delete() re-fetches via select_for_update() and saves the fetched
        # instance — the first save() call is the state change we want to fail.
        original_save = Document.save
        call_count = [0]

        def failing_save(self_doc, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("simulated DB failure on save")
            return original_save(self_doc, *args, **kwargs)

        with patch.object(Document, "save", failing_save):
            with self.assertRaises(RuntimeError):
                soft_delete(document=doc, deleted_by=user, reason="test_atomicity")

        # No AuditLogEntry should exist — doc.save() and record_event() are both
        # inside the same atomic() block, so the save failure rolls back both.
        self.assertEqual(
            AuditLogEntry.objects.filter(resource_id=str(doc.pk)).count(),
            0,
            "No AuditLogEntry must exist if document save fails and transaction rolls back",
        )
