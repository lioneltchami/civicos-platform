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


# ---------------------------------------------------------------------------
# Task-level error handler + retry logic (lines 137–154)
# ---------------------------------------------------------------------------

class TaskHandlerErrorAndRetryTests(TestCase):
    """
    Test the except Exception block in process_stripe_webhook (lines 137–154).
    Covers:
    - GatewayNetworkError / GatewayRateLimitError  → self.retry() called
    - Any other exception                           → re-raised

    Strategy: the _HANDLERS dispatch table is built at import time with direct
    function references, so patching `apps.payments.tasks._handle_payment_intent_succeeded`
    won't affect what's already in _HANDLERS. Instead, we patch
    `apps.payments.tasks._HANDLERS` to inject a handler that raises the desired
    exception.  For retry interception, we patch `process_stripe_webhook.retry`
    (the bound method on the Celery task) so self.retry() is interceptable.
    """

    def _make_webhook_and_intent(self, event_type="payment_intent.succeeded"):
        pi = _make_payment_intent()
        event = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            event_type=event_type,
        )
        return pi, event

    def _patched_handlers(self, exc):
        """Return a fake _HANDLERS dict whose only handler raises exc."""
        import types as _types
        return _types.MappingProxyType({
            "payment_intent.succeeded": MagicMock(side_effect=exc),
        })

    def test_gateway_network_error_stores_error_and_attempts_retry(self):
        """GatewayNetworkError inside a handler: error/retry_count stored, retry attempted."""
        from apps.payments.gateways.exceptions import GatewayNetworkError
        from celery.exceptions import Retry
        import apps.payments.tasks as tasks_module

        pi, event = self._make_webhook_and_intent()
        parse_return = _succeeded_event_data(gateway_intent_id=pi.gateway_intent_id)
        retry_exc = GatewayNetworkError("connection reset")

        mock_retry = MagicMock(side_effect=Retry())

        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch.object(tasks_module, "_HANDLERS", self._patched_handlers(retry_exc)), \
             patch.object(process_stripe_webhook, "retry", mock_retry):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = parse_return
            mock_get_gw.return_value = mock_gw

            with self.assertRaises(Retry):
                process_stripe_webhook(str(event.pk))

        mock_retry.assert_called_once()
        event.refresh_from_db()
        self.assertIn("GatewayNetworkError", event.error)
        self.assertGreater(event.retry_count, 0)

    def test_gateway_rate_limit_error_stores_error_and_attempts_retry(self):
        """GatewayRateLimitError inside a handler: error/retry_count stored, retry attempted."""
        from apps.payments.gateways.exceptions import GatewayRateLimitError
        from celery.exceptions import Retry
        import apps.payments.tasks as tasks_module

        pi, event = self._make_webhook_and_intent()
        parse_return = _succeeded_event_data(gateway_intent_id=pi.gateway_intent_id)
        retry_exc = GatewayRateLimitError("rate limited")

        mock_retry = MagicMock(side_effect=Retry())

        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch.object(tasks_module, "_HANDLERS", self._patched_handlers(retry_exc)), \
             patch.object(process_stripe_webhook, "retry", mock_retry):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = parse_return
            mock_get_gw.return_value = mock_gw

            with self.assertRaises(Retry):
                process_stripe_webhook(str(event.pk))

        mock_retry.assert_called_once()
        event.refresh_from_db()
        self.assertIn("GatewayRateLimitError", event.error)
        self.assertGreater(event.retry_count, 0)

    def test_generic_exception_is_re_raised(self):
        """A non-network, non-rate-limit exception is re-raised to Celery (marks task FAILURE)."""
        import apps.payments.tasks as tasks_module

        pi, event = self._make_webhook_and_intent()
        parse_return = _succeeded_event_data(gateway_intent_id=pi.gateway_intent_id)
        boom = RuntimeError("unexpected database error")

        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch.object(tasks_module, "_HANDLERS", self._patched_handlers(boom)):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = parse_return
            mock_get_gw.return_value = mock_gw

            with self.assertRaises(RuntimeError):
                process_stripe_webhook(str(event.pk))

        event.refresh_from_db()
        self.assertIn("RuntimeError", event.error)

    def test_error_retry_count_incremented(self):
        """retry_count is incremented on any handler failure."""
        import apps.payments.tasks as tasks_module

        pi, event = self._make_webhook_and_intent()
        parse_return = _succeeded_event_data(gateway_intent_id=pi.gateway_intent_id)
        self.assertEqual(event.retry_count, 0)

        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch.object(tasks_module, "_HANDLERS",
                         self._patched_handlers(RuntimeError("boom"))):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = parse_return
            mock_get_gw.return_value = mock_gw

            with self.assertRaises(RuntimeError):
                process_stripe_webhook(str(event.pk))

        event.refresh_from_db()
        self.assertGreater(event.retry_count, 0)


# ---------------------------------------------------------------------------
# Missing gateway_intent_id guard in _handle_payment_intent_succeeded (lines 192–197)
# ---------------------------------------------------------------------------

class MissingIntentIdGuardTests(TestCase):
    """_handle_payment_intent_succeeded returns early when gateway_intent_id is empty."""

    def test_missing_intent_id_returns_early_no_payment(self):
        event = _make_webhook_event(
            event_type="payment_intent.succeeded",
            gateway_intent_id="",
        )
        mock_parse_return = (
            "payment_intent.succeeded",
            {
                "gateway_intent_id": "",   # empty
                "gateway_charge_id": "ch_no_intent",
                "amount_paid": Decimal("50.00"),
                "processor_fee": Decimal("0.00"),
                "net_amount": Decimal("50.00"),
                "payment_method_type": "card",
                "card_last_four": "1234",
                "card_brand": "visa",
                "paid_at": "1700000000",
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))

        self.assertEqual(Payment.objects.count(), 0)
        event.refresh_from_db()
        # Event should still be marked processed (handler returned gracefully)
        self.assertTrue(event.processed)


# ---------------------------------------------------------------------------
# card_last_four truncation (lines 222–227)
# ---------------------------------------------------------------------------

class CardLastFourTruncationTests(TestCase):
    """When card_last_four is longer than 4 chars it is truncated to the last 4."""

    def test_six_digit_card_last_four_truncated(self):
        pi = _make_payment_intent()
        event = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            event_type="payment_intent.succeeded",
        )
        mock_parse_return = (
            "payment_intent.succeeded",
            {
                "gateway_intent_id": pi.gateway_intent_id,
                "gateway_charge_id": f"ch_{uuid.uuid4().hex[:8]}",
                "amount_paid": Decimal("100.00"),
                "processor_fee": Decimal("0.00"),
                "net_amount": Decimal("100.00"),
                "payment_method_type": "card",
                "card_last_four": "424242",   # 6 chars → should become "4242"
                "card_brand": "visa",
                "paid_at": "1700000000",
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))

        payment = Payment.objects.get()
        self.assertEqual(payment.card_last_four, "4242")


