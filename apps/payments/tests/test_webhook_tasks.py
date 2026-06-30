"""
Tests for process_stripe_webhook Celery task.
Uses TestCase (DB required). Task called directly/synchronously.
Mocks only Stripe HTTP calls (via gateway.parse_webhook_event).
Uses patch("django.db.transaction.on_commit", side_effect=lambda fn: fn())
to fire on_commit callbacks immediately in TestCase.
"""
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch, call

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.payments.gateways.exceptions import GatewayWebhookError
from apps.payments.models import (
    GATEWAY_STRIPE,
    PLAN_STATUS_ACTIVE,
    PLAN_STATUS_CANCELLED,
    PLAN_STATUS_PAUSED,
    Payment,
    PaymentIntent,
    RecurringGiftPlan,
    WebhookEvent,
)
from apps.payments.tasks import process_stripe_webhook

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_user(email=None):
    email = email or f"user_{uuid.uuid4().hex[:6]}@example.com"
    return User.objects.create_user(email=email, password="TestPass123!")


def _make_payment_intent(user=None, status=None, gateway_intent_id=None):
    """Create a minimal PaymentIntent for testing."""
    if user is None:
        user = _make_user()
    return PaymentIntent.objects.create(
        payer=user,
        amount=Decimal("100.00"),
        currency="CAD",
        purpose=PaymentIntent.PURPOSE_SERVICE_FEE,
        status=status or PaymentIntent.STATUS_PENDING,
        gateway=GATEWAY_STRIPE,
        gateway_intent_id=gateway_intent_id or f"pi_test_{uuid.uuid4().hex[:8]}",
    )


def _make_webhook_event(
    gateway_intent_id="pi_test_001",
    event_type="payment_intent.succeeded",
    gateway_event_id=None,
    processed=False,
    signature_verified=True,
    raw_payload=None,
):
    """Create a WebhookEvent for testing."""
    if gateway_event_id is None:
        gateway_event_id = f"evt_{uuid.uuid4().hex[:8]}"

    payload = raw_payload or {
        "id": gateway_event_id,
        "type": event_type,
        "data": {"object": {"id": gateway_intent_id}},
    }
    return WebhookEvent.objects.create(
        gateway=GATEWAY_STRIPE,
        gateway_event_id=gateway_event_id,
        event_type=event_type,
        payload=payload,
        signature_verified=signature_verified,
        processed=processed,
    )


def _succeeded_event_data(gateway_intent_id="pi_test_001", gateway_charge_id="ch_test_001"):
    """Return a parse_webhook_event return value for payment_intent.succeeded."""
    return (
        "payment_intent.succeeded",
        {
            "gateway_intent_id": gateway_intent_id,
            "gateway_charge_id": gateway_charge_id,
            "amount_paid": Decimal("100.00"),
            "processor_fee": Decimal("0.00"),
            "net_amount": Decimal("100.00"),
            "payment_method_type": "card",
            "card_last_four": "4242",
            "card_brand": "visa",
            "paid_at": "1700000000",  # Unix timestamp string
        },
    )


def _failed_event_data(gateway_intent_id="pi_test_001"):
    """Return a parse_webhook_event return value for payment_intent.payment_failed."""
    return (
        "payment_intent.payment_failed",
        {
            "gateway_intent_id": gateway_intent_id,
            "failure_reason": "insufficient_funds",
        },
    )


# ---------------------------------------------------------------------------
# Idempotency gate tests
# ---------------------------------------------------------------------------

class IdempotencyGateTests(TestCase):
    """Test that already-processed events are skipped."""

    def test_already_processed_event_returns_immediately(self):
        """Task returns without error when WebhookEvent.processed is True."""
        pi = _make_payment_intent()
        event = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            processed=True,
        )
        # Should not raise, should not create any Payment
        process_stripe_webhook(str(event.pk))
        self.assertEqual(Payment.objects.count(), 0)

    def test_already_processed_event_does_not_change_event(self):
        pi = _make_payment_intent()
        event = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            processed=True,
        )
        process_stripe_webhook(str(event.pk))
        event.refresh_from_db()
        self.assertTrue(event.processed)

    def test_nonexistent_pk_returns_without_error(self):
        """Task exits gracefully when WebhookEvent does not exist."""
        nonexistent_pk = str(uuid.uuid4())
        # Should not raise
        process_stripe_webhook(nonexistent_pk)

    def test_signature_not_verified_does_not_process(self):
        """Task refuses to process events where signature_verified=False."""
        pi = _make_payment_intent()
        event = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            signature_verified=False,
        )
        process_stripe_webhook(str(event.pk))
        self.assertEqual(Payment.objects.count(), 0)
        event.refresh_from_db()
        self.assertFalse(event.processed)


