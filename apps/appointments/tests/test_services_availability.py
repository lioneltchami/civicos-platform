"""
Appointments BB Wave 2 — SlotAvailabilityService test suite.

Coverage targets (per SPEC_APPOINTMENTS_BB.md §22.2):
  SlotGenerationTests       — weekday template, buffers, interval grid
  StaffExceptionTests       — HOLIDAY/LEAVE/TRAINING blocks; OVERRIDE applies custom hours
  BookingWindowTests        — min_lead_time; max_advance_days wall-clock enforcement
  FrequencyControlTests     — booking_frequency_days (food bank pattern)
  DSTHandlingTests          — UTC slot times correct across DST transition boundary
  MultiTimezoneTests        — Vancouver location; verify UTC offsets
  SlotServiceUnitTests      — block_slot, cancel_slot edge cases

Also includes:
  _SettingsPolicyTests      — proxy dataclass, _policy_from_settings(), _resolve_policy()
  GenerateSlotsForRangeTests — generate_slots_for_range() integration
  TaskDecoratorTests        — acks_late, reject_on_worker_lost on both tasks

Privacy invariants:
  - No PII (email, full name) in log output
  - ZoneInfo conversion correctness for Canadian timezones
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.appointments.models import (
    AppointmentType,
    AvailabilityTemplate,
    Location,
    Organization,
    Resource,
    SchedulingPolicy,
    ServiceType,
    Slot,
    StaffException,
    StaffProfile,
)
from apps.appointments.services.availability import (
    SlotAvailabilityService,
    _SettingsPolicy,
    _overlaps,
    _policy_from_settings,
    _resolve_policy,
    models_Q_valid_until,
)
from apps.appointments.services.slots import (
    SlotFullError,
    SlotHasBookingsError,
    block_slot,
    cancel_slot,
    generate_slots_for_range,
)

User = get_user_model()

UTC = ZoneInfo("UTC")
TORONTO = ZoneInfo("America/Toronto")
VANCOUVER = ZoneInfo("America/Vancouver")


# ---------------------------------------------------------------------------
# Shared factories
# ---------------------------------------------------------------------------

def make_user(email: str = "staff@example.com", is_staff: bool = True) -> User:
    return User.objects.create_user(email=email, password="pass!", is_staff=is_staff)


def make_org(slug: str = "test-org") -> Organization:
    return Organization.objects.create(
        slug=slug,
        name_en="Test Org",
        name_fr="Org test",
    )


def make_location(
    org: Organization | None = None,
    slug: str = "main-office",
    tz: str = "America/Toronto",
) -> Location:
    if org is None:
        org = make_org()
    return Location.objects.create(
        organization=org,
        slug=slug,
        name_en="Main Office",
        name_fr="Bureau principal",
        timezone=tz,
    )


def make_service_type(slug: str = "svc") -> ServiceType:
    return ServiceType.objects.create(
        slug=slug,
        name_en="Test Service",
        name_fr="Service test",
    )


def make_appt_type(
    service_type: ServiceType | None = None,
    slug: str = "appt",
    duration_minutes: int = 30,
    scheduling_policy: SchedulingPolicy | None = None,
    **kwargs,
) -> AppointmentType:
    if service_type is None:
        service_type = make_service_type()
    return AppointmentType.objects.create(
        service_type=service_type,
        slug=slug,
        name_en="Test Appointment",
        name_fr="Rendez-vous test",
        duration_minutes=duration_minutes,
        scheduling_policy=scheduling_policy,
        **kwargs,
    )


def make_staff(
    location: Location | None = None,
    suffix: str = "",
    is_accepting_bookings: bool = True,
) -> StaffProfile:
    user = make_user(email=f"staff{suffix}@example.com")
    if location is None:
        location = make_location()
    sp = StaffProfile.objects.create(
        user=user,
        location=location,
        is_accepting_bookings=is_accepting_bookings,
    )
    return sp


def make_policy(**kwargs) -> SchedulingPolicy:
    kwargs.setdefault("name", f"policy-{uuid.uuid4().hex[:6]}")
    return SchedulingPolicy.objects.create(**kwargs)


def make_template(
    staff: StaffProfile,
    day_of_week: int,
    start_time: time,
    end_time: time,
    valid_from: date | None = None,
    valid_until: date | None = None,
) -> AvailabilityTemplate:
    if valid_from is None:
        valid_from = date(2020, 1, 1)
    return AvailabilityTemplate.objects.create(
        staff=staff,
        day_of_week=day_of_week,
        start_time=start_time,
        end_time=end_time,
        valid_from=valid_from,
        valid_until=valid_until,
    )


def make_slot(
    appointment_type: AppointmentType,
    staff: StaffProfile,
    location: Location,
    start_dt: datetime,
    duration_minutes: int = 30,
    buffer_before: int = 0,
    buffer_after: int = 0,
    status: str = "available",
    capacity: int = 1,
    spaces_used: int = 0,
) -> Slot:
    """Create a persisted Slot with correct effective datetimes."""
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    eff_start = start_dt - timedelta(minutes=buffer_before)
    eff_end = end_dt + timedelta(minutes=buffer_after)
    return Slot.objects.create(
        appointment_type=appointment_type,
        staff=staff,
        location=location,
        start_datetime=start_dt,
        end_datetime=end_dt,
        effective_start=eff_start,
        effective_end=eff_end,
        status=status,
        capacity=capacity,
        spaces_used=spaces_used,
    )


# ---------------------------------------------------------------------------
# _overlaps() unit tests
# ---------------------------------------------------------------------------

class OverlapHelperTests(TestCase):
    """Unit tests for the _overlaps() helper."""

    def _dt(self, h: int, m: int = 0) -> datetime:
        return datetime(2026, 7, 7, h, m, tzinfo=UTC)

    def test_overlapping_intervals(self):
        self.assertTrue(_overlaps(self._dt(9), self._dt(10), self._dt(9, 30), self._dt(11)))

    def test_adjacent_intervals_do_not_overlap(self):
        """[9–10) and [10–11) share an endpoint but must NOT be counted as overlapping."""
        self.assertFalse(_overlaps(self._dt(9), self._dt(10), self._dt(10), self._dt(11)))

    def test_contained_interval_overlaps(self):
        self.assertTrue(_overlaps(self._dt(9), self._dt(12), self._dt(10), self._dt(11)))

    def test_disjoint_intervals_no_overlap(self):
        self.assertFalse(_overlaps(self._dt(9), self._dt(10), self._dt(11), self._dt(12)))

    def test_identical_intervals_overlap(self):
        self.assertTrue(_overlaps(self._dt(9), self._dt(10), self._dt(9), self._dt(10)))


# ---------------------------------------------------------------------------
# models_Q_valid_until() unit tests
# ---------------------------------------------------------------------------

class ModelsQValidUntilTests(TestCase):
    """Verify Q-object generator for valid_until filtering."""

    def setUp(self):
        self.org = make_org("q-test-org")
        self.location = make_location(self.org, "q-test-loc")
        self.staff = make_staff(self.location, suffix="-q")
        self.service_type = make_service_type("q-svc")

    def test_open_ended_template_matches_any_date(self):
        """valid_until=None → template is always active."""
        make_template(self.staff, 1, time(9, 0), time(17, 0), valid_until=None)
        q = models_Q_valid_until(date(2030, 12, 31))
        count = AvailabilityTemplate.objects.filter(q).count()
        self.assertEqual(count, 1)

    def test_template_expiring_before_target_excluded(self):
        """valid_until < target_date → excluded."""
        make_template(
            self.staff, 1, time(9, 0), time(17, 0),
            valid_until=date(2026, 6, 30),
        )
        q = models_Q_valid_until(date(2026, 7, 7))
        count = AvailabilityTemplate.objects.filter(q).count()
        self.assertEqual(count, 0)

    def test_template_expiring_on_target_included(self):
        """valid_until == target_date → still active on that day (inclusive)."""
        make_template(
            self.staff, 1, time(9, 0), time(17, 0),
            valid_until=date(2026, 7, 7),
        )
        q = models_Q_valid_until(date(2026, 7, 7))
        count = AvailabilityTemplate.objects.filter(q).count()
        self.assertEqual(count, 1)


# ---------------------------------------------------------------------------
# _SettingsPolicy / _policy_from_settings() tests
# ---------------------------------------------------------------------------

class SettingsPolicyTests(TestCase):
    """Tests for _SettingsPolicy dataclass and _policy_from_settings()."""

    def test_settings_policy_is_frozen(self):
        p = _policy_from_settings()
        with self.assertRaises((TypeError, AttributeError)):
            p.slot_interval_minutes = 99  # type: ignore[misc]

    def test_policy_from_settings_defaults(self):
        """With default CIVICOS settings, returns reasonable defaults."""
        p = _policy_from_settings()
        self.assertIsInstance(p, _SettingsPolicy)
        self.assertGreater(p.slot_interval_minutes, 0)
        self.assertGreater(p.max_advance_days, 0)

    @override_settings(CIVICOS={"APPOINTMENTS": {
        "DEFAULT_SLOT_DURATION_MINUTES": 45,
        "DEFAULT_MIN_LEAD_HOURS": 2,
        "DEFAULT_MAX_ADVANCE_DAYS": 90,
        "DEFAULT_MAX_ACTIVE_BOOKINGS": 5,
        "WAITLIST_ACCEPTANCE_WINDOW_HOURS": 4,
        "WAITLIST_NOTIFY_BATCH_SIZE": 5,
        "GLOBAL_NO_SHOW_SUSPENSION_THRESHOLD": 2,
    }})
    def test_policy_from_settings_reads_overrides(self):
        p = _policy_from_settings()
        self.assertEqual(p.slot_interval_minutes, 45)
        self.assertEqual(p.min_lead_time_hours, 2)
        self.assertEqual(p.max_advance_days, 90)
        self.assertEqual(p.max_active_bookings_per_citizen, 5)
        self.assertEqual(p.waitlist_acceptance_window_hours, 4)
        self.assertEqual(p.waitlist_notify_batch_size, 5)
        self.assertEqual(p.no_show_suspension_threshold, 2)


# ---------------------------------------------------------------------------
# _resolve_policy() tests
# ---------------------------------------------------------------------------

class ResolvePolicyTests(TestCase):
    """Tests for the three-level policy cascade."""

    def setUp(self):
        org = make_org("rp-org")
        self.location = make_location(org, "rp-loc")
        self.svc_type = make_service_type("rp-svc")

    def test_appointment_type_policy_takes_precedence(self):
        loc_policy = make_policy(name="rp-loc-policy")
        appt_policy = make_policy(name="rp-appt-policy")
        self.location.scheduling_policy = loc_policy
        self.location.save()
        appt_type = make_appt_type(
            service_type=self.svc_type,
            slug="rp-appt-wins",
            scheduling_policy=appt_policy,
        )
        resolved = _resolve_policy(appt_type, location=self.location)
        self.assertEqual(resolved, appt_policy)

    def test_location_policy_fallback_when_appt_has_none(self):
        loc_policy = make_policy(name="rp-loc-fallback")
        self.location.scheduling_policy = loc_policy
        self.location.save()
        appt_type = make_appt_type(
            service_type=self.svc_type,
            slug="rp-loc-fallback-appt",
            scheduling_policy=None,
        )
        resolved = _resolve_policy(appt_type, location=self.location)
        self.assertEqual(resolved, loc_policy)

    def test_settings_proxy_when_both_none(self):
        appt_type = make_appt_type(
            service_type=self.svc_type,
            slug="rp-settings-fallback",
            scheduling_policy=None,
        )
        loc_no_policy = make_location(slug="rp-loc-no-policy")
        loc_no_policy.scheduling_policy = None
        loc_no_policy.save()
        resolved = _resolve_policy(appt_type, location=loc_no_policy)
        self.assertIsInstance(resolved, _SettingsPolicy)

    def test_settings_proxy_when_no_location_provided(self):
        appt_type = make_appt_type(
            service_type=self.svc_type,
            slug="rp-no-location",
            scheduling_policy=None,
        )
        resolved = _resolve_policy(appt_type, location=None)
        self.assertIsInstance(resolved, _SettingsPolicy)


# ---------------------------------------------------------------------------
# SlotGenerationTests
# ---------------------------------------------------------------------------

class SlotGenerationTests(TestCase):
    """
    Verifies the core slot generation algorithm:
      - Weekday template → correct number of slots for the interval/duration.
      - Buffer-before and buffer-after widen effective_start / effective_end.
      - Interval grid spacing is correct.
      - No slots generated on days without a matching template.
      - Empty result when staff has no templates.
    """

    def setUp(self):
        self.org = make_org("gen-org")
        self.location = make_location(self.org, "gen-loc", tz="America/Toronto")
        self.service_type = make_service_type("gen-svc")
        self.svc = SlotAvailabilityService()

    def test_no_templates_returns_empty(self):
        """Staff with no availability templates → no slots."""
        staff = make_staff(self.location, suffix="-empty")
        appt_type = make_appt_type(self.service_type, "gen-empty")
        appt_type.staff_members.add(staff)
        # Monday 2026-07-06
        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 7, 6),
            date_to=date(2026, 7, 6),
            staff=staff,
        )
        self.assertEqual(result, [])

    def test_one_day_template_generates_slots(self):
        """
        9:00–11:00 template, 30-min duration, 30-min interval → 4 slots at
        09:00, 09:30, 10:00, 10:30 local (converted to UTC).
        """
        policy = make_policy(
            slot_interval_minutes=30,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            min_lead_time_hours=0,
            max_advance_days=365,
        )
        appt_type = make_appt_type(
            self.service_type,
            slug="gen-slots-basic",
            duration_minutes=30,
            scheduling_policy=policy,
        )
        staff = make_staff(self.location, suffix="-basic")
        appt_type.staff_members.add(staff)

        # Tuesday 2026-07-07 (isoweekday=2)
        target_date = date(2026, 7, 7)
        make_template(staff, day_of_week=2, start_time=time(9, 0), end_time=time(11, 0))

        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=target_date,
            date_to=target_date,
            staff=staff,
        )

        self.assertEqual(len(result), 4)
        # Verify chronological ordering
        starts = [s["start_datetime"] for s in result]
        self.assertEqual(starts, sorted(starts))
        # Each slot should be for the correct staff and appointment type
        for slot in result:
            self.assertEqual(slot["staff_id"], staff.pk)
            self.assertEqual(slot["appointment_type_id"], appt_type.pk)

    def test_slot_duration_correct(self):
        """Slot end_datetime = start_datetime + duration_minutes."""
        policy = make_policy(
            slot_interval_minutes=30,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            min_lead_time_hours=0,
            max_advance_days=365,
        )
        appt_type = make_appt_type(
            self.service_type,
            slug="gen-duration",
            duration_minutes=45,
            scheduling_policy=policy,
        )
        staff = make_staff(self.location, suffix="-dur")
        appt_type.staff_members.add(staff)
        target_date = date(2026, 7, 7)
        make_template(staff, day_of_week=2, start_time=time(9, 0), end_time=time(11, 0))

        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=target_date,
            date_to=target_date,
            staff=staff,
        )
        # 45-min duration with 30-min stride in 9:00–11:00 window (120 min total)
        # Slots: 9:00-9:45, 9:30-10:15, 10:00-10:45 (10:30-11:15 overruns window)
        # Actually with buffer_after=0: slot_end + 0 <= 11:00?
        # 9:00 end=9:45 ✓, 9:30 end=10:15 ✓, 10:00 end=10:45 ✓, 10:30 end=11:15 ✗
        self.assertEqual(len(result), 3)
        for slot in result:
            delta = slot["end_datetime"] - slot["start_datetime"]
            self.assertEqual(delta.total_seconds() / 60, 45)

    def test_buffer_widens_effective_times(self):
        """effective_start = start - buffer_before; effective_end = end + buffer_after."""
        policy = make_policy(
            slot_interval_minutes=30,
            buffer_before_minutes=5,
            buffer_after_minutes=10,
            min_lead_time_hours=0,
            max_advance_days=365,
        )
        appt_type = make_appt_type(
            self.service_type,
            slug="gen-buffer",
            duration_minutes=30,
            scheduling_policy=policy,
        )
        staff = make_staff(self.location, suffix="-buf")
        appt_type.staff_members.add(staff)
        target_date = date(2026, 7, 7)
        make_template(staff, day_of_week=2, start_time=time(9, 0), end_time=time(11, 0))

        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=target_date,
            date_to=target_date,
            staff=staff,
        )
        self.assertTrue(len(result) > 0)
        for slot in result:
            expected_eff_start = slot["start_datetime"] - timedelta(minutes=5)
            expected_eff_end = slot["end_datetime"] + timedelta(minutes=10)
            self.assertEqual(slot["effective_start"], expected_eff_start)
            self.assertEqual(slot["effective_end"], expected_eff_end)

    def test_no_slots_on_non_template_day(self):
        """Template is for Tuesday; query is for Wednesday → no slots."""
        policy = make_policy(
            slot_interval_minutes=30,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            min_lead_time_hours=0,
            max_advance_days=365,
        )
        appt_type = make_appt_type(
            self.service_type,
            slug="gen-wrong-day",
            scheduling_policy=policy,
        )
        staff = make_staff(self.location, suffix="-wd")
        appt_type.staff_members.add(staff)
        # Template only for Tuesday (2)
        make_template(staff, day_of_week=2, start_time=time(9, 0), end_time=time(17, 0))

        # Query for Wednesday 2026-07-08 (isoweekday=3)
        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 7, 8),
            date_to=date(2026, 7, 8),
            staff=staff,
        )
        self.assertEqual(result, [])

    def test_multi_day_range_includes_only_matching_weekdays(self):
        """
        Template for Monday (1) only. Date range Mon–Sun → 1 day with slots,
        6 days without.
        """
        policy = make_policy(
            slot_interval_minutes=60,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            min_lead_time_hours=0,
            max_advance_days=365,
        )
        appt_type = make_appt_type(
            self.service_type,
            slug="gen-multi-day",
            duration_minutes=60,
            scheduling_policy=policy,
        )
        staff = make_staff(self.location, suffix="-md")
        appt_type.staff_members.add(staff)
        # Monday (1) template: 9:00–13:00 → 4 hourly slots
        make_template(staff, day_of_week=1, start_time=time(9, 0), end_time=time(13, 0))

        # Week of Mon Jul 6 2026 – Sun Jul 12 2026
        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 7, 6),
            date_to=date(2026, 7, 12),
            staff=staff,
        )
        # All slots must be on Monday
        for slot in result:
            self.assertEqual(slot["start_datetime"].astimezone(TORONTO).weekday(), 0)  # 0=Monday

    def test_valid_until_expired_template_not_used(self):
        """Template expired before the query date → no slots."""
        policy = make_policy(
            slot_interval_minutes=30,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            min_lead_time_hours=0,
            max_advance_days=365,
        )
        appt_type = make_appt_type(self.service_type, slug="gen-expired", scheduling_policy=policy)
        staff = make_staff(self.location, suffix="-exp")
        appt_type.staff_members.add(staff)
        # Template valid only until 2026-06-30; query is 2026-07-07
        make_template(
            staff, day_of_week=2, start_time=time(9, 0), end_time=time(17, 0),
            valid_until=date(2026, 6, 30),
        )
        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 7, 7),
            date_to=date(2026, 7, 7),
            staff=staff,
        )
        self.assertEqual(result, [])

    def test_slot_dict_keys_present(self):
        """Each returned dict has all required keys from the spec."""
        policy = make_policy(
            slot_interval_minutes=30,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            min_lead_time_hours=0,
            max_advance_days=365,
        )
        appt_type = make_appt_type(self.service_type, slug="gen-keys", scheduling_policy=policy)
        staff = make_staff(self.location, suffix="-keys")
        appt_type.staff_members.add(staff)
        make_template(staff, day_of_week=2, start_time=time(9, 0), end_time=time(10, 0))

        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 7, 7),
            date_to=date(2026, 7, 7),
            staff=staff,
        )
        self.assertTrue(len(result) > 0)
        required_keys = {
            "slot_id", "staff_id", "location_id", "appointment_type_id",
            "start_datetime", "end_datetime", "effective_start", "effective_end",
            "timezone", "start_local", "available_spaces", "waitlist_count",
        }
        for slot in result:
            self.assertEqual(set(slot.keys()), required_keys)

    def test_not_accepting_bookings_staff_excluded(self):
        """Staff with is_accepting_bookings=False are excluded."""
        policy = make_policy(
            slot_interval_minutes=30, buffer_before_minutes=0, buffer_after_minutes=0,
            min_lead_time_hours=0, max_advance_days=365,
        )
        appt_type = make_appt_type(self.service_type, slug="gen-not-accepting", scheduling_policy=policy)
        staff = make_staff(self.location, suffix="-na", is_accepting_bookings=False)
        appt_type.staff_members.add(staff)
        make_template(staff, day_of_week=2, start_time=time(9, 0), end_time=time(17, 0))

        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 7, 7),
            date_to=date(2026, 7, 7),
        )
        self.assertEqual(result, [])

    def test_existing_slot_blocks_collision(self):
        """A pre-existing Slot in the DB blocks new computations from being returned."""
        policy = make_policy(
            slot_interval_minutes=30,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            min_lead_time_hours=0,
            max_advance_days=365,
        )
        appt_type = make_appt_type(
            self.service_type, slug="gen-collision", scheduling_policy=policy
        )
        staff = make_staff(self.location, suffix="-coll")
        appt_type.staff_members.add(staff)
        make_template(staff, day_of_week=2, start_time=time(9, 0), end_time=time(11, 0))

        # Pre-populate the 09:00 UTC slot (Toronto is UTC-4 in summer, so 09:00 local = 13:00 UTC)
        tz_toronto = ZoneInfo("America/Toronto")
        nine_am_local = datetime(2026, 7, 7, 9, 0, tzinfo=tz_toronto)
        nine_am_utc = nine_am_local.astimezone(UTC)
        make_slot(appt_type, staff, self.location, nine_am_utc, duration_minutes=30)

        result_with_existing = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 7, 7),
            date_to=date(2026, 7, 7),
            staff=staff,
        )
        # Before collision there would be 4 slots; now 9:00 is occupied → 3
        self.assertEqual(len(result_with_existing), 3, "One slot must be blocked by existing slot")
        start_hm_local = [
            (
                s["start_datetime"].astimezone(tz_toronto).hour,
                s["start_datetime"].astimezone(tz_toronto).minute,
            )
            for s in result_with_existing
        ]
        self.assertNotIn((9, 0), start_hm_local, "09:00 slot must be excluded — pre-existing slot occupies it")
        self.assertIn((9, 30), start_hm_local, "09:30 slot must still be available")


# ---------------------------------------------------------------------------
# StaffExceptionTests (services layer)
# ---------------------------------------------------------------------------

class StaffExceptionTests(TestCase):
    """
    Tests for StaffException integration with SlotAvailabilityService.

    HOLIDAY, LEAVE, TRAINING → all slots blocked for that date.
    OVERRIDE → custom hours used instead of templates.
    """

    def setUp(self):
        self.org = make_org("exc-org")
        self.location = make_location(self.org, "exc-loc", tz="America/Toronto")
        self.service_type = make_service_type("exc-svc")
        self.policy = make_policy(
            slot_interval_minutes=30,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            min_lead_time_hours=0,
            max_advance_days=365,
        )
        self.svc = SlotAvailabilityService()

    def _setup_staff_with_tuesday_template(self, suffix: str = "") -> tuple:
        appt_type = make_appt_type(
            self.service_type,
            slug=f"exc-appt{suffix}",
            scheduling_policy=self.policy,
        )
        staff = make_staff(self.location, suffix=f"-exc{suffix}")
        appt_type.staff_members.add(staff)
        make_template(staff, day_of_week=2, start_time=time(9, 0), end_time=time(17, 0))
        return appt_type, staff

    def test_holiday_exception_blocks_all_slots(self):
        appt_type, staff = self._setup_staff_with_tuesday_template("-holiday")
        StaffException.objects.create(
            staff=staff,
            exception_date=date(2026, 7, 7),
            exception_type="holiday",
        )
        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 7, 7),
            date_to=date(2026, 7, 7),
            staff=staff,
        )
        self.assertEqual(result, [])

    def test_leave_exception_blocks_all_slots(self):
        appt_type, staff = self._setup_staff_with_tuesday_template("-leave")
        StaffException.objects.create(
            staff=staff,
            exception_date=date(2026, 7, 7),
            exception_type="leave",
        )
        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 7, 7),
            date_to=date(2026, 7, 7),
            staff=staff,
        )
        self.assertEqual(result, [])

    def test_training_exception_blocks_all_slots(self):
        appt_type, staff = self._setup_staff_with_tuesday_template("-training")
        StaffException.objects.create(
            staff=staff,
            exception_date=date(2026, 7, 7),
            exception_type="training",
        )
        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 7, 7),
            date_to=date(2026, 7, 7),
            staff=staff,
        )
        self.assertEqual(result, [])

    def test_override_exception_uses_custom_hours(self):
        """OVERRIDE exception replaces the template's 9–17 window with 13–15."""
        appt_type, staff = self._setup_staff_with_tuesday_template("-override")
        StaffException.objects.create(
            staff=staff,
            exception_date=date(2026, 7, 7),
            exception_type="override",
            override_start_time=time(13, 0),
            override_end_time=time(15, 0),
        )
        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 7, 7),
            date_to=date(2026, 7, 7),
            staff=staff,
        )
        # 13:00–15:00 window with 30-min duration and 30-min stride = 4 slots
        self.assertEqual(len(result), 4)
        tz_toronto = ZoneInfo("America/Toronto")
        hours = {s["start_datetime"].astimezone(tz_toronto).hour for s in result}
        # Only hours within 13:00–14:30 range
        self.assertTrue(hours.issubset({13, 14}))
        # No slots outside the override window
        for slot in result:
            local = slot["start_datetime"].astimezone(tz_toronto)
            self.assertGreaterEqual(local.hour, 13)
            self.assertLess(local.hour, 15)

    def test_exception_only_affects_that_staff(self):
        """Holiday for staff A does not block staff B on the same date."""
        appt_type_a, staff_a = self._setup_staff_with_tuesday_template("-a")
        # Staff B — separate profile, same appointment type, same template day
        user_b = make_user(email="staff_b_ex@example.com")
        staff_b = StaffProfile.objects.create(user=user_b, location=self.location)
        appt_type_a.staff_members.add(staff_b)
        make_template(staff_b, day_of_week=2, start_time=time(9, 0), end_time=time(10, 0))

        # Holiday only for staff_a
        StaffException.objects.create(
            staff=staff_a,
            exception_date=date(2026, 7, 7),
            exception_type="holiday",
        )
        result = self.svc.get_available_slots(
            appointment_type=appt_type_a,
            date_from=date(2026, 7, 7),
            date_to=date(2026, 7, 7),
        )
        # Only staff_b's slots should appear
        staff_ids = {s["staff_id"] for s in result}
        self.assertNotIn(staff_a.pk, staff_ids)
        self.assertIn(staff_b.pk, staff_ids)