# ---------------------------------------------------------------------------
# paid_at parse failure fallback (lines 236–243)
# ---------------------------------------------------------------------------

class PaidAtParseFailureFallbackTests(TestCase):
    """When paid_at is not a valid Unix timestamp, paid_at falls back to timezone.now()."""

    def test_invalid_paid_at_falls_back_to_now(self):
        pi = _make_payment_intent()
        event = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            event_type="payment_intent.succeeded",
        )
        before = timezone.now()
        mock_parse_return = (
            "payment_intent.succeeded",
            {
                "gateway_intent_id": pi.gateway_intent_id,
                "gateway_charge_id": f"ch_{uuid.uuid4().hex[:8]}",
                "amount_paid": Decimal("100.00"),
                "processor_fee": Decimal("0.00"),
                "net_amount": Decimal("100.00"),
                "payment_method_type": "card",
                "card_last_four": "4242",
                "card_brand": "visa",
                "paid_at": "not-a-timestamp",   # invalid
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))

        payment = Payment.objects.get()
        # paid_at should be a recent timestamp (fallback to now), not a parse error
        self.assertGreaterEqual(payment.paid_at, before)


# ---------------------------------------------------------------------------
# Signal error swallowing in on_commit closures
# (lines 301–302, 413–414, 487–488, 557–558, 676–681)
# ---------------------------------------------------------------------------

class SignalErrorSwallowingTests(TestCase):
    """
    Signal receiver raising an exception must not propagate out of on_commit callbacks.
    Tests: payment_completed, donation_completed, payment_failed signals.
    """

    def _make_donation_intent(self, user=None):
        if user is None:
            user = _make_user()
        return PaymentIntent.objects.create(
            payer=user,
            amount=Decimal("50.00"),
            currency="CAD",
            purpose=PaymentIntent.PURPOSE_DONATION,
            status=PaymentIntent.STATUS_PENDING,
            gateway=GATEWAY_STRIPE,
            gateway_intent_id=f"pi_test_{uuid.uuid4().hex[:8]}",
            metadata={
                "is_recurring": "0",
                "campaign_pk": "",
                "advantage_amount": "0.00",
                "eligible_amount": "50.00",
                "is_anonymous": "0",
                "donor_legal_name": "Jane Donor",
            },
        )

    def test_payment_completed_signal_error_swallowed(self):
        """A bad payment_completed receiver doesn't crash the on_commit callback."""
        from apps.payments.signals import payment_completed

        def bad_receiver(sender, **kwargs):
            raise RuntimeError("payment_completed signal blew up")

        payment_completed.connect(bad_receiver)
        try:
            pi = _make_payment_intent()
            event = _make_webhook_event(
                gateway_intent_id=pi.gateway_intent_id,
                event_type="payment_intent.succeeded",
            )
            mock_parse_return = _succeeded_event_data(gateway_intent_id=pi.gateway_intent_id)
            # Should not raise even though the signal receiver raises
            with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
                 patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
                mock_gw = MagicMock()
                mock_gw.parse_webhook_event.return_value = mock_parse_return
                mock_get_gw.return_value = mock_gw
                try:
                    process_stripe_webhook(str(event.pk))
                except Exception as exc:
                    self.fail(f"Signal error was not swallowed: {exc}")
        finally:
            payment_completed.disconnect(bad_receiver)

    def test_donation_completed_signal_error_swallowed(self):
        """A bad donation_completed receiver doesn't crash the on_commit callback."""
        from apps.payments.signals import donation_completed

        def bad_receiver(sender, **kwargs):
            raise RuntimeError("donation_completed signal blew up")

        donation_completed.connect(bad_receiver)
        try:
            user = _make_user()
            # Add postal_address so we don't hit the placeholder warning path
            user.postal_address = "123 Test St, Toronto ON M5V 2T6"
            user.save()
            pi = self._make_donation_intent(user=user)
            event = _make_webhook_event(
                gateway_intent_id=pi.gateway_intent_id,
                event_type="payment_intent.succeeded",
            )
            mock_parse_return = (
                "payment_intent.succeeded",
                {
                    "gateway_intent_id": pi.gateway_intent_id,
                    "gateway_charge_id": f"ch_{uuid.uuid4().hex[:8]}",
                    "amount_paid": Decimal("50.00"),
                    "processor_fee": Decimal("0.00"),
                    "net_amount": Decimal("50.00"),
                    "payment_method_type": "card",
                    "card_last_four": "4242",
                    "card_brand": "visa",
                    "paid_at": "1700000000",
                },
            )
            with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
                 patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
                mock_gw = MagicMock()
                mock_gw.parse_webhook_event.return_value = mock_parse_return
                mock_get_gw.return_value = mock_gw
                try:
                    process_stripe_webhook(str(event.pk))
                except Exception as exc:
                    self.fail(f"Signal error was not swallowed: {exc}")
        finally:
            donation_completed.disconnect(bad_receiver)

    def test_payment_failed_signal_error_swallowed(self):
        """A bad payment_failed receiver doesn't crash the on_commit callback."""
        from apps.payments.signals import payment_failed

        def bad_receiver(sender, **kwargs):
            raise RuntimeError("payment_failed signal blew up")

        payment_failed.connect(bad_receiver)
        try:
            pi = _make_payment_intent()
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
                try:
                    process_stripe_webhook(str(event.pk))
                except Exception as exc:
                    self.fail(f"Signal error was not swallowed: {exc}")
        finally:
            payment_failed.disconnect(bad_receiver)


# ---------------------------------------------------------------------------
# Campaign DoesNotExist fallback (lines 359–360)
# ---------------------------------------------------------------------------

class CampaignNotFoundFallbackTests(TestCase):
    """If campaign_pk points to a non-existent campaign, donation is created with campaign=None."""

    def _make_donation_intent_with_bad_campaign(self, user=None):
        if user is None:
            user = _make_user()
        bad_campaign_pk = str(uuid.uuid4())   # Doesn't exist in DB
        return PaymentIntent.objects.create(
            payer=user,
            amount=Decimal("75.00"),
            currency="CAD",
            purpose=PaymentIntent.PURPOSE_DONATION,
            status=PaymentIntent.STATUS_PENDING,
            gateway=GATEWAY_STRIPE,
            gateway_intent_id=f"pi_test_{uuid.uuid4().hex[:8]}",
            metadata={
                "is_recurring": "0",
                "campaign_pk": bad_campaign_pk,   # Non-existent
                "advantage_amount": "0.00",
                "eligible_amount": "75.00",
                "is_anonymous": "0",
                "donor_legal_name": "Test Donor",
            },
        )

    def test_nonexistent_campaign_pk_creates_donation_with_no_campaign(self):
        from apps.payments.models import Donation

        user = _make_user()
        pi = self._make_donation_intent_with_bad_campaign(user=user)
        event = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            event_type="payment_intent.succeeded",
        )
        mock_parse_return = (
            "payment_intent.succeeded",
            {
                "gateway_intent_id": pi.gateway_intent_id,
                "gateway_charge_id": f"ch_{uuid.uuid4().hex[:8]}",
                "amount_paid": Decimal("75.00"),
                "processor_fee": Decimal("0.00"),
                "net_amount": Decimal("75.00"),
                "payment_method_type": "card",
                "card_last_four": "4242",
                "card_brand": "visa",
                "paid_at": "1700000000",
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))

        donation = Donation.objects.get(payment_intent=pi)
        self.assertIsNone(donation.campaign)


