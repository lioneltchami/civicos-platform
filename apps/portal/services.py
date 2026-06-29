"""
Portal service layer.

All mutations to ServiceRequest and StatusUpdate go through these functions.
Views are thin — they call services, never touch ORM directly for writes.
"""
from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .models import ServiceRequest, ServiceRequestStatus, StatusUpdate

logger = logging.getLogger(__name__)
User = get_user_model()


def create_service_request(
    citizen: Any,
    service_name: str,
    submission_data: dict,
    service_page_id: int | None = None,
    retention_days: int = 2555,
) -> ServiceRequest:
    """
    Create a new ServiceRequest for a citizen.

    Args:
        citizen: The authenticated user submitting the request.
        service_name: Human-readable name of the service (snapshot).
        submission_data: Form field values as a dict.
        service_page_id: Optional CMS ServicePage PK this request came from.
        retention_days: Days to retain submission data (default 7 years).

    Returns:
        The created ServiceRequest instance.

    Raises:
        ValueError: If citizen is anonymous.
    """
    if not hasattr(citizen, "pk") or citizen.pk is None:
        raise ValueError(
            "Cannot create service request for anonymous user. Use guest token flow."
        )

    with transaction.atomic():
        expires_at = timezone.now() + timezone.timedelta(days=retention_days)
        request = ServiceRequest.objects.create(
            citizen=citizen,
            service_name=service_name,
            service_page_id=service_page_id,
            submission_data=submission_data,
            status=ServiceRequestStatus.SUBMITTED,
            expires_at=expires_at,
        )
        # Write initial status update (old_status is blank — no previous state)
        StatusUpdate.objects.create(
            service_request=request,
            old_status="",
            new_status=ServiceRequestStatus.SUBMITTED,
            changed_by=citizen,
            public_note=(
                "Your request has been received and will be reviewed shortly. / "
                "Votre demande a été reçue et sera examinée prochainement."
            ),
        )
        transaction.on_commit(
            lambda: _fire_notification(request, ServiceRequestStatus.SUBMITTED)
        )
        transaction.on_commit(
            lambda: _write_audit(request, "workflow.submission.received", citizen)
        )

    return request


def update_request_status(
    service_request: ServiceRequest,
    new_status: str,
    changed_by: Any,
    public_note: str = "",
) -> StatusUpdate:
    """
    Transition a ServiceRequest to a new status.

    Creates an immutable StatusUpdate record and fires notifications.

    Args:
        service_request: The ServiceRequest to update.
        new_status: Target status (must be a valid ServiceRequestStatus value).
        changed_by: Staff user making the change (or system).
        public_note: Citizen-visible explanation of the change.

    Returns:
        The created StatusUpdate.

    Raises:
        ValueError: If new_status is invalid or the request is in a terminal state.
    """
    if new_status not in ServiceRequestStatus.values:
        raise ValueError(f"Invalid status: {new_status}")
    if service_request.status == new_status:
        raise ValueError(f"Request is already in status {new_status}")
    if service_request.status == ServiceRequestStatus.CLOSED:
        raise ValueError("Cannot transition from a closed request")
    if service_request.is_terminal() and new_status != ServiceRequestStatus.CLOSED:
        raise ValueError(
            f"Cannot transition from terminal status {service_request.status} "
            f"to {new_status}"
        )

    with transaction.atomic():
        old_status = service_request.status
        service_request.status = new_status
        service_request.save(update_fields=["status", "updated_at"])

        update = StatusUpdate.objects.create(
            service_request=service_request,
            old_status=old_status,
            new_status=new_status,
            changed_by=changed_by,
            public_note=public_note,
        )
        transaction.on_commit(
            lambda: _fire_notification(service_request, new_status)
        )
        _detail = {"old_status": old_status, "new_status": new_status}
        transaction.on_commit(
            lambda: _write_audit(service_request, "workflow.status.changed", changed_by, detail=_detail)
        )

    return update


def cancel_service_request(
    service_request: ServiceRequest,
    citizen: Any,
    reason: str = "",
) -> StatusUpdate:
    """
    Citizen-initiated cancellation of their own request.
    Only allowed if the request is not in a terminal state.
    """
    if service_request.citizen_id != citizen.pk:
        raise PermissionError("You can only cancel your own requests.")
    if not service_request.can_be_cancelled():
        raise ValueError(
            f"Request {service_request.reference_number} cannot be cancelled "
            f"(current status: {service_request.status})"
        )

    note = (
        "Request cancelled by citizen. / Demande annulée par le citoyen."
        + (f" Reason: {reason}" if reason else "")
    )
    return update_request_status(
        service_request=service_request,
        new_status=ServiceRequestStatus.CLOSED,
        changed_by=citizen,
        public_note=note,
    )


def get_citizen_requests(citizen: Any, status_filter: str | None = None):
    """
    Return a queryset of service requests for a citizen, optionally filtered by status.
    Always scoped to the citizen to prevent IDOR.
    """
    qs = ServiceRequest.objects.filter(citizen=citizen).order_by("-created_at")
    if status_filter and status_filter in ServiceRequestStatus.values:
        qs = qs.filter(status=status_filter)
    return qs.select_related("citizen").prefetch_related("status_updates")


# ---------------------------------------------------------------------------
# Internal helpers — must never raise exceptions into the caller's transaction
# ---------------------------------------------------------------------------


def _fire_notification(service_request: ServiceRequest, new_status: str) -> None:
    """Fire an async Celery notification task. Failure must never break the main flow."""
    try:
        from apps.notifications.tasks import send_status_update_notification

        send_status_update_notification.delay(
            citizen_id=str(service_request.citizen_id),
            request_reference=service_request.reference_number,
            new_status=new_status,
        )
    except Exception:
        logger.exception(
            "Failed to fire notification for request=%s status=%s",
            service_request.reference_number,
            new_status,
        )


def _write_audit(
    service_request: ServiceRequest,
    event_type: str,
    actor: Any,
    detail: dict | None = None,
) -> None:
    """
    Write an audit log entry.

    event_type must be a valid AuditEventType value (e.g. 'workflow.submission.received').
    Failure must never break the main flow.
    """
    try:
        from apps.audit.models import AuditLogEntry

        AuditLogEntry.objects.create(
            event_type=event_type,
            outcome="success",
            actor_id=str(actor.pk) if hasattr(actor, "pk") else "",
            actor_email=getattr(actor, "email", ""),
            actor_ip=None,
            actor_user_agent="",
            resource_type="portal.ServiceRequest",
            resource_id=str(service_request.pk),
            event_detail=detail or {},
            request_id="",
            session_id="",
            prev_hash="",
            # entry_hash is computed by AuditLogEntry.save() — do not pass it
        )
    except Exception:
        logger.exception("Audit log failed for event_type=%s", event_type)
