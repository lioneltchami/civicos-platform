"""
Workflow service layer for CivicOS.

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

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from apps.workflows.models import (
    SLA_HOURS,
    TERMINAL_STATUSES,
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
    actor,  # noqa: ANN001
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


def _compute_due_at(priority: int):  # noqa: ANN202
    hours = SLA_HOURS.get(priority, SLA_HOURS[WorkItemPriority.NORMAL])
    return timezone.now() + timedelta(hours=hours)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_work_item(
    content_object,  # noqa: ANN001
    *,
    title: str,
    actor,  # noqa: ANN001
    description: str = "",
    priority: int = WorkItemPriority.NORMAL,
    due_at=None,  # noqa: ANN001
) -> WorkItem:
    """
    Create a WorkItem linked to any content object (ServiceRequest, etc.).

    Computes due_at from SLA policy if not explicitly provided.
    actor may be None for system-initiated items (portal signal handler).
    History entry records actor=None in that case — auditable and intentional.
    Fires work_item_created signal after the enclosing transaction commits.
    """
    if not title:
        raise ValueError("title is required")

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
        # Register on_commit INSIDE the atomic block so it fires at the correct
        # savepoint level. If called from a nested atomic(), it fires when the
        # *innermost* savepoint that contains it releases — correct semantics.
        _pk = work_item.pk
        transaction.on_commit(
            lambda: work_item_created.send_robust(
                sender=WorkItem,
                work_item=WorkItem.objects.get(pk=_pk),
                actor=actor,
            )
        )
    return work_item


def claim_work_item(work_item: WorkItem, actor) -> WorkItem:  # noqa: ANN001
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
    _pk = work_item.pk
    _actor_pk = actor.pk

    with transaction.atomic():
        work_item.assigned_to = actor
        work_item.status = new_status
        work_item.save(update_fields=["assigned_to", "status", "updated_at"])
        _record_history(
            work_item,
            "claimed",
            actor,
            old_status=old_status,
            new_status=new_status,
        )
        # Capture PKs; reload fresh instances in on_commit to avoid stale data.
        transaction.on_commit(lambda: _fire_claim_signals(_pk, _actor_pk, old_status, new_status))
    return work_item


def _fire_claim_signals(work_item_pk, actor_pk, old_status, new_status) -> None:  # noqa: ANN001
    """Reload fresh instances from DB before firing signals (avoids stale-data bugs)."""
    from django.contrib.auth import get_user_model

    User = get_user_model()  # noqa: N806
    try:
        work_item = WorkItem.objects.get(pk=work_item_pk)
        actor = User.objects.get(pk=actor_pk)
    except (WorkItem.DoesNotExist, User.DoesNotExist):
        logger.warning(
            "_fire_claim_signals: work_item or actor no longer exists pk=%s", work_item_pk
        )
        return
    work_item_assigned.send_robust(
        sender=WorkItem, work_item=work_item, assignee=actor, actor=actor
    )
    work_item_status_changed.send_robust(
        sender=WorkItem,
        work_item=work_item,
        old_status=old_status,
        new_status=new_status,
        actor=actor,
        notes="",
    )


def assign_work_item(work_item: WorkItem, assignee, actor) -> WorkItem:  # noqa: ANN001
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

    _pk = work_item.pk
    _assignee_pk = assignee.pk
    _actor_pk = actor.pk

    with transaction.atomic():
        work_item.assigned_to = assignee
        work_item.save(update_fields=["assigned_to", "updated_at"])
        _record_history(
            work_item,
            "assigned",
            actor,
            notes=f"Assigned to {assignee.display_name}",
        )
        transaction.on_commit(lambda: _fire_assigned_signal(_pk, _assignee_pk, _actor_pk))
    return work_item


def _fire_assigned_signal(work_item_pk, assignee_pk, actor_pk) -> None:  # noqa: ANN001
    from django.contrib.auth import get_user_model

    User = get_user_model()  # noqa: N806
    try:
        work_item = WorkItem.objects.get(pk=work_item_pk)
        assignee = User.objects.get(pk=assignee_pk)
        actor = User.objects.get(pk=actor_pk)
    except (WorkItem.DoesNotExist, User.DoesNotExist):
        return
    work_item_assigned.send_robust(
        sender=WorkItem,
        work_item=work_item,
        assignee=assignee,
        actor=actor,
    )


def update_work_item_status(
    work_item: WorkItem,
    new_status: str,
    actor,  # noqa: ANN001
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
        raise ValueError(f"Cannot transition from '{work_item.status}' to '{new_status}'.")

    old_status = work_item.status
    _pk = work_item.pk
    _actor_pk = actor.pk

    with transaction.atomic():
        work_item.status = new_status
        update_fields = ["status", "updated_at"]

        if new_status in TERMINAL_STATUSES and not work_item.completed_at:
            work_item.completed_at = timezone.now()
            update_fields.append("completed_at")

        work_item.save(update_fields=update_fields)
        _record_history(
            work_item,
            f"status_changed_to_{new_status}",
            actor,
            old_status=old_status,
            new_status=new_status,
            notes=notes,
        )
        transaction.on_commit(
            lambda: _fire_status_changed_signal(_pk, _actor_pk, old_status, new_status, notes)
        )
    return work_item


def _fire_status_changed_signal(work_item_pk, actor_pk, old_status, new_status, notes) -> None:  # noqa: ANN001
    from django.contrib.auth import get_user_model

    User = get_user_model()  # noqa: N806
    try:
        work_item = WorkItem.objects.get(pk=work_item_pk)
        actor = User.objects.get(pk=actor_pk)
    except (WorkItem.DoesNotExist, User.DoesNotExist):
        return
    work_item_status_changed.send_robust(
        sender=WorkItem,
        work_item=work_item,
        old_status=old_status,
        new_status=new_status,
        actor=actor,
        notes=notes,
    )


def escalate_work_item(work_item: WorkItem, actor, *, reason: str = "") -> WorkItem:  # noqa: ANN001
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
    _pk = work_item.pk
    _actor_pk = actor.pk

    with transaction.atomic():
        work_item.escalation_level += 1
        if not work_item.escalated_at:
            work_item.escalated_at = now
        work_item.save(update_fields=["escalation_level", "escalated_at", "updated_at"])
        _record_history(
            work_item,
            "escalated",
            actor,
            notes=reason or f"Escalated to level {work_item.escalation_level}",
        )
        _level = work_item.escalation_level
        transaction.on_commit(lambda: _fire_escalated_signal(_pk, _actor_pk, _level, reason))
    return work_item


def _fire_escalated_signal(work_item_pk, actor_pk, level, reason) -> None:  # noqa: ANN001
    from django.contrib.auth import get_user_model

    User = get_user_model()  # noqa: N806
    try:
        work_item = WorkItem.objects.get(pk=work_item_pk)
        actor = User.objects.get(pk=actor_pk)
    except (WorkItem.DoesNotExist, User.DoesNotExist):
        return
    work_item_escalated.send_robust(
        sender=WorkItem,
        work_item=work_item,
        level=level,
        actor=actor,
        reason=reason,
    )


def add_comment(work_item: WorkItem, actor, body: str) -> WorkItemComment:  # noqa: ANN001
    """
    Add an internal staff comment to a WorkItem.

    Raises PermissionError if actor is not staff.
    Raises ValueError if body is empty or work item is in a terminal status.
    """
    if not getattr(actor, "is_staff", False):
        raise PermissionError("Only staff users can comment on work items.")
    if work_item.status in TERMINAL_STATUSES:
        raise ValueError(f"Cannot add a comment to a {work_item.status} work item.")
    body = body.strip()
    if not body:
        raise ValueError("Comment body cannot be empty.")

    _pk = work_item.pk
    _actor_pk = actor.pk

    with transaction.atomic():
        comment = WorkItemComment.objects.create(
            work_item=work_item,
            author=actor,
            body=body,
        )
        _record_history(work_item, "commented", actor, notes=body[:200])
        _comment_pk = comment.pk
        transaction.on_commit(lambda: _fire_commented_signal(_pk, _actor_pk, _comment_pk))
    return comment


def _fire_commented_signal(work_item_pk, actor_pk, comment_pk) -> None:  # noqa: ANN001
    from django.contrib.auth import get_user_model

    User = get_user_model()  # noqa: N806
    try:
        work_item = WorkItem.objects.get(pk=work_item_pk)
        actor = User.objects.get(pk=actor_pk)
        comment = WorkItemComment.objects.get(pk=comment_pk)
    except (WorkItem.DoesNotExist, User.DoesNotExist, WorkItemComment.DoesNotExist):
        return
    work_item_commented.send_robust(
        sender=WorkItem,
        work_item=work_item,
        comment=comment,
        actor=actor,
    )


def get_staff_queue(  # noqa: ANN201
    actor,  # noqa: ANN001
    *,
    status_filter: str | None = None,
    assigned_to_me: bool = False,
    unassigned_only: bool = False,
    priority_filter: int | None = None,
):
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

    Uses select_for_update(skip_locked=True) to prevent duplicate processing
    when multiple Celery workers run concurrently. Bulk-updates and bulk-creates
    to avoid N+1 writes on large queues.
    """
    now = timezone.now()

    with transaction.atomic():
        breached_items = list(
            WorkItem.objects.select_for_update(skip_locked=True)
            .filter(due_at__lt=now, sla_breached_at__isnull=True)
            .exclude(status__in=list(TERMINAL_STATUSES))
        )

        if not breached_items:
            return 0

        breached_ids = [item.pk for item in breached_items]

        WorkItem.objects.filter(pk__in=breached_ids).update(
            sla_breached_at=now,
            updated_at=now,
        )

        WorkItemHistory.objects.bulk_create(
            [
                WorkItemHistory(
                    work_item=item,
                    action="sla_breached",
                    actor=None,
                    notes=(
                        f"SLA deadline {item.due_at:%Y-%m-%d %H:%M} UTC "
                        "passed without resolution."
                    ),
                )
                for item in breached_items
            ]
        )

    count = len(breached_ids)
    if count:
        # Log IDs only — no titles, no PII
        logger.warning(
            "SLA breach check: %d item(s) newly marked. IDs: %s",
            count,
            [str(pk) for pk in breached_ids],
        )
    return count
