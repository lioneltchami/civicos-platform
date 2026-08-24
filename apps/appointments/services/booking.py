"""
Appointments BB — Booking lifecycle service.

Functions:
  create_booking      — reserve a slot for a citizen; enforce all policy gates
  confirm_booking     — staff confirms a pending booking
  cancel_booking      — citizen or staff cancels a booking
  reschedule_booking  — create new booking; cancel old (Cal.com rescheduling pattern)
  mark_no_show        — staff marks a confirmed booking as a no-show
  complete_booking    — mark a booking as completed
  reject_booking      — staff rejects a pending booking

Custom exceptions:
  BookingError            — base class for all booking errors
  SlotFullError           — slot has no available spaces
  CitizenSuspendedError   — citizen is suspended from self-booking
  FrequencyWindowError    — citizen within booking_frequency_days cooldown
  MaxActiveBookingsError  — citizen has reached max active bookings
  RescheduleCountError    — booking has reached max reschedule count
  RescheduleWindowError   — within reschedule_notice_hours of appointment
  InvalidStatusTransitionError — attempted an invalid state machine transition

Security invariants:
  - EVERY function runs inside transaction.atomic().
  - SELECT FOR UPDATE on all Slot rows involved before status checks (TOCTOU).
  - BookingAuditLog written INSIDE the same atomic() block (PIPEDA 4.5.3).
  - Signals dispatched via transaction.on_commit() using send_robust().
  - No PII (email, name) in log messages or audit detail — use PKs only.
  - Suspension check before slot lock in create_booking().
  - video_join_url_citizen NEVER set here — only inside authenticated session (Wave 7).
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from celery import current_app

if TYPE_CHECKING:
    pass

logger = logging.getLogger("civicos.appointments.services.booking")


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class BookingError(Exception):
    """Base class for all booking service errors."""


class SlotFullError(BookingError):
    """Slot has no available spaces. Caller should offer waitlist."""


class CitizenSuspendedError(BookingError):
    """Citizen is suspended from self-booking pending staff review."""


class FrequencyWindowError(BookingError):
    """Citizen has a recent booking within the booking_frequency_days window."""


class MaxActiveBookingsError(BookingError):
    """Citizen has reached max_active_bookings_per_citizen."""


class RescheduleCountError(BookingError):
    """Booking has reached max_reschedule_count."""


class RescheduleWindowError(BookingError):
    """Too close to the appointment start to reschedule."""


class InvalidStatusTransitionError(BookingError):
    """Attempted an invalid status state machine transition."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_policy(appointment_type, location=None):  # noqa: ANN001, ANN202
    """
    Three-level policy fallback:
      1. AppointmentType.scheduling_policy (most specific)
      2. Location.scheduling_policy
      3. settings.CIVICOS['APPOINTMENTS'] defaults (global fallback)
    """
    from apps.appointments.services.availability import _resolve_policy as _avail_resolve

    return _avail_resolve(appointment_type, location=location)


def _get_or_create_no_show_record(citizen):  # noqa: ANN001, ANN202
    """Return (record, created) for the citizen's ClientNoShowRecord."""
    from apps.appointments.models import ClientNoShowRecord

    return ClientNoShowRecord.objects.get_or_create(citizen=citizen)


def _write_audit_log(
    *,
    booking,  # noqa: ANN001
    action: str,
    actor=None,  # noqa: ANN001
    previous_status: str = "",
    new_status: str = "",
    detail: dict | None = None,
    actor_ip: str | None = None,
) -> None:
    """
    Append an immutable BookingAuditLog entry.
    MUST be called inside the originating transaction.atomic() block (PIPEDA 4.5.3).

    detail MUST NOT contain PII — slugs and UUIDs only.
    actor_id is stored as str(actor.pk) or "system" — NOT the User object.
    """
    from apps.appointments.models import BookingAuditLog

    if actor is None:
        _actor_role = "system"
    elif actor.is_staff:
        _actor_role = "organizer"
    else:
        _actor_role = "subscriber"
    BookingAuditLog.objects.create(
        booking=booking,
        action=action,
        actor_id=str(actor.pk) if actor else "system",
        actor_ip=actor_ip,
        previous_status=previous_status,
        new_status=new_status,
        detail=detail or {},
        actor_role=_actor_role,
    )


