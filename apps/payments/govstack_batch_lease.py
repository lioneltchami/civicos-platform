"""Durable owner-token/generation fencing for the live GovStack bulk worker.

The service is intentionally narrow.  It owns the existing ``BatchLease`` row
and provides only compare-and-set lease operations; it does not make payment
finality, policy, or batch-decision choices.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from apps.payments.govstack_models import BatchLease, BulkPaymentBatch


DEFAULT_BATCH_LEASE_TTL = timedelta(minutes=5)


class BatchLeaseOwnershipLost(RuntimeError):
    """Raised when a caller no longer owns the current unexpired lease."""


@dataclass(frozen=True)
class BatchLeaseHandle:
    """Opaque durable ownership context carried by one bulk-worker invocation."""

    batch_id: str
    owner_token: str
    generation: int


class BatchLeaseService:
    """Atomic lifecycle for the existing one-row-per-batch lease record."""

    @staticmethod
    def _now(value: datetime | None) -> datetime:
        return value if value is not None else timezone.now()

    @classmethod
    def acquire(
        cls,
        *,
        batch: BulkPaymentBatch,
        owner_token: str,
        now: datetime | None = None,
        ttl: timedelta = DEFAULT_BATCH_LEASE_TTL,
    ) -> BatchLeaseHandle | None:
        """Acquire, replay, or take over a durable lease.

        A live lease held by another token returns ``None``.  An expired lease
        is taken over by a new owner and advances generation exactly once.
        Re-acquiring a live lease with the same token is a replay and preserves
        both expiry and generation; callers use ``heartbeat`` to extend it.
        """
        if not owner_token:
            raise ValueError("owner_token is required")
        current_time = cls._now(now)
        with transaction.atomic():
            # This lock serializes creation as well as expiry takeover on
            # databases where a missing one-to-one row cannot itself be locked.
            locked_batch = BulkPaymentBatch.objects.select_for_update().get(pk=batch.pk)
            lease, created = BatchLease.objects.select_for_update().get_or_create(
                batch=locked_batch,
                defaults={
                    "owner_token": owner_token,
                    "generation": 1,
                    "expires_at": current_time + ttl,
                },
            )
            if created:
                return BatchLeaseHandle(
                    batch_id=str(locked_batch.pk),
                    owner_token=owner_token,
                    generation=lease.generation,
                )

            if lease.owner_token == owner_token and lease.expires_at > current_time:
                return BatchLeaseHandle(
                    batch_id=str(locked_batch.pk),
                    owner_token=owner_token,
                    generation=lease.generation,
                )

            if lease.expires_at <= current_time:
                lease.owner_token = owner_token
                lease.generation += 1
                lease.expires_at = current_time + ttl
                lease.save(
                    update_fields=["owner_token", "generation", "expires_at", "updated_at"]
                )
                return BatchLeaseHandle(
                    batch_id=str(locked_batch.pk),
                    owner_token=owner_token,
                    generation=lease.generation,
                )

            return None

    @classmethod
    def _current_locked(
        cls,
        handle: BatchLeaseHandle,
        *,
        now: datetime | None = None,
    ) -> BatchLease:
        current_time = cls._now(now)
        try:
            lease = BatchLease.objects.select_for_update().get(batch_id=handle.batch_id)
        except BatchLease.DoesNotExist as exc:
            raise BatchLeaseOwnershipLost("batch lease no longer exists") from exc
        if (
            lease.owner_token != handle.owner_token
            or lease.generation != handle.generation
            or lease.expires_at <= current_time
        ):
            raise BatchLeaseOwnershipLost("batch lease owner, generation, or expiry is stale")
        return lease

    @classmethod
    def assert_current_owner(
        cls,
        handle: BatchLeaseHandle,
        *,
        now: datetime | None = None,
    ) -> BatchLease:
        """Verify current ownership immediately before a durable side effect."""
        with transaction.atomic():
            return cls._current_locked(handle, now=now)

    @classmethod
    def heartbeat(
        cls,
        handle: BatchLeaseHandle,
        *,
        now: datetime | None = None,
        ttl: timedelta = DEFAULT_BATCH_LEASE_TTL,
    ) -> BatchLeaseHandle:
        """Extend only the still-current owner's lease without changing generation."""
        current_time = cls._now(now)
        with transaction.atomic():
            lease = cls._current_locked(handle, now=current_time)
            lease.expires_at = current_time + ttl
            lease.save(update_fields=["expires_at", "updated_at"])
        return handle

    @classmethod
    def release(
        cls,
        handle: BatchLeaseHandle,
        *,
        now: datetime | None = None,
    ) -> None:
        """Expire the lease only if the same owner/generation still holds it."""
        current_time = cls._now(now)
        with transaction.atomic():
            lease = cls._current_locked(handle, now=current_time)
            lease.expires_at = current_time
            lease.save(update_fields=["expires_at", "updated_at"])
