"""Durable Scheduler recipient-delivery state transitions.

This module owns only database state and never performs transport or broker I/O.
Workers claim rows here, perform I/O after the transaction ends, then return here
with the same lease token to record a bounded outcome.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.appointments.models import GovStackAlertSchedule, SchedulerOutbox, SchedulerRecipientDelivery


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


def _materialize_locked(
    *,
    schedule: GovStackAlertSchedule,
    generation: int,
    recipient_kind: str,
    recipient_ref: str,
    owner_key: str,
    correlation_id: str,
    payload: dict,
    max_attempts: int,
):
    """Converge one row pair beneath an already-locked admission authority.

    This private helper neither establishes schedule authority nor accepts an
    external generation decision.  It is only called from
    :func:`admit_schedule_generation` after the schedule lock and generation
    comparison have succeeded.
    """
    now = timezone.now()
    key = recipient_idempotency_key(
        schedule_id=schedule.pk,
        generation=generation,
        recipient_kind=recipient_kind,
        recipient_ref=recipient_ref,
    )
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


def admit_schedule_generation(
    *,
    schedule_id,
    expected_generation: int,
    recipients: list[tuple[str, str]],
    owner_key: str | None = None,
    correlation_id: str | None = None,
    payload: dict | None = None,
    max_attempts: int = 3,
) -> dict:
    """Admit only the locked current schedule generation without external I/O."""
    payload = dict(payload or {})
    with transaction.atomic():
        schedule = GovStackAlertSchedule.objects.select_for_update().get(pk=schedule_id)
        if not schedule.delivery_admittable:
            return {
                "outcome": "not_admittable",
                "generation": schedule.delivery_generation,
                "created": 0,
                "deliveries": [],
            }
        if expected_generation != schedule.delivery_generation:
            return {
                "outcome": GovStackAlertSchedule.ADMISSION_STALE_GENERATION,
                "generation": schedule.delivery_generation,
                "created": 0,
                "deliveries": [],
            }

        generation = schedule.delivery_generation
        canonical_recipients = sorted({(str(kind), str(reference)) for kind, reference in recipients})
        if not canonical_recipients:
            if schedule.admitted_generation == generation:
                return {
                    "outcome": GovStackAlertSchedule.ADMISSION_DUPLICATE,
                    "generation": generation,
                    "created": 0,
                    "deliveries": [],
                }
            schedule.admitted_generation = generation
            schedule.admission_outcome = GovStackAlertSchedule.ADMISSION_ZERO_RECIPIENTS
            schedule.save(update_fields=["admitted_generation", "admission_outcome", "updated_at"])
            return {
                "outcome": GovStackAlertSchedule.ADMISSION_ZERO_RECIPIENTS,
                "generation": generation,
                "created": 0,
                "deliveries": [],
            }

        created_count = 0
        deliveries = []
        for recipient_kind, recipient_ref in canonical_recipients:
            delivery, created = _materialize_locked(
                schedule=schedule,
                generation=generation,
                recipient_kind=recipient_kind,
                recipient_ref=recipient_ref,
                owner_key=owner_key or f"schedule:{schedule.pk}",
                correlation_id=correlation_id or f"scheduler:{schedule.pk}:{generation}",
                payload=payload,
                max_attempts=max_attempts,
            )
            deliveries.append(delivery)
            created_count += int(created)

        if schedule.admitted_generation == generation and not created_count:
            return {
                "outcome": GovStackAlertSchedule.ADMISSION_DUPLICATE,
                "generation": generation,
                "created": 0,
                "deliveries": deliveries,
            }

        schedule.admitted_generation = generation
        schedule.admission_outcome = GovStackAlertSchedule.ADMISSION_CREATED
        schedule.save(update_fields=["admitted_generation", "admission_outcome", "updated_at"])
        return {
            "outcome": GovStackAlertSchedule.ADMISSION_CREATED,
            "generation": generation,
            "created": created_count,
            "deliveries": deliveries,
        }


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
    """Compatibility wrapper; all durable creation flows through locked admission."""
    result = admit_schedule_generation(
        schedule_id=schedule.pk,
        expected_generation=generation,
        recipients=[(recipient_kind, recipient_ref)],
        owner_key=owner_key,
        correlation_id=correlation_id,
        payload=payload,
        max_attempts=max_attempts,
    )
    deliveries = result["deliveries"]
    return (deliveries[0] if deliveries else None), bool(result["created"])


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


def _fence_locked_schedule_child_work(*, schedule: GovStackAlertSchedule, now) -> int:
    """Fence current non-terminal child work beneath an already-locked schedule."""
    rows = SchedulerRecipientDelivery.objects.select_for_update().filter(
        schedule_id=schedule.pk
    ).exclude(status__in=TERMINAL)
    fenced_count = rows.update(
        status=SchedulerRecipientDelivery.CANCELLED,
        cancelled_at=now,
        lease_token=None,
        lease_expires_at=None,
        updated_at=now,
    )
    SchedulerOutbox.objects.select_for_update().filter(
        delivery__schedule_id=schedule.pk,
        published_at__isnull=True,
        cancelled_at__isnull=True,
    ).update(
        cancelled_at=now,
        publisher_state=SchedulerOutbox.CANCELLED,
        publisher_state_changed_at=now,
        publisher_token=None,
        publisher_owner="",
        publisher_lease_expires_at=None,
        updated_at=now,
    )
    return fenced_count


def fence_schedule_for_modify(*, schedule: GovStackAlertSchedule) -> int:
    """Fence superseded child work while keeping a locked schedule admittable.

    The caller must hold ``select_for_update()`` on ``schedule``.  A genuine
    delivery-content modification uses this once to invalidate old work and
    advance the authoritative generation before new admission can occur.
    """
    now = timezone.now()
    fenced_count = _fence_locked_schedule_child_work(schedule=schedule, now=now)
    schedule.delivery_generation += 1
    schedule.delivery_admittable = True
    schedule.admitted_generation = None
    schedule.admission_outcome = ""
    schedule.save(update_fields=[
        "delivery_generation", "delivery_admittable", "admitted_generation",
        "admission_outcome", "updated_at",
    ])
    return fenced_count


def cancel_schedule(*, schedule_id) -> int:
    """Durably cancel one schedule and fence all unpublished delivery work."""
    now = timezone.now()
    with transaction.atomic():
        schedule = GovStackAlertSchedule.objects.select_for_update().get(pk=schedule_id)
        schedule.delivery_admittable = False
        schedule.admitted_generation = None
        schedule.admission_outcome = ""
        schedule.save(update_fields=["delivery_admittable", "admitted_generation", "admission_outcome", "updated_at"])

        return _fence_locked_schedule_child_work(schedule=schedule, now=now)


def delete_schedule(*, schedule_id) -> int:
    """Fence durable child work before the parent hard-delete/cascade boundary.

    The model-level broker revoke remains best-effort cleanup.  Correctness is
    established by the locked parent fence and deletion of the schedule row.
    """
    now = timezone.now()
    with transaction.atomic():
        schedule = GovStackAlertSchedule.objects.select_for_update().get(pk=schedule_id)
        fenced_count = _fence_locked_schedule_child_work(schedule=schedule, now=now)
        schedule.delete()
        return fenced_count


def rearm_schedule(*, schedule_id) -> dict:
    """Restore one durably-cancelled schedule for exactly one new generation.

    Re-arm is idempotent: an already-admittable schedule is unchanged and
    returns a duplicate outcome rather than advancing generation again.
    """
    now = timezone.now()
    with transaction.atomic():
        schedule = GovStackAlertSchedule.objects.select_for_update().get(pk=schedule_id)
        if schedule.delivery_admittable:
            return {
                "outcome": GovStackAlertSchedule.ADMISSION_DUPLICATE,
                "generation": schedule.delivery_generation,
                "fenced": 0,
            }
        fenced_count = _fence_locked_schedule_child_work(schedule=schedule, now=now)
        schedule.delivery_generation += 1
        schedule.delivery_admittable = True
        schedule.admitted_generation = None
        schedule.admission_outcome = ""
        schedule.save(update_fields=[
            "delivery_generation", "delivery_admittable", "admitted_generation",
            "admission_outcome", "updated_at",
        ])
        return {
            "outcome": "rearmed",
            "generation": schedule.delivery_generation,
            "fenced": fenced_count,
        }


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


def _publisher_policy():
    return (
        int(getattr(settings, "GOVSTACK_SCHEDULER_PUBLISH_MAX_ATTEMPTS", 3)),
        int(getattr(settings, "GOVSTACK_SCHEDULER_PUBLISH_RETRY_BASE_SECONDS", 30)),
        int(getattr(settings, "GOVSTACK_SCHEDULER_PUBLISH_RETRY_MAX_SECONDS", 900)),
    )


def _publisher_backoff(*, attempt: int) -> timedelta:
    _, base_seconds, max_seconds = _publisher_policy()
    return timedelta(seconds=min(max_seconds, base_seconds * (2 ** max(0, attempt - 1))))


def _clear_publisher_lease(row: SchedulerOutbox):
    row.publisher_token = None
    row.publisher_owner = ""
    row.publisher_lease_expires_at = None


def claim_outbox(*, lease_seconds: int = 60, owner: str = "scheduler-publisher", now=None):
    """Claim one due publisher intent in a short transaction; never performs broker I/O."""
    now = now or timezone.now()
    token = uuid.uuid4().hex
    claimable = Q(publisher_state__in=[SchedulerOutbox.PENDING, SchedulerOutbox.LOCAL_FAILURE, SchedulerOutbox.UNKNOWN_HANDOFF])
    expired_claim = Q(publisher_state=SchedulerOutbox.CLAIMED, publisher_lease_expires_at__lt=now)
    with transaction.atomic():
        row = (SchedulerOutbox.objects.select_for_update()
               .filter(published_at__isnull=True, cancelled_at__isnull=True, available_at__lte=now)
               .filter(claimable | expired_claim)
               .filter(Q(publisher_lease_expires_at__isnull=True) | Q(publisher_lease_expires_at__lt=now))
               .order_by("id").first())
        if row is None:
            return None
        max_attempts, _, _ = _publisher_policy()
        if row.publish_attempts >= max_attempts:
            row.publisher_state = SchedulerOutbox.EXHAUSTED
            row.exhausted_at = now
            row.publisher_state_changed_at = now
            _clear_publisher_lease(row)
            row.save(update_fields=["publisher_state", "exhausted_at", "publisher_state_changed_at", "publisher_token", "publisher_owner", "publisher_lease_expires_at", "updated_at"])
            return None
        row.publisher_generation += 1
        row.publisher_token = token
        row.publisher_owner = owner[:120]
        row.publisher_lease_expires_at = now + timedelta(seconds=lease_seconds)
        row.publish_attempts += 1
        row.publisher_state = SchedulerOutbox.CLAIMED
        row.publisher_state_changed_at = now
        row.save(update_fields=["publisher_generation", "publisher_token", "publisher_owner", "publisher_lease_expires_at", "publish_attempts", "publisher_state", "publisher_state_changed_at", "updated_at"])
        return row.pk, row.delivery.idempotency_key, token, row.publisher_generation


def mark_outbox_published(*, outbox_id, token: str, generation: int, now=None) -> bool:
    now = now or timezone.now()
    with transaction.atomic():
        updated = SchedulerOutbox.objects.filter(
            pk=outbox_id, published_at__isnull=True, cancelled_at__isnull=True,
            publisher_state=SchedulerOutbox.CLAIMED, publisher_token=token, publisher_generation=generation,
        ).update(
            published_at=now, publisher_state=SchedulerOutbox.PUBLISHED,
            publisher_state_changed_at=now, publisher_failure_class="", exhausted_at=None,
            publisher_token=None, publisher_owner="", publisher_lease_expires_at=None,
            last_error="", updated_at=now,
        )
        return bool(updated)


def mark_outbox_failed(*, outbox_id, token: str, generation: int, error_class: str, now=None, unknown_handoff: bool = False) -> bool:
    """Record a fenced publisher failure with finite backoff or terminal exhaustion."""
    now = now or timezone.now()
    with transaction.atomic():
        row = (SchedulerOutbox.objects.select_for_update().filter(
            pk=outbox_id, published_at__isnull=True, cancelled_at__isnull=True,
            publisher_state=SchedulerOutbox.CLAIMED, publisher_token=token, publisher_generation=generation,
        ).first())
        if row is None:
            return False
        max_attempts, _, _ = _publisher_policy()
        row.publisher_failure_class = error_class[:32]
        row.last_error = error_class[:240]
        row.publisher_state_changed_at = now
        _clear_publisher_lease(row)
        if row.publish_attempts >= max_attempts:
            row.publisher_state = SchedulerOutbox.EXHAUSTED
            row.exhausted_at = now
            row.available_at = now
        else:
            row.publisher_state = SchedulerOutbox.UNKNOWN_HANDOFF if unknown_handoff else SchedulerOutbox.LOCAL_FAILURE
            row.available_at = now + _publisher_backoff(attempt=row.publish_attempts)
        row.save(update_fields=["publisher_state", "publisher_failure_class", "publisher_state_changed_at", "publisher_token", "publisher_owner", "publisher_lease_expires_at", "available_at", "exhausted_at", "last_error", "updated_at"])
        return True


def mark_outbox_unknown_handoff(*, outbox_id, token: str, generation: int, error_class: str, now=None) -> bool:
    return mark_outbox_failed(outbox_id=outbox_id, token=token, generation=generation, error_class=error_class, now=now, unknown_handoff=True)


def replay_outbox(*, outbox_id, now=None) -> bool:
    """Deliberately reopen an eligible exhausted publisher intent without losing correlation."""
    now = now or timezone.now()
    with transaction.atomic():
        row = (SchedulerOutbox.objects.select_for_update().filter(
            pk=outbox_id, publisher_state=SchedulerOutbox.EXHAUSTED,
            published_at__isnull=True, cancelled_at__isnull=True,
        ).first())
        if row is None:
            return False
        row.publisher_state = SchedulerOutbox.PENDING
        row.publisher_failure_class = ""
        row.publisher_state_changed_at = now
        row.exhausted_at = None
        row.publish_attempts = 0
        row.available_at = now
        _clear_publisher_lease(row)
        row.save(update_fields=["publisher_state", "publisher_failure_class", "publisher_state_changed_at", "exhausted_at", "publish_attempts", "available_at", "publisher_token", "publisher_owner", "publisher_lease_expires_at", "updated_at"])
        return True


def due_outbox(*, now=None):
    now = now or timezone.now()
    return SchedulerOutbox.objects.filter(
        published_at__isnull=True, cancelled_at__isnull=True,
        publisher_state__in=[SchedulerOutbox.PENDING, SchedulerOutbox.LOCAL_FAILURE, SchedulerOutbox.UNKNOWN_HANDOFF],
        available_at__lte=now,
    ).select_related("delivery")
