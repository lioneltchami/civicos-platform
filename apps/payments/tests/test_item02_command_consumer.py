from unittest.mock import patch

from django.test import TransactionTestCase

from apps.payments.govstack_models import PaymentAttempt, PaymentExecutionIntent
from apps.payments.models import PaymentCommandOutbox
from apps.payments.payment_command_boundary import PaymentCommandService, PaymentScope
from apps.payments.payment_command_consumer import (
    InvalidPaymentCommandDelivery,
    acknowledge_reserved_command,
)


class PaymentCommandConsumerFenceTests(TransactionTestCase):
    def setUp(self):
        command, _ = PaymentCommandService.reserve(
            scope=PaymentScope(caller_bb_id="caller", tenant_id="tenant"),
            operation="g2p_bulk_payment",
            request_identity="REQ000000006",
            payload={"BatchID": "batch-1"},
        )
        self.outbox = PaymentCommandOutbox.objects.get(command=command)
        self.delivery = {
            "command_id": str(command.pk),
            "attempt_id": str(command.attempt_id),
            "tenant_id": "tenant",
            "operation": "g2p_bulk_payment",
            "fingerprint": command.fingerprint,
            "acknowledgement_token": "consumer-token-1",
        }

    def test_first_valid_delivery_acknowledges_and_schedules_once(self):
        with patch("apps.payments.payment_command_consumer._schedule_orchestration") as schedule:
            result = acknowledge_reserved_command(outbox_id=self.outbox.pk, delivery=self.delivery)
        self.assertTrue(result["acknowledged"])
        self.assertTrue(result["scheduled"])
        schedule.assert_called_once_with(self.delivery["attempt_id"])
        self.outbox.refresh_from_db()
        self.assertIsNotNone(self.outbox.acknowledged_at)
        self.assertEqual(self.outbox.acknowledgement_token, "consumer-token-1")

    def test_duplicate_delivery_returns_stored_result_without_second_schedule(self):
        with patch("apps.payments.payment_command_consumer._schedule_orchestration") as schedule:
            first = acknowledge_reserved_command(outbox_id=self.outbox.pk, delivery=self.delivery)
            second = acknowledge_reserved_command(outbox_id=self.outbox.pk, delivery=self.delivery)
        self.assertEqual(second, first)
        schedule.assert_called_once_with(self.delivery["attempt_id"])

    def test_mismatched_delivery_is_side_effect_free(self):
        invalid = dict(self.delivery, fingerprint="0" * 64)
        with patch("apps.payments.payment_command_consumer._schedule_orchestration") as schedule:
            with self.assertRaises(InvalidPaymentCommandDelivery):
                acknowledge_reserved_command(outbox_id=self.outbox.pk, delivery=invalid)
        self.outbox.refresh_from_db()
        self.assertIsNone(self.outbox.acknowledged_at)
        schedule.assert_not_called()
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(PaymentExecutionIntent.objects.count(), 1)
