"""The sole canonical boundary for provider-bound payment work.

This module deliberately performs only durable admission. Provider I/O remains in
``provider_runtime`` and is scheduled after the transaction commits.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from .govstack_models import IdempotencyLedger, PaymentAttempt, ProviderRegistration
from .provider_runtime import enqueue_attempt


class AdmissionError(ValueError):
    pass


@dataclass(frozen=True)
class AdmissionResult:
    attempt: PaymentAttempt
    admitted: bool
    replayed: bool = False
    idempotency_replayed: bool = False


def trusted_tenant_id(*, authenticated_tenant: str | None, declared_tenant: str | None = None) -> str:
    """Return only the tenant supplied by an already-authenticated boundary.

    A request body/header is not an authority. If a protocol carries a declared
    value it must match the trusted principal, otherwise admission fails closed.
    """
    trusted = (authenticated_tenant or "").strip()
    declared = (declared_tenant or "").strip()
    if not trusted or (declared and declared != trusted):
        raise AdmissionError("trusted tenant identity is required")
    return trusted


def _fingerprint(*, tenant_id: str, operation: str, request_key: str, payload: dict[str, Any], amount: Decimal | str | None, currency: str) -> str:
    value = {
        "tenant": tenant_id, "operation": operation, "request_key": request_key,
        "payload": payload, "amount": str(amount) if amount is not None else None,
        "currency": currency,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def admit_provider_attempt(*, tenant_id: str, operation: str, request_key: str,
                           payload: dict[str, Any], amount: Decimal | str | None,
                           currency: str, domain_references: dict[str, Any] | None = None,
                           trusted_tenant: str | None = None) -> AdmissionResult:
    tenant_id = trusted_tenant_id(authenticated_tenant=trusted_tenant or tenant_id, declared_tenant=tenant_id)
    operation, request_key, currency = (operation or "").strip(), (request_key or "").strip(), (currency or "").strip().upper()
    if not operation or not request_key or not currency or not isinstance(payload, dict):
        raise AdmissionError("operation, request key, currency, and payload are required")
    fingerprint = _fingerprint(tenant_id=tenant_id, operation=operation, request_key=request_key, payload=payload, amount=amount, currency=currency)
    with transaction.atomic():
        registrations = ProviderRegistration.objects.select_for_update().filter(tenant_id=tenant_id, operation=operation, active=True)
        if registrations.count() != 1:
            raise AdmissionError("provider configuration is unavailable")
        registration = registrations.get()
        if not registration.provider_name or not registration.configuration_version or not isinstance(registration.configuration, dict):
            raise AdmissionError("provider configuration is malformed")
        ledger, created_ledger = IdempotencyLedger.objects.get_or_create(
            tenant_id=tenant_id, method="POST", path=f"provider:{operation}", key=request_key,
            defaults={"fingerprint": fingerprint},
        )
        if not created_ledger:
            if ledger.fingerprint != fingerprint:
                raise AdmissionError("idempotency key conflicts with existing request")
            existing = PaymentAttempt.objects.filter(tenant_id=tenant_id, operation=operation, request_id=request_key).first()
            if existing is None:
                raise AdmissionError("idempotency reservation has no admitted attempt")
            return AdmissionResult(existing, admitted=True, replayed=True, idempotency_replayed=True)
        attempt = PaymentAttempt.objects.create(
            tenant_id=tenant_id, operation=operation, request_id=request_key,
            amount=amount, currency=currency, payload_fingerprint=fingerprint,
            provider_registration=registration,
            source_bb_id=str((domain_references or {}).get("source_bb_id", ""))[:50],
        )
        transaction.on_commit(lambda attempt_id=str(attempt.pk): enqueue_attempt(PaymentAttempt.objects.get(pk=attempt_id)))
        return AdmissionResult(attempt=attempt, admitted=True)


__all__ = ["AdmissionError", "AdmissionResult", "admit_provider_attempt", "trusted_tenant_id"]