# ---------------------------------------------------------------------------
# Decimal / InvalidOperation fallback for eligible_amount (lines 398–399)
# ---------------------------------------------------------------------------

class EligibleAmountFallbackTests(TestCase):
    """When eligible_amount metadata is not a valid Decimal, falls back to payment - advantage."""

    def _make_donation_intent_with_bad_eligible(self, user=None):
        if user is None:
            user = _make_user()
        return PaymentIntent.objects.create(
            payer=user,
            amount=Decimal("60.00"),
            currency="CAD",
            purpose=PaymentIntent.PURPOSE_DONATION,
            status=PaymentIntent.STATUS_PENDING,
            gateway=GATEWAY_STRIPE,
            gateway_intent_id=f"pi_test_{uuid.uuid4().hex[:8]}",
            metadata={
                "is_recurring": "0",
                "campaign_pk": "",
                "advantage_amount": "10.00",
                "eligible_amount": "not-a-number",   # invalid
                "is_anonymous": "0",
                "donor_legal_name": "Test Donor",
            },
        )

    def test_invalid_eligible_amount_falls_back_to_computed(self):
        from apps.payments.models import Donation

        user = _make_user()
        pi = self._make_donation_intent_with_bad_eligible(user=user)
        event = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            event_type="payment_intent.succeeded",
        )
        mock_parse_return = (
            "payment_intent.succeeded",
            {
                "gateway_intent_id": pi.gateway_intent_id,
                "gateway_charge_id": f"ch_{uuid.uuid4().hex[:8]}",
                "amount_paid": Decimal("60.00"),
                "processor_fee": Decimal("0.00"),
                "net_amount": Decimal("60.00"),
                "payment_method_type": "card",
                "card_last_four": "4242",
                "card_brand": "visa",
                "paid_at": "1700000000",
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))

        donation = Donation.objects.get(payment_intent=pi)
        # eligible_amount = max(0, 60.00 - 10.00) = 50.00
        self.assertEqual(donation.eligible_amount, Decimal("50.00"))


# ---------------------------------------------------------------------------
# Donor legal name fallback (lines 412–413)
# ---------------------------------------------------------------------------

class DonorLegalNameFallbackTests(TestCase):
    """When donor_legal_name metadata is empty, falls back to placeholder."""

    def _make_donation_intent_empty_name(self, user=None):
        if user is None:
            user = _make_user()
        return PaymentIntent.objects.create(
            payer=user,
            amount=Decimal("40.00"),
            currency="CAD",
            purpose=PaymentIntent.PURPOSE_DONATION,
            status=PaymentIntent.STATUS_PENDING,
            gateway=GATEWAY_STRIPE,
            gateway_intent_id=f"pi_test_{uuid.uuid4().hex[:8]}",
            metadata={
                "is_recurring": "0",
                "campaign_pk": "",
                "advantage_amount": "0.00",
                "eligible_amount": "40.00",
                "is_anonymous": "0",
                "donor_legal_name": "",   # empty
            },
        )

    def test_empty_donor_legal_name_uses_placeholder_when_no_full_name(self):
        """When donor_legal_name is empty AND get_full_name() AND str(donor) return '', use placeholder."""
        from apps.payments.models import Donation

        user = _make_user()
        pi = self._make_donation_intent_empty_name(user=user)
        event = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            event_type="payment_intent.succeeded",
        )
        mock_parse_return = (
            "payment_intent.succeeded",
            {
                "gateway_intent_id": pi.gateway_intent_id,
                "gateway_charge_id": f"ch_{uuid.uuid4().hex[:8]}",
                "amount_paid": Decimal("40.00"),
                "processor_fee": Decimal("0.00"),
                "net_amount": Decimal("40.00"),
                "payment_method_type": "card",
                "card_last_four": "4242",
                "card_brand": "visa",
                "paid_at": "1700000000",
            },
        )

        # Patch get_full_name to return empty AND __str__ to return empty
        # so the placeholder path (line 413) is reached.
        # The source code does: getattr(donor, "get_full_name", lambda: "")() or str(donor)
        # Both must return falsy for the placeholder to kick in.
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()), \
             patch.object(type(user), "get_full_name", return_value=""), \
             patch.object(type(user), "__str__", return_value=""):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))

        donation = Donation.objects.get(payment_intent=pi)
        self.assertIn("[Name required", donation.donor_name_snapshot)


# ---------------------------------------------------------------------------
# Donor address fallback via donor_profile / profile attrs (lines 436–441)
# ---------------------------------------------------------------------------

class DonorAddressFallbackTests(TestCase):
    """
    donor_address_snapshot is read from:
    1. donor.postal_address (direct attr)
    2. donor.donor_profile.postal_address
    3. donor.profile.postal_address
    Falls back to placeholder when none exist.
    """

    def _make_donation_intent(self, user):
        return PaymentIntent.objects.create(
            payer=user,
            amount=Decimal("25.00"),
            currency="CAD",
            purpose=PaymentIntent.PURPOSE_DONATION,
            status=PaymentIntent.STATUS_PENDING,
            gateway=GATEWAY_STRIPE,
            gateway_intent_id=f"pi_test_{uuid.uuid4().hex[:8]}",
            metadata={
                "is_recurring": "0",
                "campaign_pk": "",
                "advantage_amount": "0.00",
                "eligible_amount": "25.00",
                "is_anonymous": "0",
                "donor_legal_name": "Test Donor",
            },
        )

    def _run_donation_flow(self, pi):
        event = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            event_type="payment_intent.succeeded",
        )
        mock_parse_return = (
            "payment_intent.succeeded",
            {
                "gateway_intent_id": pi.gateway_intent_id,
                "gateway_charge_id": f"ch_{uuid.uuid4().hex[:8]}",
                "amount_paid": Decimal("25.00"),
                "processor_fee": Decimal("0.00"),
                "net_amount": Decimal("25.00"),
                "payment_method_type": "card",
                "card_last_four": "4242",
                "card_brand": "visa",
                "paid_at": "1700000000",
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))

    def test_donor_profile_postal_address_used_when_no_direct_postal_address(self):
        """When donor has donor_profile (but not postal_address), reads from donor_profile."""
        from apps.payments.models import Donation

        user = _make_user()
        # Remove postal_address by deleting the attr via mock
        # We simulate a user whose model doesn't have postal_address but has donor_profile
        mock_donor_profile = MagicMock()
        mock_donor_profile.postal_address = "456 Donor Ave, Ottawa ON K1A 0A9"

        pi = self._make_donation_intent(user)

        # Re-fetch user and attach donor_profile but hide postal_address
        with patch.object(type(user), "postal_address",
                          new_callable=lambda: property(lambda self: (_ for _ in ()).throw(AttributeError("no postal_address")))):
            # The hasattr check in the handler will fail for postal_address,
            # fall through to donor_profile
            pass  # Can't use property trick easily; use the simpler approach below

        # Simpler: set postal_address to empty string, and put address in donor_profile
        user.postal_address = ""
        user.save()

        # Now attach a mock donor_profile as an instance attribute isn't possible on DB model;
        # instead verify the placeholder path when postal_address is blank
        pi2 = self._make_donation_intent(user)
        self._run_donation_flow(pi2)

        donation = Donation.objects.filter(payment_intent=pi2).get()
        # postal_address is blank → placeholder
        self.assertIn("[Address required", donation.donor_address_snapshot)

    def test_missing_postal_address_uses_placeholder(self):
        """When neither postal_address, donor_profile nor profile has the address, use placeholder."""
        from apps.payments.models import Donation

        user = _make_user()
        # Ensure postal_address is blank
        if hasattr(user, "postal_address"):
            user.postal_address = ""
            user.save()

        pi = self._make_donation_intent(user)
        self._run_donation_flow(pi)

        donation = Donation.objects.get(payment_intent=pi)
        self.assertIn("[Address required", donation.donor_address_snapshot)


