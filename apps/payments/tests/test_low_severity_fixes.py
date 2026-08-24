"""
Tests for three Low severity fixes.

L1 — charge.dispute.* Stripe events must not be silently dropped.
L2 — FERNET_KEYS startup guard in PaymentsConfig.ready().
L3 — Redundant receipt_locked.save() removed from tasks_receipts.generate_and_send_receipt.
"""

import inspect
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

# ---------------------------------------------------------------------------
# L1: charge.dispute.* handler registration and behaviour
# ---------------------------------------------------------------------------


class DisputeHandlerRegistrationTest(SimpleTestCase):
    """L1: dispute event types must be present in the _HANDLERS dispatch table."""

    def test_dispute_created_in_handlers(self):
        """charge.dispute.created must be mapped to a handler."""
        from apps.payments import tasks as tasks_module

        self.assertIn(
            "charge.dispute.created",
            tasks_module._HANDLERS,
            "charge.dispute.created is not mapped in _HANDLERS — dispute events will be silently dropped",  # noqa: E501
        )

    def test_dispute_updated_in_handlers(self):
        """charge.dispute.updated must be mapped to a handler."""
        from apps.payments import tasks as tasks_module

        self.assertIn(
            "charge.dispute.updated",
            tasks_module._HANDLERS,
            "charge.dispute.updated is not mapped in _HANDLERS",
        )

    def test_dispute_closed_in_handlers(self):
        """charge.dispute.closed must be mapped to a handler."""
        from apps.payments import tasks as tasks_module

        self.assertIn(
            "charge.dispute.closed",
            tasks_module._HANDLERS,
            "charge.dispute.closed is not mapped in _HANDLERS",
        )

    def test_dispute_handlers_are_callable(self):
        """All three dispute handler values must be callable."""
        from apps.payments import tasks as tasks_module

        for event_type in (
            "charge.dispute.created",
            "charge.dispute.updated",
            "charge.dispute.closed",
        ):
            handler = tasks_module._HANDLERS[event_type]
            self.assertTrue(callable(handler), f"Handler for {event_type} is not callable")

    def test_dispute_event_types_in_source(self):
        """Source-level check: all three dispute event strings must appear in tasks.py."""
        from apps.payments import tasks as tasks_module

        source = inspect.getsource(tasks_module)
        for event_type in (
            "charge.dispute.created",
            "charge.dispute.updated",
            "charge.dispute.closed",
        ):
            self.assertIn(
                event_type,
                source,
                f"{event_type} must appear in tasks.py source",
            )


class DisputeCreatedHandlerTest(SimpleTestCase):
    """L1: _handle_charge_dispute_created must log at WARNING level."""

    def _make_webhook_event(self, payload=None):
        event = MagicMock()
        event.gateway_event_id = "evt_test_dispute"
        event.event_type = "charge.dispute.created"
        event.payload = payload or {
            "data": {
                "object": {
                    "charge": "ch_test123",
                    "amount": 5000,
                    "currency": "cad",
                    "reason": "fraudulent",
                    "evidence_details": {"due_by": 1700000000},
                }
            }
        }
        return event

    def test_dispute_created_logs_warning(self):
        """_handle_charge_dispute_created must emit at least one WARNING log."""
        from apps.payments.tasks import _handle_charge_dispute_created

        webhook_event = self._make_webhook_event()
        event_data = webhook_event.payload.get("data", {}).get("object", {})

        with self.assertLogs("apps.payments", level="WARNING") as log_ctx:
            _handle_charge_dispute_created(event_data, webhook_event)

        self.assertTrue(
            any("dispute_created" in msg for msg in log_ctx.output),
            f"Expected 'dispute_created' in WARNING logs. Got: {log_ctx.output}",
        )

    def test_dispute_created_logs_charge_id(self):
        """WARNING log must include the charge_id for operator traceability."""
        from apps.payments.tasks import _handle_charge_dispute_created

        webhook_event = self._make_webhook_event()
        event_data = webhook_event.payload.get("data", {}).get("object", {})

        with self.assertLogs("apps.payments", level="WARNING") as log_ctx:
            _handle_charge_dispute_created(event_data, webhook_event)

        combined = " ".join(log_ctx.output)
        self.assertIn("ch_test123", combined, "charge_id 'ch_test123' must appear in WARNING log")

    def test_dispute_created_logs_reason(self):
        """WARNING log must include the dispute reason."""
        from apps.payments.tasks import _handle_charge_dispute_created

        webhook_event = self._make_webhook_event()
        event_data = webhook_event.payload.get("data", {}).get("object", {})

        with self.assertLogs("apps.payments", level="WARNING") as log_ctx:
            _handle_charge_dispute_created(event_data, webhook_event)

        combined = " ".join(log_ctx.output)
        self.assertIn("fraudulent", combined, "dispute reason must appear in WARNING log")

    def test_dispute_created_missing_fields_does_not_raise(self):
        """Handler must not raise even if dispute object fields are missing."""
        from apps.payments.tasks import _handle_charge_dispute_created

        webhook_event = MagicMock()
        webhook_event.gateway_event_id = "evt_empty"
        # Deliberately empty event_data
        event_data = {}

        try:
            with self.assertLogs("apps.payments", level="WARNING"):
                _handle_charge_dispute_created(event_data, webhook_event)
        except Exception as exc:
            self.fail(f"_handle_charge_dispute_created raised on empty event_data: {exc}")


