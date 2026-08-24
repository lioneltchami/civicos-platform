from __future__ import annotations

from celery import shared_task

from .models import PaymentCommandOutbox
from .payment_command_consumer import acknowledge_reserved_command


@shared_task(
    bind=False,
    name="payments.consume_bound_command_outbox",
    queue="payments",
    acks_late=True,
    reject_on_worker_lost=True,
)
def consume_bound_command_outbox_task(outbox_id: str):  # noqa: ANN201
    if not outbox_id:
        raise ValueError("bound payment outbox ID is required")
    outbox = PaymentCommandOutbox.objects.select_related("command", "command__attempt").get(
        pk=outbox_id
    )
    command = outbox.command
    attempt = command.attempt
    if attempt is None:
        return {"acknowledged": False, "reason": "missing_attempt"}
    delivery = {
        "command_id": str(command.pk),
        "attempt_id": str(attempt.pk),
        "tenant_id": command.tenant_id,
        "operation": command.operation,
        "fingerprint": command.fingerprint,
    }
    return acknowledge_reserved_command(outbox_id=outbox_id, delivery=delivery)
