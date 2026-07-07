"""
Appointments BB — Celery tasks.

Wave 2 tasks (implemented here):
  generate_slots_for_period   — generate Slot records for the upcoming horizon window
  mark_past_slots_completed   — transition past Slots to 'completed' status

Later waves will add:
  Wave 3: send_appointment_reminder, expire_pending_bookings
  Wave 4: send_waitlist_notification, detect_no_shows, expire_waitlist_notifications
  Wave 6: iCal reminder email dispatch

Security invariants (apply to ALL tasks):
  - NEVER include PII (citizen email, name) in task args or log messages — use PKs only.
  - Tasks MUST be idempotent (safe to retry on transient failure).
  - record_event() MUST be called inside transaction.atomic() (Wave 3+).
  - Use select_for_update() inside atomic() for capacity/status checks (Wave 3+).
  - acks_late=True on all tasks — Celery will not ACK until the task returns,
    preventing message loss on worker crash.
  - reject_on_worker_lost=True — task will be requeued if worker dies.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Wave 2: generate_slots_for_period
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    name="appointments.generate_slots_for_period",
    max_retries=3,
    default_retry_delay=300,  # 5 minutes
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=3300,  # 55 min — catch overruns and log cleanly before hard kill
    time_limit=3600,       # 60 min hard kill
)
def generate_slots_for_period(self, horizon_days: int | None = None) -> dict:
    """
    Generate Slot records for all active staff × active appointment_type
    combinations over the upcoming horizon window.

    Idempotent: generate_slots_for_range() skips slots that already exist
    for the same (staff, appointment_type, start_datetime) combination.

    Called by: Celery Beat daily at 02:00 UTC.

    Args:
        horizon_days: Days ahead to generate slots for.
                      Defaults to CIVICOS['APPOINTMENTS']['SLOT_GENERATION_HORIZON_DAYS'].

    Returns:
        {"slots_created": N, "combinations_processed": M}
    """
    from django.conf import settings

    from apps.appointments.models import AppointmentType, StaffProfile
    from apps.appointments.services.slots import generate_slots_for_range

    if horizon_days is None:
        appt_settings = settings.CIVICOS.get("APPOINTMENTS", {})
        horizon_days = appt_settings.get("SLOT_GENERATION_HORIZON_DAYS", 60)

    today = timezone.now().date()
    date_to = today + timedelta(days=horizon_days)

    # Fetch active appointment types
    active_appt_types = list(
        AppointmentType.objects.filter(is_active=True)
    )
    active_appt_type_pks = {at.pk for at in active_appt_types}
    appt_type_by_pk = {at.pk: at for at in active_appt_types}

    # Fetch active staff with at least one linked appointment type.
    # Wrapped in try so a DB connection failure at fetch time retries the task.
    # list() forces queryset evaluation here rather than lazily at iteration —
    # this ensures the outer except actually catches a connection error at fetch
    # time. Do NOT use .iterator() — it disables Django's result cache and
    # thereby silently drops prefetch_related("appointment_types"), causing an
    # extra DB query per staff member (N+1). The queryset is bounded by
    # active/accepting staff so loading it fully into memory is acceptable.
    try:
        staff_list = list(
            StaffProfile.objects.filter(
                is_accepting_bookings=True,
                location__is_active=True,
            )
            .select_related("location")
            .prefetch_related("appointment_types")
        )
    except Exception as exc:
        logger.exception(
            "generate_slots_for_period: failed to fetch staff queryset: %s",
            type(exc).__name__,
        )
        raise self.retry(exc=exc)

    total_created = 0
    combinations = 0

    # Iterate and process — per-combination errors are logged but do not abort
    # the run or retry the whole task.
    try:
        for staff_member in staff_list:
            staff_appt_type_pks = set(
                staff_member.appointment_types.values_list("pk", flat=True)
            )
            eligible_pks = staff_appt_type_pks & active_appt_type_pks

            for appt_type_pk in eligible_pks:
                appt_type = appt_type_by_pk[appt_type_pk]
                try:
                    created = generate_slots_for_range(
                        appointment_type=appt_type,
                        staff=staff_member,
                        date_from=today,
                        date_to=date_to,
                        created_by_task=True,
                    )
                    total_created += created
                    combinations += 1
                except Exception as exc:
                    # Log but don't abort — continue with other combinations
                    logger.error(
                        "generate_slots_for_period: error for staff_id=%s, "
                        "appointment_type_id=%s: %s",
                        staff_member.pk,
                        appt_type_pk,
                        type(exc).__name__,
                    )
    except SoftTimeLimitExceeded:
        logger.warning(
            "generate_slots_for_period soft time limit reached; "
            "processed partial staff list. Task will be re-queued on next Beat trigger."
        )
        # Do NOT re-raise — let the task complete gracefully with partial results.
        # The next nightly run will cover any missed staff members.

    logger.info(
        "generate_slots_for_period: %d slots created across %d staff×type combinations "
        "(horizon=%d days)",
        total_created,
        combinations,
        horizon_days,
    )
    return {"slots_created": total_created, "combinations_processed": combinations}


# ---------------------------------------------------------------------------
# Wave 2: mark_past_slots_completed
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    name="appointments.mark_past_slots_completed",
    max_retries=3,
    default_retry_delay=300,  # 5 minutes — consistent with generate_slots_for_period
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=270,  # catch overruns before the hard kill
    time_limit=300,       # hard kill — explicit, self-documenting
)
def mark_past_slots_completed(self) -> dict:
    """
    Transition all past Slot records in non-terminal statuses to 'completed'.

    A slot is considered 'past' when its end_datetime < now (UTC).
    Only slots in status 'available', 'partial', or 'full' are transitioned —
    'blocked' and 'cancelled' slots remain as-is (they are already terminal
    in the context of booking availability).

    Idempotent: safe to run multiple times. Each run only affects newly-past slots.

    Called by: Celery Beat daily at 23:30 UTC.

    Returns:
        {"slots_updated": N}
    """
    from apps.appointments.models import Slot

    try:
        now = timezone.now()
        try:
            count = Slot.objects.filter(
                end_datetime__lt=now,
                status__in=["available", "partial", "full"],
            ).update(status="completed")
        except SoftTimeLimitExceeded:
            logger.warning(
                "mark_past_slots_completed: soft time limit reached before update "
                "completed. Remaining slots will be processed on the next Beat trigger."
            )
            return {"slots_updated": 0, "timed_out": True}

        logger.info("mark_past_slots_completed: %d slots marked completed", count)
        return {"slots_updated": count}

    except Exception as exc:
        logger.exception(
            "mark_past_slots_completed: error — retrying. error_type=%s",
            type(exc).__name__,
        )
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Wave 3: cleanup_expired_pending_bookings
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    name="appointments.cleanup_expired_pending_bookings",
    max_retries=3,
    default_retry_delay=300,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=270,
    time_limit=300,
)
def cleanup_expired_pending_bookings(self) -> dict:
    """
    Cancel PENDING bookings that have exceeded the pending timeout window.

    Timeout: CIVICOS['APPOINTMENTS']['PENDING_BOOKING_TIMEOUT_MINUTES'] (default 30).
    Idempotent: safe to run multiple times.
    Called by Celery Beat every 15 minutes.

    Returns:
        {"bookings_cancelled": N}
    """
    from django.conf import settings

    from apps.appointments.models import Booking
    from apps.appointments.services.booking import cancel_booking

    try:
        appt_cfg = settings.CIVICOS.get("APPOINTMENTS", {})
        timeout_minutes = appt_cfg.get("PENDING_BOOKING_TIMEOUT_MINUTES", 30)
        cutoff = timezone.now() - timedelta(minutes=timeout_minutes)

        expired_pks = list(
            Booking.objects.filter(
                status="pending",
                created_at__lt=cutoff,
            ).values_list("pk", flat=True)[:200]  # Max 200 per run
        )

        cancelled = 0
        for pk in expired_pks:
            try:
                booking = Booking.objects.get(pk=pk)
                if booking.status != "pending":
                    continue  # Race: already changed
                cancel_booking(booking=booking, actor=None, reason="Pending timeout exceeded.")
                cancelled += 1
            except Booking.DoesNotExist:
                pass
            except Exception as exc:
                logger.error(
                    "cleanup_expired_pending_bookings: error booking_id=%s: %s",
                    pk, type(exc).__name__,
                )

        logger.info(
            "cleanup_expired_pending_bookings: %d/%d expired pending bookings cancelled",
            cancelled, len(expired_pks),
        )
        return {"bookings_cancelled": cancelled}

    except Exception as exc:
        logger.exception(
            "cleanup_expired_pending_bookings: unhandled error — retrying. error_type=%s",
            type(exc).__name__,
        )
        raise self.retry(exc=exc)
