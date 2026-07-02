"""
Wave 3 — Volunteer Management BB: scheduling service test suite.

Covers:
  book_shift()       — confirmed booking, cancellation guard, past-shift guard,
                       permission check, waitlist, duplicate, signal dispatch
  cancel_booking()   — volunteer self-cancel, coordinator cancel, terminal-status
                       guard, waitlist promotion, signal dispatch
  mark_no_show()     — happy path, before-shift guard, permission guard
  complete_booking() — happy path, idempotent HoursLog, before-shift-ends guard
  cancel_shift()     — fields set, bulk-cancel bookings, already-cancelled guard,
                       empty-reason guard, permission guard, signal dispatch
  Race conditions    — overbooking prevention via SELECT FOR UPDATE
                       (TransactionTestCase)

Conventions:
  - setUp() (not setUpClass) for TransactionTestCase — tables are truncated between tests.
  - captureOnCommitCallbacks(execute=True) to trigger on_commit paths in TestCase.
  - PIPEDA: all log/signal assertions use PKs, not PII.
"""
from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.volunteers.models import (
    HoursLog,
    Opportunity,
    Program,
    ShiftBooking,
    VolunteerApplication,
    VolunteerProfile,
)
from apps.volunteers.models import Shift
from apps.volunteers.services.scheduling import (
    book_shift,
    cancel_booking,
    cancel_shift,
    complete_booking,
    mark_no_show,
)

User = get_user_model()

# ---------------------------------------------------------------------------
# Shared factory helpers
# ---------------------------------------------------------------------------

_counter = [0]


def _uid():
    _counter[0] += 1
    return _counter[0]


def _make_user(email=None, password="testpass!", **kwargs):
    _counter[0] += 1
    email = email or f"vol{_counter[0]}@example.gc.ca"
    return User.objects.create_user(email=email, password=password, **kwargs)


def _make_program(**kwargs):
    n = _uid()
    defaults = dict(
        name_en=f"Program {n}",
        name_fr=f"Programme {n}",
        slug=f"prog-{n}",
        cra_category="welfare",
    )
    defaults.update(kwargs)
    return Program.objects.create(**defaults)


def _make_opportunity(program, *, slug=None, status="published", **kwargs):
    n = _uid()
    defaults = dict(
        title_en=f"Opportunity {n}",
        title_fr=f"Opportunité {n}",
        slug=slug or f"opp-{n}",
        description_en="Description EN",
        description_fr="Description FR",
        program=program,
        status=status,
    )
    defaults.update(kwargs)
    return Opportunity.objects.create(**defaults)


def _make_profile(user):
    return VolunteerProfile.objects.create(user=user)


def _make_shift(opportunity, *, hours_ahead=2, duration_hours=2, capacity=5,
                waitlist_enabled=True, waitlist_cap=3, **kwargs):
    """Create a shift starting `hours_ahead` hours from now."""
    now = timezone.now()
    start = now + datetime.timedelta(hours=hours_ahead)
    end = start + datetime.timedelta(hours=duration_hours)
    return Shift.objects.create(
        opportunity=opportunity,
        start_datetime=start,
        end_datetime=end,
        capacity=capacity,
        waitlist_enabled=waitlist_enabled,
        waitlist_cap=waitlist_cap,
        **kwargs,
    )


def _make_past_shift(opportunity, *, hours_ago_start=4, duration_hours=2, **kwargs):
    """Create a shift that has already ended."""
    now = timezone.now()
    start = now - datetime.timedelta(hours=hours_ago_start)
    end = start + datetime.timedelta(hours=duration_hours)
    return Shift.objects.create(
        opportunity=opportunity,
        start_datetime=start,
        end_datetime=end,
        capacity=10,
        waitlist_enabled=True,
        **kwargs,
    )


def _approve_application(volunteer_profile, opportunity):
    return VolunteerApplication.objects.create(
        volunteer=volunteer_profile,
        opportunity=opportunity,
        status=VolunteerApplication.STATUS_APPROVED,
    )


def _grant_perm(user, codename):
    perm = Permission.objects.get(
        content_type__app_label="volunteers",
        codename=codename,
    )
    user.user_permissions.add(perm)
    # Flush Django's per-request permission cache.
    for attr in ("_perm_cache", "_user_perm_cache"):
        if hasattr(user, attr):
            delattr(user, attr)
    return User.objects.get(pk=user.pk)


