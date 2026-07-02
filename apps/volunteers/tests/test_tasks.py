"""
Wave 3 — Volunteer Management BB: Celery task test suite.

Covers:
  send_shift_reminders_24h  — marks flag, idempotency, skips cancelled,
                              skips already-sent, only targets correct window
  send_shift_reminders_2h   — marks flag, idempotency, skips cancelled,
                              only targets correct window
  check_expiring_screenings   — fires signal for records expiring within 30 days,
                                excludes already-expired, excludes outside window
  check_expiring_certifications — same patterns as screening checks

Conventions:
  - Tasks are called directly as plain functions (not via .apply() or .delay())
    since we're testing business logic, not Celery routing.
  - send_email_notification is mocked at its import site in tasks.py so no
    real email delivery is attempted.
  - Signal assertions verify send_robust() is called; receiver errors are safe
    because signals use send_robust().
  - PIPEDA: log assertions contain PKs only; no volunteer PII in task log output.
"""
from __future__ import annotations

import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.volunteers.models import (
    Certification,
    Opportunity,
    Program,
    ScreeningRecord,
    Shift,
    ShiftBooking,
    VolunteerApplication,
    VolunteerProfile,
)
from apps.volunteers.tasks import (
    check_expiring_certifications,
    check_expiring_screenings,
    send_shift_reminders_24h,
    send_shift_reminders_2h,
)

User = get_user_model()

# ---------------------------------------------------------------------------
# Shared factory helpers
# ---------------------------------------------------------------------------

_counter = [0]


def _uid():
    _counter[0] += 1
    return _counter[0]


def _make_user(email=None, **kwargs):
    _counter[0] += 1
    email = email or f"task{_counter[0]}@example.gc.ca"
    return User.objects.create_user(email=email, password="testpass!", **kwargs)


def _make_program(**kwargs):
    n = _uid()
    defaults = dict(
        name_en=f"Program {n}",
        name_fr=f"Programme {n}",
        slug=f"tprog-{n}",
        cra_category="welfare",
    )
    defaults.update(kwargs)
    return Program.objects.create(**defaults)


def _make_opportunity(program, *, slug=None, status="published", **kwargs):
    n = _uid()
    defaults = dict(
        title_en=f"Opportunity {n}",
        title_fr=f"Opportunité {n}",
        slug=slug or f"topp-{n}",
        description_en="Desc",
        description_fr="Desc FR",
        program=program,
        status=status,
    )
    defaults.update(kwargs)
    return Opportunity.objects.create(**defaults)


def _make_profile(user):
    return VolunteerProfile.objects.create(user=user)


def _make_shift(opportunity, *, start_offset_hours, duration_hours=2, **kwargs):
    """Create a shift with start_datetime offset by `start_offset_hours` from now."""
    now = timezone.now()
    start = now + datetime.timedelta(hours=start_offset_hours)
    end = start + datetime.timedelta(hours=duration_hours)
    return Shift.objects.create(
        opportunity=opportunity,
        start_datetime=start,
        end_datetime=end,
        capacity=10,
        **kwargs,
    )


def _make_booking(shift, volunteer_profile, *, status=ShiftBooking.STATUS_CONFIRMED,
                  reminder_24h_sent=False, reminder_2h_sent=False):
    return ShiftBooking.objects.create(
        shift=shift,
        volunteer=volunteer_profile,
        status=status,
        reminder_24h_sent=reminder_24h_sent,
        reminder_2h_sent=reminder_2h_sent,
    )


# ---------------------------------------------------------------------------
# Base setup shared by reminder task tests
# ---------------------------------------------------------------------------

class ReminderTaskBaseTestCase(TestCase):
    """
    Shared fixtures for reminder task tests.

    Attributes:
        volunteer_user, volunteer_profile
        program, opportunity
    """

    def setUp(self):
        self.volunteer_user = _make_user("taskrvol@example.gc.ca")
        self.volunteer_profile = _make_profile(self.volunteer_user)
        self.program = _make_program(slug="task-prog")
        self.opportunity = _make_opportunity(self.program, slug="task-opp")
        VolunteerApplication.objects.create(
            volunteer=self.volunteer_profile,
            opportunity=self.opportunity,
            status=VolunteerApplication.STATUS_APPROVED,
        )


# ===========================================================================
# send_shift_reminders_24h task tests
# ===========================================================================