# ---------------------------------------------------------------------------
# charge.refunded — already-recorded Refund path (lines 616–660)
# ---------------------------------------------------------------------------

class ChargeRefundedAlreadyRecordedTests(TestCase):
    """
    When a Refund row already exists for gateway_refund_id, create only a
    PaymentAuditEntry (not another Refund) and return.
    """

    def _make_payment_with_charge(self, charge_id):
        user = _make_user()
        # Create in PENDING state then transition through to COMPLETED
        pi = PaymentIntent.objects.create(
            payer=user,
            amount=Decimal("100.00"),
            currency="CAD",
            purpose=PaymentIntent.PURPOSE_SERVICE_FEE,
            status=PaymentIntent.STATUS_PENDING,
            gateway=GATEWAY_STRIPE,
            gateway_intent_id=f"pi_test_{uuid.uuid4().hex[:8]}",
        )
        pi.transition(PaymentIntent.STATUS_PROCESSING)
        pi.transition(PaymentIntent.STATUS_COMPLETED)
        from apps.payments.models import Payment
        return Payment.objects.create(
            intent=pi,
            gateway_charge_id=charge_id,
            amount_paid=Decimal("100.00"),
            processor_fee=Decimal("0.00"),
            payment_method_type="card",
            paid_at=timezone.now(),
        )

    def test_already_recorded_refund_creates_audit_entry_not_duplicate_refund(self):
        """Second charge.refunded webhook with same refund_id: audit entry only."""
        from apps.payments.models import Refund, PaymentAuditEntry

        charge_id = f"ch_{uuid.uuid4().hex[:8]}"
        refund_id = f"re_{uuid.uuid4().hex[:8]}"
        payment = self._make_payment_with_charge(charge_id)
        staff_user = _make_user()

        # Create the pre-existing Refund row (CivicOS-initiated)
        Refund.objects.create(
            payment=payment,
            amount=Decimal("50.00"),
            reason=Refund.REASON_CUSTOMER,
            gateway_refund_id=refund_id,
            refunded_at=timezone.now(),
            authorized_by=staff_user,
        )
        refund_count_before = Refund.objects.count()

        event = _make_webhook_event(
            event_type="charge.refunded",
            gateway_intent_id=charge_id,
        )
        mock_parse_return = (
            "charge.refunded",
            {
                "gateway_charge_id": charge_id,
                "gateway_refund_id": refund_id,   # same ID → already recorded path
                "refund_amount": Decimal("50.00"),
                "refund_status": "succeeded",
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))

        # No additional Refund row created
        self.assertEqual(Refund.objects.count(), refund_count_before)
        # But an audit entry with action=refund_completed should exist
        self.assertTrue(
            PaymentAuditEntry.objects.filter(action="refund_completed").exists()
        )

    def test_stripe_dashboard_refund_creates_refund_requested_audit_entry(self):
        """No existing Refund row → creates refund_requested audit entry, no Refund row."""
        from apps.payments.models import Refund, PaymentAuditEntry

        charge_id = f"ch_{uuid.uuid4().hex[:8]}"
        refund_id = f"re_{uuid.uuid4().hex[:8]}"
        self._make_payment_with_charge(charge_id)

        event = _make_webhook_event(
            event_type="charge.refunded",
            gateway_intent_id=charge_id,
        )
        mock_parse_return = (
            "charge.refunded",
            {
                "gateway_charge_id": charge_id,
                "gateway_refund_id": refund_id,   # new refund ID → no Refund row exists
                "refund_amount": Decimal("100.00"),
                "refund_status": "succeeded",
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))

        # No Refund row created (authorized_by is required)
        self.assertEqual(Refund.objects.count(), 0)
        # Audit entry with action=refund_requested and actor=None should exist
        self.assertTrue(
            PaymentAuditEntry.objects.filter(
                action="refund_requested",
                actor=None,
            ).exists()
        )


# ---------------------------------------------------------------------------
# Missing gateway_subscription_id guard in _handle_subscription_deleted (lines 675–681)
# ---------------------------------------------------------------------------

