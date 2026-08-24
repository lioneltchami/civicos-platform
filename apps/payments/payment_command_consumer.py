"""Worker-only acknowledgement fence for a bound payment command outbox."""

from __future__ import annotations

import uuid
from typing import Any

from django.db import transaction
from django.utils import timezone

from .govstack_models import PaymentExecutionIntent
from .models import PaymentCommandOutbox

TOPIC = "payments.command.reserved"


class InvalidPaymentCommandDelivery(ValueError):  # noqa: N818
    """Raised when a delivered command cannot enter orchestration."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def acknowledge_reserved_command(*, outbox_id, delivery: dict[str, Any]) -> dict[str, Any]:  # noqa: ANN001
    """Acknowledge one valid bound delivery and schedule exactly one handoff."""
    command_id = _text(delivery.get("command_id"))
    attempt_id = _text(delivery.get("attempt_id"))
    tenant_id = _text(delivery.get("tenant_id"))
    operation = _text(delivery.get("operation"))
    fingerprint = _text(delivery.get("fingerprint"))
    token = _text(delivery.get("acknowledgement_token")) or str(uuid.uuid4())
    if not all((command_id, attempt_id, tenant_id, operation, fingerprint)):
        raise InvalidPaymentCommandDelivery("complete delivery identity is required")

    with transaction.atomic():
        outbox = (
            PaymentCommandOutbox.objects.select_for_update()
            .select_related("command", "command__attempt")
            .get(pk=outbox_id)
        )
        if outbox.acknowledged_at is not None:
            return dict(
                outbox.acknowledgement_result
                or {
                    "acknowledged": True,
                    "scheduled": False,
                    "attempt_id": str(outbox.command.attempt_id),
                }
            )

        command = outbox.command
        attempt = command.attempt
        if outbox.topic != TOPIC or _text(command.pk) != command_id:
            raise InvalidPaymentCommandDelivery("topic or command identity mismatch")
        if attempt is None or _text(attempt.pk) != attempt_id:
            raise InvalidPaymentCommandDelivery("attempt identity mismatch")
        if command.tenant_id != tenant_id or attempt.tenant_id != tenant_id:
            raise InvalidPaymentCommandDelivery("tenant mismatch")
        if command.operation != operation or attempt.operation != operation[:30]:
            raise InvalidPaymentCommandDelivery("operation mismatch")
        if command.fingerprint != fingerprint or attempt.payload_fingerprint != fingerprint:
            raise InvalidPaymentCommandDelivery("fingerprint mismatch")
        intent = (
            PaymentExecutionIntent.objects.select_for_update().filter(attempt_id=attempt.pk).first()
        )
        if (
            intent is None
            or intent.scope != tenant_id
            or intent.operation != operation[:30]
            or intent.payload_fingerprint != fingerprint
        ):
            raise InvalidPaymentCommandDelivery("execution intent mismatch")
        payload = outbox.payload or {}
        if (
            _text(payload.get("command_id")) != command_id
            or _text(payload.get("attempt_id")) != attempt_id
            or _text(payload.get("operation")) != operation
        ):
            raise InvalidPaymentCommandDelivery("outbox payload mismatch")

        result = {
            "acknowledged": True,
            "scheduled": True,
            "attempt_id": attempt_id,
            "acknowledgement_token": token,
        }
        outbox.acknowledged_at = timezone.now()
        outbox.acknowledgement_token = token
        outbox.acknowledgement_result = result
        outbox.save(
            update_fields=[
                "acknowledged_at",
                "acknowledgement_token",
                "acknowledgement_result",
                "updated_at",
            ]
        )
        transaction.on_commit(
            lambda admitted_attempt_id=attempt_id: _schedule_orchestration(admitted_attempt_id)
        )
    return result


def _schedule_orchestration(attempt_id: str) -> None:
    from .provider_runtime_tasks import orchestrate_attempt_task

    orchestrate_attempt_task.delay(attempt_id)
