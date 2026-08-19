"""Pure batch safety policy: pause on threshold and exclude settled items."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class BatchDecision:
    state: str
    kick_back: bool
    retry_ids: tuple[str, ...]
    settled_ids: tuple[str, ...]


def evaluate(items: list[dict], *, failure_threshold: float = 0.25) -> BatchDecision:
    settled = tuple(str(i["id"]) for i in items if i.get("status") == "settled")
    failures = [i for i in items if i.get("status") in {"rejected", "uncertain", "retryable", "review"}]
    ratio = len(failures) / len(items) if items else 0
    state = "paused" if ratio > failure_threshold else ("partial" if failures else "completed")
    retry = tuple(str(i["id"]) for i in failures if i.get("status") in {"retryable", "uncertain"} and i.get("id") not in settled)
    return BatchDecision(state, state == "paused", retry, settled)


@dataclass(frozen=True)
class Lease:
    item_id: str
    owner: str
    expires_at: float


def select_partial_retry(items: list[dict], *, owner: str, now: float, lease_seconds: int = 300) -> tuple[Lease, ...]:
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
