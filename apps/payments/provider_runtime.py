"""Durable, status-first provider execution boundary."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Mapping

from django.db import transaction
from django.utils import timezone

from .govstack_failure_services import PaymentLifecycleService
from .govstack_models import PaymentAttempt, PaymentExecutionIntent, ProviderObservation, ProviderRegistration
from .govstack_provider import PaymentProvider, ProviderOutcome, ProviderResult
from .provider_registry import (
    FACTORY_DETERMINISTIC,
    SCHEMA_VERSION,
    ProviderRegistryError,
    resolve_provider,
)


class ProviderUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderKey:
    tenant_id: str
    operation: str


class ProviderRuntime:
    """Workers resolve adapters exclusively from durable registration config."""

    @staticmethod
    def worker_test_seam(*, phase: str, attempt_id: str, generation: int) -> None:
        """No-op production seam used only by deterministic worker-race tests."""
        return None

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
        if path != "apps.payments.providers.deterministic.DeterministicProvider":
            raise ValueError("provider type is not allowlisted")
        # The deterministic adapter is deliberately the sole local fixture.
        # Persist bounded result metadata so a fresh worker does not share
        # request-process state or execute arbitrary import paths.
        configuration: dict[str, Any] = {
            "outcomes": [cls._serialize_result(item) for item in getattr(provider, "_outcomes", ())],
            "statuses": {str(key): cls._serialize_result(item) for key, item in getattr(provider, "_statuses", {}).items()},
        }
        with transaction.atomic():
            registration, _ = ProviderRegistration.objects.update_or_create(
                tenant_id=tenant_id, operation=operation,
                defaults={
                    "provider_name": provider_type.__name__[:80],
                    "factory_key": FACTORY_DETERMINISTIC,
                    "schema_version": SCHEMA_VERSION,
                    "configuration_version": "allowlisted-registry-v1",
                    "configuration": configuration,
                    "audit_metadata": {"source": "local-deterministic-fixture"},
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
        try:
            return resolve_provider(tenant_id=tenant_id, operation=operation)
        except ProviderRegistryError as exc:
            raise ProviderUnavailable("durable provider adapter cannot be resolved") from exc

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
            execution_intent = PaymentExecutionIntent.objects.select_for_update().filter(
                attempt_id=attempt.pk
            ).first()
            durable_correlation = execution_intent.provider_correlation if execution_intent else ""
            evidence = attempt.recovery_evidence or {}
            # Reservation alone is not evidence of provider acceptance. Poll only
            # where a durable provider identifier or ambiguous provider outcome exists.
            should_poll = bool(
                durable_correlation
                or attempt.provider_attempt_id
                or attempt.external_transaction_id
                or evidence.get("ambiguous_outcome")
            )
            if execution_intent is None:
                execution_intent = PaymentExecutionIntent.objects.create(
                    attempt=attempt,
                    scope=attempt.tenant_id,
                    operation=attempt.operation,
                    request_identity=attempt.request_id,
                    payload_fingerprint=attempt.payload_fingerprint,
                )
            # A committed submit admission is fail-closed evidence that an
            # external call may have happened. A takeover must poll, not submit.
            if not should_poll and execution_intent.submit_started_at:
                should_poll = True
            elif not should_poll:
                execution_intent.submit_started_at = now
                execution_intent.submit_admission_generation = generation
                execution_intent.save(
                    update_fields=[
                        "submit_started_at",
                        "submit_admission_generation",
                        "updated_at",
                    ]
                )
            intent = {"kind": "poll" if should_poll else "submit", "request_id": attempt.request_id, "generation": generation}
            attempt.claim_token, attempt.claim_generation = token, generation
            attempt.claim_expires_at = now + timedelta(minutes=5)
            attempt.claim_heartbeat_at = now
            attempt.submission_intent = intent
            attempt.attempt_count += 1
            attempt.save(update_fields=["claim_token", "claim_generation", "claim_expires_at", "claim_heartbeat_at", "submission_intent", "attempt_count", "updated_at"])
            request_id, provider_attempt_id = attempt.request_id, attempt.provider_attempt_id
            external_transaction_id = durable_correlation or attempt.external_transaction_id
            payload = {"request_id": request_id, "amount": str(attempt.amount or ""), "currency": attempt.currency}
        try:
            result = provider.get_status(request_id=request_id, provider_attempt_id=provider_attempt_id, external_transaction_id=external_transaction_id) if should_poll else provider.submit(request_id=request_id, payment=payload)
        except Exception:
            result = ProviderResult(ProviderOutcome.NETWORK, provider_attempt_id=provider_attempt_id, external_transaction_id=external_transaction_id, code="PROVIDER_RUNTIME_ERROR", message="Provider invocation failed; authoritative status is required.", observation_id=f"runtime-error:{attempt_id}:{generation}", event_id=f"runtime-error:{attempt_id}:{generation}")
        if not isinstance(result, ProviderResult):
            raise TypeError("provider must return ProviderResult")
        cls.worker_test_seam(
            phase="after_provider_call",
            attempt_id=str(attempt_id),
            generation=generation,
        )
        return cls.finalize_claimed_result(
            attempt_id=str(attempt_id), token=token, generation=generation, result=result
        )

    @classmethod
    def finalize_claimed_result(
        cls, *, attempt_id: str, token: str, generation: int, result: ProviderResult
    ) -> ProviderResult:
        """Persist a provider result only for the current fenced claim."""
        with transaction.atomic():
            attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
            if attempt.claim_token != token or attempt.claim_generation != generation:
                return ProviderResult(ProviderOutcome.UNCERTAIN, code="STALE_CLAIM")
            PaymentLifecycleService.record_provider_result(attempt, result)
            if result.outcome in (ProviderOutcome.NETWORK, ProviderOutcome.TIMEOUT, ProviderOutcome.UNCERTAIN):
                attempt.recovery_evidence = {
                    "ambiguous_outcome": True,
                    "code": result.code[:50],
                    "provider_attempt_id": result.provider_attempt_id[:100],
                    "external_transaction_id": result.external_transaction_id[:100],
                }
            elif result.provider_attempt_id or result.external_transaction_id:
                attempt.recovery_evidence = {
                    "provider_attempt_id": result.provider_attempt_id[:100],
                    "external_transaction_id": result.external_transaction_id[:100],
                }
            attempt.claim_token = ""
            attempt.claim_expires_at = None
            attempt.claim_heartbeat_at = None
            attempt.submission_intent = {}
            attempt.save(update_fields=["claim_token", "claim_expires_at", "claim_heartbeat_at", "submission_intent", "recovery_evidence", "updated_at"])
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
    from .payment_recovery import RecoverPaymentAttemptCommand

    try:
        return RecoverPaymentAttemptCommand.execute(str(attempt_id))
    except ProviderUnavailable:
        return ProviderRuntime.unavailable(attempt_id)


def enqueue_attempt(attempt: PaymentAttempt) -> Any:
    from django.db import transaction as db_transaction
    from .provider_runtime_tasks import orchestrate_attempt_task
    db_transaction.on_commit(lambda: orchestrate_attempt_task.delay(str(attempt.pk)))
    return attempt


__all__ = ["ProviderRuntime", "ProviderUnavailable", "ProviderKey", "enqueue_attempt", "orchestrate_attempt"]
