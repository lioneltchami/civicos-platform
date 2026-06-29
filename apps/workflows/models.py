"""
Workflow engine models for Govstack.

Provides a lightweight, configurable workflow system for routing service
requests through staff review, approval, and resolution.

Design:
- A WorkItem is created when a ServiceRequest is submitted.
- Staff claim, action, and advance the WorkItem through configured states.
- Every state transition is recorded in WorkItemHistory (immutable).
- Staff add WorkItemComments for internal notes (not citizen-visible).
- SLA breach is detected by a Celery periodic task that sets sla_breached_at.
- Escalation increments escalation_level and records escalated_at.

Security:
- WorkItems are staff-only; citizens never access this module directly.
- GenericForeignKey links to the source object without exposing internal IDs.
- All mutations go through services.py, never direct model.save() from views.
"""

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel


class WorkItemStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    IN_PROGRESS = "in_progress", _("In progress")
    WAITING = "waiting", _("Waiting for information")
    COMPLETED = "completed", _("Completed")
    CANCELLED = "cancelled", _("Cancelled")


# Statuses from which no further transitions are allowed
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {WorkItemStatus.COMPLETED, WorkItemStatus.CANCELLED}
)

# Valid transitions: {from_status: {to_status, ...}}
VALID_TRANSITIONS: dict[str, set[str]] = {
    WorkItemStatus.PENDING: {WorkItemStatus.IN_PROGRESS, WorkItemStatus.CANCELLED},
    WorkItemStatus.IN_PROGRESS: {
        WorkItemStatus.WAITING,
        WorkItemStatus.COMPLETED,
        WorkItemStatus.CANCELLED,
    },
    WorkItemStatus.WAITING: {WorkItemStatus.IN_PROGRESS, WorkItemStatus.CANCELLED},
    WorkItemStatus.COMPLETED: set(),
    WorkItemStatus.CANCELLED: set(),
}

# SLA hours by priority level (1=Critical, 2=High, 3=Normal, 4=Low)
SLA_HOURS: dict[int, int] = {
    1: 4,     # Critical — 4 hours
    2: 24,    # High — 1 business day
    3: 120,   # Normal — 5 business days (approx.)
    4: 240,   # Low — 10 business days (approx.)
}


class WorkItemPriority(models.IntegerChoices):
    CRITICAL = 1, _("Critical")
    HIGH = 2, _("High")
    NORMAL = 3, _("Normal")
    LOW = 4, _("Low")


class WorkItem(BaseModel):
    """
    A unit of staff work tied to a ServiceRequest (or any content object).

    Created automatically when a ServiceRequest is submitted.
    Staff see WorkItems in their queue and action them here.

    The GenericForeignKey (content_type + object_id) allows any model to
    spawn a WorkItem without circular imports.
    """

    # Generic link to the triggering object (ServiceRequest, FormSubmission, etc.)
    content_type = models.ForeignKey(
        "contenttypes.ContentType",
        on_delete=models.CASCADE,
        verbose_name=_("Content type"),
    )
    object_id = models.CharField(
        max_length=64,
        verbose_name=_("Object ID"),
    )
    content_object = GenericForeignKey("content_type", "object_id")

    title = models.CharField(
        max_length=255,
        verbose_name=_("Title"),
    )
    description = models.TextField(
        blank=True,
        verbose_name=_("Description"),
        help_text=_("Brief description of the work required."),
    )
    status = models.CharField(
        max_length=32,
        choices=WorkItemStatus.choices,
        default=WorkItemStatus.PENDING,
        db_index=True,
        verbose_name=_("Status"),
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_work_items",
        verbose_name=_("Assigned to"),
        limit_choices_to={"is_staff": True},
    )
    priority = models.PositiveSmallIntegerField(
        default=WorkItemPriority.NORMAL,
        choices=WorkItemPriority.choices,
        db_index=True,
        verbose_name=_("Priority"),
    )
    due_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Due at"),
    )

    # SLA and escalation tracking
    sla_breached_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("SLA breached at"),
        help_text=_("Set by periodic task when due_at passes without completion."),
    )
    escalation_level = models.PositiveSmallIntegerField(
        default=0,
        verbose_name=_("Escalation level"),
        help_text=_("0=normal, 1=supervisor, 2=director."),
    )
    escalated_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Escalated at"),
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Completed at"),
        help_text=_("Set when status transitions to completed or cancelled."),
    )

    class Meta:
        verbose_name = _("Work item")
        verbose_name_plural = _("Work items")
        ordering = ["priority", "created_at"]
        indexes = [
            models.Index(fields=["assigned_to", "status"]),
            models.Index(fields=["content_type", "object_id"]),
            models.Index(fields=["status", "due_at"]),
            models.Index(fields=["escalation_level", "status"]),
            models.Index(fields=["sla_breached_at", "status"], name="workitem_sla_status_idx"),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_overdue(self) -> bool:
        """True if due_at has passed and the item is not yet terminal."""
        if self.status in TERMINAL_STATUSES:
            return False
        return bool(self.due_at and self.due_at < timezone.now())

    @property
    def is_sla_breached(self) -> bool:
        return self.sla_breached_at is not None

    @property
    def is_escalated(self) -> bool:
        return self.escalation_level > 0

    def can_transition_to(self, new_status: str) -> bool:
        """Return True if the state machine allows this transition."""
        return new_status in VALID_TRANSITIONS.get(self.status, set())


class WorkItemHistory(BaseModel):
    """
    Immutable record of each action taken on a WorkItem.

    Never update or delete rows — append only.
    """

    work_item = models.ForeignKey(
        WorkItem,
        on_delete=models.CASCADE,
        related_name="history",
        verbose_name=_("Work item"),
    )
    action = models.CharField(
        max_length=64,
        verbose_name=_("Action"),
    )
    old_status = models.CharField(max_length=32, blank=True, verbose_name=_("Old status"))
    new_status = models.CharField(max_length=32, blank=True, verbose_name=_("New status"))
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        verbose_name=_("Actor"),
    )
    notes = models.TextField(blank=True, verbose_name=_("Notes"))

    class Meta:
        verbose_name = _("Work item history")
        verbose_name_plural = _("Work item history entries")
        ordering = ["created_at"]

    def __str__(self) -> str:
        ts = self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else "?"
        return f"{self.work_item.title}: {self.action} at {ts}"

    def save(self, *args, **kwargs) -> None:
        # Enforce immutability — history rows are append-only
        if not self._state.adding:
            raise ValueError(
                "WorkItemHistory records are immutable. Create a new record instead."
            )
        super().save(*args, **kwargs)


class WorkItemComment(BaseModel):
    """
    Internal staff note on a WorkItem.

    Comments are NOT citizen-visible. They are for staff coordination only.
    """

    work_item = models.ForeignKey(
        WorkItem,
        on_delete=models.CASCADE,
        related_name="comments",
        verbose_name=_("Work item"),
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="work_item_comments",
        verbose_name=_("Author"),
    )
    body = models.TextField(verbose_name=_("Comment"))

    class Meta:
        verbose_name = _("Work item comment")
        verbose_name_plural = _("Work item comments")
        ordering = ["created_at"]

    def __str__(self) -> str:
        author = self.author.display_name if self.author else "Unknown"
        ts = self.created_at.strftime("%Y-%m-%d") if self.created_at else "?"
        return f"Comment by {author} on {ts}"
