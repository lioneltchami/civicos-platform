"""Local-only Scheduler evidence contracts; no outbound transport."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Iterable

class FakeOutcome(str, Enum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    MALFORMED = "malformed"

@dataclass(frozen=True)
class FakeResult:
    outcome: FakeOutcome
    authority: str
    correlation_id: str
    detail: str = ""
    payload: Mapping[str, Any] = None

class LocalFakeAdapter:
    OPERATIONS = frozenset(x.value for x in FakeOutcome)
    def __init__(self, *, authority: str, enabled: bool = False):
        self.authority, self.enabled, self._seen = authority, enabled, set()
    def invoke(self, *, operation: str = "success", correlation_id: str = "", payload: Mapping[str, Any] | None = None) -> FakeResult:
        if not self.enabled:
            raise RuntimeError("local fake adapter is disabled")
        if operation not in self.OPERATIONS:
            return FakeResult(FakeOutcome.REJECTED, self.authority, correlation_id, "operation not allowlisted", {})
        if not isinstance(correlation_id, str) or not correlation_id:
            return FakeResult(FakeOutcome.MALFORMED, self.authority, str(correlation_id), "correlation_id is required", {})
        if correlation_id in self._seen:
            return FakeResult(FakeOutcome.DUPLICATE, self.authority, correlation_id, "duplicate correlation", {})
        self._seen.add(correlation_id)
        return FakeResult(FakeOutcome(operation), self.authority, correlation_id, "", payload or {})

class LocalPaymentsFake(LocalFakeAdapter):
    def __init__(self, *, enabled: bool = False): super().__init__(authority="Payments", enabled=enabled)
class LocalConsentFake(LocalFakeAdapter):
    def __init__(self, *, enabled: bool = False): super().__init__(authority="Consent", enabled=enabled)
PaymentsFakeAdapter = LocalPaymentsFake
ConsentFakeAdapter = LocalConsentFake

@dataclass(frozen=True)
class SchedulerStatus:
    owner_id: str
    tenant_id: str
    queued: int = 0
    leased: int = 0
    retryable: int = 0
    dead_letter: int = 0
    acknowledged: int = 0

def operational_status(records: Iterable[Any], *, owner_id: str, tenant_id: str) -> SchedulerStatus:
    selected = [r for r in records if getattr(r, "owner_id", None) == owner_id and getattr(r, "tenant_id", None) == tenant_id]
    return SchedulerStatus(owner_id, tenant_id, **{s: sum(getattr(r, "state", None) == s for r in selected) for s in ("queued", "leased", "retryable", "dead_letter", "acknowledged")})

def redact_trace(event: Mapping[str, Any]) -> dict[str, Any]:
    sensitive = {"token", "authorization", "password", "email", "phone", "name", "address"}
    return {k: ("[REDACTED]" if k.lower() in sensitive else v) for k, v in event.items()}

class LocalAuthorityContract:
    def __init__(self, *, enabled: bool = False):
        self.payments, self.consent = LocalPaymentsFake(enabled=enabled), LocalConsentFake(enabled=enabled)
    def call(self, authority: str, **kwargs) -> FakeResult:
        if authority == "Payments": return self.payments.invoke(**kwargs)
        if authority == "Consent": return self.consent.invoke(**kwargs)
        return FakeResult(FakeOutcome.REJECTED, authority, kwargs.get("correlation_id", ""), "unknown authority", {})

class LocalSchedulerStatusService:
    def get(self, records: Iterable[Any], *, owner_id: str, tenant_id: str) -> SchedulerStatus:
        return operational_status(records, owner_id=owner_id, tenant_id=tenant_id)

__all__ = ["FakeOutcome", "FakeResult", "LocalFakeAdapter", "LocalPaymentsFake", "LocalConsentFake", "PaymentsFakeAdapter", "ConsentFakeAdapter", "SchedulerStatus", "operational_status", "redact_trace", "LocalAuthorityContract", "LocalSchedulerStatusService"]
