"""
Wave 4 — Volunteer Management BB: screening service test suite.

Covers:
  record_check()        — happy path, permission check, invalid type,
                          defaults, opportunity scope, unique constraint
  complete_check()      — verified clear/not-clear, verified_by, verified_at,
                          permission, VSC note restrictions, update_fields
  check_expiring_soon() — window filter, verified_clear exclusions,
                          already-expired exclusion, ordering
  ScreeningRecord props — is_expired, expires_within_30_days (time-patched)

Conventions:
  - patch("django.utils.timezone.now") to freeze "today" for property tests.
  - No time.sleep() — all temporal assertions use mocked now().
  - PIPEDA: volunteers referenced by profile PK only.
"""
from __future__ import annotations

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.volunteers.models import (
    Opportunity,
    Program,
    ScreeningRecord,
    VolunteerProfile,
)
from apps.volunteers.services.screening import (
    check_expiring_soon,
    complete_check,
    record_check,
)

User = get_user_model()

# ---------------------------------------------------------------------------
# Shared factory helpers  (verbatim pattern from test_services_hours.py)
# ---------------------------------------------------------------------------

_counter = [0]


def _uid():
    _counter[0] += 1
    return _counter[0]


def _make_user(email=None, password="testpass!", **kwargs):
    _counter[0] += 1
    email = email or f"s{_counter[0]}@example.gc.ca"
    return User.objects.create_user(email=email, password=password, **kwargs)


def _make_program(**kwargs):
    n = _uid()
    defaults = dict(
        name_en=f"Program {n}",
        name_fr=f"Programme {n}",
        slug=f"sprog-{n}",
        cra_category="welfare",
    )
    defaults.update(kwargs)
    return Program.objects.create(**defaults)


def _make_opportunity(program, *, slug=None, status="published", **kwargs):
    n = _uid()
    defaults = dict(
        title_en=f"Opportunity {n}",
        title_fr=f"Opportunité {n}",
        slug=slug or f"sopp-{n}",
        description_en="Description",
        description_fr="Description FR",
        program=program,
        status=status,
    )
    defaults.update(kwargs)
    return Opportunity.objects.create(**defaults)


def _make_profile(user):
    return VolunteerProfile.objects.create(user=user)


def _grant_perm(user, codename):
    perm = Permission.objects.get(
        content_type__app_label="volunteers",
        codename=codename,
    )
    user.user_permissions.add(perm)
    for attr in ("_perm_cache", "_user_perm_cache"):
        if hasattr(user, attr):
            delattr(user, attr)
    return User.objects.get(pk=user.pk)


# ---------------------------------------------------------------------------
# Shared base setup
# ---------------------------------------------------------------------------

class ScreeningBaseTestCase(TestCase):
    """
    Shared fixtures for screening service tests.

    Attributes:
        coordinator       — User with volunteers.add_screeningrecord
                            and volunteers.change_screeningrecord
        volunteer_user    — regular volunteer User
        volunteer_profile — VolunteerProfile for volunteer_user
        program, opportunity
    """

    def setUp(self):
        coord = _make_user("scoord@example.gc.ca", is_staff=True)
        coord = _grant_perm(coord, "add_screeningrecord")
        self.coordinator = _grant_perm(coord, "change_screeningrecord")

        self.volunteer_user = _make_user("svol@example.gc.ca")
        self.volunteer_profile = _make_profile(self.volunteer_user)

        self.program = _make_program(slug="screen-base-prog")
        self.opportunity = _make_opportunity(self.program, slug="screen-base-opp")


# ===========================================================================
# record_check() tests
# ===========================================================================

