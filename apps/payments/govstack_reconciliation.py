"""Deterministic reconciliation primitives for internal provider/source feeds.

A comparison can produce review data, but only a verified exact observation may
be used by lifecycle code to establish finality.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ProviderObservation:
    tenant_id: str
    payment_id: str
    provider_transaction_id: str
    event_id: str
    amount: Decimal
    currency: str
    outcome: str
    verified: bool = False
    observed_at: datetime | None = None
    source: str = "provider"

    @property
    def exact_binding(self) -> bool:
        return bool(
            self.tenant_id
            and self.payment_id
            and self.provider_transaction_id
            and self.event_id
            and self.amount >= 0
            and self.currency
        )

    @property
    def establishes_finality(self) -> bool:
        return self.verified and self.exact_binding and self.outcome in {"settled", "rejected"}


@dataclass(frozen=True)
class ReconciliationResult:
    state: str
    final: bool
    reason: str
    redacted: dict[str, Any]


def reconcile(
    *,
    tenant_id: str,
    payment_id: str,
    internal_amount: Decimal | str,
    internal_currency: str,
    observation: ProviderObservation | None,
) -> ReconciliationResult:
    if observation is None:
        return ReconciliationResult(
            "unknown", False, "NO_OBSERVATION", {"tenant_id": tenant_id, "payment_id": payment_id}
        )
    amount = Decimal(str(internal_amount))
    exact = (
        observation.tenant_id == tenant_id
        and observation.payment_id == payment_id
        and observation.amount == amount
        and observation.currency.upper() == internal_currency.upper()
        and observation.exact_binding
    )
    if not exact:
        return ReconciliationResult(
            "mismatch",
            False,
            "EXACT_BINDING_MISMATCH",
            {
                "tenant_id": tenant_id,
                "payment_id": payment_id,
                "event_id": observation.event_id[:100],
            },
        )
    if not observation.verified:
        return ReconciliationResult(
            "review",
            False,
            "UNVERIFIED_OBSERVATION",
            {
                "tenant_id": tenant_id,
                "payment_id": payment_id,
                "event_id": observation.event_id[:100],
            },
        )
    return ReconciliationResult(
        "matched",
        observation.establishes_finality,
        "VERIFIED_EXACT_OBSERVATION",
        {
            "tenant_id": tenant_id,
            "payment_id": payment_id,
            "event_id": observation.event_id[:100],
            "provider_transaction_id": observation.provider_transaction_id[:100],
            "outcome": observation.outcome,
        },
    )


def mismatch_report(*, tenant_id: str, rows: list[ReconciliationResult]) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "count": len(rows),
        "mismatches": [
            r.redacted | {"state": r.state, "reason": r.reason}
            for r in rows
            if r.state in {"mismatch", "unknown", "review"}
        ],
    }
