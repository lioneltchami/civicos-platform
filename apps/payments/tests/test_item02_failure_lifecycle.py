from decimal import Decimal

from django.test import TestCase

from apps.payments.govstack_failure_services import PaymentLifecycleService
from apps.payments.govstack_models import IdempotencyConflict, PaymentAttempt, PaymentOutcome


class Item02FailureLifecycleTests(TestCase):
    def payload(self, amount="10.00"):
        return {"bill_id": "B-1", "amount": amount, "currency": "USD"}

    def test_same_key_same_payload_replays_one_attempt(self):
        a, created = PaymentLifecycleService.get_or_create_attempt(
            tenant_id="t1",
            operation="p2g",
            request_id="r1",
            payload=self.payload(),
            amount=Decimal("10.00"),
            currency="USD",
        )
        b, created2 = PaymentLifecycleService.get_or_create_attempt(
            tenant_id="t1",
            operation="p2g",
            request_id="r1",
            payload=self.payload(),
            amount=Decimal("10.00"),
            currency="USD",
        )
        self.assertTrue(created)
        self.assertFalse(created2)
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(PaymentAttempt.objects.count(), 1)

    def test_same_key_changed_payload_conflicts(self):
        PaymentLifecycleService.get_or_create_attempt(
            tenant_id="t1", operation="g2p", request_id="r1", payload=self.payload()
        )
        with self.assertRaises(IdempotencyConflict):
            PaymentLifecycleService.get_or_create_attempt(
                tenant_id="t1", operation="g2p", request_id="r1", payload=self.payload("11.00")
            )

    def test_outcomes_are_typed_and_uncertain_requires_reconciliation(self):
        a, _ = PaymentLifecycleService.get_or_create_attempt(
            tenant_id="t1", operation="g2p", request_id="r2", payload=self.payload()
        )
        PaymentLifecycleService.apply_outcome(
            a, PaymentOutcome("uncertain", "TIMEOUT_AFTER_ACCEPT", "timeout", False, "pa-1")
        )
        a.refresh_from_db()
        self.assertEqual(a.status, PaymentAttempt.STATUS_UNCERTAIN)
        recon = PaymentLifecycleService.reconcile(a, "settled", "source-settled")
        self.assertEqual(recon.status, "mismatch")

    def test_settlement_is_terminal_and_invalid_transition_is_blocked(self):
        a, _ = PaymentLifecycleService.get_or_create_attempt(
            tenant_id="t1", operation="g2p", request_id="r3", payload=self.payload()
        )
        PaymentLifecycleService.apply_outcome(
            a, PaymentOutcome("settled", external_transaction_id="tx-1")
        )
        a.refresh_from_db()
        self.assertTrue(a.is_terminal)
        PaymentLifecycleService.apply_outcome(a, PaymentOutcome("rejected"))
        a.refresh_from_db()
        self.assertEqual(a.external_transaction_id, "tx-1")

    def test_retry_is_due_with_backoff(self):
        a, _ = PaymentLifecycleService.get_or_create_attempt(
            tenant_id="t1", operation="g2p", request_id="r4", payload=self.payload()
        )
        PaymentLifecycleService.apply_outcome(
            a, PaymentOutcome("retryable", "NETWORK", "transport", True)
        )
        a.refresh_from_db()
        self.assertEqual(a.status, PaymentAttempt.STATUS_RETRYABLE)
        self.assertFalse(PaymentLifecycleService.due_retries().filter(pk=a.pk).exists())
