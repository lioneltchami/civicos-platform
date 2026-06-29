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
from django_celery_beat.models import CrontabSchedule, PeriodicTask


class Command(BaseCommand):
    help = "Seed Celery beat periodic tasks (idempotent)"

    def handle(self, *args, **options):
        self._seed_flush_expired_tokens()
        self._seed_check_sla_breaches()
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