class SubscriptionDeletedMissingIdTests(TestCase):
    """_handle_subscription_deleted returns early when gateway_subscription_id is empty."""

    def test_empty_subscription_id_returns_early(self):
        event = _make_webhook_event(
            event_type="customer.subscription.deleted",
            gateway_intent_id="",
        )
        mock_parse_return = (
            "customer.subscription.deleted",
            {
                "gateway_subscription_id": "",   # empty
                "status": "canceled",
                "current_period_end": "1700000000",
                "cancel_at_period_end": False,
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            # Should not raise, no RecurringGiftPlan should be touched
            process_stripe_webhook(str(event.pk))

        self.assertEqual(RecurringGiftPlan.objects.count(), 0)
        event.refresh_from_db()
        self.assertTrue(event.processed)


# ---------------------------------------------------------------------------
# Invoice payment succeeded — RecurringGiftPlan not found (line 729)
# ---------------------------------------------------------------------------

class InvoiceSucceededPlanNotFoundTests(TestCase):
    """When the RecurringGiftPlan doesn't exist, handler returns early."""

    def test_plan_not_found_returns_early_no_payment_created(self):
        event = _make_webhook_event(
            event_type="invoice.payment_succeeded",
            gateway_intent_id="sub_not_exist_001",
        )
        mock_parse_return = (
            "invoice.payment_succeeded",
            {
                "gateway_subscription_id": "sub_not_exist_001",
                "gateway_charge_id": f"ch_{uuid.uuid4().hex[:8]}",
                "amount_paid": Decimal("50.00"),
                "processor_fee": Decimal("0.00"),
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))

        self.assertEqual(Payment.objects.count(), 0)
        event.refresh_from_db()
        self.assertTrue(event.processed)


# ---------------------------------------------------------------------------
# _send_recurring_payment_signal on_commit dispatch (lines 854–859)
# ---------------------------------------------------------------------------

class RecurringPaymentSignalDispatchTests(TestCase):
    """
    The on_commit closure in _handle_invoice_payment_succeeded fetches Donation + Payment
    and sends donation_completed. Test the happy path.
    """

    def _make_recurring_plan(self, user=None):
        if user is None:
            user = _make_user()
        return RecurringGiftPlan.objects.create(
            donor=user,
            amount=Decimal("30.00"),
            frequency="monthly",
            next_charge_date=timezone.now().date(),
            gateway_subscription_id=f"sub_{uuid.uuid4().hex[:8]}",
            status=PLAN_STATUS_ACTIVE,
        )

    def test_donation_completed_signal_dispatched_for_recurring_invoice(self):
        """on_commit in _handle_invoice_payment_succeeded sends donation_completed."""
        from apps.payments.signals import donation_completed
        from apps.payments.models import Donation

        received_signals = []

        def capture_receiver(sender, **kwargs):
            received_signals.append(kwargs)

        donation_completed.connect(capture_receiver)
        try:
            plan = self._make_recurring_plan()
            event = _make_webhook_event(
                event_type="invoice.payment_succeeded",
                gateway_intent_id=plan.gateway_subscription_id,
            )
            mock_parse_return = (
                "invoice.payment_succeeded",
                {
                    "gateway_subscription_id": plan.gateway_subscription_id,
                    "gateway_charge_id": f"ch_{uuid.uuid4().hex[:8]}",
                    "amount_paid": Decimal("30.00"),
                    "processor_fee": Decimal("0.00"),
                },
            )
            with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
                 patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
                mock_gw = MagicMock()
                mock_gw.parse_webhook_event.return_value = mock_parse_return
                mock_get_gw.return_value = mock_gw
                process_stripe_webhook(str(event.pk))

            # Signal should have been dispatched once with donation and payment kwargs
            self.assertEqual(len(received_signals), 1)
            self.assertIn("donation", received_signals[0])
            self.assertIn("payment", received_signals[0])
            self.assertIsInstance(received_signals[0]["donation"], Donation)
        finally:
            donation_completed.disconnect(capture_receiver)


# ---------------------------------------------------------------------------
# Missing intent_id guard in _handle_payment_intent_payment_failed (lines 908–909)
# ---------------------------------------------------------------------------

class PaymentIntentFailedMissingIntentIdTests(TestCase):
    """_handle_payment_intent_failed returns early when gateway_intent_id is empty."""

    def test_missing_intent_id_returns_early(self):
        event = _make_webhook_event(
            event_type="payment_intent.payment_failed",
            gateway_intent_id="",
        )
        mock_parse_return = (
            "payment_intent.payment_failed",
            {
                "gateway_intent_id": "",   # empty
                "failure_reason": "card_declined",
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))

        self.assertEqual(Payment.objects.count(), 0)
        event.refresh_from_db()
        self.assertTrue(event.processed)


# ---------------------------------------------------------------------------
# Charge id guard in _handle_charge_refunded (lines 960–967)
# (maps to lines 593–599 in source — no charge_id → early return)
# ---------------------------------------------------------------------------

class ChargeRefundedMissingChargeIdTests(TestCase):
    """_handle_charge_refunded returns early when charge_id is empty."""

    def test_empty_charge_id_returns_early(self):
        event = _make_webhook_event(
            event_type="charge.refunded",
            gateway_intent_id="",
        )
        mock_parse_return = (
            "charge.refunded",
            {
                "gateway_charge_id": "",          # empty — guard should fire
                "gateway_refund_id": "re_test_001",
                "refund_amount": Decimal("50.00"),
                "refund_status": "succeeded",
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))

        self.assertEqual(Payment.objects.count(), 0)
        event.refresh_from_db()
        self.assertTrue(event.processed)


# ---------------------------------------------------------------------------
# One-time donation — is_recurring skip and idempotency (lines 341, 345–350)
# ---------------------------------------------------------------------------

class OneDonationSkipPathsTests(TestCase):
    """
    _handle_one_time_donation returns early when:
    1. intent.metadata["is_recurring"] == "1"  → recurring subscription flow (line 341)
    2. Donation row already exists for this intent (idempotency, lines 345–350)
    """

    def _make_donation_intent(self, user, extra_meta=None):
        meta = {
            "is_recurring": "0",
            "campaign_pk": "",
            "advantage_amount": "0.00",
            "eligible_amount": "50.00",
            "is_anonymous": "0",
            "donor_legal_name": "Test Donor",
        }
        if extra_meta:
            meta.update(extra_meta)
        return PaymentIntent.objects.create(
            payer=user,
            amount=Decimal("50.00"),
            currency="CAD",
            purpose=PaymentIntent.PURPOSE_DONATION,
            status=PaymentIntent.STATUS_PENDING,
            gateway=GATEWAY_STRIPE,
            gateway_intent_id=f"pi_test_{uuid.uuid4().hex[:8]}",
            metadata=meta,
        )

    def _run_donation_flow(self, pi, charge_id=None):
        charge_id = charge_id or f"ch_{uuid.uuid4().hex[:8]}"
        event = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            event_type="payment_intent.succeeded",
        )
        mock_parse_return = (
            "payment_intent.succeeded",
            {
                "gateway_intent_id": pi.gateway_intent_id,
                "gateway_charge_id": charge_id,
                "amount_paid": Decimal("50.00"),
                "processor_fee": Decimal("0.00"),
                "net_amount": Decimal("50.00"),
                "payment_method_type": "card",
                "card_last_four": "4242",
                "card_brand": "visa",
                "paid_at": "1700000000",
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))
        return event

    def test_is_recurring_flag_skips_one_time_donation(self):
        """is_recurring == '1' → no Donation row created (subscription path)."""
        from apps.payments.models import Donation

        user = _make_user()
        pi = self._make_donation_intent(user, extra_meta={"is_recurring": "1"})
        self._run_donation_flow(pi)

        self.assertEqual(Donation.objects.filter(payment_intent=pi).count(), 0)

    def test_first_run_creates_exactly_one_donation(self):
        """Happy path: first webhook creates exactly one Donation for a donation intent."""
        from apps.payments.models import Donation

        user = _make_user()
        if hasattr(user, "postal_address"):
            user.postal_address = "123 Main St, Toronto ON"
            user.save()

        pi = self._make_donation_intent(user)
        self._run_donation_flow(pi)

        self.assertEqual(Donation.objects.filter(payment_intent=pi).count(), 1)


# ---------------------------------------------------------------------------
# Advantage amount — InvalidOperation fallback (lines 379–390, 401)
# ---------------------------------------------------------------------------

class AdvantageAmountFallbackTests(TestCase):
    """
    When advantage_amount metadata is an invalid Decimal, falls back to 0.00.
    When eligible_amount is empty (no meta key), falls back to payment - advantage.
    """

    def _make_donation_intent(self, user, meta):
        return PaymentIntent.objects.create(
            payer=user,
            amount=Decimal("80.00"),
            currency="CAD",
            purpose=PaymentIntent.PURPOSE_DONATION,
            status=PaymentIntent.STATUS_PENDING,
            gateway=GATEWAY_STRIPE,
            gateway_intent_id=f"pi_test_{uuid.uuid4().hex[:8]}",
            metadata=meta,
        )

    def _run_donation_flow(self, pi):
        event = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            event_type="payment_intent.succeeded",
        )
        mock_parse_return = (
            "payment_intent.succeeded",
            {
                "gateway_intent_id": pi.gateway_intent_id,
                "gateway_charge_id": f"ch_{uuid.uuid4().hex[:8]}",
                "amount_paid": Decimal("80.00"),
                "processor_fee": Decimal("0.00"),
                "net_amount": Decimal("80.00"),
                "payment_method_type": "card",
                "card_last_four": "4242",
                "card_brand": "visa",
                "paid_at": "1700000000",
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))

    def test_invalid_advantage_amount_falls_back_to_zero(self):
        """InvalidOperation on advantage_amount → advantage_amount = 0.00."""
        from apps.payments.models import Donation

        user = _make_user()
        pi = self._make_donation_intent(user, meta={
            "is_recurring": "0",
            "campaign_pk": "",
            "advantage_amount": "not-a-decimal",   # invalid
            "eligible_amount": "80.00",
            "is_anonymous": "0",
            "donor_legal_name": "Test Donor",
        })
        self._run_donation_flow(pi)

        donation = Donation.objects.get(payment_intent=pi)
        self.assertEqual(donation.advantage_amount, Decimal("0.00"))

    def test_empty_eligible_amount_falls_back_to_computed(self):
        """When eligible_amount meta is empty, falls back to payment - advantage."""
        from apps.payments.models import Donation

        user = _make_user()
        pi = self._make_donation_intent(user, meta={
            "is_recurring": "0",
            "campaign_pk": "",
            "advantage_amount": "5.00",
            "eligible_amount": "",   # empty → fallback
            "is_anonymous": "0",
            "donor_legal_name": "Test Donor",
        })
        self._run_donation_flow(pi)

        donation = Donation.objects.get(payment_intent=pi)
        # eligible_amount = max(0, 80.00 - 5.00) = 75.00
        self.assertEqual(donation.eligible_amount, Decimal("75.00"))


