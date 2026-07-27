"""
Tests for apps/payments/apps.py — PaymentsConfig.ready() WeasyPrint startup checks.

The ready() method imports weasyprint and logs a WARNING if:
  - OSError is raised  (Pango/Cairo system libraries missing)
  - ImportError is raised (weasyprint package not installed)

Strategy: Call PaymentsConfig.ready() directly on a new instance, patching out
the signals import and the weasyprint import so we can exercise the error branches.
"""
import sys
from unittest.mock import patch, MagicMock

from django.test import SimpleTestCase, TestCase, override_settings


def _make_config():
    """Return a fresh PaymentsConfig instance (not the live app registry copy)."""
    import apps.payments as payments_module
    from apps.payments.apps import PaymentsConfig
    return PaymentsConfig("payments", payments_module)


@override_settings(DEBUG=True)
class WeasyPrintCheckOSErrorTest(SimpleTestCase):
    """When weasyprint raises OSError (missing Pango/Cairo), a WARNING is logged."""

    def _ready_with_oserror(self):
        """Call ready() with weasyprint raising OSError."""
        config = _make_config()

        saved = sys.modules.pop("weasyprint", None)
        try:
            import builtins as _builtins
            original_import = _builtins.__import__

            def fake_import(name, *args, **kwargs):
                if name == "weasyprint":
                    raise OSError("libpango.so.1: cannot open shared object file")
                if name in ("apps.payments.signals", "apps.payments.receivers"):
                    return MagicMock()
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=fake_import):
                with patch("apps.payments.apps.PaymentsConfig.ready",
                           wraps=lambda s=None: None):
                    # Directly call the try/except block by invoking ready()
                    # but patching signal connection to be a no-op
                    pass

            # Instead: directly invoke the weasyprint try/except from ready()
            # by patching the import at module level
            with patch("builtins.__import__", side_effect=fake_import):
                # Simulate what ready() does — connect signals then check weasyprint
                config._run_weasyprint_startup_check_for_test()
        finally:
            if saved is not None:
                sys.modules["weasyprint"] = saved

    def test_oserror_logs_warning(self):
        """OSError during weasyprint import logs a WARNING."""
        config = _make_config()

        saved = sys.modules.pop("weasyprint", None)
        try:
            import builtins as _builtins
            original_import = _builtins.__import__

            def fake_import(name, *args, **kwargs):
                if name == "weasyprint":
                    raise OSError("Pango not found")
                return original_import(name, *args, **kwargs)

            # Patch signal connectors and the import, then call ready()
            with patch("apps.payments.apps.PaymentsConfig.ready.__wrapped__",
                       create=True):
                pass

            # Cleanest approach: call the actual ready() but mock signals
            fake_signals_module = MagicMock()
            fake_receivers_module = MagicMock()
            fake_signals_module.donation_completed = MagicMock()
            fake_signals_module.receipt_issued = MagicMock()
            fake_receivers_module.on_donation_completed = MagicMock()
            fake_receivers_module.on_receipt_issued = MagicMock()

            with patch.dict("sys.modules", {
                "apps.payments.signals": fake_signals_module,
                "apps.payments.receivers": fake_receivers_module,
            }):
                with patch("builtins.__import__", side_effect=fake_import):
                    with self.assertLogs("apps.payments.apps", level="WARNING") as log_ctx:
                        config.ready()

            self.assertTrue(
                any("weasyprint_unavailable" in msg for msg in log_ctx.output),
                f"Expected 'weasyprint_unavailable' in logs. Got: {log_ctx.output}",
            )
        finally:
            if saved is not None:
                sys.modules["weasyprint"] = saved

    def test_oserror_includes_exc_type_oserror_in_log(self):
        """The OSError WARNING message includes 'OSError' as the exc_type."""
        config = _make_config()
        saved = sys.modules.pop("weasyprint", None)
        try:
            import builtins as _builtins
            original_import = _builtins.__import__

            def fake_import(name, *args, **kwargs):
                if name == "weasyprint":
                    raise OSError("Cairo not available")
                return original_import(name, *args, **kwargs)

            fake_signals_module = MagicMock()
            fake_receivers_module = MagicMock()
            fake_signals_module.donation_completed = MagicMock()
            fake_signals_module.receipt_issued = MagicMock()
            fake_receivers_module.on_donation_completed = MagicMock()
            fake_receivers_module.on_receipt_issued = MagicMock()

            with patch.dict("sys.modules", {
                "apps.payments.signals": fake_signals_module,
                "apps.payments.receivers": fake_receivers_module,
            }):
                with patch("builtins.__import__", side_effect=fake_import):
                    with self.assertLogs("apps.payments.apps", level="WARNING") as log_ctx:
                        config.ready()

            combined = " ".join(log_ctx.output)
            self.assertIn("OSError", combined)
        finally:
            if saved is not None:
                sys.modules["weasyprint"] = saved