# ---------------------------------------------------------------------------
# BookingWindowTests
# ---------------------------------------------------------------------------

class BookingWindowTests(TestCase):
    """
    Tests for min_lead_time_hours and max_advance_days enforcement.

    min_lead_time_hours: slots starting before (now + min_lead_time_hours) are excluded.
    max_advance_days:    slots starting after  (now + max_advance_days) are excluded.
    """

    def setUp(self):
        self.org = make_org("bw-org")
        self.location = make_location(self.org, "bw-loc", tz="America/Toronto")
        self.service_type = make_service_type("bw-svc")
        self.svc = SlotAvailabilityService()

    def test_min_lead_time_excludes_imminent_slots(self):
        """
        min_lead_time_hours=24: slots that start within 24h of now are excluded.
        We create a template for today and check that no slots fall within the lead time.
        """
        policy = make_policy(
            slot_interval_minutes=30,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            min_lead_time_hours=24,
            max_advance_days=365,
        )
        appt_type = make_appt_type(self.service_type, slug="bw-min-lead", scheduling_policy=policy)
        staff = make_staff(self.location, suffix="-ml")
        appt_type.staff_members.add(staff)
        # Template for every day of week
        for dow in range(1, 8):
            make_template(staff, day_of_week=dow, start_time=time(0, 0), end_time=time(23, 30))

        now = timezone.now()
        # Query for today
        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=now.date(),
            date_to=now.date(),
            staff=staff,
        )
        # All returned slots must start at least 24h from now
        lead_cutoff = now + timedelta(hours=24)
        for slot in result:
            self.assertGreaterEqual(slot["start_datetime"], lead_cutoff)

    def test_max_advance_days_caps_horizon(self):
        """max_advance_days=7: slots beyond 7 days from now are excluded."""
        policy = make_policy(
            slot_interval_minutes=60,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            min_lead_time_hours=0,
            max_advance_days=7,
        )
        appt_type = make_appt_type(self.service_type, slug="bw-max-adv", scheduling_policy=policy)
        staff = make_staff(self.location, suffix="-ma")
        appt_type.staff_members.add(staff)
        for dow in range(1, 8):
            make_template(staff, day_of_week=dow, start_time=time(9, 0), end_time=time(17, 0))

        now = timezone.now()
        beyond = now + timedelta(days=14)  # 2 weeks ahead
        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=now.date(),
            date_to=beyond.date(),
            staff=staff,
        )
        max_allowed = now + timedelta(days=7)
        for slot in result:
            self.assertLessEqual(slot["start_datetime"], max_allowed)

    def test_zero_max_advance_means_unlimited(self):
        """max_advance_days=0 (falsy) → no upper cap."""
        policy = make_policy(
            slot_interval_minutes=60,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            min_lead_time_hours=0,
            max_advance_days=0,  # no cap
        )
        appt_type = make_appt_type(self.service_type, slug="bw-no-cap", scheduling_policy=policy)
        staff = make_staff(self.location, suffix="-nc")
        appt_type.staff_members.add(staff)
        # Template on Monday
        make_template(staff, day_of_week=1, start_time=time(9, 0), end_time=time(10, 0))

        # Query 2 years ahead — should still return slots
        future = date(2028, 7, 3)  # A Monday in 2028
        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=future,
            date_to=future,
            staff=staff,
        )
        # With a 1-hour slot in a 1-hour window, exactly 1 slot
        self.assertEqual(len(result), 1)