class RecordCheckTests(ScreeningBaseTestCase):

    def test_creates_screening_record_with_defaults(self):
        """Happy path: record_check() creates a ScreeningRecord with verified_clear=None."""
        record = record_check(
            volunteer_profile=self.volunteer_profile,
            check_type=ScreeningRecord.CHECK_TYPE_VSC,
            requested_by=self.coordinator,
        )
        self.assertIsNotNone(record.pk)
        self.assertIsNone(record.verified_clear)
        self.assertEqual(record.check_type, ScreeningRecord.CHECK_TYPE_VSC)
        self.assertEqual(record.volunteer, self.volunteer_profile)

    def test_completed_date_defaults_to_today(self):
        """completed_date defaults to today when not explicitly supplied."""
        today = timezone.localtime(timezone.now()).date()
        record = record_check(
            volunteer_profile=self.volunteer_profile,
            check_type=ScreeningRecord.CHECK_TYPE_PRC,
            requested_by=self.coordinator,
        )
        self.assertEqual(record.completed_date, today)

    def test_explicit_completed_date_and_expiry_date(self):
        """Explicit completed_date and expiry_date are persisted correctly."""
        completed = datetime.date(2026, 1, 15)
        expiry = datetime.date(2029, 1, 15)
        record = record_check(
            volunteer_profile=self.volunteer_profile,
            check_type=ScreeningRecord.CHECK_TYPE_VSC,
            requested_by=self.coordinator,
            completed_date=completed,
            expiry_date=expiry,
        )
        self.assertEqual(record.completed_date, completed)
        self.assertEqual(record.expires_date, expiry)

    def test_invalid_check_type_raises_value_error(self):
        """record_check() raises ValueError for unknown check_type strings."""
        with self.assertRaises(ValueError):
            record_check(
                volunteer_profile=self.volunteer_profile,
                check_type="space_background_check",
                requested_by=self.coordinator,
            )

    def test_raises_permission_denied_without_add_perm(self):
        """record_check() raises PermissionDenied for users lacking add_screeningrecord."""
        with self.assertRaises(PermissionDenied):
            record_check(
                volunteer_profile=self.volunteer_profile,
                check_type=ScreeningRecord.CHECK_TYPE_VSC,
                requested_by=self.volunteer_user,
            )

    def test_creates_record_with_pending_verified_clear(self):
        """A newly created record has verified_clear=None (pending outcome)."""
        record = record_check(
            volunteer_profile=self.volunteer_profile,
            check_type=ScreeningRecord.CHECK_TYPE_REFERENCE,
            requested_by=self.coordinator,
        )
        record.refresh_from_db()
        self.assertIsNone(record.verified_clear)

    def test_with_opportunity_scope(self):
        """opportunity param is stored on the ScreeningRecord FK."""
        record = record_check(
            volunteer_profile=self.volunteer_profile,
            check_type=ScreeningRecord.CHECK_TYPE_VSC,
            requested_by=self.coordinator,
            opportunity=self.opportunity,
        )
        self.assertEqual(record.opportunity, self.opportunity)

    def test_duplicate_raises_validation_error(self):
        """
        Creating a second ScreeningRecord for the same volunteer+check_type
        (with the same opportunity scope) violates the UniqueConstraint.
        """
        record_check(
            volunteer_profile=self.volunteer_profile,
            check_type=ScreeningRecord.CHECK_TYPE_PRC,
            requested_by=self.coordinator,
        )
        with self.assertRaises(ValidationError):
            record_check(
                volunteer_profile=self.volunteer_profile,
                check_type=ScreeningRecord.CHECK_TYPE_PRC,
                requested_by=self.coordinator,
            )

    def test_record_check_returns_screening_record(self):
        """M-4: record_check() returns a saved ScreeningRecord with verified_clear=None."""
        record = record_check(
            volunteer_profile=self.volunteer_profile,
            check_type=ScreeningRecord.CHECK_TYPE_PRC,
            requested_by=self.coordinator,
        )
        self.assertIsInstance(record, ScreeningRecord)
        self.assertIsNone(record.verified_clear)  # pending by default


# ===========================================================================
# complete_check() tests
# ===========================================================================

