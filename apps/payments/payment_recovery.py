"""Canonical internal entry point for durable payment-attempt recovery.

This boundary deliberately delegates provider I/O to the existing worker-only
runtime. Subsequent increments must move every retry/replay/reconciliation
caller through this command and prove token/generation fencing end to end.
"""
from __future__ import annotations

from .govstack_models import PaymentAttempt
from .govstack_provider import ProviderOutcome, ProviderResult
from .provider_runtime import ProviderRuntime


class RecoverPaymentAttemptCommand:
    """Recover one durable attempt without allowing request-path provider I/O."""

    @classmethod
    def execute(cls, attempt_id: str) -> ProviderResult:
        if not attempt_id:
            raise ValueError("admitted attempt ID is required")
        attempt = PaymentAttempt.objects.get(pk=attempt_id)
        if attempt.is_terminal or attempt.status == PaymentAttempt.STATUS_REVIEW:
            return ProviderResult(ProviderOutcome.UNCERTAIN, code="NOOP_TERMINAL")
        if attempt.status == PaymentAttempt.STATUS_UNCERTAIN:
            return ProviderRuntime.status_first_recovery(str(attempt.pk))
        return ProviderRuntime.submit_or_poll(str(attempt.pk))
