"""
Tests for Consent & Privacy models.

Covers:
- ConsentCategory creation, str, required/optional
- ConsentRecord creation, status transitions, unique_together
- ConsentRecord auto-sets granted_at / withdrawn_at on status change
- DataExportRequest creation, STATUS_* constants, download_token uniqueness
- ConsentAuditEntry creation, immutability
"""

import uuid

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from apps.consent.models import (
    ConsentAuditEntry,
    ConsentCategory,
    ConsentRecord,
    ConsentRevision,
    DataExportRequest,
)

User = get_user_model()
VALID_PASSWORD = "SecureTest123!"


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


class ConsentCategoryModelTests(TestCase):
    """Tests for the ConsentCategory model."""

    def test_create_basic_category(self):
        cat = _make_category(slug="analytics")
        self.assertEqual(cat.slug, "analytics")
        self.assertFalse(cat.is_required)
        self.assertTrue(cat.is_active)

    def test_str_returns_name_en(self):
        cat = _make_category(slug="marketing")
        cat.name_en = "Marketing Consent"
        cat.save()
        self.assertEqual(str(cat), "Marketing Consent")

    def test_required_category(self):
        cat = _make_category(slug="platform-operations", is_required=True)
        self.assertTrue(cat.is_required)

    def test_optional_category(self):
        cat = _make_category(slug="optional-analytics", is_required=False)
        self.assertFalse(cat.is_required)

    def test_inactive_category(self):
        cat = _make_category(slug="inactive-cat", is_active=False)
        self.assertFalse(cat.is_active)

    def test_default_sort_order_zero(self):
        cat = _make_category()
        self.assertEqual(cat.sort_order, 0)

    def test_slug_is_unique(self):
        _make_category(slug="unique-slug")
        with self.assertRaises(IntegrityError):
            _make_category(slug="unique-slug")

    def test_ordering_by_sort_order_then_slug(self):
        ConsentCategory.objects.create(
            slug="b-cat",
            name_en="B",
            name_fr="B",
            purpose_en="p",
            purpose_fr="p",
            sort_order=1,
        )
        ConsentCategory.objects.create(
            slug="a-cat",
            name_en="A",
            name_fr="A",
            purpose_en="p",
            purpose_fr="p",
            sort_order=0,
        )
        cats = list(ConsentCategory.objects.all())
        self.assertEqual(cats[0].slug, "a-cat")
        self.assertEqual(cats[1].slug, "b-cat")


