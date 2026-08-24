"""
Appointments BB — Booking service tests.

Covers:
  CreateBookingHappyPathTests       — successful booking creation (auto/manual confirm)
  CreateBookingCapacityTests        — SlotFullError on capacity exceeded
  CreateBookingSuspendedCitizenTests — CitizenSuspendedError blocks booking
  CreateBookingFrequencyControlTests — FrequencyWindowError enforcement
  CreateBookingMaxActiveTests       — MaxActiveBookingsError enforcement
  ConfirmBookingTests               — PENDING → CONFIRMED transition
  CancelBookingTests                — cancellation with late-flag logic
  RescheduleBookingTests            — dual-slot swap, chain links, counter
  MarkNoShowTests                   — no_show flag, aggregate, thresholds
  CompleteBookingTests              — CONFIRMED → COMPLETED transition
  RejectBookingTests                — PENDING → REJECTED transition
  BookingAuditLogImmutabilityTests  — save() and delete() guards
  ClientNoShowRecordPropertyTests   — no_show_rate, one-per-citizen constraint
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from apps.appointments.services.booking import (
    CitizenSuspendedError,
    FrequencyWindowError,
    InvalidStatusTransitionError,
    MaxActiveBookingsError,
    RescheduleCountError,
    SlotFullError,
    cancel_booking,
    complete_booking,
    confirm_booking,
    create_booking,
    mark_no_show,
    reject_booking,
    reschedule_booking,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Test factories
# ---------------------------------------------------------------------------


def _make_user(email=None, is_staff=False):
    if email is None:
        email = f"user_{uuid.uuid4().hex[:8]}@example.com"
    return User.objects.create_user(
        email=email,
        password="testpass123!",
        is_staff=is_staff,
    )


def _make_org():
    from apps.appointments.models import Organization

    return Organization.objects.create(
        name_en=f"Test Org {uuid.uuid4().hex[:6]}",
        name_fr="Org de test",
        slug=f"test-org-{uuid.uuid4().hex[:6]}",
        organization_type="government_federal",
    )


def _make_location(org=None):
    from apps.appointments.models import Location

    if org is None:
        org = _make_org()
    return Location.objects.create(
        organization=org,
        name_en=f"Test Location {uuid.uuid4().hex[:4]}",
        name_fr="Lieu de test",
        slug=f"test-loc-{uuid.uuid4().hex[:6]}",
        timezone="America/Toronto",
        is_active=True,
    )


def _make_service_type(org=None):
    from apps.appointments.models import ServiceType

    return ServiceType.objects.create(
        name_en=f"Test Service {uuid.uuid4().hex[:4]}",
        name_fr="Service test",
        slug=f"test-svc-{uuid.uuid4().hex[:6]}",
        is_active=True,
    )


def _make_scheduling_policy(org=None, **overrides):
    """Create a SchedulingPolicy with permissive defaults for testing."""
    from apps.appointments.models import SchedulingPolicy

    if org is None:
        org = _make_org()
    defaults = {
        "name": f"Test Policy {uuid.uuid4().hex[:4]}",
        "slot_interval_minutes": 30,
        "buffer_before_minutes": 0,
        "buffer_after_minutes": 0,
        "min_lead_time_hours": 0,
        "max_advance_days": 365,
        "max_active_bookings_per_citizen": 10,
        "booking_frequency_days": 0,
        "cancellation_notice_hours": 24,
        "reschedule_notice_hours": 0,
        "max_reschedule_count": 3,
        "no_show_warning_threshold": 2,
        "no_show_suspension_threshold": 3,
        "waitlist_enabled": True,
        "waitlist_acceptance_window_hours": 2,
        "max_waitlist_per_slot": 10,
        "waitlist_notify_batch_size": 3,
    }
    defaults.update(overrides)
    return SchedulingPolicy.objects.create(**defaults)


def _make_appointment_type(
    service_type=None,
    policy=None,
    requires_staff_confirmation=False,
    capacity_per_slot=2,
):
    from apps.appointments.models import AppointmentType

    if service_type is None:
        service_type = _make_service_type()
    obj = AppointmentType.objects.create(
        service_type=service_type,
        name_en=f"Test Appt {uuid.uuid4().hex[:4]}",
        name_fr="RDV test",
        slug=f"test-appt-{uuid.uuid4().hex[:6]}",
        duration_minutes=30,
        capacity_per_slot=capacity_per_slot,
        is_active=True,
        requires_staff_confirmation=requires_staff_confirmation,
        mode="in_person",
    )
    if policy is not None:
        obj.scheduling_policy = policy
        obj.save(update_fields=["scheduling_policy"])
    return obj


def _make_staff_profile(location=None, appointment_type=None):
    from apps.appointments.models import StaffProfile

    user = _make_user(is_staff=True)
    if location is None:
        location = _make_location()
    sp = StaffProfile.objects.create(
        user=user,
        location=location,
        is_accepting_bookings=True,
    )
    if appointment_type is not None:
        sp.appointment_types.add(appointment_type)
    return sp


def _make_slot(
    appointment_type=None,
    staff=None,
    location=None,
    capacity=2,
    spaces_used=0,
    start_offset_days=2,
):
    from apps.appointments.models import Slot

    if location is None:
        location = _make_location()
    if appointment_type is None:
        appointment_type = _make_appointment_type(capacity_per_slot=capacity)
    if staff is None:
        staff = _make_staff_profile(location=location, appointment_type=appointment_type)
    now = timezone.now()
    start = now + timedelta(days=start_offset_days)
    end = start + timedelta(minutes=30)
    if spaces_used == 0:
        status = "available"
    elif spaces_used >= capacity:
        status = "full"
    else:
        status = "partial"
    return Slot.objects.create(
        appointment_type=appointment_type,
        staff=staff,
        location=location,
        start_datetime=start,
        end_datetime=end,
        effective_start=start - timedelta(minutes=5),
        effective_end=end + timedelta(minutes=5),
        capacity=capacity,
        spaces_used=spaces_used,
        status=status,
    )


def _make_booking(slot=None, citizen=None, status="confirmed", **kwargs):
    """Create a Booking directly, bypassing the service layer."""
    from apps.appointments.models import Booking

    if slot is None:
        slot = _make_slot()
    if citizen is None:
        citizen = _make_user()
    return Booking.objects.create(
        slot=slot,
        citizen=citizen,
        status=status,
        appointment_mode="in_person",
        booking_channel="online",
        language="en",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Create booking — happy path
# ---------------------------------------------------------------------------


class CreateBookingHappyPathTests(TestCase):
    def setUp(self):
        self.citizen = _make_user()
        self.staff_user = _make_user(is_staff=True)
        self.slot = _make_slot(capacity=2, spaces_used=0)

    def test_returns_booking_instance(self):
        from apps.appointments.models import Booking

        booking = create_booking(
            slot=self.slot,
            citizen=self.citizen,
            appointment_mode="in_person",
            form_responses={},
            actor=self.citizen,
        )
        self.assertIsInstance(booking, Booking)

    def test_auto_confirm_when_no_staff_confirmation_required(self):
        from apps.appointments.models import Booking

        # Default appointment type has requires_staff_confirmation=False
        booking = create_booking(
            slot=self.slot,
            citizen=self.citizen,
            appointment_mode="in_person",
            form_responses={},
            actor=self.citizen,
        )
        self.assertEqual(booking.status, Booking.STATUS_CONFIRMED)

    def test_pending_when_requires_staff_confirmation(self):
        from apps.appointments.models import Booking

        slot = _make_slot(
            appointment_type=_make_appointment_type(requires_staff_confirmation=True),
            capacity=2,
        )
        booking = create_booking(
            slot=slot,
            citizen=self.citizen,
            appointment_mode="in_person",
            form_responses={},
            actor=self.citizen,
        )
        self.assertEqual(booking.status, Booking.STATUS_PENDING)

    def test_increments_slot_spaces_used(self):
        create_booking(
            slot=self.slot,
            citizen=self.citizen,
            appointment_mode="in_person",
            form_responses={},
            actor=self.citizen,
        )
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.spaces_used, 1)

    def test_slot_status_partial_after_one_of_two(self):
        create_booking(
            slot=self.slot,
            citizen=self.citizen,
            appointment_mode="in_person",
            form_responses={},
            actor=self.citizen,
        )
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.status, "partial")

    def test_slot_status_full_after_capacity_reached(self):
        slot = _make_slot(capacity=1, spaces_used=0)
        create_booking(
            slot=slot,
            citizen=self.citizen,
            appointment_mode="in_person",
            form_responses={},
            actor=self.citizen,
        )
        slot.refresh_from_db()
        self.assertEqual(slot.status, "full")

    def test_audit_log_written(self):
        from apps.appointments.models import BookingAuditLog

        booking = create_booking(
            slot=self.slot,
            citizen=self.citizen,
            appointment_mode="in_person",
            form_responses={},
            actor=self.citizen,
        )
        self.assertTrue(
            BookingAuditLog.objects.filter(
                booking=booking, action=BookingAuditLog.ACTION_CREATED
            ).exists()
        )

    def test_consent_audit_log_written_when_provided(self):
        from apps.appointments.models import BookingAuditLog

        booking = create_booking(
            slot=self.slot,
            citizen=self.citizen,
            appointment_mode="in_person",
            form_responses={},
            actor=self.citizen,
            consent_recorded_at=timezone.now(),
            consent_version="v1.0",
        )
        self.assertTrue(
            BookingAuditLog.objects.filter(
                booking=booking, action=BookingAuditLog.ACTION_CONSENT_RECORDED
            ).exists()
        )

    def test_booking_channel_stored(self):
        booking = create_booking(
            slot=self.slot,
            citizen=self.citizen,
            appointment_mode="in_person",
            form_responses={},
            actor=self.citizen,
            booking_channel="staff_portal",
        )
        self.assertEqual(booking.booking_channel, "staff_portal")


# ---------------------------------------------------------------------------
# Create booking — capacity
# ---------------------------------------------------------------------------


class CreateBookingCapacityTests(TestCase):
    def setUp(self):
        self.citizen = _make_user()

    def test_raises_when_spaces_used_equals_capacity(self):
        slot = _make_slot(capacity=1, spaces_used=1)
        slot.status = "full"
        slot.save(update_fields=["status"])
        with self.assertRaises(SlotFullError):
            create_booking(
                slot=slot,
                citizen=self.citizen,
                appointment_mode="in_person",
                form_responses={},
                actor=self.citizen,
            )

    def test_raises_when_slot_status_full(self):
        slot = _make_slot(capacity=2, spaces_used=2)
        slot.status = "full"
        slot.save(update_fields=["status"])
        with self.assertRaises(SlotFullError):
            create_booking(
                slot=slot,
                citizen=self.citizen,
                appointment_mode="in_person",
                form_responses={},
                actor=self.citizen,
            )

    def test_raises_when_slot_blocked(self):
        slot = _make_slot(capacity=2, spaces_used=0)
        slot.status = "blocked"
        slot.save(update_fields=["status"])
        with self.assertRaises(SlotFullError):
            create_booking(
                slot=slot,
                citizen=self.citizen,
                appointment_mode="in_person",
                form_responses={},
                actor=self.citizen,
            )


# ---------------------------------------------------------------------------
# Create booking — suspended citizen
# ---------------------------------------------------------------------------


class CreateBookingSuspendedCitizenTests(TestCase):
    def setUp(self):
        self.citizen = _make_user()

    def test_suspended_citizen_cannot_book(self):
        from apps.appointments.models import ClientNoShowRecord

        ClientNoShowRecord.objects.create(
            citizen=self.citizen,
            is_suspended=True,
        )
        slot = _make_slot()
        with self.assertRaises(CitizenSuspendedError):
            create_booking(
                slot=slot,
                citizen=self.citizen,
                appointment_mode="in_person",
                form_responses={},
                actor=self.citizen,
            )

    def test_non_suspended_citizen_can_book(self):
        from apps.appointments.models import ClientNoShowRecord

        ClientNoShowRecord.objects.create(
            citizen=self.citizen,
            is_suspended=False,
        )
        slot = _make_slot()
        booking = create_booking(
            slot=slot,
            citizen=self.citizen,
            appointment_mode="in_person",
            form_responses={},
            actor=self.citizen,
        )
        self.assertIsNotNone(booking.pk)

    def test_citizen_without_record_can_book(self):
        slot = _make_slot()
        booking = create_booking(
            slot=slot,
            citizen=self.citizen,
            appointment_mode="in_person",
            form_responses={},
            actor=self.citizen,
        )
        self.assertIsNotNone(booking.pk)


# ---------------------------------------------------------------------------
# Create booking — frequency control
# ---------------------------------------------------------------------------


class CreateBookingFrequencyControlTests(TestCase):
    def setUp(self):
        self.citizen = _make_user()

    def test_frequency_control_blocks_citizen_with_recent_booking(self):
        org = _make_org()
        policy = _make_scheduling_policy(org=org, booking_frequency_days=7)
        service_type = _make_service_type(org=org)
        appt_type = _make_appointment_type(service_type=service_type, policy=policy)

        location = _make_location(org=org)
        slot1 = _make_slot(appointment_type=appt_type, location=location)
        # Create a recent confirmed booking for the same service type
        _make_booking(slot=slot1, citizen=self.citizen, status="confirmed")

        slot2 = _make_slot(appointment_type=appt_type, location=location)
        with self.assertRaises(FrequencyWindowError):
            create_booking(
                slot=slot2,
                citizen=self.citizen,
                appointment_mode="in_person",
                form_responses={},
                actor=self.citizen,
            )

    def test_frequency_control_zero_allows_booking(self):
        """booking_frequency_days=0 disables frequency control."""
        org = _make_org()
        policy = _make_scheduling_policy(org=org, booking_frequency_days=0)
        service_type = _make_service_type(org=org)
        appt_type = _make_appointment_type(service_type=service_type, policy=policy)
        location = _make_location(org=org)
        slot1 = _make_slot(appointment_type=appt_type, location=location)
        _make_booking(slot=slot1, citizen=self.citizen, status="confirmed")
        slot2 = _make_slot(appointment_type=appt_type, location=location)
        # Should not raise even with a recent booking
        booking = create_booking(
            slot=slot2,
            citizen=self.citizen,
            appointment_mode="in_person",
            form_responses={},
            actor=self.citizen,
        )
        self.assertIsNotNone(booking.pk)


# ---------------------------------------------------------------------------
# Create booking — max active
# ---------------------------------------------------------------------------


class CreateBookingMaxActiveTests(TestCase):
    def setUp(self):
        self.citizen = _make_user()

    def test_max_active_bookings_blocks_new_booking(self):
        org = _make_org()
        policy = _make_scheduling_policy(org=org, max_active_bookings_per_citizen=2)
        service_type = _make_service_type(org=org)
        appt_type = _make_appointment_type(service_type=service_type, policy=policy)
        location = _make_location(org=org)

        # Create 2 active bookings directly
        for _ in range(2):
            slot = _make_slot(appointment_type=appt_type, location=location)
            _make_booking(slot=slot, citizen=self.citizen, status="confirmed")

        new_slot = _make_slot(appointment_type=appt_type, location=location)
        with self.assertRaises(MaxActiveBookingsError):
            create_booking(
                slot=new_slot,
                citizen=self.citizen,
                appointment_mode="in_person",
                form_responses={},
                actor=self.citizen,
            )


# ---------------------------------------------------------------------------
# Confirm booking
# ---------------------------------------------------------------------------


class ConfirmBookingTests(TestCase):
    def setUp(self):
        self.staff = _make_user(is_staff=True)

    def test_confirms_pending_booking(self):
        from apps.appointments.models import Booking

        slot = _make_slot(appointment_type=_make_appointment_type(requires_staff_confirmation=True))
        citizen = _make_user()
        booking = _make_booking(slot=slot, citizen=citizen, status="pending")
        result = confirm_booking(booking=booking, actor=self.staff)
        self.assertEqual(result.status, Booking.STATUS_CONFIRMED)

    def test_writes_audit_log(self):
        from apps.appointments.models import BookingAuditLog

        slot = _make_slot()
        citizen = _make_user()
        booking = _make_booking(slot=slot, citizen=citizen, status="pending")
        confirm_booking(booking=booking, actor=self.staff)
        self.assertTrue(
            BookingAuditLog.objects.filter(
                booking=booking, action=BookingAuditLog.ACTION_CONFIRMED
            ).exists()
        )

    def test_raises_if_not_pending(self):
        slot = _make_slot()
        citizen = _make_user()
        booking = _make_booking(slot=slot, citizen=citizen, status="confirmed")
        with self.assertRaises(InvalidStatusTransitionError):
            confirm_booking(booking=booking, actor=self.staff)

    def test_raises_if_cancelled(self):
        slot = _make_slot()
        citizen = _make_user()
        booking = _make_booking(slot=slot, citizen=citizen, status="cancelled")
        with self.assertRaises(InvalidStatusTransitionError):
            confirm_booking(booking=booking, actor=self.staff)


# ---------------------------------------------------------------------------
# Cancel booking
# ---------------------------------------------------------------------------


class CancelBookingTests(TestCase):
    def setUp(self):
        self.citizen = _make_user()
        self.staff = _make_user(is_staff=True)

    def test_cancels_confirmed_booking(self):
        from apps.appointments.models import Booking

        slot = _make_slot(spaces_used=1)
        booking = _make_booking(slot=slot, citizen=self.citizen, status="confirmed")
        result = cancel_booking(booking=booking, actor=self.citizen, reason="Changed mind")
        self.assertEqual(result.status, Booking.STATUS_CANCELLED)

    def test_decrements_spaces_used_after_cancel(self):
        slot = _make_slot(spaces_used=1)
        booking = _make_booking(slot=slot, citizen=self.citizen, status="confirmed")
        cancel_booking(booking=booking, actor=self.citizen)
        slot.refresh_from_db()
        self.assertEqual(slot.spaces_used, 0)

    def test_late_cancellation_when_within_notice_window(self):
        """Slot starts in 12h, notice=24h → late cancellation."""
        from apps.appointments.models import Slot

        org = _make_org()
        policy = _make_scheduling_policy(org=org, cancellation_notice_hours=24)
        service_type = _make_service_type(org=org)
        appt_type = _make_appointment_type(service_type=service_type, policy=policy)
        location = _make_location(org=org)
        staff = _make_staff_profile(location=location, appointment_type=appt_type)

        # Slot starts in 12 hours
        now = timezone.now()
        start = now + timedelta(hours=12)
        slot = Slot.objects.create(
            appointment_type=appt_type,
            staff=staff,
            location=location,
            start_datetime=start,
            end_datetime=start + timedelta(minutes=30),
            effective_start=start - timedelta(minutes=5),
            effective_end=start + timedelta(minutes=35),
            capacity=2,
            spaces_used=1,
            status="partial",
        )
        booking = _make_booking(slot=slot, citizen=self.citizen, status="confirmed")
        result = cancel_booking(booking=booking, actor=self.citizen)
        self.assertTrue(result.late_cancellation)

    def test_not_late_when_outside_notice_window(self):
        """Slot starts in 48h, notice=24h → NOT late cancellation."""
        # Default slot is 2 days out, default policy notice is 24h
        slot = _make_slot(spaces_used=1)
        booking = _make_booking(slot=slot, citizen=self.citizen, status="confirmed")
        result = cancel_booking(booking=booking, actor=self.citizen)
        self.assertFalse(result.late_cancellation)

    def test_raises_if_already_cancelled(self):
        slot = _make_slot()
        booking = _make_booking(slot=slot, citizen=self.citizen, status="cancelled")
        with self.assertRaises(InvalidStatusTransitionError):
            cancel_booking(booking=booking, actor=self.citizen)

    def test_raises_if_completed(self):
        slot = _make_slot()
        booking = _make_booking(slot=slot, citizen=self.citizen, status="completed")
        with self.assertRaises(InvalidStatusTransitionError):
            cancel_booking(booking=booking, actor=self.citizen)

    def test_writes_citizen_audit_log(self):
        from apps.appointments.models import BookingAuditLog

        slot = _make_slot(spaces_used=1)
        booking = _make_booking(slot=slot, citizen=self.citizen, status="confirmed")
        cancel_booking(booking=booking, actor=self.citizen)
        self.assertTrue(
            BookingAuditLog.objects.filter(
                booking=booking, action=BookingAuditLog.ACTION_CANCELLED_CITIZEN
            ).exists()
        )

    def test_writes_staff_audit_log(self):
        from apps.appointments.models import BookingAuditLog

        slot = _make_slot(spaces_used=1)
        booking = _make_booking(slot=slot, citizen=self.citizen, status="confirmed")
        cancel_booking(booking=booking, actor=self.staff)
        self.assertTrue(
            BookingAuditLog.objects.filter(
                booking=booking, action=BookingAuditLog.ACTION_CANCELLED_STAFF
            ).exists()
        )

    def test_writes_system_audit_log_when_actor_none(self):
        from apps.appointments.models import BookingAuditLog

        slot = _make_slot(spaces_used=1)
        booking = _make_booking(slot=slot, citizen=self.citizen, status="confirmed")
        cancel_booking(booking=booking, actor=None)
        self.assertTrue(
            BookingAuditLog.objects.filter(
                booking=booking, action=BookingAuditLog.ACTION_CANCELLED_SYSTEM
            ).exists()
        )


# ---------------------------------------------------------------------------
# Reschedule booking
# ---------------------------------------------------------------------------


class RescheduleBookingTests(TestCase):
    def setUp(self):
        self.citizen = _make_user()
        self.staff = _make_user(is_staff=True)
        org = _make_org()
        policy = _make_scheduling_policy(org=org, max_reschedule_count=3, reschedule_notice_hours=0)
        service_type = _make_service_type(org=org)
        self.appt_type = _make_appointment_type(service_type=service_type, policy=policy)
        self.location = _make_location(org=org)
        self.old_slot = _make_slot(
            appointment_type=self.appt_type,
            location=self.location,
            spaces_used=1,
        )
        self.new_slot = _make_slot(
            appointment_type=self.appt_type,
            location=self.location,
            spaces_used=0,
        )
        self.booking = _make_booking(
            slot=self.old_slot,
            citizen=self.citizen,
            status="confirmed",
        )

    def test_creates_new_booking(self):
        from apps.appointments.models import Booking

        new_booking = reschedule_booking(
            booking=self.booking, new_slot=self.new_slot, actor=self.staff
        )
        self.assertIsInstance(new_booking, Booking)
        self.assertNotEqual(new_booking.pk, self.booking.pk)

    def test_old_booking_cancelled(self):
        from apps.appointments.models import Booking

        reschedule_booking(booking=self.booking, new_slot=self.new_slot, actor=self.staff)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.STATUS_CANCELLED)

    def test_old_booking_rescheduled_flag(self):
        reschedule_booking(booking=self.booking, new_slot=self.new_slot, actor=self.staff)
        self.booking.refresh_from_db()
        self.assertTrue(self.booking.rescheduled)

    def test_new_booking_links_to_old(self):
        new_booking = reschedule_booking(
            booking=self.booking, new_slot=self.new_slot, actor=self.staff
        )
        self.assertEqual(new_booking.rescheduled_from_id, self.booking.pk)

    def test_reschedule_count_incremented(self):
        new_booking = reschedule_booking(
            booking=self.booking, new_slot=self.new_slot, actor=self.staff
        )
        self.assertEqual(new_booking.reschedule_count, 1)

    def test_old_slot_spaces_decremented(self):
        reschedule_booking(booking=self.booking, new_slot=self.new_slot, actor=self.staff)
        self.old_slot.refresh_from_db()
        self.assertEqual(self.old_slot.spaces_used, 0)

    def test_new_slot_spaces_incremented(self):
        reschedule_booking(booking=self.booking, new_slot=self.new_slot, actor=self.staff)
        self.new_slot.refresh_from_db()
        self.assertEqual(self.new_slot.spaces_used, 1)

    def test_raises_if_not_confirmed(self):
        pending_booking = _make_booking(slot=self.old_slot, citizen=self.citizen, status="pending")
        with self.assertRaises(InvalidStatusTransitionError):
            reschedule_booking(booking=pending_booking, new_slot=self.new_slot, actor=self.staff)

    def test_raises_if_no_show(self):
        no_show_booking = _make_booking(
            slot=self.old_slot, citizen=self.citizen, status="confirmed", no_show=True
        )
        with self.assertRaises(InvalidStatusTransitionError):
            reschedule_booking(booking=no_show_booking, new_slot=self.new_slot, actor=self.staff)

    def test_raises_if_max_reschedule_count_reached(self):
        org = _make_org()
        policy = _make_scheduling_policy(org=org, max_reschedule_count=0, reschedule_notice_hours=0)
        service_type = _make_service_type(org=org)
        appt_type = _make_appointment_type(service_type=service_type, policy=policy)
        location = _make_location(org=org)
        slot1 = _make_slot(appointment_type=appt_type, location=location, spaces_used=1)
        slot2 = _make_slot(appointment_type=appt_type, location=location, spaces_used=0)
        booking = _make_booking(
            slot=slot1, citizen=self.citizen, status="confirmed", reschedule_count=0
        )
        with self.assertRaises(RescheduleCountError):
            reschedule_booking(booking=booking, new_slot=slot2, actor=self.staff)

    def test_raises_if_new_slot_full(self):
        full_slot = _make_slot(
            appointment_type=self.appt_type,
            location=self.location,
            capacity=1,
            spaces_used=1,
        )
        full_slot.status = "full"
        full_slot.save(update_fields=["status"])
        with self.assertRaises(SlotFullError):
            reschedule_booking(booking=self.booking, new_slot=full_slot, actor=self.staff)

    def test_audit_logs_written_on_both_bookings(self):
        from apps.appointments.models import BookingAuditLog

        new_booking = reschedule_booking(
            booking=self.booking, new_slot=self.new_slot, actor=self.staff
        )
        self.assertTrue(
            BookingAuditLog.objects.filter(
                booking=self.booking, action=BookingAuditLog.ACTION_RESCHEDULED
            ).exists()
        )
        self.assertTrue(
            BookingAuditLog.objects.filter(
                booking=new_booking, action=BookingAuditLog.ACTION_CREATED
            ).exists()
        )


# ---------------------------------------------------------------------------
# Mark no-show
# ---------------------------------------------------------------------------


class MarkNoShowTests(TestCase):
    def setUp(self):
        self.citizen = _make_user()
        self.staff = _make_user(is_staff=True)
        org = _make_org()
        policy = _make_scheduling_policy(
            org=org, no_show_warning_threshold=2, no_show_suspension_threshold=3
        )
        service_type = _make_service_type(org=org)
        appt_type = _make_appointment_type(service_type=service_type, policy=policy)
        location = _make_location(org=org)
        self.slot = _make_slot(appointment_type=appt_type, location=location)
        self.booking = _make_booking(slot=self.slot, citizen=self.citizen, status="confirmed")

    def test_sets_no_show_flag(self):
        mark_no_show(booking=self.booking, actor=self.staff)
        self.booking.refresh_from_db()
        self.assertTrue(self.booking.no_show)

    def test_increments_no_show_count(self):
        from apps.appointments.models import ClientNoShowRecord

        mark_no_show(booking=self.booking, actor=self.staff)
        record = ClientNoShowRecord.objects.get(citizen=self.citizen)
        self.assertEqual(record.no_show_count, 1)

    def test_status_unchanged_after_no_show(self):
        from apps.appointments.models import Booking

        mark_no_show(booking=self.booking, actor=self.staff)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.STATUS_CONFIRMED)

    def test_flags_at_warning_threshold(self):
        from apps.appointments.models import ClientNoShowRecord

        # Pre-create record with count just below warning (threshold=2, count=1)
        ClientNoShowRecord.objects.create(
            citizen=self.citizen, no_show_count=1, total_appointments=1
        )
        mark_no_show(booking=self.booking, actor=self.staff)
        record = ClientNoShowRecord.objects.get(citizen=self.citizen)
        self.assertTrue(record.is_flagged)

    def test_suspends_at_suspension_threshold(self):
        from apps.appointments.models import ClientNoShowRecord

        # Pre-create record with count at suspension_threshold - 1 (threshold=3, count=2)
        ClientNoShowRecord.objects.create(
            citizen=self.citizen, no_show_count=2, total_appointments=2
        )
        mark_no_show(booking=self.booking, actor=self.staff)
        record = ClientNoShowRecord.objects.get(citizen=self.citizen)
        self.assertTrue(record.is_suspended)

    def test_raises_if_not_confirmed(self):
        slot = _make_slot()
        pending_booking = _make_booking(slot=slot, citizen=self.citizen, status="pending")
        with self.assertRaises(InvalidStatusTransitionError):
            mark_no_show(booking=pending_booking, actor=self.staff)

    def test_raises_if_already_no_show(self):
        mark_no_show(booking=self.booking, actor=self.staff)
        self.booking.refresh_from_db()
        with self.assertRaises(InvalidStatusTransitionError):
            mark_no_show(booking=self.booking, actor=self.staff)

    def test_writes_audit_log(self):
        from apps.appointments.models import BookingAuditLog

        mark_no_show(booking=self.booking, actor=self.staff)
        self.assertTrue(
            BookingAuditLog.objects.filter(
                booking=self.booking, action=BookingAuditLog.ACTION_NO_SHOW_MARKED
            ).exists()
        )


# ---------------------------------------------------------------------------
# Complete booking
# ---------------------------------------------------------------------------


class CompleteBookingTests(TestCase):
    def setUp(self):
        self.citizen = _make_user()
        self.staff = _make_user(is_staff=True)

    def test_changes_status_to_completed(self):
        from apps.appointments.models import Booking

        slot = _make_slot()
        booking = _make_booking(slot=slot, citizen=self.citizen, status="confirmed")
        result = complete_booking(booking=booking, actor=self.staff)
        self.assertEqual(result.status, Booking.STATUS_COMPLETED)

    def test_raises_if_not_confirmed(self):
        slot = _make_slot()
        booking = _make_booking(slot=slot, citizen=self.citizen, status="pending")
        with self.assertRaises(InvalidStatusTransitionError):
            complete_booking(booking=booking, actor=self.staff)

    def test_increments_total_appointments(self):
        from apps.appointments.models import ClientNoShowRecord

        slot = _make_slot()
        booking = _make_booking(slot=slot, citizen=self.citizen, status="confirmed")
        complete_booking(booking=booking, actor=self.staff)
        record = ClientNoShowRecord.objects.get(citizen=self.citizen)
        self.assertEqual(record.total_appointments, 1)

    def test_writes_audit_log(self):
        from apps.appointments.models import BookingAuditLog

        slot = _make_slot()
        booking = _make_booking(slot=slot, citizen=self.citizen, status="confirmed")
        complete_booking(booking=booking)
        self.assertTrue(
            BookingAuditLog.objects.filter(
                booking=booking, action=BookingAuditLog.ACTION_COMPLETED
            ).exists()
        )


# ---------------------------------------------------------------------------
# Reject booking
# ---------------------------------------------------------------------------


class RejectBookingTests(TestCase):
    def setUp(self):
        self.citizen = _make_user()
        self.staff = _make_user(is_staff=True)

    def test_rejects_pending_booking(self):
        from apps.appointments.models import Booking

        slot = _make_slot(spaces_used=1)
        booking = _make_booking(slot=slot, citizen=self.citizen, status="pending")
        result = reject_booking(booking=booking, actor=self.staff, reason="Ineligible")
        self.assertEqual(result.status, Booking.STATUS_REJECTED)

    def test_decrements_slot_spaces_used(self):
        slot = _make_slot(spaces_used=1)
        booking = _make_booking(slot=slot, citizen=self.citizen, status="pending")
        reject_booking(booking=booking, actor=self.staff)
        slot.refresh_from_db()
        self.assertEqual(slot.spaces_used, 0)

    def test_raises_if_not_pending(self):
        slot = _make_slot()
        booking = _make_booking(slot=slot, citizen=self.citizen, status="confirmed")
        with self.assertRaises(InvalidStatusTransitionError):
            reject_booking(booking=booking, actor=self.staff)

    def test_writes_audit_log(self):
        from apps.appointments.models import BookingAuditLog

        slot = _make_slot(spaces_used=1)
        booking = _make_booking(slot=slot, citizen=self.citizen, status="pending")
        reject_booking(booking=booking, actor=self.staff)
        self.assertTrue(
            BookingAuditLog.objects.filter(
                booking=booking, action=BookingAuditLog.ACTION_REJECTED
            ).exists()
        )


# ---------------------------------------------------------------------------
# BookingAuditLog immutability
# ---------------------------------------------------------------------------


class BookingAuditLogImmutabilityTests(TestCase):
    def setUp(self):
        self.citizen = _make_user()
        self.slot = _make_slot()
        self.booking = _make_booking(slot=self.slot, citizen=self.citizen)

    def _make_log(self):
        from apps.appointments.models import BookingAuditLog

        return BookingAuditLog.objects.create(
            booking=self.booking,
            action=BookingAuditLog.ACTION_CREATED,
            actor_id="system",
        )

    def test_create_succeeds(self):
        log = self._make_log()
        self.assertIsNotNone(log.pk)

    def test_save_raises_on_existing_record(self):
        log = self._make_log()
        with self.assertRaises(ValueError):
            log.save()

    def test_delete_always_raises(self):
        log = self._make_log()
        with self.assertRaises(ValueError):
            log.delete()

    def test_detail_stores_and_retrieves_dict(self):
        from apps.appointments.models import BookingAuditLog

        log = BookingAuditLog.objects.create(
            booking=self.booking,
            action=BookingAuditLog.ACTION_CREATED,
            actor_id="system",
            detail={"slot_id": "abc123", "channel": "online"},
        )
        log.refresh_from_db()
        self.assertEqual(log.detail["slot_id"], "abc123")

    def test_actor_id_is_string(self):
        from apps.appointments.models import BookingAuditLog

        log = BookingAuditLog.objects.create(
            booking=self.booking,
            action=BookingAuditLog.ACTION_CREATED,
            actor_id=str(self.citizen.pk),
        )
        # actor_id is a CharField — must be a string, never a User FK
        self.assertIsInstance(log.actor_id, str)


# ---------------------------------------------------------------------------
# ClientNoShowRecord property tests
# ---------------------------------------------------------------------------


class ClientNoShowRecordPropertyTests(TestCase):
    def setUp(self):
        self.citizen = _make_user()

    def test_no_show_rate_zero_when_no_appointments(self):
        from apps.appointments.models import ClientNoShowRecord

        record = ClientNoShowRecord.objects.create(
            citizen=self.citizen, no_show_count=0, total_appointments=0
        )
        self.assertEqual(record.no_show_rate, 0.0)

    def test_no_show_rate_calculation(self):
        from apps.appointments.models import ClientNoShowRecord

        record = ClientNoShowRecord.objects.create(
            citizen=self.citizen, no_show_count=2, total_appointments=10
        )
        self.assertAlmostEqual(record.no_show_rate, 20.0)

    def test_one_record_per_citizen_constraint(self):
        from apps.appointments.models import ClientNoShowRecord

        ClientNoShowRecord.objects.create(citizen=self.citizen)
        with self.assertRaises(Exception):  # IntegrityError (OneToOneField)  # noqa: B017
            with transaction.atomic():
                ClientNoShowRecord.objects.create(citizen=self.citizen)
