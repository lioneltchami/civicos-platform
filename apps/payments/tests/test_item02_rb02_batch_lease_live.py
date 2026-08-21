"""Live-path RB-02.1 lease and basic-fencing tests.

These tests deliberately exercise the real ``process_bulk_payment_batch`` task,
the durable ``BatchLease`` row, and the authoritative RB-02.2 child-finality
projection. They do not test policy, durable decisions, or broader
competing-worker behavior.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import threading

from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.payments.govstack_batch_lease import (
    BatchLeaseHandle,
    BatchLeaseOwnershipLost,
    BatchLeaseService,
)
from apps.payments.govstack_failure_services import PaymentLifecycleService
from apps.payments.govstack_models import (
    BatchLease,
    BulkPaymentBatch,
    CallbackDelivery,
    CreditInstruction,
    GovStackBatchDecision,
    GovStackBeneficiary,
    GovStackPaymentAuditEntry,
    PaymentAttempt,
    PaymentOutcome,
)
from apps.payments import govstack_tasks
from apps.payments.govstack_tasks import process_bulk_payment_batch


class RB021BatchLeaseLiveTests(TransactionTestCase):
    """RB-02.1's exact three required durable live-worker tests."""

    def _make_batch(self, *, suffix: str, callback_url: str = "") -> BulkPaymentBatch:
        batch = BulkPaymentBatch.objects.create(
            request_id=f"LeaseReq{suffix}",
            source_bb_id="LeaseSourceBB",
            batch_id=f"LeaseBatch{suffix}",
            status=BulkPaymentBatch.STATUS_RECEIVED,
            callback_url=callback_url,
            total_amount=Decimal("100.00"),
        )
        CreditInstruction.objects.create(
            batch=batch,
            instruction_id=f"LeaseInstruction{suffix}",
            payee_functional_id=f"LeasePayee{suffix}",
            amount=Decimal("100.00"),
            currency="USD",
            narration="RB-02.1 lease test",
            status=CreditInstruction.STATUS_PENDING,
        )
        GovStackBeneficiary.objects.create(
            payee_functional_id=f"LeasePayee{suffix}",
            source_bb_id="LeaseSourceBB",
            is_active=True,
        )
        return batch

    def _make_empty_batch(self, *, suffix: str, callback_url: str = "") -> BulkPaymentBatch:
        return BulkPaymentBatch.objects.create(
            request_id=f"EmptyReq{suffix}",
            source_bb_id="LeaseSourceBB",
            batch_id=f"EmptyBatch{suffix}",
            status=BulkPaymentBatch.STATUS_RECEIVED,
            callback_url=callback_url,
            total_amount=Decimal("0.00"),
        )

    def _set_nonfinal_status(self, attempt: PaymentAttempt, status: str) -> None:
        PaymentLifecycleService.apply_outcome(
            attempt,
            PaymentOutcome(
                status,
                code=f"RB023_{status.upper()}",
                category="rb02_3_test",
                retryable=status == "retryable",
            ),
        )

    def _run_live_task(self, batch: BulkPaymentBatch, *, task_id: str | None = None) -> None:
        process_bulk_payment_batch.apply(args=[str(batch.pk)], task_id=task_id)

    def _bind_final_attempt(
        self,
        *,
        batch: BulkPaymentBatch,
        instruction: CreditInstruction,
        outcome: str,
        observation_id: str,
    ) -> PaymentAttempt:
        attempt, _ = PaymentLifecycleService.get_or_create_attempt(
            tenant_id="",
            operation="g2p_bulk_instruction",
            request_id=f"{batch.request_id}:{instruction.instruction_id}",
            payload={
                "batch_pk": str(batch.pk),
                "instruction_pk": str(instruction.pk),
                "amount": str(instruction.amount),
                "currency": instruction.currency,
            },
            amount=instruction.amount,
            currency=instruction.currency,
            correlation_id=batch.correlation_id,
            source_bb_id=batch.source_bb_id,
        )
        PaymentLifecycleService.bind_credit_instruction_attempt(instruction, attempt)
        PaymentLifecycleService.record_observation(
            attempt,
            tenant_id="",
            observation_kind="provider",
            observation_id=observation_id,
            outcome=outcome,
            amount=instruction.amount,
            currency=instruction.currency,
            verified=True,
            verification_method="rb02_2_test",
            binding_hash=PaymentLifecycleService.binding_hash(
                "", attempt, instruction.amount, instruction.currency
            ),
        )
        attempt.refresh_from_db()
        PaymentLifecycleService.reconcile(attempt, provider_status=outcome)
        return attempt

    def _bind_nonfinal_attempt(
        self,
        *,
        batch: BulkPaymentBatch,
        instruction: CreditInstruction,
    ) -> PaymentAttempt:
        attempt, _ = PaymentLifecycleService.get_or_create_attempt(
            tenant_id="",
            operation="g2p_bulk_instruction",
            request_id=f"{batch.request_id}:{instruction.instruction_id}",
            payload={
                "batch_pk": str(batch.pk),
                "instruction_pk": str(instruction.pk),
                "amount": str(instruction.amount),
                "currency": instruction.currency,
            },
            amount=instruction.amount,
            currency=instruction.currency,
            correlation_id=batch.correlation_id,
            source_bb_id=batch.source_bb_id,
        )
        PaymentLifecycleService.bind_credit_instruction_attempt(instruction, attempt)
        return attempt

    def test_fresh_acquire_and_heartbeat_is_fenced(self):
        batch = self._make_batch(suffix="fresh")
        now = timezone.now()
        handle = BatchLeaseService.acquire(
            batch=batch,
            owner_token="worker-a",
            now=now,
            ttl=timedelta(minutes=2),
        )
        self.assertIsNotNone(handle)
        assert handle is not None
        before = BatchLease.objects.get(batch=batch).expires_at

        BatchLeaseService.heartbeat(
            handle,
            now=now + timedelta(seconds=30),
            ttl=timedelta(minutes=2),
        )
        lease = BatchLease.objects.get(batch=batch)
        self.assertEqual(lease.owner_token, "worker-a")
        self.assertEqual(lease.generation, handle.generation)
        self.assertGreater(lease.expires_at, before)

        stale = BatchLeaseHandle(
            batch_id=str(batch.pk),
            owner_token="worker-a",
            generation=handle.generation + 1,
        )
        with self.assertRaises(BatchLeaseOwnershipLost):
            BatchLeaseService.heartbeat(stale, now=now + timedelta(seconds=31))

        BatchLeaseService.release(handle)
        self._run_live_task(batch)
        batch.refresh_from_db()
        self.assertNotEqual(batch.status, BulkPaymentBatch.STATUS_RECEIVED)

    def test_expiry_takeover_advances_generation_once(self):
        batch = self._make_batch(suffix="takeover")
        now = timezone.now()
        first = BatchLeaseService.acquire(
            batch=batch,
            owner_token="worker-a",
            now=now,
            ttl=timedelta(seconds=1),
        )
        self.assertIsNotNone(first)
        assert first is not None
        second = BatchLeaseService.acquire(
            batch=batch,
            owner_token="worker-b",
            now=now + timedelta(seconds=2),
            ttl=timedelta(minutes=2),
        )
        self.assertIsNotNone(second)
        assert second is not None
        self.assertEqual(second.generation, first.generation + 1)

        replay = BatchLeaseService.acquire(
            batch=batch,
            owner_token="worker-b",
            now=now + timedelta(seconds=3),
        )
        self.assertEqual(replay, second)
        lease = BatchLease.objects.get(batch=batch)
        self.assertEqual(lease.generation, second.generation)

        BatchLeaseService.release(second, now=timezone.now())
        self._run_live_task(batch)
        batch.refresh_from_db()
        self.assertNotEqual(batch.status, BulkPaymentBatch.STATUS_RECEIVED)

    def test_stale_owner_cannot_write_any_side_effect(self):
        batch = self._make_batch(suffix="stale", callback_url="https://example.com/callback")
        now = timezone.now()
        stale_handle = BatchLeaseService.acquire(
            batch=batch,
            owner_token="worker-a",
            now=now,
            ttl=timedelta(seconds=1),
        )
        self.assertIsNotNone(stale_handle)
        assert stale_handle is not None
        takeover = BatchLeaseService.acquire(
            batch=batch,
            owner_token="worker-b",
            now=now + timedelta(seconds=2),
            ttl=timedelta(minutes=5),
        )
        self.assertIsNotNone(takeover)
        with self.assertRaises(BatchLeaseOwnershipLost):
            BatchLeaseService.assert_current_owner(stale_handle, now=now + timedelta(seconds=2))

        # Worker-a now enters the real bulk task after worker-b has committed the
        # expiry takeover. The task must exit before any existing durable write.
        self._run_live_task(batch, task_id="worker-a")

        batch.refresh_from_db()
        instruction = CreditInstruction.objects.get(batch=batch)
        lease = BatchLease.objects.get(batch=batch)
        self.assertEqual(batch.status, BulkPaymentBatch.STATUS_RECEIVED)
        self.assertEqual(instruction.status, CreditInstruction.STATUS_PENDING)
        self.assertEqual(lease.owner_token, "worker-b")
        self.assertEqual(lease.generation, 2)
        self.assertFalse(
            PaymentAttempt.objects.filter(
                operation="g2p_bulk_instruction",
                request_id=f"{batch.request_id}:{instruction.instruction_id}",
            ).exists()
        )
        self.assertFalse(GovStackPaymentAuditEntry.objects.filter(object_pk=str(batch.pk)).exists())
        self.assertFalse(CallbackDelivery.objects.exists())

    def test_authoritative_mixed_child_finality_is_nonterminal(self):
        batch = self._make_batch(suffix="mixed")
        settled_instruction = CreditInstruction.objects.get(batch=batch)
        unsettled_instruction = CreditInstruction.objects.create(
            batch=batch,
            instruction_id="LeaseInstructionMixedTwo",
            payee_functional_id="LeasePayeeMixedTwo",
            amount=Decimal("50.00"),
            currency="USD",
            narration="RB-02.2 non-final child",
            status=CreditInstruction.STATUS_PENDING,
        )
        batch.total_amount = Decimal("150.00")
        batch.save(update_fields=["total_amount"])
        self._bind_final_attempt(
            batch=batch,
            instruction=settled_instruction,
            outcome="settled",
            observation_id="rb022-mixed-settled",
        )
        nonfinal_attempt = self._bind_nonfinal_attempt(
            batch=batch,
            instruction=unsettled_instruction,
        )

        self._run_live_task(batch)

        batch.refresh_from_db()
        settled_instruction.refresh_from_db()
        unsettled_instruction.refresh_from_db()
        nonfinal_attempt.refresh_from_db()
        self.assertEqual(batch.status, BulkPaymentBatch.STATUS_PROCESSING)
        self.assertEqual(batch.completed_amount, Decimal("100.00"))
        self.assertEqual(batch.failed_amount, Decimal("0.00"))
        self.assertIsNone(batch.result_generated_at)
        self.assertEqual(settled_instruction.status, CreditInstruction.STATUS_COMPLETED)
        self.assertEqual(unsettled_instruction.status, CreditInstruction.STATUS_VALIDATED)
        self.assertEqual(nonfinal_attempt.status, PaymentAttempt.STATUS_PENDING)

    def test_already_terminal_children_are_aggregated_not_reprocessed(self):
        batch = self._make_batch(suffix="terminal")
        instruction = CreditInstruction.objects.get(batch=batch)
        attempt = self._bind_final_attempt(
            batch=batch,
            instruction=instruction,
            outcome="settled",
            observation_id="rb022-terminal-settled",
        )
        instruction.status = CreditInstruction.STATUS_COMPLETED
        instruction.save(update_fields=["status"])
        attempt.refresh_from_db()
        attempt_count_before = attempt.attempt_count

        self._run_live_task(batch)

        batch.refresh_from_db()
        instruction.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(batch.status, BulkPaymentBatch.STATUS_COMPLETED)
        self.assertEqual(batch.completed_amount, instruction.amount)
        self.assertEqual(instruction.payment_attempt_id, attempt.pk)
        self.assertEqual(instruction.status, CreditInstruction.STATUS_COMPLETED)
        self.assertEqual(attempt.attempt_count, attempt_count_before)

    def test_threshold_pause_persists_one_idempotent_decision(self):
        batch = self._make_batch(suffix="pause")
        settled_instruction = CreditInstruction.objects.get(batch=batch)
        paused_instruction = CreditInstruction.objects.create(
            batch=batch,
            instruction_id="LeaseInstructionPauseTwo",
            payee_functional_id="LeasePayeePauseTwo",
            amount=Decimal("50.00"),
            currency="USD",
            narration="RB-02.3 threshold pause",
            status=CreditInstruction.STATUS_PENDING,
        )
        batch.total_amount = Decimal("150.00")
        batch.save(update_fields=["total_amount"])
        self._bind_final_attempt(
            batch=batch,
            instruction=settled_instruction,
            outcome="settled",
            observation_id="rb023-pause-settled",
        )
        paused_attempt = self._bind_nonfinal_attempt(
            batch=batch,
            instruction=paused_instruction,
        )
        self._set_nonfinal_status(paused_attempt, "uncertain")

        with override_settings(GOVSTACK_BULK_FAILURE_THRESHOLD=0.4):
            self._run_live_task(batch)
            self._run_live_task(batch)

        batch.refresh_from_db()
        decisions = GovStackBatchDecision.objects.filter(batch=batch)
        self.assertEqual(decisions.count(), 1)
        decision = decisions.get()
        self.assertEqual(decision.policy_state, "paused")
        self.assertEqual(decision.outcome_action, GovStackBatchDecision.ACTION_PAUSE)
        self.assertEqual(decision.non_final_count, 1)
        self.assertEqual(batch.status, BulkPaymentBatch.STATUS_PROCESSING)
        self.assertEqual(
            GovStackPaymentAuditEntry.objects.filter(
                object_type="batch_decision", object_pk=str(decision.pk)
            ).count(),
            1,
        )

    def test_retry_decision_is_durable_and_idempotent(self):
        batch = self._make_batch(suffix="retry")
        settled_instruction = CreditInstruction.objects.get(batch=batch)
        retry_instruction = CreditInstruction.objects.create(
            batch=batch,
            instruction_id="LeaseInstructionRetryTwo",
            payee_functional_id="LeasePayeeRetryTwo",
            amount=Decimal("50.00"),
            currency="USD",
            narration="RB-02.3 retry",
            status=CreditInstruction.STATUS_PENDING,
        )
        batch.total_amount = Decimal("150.00")
        batch.save(update_fields=["total_amount"])
        self._bind_final_attempt(
            batch=batch,
            instruction=settled_instruction,
            outcome="settled",
            observation_id="rb023-retry-settled",
        )
        retry_attempt = self._bind_nonfinal_attempt(batch=batch, instruction=retry_instruction)
        self._set_nonfinal_status(retry_attempt, "retryable")

        with override_settings(GOVSTACK_BULK_FAILURE_THRESHOLD=1.0):
            self._run_live_task(batch)
            self._run_live_task(batch)

        batch.refresh_from_db()
        decision = GovStackBatchDecision.objects.get(batch=batch)
        self.assertEqual(decision.policy_state, "retryable")
        self.assertEqual(decision.outcome_action, GovStackBatchDecision.ACTION_RETRY)
        self.assertEqual(decision.non_final_count, 1)
        self.assertEqual(batch.status, BulkPaymentBatch.STATUS_PROCESSING)
        self.assertEqual(GovStackBatchDecision.objects.filter(batch=batch).count(), 1)

    def test_review_decision_is_durable_and_idempotent(self):
        batch = self._make_batch(suffix="review")
        settled_instruction = CreditInstruction.objects.get(batch=batch)
        review_instruction = CreditInstruction.objects.create(
            batch=batch,
            instruction_id="LeaseInstructionReviewTwo",
            payee_functional_id="LeasePayeeReviewTwo",
            amount=Decimal("50.00"),
            currency="USD",
            narration="RB-02.3 review",
            status=CreditInstruction.STATUS_PENDING,
        )
        batch.total_amount = Decimal("150.00")
        batch.save(update_fields=["total_amount"])
        self._bind_final_attempt(
            batch=batch,
            instruction=settled_instruction,
            outcome="settled",
            observation_id="rb023-review-settled",
        )
        review_attempt = self._bind_nonfinal_attempt(batch=batch, instruction=review_instruction)
        self._set_nonfinal_status(review_attempt, "review")

        with override_settings(GOVSTACK_BULK_FAILURE_THRESHOLD=1.0):
            self._run_live_task(batch)
            self._run_live_task(batch)

        batch.refresh_from_db()
        decision = GovStackBatchDecision.objects.get(batch=batch)
        self.assertEqual(decision.policy_state, "review")
        self.assertEqual(decision.outcome_action, GovStackBatchDecision.ACTION_REVIEW)
        self.assertEqual(batch.status, BulkPaymentBatch.STATUS_PROCESSING)
        self.assertEqual(GovStackBatchDecision.objects.filter(batch=batch).count(), 1)

    @override_settings(GOVSTACK_BULK_RETURN_FUNDS_ENABLED=True)
    def test_configured_return_funds_is_explicit_and_idempotent(self):
        batch = self._make_batch(suffix="return")
        instruction = CreditInstruction.objects.get(batch=batch)
        self._bind_final_attempt(
            batch=batch,
            instruction=instruction,
            outcome="rejected",
            observation_id="rb023-return-rejected",
        )

        self._run_live_task(batch)
        self._run_live_task(batch)

        batch.refresh_from_db()
        decision = GovStackBatchDecision.objects.get(batch=batch)
        self.assertEqual(decision.policy_state, "partial")
        self.assertEqual(decision.outcome_action, GovStackBatchDecision.ACTION_RETURN_FUNDS)
        self.assertEqual(batch.status, BulkPaymentBatch.STATUS_FAILED)
        self.assertEqual(GovStackBatchDecision.objects.filter(batch=batch).count(), 1)
        self.assertFalse(PaymentAttempt.objects.filter(operation="g2p_return_funds").exists())

    def test_empty_batch_has_one_durable_outcome(self):
        batch = self._make_empty_batch(suffix="empty")

        self._run_live_task(batch)
        self._run_live_task(batch)

        batch.refresh_from_db()
        decision = GovStackBatchDecision.objects.get(batch=batch)
        self.assertEqual(decision.outcome_action, GovStackBatchDecision.ACTION_EMPTY)
        self.assertEqual(decision.total_count, 0)
        self.assertEqual(decision.non_final_count, 0)
        self.assertEqual(batch.status, BulkPaymentBatch.STATUS_PROCESSING)
        self.assertEqual(GovStackBatchDecision.objects.filter(batch=batch).count(), 1)
        self.assertEqual(
            GovStackPaymentAuditEntry.objects.filter(
                object_type="batch_decision", object_pk=str(decision.pk)
            ).count(),
            1,
        )

    def test_duplicate_finalization_creates_one_decision_audit_and_callback(self):
        batch = self._make_batch(
            suffix="duplicate",
            callback_url="https://example.com/callback",
        )
        instruction = CreditInstruction.objects.get(batch=batch)
        self._bind_final_attempt(
            batch=batch,
            instruction=instruction,
            outcome="settled",
            observation_id="rb023-duplicate-settled",
        )

        self._run_live_task(batch)
        self._run_live_task(batch)

        decision = GovStackBatchDecision.objects.get(batch=batch)
        self.assertEqual(GovStackBatchDecision.objects.filter(batch=batch).count(), 1)
        self.assertEqual(
            GovStackPaymentAuditEntry.objects.filter(
                object_type="batch_decision", object_pk=str(decision.pk)
            ).count(),
            1,
        )
        callback_attempt = PaymentAttempt.objects.get(
            operation="g2p_bulk_callback",
            request_id=f"callback:{batch.request_id}:{batch.batch_id}",
        )
        self.assertEqual(CallbackDelivery.objects.filter(attempt=callback_attempt).count(), 1)

    def test_two_live_workers_cross_expiry_and_stale_finalization(self):
        batch = self._make_batch(
            suffix="cross_expiry",
            callback_url="https://example.com/callback",
        )
        instruction = CreditInstruction.objects.get(batch=batch)
        self._bind_final_attempt(
            batch=batch,
            instruction=instruction,
            outcome="settled",
            observation_id="rb024-cross-expiry-settled",
        )

        worker_a_entered = threading.Event()
        release_worker_a = threading.Event()
        worker_a_handles: list[BatchLeaseHandle] = []
        worker_a_errors: list[BaseException] = []

        def pause_after_committed_admission(handle: BatchLeaseHandle) -> None:
            if handle.owner_token != "worker-a-live":
                return
            worker_a_handles.append(handle)
            worker_a_entered.set()
            self.assertTrue(
                release_worker_a.wait(timeout=10),
                "Worker A was not released after Worker B completed takeover",
            )

        def run_worker_a() -> None:
            close_old_connections()
            try:
                self._run_live_task(batch, task_id="worker-a-live")
            except BaseException as exc:  # surface thread failures to the test
                worker_a_errors.append(exc)
            finally:
                close_old_connections()

        original_hook = govstack_tasks._PROCESS_BULK_PAYMENT_BATCH_TEST_HOOK
        govstack_tasks._PROCESS_BULK_PAYMENT_BATCH_TEST_HOOK = pause_after_committed_admission
        worker_a = threading.Thread(target=run_worker_a, name="rb024-worker-a")
        try:
            worker_a.start()
            self.assertTrue(
                worker_a_entered.wait(timeout=10),
                "Worker A did not reach the committed-admission pause point",
            )
            self.assertEqual(len(worker_a_handles), 1)
            handle_a = worker_a_handles[0]
            self.assertEqual(handle_a.owner_token, "worker-a-live")
            self.assertEqual(handle_a.generation, 1)

            # The admission transaction has committed, so expiry is a durable
            # condition visible to Worker B's independent real task invocation.
            BatchLease.objects.filter(batch=batch, generation=handle_a.generation).update(
                expires_at=timezone.now() - timedelta(seconds=1)
            )
            self._run_live_task(batch, task_id="worker-b-live")

            batch.refresh_from_db()
            instruction.refresh_from_db()
            lease_after_b = BatchLease.objects.get(batch=batch)
            decision_after_b = GovStackBatchDecision.objects.get(batch=batch)
            callback_attempt_after_b = PaymentAttempt.objects.get(
                operation="g2p_bulk_callback",
                request_id=f"callback:{batch.request_id}:{batch.batch_id}",
            )
            delivery_after_b = CallbackDelivery.objects.get(attempt=callback_attempt_after_b)
            audit_count_after_b = GovStackPaymentAuditEntry.objects.filter(
                object_type="batch_decision",
                object_pk=str(decision_after_b.pk),
            ).count()
            snapshot_after_b = {
                "lease_owner": lease_after_b.owner_token,
                "lease_generation": lease_after_b.generation,
                "batch_status": batch.status,
                "completed_amount": batch.completed_amount,
                "failed_amount": batch.failed_amount,
                "result_generated_at": batch.result_generated_at,
                "instruction_status": instruction.status,
                "instruction_failure_reason": instruction.failure_reason,
                "decision_count": GovStackBatchDecision.objects.filter(batch=batch).count(),
                "decision_id": decision_after_b.pk,
                "decision_owner": decision_after_b.lease_owner_token,
                "decision_generation": decision_after_b.lease_generation,
                "audit_count": audit_count_after_b,
                "callback_attempt_count": PaymentAttempt.objects.filter(
                    operation="g2p_bulk_callback",
                    request_id=f"callback:{batch.request_id}:{batch.batch_id}",
                ).count(),
                "callback_delivery_count": CallbackDelivery.objects.filter(
                    attempt=callback_attempt_after_b
                ).count(),
                "delivery_id": delivery_after_b.pk,
            }

            self.assertEqual(snapshot_after_b["lease_owner"], "worker-b-live")
            self.assertEqual(snapshot_after_b["lease_generation"], handle_a.generation + 1)
            self.assertEqual(snapshot_after_b["decision_count"], 1)
            self.assertEqual(snapshot_after_b["decision_owner"], "worker-b-live")
            self.assertEqual(
                snapshot_after_b["decision_generation"], snapshot_after_b["lease_generation"]
            )
            self.assertEqual(batch.status, BulkPaymentBatch.STATUS_COMPLETED)
            self.assertEqual(instruction.status, CreditInstruction.STATUS_COMPLETED)
            self.assertEqual(audit_count_after_b, 1)
            self.assertEqual(snapshot_after_b["callback_attempt_count"], 1)
            self.assertEqual(snapshot_after_b["callback_delivery_count"], 1)

            release_worker_a.set()
            worker_a.join(timeout=10)
            self.assertFalse(worker_a.is_alive(), "Worker A did not finish after release")
            self.assertEqual(worker_a_errors, [])

            batch.refresh_from_db()
            instruction.refresh_from_db()
            lease_after_a = BatchLease.objects.get(batch=batch)
            decision_after_a = GovStackBatchDecision.objects.get(batch=batch)
            callback_attempt_after_a = PaymentAttempt.objects.get(
                operation="g2p_bulk_callback",
                request_id=f"callback:{batch.request_id}:{batch.batch_id}",
            )
            self.assertEqual(
                {
                    "lease_owner": lease_after_a.owner_token,
                    "lease_generation": lease_after_a.generation,
                    "batch_status": batch.status,
                    "completed_amount": batch.completed_amount,
                    "failed_amount": batch.failed_amount,
                    "result_generated_at": batch.result_generated_at,
                    "instruction_status": instruction.status,
                    "instruction_failure_reason": instruction.failure_reason,
                    "decision_count": GovStackBatchDecision.objects.filter(batch=batch).count(),
                    "decision_id": decision_after_a.pk,
                    "decision_owner": decision_after_a.lease_owner_token,
                    "decision_generation": decision_after_a.lease_generation,
                    "audit_count": GovStackPaymentAuditEntry.objects.filter(
                        object_type="batch_decision",
                        object_pk=str(decision_after_a.pk),
                    ).count(),
                    "callback_attempt_count": PaymentAttempt.objects.filter(
                        operation="g2p_bulk_callback",
                        request_id=f"callback:{batch.request_id}:{batch.batch_id}",
                    ).count(),
                    "callback_delivery_count": CallbackDelivery.objects.filter(
                        attempt=callback_attempt_after_a
                    ).count(),
                    "delivery_id": CallbackDelivery.objects.get(
                        attempt=callback_attempt_after_a
                    ).pk,
                },
                snapshot_after_b,
            )
        finally:
            release_worker_a.set()
            worker_a.join(timeout=10)
            govstack_tasks._PROCESS_BULK_PAYMENT_BATCH_TEST_HOOK = original_hook