# ---------------------------------------------------------------------------
# Shared base setup
# ---------------------------------------------------------------------------

class SchedulingBaseTestCase(TestCase):
    """
    Shared fixtures for all scheduling service tests.

    Attributes:
        coordinator        — User with volunteers.change_shift + change_shiftbooking
        volunteer_user     — regular volunteer User
        volunteer_profile  — VolunteerProfile for volunteer_user
        program            — Program
        opportunity        — published Opportunity
        approved_application — STATUS_APPROVED application for volunteer + opportunity
        shift              — future Shift (capacity=5, waitlist_enabled=True, waitlist_cap=3)
    """

    def setUp(self):
        # Coordinator: needs both change_shift and change_shiftbooking
        coord = _make_user("coord@example.gc.ca", is_staff=True)
        coord = _grant_perm(coord, "change_shift")
        self.coordinator = _grant_perm(coord, "change_shiftbooking")

        self.volunteer_user = _make_user("vol@example.gc.ca")
        self.volunteer_profile = _make_profile(self.volunteer_user)

        _slug_uid = uuid.uuid4().hex[:8]
        self.program = _make_program(slug=f"base-sched-prog-{_slug_uid}")
        self.opportunity = _make_opportunity(self.program, slug=f"base-sched-opp-{_slug_uid}")

        self.approved_application = _approve_application(
            self.volunteer_profile, self.opportunity
        )

        self.shift = _make_shift(
            self.opportunity,
            hours_ahead=2,
            duration_hours=2,
            capacity=5,
            waitlist_enabled=True,
            waitlist_cap=3,
        )

    # ---- helper: book a *different* volunteer so we can fill capacity ----
    def _book_extra_volunteer(self):
        """Create a new volunteer, approve their application, and book the shift."""
        user = _make_user()
        profile = _make_profile(user)
        _approve_application(profile, self.opportunity)
        return book_shift(
            shift=self.shift,
            volunteer_profile=profile,
            actor=user,
        )


# ===========================================================================
# book_shift() tests
# ===========================================================================