class ConsentRecordModelTests(TestCase):
    """Tests for the ConsentRecord model."""

    def setUp(self):
        self.citizen = _make_citizen()
        self.category = _make_category()

    def test_create_consent_record(self):
        record = ConsentRecord.objects.create(
            citizen=self.citizen,
            category=self.category,
            status=ConsentRecord.STATUS_PENDING,
        )
        self.assertEqual(record.status, "pending")
        self.assertIsNone(record.granted_at)
        self.assertIsNone(record.withdrawn_at)

    def test_status_constants(self):
        self.assertEqual(ConsentRecord.STATUS_PENDING, "pending")
        self.assertEqual(ConsentRecord.STATUS_GRANTED, "granted")
        self.assertEqual(ConsentRecord.STATUS_WITHDRAWN, "withdrawn")

    def test_str_representation(self):
        record = ConsentRecord.objects.create(
            citizen=self.citizen,
            category=self.category,
            status=ConsentRecord.STATUS_PENDING,
        )
        self.assertIn(f"ConsentRecord #{record.pk}", str(record))
        self.assertIn("pending", str(record))

    def test_multiple_records_per_citizen_category_allowed(self):
        """
        F4 fix: unique_together removed — multiple ConsentRecord rows per
        citizen/category are now allowed to preserve full consent history.
        The is_current BooleanField identifies the authoritative current row.

        Fix 4 (DB-level uniqueness): only one row may have is_current=True at
        a time per (citizen, category) — r1 must be explicitly archived
        (is_current=False) before r2 is created, matching what
        ConsentService.grant() itself does before creating a new "current"
        row (see the archive-then-create sequence in services.py).
        """
        r1 = ConsentRecord.objects.create(
            citizen=self.citizen,
            category=self.category,
            status=ConsentRecord.STATUS_PENDING,
            is_current=False,
        )
        r2 = ConsentRecord.objects.create(
            citizen=self.citizen,
            category=self.category,
            status=ConsentRecord.STATUS_GRANTED,
        )
        self.assertNotEqual(r1.pk, r2.pk)
        self.assertEqual(
            ConsentRecord.objects.filter(citizen=self.citizen, category=self.category).count(),
            2,
        )

    def test_two_citizens_same_category_allowed(self):
        citizen2 = _make_citizen()
        ConsentRecord.objects.create(citizen=self.citizen, category=self.category)
        record2 = ConsentRecord.objects.create(citizen=citizen2, category=self.category)
        self.assertIsNotNone(record2.pk)

    def test_second_is_current_row_same_citizen_category_raises_integrity_error(self):
        """
        Fix 4: unique_current_consent_record_per_citizen_category (a DB-level
        partial UniqueConstraint on (citizen, category) WHERE is_current=True)
        must reject a second is_current=True row for the same citizen/category
        pair, even outside of ConsentService.grant()'s own archive-then-create
        sequence.

        This is the non-concurrency-dependent guard called for in the
        certifiability review: it validates the constraint exists and works
        even without simulating true concurrent requests (which requires a
        real transaction-capable DB — see ConcurrentFirstGrantTests below).
        """
        ConsentRecord.objects.create(
            citizen=self.citizen,
            category=self.category,
            status=ConsentRecord.STATUS_PENDING,
            is_current=True,
        )
        with self.assertRaises(IntegrityError):
            ConsentRecord.objects.create(
                citizen=self.citizen,
                category=self.category,
                status=ConsentRecord.STATUS_PENDING,
                is_current=True,
            )

    def test_is_current_false_rows_do_not_collide(self):
        """Any number of is_current=False (historical) rows may coexist."""
        ConsentRecord.objects.create(
            citizen=self.citizen,
            category=self.category,
            is_current=False,
        )
        ConsentRecord.objects.create(
            citizen=self.citizen,
            category=self.category,
            is_current=False,
        )
        third = ConsentRecord.objects.create(
            citizen=self.citizen,
            category=self.category,
            is_current=True,
        )
        self.assertIsNotNone(third.pk)
        self.assertEqual(
            ConsentRecord.objects.filter(citizen=self.citizen, category=self.category).count(),
            3,
        )

    def test_default_source_is_web(self):
        record = ConsentRecord.objects.create(citizen=self.citizen, category=self.category)
        self.assertEqual(record.source, "web")

    def test_granted_at_can_be_set(self):
        now = timezone.now()
        record = ConsentRecord.objects.create(
            citizen=self.citizen,
            category=self.category,
            status=ConsentRecord.STATUS_GRANTED,
            granted_at=now,
        )
        self.assertEqual(record.granted_at, now)

    def test_withdrawn_at_can_be_set(self):
        now = timezone.now()
        record = ConsentRecord.objects.create(
            citizen=self.citizen,
            category=self.category,
            status=ConsentRecord.STATUS_WITHDRAWN,
            withdrawn_at=now,
        )
        self.assertEqual(record.withdrawn_at, now)

    def test_update_status_to_granted(self):
        record = ConsentRecord.objects.create(
            citizen=self.citizen,
            category=self.category,
            status=ConsentRecord.STATUS_PENDING,
        )
        now = timezone.now()
        record.status = ConsentRecord.STATUS_GRANTED
        record.granted_at = now
        record.save()
        record.refresh_from_db()
        self.assertEqual(record.status, ConsentRecord.STATUS_GRANTED)
        self.assertEqual(record.granted_at, now)

    def test_update_status_to_withdrawn(self):
        record = ConsentRecord.objects.create(
            citizen=self.citizen,
            category=self.category,
            status=ConsentRecord.STATUS_GRANTED,
            granted_at=timezone.now(),
        )
        now = timezone.now()
        record.status = ConsentRecord.STATUS_WITHDRAWN
        record.withdrawn_at = now
        record.save()
        record.refresh_from_db()
        self.assertEqual(record.status, ConsentRecord.STATUS_WITHDRAWN)
        self.assertEqual(record.withdrawn_at, now)


