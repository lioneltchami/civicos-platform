"""Durable Scheduler recipient-delivery state transitions.

This module owns only database state and never performs transport or broker I/O.
Workers claim rows here, perform I/O after the transaction ends, then return here
with the same lease token to record a bounded outcome.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.appointments.models import SchedulerOutbox, SchedulerRecipientDelivery


TERMINAL = frozenset(
    {
        SchedulerRecipientDelivery.DELIVERED,
        SchedulerRecipientDelivery.ACKNOWLEDGED,
        SchedulerRecipientDelivery.CANCELLED,
        SchedulerRecipientDelivery.DEAD_LETTER,
    }
)


def recipient_idempotency_key(*, schedule_id, generation: int, recipient_kind: str, recipient_ref: str) -> str:
    """Return a non-PII deterministic key without persisting raw destination data."""
    canonical = f"{schedule_id}:{generation}:{recipient_kind}:{recipient_ref}".encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def materialize(
    *,
    schedule,
    owner_key: str,
    correlation_id: str,
    recipient_kind: str,
    recipient_ref: str,
    payload: dict,
    generation: int = 1,
    max_attempts: int = 3,
):
    """Create one recipient work row and its outbox event in one transaction."""
    now = timezone.now()
    key = recipient_idempotency_key(
        schedule_id=schedule.pk,
        generation=generation,
        recipient_kind=recipient_kind,
        recipient_ref=recipient_ref,
    )
    with transaction.atomic():
        delivery, created = SchedulerRecipientDelivery.objects.get_or_create(
            schedule=schedule,
            dispatch_generation=generation,
            recipient_kind=recipient_kind,
            recipient_ref=recipient_ref,
            defaults={
                "idempotency_key": key,
                "owner_key": owner_key[:180],
                "correlation_id": correlation_id[:180],
                "payload": dict(payload),
                "max_attempts": max_attempts,
                "next_attempt_at": now,
            },
        )
        if created:
            SchedulerOutbox.objects.create(delivery=delivery, available_at=now)
        return delivery, created


def claim(*, idempotency_key: str, lease_seconds: int = 300, now=None) -> str | None:
    """Conditionally claim a due row. A later worker cannot overwrite this lease."""
    now = now or timezone.now()
    token = uuid.uuid4().hex
    with transaction.atomic():
        row = SchedulerRecipientDelivery.objects.select_for_update().get(idempotency_key=idempotency_key)
        if row.status in TERMINAL:
            return None
        if row.next_attempt_at and row.next_attempt_at > now:
            return None
        if row.lease_expires_at and row.lease_expires_at > now:
            return None
        row.status = SchedulerRecipientDelivery.IN_FLIGHT
        row.attempts += 1
        row.lease_token = token
        row.lease_expires_at = now + timedelta(seconds=lease_seconds)
        row.save(update_fields=["status", "attempts", "lease_token", "lease_expires_at", "updated_at"])
        return token


def _with_valid_lease(*, idempotency_key: str, lease_token: str):
    row = SchedulerRecipientDelivery.objects.select_for_update().get(idempotency_key=idempotency_key)
    if row.status != SchedulerRecipientDelivery.IN_FLIGHT or row.lease_token != lease_token:
        return None
    return row


def succeed(*, idempotency_key: str, lease_token: str) -> bool:
    with transaction.atomic():
        row = _with_valid_lease(idempotency_key=idempotency_key, lease_token=lease_token)
        if row is None:
            return False
        row.status = SchedulerRecipientDelivery.DELIVERED
        row.lease_token = None
        row.lease_expires_at = None
        row.last_error = ""
        row.save(update_fields=["status", "lease_token", "lease_expires_at", "last_error", "updated_at"])
        return True


def fail(*, idempotency_key: str, lease_token: str, error_class: str, now=None) -> bool:
    """Persist a bounded, non-PII retry or terminal dead-letter outcome."""
    now = now or timezone.now()
    with transaction.atomic():
        row = _with_valid_lease(idempotency_key=idempotency_key, lease_token=lease_token)
        if row is None:
            return False
        row.lease_token = None
        row.lease_expires_at = None
        row.last_error = error_class[:240]
        if row.attempts >= row.max_attempts:
            row.status = SchedulerRecipientDelivery.DEAD_LETTER
            row.dead_lettered_at = now
            row.next_attempt_at = None
        else:
            row.status = SchedulerRecipientDelivery.RETRY
            row.next_attempt_at = now + timedelta(seconds=min(3600, 2 ** min(row.attempts, 10)))
        row.save(update_fields=["status", "lease_token", "lease_expires_at", "last_error", "dead_lettered_at", "next_attempt_at", "updated_at"])
        return True


def cancel_schedule(*, schedule_id) -> int:
    """Fence all non-terminal work before a delete or superseding re-arm."""
    now = timezone.now()
    with transaction.atomic():
        rows = SchedulerRecipientDelivery.objects.select_for_update().filter(schedule_id=schedule_id).exclude(status__in=TERMINAL)
        return rows.update(
            status=SchedulerRecipientDelivery.CANCELLED,
            cancelled_at=now,
            lease_token=None,
            lease_expires_at=None,
            updated_at=now,
        )


def acknowledge(*, idempotency_key: str) -> bool:
    with transaction.atomic():
        row = SchedulerRecipientDelivery.objects.select_for_update().get(idempotency_key=idempotency_key)
        if row.status != SchedulerRecipientDelivery.DELIVERED:
            return False
        row.status = SchedulerRecipientDelivery.ACKNOWLEDGED
        row.acknowledged_at = timezone.now()
        row.save(update_fields=["status", "acknowledged_at", "updated_at"])
        return True


def replay(*, idempotency_key: str) -> bool:
    with transaction.atomic():
        row = SchedulerRecipientDelivery.objects.select_for_update().get(idempotency_key=idempotency_key)
        if row.status != SchedulerRecipientDelivery.DEAD_LETTER:
            return False
        row.status = SchedulerRecipientDelivery.RETRY
        row.next_attempt_at = timezone.now()
        row.last_error = ""
        row.dead_lettered_at = None
        row.save(update_fields=["status", "next_attempt_at", "last_error", "dead_lettered_at", "updated_at"])
        return True


def reap_expired(*, now=None) -> int:
    now = now or timezone.now()
    count = 0
    with transaction.atomic():
        rows = SchedulerRecipientDelivery.objects.select_for_update().filter(
            status=SchedulerRecipientDelivery.IN_FLIGHT,
            lease_expires_at__lt=now,
        )
        for row in rows:
            row.lease_token = None
            row.lease_expires_at = None
            if row.attempts >= row.max_attempts:
                row.status = SchedulerRecipientDelivery.DEAD_LETTER
                row.dead_lettered_at = now
                row.next_attempt_at = None
            else:
                row.status = SchedulerRecipientDelivery.RETRY
                row.next_attempt_at = now
            row.save(update_fields=["status", "lease_token", "lease_expires_at", "dead_lettered_at", "next_attempt_at", "updated_at"])
            count += 1
    return count


def claim_outbox(*, lease_seconds: int = 60, owner: str = "scheduler-publisher", now=None):
    """Claim one due outbox row in a short transaction; never performs broker I/O."""
    now = now or timezone.now()
    token = uuid.uuid4().hex
    with transaction.atomic():
        row = (SchedulerOutbox.objects.select_for_update()
               .filter(published_at__isnull=True, available_at__lte=now)
               .filter(Q(publisher_lease_expires_at__isnull=True) | Q(publisher_lease_expires_at__lt=now))
               .order_by("id").first())
        if row is None:
            return None
        row.publisher_generation += 1
        row.publisher_token = token
        row.publisher_owner = owner[:120]
        row.publisher_lease_expires_at = now + timedelta(seconds=lease_seconds)
        row.publish_attempts += 1
        row.save(update_fields=["publisher_generation", "publisher_token", "publisher_owner", "publisher_lease_expires_at", "publish_attempts", "updated_at"])
        return row.pk, row.delivery.idempotency_key, token, row.publisher_generation


def mark_outbox_published(*, outbox_id, token: str, generation: int, now=None) -> bool:
    now = now or timezone.now()
    with transaction.atomic():
        updated = SchedulerOutbox.objects.filter(pk=outbox_id, published_at__isnull=True,
            publisher_token=token, publisher_generation=generation).update(
                published_at=now, publisher_token=None, publisher_owner="", publisher_lease_expires_at=None,
                last_error="", updated_at=now)
        return bool(updated)


def mark_outbox_failed(*, outbox_id, token: str, generation: int, error_class: str, now=None) -> bool:
    now = now or timezone.now()
    with transaction.atomic():
        updated = SchedulerOutbox.objects.filter(pk=outbox_id, published_at__isnull=True,
            publisher_token=token, publisher_generation=generation).update(
                publisher_token=None, publisher_owner="", publisher_lease_expires_at=None,
                available_at=now, last_error=error_class[:240], updated_at=now)
        return bool(updated)


def due_outbox(*, now=None):
    now = now or timezone.now()
    return SchedulerOutbox.objects.filter(published_at__isnull=True, available_at__lte=now).select_related("delivery")
