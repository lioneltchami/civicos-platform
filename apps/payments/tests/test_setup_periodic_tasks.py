"""
Tests for apps/payments/management/commands/setup_periodic_tasks.py

Covers all branches:
- Basic run creates crontab + PeriodicTask
- Second run (idempotent) reuses existing crontab and task
- --dry-run flag: prints description but writes nothing to DB
- --static-year flag: uses the real task path with a fixed year arg
- Idempotent update branches: crontab_id mismatch, task path mismatch,
  args mismatch, task disabled → re-enabled, no changes (already up-to-date)
"""

import json
from io import StringIO

from django.core.management import call_command
from django.test import TestCase


def _call_setup(stdout=None, **kwargs):
    out = stdout or StringIO()
    call_command("setup_periodic_tasks", stdout=out, **kwargs)
    return out.getvalue()


class SetupPeriodicTasksBasicTest(TestCase):
    def test_creates_crontab_and_periodic_task(self):
        from django_celery_beat.models import CrontabSchedule, PeriodicTask

        _call_setup()

        self.assertEqual(
            CrontabSchedule.objects.filter(
                minute="0", hour="8", day_of_month="2", month_of_year="1"
            ).count(),
            1,
        )
        self.assertTrue(
            PeriodicTask.objects.filter(name="payments.generate_annual_receipts").exists()
        )

    def test_creates_task_with_kickoff_task_path(self):
        from django_celery_beat.models import PeriodicTask

        _call_setup()

        task = PeriodicTask.objects.get(name="payments.generate_annual_receipts")
        self.assertEqual(task.task, "apps.payments.tasks_receipts.kickoff_annual_receipts")

    def test_creates_task_enabled(self):
        from django_celery_beat.models import PeriodicTask

        _call_setup()

        task = PeriodicTask.objects.get(name="payments.generate_annual_receipts")
        self.assertTrue(task.enabled)

    def test_output_contains_task_name(self):
        output = _call_setup()
        self.assertIn("payments.generate_annual_receipts", output)

    def test_output_contains_crontab_info(self):
        output = _call_setup()
        # Should print "0 8 2 1 *" in some form
        self.assertIn("0", output)
        self.assertIn("8", output)

    def test_output_contains_completion_message(self):
        output = _call_setup()
        self.assertIn("setup_periodic_tasks complete", output)

    def test_created_output_message(self):
        output = _call_setup()
        self.assertIn("Created", output)


class SetupPeriodicTasksIdempotentTest(TestCase):
    """Running the command twice should not raise and should reuse existing objects."""

    def test_second_run_does_not_create_duplicate_crontab(self):
        from django_celery_beat.models import CrontabSchedule

        _call_setup()
        _call_setup()

        self.assertEqual(
            CrontabSchedule.objects.filter(
                minute="0", hour="8", day_of_month="2", month_of_year="1"
            ).count(),
            1,
        )

    def test_second_run_does_not_create_duplicate_task(self):
        from django_celery_beat.models import PeriodicTask

        _call_setup()
        _call_setup()

        self.assertEqual(
            PeriodicTask.objects.filter(name="payments.generate_annual_receipts").count(),
            1,
        )

    def test_second_run_outputs_already_up_to_date(self):
        _call_setup()
        output = _call_setup()
        self.assertIn("already up-to-date", output)

    def test_idempotent_multiple_runs(self):
        """Three consecutive runs should all succeed without error."""
        from django_celery_beat.models import PeriodicTask

        for _ in range(3):
            _call_setup()

        self.assertEqual(
            PeriodicTask.objects.filter(name="payments.generate_annual_receipts").count(),
            1,
        )


class SetupPeriodicTasksDryRunTest(TestCase):
    def test_dry_run_prints_warning(self):
        output = _call_setup(dry_run=True)
        self.assertIn("Dry run", output)

    def test_dry_run_does_not_create_crontab(self):
        from django_celery_beat.models import CrontabSchedule

        # Documents BB seed migrations may pre-create CrontabSchedule rows.
        # We only care that dry_run does NOT add any NEW rows.
        count_before = CrontabSchedule.objects.count()
        _call_setup(dry_run=True)

        self.assertEqual(CrontabSchedule.objects.count(), count_before)

    def test_dry_run_does_not_create_periodic_task(self):
        from django_celery_beat.models import PeriodicTask

        _call_setup(dry_run=True)

        self.assertFalse(
            PeriodicTask.objects.filter(name="payments.generate_annual_receipts").exists()
        )

    def test_dry_run_still_prints_task_info(self):
        output = _call_setup(dry_run=True)
        self.assertIn("payments.generate_annual_receipts", output)


