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
import threading
import unittest
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection
from django.test import TestCase, TransactionTestCase

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


class HasConsentFixTests(TestCase):
    """H-05 fix: has_consent() now checks is_current=True and state=signed."""

    def setUp(self):
        self.citizen = User.objects.create_user(
            email=f"hc-{uuid.uuid4().hex[:8]}@example.gov",
            password="SecureTest123!",
        )
        self.category = ConsentCategory.objects.create(
            slug=f"hc-{uuid.uuid4().hex[:6]}",
            name_en="Test Cat", name_fr="Cat Test",
            purpose_en="p", purpose_fr="p",
            is_required=False, is_active=True,
        )

    def test_returns_true_when_granted(self):
        ConsentService.grant(citizen=self.citizen, category_slug=self.category.slug)
        self.assertTrue(ConsentService.has_consent(self.citizen, self.category.slug))

    def test_returns_false_after_withdrawal(self):
        """After withdrawal, stale historical granted row must not produce True."""
        ConsentService.grant(citizen=self.citizen, category_slug=self.category.slug)
        ConsentService.withdraw(citizen=self.citizen, category_slug=self.category.slug)
        self.assertFalse(ConsentService.has_consent(self.citizen, self.category.slug))

    def test_returns_false_for_unknown_category(self):
        self.assertFalse(ConsentService.has_consent(self.citizen, "does-not-exist"))

    def test_returns_true_for_required_category_without_record(self):
        req_cat = ConsentCategory.objects.create(
            slug=f"req-{uuid.uuid4().hex[:6]}",
            name_en="Req", name_fr="Req",
            purpose_en="p", purpose_fr="p",
            is_required=True, is_active=True,
        )
        self.assertTrue(ConsentService.has_consent(self.citizen, req_cat.slug))


class ConsentServiceGrantRevisionTests(TestCase):
    """H-02 fix: grant() now respects caller-supplied revision parameter."""

    def setUp(self):
        self.citizen = _make_citizen()
        # BUGFIX (item 5 hardening round): this test previously used the bare
        # _make_category() helper (a plain ConsentCategory.objects.create()),
        # which never creates a ConsentRevision for the category. grant()
        # only auto-attaches a revision via _get_latest_revision(category)
        # when one actually exists (services.py:100-101) — with none ever
        # created, record1.data_agreement_revision was ALWAYS None, so
        # test_grant_with_specific_revision_uses_that_revision's own
        # `if revision1 is None: self.skipTest(...)` guard fired on every
        # single run. This test had therefore never once executed the
        # "H-02 fix" behavior it exists to verify, on any backend — not a
        # SQLite-only limitation like ConcurrentFirstGrantTests below, just
        # a genuinely broken test fixture. Fixed by creating the category via
        # ConsentService.create_data_agreement(), the same service method
        # production code paths use, which creates an initial ConsentRevision
        # alongside the ConsentCategory (services.py:650-670).
        self.category, self.initial_revision = ConsentService.create_data_agreement({
            "slug": f"rev-{uuid.uuid4().hex[:6]}",
            "name_en": "Revision Test Category",
            "name_fr": "Catégorie de test de révision",
            "purpose_en": "Test purpose",
            "purpose_fr": "Objet de test",
            "lawful_basis": "consent",
            "is_required": False,
            "is_active": True,
        })

    def test_grant_with_specific_revision_uses_that_revision(self):
        """H-02 fix: when a revision is supplied to grant(), it is used not discarded."""
        # grant() auto-attaches the latest revision (self.initial_revision)
        # via _get_latest_revision() since none is explicitly supplied here.
        record1 = ConsentService.grant(citizen=self.citizen, category_slug=self.category.slug)
        revision1 = record1.data_agreement_revision
        self.assertIsNotNone(
            revision1,
            "ConsentService.create_data_agreement() must produce a category "
            "with a real ConsentRevision, and grant() must auto-attach it "
            "via _get_latest_revision() — this is the precondition the rest "
            "of this test relies on to actually exercise the H-02 fix.",
        )
        self.assertEqual(revision1.pk, self.initial_revision.pk)
        # Withdraw and re-grant supplying the specific revision object
        ConsentService.withdraw(citizen=self.citizen, category_slug=self.category.slug)
        record2 = ConsentService.grant(
            citizen=self.citizen,
            category_slug=self.category.slug,
            revision=revision1,
        )
        self.assertEqual(record2.data_agreement_revision, revision1)

    def test_grant_withdraw_regrant_withdraw_returns_false(self):
        ConsentService.grant(citizen=self.citizen, category_slug=self.category.slug)
        ConsentService.withdraw(citizen=self.citizen, category_slug=self.category.slug)
        ConsentService.grant(citizen=self.citizen, category_slug=self.category.slug)
        ConsentService.withdraw(citizen=self.citizen, category_slug=self.category.slug)
        self.assertFalse(ConsentService.has_consent(self.citizen, self.category.slug))


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


