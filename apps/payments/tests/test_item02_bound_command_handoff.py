from unittest.mock import patch

from django.db import transaction
from django.test import TransactionTestCase

from apps.payments.govstack_models import PaymentAttempt, PaymentExecutionIntent
from apps.payments.models import PaymentCommand, PaymentCommandOutbox
from apps.payments.payment_command_boundary import (
    PaymentCommandService,
    PaymentIdempotencyConflict,
    PaymentScope,
)


class BoundCommandHandoffTests(TransactionTestCase):
    def scope(self):
        return PaymentScope(caller_bb_id="caller", tenant_id="tenant")

    def reserve(self, identity="REQ000000001", payload=None):
        return PaymentCommandService.reserve(
            scope=self.scope(),
            operation="g2p_bulk_payment",
            request_identity=identity,
            payload=payload or {"BatchID": "batch-1", "CreditInstructions": []},
        )

    def test_command_attempt_intent_and_outbox_are_bound_before_publish(self):
        with patch.object(PaymentCommandService, "publish") as publish:
            command, replayed = self.reserve()
        self.assertFalse(replayed)
        command.refresh_from_db()
        self.assertIsNotNone(command.attempt_id)
        self.assertTrue(PaymentAttempt.objects.filter(pk=command.attempt_id).exists())
        self.assertTrue(PaymentExecutionIntent.objects.filter(attempt_id=command.attempt_id).exists())
        outbox = PaymentCommandOutbox.objects.get(command=command)
        self.assertEqual(outbox.payload["attempt_id"], str(command.attempt_id))
        self.assertIsNone(outbox.published_at)
        publish.assert_called_once_with(outbox.id)
        PaymentCommandService.publish(outbox.pk)
        outbox.refresh_from_db()
        command.refresh_from_db()
        self.assertIsNotNone(outbox.published_at)
        self.assertEqual(command.status, PaymentCommand.STATUS_DISPATCHED)

    def test_same_key_replay_reuses_single_bound_chain(self):
        first, first_replayed = self.reserve("REQ000000002")
        second, second_replayed = self.reserve("REQ000000002")
        self.assertFalse(first_replayed)
        self.assertTrue(second_replayed)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PaymentCommand.objects.count(), 1)
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(PaymentExecutionIntent.objects.count(), 1)
        self.assertEqual(PaymentCommandOutbox.objects.count(), 1)

    def test_changed_payload_conflicts_without_new_attempt(self):
        self.reserve("REQ000000003", {"value": 1})
        with self.assertRaises(PaymentIdempotencyConflict):
            self.reserve("REQ000000003", {"value": 2})
        self.assertEqual(PaymentCommand.objects.count(), 1)
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(PaymentExecutionIntent.objects.count(), 1)

    def test_rollback_leaves_no_bound_work(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.reserve("REQ000000004")
                raise RuntimeError("rollback")
        self.assertFalse(PaymentCommand.objects.filter(request_identity="REQ000000004").exists())
        self.assertFalse(PaymentAttempt.objects.filter(request_id="REQ000000004").exists())
        self.assertFalse(PaymentExecutionIntent.objects.filter(request_identity="REQ000000004").exists())

    def test_request_boundary_never_resolves_provider(self):
        with patch("apps.payments.provider_runtime.ProviderRuntime.resolve") as resolve:
            self.reserve("REQ000000005")
        resolve.assert_not_called()
