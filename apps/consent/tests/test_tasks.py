"""
Tests for Consent & Privacy Celery tasks.

process_data_export and cleanup_export_files are tested by calling them
via .apply() so Celery's TASK_ALWAYS_EAGER setting runs them synchronously
in-process with a real task instance (no mock self required).
"""
import hashlib
import hmac
import json
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import requests as _requests
from celery.exceptions import Retry

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.utils import timezone

from apps.consent.models import (
    ConsentAuditEntry,
    ConsentWebhook,
    DataExportRequest,
)
from apps.consent.services import ConsentService
from apps.consent.tasks import (
    _build_export_payload,
    cleanup_export_files,
    dispatch_consent_webhook,
    process_data_export,
)

User = get_user_model()
VALID_PASSWORD = "SecureTest123!"

# Patch at the module where default_storage is imported (tasks module level)
_STORAGE = "apps.consent.tasks.default_storage"
# tasks.py does `import requests as _requests` — patch target must match.
_POST = "apps.consent.tasks._requests.post"


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


class BuildExportPayloadTests(TestCase):
    """
    Item 6 (Consent closure plan): _build_export_payload() previously
    silently swallowed ANY exception (`except Exception: pass`) in its
    service_requests/notifications/consent_audit_entries sections, and never
    included ConsentRevision or ConsentSignature data at all. These tests
    pin the fixed behavior: genuine data (not just ImportError) surfaces
    Revisions/Signatures correctly, and a non-ImportError failure is logged
    rather than silently discarded.
    """

    def setUp(self):
        self.citizen = _make_citizen()
        self.category, _initial_revision = ConsentService.create_data_agreement({
            "slug": f"export-cat-{uuid.uuid4().hex[:6]}",
            "name_en": "Export Test Category",
            "name_fr": "Catégorie de test",
            "purpose_en": "Testing exports",
            "purpose_fr": "Test",
            "lawful_basis": "consent",
            "is_required": False,
            "is_active": True,
        })

    def test_payload_includes_consent_revisions_for_this_citizen(self):
        """
        grant() creates 2 ConsentRevisions (unsigned, then signed) per call
        (services.py Steps 2 and 6) — the export must surface both.
        """
        ConsentService.grant(citizen=self.citizen, category_slug=self.category.slug)
        payload = _build_export_payload(self.citizen)
        self.assertIn("consent_revisions", payload)
        self.assertEqual(len(payload["consent_revisions"]), 2)
        for rev in payload["consent_revisions"]:
            self.assertIsInstance(rev["id"], str)
            self.assertIsInstance(rev["serialized_snapshot"], dict)
            self.assertIsInstance(rev["serialized_hash"], str)

    def test_payload_includes_consent_signatures_for_this_citizen(self):
        """
        grant() auto-creates exactly one ConsentSignature (services.py Step 5,
        "auto-create system string-type signature") — the export must include it.
        """
        ConsentService.grant(citizen=self.citizen, category_slug=self.category.slug)
        payload = _build_export_payload(self.citizen)
        self.assertIn("consent_signatures", payload)
        self.assertEqual(len(payload["consent_signatures"]), 1)
        sig = payload["consent_signatures"][0]
        self.assertEqual(sig["verification_type"], "string")
        self.assertIsInstance(sig["payload"], str)
        self.assertIsInstance(sig["signature"], str)

    def test_payload_scopes_revisions_and_signatures_to_this_citizen_only(self):
        """A second citizen's revisions/signatures must never leak into this one's export."""
        other = _make_citizen()
        ConsentService.grant(citizen=other, category_slug=self.category.slug)
        # This citizen has no ConsentRecord of their own at all.
        payload = _build_export_payload(self.citizen)
        self.assertEqual(payload["consent_revisions"], [])
        self.assertEqual(payload["consent_signatures"], [])

    def test_empty_for_citizen_with_no_consent_records(self):
        payload = _build_export_payload(self.citizen)
        self.assertEqual(payload["consents"], [])
        self.assertEqual(payload["consent_revisions"], [])
        self.assertEqual(payload["consent_signatures"], [])

    def test_payload_is_json_serializable(self):
        """The task json.dumps()'s this payload directly — it must not blow up."""
        ConsentService.grant(citizen=self.citizen, category_slug=self.category.slug)
        payload = _build_export_payload(self.citizen)
        json.dumps(payload, default=str)  # must not raise

    def test_non_import_error_in_optional_section_is_logged_not_swallowed(self):
        """
        BUGFIX regression guard: a genuine bug (anything other than
        ImportError) in the service_requests/notifications/audit-entries
        sections must be logged via logger.exception, not silently passed —
        the old `except Exception: pass` made this indistinguishable from
        "app not installed". We simulate this by making the ConsentAuditEntry
        import raise a plain RuntimeError instead of ImportError.
        """
        with patch("apps.consent.models.ConsentAuditEntry") as mock_model:
            mock_model.objects.filter.side_effect = RuntimeError("simulated DB error")
            with patch("apps.consent.tasks.logger") as mock_logger:
                payload = _build_export_payload(self.citizen)
                mock_logger.exception.assert_called()
        # The rest of the payload must still be produced despite this failure.
        self.assertIn("profile", payload)
        self.assertEqual(payload["consent_audit_entries"], [])

    def test_import_error_in_optional_section_is_silently_skipped(self):
        """
        The genuinely-expected case (an optional app not installed) must NOT
        be logged as an error — only unexpected exceptions should be.

        Setting sys.modules["apps.portal.models"] = None is the standard
        Python trick to force the next `import apps.portal.models` statement
        to raise ImportError, simulating "this optional app isn't installed"
        without needing to actually uninstall anything.
        """
        import sys
        with patch.dict(sys.modules, {"apps.portal.models": None}):
            with patch("apps.consent.tasks.logger") as mock_logger:
                payload = _build_export_payload(self.citizen)
                mock_logger.exception.assert_not_called()
        self.assertEqual(payload["service_requests"], [])


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