# ===========================================================================
# Fix 4 — concurrent first-grant race (TransactionTestCase)
# ===========================================================================

@unittest.skipIf(
    connection.vendor == "sqlite",
    "Two independent, permanent SQLite limitations — not a sandbox/CI "
    "artifact, and not fixable by config alone — make this test unable to "
    "prove what it claims on this backend: (1) Django's sqlite3 backend "
    "reports has_select_for_update=False (confirmed via "
    "connection.features.has_select_for_update), so ConsentService.grant()'s "
    "select_for_update() silently becomes a no-op — the row-level lock this "
    "test exists to exercise simply isn't taken; (2) this project's test "
    "settings use NAME=':memory:' (config/settings/test.py), and each thread "
    "opens its own separate SQLite connection — per SQLite's own semantics, "
    "each connection to ':memory:' gets an independent, unshared database, "
    "so the two threads below would not even see each other's writes, "
    "making 'concurrent race' meaningless here regardless of locking. "
    "Real concurrency + real row locking requires PostgreSQL. This is why "
    "GrantIntegrityErrorRecoveryTests exists below: it exercises the exact "
    "same `except IntegrityError` recovery branch in grant() deterministically "
    "via mocking (no threads, no Postgres, SQLite-safe), so the recovery "
    "LOGIC has real coverage on every backend even though the true "
    "concurrent-locking GUARANTEE can only be verified against Postgres.",
)
class ConcurrentFirstGrantTests(TransactionTestCase):
    """
    Two concurrent ConsentService.grant() calls for the SAME citizen+category
    where NEITHER has an existing record yet (the "very first grant" race
    described in the certifiability review): grant()'s select_for_update()
    only locks EXISTING signed/granted rows, so with nothing to lock, both
    concurrent requests could otherwise reach ConsentRecord.objects.create()
    with is_current=True.

    The DB-level unique_current_consent_record_per_citizen_category
    constraint (models.py) plus grant()'s IntegrityError recovery path
    (services.py) must together guarantee exactly one is_current=True row
    survives, and BOTH threads must return successfully (no unhandled
    exception propagates to the caller) regardless of thread ordering.

    TransactionTestCase is required (mirrors apps/volunteers/tests/
    test_invariants.py's ConcurrentOverbookingTests pattern) because
    select_for_update() needs real row-level locking semantics unavailable
    inside TestCase's wrapping savepoint.
    """

    def setUp(self):
        self.citizen = _make_citizen()
        self.category = _make_category(slug=f"race-{uuid.uuid4().hex[:6]}")

    def test_exactly_one_current_record_survives_concurrent_first_grant(self):
        results = []
        errors = []
        lock = threading.Lock()

        def _grant():
            try:
                record = ConsentService.grant(
                    citizen=self.citizen, category_slug=self.category.slug
                )
                with lock:
                    results.append(record)
            except Exception as exc:  # pragma: no cover - failure path under test
                with lock:
                    errors.append(exc)
            finally:
                connection.close()

        t1 = threading.Thread(target=_grant)
        t2 = threading.Thread(target=_grant)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Both concurrent calls must complete successfully — the IntegrityError
        # race must be fully recovered from inside grant(), never surfaced to
        # the caller.
        self.assertEqual(
            len(errors), 0,
            f"grant() must recover from the is_current race internally; got errors: {errors}",
        )
        self.assertEqual(len(results), 2)

        # Exactly one is_current=True row must exist for this citizen/category
        # — the DB constraint (backstopped by grant()'s recovery path) must
        # prevent true overwriting/duplication regardless of thread ordering.
        current_count = ConsentRecord.objects.filter(
            citizen=self.citizen, category=self.category, is_current=True
        ).count()
        self.assertEqual(
            current_count, 1,
            f"Expected exactly 1 is_current=True ConsentRecord; got {current_count}.",
        )

        # Both threads must have observed the SAME winning record (grant() is
        # idempotent — the loser re-fetches and returns the winner's row).
        self.assertEqual(results[0].pk, results[1].pk)


# ===========================================================================
# Fix 2 (this round) — grant()'s IntegrityError recovery, SQLite-compatible
#
# ConcurrentFirstGrantTests above is the only test that previously exercised
# grant()'s except-IntegrityError branch, and it is skipped whenever the DB
# backend is SQLite (this project's test backend — config/settings/test.py).
# That means "N tests passing" previously included ZERO executed tests of
# this recovery code path. These tests decouple "does the recovery LOGIC
# work" (provable by mocking .create(), no real concurrency/Postgres
# required) from "does Postgres actually enforce the constraint under true
# concurrency" (which legitimately does need Postgres — already covered,
# separately, by ConcurrentFirstGrantTests, which is left unchanged).
# ===========================================================================