def _revoke_reminder_tasks(booking) -> None:  # noqa: ANN001
    """
    Revoke pending Celery reminder tasks for a booking.
    Called on cancel or reschedule. Best-effort — never raises.
    """
    for field_name in ("reminder_72h_task_id", "reminder_24h_task_id", "reminder_2h_task_id"):
        task_id = getattr(booking, field_name, "")
        if task_id:
            try:
                current_app.control.revoke(task_id, terminate=False)
                logger.debug(
                    "_revoke_reminder_tasks: revoked %s=%s for booking_id=%s",
                    field_name,
                    task_id,
                    booking.pk,
                )
            except Exception:
                logger.warning(
                    "_revoke_reminder_tasks: failed to revoke %s=%s for booking_id=%s",
                    field_name,
                    task_id,
                    booking.pk,
                )


def _update_slot_status(slot) -> None:  # noqa: ANN001
    """
    Recalculate and save slot status based on spaces_used vs capacity.
    Must be called with slot already locked via select_for_update() inside atomic().
    """
    if slot.spaces_used == 0:
        new_status = "available"
    elif slot.spaces_used < slot.capacity:
        new_status = "partial"
    else:
        new_status = "full"
    slot.status = new_status
    slot.save(update_fields=["spaces_used", "status", "updated_at"])


def _extract_ip(request) -> str | None:  # noqa: ANN001
    """
    Extract and normalize the client IP from a Django request object.
    Returns None if request is None.
    IPv4-mapped IPv6 addresses (::ffff:x.x.x.x) are normalized to plain IPv4.
    """
    if request is None:
        return None
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        ip = xff.split(",")[0].strip()
    else:
        ip = request.META.get("REMOTE_ADDR", "")
    if not ip:
        return None
    if ip.startswith("::ffff:"):
        ip = ip[7:]
    return ip or None


# ---------------------------------------------------------------------------
# create_booking
# ---------------------------------------------------------------------------


