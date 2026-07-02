"""
Volunteer Management BB — Celery tasks.

Periodic tasks for shift reminders and expiry checks. All tasks are routed to
the "volunteers" queue via CELERY_TASK_ROUTES in config/settings/base.py:
    "apps.volunteers.tasks.*": {"queue": "volunteers"}

Beat schedule:
    This project uses django_celery_beat's DatabaseScheduler
    (CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler").
    Periodic tasks are NOT hardcoded in settings.CELERY_BEAT_SCHEDULE. They
    must be registered either:
      - via Django admin → Periodic Tasks, or
      - via the create_beat_schedule() helper at the bottom of this file
        (call it from a data migration or a management command).

    Suggested schedule:
      send_shift_reminders_24h  — every hour (crontab: minute=0)
      send_shift_reminders_2h   — every hour (crontab: minute=0)
      check_expiring_screenings  — daily at 08:00 America/Toronto
      check_expiring_certifications — daily at 08:00 America/Toronto

Idempotency guarantees:
    - Reminder tasks use an atomic update-with-filter pattern (optimistic lock)
      so retries and concurrent workers never double-send.
    - Expiry tasks fire signals; receivers must be idempotent (they are — the
      notification service deduplicates at the email layer).

PIPEDA:
    Log messages contain PKs only — never names, emails, or any PII.
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format as django_date_format
from datetime import timedelta

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task 1: 24-hour shift reminders
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_shift_reminders_24h(self):
    """
    Find all confirmed shift bookings whose shift starts in the 23h–25h window
    from now and whose 24h reminder has not been sent, then send it.

    Scheduled: every hour via Celery Beat (see module docstring).

    The 2-hour window (23h–25h) ensures no booking falls through a gap between
    hourly runs, even if a run is slightly delayed.

    Idempotency: the update-with-filter pattern atomically sets reminder_24h_sent
    before firing the notification. A concurrent worker or retry sees updated==0
    and skips cleanly.
    """
    from apps.volunteers.models import ShiftBooking
    from apps.notifications.services import send_email_notification

    now = timezone.now()
    window_start = now + timedelta(hours=23)
    window_end = now + timedelta(hours=25)

    bookings = (
        ShiftBooking.objects.filter(
            status=ShiftBooking.STATUS_CONFIRMED,
            reminder_24h_sent=False,
            shift__start_datetime__gte=window_start,
            shift__start_datetime__lte=window_end,
            shift__is_cancelled=False,
        )
        .select_related("shift__opportunity__program", "shift", "volunteer__user")
    )

    sent = 0
    skipped = 0
    error_count = 0

    for booking in bookings:
        try:
            # Atomic mark-before-send: prevents duplicate sends on retry or
            # concurrent worker. Also rechecks status so a booking cancelled
            # between queryset fetch and this update is never marked or emailed.
            with transaction.atomic():
                updated = ShiftBooking.objects.filter(
                    pk=booking.pk,
                    reminder_24h_sent=False,
                    status=ShiftBooking.STATUS_CONFIRMED,
                ).update(reminder_24h_sent=True)

            if updated == 0:
                skipped += 1
                logger.debug(
                    "volunteers.tasks.send_shift_reminders_24h: booking #%s "
                    "already marked or no longer confirmed — skipping.",
                    booking.pk,
                )
                continue

            local_start = timezone.localtime(booking.shift.start_datetime)
            local_end = timezone.localtime(booking.shift.end_datetime)
            context = {
                "shift_title": booking.shift.opportunity.get_title(),
                "shift_start": django_date_format(local_start, "DATETIME_FORMAT"),
                "shift_end": django_date_format(local_end, "DATETIME_FORMAT"),
                "shift_location": booking.shift.location or "",
                "volunteer_display": booking.volunteer.display_name,
                "portal_url": settings.SITE_URL + reverse("volunteers:my_shifts"),
            }

            # Notify the volunteer directly (no reminder signal declared).
            # Receivers in receivers.py handle application-lifecycle signals;
            # reminder delivery goes straight to the notifications service.
            volunteer_user = booking.volunteer.user
            send_email_notification(
                recipient=volunteer_user,
                subject_key="volunteer_shift_reminder_24h",
                context=context,
            )
            sent += 1
            logger.info(
                "volunteers.tasks.send_shift_reminders_24h: sent reminder for "
                "booking #%s (shift #%s, volunteer profile #%s).",
                booking.pk,
                booking.shift_id,
                booking.volunteer_id,
            )

        except Exception as exc:
            # Log and continue — one failed email must not abort the remaining
            # bookings. The flag was already set atomically above, so this
            # booking's reminder is missed for this run (preferred over aborting
            # all remaining reminders or double-sending on retry).
            error_count += 1
            logger.error(
                "volunteers.tasks.send_shift_reminders_24h: failed for "
                "booking #%s: %s — continuing with remaining bookings.",
                booking.pk,
                exc,
            )

    logger.info(
        "volunteers.tasks.send_shift_reminders_24h: complete — "
        "sent=%d skipped=%d errors=%d window=[%s, %s].",
        sent,
        skipped,
        error_count,
        window_start.isoformat(),
        window_end.isoformat(),
    )
    return {"sent": sent, "skipped": skipped, "errors": error_count}


# ---------------------------------------------------------------------------
# Task 2: 2-hour shift reminders
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_shift_reminders_2h(self):
    """
    Find all confirmed shift bookings whose shift starts in the 1h–3h window
    from now and whose 2h reminder has not been sent, then send it.

    Scheduled: every hour via Celery Beat (see module docstring).

    Same idempotency pattern as send_shift_reminders_24h.
    """
    from apps.volunteers.models import ShiftBooking
    from apps.notifications.services import send_email_notification

    now = timezone.now()
    window_start = now + timedelta(hours=1)
    window_end = now + timedelta(hours=3)

    bookings = (
        ShiftBooking.objects.filter(
            status=ShiftBooking.STATUS_CONFIRMED,
            reminder_2h_sent=False,
            shift__start_datetime__gte=window_start,
            shift__start_datetime__lte=window_end,
            shift__is_cancelled=False,
        )
        .select_related("shift__opportunity__program", "shift", "volunteer__user")
    )

    sent = 0
    skipped = 0
    error_count = 0

    for booking in bookings:
        try:
            # Atomic mark-before-send: also rechecks status so a booking
            # cancelled between queryset fetch and this update is never
            # marked or emailed.
            with transaction.atomic():
                updated = ShiftBooking.objects.filter(
                    pk=booking.pk,
                    reminder_2h_sent=False,
                    status=ShiftBooking.STATUS_CONFIRMED,
                ).update(reminder_2h_sent=True)

            if updated == 0:
                skipped += 1
                logger.debug(
                    "volunteers.tasks.send_shift_reminders_2h: booking #%s "
                    "already marked or no longer confirmed — skipping.",
                    booking.pk,
                )
                continue

            local_start = timezone.localtime(booking.shift.start_datetime)
            local_end = timezone.localtime(booking.shift.end_datetime)
            context = {
                "shift_title": booking.shift.opportunity.get_title(),
                "shift_start": django_date_format(local_start, "DATETIME_FORMAT"),
                "shift_end": django_date_format(local_end, "DATETIME_FORMAT"),
                "shift_location": booking.shift.location or "",
                "volunteer_display": booking.volunteer.display_name,
                "portal_url": settings.SITE_URL + reverse("volunteers:my_shifts"),
            }

            volunteer_user = booking.volunteer.user
            send_email_notification(
                recipient=volunteer_user,
                subject_key="volunteer_shift_reminder_2h",
                context=context,
            )
            sent += 1
            logger.info(
                "volunteers.tasks.send_shift_reminders_2h: sent reminder for "
                "booking #%s (shift #%s, volunteer profile #%s).",
                booking.pk,
                booking.shift_id,
                booking.volunteer_id,
            )

        except Exception as exc:
            # Log and continue — one failed email must not abort the remaining
            # bookings.
            error_count += 1
            logger.error(
                "volunteers.tasks.send_shift_reminders_2h: failed for "
                "booking #%s: %s — continuing with remaining bookings.",
                booking.pk,
                exc,
            )

    logger.info(
        "volunteers.tasks.send_shift_reminders_2h: complete — "
        "sent=%d skipped=%d errors=%d window=[%s, %s].",
        sent,
        skipped,
        error_count,
        window_start.isoformat(),
        window_end.isoformat(),
    )
    return {"sent": sent, "skipped": skipped, "errors": error_count}


# ---------------------------------------------------------------------------
# Task 3: Expiring screening checks
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def check_expiring_screenings(self):
    """
    Fire the screening_expiring signal for every ScreeningRecord that expires
    within the next 30 days (inclusive of today, exclusive of already-expired).

    Scheduled: daily at 08:00 America/Toronto via Celery Beat.

    No deduplication flag exists on ScreeningRecord — this task fires daily;
    the notification service / receiver must be idempotent (it is: the email
    layer deduplicates). The screening_expiring signal is already declared in
    signals.py and handled by notify_on_screening_expiring in receivers.py.
    """
    from apps.volunteers.models import ScreeningRecord
    from apps.volunteers.signals import screening_expiring

    today = timezone.localtime(timezone.now()).date()
    expiry_window = today + timedelta(days=30)

    records = (
        ScreeningRecord.objects.filter(
            expires_date__gte=today,
            expires_date__lte=expiry_window,
        )
        .select_related("volunteer__user")
    )

    fired = 0
    errors = 0

    for record in records:
        try:
            # send_robust() ensures a failing receiver never raises here.
            responses = screening_expiring.send_robust(
                sender=ScreeningRecord,
                instance=record,
                volunteer=record.volunteer,
                check_type=record.check_type,
            )
            for _receiver, response in responses:
                if isinstance(response, Exception):
                    logger.error(
                        "volunteers.tasks.check_expiring_screenings: receiver "
                        "error for ScreeningRecord #%s (volunteer profile #%s): %s",
                        record.pk,
                        record.volunteer_id,
                        response,
                    )
                    errors += 1

            fired += 1
            logger.info(
                "volunteers.tasks.check_expiring_screenings: fired signal for "
                "ScreeningRecord #%s (volunteer profile #%s, expires %s).",
                record.pk,
                record.volunteer_id,
                record.expires_date,
            )

        except Exception as exc:
            logger.error(
                "volunteers.tasks.check_expiring_screenings: unexpected error "
                "for ScreeningRecord #%s: %s",
                record.pk,
                exc,
            )
            errors += 1

    logger.info(
        "volunteers.tasks.check_expiring_screenings: complete — "
        "fired=%d receiver_errors=%d window=[%s, %s].",
        fired,
        errors,
        today.isoformat(),
        expiry_window.isoformat(),
    )
    return {"fired": fired, "receiver_errors": errors}


# ---------------------------------------------------------------------------
# Task 4: Expiring certifications
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def check_expiring_certifications(self):
    """
    Fire the certification_expiring signal for every Certification that expires
    within the next 30 days (inclusive of today, exclusive of already-expired).

    Scheduled: daily at 08:00 America/Toronto via Celery Beat.

    Same pattern as check_expiring_screenings. The certification_expiring signal
    is declared in signals.py; a receiver should be wired in receivers.py (Wave 3
    deliverable).
    """
    from apps.volunteers.models import Certification
    from apps.volunteers.signals import certification_expiring

    today = timezone.localtime(timezone.now()).date()
    expiry_window = today + timedelta(days=30)

    records = (
        Certification.objects.filter(
            expires_date__gte=today,
            expires_date__lte=expiry_window,
        )
        .select_related("volunteer__user")
    )

    fired = 0
    errors = 0

    for record in records:
        try:
            responses = certification_expiring.send_robust(
                sender=Certification,
                instance=record,
                volunteer=record.volunteer,
                cert_type=record.cert_type,
            )
            for _receiver, response in responses:
                if isinstance(response, Exception):
                    logger.error(
                        "volunteers.tasks.check_expiring_certifications: receiver "
                        "error for Certification #%s (volunteer profile #%s): %s",
                        record.pk,
                        record.volunteer_id,
                        response,
                    )
                    errors += 1

            fired += 1
            logger.info(
                "volunteers.tasks.check_expiring_certifications: fired signal for "
                "Certification #%s (volunteer profile #%s, expires %s).",
                record.pk,
                record.volunteer_id,
                record.expires_date,
            )

        except Exception as exc:
            logger.error(
                "volunteers.tasks.check_expiring_certifications: unexpected error "
                "for Certification #%s: %s",
                record.pk,
                exc,
            )
            errors += 1

    logger.info(
        "volunteers.tasks.check_expiring_certifications: complete — "
        "fired=%d receiver_errors=%d window=[%s, %s].",
        fired,
        errors,
        today.isoformat(),
        expiry_window.isoformat(),
    )
    return {"fired": fired, "receiver_errors": errors}


# ---------------------------------------------------------------------------
# Beat schedule helper (DatabaseScheduler — call from a data migration)
# ---------------------------------------------------------------------------

def create_beat_schedule():
    """
    Register all four periodic tasks in django_celery_beat's PeriodicTask table.

    Call this from a data migration or a management command after django_celery_beat
    migrations have run. Safe to call repeatedly — uses get_or_create.

    Example data migration (0002_add_volunteer_beat_tasks.py):

        from django.db import migrations
        from apps.volunteers.tasks import create_beat_schedule

        class Migration(migrations.Migration):
            dependencies = [
                ("django_celery_beat", "0018_improve_crontab_helptext"),
                ("volunteers", "0001_initial"),
            ]
            operations = [
                migrations.RunPython(
                    lambda apps, schema_editor: create_beat_schedule(),
                    migrations.RunPython.noop,
                )
            ]
    """
    from django_celery_beat.models import CrontabSchedule, PeriodicTask
    import json

    # Every hour on the hour (UTC) — reminder windows are large enough to be tz-safe.
    hourly, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="*",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
        timezone="UTC",
    )

    # 08:00 America/Toronto daily.
    daily_morning, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="8",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
        timezone="America/Toronto",
    )

    tasks = [
        {
            "name": "volunteers: 24h shift reminders (hourly)",
            "task": "apps.volunteers.tasks.send_shift_reminders_24h",
            "crontab": hourly,
        },
        {
            "name": "volunteers: 2h shift reminders (hourly)",
            "task": "apps.volunteers.tasks.send_shift_reminders_2h",
            "crontab": hourly,
        },
        {
            "name": "volunteers: check expiring screenings (daily 08:00 ET)",
            "task": "apps.volunteers.tasks.check_expiring_screenings",
            "crontab": daily_morning,
        },
        {
            "name": "volunteers: check expiring certifications (daily 08:00 ET)",
            "task": "apps.volunteers.tasks.check_expiring_certifications",
            "crontab": daily_morning,
        },
    ]

    for spec in tasks:
        obj, created = PeriodicTask.objects.get_or_create(
            name=spec["name"],
            defaults={
                "task": spec["task"],
                "crontab": spec["crontab"],
                "args": json.dumps([]),
                "enabled": True,
            },
        )
        if not created:
            # Ensure task name and crontab are up to date if re-run.
            obj.task = spec["task"]
            obj.crontab = spec["crontab"]
            obj.enabled = True
            obj.save(update_fields=["task", "crontab", "enabled"])