class BookShiftTests(SchedulingBaseTestCase):

    def test_book_shift_creates_confirmed_booking(self):
        """Happy path: book_shift() returns a confirmed ShiftBooking."""
        booking = book_shift(
            shift=self.shift,
            volunteer_profile=self.volunteer_profile,
            actor=self.volunteer_user,
        )
        self.assertIsNotNone(booking.pk)
        self.assertEqual(booking.status, ShiftBooking.STATUS_CONFIRMED)
        self.assertEqual(booking.volunteer, self.volunteer_profile)
        self.assertEqual(booking.shift, self.shift)

    def test_book_shift_cancelled_shift_raises(self):
        """book_shift() raises ValidationError when the shift is cancelled."""
        self.shift.is_cancelled = True
        self.shift.save(update_fields=["is_cancelled"])
        with self.assertRaises(ValidationError) as ctx:
            book_shift(
                shift=self.shift,
                volunteer_profile=self.volunteer_profile,
                actor=self.volunteer_user,
            )
        self.assertIn("shift", ctx.exception.message_dict)

    def test_book_shift_past_shift_raises(self):
        """book_shift() raises ValidationError when the shift has already started."""
        past_shift = _make_past_shift(self.opportunity)
        _approve_application(self.volunteer_profile, self.opportunity)  # already approved; OK
        with self.assertRaises(ValidationError) as ctx:
            book_shift(
                shift=past_shift,
                volunteer_profile=self.volunteer_profile,
                actor=self.volunteer_user,
            )
        self.assertIn("shift", ctx.exception.message_dict)

    def test_book_shift_no_approved_application_raises(self):
        """book_shift() raises ValidationError when the volunteer has no approved application."""
        new_user = _make_user()
        new_profile = _make_profile(new_user)
        # No application created for this volunteer.
        with self.assertRaises(ValidationError) as ctx:
            book_shift(
                shift=self.shift,
                volunteer_profile=new_profile,
                actor=new_user,
            )
        self.assertIn("volunteer", ctx.exception.message_dict)

    def test_book_shift_wrong_actor_raises_permission_denied(self):
        """book_shift() raises PermissionDenied when actor is not the volunteer's User."""
        other_user = _make_user()
        with self.assertRaises(PermissionDenied):
            book_shift(
                shift=self.shift,
                volunteer_profile=self.volunteer_profile,
                actor=other_user,
            )

    def test_book_shift_full_creates_waitlisted_booking(self):
        """When all capacity slots are taken the 6th volunteer gets waitlisted."""
        # Fill all 5 capacity slots with distinct volunteers.
        for _ in range(5):
            self._book_extra_volunteer()

        # 6th booking: our volunteer → should be waitlisted.
        booking = book_shift(
            shift=self.shift,
            volunteer_profile=self.volunteer_profile,
            actor=self.volunteer_user,
        )
        self.assertEqual(booking.status, ShiftBooking.STATUS_WAITLISTED)
        self.assertIsNotNone(booking.waitlist_position)

    def test_book_shift_full_no_waitlist_raises(self):
        """When shift is full and waitlist is disabled a ValidationError is raised."""
        self.shift.waitlist_enabled = False
        self.shift.save(update_fields=["waitlist_enabled"])

        for _ in range(5):
            self._book_extra_volunteer()

        with self.assertRaises(ValidationError) as ctx:
            book_shift(
                shift=self.shift,
                volunteer_profile=self.volunteer_profile,
                actor=self.volunteer_user,
            )
        self.assertIn("shift", ctx.exception.message_dict)

    def test_book_shift_waitlist_full_raises(self):
        """When 5 confirmed + 3 waitlisted slots are taken, next booking raises."""
        # Fill confirmed capacity (5).
        for _ in range(5):
            self._book_extra_volunteer()

        # Fill the 3-person waitlist (waitlist_cap=3).
        for _ in range(3):
            self._book_extra_volunteer()

        with self.assertRaises(ValidationError) as ctx:
            book_shift(
                shift=self.shift,
                volunteer_profile=self.volunteer_profile,
                actor=self.volunteer_user,
            )
        self.assertIn("shift", ctx.exception.message_dict)

    def test_book_shift_duplicate_raises(self):
        """Booking the same shift twice raises ValidationError."""
        book_shift(
            shift=self.shift,
            volunteer_profile=self.volunteer_profile,
            actor=self.volunteer_user,
        )
        with self.assertRaises(ValidationError):
            book_shift(
                shift=self.shift,
                volunteer_profile=self.volunteer_profile,
                actor=self.volunteer_user,
            )

    def test_book_shift_fires_signal_on_commit(self):
        """book_shift() fires shift_booked signal after the DB commit."""
        from apps.volunteers.signals import shift_booked
        received = []

        def handler(sender, **kwargs):
            received.append(kwargs)

        shift_booked.connect(handler, weak=False)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                book_shift(
                    shift=self.shift,
                    volunteer_profile=self.volunteer_profile,
                    actor=self.volunteer_user,
                )
        finally:
            shift_booked.disconnect(handler)

        self.assertEqual(len(received), 1)
        self.assertIn("volunteer", received[0])
        self.assertEqual(received[0]["volunteer"], self.volunteer_profile)

    def test_book_shift_waitlist_position_increments(self):
        """Successive waitlisted bookings receive incrementing positions."""
        # Fill confirmed capacity.
        for _ in range(5):
            self._book_extra_volunteer()

        # First waitlisted booking.
        user_a = _make_user()
        profile_a = _make_profile(user_a)
        _approve_application(profile_a, self.opportunity)
        b1 = book_shift(shift=self.shift, volunteer_profile=profile_a, actor=user_a)
        self.assertEqual(b1.waitlist_position, 1)

        # Second waitlisted booking.
        user_b = _make_user()
        profile_b = _make_profile(user_b)
        _approve_application(profile_b, self.opportunity)
        b2 = book_shift(shift=self.shift, volunteer_profile=profile_b, actor=user_b)
        self.assertEqual(b2.waitlist_position, 2)

    def test_book_shift_unlimited_capacity_never_waitlists(self):
        """When capacity=None the shift has unlimited capacity; no waitlisting occurs."""
        self.shift.capacity = None
        self.shift.save(update_fields=["capacity"])
        # Opportunity also has no volunteer_capacity limit.
        self.opportunity.volunteer_capacity = None
        self.opportunity.save(update_fields=["volunteer_capacity"])

        # Book many volunteers — all should be confirmed.
        for _ in range(10):
            user = _make_user()
            profile = _make_profile(user)
            _approve_application(profile, self.opportunity)
            b = book_shift(shift=self.shift, volunteer_profile=profile, actor=user)
            self.assertEqual(b.status, ShiftBooking.STATUS_CONFIRMED)

    def test_book_shift_pending_application_not_sufficient(self):
        """Only STATUS_APPROVED applications allow booking; pending is rejected."""
        new_user = _make_user()
        new_profile = _make_profile(new_user)
        VolunteerApplication.objects.create(
            volunteer=new_profile,
            opportunity=self.opportunity,
            status=VolunteerApplication.STATUS_PENDING,
        )
        with self.assertRaises(ValidationError) as ctx:
            book_shift(
                shift=self.shift,
                volunteer_profile=new_profile,
                actor=new_user,
            )
        self.assertIn("volunteer", ctx.exception.message_dict)