# ---------------------------------------------------------------------------
# payment_intent.succeeded handler tests
# ---------------------------------------------------------------------------

class PaymentIntentSucceededHandlerTests(TestCase):
    """Test _handle_payment_intent_succeeded via the task."""

    def _run_succeeded(self, pi, charge_id="ch_test_succeeded"):
        """Run the task for a payment_intent.succeeded event."""
        event = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            event_type="payment_intent.succeeded",
        )
        mock_parse_return = _succeeded_event_data(
            gateway_intent_id=pi.gateway_intent_id,
            gateway_charge_id=charge_id,
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))
        return event

    def test_creates_payment_row(self):
        pi = _make_payment_intent()
        self._run_succeeded(pi)
        self.assertEqual(Payment.objects.count(), 1)

    def test_payment_linked_to_intent(self):
        pi = _make_payment_intent()
        self._run_succeeded(pi)
        payment = Payment.objects.get()
        self.assertEqual(payment.intent, pi)

    def test_payment_amount_paid(self):
        pi = _make_payment_intent()
        self._run_succeeded(pi)
        payment = Payment.objects.get()
        self.assertEqual(payment.amount_paid, Decimal("100.00"))

    def test_payment_gateway_charge_id(self):
        pi = _make_payment_intent()
        self._run_succeeded(pi, charge_id="ch_specific_001")
        payment = Payment.objects.get()
        self.assertEqual(payment.gateway_charge_id, "ch_specific_001")

    def test_payment_card_last_four(self):
        pi = _make_payment_intent()
        self._run_succeeded(pi)
        payment = Payment.objects.get()
        self.assertEqual(payment.card_last_four, "4242")

    def test_payment_intent_transitions_to_completed(self):
        pi = _make_payment_intent(status=PaymentIntent.STATUS_PENDING)
        self._run_succeeded(pi)
        pi.refresh_from_db()
        self.assertEqual(pi.status, PaymentIntent.STATUS_COMPLETED)

    def test_webhook_event_marked_processed(self):
        pi = _make_payment_intent()
        event = self._run_succeeded(pi)
        event.refresh_from_db()
        self.assertTrue(event.processed)

    def test_webhook_event_processed_at_set(self):
        pi = _make_payment_intent()
        event = self._run_succeeded(pi)
        event.refresh_from_db()
        self.assertIsNotNone(event.processed_at)

    def test_second_call_with_same_event_is_idempotent(self):
        """Second run with processed=True exits immediately — no second Payment created."""
        pi = _make_payment_intent()
        event = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            event_type="payment_intent.succeeded",
        )
        mock_parse_return = _succeeded_event_data(gateway_intent_id=pi.gateway_intent_id)
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))  # First call
            process_stripe_webhook(str(event.pk))  # Second call — should be idempotent
        self.assertEqual(Payment.objects.count(), 1)

    def test_duplicate_charge_id_is_idempotent(self):
        """If a Payment already exists for the charge_id, handler returns without creating another."""
        pi = _make_payment_intent()
        charge_id = "ch_duplicate_test"

        event1 = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            gateway_event_id="evt_dup_001",
        )
        event2 = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            gateway_event_id="evt_dup_002",
        )

        mock_parse_return = _succeeded_event_data(
            gateway_intent_id=pi.gateway_intent_id,
            gateway_charge_id=charge_id,
        )

        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event1.pk))

        # Now run a second event with the same charge_id — should be idempotent
        # (pi is now COMPLETED, so we need a new pi for the second test)
        pi2 = _make_payment_intent(gateway_intent_id="pi_idempotent_002")
        mock_parse_return2 = _succeeded_event_data(
            gateway_intent_id=pi2.gateway_intent_id,
            gateway_charge_id=charge_id,  # Same charge ID
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw2, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw2 = MagicMock()
            mock_gw2.parse_webhook_event.return_value = mock_parse_return2
            mock_get_gw2.return_value = mock_gw2
            process_stripe_webhook(str(event2.pk))

        # Only one Payment should exist for this charge_id
        self.assertEqual(
            Payment.objects.filter(gateway_charge_id=charge_id).count(), 1
        )

    def test_intent_not_found_logs_error_and_marks_event(self):
        """PaymentIntent not found → task returns, event is not marked processed."""
        event = _make_webhook_event(
            gateway_intent_id="pi_does_not_exist",
            event_type="payment_intent.succeeded",
        )
        mock_parse_return = _succeeded_event_data(
            gateway_intent_id="pi_does_not_exist",
            gateway_charge_id="ch_no_intent",
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))
        # Payment should not be created
        self.assertEqual(Payment.objects.count(), 0)

    def test_payment_completed_signal_registered_via_on_commit(self):
        """Verify on_commit is called (signal emission is registered post-commit)."""
        pi = _make_payment_intent()
        event = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            event_type="payment_intent.succeeded",
        )
        mock_parse_return = _succeeded_event_data(gateway_intent_id=pi.gateway_intent_id)

        on_commit_callbacks = []

        def capture_on_commit(fn):
            on_commit_callbacks.append(fn)

        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=capture_on_commit):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))

        # At least one on_commit callback should have been registered
        self.assertGreater(len(on_commit_callbacks), 0)