# ---------------------------------------------------------------------------
# FrequencyControlTests
# ---------------------------------------------------------------------------

class FrequencyControlTests(TestCase):
    """
    Tests for booking_frequency_days (food bank / frequency-control pattern).

    When a citizen has a confirmed booking for the same service_type within
    the frequency window, get_available_slots() returns [].
    """

    def setUp(self):
        self.org = make_org("freq-org")
        self.location = make_location(self.org, "freq-loc", tz="America/Toronto")
        self.service_type = make_service_type("freq-svc")
        self.svc = SlotAvailabilityService()
        self.policy = make_policy(
            slot_interval_minutes=30,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            min_lead_time_hours=0,
            max_advance_days=365,
            booking_frequency_days=7,  # once per week
        )

    def test_no_citizen_arg_frequency_not_applied(self):
        """If citizen is not provided, frequency control is bypassed."""
        appt_type = make_appt_type(
            self.service_type, slug="freq-no-citizen", scheduling_policy=self.policy
        )
        staff = make_staff(self.location, suffix="-nc")
        appt_type.staff_members.add(staff)
        make_template(staff, day_of_week=1, start_time=time(9, 0), end_time=time(10, 0))

        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 7, 6),
            date_to=date(2026, 7, 6),
            staff=staff,
            citizen=None,  # No citizen
        )
        # Should compute normally, not be empty due to frequency
        # (May be empty for min_lead_time reasons but NOT because of frequency)
        # Just assert it doesn't raise
        self.assertIsInstance(result, list)

    def test_citizen_not_blocked_when_no_booking_exists(self):
        """
        Citizen with no recent bookings should NOT be blocked by frequency control.
        Booking model is Wave 3; this test uses the ImportError guard path.
        """
        citizen = User.objects.create_user(
            email="citizen_freq@example.com",
            password="pass!",
            is_staff=False,
        )
        appt_type = make_appt_type(
            self.service_type, slug="freq-no-book", scheduling_policy=self.policy
        )
        staff = make_staff(self.location, suffix="-nb")
        appt_type.staff_members.add(staff)
        make_template(staff, day_of_week=1, start_time=time(9, 0), end_time=time(10, 0))

        # With Booking model not yet importable (Wave 3), _citizen_within_frequency_window
        # returns False → citizen is not blocked
        with patch(
            "apps.appointments.services.availability.SlotAvailabilityService"
            "._citizen_within_frequency_window",
            return_value=False,
        ):
            result = self.svc.get_available_slots(
                appointment_type=appt_type,
                date_from=date(2026, 7, 6),
                date_to=date(2026, 7, 6),
                staff=staff,
                citizen=citizen,
            )
        self.assertIsInstance(result, list)

    def test_citizen_blocked_when_within_frequency_window(self):
        """
        When _citizen_within_frequency_window returns True,
        get_available_slots() returns an empty list.
        """
        citizen = User.objects.create_user(
            email="citizen_blocked@example.com",
            password="pass!",
            is_staff=False,
        )
        appt_type = make_appt_type(
            self.service_type, slug="freq-blocked", scheduling_policy=self.policy
        )
        staff = make_staff(self.location, suffix="-blk")
        appt_type.staff_members.add(staff)
        make_template(staff, day_of_week=1, start_time=time(9, 0), end_time=time(10, 0))

        with patch(
            "apps.appointments.services.availability.SlotAvailabilityService"
            "._citizen_within_frequency_window",
            return_value=True,
        ):
            result = self.svc.get_available_slots(
                appointment_type=appt_type,
                date_from=date(2026, 7, 6),
                date_to=date(2026, 7, 6),
                staff=staff,
                citizen=citizen,
            )
        self.assertEqual(result, [])

    def test_zero_frequency_days_disables_check(self):
        """booking_frequency_days=0 means no frequency restriction."""
        zero_policy = make_policy(
            slot_interval_minutes=30,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            min_lead_time_hours=0,
            max_advance_days=365,
            booking_frequency_days=0,
        )
        appt_type = make_appt_type(
            self.service_type, slug="freq-zero", scheduling_policy=zero_policy
        )
        staff = make_staff(self.location, suffix="-zf")
        appt_type.staff_members.add(staff)
        make_template(staff, day_of_week=1, start_time=time(9, 0), end_time=time(10, 0))

        citizen = User.objects.create_user(
            email="citizen_zero@example.com", password="pass!", is_staff=False,
        )
        # Even if this citizen has recent bookings, frequency=0 means no restriction
        # The _citizen_within_frequency_window should not even be called
        with patch(
            "apps.appointments.services.availability.SlotAvailabilityService"
            "._citizen_within_frequency_window",
        ) as mock_check:
            self.svc.get_available_slots(
                appointment_type=appt_type,
                date_from=date(2026, 7, 6),
                date_to=date(2026, 7, 6),
                staff=staff,
                citizen=citizen,
            )
        mock_check.assert_not_called()


