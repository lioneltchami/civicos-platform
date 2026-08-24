"""Pure provider-finality-aware batch decision policy.

This module is deliberately a decision primitive only. It does not call providers,
write callbacks, or persist state.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BatchDecision:
    state: str
    kick_back: bool
    retry_ids: tuple[str, ...]
    settled_ids: tuple[str, ...]


_TERMINAL = {"settled", "rejected"}
_NON_FINAL = {"retryable", "uncertain", "review", "unresolved"}


def evaluate(items: list[dict], *, failure_threshold: float = 0.25) -> BatchDecision:
    """Prevent non-final provider outcomes from becoming a terminal batch result."""
    settled_ids = tuple(str(item["id"]) for item in items if item.get("status") == "settled")
    statuses = [item.get("status") for item in items]
    non_final = [status for status in statuses if status in _NON_FINAL]
    rejected_count = sum(status == "rejected" for status in statuses)
    blocking_count = sum(status not in _TERMINAL for status in statuses)
    ratio = blocking_count / len(items) if items else 0

    if items and ratio > failure_threshold:
        state = "paused"
    elif "unresolved" in non_final:
        state = "unresolved"
    elif "uncertain" in non_final:
        state = "uncertain"
    elif "retryable" in non_final:
        state = "retryable"
    elif "review" in non_final:
        state = "review"
    elif rejected_count:
        state = "partial"
    else:
        state = "completed"

    retry_ids = tuple(
        str(item["id"])
        for item in items
        if item.get("status") in {"retryable", "uncertain", "unresolved"}
    )
    return BatchDecision(state, state == "paused" or bool(non_final), retry_ids, settled_ids)


@dataclass(frozen=True)
class Lease:
    item_id: str
    owner: str
    expires_at: float


def select_partial_retry(
    items: list[dict], *, owner: str, now: float, lease_seconds: int = 300
) -> tuple[Lease, ...]:
    """Select retryable work once, with deterministic owner/expiry leases."""
    if not owner or lease_seconds <= 0:
        return ()
    leases = []
    for item in items:
        item_id = str(item.get("id", ""))
        if not item_id or item.get("status") in {"settled", "rejected"}:
            continue
        if item.get("status") not in {"retryable", "uncertain", "review"}:
            continue
        current = item.get("lease")
        if isinstance(current, dict) and float(current.get("expires_at", 0)) > now:
            continue
        leases.append(Lease(item_id, owner[:100], now + lease_seconds))
    return tuple(leases)