# ---------------------------------------------------------------------------
# payment_intent.payment_failed handler tests
# ---------------------------------------------------------------------------

class PaymentIntentFailedHandlerTests(TestCase):
    """Test _handle_payment_intent_failed via the task."""

    def _run_failed(self, pi):
        event = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            event_type="payment_intent.payment_failed",
        )
        mock_parse_return = _failed_event_data(gateway_intent_id=pi.gateway_intent_id)
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))
        return event

    def test_intent_transitions_to_failed_from_pending(self):
        pi = _make_payment_intent(status=PaymentIntent.STATUS_PENDING)
        self._run_failed(pi)
        pi.refresh_from_db()
        self.assertEqual(pi.status, PaymentIntent.STATUS_FAILED)

    def test_intent_transitions_to_failed_from_processing(self):
        pi = _make_payment_intent(status=PaymentIntent.STATUS_PROCESSING)
        self._run_failed(pi)
        pi.refresh_from_db()
        self.assertEqual(pi.status, PaymentIntent.STATUS_FAILED)

    def test_webhook_event_marked_processed(self):
        pi = _make_payment_intent()
        event = self._run_failed(pi)
        event.refresh_from_db()
        self.assertTrue(event.processed)

    def test_no_payment_created(self):
        pi = _make_payment_intent()
        self._run_failed(pi)
        self.assertEqual(Payment.objects.count(), 0)

    def test_intent_not_found_no_crash(self):
        """No exception when PaymentIntent not found for failed event."""
        event = _make_webhook_event(
            gateway_intent_id="pi_not_found_fail",
            event_type="payment_intent.payment_failed",
        )
        mock_parse_return = _failed_event_data(gateway_intent_id="pi_not_found_fail")
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            # Should not raise
            process_stripe_webhook(str(event.pk))


# ---------------------------------------------------------------------------
# charge.refunded handler tests
# ---------------------------------------------------------------------------

