"""Durable, status-first provider execution boundary."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from importlib import import_module
from datetime import timedelta
from typing import Any, Mapping

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
    """Workers resolve adapters exclusively from durable registration config."""

    @staticmethod
    def _serialize_result(result: ProviderResult) -> dict[str, Any]:
        """Persist only bounded deterministic-test fixture data, never raw provider data."""
        return {
            "outcome": str(result.outcome),
            "provider_attempt_id": result.provider_attempt_id[:100],
            "external_transaction_id": result.external_transaction_id[:100],
            "code": result.code[:50],
            "message": result.message[:255],
            "retryable": bool(result.retryable),
            "observation_id": result.observation_id[:160],
            "event_id": result.event_id[:160],
            "verified": bool(result.verified),
            "verification_method": result.verification_method[:80],
        }

    @staticmethod
    def _deserialize_result(value: Mapping[str, Any]) -> ProviderResult:
        return ProviderResult(
            ProviderOutcome(str(value.get("outcome", ProviderOutcome.UNCERTAIN))),
            provider_attempt_id=str(value.get("provider_attempt_id", "")),
            external_transaction_id=str(value.get("external_transaction_id", "")),
            code=str(value.get("code", "")),
            message=str(value.get("message", "")),
            retryable=bool(value.get("retryable", False)),
            observation_id=str(value.get("observation_id", "")),
            event_id=str(value.get("event_id", "")),
            verified=bool(value.get("verified", False)),
            verification_method=str(value.get("verification_method", "")),
        )

    @classmethod
    def configure(cls, *, tenant_id: str, operation: str, provider: PaymentProvider) -> ProviderRegistration:
        """Register only a durable, worker-resolvable adapter factory configuration.

        ``configure`` exists for local deterministic tests. Production registration
        must be created by controlled configuration management, never by a request.
        """
        tenant_id, operation = (tenant_id or "").strip(), (operation or "").strip()
        if not tenant_id or not operation or not isinstance(provider, PaymentProvider):
            raise ValueError("tenant_id, operation, and provider are required")
        provider_type = type(provider)
        path = f"{provider_type.__module__}.{provider_type.__qualname__}"
        if "<locals>" in path:
            raise ValueError("provider must be an importable durable factory")
        configuration: dict[str, Any] = {"adapter_path": path}
        # The local deterministic adapter is the only supported fixture type.
        # Serialize bounded result metadata so the worker can construct a fresh
        # adapter without sharing web-process memory.
        if path == "apps.payments.providers.deterministic.DeterministicProvider":
            configuration["factory_kwargs"] = {
                "outcomes": [cls._serialize_result(item) for item in getattr(provider, "_outcomes", ())],
                "statuses": {str(key): cls._serialize_result(item) for key, item in getattr(provider, "_statuses", {}).items()},
            }
        with transaction.atomic():
            registration, _ = ProviderRegistration.objects.update_or_create(
                tenant_id=tenant_id, operation=operation,
                defaults={
                    "provider_name": provider_type.__name__[:80],
                    "configuration_version": "durable-factory-v3",
                    "configuration": configuration,
                    "active": True,
                },
            )
        return registration

    @classmethod
    def clear(cls) -> None:
        return None

    @classmethod
    def resolve(cls, *, tenant_id: str, operation: str) -> PaymentProvider:
        if not tenant_id or not operation:
            raise ProviderUnavailable("provider scope is blank")
        registrations = list(ProviderRegistration.objects.filter(tenant_id=tenant_id, operation=operation, active=True))
        if len(registrations) != 1:
            raise ProviderUnavailable("provider registration is missing or ambiguous")
        registration = registrations[0]
        config = registration.configuration
        path = config.get("adapter_path") if isinstance(config, dict) else None
        if not isinstance(path, str) or not path or path.count(".") < 1:
            raise ProviderUnavailable("durable provider factory is malformed")
        try:
            module_name, attr = path.rsplit(".", 1)
            factory = getattr(import_module(module_name), attr)
            kwargs = config.get("factory_kwargs", {})
            if not isinstance(kwargs, dict):
                raise ValueError("durable provider factory arguments are malformed")
            if path == "apps.payments.providers.deterministic.DeterministicProvider":
                kwargs = {
                    "outcomes": [cls._deserialize_result(item) for item in kwargs.get("outcomes", []) if isinstance(item, dict)],
                    "statuses": {str(key): cls._deserialize_result(item) for key, item in kwargs.get("statuses", {}).items() if isinstance(item, dict)},
                }
            elif kwargs:
                raise ValueError("provider factory arguments are not supported")
            provider = factory(**kwargs) if callable(factory) else None
        except (ImportError, AttributeError, TypeError, ValueError) as exc:
            raise ProviderUnavailable("durable provider adapter cannot be resolved") from exc
        if not isinstance(provider, PaymentProvider):
            raise ProviderUnavailable("durable provider adapter has invalid type")
        return provider

    @classmethod
    def status_first_recovery(cls, attempt_id: str) -> ProviderResult:
        with transaction.atomic():
            attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
            if attempt.is_terminal or attempt.status == PaymentAttempt.STATUS_REVIEW:
                return ProviderResult(ProviderOutcome.UNCERTAIN, code="NOOP_TERMINAL")
            attempt.status = PaymentAttempt.STATUS_UNCERTAIN
            attempt.save(update_fields=["status", "updated_at"])
        return cls.submit_or_poll(str(attempt_id))

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
            generation = attempt.claim_generation + 1
            should_poll = attempt.status == PaymentAttempt.STATUS_UNCERTAIN
            intent = {"kind": "poll" if should_poll else "submit", "request_id": attempt.request_id, "generation": generation}
            attempt.claim_token, attempt.claim_generation = token, generation
            attempt.claim_expires_at = now + timedelta(minutes=5)
            attempt.claim_heartbeat_at = now
            attempt.submission_intent = intent
            attempt.attempt_count += 1
            attempt.save(update_fields=["claim_token", "claim_generation", "claim_expires_at", "claim_heartbeat_at", "submission_intent", "attempt_count", "updated_at"])
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
            attempt.claim_heartbeat_at = None
            attempt.submission_intent = {}
            attempt.save(update_fields=["claim_token", "claim_expires_at", "claim_heartbeat_at", "submission_intent", "updated_at"])
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
    if not attempt_id:
        raise ValueError("admitted attempt ID is required")
    try:
        return ProviderRuntime.submit_or_poll(str(attempt_id))
    except ProviderUnavailable:
        return ProviderRuntime.unavailable(attempt_id)


def enqueue_attempt(attempt: PaymentAttempt) -> Any:
    from django.db import transaction as db_transaction
    from .provider_runtime_tasks import orchestrate_attempt_task
    db_transaction.on_commit(lambda: orchestrate_attempt_task.delay(str(attempt.pk)))
    return attempt


__all__ = ["ProviderRuntime", "ProviderUnavailable", "ProviderKey", "enqueue_attempt", "orchestrate_attempt"]