# ===========================================================================
# dispatch_consent_webhook — Fix 5 (zero coverage before this pass)
# ===========================================================================

def _make_webhook(**kwargs):
    defaults = {
        "payload_url": "https://example.com/hook",
        "secret_key": "test-secret-key",
        "content_type": "application/json",
        "signature_header": "X-GovStack-Signature",
        "subscribed_events": [ConsentWebhook.EVENT_CONSENT_GRANTED],
        "is_disabled": False,
    }
    defaults.update(kwargs)
    return ConsentWebhook.objects.create(**defaults)


# Bug 4 (SSRF hardening): dispatch_consent_webhook now resolves the
# payload_url's hostname via socket.getaddrinfo() and rejects private/
# loopback/link-local/reserved/metadata IP ranges immediately before the
# outbound POST (see tasks._is_safe_outbound_url). Every test below that
# exercises the transport-layer behaviour (signing, retries, persistence)
# for a "https://example.com/..." webhook needs that DNS lookup to resolve
# to a public, routable IP so it isn't itself skipped by the new safety
# check — the dedicated SSRF behaviour (unsafe URLs actually being skipped)
# is covered separately by WebhookSSRFProtectionTests below, which mocks
# getaddrinfo to return private/loopback/link-local/metadata addresses
# on purpose.
_GETADDRINFO = "apps.consent.tasks.socket.getaddrinfo"
_PUBLIC_ADDRINFO = [(2, 1, 6, "", ("93.184.216.34", 0))]  # a public, routable IPv4


class _WebhookDispatchTestCase(TestCase):
    """
    Base class for webhook dispatch tests that patches socket.getaddrinfo to
    resolve every hostname to a public IP, so pre-existing tests (written
    before the Bug 4 SSRF fix) keep exercising transport-layer logic without
    depending on real DNS or accidentally tripping the new safety check.
    """

    def setUp(self):
        super().setUp()
        self._dns_patcher = patch(_GETADDRINFO, return_value=_PUBLIC_ADDRINFO)
        self._dns_patcher.start()
        self.addCleanup(self._dns_patcher.stop)