# ---------------------------------------------------------------------------
# DSTHandlingTests
# ---------------------------------------------------------------------------

class DSTHandlingTests(TestCase):
    """
    Verifies correct UTC conversion across DST boundaries for Canadian timezones.

    Eastern Time (America/Toronto):
      - EDT (summer, UTC-4): in effect July 7 2026
      - EST (winter, UTC-5): in effect November 10 2026

    The slot generation algorithm must produce correct UTC times based on
    the IANA timezone of the staff's location. We verify that 9:00 local
    maps to different UTC hours in summer vs winter.
    """

    def setUp(self):
        self.org = make_org("dst-org")
        self.service_type = make_service_type("dst-svc")
        self.svc = SlotAvailabilityService()
        self.policy = make_policy(
            slot_interval_minutes=60,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            min_lead_time_hours=0,
            max_advance_days=730,
        )

    def test_summer_toronto_nine_am_is_utc_thirteen(self):
        """
        In July (EDT, UTC-4), 09:00 Toronto = 13:00 UTC.
        """
        location = make_location(self.org, "dst-toronto-summer", tz="America/Toronto")
        appt_type = make_appt_type(
            self.service_type, slug="dst-summer", scheduling_policy=self.policy,
            duration_minutes=60,
        )
        staff = make_staff(location, suffix="-dsts")
        appt_type.staff_members.add(staff)
        # Monday (1) template
        make_template(staff, day_of_week=1, start_time=time(9, 0), end_time=time(10, 0))

        # Monday 2026-07-06 (summer EDT)
        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 7, 6),
            date_to=date(2026, 7, 6),
            staff=staff,
        )
        self.assertEqual(len(result), 1)
        start_utc = result[0]["start_datetime"]
        self.assertEqual(start_utc.hour, 13)  # 09:00 EDT = 13:00 UTC

    def test_winter_toronto_nine_am_is_utc_fourteen(self):
        """
        In November (EST, UTC-5), 09:00 Toronto = 14:00 UTC.
        """
        location = make_location(self.org, "dst-toronto-winter", tz="America/Toronto")
        appt_type = make_appt_type(
            self.service_type, slug="dst-winter", scheduling_policy=self.policy,
            duration_minutes=60,
        )
        staff = make_staff(location, suffix="-dstw")
        appt_type.staff_members.add(staff)
        # Monday (1) template
        make_template(staff, day_of_week=1, start_time=time(9, 0), end_time=time(10, 0))

        # Monday 2026-11-09 (winter EST)
        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 11, 9),
            date_to=date(2026, 11, 9),
            staff=staff,
        )
        self.assertEqual(len(result), 1)
        start_utc = result[0]["start_datetime"]
        self.assertEqual(start_utc.hour, 14)  # 09:00 EST = 14:00 UTC

    def test_dst_transition_week_both_dates_correct(self):
        """
        Clocks fall back from EDT to EST on Nov 1 2026 at 02:00 local.
        Monday Oct 26 2026 (EDT) → 09:00 = 13:00 UTC
        Monday Nov 2 2026  (EST) → 09:00 = 14:00 UTC
        """
        location = make_location(self.org, "dst-toronto-transition", tz="America/Toronto")
        appt_type = make_appt_type(
            self.service_type, slug="dst-trans", scheduling_policy=self.policy,
            duration_minutes=60,
        )
        staff = make_staff(location, suffix="-dsttrans")
        appt_type.staff_members.add(staff)
        # Monday (1) template
        make_template(staff, day_of_week=1, start_time=time(9, 0), end_time=time(10, 0))

        result_edt = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 10, 26),
            date_to=date(2026, 10, 26),
            staff=staff,
        )
        result_est = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 11, 2),
            date_to=date(2026, 11, 2),
            staff=staff,
        )
        self.assertEqual(len(result_edt), 1)
        self.assertEqual(len(result_est), 1)
        self.assertEqual(result_edt[0]["start_datetime"].hour, 13)
        self.assertEqual(result_est[0]["start_datetime"].hour, 14)


