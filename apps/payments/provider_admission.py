from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.db import transaction

from .govstack_models import PaymentAttempt, ProviderRegistration
from .provider_runtime import enqueue_attempt


class AdmissionError(ValueError):
    pass


@dataclass(frozen=True)
class AdmissionResult:
    attempt: PaymentAttempt
    admitted: bool
    replayed: bool = False


def admit_provider_attempt(*, tenant_id: str, operation: str, request_key: str,
                           payload: dict[str, Any], amount: Decimal | str | None,
                           currency: str, domain_references: dict[str, Any] | None = None) -> AdmissionResult:
    tenant_id = (tenant_id or "").strip()
    operation = (operation or "").strip()
    request_key = (request_key or "").strip()
    currency = (currency or "").strip().upper()
    if not tenant_id or not operation or not request_key or not currency:
        raise AdmissionError("tenant, operation, request key, and currency are required")
    with transaction.atomic():
        registrations = ProviderRegistration.objects.select_for_update().filter(
            tenant_id=tenant_id, operation=operation, active=True
        )
        if registrations.count() != 1:
            raise AdmissionError("provider configuration is unavailable")
        registration = registrations.get()
        if not registration.provider_name or not registration.configuration_version or not isinstance(registration.configuration, dict):
            raise AdmissionError("provider configuration is malformed")
        attempt, created = PaymentAttempt.objects.get_or_create(
            tenant_id=tenant_id, operation=operation, request_id=request_key,
            defaults={"amount": amount, "currency": currency, "payload_fingerprint": "admitted", "provider_registration": registration,
                      "source_bb_id": str((domain_references or {}).get("source_bb_id", ""))[:50]},
        )
        if not created:
            if attempt.tenant_id != tenant_id or attempt.currency != currency or attempt.amount != Decimal(str(amount)):
                raise AdmissionError("request key conflicts with existing attempt")
            return AdmissionResult(attempt=attempt, admitted=True, replayed=True)
        transaction.on_commit(lambda: enqueue_attempt(attempt))
        return AdmissionResult(attempt=attempt, admitted=True)


__all__ = ["AdmissionError", "AdmissionResult", "admit_provider_attempt"]