class DataExportRequestModelTests(TestCase):
    """Tests for the DataExportRequest model."""

    def setUp(self):
        self.citizen = _make_citizen()

    def test_create_export_request(self):
        export = DataExportRequest.objects.create(citizen=self.citizen)
        self.assertIsNotNone(export.pk)
        self.assertIsInstance(export.pk, uuid.UUID)

    def test_default_status_is_pending(self):
        export = DataExportRequest.objects.create(citizen=self.citizen)
        self.assertEqual(export.status, DataExportRequest.STATUS_PENDING)

    def test_status_constants(self):
        self.assertEqual(DataExportRequest.STATUS_PENDING, "pending")
        self.assertEqual(DataExportRequest.STATUS_PROCESSING, "processing")
        self.assertEqual(DataExportRequest.STATUS_READY, "ready")
        self.assertEqual(DataExportRequest.STATUS_DELIVERED, "delivered")
        self.assertEqual(DataExportRequest.STATUS_FAILED, "failed")
        self.assertEqual(DataExportRequest.STATUS_EXPIRED, "expired")

    def test_download_token_is_unique(self):
        # Use non-conflicting statuses to avoid unique_active_export_per_citizen constraint
        token1 = DataExportRequest.objects.create(
            citizen=self.citizen, status=DataExportRequest.STATUS_DELIVERED
        ).download_token
        token2 = DataExportRequest.objects.create(
            citizen=self.citizen, status=DataExportRequest.STATUS_DELIVERED
        ).download_token
        self.assertNotEqual(token1, token2)

    def test_str_representation(self):
        export = DataExportRequest.objects.create(citizen=self.citizen)
        result = str(export)
        self.assertIn("Export", result)
        self.assertIn(str(export.pk), result)

    def test_default_format_is_json(self):
        export = DataExportRequest.objects.create(citizen=self.citizen)
        self.assertEqual(export.format, "json")

    def test_expires_at_nullable(self):
        export = DataExportRequest.objects.create(citizen=self.citizen)
        self.assertIsNone(export.expires_at)

    def test_ordering_newest_first(self):
        # Use non-conflicting statuses to avoid unique_active_export_per_citizen constraint
        DataExportRequest.objects.create(
            citizen=self.citizen, status=DataExportRequest.STATUS_DELIVERED
        )
        export2 = DataExportRequest.objects.create(
            citizen=self.citizen, status=DataExportRequest.STATUS_DELIVERED
        )
        exports = list(DataExportRequest.objects.filter(citizen=self.citizen))
        # Newest first — export2 should come before export1
        self.assertEqual(exports[0].pk, export2.pk)


