"""Strict Item 02 request scope, command reservation, and outbox primitives.

This module deliberately does not invoke payment providers. A later worker-only
runtime step consumes published command records after canonical admission.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.payments.govstack_models import GovStackRegisteredBB
from apps.payments.models import PaymentCommand, PaymentCommandOutbox


class PaymentScopeDenied(PermissionError):
    """Raised before any command, task, or provider side effect is created."""


class PaymentIdempotencyConflict(ValueError):
    """Raised when a request identity is reused for a changed payload."""


@dataclass(frozen=True)
class PaymentScope:
    """Immutable caller and tenant authority after registry verification."""

    caller_bb_id: str
    tenant_id: str


def _header_tenant(request: Any) -> str:
    return (
        request.headers.get("X-Platform-TenantId")
        or request.headers.get("Platform-TenantId")
        or ""
    ).strip()


def resolve_registered_bb_scope(request: Any) -> PaymentScope:
    """Resolve an active caller and tenant mapping in production mode.

    The caller identity is populated only by the existing registered-BB
    permission. The tenant header is a claim, never authority: it must be in
    the caller's explicit nonempty allowlist. Harness mode is intentionally
    isolated and marked by synthetic values because it cannot establish a
    production tenant authority.
    """
    production = bool(getattr(settings, "GOVSTACK_REQUIRE_REGISTERED_BB", False))
    caller = (request.META.get("_gs_payer_identity") or "").strip()
    tenant = _header_tenant(request)

    if not production:
        request_id = str(getattr(request, "data", {}).get("RequestID", "")).strip()
        if not request_id:
            raise PaymentScopeDenied("canonical request identity is required")
        return PaymentScope(caller_bb_id="__harness__", tenant_id="__harness__")

    if not caller or not tenant:
        raise PaymentScopeDenied("registered caller and platform tenant are required")

    registration = GovStackRegisteredBB.objects.filter(
        bb_id=caller,
        is_active=True,
    ).only("allowed_platform_tenant_ids").first()
    allowed = list(registration.allowed_platform_tenant_ids or []) if registration else []
    if not allowed or tenant not in allowed:
        raise PaymentScopeDenied("caller is not authorised for the declared platform tenant")

    return PaymentScope(caller_bb_id=caller, tenant_id=tenant)


def json_safe_payload(payload: Any) -> Any:
    """Canonical JSON-compatible representation for durable command storage."""
    return json.loads(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str))


def canonical_fingerprint(payload: Any) -> str:
    value = json.dumps(json_safe_payload(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PaymentCommandService:
    """Reserve one immutable command and one post-commit outbox record."""

    @classmethod
    def admit(
        cls,
        *,
        request: Any,
        operation: str,
        request_identity: str,
        payload: dict[str, Any],
    ) -> tuple[PaymentCommand, bool]:
        """Canonical request boundary: trusted scope, then durable reservation."""
        return cls.reserve(
            scope=resolve_registered_bb_scope(request),
            operation=operation,
            request_identity=request_identity,
            payload=payload,
        )

    @staticmethod
    def reserve(*, scope: PaymentScope, operation: str, request_identity: str, payload: dict[str, Any]) -> tuple[PaymentCommand, bool]:
        operation = (operation or "").strip()
        request_identity = (request_identity or "").strip()
        if not operation or not request_identity:
            raise PaymentScopeDenied("operation and canonical request identity are required")

        stored_payload = json_safe_payload(payload)
        fingerprint = canonical_fingerprint(stored_payload)
        with transaction.atomic():
            try:
                with transaction.atomic():
                    command = PaymentCommand.objects.create(
                        tenant_id=scope.tenant_id,
                        caller_bb_id=scope.caller_bb_id,
                        operation=operation,
                        request_identity=request_identity,
                        fingerprint=fingerprint,
                        payload=stored_payload,
                    )
                    outbox = PaymentCommandOutbox.objects.create(
                        command=command,
                        topic="payments.command.reserved",
                        payload={"command_id": str(command.id), "operation": operation},
                    )
            except IntegrityError:
                command = PaymentCommand.objects.select_for_update().get(
                    tenant_id=scope.tenant_id,
                    operation=operation,
                    request_identity=request_identity,
                )
                if command.fingerprint != fingerprint:
                    raise PaymentIdempotencyConflict("request identity was reused with a different payload")
                return command, True

            transaction.on_commit(lambda: PaymentCommandService.publish(outbox.id))
            return command, False

    @staticmethod
    def publish(outbox_id) -> None:
        """Mark the durable handoff published; worker routing is a later locked step."""
        updated = PaymentCommandOutbox.objects.filter(
            id=outbox_id,
            published_at__isnull=True,
        ).update(published_at=timezone.now())
        if updated:
            PaymentCommand.objects.filter(
                id=PaymentCommandOutbox.objects.values_list("command_id", flat=True).get(id=outbox_id)
            ).update(status=PaymentCommand.STATUS_DISPATCHED)