class ShiftReminderTask24hTests(ReminderTaskBaseTestCase):
    """Tests for send_shift_reminders_24h Celery task."""

    def setUp(self):
        super().setUp()
        # Shift starting ~24h from now (inside the 23h–25h window).
        self.shift = _make_shift(self.opportunity, start_offset_hours=24)
        self.booking = _make_booking(
            self.shift,
            self.volunteer_profile,
            reminder_24h_sent=False,
        )

    def test_send_shift_reminders_24h_marks_flag(self):
        """Task sets reminder_24h_sent=True on matching bookings."""
        with mock.patch("apps.volunteers.tasks.send_email_notification") as m:
            send_shift_reminders_24h()

        self.booking.refresh_from_db()
        self.assertTrue(self.booking.reminder_24h_sent)
        m.assert_called_once()

    def test_send_shift_reminders_24h_idempotent(self):
        """Running the task twice does not double-send (flag already True)."""
        with mock.patch("apps.volunteers.tasks.send_email_notification") as m:
            send_shift_reminders_24h()
            send_shift_reminders_24h()

        self.assertEqual(m.call_count, 1)

    def test_send_shift_reminders_24h_skips_cancelled_shift(self):
        """Task skips bookings whose shift is cancelled."""
        self.shift.is_cancelled = True
        self.shift.save(update_fields=["is_cancelled"])

        with mock.patch("apps.volunteers.tasks.send_email_notification") as m:
            send_shift_reminders_24h()

        m.assert_not_called()
        self.booking.refresh_from_db()
        self.assertFalse(self.booking.reminder_24h_sent)

    def test_send_shift_reminders_24h_skips_already_sent(self):
        """Task skips bookings where reminder_24h_sent is already True."""
        ShiftBooking.objects.filter(pk=self.booking.pk).update(reminder_24h_sent=True)

        with mock.patch("apps.volunteers.tasks.send_email_notification") as m:
            send_shift_reminders_24h()

        m.assert_not_called()

    def test_send_shift_reminders_24h_skips_outside_window(self):
        """Task ignores bookings for shifts outside the 23h–25h window."""
        # Shift starting in 48h — outside the 24h window.
        far_shift = _make_shift(self.opportunity, start_offset_hours=48)
        _make_booking(far_shift, self.volunteer_profile, reminder_24h_sent=False)

        with mock.patch("apps.volunteers.tasks.send_email_notification") as m:
            # Only count calls for the far-future shift.
            send_shift_reminders_24h()

        # The 24h booking (self.booking) is in range; the 48h one is not.
        # Total calls must not include the far shift.
        # (self.booking IS in range; so exactly 1 call total from self.booking.)
        self.assertEqual(m.call_count, 1)

    def test_send_shift_reminders_24h_skips_waitlisted_bookings(self):
        """Task only sends reminders for STATUS_CONFIRMED bookings."""
        ShiftBooking.objects.filter(pk=self.booking.pk).update(
            status=ShiftBooking.STATUS_WAITLISTED
        )
        with mock.patch("apps.volunteers.tasks.send_email_notification") as m:
            send_shift_reminders_24h()
        m.assert_not_called()

    def test_send_shift_reminders_24h_returns_counts(self):
        """Task returns a dict with 'sent' and 'skipped' counters."""
        with mock.patch("apps.volunteers.tasks.send_email_notification"):
            result = send_shift_reminders_24h()
        self.assertIn("sent", result)
        self.assertIn("skipped", result)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["skipped"], 0)


# ===========================================================================
# send_shift_reminders_2h task tests
# ===========================================================================