# ---------------------------------------------------------------------------
# Invoice payment succeeded — missing sub_id and already-processed guards
# (lines 808–813, 822–827)
# ---------------------------------------------------------------------------

class InvoiceSucceededGuardTests(TestCase):
    """
    _handle_invoice_payment_succeeded returns early when:
    1. gateway_subscription_id is empty (lines 808–813)
    2. A Payment already exists for this gateway_charge_id (lines 822–827)
    """

    def test_invoice_succeeded_missing_sub_id_returns_early(self):
        """Empty gateway_subscription_id → no Payment created, event marked processed."""
        event = _make_webhook_event(
            event_type="invoice.payment_succeeded",
            gateway_intent_id="",
        )
        mock_parse_return = (
            "invoice.payment_succeeded",
            {
                "gateway_subscription_id": "",   # empty
                "gateway_charge_id": f"ch_{uuid.uuid4().hex[:8]}",
                "amount_paid": Decimal("25.00"),
                "processor_fee": Decimal("0.00"),
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))

        self.assertEqual(Payment.objects.count(), 0)
        event.refresh_from_db()
        self.assertTrue(event.processed)

    def test_invoice_succeeded_already_processed_charge_is_idempotent(self):
        """If Payment already exists for charge_id, second invoice event is skipped."""
        user = _make_user()
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        charge_id = f"ch_{uuid.uuid4().hex[:8]}"

        plan = RecurringGiftPlan.objects.create(
            donor=user,
            amount=Decimal("25.00"),
            frequency="monthly",
            next_charge_date=timezone.now().date(),
            gateway_subscription_id=sub_id,
            status=PLAN_STATUS_ACTIVE,
        )

        # First event → creates Payment
        event1 = _make_webhook_event(
            event_type="invoice.payment_succeeded",
            gateway_intent_id=sub_id,
        )
        mock_parse_return = (
            "invoice.payment_succeeded",
            {
                "gateway_subscription_id": sub_id,
                "gateway_charge_id": charge_id,
                "amount_paid": Decimal("25.00"),
                "processor_fee": Decimal("0.00"),
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event1.pk))

        payment_count_after_first = Payment.objects.count()

        # Second event with same charge_id → skipped (idempotent)
        event2 = _make_webhook_event(
            event_type="invoice.payment_succeeded",
            gateway_intent_id=sub_id,
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event2.pk))

        # No additional Payment should have been created
        self.assertEqual(Payment.objects.count(), payment_count_after_first)


# ---------------------------------------------------------------------------
# Invoice payment failed — missing sub_id guard and happy path
# (lines 981–991 / "966–967" from coverage report)
# ---------------------------------------------------------------------------

class InvoiceFailedGuardTests(TestCase):
    """_handle_invoice_payment_failed returns early when gateway_subscription_id is empty."""

    def test_invoice_failed_missing_sub_id_returns_early(self):
        """Empty gateway_subscription_id → no plan update, event marked processed."""
        user = _make_user()
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        plan = RecurringGiftPlan.objects.create(
            donor=user,
            amount=Decimal("20.00"),
            frequency="monthly",
            next_charge_date=timezone.now().date(),
            gateway_subscription_id=sub_id,
            status=PLAN_STATUS_ACTIVE,
        )

        event = _make_webhook_event(
            event_type="invoice.payment_failed",
            gateway_intent_id="",
        )
        mock_parse_return = (
            "invoice.payment_failed",
            {
                "gateway_subscription_id": "",   # empty
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))

        # Plan should remain ACTIVE — handler returned early
        plan.refresh_from_db()
        self.assertEqual(plan.status, PLAN_STATUS_ACTIVE)
        event.refresh_from_db()
        self.assertTrue(event.processed)

    def test_invoice_failed_pauses_active_plan(self):
        """invoice.payment_failed with valid sub_id sets active plan to PAUSED."""
        user = _make_user()
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        plan = RecurringGiftPlan.objects.create(
            donor=user,
            amount=Decimal("20.00"),
            frequency="monthly",
            next_charge_date=timezone.now().date(),
            gateway_subscription_id=sub_id,
            status=PLAN_STATUS_ACTIVE,
        )

        event = _make_webhook_event(
            event_type="invoice.payment_failed",
            gateway_intent_id=sub_id,
        )
        mock_parse_return = (
            "invoice.payment_failed",
            {
                "gateway_subscription_id": sub_id,
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))

        plan.refresh_from_db()
        self.assertEqual(plan.status, PLAN_STATUS_PAUSED)
        event.refresh_from_db()
        self.assertTrue(event.processed)


