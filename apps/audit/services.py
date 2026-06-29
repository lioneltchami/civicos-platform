"""
Audit log service layer.

All audit log writes go through this module. Never write AuditLogEntry
directly from views or signal handlers — always call these service functions
so that chain-of-custody hashing, PII redaction, and error handling are
applied consistently.
"""

import logging
from typing import Any

from django.db import transaction

from .models import AuditEventType, AuditLogEntry

logger = logging.getLogger(__name__)


def record_event(
    *,
    event_type: str,
    outcome: str = "success",
    actor_id: str | None = None,
    actor_email: str = "",
    actor_ip: str | None = None,
    actor_user_agent: str = "",
    resource_type: str = "",
    resource_id: str = "",
    before_state: dict | None = None,
    after_state: dict | None = None,
    event_detail: dict | None = None,
    request_id: str = "",
    session_id: str = "",
) -> AuditLogEntry | None:
    """
    Create a single audit log entry.

    Returns the created entry, or None if creation failed (errors are logged
    but not re-raised — audit failures must never break the main request).

    Thread safety: uses select_for_update on the last entry to compute the
    prev_hash safely under concurrent writes.
    """
    try:
        with transaction.atomic():
            # Get the hash of the most recent entry for chain linkage
            last_entry = (
                AuditLogEntry.objects.select_for_update()
                .order_by("-timestamp")
                .values("entry_hash")
                .first()
            )
            prev_hash = last_entry["entry_hash"] if last_entry else ""

            entry = AuditLogEntry(
                event_type=event_type,
                outcome=outcome,
                actor_id=actor_id,
                actor_email=actor_email,
                actor_ip=actor_ip,
                actor_user_agent=actor_user_agent,
                resource_type=resource_type,
                resource_id=str(resource_id),
                before_state=before_state,
                after_state=after_state,
                event_detail=event_detail or {},
                request_id=request_id,
                session_id=session_id,
                prev_hash=prev_hash,
            )
            entry.save()
            return entry

    except Exception:
        # Audit failures must never propagate — log and continue
        logger.exception(
            "Failed to write audit log entry",
            extra={"event_type": event_type, "actor_id": actor_id},
        )
        return None


def record_event_from_request(
    request,
    *,
    event_type: str,
    outcome: str = "success",
    resource_type: str = "",
    resource_id: Any = "",
    before_state: dict | None = None,
    after_state: dict | None = None,
    event_detail: dict | None = None,
) -> AuditLogEntry | None:
    """
    Convenience wrapper that extracts actor context from a Django request.

    Use this in views and signal handlers when a request object is available.
    """
    audit_ctx = getattr(request, "audit_context", {})
    user = getattr(request, "user", None)

    return record_event(
        event_type=event_type,
        outcome=outcome,
        actor_id=audit_ctx.get("actor_id") or (str(user.pk) if user and user.is_authenticated else None),
        actor_email=user.email if user and user.is_authenticated else "",
        actor_ip=audit_ctx.get("actor_ip", ""),
        actor_user_agent=audit_ctx.get("actor_user_agent", ""),
        resource_type=resource_type,
        resource_id=str(resource_id),
        before_state=before_state,
        after_state=after_state,
        event_detail=event_detail,
        request_id=audit_ctx.get("request_id", ""),
        session_id=request.session.session_key or "" if hasattr(request, "session") else "",
    )
