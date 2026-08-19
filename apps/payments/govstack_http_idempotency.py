"""Canonical HTTP idempotency helpers shared by GovStack payment entry points."""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass

@dataclass(frozen=True)
class IdempotencyDecision:
    kind: str
    fingerprint: str
    status_code: int = 0


def canonical_fingerprint(*, tenant: str, method: str, path: str, key: str, payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    value = "|".join((tenant.strip(), method.upper(), "/" + path.strip("/"), key.strip())) + "|" + canonical
    return hashlib.sha256(value.encode()).hexdigest()


def decide(existing_fingerprint: str | None, fingerprint: str) -> IdempotencyDecision:
    if existing_fingerprint is None:
        return IdempotencyDecision("first_writer", fingerprint)
    if existing_fingerprint == fingerprint:
        return IdempotencyDecision("replay", fingerprint, 200)
    return IdempotencyDecision("conflict", fingerprint, 409)
