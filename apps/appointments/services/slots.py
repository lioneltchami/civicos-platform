"""
Appointments BB — Slot management service.

Functions:
  generate_slots_for_range  — create and persist Slot records for a date range
  block_slot                — admin: block a slot (status -> 'blocked')
  cancel_slot               — admin: cancel a slot (status -> 'cancelled')

Custom exceptions:
  SlotFullError             — slot has no available spaces
  SlotHasBookingsError      — slot has active bookings; cannot be blocked/cancelled

Security invariants:
  - All Slot datetimes stored in UTC.
  - No PII in log messages.
  - Idempotent: generate_slots_for_range skips existing slots.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from django.db import transaction

logger = logging.getLogger("civicos.appointments.services.slots")


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class SlotFullError(Exception):
    """Raised when a slot has no available spaces for a new booking."""


class SlotHasBookingsError(Exception):
    """
    Raised when trying to block or cancel a slot that has active bookings.
    Caller must cancel the bookings first, then retry the slot operation.
    """


# ---------------------------------------------------------------------------
# generate_slots_for_range
# ---------------------------------------------------------------------------

def generate_slots_for_range(
    *,
    appointment_type,
    staff,
    date_from: date,
    date_to: date,
    created_by_task: bool = False,
) -> int:
    """
    Generate and persist Slot records for the given staff + appointment_type
    over [date_from, date_to].

    Idempotent: skips dates where a Slot already exists with the same
    (staff, appointment_type, start_datetime) combination.

    Uses bulk_create with ignore_conflicts=True for efficiency.

    Note: This is a batch generation function, not a user-triggered action, so it
    does not carry an `actor` parameter. Audit logging will be handled in Wave 3
    via the task context (Celery task ID + triggered_by staff PK).

    Args:
        appointment_type: AppointmentType to generate slots for.
        staff:            StaffProfile to generate slots for.
        date_from:        First date (inclusive).
        date_to:          Last date (inclusive).
        created_by_task:  True when called from a Celery task (for logging).

    Returns:
        int: Count of new Slot PKs assigned during this call (0 on repeat runs
        because get_available_slots() returns empty when busy_times already
        contains the pre-existing "available" slots — bulk_create is never
        reached on the second run).
    """
    from apps.appointments.models import Slot
    from apps.appointments.services.availability import SlotAvailabilityService

    svc = SlotAvailabilityService()
    computed = svc.get_available_slots(
        appointment_type=appointment_type,
        date_from=date_from,
        date_to=date_to,
        staff=staff,
    )

    if not computed:
        logger.debug(
            "generate_slots_for_range: no slots computed for staff_id=%s, "
            "appointment_type_id=%s, %s->%s",
            staff.pk, appointment_type.pk, date_from, date_to,
        )
        return 0

    # Build Slot objects from computed results
    slots_to_create = []
    for slot_dict in computed:
        slots_to_create.append(
            Slot(
                appointment_type_id=appointment_type.pk,
                staff_id=staff.pk,
                location_id=slot_dict["location_id"],
                start_datetime=slot_dict["start_datetime"],
                end_datetime=slot_dict["end_datetime"],
                effective_start=slot_dict["effective_start"],
                effective_end=slot_dict["effective_end"],
                capacity=appointment_type.capacity_per_slot,
                spaces_used=0,
                status="available",
            )
        )

    # bulk_create with ignore_conflicts — idempotent for retry safety.
    # Note: on PostgreSQL, bulk_create(ignore_conflicts=True) ALWAYS returns
    # every input object regardless of how many rows were actually inserted —
    # so len(created) would equal len(slots_to_create) even on a 100% duplicate
    # run. Instead we capture the PKs we attempted to insert (Slot uses UUID PKs
    # assigned in Python before the DB call) and query after the fact to find
    # how many of those PKs are now present in the database. Pre-existing rows
    # that triggered a conflict are already counted here, which is fine: the
    # function contract is idempotent generation, and this count represents
    # "slots available after this call" for the requested range.
    if not slots_to_create:
        return 0

    slot_pks = [s.pk for s in slots_to_create]

    with transaction.atomic():
        Slot.objects.bulk_create(slots_to_create, ignore_conflicts=True)

    # Count how many of the attempted PKs are now in the DB.
    # New inserts + pre-existing rows that survived conflict-skip are included.
    inserted_count = Slot.objects.filter(pk__in=slot_pks).count()

    logger.info(
        "generate_slots_for_range: %d/%d slots available after upsert — "
        "staff_id=%s, appointment_type_id=%s, %s->%s%s",
        inserted_count,
        len(slot_pks),
        staff.pk,
        appointment_type.pk,
        date_from,
        date_to,
        " (via task)" if created_by_task else "",
    )
    return inserted_count


# ---------------------------------------------------------------------------
# block_slot
# ---------------------------------------------------------------------------

def block_slot(*, slot, reason: str = "", actor=None) -> object:
    """
    Block a slot (admin action). Transitions status -> 'blocked'.

    Idempotent if the slot is already blocked.

    Raises:
        SlotHasBookingsError: if the slot has confirmed or pending bookings.
            Caller must cancel bookings first, then retry.

    Args:
        slot:   Slot instance to block.
        reason: Optional reason stored in slot.internal_note.
        actor:  Optional User performing the action (for audit logs, Wave 3).

    Returns:
        Updated Slot instance.
    """
    from apps.appointments.models import Slot  # noqa: PLC0415 — deferred to avoid circular imports

    with transaction.atomic():
        slot = Slot.objects.select_for_update().get(pk=slot.pk)  # H-4: re-fetch with row lock

        # H-3: State machine guard — terminal states cannot be re-transitioned
        if slot.status in ("completed", "cancelled"):
            raise ValueError(
                f"Cannot block slot {slot.pk}: slot is already in terminal state '{slot.status}'. "
                "Completed and cancelled slots are immutable for audit integrity."
            )

        # Idempotency: already blocked — no-op
        if slot.status == "blocked":
            return slot

        # Guard: refuse to block slots with active bookings
        # Booking model is Wave 3; guard against ImportError
        try:
            from apps.appointments.models import Booking  # noqa: F401
            active_booking_count = slot.bookings.filter(
                status__in=("pending", "confirmed")
            ).count()
            if active_booking_count > 0:
                raise SlotHasBookingsError(
                    f"Slot {slot.pk} has {active_booking_count} active booking(s). "
                    "Cancel them before blocking the slot."
                )
        except (ImportError, AttributeError):
            # Booking not yet implemented (Wave 3) — skip guard
            pass

        slot.status = "blocked"
        if reason:
            existing_note = slot.internal_note
            slot.internal_note = (
                f"{existing_note}\n[BLOCKED] {reason}".strip()
                if existing_note
                else f"[BLOCKED] {reason}"
            )
        slot.save(update_fields=["status", "internal_note", "updated_at"])
        # TODO Wave 3: pass actor to AuditLog.record_event(
        #     action="slot_blocked",
        #     target_pk=slot.pk,
        #     actor_pk=actor.pk if actor else None,
        # ) inside this atomic block.

    logger.info(
        "block_slot: slot_id=%s blocked. reason_provided=%s",
        slot.pk,
        bool(reason),
    )
    return slot


# ---------------------------------------------------------------------------
# cancel_slot
# ---------------------------------------------------------------------------

def cancel_slot(*, slot, reason: str = "", actor=None) -> object:
    """
    Cancel a slot (admin action). Transitions status -> 'cancelled'.

    Unlike block_slot, cancel_slot refuses to proceed if the slot has any
    active (pending or confirmed) bookings. Caller MUST cancel bookings
    first using the booking service (Wave 3), then call cancel_slot().

    Raises:
        SlotHasBookingsError: if active bookings exist.

    Args:
        slot:   Slot instance to cancel.
        reason: Optional reason stored in slot.internal_note.
        actor:  Optional User performing the action (for audit logs, Wave 3).

    Returns:
        Updated Slot instance.
    """
    from apps.appointments.models import Slot  # noqa: PLC0415 — deferred to avoid circular imports

    with transaction.atomic():
        slot = Slot.objects.select_for_update().get(pk=slot.pk)  # H-4: re-fetch with row lock

        # H-3: State machine guard — completed slots cannot be cancelled
        if slot.status == "completed":
            raise ValueError(
                f"Cannot cancel slot {slot.pk}: slot is already completed. "
                "Completed slots are immutable for audit integrity."
            )

        # Idempotency: already cancelled — no-op
        if slot.status == "cancelled":
            return slot

        # Guard: refuse if active bookings exist
        try:
            from apps.appointments.models import Booking  # noqa: F401
            active_count = slot.bookings.filter(
                status__in=("pending", "confirmed")
            ).count()
            if active_count > 0:
                raise SlotHasBookingsError(
                    f"Slot {slot.pk} has {active_count} active booking(s). "
                    "Cancel the bookings via the booking service before cancelling the slot."
                )
        except (ImportError, AttributeError):
            pass

        slot.status = "cancelled"
        if reason:
            existing_note = slot.internal_note
            slot.internal_note = (
                f"{existing_note}\n[CANCELLED] {reason}".strip()
                if existing_note
                else f"[CANCELLED] {reason}"
            )
        slot.save(update_fields=["status", "internal_note", "updated_at"])
        # TODO Wave 3: pass actor to AuditLog.record_event(
        #     action="slot_cancelled",
        #     target_pk=slot.pk,
        #     actor_pk=actor.pk if actor else None,
        # ) inside this atomic block.

    logger.info(
        "cancel_slot: slot_id=%s cancelled. reason_provided=%s",
        slot.pk,
        bool(reason),
    )
    return slot
