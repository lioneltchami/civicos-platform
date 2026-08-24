"""
Appointments / Scheduling BB — Signal definitions.

All signals are defined here and dispatched via .send_robust() from service
functions to prevent a failing receiver from rolling back the originating
transaction.

Receivers are registered in apps/appointments/receivers.py (implemented in
Wave 3 alongside the booking service layer).

Signal naming convention: appt_<noun>_<past_tense_verb>
PIPEDA note: kwargs must contain only PKs and slugs — no email, name, or
             content that constitutes personal information.
"""

from django.dispatch import Signal

# ---------------------------------------------------------------------------
# Booking lifecycle (Wave 3 — service layer)
# ---------------------------------------------------------------------------

# Fired when a new Booking record is created (status=pending or confirmed).
# kwargs: booking_id (UUID str), slot_id (int), citizen_id (int), channel (str)
appt_booking_created = Signal()

# Fired when a pending Booking is confirmed by staff or auto-confirmed.
# kwargs: booking_id (UUID str), slot_id (int)
appt_booking_confirmed = Signal()

# Fired when a Booking is rejected by staff.
# kwargs: booking_id (UUID str), slot_id (int)
appt_booking_rejected = Signal()

# Fired when a Booking is cancelled (by citizen, staff, or system).
# kwargs: booking_id (UUID str), slot_id (int), cancelled_by_id (int), late (bool)
appt_booking_cancelled = Signal()

# Fired when a Booking is rescheduled.
# old_booking_id → cancelled with rescheduled=True; new_booking_id → confirmed.
# kwargs: old_booking_id (UUID str), new_booking_id (UUID str), slot_id (int)
appt_booking_rescheduled = Signal()

# Fired when a Booking is marked as completed.
# kwargs: booking_id (UUID str)
appt_booking_completed = Signal()

# Fired when a confirmed booking is flagged as a no-show.
# kwargs: booking_id (UUID str), citizen_id (int), no_show_count (int)
appt_no_show_marked = Signal()

# ---------------------------------------------------------------------------
# Waitlist lifecycle (Wave 4)
# ---------------------------------------------------------------------------

# Fired when a citizen joins the waitlist for a full slot.
# kwargs: waitlist_entry_id (int), slot_id (int), citizen_id (int), position (int)
appt_waitlist_joined = Signal()

# Fired when a waitlisted citizen is notified of an available slot.
# kwargs: waitlist_entry_id (int), acceptance_deadline (ISO datetime str)
appt_waitlist_notified = Signal()

# Fired when a waitlist notification expires without acceptance.
# kwargs: waitlist_entry_id (int), slot_id (int)
appt_waitlist_expired = Signal()

# ---------------------------------------------------------------------------
# Reminder lifecycle (Wave 6 — Celery tasks)
# ---------------------------------------------------------------------------

# Fired after each reminder email/SMS is dispatched successfully.
# kwargs: booking_id (UUID str), reminder_type (str: "72h", "24h", "2h")
appt_reminder_sent = Signal()