def create_booking(  # noqa: ANN201
    *,
    slot,  # noqa: ANN001
    citizen,  # noqa: ANN001
    appointment_mode: str,
    form_responses: dict,
    actor,  # noqa: ANN001
    booking_channel: str = "online",
    interpreter_needed: bool = False,
    interpreter_language: str = "",
    accessibility_needs: str = "",
    language: str = "en",
    consent_recorded_at=None,  # noqa: ANN001
    consent_version: str = "",
    request=None,  # noqa: ANN001
):
    """
    Reserve a slot for a citizen.

    PIPEDA 4.5.3: runs entirely inside transaction.atomic().
    Concurrency: SELECT FOR UPDATE on Slot row prevents TOCTOU double-booking.

    Steps:
    1. Suspension check (ClientNoShowRecord.is_suspended) — before slot lock.
    2. SELECT FOR UPDATE on slot.
    3. Capacity check: status in (available, partial) and spaces_used < capacity.
    4. Frequency control (booking_frequency_days).
    5. Max active bookings check.
    6. Determine initial status (pending vs confirmed).
    7. Create Booking record.
    8. Increment slot.spaces_used; update slot.status.
    9. Write BookingAuditLog inside atomic().
    10. Emit appt_booking_created via transaction.on_commit(send_robust).
    11. If auto-confirmed: emit appt_booking_confirmed via on_commit too.

    Raises:
        CitizenSuspendedError: Citizen is suspended from self-booking.
        SlotFullError: Slot is full — caller should offer waitlist.
        FrequencyWindowError: Citizen within booking_frequency_days cooldown.
        MaxActiveBookingsError: Citizen has too many active bookings.
    """
    from apps.appointments.models import Booking, BookingAuditLog, ClientNoShowRecord
    from apps.appointments.signals import appt_booking_confirmed, appt_booking_created

    now_utc = timezone.now()

    # Step 1: Suspension check — before acquiring slot lock
    try:
        no_show_record = ClientNoShowRecord.objects.get(citizen=citizen)
        if no_show_record.is_suspended:
            raise CitizenSuspendedError(
                f"Citizen {citizen.pk} is suspended from self-booking. "
                "A staff member must review and clear the suspension."
            )
    except ClientNoShowRecord.DoesNotExist:
        pass

    with transaction.atomic():
        # Step 2: Lock the slot row (TOCTOU prevention)
        from apps.appointments.models import Slot as SlotModel

        slot = SlotModel.objects.select_for_update().get(pk=slot.pk)

        # Step 3: Capacity check
        if slot.status not in ("available", "partial") or slot.spaces_used >= slot.capacity:
            raise SlotFullError(
                f"Slot {slot.pk} is full (spaces_used={slot.spaces_used}, capacity={slot.capacity}). "  # noqa: E501
                "Offer waitlist instead."
            )

        # Resolve policy (3-level fallback)
        appointment_type = slot.appointment_type
        policy = _resolve_policy(appointment_type, location=slot.location)

        # Step 4: Frequency control
        if policy.booking_frequency_days > 0:
            cutoff = now_utc - datetime.timedelta(days=policy.booking_frequency_days)
            has_recent = Booking.objects.filter(
                citizen=citizen,
                slot__appointment_type__service_type=appointment_type.service_type,
                status__in=("confirmed", "completed"),
                slot__start_datetime__gte=cutoff,
            ).exists()
            if has_recent:
                raise FrequencyWindowError(
                    f"Citizen {citizen.pk} has a recent booking within the "
                    f"{policy.booking_frequency_days}-day frequency window."
                )

        # Step 5: Max active bookings
        active_count = Booking.objects.filter(
            citizen=citizen,
            status__in=("pending", "confirmed"),
        ).count()
        if active_count >= policy.max_active_bookings_per_citizen:
            raise MaxActiveBookingsError(
                f"Citizen {citizen.pk} already has {active_count} active booking(s). "
                f"Maximum is {policy.max_active_bookings_per_citizen}."
            )

        # Step 6: Determine initial status
        requires_confirmation = getattr(appointment_type, "requires_staff_confirmation", False)
        initial_status = (
            Booking.STATUS_PENDING if requires_confirmation else Booking.STATUS_CONFIRMED
        )

        # Step 7: Create booking
        actor_ip = _extract_ip(request)
        booking = Booking.objects.create(
            slot=slot,
            citizen=citizen,
            status=initial_status,
            appointment_mode=appointment_mode,
            form_responses=form_responses,
            booking_channel=booking_channel,
            language=language,
            interpreter_needed=interpreter_needed,
            interpreter_language=interpreter_language,
            accessibility_needs=accessibility_needs,
            consent_recorded_at=consent_recorded_at,
            consent_version=consent_version or "",
        )

        # Step 8: Update slot
        slot.spaces_used += 1
        _update_slot_status(slot)

        # Step 9: Write audit log (PIPEDA 4.5.3 — inside atomic())
        _write_audit_log(
            booking=booking,
            action=BookingAuditLog.ACTION_CREATED,
            actor=actor,
            previous_status="",
            new_status=initial_status,
            detail={
                "slot_id": str(slot.pk),
                "appointment_type_slug": appointment_type.slug,
                "channel": booking_channel,
                "auto_confirmed": initial_status == Booking.STATUS_CONFIRMED,
            },
            actor_ip=actor_ip,
        )

        if consent_recorded_at:
            _write_audit_log(
                booking=booking,
                action=BookingAuditLog.ACTION_CONSENT_RECORDED,
                actor=actor,
                detail={"consent_version": consent_version},
                actor_ip=actor_ip,
            )

        # Step 10 & 11: Signals via on_commit (safe — won't fire on rollback)
        booking_id_str = str(booking.pk)
        slot_id_str = str(slot.pk)
        citizen_id_str = str(citizen.pk)

        def _dispatch_created() -> None:
            results = appt_booking_created.send_robust(
                sender=Booking,
                booking_id=booking_id_str,
                slot_id=slot_id_str,
                citizen_id=citizen_id_str,
                channel=booking_channel,
            )
            for receiver, exc in results:
                if isinstance(exc, Exception):
                    logger.error(
                        "create_booking: appt_booking_created receiver %s raised %s",
                        receiver,
                        type(exc).__name__,
                    )

        transaction.on_commit(_dispatch_created)

        if initial_status == Booking.STATUS_CONFIRMED:

            def _dispatch_confirmed() -> None:
                results = appt_booking_confirmed.send_robust(
                    sender=Booking,
                    booking_id=booking_id_str,
                    slot_id=slot_id_str,
                )
                for receiver, exc in results:
                    if isinstance(exc, Exception):
                        logger.error(
                            "create_booking (auto-confirm): appt_booking_confirmed receiver %s raised %s",  # noqa: E501
                            receiver,
                            type(exc).__name__,
                        )

            transaction.on_commit(_dispatch_confirmed)

    logger.info(
        "create_booking: booking_id=%s status=%s citizen_id=%s slot_id=%s",
        booking.pk,
        initial_status,
        citizen.pk,
        slot.pk,
    )
    return booking


