from __future__ import annotations

"""Test-only Scheduler evidence boundaries.

This module contains no production transport.  Fakes are explicit opt-in and
all evidence is synthetic, bounded, deterministic, and redacted.
"""
import json  # noqa: E402
from collections.abc import Iterable, Mapping  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from enum import Enum  # noqa: E402
from hashlib import sha256  # noqa: E402
from typing import Any  # noqa: E402


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
    payload: Mapping[str, Any] | None = None


class LocalFakeAdapter:
    OPERATIONS = frozenset(x.value for x in FakeOutcome)
    AUTHORITIES = frozenset(("Payments", "Consent"))

    def __init__(self, *, authority: str, enabled: bool = False) -> None:
        if authority not in self.AUTHORITIES:
            raise ValueError("unknown authority")
        self.authority, self.enabled, self._seen = authority, enabled, set()

    def invoke(
        self,
        *,
        operation: str = "success",
        correlation_id: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> FakeResult:
        if not self.enabled:
            raise RuntimeError("local fake adapter is disabled")
        if operation not in self.OPERATIONS:
            return FakeResult(
                FakeOutcome.REJECTED,
                self.authority,
                correlation_id,
                "operation not allowlisted",
                {},
            )
        if not isinstance(correlation_id, str) or not correlation_id or len(correlation_id) > 180:
            return FakeResult(
                FakeOutcome.MALFORMED,
                self.authority,
                str(correlation_id),
                "correlation_id is required",
                {},
            )
        if correlation_id in self._seen:
            return FakeResult(
                FakeOutcome.DUPLICATE, self.authority, correlation_id, "duplicate correlation", {}
            )
        self._seen.add(correlation_id)
        return FakeResult(FakeOutcome(operation), self.authority, correlation_id, "", {})


class LocalPaymentsFake(LocalFakeAdapter):
    def __init__(self, *, enabled: bool = False) -> None:
        super().__init__(authority="Payments", enabled=enabled)


class LocalConsentFake(LocalFakeAdapter):
    def __init__(self, *, enabled: bool = False) -> None:
        super().__init__(authority="Consent", enabled=enabled)


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
    delivered: int = 0
    cancelled: int = 0
    total: int = 0


def _status(records: Iterable[Any], owner_id: str, tenant_id: str) -> SchedulerStatus:
    selected = [
        r
        for r in records
        if getattr(r, "owner_key", getattr(r, "owner_id", None)) == owner_id
        and getattr(r, "tenant_id", tenant_id) == tenant_id
    ]
    counts = {
        s: sum(getattr(r, "status", getattr(r, "state", None)) == s for r in selected)
        for s in (
            "pending",
            "in_flight",
            "retry",
            "dead_letter",
            "acknowledged",
            "delivered",
            "cancelled",
        )
    }
    return SchedulerStatus(
        owner_id,
        tenant_id,
        counts["pending"],
        counts["in_flight"],
        counts["retry"],
        counts["dead_letter"],
        counts["acknowledged"],
        counts["delivered"],
        counts["cancelled"],
        len(selected),
    )


def operational_status(records: Iterable[Any], *, owner_id: str, tenant_id: str) -> SchedulerStatus:
    return _status(records, owner_id, tenant_id)


class LocalSchedulerStatusService:
    """Read-only projection; scope must be supplied by trusted auth/ownership."""

    def get(self, records: Iterable[Any], *, owner_id: str, tenant_id: str) -> SchedulerStatus:
        if not owner_id or not tenant_id:
            raise PermissionError("trusted owner and tenant scope required")
        return _status(records, owner_id, tenant_id)

    def from_database(
        self, *, owner_id: str, tenant_id: str, limit: int = 100
    ) -> tuple[SchedulerStatus, list[dict[str, Any]]]:
        if not owner_id or not tenant_id or not 0 < limit <= 1000:
            raise PermissionError("valid trusted scope and bounded limit required")
        from apps.appointments.models import SchedulerRecipientDelivery

        qs = SchedulerRecipientDelivery.objects.filter(owner_key=owner_id).order_by("pk")[:limit]
        rows = list(qs)
        return _status(rows, owner_id, tenant_id), [
            {"id": r.pk, "status": r.status, "attempts": r.attempts} for r in rows
        ]


def redact_trace(event: Mapping[str, Any]) -> dict[str, Any]:
    sensitive = {
        "token",
        "authorization",
        "password",
        "email",
        "phone",
        "name",
        "address",
        "url",
        "payload",
        "recipient",
        "contact",
        "message",
        "credential",
    }
    return {
        k: (
            "[REDACTED]"
            if k.lower() in sensitive
            or any(x in k.lower() for x in ("token", "secret", "password"))
            else v
        )
        for k, v in event.items()
    }


class LocalAuthorityContract:
    def __init__(self, *, enabled: bool = False) -> None:
        self.payments, self.consent = (
            LocalPaymentsFake(enabled=enabled),
            LocalConsentFake(enabled=enabled),
        )

    def call(self, authority: str, **kwargs) -> FakeResult:  # noqa: ANN003
        if authority == "Payments":
            return self.payments.invoke(**kwargs)
        if authority == "Consent":
            return self.consent.invoke(**kwargs)
        return FakeResult(
            FakeOutcome.REJECTED,
            authority,
            kwargs.get("correlation_id", ""),
            "unknown authority",
            {},
        )


OPERATION_IDS = tuple(f"SCHED-{i:02d}" for i in range(1, 38))


def build_local_trace(
    events: Iterable[Mapping[str, Any]], *, source_revision: str
) -> dict[str, Any]:
    by_id = {str(e.get("operation_id")): redact_trace(e) for e in events}
    return {
        "source_revision": source_revision,
        "operations": [{"operation_id": i, **by_id[i]} for i in OPERATION_IDS if i in by_id],
    }


def validate_local_evidence(bundle: Mapping[str, Any]) -> tuple[bool, str]:
    ops = bundle.get("operations", [])
    ids = [x.get("operation_id") for x in ops]
    if ids != list(OPERATION_IDS):
        return False, "exact 37 operation IDs required in deterministic order"
    if bundle.get("source_revision") in (None, ""):
        return False, "source revision required"
    raw = json.dumps(bundle, sort_keys=True, separators=(",", ":"))
    if bundle.get("checksum") and bundle["checksum"] != sha256(raw.encode()).hexdigest():
        return False, "checksum mismatch"
    return True, "valid"


__all__ = [
    "OPERATION_IDS",
    "ConsentFakeAdapter",
    "FakeOutcome",
    "FakeResult",
    "LocalAuthorityContract",
    "LocalConsentFake",
    "LocalFakeAdapter",
    "LocalPaymentsFake",
    "LocalSchedulerStatusService",
    "PaymentsFakeAdapter",
    "SchedulerStatus",
    "build_local_trace",
    "operational_status",
    "redact_trace",
    "validate_local_evidence",
]