class ConsentAuditEntryModelTests(TestCase):
    """Tests for ConsentAuditEntry — immutability and creation."""

    def setUp(self):
        self.citizen = _make_citizen()
        self.category = _make_category()

    def test_create_audit_entry(self):
        entry = ConsentAuditEntry.objects.create(
            citizen=self.citizen,
            actor=self.citizen,
            action="granted",
            category=self.category,
            details={"category_slug": self.category.slug},
        )
        self.assertIsNotNone(entry.pk)
        self.assertEqual(entry.action, "granted")

    def test_str_representation(self):
        entry = ConsentAuditEntry.objects.create(
            citizen=self.citizen,
            actor=self.citizen,
            action="granted",
        )
        self.assertIn("granted", str(entry))

    def test_category_nullable(self):
        entry = ConsentAuditEntry.objects.create(
            citizen=self.citizen,
            actor=self.citizen,
            action="export_requested",
            category=None,
        )
        self.assertIsNone(entry.category)

    def test_export_request_nullable(self):
        entry = ConsentAuditEntry.objects.create(
            citizen=self.citizen,
            actor=self.citizen,
            action="granted",
            export_request=None,
        )
        self.assertIsNone(entry.export_request)

    def test_details_defaults_to_empty_dict(self):
        entry = ConsentAuditEntry.objects.create(
            citizen=self.citizen,
            actor=self.citizen,
            action="granted",
        )
        self.assertEqual(entry.details, {})

    def test_timestamp_set_on_creation(self):
        before = timezone.now()
        entry = ConsentAuditEntry.objects.create(
            citizen=self.citizen, actor=self.citizen, action="granted"
        )
        after = timezone.now()
        self.assertGreaterEqual(entry.timestamp, before)
        self.assertLessEqual(entry.timestamp, after)

    def test_actor_nullable(self):
        # actor can be NULL (e.g. system-generated audit entries)
        entry = ConsentAuditEntry.objects.create(
            citizen=self.citizen,
            actor=None,
            action="export_expired",
        )
        self.assertIsNone(entry.actor)


# ===========================================================================
# Bug 7 (RTBF/PIPEDA erasure gap) — ConsentRevision.redact_pii()
# ===========================================================================


def _make_consent_record_revision(citizen, category):
    """
    Build a minimal ConsentRecord + ConsentRevision pair shaped like the one
    ConsentService.grant() produces via _consent_record_snapshot(), without
    pulling in the full service layer (kept model-level, per this file's
    scope).
    """
    record = ConsentRecord.objects.create(
        citizen=citizen,
        category=category,
        status=ConsentRecord.STATUS_GRANTED,
        state=ConsentRecord.STATE_SIGNED,
        is_current=True,
    )
    return ConsentRevision.create_for(
        schema_name="ConsentRecord",
        obj=record,
        snapshot={
            "id": str(record.pk),
            "individual": str(citizen.pk),
            "dataAgreement": str(category.pk),
            "dataAgreementRevision": None,
            "dataAgreementRevisionHash": "",
            "optIn": True,
            "state": record.state,
            "status": record.status,
            "isCurrent": record.is_current,
            "consentVersion": "",
        },
        authorized_by=citizen,
        authorized_by_other="",
    )


