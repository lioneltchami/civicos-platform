from unittest.mock import patch

from django.test import TransactionTestCase

from apps.payments.execution_intent import ExecutionIntentConflict, record_provider_correlation
from apps.payments.models import PaymentCommand, PaymentCommandOutbox
from apps.payments.payment_command_boundary import PaymentCommandService, PaymentScope
from apps.payments.payment_command_tasks import consume_bound_command_outbox_task


class RB01ConsumerDurableTests(TransactionTestCase):
    reset_sequences = True

    def _reserve(self, request_id="rb01-consumer"):
        with patch.object(PaymentCommandService, "enqueue_consumer"):
            command, _ = PaymentCommandService.reserve(
                scope=PaymentScope(caller_bb_id="caller", tenant_id="rb01-tenant"),
                operation="g2p_bulk_payment",
                request_identity=request_id,
                payload={"BatchID": request_id},
            )
        return command, PaymentCommandOutbox.objects.get(command=command)

    @staticmethod
    def _delivery(command):
        return {
            "command_id": str(command.pk),
            "attempt_id": str(command.attempt_id),
            "tenant_id": command.tenant_id,
            "operation": command.operation,
            "fingerprint": command.fingerprint,
            "acknowledgement_token": "rb01-durable-token",
        }

    def test_bound_command_is_published_before_consumer_acknowledgement(self):
        command, outbox = self._reserve()
        command.refresh_from_db()
        outbox.refresh_from_db()
        self.assertEqual(command.status, PaymentCommand.STATUS_DISPATCHED)
        self.assertIsNotNone(outbox.published_at)
        self.assertIsNone(outbox.acknowledged_at)
        self.assertIsNotNone(command.attempt_id)
        self.assertEqual(command.attempt.execution_intent.attempt_id, command.attempt_id)

    def test_duplicate_delivery_persists_one_acknowledgement_and_one_schedule(self):
        command, outbox = self._reserve("rb01-redelivery")
        with patch("apps.payments.payment_command_consumer._schedule_orchestration") as schedule:
            first = consume_bound_command_outbox_task(str(outbox.pk))
            second = consume_bound_command_outbox_task(str(outbox.pk))
        outbox.refresh_from_db()
        self.assertEqual(first, second)
        self.assertIsNotNone(outbox.acknowledged_at)
        self.assertTrue(outbox.acknowledgement_token)
        schedule.assert_called_once_with(str(command.attempt_id))

    def test_correlation_collision_fails_closed_without_overwrite(self):
        command, _ = self._reserve("rb01-collision")
        intent = command.attempt.execution_intent
        record_provider_correlation(intent, "correlation-original")
        with self.assertRaises(ExecutionIntentConflict):
            record_provider_correlation(intent, "correlation-conflict")
        intent.refresh_from_db()
        self.assertEqual(intent.provider_correlation, "correlation-original")