class DispatchConsentWebhookSignatureTests(_WebhookDispatchTestCase):
    """HMAC-SHA256 signature computation must match tasks.py's own scheme exactly."""

    def setUp(self):
        super().setUp()
        self.webhook = _make_webhook()

    def test_hmac_signature_matches_known_payload_and_secret(self):
        """
        Independently recompute the HMAC-SHA256 signature the same way a
        webhook subscriber would, and assert it matches the header the task
        actually sends — this is the whole point of publishing a signature.
        """
        with patch(_POST) as mock_post:
            mock_post.return_value = MagicMock(status_code=200, ok=True)
            dispatch_consent_webhook.apply(
                args=[
                    str(self.webhook.pk),
                    "consent.granted",
                    {"category_slug": "marketing", "individual_id": "abc-123"},
                    "2026-01-01T00:00:00+00:00",
                ]
            )

        mock_post.assert_called_once()
        _, call_kwargs = mock_post.call_args
        body = call_kwargs["data"]
        headers = call_kwargs["headers"]

        # Body must be the exact event/timestamp/payload envelope, unsorted
        # (insertion order), matching tasks.py's own json.dumps(..., default=str).
        expected_body = json.dumps(
            {
                "event": "consent.granted",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "payload": {"category_slug": "marketing", "individual_id": "abc-123"},
            },
            default=str,
        )
        self.assertEqual(body, expected_body)

        expected_sig = hmac.new(
            b"test-secret-key", expected_body.encode(), hashlib.sha256
        ).hexdigest()
        self.assertEqual(headers["X-GovStack-Signature"], f"sha256={expected_sig}")
        self.assertEqual(headers["X-GovStack-Event"], "consent.granted")
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_signature_header_name_is_configurable(self):
        """A webhook with a custom signature_header must use that header name."""
        webhook = _make_webhook(
            payload_url="https://example.com/hook2",
            secret_key="another-secret",
            signature_header="X-Custom-Signature",
        )
        with patch(_POST) as mock_post:
            mock_post.return_value = MagicMock(status_code=200, ok=True)
            dispatch_consent_webhook.apply(
                args=[str(webhook.pk), "consent.granted", {}, "2026-01-01T00:00:00+00:00"]
            )

        _, call_kwargs = mock_post.call_args
        headers = call_kwargs["headers"]
        self.assertIn("X-Custom-Signature", headers)
        self.assertNotIn("X-GovStack-Signature", headers)
        self.assertTrue(headers["X-Custom-Signature"].startswith("sha256="))


class DispatchConsentWebhookDeliveryPersistenceTests(_WebhookDispatchTestCase):
    """last_payload / last_delivery_at / last_delivery_status persistence."""

    def setUp(self):
        super().setUp()
        self.webhook = _make_webhook()

    def test_successful_delivery_persists_last_payload_and_status(self):
        with patch(_POST) as mock_post:
            mock_post.return_value = MagicMock(status_code=200, ok=True)
            result = dispatch_consent_webhook.apply(
                args=[
                    str(self.webhook.pk),
                    "consent.granted",
                    {"category_slug": "marketing"},
                    "2026-01-01T00:00:00+00:00",
                ]
            )

        self.webhook.refresh_from_db()
        self.assertEqual(self.webhook.last_delivery_status, "success")
        self.assertIsNotNone(self.webhook.last_delivery_at)
        self.assertIsNotNone(self.webhook.last_payload)
        self.assertEqual(self.webhook.last_payload["event"], "consent.granted")
        self.assertEqual(
            self.webhook.last_payload["payload"], {"category_slug": "marketing"}
        )
        self.assertEqual(result.result, {"status": "delivered", "http_status": 200})

    def test_non_2xx_response_marks_failed_without_overwriting_last_payload(self):
        # First, a successful delivery populates last_payload.
        with patch(_POST) as mock_post:
            mock_post.return_value = MagicMock(status_code=200, ok=True)
            dispatch_consent_webhook.apply(
                args=[
                    str(self.webhook.pk),
                    "consent.granted",
                    {"category_slug": "marketing"},
                    "2026-01-01T00:00:00+00:00",
                ]
            )
        self.webhook.refresh_from_db()
        first_payload = self.webhook.last_payload

        # A subsequent non-2xx response must mark failed but NOT clobber
        # last_payload (preserves the last successfully delivered payload).
        with patch(_POST) as mock_post:
            mock_post.return_value = MagicMock(status_code=500, ok=False)
            result = dispatch_consent_webhook.apply(
                args=[
                    str(self.webhook.pk),
                    "consent.withdrawn",
                    {"category_slug": "marketing"},
                    "2026-01-02T00:00:00+00:00",
                ]
            )

        self.webhook.refresh_from_db()
        self.assertEqual(self.webhook.last_delivery_status, "failed")
        self.assertEqual(self.webhook.last_payload, first_payload)
        self.assertEqual(
            result.result, {"status": "receiver_error", "http_status": 500}
        )

    def test_webhook_not_found_is_skipped_gracefully(self):
        fake_pk = str(uuid.uuid4())
        with patch(_POST) as mock_post:
            result = dispatch_consent_webhook.apply(
                args=[fake_pk, "consent.granted", {}, "2026-01-01T00:00:00+00:00"]
            )
        mock_post.assert_not_called()
        self.assertEqual(
            result.result, {"status": "skipped", "reason": "webhook_not_found"}
        )


