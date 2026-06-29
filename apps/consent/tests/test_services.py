"""
Tests for ConsentService — business logic layer.

Covers:
- grant() happy path, idempotency, unknown slug
- withdraw() happy path, required category, non-existent record
- has_consent() all states
- get_citizen_consents() scoping
- request_export() creation, duplicate guard
- get_citizen_exports() scoping
"""
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.consent.models import (
    ConsentAuditEntry,
    ConsentCategory,
    ConsentRecord,
    DataExportRequest,
)
from apps.consent.services import ConsentService

User = get_user_model()
VALID_PASSWORD = "SecureTest123!"

_PROCESS_TASK = "apps.consent.tasks.process_data_export"


def _make_citizen(email=None):
    return User.objects.create_user(
        email=email or f"citizen-{uuid.uuid4().hex[:8]}@example.gov",
        password=VALID_PASSWORD,
    )


def _make_category(slug=None, is_required=False, is_active=True):
    slug = slug or f"cat-{uuid.uuid4().hex[:6]}"
    return ConsentCategory.objects.create(
        slug=slug,
        name_en=f"Category {slug}",
        name_fr=f"Catégorie {slug}",
        purpose_en="Test purpose",
        purpose_fr="Objet de test",
        lawful_basis="consent",
        is_required=is_required,
        is_active=is_active,
    )


class ConsentServiceGrantTests(TestCase):
    """Tests for ConsentService.grant()."""

    def setUp(self):
        self.citizen = _make_citizen()
        self.category = _make_category(slug="marketing")

    def test_grant_creates_record_with_granted_status(self):
        record = ConsentService.grant(self.citizen, "marketing")
        self.assertEqual(record.status, ConsentRecord.STATUS_GRANTED)
        self.assertIsNotNone(record.granted_at)

    def test_grant_returns_consent_record_instance(self):
        record = ConsentService.grant(self.citizen, "marketing")
        self.assertIsInstance(record, ConsentRecord)

    def test_grant_logs_audit_entry(self):
        ConsentService.grant(self.citizen, "marketing")
        entry = ConsentAuditEntry.objects.filter(
            citizen=self.citizen, action="granted", category=self.category
        ).first()
        self.assertIsNotNone(entry)

    def test_grant_idempotent_does_not_create_duplicate_records(self):
        ConsentService.grant(self.citizen, "marketing")
        ConsentService.grant(self.citizen, "marketing")
        count = ConsentRecord.objects.filter(
            citizen=self.citizen, category=self.category
        ).count()
        self.assertEqual(count, 1)

    def test_grant_idempotent_does_not_create_duplicate_audit_entry(self):
        # Re-granting an already-granted consent must NOT produce a phantom audit entry.
        # PIPEDA audit trail integrity: only real state changes are recorded.
        ConsentService.grant(self.citizen, "marketing")
        ConsentService.grant(self.citizen, "marketing")
        count = ConsentAuditEntry.objects.filter(
            citizen=self.citizen, action="granted"
        ).count()
        self.assertEqual(count, 1)

    def test_grant_unknown_slug_raises_value_error(self):
        with self.assertRaises(ValueError):
            ConsentService.grant(self.citizen, "nonexistent-category")

    def test_grant_inactive_category_raises_value_error(self):
        _make_category(slug="inactive-cat", is_active=False)
        with self.assertRaises(ValueError):
            ConsentService.grant(self.citizen, "inactive-cat")

    def test_grant_re_grants_withdrawn_consent(self):
        # Grant → Withdraw → Grant should set status back to granted
        record = ConsentService.grant(self.citizen, "marketing")
        # Manually withdraw
        record.status = ConsentRecord.STATUS_WITHDRAWN
        record.save(update_fields=["status"])
        # Re-grant
        record = ConsentService.grant(self.citizen, "marketing")
        self.assertEqual(record.status, ConsentRecord.STATUS_GRANTED)