@override_settings(DEBUG=True)
class WeasyPrintCheckImportErrorTest(SimpleTestCase):
    """When weasyprint is not installed (ImportError), a WARNING is logged."""

    def _make_fake_signal_modules(self):
        fake_signals_module = MagicMock()
        fake_receivers_module = MagicMock()
        fake_signals_module.donation_completed = MagicMock()
        fake_signals_module.receipt_issued = MagicMock()
        fake_receivers_module.on_donation_completed = MagicMock()
        fake_receivers_module.on_receipt_issued = MagicMock()
        return fake_signals_module, fake_receivers_module

    def test_import_error_logs_warning(self):
        config = _make_config()
        saved = sys.modules.pop("weasyprint", None)
        try:
            import builtins as _builtins
            original_import = _builtins.__import__

            def fake_import(name, *args, **kwargs):
                if name == "weasyprint":
                    raise ImportError("No module named 'weasyprint'")
                return original_import(name, *args, **kwargs)

            fake_sig, fake_recv = self._make_fake_signal_modules()
            with patch.dict("sys.modules", {
                "apps.payments.signals": fake_sig,
                "apps.payments.receivers": fake_recv,
            }):
                with patch("builtins.__import__", side_effect=fake_import):
                    with self.assertLogs("apps.payments.apps", level="WARNING") as log_ctx:
                        config.ready()

            self.assertTrue(
                any("weasyprint_not_installed" in msg for msg in log_ctx.output),
                f"Expected 'weasyprint_not_installed' in logs. Got: {log_ctx.output}",
            )
        finally:
            if saved is not None:
                sys.modules["weasyprint"] = saved

    def test_import_error_suggests_pip_install(self):
        config = _make_config()
        saved = sys.modules.pop("weasyprint", None)
        try:
            import builtins as _builtins
            original_import = _builtins.__import__

            def fake_import(name, *args, **kwargs):
                if name == "weasyprint":
                    raise ImportError("No module named 'weasyprint'")
                return original_import(name, *args, **kwargs)

            fake_sig, fake_recv = self._make_fake_signal_modules()
            with patch.dict("sys.modules", {
                "apps.payments.signals": fake_sig,
                "apps.payments.receivers": fake_recv,
            }):
                with patch("builtins.__import__", side_effect=fake_import):
                    with self.assertLogs("apps.payments.apps", level="WARNING") as log_ctx:
                        config.ready()

            combined = " ".join(log_ctx.output)
            self.assertIn("pip install weasyprint", combined)
        finally:
            if saved is not None:
                sys.modules["weasyprint"] = saved


class PaymentsAppConfigMetaTest(SimpleTestCase):
    """Sanity-check that PaymentsConfig is properly registered."""

    def test_app_label(self):
        from django.apps import apps
        config = apps.get_app_config("payments")
        self.assertEqual(config.label, "payments")

    def test_app_name(self):
        from django.apps import apps
        config = apps.get_app_config("payments")
        self.assertEqual(config.name, "apps.payments")

    def test_verbose_name(self):
        from django.apps import apps
        config = apps.get_app_config("payments")
        self.assertEqual(config.verbose_name, "Payments")


# ---------------------------------------------------------------------------
# L2: debug_task must not exist in config.celery (production safety)
# ---------------------------------------------------------------------------

class CeleryDebugTaskRemovedTest(SimpleTestCase):
    """
    L2 fix: debug_task was removed from config/celery.py.

    debug_task is Django-Celery boilerplate that prints worker request
    internals (including task headers that may contain metadata).
    It must not be present in the production Celery module.
    """

    def test_debug_task_not_in_celery_module(self):
        """config.celery must not define debug_task."""
        import config.celery as celery_module
        self.assertFalse(
            hasattr(celery_module, "debug_task"),
            "debug_task must be removed from config/celery.py — "
            "it leaks worker internals and is not safe for production.",
        )

    def test_celery_app_exists(self):
        """config.celery.app must still exist after debug_task removal."""
        import config.celery as celery_module
        self.assertTrue(hasattr(celery_module, "app"))


# ---------------------------------------------------------------------------
# Payments BB certifiability fix pass (2026-07-27), Finding 2 — real-server
# async-dispatch gap. Regression coverage for config/__init__.py.
# ---------------------------------------------------------------------------

