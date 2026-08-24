"""
Appointments BB — Signal + receiver tests.

Tests:
  BookingCreatedSignalTests     — appt_booking_created fires on create_booking()
  BookingConfirmedSignalTests   — appt_booking_confirmed fires on confirm_booking()
  BookingCancelledSignalTests   — appt_booking_cancelled fires on cancel_booking()
  BookingRescheduledSignalTests — appt_booking_rescheduled fires on reschedule()
  NoShowSignalTests             — appt_no_show_marked fires on mark_no_show()
  ReceiverConnectionTests       — all receivers connected after app ready
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

User = get_user_model()


# ---------------------------------------------------------------------------
# Shared factories
# ---------------------------------------------------------------------------


def _make_user(is_staff=False):
    return User.objects.create_user(
        email=f"user_{uuid.uuid4().hex[:8]}@example.com",
        password="testpass123!",
        is_staff=is_staff,
    )


def _make_confirmed_booking():
    """Create a confirmed booking with a slot 2 days from now."""
    from apps.appointments.models import (
        AppointmentType,
        Booking,
        Location,
        Organization,
        ServiceType,
        Slot,
        StaffProfile,
    )

    org = Organization.objects.create(
        name_en="Sig Org",
        name_fr="Org Sig",
        slug=f"sig-org-{uuid.uuid4().hex[:6]}",
        organization_type="government_federal",
    )
    location = Location.objects.create(
        organization=org,
        name_en="Sig Loc",
        name_fr="Loc Sig",
        slug=f"sig-loc-{uuid.uuid4().hex[:6]}",
        timezone="UTC",
        is_active=True,
    )
    service_type = ServiceType.objects.create(
        name_en="Sig Svc",
        name_fr="Svc Sig",
        slug=f"sig-svc-{uuid.uuid4().hex[:6]}",
        is_active=True,
    )
    appt_type = AppointmentType.objects.create(
        service_type=service_type,
        name_en="Sig Appt",
        name_fr="RDV Sig",
        slug=f"sig-appt-{uuid.uuid4().hex[:6]}",
        duration_minutes=30,
        capacity_per_slot=2,
        is_active=True,
        requires_staff_confirmation=False,
        mode="in_person",
    )
    staff_user = _make_user(is_staff=True)
    staff = StaffProfile.objects.create(
        user=staff_user,
        location=location,
        is_accepting_bookings=True,
    )
    staff.appointment_types.add(appt_type)

    now = timezone.now()
    start = now + timedelta(days=2)
    slot = Slot.objects.create(
        appointment_type=appt_type,
        staff=staff,
        location=location,
        start_datetime=start,
        end_datetime=start + timedelta(minutes=30),
        effective_start=start - timedelta(minutes=5),
        effective_end=start + timedelta(minutes=35),
        capacity=2,
        spaces_used=0,
        status="available",
    )
    citizen = _make_user()
    booking = Booking.objects.create(
        slot=slot,
        citizen=citizen,
        status="confirmed",
        appointment_mode="in_person",
        booking_channel="online",
        language="en",
    )
    return booking, citizen, slot, staff_user


# ---------------------------------------------------------------------------
# appt_booking_created
# ---------------------------------------------------------------------------


class BookingCreatedSignalTests(TestCase):
    def test_signal_fired_after_create_booking(self):
        """appt_booking_created is fired via on_commit after create_booking()."""
        from apps.appointments.models import (
            AppointmentType,
            Location,
            Organization,
            ServiceType,
            Slot,
            StaffProfile,
        )
        from apps.appointments.services.booking import create_booking
        from apps.appointments.signals import appt_booking_created

        citizen = _make_user()
        staff_user = _make_user(is_staff=True)
        org = Organization.objects.create(
            name_en="CBOrg",
            name_fr="Org CB",
            slug=f"cb-org-{uuid.uuid4().hex[:6]}",
            organization_type="government_federal",
        )
        location = Location.objects.create(
            organization=org,
            name_en="CBLoc",
            name_fr="Loc CB",
            slug=f"cb-loc-{uuid.uuid4().hex[:6]}",
            timezone="UTC",
            is_active=True,
        )
        service_type = ServiceType.objects.create(
            name_en="CBSvc",
            name_fr="Svc CB",
            slug=f"cb-svc-{uuid.uuid4().hex[:6]}",
            is_active=True,
        )
        appt_type = AppointmentType.objects.create(
            service_type=service_type,
            name_en="CBAppt",
            name_fr="RDV CB",
            slug=f"cb-appt-{uuid.uuid4().hex[:6]}",
            duration_minutes=30,
            capacity_per_slot=2,
            is_active=True,
            requires_staff_confirmation=False,
            mode="in_person",
        )
        staff = StaffProfile.objects.create(
            user=staff_user,
            location=location,
            is_accepting_bookings=True,
        )
        staff.appointment_types.add(appt_type)

        now = timezone.now()
        start = now + timedelta(days=2)
        slot = Slot.objects.create(
            appointment_type=appt_type,
            staff=staff,
            location=location,
            start_datetime=start,
            end_datetime=start + timedelta(minutes=30),
            effective_start=start - timedelta(minutes=5),
            effective_end=start + timedelta(minutes=35),
            capacity=2,
            spaces_used=0,
            status="available",
        )

        received = []

        def handler(sender, **kwargs):
            received.append(kwargs)

        appt_booking_created.connect(handler, dispatch_uid="test_created_handler")
        try:
            with self.captureOnCommitCallbacks(execute=True):
                create_booking(
                    slot=slot,
                    citizen=citizen,
                    appointment_mode="in_person",
                    form_responses={},
                    actor=citizen,
                )
            self.assertTrue(len(received) >= 1, "appt_booking_created signal not fired")
            self.assertIn("booking_id", received[0])
        finally:
            appt_booking_created.disconnect(dispatch_uid="test_created_handler")


# ---------------------------------------------------------------------------
# appt_booking_confirmed
# ---------------------------------------------------------------------------


class BookingConfirmedSignalTests(TestCase):
    def test_signal_fired_on_confirm(self):
        from apps.appointments.models import Booking
        from apps.appointments.services.booking import confirm_booking
        from apps.appointments.signals import appt_booking_confirmed

        booking, citizen, slot, staff_user = _make_confirmed_booking()
        # Reset to pending so confirm_booking() is valid
        booking.status = Booking.STATUS_PENDING
        booking.save(update_fields=["status"])

        received = []

        def handler(sender, **kwargs):
            received.append(kwargs)

        appt_booking_confirmed.connect(handler, dispatch_uid="test_confirmed_handler")
        try:
            staff = _make_user(is_staff=True)
            with self.captureOnCommitCallbacks(execute=True):
                confirm_booking(booking=booking, actor=staff)
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0]["booking_id"], str(booking.pk))
        finally:
            appt_booking_confirmed.disconnect(dispatch_uid="test_confirmed_handler")


# ---------------------------------------------------------------------------
# appt_booking_cancelled
# ---------------------------------------------------------------------------


class BookingCancelledSignalTests(TestCase):
    def test_signal_fired_on_cancel(self):
        from apps.appointments.services.booking import cancel_booking
        from apps.appointments.signals import appt_booking_cancelled

        booking, citizen, slot, _ = _make_confirmed_booking()
        # Set spaces_used so cancel doesn't go below 0
        slot.spaces_used = 1
        slot.save(update_fields=["spaces_used"])

        received = []

        def handler(sender, **kwargs):
            received.append(kwargs)

        appt_booking_cancelled.connect(handler, dispatch_uid="test_cancelled_handler")
        try:
            with self.captureOnCommitCallbacks(execute=True):
                cancel_booking(booking=booking, actor=citizen)
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0]["booking_id"], str(booking.pk))
        finally:
            appt_booking_cancelled.disconnect(dispatch_uid="test_cancelled_handler")


# ---------------------------------------------------------------------------
# appt_booking_rescheduled
# ---------------------------------------------------------------------------


class BookingRescheduledSignalTests(TestCase):
    def test_signal_fired_on_reschedule(self):
        from apps.appointments.models import (
            AppointmentType,
            Location,
            Organization,
            SchedulingPolicy,
            ServiceType,
            Slot,
            StaffProfile,
        )
        from apps.appointments.services.booking import reschedule_booking
        from apps.appointments.signals import appt_booking_rescheduled

        org = Organization.objects.create(
            name_en="RSOrg",
            name_fr="Org RS",
            slug=f"rs-org-{uuid.uuid4().hex[:6]}",
            organization_type="government_federal",
        )
        location = Location.objects.create(
            organization=org,
            name_en="RSLoc",
            name_fr="Loc RS",
            slug=f"rs-loc-{uuid.uuid4().hex[:6]}",
            timezone="UTC",
            is_active=True,
        )
        policy = SchedulingPolicy.objects.create(
            name=f"RS Policy {uuid.uuid4().hex[:4]}",
            max_reschedule_count=3,
            reschedule_notice_hours=0,
            cancellation_notice_hours=24,
        )
        service_type = ServiceType.objects.create(
            name_en="RSSvc",
            name_fr="Svc RS",
            slug=f"rs-svc-{uuid.uuid4().hex[:6]}",
            is_active=True,
        )
        appt_type = AppointmentType.objects.create(
            service_type=service_type,
            name_en="RSAppt",
            name_fr="RDV RS",
            slug=f"rs-appt-{uuid.uuid4().hex[:6]}",
            duration_minutes=30,
            capacity_per_slot=2,
            is_active=True,
            requires_staff_confirmation=False,
            mode="in_person",
            scheduling_policy=policy,
        )
        staff_user = _make_user(is_staff=True)
        staff = StaffProfile.objects.create(
            user=staff_user,
            location=location,
            is_accepting_bookings=True,
        )
        staff.appointment_types.add(appt_type)

        now = timezone.now()
        start1 = now + timedelta(days=2)
        old_slot = Slot.objects.create(
            appointment_type=appt_type,
            staff=staff,
            location=location,
            start_datetime=start1,
            end_datetime=start1 + timedelta(minutes=30),
            effective_start=start1 - timedelta(minutes=5),
            effective_end=start1 + timedelta(minutes=35),
            capacity=2,
            spaces_used=1,
            status="partial",
        )
        start2 = now + timedelta(days=3)
        new_slot = Slot.objects.create(
            appointment_type=appt_type,
            staff=staff,
            location=location,
            start_datetime=start2,
            end_datetime=start2 + timedelta(minutes=30),
            effective_start=start2 - timedelta(minutes=5),
            effective_end=start2 + timedelta(minutes=35),
            capacity=2,
            spaces_used=0,
            status="available",
        )

        from apps.appointments.models import Booking

        citizen = _make_user()
        booking = Booking.objects.create(
            slot=old_slot,
            citizen=citizen,
            status="confirmed",
            appointment_mode="in_person",
            booking_channel="online",
            language="en",
        )

        received = []

        def handler(sender, **kwargs):
            received.append(kwargs)

        appt_booking_rescheduled.connect(handler, dispatch_uid="test_rescheduled_handler")
        try:
            actor = _make_user(is_staff=True)
            with self.captureOnCommitCallbacks(execute=True):
                reschedule_booking(booking=booking, new_slot=new_slot, actor=actor)
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0]["old_booking_id"], str(booking.pk))
            self.assertIn("new_booking_id", received[0])
        finally:
            appt_booking_rescheduled.disconnect(dispatch_uid="test_rescheduled_handler")


# ---------------------------------------------------------------------------
# appt_no_show_marked
# ---------------------------------------------------------------------------


class NoShowSignalTests(TestCase):
    def test_signal_fired_on_no_show(self):
        from apps.appointments.services.booking import mark_no_show
        from apps.appointments.signals import appt_no_show_marked

        booking, citizen, slot, _ = _make_confirmed_booking()
        staff = _make_user(is_staff=True)

        received = []

        def handler(sender, **kwargs):
            received.append(kwargs)

        appt_no_show_marked.connect(handler, dispatch_uid="test_noshow_handler")
        try:
            with self.captureOnCommitCallbacks(execute=True):
                mark_no_show(booking=booking, actor=staff)
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0]["booking_id"], str(booking.pk))
            self.assertEqual(received[0]["citizen_id"], str(citizen.pk))
        finally:
            appt_no_show_marked.disconnect(dispatch_uid="test_noshow_handler")


# ---------------------------------------------------------------------------
# Receiver connection verification
# ---------------------------------------------------------------------------


class ReceiverConnectionTests(TestCase):
    def test_all_receivers_connected(self):
        """Verify all Wave 3 signals have at least one receiver connected."""
        from apps.appointments.signals import (
            appt_booking_cancelled,
            appt_booking_completed,
            appt_booking_confirmed,
            appt_booking_created,
            appt_booking_rejected,
            appt_no_show_marked,
        )

        signals_to_check = [
            ("appt_booking_created", appt_booking_created),
            ("appt_booking_confirmed", appt_booking_confirmed),
            ("appt_booking_cancelled", appt_booking_cancelled),
            ("appt_booking_rejected", appt_booking_rejected),
            ("appt_booking_completed", appt_booking_completed),
            ("appt_no_show_marked", appt_no_show_marked),
        ]
        for name, signal in signals_to_check:
            receivers = signal.receivers
            self.assertGreater(
                len(receivers),
                0,
                f"Signal {name} has no connected receivers — "
                "check that AppointmentsConfig.ready() calls connect_receivers()",
            )
