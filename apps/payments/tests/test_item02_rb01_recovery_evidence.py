from datetime import timedelta
import threading
from unittest import mock

from django.test import TransactionTestCase
from django.utils import timezone

from apps.payments.govstack_failure_services import PaymentLifecycleService
from apps.payments.govstack_models import PaymentAttempt
from apps.payments.execution_intent import reserve_execution_intent
from apps.payments.govstack_provider import ProviderOutcome, ProviderResult
from apps.payments.provider_runtime import ProviderRuntime, orchestrate_attempt
from apps.payments.providers.deterministic import DeterministicProvider


class RB01RecoveryEvidenceTests(TransactionTestCase):
    reset_sequences = True
    def _attempt(self, request_id, **changes):
        attempt, _ = PaymentLifecycleService.get_or_create_attempt(
            tenant_id="rb01-tenant",
            operation="g2p_bulk_instruction",
            request_id=request_id,
            payload={"request_id": request_id, "amount": "1.00", "currency": "USD"},
            amount=1,
            currency="USD",
        )
        for name, value in changes.items():
            setattr(attempt, name, value)
        if changes:
            attempt.save(update_fields=[*changes, "updated_at"])
        return attempt

    def _configure(self, outcome):
        ProviderRuntime.configure(
            tenant_id="rb01-tenant",
            operation="g2p_bulk_instruction",
            provider=DeterministicProvider([outcome]),
        )

    def tearDown(self):
        ProviderRuntime.clear()

    def test_reserved_attempt_submits_first_when_no_durable_external_evidence_exists(self):
        attempt = self._attempt("rb01-first")
        self._configure(
            ProviderResult(
                ProviderOutcome.REJECTED,
                observation_id="rb01-first-observation",
                event_id="rb01-first-event",
                verified=True,
                verification_method="test",
            )
        )
        result = orchestrate_attempt(str(attempt.pk))
        attempt.refresh_from_db()
        self.assertEqual(result.outcome, ProviderOutcome.REJECTED)
        self.assertEqual(attempt.status, PaymentAttempt.STATUS_REJECTED)

    def test_live_claim_is_not_taken_over(self):
        attempt = self._attempt(
            "rb01-live",
            claim_token="worker-a",
            claim_generation=7,
            claim_expires_at=timezone.now() + timedelta(minutes=1),
        )
        result = ProviderRuntime.submit_or_poll(str(attempt.pk))
        attempt.refresh_from_db()
        self.assertEqual(result.code, "CLAIMED")
        self.assertEqual(attempt.claim_token, "worker-a")
        self.assertEqual(attempt.claim_generation, 7)

    def test_expired_claim_is_taken_over_with_new_generation(self):
        attempt = self._attempt(
            "rb01-expired",
            claim_token="worker-a",
            claim_generation=7,
            claim_expires_at=timezone.now() - timedelta(seconds=1),
        )
        self._configure(
            ProviderResult(
                ProviderOutcome.REJECTED,
                observation_id="rb01-expired-observation",
                event_id="rb01-expired-event",
                verified=True,
                verification_method="test",
            )
        )
        orchestrate_attempt(str(attempt.pk))
        attempt.refresh_from_db()
        self.assertEqual(attempt.claim_generation, 8)
        self.assertEqual(attempt.status, PaymentAttempt.STATUS_REJECTED)

    def test_timeout_stays_non_final_and_records_ambiguous_recovery_evidence(self):
        attempt = self._attempt("rb01-timeout")
        self._configure(ProviderResult(ProviderOutcome.TIMEOUT, code="TIMEOUT"))
        result = orchestrate_attempt(str(attempt.pk))
        attempt.refresh_from_db()
        self.assertEqual(result.outcome, ProviderOutcome.TIMEOUT)
        self.assertEqual(attempt.status, PaymentAttempt.STATUS_UNCERTAIN)
        self.assertTrue(attempt.recovery_evidence.get("ambiguous_outcome"))

    def test_stale_claim_finalization_cannot_persist_after_takeover(self):
        attempt = self._attempt(
            "rb01-stale",
            claim_token="worker-a",
            claim_generation=4,
            claim_expires_at=timezone.now() - timedelta(seconds=1),
        )
        # Simulate the durable state written by a takeover before worker A's
        # delayed completion tries to persist.
        attempt.claim_token = "worker-b"
        attempt.claim_generation = 5
        attempt.claim_expires_at = timezone.now() + timedelta(minutes=5)
        attempt.save(
            update_fields=[
                "claim_token",
                "claim_generation",
                "claim_expires_at",
                "updated_at",
            ]
        )

        result = ProviderRuntime.finalize_claimed_result(
            attempt_id=str(attempt.pk),
            token="worker-a",
            generation=4,
            result=ProviderResult(
                ProviderOutcome.SETTLED,
                observation_id="rb01-stale-observation",
                event_id="rb01-stale-event",
                verified=True,
                verification_method="test",
            ),
        )

        attempt.refresh_from_db()
        self.assertEqual(result.code, "STALE_CLAIM")
        self.assertEqual(attempt.status, PaymentAttempt.STATUS_PENDING)
        self.assertEqual(attempt.claim_token, "worker-b")
        self.assertEqual(attempt.claim_generation, 5)
        self.assertFalse(
            attempt.observations.filter(
                observation_id="rb01-stale-observation"
            ).exists()
        )

    def test_submit_admission_marker_forces_later_worker_to_poll_without_reopening_submit(self):
        attempt = self._attempt("rb01-submit-admission")
        intent = reserve_execution_intent(
            attempt=attempt,
            scope="rb01-tenant",
            operation=attempt.operation,
            request_identity=attempt.request_id,
            payload={"request_id": attempt.request_id, "amount": "1.00", "currency": "USD"},
        )
        intent.submit_started_at = timezone.now()
        intent.submit_admission_generation = 1
        intent.save(
            update_fields=[
                "submit_started_at",
                "submit_admission_generation",
                "updated_at",
            ]
        )
        self._configure(
            ProviderResult(
                ProviderOutcome.REJECTED,
                observation_id="rb01-submit-admission-observation",
                event_id="rb01-submit-admission-event",
                verified=True,
                verification_method="test",
            )
        )

        orchestrate_attempt(str(attempt.pk))

        intent.refresh_from_db()
        self.assertIsNotNone(intent.submit_started_at)
        self.assertEqual(intent.submit_admission_generation, 1)

    def test_delayed_worker_completion_is_fenced_after_expiry_takeover(self):
        attempt = self._attempt("rb01-delayed-worker")
        self._configure(
            ProviderResult(
                ProviderOutcome.UNCERTAIN,
                provider_attempt_id="rb01-provider-attempt",
                external_transaction_id="rb01-provider-correlation",
                code="ACCEPTED_PENDING",
                observation_id="rb01-accepted-observation",
                event_id="rb01-accepted-event",
            )
        )
        provider_returned = threading.Event()
        release_first = threading.Event()
        first_errors = []

        def seam(*, phase, attempt_id, generation):
            if phase == "after_provider_call" and generation == 1:
                provider_returned.set()
                if not release_first.wait(10):
                    raise TimeoutError("test did not release delayed worker")

        def first_worker():
            try:
                orchestrate_attempt(str(attempt.pk))
            except Exception as exc:
                first_errors.append(exc)

        with mock.patch.object(ProviderRuntime, "worker_test_seam", side_effect=seam):
            thread = threading.Thread(target=first_worker)
            thread.start()
            self.assertTrue(provider_returned.wait(10))
            PaymentAttempt.objects.filter(pk=attempt.pk).update(
                claim_expires_at=timezone.now() - timedelta(seconds=1)
            )
            # The durable submit-admission marker forces recovery to poll rather
            # than open a second submit admission after takeover.
            second = ProviderRuntime.submit_or_poll(str(attempt.pk))
            release_first.set()
            thread.join(10)

        self.assertFalse(first_errors)
        attempt.refresh_from_db()
        intent = attempt.execution_intent
        self.assertEqual(intent.submit_admission_generation, 1)
        self.assertIsNotNone(intent.submit_started_at)
        self.assertNotEqual(attempt.claim_generation, 1)
        self.assertNotEqual(second.code, "STALE_CLAIM")
