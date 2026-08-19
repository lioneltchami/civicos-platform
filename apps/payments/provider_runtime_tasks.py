"""Asynchronous provider-runtime task boundary."""
from __future__ import annotations

from celery import shared_task

from .provider_runtime import orchestrate_attempt


@shared_task(
    bind=False,
    name="payments.orchestrate_provider_attempt",
    queue="payments",
    acks_late=True,
    reject_on_worker_lost=True,
)
def orchestrate_attempt_task(attempt_id: str):
    return orchestrate_attempt(attempt_id)
