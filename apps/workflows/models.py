"""
Workflow engine models for Govstack.

Provides a lightweight, configurable workflow system for routing service
requests through staff review, approval, and resolution.

Design: a WorkItem is created when a ServiceRequest is submitted. Staff claim,
action, and advance the WorkItem through configured states. Every state
transition is recorded in WorkItemHistory and fires a signal for audit logging.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel


class WorkItemStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    IN_PROGRESS = "in_progress", _("In progress")
    WAITING = "waiting", _("Waiting for information")
    COMPLETED = "completed", _("Completed")
    CANCELLED = "cancelled", _("Cancelled")


class WorkItem(BaseModel):
    """
    A unit of staff work tied to a ServiceRequest.

    Created automatically when a ServiceRequest is submitted.
    Staff see WorkItems in their queue and action them here.
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

    title = models.CharField(
        max_length=255,
        verbose_name=_("Title"),
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
    )
    due_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Due at"),
    )
    priority = models.PositiveSmallIntegerField(
        default=3,
        choices=[(1, _("Critical")), (2, _("High")), (3, _("Normal")), (4, _("Low"))],
        verbose_name=_("Priority"),
    )

    class Meta:
        verbose_name = _("Work item")
        verbose_name_plural = _("Work items")
        ordering = ["priority", "created_at"]
        indexes = [
            models.Index(fields=["assigned_to", "status"]),
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self) -> str:
        return self.title


class WorkItemHistory(BaseModel):
    """Immutable record of each action taken on a WorkItem."""

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
    old_status = models.CharField(max_length=32, blank=True)
    new_status = models.CharField(max_length=32, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        verbose_name=_("Actor"),
    )
    notes = models.TextField(blank=True, verbose_name=_("Notes"))

    class Meta:
        verbose_name = _("Work item history")
        ordering = ["-created_at"]