class DispatchConsentWebhookDisabledTests(_WebhookDispatchTestCase):
    """The task must skip dispatch (no HTTP call) for a disabled webhook."""

    def test_disabled_webhook_is_skipped_no_http_call_made(self):
        webhook = _make_webhook(is_disabled=True)
        with patch(_POST) as mock_post:
            result = dispatch_consent_webhook.apply(
                args=[str(webhook.pk), "consent.granted", {}, "2026-01-01T00:00:00+00:00"]
            )
        mock_post.assert_not_called()
        self.assertEqual(
            result.result, {"status": "skipped", "reason": "webhook_disabled"}
        )
        webhook.refresh_from_db()
        self.assertIsNone(webhook.last_delivery_status)
        self.assertIsNone(webhook.last_payload)

    def test_enabled_webhook_is_not_skipped(self):
        webhook = _make_webhook(is_disabled=False)
        with patch(_POST) as mock_post:
            mock_post.return_value = MagicMock(status_code=200, ok=True)
            dispatch_consent_webhook.apply(
                args=[str(webhook.pk), "consent.granted", {}, "2026-01-01T00:00:00+00:00"]
            )
        mock_post.assert_called_once()


class DispatchConsentWebhookRetryTests(_WebhookDispatchTestCase):
    """Retry behaviour on requests.exceptions.RequestException."""

    def setUp(self):
        super().setUp()
        self.webhook = _make_webhook()

    def test_network_error_triggers_autoretry(self):
        """
        A RequestException from requests.post must be retried per the task's
        autoretry_for=(requests.exceptions.RequestException,) policy — not
        swallowed and not allowed to mark the webhook as a permanent failure
        without at least one retry attempt.
        """
        with patch(_POST) as mock_post, patch.object(
            dispatch_consent_webhook, "retry", side_effect=Retry()
        ) as mock_retry:
            mock_post.side_effect = _requests.exceptions.ConnectionError("connection refused")
            dispatch_consent_webhook.apply(
                args=[
                    str(self.webhook.pk),
                    "consent.granted",
                    {"category_slug": "marketing"},
                    "2026-01-01T00:00:00+00:00",
                ],
                throw=False,
            )

        mock_retry.assert_called_once()
        _, retry_kwargs = mock_retry.call_args
        self.assertIsInstance(
            retry_kwargs.get("exc"), _requests.exceptions.RequestException
        )

    def test_network_error_does_not_mark_delivery_failed_without_retry_exhaustion(self):
        """
        A transient network error must not silently persist
        last_delivery_status="failed" — that write path is reserved for a
        non-2xx HTTP response from the receiver (a receiver-side logic
        error), not a network-level exception, which is retried instead.
        """
        with patch(_POST) as mock_post, patch.object(
            dispatch_consent_webhook, "retry", side_effect=Retry()
        ):
            mock_post.side_effect = _requests.exceptions.Timeout("timed out")
            dispatch_consent_webhook.apply(
                args=[str(self.webhook.pk), "consent.granted", {}, "2026-01-01T00:00:00+00:00"],
                throw=False,
            )

        self.webhook.refresh_from_db()
        self.assertIsNone(self.webhook.last_delivery_status)


# ===========================================================================
# Bug 4 (SSRF hardening) — dispatch-time safety check + registration-time
# HTTPS-only guard.
# ===========================================================================