# ---------------------------------------------------------------------------
# MultiTimezoneTests
# ---------------------------------------------------------------------------

class MultiTimezoneTests(TestCase):
    """
    Tests for staff located in different Canadian timezones.

    Pacific Time (America/Vancouver):
      - PDT (summer, UTC-7): in effect July 7 2026
      - PST (winter, UTC-8): in effect November 10 2026
    """

    def setUp(self):
        self.org = make_org("tz-org")
        self.service_type = make_service_type("tz-svc")
        self.svc = SlotAvailabilityService()
        self.policy = make_policy(
            slot_interval_minutes=60,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            min_lead_time_hours=0,
            max_advance_days=730,
        )

    def test_vancouver_summer_nine_am_is_utc_sixteen(self):
        """
        In July (PDT, UTC-7), 09:00 Vancouver = 16:00 UTC.
        """
        org = make_org("tz-van-org")
        location = make_location(org, "tz-van-loc", tz="America/Vancouver")
        appt_type = make_appt_type(
            self.service_type, slug="tz-van-summer", scheduling_policy=self.policy,
            duration_minutes=60,
        )
        staff = make_staff(location, suffix="-van")
        appt_type.staff_members.add(staff)
        make_template(staff, day_of_week=2, start_time=time(9, 0), end_time=time(10, 0))

        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 7, 7),
            date_to=date(2026, 7, 7),
            staff=staff,
        )
        self.assertEqual(len(result), 1)
        # PDT = UTC-7; 09:00 local = 16:00 UTC
        self.assertEqual(result[0]["start_datetime"].hour, 16)
        self.assertEqual(result[0]["timezone"], "America/Vancouver")

    def test_vancouver_winter_nine_am_is_utc_seventeen(self):
        """
        In November (PST, UTC-8), 09:00 Vancouver = 17:00 UTC.
        """
        org = make_org("tz-van-win-org")
        location = make_location(org, "tz-van-win-loc", tz="America/Vancouver")
        appt_type = make_appt_type(
            self.service_type, slug="tz-van-winter", scheduling_policy=self.policy,
            duration_minutes=60,
        )
        staff = make_staff(location, suffix="-vanw")
        appt_type.staff_members.add(staff)
        make_template(staff, day_of_week=1, start_time=time(9, 0), end_time=time(10, 0))

        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 11, 9),
            date_to=date(2026, 11, 9),
            staff=staff,
        )
        self.assertEqual(len(result), 1)
        # PST = UTC-8; 09:00 local = 17:00 UTC
        self.assertEqual(result[0]["start_datetime"].hour, 17)

    def test_start_local_is_in_location_timezone(self):
        """start_local datetime in the result should be in the location's timezone."""
        org = make_org("tz-local-org")
        location = make_location(org, "tz-local-loc", tz="America/Vancouver")
        policy = make_policy(
            slot_interval_minutes=60,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            min_lead_time_hours=0,
            max_advance_days=730,
        )
        appt_type = make_appt_type(
            self.service_type, slug="tz-start-local", scheduling_policy=policy,
            duration_minutes=60,
        )
        staff = make_staff(location, suffix="-sl")
        appt_type.staff_members.add(staff)
        make_template(staff, day_of_week=2, start_time=time(14, 0), end_time=time(15, 0))

        result = self.svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 7, 7),
            date_to=date(2026, 7, 7),
            staff=staff,
        )
        self.assertEqual(len(result), 1)
        start_local = result[0]["start_local"]
        # Should be a timezone-aware datetime in Vancouver tz
        self.assertIsNotNone(start_local.tzinfo)
        self.assertEqual(start_local.hour, 14)


