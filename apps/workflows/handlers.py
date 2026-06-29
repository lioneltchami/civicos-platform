"""
Signal handlers for the workflows building block.

Two categories:
1. Portal → Workflows: create a WorkItem when a ServiceRequest is submitted.
2. Workflows → Audit: write an audit entry for every WorkItem state change.

Handlers are connected explicitly in WorkflowsConfig.ready() — no @receiver
decorators are used here. This prevents the double-connection bug that occurs
when something imports this module before ready() is called: @receiver fires
at import time AND again when ready() re-imports the module.
"""

import logging

from apps.audit.services import record_event
from apps.workflows.models import WorkItem, WorkItemPriority

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Portal → Workflows integration
# ---------------------------------------------------------------------------

def on_service_request_submitted(sender, instance, actor=None, **kwargs) -> None:
    """
    Automatically create a WorkItem when a ServiceRequest is submitted.

    Fired by portal.services.create_service_request() via transaction.on_commit()
    so the ServiceRequest is fully committed before the WorkItem is created.

    actor is the citizen who submitted; WorkItem is created as a system action
    (actor=None in history) because citizens are not workflow staff actors.
    """
    create_work_item_for_service_request(instance, actor=None)


def create_work_item_for_service_request(service_request, actor=None) -> None:
    """
    Create a WorkItem for a ServiceRequest.

    Called from portal's service_request_submitted handler (not a signal receiver
    directly — imported and invoked by the portal signals module to avoid
    circular imports at module load time).
    """
    # Import here to avoid circular dependency at module level
    from apps.workflows.services import create_work_item

    # Determine priority from service request urgency (default Normal)
    priority = WorkItemPriority.NORMAL

    try:
        create_work_item(
            service_request,
            title=f"[{service_request.reference_number}] {service_request.service_name}",
            actor=actor,
            description=(
                f"Service request submitted by citizen. "
                f"Reference: {service_request.reference_number}"
            ),
            priority=priority,
        )
    except Exception:
        # Never let workflow creation break the portal submission
        logger.exception(
            "Failed to create work item for service_request_id=%s",
            service_request.pk,
        )


# ---------------------------------------------------------------------------
# Workflows → Audit
# ---------------------------------------------------------------------------

def _record_work_item_event(
    *,
    event_type: str,
    work_item: WorkItem,
    actor,
    event_detail: dict | None = None,
) -> None:
    """Write a workflow audit event using the shared audit service contract."""
    try:
        record_event(
            event_type=event_type,
            outcome="success",
            actor_id=str(actor.pk) if actor and getattr(actor, "pk", None) else None,
            actor_email="",
            resource_type="workflows.WorkItem",
            resource_id=str(work_item.pk),
            event_detail=event_detail or {},
        )
    except Exception:
        logger.exception(
            "Audit failed for %s work_item_id=%s",
            event_type,
            work_item.pk,
        )


def audit_work_item_created(sender, work_item: WorkItem, actor, **kwargs) -> None:
    _record_work_item_event(
        event_type="workflows.work_item.created",
        work_item=work_item,
        actor=actor,
        event_detail={"title": work_item.title, "priority": work_item.priority},
    )


def audit_work_item_assigned(sender, work_item: WorkItem, assignee, actor, **kwargs) -> None:
    _record_work_item_event(
        event_type="workflows.work_item.assigned",
        work_item=work_item,
        actor=actor,
        event_detail={"assignee_id": str(assignee.pk) if assignee else None},
    )


def audit_work_item_status_changed(
    sender, work_item: WorkItem, old_status, new_status, actor, notes, **kwargs
) -> None:
    _record_work_item_event(
        event_type="workflows.work_item.status_changed",
        work_item=work_item,
        actor=actor,
        event_detail={
            "old_status": old_status,
            "new_status": new_status,
            "notes": notes[:500] if notes else "",
        },
    )


def audit_work_item_escalated(
    sender, work_item: WorkItem, level, actor, reason, **kwargs
) -> None:
    _record_work_item_event(
        event_type="workflows.work_item.escalated",
        work_item=work_item,
        actor=actor,
        event_detail={
            "escalation_level": level,
            "reason": reason[:500] if reason else "",
        },
    )
