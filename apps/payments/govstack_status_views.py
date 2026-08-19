"""Non-PII status projection helpers for tenant-scoped HTTP views."""
from __future__ import annotations

def canonical_status(*, internal: str, provider: str = "", reconciliation: str = "") -> str:
    if provider == "settled" and internal == "settled": return "settled"
    if provider == "settled" and internal != "settled": return "review"
    if provider in {"rejected", "invalid_account", "insufficient_funds"} and internal == "rejected": return "rejected"
    if reconciliation in {"mismatch", "unknown"} or provider in {"", "unknown", "uncertain"}: return "review"
    return internal if internal in {"received", "processing", "retryable", "uncertain", "review", "dead_letter", "settled", "rejected"} else "review"

def tenant_status(*, tenant_id: str, attempt_id: str, internal: str, provider: str = "", reconciliation: str = "") -> dict[str, str]:
    return {"tenant_id": tenant_id[:100], "attempt_id": attempt_id[:100], "status": canonical_status(internal=internal, provider=provider, reconciliation=reconciliation)}
