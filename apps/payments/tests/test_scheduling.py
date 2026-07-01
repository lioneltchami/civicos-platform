"""
Tests for Celery Beat scheduling tasks and management commands.

Tests:
- kickoff_annual_receipts: auto-resolves tax_year, dispatches generate_annual_receipts
- setup_periodic_tasks management command: idempotent PeriodicTask creation
"""
import inspect
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase
from django.utils.timezone import now


class KickoffAnnualReceiptsTests(TestCase):
    """kickoff_annual_receipts dispatches generate_annual_receipts with correct year."""

    def setUp(self):
        # Clear the Django cache between tests so the M-B idempotency lock
        # (cache.add key "payments:kickoff_annual_receipts:{year}") does not
        # bleed from one test into another and cause "already_running" responses
        # in tests that expect a fresh dispatch.
        from django.core.cache import cache
        cache.clear()

    @patch("apps.payments.tasks_receipts.generate_annual_receipts.delay")
    def test_dispatches_with_previous_year(self, mock_delay):
        """
        kickoff_annual_receipts must dispatch generate_annual_receipts
        with tax_year = current_year - 1 (not current_year).
        """
        from apps.payments.tasks_receipts import kickoff_annual_receipts

        mock_result = MagicMock()
        mock_result.id = "test-task-id-123"
        mock_delay.return_value = mock_result

        expected_tax_year = now().year - 1

        result = kickoff_annual_receipts.apply().get()

        mock_delay.assert_called_once_with(expected_tax_year)
        self.assertEqual(result["tax_year"], expected_tax_year)
        self.assertEqual(result["task_id"], "test-task-id-123")
        self.assertEqual(result["status"], "dispatched")

    def test_does_not_block_on_result(self):
        """
        kickoff_annual_receipts must use .delay() NOT .apply_async().get().
        Blocking would cause worker pool deadlock.
        """
        import ast
        import textwrap
        from apps.payments.tasks_receipts import kickoff_annual_receipts

        src = inspect.getsource(kickoff_annual_receipts)
        # Parse the source to extract only the function body as an AST,
        # then unparse it — this strips docstrings, comments, and decorators.
        # We look for Call nodes with attribute "get" on the result object,
        # which would indicate a blocking .get() call.
        dedented = textwrap.dedent(src)
        tree = ast.parse(dedented)

        # Collect all attribute access names in Call positions
        blocking_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                    # Only flag .get() calls that are on a sub-expression (not bare .get())
                    # characteristic of result.get() or async_result.get()
                    blocking_calls.append(ast.dump(node))

        self.assertEqual(
            blocking_calls,
            [],
            f"kickoff_annual_receipts must not call .get() — deadlock risk. "
            f"Found: {blocking_calls}",
        )

        # Confirm .delay( appears in source code (raw check is fine for this)
        self.assertIn(
            ".delay(",
            src,
            "kickoff_annual_receipts must use .delay() for fire-and-forget",
        )

    @patch("apps.payments.tasks_receipts.generate_annual_receipts.delay")
    def test_returns_dict_with_required_keys(self, mock_delay):
        """Return value must include tax_year, task_id, status."""
        from apps.payments.tasks_receipts import kickoff_annual_receipts

        mock_result = MagicMock()
        mock_result.id = "abc-123"
        mock_delay.return_value = mock_result

        result = kickoff_annual_receipts.apply().get()

        self.assertIn("tax_year", result)
        self.assertIn("task_id", result)
        self.assertIn("status", result)

    @patch("apps.payments.tasks_receipts.generate_annual_receipts.delay")
    def test_logs_dispatch(self, mock_delay):
        """kickoff_annual_receipts must log the dispatch at INFO level."""
        from apps.payments.tasks_receipts import kickoff_annual_receipts

        mock_result = MagicMock()
        mock_result.id = "log-test-id"
        mock_delay.return_value = mock_result

        with self.assertLogs("apps.payments.tasks_receipts", level="INFO") as log_ctx:
            kickoff_annual_receipts.apply().get()

        output = "\n".join(log_ctx.output)
        self.assertIn("dispatched", output)
        self.assertIn("log-test-id", output)


class SetupPeriodicTasksCommandTests(TestCase):
    """setup_periodic_tasks management command creates Celery Beat records."""

    def _run_command(self, **kwargs):
        out = StringIO()
        call_command("setup_periodic_tasks", stdout=out, **kwargs)
        return out.getvalue()

    def test_creates_annual_receipt_task(self):
        """Command creates the payments.generate_annual_receipts periodic task."""
        try:
            from django_celery_beat.models import PeriodicTask
        except ImportError:
            self.skipTest("django-celery-beat not installed")

        output = self._run_command()
        self.assertIn("annual", output.lower())

        task = PeriodicTask.objects.filter(
            name="payments.generate_annual_receipts"
        ).first()
        self.assertIsNotNone(task, "PeriodicTask 'payments.generate_annual_receipts' was not created")
        self.assertTrue(task.enabled)

    def test_idempotent_second_run(self):
        """Running the command twice does not create duplicate tasks."""
        try:
            from django_celery_beat.models import PeriodicTask
        except ImportError:
            self.skipTest("django-celery-beat not installed")

        self._run_command()
        self._run_command()

        count = PeriodicTask.objects.filter(
            name="payments.generate_annual_receipts"
        ).count()
        self.assertEqual(
            count,
            1,
            "Command is not idempotent — created duplicate tasks",
        )

    def test_output_confirms_creation(self):
        """Command output mentions the task name."""
        try:
            from django_celery_beat.models import PeriodicTask
        except ImportError:
            self.skipTest("django-celery-beat not installed")

        output = self._run_command()
        self.assertIn("payments.generate_annual_receipts", output)
