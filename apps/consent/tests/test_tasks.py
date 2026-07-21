"""
Tests for Consent & Privacy Celery tasks.

process_data_export and cleanup_export_files are tested by calling them
via .apply() so Celery's TASK_ALWAYS_EAGER setting runs them synchronously
in-process with a real task instance (no mock self required).
"""
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.utils import timezone

from apps.consent.models import (
    ConsentAuditEntry,
    DataExportRequest,
)
from apps.consent.tasks import cleanup_export_files, process_data_export

User = get_user_model()
VALID_PASSWORD = "SecureTest123!"

# Patch at the module where default_storage is imported (tasks module level)
_STORAGE = "apps.consent.tasks.default_storage"


def _make_citizen(email=None):
    return User.objects.create_user(
        email=email or f"citizen-{uuid.uuid4().hex[:8]}@example.gov",
        password=VALID_PASSWORD,
    )


def _make_export(citizen, status=DataExportRequest.STATUS_PENDING, expires_at=None):
    return DataExportRequest.objects.create(
        citizen=citizen,
        status=status,
        expires_at=expires_at,
    )


def _run(export_pk):
    """Run process_data_export synchronously via Celery's apply(), patching storage."""
    with patch(_STORAGE):
        result = process_data_export.apply(args=[str(export_pk)])
    return result.result


def _run_with_storage_mock(export_pk, mock_storage):
    """Run process_data_export with a specific storage mock already in place."""
    return process_data_export.apply(args=[str(export_pk)])


class ProcessDataExportTaskTests(TestCase):
    """Tests for the process_data_export Celery task."""

    def setUp(self):
        self.citizen = _make_citizen()

    def test_happy_path_sets_status_ready(self):
        export = _make_export(self.citizen)
        with patch(_STORAGE):
            process_data_export.apply(args=[str(export.pk)])
        export.refresh_from_db()
        self.assertEqual(export.status, DataExportRequest.STATUS_READY)

    def test_sets_status_processing_then_ready(self):
        """Processing must appear before READY — verified via audit trail."""
        export = _make_export(self.citizen)
        with patch(_STORAGE):
            process_data_export.apply(args=[str(export.pk)])
        export.refresh_from_db()
        self.assertEqual(export.status, DataExportRequest.STATUS_READY)
        # export_ready audit entry is only created after the PROCESSING → READY transition
        self.assertTrue(
            ConsentAuditEntry.objects.filter(
                citizen=self.citizen,
                action="export_ready",
            ).exists()
        )

    def test_invalid_pk_returns_error_dict(self):
        fake_pk = str(uuid.uuid4())
        # CELERY_TASK_ALWAYS_EAGER — apply() runs synchronously; throws=False (default)
        result = process_data_export.apply(args=[fake_pk])
        ret = result.result
        self.assertIsInstance(ret, dict)
        self.assertIn("error", ret)
        self.assertEqual(ret["error"], "not_found")

    def test_creates_audit_entry_with_export_ready_action(self):
        export = _make_export(self.citizen)
        with patch(_STORAGE):
            process_data_export.apply(args=[str(export.pk)])
        entry = ConsentAuditEntry.objects.filter(
            citizen=self.citizen,
            action="export_ready",
            export_request=export,
        ).first()
        self.assertIsNotNone(entry, "Expected ConsentAuditEntry with action='export_ready'")

    def test_sends_email_notification(self):
        export = _make_export(self.citizen)
        with patch(_STORAGE):
            process_data_export.apply(args=[str(export.pk)])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.citizen.email, mail.outbox[0].to)

    def test_email_subject_mentions_export(self):
        export = _make_export(self.citizen)
        with patch(_STORAGE):
            process_data_export.apply(args=[str(export.pk)])
        self.assertTrue(len(mail.outbox) > 0)
        subject = mail.outbox[0].subject.lower()
        self.assertIn("export", subject)

    def test_sets_processed_at(self):
        export = _make_export(self.citizen)
        with patch(_STORAGE):
            process_data_export.apply(args=[str(export.pk)])
        export.refresh_from_db()
        self.assertIsNotNone(export.processed_at)

    def test_sets_expires_at(self):
        export = _make_export(self.citizen)
        with patch(_STORAGE):
            process_data_export.apply(args=[str(export.pk)])
        export.refresh_from_db()
        self.assertIsNotNone(export.expires_at)

    def test_sets_document_or_storage_path(self):
        """After a successful export, the export must have a document or a saved path."""
        export = _make_export(self.citizen)
        with patch(_STORAGE):
            process_data_export.apply(args=[str(export.pk)])
        export.refresh_from_db()
        # Wave 6: storage_path field removed; document FK used when category is seeded.
        # Without a seeded category the task falls back to a storage-only path (no doc).
        # Either way, processed_at must be set.
        self.assertIsNotNone(export.processed_at)

    def test_result_contains_ready_status(self):
        export = _make_export(self.citizen)
        with patch(_STORAGE):
            result = process_data_export.apply(args=[str(export.pk)])
        ret = result.result
        self.assertIsInstance(ret, dict)
        self.assertEqual(ret.get("status"), "ready")

    def test_failure_sets_status_failed(self):
        export = _make_export(self.citizen)
        with patch(_STORAGE) as mock_storage:
            mock_storage.save.side_effect = OSError("disk full")
            # CELERY_TASK_EAGER_PROPAGATES=True means exceptions re-raise
            # but our task catches and retries — MaxRetriesExceeded or the OSError
            # bubbles out.  Use throws=False so apply() captures it.
            result = process_data_export.apply(args=[str(export.pk)], throw=False)
        export.refresh_from_db()
        self.assertEqual(export.status, DataExportRequest.STATUS_FAILED)