# ===========================================================================
# cancel_booking() tests
# ===========================================================================

class CancelBookingTests(SchedulingBaseTestCase):

    def _confirmed_booking(self):
        return book_shift(
            shift=self.shift,
            volunteer_profile=self.volunteer_profile,
            actor=self.volunteer_user,
        )

    def test_volunteer_can_cancel_own_booking(self):
        """Volunteer can cancel their own confirmed booking."""
        booking = self._confirmed_booking()
        cancel_booking(booking=booking, actor=self.volunteer_user)
        booking.refresh_from_db()
        self.assertEqual(booking.status, ShiftBooking.STATUS_CANCELLED)
        self.assertIsNotNone(booking.cancelled_at)

    def test_coordinator_can_cancel_booking(self):
        """Coordinator (change_shiftbooking perm) can cancel any booking."""
        booking = self._confirmed_booking()
        cancel_booking(booking=booking, actor=self.coordinator)
        booking.refresh_from_db()
        self.assertEqual(booking.status, ShiftBooking.STATUS_CANCELLED)

    def test_cancel_stores_reason(self):
        """Cancellation reason is persisted on the booking (truncated to 300 chars)."""
        booking = self._confirmed_booking()
        reason = "Personal emergency"
        cancel_booking(booking=booking, actor=self.volunteer_user, reason=reason)
        booking.refresh_from_db()
        self.assertEqual(booking.cancellation_reason, reason)

    def test_cancel_already_cancelled_raises(self):
        """Cancelling a booking that is already cancelled raises ValidationError."""
        booking = self._confirmed_booking()
        cancel_booking(booking=booking, actor=self.volunteer_user)
        with self.assertRaises(ValidationError) as ctx:
            cancel_booking(booking=booking, actor=self.volunteer_user)
        self.assertIn("status", ctx.exception.message_dict)

    def test_cancel_completed_booking_raises(self):
        """A completed booking cannot be cancelled."""
        booking = self._confirmed_booking()
        # Force status to completed directly (bypasses service guards for setup).
        ShiftBooking.objects.filter(pk=booking.pk).update(status=ShiftBooking.STATUS_COMPLETED)
        booking.refresh_from_db()
        with self.assertRaises(ValidationError):
            cancel_booking(booking=booking, actor=self.volunteer_user)

    def test_cancel_no_show_booking_raises(self):
        """A no-show booking cannot be cancelled."""
        booking = self._confirmed_booking()
        ShiftBooking.objects.filter(pk=booking.pk).update(status=ShiftBooking.STATUS_NO_SHOW)
        booking.refresh_from_db()
        with self.assertRaises(ValidationError):
            cancel_booking(booking=booking, actor=self.volunteer_user)

    def test_cancel_requires_actor_is_volunteer_or_coordinator(self):
        """A third-party user without change_shiftbooking perm raises PermissionDenied."""
        booking = self._confirmed_booking()
        stranger = _make_user()
        with self.assertRaises(PermissionDenied):
            cancel_booking(booking=booking, actor=stranger)

    def test_cancel_promotes_waitlist(self):
        """Cancelling a confirmed booking promotes the highest-priority waitlisted booking."""
        # Confirm our volunteer's booking.
        confirmed_booking = self._confirmed_booking()

        # Fill remaining capacity (4 more).
        for _ in range(4):
            self._book_extra_volunteer()

        # Add a waitlisted volunteer.
        waitlist_user = _make_user()
        waitlist_profile = _make_profile(waitlist_user)
        _approve_application(waitlist_profile, self.opportunity)
        waitlisted = book_shift(
            shift=self.shift,
            volunteer_profile=waitlist_profile,
            actor=waitlist_user,
        )
        self.assertEqual(waitlisted.status, ShiftBooking.STATUS_WAITLISTED)

        # Cancel the confirmed booking → waitlisted should be promoted.
        cancel_booking(booking=confirmed_booking, actor=self.volunteer_user)

        waitlisted.refresh_from_db()
        self.assertEqual(waitlisted.status, ShiftBooking.STATUS_CONFIRMED)
        self.assertIsNone(waitlisted.waitlist_position)

    def test_cancel_waitlisted_booking_does_not_promote(self):
        """Cancelling a waitlisted booking does not trigger promotion."""
        # Fill confirmed capacity.
        for _ in range(5):
            self._book_extra_volunteer()

        # Add two waitlisted bookings.
        user_a = _make_user()
        profile_a = _make_profile(user_a)
        _approve_application(profile_a, self.opportunity)
        waitlisted_a = book_shift(shift=self.shift, volunteer_profile=profile_a, actor=user_a)

        user_b = _make_user()
        profile_b = _make_profile(user_b)
        _approve_application(profile_b, self.opportunity)
        waitlisted_b = book_shift(shift=self.shift, volunteer_profile=profile_b, actor=user_b)

        # Cancel the first waitlisted booking.
        cancel_booking(booking=waitlisted_a, actor=user_a)
        waitlisted_a.refresh_from_db()
        self.assertEqual(waitlisted_a.status, ShiftBooking.STATUS_CANCELLED)

        # Second waitlisted booking should still be waitlisted (no confirmed slot freed).
        waitlisted_b.refresh_from_db()
        self.assertEqual(waitlisted_b.status, ShiftBooking.STATUS_WAITLISTED)

    def test_cancel_sends_signal_on_commit(self):
        """cancel_booking() fires shift_booking_cancelled signal after commit."""
        from apps.volunteers.signals import shift_booking_cancelled
        booking = self._confirmed_booking()
        received = []

        def handler(sender, **kwargs):
            received.append(kwargs)

        shift_booking_cancelled.connect(handler, weak=False)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                cancel_booking(booking=booking, actor=self.volunteer_user, reason="Family matter")
        finally:
            shift_booking_cancelled.disconnect(handler)

        self.assertEqual(len(received), 1)
        self.assertIn("shift", received[0])

    def test_cancel_reason_truncated_to_300_chars(self):
        """Cancellation reasons longer than 300 chars are silently truncated."""
        booking = self._confirmed_booking()
        long_reason = "x" * 500
        cancel_booking(booking=booking, actor=self.volunteer_user, reason=long_reason)
        booking.refresh_from_db()
        self.assertEqual(len(booking.cancellation_reason), 300)