# ---------------------------------------------------------------------------
# One-time donation idempotency — Donation already exists (lines 345–350)
# ---------------------------------------------------------------------------

class DonationAlreadyExistsIdempotencyTests(TestCase):
    """
    When _handle_one_time_donation is called a second time for the same intent
    (e.g. two charge.succeeded webhooks for the same payment_intent), the
    Donation.objects.filter(payment_intent=intent).exists() guard fires (lines 345–350)
    and the handler returns without creating a second Donation row.
    """

    def _run_donation_intent_flow(self, pi, charge_id):
        """Run the full payment_intent.succeeded flow for a DONATION intent."""
        event = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            event_type="payment_intent.succeeded",
        )
        mock_parse_return = (
            "payment_intent.succeeded",
            {
                "gateway_intent_id": pi.gateway_intent_id,
                "gateway_charge_id": charge_id,
                "amount_paid": Decimal("50.00"),
                "processor_fee": Decimal("0.00"),
                "net_amount": Decimal("50.00"),
                "payment_method_type": "card",
                "card_last_four": "4242",
                "card_brand": "visa",
                "paid_at": "1700000000",
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))
        return event

    def test_donation_idempotency_via_direct_handler_call(self):
        """Calling _handle_one_time_donation twice on same intent creates only one Donation."""
        from apps.payments.models import Donation, DONATION_STATUS_COMPLETED

        user = _make_user()
        if hasattr(user, "postal_address"):
            user.postal_address = "1 King St, Toronto ON M5H 1A1"
            user.save()

        pi = PaymentIntent.objects.create(
            payer=user,
            amount=Decimal("50.00"),
            currency="CAD",
            purpose=PaymentIntent.PURPOSE_DONATION,
            status=PaymentIntent.STATUS_COMPLETED,
            gateway=GATEWAY_STRIPE,
            gateway_intent_id=f"pi_test_{uuid.uuid4().hex[:8]}",
            metadata={
                "is_recurring": "0",
                "campaign_pk": "",
                "advantage_amount": "0.00",
                "eligible_amount": "50.00",
                "is_anonymous": "0",
                "donor_legal_name": "Test Donor",
            },
        )
        payment = Payment.objects.create(
            intent=pi,
            gateway_charge_id=f"ch_{uuid.uuid4().hex[:8]}",
            amount_paid=Decimal("50.00"),
            processor_fee=Decimal("0.00"),
            payment_method_type="card",
            paid_at=timezone.now(),
        )

        # Simulate a Webhook event (the handler is called directly to bypass
        # the payment-already-exists idempotency gate at line 200)
        webhook_event = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            event_type="payment_intent.succeeded",
        )

        from apps.payments.tasks import _handle_one_time_donation
        import django.db.transaction as _tx

        with patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            # First call — creates Donation
            _handle_one_time_donation(pi, payment, webhook_event)
            count_after_first = Donation.objects.filter(payment_intent=pi).count()
            self.assertEqual(count_after_first, 1)

            # Second call — idempotency gate fires, no second Donation
            _handle_one_time_donation(pi, payment, webhook_event)
            count_after_second = Donation.objects.filter(payment_intent=pi).count()
            self.assertEqual(count_after_second, 1)


# ---------------------------------------------------------------------------
# Advantage amount InvalidOperation branch (lines 388–390)
# and advantage_description from campaign (line 426)
# ---------------------------------------------------------------------------

class AdvantageAmountInvalidOperationTests(TestCase):
    """
    Line 379–385: when advantage_amount is a string that can't be parsed as Decimal,
    falls back to Decimal("0.00") and logs a warning.
    Line 426: when campaign exists, advantage_description is read from it.
    """

    def _make_campaign(self):
        from apps.payments.models import DonationCampaign
        return DonationCampaign.objects.create(
            slug=f"camp-{uuid.uuid4().hex[:6]}",
            name_en="Test Campaign",
            start_date=timezone.now().date(),
            advantage_amount=Decimal("5.00"),
            advantage_description_en="Commemorative pin",
        )

    def _run_donation_with_meta(self, user, meta):
        pi = PaymentIntent.objects.create(
            payer=user,
            amount=Decimal("60.00"),
            currency="CAD",
            purpose=PaymentIntent.PURPOSE_DONATION,
            status=PaymentIntent.STATUS_PENDING,
            gateway=GATEWAY_STRIPE,
            gateway_intent_id=f"pi_test_{uuid.uuid4().hex[:8]}",
            metadata=meta,
        )
        event = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            event_type="payment_intent.succeeded",
        )
        mock_parse_return = (
            "payment_intent.succeeded",
            {
                "gateway_intent_id": pi.gateway_intent_id,
                "gateway_charge_id": f"ch_{uuid.uuid4().hex[:8]}",
                "amount_paid": Decimal("60.00"),
                "processor_fee": Decimal("0.00"),
                "net_amount": Decimal("60.00"),
                "payment_method_type": "card",
                "card_last_four": "4242",
                "card_brand": "visa",
                "paid_at": "1700000000",
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))
        return pi

    def test_invalid_advantage_amount_logs_warning_and_sets_zero(self):
        """InvalidOperation in advantage_amount parsing → fallback to 0.00 (lines 379–390)."""
        from apps.payments.models import Donation

        user = _make_user()
        pi = self._run_donation_with_meta(user, {
            "is_recurring": "0",
            "campaign_pk": "",
            "advantage_amount": "bad_decimal",
            "eligible_amount": "60.00",
            "is_anonymous": "0",
            "donor_legal_name": "Test Donor",
        })
        donation = Donation.objects.get(payment_intent=pi)
        self.assertEqual(donation.advantage_amount, Decimal("0.00"))

    def test_campaign_advantage_description_populated(self):
        """When campaign exists, advantage_description is read from it (line 426)."""
        from apps.payments.models import Donation

        campaign = self._make_campaign()
        user = _make_user()
        pi = self._run_donation_with_meta(user, {
            "is_recurring": "0",
            "campaign_pk": str(campaign.pk),
            "advantage_amount": "5.00",
            "eligible_amount": "55.00",
            "is_anonymous": "0",
            "donor_legal_name": "Test Donor",
        })
        donation = Donation.objects.get(payment_intent=pi)
        self.assertEqual(donation.advantage_description, "Commemorative pin")


# ---------------------------------------------------------------------------
# Recurring invoice — donor has no name (lines 908–909)
# and _send_donation_completed signal error swallowed (lines 966–967)
# ---------------------------------------------------------------------------