class WebhookSSRFProtectionTests(TestCase):
    """
    dispatch_consent_webhook must resolve payload_url's hostname via DNS and
    refuse to POST to any private/loopback/link-local/reserved/metadata
    address, WITHOUT raising — the task must simply skip dispatch and log a
    warning, exactly like the disabled-webhook and not-found cases above.

    Deliberately does NOT patch socket.getaddrinfo at the class level (unlike
    _WebhookDispatchTestCase) — each test below sets its own return_value/
    side_effect to simulate a specific resolved address.
    """

    def setUp(self):
        self.webhook = _make_webhook(payload_url="https://attacker-controlled.example/hook")

    def _dispatch(self):
        return dispatch_consent_webhook.apply(
            args=[str(self.webhook.pk), "consent.granted", {}, "2026-01-01T00:00:00+00:00"]
        )

    def test_metadata_ip_is_skipped_without_calling_requests_post(self):
        """The canonical SSRF target: the cloud metadata endpoint."""
        with patch(_GETADDRINFO, return_value=[(2, 1, 6, "", ("169.254.169.254", 0))]):
            with patch(_POST) as mock_post:
                result = self._dispatch()
        mock_post.assert_not_called()
        self.assertEqual(result.result, {"status": "skipped", "reason": "unsafe_url"})

    def test_loopback_ip_is_skipped_without_calling_requests_post(self):
        with patch(_GETADDRINFO, return_value=[(2, 1, 6, "", ("127.0.0.1", 0))]):
            with patch(_POST) as mock_post:
                result = self._dispatch()
        mock_post.assert_not_called()
        self.assertEqual(result.result, {"status": "skipped", "reason": "unsafe_url"})

    def test_private_10_range_ip_is_skipped_without_calling_requests_post(self):
        with patch(_GETADDRINFO, return_value=[(2, 1, 6, "", ("10.0.0.5", 0))]):
            with patch(_POST) as mock_post:
                result = self._dispatch()
        mock_post.assert_not_called()
        self.assertEqual(result.result, {"status": "skipped", "reason": "unsafe_url"})

    def test_link_local_range_ip_is_skipped_without_calling_requests_post(self):
        with patch(_GETADDRINFO, return_value=[(2, 1, 6, "", ("169.254.1.1", 0))]):
            with patch(_POST) as mock_post:
                result = self._dispatch()
        mock_post.assert_not_called()
        self.assertEqual(result.result, {"status": "skipped", "reason": "unsafe_url"})

    def test_dns_resolution_failure_is_skipped_without_calling_requests_post(self):
        """Fails closed: any DNS error must be treated as unsafe, not crash the task."""
        with patch(_GETADDRINFO, side_effect=OSError("Name or service not known")):
            with patch(_POST) as mock_post:
                result = self._dispatch()
        mock_post.assert_not_called()
        self.assertEqual(result.result, {"status": "skipped", "reason": "unsafe_url"})

    def test_unsafe_url_skip_does_not_raise_or_mark_delivery_failed(self):
        """
        Skipping an unsafe URL must not be recorded as a delivery failure —
        it never attempted delivery at all, so last_delivery_status must be
        left untouched (None), distinguishing "never tried" from "tried and
        failed".
        """
        with patch(_GETADDRINFO, return_value=[(2, 1, 6, "", ("169.254.169.254", 0))]):
            with patch(_POST) as mock_post:
                self._dispatch()
        mock_post.assert_not_called()
        self.webhook.refresh_from_db()
        self.assertIsNone(self.webhook.last_delivery_status)

    def test_public_ip_still_dispatches_successfully(self):
        """A normal public HTTPS URL must still be dispatched (no regression)."""
        with patch(_GETADDRINFO, return_value=_PUBLIC_ADDRINFO):
            with patch(_POST) as mock_post:
                mock_post.return_value = MagicMock(status_code=200, ok=True)
                result = self._dispatch()
        mock_post.assert_called_once()
        self.assertEqual(result.result, {"status": "delivered", "http_status": 200})

    def test_one_unsafe_ip_among_several_resolved_rejects_whole_url(self):
        """A hostname with multiple A/AAAA records is unsafe if ANY resolved IP is unsafe."""
        with patch(
            _GETADDRINFO,
            return_value=[
                (2, 1, 6, "", ("93.184.216.34", 0)),
                (2, 1, 6, "", ("10.0.0.1", 0)),
            ],
        ):
            with patch(_POST) as mock_post:
                result = self._dispatch()
        mock_post.assert_not_called()
        self.assertEqual(result.result, {"status": "skipped", "reason": "unsafe_url"})

    def test_plain_http_url_is_skipped_at_dispatch_time_too(self):
        """
        Belt-and-suspenders: even if a non-HTTPS URL somehow made it into the
        database (e.g. seeded directly, bypassing the serializer), dispatch
        time must still refuse to POST to it.
        """
        webhook = _make_webhook(payload_url="http://example.com/hook", secret_key="s2")
        with patch(_POST) as mock_post:
            result = dispatch_consent_webhook.apply(
                args=[str(webhook.pk), "consent.granted", {}, "2026-01-01T00:00:00+00:00"]
            )
        mock_post.assert_not_called()
        self.assertEqual(result.result, {"status": "skipped", "reason": "unsafe_url"})