class DisputeUpdatedHandlerTest(SimpleTestCase):
    """L1: _handle_charge_dispute_updated must log at WARNING level."""

    def test_dispute_updated_logs_warning(self):
        """_handle_charge_dispute_updated must emit at least one WARNING log."""
        from apps.payments.tasks import _handle_charge_dispute_updated

        webhook_event = MagicMock()
        webhook_event.gateway_event_id = "evt_upd"
        event_data = {"charge": "ch_upd123", "status": "needs_response"}

        with self.assertLogs("apps.payments", level="WARNING") as log_ctx:
            _handle_charge_dispute_updated(event_data, webhook_event)

        self.assertTrue(
            any("dispute_updated" in msg for msg in log_ctx.output),
            f"Expected 'dispute_updated' in WARNING logs. Got: {log_ctx.output}",
        )

    def test_dispute_updated_logs_charge_id(self):
        """WARNING log must include the charge_id."""
        from apps.payments.tasks import _handle_charge_dispute_updated

        webhook_event = MagicMock()
        event_data = {"charge": "ch_upd456", "status": "under_review"}

        with self.assertLogs("apps.payments", level="WARNING") as log_ctx:
            _handle_charge_dispute_updated(event_data, webhook_event)

        combined = " ".join(log_ctx.output)
        self.assertIn("ch_upd456", combined)


class DisputeClosedHandlerTest(SimpleTestCase):
    """L1: _handle_charge_dispute_closed must log at WARNING level."""

    def test_dispute_closed_logs_warning(self):
        """_handle_charge_dispute_closed must emit at least one WARNING log."""
        from apps.payments.tasks import _handle_charge_dispute_closed

        webhook_event = MagicMock()
        webhook_event.gateway_event_id = "evt_closed"
        event_data = {"charge": "ch_cls789", "status": "lost"}

        with self.assertLogs("apps.payments", level="WARNING") as log_ctx:
            _handle_charge_dispute_closed(event_data, webhook_event)

        self.assertTrue(
            any("dispute_closed" in msg for msg in log_ctx.output),
            f"Expected 'dispute_closed' in WARNING logs. Got: {log_ctx.output}",
        )

    def test_dispute_closed_logs_charge_id(self):
        """WARNING log must include the charge_id."""
        from apps.payments.tasks import _handle_charge_dispute_closed

        webhook_event = MagicMock()
        event_data = {"charge": "ch_cls789", "status": "won"}

        with self.assertLogs("apps.payments", level="WARNING") as log_ctx:
            _handle_charge_dispute_closed(event_data, webhook_event)

        combined = " ".join(log_ctx.output)
        self.assertIn("ch_cls789", combined)


# ---------------------------------------------------------------------------
# L2: FERNET_KEYS startup guard in PaymentsConfig.ready()
# ---------------------------------------------------------------------------


def _make_payments_config():
    """Return a fresh PaymentsConfig instance (not the live app registry copy)."""
    import apps.payments as payments_module
    from apps.payments.apps import PaymentsConfig

    return PaymentsConfig("payments", payments_module)


def _make_fake_signal_modules():
    """Return fake signal/receiver modules to avoid side effects in ready()."""
    fake_signals = MagicMock()
    fake_receivers = MagicMock()
    fake_signals.donation_completed = MagicMock()
    fake_signals.receipt_issued = MagicMock()
    fake_receivers.on_donation_completed = MagicMock()
    fake_receivers.on_receipt_issued = MagicMock()
    return fake_signals, fake_receivers