class CompleteCheckTests(ScreeningBaseTestCase):

    def _pending_record(self, check_type=None, opportunity=None):
        """Create a pending ScreeningRecord for use in complete_check() tests."""
        return record_check(
            volunteer_profile=self.volunteer_profile,
            check_type=check_type or ScreeningRecord.CHECK_TYPE_PRC,
            requested_by=self.coordinator,
            opportunity=opportunity,
        )

    def test_marks_as_verified_clear_true(self):
        """complete_check() with verified_clear=True sets the flag correctly."""
        record = self._pending_record()
        result = complete_check(
            screening_record=record,
            verified_clear=True,
            completed_by=self.coordinator,
        )
        self.assertTrue(result.verified_clear)

    def test_marks_as_verified_clear_false(self):
        """complete_check() with verified_clear=False records a non-clear result."""
        record = self._pending_record()
        result = complete_check(
            screening_record=record,
            verified_clear=False,
            completed_by=self.coordinator,
        )
        self.assertFalse(result.verified_clear)

    def test_sets_verified_by_and_verified_at(self):
        """complete_check() stores the coordinator as verified_by and sets verified_at."""
        record = self._pending_record()
        before = timezone.now()
        result = complete_check(
            screening_record=record,
            verified_clear=True,
            completed_by=self.coordinator,
        )
        after = timezone.now()
        self.assertEqual(result.verified_by, self.coordinator)
        self.assertIsNotNone(result.verified_at)
        self.assertGreaterEqual(result.verified_at, before)
        self.assertLessEqual(result.verified_at, after)

    def test_raises_permission_denied_without_change_perm(self):
        """complete_check() raises PermissionDenied without change_screeningrecord."""
        record = self._pending_record()
        with self.assertRaises(PermissionDenied):
            complete_check(
                screening_record=record,
                verified_clear=True,
                completed_by=self.volunteer_user,
            )

    def test_vsc_note_prohibited_keyword_raises_validation_error(self):
        """VSC notes containing 'conviction' (criminal-record detail) raise ValidationError."""
        record = self._pending_record(check_type=ScreeningRecord.CHECK_TYPE_VSC)
        with self.assertRaises(ValidationError) as ctx:
            complete_check(
                screening_record=record,
                verified_clear=True,
                completed_by=self.coordinator,
                notes="Volunteer has a prior conviction",
            )
        self.assertIn("notes", ctx.exception.message_dict)

    def test_vsc_note_too_long_raises_validation_error(self):
        """VSC notes longer than 150 chars raise ValidationError when verified_clear is set."""
        record = self._pending_record(check_type=ScreeningRecord.CHECK_TYPE_VSC)
        long_note = "Submitted to RCMP detachment. " * 6  # >150 chars
        with self.assertRaises(ValidationError) as ctx:
            complete_check(
                screening_record=record,
                verified_clear=True,
                completed_by=self.coordinator,
                notes=long_note,
            )
        self.assertIn("notes", ctx.exception.message_dict)

    def test_non_vsc_notes_not_restricted(self):
        """Police record check allows notes longer than 150 chars (VSC restriction is VSC-only)."""
        record = self._pending_record(check_type=ScreeningRecord.CHECK_TYPE_PRC)
        long_note = "Standard police record check completed without incident. " * 3  # >150 chars
        result = complete_check(
            screening_record=record,
            verified_clear=True,
            completed_by=self.coordinator,
            notes=long_note,
        )
        self.assertEqual(result.verified_clear, True)
        self.assertEqual(result.notes, long_note)

    def test_update_fields_preserved(self):
        """
        complete_check() uses update_fields so only verified_clear, verified_at,
        verified_by, notes, and updated_at are written to the DB.
        Verify by checking the saved record reflects the completion state.
        """
        record = self._pending_record()
        original_completed_date = record.completed_date

        result = complete_check(
            screening_record=record,
            verified_clear=True,
            completed_by=self.coordinator,
            notes="Submitted 2026-01-01",
        )
        result.refresh_from_db()
        # Fields set by complete_check() are persisted.
        self.assertTrue(result.verified_clear)
        self.assertIsNotNone(result.verified_at)
        self.assertEqual(result.notes, "Submitted 2026-01-01")
        # Fields not touched by complete_check() remain unchanged.
        self.assertEqual(result.completed_date, original_completed_date)

    def test_vsc_note_without_prohibited_keyword_succeeds(self):
        """M-6: VSC completion with an allowed note must not raise ValidationError."""
        record = self._pending_record(check_type=ScreeningRecord.CHECK_TYPE_VSC)
        # This must NOT raise
        result = complete_check(
            screening_record=record,
            verified_clear=True,
            completed_by=self.coordinator,
            notes="Volunteer cleared with no issues noted.",
        )
        result.refresh_from_db()
        self.assertTrue(result.verified_clear)


# ===========================================================================
# check_expiring_soon() tests
# ===========================================================================