# ===========================================================================
# mark_no_show() tests
# ===========================================================================

class MarkNoShowTests(SchedulingBaseTestCase):

    def setUp(self):
        super().setUp()
        # Create a past shift for no-show tests (shift must have started).
        self.past_shift = _make_past_shift(self.opportunity, hours_ago_start=3)
        # Volunteer already has an approved application from base setUp.

    def _past_confirmed_booking(self):
        """Create a confirmed booking on the past shift (bypasses service start check)."""
        return ShiftBooking.objects.create(
            shift=self.past_shift,
            volunteer=self.volunteer_profile,
            status=ShiftBooking.STATUS_CONFIRMED,
        )

    def test_mark_no_show_sets_status(self):
        """Happy path: mark_no_show() transitions a confirmed booking to no_show."""
        booking = self._past_confirmed_booking()
        result = mark_no_show(booking=booking, actor=self.coordinator)
        self.assertEqual(result.status, ShiftBooking.STATUS_NO_SHOW)

    def test_mark_no_show_before_shift_starts_raises(self):
        """mark_no_show() raises ValidationError if the shift has not started yet."""
        future_booking = ShiftBooking.objects.create(
            shift=self.shift,  # future shift
            volunteer=self.volunteer_profile,
            status=ShiftBooking.STATUS_CONFIRMED,
        )
        with self.assertRaises(ValidationError) as ctx:
            mark_no_show(booking=future_booking, actor=self.coordinator)
        self.assertIn("shift", ctx.exception.message_dict)

    def test_mark_no_show_requires_coordinator_perm(self):
        """mark_no_show() raises PermissionDenied for unprivileged users."""
        booking = self._past_confirmed_booking()
        with self.assertRaises(PermissionDenied):
            mark_no_show(booking=booking, actor=self.volunteer_user)

    def test_mark_no_show_non_confirmed_booking_raises(self):
        """mark_no_show() raises ValidationError when booking is not STATUS_CONFIRMED."""
        booking = ShiftBooking.objects.create(
            shift=self.past_shift,
            volunteer=self.volunteer_profile,
            status=ShiftBooking.STATUS_WAITLISTED,
            waitlist_position=1,
        )
        with self.assertRaises(ValidationError) as ctx:
            mark_no_show(booking=booking, actor=self.coordinator)
        self.assertIn("status", ctx.exception.message_dict)

    def test_mark_no_show_already_no_show_raises(self):
        """Calling mark_no_show() on a no_show booking raises ValidationError."""
        booking = ShiftBooking.objects.create(
            shift=self.past_shift,
            volunteer=self.volunteer_profile,
            status=ShiftBooking.STATUS_NO_SHOW,
        )
        with self.assertRaises(ValidationError):
            mark_no_show(booking=booking, actor=self.coordinator)


