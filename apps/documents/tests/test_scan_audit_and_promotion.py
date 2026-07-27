"""
apps/documents/tests/test_scan_audit_and_promotion.py
=====================================================
Regression tests for two adversarial-audit findings against the Document
Management BB's virus-scan pipeline:

  FIX #1 (HIGH)   — no audit entry was ever written for ANY scan outcome.
                    ``AuditEventType.THREAT_DETECTED`` was defined in
                    apps/audit/models.py and never invoked anywhere in the BB,
                    so a malware detection left zero trace in the tamper-evident
                    audit log.

  FIX #2 (MEDIUM) — storage prefix semantics were inverted: every untrusted
                    upload path wrote ``documents/quarantine/…`` and nothing
                    ever promoted a clean file to ``documents/active/…``,
                    contradicting the code's own comments and
                    ``_VALID_PREFIXES``.

Test classes
------------
  - ScanOutcomeAuditTests       — an audit entry exists for BOTH outcomes, with
                                  the exact minimal PIPEDA-safe event_detail.
  - QuarantinePromotionTests    — a clean scan really does move the object from
                                  quarantine/ to active/ and repoint storage_key.
  - PromotionFailureTests       — a failed move must NOT leave an ACTIVE row.
  - PromotionHelperUnitTests    — direct unit coverage of the move helper.

Conventions mirror apps/documents/tests/test_wave3_clamav.py (same CIVICOS
override, same document/category factories, same FileSystemStorage MEDIA_ROOT).
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import patch

from celery.exceptions import Retry
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings

from apps.audit.models import AuditEventType, AuditLogEntry
from apps.documents.models import Document, DocumentCategory
from apps.documents.services.upload import _make_storage_key
from apps.documents.tasks import (
    _promote_storage_object_to_active,
    _quarantine_on_scan_failure,
    _StoragePromotionError,
    scan_document,
)

User = get_user_model()


# CLAMAV_HOST set → scan_document takes the real ClamAV branch (not DEV_BYPASS).
CIVICOS_CLAMAV = {
    "CLAMAV_HOST": "clamav",
    "CLAMAV_PORT": 3310,
    "CLAMAV_REQUIRED": False,
    "CLAMAV_TIMEOUT": 30,
    "DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES": 10 * 1024 * 1024,
    "DOCUMENT_MAX_STAFF_UPLOAD_BYTES": 50 * 1024 * 1024,
    "DOCUMENT_PRESIGNED_POST_TTL_SECONDS": 900,
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def make_user() -> "User":
    uid = uuid.uuid4().hex[:8]
    return User.objects.create_user(email=f"scanaudit-{uid}@example.com", password="hunter2")


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
    user,
    category: DocumentCategory,
    scan_status: str = Document.ScanStatus.SCANNING,
    storage_key: str | None = None,
) -> Document:
    return Document.objects.create(
        category=category,
        uploaded_by=user,
        original_filename="citizen-birth-certificate.pdf",  # PII-ish on purpose
        _storage_key=storage_key or _make_storage_key(str(uuid.uuid4()), prefix="quarantine"),
        mime_type="application/pdf",
        size_bytes=1024,
        scan_status=scan_status,
        security_classification=DocumentCategory.SecurityClassification.PROTECTED_B,
    )


def write_fake_file(storage_key: str, content: bytes = b"fake pdf bytes") -> None:
    """Write real bytes at MEDIA_ROOT/<storage_key> (FileSystemStorage in tests)."""
    from django.conf import settings as django_settings

    full_path = os.path.join(django_settings.MEDIA_ROOT, storage_key)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "wb") as fh:
        fh.write(content)


def run_scan(doc: Document, verdict: str) -> None:
    """Run scan_document with _scan_with_clamav stubbed to a fixed verdict."""
    with patch("apps.documents.tasks._scan_with_clamav", return_value=verdict):
        scan_document.run(str(doc.pk))


# ─────────────────────────────────────────────────────────────────────────────
# 1. ScanOutcomeAuditTests  (FIX #1)
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=CIVICOS_CLAMAV)
class ScanOutcomeAuditTests(TestCase):
    """Every scan outcome must leave exactly one audit entry."""

    def setUp(self):
        self.user = make_user()
        self.category = make_category()

    # ── Quarantine outcome ────────────────────────────────────────────────────

    def test_threat_detection_writes_threat_detected_audit_entry(self):
        doc = make_document(self.user, self.category)
        run_scan(doc, "Eicar-Test-Signature")

        entries = AuditLogEntry.objects.filter(
            event_type=AuditEventType.THREAT_DETECTED,
            resource_id=str(doc.pk),
        )
        self.assertEqual(
            entries.count(),
            1,
            "A ClamAV detection must write exactly one THREAT_DETECTED audit entry.",
        )

    def test_threat_detected_entry_has_minimal_event_detail_shape(self):
        doc = make_document(self.user, self.category)
        run_scan(doc, "Eicar-Test-Signature")

        entry = AuditLogEntry.objects.get(
            event_type=AuditEventType.THREAT_DETECTED,
            resource_id=str(doc.pk),
        )
        self.assertEqual(
            set(entry.event_detail.keys()),
            {"document_pk", "scan_engine_result"},
            "event_detail must contain ONLY the document pk and the scan engine result.",
        )
        self.assertEqual(entry.event_detail["document_pk"], str(doc.pk))
        self.assertEqual(
            entry.event_detail["scan_engine_result"],
            "FOUND: Eicar-Test-Signature",
        )

    def test_threat_detected_entry_carries_no_pii(self):
        """PIPEDA: no filename, no storage_key, no uploader identity anywhere."""
        doc = make_document(self.user, self.category)
        quarantine_key = doc.storage_key
        run_scan(doc, "Eicar-Test-Signature")

        entry = AuditLogEntry.objects.get(
            event_type=AuditEventType.THREAT_DETECTED,
            resource_id=str(doc.pk),
        )
        blob = str(entry.event_detail) + str(entry.before_state) + str(entry.after_state)
        self.assertNotIn("citizen-birth-certificate", blob)
        self.assertNotIn(quarantine_key, blob)
        self.assertNotIn(self.user.email, blob)
        self.assertEqual(entry.actor_email, "")
        self.assertIsNone(entry.actor_id, "A system-initiated scan has no human actor.")

    def test_threat_detected_entry_records_failure_outcome(self):
        doc = make_document(self.user, self.category)
        run_scan(doc, "Eicar-Test-Signature")

        entry = AuditLogEntry.objects.get(
            event_type=AuditEventType.THREAT_DETECTED,
            resource_id=str(doc.pk),
        )
        self.assertEqual(entry.outcome, "failure")
        self.assertEqual(entry.resource_type, "documents.Document")

    def test_storage_read_failure_also_writes_threat_detected_entry(self):
        """A permanent storage read failure quarantines — and must be audited."""
        from apps.documents.tasks import _StorageReadError

        doc = make_document(self.user, self.category)
        with patch(
            "apps.documents.tasks._scan_with_clamav",
            side_effect=_StorageReadError("boom"),
        ):
            scan_document.run(str(doc.pk))

        entry = AuditLogEntry.objects.get(
            event_type=AuditEventType.THREAT_DETECTED,
            resource_id=str(doc.pk),
        )
        self.assertEqual(
            entry.event_detail["scan_engine_result"],
            "FOUND: STORAGE_ERROR:_StorageReadError",
        )

    def test_retry_exhaustion_quarantine_writes_threat_detected_entry(self):
        """_quarantine_on_scan_failure (on_failure hook) must audit too."""
        doc = make_document(self.user, self.category)
        _quarantine_on_scan_failure(doc_pk=str(doc.pk), exc=RuntimeError("clamd down"))

        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.QUARANTINED)

        entry = AuditLogEntry.objects.get(
            event_type=AuditEventType.THREAT_DETECTED,
            resource_id=str(doc.pk),
        )
        self.assertEqual(
            entry.event_detail,
            {
                "document_pk": str(doc.pk),
                "scan_engine_result": "SCAN_FAILURE:RuntimeError",
            },
        )

    def test_idempotent_rerun_does_not_duplicate_the_audit_entry(self):
        """Re-running the task on an already-quarantined doc writes nothing new."""
        doc = make_document(self.user, self.category)
        run_scan(doc, "Eicar-Test-Signature")
        run_scan(doc, "Eicar-Test-Signature")

        self.assertEqual(
            AuditLogEntry.objects.filter(
                event_type=AuditEventType.THREAT_DETECTED,
                resource_id=str(doc.pk),
            ).count(),
            1,
        )

    # ── Clean outcome ─────────────────────────────────────────────────────────

    def test_clean_scan_writes_status_changed_audit_entry(self):
        doc = make_document(self.user, self.category)
        run_scan(doc, "OK")

        entries = AuditLogEntry.objects.filter(
            event_type=AuditEventType.STATUS_CHANGED,
            resource_id=str(doc.pk),
        )
        self.assertEqual(
            entries.count(),
            1,
            "A clean scan must write exactly one audit entry for the SCANNING→ACTIVE flip.",
        )

    def test_clean_scan_audit_entry_shape_and_privacy(self):
        doc = make_document(self.user, self.category)
        quarantine_key = doc.storage_key
        run_scan(doc, "OK")

        entry = AuditLogEntry.objects.get(
            event_type=AuditEventType.STATUS_CHANGED,
            resource_id=str(doc.pk),
        )
        self.assertEqual(
            entry.event_detail,
            {"document_pk": str(doc.pk), "scan_engine_result": "OK"},
        )
        self.assertEqual(entry.outcome, "success")
        self.assertEqual(entry.resource_type, "documents.Document")
        self.assertIsNone(entry.actor_id)
        blob = str(entry.event_detail)
        self.assertNotIn("citizen-birth-certificate", blob)
        self.assertNotIn(quarantine_key, blob)

    def test_clean_scan_does_not_write_a_threat_detected_entry(self):
        doc = make_document(self.user, self.category)
        run_scan(doc, "OK")
        self.assertFalse(
            AuditLogEntry.objects.filter(
                event_type=AuditEventType.THREAT_DETECTED,
                resource_id=str(doc.pk),
            ).exists()
        )

    def test_audit_chain_remains_verifiable_after_scan_outcomes(self):
        """The hash chain must still verify with the new entries in it."""
        clean_doc = make_document(self.user, self.category)
        infected_doc = make_document(self.user, self.category)
        run_scan(clean_doc, "OK")
        run_scan(infected_doc, "Eicar-Test-Signature")

        result = AuditLogEntry.verify_chain()
        self.assertTrue(getattr(result, "ok", result), "Audit hash chain must stay intact.")


# ─────────────────────────────────────────────────────────────────────────────
# 2. QuarantinePromotionTests  (FIX #2)
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=CIVICOS_CLAMAV)
class QuarantinePromotionTests(TestCase):
    """A clean scan must physically move the object quarantine/ → active/."""

    def setUp(self):
        self.user = make_user()
        self.category = make_category()

    def test_clean_scan_promotes_storage_key_to_active_prefix(self):
        doc = make_document(self.user, self.category)
        old_key = doc.storage_key
        write_fake_file(old_key)

        run_scan(doc, "OK")
        doc.refresh_from_db()

        self.assertEqual(doc.scan_status, Document.ScanStatus.ACTIVE)
        self.assertNotEqual(doc.storage_key, old_key, "storage_key must change.")
        self.assertTrue(doc.storage_key.startswith("documents/active/"))
        self.assertFalse(doc.storage_key.startswith("documents/quarantine/"))

    def test_promoted_key_preserves_the_document_and_file_uuids(self):
        doc = make_document(self.user, self.category)
        old_key = doc.storage_key
        write_fake_file(old_key)

        run_scan(doc, "OK")
        doc.refresh_from_db()

        self.assertEqual(
            doc.storage_key,
            old_key.replace("documents/quarantine/", "documents/active/", 1),
            "Only the prefix may change — the UUID path segments must be preserved.",
        )

    def test_object_is_reachable_at_the_new_location(self):
        doc = make_document(self.user, self.category)
        old_key = doc.storage_key
        write_fake_file(old_key, b"the real bytes")

        run_scan(doc, "OK")
        doc.refresh_from_db()

        self.assertTrue(default_storage.exists(doc.storage_key))
        with default_storage.open(doc.storage_key, "rb") as fh:
            self.assertEqual(fh.read(), b"the real bytes")

    def test_object_is_gone_from_the_old_quarantine_location(self):
        """
        The stale quarantine copy is deleted via transaction.on_commit(), so it
        must survive a rollback and disappear on commit. captureOnCommitCallbacks
        runs the deferred callback that TestCase's outer atomic block otherwise
        swallows.
        """
        doc = make_document(self.user, self.category)
        old_key = doc.storage_key
        write_fake_file(old_key)

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            run_scan(doc, "OK")

        self.assertEqual(
            len(callbacks), 1, "Exactly one deferred cleanup callback is expected."
        )
        self.assertFalse(
            default_storage.exists(old_key),
            "The stale quarantine copy must be deleted after the promotion commits.",
        )

    def test_quarantine_copy_survives_until_the_transaction_commits(self):
        """Without a commit, the old object must still be there (no data loss)."""
        doc = make_document(self.user, self.category)
        old_key = doc.storage_key
        write_fake_file(old_key)

        run_scan(doc, "OK")  # no captureOnCommitCallbacks → callback not executed

        self.assertTrue(default_storage.exists(old_key))
        doc.refresh_from_db()
        self.assertTrue(default_storage.exists(doc.storage_key))

    def test_promotion_is_skipped_when_no_object_exists(self):
        """Dev/test rows with no real bytes still go ACTIVE, key unchanged."""
        doc = make_document(self.user, self.category)
        old_key = doc.storage_key  # nothing written to storage

        run_scan(doc, "OK")
        doc.refresh_from_db()

        self.assertEqual(doc.scan_status, Document.ScanStatus.ACTIVE)
        self.assertEqual(doc.storage_key, old_key)

    def test_quarantined_document_is_not_promoted(self):
        doc = make_document(self.user, self.category)
        old_key = doc.storage_key
        write_fake_file(old_key)

        run_scan(doc, "Eicar-Test-Signature")
        doc.refresh_from_db()

        self.assertEqual(doc.scan_status, Document.ScanStatus.QUARANTINED)
        self.assertTrue(doc.storage_key.startswith("documents/quarantine/"))
        self.assertFalse(
            default_storage.exists(old_key),
            "An infected file must be deleted, never promoted.",
        )

    def test_dev_bypass_does_not_promote(self):
        """DEV_BYPASS means no scan ran — nothing has been proven clean."""
        doc = make_document(self.user, self.category)
        old_key = doc.storage_key
        write_fake_file(old_key)

        with override_settings(
            CIVICOS={**CIVICOS_CLAMAV, "CLAMAV_HOST": "", "CLAMAV_REQUIRED": False}
        ):
            scan_document.run(str(doc.pk))

        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.ACTIVE)
        self.assertEqual(doc.scan_engine_result, "DEV_BYPASS")
        self.assertEqual(doc.storage_key, old_key)


# ─────────────────────────────────────────────────────────────────────────────
# 3. PromotionFailureTests  (FIX #2 — failure path)
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=CIVICOS_CLAMAV)
class PromotionFailureTests(TestCase):
    """A failed move must never leave an ACTIVE row with a stale storage_key."""

    def setUp(self):
        self.user = make_user()
        self.category = make_category()

    def _run_with_failing_promotion(self, doc: Document):
        with patch("apps.documents.tasks._scan_with_clamav", return_value="OK"):
            with patch(
                "apps.documents.tasks._promote_storage_object_to_active",
                side_effect=_StoragePromotionError("S3 copy_object failed: ClientError"),
            ):
                with patch.object(scan_document, "retry", side_effect=Retry()) as mock_retry:
                    with self.assertRaises(Retry):
                        scan_document.run(str(doc.pk))
        return mock_retry

    def test_failed_promotion_leaves_document_in_scanning(self):
        doc = make_document(self.user, self.category)
        write_fake_file(doc.storage_key)

        self._run_with_failing_promotion(doc)

        doc.refresh_from_db()
        self.assertEqual(
            doc.scan_status,
            Document.ScanStatus.SCANNING,
            "The status flip must roll back with the failed move.",
        )

    def test_failed_promotion_leaves_storage_key_untouched(self):
        doc = make_document(self.user, self.category)
        old_key = doc.storage_key
        write_fake_file(old_key)

        self._run_with_failing_promotion(doc)

        doc.refresh_from_db()
        self.assertEqual(doc.storage_key, old_key)
        self.assertTrue(default_storage.exists(old_key))

    def test_failed_promotion_writes_no_clean_scan_audit_entry(self):
        doc = make_document(self.user, self.category)
        write_fake_file(doc.storage_key)

        self._run_with_failing_promotion(doc)

        self.assertFalse(
            AuditLogEntry.objects.filter(
                event_type=AuditEventType.STATUS_CHANGED,
                resource_id=str(doc.pk),
            ).exists(),
            "A rolled-back transaction must not leave an audit entry behind.",
        )

    def test_failed_promotion_retries_the_task(self):
        doc = make_document(self.user, self.category)
        write_fake_file(doc.storage_key)

        mock_retry = self._run_with_failing_promotion(doc)
        mock_retry.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 4. PromotionHelperUnitTests
# ─────────────────────────────────────────────────────────────────────────────


class PromotionHelperUnitTests(TestCase):
    """Direct coverage of _promote_storage_object_to_active()."""

    def test_returns_none_for_a_key_already_under_the_active_prefix(self):
        key = _make_storage_key(str(uuid.uuid4()), prefix="active")
        write_fake_file(key)
        self.assertIsNone(
            _promote_storage_object_to_active(doc_pk="x", storage_key=key),
            "An already-promoted key is an idempotent no-op.",
        )

    def test_returns_none_for_an_empty_key(self):
        self.assertIsNone(_promote_storage_object_to_active(doc_pk="x", storage_key=""))

    def test_returns_none_when_the_source_object_is_missing(self):
        key = _make_storage_key(str(uuid.uuid4()), prefix="quarantine")
        self.assertIsNone(_promote_storage_object_to_active(doc_pk="x", storage_key=key))

    def test_copies_the_object_and_returns_the_new_key(self):
        key = _make_storage_key(str(uuid.uuid4()), prefix="quarantine")
        write_fake_file(key, b"payload")

        new_key = _promote_storage_object_to_active(doc_pk="x", storage_key=key)

        self.assertEqual(new_key, key.replace("documents/quarantine/", "documents/active/", 1))
        self.assertTrue(default_storage.exists(new_key))
        # The helper itself does NOT delete the source — the caller does that
        # via transaction.on_commit() once the new key is durably committed.
        self.assertTrue(default_storage.exists(key))

    def test_wraps_unexpected_storage_errors_in_storage_promotion_error(self):
        key = _make_storage_key(str(uuid.uuid4()), prefix="quarantine")
        write_fake_file(key)

        with patch(
            "django.core.files.storage.default_storage.save",
            side_effect=OSError("disk full"),
        ):
            with self.assertRaises(_StoragePromotionError):
                _promote_storage_object_to_active(doc_pk="x", storage_key=key)