# ---------------------------------------------------------------------------
# confirm_booking
# ---------------------------------------------------------------------------


def confirm_booking(*, booking, actor, actor_ip: str | None = None):  # noqa: ANN001, ANN201
    """
    Staff confirms a pending booking. Transitions: PENDING → CONFIRMED.

    Raises:
        InvalidStatusTransitionError: If booking is not in PENDING status.
    """
    from apps.appointments.models import Booking, BookingAuditLog
    from apps.appointments.signals import appt_booking_confirmed

    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking.pk)

        if booking.status != Booking.STATUS_PENDING:
            raise InvalidStatusTransitionError(
                f"Cannot confirm booking {booking.pk}: status is '{booking.status}', "
                "expected 'pending'."
            )

        previous_status = booking.status
        booking.status = Booking.STATUS_CONFIRMED
        booking.save(update_fields=["status", "updated_at"])

        _write_audit_log(
            booking=booking,
            action=BookingAuditLog.ACTION_CONFIRMED,
            actor=actor,
            previous_status=previous_status,
            new_status=Booking.STATUS_CONFIRMED,
            actor_ip=actor_ip,
        )

        booking_id_str = str(booking.pk)
        slot_id_str = str(booking.slot_id)

        def _dispatch() -> None:
            results = appt_booking_confirmed.send_robust(
                sender=Booking,
                booking_id=booking_id_str,
                slot_id=slot_id_str,
            )
            for receiver, exc in results:
                if isinstance(exc, Exception):
                    logger.error(
                        "confirm_booking: appt_booking_confirmed receiver %s raised %s",
                        receiver,
                        type(exc).__name__,
                    )

        transaction.on_commit(_dispatch)

    logger.info(
        "confirm_booking: booking_id=%s confirmed by actor_id=%s",
        booking.pk,
        actor.pk,
    )
    return booking


# ---------------------------------------------------------------------------
# cancel_booking
# ---------------------------------------------------------------------------