class ChargeRefundedHandlerTests(TestCase):
    """Test _handle_charge_refunded via the task."""

    def test_no_op_when_gateway_refund_id_empty(self):
        """Handler logs and returns when gateway_refund_id is empty."""
        event = _make_webhook_event(
            event_type="charge.refunded",
            gateway_intent_id="ch_no_refund_id",
        )
        mock_parse_return = (
            "charge.refunded",
            {
                "gateway_charge_id": "ch_test_001",
                "gateway_refund_id": "",  # Empty
                "refund_amount": Decimal("50.00"),
                "refund_status": "succeeded",
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            # Should not raise
            process_stripe_webhook(str(event.pk))
        event.refresh_from_db()
        # Task completes without error — event is marked processed
        self.assertTrue(event.processed)

    def test_with_valid_refund_id_processes_without_error(self):
        """Handler with valid gateway_refund_id completes without raising."""
        event = _make_webhook_event(
            event_type="charge.refunded",
            gateway_intent_id="ch_refunded_001",
        )
        mock_parse_return = (
            "charge.refunded",
            {
                "gateway_charge_id": "ch_refunded_001",
                "gateway_refund_id": "re_valid_001",
                "refund_amount": Decimal("50.00"),
                "refund_status": "succeeded",
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))
        event.refresh_from_db()
        self.assertTrue(event.processed)


# ---------------------------------------------------------------------------
# customer.subscription.deleted handler tests
# ---------------------------------------------------------------------------

class SubscriptionDeletedHandlerTests(TestCase):
    """Test _handle_subscription_deleted via the task."""

    def _make_recurring_plan(self, user=None, status=PLAN_STATUS_ACTIVE, sub_id=None):
        if user is None:
            user = _make_user()
        return RecurringGiftPlan.objects.create(
            donor=user,
            amount=Decimal("25.00"),
            frequency="monthly",
            next_charge_date=timezone.now().date(),
            gateway_subscription_id=sub_id or f"sub_{uuid.uuid4().hex[:8]}",
            status=status,
        )

    def _run_subscription_deleted(self, sub_id):
        event = _make_webhook_event(
            event_type="customer.subscription.deleted",
            gateway_intent_id=sub_id,
        )
        mock_parse_return = (
            "customer.subscription.deleted",
            {
                "gateway_subscription_id": sub_id,
                "status": "canceled",
                "current_period_end": "1700000000",
                "cancel_at_period_end": False,
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))
        return event

    def test_sets_plan_status_to_cancelled(self):
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        plan = self._make_recurring_plan(sub_id=sub_id)
        self._run_subscription_deleted(sub_id)
        plan.refresh_from_db()
        self.assertEqual(plan.status, PLAN_STATUS_CANCELLED)

    def test_sets_cancelled_at(self):
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        plan = self._make_recurring_plan(sub_id=sub_id)
        self._run_subscription_deleted(sub_id)
        plan.refresh_from_db()
        self.assertIsNotNone(plan.cancelled_at)

    def test_already_cancelled_is_idempotent(self):
        """Already-cancelled plan — no update, no error."""
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        plan = self._make_recurring_plan(sub_id=sub_id, status=PLAN_STATUS_CANCELLED)
        original_cancelled_at = timezone.now()
        plan.cancelled_at = original_cancelled_at
        plan.save(update_fields=["cancelled_at"])

        self._run_subscription_deleted(sub_id)
        plan.refresh_from_db()
        self.assertEqual(plan.status, PLAN_STATUS_CANCELLED)

    def test_webhook_event_marked_processed(self):
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        self._make_recurring_plan(sub_id=sub_id)
        event = self._run_subscription_deleted(sub_id)
        event.refresh_from_db()
        self.assertTrue(event.processed)

    def test_plan_not_found_no_crash(self):
        """No crash when RecurringGiftPlan not found."""
        sub_id = "sub_not_found_001"
        event = _make_webhook_event(
            event_type="customer.subscription.deleted",
            gateway_intent_id=sub_id,
        )
        mock_parse_return = (
            "customer.subscription.deleted",
            {
                "gateway_subscription_id": sub_id,
                "status": "canceled",
                "current_period_end": "",
                "cancel_at_period_end": False,
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            # Should not raise
            process_stripe_webhook(str(event.pk))


# ---------------------------------------------------------------------------
# customer.subscription.updated handler tests
# ---------------------------------------------------------------------------

class SubscriptionUpdatedHandlerTests(TestCase):
    """Test _handle_subscription_updated via the task."""

    def _make_recurring_plan(self, user=None, status=PLAN_STATUS_ACTIVE, sub_id=None):
        if user is None:
            user = _make_user()
        return RecurringGiftPlan.objects.create(
            donor=user,
            amount=Decimal("25.00"),
            frequency="monthly",
            next_charge_date=timezone.now().date(),
            gateway_subscription_id=sub_id or f"sub_{uuid.uuid4().hex[:8]}",
            status=status,
        )

    def _run_subscription_updated(self, sub_id, stripe_status):
        event = _make_webhook_event(
            event_type="customer.subscription.updated",
            gateway_intent_id=sub_id,
        )
        mock_parse_return = (
            "customer.subscription.updated",
            {
                "gateway_subscription_id": sub_id,
                "status": stripe_status,
                "current_period_end": "1700000000",
                "cancel_at_period_end": False,
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))
        return event

    def test_active_status_mapped_correctly(self):
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        plan = self._make_recurring_plan(sub_id=sub_id, status=PLAN_STATUS_PAUSED)
        self._run_subscription_updated(sub_id, "active")
        plan.refresh_from_db()
        self.assertEqual(plan.status, PLAN_STATUS_ACTIVE)

    def test_paused_status_mapped_correctly(self):
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        plan = self._make_recurring_plan(sub_id=sub_id, status=PLAN_STATUS_ACTIVE)
        self._run_subscription_updated(sub_id, "paused")
        plan.refresh_from_db()
        self.assertEqual(plan.status, PLAN_STATUS_PAUSED)

    def test_canceled_status_mapped_correctly(self):
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        plan = self._make_recurring_plan(sub_id=sub_id, status=PLAN_STATUS_ACTIVE)
        self._run_subscription_updated(sub_id, "canceled")
        plan.refresh_from_db()
        self.assertEqual(plan.status, PLAN_STATUS_CANCELLED)

    def test_unmapped_status_past_due_no_crash(self):
        """Unmapped Stripe status 'past_due' — no crash, plan unchanged."""
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        plan = self._make_recurring_plan(sub_id=sub_id, status=PLAN_STATUS_ACTIVE)
        self._run_subscription_updated(sub_id, "past_due")
        plan.refresh_from_db()
        self.assertEqual(plan.status, PLAN_STATUS_ACTIVE)  # Unchanged

    def test_unmapped_status_incomplete_no_crash(self):
        """Unmapped Stripe status 'incomplete' — no crash."""
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        plan = self._make_recurring_plan(sub_id=sub_id, status=PLAN_STATUS_ACTIVE)
        self._run_subscription_updated(sub_id, "incomplete")
        plan.refresh_from_db()
        self.assertEqual(plan.status, PLAN_STATUS_ACTIVE)  # Unchanged

    def test_webhook_event_marked_processed(self):
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        self._make_recurring_plan(sub_id=sub_id)
        event = self._run_subscription_updated(sub_id, "active")
        event.refresh_from_db()
        self.assertTrue(event.processed)


# ---------------------------------------------------------------------------
# Unknown event type tests
# ---------------------------------------------------------------------------

class UnknownEventTypeTests(TestCase):
    """Test behavior for unhandled event types."""

    def test_unknown_event_type_marks_processed(self):
        """Unhandled event types are marked processed=True (so Stripe stops retrying)."""
        event = _make_webhook_event(event_type="account.updated")
        mock_parse_return = (
            "account.updated",
            {"gateway_intent_id": "acct_001"},
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))
        event.refresh_from_db()
        self.assertTrue(event.processed)

    def test_unknown_event_type_no_exception(self):
        """No exception raised for unknown event types."""
        event = _make_webhook_event(event_type="review.opened")
        mock_parse_return = (
            "review.opened",
            {"gateway_intent_id": "prv_001"},
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            try:
                process_stripe_webhook(str(event.pk))
            except Exception as exc:
                self.fail(f"Task raised unexpectedly for unknown event type: {exc}")


# ---------------------------------------------------------------------------
# Parse error tests
# ---------------------------------------------------------------------------

class ParseErrorTests(TestCase):
    """Test error handling when gateway.parse_webhook_event raises."""

    def test_parse_error_marks_event_with_error_field(self):
        """GatewayWebhookError from parse_webhook_event → event.error set, not processed."""
        event = _make_webhook_event()
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.side_effect = GatewayWebhookError(
                "missing type field",
                gateway_code="malformed_payload",
            )
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))
        event.refresh_from_db()
        # Event should NOT be marked processed when parse fails
        self.assertFalse(event.processed)
        self.assertIn("GatewayWebhookError", event.error)

    def test_parse_error_no_payment_created(self):
        """No Payment created when parse fails."""
        event = _make_webhook_event()
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.side_effect = GatewayWebhookError("bad payload")
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))
        self.assertEqual(Payment.objects.count(), 0)

    def test_parse_error_no_exception_propagated(self):
        """Task should not propagate GatewayWebhookError to caller."""
        event = _make_webhook_event()
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.side_effect = GatewayWebhookError("bad payload")
            mock_get_gw.return_value = mock_gw
            try:
                process_stripe_webhook(str(event.pk))
            except Exception as exc:
                self.fail(f"Task raised unexpectedly on parse error: {exc}")


