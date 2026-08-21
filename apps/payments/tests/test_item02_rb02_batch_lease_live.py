"""Live-path RB-02.1 lease and basic-fencing tests.

These tests deliberately exercise the real ``process_bulk_payment_batch`` task
and the durable ``BatchLease`` row.  They do not test RB-02.2 provider-finality,
policy, decision, or broader competing-worker behavior.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from django.test import TransactionTestCase
from django.utils import timezone

from apps.payments.govstack_batch_lease import (
    BatchLeaseHandle,
    BatchLeaseOwnershipLost,
    BatchLeaseService,
)
from apps.payments.govstack_models import (
    BatchLease,
    BulkPaymentBatch,
    CallbackDelivery,
    CreditInstruction,
    GovStackBeneficiary,
    GovStackPaymentAuditEntry,
    PaymentAttempt,
)
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

    def _run_live_task(self, batch: BulkPaymentBatch, *, task_id: str | None = None) -> None:
        process_bulk_payment_batch.apply(args=[str(batch.pk)], task_id=task_id)

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