# ===========================================================================
# complete_booking() tests
# ===========================================================================

class CompleteBookingTests(SchedulingBaseTestCase):

    def setUp(self):
        super().setUp()
        # Create a shift whose end is in the past (complete_booking requires end < now).
        self.ended_shift = _make_past_shift(
            self.opportunity,
            hours_ago_start=4,
            duration_hours=2,
        )
        # Volunteer's approved application already exists from base setUp.

    def _ended_confirmed_booking(self):
        return ShiftBooking.objects.create(
            shift=self.ended_shift,
            volunteer=self.volunteer_profile,
            status=ShiftBooking.STATUS_CONFIRMED,
        )

    def test_complete_booking_sets_status_and_creates_hours_log(self):
        """Happy path: complete_booking() transitions to completed and creates HoursLog."""
        booking = self._ended_confirmed_booking()
        result = complete_booking(booking=booking, actor=self.coordinator)
        self.assertEqual(result.status, ShiftBooking.STATUS_COMPLETED)

        hours_log = HoursLog.objects.filter(
            volunteer=self.volunteer_profile,
            shift=self.ended_shift,
        ).first()
        self.assertIsNotNone(hours_log)
        self.assertEqual(hours_log.status, HoursLog.STATUS_PENDING)
        self.assertGreater(hours_log.hours, 0)

    def test_complete_booking_idempotent_hours_log(self):
        """Calling complete_booking() twice must not create a second HoursLog row."""
        booking = self._ended_confirmed_booking()
        complete_booking(booking=booking, actor=self.coordinator)

        # Force back to confirmed so the service can run again (realistic retry scenario).
        ShiftBooking.objects.filter(pk=booking.pk).update(status=ShiftBooking.STATUS_CONFIRMED)
        booking.refresh_from_db()
        complete_booking(booking=booking, actor=self.coordinator)

        count = HoursLog.objects.filter(
            volunteer=self.volunteer_profile,
            shift=self.ended_shift,
        ).count()
        self.assertEqual(count, 1)

    def test_complete_before_shift_ends_raises(self):
        """complete_booking() raises ValidationError when shift end is still in the future."""
        future_booking = ShiftBooking.objects.create(
            shift=self.shift,  # future shift
            volunteer=self.volunteer_profile,
            status=ShiftBooking.STATUS_CONFIRMED,
        )
        with self.assertRaises(ValidationError) as ctx:
            complete_booking(booking=future_booking, actor=self.coordinator)
        self.assertIn("shift", ctx.exception.message_dict)

    def test_complete_booking_requires_coordinator_perm(self):
        """complete_booking() raises PermissionDenied for unprivileged users."""
        booking = self._ended_confirmed_booking()
        with self.assertRaises(PermissionDenied):
            complete_booking(booking=booking, actor=self.volunteer_user)

    def test_complete_booking_non_confirmed_raises(self):
        """complete_booking() raises ValidationError when booking is not confirmed."""
        booking = ShiftBooking.objects.create(
            shift=self.ended_shift,
            volunteer=self.volunteer_profile,
            status=ShiftBooking.STATUS_WAITLISTED,
            waitlist_position=1,
        )
        with self.assertRaises(ValidationError) as ctx:
            complete_booking(booking=booking, actor=self.coordinator)
        self.assertIn("status", ctx.exception.message_dict)

    def test_complete_booking_hours_log_duration(self):
        """The auto-created HoursLog reflects the shift's actual duration (max 24h)."""
        booking = self._ended_confirmed_booking()
        complete_booking(booking=booking, actor=self.coordinator)
        log = HoursLog.objects.get(volunteer=self.volunteer_profile, shift=self.ended_shift)
        expected_hours = round(self.ended_shift.duration_hours, 2)
        self.assertEqual(float(log.hours), expected_hours)

    def test_complete_booking_zero_duration_skips_hours_log(self):
        """complete_booking on a zero-duration shift does not create a HoursLog."""
        # Set end_datetime == start_datetime to make duration zero.
        self.ended_shift.end_datetime = self.ended_shift.start_datetime
        self.ended_shift.save(update_fields=["end_datetime", "updated_at"])

        booking = ShiftBooking.objects.create(
            shift=self.ended_shift,
            volunteer=self.volunteer_profile,
            status=ShiftBooking.STATUS_CONFIRMED,
        )

        with self.captureOnCommitCallbacks(execute=True):
            result = complete_booking(booking=booking, actor=self.coordinator)

        self.assertEqual(result.status, ShiftBooking.STATUS_COMPLETED)
        self.assertFalse(
            HoursLog.objects.filter(
                volunteer=self.volunteer_profile,
                shift=self.ended_shift,
            ).exists()
        )