class CeleryAppBootstrapTest(TestCase):
    """
    Uses TestCase (not SimpleTestCase) because
    test_real_unmocked_delay_executes_synchronously_without_a_broker below
    genuinely touches the database (process_bulk_payment_batch's real,
    unmocked code path queries BulkPaymentBatch inside a transaction) — that
    is the entire point of that test: proving the real DB-touching code path
    runs in-process under eager mode rather than attempting a broker
    connection.

    ``config/__init__.py`` was previously EMPTY — it never imported the
    properly Django-configured ``Celery("civicos")`` app from
    ``config/celery.py``. Every ``@shared_task``-decorated task (e.g.
    ``apps.payments.govstack_tasks.process_bulk_payment_batch``) therefore
    bound lazily, at first ``.delay()``/``.apply_async()`` call, to Celery's
    own internal, UNCONFIGURED default app instead — which has
    ``task_always_eager=False`` and Celery's own default AMQP broker URL,
    regardless of what ``settings.CELERY_TASK_ALWAYS_EAGER`` /
    ``CELERY_BROKER_URL`` say. This was invisible to the test suite because
    every existing test that dispatches a task mocks ``.delay()`` out
    entirely; a live run of the upstream GovStack harness against a real
    running server surfaced it as a genuine ``ConnectionRefusedError`` on 4
    scenarios (see ``MASTER_BB_CERTIFIABILITY_REPORT.md``, "Payments BB",
    and ``SPEC_GOVSTACK_PAYMENTS_BB.md`` §26 for the full incident writeup).

    ``config/__init__.py`` now does ``from .celery import app as celery_app``
    unconditionally, so importing the ``config`` package (which happens for
    every Django entrypoint, including ``manage.py test``) instantiates and
    Django-settings-configures the real Celery app before any task can be
    dispatched. These tests pin that fix directly, rather than relying only
    on the existing mocked-``.delay()`` task-dispatch tests, which would not
    catch a regression here (they never touch the real Celery ``app``
    object at all).
    """

    def test_config_package_exposes_celery_app(self):
        """The config package's own __init__ must expose the configured app."""
        import config
        self.assertTrue(
            hasattr(config, "celery_app"),
            "config/__init__.py must import the Celery app as `celery_app` — "
            "see that file's docstring for why this is required, not optional.",
        )

    def test_shared_task_is_bound_to_the_configured_app_not_celerys_default(self):
        """
        A real @shared_task (not a mock) must be bound to this project's own
        configured Celery("civicos") app — not to Celery's internal default
        app, which is what @shared_task lazily falls back to if config/celery.py
        was never actually imported/configured before the task is first used.
        """
        import config.celery as celery_module
        from apps.payments.govstack_tasks import process_bulk_payment_batch

        self.assertIs(
            process_bulk_payment_batch.app,
            celery_module.app,
            "process_bulk_payment_batch is bound to a different Celery app "
            "than config.celery.app — this is exactly the bug config/__init__.py "
            "exists to prevent (see its docstring). If this assertion fails, "
            "something has broken the config package's Celery bootstrap again.",
        )

    def test_shared_task_app_reflects_django_settings_eager_mode(self):
        """
        The Celery app a real task is bound to must actually read
        CELERY_TASK_ALWAYS_EAGER from Django settings — not silently fall
        back to Celery's own hardcoded default (False), which is exactly
        what happened when config/celery.py's app.config_from_object(...)
        was never triggered because nothing imported config.celery at
        package-init time.
        """
        from django.conf import settings

        from apps.payments.govstack_tasks import process_bulk_payment_batch

        self.assertEqual(
            process_bulk_payment_batch.app.conf.task_always_eager,
            settings.CELERY_TASK_ALWAYS_EAGER,
            "process_bulk_payment_batch.app.conf.task_always_eager does not "
            "match settings.CELERY_TASK_ALWAYS_EAGER — the task's bound "
            "Celery app is not reading Django settings, meaning it is not "
            "config.celery.app. This is the exact failure mode config/__init__.py "
            "exists to prevent.",
        )

    def test_real_unmocked_delay_executes_synchronously_without_a_broker(self):
        """
        The genuine end-to-end regression test: call the real, UNMOCKED
        .delay() on a real task and confirm it runs synchronously in-process
        (the correct eager behaviour) rather than attempting a real broker
        connection. This is the exact code path that silently broke in
        production/CI before the config/__init__.py fix, and that every
        other test in this suite avoids exercising by mocking .delay() out.
        """
        from apps.payments.govstack_tasks import process_bulk_payment_batch

        # A deliberately nonexistent batch_pk: we are not testing the task's
        # business logic here (that's covered extensively elsewhere in this
        # suite) — only that .delay() executes AT ALL, in-process, with no
        # broker connection attempted. The task's own not-found handling
        # (whatever it does for a missing batch) is a normal, safely-caught
        # return path, not a broker-connection error.
        result = process_bulk_payment_batch.delay("00000000-0000-0000-0000-000000000000")
        # In eager mode, .delay() returns an EagerResult with the task's
        # return value already computed — if this raises kombu.exceptions
        # .OperationalError (Connection refused), the bootstrap fix has
        # regressed.
        self.assertTrue(result.ready())