class ShiftReminderTask2hTests(ReminderTaskBaseTestCase):
    """Tests for send_shift_reminders_2h Celery task."""

    def setUp(self):
        super().setUp()
        # Shift starting ~2h from now (inside the 1h–3h window).
        self.shift = _make_shift(self.opportunity, start_offset_hours=2)
        self.booking = _make_booking(
            self.shift,
            self.volunteer_profile,
            reminder_2h_sent=False,
        )

    def test_send_shift_reminders_2h_marks_flag(self):
        """Task sets reminder_2h_sent=True on matching bookings."""
        with mock.patch("apps.volunteers.tasks.send_email_notification") as m:
            send_shift_reminders_2h()

        self.booking.refresh_from_db()
        self.assertTrue(self.booking.reminder_2h_sent)
        m.assert_called_once()

    def test_send_shift_reminders_2h_idempotent(self):
        """Running the task twice does not double-send."""
        with mock.patch("apps.volunteers.tasks.send_email_notification") as m:
            send_shift_reminders_2h()
            send_shift_reminders_2h()
        self.assertEqual(m.call_count, 1)

    def test_send_shift_reminders_2h_skips_cancelled_shift(self):
        """Task skips bookings whose shift is cancelled."""
        self.shift.is_cancelled = True
        self.shift.save(update_fields=["is_cancelled"])
        with mock.patch("apps.volunteers.tasks.send_email_notification") as m:
            send_shift_reminders_2h()
        m.assert_not_called()

    def test_send_shift_reminders_2h_skips_outside_window(self):
        """Task ignores shifts outside the 1h–3h window (e.g., 24h away)."""
        far_shift = _make_shift(self.opportunity, start_offset_hours=24)
        far_user = _make_user()
        far_profile = _make_profile(far_user)
        VolunteerApplication.objects.create(
            volunteer=far_profile,
            opportunity=self.opportunity,
            status=VolunteerApplication.STATUS_APPROVED,
        )
        _make_booking(far_shift, far_profile, reminder_2h_sent=False)

        with mock.patch("apps.volunteers.tasks.send_email_notification") as m:
            send_shift_reminders_2h()

        # Only self.booking (2h window) should trigger; far_shift should not.
        # Calls should be exactly 1 (for self.booking).
        self.assertEqual(m.call_count, 1)

    def test_send_shift_reminders_2h_returns_counts(self):
        """Task returns a dict with 'sent' and 'skipped' counters."""
        with mock.patch("apps.volunteers.tasks.send_email_notification"):
            result = send_shift_reminders_2h()
        self.assertIn("sent", result)
        self.assertEqual(result["sent"], 1)


# ===========================================================================
# check_expiring_screenings task tests
# ===========================================================================

class ExpiringScreeningTaskTests(TestCase):
    """Tests for check_expiring_screenings Celery task."""

    def setUp(self):
        self.coordinator = _make_user("screen_coord@example.gc.ca", is_staff=True)
        self.volunteer_user = _make_user("screen_vol@example.gc.ca")
        self.volunteer_profile = _make_profile(self.volunteer_user)

    def _make_screening_record(self, expires_days_from_now, check_type=ScreeningRecord.CHECK_TYPE_PRC):
        """Create a verified-clear ScreeningRecord expiring in `expires_days_from_now` days."""
        today = timezone.localtime(timezone.now()).date()
        return ScreeningRecord.objects.create(
            volunteer=self.volunteer_profile,
            check_type=check_type,
            completed_date=today - datetime.timedelta(days=30),
            expires_date=today + datetime.timedelta(days=expires_days_from_now),
            verified_clear=True,
            verified_by=self.coordinator,
            verified_at=timezone.now(),
        )

    def test_expiring_screenings_fires_signal(self):
        """Task fires screening_expiring signal for records expiring within 30 days."""
        self._make_screening_record(expires_days_from_now=15)

        with mock.patch(
            "apps.volunteers.tasks.screening_expiring.send_robust"
        ) as m:
            m.return_value = []  # No receivers registered in this test.
            check_expiring_screenings()

        m.assert_called_once()

    def test_expiring_screenings_excludes_already_expired(self):
        """Task does not fire signals for records that have already expired."""
        today = timezone.localtime(timezone.now()).date()
        ScreeningRecord.objects.create(
            volunteer=self.volunteer_profile,
            check_type=ScreeningRecord.CHECK_TYPE_REFERENCE,
            completed_date=today - datetime.timedelta(days=60),
            expires_date=today - datetime.timedelta(days=1),  # expired yesterday
            verified_clear=True,
            verified_by=self.coordinator,
            verified_at=timezone.now(),
        )

        with mock.patch(
            "apps.volunteers.tasks.screening_expiring.send_robust"
        ) as m:
            check_expiring_screenings()

        m.assert_not_called()

    def test_expiring_screenings_excludes_outside_window(self):
        """Task ignores records expiring more than 30 days from now."""
        self._make_screening_record(expires_days_from_now=45)

        with mock.patch(
            "apps.volunteers.tasks.screening_expiring.send_robust"
        ) as m:
            check_expiring_screenings()

        m.assert_not_called()

    def test_expiring_screenings_returns_counts(self):
        """Task returns a dict with 'fired' and 'receiver_errors' counters."""
        self._make_screening_record(expires_days_from_now=10)

        with mock.patch(
            "apps.volunteers.tasks.screening_expiring.send_robust",
            return_value=[],
        ):
            result = check_expiring_screenings()

        self.assertIn("fired", result)
        self.assertIn("receiver_errors", result)
        self.assertEqual(result["fired"], 1)
        self.assertEqual(result["receiver_errors"], 0)

    def test_expiring_screenings_multiple_records_all_fired(self):
        """When multiple records are expiring, a signal is fired for each."""
        # Two volunteers with distinct check types to avoid unique constraint.
        user_a = _make_user()
        profile_a = _make_profile(user_a)
        user_b = _make_user()
        profile_b = _make_profile(user_b)
        today = timezone.localtime(timezone.now()).date()

        for profile in [profile_a, profile_b]:
            ScreeningRecord.objects.create(
                volunteer=profile,
                check_type=ScreeningRecord.CHECK_TYPE_PRC,
                completed_date=today - datetime.timedelta(days=30),
                expires_date=today + datetime.timedelta(days=20),
                verified_clear=True,
                verified_by=self.coordinator,
                verified_at=timezone.now(),
            )

        with mock.patch(
            "apps.volunteers.tasks.screening_expiring.send_robust",
            return_value=[],
        ) as m:
            result = check_expiring_screenings()

        self.assertEqual(m.call_count, 2)
        self.assertEqual(result["fired"], 2)

    def test_expiring_screenings_receiver_error_counted(self):
        """Task counts but does not raise on failing receivers (send_robust semantics)."""
        self._make_screening_record(expires_days_from_now=5)

        failing_exception = Exception("Notification service down")
        with mock.patch(
            "apps.volunteers.tasks.screening_expiring.send_robust",
            return_value=[("some_receiver", failing_exception)],
        ):
            result = check_expiring_screenings()

        self.assertEqual(result["receiver_errors"], 1)


