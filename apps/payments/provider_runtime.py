"""Provider-runtime boundary for durable, asynchronous payment execution.

This module deliberately has no default provider. Applications must explicitly
register a provider per tenant and operation (normally during process startup).
HTTP views should create/reuse an attempt and enqueue ``orchestrate_attempt``;
provider calls happen only in the worker boundary below.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.db import transaction

from .govstack_failure_services import PaymentLifecycleService
from .govstack_models import PaymentAttempt, ProviderObservation
from .govstack_provider import PaymentProvider, ProviderOutcome, ProviderResult


class ProviderUnavailable(RuntimeError):
    """Raised when no explicitly configured provider exists for the scope."""


@dataclass(frozen=True)
class ProviderKey:
    tenant_id: str
    operation: str


class ProviderRuntime:
    """Resolve providers explicitly and execute one durable attempt at a time."""

    _providers: dict[ProviderKey, PaymentProvider] = {}

    @classmethod
    def configure(cls, *, tenant_id: str, operation: str, provider: PaymentProvider) -> None:
        if not tenant_id or not operation or provider is None:
            raise ValueError("tenant_id, operation, and provider are required")
        cls._providers[ProviderKey(tenant_id[:100], operation[:30])] = provider

    @classmethod
    def clear(cls) -> None:
        cls._providers.clear()

    @classmethod
    def resolve(cls, *, tenant_id: str, operation: str) -> PaymentProvider:
        provider = cls._providers.get(ProviderKey(tenant_id[:100], operation[:30]))
        if provider is None:
            raise ProviderUnavailable("no provider is configured for this tenant and operation")
        return provider

    @classmethod
    def submit_or_poll(cls, attempt_id: str) -> ProviderResult:
        """Submit pending work, but poll uncertain work before any resubmission.

        The row lock is held only while claiming the durable attempt and recording
        the result; network I/O is outside the lock. A terminal or review attempt
        has no provider side effect.
        """
        with transaction.atomic():
            attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
            if attempt.is_terminal or attempt.status == PaymentAttempt.STATUS_REVIEW:
                return ProviderResult(ProviderOutcome.UNCERTAIN, code="NOOP_TERMINAL")
            provider = cls.resolve(tenant_id=attempt.tenant_id, operation=attempt.operation)
            should_poll = attempt.status == PaymentAttempt.STATUS_UNCERTAIN
            request_id = attempt.request_id
            provider_attempt_id = attempt.provider_attempt_id
            external_transaction_id = attempt.external_transaction_id
            payload = {"request_id": request_id, "amount": str(attempt.amount or ""), "currency": attempt.currency}

        try:
            if should_poll:
                result = provider.get_status(
                    request_id=request_id,
                    provider_attempt_id=provider_attempt_id,
                    external_transaction_id=external_transaction_id,
                )
            else:
                result = provider.submit(request_id=request_id, payment=payload)
        except Exception:  # Provider transport errors are non-final by design.
            result = ProviderResult(
                ProviderOutcome.NETWORK,
                provider_attempt_id=provider_attempt_id,
                external_transaction_id=external_transaction_id,
                code="PROVIDER_RUNTIME_ERROR",
                message="Provider invocation failed; authoritative status is required.",
                observation_id=f"runtime-error:{attempt_id}:{attempt.attempt_count + 1}",
                event_id=f"runtime-error:{attempt_id}:{attempt.attempt_count + 1}",
                verification_method="runtime-error",
            )

        if not isinstance(result, ProviderResult):
            raise TypeError("provider must return ProviderResult")
        with transaction.atomic():
            attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
            if attempt.is_terminal:
                return result
            PaymentLifecycleService.record_provider_result(attempt, result)
        return result

    @classmethod
    def unavailable(cls, attempt_id: str) -> ProviderResult:
        """Persist fail-closed configuration state without invoking a provider."""
        with transaction.atomic():
            attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
            observation_id = f"configuration-unavailable:{attempt.pk}"
            result = ProviderResult(
                ProviderOutcome.UNCERTAIN,
                code="PROVIDER_UNAVAILABLE",
                message="Provider configuration is unavailable",
                observation_id=observation_id,
                event_id=observation_id,
                verification_method="configuration",
            )
            if attempt.is_terminal:
                return result
            if ProviderObservation.objects.filter(
                observation_kind=ProviderObservation.KIND_PROVIDER,
                observation_id=observation_id,
            ).exists():
                return result
            PaymentLifecycleService.record_provider_result(attempt, result)
            return result


def orchestrate_attempt(attempt_id: str) -> ProviderResult:
    """Worker entry point used by G2P, prepayment, and P2G task paths."""
    try:
        return ProviderRuntime.submit_or_poll(attempt_id)
    except ProviderUnavailable:
        return ProviderRuntime.unavailable(attempt_id)


def enqueue_attempt(attempt: PaymentAttempt) -> Any:
    """Queue the shared boundary after the caller's transaction commits."""
    from django.db import transaction as db_transaction
    from .provider_runtime_tasks import orchestrate_attempt_task
    db_transaction.on_commit(lambda: orchestrate_attempt_task.delay(str(attempt.pk)))
    return attempt


__all__ = ["ProviderRuntime", "ProviderUnavailable", "ProviderKey", "enqueue_attempt", "orchestrate_attempt"]
