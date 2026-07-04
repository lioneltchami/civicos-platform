"""
Management command to seed Celery beat periodic tasks into the database.

Idempotent — safe to run multiple times (uses update_or_create).
Should be called in the Docker entrypoint after migrate:

    python manage.py migrate --noinput
    python manage.py seed_periodic_tasks

Tasks seeded:
  - flush-expired-jwt-tokens  (daily @ 03:00 UTC)
  - check-sla-breaches        (every 15 minutes)
"""
from django.core.management.base import BaseCommand
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask


class Command(BaseCommand):
    help = "Seed Celery beat periodic tasks (idempotent)"

    def handle(self, *args, **options):
        self._seed_flush_expired_tokens()
        self._seed_check_sla_breaches()
        self._seed_cleanup_export_files()
        # Volunteer Management BB
        self._seed_check_expiring_screenings()
        self._seed_check_expiring_certifications()
        self._seed_send_monthly_hours_summary()
        self._seed_compute_volunteer_impact_snapshot()
        # Document Management BB
        self._seed_cleanup_stale_pending_uploads()
        self.stdout.write(self.style.SUCCESS("✓ Periodic tasks seeded successfully."))

    # ------------------------------------------------------------------

    def _seed_flush_expired_tokens(self):
        """Purge expired JWT blacklist tokens daily at 03:00 UTC."""
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute="0",
            hour="3",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        _, created = PeriodicTask.objects.update_or_create(
            name="flush-expired-jwt-tokens",
            defaults={
                "task": "auth_extension.flush_expired_jwt_tokens",
                "crontab": schedule,
                "interval": None,
                "solar": None,
                "clocked": None,
                "enabled": True,
                "description": (
                    "Purge expired JWT blacklist tokens daily to prevent "
                    "OutstandingToken/BlacklistedToken table bloat."
                ),
            },
        )
        verb = "created" if created else "updated"
        self.stdout.write(f"  flush-expired-jwt-tokens: {verb}")

    def _seed_check_sla_breaches(self):
        """Check for SLA breaches every 15 minutes."""
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute="*/15",
            hour="*",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        _, created = PeriodicTask.objects.update_or_create(
            name="check-sla-breaches",
            defaults={
                "task": "workflows.check_sla_breaches",
                "crontab": schedule,
                "interval": None,
                "solar": None,
                "clocked": None,
                "enabled": True,
                "description": (
                    "Mark overdue WorkItems as SLA-breached. "
                    "Runs every 15 minutes; idempotent."
                ),
            },
        )
        verb = "created" if created else "updated"
        self.stdout.write(f"  check-sla-breaches: {verb}")

    def _seed_cleanup_export_files(self):
        """Delete expired PIPEDA data export files daily at 04:00 UTC."""
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute="0",
            hour="4",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        _, created = PeriodicTask.objects.update_or_create(
            name="cleanup-export-files",
            defaults={
                "task": "consent.cleanup_export_files",
                "crontab": schedule,
                "interval": None,
                "solar": None,
                "clocked": None,
                "enabled": True,
                "description": (
                    "Mark expired PIPEDA data export requests and delete stored files. "
                    "Runs daily at 04:00 UTC."
                ),
            },
        )
        verb = "created" if created else "updated"
        self.stdout.write(f"  cleanup-export-files: {verb}")

    # ------------------------------------------------------------------
    # Volunteer Management BB
    # ------------------------------------------------------------------
    # IMPORTANT: Task names use short form ("volunteers.task_name") which requires
    # tasks in apps/volunteers/tasks.py to be registered with explicit name= args:
    #   @shared_task(name="volunteers.check_expiring_screenings")
    #   @shared_task(name="volunteers.check_expiring_certifications")
    #   @shared_task(name="volunteers.send_monthly_hours_summary")
    #   @shared_task(name="volunteers.compute_volunteer_impact_snapshot")
    # This matches the project-wide naming convention used by all other BBs.
    # ------------------------------------------------------------------

    def _seed_check_expiring_screenings(self):
        """Alert coordinators of VSC / PRC records expiring within 30 days — daily 08:00 Toronto."""
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute="0",
            hour="8",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="America/Toronto",
        )
        _, created = PeriodicTask.objects.update_or_create(
            name="volunteers-check-expiring-screenings",
            defaults={
                "task": "volunteers.check_expiring_screenings",
                "crontab": schedule,
                "interval": None,
                "solar": None,
                "clocked": None,
                "enabled": True,
                "description": (
                    "Dispatch screening_expiring signal for ScreeningRecord rows "
                    "whose expires_date falls within 30 days. "
                    "Runs daily at 08:00 America/Toronto."
                ),
            },
        )
        verb = "created" if created else "updated"
        self.stdout.write(f"  volunteers-check-expiring-screenings: {verb}")

    def _seed_check_expiring_certifications(self):
        """Alert coordinators of volunteer certifications expiring within 30 days — daily 08:00 Toronto."""
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute="0",
            hour="8",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="America/Toronto",
        )
        _, created = PeriodicTask.objects.update_or_create(
            name="volunteers-check-expiring-certifications",
            defaults={
                "task": "volunteers.check_expiring_certifications",
                "crontab": schedule,
                "interval": None,
                "solar": None,
                "clocked": None,
                "enabled": True,
                "description": (
                    "Dispatch certification_expiring signal for Certification rows "
                    "whose expires_date falls within 30 days. "
                    "Runs daily at 08:00 America/Toronto."
                ),
            },
        )
        verb = "created" if created else "updated"
        self.stdout.write(f"  volunteers-check-expiring-certifications: {verb}")

    def _seed_send_monthly_hours_summary(self):
        """Email each active volunteer their approved-hours summary for the prior month — 1st @ 09:00 Toronto."""
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute="0",
            hour="9",
            day_of_week="*",
            day_of_month="1",
            month_of_year="*",
            timezone="America/Toronto",
        )
        _, created = PeriodicTask.objects.update_or_create(
            name="volunteers-send-monthly-hours-summary",
            defaults={
                "task": "volunteers.send_monthly_hours_summary",
                "crontab": schedule,
                "interval": None,
                "solar": None,
                "clocked": None,
                "enabled": True,
                "description": (
                    "Send each active volunteer a monthly summary of their approved hours "
                    "for the prior calendar month. "
                    "Runs on the 1st of each month at 09:00 America/Toronto."
                ),
            },
        )
        verb = "created" if created else "updated"
        self.stdout.write(f"  volunteers-send-monthly-hours-summary: {verb}")

    def _seed_compute_volunteer_impact_snapshot(self):
        """Compute and store the monthly volunteer impact ReportSnapshot — 2nd @ 03:00 Toronto."""
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute="0",
            hour="3",
            day_of_week="*",
            day_of_month="2",
            month_of_year="*",
            timezone="America/Toronto",
        )
        _, created = PeriodicTask.objects.update_or_create(
            name="volunteers-compute-impact-snapshot",
            defaults={
                "task": "volunteers.compute_volunteer_impact_snapshot",
                "crontab": schedule,
                "interval": None,
                "solar": None,
                "clocked": None,
                "enabled": True,
                "description": (
                    "Aggregate volunteer hours, active volunteers, and milestone data "
                    "into a monthly ReportSnapshot for the Analytics & Reporting BB. "
                    "Runs on the 2nd of each month at 03:00 America/Toronto "
                    "(after the hours-summary run on the 1st)."
                ),
            },
        )
        verb = "created" if created else "updated"
        self.stdout.write(f"  volunteers-compute-impact-snapshot: {verb}")

    # ------------------------------------------------------------------
    # Document Management BB
    # ------------------------------------------------------------------

    def _seed_cleanup_stale_pending_uploads(self):
        """Delete Document rows stuck in PENDING_UPLOAD beyond the presigned URL TTL — every 30 min."""
        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=30,
            period=IntervalSchedule.MINUTES,
        )
        _, created = PeriodicTask.objects.update_or_create(
            name="documents-cleanup-stale-pending-uploads",
            defaults={
                "task": "apps.documents.tasks.cleanup_stale_pending_uploads",
                "interval": schedule,
                "crontab": None,
                "solar": None,
                "clocked": None,
                "enabled": True,
                "description": (
                    "Hard-delete Document rows stuck in PENDING_UPLOAD state beyond "
                    "the presigned URL TTL + 5-minute grace period. "
                    "Purges abandoned uploads where the browser never POSTed the file. "
                    "Runs every 30 minutes; idempotent."
                ),
            },
        )
        verb = "created" if created else "updated"
        self.stdout.write(f"  documents-cleanup-stale-pending-uploads: {verb}")
