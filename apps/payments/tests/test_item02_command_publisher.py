from unittest.mock import patch

from django.test import TransactionTestCase

from apps.payments.models import PaymentCommandOutbox
from apps.payments.payment_command_boundary import PaymentCommandService, PaymentScope


class PaymentCommandPublisherTests(TransactionTestCase):
    def test_bound_command_publishes_consumer_after_commit(self):
        with patch.object(PaymentCommandService, "enqueue_consumer") as enqueue:
            command, replayed = PaymentCommandService.reserve(
                scope=PaymentScope(caller_bb_id="caller", tenant_id="tenant"),
                operation="g2p_bulk_payment",
                request_identity="REQ000000007",
                payload={"BatchID": "batch-1"},
            )
        self.assertFalse(replayed)
        command.refresh_from_db()
        outbox = PaymentCommandOutbox.objects.get(command=command)
        self.assertIsNotNone(command.attempt_id)
        self.assertIsNotNone(outbox.published_at)
        self.assertEqual(outbox.payload["attempt_id"], str(command.attempt_id))
        enqueue.assert_called_once_with(str(outbox.pk))

    def test_replay_does_not_enqueue_second_consumer_delivery(self):
        with patch.object(PaymentCommandService, "enqueue_consumer") as enqueue:
            first, _ = PaymentCommandService.reserve(
                scope=PaymentScope(caller_bb_id="caller", tenant_id="tenant"),
                operation="g2p_bulk_payment",
                request_identity="REQ000000008",
                payload={"BatchID": "batch-1"},
            )
            second, replayed = PaymentCommandService.reserve(
                scope=PaymentScope(caller_bb_id="caller", tenant_id="tenant"),
                operation="g2p_bulk_payment",
                request_identity="REQ000000008",
                payload={"BatchID": "batch-1"},
            )
        self.assertTrue(replayed)
        self.assertEqual(first.pk, second.pk)
        enqueue.assert_called_once()