class ConsentServiceWithdrawTests(TestCase):
    """Tests for ConsentService.withdraw()."""

    def setUp(self):
        self.citizen = _make_citizen()
        self.optional_category = _make_category(slug="analytics", is_required=False)
        self.required_category = _make_category(slug="platform", is_required=True)

    def test_withdraw_sets_status_withdrawn(self):
        ConsentService.grant(self.citizen, "analytics")
        record = ConsentService.withdraw(self.citizen, "analytics")
        self.assertEqual(record.status, ConsentRecord.STATUS_WITHDRAWN)

    def test_withdraw_sets_withdrawn_at(self):
        ConsentService.grant(self.citizen, "analytics")
        record = ConsentService.withdraw(self.citizen, "analytics")
        self.assertIsNotNone(record.withdrawn_at)

    def test_withdraw_logs_audit_entry(self):
        ConsentService.grant(self.citizen, "analytics")
        ConsentService.withdraw(self.citizen, "analytics")
        entry = ConsentAuditEntry.objects.filter(
            citizen=self.citizen, action="withdrawn", category=self.optional_category
        ).first()
        self.assertIsNotNone(entry)

    def test_withdraw_required_category_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            ConsentService.withdraw(self.citizen, "platform")
        self.assertIn("required", str(ctx.exception).lower())

    def test_withdraw_nonexistent_record_raises_value_error(self):
        # No consent record has been created for analytics
        with self.assertRaises(ValueError):
            ConsentService.withdraw(self.citizen, "analytics")

    def test_withdraw_unknown_slug_raises_value_error(self):
        with self.assertRaises(ValueError):
            ConsentService.withdraw(self.citizen, "totally-unknown")


class ConsentServiceHasConsentTests(TestCase):
    """Tests for ConsentService.has_consent()."""

    def setUp(self):
        self.citizen = _make_citizen()
        self.category = _make_category(slug="newsletter")

    def test_returns_true_when_granted(self):
        ConsentService.grant(self.citizen, "newsletter")
        self.assertTrue(ConsentService.has_consent(self.citizen, "newsletter"))

    def test_returns_false_when_withdrawn(self):
        ConsentService.grant(self.citizen, "newsletter")
        ConsentService.withdraw(self.citizen, "newsletter")
        self.assertFalse(ConsentService.has_consent(self.citizen, "newsletter"))

    def test_returns_false_when_no_record(self):
        self.assertFalse(ConsentService.has_consent(self.citizen, "newsletter"))

    def test_returns_false_for_different_citizen(self):
        other_citizen = _make_citizen()
        ConsentService.grant(other_citizen, "newsletter")
        self.assertFalse(ConsentService.has_consent(self.citizen, "newsletter"))


class ConsentServiceGetCitizenConsentsTests(TestCase):
    """Tests for ConsentService.get_citizen_consents()."""

    def setUp(self):
        self.citizen = _make_citizen()
        self.other_citizen = _make_citizen()
        self.cat1 = _make_category(slug="cat-one")
        self.cat2 = _make_category(slug="cat-two")

    def test_returns_only_this_citizens_records(self):
        ConsentService.grant(self.citizen, "cat-one")
        ConsentService.grant(self.other_citizen, "cat-two")
        records = list(ConsentService.get_citizen_consents(self.citizen))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].citizen_id, self.citizen.pk)

    def test_returns_all_categories_for_citizen(self):
        ConsentService.grant(self.citizen, "cat-one")
        ConsentService.grant(self.citizen, "cat-two")
        records = list(ConsentService.get_citizen_consents(self.citizen))
        self.assertEqual(len(records), 2)

    def test_returns_empty_queryset_for_new_citizen(self):
        new_citizen = _make_citizen()
        records = ConsentService.get_citizen_consents(new_citizen)
        self.assertEqual(records.count(), 0)