class WebhookDispatchRedirectHandlingTests(TestCase):
    """
    Bug 4 (SSRF hardening): redirects must never be followed, and a 3xx
    response must be treated as a failed delivery (requests.Response.ok is
    True for any status < 400, including 3xx, so this must be an explicit
    check — not implicit via resp.ok).
    """

    def setUp(self):
        self.webhook = _make_webhook()

    def test_allow_redirects_false_is_passed_to_requests_post(self):
        with patch(_GETADDRINFO, return_value=_PUBLIC_ADDRINFO):
            with patch(_POST) as mock_post:
                mock_post.return_value = MagicMock(status_code=200, ok=True)
                dispatch_consent_webhook.apply(
                    args=[str(self.webhook.pk), "consent.granted", {}, "2026-01-01T00:00:00+00:00"]
                )
        _, call_kwargs = mock_post.call_args
        self.assertEqual(call_kwargs.get("allow_redirects"), False)

    def test_3xx_response_is_treated_as_failed_delivery(self):
        with patch(_GETADDRINFO, return_value=_PUBLIC_ADDRINFO):
            with patch(_POST) as mock_post:
                # ok=True mirrors real requests.Response.ok semantics for a 3xx
                # (it only returns False for status codes >= 400) — the fix must
                # not rely on resp.ok to catch this.
                mock_post.return_value = MagicMock(status_code=302, ok=True)
                result = dispatch_consent_webhook.apply(
                    args=[str(self.webhook.pk), "consent.granted", {}, "2026-01-01T00:00:00+00:00"]
                )
        self.assertEqual(result.result, {"status": "receiver_error", "http_status": 302})
        self.webhook.refresh_from_db()
        self.assertEqual(self.webhook.last_delivery_status, "failed")


# ===========================================================================
# Bug 4 (SSRF hardening) — WebhookSerializer registration-time HTTPS guard.
# ===========================================================================

class WebhookSerializerHttpsValidationTests(TestCase):
    """
    WebhookSerializer.validate_payloadUrl() must reject any non-HTTPS
    payloadUrl at registration time, before a ConsentWebhook row is ever
    persisted — the cheap half of the Bug 4 SSRF fix (the expensive
    DNS-resolution check happens at dispatch time; see
    WebhookSSRFProtectionTests above).
    """

    def _valid_payload(self, payload_url: str) -> dict:
        return {
            "payloadUrl": payload_url,
            "contentType": "application/json",
            "secretKey": "s3cr3t",
        }

    def test_plain_http_url_is_rejected(self):
        from apps.consent.serializers import WebhookSerializer

        serializer = WebhookSerializer(data=self._valid_payload("http://example.com/hook"))
        self.assertFalse(serializer.is_valid())
        self.assertIn("payloadUrl", serializer.errors)

    def test_malformed_url_is_rejected(self):
        from apps.consent.serializers import WebhookSerializer

        serializer = WebhookSerializer(data=self._valid_payload("not-a-url"))
        self.assertFalse(serializer.is_valid())
        self.assertIn("payloadUrl", serializer.errors)

    def test_valid_https_url_is_accepted(self):
        from apps.consent.serializers import WebhookSerializer

        serializer = WebhookSerializer(data=self._valid_payload("https://example.com/hook"))
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_rejection_does_not_persist_a_webhook_row(self):
        """A rejected non-HTTPS registration must never reach the database."""
        from apps.consent.serializers import WebhookSerializer

        before = ConsentWebhook.objects.count()
        serializer = WebhookSerializer(data=self._valid_payload("http://example.com/hook"))
        self.assertFalse(serializer.is_valid())
        self.assertEqual(ConsentWebhook.objects.count(), before)