class FernetKeysStartupGuardTest(SimpleTestCase):
    """L2: PaymentsConfig.ready() must raise ImproperlyConfigured when FERNET_KEYS is
    absent in a non-DEBUG, non-TESTING environment."""

    def _ready_without_fernet(self):
        """Call ready() with FERNET_KEYS absent, DEBUG=False, TESTING=False."""
        config = _make_payments_config()
        fake_sig, fake_recv = _make_fake_signal_modules()

        with patch.dict(
            "sys.modules",
            {
                "apps.payments.signals": fake_sig,
                "apps.payments.receivers": fake_recv,
            },
        ):
            with override_settings(FERNET_KEYS=None, DEBUG=False, TESTING=False):
                config.ready()

    def test_missing_fernet_keys_raises_improperly_configured(self):
        """ready() must raise ImproperlyConfigured when FERNET_KEYS is None."""
        from django.core.exceptions import ImproperlyConfigured

        with self.assertRaises(ImproperlyConfigured):
            self._ready_without_fernet()

    def test_missing_fernet_keys_error_message_is_actionable(self):
        """ImproperlyConfigured message must mention FERNET_KEYS so ops can fix it."""
        from django.core.exceptions import ImproperlyConfigured

        with self.assertRaises(ImproperlyConfigured) as ctx:
            self._ready_without_fernet()
        self.assertIn("FERNET_KEYS", str(ctx.exception))

    def test_fernet_keys_present_does_not_raise(self):
        """ready() must succeed when FERNET_KEYS is set."""
        from cryptography.fernet import Fernet

        config = _make_payments_config()
        fake_sig, fake_recv = _make_fake_signal_modules()
        test_key = Fernet.generate_key().decode()

        with patch.dict(
            "sys.modules",
            {
                "apps.payments.signals": fake_sig,
                "apps.payments.receivers": fake_recv,
            },
        ):
            with override_settings(FERNET_KEYS=[test_key], DEBUG=False, TESTING=False):
                try:
                    config.ready()
                except Exception as exc:
                    self.fail(f"ready() raised unexpectedly with FERNET_KEYS set: {exc}")

    def test_debug_mode_suppresses_guard(self):
        """ready() must not raise when DEBUG=True even if FERNET_KEYS is absent."""
        config = _make_payments_config()
        fake_sig, fake_recv = _make_fake_signal_modules()

        with patch.dict(
            "sys.modules",
            {
                "apps.payments.signals": fake_sig,
                "apps.payments.receivers": fake_recv,
            },
        ):
            with override_settings(FERNET_KEYS=None, DEBUG=True):
                try:
                    config.ready()
                except Exception as exc:
                    self.fail(f"ready() raised in DEBUG mode with no FERNET_KEYS: {exc}")

    def test_testing_mode_suppresses_guard(self):
        """ready() must not raise when TESTING=True even if FERNET_KEYS is absent."""
        config = _make_payments_config()
        fake_sig, fake_recv = _make_fake_signal_modules()

        with patch.dict(
            "sys.modules",
            {
                "apps.payments.signals": fake_sig,
                "apps.payments.receivers": fake_recv,
            },
        ):
            with override_settings(FERNET_KEYS=None, DEBUG=False, TESTING=True):
                try:
                    config.ready()
                except Exception as exc:
                    self.fail(f"ready() raised in TESTING mode with no FERNET_KEYS: {exc}")


# ---------------------------------------------------------------------------
# L3: Redundant receipt_locked.save() removed from generate_and_send_receipt
# ---------------------------------------------------------------------------


class RedundantSaveRemovedTest(SimpleTestCase):
    """L3: save_receipt_pdf() already persists pdf_path via QuerySet.update().
    The subsequent receipt_locked.save() is redundant and was removed."""

    def test_generate_and_send_receipt_does_not_call_save_after_save_receipt_pdf(self):
        """
        Inspect the source of generate_and_send_receipt: after save_receipt_pdf()
        is called, receipt_locked.save() must NOT appear in the same branch.

        We parse the source to detect the specific pattern that caused L3.
        A standalone receipt_locked.save(update_fields=["pdf_path"...]) after
        save_receipt_pdf() in the PDF-generation branch is the bug; its absence
        confirms the fix.
        """
        from apps.payments import tasks_receipts

        source = inspect.getsource(tasks_receipts.generate_and_send_receipt)

        # The redundant pattern: receipt_locked.save() immediately after save_receipt_pdf()
        # within the PDF generation branch. After the fix, no receipt_locked.save() call
        # should appear in the function body at all (the flag is set via _base_manager.update).
        self.assertNotIn(
            "receipt_locked.save(",
            source,
            "receipt_locked.save() still exists in generate_and_send_receipt — "
            "this is a redundant DB write since save_receipt_pdf() already persists "
            "pdf_path via _base_manager.update(). Remove it.",
        )

    def test_save_receipt_pdf_persists_pdf_path_via_base_manager(self):
        """
        Confirm save_receipt_pdf() itself uses _base_manager.update() to persist
        pdf_path — so the caller does not need an extra save() call.
        """
        from apps.payments.services import receipt_pdf

        source = inspect.getsource(receipt_pdf.save_receipt_pdf)
        self.assertIn(
            "_base_manager",
            source,
            "save_receipt_pdf() must persist pdf_path via _base_manager.update() "
            "so the caller does not need an extra receipt.save() call.",
        )