def cancel_booking(  # noqa: ANN201
    *,
    booking,  # noqa: ANN001
    actor,  # noqa: ANN001
    reason: str = "",
    actor_ip: str | None = None,
):
    """
    Cancel a booking. May be called by citizen, staff, or system (actor=None).

    Steps:
    1. Lock booking with SELECT FOR UPDATE.
    2. Guard against already-terminal status.
    3. Determine late cancellation (within cancellation_notice_hours window).
    4. Update booking fields.
    5. Revoke reminder Celery tasks.
    6. Decrement slot spaces_used if booking was confirmed.
    7. Update ClientNoShowRecord if late cancellation.
    8. Write BookingAuditLog.
    9. Emit appt_booking_cancelled via on_commit.

    Raises:
        InvalidStatusTransitionError: If booking is already in a terminal status.
    """
    from apps.appointments.models import Booking, BookingAuditLog
    from apps.appointments.signals import appt_booking_cancelled

    now_utc = timezone.now()

    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking.pk)

        if booking.status in (
            Booking.STATUS_CANCELLED,
            Booking.STATUS_COMPLETED,
            Booking.STATUS_REJECTED,
        ):
            raise InvalidStatusTransitionError(
                f"Cannot cancel booking {booking.pk}: already in terminal status '{booking.status}'."  # noqa: E501
            )

        was_confirmed = booking.status == Booking.STATUS_CONFIRMED
        previous_status = booking.status

        # Check if late cancellation
        appointment_type = booking.slot.appointment_type
        policy = _resolve_policy(appointment_type, location=booking.slot.location)
        slot_start = booking.slot.start_datetime
        notice_window = datetime.timedelta(hours=policy.cancellation_notice_hours)
        is_late = (now_utc + notice_window) > slot_start

        # Determine audit action
        if actor is None:
            audit_action = BookingAuditLog.ACTION_CANCELLED_SYSTEM
        elif getattr(actor, "is_staff", False):
            audit_action = BookingAuditLog.ACTION_CANCELLED_STAFF
        else:
            audit_action = BookingAuditLog.ACTION_CANCELLED_CITIZEN

        # Update booking
        booking.status = Booking.STATUS_CANCELLED
        booking.cancelled_at = now_utc
        booking.cancelled_by = actor
        booking.cancellation_reason = reason
        booking.late_cancellation = is_late
        booking.save(
            update_fields=[
                "status",
                "cancelled_at",
                "cancelled_by",
                "cancellation_reason",
                "late_cancellation",
                "updated_at",
            ]
        )

        # Revoke reminder tasks (best-effort)
        _revoke_reminder_tasks(booking)

        # Decrement slot spaces_used only if booking was confirmed or pending
        # (we increment on create regardless of status)
        from apps.appointments.models import Slot as SlotModel

        slot = SlotModel.objects.select_for_update().get(pk=booking.slot_id)
        slot.spaces_used = max(0, slot.spaces_used - 1)
        _update_slot_status(slot)

        # Late cancellation → update ClientNoShowRecord (only for confirmed bookings)
        if is_late and was_confirmed:
            record, _ = _get_or_create_no_show_record(booking.citizen)
            record.late_cancellation_count += 1
            record.save(update_fields=["late_cancellation_count", "updated_at"])

        _write_audit_log(
            booking=booking,
            action=audit_action,
            actor=actor,
            previous_status=previous_status,
            new_status=Booking.STATUS_CANCELLED,
            detail={
                "late": is_late,
                "slot_id": str(booking.slot_id),
                "was_confirmed": was_confirmed,
            },
            actor_ip=actor_ip,
        )

        booking_id_str = str(booking.pk)
        slot_id_str = str(booking.slot_id)
        actor_id_str = str(actor.pk) if actor else "system"

        def _dispatch() -> None:
            results = appt_booking_cancelled.send_robust(
                sender=Booking,
                booking_id=booking_id_str,
                slot_id=slot_id_str,
                cancelled_by_id=actor_id_str,
                late=is_late,
            )
            for receiver, exc in results:
                if isinstance(exc, Exception):
                    logger.error(
                        "cancel_booking: appt_booking_cancelled receiver %s raised %s",
                        receiver,
                        type(exc).__name__,
                    )

        transaction.on_commit(_dispatch)

    logger.info(
        "cancel_booking: booking_id=%s cancelled actor_id=%s late=%s",
        booking.pk,
        actor_id_str,
        is_late,
    )
    return booking


# ---------------------------------------------------------------------------
# reschedule_booking
# ---------------------------------------------------------------------------


