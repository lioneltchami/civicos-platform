"""Provider-neutral Payments adapter contract.

Adapters are injected by callers and must never persist credentials.  Only a
provider/source outcome can establish settlement finality; local validation is
not settlement evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Protocol


class ProviderOutcome(StrEnum):
    SETTLED = "settled"
    REJECTED = "rejected"
    INVALID_ACCOUNT = "invalid_account"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    TIMEOUT = "timeout"
    NETWORK = "network"
    DUPLICATE = "duplicate"
    UNCERTAIN = "uncertain"
    COMPENSATED = "compensated"


@dataclass(frozen=True)
class ProviderResult:
    outcome: ProviderOutcome
    provider_attempt_id: str = ""
    external_transaction_id: str = ""
    code: str = ""
    message: str = ""
    retryable: bool = False
    raw: Mapping[str, Any] | None = None
    # Finality is explicit: an adapter must supply a stable observation/event
    # identity and the caller must separately attest that its source was
    # verified. Defaults preserve fail-closed behavior for existing adapters.
    observation_id: str = ""
    event_id: str = ""
    verified: bool = False
    verification_method: str = ""

    @property
    def is_final(self) -> bool:
        return self.outcome in {ProviderOutcome.SETTLED, ProviderOutcome.REJECTED, ProviderOutcome.INVALID_ACCOUNT, ProviderOutcome.INSUFFICIENT_FUNDS, ProviderOutcome.COMPENSATED}

    @property
    def is_settled(self) -> bool:
        return self.outcome is ProviderOutcome.SETTLED


class PaymentProvider(Protocol):
    def submit(self, *, request_id: str, payment: Mapping[str, Any]) -> ProviderResult: ...
    def get_status(self, *, request_id: str, provider_attempt_id: str = "", external_transaction_id: str = "") -> ProviderResult: ...
    def compensate(self, *, request_id: str, external_transaction_id: str) -> ProviderResult: ...


def normalize_result(result: ProviderResult) -> ProviderResult:
    """Strip potentially sensitive raw provider data at the application boundary."""
    return ProviderResult(
        result.outcome,
        result.provider_attempt_id[:100],
        result.external_transaction_id[:100],
        result.code[:50],
        result.message[:255],
        result.retryable,
        None,
        result.observation_id[:160],
        result.event_id[:160],
        bool(result.verified),
        result.verification_method[:80],
    )