# ---------------------------------------------------------------------------
# Item 15 — _handle_subscription_updated backwards-transition guard
# Item 16 — updated_at populated by QuerySet.update()
# ---------------------------------------------------------------------------

class SubscriptionUpdatedBackwardsTransitionTests(TestCase):
    """
    Item 15: subscription.updated must never reactivate a cancelled plan.
    Item 16: QuerySet.update() in _handle_subscription_updated must include
             updated_at=timezone.now() since auto_now=True is not respected.
    """

    def _make_recurring_plan(self, user=None, status=PLAN_STATUS_ACTIVE, sub_id=None):
        if user is None:
            user = _make_user()
        return RecurringGiftPlan.objects.create(
            donor=user,
            amount=Decimal("25.00"),
            frequency="monthly",
            next_charge_date=timezone.now().date(),
            gateway_subscription_id=sub_id or f"sub_{uuid.uuid4().hex[:8]}",
            status=status,
        )

    def _run_subscription_updated(self, sub_id, stripe_status):
        event = _make_webhook_event(
            event_type="customer.subscription.updated",
            gateway_intent_id=sub_id,
        )
        mock_parse_return = (
            "customer.subscription.updated",
            {
                "gateway_subscription_id": sub_id,
                "status": stripe_status,
                "current_period_end": "1700000000",
                "cancel_at_period_end": False,
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))
        return event

    # Item 15 — cancelled plan must not be reactivated by subscription.updated(status=active)
    def test_subscription_updated_does_not_reactivate_cancelled_plan(self):
        """A subscription.updated(status=active) must not un-cancel an already-cancelled plan."""
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        plan = self._make_recurring_plan(sub_id=sub_id, status=PLAN_STATUS_CANCELLED)

        self._run_subscription_updated(sub_id, "active")

        plan.refresh_from_db()
        self.assertEqual(
            plan.status,
            PLAN_STATUS_CANCELLED,
            "Backwards transition prevention failed: cancelled plan was reactivated.",
        )

    # Item 15 — cancelled plan must not be set to paused either
    def test_subscription_updated_does_not_change_cancelled_plan_to_paused(self):
        """A subscription.updated(status=paused) must not change a cancelled plan to paused."""
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        plan = self._make_recurring_plan(sub_id=sub_id, status=PLAN_STATUS_CANCELLED)

        self._run_subscription_updated(sub_id, "paused")

        plan.refresh_from_db()
        self.assertEqual(plan.status, PLAN_STATUS_CANCELLED)

    # Item 16 — updated_at must be bumped by the QuerySet.update() call
    def test_subscription_updated_sets_updated_at(self):
        """updated_at must be bumped; auto_now=True is NOT respected by QuerySet.update()."""
        from datetime import timedelta
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        plan = self._make_recurring_plan(sub_id=sub_id, status=PLAN_STATUS_PAUSED)

        # Back-date updated_at so we can confirm it was bumped by the handler
        before = timezone.now() - timedelta(seconds=5)
        RecurringGiftPlan.objects.filter(pk=plan.pk).update(updated_at=before)

        self._run_subscription_updated(sub_id, "active")

        plan.refresh_from_db()
        self.assertGreater(
            plan.updated_at,
            before,
            "updated_at was not bumped — auto_now=True is not respected by "
            "QuerySet.update(); updated_at must be passed explicitly.",
        )