def reschedule_booking(  # noqa: ANN201
    *,
    booking,  # noqa: ANN001
    new_slot,  # noqa: ANN001
    actor,  # noqa: ANN001
    reason: str = "",
    actor_ip: str | None = None,
):
    """
    Reschedule a confirmed booking to a new slot.

    Creates a NEW Booking on new_slot; cancels old booking with rescheduled=True.
    New booking has rescheduled_from pointing to old booking.

    Dual-slot lock ordering: sort slot PKs (as strings) and lock min-first
    to prevent deadlock under concurrent rescheduling.

    Raises:
        InvalidStatusTransitionError: Old booking not CONFIRMED or is a no-show.
        RescheduleCountError: Max reschedule count reached.
        RescheduleWindowError: Too close to appointment start.
        SlotFullError: New slot has no available space.
    """
    from apps.appointments.models import Booking, BookingAuditLog
    from apps.appointments.models import Slot as SlotModel
    from apps.appointments.signals import appt_booking_rescheduled

    now_utc = timezone.now()

    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking.pk)

        if booking.status != Booking.STATUS_CONFIRMED:
            raise InvalidStatusTransitionError(
                f"Cannot reschedule booking {booking.pk}: status is '{booking.status}', "
                "expected 'confirmed'."
            )
        if booking.no_show:
            raise InvalidStatusTransitionError(
                f"Cannot reschedule booking {booking.pk}: booking is marked as a no-show."
            )

        appointment_type = booking.slot.appointment_type
        policy = _resolve_policy(appointment_type, location=new_slot.location)

        if booking.reschedule_count >= policy.max_reschedule_count:
            raise RescheduleCountError(
                f"Booking {booking.pk} has reached the maximum reschedule count "
                f"({policy.max_reschedule_count})."
            )

        notice_window = datetime.timedelta(hours=policy.reschedule_notice_hours)
        if now_utc + notice_window >= booking.slot.start_datetime:
            raise RescheduleWindowError(
                f"Cannot reschedule: within {policy.reschedule_notice_hours}h of appointment start."
            )

        # Dual-slot lock — consistent order (min pk string first) prevents deadlock
        old_slot_pk = booking.slot_id
        new_slot_pk = new_slot.pk
        slots_qs = (
            SlotModel.objects.select_for_update()
            .filter(pk__in=[old_slot_pk, new_slot_pk])
            .order_by("pk")
        )
        slots = {str(s.pk): s for s in slots_qs}
        old_slot = slots[str(old_slot_pk)]
        new_slot_locked = slots[str(new_slot_pk)]

        # Check new slot capacity
        if (
            new_slot_locked.status not in ("available", "partial")
            or new_slot_locked.spaces_used >= new_slot_locked.capacity
        ):
            raise SlotFullError(f"New slot {new_slot_locked.pk} is full — cannot reschedule.")

        old_booking_id_str = str(booking.pk)
        previous_status = booking.status

        # Cancel old booking
        booking.status = Booking.STATUS_CANCELLED
        booking.rescheduled = True
        booking.cancelled_at = now_utc
        booking.cancelled_by = actor
        booking.cancellation_reason = reason or "Rescheduled by citizen/staff."
        booking.save(
            update_fields=[
                "status",
                "rescheduled",
                "cancelled_at",
                "cancelled_by",
                "cancellation_reason",
                "updated_at",
            ]
        )

        # Revoke old reminder tasks
        _revoke_reminder_tasks(booking)

        # Decrement old slot
        old_slot.spaces_used = max(0, old_slot.spaces_used - 1)
        _update_slot_status(old_slot)

        # Create new booking
        new_booking = Booking.objects.create(
            slot=new_slot_locked,
            citizen=booking.citizen,
            status=Booking.STATUS_CONFIRMED,
            appointment_mode=booking.appointment_mode,
            form_responses=booking.form_responses,
            booking_channel=booking.booking_channel,
            language=booking.language,
            interpreter_needed=booking.interpreter_needed,
            interpreter_language=booking.interpreter_language,
            accessibility_needs=booking.accessibility_needs,
            rescheduled_from=booking,
            reschedule_count=booking.reschedule_count + 1,
            consent_recorded_at=booking.consent_recorded_at,
            consent_version=booking.consent_version,
        )

        # Increment new slot
        new_slot_locked.spaces_used += 1
        _update_slot_status(new_slot_locked)

        # Audit logs — both bookings
        _write_audit_log(
            booking=booking,
            action=BookingAuditLog.ACTION_RESCHEDULED,
            actor=actor,
            previous_status=previous_status,
            new_status=Booking.STATUS_CANCELLED,
            detail={
                "new_booking_id": str(new_booking.pk),
                "new_slot_id": str(new_slot_locked.pk),
            },
            actor_ip=actor_ip,
        )
        _write_audit_log(
            booking=new_booking,
            action=BookingAuditLog.ACTION_CREATED,
            actor=actor,
            previous_status="",
            new_status=Booking.STATUS_CONFIRMED,
            detail={
                "rescheduled_from": old_booking_id_str,
                "old_slot_id": str(old_slot_pk),
                "reschedule_count": new_booking.reschedule_count,
            },
            actor_ip=actor_ip,
        )

        new_booking_id_str = str(new_booking.pk)
        new_slot_id_str = str(new_slot_locked.pk)

        def _dispatch() -> None:
            results = appt_booking_rescheduled.send_robust(
                sender=Booking,
                old_booking_id=old_booking_id_str,
                new_booking_id=new_booking_id_str,
                slot_id=new_slot_id_str,
            )
            for receiver, exc in results:
                if isinstance(exc, Exception):
                    logger.error(
                        "reschedule_booking: appt_booking_rescheduled receiver %s raised %s",
                        receiver,
                        type(exc).__name__,
                    )

        transaction.on_commit(_dispatch)

    logger.info(
        "reschedule_booking: old_booking_id=%s → new_booking_id=%s actor_id=%s",
        old_booking_id_str,
        new_booking.pk,
        actor.pk,
    )
    return new_booking


