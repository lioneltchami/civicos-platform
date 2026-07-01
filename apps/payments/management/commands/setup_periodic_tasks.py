"""
Management command: setup_periodic_tasks

Registers Celery Beat periodic tasks via django-celery-beat's database
scheduler.  Safe to run multiple times (idempotent — uses get_or_create).

Usage:
    python manage.py setup_periodic_tasks

Tasks registered:
  - generate_annual_receipts: runs at 08:00 on January 2nd each year
    (America/Toronto).  No args/kwargs are stored in the Beat schedule;
    instead, a thin annual-kickoff task wrapper (below) resolves the
    correct tax_year at runtime and chains into generate_annual_receipts.

    If you prefer to store the year statically (and update it yearly), run:
        python manage.py setup_periodic_tasks --static-year=2025

Why January 2nd and not January 1st?
  - January 1st is a national statutory holiday in Canada.  Donors who
    receive CRA tax receipts that day face delivery issues and confusion.
    January 2nd is the first business-adjacent day of the new year.

Why 08:00 America/Toronto?
  - Donors receive their receipts at the start of the business day in the
    charity's home timezone.
"""
from __future__ import annotations

import json
import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger("apps.payments.management.setup_periodic_tasks")


class Command(BaseCommand):
    help = "Register Celery Beat periodic tasks via django-celery-beat (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be created without writing to the database.",
        )
        parser.add_argument(
            "--static-year",
            type=int,
            default=None,
            help=(
                "If provided, store this integer as the tax_year arg in the Beat "
                "schedule.  Useful for backfill runs.  Omit to use the kickoff wrapper "
                "task that auto-resolves to (current_year - 1) at runtime."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        static_year = options["static_year"]

        # ── Annual receipt task ────────────────────────────────────────────
        # Crontab: 08:00 on January 2nd each year (America/Toronto).
        if static_year is not None:
            task_path = "apps.payments.tasks_receipts.generate_annual_receipts"
            task_args = json.dumps([static_year])
            description = (
                f"Issue annual consolidated CRA donation tax receipts for tax year "
                f"{static_year}.  Runs 08:00 EST on January 2nd."
            )
        else:
            task_path = "apps.payments.tasks_receipts.kickoff_annual_receipts"
            task_args = json.dumps([])
            description = (
                "Issue annual consolidated CRA donation tax receipts for the previous "
                "calendar year.  Runs 08:00 EST on January 2nd each year.  "
                "Resolves tax_year = (current_year - 1) at runtime."
            )

        self._register_task(
            dry_run=dry_run,
            task_name="payments.generate_annual_receipts",
            task_path=task_path,
            schedule_kwargs={
                "minute": "0",
                "hour": "8",
                "day_of_week": "*",
                "day_of_month": "2",
                "month_of_year": "1",
                "timezone": "America/Toronto",
            },
            task_args=task_args,
            task_kwargs_str=json.dumps({}),
            description=description,
        )

        # ── Reports BB: compute_monthly_snapshots ──────────────────────────
        # Crontab: 02:00 on the 2nd of each month (America/Toronto).
        # Runs after midnight when the previous month's data is fully settled.
        # January 2nd is intentional: gives the annual receipt task (Jan 2, 08:00)
        # time to complete before the snapshot overwrites the December data.
        self._register_task(
            dry_run=dry_run,
            task_name="reports.compute_monthly_snapshots",
            task_path="apps.reports.tasks.compute_monthly_snapshots",
            schedule_kwargs={
                "minute": "0",
                "hour": "2",
                "day_of_week": "*",
                "day_of_month": "2",
                "month_of_year": "*",
                "timezone": "America/Toronto",
            },
            task_args="[]",
            task_kwargs_str="{}",
            description=(
                "Compute Analytics & Reporting BB monthly snapshots for the previous "
                "calendar month.  Runs at 02:00 Toronto time on the 2nd of each month."
            ),
        )

        self.stdout.write(self.style.SUCCESS("\nsetup_periodic_tasks complete.\n"))

    def _register_task(
        self,
        *,
        dry_run: bool,
        task_name: str,
        task_path: str,
        schedule_kwargs: dict,
        task_args: str,
        task_kwargs_str: str,
        description: str,
    ) -> None:
        """Register a single Celery Beat PeriodicTask (idempotent)."""
        import json as _json
        from django_celery_beat.models import CrontabSchedule, PeriodicTask

        self.stdout.write(
            f"\nPeriodic task : {task_name!r}\n"
            f"  Celery task : {task_path}\n"
            f"  Crontab     : {schedule_kwargs['minute']} {schedule_kwargs['hour']} "
            f"{schedule_kwargs['day_of_month']} {schedule_kwargs['month_of_year']} "
            f"{schedule_kwargs['day_of_week']} (TZ: {schedule_kwargs['timezone']})\n"
            f"  Args        : {task_args}\n"
            f"  Kwargs      : {task_kwargs_str}\n"
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("  Dry run — skipping.\n"))
            return

        crontab, crontab_created = CrontabSchedule.objects.get_or_create(**schedule_kwargs)
        status_str = "Created" if crontab_created else "Reusing"
        self.stdout.write(f"  {status_str} crontab schedule pk={crontab.pk}")

        task, task_created = PeriodicTask.objects.get_or_create(
            name=task_name,
            defaults={
                "task": task_path,
                "crontab": crontab,
                "args": task_args,
                "kwargs": task_kwargs_str,
                "enabled": True,
                "description": description,
            },
        )

        if task_created:
            self.stdout.write(self.style.SUCCESS(f"  Created PeriodicTask pk={task.pk}"))
        else:
            changed_fields = []
            if task.crontab_id != crontab.pk:
                task.crontab = crontab
                changed_fields.append("crontab")
            if task.task != task_path:
                task.task = task_path
                changed_fields.append("task")
            if task.args != task_args:
                task.args = task_args
                changed_fields.append("args")
            if not task.enabled:
                task.enabled = True
                changed_fields.append("enabled")
            if changed_fields:
                task.save(update_fields=changed_fields)
                self.stdout.write(
                    self.style.WARNING(
                        f"  Updated PeriodicTask pk={task.pk} fields={changed_fields}"
                    )
                )
            else:
                self.stdout.write(f"  PeriodicTask pk={task.pk} is already up-to-date")

        logger.info(
            "setup_periodic_tasks.registered task=%s crontab_pk=%s task_pk=%s",
            task_name,
            crontab.pk,
            task.pk,
        )
