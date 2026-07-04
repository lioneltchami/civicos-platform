"""
Tests for apps.documents.services.retention.schedule_expiry — Wave 2.

Tests verify:
  - expires_at and retain_until set correctly from category days settings
  - Transitory categories: expires_at stays None; retain_until still set if min > 0
  - Zero min_retention_days: retain_until stays None
  - Zero max_retention_days (non-transitory): expires_at stays None
  - Saved with update_fields (only expected fields touched)
  - Privacy Act s.6(1): 730-day minimum retained correctly
  - LAC DA #2016/001: transitory records don't get a hard expires_at
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.documents.models import Document, DocumentCategory
from apps.documents.services.retention import schedule_expiry

User = get_user_model()


def make_category(**kwargs) -> DocumentCategory:
    defaults = {
        "name_en": "Test Category",
        "name_fr": "Catégorie de test",
        "slug": f"ret-test-{uuid.uuid4().hex[:6]}",
        "security_classification": DocumentCategory.SecurityClassification.PROTECTED_B,
        "allowed_mime_types": [],
        "max_size_bytes": 0,
        "min_retention_days": 730,
        "max_retention_days": 2555,
        "is_transitory": False,
    }
    defaults.update(kwargs)
    return DocumentCategory.objects.create(**defaults)


def make_user() -> User:
    uid = uuid.uuid4().hex[:8]
    return User.objects.create_user(
        email=f"ret-{uid}@example.com",
        password="hunter2",
    )


def make_document(category: DocumentCategory, user: User) -> Document:
    """
    Create a Document in PENDING_UPLOAD state (pre-expiry-scheduling).
    Does NOT call schedule_expiry — that is the function under test.
    """
    return Document.objects.create(
        category=category,
        uploaded_by=user,
        original_filename="test.pdf",
        _storage_key=f"documents/quarantine/{uuid.uuid4().hex}/{uuid.uuid4().hex}.bin",
        mime_type="application/pdf",
        size_bytes=1024,
        scan_status=Document.ScanStatus.PENDING_UPLOAD,
        security_classification=category.security_classification,
        # expires_at and retain_until are intentionally left as None (model defaults)
    )


class ScheduleExpiryNonTransitoryTests(TestCase):
    """
    Tests for standard (non-transitory) document categories.
    Both expires_at and retain_until should be set.
    """

    def setUp(self):
        self.user = make_user()

    def test_expires_at_set_to_created_at_plus_max_retention(self):
        """
        expires_at = created_at + max_retention_days (as datetime).
        """
        category = make_category(max_retention_days=2555, min_retention_days=730)
        doc = make_document(category, self.user)

        schedule_expiry(document=doc)
        doc.refresh_from_db()

        expected_expires_at = doc.created_at + timedelta(days=2555)
        # Allow 5-second tolerance for test execution time
        self.assertAlmostEqual(
            doc.expires_at.timestamp(),
            expected_expires_at.timestamp(),
            delta=5.0,
        )

    def test_retain_until_set_to_created_at_plus_min_retention_as_date(self):
        """
        retain_until = (created_at + min_retention_days).date()
        It's a DateField — must be a date, not a datetime.
        """
        category = make_category(max_retention_days=2555, min_retention_days=730)
        doc = make_document(category, self.user)

        schedule_expiry(document=doc)
        doc.refresh_from_db()

        expected_date = (doc.created_at + timedelta(days=730)).date()
        self.assertEqual(doc.retain_until, expected_date)
        self.assertIsInstance(doc.retain_until, date)

    def test_expires_at_is_after_retain_until(self):
        """
        max_retention_days >= min_retention_days (enforced by DocumentCategory.clean()).
        expires_at must be on or after retain_until.
        """
        category = make_category(max_retention_days=2555, min_retention_days=730)
        doc = make_document(category, self.user)
        schedule_expiry(document=doc)
        doc.refresh_from_db()

        self.assertGreaterEqual(doc.expires_at.date(), doc.retain_until)

    def test_seven_year_category(self):
        """Verify the math for a 7-year (2555-day) max retention category."""
        category = make_category(max_retention_days=2555, min_retention_days=730)
        doc = make_document(category, self.user)
        schedule_expiry(document=doc)
        doc.refresh_from_db()

        delta = doc.expires_at - doc.created_at
        self.assertAlmostEqual(delta.days, 2555, delta=1)

    def test_privacy_act_minimum_730_days(self):
        """
        Privacy Act s.6(1): minimum 2-year (730-day) retention.
        retain_until must be at least 730 days from created_at.
        """
        category = make_category(min_retention_days=730, max_retention_days=730)
        doc = make_document(category, self.user)
        schedule_expiry(document=doc)
        doc.refresh_from_db()

        delta = doc.retain_until - doc.created_at.date()
        self.assertGreaterEqual(delta.days, 730)

    def test_equal_min_max_retention(self):
        """When min == max, expires_at and retain_until should be on the same day."""
        category = make_category(min_retention_days=730, max_retention_days=730)
        doc = make_document(category, self.user)
        schedule_expiry(document=doc)
        doc.refresh_from_db()

        self.assertEqual(doc.expires_at.date(), doc.retain_until)

    def test_short_max_retention(self):
        """Verify correct calculation for a short-lived document category."""
        category = make_category(min_retention_days=30, max_retention_days=90)
        doc = make_document(category, self.user)
        schedule_expiry(document=doc)
        doc.refresh_from_db()

        delta_expires = doc.expires_at - doc.created_at
        delta_retain = doc.retain_until - doc.created_at.date()

        self.assertAlmostEqual(delta_expires.days, 90, delta=1)
        self.assertEqual(delta_retain.days, 30)


class ScheduleExpiryTransitoryTests(TestCase):
    """
    Tests for transitory document categories (LAC DA #2016/001).
    expires_at must NOT be set at creation; retain_until is set if min > 0.
    """

    def setUp(self):
        self.user = make_user()

    def test_transitory_expires_at_is_none(self):
        """
        Transitory records must NOT have expires_at set at creation.
        It is set later when purpose is fulfilled (mark_purpose_fulfilled).
        LAC DA #2016/001: "transitory records destroyed once purpose fulfilled."
        """
        category = make_category(
            is_transitory=True,
            min_retention_days=730,
            max_retention_days=2555,
        )
        doc = make_document(category, self.user)
        schedule_expiry(document=doc)
        doc.refresh_from_db()

        self.assertIsNone(doc.expires_at)

    def test_transitory_retain_until_set_if_min_positive(self):
        """
        Even transitory records must respect the minimum retention floor
        if the category defines one (e.g. 730 days for administrative records).
        """
        category = make_category(
            is_transitory=True,
            min_retention_days=730,
            max_retention_days=2555,
        )
        doc = make_document(category, self.user)
        schedule_expiry(document=doc)
        doc.refresh_from_db()

        self.assertIsNotNone(doc.retain_until)
        expected_date = (doc.created_at + timedelta(days=730)).date()
        self.assertEqual(doc.retain_until, expected_date)

    def test_transitory_zero_min_retention_both_none(self):
        """
        Transitory + zero min_retention_days → both expires_at and retain_until
        are None. The document is destroyed at purpose-fulfilled time only.
        """
        category = make_category(
            is_transitory=True,
            min_retention_days=0,
            max_retention_days=2555,
        )
        doc = make_document(category, self.user)
        schedule_expiry(document=doc)
        doc.refresh_from_db()

        self.assertIsNone(doc.expires_at)
        self.assertIsNone(doc.retain_until)

    def test_transitory_doc_not_in_pending_disposal_immediately(self):
        """
        A newly created transitory document should NOT appear in pending_disposal()
        because expires_at is None (no disposal scheduled yet).
        """
        category = make_category(
            is_transitory=True,
            min_retention_days=0,
            max_retention_days=2555,
        )
        doc = make_document(category, self.user)
        schedule_expiry(document=doc)

        # pending_disposal() requires both expires_at and retain_until to be in the past
        self.assertNotIn(doc, Document.objects.pending_disposal())


class ScheduleExpiryZeroRetentionTests(TestCase):
    """
    Edge cases with zero min_retention_days or zero max_retention_days.
    """

    def setUp(self):
        self.user = make_user()

    def test_zero_min_retention_retain_until_is_none(self):
        category = make_category(min_retention_days=0, max_retention_days=2555)
        doc = make_document(category, self.user)
        schedule_expiry(document=doc)
        doc.refresh_from_db()

        self.assertIsNone(doc.retain_until)
        # expires_at should still be set (non-transitory)
        self.assertIsNotNone(doc.expires_at)

    def test_zero_max_retention_non_transitory_expires_at_none(self):
        """
        If max_retention_days=0 for a non-transitory category, expires_at is None
        (document never automatically expires on a calendar date).
        This is an unusual configuration — validates the boundary.
        """
        category = make_category(min_retention_days=0, max_retention_days=0)
        doc = make_document(category, self.user)
        schedule_expiry(document=doc)
        doc.refresh_from_db()

        self.assertIsNone(doc.expires_at)


class ScheduleExpiryPersistenceTests(TestCase):
    """
    Tests verifying correct DB persistence behaviour of schedule_expiry().
    """

    def setUp(self):
        self.user = make_user()

    def test_expires_at_saved_to_db(self):
        """After schedule_expiry(), a fresh DB fetch must return the correct value."""
        category = make_category(max_retention_days=2555, min_retention_days=730)
        doc = make_document(category, self.user)
        schedule_expiry(document=doc)

        # Fresh fetch — verify persisted
        fresh = Document.objects.get(pk=doc.pk)
        self.assertIsNotNone(fresh.expires_at)

    def test_retain_until_saved_to_db(self):
        category = make_category(max_retention_days=2555, min_retention_days=730)
        doc = make_document(category, self.user)
        schedule_expiry(document=doc)

        fresh = Document.objects.get(pk=doc.pk)
        self.assertIsNotNone(fresh.retain_until)

    def test_schedule_expiry_only_touches_retention_fields(self):
        """
        schedule_expiry() must use update_fields to avoid clobbering other
        fields that may have been set concurrently (e.g. scan_status).
        We verify this by confirming scan_status is not changed.
        """
        category = make_category(max_retention_days=2555, min_retention_days=730)
        doc = make_document(category, self.user)
        # Set a non-default scan_status to detect if it's overwritten
        doc.scan_status = Document.ScanStatus.PENDING_UPLOAD
        doc.save(update_fields=["scan_status", "updated_at"])

        schedule_expiry(document=doc)
        doc.refresh_from_db()

        self.assertEqual(doc.scan_status, Document.ScanStatus.PENDING_UPLOAD)

    def test_idempotent_second_call(self):
        """
        A second call to schedule_expiry() on the same document must not
        corrupt the values. (It re-derives from created_at, so it's stable.)
        """
        category = make_category(max_retention_days=2555, min_retention_days=730)
        doc = make_document(category, self.user)
        schedule_expiry(document=doc)
        doc.refresh_from_db()
        first_expires_at = doc.expires_at
        first_retain_until = doc.retain_until

        schedule_expiry(document=doc)
        doc.refresh_from_db()

        self.assertEqual(doc.expires_at, first_expires_at)
        self.assertEqual(doc.retain_until, first_retain_until)

    def test_save_uses_update_fields(self):
        """
        Verify that save() is called with update_fields containing exactly
        the retention fields + updated_at (not a full-model save).
        """
        category = make_category(max_retention_days=2555, min_retention_days=730)
        doc = make_document(category, self.user)

        with patch.object(doc, "save", wraps=doc.save) as mock_save:
            schedule_expiry(document=doc)

        mock_save.assert_called_once()
        call_kwargs = mock_save.call_args.kwargs
        update_fields = set(call_kwargs.get("update_fields", []))

        self.assertIn("expires_at", update_fields)
        self.assertIn("retain_until", update_fields)
        self.assertIn("updated_at", update_fields)
        # Must NOT be a full-model save (no scan_status, no uploaded_by, etc.)
        self.assertNotIn("scan_status", update_fields)
        self.assertNotIn("original_filename", update_fields)


class ScheduleExpiryPendingDisposalIntegrationTests(TestCase):
    """
    Integration tests verifying that documents correctly appear in (or are
    excluded from) pending_disposal() based on schedule_expiry() output.
    """

    def setUp(self):
        self.user = make_user()

    def test_fresh_document_not_in_pending_disposal(self):
        """A newly created document with standard retention is NOT yet eligible."""
        category = make_category(max_retention_days=2555, min_retention_days=730)
        doc = make_document(category, self.user)
        schedule_expiry(document=doc)

        self.assertNotIn(doc, Document.objects.pending_disposal())

    def test_backdate_expires_at_and_retain_until_appears_in_pending(self):
        """
        Simulate a document past its retention period by backdating both
        expires_at and retain_until. It must appear in pending_disposal().
        """
        category = make_category(max_retention_days=730, min_retention_days=730)
        doc = make_document(category, self.user)
        schedule_expiry(document=doc)

        # Backdate to simulate a document created 3 years ago
        past_datetime = timezone.now() - timedelta(days=1100)
        past_date = (timezone.now() - timedelta(days=1100)).date()
        Document.objects.filter(pk=doc.pk).update(
            expires_at=past_datetime,
            retain_until=past_date,
        )

        self.assertIn(doc, Document.objects.pending_disposal())

    def test_only_expires_at_past_not_in_pending_disposal(self):
        """
        BOTH expires_at AND retain_until must be past for disposal.
        Privacy Act s.6(1): retain_until is the hard floor.
        """
        category = make_category(max_retention_days=730, min_retention_days=730)
        doc = make_document(category, self.user)
        schedule_expiry(document=doc)

        # expires_at in the past, retain_until in the future
        Document.objects.filter(pk=doc.pk).update(
            expires_at=timezone.now() - timedelta(days=10),
            retain_until=(timezone.now() + timedelta(days=720)).date(),
        )

        self.assertNotIn(doc, Document.objects.pending_disposal())

    def test_legal_hold_blocks_pending_disposal(self):
        """
        legal_hold=True is an absolute block — even if retention period has passed.
        """
        category = make_category(max_retention_days=730, min_retention_days=730)
        doc = make_document(category, self.user)
        schedule_expiry(document=doc)

        Document.objects.filter(pk=doc.pk).update(
            expires_at=timezone.now() - timedelta(days=1100),
            retain_until=(timezone.now() - timedelta(days=1100)).date(),
            legal_hold=True,
        )

        self.assertNotIn(doc, Document.objects.pending_disposal())