# ---------------------------------------------------------------------------
# mark_no_show
# ---------------------------------------------------------------------------


def mark_no_show(*, booking, actor, actor_ip: str | None = None):  # noqa: ANN001, ANN201
    """
    Staff marks a confirmed booking as a no-show.

    no_show=True is a separate Boolean — NOT a status change.
    Booking remains CONFIRMED.

    Side effects:
    - Increments ClientNoShowRecord.no_show_count.
    - Flags/suspends citizen if threshold reached.
    - Emits appt_no_show_marked via on_commit.

    Raises:
        InvalidStatusTransitionError: Booking not confirmed, or already no-show.
    """
    from apps.appointments.models import Booking, BookingAuditLog
    from apps.appointments.signals import appt_no_show_marked

    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking.pk)

        if booking.status != Booking.STATUS_CONFIRMED:
            raise InvalidStatusTransitionError(
                f"Cannot mark no-show for booking {booking.pk}: status is '{booking.status}', "
                "expected 'confirmed'."
            )
        if booking.no_show:
            raise InvalidStatusTransitionError(
                f"Booking {booking.pk} is already marked as no-show."
            )

        booking.no_show = True
        booking.save(update_fields=["no_show", "updated_at"])

        # Update aggregate no-show record
        record, _ = _get_or_create_no_show_record(booking.citizen)
        record.no_show_count += 1
        record.total_appointments += 1
        record.last_no_show_at = timezone.now()

        # Policy thresholds
        appointment_type = booking.slot.appointment_type
        policy = _resolve_policy(appointment_type, location=booking.slot.location)
        no_show_count = record.no_show_count

        if no_show_count >= policy.no_show_suspension_threshold:
            record.is_suspended = True
            if not record.is_flagged:
                record.is_flagged = True
                record.flagged_at = timezone.now()
                record.flagged_by = actor
        elif no_show_count >= policy.no_show_warning_threshold and not record.is_flagged:
            record.is_flagged = True
            record.flagged_at = timezone.now()
            record.flagged_by = actor

        record.save(
            update_fields=[
                "no_show_count",
                "total_appointments",
                "last_no_show_at",
                "is_flagged",
                "flagged_at",
                "flagged_by",
                "is_suspended",
                "updated_at",
            ]
        )

        _write_audit_log(
            booking=booking,
            action=BookingAuditLog.ACTION_NO_SHOW_MARKED,
            actor=actor,
            previous_status=Booking.STATUS_CONFIRMED,
            new_status=Booking.STATUS_CONFIRMED,
            detail={
                "no_show_count": no_show_count,
                "is_flagged": record.is_flagged,
                "is_suspended": record.is_suspended,
                "slot_id": str(booking.slot_id),
            },
            actor_ip=actor_ip,
        )

        citizen_id_str = str(booking.citizen_id)
        booking_id_str = str(booking.pk)

        def _dispatch() -> None:
            results = appt_no_show_marked.send_robust(
                sender=Booking,
                booking_id=booking_id_str,
                citizen_id=citizen_id_str,
                no_show_count=no_show_count,
            )
            for receiver, exc in results:
                if isinstance(exc, Exception):
                    logger.error(
                        "mark_no_show: appt_no_show_marked receiver %s raised %s",
                        receiver,
                        type(exc).__name__,
                    )

        transaction.on_commit(_dispatch)

    logger.info(
        "mark_no_show: booking_id=%s actor_id=%s no_show_count=%d",
        booking.pk,
        actor.pk,
        no_show_count,
    )
    return booking


# ---------------------------------------------------------------------------
# complete_booking
# ---------------------------------------------------------------------------