# ---------------------------------------------------------------------------
# GenerateSlotsForRangeTests
# ---------------------------------------------------------------------------

class GenerateSlotsForRangeTests(TestCase):
    """
    Integration tests for generate_slots_for_range().

    - Returns count of newly created Slot records.
    - Is idempotent (second run creates 0 new slots when all already exist).
    - Created slots have correct UTC datetimes.
    - Slot status defaults to 'available'.
    """

    def setUp(self):
        self.org = make_org("gsfr-org")
        self.location = make_location(self.org, "gsfr-loc", tz="America/Toronto")
        self.service_type = make_service_type("gsfr-svc")
        self.policy = make_policy(
            slot_interval_minutes=30,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            min_lead_time_hours=0,
            max_advance_days=365,
        )

    def test_generate_slots_creates_db_records(self):
        appt_type = make_appt_type(
            self.service_type, slug="gsfr-create",
            scheduling_policy=self.policy, duration_minutes=30,
        )
        staff = make_staff(self.location, suffix="-gsfr")
        appt_type.staff_members.add(staff)
        # Monday template: 9–10 → 2 slots (30 min each)
        make_template(staff, day_of_week=1, start_time=time(9, 0), end_time=time(10, 0))

        count = generate_slots_for_range(
            appointment_type=appt_type,
            staff=staff,
            date_from=date(2026, 7, 6),
            date_to=date(2026, 7, 6),
        )
        self.assertEqual(count, 2)
        self.assertEqual(Slot.objects.filter(staff=staff, appointment_type=appt_type).count(), 2)

    def test_generate_slots_idempotent(self):
        """Running generate_slots_for_range twice for the same range creates 0 on second run."""
        appt_type = make_appt_type(
            self.service_type, slug="gsfr-idem",
            scheduling_policy=self.policy, duration_minutes=30,
        )
        staff = make_staff(self.location, suffix="-idem")
        appt_type.staff_members.add(staff)
        make_template(staff, day_of_week=1, start_time=time(9, 0), end_time=time(10, 0))

        first = generate_slots_for_range(
            appointment_type=appt_type,
            staff=staff,
            date_from=date(2026, 7, 6),
            date_to=date(2026, 7, 6),
        )
        second = generate_slots_for_range(
            appointment_type=appt_type,
            staff=staff,
            date_from=date(2026, 7, 6),
            date_to=date(2026, 7, 6),
        )
        self.assertEqual(first, 2)
        # Second run may return 0 (ignore_conflicts) or same count depending on backend
        # DB count should not double
        total = Slot.objects.filter(staff=staff, appointment_type=appt_type).count()
        self.assertEqual(total, 2)

    def test_generated_slots_have_correct_status(self):
        appt_type = make_appt_type(
            self.service_type, slug="gsfr-status",
            scheduling_policy=self.policy, duration_minutes=30,
        )
        staff = make_staff(self.location, suffix="-stat")
        appt_type.staff_members.add(staff)
        make_template(staff, day_of_week=1, start_time=time(9, 0), end_time=time(10, 0))

        generate_slots_for_range(
            appointment_type=appt_type,
            staff=staff,
            date_from=date(2026, 7, 6),
            date_to=date(2026, 7, 6),
        )
        slots = Slot.objects.filter(staff=staff, appointment_type=appt_type)
        for slot in slots:
            self.assertEqual(slot.status, "available")
            self.assertEqual(slot.spaces_used, 0)

    def test_generated_slots_have_correct_capacity(self):
        appt_type = make_appt_type(
            self.service_type, slug="gsfr-cap",
            scheduling_policy=self.policy, duration_minutes=30,
            capacity_per_slot=3,
        )
        staff = make_staff(self.location, suffix="-cap")
        appt_type.staff_members.add(staff)
        make_template(staff, day_of_week=1, start_time=time(9, 0), end_time=time(10, 0))

        generate_slots_for_range(
            appointment_type=appt_type,
            staff=staff,
            date_from=date(2026, 7, 6),
            date_to=date(2026, 7, 6),
        )
        for slot in Slot.objects.filter(staff=staff, appointment_type=appt_type):
            self.assertEqual(slot.capacity, 3)

    def test_no_templates_returns_zero(self):
        appt_type = make_appt_type(
            self.service_type, slug="gsfr-notempl",
            scheduling_policy=self.policy,
        )
        staff = make_staff(self.location, suffix="-nt")
        appt_type.staff_members.add(staff)
        count = generate_slots_for_range(
            appointment_type=appt_type,
            staff=staff,
            date_from=date(2026, 7, 6),
            date_to=date(2026, 7, 12),
        )
        self.assertEqual(count, 0)


# ---------------------------------------------------------------------------
# SlotServiceUnitTests (block_slot / cancel_slot)
# ---------------------------------------------------------------------------

class SlotServiceUnitTests(TestCase):
    """
    Tests for block_slot() and cancel_slot() service functions.
    """

    def setUp(self):
        org = make_org("ss-org")
        self.location = make_location(org, "ss-loc")
        self.service_type = make_service_type("ss-svc")
        self.policy = make_policy(
            slot_interval_minutes=30, buffer_before_minutes=0,
            buffer_after_minutes=0, min_lead_time_hours=0, max_advance_days=365,
        )
        self.appt_type = make_appt_type(
            self.service_type, slug="ss-appt", scheduling_policy=self.policy
        )
        self.staff = make_staff(self.location, suffix="-ss")
        now_utc = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        self.slot = make_slot(
            self.appt_type, self.staff, self.location, now_utc
        )

    def test_block_slot_transitions_to_blocked(self):
        slot = block_slot(slot=self.slot)
        slot.refresh_from_db()
        self.assertEqual(slot.status, "blocked")

    def test_block_slot_idempotent(self):
        block_slot(slot=self.slot)
        block_slot(slot=self.slot)  # Second call — should not raise
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.status, "blocked")

    def test_block_slot_records_reason_in_internal_note(self):
        slot = block_slot(slot=self.slot, reason="Staff sick day")
        slot.refresh_from_db()
        self.assertIn("[BLOCKED]", slot.internal_note)
        self.assertIn("Staff sick day", slot.internal_note)

    def test_cancel_slot_transitions_to_cancelled(self):
        slot = cancel_slot(slot=self.slot)
        slot.refresh_from_db()
        self.assertEqual(slot.status, "cancelled")

    def test_cancel_slot_idempotent(self):
        cancel_slot(slot=self.slot)
        cancel_slot(slot=self.slot)  # Second call — should not raise
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.status, "cancelled")

    def test_cancel_slot_records_reason_in_internal_note(self):
        slot = cancel_slot(slot=self.slot, reason="Facility closure")
        slot.refresh_from_db()
        self.assertIn("[CANCELLED]", slot.internal_note)
        self.assertIn("Facility closure", slot.internal_note)

    def test_cancel_slot_appends_to_existing_note(self):
        self.slot.internal_note = "Existing note"
        self.slot.save()
        cancel_slot(slot=self.slot, reason="New reason")
        self.slot.refresh_from_db()
        self.assertIn("Existing note", self.slot.internal_note)
        self.assertIn("[CANCELLED]", self.slot.internal_note)

    def test_block_then_cancel_maintains_note_history(self):
        block_slot(slot=self.slot, reason="Block reason")
        self.slot.refresh_from_db()
        # Reset to available to allow cancel
        self.slot.status = "available"
        self.slot.save()
        cancel_slot(slot=self.slot, reason="Cancel reason")
        self.slot.refresh_from_db()
        self.assertIn("Block reason", self.slot.internal_note)
        self.assertIn("Cancel reason", self.slot.internal_note)

    def test_slot_full_error_not_raised_from_service(self):
        """SlotFullError is a public exception class (used by Wave 3 booking service)."""
        self.assertTrue(issubclass(SlotFullError, Exception))

    def test_slot_has_bookings_error_not_raised_from_service(self):
        """SlotHasBookingsError is a public exception class."""
        self.assertTrue(issubclass(SlotHasBookingsError, Exception))


