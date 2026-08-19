from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from .govstack_failure_services import PaymentLifecycleService
from .govstack_models import PaymentAttempt, ProviderObservation, ProviderRegistration
from .govstack_provider import PaymentProvider, ProviderOutcome, ProviderResult


class ProviderUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderKey:
    tenant_id: str
    operation: str


class ProviderRuntime:
    _providers: dict[ProviderKey, PaymentProvider] = {}

    @classmethod
    def configure(cls, *, tenant_id: str, operation: str, provider: PaymentProvider) -> None:
        """Register a locally available adapter against an explicit durable scope.

        The in-memory map holds executable adapter objects only; durable
        ``ProviderRegistration`` remains the source of tenant/operation authority.
        Production workers must configure the adapter deliberately during startup.
        """
        tenant_id = (tenant_id or "").strip()
        operation = (operation or "").strip()
        if not tenant_id or not operation or provider is None:
            raise ValueError("tenant_id, operation, and provider are required")
        provider_name = provider.__class__.__name__[:80]
        with transaction.atomic():
            registration, created = ProviderRegistration.objects.select_for_update().get_or_create(
                tenant_id=tenant_id,
                operation=operation,
                defaults={
                    "provider_name": provider_name,
                    "configuration_version": "local-adapter-v1",
                    "configuration": {"mode": "explicit_local_adapter"},
                    "active": True,
                },
            )
            if not created and (not registration.active or registration.provider_name != provider_name):
                registration.provider_name = provider_name
                registration.configuration_version = "local-adapter-v1"
                registration.configuration = {"mode": "explicit_local_adapter"}
                registration.active = True
                registration.save(
                    update_fields=[
                        "provider_name",
                        "configuration_version",
                        "configuration",
                        "active",
                        "updated_at",
                    ]
                )
        cls._providers[ProviderKey(tenant_id, operation)] = provider

    @classmethod
    def clear(cls) -> None:
        cls._providers.clear()

    @classmethod
    def resolve(cls, *, tenant_id: str, operation: str) -> PaymentProvider:
        if not tenant_id or not operation:
            raise ProviderUnavailable("provider scope is blank")
        registration = ProviderRegistration.objects.filter(tenant_id=tenant_id, operation=operation, active=True).first()
        if registration is None or not isinstance(registration.configuration, dict) or not registration.provider_name:
            raise ProviderUnavailable("no active durable provider registration")
        provider = cls._providers.get(ProviderKey(tenant_id, operation))
        if provider is None:
            raise ProviderUnavailable("registered provider is not available in this worker")
        return provider

    @classmethod
    def submit_or_poll(cls, attempt_id: str) -> ProviderResult:
        token = secrets.token_urlsafe(32)
        with transaction.atomic():
            attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
            if not attempt.tenant_id or attempt.is_terminal or attempt.status == PaymentAttempt.STATUS_REVIEW:
                return ProviderResult(ProviderOutcome.UNCERTAIN, code="NOOP_TERMINAL")
            now = timezone.now()
            if attempt.claim_token and attempt.claim_expires_at and attempt.claim_expires_at > now:
                return ProviderResult(ProviderOutcome.UNCERTAIN, code="CLAIMED")
            provider = cls.resolve(tenant_id=attempt.tenant_id, operation=attempt.operation)
            attempt.claim_token = token
            attempt.claim_generation += 1
            attempt.claim_expires_at = now + timedelta(minutes=5)
            attempt.attempt_count += 1
            attempt.save(update_fields=["claim_token", "claim_generation", "claim_expires_at", "attempt_count", "updated_at"])
            generation = attempt.claim_generation
            should_poll = attempt.status == PaymentAttempt.STATUS_UNCERTAIN
            request_id, provider_attempt_id = attempt.request_id, attempt.provider_attempt_id
            external_transaction_id = attempt.external_transaction_id
            payload = {"request_id": request_id, "amount": str(attempt.amount or ""), "currency": attempt.currency}
        try:
            result = provider.get_status(request_id=request_id, provider_attempt_id=provider_attempt_id, external_transaction_id=external_transaction_id) if should_poll else provider.submit(request_id=request_id, payment=payload)
        except Exception:
            result = ProviderResult(ProviderOutcome.NETWORK, provider_attempt_id=provider_attempt_id, external_transaction_id=external_transaction_id, code="PROVIDER_RUNTIME_ERROR", message="Provider invocation failed; authoritative status is required.", observation_id=f"runtime-error:{attempt_id}:{generation}", event_id=f"runtime-error:{attempt_id}:{generation}")
        if not isinstance(result, ProviderResult):
            raise TypeError("provider must return ProviderResult")
        with transaction.atomic():
            attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
            if attempt.claim_token != token or attempt.claim_generation != generation:
                return ProviderResult(ProviderOutcome.UNCERTAIN, code="STALE_CLAIM")
            PaymentLifecycleService.record_provider_result(attempt, result)
            attempt.claim_token = ""
            attempt.claim_expires_at = None
            attempt.save(update_fields=["claim_token", "claim_expires_at", "updated_at"])
        return result

    @classmethod
    def unavailable(cls, attempt_id: str) -> ProviderResult:
        with transaction.atomic():
            attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
            observation_id = f"configuration-unavailable:{attempt.pk}"
            result = ProviderResult(ProviderOutcome.UNCERTAIN, code="PROVIDER_UNAVAILABLE", message="Provider configuration is unavailable", observation_id=observation_id, event_id=observation_id, verification_method="configuration")
            if not attempt.is_terminal and not ProviderObservation.objects.filter(observation_kind=ProviderObservation.KIND_PROVIDER, observation_id=observation_id).exists():
                PaymentLifecycleService.record_provider_result(attempt, result)
            return result


def orchestrate_attempt(attempt_id: str) -> ProviderResult:
    try:
        return ProviderRuntime.submit_or_poll(attempt_id)
    except ProviderUnavailable:
        return ProviderRuntime.unavailable(attempt_id)


def enqueue_attempt(attempt: PaymentAttempt) -> Any:
    from django.db import transaction as db_transaction
    from .provider_runtime_tasks import orchestrate_attempt_task
    db_transaction.on_commit(lambda: orchestrate_attempt_task.delay(str(attempt.pk)))
    return attempt


__all__ = ["ProviderRuntime", "ProviderUnavailable", "ProviderKey", "enqueue_attempt", "orchestrate_attempt"]
