"""Asynchronous provider-runtime task boundary."""
from __future__ import annotations

from celery import shared_task

from .payment_recovery import RecoverPaymentAttemptCommand
from .provider_runtime import orchestrate_attempt


@shared_task(
    bind=False,
    name="payments.orchestrate_provider_attempt",
    queue="payments",
    acks_late=True,
    reject_on_worker_lost=True,
)
def orchestrate_attempt_task(attempt_id: str):
    if not attempt_id:
        raise ValueError("admitted attempt ID is required")
    return orchestrate_attempt(str(attempt_id))


@shared_task(
    bind=False,
    name="payments.recover_provider_attempt_status_first",
    queue="payments",
    acks_late=True,
    reject_on_worker_lost=True,
)
def recover_attempt_status_first_task(attempt_id: str):
    if not attempt_id:
        raise ValueError("admitted attempt ID is required")
    return RecoverPaymentAttemptCommand.execute(str(attempt_id))
