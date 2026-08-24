"""Durable immutable execution identity reserved before provider I/O."""

from __future__ import annotations

import hashlib
import json

from django.db import IntegrityError, transaction

from .govstack_models import PaymentAttempt, PaymentExecutionIntent


class ExecutionIntentConflict(ValueError):  # noqa: N818
    """Raised when a canonical execution identity is reused inconsistently."""


def payload_fingerprint(payload) -> str:  # noqa: ANN001
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def reserve_execution_intent(
    *,
    attempt: PaymentAttempt,
    scope: str,
    operation: str,
    request_identity: str,
    payload,  # noqa: ANN001
) -> PaymentExecutionIntent:
    """Reserve one immutable identity before any worker/provider action."""
    fingerprint = payload_fingerprint(payload)
    with transaction.atomic():
        try:
            with transaction.atomic():
                intent = PaymentExecutionIntent.objects.create(
                    attempt=attempt,
                    scope=scope,
                    operation=operation,
                    request_identity=request_identity,
                    payload_fingerprint=fingerprint,
                )
        except IntegrityError:
            intent = PaymentExecutionIntent.objects.select_for_update().get(
                scope=scope,
                operation=operation,
                request_identity=request_identity,
            )
        if intent.payload_fingerprint != fingerprint or intent.attempt_id != attempt.pk:
            raise ExecutionIntentConflict("conflicting canonical execution identity")
        return intent


def record_provider_correlation(
    intent: PaymentExecutionIntent, correlation: str
) -> PaymentExecutionIntent:
    """Record exactly one provider correlation; later conflicting values fail closed."""
    correlation = (correlation or "").strip()
    if not correlation:
        return intent
    with transaction.atomic():
        locked = PaymentExecutionIntent.objects.select_for_update().get(pk=intent.pk)
        if locked.provider_correlation and locked.provider_correlation != correlation:
            raise ExecutionIntentConflict("provider correlation is immutable")
        if not locked.provider_correlation:
            locked.provider_correlation = correlation
            locked.state = PaymentExecutionIntent.STATE_CORRELATED
            locked.save(update_fields=["provider_correlation", "state", "updated_at"])
        return locked
