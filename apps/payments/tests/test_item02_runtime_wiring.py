import json
from decimal import Decimal

from django.test import TestCase

from apps.payments.execution_intent import record_provider_correlation, reserve_execution_intent
from apps.payments.govstack_failure_services import PaymentLifecycleService
from apps.payments.govstack_models import PaymentAttempt
from apps.payments.govstack_provider import ProviderOutcome, ProviderResult
from apps.payments.platform_scope import IdempotencyService
from apps.payments.provider_runtime import ProviderRuntime, orchestrate_attempt
from apps.payments.providers.deterministic import DeterministicProvider


class ProviderRuntimeIntegrationTests(TestCase):
    def tearDown(self):
        ProviderRuntime.clear()

    def _attempt(self, request_id: str, *, status: str = PaymentAttempt.STATUS_PENDING):
        attempt, _ = PaymentLifecycleService.get_or_create_attempt(
            tenant_id="tenant-runtime",
            operation="g2p_bulk_instruction",
            request_id=request_id,
            payload={"request_id": request_id, "amount": "4.25", "currency": "USD"},
            amount=Decimal("4.25"),
            currency="USD",
        )
        if status != PaymentAttempt.STATUS_PENDING:
            attempt.status = status
            attempt.save(update_fields=["status"])
        return attempt

    def test_configured_runtime_submits_and_records_verified_finality(self):
        attempt = self._attempt("runtime-settled")
        provider = DeterministicProvider(
            outcomes=[
                ProviderResult(
                    ProviderOutcome.SETTLED,
                    provider_attempt_id="provider-attempt-1",
                    external_transaction_id="provider-transaction-1",
                    observation_id="provider-event-1",
                    event_id="provider-event-1",
                    verified=True,
                    verification_method="deterministic-test",
                )
            ]
        )
        ProviderRuntime.configure(
            tenant_id="tenant-runtime",
            operation="g2p_bulk_instruction",
            provider=provider,
        )

        result = orchestrate_attempt(str(attempt.pk))

        attempt.refresh_from_db()
        self.assertEqual(result.outcome, ProviderOutcome.SETTLED)
        # A fresh worker-local deterministic adapter is materialized from the
        # durable registration; the web-process fixture itself must not receive
        # the invocation.
        self.assertEqual(provider.submissions, [])
        self.assertEqual(attempt.status, PaymentAttempt.STATUS_SETTLED)
        self.assertEqual(attempt.reconciliations.latest("created_at").status, "matched")

    def test_uncertain_attempt_polls_before_any_resubmission(self):
        attempt = self._attempt("runtime-poll", status=PaymentAttempt.STATUS_UNCERTAIN)
        attempt.recovery_evidence = {"ambiguous_outcome": True}
        attempt.save(update_fields=["recovery_evidence", "updated_at"])
        provider = DeterministicProvider(
            outcomes=[ProviderResult(ProviderOutcome.SETTLED)],
            statuses={
                "runtime-poll": ProviderResult(
                    ProviderOutcome.REJECTED,
                    provider_attempt_id="provider-attempt-2",
                    observation_id="provider-event-2",
                    event_id="provider-event-2",
                    verified=True,
                    verification_method="deterministic-test",
                )
            },
        )
        ProviderRuntime.configure(
            tenant_id="tenant-runtime",
            operation="g2p_bulk_instruction",
            provider=provider,
        )

        orchestrate_attempt(str(attempt.pk))

        attempt.refresh_from_db()
        # The poll was performed by a newly materialized worker adapter, not by
        # the original process-local fixture.
        self.assertEqual(provider.submissions, [])
        self.assertEqual(provider.status_queries, [])
        self.assertEqual(attempt.status, PaymentAttempt.STATUS_REJECTED)

    def test_durable_correlation_forces_status_first_polling_from_pending(self):
        attempt = self._attempt("runtime-correlation")
        intent = reserve_execution_intent(
            attempt=attempt,
            scope="tenant-runtime",
            operation="g2p_bulk_instruction",
            request_identity="runtime-correlation",
            payload={"request_id": "runtime-correlation", "amount": "4.25", "currency": "USD"},
        )
        record_provider_correlation(intent, "correlation-1")
        provider = DeterministicProvider(
            outcomes=[ProviderResult(ProviderOutcome.SETTLED)],
            statuses={
                "correlation-1": ProviderResult(
                    ProviderOutcome.REJECTED,
                    external_transaction_id="correlation-1",
                    observation_id="correlation-event-1",
                    event_id="correlation-event-1",
                    verified=True,
                    verification_method="deterministic-test",
                )
            },
        )
        ProviderRuntime.configure(
            tenant_id="tenant-runtime",
            operation="g2p_bulk_instruction",
            provider=provider,
        )

        orchestrate_attempt(str(attempt.pk))

        attempt.refresh_from_db()
        self.assertEqual(provider.submissions, [])
        self.assertEqual(attempt.status, PaymentAttempt.STATUS_REJECTED)

    def test_unconfigured_runtime_fails_closed_without_provider_submission(self):
        attempt = self._attempt("runtime-unavailable")

        result = orchestrate_attempt(str(attempt.pk))

        attempt.refresh_from_db()
        self.assertEqual(result.code, "PROVIDER_UNAVAILABLE")
        self.assertEqual(attempt.status, PaymentAttempt.STATUS_UNCERTAIN)
        self.assertFalse(attempt.observations.latest("created_at").accepted_finality)


class DurableHttpIdempotencyTests(TestCase):
    def test_matching_replay_and_changed_payload_conflict_are_durable(self):
        row, first_writer = IdempotencyService.reserve(
            tenant_id="tenant-runtime",
            method="POST",
            path="/govstack/payments/billTransferRequests",
            key="key-1",
            payload={"requestId": "request-1", "amount": "4.25"},
        )
        self.assertTrue(first_writer)
        IdempotencyService.complete(
            row,
            status_code=202,
            body={"responseCode": "00", "requestID": "request-1"},
        )

        replay_row, first_writer = IdempotencyService.reserve(
            tenant_id="tenant-runtime",
            method="POST",
            path="/govstack/payments/billTransferRequests",
            key="key-1",
            payload={"amount": "4.25", "requestId": "request-1"},
        )
        self.assertFalse(first_writer)
        replay = IdempotencyService.replay(replay_row)
        self.assertEqual(replay.status_code, 202)
        self.assertEqual(
            json.loads(replay.content), {"responseCode": "00", "requestID": "request-1"}
        )

        with self.assertRaises(ValueError):
            IdempotencyService.reserve(
                tenant_id="tenant-runtime",
                method="POST",
                path="/govstack/payments/billTransferRequests",
                key="key-1",
                payload={"requestId": "request-1", "amount": "9.99"},
            )