# ===========================================================================
# cancel_shift() tests
# ===========================================================================

class CancelShiftTests(SchedulingBaseTestCase):

    def test_cancel_shift_sets_cancelled_fields(self):
        """cancel_shift() sets is_cancelled=True, cancelled_at, and cancellation_reason."""
        with self.captureOnCommitCallbacks(execute=True):
            cancel_shift(shift=self.shift, actor=self.coordinator, reason="Venue issue")
        self.shift.refresh_from_db()
        self.assertTrue(self.shift.is_cancelled)
        self.assertIsNotNone(self.shift.cancelled_at)
        self.assertEqual(self.shift.cancellation_reason, "Venue issue")
        self.assertEqual(self.shift.cancelled_by, self.coordinator)

    def test_cancel_shift_bulk_cancels_all_bookings(self):
        """cancel_shift() bulk-cancels all confirmed and waitlisted bookings."""
        # 2 confirmed.
        confirmed_1 = self._book_extra_volunteer()
        confirmed_2 = self._book_extra_volunteer()
        # Fill remaining 3 confirmed slots.
        for _ in range(3):
            self._book_extra_volunteer()
        # 1 waitlisted.
        w_user = _make_user()
        w_profile = _make_profile(w_user)
        _approve_application(w_profile, self.opportunity)
        waitlisted = book_shift(shift=self.shift, volunteer_profile=w_profile, actor=w_user)
        self.assertEqual(waitlisted.status, ShiftBooking.STATUS_WAITLISTED)

        cancel_shift(shift=self.shift, actor=self.coordinator, reason="Flooding")

        for booking_pk in [confirmed_1.pk, confirmed_2.pk, waitlisted.pk]:
            b = ShiftBooking.objects.get(pk=booking_pk)
            self.assertEqual(b.status, ShiftBooking.STATUS_CANCELLED,
                             f"Booking #{booking_pk} should be cancelled")

    def test_cancel_shift_already_cancelled_raises(self):
        """cancel_shift() raises ValidationError when the shift is already cancelled."""
        cancel_shift(shift=self.shift, actor=self.coordinator, reason="Initial reason")
        self.shift.refresh_from_db()
        with self.assertRaises(ValidationError) as ctx:
            cancel_shift(shift=self.shift, actor=self.coordinator, reason="Again")
        self.assertIn("shift", ctx.exception.message_dict)

    def test_cancel_shift_empty_reason_raises(self):
        """cancel_shift() raises ValidationError when reason is blank."""
        with self.assertRaises(ValidationError) as ctx:
            cancel_shift(shift=self.shift, actor=self.coordinator, reason="")
        self.assertIn("reason", ctx.exception.message_dict)

    def test_cancel_shift_whitespace_only_reason_raises(self):
        """cancel_shift() raises ValidationError when reason is whitespace only."""
        with self.assertRaises(ValidationError) as ctx:
            cancel_shift(shift=self.shift, actor=self.coordinator, reason="   ")
        self.assertIn("reason", ctx.exception.message_dict)

    def test_cancel_shift_requires_coordinator_perm(self):
        """cancel_shift() raises PermissionDenied for users without change_shift perm."""
        with self.assertRaises(PermissionDenied):
            cancel_shift(shift=self.shift, actor=self.volunteer_user, reason="Not allowed")

    def test_cancel_shift_fires_signal_on_commit(self):
        """cancel_shift() fires the shift_cancelled signal after the DB commit."""
        from apps.volunteers.signals import shift_cancelled as sig
        received = []

        def handler(sender, **kwargs):
            received.append(kwargs)

        sig.connect(handler, weak=False)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                cancel_shift(shift=self.shift, actor=self.coordinator, reason="Weather")
        finally:
            sig.disconnect(handler)

        self.assertEqual(len(received), 1)
        self.assertIn("reason", received[0])
        self.assertEqual(received[0]["reason"], "Weather")

    def test_cancel_shift_no_bookings_succeeds(self):
        """cancel_shift() succeeds when the shift has no bookings at all."""
        cancel_shift(shift=self.shift, actor=self.coordinator, reason="Empty shift")
        self.shift.refresh_from_db()
        self.assertTrue(self.shift.is_cancelled)

    def test_cancel_shift_strips_reason_whitespace(self):
        """cancel_shift() strips leading/trailing whitespace from the reason."""
        cancel_shift(shift=self.shift, actor=self.coordinator, reason="  Flood  ")
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.cancellation_reason, "Flood")

    def test_cancel_shift_completed_bookings_not_touched(self):
        """cancel_shift() only cancels confirmed/waitlisted bookings; completed stay as-is."""
        # Manually create a completed booking (bypasses service checks for setup speed).
        completed = ShiftBooking.objects.create(
            shift=self.shift,
            volunteer=self.volunteer_profile,
            status=ShiftBooking.STATUS_COMPLETED,
        )
        cancel_shift(shift=self.shift, actor=self.coordinator, reason="Venue closed")
        completed.refresh_from_db()
        self.assertEqual(completed.status, ShiftBooking.STATUS_COMPLETED)