class ConsentServiceGetActiveCategoriesTests(TestCase):
    """Tests for ConsentService.get_active_categories()."""

    def test_returns_only_active_categories(self):
        _make_category(slug="active-one", is_active=True)
        _make_category(slug="inactive-one", is_active=False)
        cats = ConsentService.get_active_categories()
        slugs = list(cats.values_list("slug", flat=True))
        self.assertIn("active-one", slugs)
        self.assertNotIn("inactive-one", slugs)


class ConsentServiceRequestExportTests(TestCase):
    """Tests for ConsentService.request_export()."""

    def setUp(self):
        self.citizen = _make_citizen()

    def test_creates_data_export_request(self):
        with self.captureOnCommitCallbacks(execute=False):
            export = ConsentService.request_export(self.citizen)
        self.assertIsNotNone(export.pk)
        self.assertEqual(export.status, DataExportRequest.STATUS_PENDING)
        self.assertEqual(export.citizen_id, self.citizen.pk)

    def test_creates_audit_entry_for_export(self):
        with self.captureOnCommitCallbacks(execute=False):
            ConsentService.request_export(self.citizen)
        entry = ConsentAuditEntry.objects.filter(
            citizen=self.citizen, action="export_requested"
        ).first()
        self.assertIsNotNone(entry)

    def test_queues_celery_task_on_commit(self):
        # Fix 1: process_data_export is now called via .delay() for async execution.
        with patch(_PROCESS_TASK + ".delay") as mock_delay:
            with self.captureOnCommitCallbacks(execute=True):
                ConsentService.request_export(self.citizen)
        mock_delay.assert_called_once()

    def test_duplicate_pending_request_raises_value_error(self):
        with self.captureOnCommitCallbacks(execute=False):
            ConsentService.request_export(self.citizen)
        with self.assertRaises(ValueError):
            ConsentService.request_export(self.citizen)

    def test_duplicate_processing_request_raises_value_error(self):
        with self.captureOnCommitCallbacks(execute=False):
            export = ConsentService.request_export(self.citizen)
        export.status = DataExportRequest.STATUS_PROCESSING
        export.save(update_fields=["status"])
        with self.assertRaises(ValueError):
            ConsentService.request_export(self.citizen)

    def test_can_create_new_export_after_ready(self):
        with self.captureOnCommitCallbacks(execute=False):
            export = ConsentService.request_export(self.citizen)
        export.status = DataExportRequest.STATUS_READY
        export.save(update_fields=["status"])
        # Should not raise
        with self.captureOnCommitCallbacks(execute=False):
            new_export = ConsentService.request_export(self.citizen)
        self.assertNotEqual(export.pk, new_export.pk)


class ConsentServiceGetCitizenExportsTests(TestCase):
    """Tests for ConsentService.get_citizen_exports()."""

    def setUp(self):
        self.citizen = _make_citizen()
        self.other_citizen = _make_citizen()

    def test_returns_only_this_citizens_exports(self):
        DataExportRequest.objects.create(citizen=self.citizen)
        DataExportRequest.objects.create(citizen=self.other_citizen)
        exports = ConsentService.get_citizen_exports(self.citizen)
        self.assertEqual(exports.count(), 1)
        self.assertEqual(exports.first().citizen_id, self.citizen.pk)

    def test_returns_all_exports_for_citizen(self):
        # Use non-pending statuses to avoid unique_active_export_per_citizen constraint
        DataExportRequest.objects.create(
            citizen=self.citizen, status=DataExportRequest.STATUS_DELIVERED
        )
        DataExportRequest.objects.create(
            citizen=self.citizen, status=DataExportRequest.STATUS_DELIVERED
        )
        exports = ConsentService.get_citizen_exports(self.citizen)
        self.assertEqual(exports.count(), 2)

    def test_returns_empty_for_new_citizen(self):
        new_citizen = _make_citizen()
        exports = ConsentService.get_citizen_exports(new_citizen)
        self.assertEqual(exports.count(), 0)
