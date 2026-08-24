"""Repository-local recipient delivery state machine.

This module deliberately has no network, broker, or credential dependency.  A
Django/Celery adapter can persist the records and call the pure transitions
inside ``transaction.atomic``.  Correlation and idempotency keys are opaque;
logs never contain recipient addresses or message bodies.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    RETRY = "retry"
    DELIVERED = "delivered"
    ACKNOWLEDGED = "acknowledged"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


TERMINAL = frozenset(
    {
        DeliveryStatus.DELIVERED,
        DeliveryStatus.ACKNOWLEDGED,
        DeliveryStatus.CANCELLED,
        DeliveryStatus.DEAD_LETTER,
    }
)


@dataclass
class RecipientDelivery:
    correlation_id: str
    idempotency_key: str
    recipient_ref: str
    max_attempts: int = 3
    status: DeliveryStatus = DeliveryStatus.PENDING
    attempts: int = 0
    next_attempt_at: float = 0.0
    last_error: str | None = None
    lease_token: str | None = None
    acknowledged_at: float | None = None
    history: list[tuple[str, DeliveryStatus]] = field(default_factory=list)

    def claim(self, *, lease_token: str, now: float) -> bool:
        """Claim only available work; stale workers cannot claim terminal work."""
        if self.status in TERMINAL or self.next_attempt_at > now:
            return False
        self.status = DeliveryStatus.IN_FLIGHT
        self.lease_token = lease_token
        self.attempts += 1
        self.history.append(("claim", self.status))
        return True

    def succeed(self, *, lease_token: str) -> bool:
        if self.status != DeliveryStatus.IN_FLIGHT or self.lease_token != lease_token:
            return False
        self.status = DeliveryStatus.DELIVERED
        self.lease_token = None
        self.last_error = None
        self.history.append(("success", self.status))
        return True

    def fail(
        self, *, lease_token: str, error: str, now: float, backoff: Callable[[int], float]
    ) -> bool:
        if self.status != DeliveryStatus.IN_FLIGHT or self.lease_token != lease_token:
            return False
        self.lease_token = None
        self.last_error = error[:240]
        if self.attempts >= self.max_attempts:
            self.status = DeliveryStatus.DEAD_LETTER
        else:
            self.status = DeliveryStatus.RETRY
            self.next_attempt_at = now + max(0.0, backoff(self.attempts))
        self.history.append(("failure", self.status))
        return True

    def cancel(self) -> bool:
        if self.status in TERMINAL:
            return self.status == DeliveryStatus.CANCELLED
        self.status = DeliveryStatus.CANCELLED
        self.lease_token = None
        self.history.append(("cancel", self.status))
        return True

    def acknowledge(self, *, now: float) -> bool:
        if self.status != DeliveryStatus.DELIVERED:
            return False
        self.status = DeliveryStatus.ACKNOWLEDGED
        self.acknowledged_at = now
        self.history.append(("ack", self.status))
        return True

    def replay(self) -> bool:
        if self.status != DeliveryStatus.DEAD_LETTER:
            return False
        self.status = DeliveryStatus.RETRY
        self.next_attempt_at = 0.0
        self.last_error = None
        self.history.append(("replay", self.status))
        return True


class DeliveryStore:
    """In-memory deterministic stand-in for a unique-keyed durable table."""

    def __init__(self) -> None:
        self._rows: dict[str, RecipientDelivery] = {}

    def create(
        self,
        *,
        correlation_id: str,
        idempotency_key: str,
        recipient_ref: str,
        max_attempts: int = 3,
    ) -> RecipientDelivery:
        if idempotency_key in self._rows:
            return self._rows[idempotency_key]
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        row = RecipientDelivery(correlation_id, idempotency_key, recipient_ref, max_attempts)
        self._rows[idempotency_key] = row
        return row

    def get(self, idempotency_key: str) -> RecipientDelivery:
        return self._rows[idempotency_key]

    def metrics(self) -> dict[str, int]:
        result = {status.value: 0 for status in DeliveryStatus}
        for row in self._rows.values():
            result[row.status.value] += 1
        result["attempts"] = sum(row.attempts for row in self._rows.values())
        return result

    def operational_status(self, *, owner: str, authorized: bool) -> list[dict[str, object]]:
        if not authorized:
            raise PermissionError("operational delivery status requires authorization")
        # owner is an authorization boundary, not a logged or returned PII field.
        return [
            {
                "idempotency_key": row.idempotency_key,
                "status": row.status.value,
                "attempts": row.attempts,
                "owner": owner,
            }
            for row in self._rows.values()
        ]