class CheckExpiringSoonTests(ScreeningBaseTestCase):
    """
    C-2 fix: patch django.utils.timezone.now to freeze "today" so tests
    do not diverge at UTC midnight when TIME_ZONE is not UTC.
    """

    # Frozen instant: 2026-06-15 10:00 UTC.  localtime() in any timezone
    # within UTC-9..UTC+14 resolves to 2026-06-15, matching _today.
    _frozen_now = datetime.datetime(2026, 6, 15, 10, 0, 0,
                                    tzinfo=datetime.timezone.utc)
    _today = datetime.date(2026, 6, 15)

    def setUp(self):
        super().setUp()
        self._patcher = patch("django.utils.timezone.now",
                              return_value=self._frozen_now)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        super().tearDown()

    def _cleared_record(self, expires_date=None, check_type=None, opportunity=None):
        """Create a verified-clear ScreeningRecord with an optional expiry date."""
        record = ScreeningRecord.objects.create(
            volunteer=self.volunteer_profile,
            check_type=check_type or ScreeningRecord.CHECK_TYPE_PRC,
            completed_date=self._today,
            expires_date=expires_date,
            verified_clear=True,
            verified_by=self.coordinator,
            verified_at=self._frozen_now,
            opportunity=opportunity,
        )
        return record

    def test_returns_records_within_window(self):
        """Records expiring within the default 30-day window are returned."""
        expiring_soon = self._cleared_record(
            expires_date=self._today + datetime.timedelta(days=15)
        )
        qs = check_expiring_soon(days_ahead=30)
        pks = list(qs.values_list("pk", flat=True))
        self.assertIn(expiring_soon.pk, pks)

    def test_excludes_not_verified_clear(self):
        """Records with verified_clear=None (pending) are excluded."""
        ScreeningRecord.objects.create(
            volunteer=self.volunteer_profile,
            check_type=ScreeningRecord.CHECK_TYPE_REFERENCE,
            completed_date=self._today,
            expires_date=self._today + datetime.timedelta(days=10),
            verified_clear=None,  # pending
            verified_by=self.coordinator,
            verified_at=self._frozen_now,
        )
        qs = check_expiring_soon(days_ahead=30)
        self.assertFalse(qs.exists())

    def test_excludes_false_verified_clear(self):
        """Records with verified_clear=False (not clear) are excluded."""
        vol2_user = _make_user()
        vol2 = _make_profile(vol2_user)
        ScreeningRecord.objects.create(
            volunteer=vol2,
            check_type=ScreeningRecord.CHECK_TYPE_PRC,
            completed_date=self._today,
            expires_date=self._today + datetime.timedelta(days=10),
            verified_clear=False,  # not clear
            verified_by=self.coordinator,
            verified_at=self._frozen_now,
        )
        qs = check_expiring_soon(days_ahead=30)
        self.assertFalse(qs.exists())

    def test_excludes_already_expired(self):
        """Records with expires_date before today are excluded."""
        ScreeningRecord.objects.create(
            volunteer=self.volunteer_profile,
            check_type=ScreeningRecord.CHECK_TYPE_PRC,
            completed_date=self._today - datetime.timedelta(days=365),
            expires_date=self._today - datetime.timedelta(days=1),  # yesterday
            verified_clear=True,
            verified_by=self.coordinator,
            verified_at=self._frozen_now,
        )
        qs = check_expiring_soon(days_ahead=30)
        self.assertFalse(qs.exists())

    def test_days_ahead_parameter(self):
        """Custom days_ahead=14 only returns records expiring in the next 14 days."""
        # Expiring in 10 days — within 14-day window.
        near = self._cleared_record(
            expires_date=self._today + datetime.timedelta(days=10),
            check_type=ScreeningRecord.CHECK_TYPE_PRC,
        )
        # Expiring in 20 days — outside 14-day window.
        vol2_user = _make_user()
        vol2 = _make_profile(vol2_user)
        far = ScreeningRecord.objects.create(
            volunteer=vol2,
            check_type=ScreeningRecord.CHECK_TYPE_PRC,
            completed_date=self._today,
            expires_date=self._today + datetime.timedelta(days=20),
            verified_clear=True,
            verified_by=self.coordinator,
            verified_at=self._frozen_now,
        )
        qs = check_expiring_soon(days_ahead=14)
        pks = list(qs.values_list("pk", flat=True))
        self.assertIn(near.pk, pks)
        self.assertNotIn(far.pk, pks)

    def test_ordering_by_expires_date_asc(self):
        """Results are ordered by expires_date ascending (soonest expiry first)."""
        vol2_user = _make_user()
        vol2 = _make_profile(vol2_user)
        late = ScreeningRecord.objects.create(
            volunteer=vol2,
            check_type=ScreeningRecord.CHECK_TYPE_PRC,
            completed_date=self._today,
            expires_date=self._today + datetime.timedelta(days=25),
            verified_clear=True,
            verified_by=self.coordinator,
            verified_at=self._frozen_now,
        )
        early = self._cleared_record(
            expires_date=self._today + datetime.timedelta(days=5),
            check_type=ScreeningRecord.CHECK_TYPE_REFERENCE,
        )
        qs = check_expiring_soon(days_ahead=30)
        pks = list(qs.values_list("pk", flat=True))
        self.assertLess(pks.index(early.pk), pks.index(late.pk))


# ===========================================================================
# ScreeningRecord model property tests
# ===========================================================================