class CleanupExportFilesTaskTests(TestCase):
    """Tests for the cleanup_export_files periodic task."""

    def setUp(self):
        self.citizen = _make_citizen()

    def _make_ready_expired(self):
        # Wave 6: storage_path field removed; create without it.
        return DataExportRequest.objects.create(
            citizen=self.citizen,
            status=DataExportRequest.STATUS_READY,
            expires_at=timezone.now() - timedelta(hours=1),
        )

    def _make_ready_not_expired(self):
        return DataExportRequest.objects.create(
            citizen=self.citizen,
            status=DataExportRequest.STATUS_READY,
            expires_at=timezone.now() + timedelta(days=7),
        )

    def test_marks_expired_ready_requests_as_expired(self):
        export = self._make_ready_expired()
        with patch(_STORAGE):
            cleanup_export_files.apply()
        export.refresh_from_db()
        self.assertEqual(export.status, DataExportRequest.STATUS_EXPIRED)

    def test_deletes_storage_file(self):
        """When a transitory document is linked, mark_purpose_fulfilled is called."""
        # Wave 6: cleanup_export_files delegates to mark_purpose_fulfilled() for
        # transitory documents; direct storage.delete() is no longer called by the task.
        from apps.documents.models import Document, DocumentCategory

        cat, _ = DocumentCategory.objects.get_or_create(
            slug="pipeda-data-export",
            defaults={
                "name_en": "PIPEDA Export",
                "name_fr": "Export PIPEDA",
                "is_transitory": True,
                "min_retention_days": 0,
                "max_retention_days": 30,
            },
        )
        doc = Document.objects.create(
            uploaded_by=self.citizen,
            category=cat,
            original_filename="export.json",
            mime_type="application/json",
            size_bytes=100,
            _storage_key="documents/active/test/export.bin",
            scan_status=Document.ScanStatus.ACTIVE,
        )
        export = self._make_ready_expired()
        export.document = doc
        export.save(update_fields=["document"])

        with patch(
            "apps.documents.services.retention.mark_purpose_fulfilled"
        ) as mock_mpf, patch(_STORAGE):
            cleanup_export_files.apply()

        mock_mpf.assert_called_once_with(document=doc, actor=self.citizen)

    def test_creates_audit_entry_with_export_expired_action(self):
        export = self._make_ready_expired()
        with patch(_STORAGE):
            cleanup_export_files.apply()
        entry = ConsentAuditEntry.objects.filter(
            citizen=self.citizen,
            action="export_expired",
            export_request=export,
        ).first()
        self.assertIsNotNone(entry)

    def test_does_not_affect_not_expired_ready_requests(self):
        export = self._make_ready_not_expired()
        with patch(_STORAGE):
            cleanup_export_files.apply()
        export.refresh_from_db()
        self.assertEqual(export.status, DataExportRequest.STATUS_READY)

    def test_does_not_affect_pending_requests(self):
        export = DataExportRequest.objects.create(
            citizen=self.citizen,
            status=DataExportRequest.STATUS_PENDING,
            expires_at=timezone.now() - timedelta(hours=1),
        )
        with patch(_STORAGE):
            cleanup_export_files.apply()
        export.refresh_from_db()
        self.assertEqual(export.status, DataExportRequest.STATUS_PENDING)

    def test_does_not_affect_processing_requests(self):
        export = DataExportRequest.objects.create(
            citizen=self.citizen,
            status=DataExportRequest.STATUS_PROCESSING,
            expires_at=timezone.now() - timedelta(hours=1),
        )
        with patch(_STORAGE):
            cleanup_export_files.apply()
        export.refresh_from_db()
        self.assertEqual(export.status, DataExportRequest.STATUS_PROCESSING)

    def test_skips_disposal_when_no_document(self):
        """When no Document is linked, mark_purpose_fulfilled must not be called."""
        # Wave 6: storage_path removed; the "no file" case is now document=None.
        export = DataExportRequest.objects.create(
            citizen=self.citizen,
            status=DataExportRequest.STATUS_READY,
            expires_at=timezone.now() - timedelta(hours=1),
        )
        # document is NULL — no disposal should be attempted.
        with patch(
            "apps.documents.services.retention.mark_purpose_fulfilled"
        ) as mock_mpf, patch(_STORAGE):
            cleanup_export_files.apply()
        mock_mpf.assert_not_called()
        export.refresh_from_db()
        self.assertEqual(export.status, DataExportRequest.STATUS_EXPIRED)

    def test_processes_multiple_expired_exports(self):
        export1 = self._make_ready_expired()
        export2 = self._make_ready_expired()
        with patch(_STORAGE):
            cleanup_export_files.apply()
        export1.refresh_from_db()
        export2.refresh_from_db()
        self.assertEqual(export1.status, DataExportRequest.STATUS_EXPIRED)
        self.assertEqual(export2.status, DataExportRequest.STATUS_EXPIRED)

    def test_returns_count_of_expired(self):
        self._make_ready_expired()
        self._make_ready_expired()
        with patch(_STORAGE):
            result = cleanup_export_files.apply()
        self.assertEqual(result.result.get("expired"), 2)

    # ── Stuck-processing recovery (lines 281–310 in tasks.py) ──────────────

    def _make_stuck_processing(self, hours_ago=3):
        """
        Create a DataExportRequest stuck in STATUS_PROCESSING.

        ``requested_at`` is auto_now_add, so we force it via .update() after
        creation.  The stuck-recovery filter is:
            status=PROCESSING, requested_at < (now - 2h), processed_at IS NULL
        """
        req = DataExportRequest.objects.create(
            citizen=self.citizen,
            status=DataExportRequest.STATUS_PROCESSING,
        )
        # Force requested_at into the past so it qualifies as stuck.
        DataExportRequest.objects.filter(pk=req.pk).update(
            requested_at=timezone.now() - timedelta(hours=hours_ago),
        )
        req.refresh_from_db()
        return req

    def test_stuck_processing_recovered_to_failed(self):
        """
        A STATUS_PROCESSING request older than 2 hours with processed_at=None
        must be flipped to STATUS_FAILED by the stuck-recovery sub-task.

        Regression guard: a SIGKILL'd Celery worker leaves the row in
        STATUS_PROCESSING permanently, blocking new exports for that citizen
        via the unique_active_export_per_citizen constraint.
        """
        stuck = self._make_stuck_processing(hours_ago=3)
        self.assertEqual(stuck.status, DataExportRequest.STATUS_PROCESSING)
        self.assertIsNone(stuck.processed_at)

        with patch(_STORAGE):
            result = cleanup_export_files.apply()

        stuck.refresh_from_db()
        self.assertEqual(
            stuck.status,
            DataExportRequest.STATUS_FAILED,
            "Stuck PROCESSING request older than 2 h must be recovered to STATUS_FAILED.",
        )
        self.assertEqual(result.result.get("recovered_stuck"), 1)

    def test_stuck_processing_audit_entry_created(self):
        """
        The stuck-recovery path must write a ConsentAuditEntry with
        action='export_failed' and reason='recovered_stuck_processing'.
        """
        stuck = self._make_stuck_processing(hours_ago=3)

        with patch(_STORAGE):
            cleanup_export_files.apply()

        entry = ConsentAuditEntry.objects.filter(
            citizen=self.citizen,
            action="export_failed",
            export_request=stuck,
        ).first()
        self.assertIsNotNone(
            entry,
            "Stuck-recovery must write a ConsentAuditEntry(action='export_failed').",
        )
        self.assertEqual(
            entry.details.get("reason"),
            "recovered_stuck_processing",
            "Audit entry details must include reason='recovered_stuck_processing'.",
        )

    def test_recent_processing_not_recovered(self):
        """
        A STATUS_PROCESSING request that is only 30 minutes old must NOT be
        recovered — only requests older than 2 hours qualify as stuck.
        """
        req = DataExportRequest.objects.create(
            citizen=self.citizen,
            status=DataExportRequest.STATUS_PROCESSING,
        )
        # 30 minutes ago — well within the 2-hour grace window.
        DataExportRequest.objects.filter(pk=req.pk).update(
            requested_at=timezone.now() - timedelta(minutes=30),
        )

        with patch(_STORAGE):
            result = cleanup_export_files.apply()

        req.refresh_from_db()
        self.assertEqual(
            req.status,
            DataExportRequest.STATUS_PROCESSING,
            "A PROCESSING request only 30 min old must not be flipped to FAILED.",
        )
        self.assertEqual(result.result.get("recovered_stuck"), 0)

    def test_result_returns_zero_recovered_when_none_stuck(self):
        """
        When no stuck requests exist, recovered_stuck must be 0 in the result.
        """
        with patch(_STORAGE):
            result = cleanup_export_files.apply()
        self.assertEqual(result.result.get("recovered_stuck"), 0)

    # ── mark_purpose_fulfilled exception path (L-1) ─────────────────────────

    def _make_transitory_doc(self):
        """
        Create a transitory Document linked to the test citizen.
        Uses get_or_create on the category so multiple tests share it safely.
        """
        from apps.documents.models import Document, DocumentCategory

        cat, _ = DocumentCategory.objects.get_or_create(
            slug="pipeda-data-export",
            defaults={
                "name_en": "PIPEDA Data Export",
                "name_fr": "Export de données PIPEDA",
                "is_transitory": True,
                "min_retention_days": 0,
                "max_retention_days": 30,
            },
        )
        return Document.objects.create(
            uploaded_by=self.citizen,
            category=cat,
            original_filename="export.json",
            mime_type="application/json",
            size_bytes=100,
            _storage_key="documents/active/test/export.bin",
            scan_status=Document.ScanStatus.ACTIVE,
        )

    def test_mark_purpose_fulfilled_exception_does_not_block_expiry(self):
        """
        When mark_purpose_fulfilled() raises (e.g. a concurrent legal hold was
        applied between the status check and the disposal call), cleanup_export_files
        must:
          (a) swallow the exception,
          (b) still mark the export STATUS_EXPIRED, and
          (c) still write the export_expired ConsentAuditEntry.

        Regression guard: the try/except around mark_purpose_fulfilled() at
        tasks.py lines 254–265 is the sole error boundary for the disposal sub-step.
        If it were accidentally removed, a ValueError from a concurrent legal hold
        would abort the per-export iteration, leaving the export stuck in STATUS_READY
        and skipping the audit entry.
        """
        export = self._make_ready_expired()
        export.document = self._make_transitory_doc()
        export.save(update_fields=["document"])

        with patch(
            "apps.documents.services.retention.mark_purpose_fulfilled",
            side_effect=ValueError("Document is on legal hold — concurrent race"),
        ), patch(_STORAGE):
            cleanup_export_files.apply()

        export.refresh_from_db()
        self.assertEqual(
            export.status,
            DataExportRequest.STATUS_EXPIRED,
            "Export must be marked STATUS_EXPIRED even when mark_purpose_fulfilled raises.",
        )
        entry = ConsentAuditEntry.objects.filter(
            citizen=self.citizen,
            action="export_expired",
            export_request=export,
        ).first()
        self.assertIsNotNone(
            entry,
            "export_expired audit entry must still be written when mark_purpose_fulfilled raises.",
        )

    def test_mark_purpose_fulfilled_exception_does_not_abort_loop(self):
        """
        When mark_purpose_fulfilled() raises for every export in the batch,
        ALL exports must still be marked STATUS_EXPIRED and the expired count
        in the task result must reflect the full batch size.

        This guards against any future change that widens the scope of the
        except clause (e.g. moving the try/except outside the for-loop), which
        would cause a single disposal failure to abort the entire cleanup batch —
        leaving all remaining exports stuck in STATUS_READY indefinitely.
        """
        export1 = self._make_ready_expired()
        export1.document = self._make_transitory_doc()
        export1.save(update_fields=["document"])

        export2 = self._make_ready_expired()
        export2.document = self._make_transitory_doc()
        export2.save(update_fields=["document"])

        # Both mark_purpose_fulfilled calls raise — the loop must survive both.
        with patch(
            "apps.documents.services.retention.mark_purpose_fulfilled",
            side_effect=ValueError("document on legal hold"),
        ), patch(_STORAGE):
            result = cleanup_export_files.apply()

        export1.refresh_from_db()
        export2.refresh_from_db()
        self.assertEqual(
            export1.status,
            DataExportRequest.STATUS_EXPIRED,
            "export1 must be EXPIRED even though its mark_purpose_fulfilled raised.",
        )
        self.assertEqual(
            export2.status,
            DataExportRequest.STATUS_EXPIRED,
            "export2 must be EXPIRED — the error from export1 must not abort the loop.",
        )
        self.assertEqual(
            result.result.get("expired"),
            2,
            "expired count must be 2 regardless of mark_purpose_fulfilled failures.",
        )