# ===========================================================================
# check_expiring_certifications task tests
# ===========================================================================

class ExpiringCertificationsTaskTests(TestCase):
    """Tests for check_expiring_certifications Celery task."""

    def setUp(self):
        self.coordinator = _make_user("cert_coord@example.gc.ca", is_staff=True)
        self.volunteer_user = _make_user("cert_vol@example.gc.ca")
        self.volunteer_profile = _make_profile(self.volunteer_user)

    def _make_certification(self, expires_days_from_now,
                            cert_type=Certification.CERT_TYPE_FIRST_AID):
        today = timezone.localtime(timezone.now()).date()
        return Certification.objects.create(
            volunteer=self.volunteer_profile,
            cert_type=cert_type,
            issued_date=today - datetime.timedelta(days=365),
            expires_date=today + datetime.timedelta(days=expires_days_from_now),
        )

    def test_expiring_certifications_fires_signal(self):
        """Task fires certification_expiring signal for certs expiring within 30 days."""
        self._make_certification(expires_days_from_now=10)

        with mock.patch(
            "apps.volunteers.tasks.certification_expiring.send_robust"
        ) as m:
            m.return_value = []
            check_expiring_certifications()

        m.assert_called_once()

    def test_expiring_certifications_excludes_already_expired(self):
        """Task does not fire signals for certs that have already expired."""
        today = timezone.localtime(timezone.now()).date()
        Certification.objects.create(
            volunteer=self.volunteer_profile,
            cert_type=Certification.CERT_TYPE_CPR,
            issued_date=today - datetime.timedelta(days=400),
            expires_date=today - datetime.timedelta(days=1),
        )

        with mock.patch(
            "apps.volunteers.tasks.certification_expiring.send_robust"
        ) as m:
            check_expiring_certifications()

        m.assert_not_called()

    def test_expiring_certifications_excludes_outside_window(self):
        """Task ignores certs expiring more than 30 days from now."""
        self._make_certification(expires_days_from_now=60)

        with mock.patch(
            "apps.volunteers.tasks.certification_expiring.send_robust"
        ) as m:
            check_expiring_certifications()

        m.assert_not_called()

    def test_expiring_certifications_returns_counts(self):
        """Task returns a dict with 'fired' and 'receiver_errors' counters."""
        self._make_certification(expires_days_from_now=7)

        with mock.patch(
            "apps.volunteers.tasks.certification_expiring.send_robust",
            return_value=[],
        ):
            result = check_expiring_certifications()

        self.assertIn("fired", result)
        self.assertIn("receiver_errors", result)
        self.assertEqual(result["fired"], 1)

    def test_expiring_certifications_no_expiry_date_excluded(self):
        """Certifications with no expiry date (expires_date=None) are excluded."""
        today = timezone.localtime(timezone.now()).date()
        Certification.objects.create(
            volunteer=self.volunteer_profile,
            cert_type=Certification.CERT_TYPE_WHMIS,
            issued_date=today - datetime.timedelta(days=100),
            expires_date=None,  # no expiry
        )

        with mock.patch(
            "apps.volunteers.tasks.certification_expiring.send_robust"
        ) as m:
            check_expiring_certifications()

        m.assert_not_called()
