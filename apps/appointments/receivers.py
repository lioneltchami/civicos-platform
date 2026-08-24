"""
Appointments BB — Signal receivers.

Receivers are connected when AppointmentsConfig.ready() runs.

All receivers:
  - NEVER log PII (email, name) — use PKs only.
  - Use try/except for optional app dependencies (workflows, notifications).

Signal → Receiver mapping:
  appt_booking_created    → on_booking_created  (WorkItem creation stub)
  appt_booking_confirmed  → on_booking_confirmed (reminder scheduling stub)
  appt_booking_cancelled  → on_booking_cancelled (waitlist promotion stub)
  appt_booking_rejected   → on_booking_rejected  (notification stub)
  appt_booking_completed  → on_booking_completed (log only)
  appt_no_show_marked     → on_no_show_marked    (staff notification stub)
"""

from __future__ import annotations

import logging
from typing import Any

from .signals import (
    appt_booking_cancelled,
    appt_booking_completed,
    appt_booking_confirmed,
    appt_booking_created,
    appt_booking_rejected,
    appt_no_show_marked,
)

logger = logging.getLogger("civicos.appointments.receivers")


# ---------------------------------------------------------------------------
# Booking created
# ---------------------------------------------------------------------------


def on_booking_created(
    sender: Any,
    *,
    booking_id: str,
    slot_id: str,
    citizen_id: str,
    channel: str,
    **kwargs,  # noqa: ANN003
) -> None:
    """
    Fired after a new Booking is created.

    Actions:
    - Create a WorkItem in apps.workflows for staff follow-up (if available).
    """
    logger.info(
        "on_booking_created: booking_id=%s slot_id=%s citizen_id=%s channel=%s",
        booking_id,
        slot_id,
        citizen_id,
        channel,
    )
    try:
        _create_work_item_for_booking(booking_id=booking_id, slot_id=slot_id)
    except Exception as exc:
        logger.error(
            "on_booking_created: WorkItem creation failed booking_id=%s: %s",
            booking_id,
            type(exc).__name__,
        )


def _create_work_item_for_booking(*, booking_id: str, slot_id: str) -> None:
    """
    Create a WorkItem in the workflows app for a new booking.
    Uses plain metadata dict — no FK to avoid circular import.
    """
    try:
        from apps.workflows.models import WorkItem
    except ImportError:
        logger.debug("_create_work_item_for_booking: workflows app not available — skipping")
        return

    try:
        from apps.appointments.models import Booking

        booking = Booking.objects.select_related("slot", "slot__appointment_type").get(
            pk=booking_id
        )
    except Exception:
        logger.warning(
            "_create_work_item_for_booking: booking %s not found — WorkItem not created",
            booking_id,
        )
        return

    try:
        WorkItem.objects.create(
            title=f"Appointment booking: {booking.slot.appointment_type.name_en}",
            description=(
                f"Booking {booking_id} created via '{booking.booking_channel}'. "
                f"Slot starts: {booking.slot.start_datetime.isoformat()}."
            ),
            status="pending",
            due_at=booking.slot.start_datetime,
            metadata={
                "booking_id": booking_id,
                "slot_id": slot_id,
                "citizen_id": str(booking.citizen_id),
                "appointment_type_slug": booking.slot.appointment_type.slug,
            },
        )
        logger.info(
            "_create_work_item_for_booking: WorkItem created for booking_id=%s",
            booking_id,
        )
    except Exception as exc:
        logger.error(
            "_create_work_item_for_booking: failed booking_id=%s: %s",
            booking_id,
            type(exc).__name__,
        )


# ---------------------------------------------------------------------------
# Booking confirmed
# ---------------------------------------------------------------------------


def on_booking_confirmed(sender, *, booking_id: str, slot_id: str, **kwargs) -> None:  # noqa: ANN001, ANN003
    """Fired when booking transitions PENDING → CONFIRMED."""
    logger.info(
        "on_booking_confirmed: booking_id=%s slot_id=%s",
        booking_id,
        slot_id,
    )
    # Wave 6: _schedule_reminder_tasks(booking_id=booking_id)


# ---------------------------------------------------------------------------
# Booking cancelled
# ---------------------------------------------------------------------------


def on_booking_cancelled(
    sender: Any,
    *,
    booking_id: str,
    slot_id: str,
    cancelled_by_id: str,
    late: bool,
    **kwargs,  # noqa: ANN003
) -> None:
    """Fired when a booking is cancelled."""
    logger.info(
        "on_booking_cancelled: booking_id=%s slot_id=%s cancelled_by_id=%s late=%s",
        booking_id,
        slot_id,
        cancelled_by_id,
        late,
    )
    # Wave 4: promote_waitlist(slot_id=slot_id)


# ---------------------------------------------------------------------------
# Booking rejected
# ---------------------------------------------------------------------------


def on_booking_rejected(sender, *, booking_id: str, slot_id: str, **kwargs) -> None:  # noqa: ANN001, ANN003
    """Fired when a booking is rejected by staff."""
    logger.info(
        "on_booking_rejected: booking_id=%s slot_id=%s",
        booking_id,
        slot_id,
    )
    # Wave 6: send_rejection_notification(booking_id=booking_id)


# ---------------------------------------------------------------------------
# Booking completed
# ---------------------------------------------------------------------------


def on_booking_completed(sender, *, booking_id: str, **kwargs) -> None:  # noqa: ANN001, ANN003
    """Fired when a booking is marked completed."""
    logger.info("on_booking_completed: booking_id=%s", booking_id)


# ---------------------------------------------------------------------------
# No-show marked
# ---------------------------------------------------------------------------


def on_no_show_marked(
    sender: Any,
    *,
    booking_id: str,
    citizen_id: str,
    no_show_count: int,
    **kwargs,  # noqa: ANN003
) -> None:
    """Fired when a booking is marked as a no-show."""
    logger.info(
        "on_no_show_marked: booking_id=%s citizen_id=%s no_show_count=%d",
        booking_id,
        citizen_id,
        no_show_count,
    )
    # Wave 6: if suspended, send staff notification


# ---------------------------------------------------------------------------
# Signal connection
# ---------------------------------------------------------------------------


def connect_receivers() -> None:
    """
    Connect all Wave 3 receivers to their signals.
    Called from AppointmentsConfig.ready().
    """
    appt_booking_created.connect(on_booking_created, dispatch_uid="appt_booking_created_receiver")
    appt_booking_confirmed.connect(
        on_booking_confirmed, dispatch_uid="appt_booking_confirmed_receiver"
    )
    appt_booking_cancelled.connect(
        on_booking_cancelled, dispatch_uid="appt_booking_cancelled_receiver"
    )
    appt_booking_rejected.connect(
        on_booking_rejected, dispatch_uid="appt_booking_rejected_receiver"
    )
    appt_booking_completed.connect(
        on_booking_completed, dispatch_uid="appt_booking_completed_receiver"
    )
    appt_no_show_marked.connect(on_no_show_marked, dispatch_uid="appt_no_show_marked_receiver")
