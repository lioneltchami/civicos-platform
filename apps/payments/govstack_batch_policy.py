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