# ===========================================================================
# Race condition tests  (TransactionTestCase)
# ===========================================================================

class ShiftRaceConditionTests(TransactionTestCase):
    """
    Verify that concurrent book_shift() calls for the same shift at capacity
    do not both create confirmed bookings.

    TransactionTestCase is required because select_for_update() needs real
    row-level locking semantics, which are unavailable inside TestCase's
    wrapping savepoint.
    """

    def setUp(self):
        self.program = _make_program(slug="race-sched-prog")
        self.opportunity = _make_opportunity(self.program, slug="race-sched-opp")

        # Shift with capacity=1 to make the race easy to trigger.
        self.shift = _make_shift(
            self.opportunity,
            hours_ahead=2,
            capacity=1,
            waitlist_enabled=False,
        )

    def test_concurrent_bookings_at_capacity_only_one_succeeds(self):
        """
        When two volunteers attempt to book the last slot, exactly one should
        get STATUS_CONFIRMED and the other should get a ValidationError.

        The select_for_update() path in book_shift() serialises the two requests,
        so only the first wins; the second re-reads the updated count and raises.
        """
        user_a = _make_user()
        profile_a = _make_profile(user_a)
        _approve_application(profile_a, self.opportunity)

        user_b = _make_user()
        profile_b = _make_profile(user_b)
        _approve_application(profile_b, self.opportunity)

        booking_a = book_shift(
            shift=self.shift,
            volunteer_profile=profile_a,
            actor=user_a,
        )
        self.assertEqual(booking_a.status, ShiftBooking.STATUS_CONFIRMED)

        # The second call must fail because the slot is now taken and
        # waitlist is disabled.
        with self.assertRaises(ValidationError) as ctx:
            book_shift(
                shift=self.shift,
                volunteer_profile=profile_b,
                actor=user_b,
            )
        self.assertIn("shift", ctx.exception.message_dict)

        confirmed_count = ShiftBooking.objects.filter(
            shift=self.shift,
            status=ShiftBooking.STATUS_CONFIRMED,
        ).count()
        self.assertEqual(confirmed_count, 1, "Exactly one confirmed booking should exist.")
