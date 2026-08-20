from django.test import TestCase

from apps.payments.execution_intent import (
    ExecutionIntentConflict,
    record_provider_correlation,
    reserve_execution_intent,
)
from apps.payments.govstack_models import PaymentAttempt, PaymentExecutionIntent


class PaymentExecutionIntentTests(TestCase):
    def create_attempt(self, *, request_id="request-1"):
        return PaymentAttempt.objects.create(
            tenant_id="intent-tenant",
            operation="g2p_bulk_instruction",
            request_id=request_id,
            payload_fingerprint=f"attempt-{request_id}",
        )

    def reserve(self, attempt, payload=None):
        return reserve_execution_intent(
            attempt=attempt,
            scope="intent-tenant",
            operation="g2p_bulk_instruction",
            request_identity=attempt.request_id,
            payload=payload or {"amount": "10.00", "currency": "USD"},
        )

    def test_creates_immutable_execution_identity(self):
        intent = self.reserve(self.create_attempt())
        self.assertEqual(intent.state, PaymentExecutionIntent.STATE_RESERVED)
        self.assertEqual(len(intent.payload_fingerprint), 64)

    def test_identical_reservation_replays_same_intent(self):
        attempt = self.create_attempt()
        first = self.reserve(attempt)
        second = self.reserve(attempt)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PaymentExecutionIntent.objects.count(), 1)

    def test_conflicting_payload_or_attempt_is_rejected(self):
        attempt = self.create_attempt()
        self.reserve(attempt)
        with self.assertRaises(ExecutionIntentConflict):
            self.reserve(attempt, {"amount": "11.00", "currency": "USD"})
        other = self.create_attempt(request_id="request-2")
        with self.assertRaises(ExecutionIntentConflict):
            reserve_execution_intent(
                attempt=other,
                scope="intent-tenant",
                operation="g2p_bulk_instruction",
                request_identity=attempt.request_id,
                payload={"amount": "10.00", "currency": "USD"},
            )

    def test_provider_correlation_is_write_once(self):
        intent = self.reserve(self.create_attempt())
        record_provider_correlation(intent, "provider-correlation-1")
        with self.assertRaises(ExecutionIntentConflict):
            record_provider_correlation(intent, "provider-correlation-2")
        intent.refresh_from_db()
        intent.scope = "different"
        with self.assertRaises(ValueError):
            intent.save()
