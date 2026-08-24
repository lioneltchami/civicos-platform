"""Canonical HTTP idempotency helpers shared by GovStack payment entry points."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class IdempotencyDecision:
    kind: str
    fingerprint: str
    status_code: int = 0


def canonical_fingerprint(*, tenant: str, method: str, path: str, key: str, payload: object) -> str:
    """Build the route-scoped canonical request identity.

    Empty keys are rejected by callers; this helper still canonicalizes them so
    middleware and direct service use cannot disagree about path semantics.
    """
    normalized_path = "/" + "/".join(part for part in path.strip().split("/") if part)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    value = (
        "|".join((tenant.strip(), method.strip().upper(), normalized_path, key.strip()))
        + "|"
        + canonical
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def decide(existing_fingerprint: str | None, fingerprint: str) -> IdempotencyDecision:
    if existing_fingerprint is None:
        return IdempotencyDecision("first_writer", fingerprint)
    if existing_fingerprint == fingerprint:
        return IdempotencyDecision("replay", fingerprint, 200)
    return IdempotencyDecision("conflict", fingerprint, 409)