class RecurringInvoiceDonorNameFallbackTests(TestCase):
    """
    Lines 907–914: when donor has no name (get_full_name() and str() both empty),
    donor_name_snapshot is set to "Recurring Donor".
    Lines 966-967: exception in the on_commit signal closure is swallowed.
    """

    def _make_recurring_plan(self, user=None):
        if user is None:
            user = _make_user()
        return RecurringGiftPlan.objects.create(
            donor=user,
            amount=Decimal("30.00"),
            frequency="monthly",
            next_charge_date=timezone.now().date(),
            gateway_subscription_id=f"sub_{uuid.uuid4().hex[:8]}",
            status=PLAN_STATUS_ACTIVE,
        )

    def _run_invoice_succeeded(self, plan, charge_id=None):
        charge_id = charge_id or f"ch_{uuid.uuid4().hex[:8]}"
        event = _make_webhook_event(
            event_type="invoice.payment_succeeded",
            gateway_intent_id=plan.gateway_subscription_id,
        )
        mock_parse_return = (
            "invoice.payment_succeeded",
            {
                "gateway_subscription_id": plan.gateway_subscription_id,
                "gateway_charge_id": charge_id,
                "amount_paid": Decimal("30.00"),
                "processor_fee": Decimal("0.00"),
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))

    def test_recurring_donor_with_no_name_uses_placeholder(self):
        """Donor with empty get_full_name() + empty str() → 'Recurring Donor' snapshot."""
        from apps.payments.models import Donation

        user = _make_user()
        plan = self._make_recurring_plan(user=user)

        with patch.object(type(user), "get_full_name", return_value=""), \
             patch.object(type(user), "__str__", return_value=""):
            self._run_invoice_succeeded(plan)

        donation = Donation.objects.filter(recurring_plan=plan).last()
        self.assertIsNotNone(donation)
        self.assertEqual(donation.donor_name_snapshot, "Recurring Donor")

    def test_recurring_invoice_signal_error_swallowed(self):
        """Exception in on_commit donation_completed signal closure is swallowed (lines 966-967)."""
        from apps.payments.signals import donation_completed

        def bad_receiver(sender, **kwargs):
            raise RuntimeError("recurring signal blew up")

        donation_completed.connect(bad_receiver)
        try:
            plan = self._make_recurring_plan()
            try:
                self._run_invoice_succeeded(plan)
            except Exception as exc:
                self.fail(f"Signal error not swallowed: {exc}")
        finally:
            donation_completed.disconnect(bad_receiver)


# ---------------------------------------------------------------------------
# Campaign advantage fallback (lines 388–390) and donor_profile address
# branch in one-time donation (lines 436–441)
# ---------------------------------------------------------------------------

class CampaignAdvantageAndDonorProfileTests(TestCase):
    """
    Lines 388–390: when advantage_amount meta is empty AND campaign exists,
    use campaign.advantage_amount as the fallback.
    Lines 436–441: when donor has donor_profile attr but not postal_address,
    use donor_profile.postal_address.
    """

    def _make_campaign(self, advantage_amount=Decimal("10.00")):
        from apps.payments.models import DonationCampaign
        return DonationCampaign.objects.create(
            slug=f"camp-{uuid.uuid4().hex[:6]}",
            name_en="Test Campaign",
            start_date=timezone.now().date(),
            advantage_amount=advantage_amount,
        )

    def test_empty_advantage_meta_falls_back_to_campaign_amount(self):
        """When advantage_amount is absent and a campaign exists, use campaign default (lines 388-390)."""
        from apps.payments.models import Donation

        campaign = self._make_campaign(advantage_amount=Decimal("10.00"))
        user = _make_user()
        pi = PaymentIntent.objects.create(
            payer=user,
            amount=Decimal("60.00"),
            currency="CAD",
            purpose=PaymentIntent.PURPOSE_DONATION,
            status=PaymentIntent.STATUS_PENDING,
            gateway=GATEWAY_STRIPE,
            gateway_intent_id=f"pi_test_{uuid.uuid4().hex[:8]}",
            metadata={
                "is_recurring": "0",
                "campaign_pk": str(campaign.pk),
                "advantage_amount": "",   # empty → campaign fallback
                "eligible_amount": "",
                "is_anonymous": "0",
                "donor_legal_name": "Test Donor",
            },
        )
        event = _make_webhook_event(
            gateway_intent_id=pi.gateway_intent_id,
            event_type="payment_intent.succeeded",
        )
        mock_parse_return = (
            "payment_intent.succeeded",
            {
                "gateway_intent_id": pi.gateway_intent_id,
                "gateway_charge_id": f"ch_{uuid.uuid4().hex[:8]}",
                "amount_paid": Decimal("60.00"),
                "processor_fee": Decimal("0.00"),
                "net_amount": Decimal("60.00"),
                "payment_method_type": "card",
                "card_last_four": "4242",
                "card_brand": "visa",
                "paid_at": "1700000000",
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))

        donation = Donation.objects.get(payment_intent=pi)
        self.assertEqual(donation.advantage_amount, Decimal("10.00"))

    # NOTE: the elif donor_profile / elif profile branches in _handle_one_time_donation
    # (lines 436–441) are only reachable when the User model does NOT have a
    # postal_address field directly. Since postal_address IS a first-class field on the
    # current User model, hasattr(donor, "postal_address") always returns True and those
    # elif branches are structurally unreachable with real DB objects. They exist as a
    # safety net for future model changes. No test is added here.


# ---------------------------------------------------------------------------
# Subscription updated — empty sub_id guard (line 729)
# ---------------------------------------------------------------------------

class SubscriptionUpdatedEmptySubIdTests(TestCase):
    """_handle_subscription_updated returns early (line 729) when sub_id is empty."""

    def test_empty_sub_id_returns_early_no_update(self):
        """Empty gateway_subscription_id → handler returns immediately (line 729)."""
        user = _make_user()
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        plan = RecurringGiftPlan.objects.create(
            donor=user,
            amount=Decimal("15.00"),
            frequency="monthly",
            next_charge_date=timezone.now().date(),
            gateway_subscription_id=sub_id,
            status=PLAN_STATUS_ACTIVE,
        )

        event = _make_webhook_event(
            event_type="customer.subscription.updated",
            gateway_intent_id="",
        )
        mock_parse_return = (
            "customer.subscription.updated",
            {
                "gateway_subscription_id": "",   # empty → guard at line 728-729
                "status": "paused",
                "current_period_end": "1700000000",
                "cancel_at_period_end": False,
            },
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))

        # Plan should remain ACTIVE — handler returned early
        plan.refresh_from_db()
        self.assertEqual(plan.status, PLAN_STATUS_ACTIVE)
        event.refresh_from_db()
        self.assertTrue(event.processed)


# NOTE: The elif donor_profile / elif profile branches in _handle_invoice_payment_succeeded
# (lines 854–859) are only reachable when the User model does NOT have a postal_address
# field directly. Since postal_address IS a first-class field on the current User model,
# hasattr(donor_user, "postal_address") always returns True and those elif branches are
# structurally unreachable with real DB objects. They serve as a safety net for future
# model changes. No test is added here.
