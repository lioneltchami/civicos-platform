"""Deterministic provider double for repository tests only; no network or secrets."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from apps.payments.govstack_provider import PaymentProvider, ProviderOutcome, ProviderResult


class DeterministicProvider(PaymentProvider):
    def __init__(self, outcomes: Iterable[ProviderResult] = (), statuses: Mapping[str, ProviderResult] | None = None):
        self._outcomes = list(outcomes)
        self._statuses = dict(statuses or {})
        self.submissions: list[str] = []
        self.status_queries: list[str] = []

    def submit(self, *, request_id: str, payment: Mapping[str, object]) -> ProviderResult:
        self.submissions.append(request_id)
        if not self._outcomes:
            return ProviderResult(ProviderOutcome.UNCERTAIN, code="NO_FIXTURE", message="No deterministic outcome supplied")
        return self._outcomes.pop(0)

    def get_status(self, *, request_id: str, provider_attempt_id: str = "", external_transaction_id: str = "") -> ProviderResult:
        key = external_transaction_id or provider_attempt_id or request_id
        self.status_queries.append(key)
        return self._statuses.get(key, ProviderResult(ProviderOutcome.UNCERTAIN, code="STATUS_UNKNOWN", message="No deterministic status supplied"))

    def compensate(self, *, request_id: str, external_transaction_id: str) -> ProviderResult:
        return ProviderResult(ProviderOutcome.COMPENSATED, external_transaction_id=external_transaction_id, code="COMPENSATED")
