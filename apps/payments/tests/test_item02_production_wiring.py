from decimal import Decimal

from django.test import TestCase

from apps.payments.govstack_failure_services import PaymentLifecycleService
from apps.payments.govstack_models import PaymentAttempt
from apps.payments.govstack_provider import ProviderOutcome, ProviderResult


class ProviderObservationFinalityTests(TestCase):
    def _attempt(self, request_id: str) -> PaymentAttempt:
        attempt, _ = PaymentLifecycleService.get_or_create_attempt(
            tenant_id="tenant-a",
            operation="p2g_bill_notification",
            request_id=request_id,
            payload={"request": request_id, "amount": "12.50", "currency": "USD"},
            amount=Decimal("12.50"),
            currency="USD",
        )
        return attempt

    def test_verified_identified_provider_settlement_establishes_finality(self):
        attempt = self._attempt("verified-finality")
        observation = PaymentLifecycleService.record_provider_result(
            attempt,
            ProviderResult(
                ProviderOutcome.SETTLED,
                external_transaction_id="provider-tx-1",
                observation_id="provider-event-1",
                event_id="provider-event-1",
                verified=True,
                verification_method="deterministic-test",
            ),
        )
        attempt.refresh_from_db()
        self.assertTrue(observation.accepted_finality)
        self.assertEqual(attempt.status, PaymentAttempt.STATUS_SETTLED)
        self.assertEqual(attempt.external_transaction_id, "provider-tx-1")
        self.assertEqual(attempt.reconciliations.latest("created_at").status, "matched")

    def test_unverified_provider_result_is_review_not_finality(self):
        attempt = self._attempt("unverified-finality")
        observation = PaymentLifecycleService.record_provider_result(
            attempt,
            ProviderResult(
                ProviderOutcome.SETTLED,
                external_transaction_id="provider-tx-2",
                observation_id="provider-event-2",
                event_id="provider-event-2",
                verified=False,
            ),
        )
        attempt.refresh_from_db()
        self.assertFalse(observation.accepted_finality)
        self.assertEqual(attempt.status, PaymentAttempt.STATUS_REVIEW)
        self.assertEqual(attempt.reconciliations.latest("created_at").status, "unknown")

    def test_timeout_is_uncertain_and_never_blindly_settled(self):
        attempt = self._attempt("timeout-is-uncertain")
        observation = PaymentLifecycleService.record_provider_result(
            attempt,
            ProviderResult(
                ProviderOutcome.TIMEOUT,
                provider_attempt_id="provider-attempt-3",
                observation_id="provider-event-3",
                event_id="provider-event-3",
            ),
        )
        attempt.refresh_from_db()
        self.assertFalse(observation.accepted_finality)
        self.assertEqual(attempt.status, PaymentAttempt.STATUS_UNCERTAIN)