class ScreeningPropertyTests(TestCase):
    """
    Unit tests for ScreeningRecord.is_expired and .expires_within_30_days.

    Temporal behaviour is tested by patching django.utils.timezone.now so that
    "today" is fixed — no time.sleep() used.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="prop@example.gc.ca", password="testpass!"
        )
        self.profile = VolunteerProfile.objects.create(user=self.user)

    def _make_record(self, expires_date=None):
        """Create a minimal ScreeningRecord with the given expiry date."""
        return ScreeningRecord(
            volunteer=self.profile,
            check_type=ScreeningRecord.CHECK_TYPE_PRC,
            completed_date=datetime.date.today(),
            expires_date=expires_date,
            verified_clear=True,
        )

    def _fixed_now(self, date_str: str):
        """Return a timezone-aware datetime for the given YYYY-MM-DD string."""
        d = datetime.date.fromisoformat(date_str)
        return datetime.datetime(d.year, d.month, d.day, 12, 0, 0,
                                 tzinfo=datetime.timezone.utc)

    # --- is_expired ---

    def test_is_expired_false_when_no_expiry(self):
        """is_expired is False when expires_date is None."""
        record = self._make_record(expires_date=None)
        self.assertFalse(record.is_expired)

    def test_is_expired_false_when_future(self):
        """is_expired is False when expires_date is in the future."""
        fixed_now = self._fixed_now("2026-06-01")
        with patch("django.utils.timezone.now", return_value=fixed_now):
            record = self._make_record(expires_date=datetime.date(2026, 12, 31))
            self.assertFalse(record.is_expired)

    def test_is_expired_true_when_past(self):
        """is_expired is True when expires_date is in the past."""
        fixed_now = self._fixed_now("2026-06-15")
        with patch("django.utils.timezone.now", return_value=fixed_now):
            record = self._make_record(expires_date=datetime.date(2026, 6, 1))
            self.assertTrue(record.is_expired)

    def test_is_expired_false_on_exact_date(self):
        """is_expired is False when expires_date equals today (expires AT end of day, not expired)."""
        fixed_now = self._fixed_now("2026-06-15")
        with patch("django.utils.timezone.now", return_value=fixed_now):
            record = self._make_record(expires_date=datetime.date(2026, 6, 15))
            self.assertFalse(record.is_expired)

    # --- expires_within_30_days ---

    def test_expires_within_30_days_false_when_no_expiry(self):
        """expires_within_30_days is False when expires_date is None."""
        record = self._make_record(expires_date=None)
        self.assertFalse(record.expires_within_30_days)

    def test_expires_within_30_days_false_when_31_days(self):
        """expires_within_30_days is False when expiry is 31 days away."""
        fixed_now = self._fixed_now("2026-06-01")
        with patch("django.utils.timezone.now", return_value=fixed_now):
            record = self._make_record(expires_date=datetime.date(2026, 7, 2))
            self.assertFalse(record.expires_within_30_days)

    def test_expires_within_30_days_true_when_30_days(self):
        """expires_within_30_days is True when expiry is exactly 30 days away."""
        fixed_now = self._fixed_now("2026-06-01")
        with patch("django.utils.timezone.now", return_value=fixed_now):
            record = self._make_record(expires_date=datetime.date(2026, 7, 1))
            self.assertTrue(record.expires_within_30_days)

    def test_expires_within_30_days_true_when_1_day(self):
        """expires_within_30_days is True when expiry is tomorrow."""
        fixed_now = self._fixed_now("2026-06-01")
        with patch("django.utils.timezone.now", return_value=fixed_now):
            record = self._make_record(expires_date=datetime.date(2026, 6, 2))
            self.assertTrue(record.expires_within_30_days)

    def test_expires_within_30_days_true_when_0_days(self):
        """expires_within_30_days is True when expiry date is today."""
        fixed_now = self._fixed_now("2026-06-01")
        with patch("django.utils.timezone.now", return_value=fixed_now):
            record = self._make_record(expires_date=datetime.date(2026, 6, 1))
            self.assertTrue(record.expires_within_30_days)

    def test_expires_within_30_days_false_when_expired(self):
        """expires_within_30_days is False when expiry date is in the past."""
        fixed_now = self._fixed_now("2026-06-15")
        with patch("django.utils.timezone.now", return_value=fixed_now):
            record = self._make_record(expires_date=datetime.date(2026, 6, 1))
            # Past date: delta is negative, 0 <= delta.days <= 30 is False
            self.assertFalse(record.expires_within_30_days)