def complete_booking(*, booking, actor=None, actor_ip: str | None = None):  # noqa: ANN001, ANN201
    """
    Mark a confirmed booking as completed.

    Called by mark_past_slots_completed task (actor=None) or staff manually.
    Transitions: CONFIRMED → COMPLETED.

    Raises:
        InvalidStatusTransitionError: Booking not in CONFIRMED status.
    """
    from apps.appointments.models import Booking, BookingAuditLog
    from apps.appointments.signals import appt_booking_completed

    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking.pk)

        if booking.status != Booking.STATUS_CONFIRMED:
            raise InvalidStatusTransitionError(
                f"Cannot complete booking {booking.pk}: status is '{booking.status}', "
                "expected 'confirmed'."
            )

        previous_status = booking.status
        booking.status = Booking.STATUS_COMPLETED
        booking.save(update_fields=["status", "updated_at"])

        # Increment total_appointments count
        record, _ = _get_or_create_no_show_record(booking.citizen)
        record.total_appointments += 1
        record.save(update_fields=["total_appointments", "updated_at"])

        _write_audit_log(
            booking=booking,
            action=BookingAuditLog.ACTION_COMPLETED,
            actor=actor,
            previous_status=previous_status,
            new_status=Booking.STATUS_COMPLETED,
            detail={"slot_id": str(booking.slot_id)},
            actor_ip=actor_ip,
        )

        booking_id_str = str(booking.pk)

        def _dispatch() -> None:
            results = appt_booking_completed.send_robust(
                sender=Booking,
                booking_id=booking_id_str,
            )
            for receiver, exc in results:
                if isinstance(exc, Exception):
                    logger.error(
                        "complete_booking: appt_booking_completed receiver %s raised %s",
                        receiver,
                        type(exc).__name__,
                    )

        transaction.on_commit(_dispatch)

    logger.info(
        "complete_booking: booking_id=%s actor_id=%s",
        booking.pk,
        str(actor.pk) if actor else "system",
    )
    return booking


# ---------------------------------------------------------------------------
# reject_booking
# ---------------------------------------------------------------------------


def reject_booking(*, booking, actor, reason: str = "", actor_ip: str | None = None):  # noqa: ANN001, ANN201
    """
    Staff rejects a pending booking. Transitions: PENDING → REJECTED.

    Decrements slot.spaces_used (pending bookings hold a space at create time).

    Raises:
        InvalidStatusTransitionError: Booking not in PENDING status.
    """
    from apps.appointments.models import Booking, BookingAuditLog
    from apps.appointments.models import Slot as SlotModel
    from apps.appointments.signals import appt_booking_rejected

    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking.pk)

        if booking.status != Booking.STATUS_PENDING:
            raise InvalidStatusTransitionError(
                f"Cannot reject booking {booking.pk}: status is '{booking.status}', "
                "expected 'pending'."
            )

        previous_status = booking.status
        booking.status = Booking.STATUS_REJECTED
        booking.cancelled_at = timezone.now()
        booking.cancelled_by = actor
        booking.cancellation_reason = reason
        booking.save(
            update_fields=[
                "status",
                "cancelled_at",
                "cancelled_by",
                "cancellation_reason",
                "updated_at",
            ]
        )

        # Decrement slot (pending bookings DO hold a space)
        slot = SlotModel.objects.select_for_update().get(pk=booking.slot_id)
        slot.spaces_used = max(0, slot.spaces_used - 1)
        _update_slot_status(slot)

        _write_audit_log(
            booking=booking,
            action=BookingAuditLog.ACTION_REJECTED,
            actor=actor,
            previous_status=previous_status,
            new_status=Booking.STATUS_REJECTED,
            detail={"slot_id": str(booking.slot_id)},
            actor_ip=actor_ip,
        )

        booking_id_str = str(booking.pk)
        slot_id_str = str(booking.slot_id)

        def _dispatch() -> None:
            results = appt_booking_rejected.send_robust(
                sender=Booking,
                booking_id=booking_id_str,
                slot_id=slot_id_str,
            )
            for receiver, exc in results:
                if isinstance(exc, Exception):
                    logger.error(
                        "reject_booking: appt_booking_rejected receiver %s raised %s",
                        receiver,
                        type(exc).__name__,
                    )

        transaction.on_commit(_dispatch)

    logger.info(
        "reject_booking: booking_id=%s rejected by actor_id=%s",
        booking.pk,
        actor.pk,
    )
    return booking
