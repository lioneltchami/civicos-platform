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

from django.test import SimpleTestCase


def _make_config():
    """Return a fresh PaymentsConfig instance (not the live app registry copy)."""
    import apps.payments as payments_module
    from apps.payments.apps import PaymentsConfig
    return PaymentsConfig("payments", payments_module)


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