class ConsentRevisionRedactPiiTests(TestCase):
    """
    Direct model-level tests for ConsentRevision.redact_pii() — the narrow,
    dedicated exception to the append-only invariant added for RTBF/PIPEDA
    erasure compliance (see the method's docstring in models.py for the
    full design reasoning).
    """

    def setUp(self):
        self.citizen = _make_citizen()
        self.category = _make_category()
        self.revision = _make_consent_record_revision(self.citizen, self.category)

    def test_redact_pii_nulls_authorized_by_individual(self):
        self.assertEqual(self.revision.authorized_by_individual_id, self.citizen.pk)
        self.revision.redact_pii()
        self.assertIsNone(self.revision.authorized_by_individual_id)
        self.revision.refresh_from_db()
        self.assertIsNone(self.revision.authorized_by_individual_id)

    def test_redact_pii_redacts_object_data_individual_key(self):
        self.assertEqual(
            self.revision.serialized_snapshot["objectData"]["individual"], str(self.citizen.pk)
        )
        self.revision.redact_pii()
        self.assertEqual(
            self.revision.serialized_snapshot["objectData"]["individual"], "[REDACTED]"
        )
        self.revision.refresh_from_db()
        self.assertEqual(
            self.revision.serialized_snapshot["objectData"]["individual"], "[REDACTED]"
        )

    def test_redact_pii_redacts_envelope_authorized_by_individual_key(self):
        self.assertEqual(
            self.revision.serialized_snapshot["authorizedByIndividual"], str(self.citizen.pk)
        )
        self.revision.redact_pii()
        self.assertEqual(self.revision.serialized_snapshot["authorizedByIndividual"], "[REDACTED]")

    def test_redact_pii_leaves_non_pii_fields_untouched(self):
        """
        schema_name, object_id, serialized_hash, timestamp, predecessor_hash
        must be byte-for-byte identical before and after redact_pii() — only
        the two PII-bearing keys/field are touched.
        """
        before = {
            "schema_name": self.revision.schema_name,
            "object_id": self.revision.object_id,
            "serialized_hash": self.revision.serialized_hash,
            "timestamp": self.revision.timestamp,
            "predecessor_hash": self.revision.predecessor_hash,
        }
        self.revision.redact_pii()
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.schema_name, before["schema_name"])
        self.assertEqual(self.revision.object_id, before["object_id"])
        self.assertEqual(self.revision.serialized_hash, before["serialized_hash"])
        self.assertEqual(self.revision.timestamp, before["timestamp"])
        self.assertEqual(self.revision.predecessor_hash, before["predecessor_hash"])

    def test_redact_pii_on_revision_with_no_authorized_by_individual_is_safe(self):
        """
        A revision created with authorized_by=None (e.g. a system/admin
        DataAgreement revision) must not error when redact_pii() is called —
        there is nothing to null out, and the snapshot may not even carry an
        "individual" key (e.g. DataAgreement snapshots don't).
        """
        category2 = _make_category()
        revision = ConsentRevision.create_for(
            schema_name="DataAgreement",
            obj=category2,
            snapshot={"slug": category2.slug},
            authorized_by=None,
            authorized_by_other="system",
        )
        self.assertIsNone(revision.authorized_by_individual_id)
        revision.redact_pii()  # must not raise
        self.assertIsNone(revision.authorized_by_individual_id)
        self.assertIsNone(revision.serialized_snapshot.get("authorizedByIndividual"))

    def test_plain_save_on_existing_revision_still_raises_append_only_guard(self):
        """
        REGRESSION GUARD: redact_pii() must be the ONLY sanctioned way to
        bypass the append-only guard. A plain revision.save() call (not
        via redact_pii()) on an already-persisted ConsentRevision must still
        raise ValueError exactly as before — proving the general invariant
        was not accidentally weakened by adding redact_pii().
        """
        with self.assertRaises(ValueError):
            self.revision.save()

    def test_plain_save_with_disallowed_update_fields_still_raises(self):
        """
        Further regression guard: passing update_fields that include the
        redaction fields directly to the ordinary save() (bypassing
        redact_pii() entirely) must still be rejected — only redact_pii()'s
        internal super().save() call is allowed to write those fields.
        """
        self.revision.serialized_snapshot = {"objectData": {"individual": "tampered"}}
        with self.assertRaises(ValueError):
            self.revision.save(update_fields=["serialized_snapshot"])

    def test_delete_on_revision_still_raises_after_redact_pii_added(self):
        """Regression guard: delete() must remain fully blocked, unaffected by redact_pii()."""
        with self.assertRaises(ValueError):
            self.revision.delete()
        self.revision.redact_pii()
        with self.assertRaises(ValueError):
            self.revision.delete()

    def test_successor_only_update_still_allowed(self):
        """Regression guard: the pre-existing successor-only save() allowance is unaffected."""
        successor = ConsentRevision.objects.create(
            schema_name="ConsentRecord",
            object_id=self.revision.object_id,
            serialized_snapshot={"objectData": {}},
        )
        self.revision.successor = successor
        self.revision.save(update_fields=["successor"])  # must not raise
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.successor_id, successor.pk)
