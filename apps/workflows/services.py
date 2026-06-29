"""
Workflow service layer for Govstack.

All WorkItem mutations pass through this module.  Views and signal
handlers import from here; they never call model.save() directly.

SLA policy (hours to resolution, measured from WorkItem.created_at):
  Priority 1 Critical →  4 h
  Priority 2 High     → 24 h
  Priority 3 Normal   → 120 h  (~5 business days)
  Priority 4 Low      → 240 h  (~10 business days)
"""

import logging
from datetime import timedelta
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from apps.workflows.models import (
    SLA_HOURS,
    TERMINAL_STATUSES,
    VALID_TRANSITIONS,
    WorkItem,
    WorkItemComment,
    WorkItemHistory,
    WorkItemPriority,
    WorkItemStatus,
)
from apps.workflows.signals import (
    work_item_assigned,
    work_item_commented,
    work_item_created,
    work_item_escalated,
    work_item_status_changed,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _record_history(
    work_item: WorkItem,
    action: str,
    actor,
    *,
    old_status: str = "",
    new_status: str = "",
    notes: str = "",
) -> WorkItemHistory:
    """Append an immutable history row."""
    return WorkItemHistory.objects.create(
        work_item=work_item,
        action=action,
        old_status=old_status,
        new_status=new_status,
        actor=actor,
        notes=notes,
    )


def _compute_due_at(priority: int) -> "timezone.datetime":
    hours = SLA_HOURS.get(priority, SLA_HOURS[WorkItemPriority.NORMAL])
    return timezone.now() + timedelta(hours=hours)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_work_item(
    content_object,
    *,
    title: str,
    actor,
    description: str = "",
    priority: int = WorkItemPriority.NORMAL,
    due_at=None,
) -> WorkItem:
    """
    Create a WorkItem linked to any content object (ServiceRequest, etc.).

    Computes due_at from SLA policy if not explicitly provided.
    Fires work_item_created signal after commit.
    """
    if not title:
        raise ValueError("title is required")
    if actor is None or getattr(actor, "pk", None) is None:
        raise ValueError("A valid staff actor is required to create a work item")

    ct = ContentType.objects.get_for_model(content_object.__class__)
    computed_due = due_at or _compute_due_at(priority)

    with transaction.atomic():
        work_item = WorkItem.objects.create(
            content_type=ct,
            object_id=str(content_object.pk),
            title=title,
            description=description,
            priority=priority,
            due_at=computed_due,
        )
        _record_history(work_item, "created", actor, notes=description)

    transaction.on_commit(
        lambda: work_item_created.send_robust(
            sender=WorkItem,
            work_item=work_item,
            actor=actor,
        )
    )
    return work_item


def claim_work_item(work_item: WorkItem, actor) -> WorkItem:
    """
    Staff member self-assigns an unassigned work item and moves it to IN_PROGRESS.

    Raises ValueError if already assigned or in a terminal status.
    """
    if not getattr(actor, "is_staff", False):
        raise PermissionError("Only staff users can claim work items.")
    if work_item.assigned_to_id is not None:
        raise ValueError(
            f"Work item '{work_item.title}' is already assigned to "
            f"{work_item.assigned_to.display_name}."
        )
    if work_item.status in TERMINAL_STATUSES:
        raise ValueError(f"Cannot claim a {work_item.status} work item.")

    old_status = work_item.status
    new_status = WorkItemStatus.IN_PROGRESS

    with transaction.atomic():
        work_item.assigned_to = actor
        work_item.status = new_status
        work_item.save(update_fields=["assigned_to", "status", "updated_at"])
        _record_history(
            work_item, "claimed", actor,
            old_status=old_status, new_status=new_status,
        )

    transaction.on_commit(lambda: _fire_claim_signals(work_item, actor, old_status, new_status))
    return work_item


def _fire_claim_signals(work_item, actor, old_status, new_status):
    work_item_assigned.send_robust(sender=WorkItem, work_item=work_item, assignee=actor, actor=actor)
    work_item_status_changed.send_robust(
        sender=WorkItem,
        work_item=work_item,
        old_status=old_status,
        new_status=new_status,
        actor=actor,
        notes="",
    )


def assign_work_item(work_item: WorkItem, assignee, actor) -> WorkItem:
    """
    Supervisor assigns a work item to a specific staff member.

    Does NOT change status (item may be pending or in_progress).
    Raises PermissionError if actor is not staff.
    """
    if not getattr(actor, "is_staff", False):
        raise PermissionError("Only staff users can assign work items.")
    if work_item.status in TERMINAL_STATUSES:
        raise ValueError(f"Cannot reassign a {work_item.status} work item.")
    if not getattr(assignee, "is_staff", False):
        raise ValueError("Work items may only be assigned to staff users.")

    with transaction.atomic():
        work_item.assigned_to = assignee
        work_item.save(update_fields=["assigned_to", "updated_at"])
        _record_history(
            work_item, "assigned", actor,
            notes=f"Assigned to {assignee.display_name}",
        )

    transaction.on_commit(
        lambda: work_item_assigned.send_robust(
            sender=WorkItem,
            work_item=work_item,
            assignee=assignee,
            actor=actor,
        )
    )
    return work_item


def update_work_item_status(
    work_item: WorkItem,
    new_status: str,
    actor,
    *,
    notes: str = "",
) -> WorkItem:
    """
    Advance (or regress) a WorkItem's status through the allowed state machine.

    Raises ValueError for invalid transitions.
    Sets completed_at when transitioning to COMPLETED or CANCELLED.
    Fires work_item_status_changed after commit.
    """
    if not getattr(actor, "is_staff", False):
        raise PermissionError("Only staff users can advance work item status.")
    if new_status not in WorkItemStatus.values:
        raise ValueError(f"'{new_status}' is not a valid WorkItemStatus.")
    if not work_item.can_transition_to(new_status):
        raise ValueError(
            f"Cannot transition from '{work_item.status}' to '{new_status}'."
        )

    old_status = work_item.status

    with transaction.atomic():
        work_item.status = new_status
        update_fields = ["status", "updated_at"]

        if new_status in TERMINAL_STATUSES and not work_item.completed_at:
            work_item.completed_at = timezone.now()
            update_fields.append("completed_at")

        work_item.save(update_fields=update_fields)
        _record_history(
            work_item, f"status_changed_to_{new_status}", actor,
            old_status=old_status, new_status=new_status, notes=notes,
        )

    transaction.on_commit(
        lambda: work_item_status_changed.send_robust(
            sender=WorkItem,
            work_item=work_item,
            old_status=old_status,
            new_status=new_status,
            actor=actor,
            notes=notes,
        )
    )
    return work_item


def escalate_work_item(work_item: WorkItem, actor, *, reason: str = "") -> WorkItem:
    """
    Increment escalation_level (max 2: supervisor → director).

    Sets escalated_at on first escalation.
    Fires work_item_escalated after commit.
    Raises ValueError if already at max escalation or in terminal status.
    """
    if not getattr(actor, "is_staff", False):
        raise PermissionError("Only staff users can escalate work items.")
    if work_item.status in TERMINAL_STATUSES:
        raise ValueError(f"Cannot escalate a {work_item.status} work item.")
    if work_item.escalation_level >= 2:
        raise ValueError("Work item is already at maximum escalation level (director).")

    now = timezone.now()
    with transaction.atomic():
        work_item.escalation_level += 1
        if not work_item.escalated_at:
            work_item.escalated_at = now
        work_item.save(update_fields=["escalation_level", "escalated_at", "updated_at"])
        _record_history(
            work_item, "escalated", actor,
            notes=reason or f"Escalated to level {work_item.escalation_level}",
        )

    level = work_item.escalation_level
    transaction.on_commit(
        lambda: work_item_escalated.send_robust(
            sender=WorkItem,
            work_item=work_item,
            level=level,
            actor=actor,
            reason=reason,
        )
    )
    return work_item


def add_comment(work_item: WorkItem, actor, body: str) -> WorkItemComment:
    """
    Add an internal staff comment to a WorkItem.

    Raises ValueError if body is empty or work item is terminal.
    """
    if not getattr(actor, "is_staff", False):
        raise PermissionError("Only staff users can comment on work items.")
    body = body.strip()
    if not body:
        raise ValueError("Comment body cannot be empty.")

    with transaction.atomic():
        comment = WorkItemComment.objects.create(
            work_item=work_item,
            author=actor,
            body=body,
        )
        _record_history(work_item, "commented", actor, notes=body[:200])

    transaction.on_commit(
        lambda: work_item_commented.send_robust(
            sender=WorkItem,
            work_item=work_item,
            comment=comment,
            actor=actor,
        )
    )
    return comment


def get_staff_queue(
    actor,
    *,
    status_filter: str | None = None,
    assigned_to_me: bool = False,
    unassigned_only: bool = False,
    priority_filter: int | None = None,
) -> "QuerySet[WorkItem]":
    """
    Return a filtered WorkItem queryset for the staff queue view.

    All staff see all items; filter options narrow the view.
    """
    qs = WorkItem.objects.select_related("assigned_to", "content_type").exclude(
        status__in=list(TERMINAL_STATUSES)
    )

    if status_filter and status_filter in WorkItemStatus.values:
        qs = qs.filter(status=status_filter)

    if assigned_to_me:
        qs = qs.filter(assigned_to=actor)

    if unassigned_only:
        qs = qs.filter(assigned_to__isnull=True)

    if priority_filter is not None:
        qs = qs.filter(priority=priority_filter)

    return qs.order_by("priority", "due_at", "created_at")


def check_sla_breaches() -> int:
    """
    Mark overdue work items as SLA-breached.

    Called by Celery periodic task. Returns the count of newly-breached items.
    Idempotent — items already marked are not updated again.
    """
    now = timezone.now()
    breached = WorkItem.objects.filter(
        due_at__lt=now,
        sla_breached_at__isnull=True,
    ).exclude(status__in=list(TERMINAL_STATUSES))

    count = 0
    for item in breached:
        item.sla_breached_at = now
        item.save(update_fields=["sla_breached_at", "updated_at"])
        WorkItemHistory.objects.create(
            work_item=item,
            action="sla_breached",
            actor=None,  # system action
            notes=f"SLA deadline {item.due_at:%Y-%m-%d %H:%M} passed without resolution.",
        )
        count += 1
        logger.warning(
            "SLA breached for work_item_id=%s title=%r priority=%s",
            item.pk, item.title, item.priority,
        )

    return count