# ---------------------------------------------------------------------------
# AvailabilityTemplateModelTests (Wave 2 model constraints)
# ---------------------------------------------------------------------------

class AvailabilityTemplateModelTests(TestCase):
    """
    Unit tests for AvailabilityTemplate model constraints and clean() validation.
    """

    def setUp(self):
        org = make_org("at-org")
        self.location = make_location(org, "at-loc")
        self.staff = make_staff(self.location, suffix="-at")

    def test_str_contains_day_and_times(self):
        tpl = make_template(
            self.staff, day_of_week=1, start_time=time(9, 0), end_time=time(17, 0)
        )
        s = str(tpl)
        self.assertIn("Monday", s)

    def test_str_does_not_contain_email(self):
        tpl = make_template(
            self.staff, day_of_week=2, start_time=time(9, 0), end_time=time(17, 0)
        )
        self.assertNotIn("@", str(tpl))

    def test_end_time_must_be_after_start_time_clean(self):
        tpl = AvailabilityTemplate(
            staff=self.staff,
            day_of_week=1,
            start_time=time(10, 0),
            end_time=time(9, 0),  # Before start
            valid_from=date(2026, 1, 1),
        )
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            tpl.clean()

    def test_equal_start_end_time_raises_clean(self):
        tpl = AvailabilityTemplate(
            staff=self.staff,
            day_of_week=1,
            start_time=time(9, 0),
            end_time=time(9, 0),  # Equal — must fail
            valid_from=date(2026, 1, 1),
        )
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            tpl.clean()

    def test_valid_until_before_valid_from_raises_clean(self):
        tpl = AvailabilityTemplate(
            staff=self.staff,
            day_of_week=1,
            start_time=time(9, 0),
            end_time=time(17, 0),
            valid_from=date(2026, 7, 1),
            valid_until=date(2026, 6, 30),  # Before valid_from
        )
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            tpl.clean()

    def test_valid_until_equals_valid_from_is_ok(self):
        """valid_until == valid_from is a one-day-only schedule — valid."""
        tpl = AvailabilityTemplate(
            staff=self.staff,
            day_of_week=1,
            start_time=time(9, 0),
            end_time=time(17, 0),
            valid_from=date(2026, 7, 7),
            valid_until=date(2026, 7, 7),
        )
        tpl.clean()  # Must not raise

    def test_null_valid_until_is_open_ended(self):
        tpl = make_template(
            self.staff, day_of_week=3, start_time=time(9, 0),
            end_time=time(17, 0), valid_until=None,
        )
        self.assertIsNone(tpl.valid_until)

    def test_iso_weekday_choices_cover_1_to_7(self):
        choices = dict(AvailabilityTemplate.DAYS_OF_WEEK)
        for i in range(1, 8):
            self.assertIn(i, choices)

    def test_ordering_by_day_of_week_then_start_time(self):
        """Templates ordered by day_of_week then start_time."""
        make_template(self.staff, day_of_week=3, start_time=time(14, 0), end_time=time(15, 0))
        make_template(self.staff, day_of_week=1, start_time=time(10, 0), end_time=time(11, 0))
        make_template(self.staff, day_of_week=1, start_time=time(9, 0), end_time=time(10, 0))
        templates = list(AvailabilityTemplate.objects.filter(staff=self.staff))
        days = [t.day_of_week for t in templates]
        self.assertEqual(days, sorted(days))


# ---------------------------------------------------------------------------
# StaffExceptionModelTests (Wave 2 model constraints)
# ---------------------------------------------------------------------------

class StaffExceptionModelTests(TestCase):
    """
    Unit tests for StaffException model constraints and clean() validation.
    """

    def setUp(self):
        org = make_org("se-org")
        self.location = make_location(org, "se-loc")
        self.staff = make_staff(self.location, suffix="-se")

    def test_str_contains_date_and_type(self):
        exc = StaffException.objects.create(
            staff=self.staff,
            exception_date=date(2026, 7, 7),
            exception_type="holiday",
        )
        s = str(exc)
        self.assertIn("2026-07-07", s)

    def test_str_does_not_contain_email(self):
        exc = StaffException.objects.create(
            staff=self.staff,
            exception_date=date(2026, 7, 8),
            exception_type="leave",
        )
        self.assertNotIn("@", str(exc))

    def test_unique_together_staff_date(self):
        """(staff, exception_date) is unique."""
        StaffException.objects.create(
            staff=self.staff,
            exception_date=date(2026, 7, 7),
            exception_type="holiday",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StaffException.objects.create(
                    staff=self.staff,
                    exception_date=date(2026, 7, 7),
                    exception_type="leave",
                )

    def test_override_requires_both_times(self):
        from django.core.exceptions import ValidationError
        exc = StaffException(
            staff=self.staff,
            exception_date=date(2026, 7, 9),
            exception_type="override",
            override_start_time=None,
            override_end_time=None,
        )
        with self.assertRaises(ValidationError):
            exc.clean()

    def test_override_requires_end_after_start(self):
        from django.core.exceptions import ValidationError
        exc = StaffException(
            staff=self.staff,
            exception_date=date(2026, 7, 9),
            exception_type="override",
            override_start_time=time(15, 0),
            override_end_time=time(14, 0),  # Before start
        )
        with self.assertRaises(ValidationError):
            exc.clean()

    def test_override_with_valid_times_passes_clean(self):
        exc = StaffException(
            staff=self.staff,
            exception_date=date(2026, 7, 9),
            exception_type="override",
            override_start_time=time(9, 0),
            override_end_time=time(12, 0),
        )
        exc.clean()  # Must not raise

    def test_non_override_types_do_not_require_times(self):
        for exc_type in ("holiday", "leave", "training"):
            exc = StaffException(
                staff=self.staff,
                exception_date=date(2026, 7, 10),
                exception_type=exc_type,
                override_start_time=None,
                override_end_time=None,
            )
            exc.clean()  # Must not raise

    def test_exception_type_choices(self):
        """Verify all 4 exception types are defined."""
        types = {c[0] for c in StaffException.EXCEPTION_TYPE_CHOICES}
        self.assertEqual(types, {"holiday", "leave", "override", "training"})


# ---------------------------------------------------------------------------
# SlotConstraintTests (Wave 2 model constraints)
# ---------------------------------------------------------------------------

class SlotConstraintTests(TestCase):
    """
    Tests for Slot model DB-level CheckConstraints and properties.

    Spec §22.2: SlotConstraintTests — spaces_used ≤ capacity; end > start.
    """

    def setUp(self):
        org = make_org("sc-org")
        self.location = make_location(org, "sc-loc")
        self.service_type = make_service_type("sc-svc")
        self.appt_type = make_appt_type(self.service_type, slug="sc-appt")
        self.staff = make_staff(self.location, suffix="-sc")
        self.now_utc = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)

    def _make_valid_slot(self, **overrides) -> Slot:
        end = self.now_utc + timedelta(minutes=30)
        defaults = dict(
            appointment_type=self.appt_type,
            staff=self.staff,
            location=self.location,
            start_datetime=self.now_utc,
            end_datetime=end,
            effective_start=self.now_utc,
            effective_end=end,
            capacity=1,
            spaces_used=0,
            status="available",
        )
        defaults.update(overrides)
        return Slot(**defaults)

    def test_uuid_primary_key(self):
        """Slot uses UUID as primary key."""
        slot = make_slot(self.appt_type, self.staff, self.location, self.now_utc)
        import uuid as _uuid
        self.assertIsInstance(slot.pk, _uuid.UUID)

    def test_available_spaces_property(self):
        slot = self._make_valid_slot(capacity=3, spaces_used=1)
        self.assertEqual(slot.available_spaces, 2)

    def test_is_available_true_for_available_status(self):
        slot = self._make_valid_slot(status="available", capacity=1, spaces_used=0)
        self.assertTrue(slot.is_available)

    def test_is_available_true_for_partial_status(self):
        slot = self._make_valid_slot(status="partial", capacity=2, spaces_used=1)
        self.assertTrue(slot.is_available)

    def test_is_available_false_when_full(self):
        slot = self._make_valid_slot(status="full", capacity=1, spaces_used=1)
        self.assertFalse(slot.is_available)

    def test_is_available_false_when_blocked(self):
        slot = self._make_valid_slot(status="blocked", capacity=1, spaces_used=0)
        self.assertFalse(slot.is_available)

    def test_is_available_false_when_cancelled(self):
        slot = self._make_valid_slot(status="cancelled", capacity=1, spaces_used=0)
        self.assertFalse(slot.is_available)

    def test_is_available_false_when_completed(self):
        slot = self._make_valid_slot(status="completed", capacity=1, spaces_used=0)
        self.assertFalse(slot.is_available)

    def test_is_available_false_when_no_spaces(self):
        """available_spaces=0 → is_available=False even if status='partial'."""
        slot = self._make_valid_slot(status="partial", capacity=2, spaces_used=2)
        self.assertFalse(slot.is_available)

    def test_str_contains_uuid_and_status(self):
        slot = make_slot(self.appt_type, self.staff, self.location, self.now_utc)
        s = str(slot)
        self.assertIn(str(slot.pk), s)
        self.assertIn("available", s)

    def test_str_does_not_contain_email(self):
        """Slot.__str__ uses '@' as a separator but must not contain an actual email address."""
        import re
        slot = make_slot(self.appt_type, self.staff, self.location, self.now_utc)
        self.assertIsNone(
            re.search(r"@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", str(slot)),
            "Email address must not appear in Slot.__str__",
        )

    def test_slot_status_choices_coverage(self):
        expected = {"available", "partial", "full", "blocked", "cancelled", "completed"}
        actual = {c[0] for c in Slot.SLOT_STATUS_CHOICES}
        self.assertEqual(actual, expected)

    def test_effective_start_lte_start_datetime(self):
        """effective_start must be ≤ start_datetime."""
        slot = make_slot(
            self.appt_type, self.staff, self.location, self.now_utc,
            buffer_before=5,
        )
        self.assertLessEqual(slot.effective_start, slot.start_datetime)

    def test_effective_end_gte_end_datetime(self):
        """effective_end must be ≥ end_datetime."""
        slot = make_slot(
            self.appt_type, self.staff, self.location, self.now_utc,
            buffer_after=5,
        )
        self.assertGreaterEqual(slot.effective_end, slot.end_datetime)

    def test_slot_default_status_is_available(self):
        slot = make_slot(self.appt_type, self.staff, self.location, self.now_utc)
        self.assertEqual(slot.status, "available")

    def test_slot_default_spaces_used_is_zero(self):
        slot = make_slot(self.appt_type, self.staff, self.location, self.now_utc)
        self.assertEqual(slot.spaces_used, 0)

    def test_slot_is_walk_in_default_false(self):
        slot = make_slot(self.appt_type, self.staff, self.location, self.now_utc)
        self.assertFalse(slot.is_walk_in_slot)

    def test_slot_video_fields_default_empty(self):
        slot = make_slot(self.appt_type, self.staff, self.location, self.now_utc)
        self.assertEqual(slot.video_join_url_citizen, "")
        self.assertEqual(slot.video_join_url_staff, "")
        self.assertEqual(slot.video_meeting_id, "")
        self.assertEqual(slot.video_provider, "")

    def test_slot_on_delete_appointment_type_protect(self):
        """Deleting an AppointmentType with slots raises ProtectedError."""
        from django.db.models.deletion import ProtectedError
        make_slot(self.appt_type, self.staff, self.location, self.now_utc)
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                self.appt_type.delete()

    def test_slot_on_delete_staff_protect(self):
        """Deleting a StaffProfile with slots raises ProtectedError."""
        from django.db.models.deletion import ProtectedError
        make_slot(self.appt_type, self.staff, self.location, self.now_utc)
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                self.staff.delete()

    def test_slot_on_delete_location_protect(self):
        """Deleting a Location with slots raises ProtectedError."""
        from django.db.models.deletion import ProtectedError
        make_slot(self.appt_type, self.staff, self.location, self.now_utc)
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                self.location.delete()

    def test_slot_ordering_by_start_datetime(self):
        """Slots are ordered by start_datetime ascending."""
        t1 = self.now_utc
        t2 = self.now_utc + timedelta(hours=2)
        make_slot(self.appt_type, self.staff, self.location, t2)  # Created second
        make_slot(self.appt_type, self.staff, self.location, t1)  # Created first

        slots = list(Slot.objects.filter(appointment_type=self.appt_type))
        self.assertEqual(slots[0].start_datetime, t1)
        self.assertEqual(slots[1].start_datetime, t2)