class SetupPeriodicTasksStaticYearTest(TestCase):
    def test_static_year_uses_real_task_path(self):
        from django_celery_beat.models import PeriodicTask

        _call_setup(static_year=2025)

        task = PeriodicTask.objects.get(name="payments.generate_annual_receipts")
        self.assertEqual(task.task, "apps.payments.tasks_receipts.generate_annual_receipts")

    def test_static_year_stores_year_in_args(self):
        from django_celery_beat.models import PeriodicTask

        _call_setup(static_year=2025)

        task = PeriodicTask.objects.get(name="payments.generate_annual_receipts")
        self.assertEqual(json.loads(task.args), [2025])

    def test_static_year_in_output(self):
        output = _call_setup(static_year=2025)
        self.assertIn("2025", output)

    def test_static_year_dry_run_no_db_writes(self):
        from django_celery_beat.models import PeriodicTask

        _call_setup(static_year=2024, dry_run=True)

        self.assertFalse(
            PeriodicTask.objects.filter(name="payments.generate_annual_receipts").exists()
        )


class SetupPeriodicTasksUpdateBranchesTest(TestCase):
    """
    Test the idempotent-update branches inside the `else` block:
    - task.crontab_id != crontab.pk  → updates crontab field
    - task.task != task_path         → updates task field
    - task.args != task_args         → updates args field
    - not task.enabled               → re-enables the task
    - no changes                     → logs "already up-to-date"
    """

    def _create_initial_task(self):
        """Create the crontab and task via a first run."""
        _call_setup()
        from django_celery_beat.models import PeriodicTask

        return PeriodicTask.objects.get(name="payments.generate_annual_receipts")

    def test_disabled_task_gets_re_enabled(self):
        """If someone disables the task manually, a second run re-enables it."""

        task = self._create_initial_task()
        task.enabled = False
        task.save()

        output = _call_setup()

        task.refresh_from_db()
        self.assertTrue(task.enabled)
        self.assertIn("Updated", output)
        self.assertIn("enabled", output)

    def test_stale_task_path_gets_updated(self):
        """If the task path stored in DB is stale, running the command updates it."""

        task = self._create_initial_task()
        task.task = "some.old.task.path"
        task.save()

        output = _call_setup()

        task.refresh_from_db()
        self.assertEqual(task.task, "apps.payments.tasks_receipts.kickoff_annual_receipts")
        self.assertIn("Updated", output)

    def test_stale_args_get_updated(self):
        """If the task was previously created with a static year, re-running without
        --static-year should update the args to []."""
        from django_celery_beat.models import PeriodicTask

        # First run with a static year
        _call_setup(static_year=2024)
        task = PeriodicTask.objects.get(name="payments.generate_annual_receipts")

        # Second run without --static-year — task path changes AND args change
        output = _call_setup()

        task.refresh_from_db()
        self.assertEqual(json.loads(task.args), [])
        self.assertIn("Updated", output)

    def test_crontab_mismatch_gets_updated(self):
        """If the task's crontab_id no longer matches the current schedule,
        the command should update it."""
        from django_celery_beat.models import CrontabSchedule, PeriodicTask

        # Create the task via normal run
        _call_setup()
        task = PeriodicTask.objects.get(name="payments.generate_annual_receipts")

        # Point the task at a different (wrong) crontab
        wrong_crontab = CrontabSchedule.objects.create(
            minute="30", hour="9", day_of_month="3", month_of_year="2"
        )
        task.crontab = wrong_crontab
        task.save()

        output = _call_setup()

        task.refresh_from_db()
        # The crontab should now point to the correct Jan 2 08:00 schedule
        self.assertEqual(task.crontab.minute, "0")
        self.assertEqual(task.crontab.hour, "8")
        self.assertIn("Updated", output)

    def test_no_changes_branch_outputs_up_to_date(self):
        """When task + crontab are already correct, outputs the up-to-date message."""
        _call_setup()
        output = _call_setup()
        self.assertIn("already up-to-date", output)