class GrantIntegrityErrorRecoveryTests(TestCase):

    def setUp(self):
        self.citizen = _make_citizen()
        self.category = _make_category(slug="integrity-race-test")

    def test_recovers_by_refetching_winning_row_on_current_consent_race(self):
        """
        Simulates the real concurrent scenario without needing real threads
        or PostgreSQL.

        grant() runs its "archive the previous current record" step
        (services.py) inside its OUTER transaction.atomic() block, then
        attempts ConsentRecord.objects.create() inside a nested savepoint
        (transaction.atomic()). If that create() raises, only the nested
        savepoint is rolled back — anything written earlier in the outer
        transaction (i.e. before the create() attempt) survives.

        That is exactly the real race window: our own archive step finds
        nothing to archive (this is the citizen's very first grant), and
        — concurrently — another grant() call's insert lands right after
        it and commits, before our own create() attempt. To simulate this
        without threads, the "concurrent winner" is inserted as a side
        effect of the archive step's .update() call (which runs in the
        outer transaction, so it is NOT undone when our own create()
        raises and its nested savepoint rolls back) rather than inside
        create()'s own side effect (which WOULD be undone, since it runs
        inside the doomed nested savepoint — that was this test's original,
        broken approach).

        grant() must catch that specific IntegrityError, re-fetch the
        winning row via select_for_update(), and return it — not crash, and
        not fabricate/return a different record.
        """
        real_manager_filter = ConsentRecord.objects.filter
        real_create = ConsentRecord.objects.create
        winner_holder = {}

        def _filter_side_effect(*args, **kwargs):
            qs = real_manager_filter(*args, **kwargs)
            is_archive_call = (
                kwargs.get("is_current") is True
                and getattr(kwargs.get("citizen"), "pk", None) == self.citizen.pk
                and getattr(kwargs.get("category"), "pk", None) == self.category.pk
                and "state" not in kwargs
                and "status" not in kwargs
            )
            if is_archive_call:
                real_update = qs.update

                def _update_side_effect(**update_kwargs):
                    result = real_update(**update_kwargs)
                    # The concurrent transaction's insert lands here — after
                    # our own archive step but before our own create()
                    # attempt below — mirroring the real race window.
                    winner_holder["winner"] = real_create(
                        citizen=self.citizen,
                        category=self.category,
                        status=ConsentRecord.STATUS_GRANTED,
                        state=ConsentRecord.STATE_SIGNED,
                        is_current=True,
                    )
                    return result

                qs.update = _update_side_effect
            return qs

        def _create_side_effect(**kwargs):
            # Our own INSERT hits the constraint the concurrent winner
            # (injected above, in the archive step) just committed under.
            raise IntegrityError(
                'duplicate key value violates unique constraint '
                '"unique_current_consent_record_per_citizen_category"'
            )

        with patch(
            "apps.consent.models.ConsentRecord.objects.filter",
            side_effect=_filter_side_effect,
        ), patch(
            "apps.consent.models.ConsentRecord.objects.create",
            side_effect=_create_side_effect,
        ):
            record = ConsentService.grant(self.citizen, "integrity-race-test")

        self.assertIn("winner", winner_holder)
        self.assertEqual(record.pk, winner_holder["winner"].pk)
        self.assertIsInstance(record, ConsentRecord)
        self.assertTrue(record.is_current)
        self.assertEqual(record.status, ConsentRecord.STATUS_GRANTED)
        self.assertEqual(record.state, ConsentRecord.STATE_SIGNED)
        # Recovery must not fabricate a duplicate — exactly one current row.
        self.assertEqual(
            ConsentRecord.objects.filter(
                citizen=self.citizen, category=self.category, is_current=True
            ).count(),
            1,
        )

    def test_unrelated_integrity_error_is_not_swallowed(self):
        """
        An IntegrityError NOT caused by the current-consent-race constraint
        (e.g. a different constraint violation entirely) must propagate to
        the caller rather than being silently treated as "lost the race".
        """
        def _create_side_effect(**kwargs):
            raise IntegrityError(
                'duplicate key value violates unique constraint '
                '"some_unrelated_constraint_name"'
            )

        with patch(
            "apps.consent.models.ConsentRecord.objects.create",
            side_effect=_create_side_effect,
        ):
            with self.assertRaises(IntegrityError):
                ConsentService.grant(self.citizen, "integrity-race-test")