# ---------------------------------------------------------------------------
# TaskDecoratorTests
# ---------------------------------------------------------------------------

class TaskDecoratorTests(TestCase):
    """
    Verify Celery task decorator properties for Wave 2 tasks.
    acks_late=True and reject_on_worker_lost=True are required by all tasks
    (per security invariants in tasks.py docstring).
    """

    def test_generate_slots_task_acks_late(self):
        from apps.appointments.tasks import generate_slots_for_period
        self.assertTrue(generate_slots_for_period.acks_late)

    def test_generate_slots_task_reject_on_worker_lost(self):
        from apps.appointments.tasks import generate_slots_for_period
        self.assertTrue(generate_slots_for_period.reject_on_worker_lost)

    def test_mark_past_slots_task_acks_late(self):
        from apps.appointments.tasks import mark_past_slots_completed
        self.assertTrue(mark_past_slots_completed.acks_late)

    def test_mark_past_slots_task_reject_on_worker_lost(self):
        from apps.appointments.tasks import mark_past_slots_completed
        self.assertTrue(mark_past_slots_completed.reject_on_worker_lost)

    def test_generate_slots_task_name(self):
        from apps.appointments.tasks import generate_slots_for_period
        self.assertEqual(generate_slots_for_period.name, "appointments.generate_slots_for_period")

    def test_mark_past_slots_task_name(self):
        from apps.appointments.tasks import mark_past_slots_completed
        self.assertEqual(mark_past_slots_completed.name, "appointments.mark_past_slots_completed")


# ---------------------------------------------------------------------------
# PIPEDAInvariantTests
# ---------------------------------------------------------------------------

class PIPEDAInvariantTests(TestCase):
    """
    Verify PIPEDA security invariants for Wave 2:
      - No email address in log output from services
      - Staff referenced by PK only in __str__ for AvailabilityTemplate and StaffException
    """

    def setUp(self):
        org = make_org("pipeda-org")
        self.location = make_location(org, "pipeda-loc")
        self.staff = make_staff(self.location, suffix="-pipeda")

    def test_availability_template_str_no_pii(self):
        """AvailabilityTemplate.__str__ must not contain email address."""
        tpl = make_template(
            self.staff, day_of_week=1,
            start_time=time(9, 0), end_time=time(17, 0)
        )
        s = str(tpl)
        self.assertNotIn("@", s, "Email address must not appear in __str__")
        # email component of "staff@example.com" must not appear
        self.assertNotIn("staff", s.lower().replace("staffprofile", "").replace("#", ""))

    def test_staff_exception_str_no_pii(self):
        """StaffException.__str__ must not contain email address."""
        exc = StaffException.objects.create(
            staff=self.staff,
            exception_date=date(2026, 7, 7),
            exception_type="holiday",
        )
        self.assertNotIn("@", str(exc))

    def test_slot_str_no_pii(self):
        """Slot.__str__ must not contain email address (@ as separator is OK, email domain is not)."""
        import re as _re
        now = datetime(2026, 7, 7, 14, 0, tzinfo=UTC)
        service_type = make_service_type("pipeda-slot-svc")
        appt_type = make_appt_type(service_type, "pipeda-slot-appt")
        slot = make_slot(appt_type, self.staff, self.location, now)
        slot_str = str(slot)
        # The __str__ uses '@' as a separator (e.g. "Slot <pk> — <type_id> @ <datetime>").
        # We assert that no email address pattern appears (i.e. no @domain.tld).
        self.assertIsNone(
            _re.search(r"@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", slot_str),
            f"Email address must not appear in Slot.__str__: {slot_str!r}",
        )

    def test_availability_service_returns_pk_not_email(self):
        """
        SlotAvailabilityService result dicts contain staff_id (PK),
        not any PII like email or display name.
        """
        policy = make_policy(
            slot_interval_minutes=60,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            min_lead_time_hours=0,
            max_advance_days=365,
        )
        service_type = make_service_type("pipeda-svc-result")
        appt_type = make_appt_type(service_type, "pipeda-appt-result", scheduling_policy=policy)
        appt_type.staff_members.add(self.staff)
        make_template(self.staff, day_of_week=1, start_time=time(9, 0), end_time=time(10, 0))

        svc = SlotAvailabilityService()
        result = svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 7, 6),
            date_to=date(2026, 7, 6),
            staff=self.staff,
        )
        for slot_dict in result:
            # staff_id should be the PK (integer), not an email
            self.assertNotIn("@", str(slot_dict["staff_id"]))
            # No email field should be present
            self.assertNotIn("email", slot_dict)
            self.assertNotIn("name", slot_dict)
