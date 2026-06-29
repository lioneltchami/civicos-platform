"""
Citizen portal models for Govstack.

Provides authenticated self-service: submit requests, track status, receive updates.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel


class ServiceRequestStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    SUBMITTED = "submitted", _("Submitted")
    IN_REVIEW = "in_review", _("In review")
    AWAITING_INFO = "awaiting_info", _("Awaiting information")
    APPROVED = "approved", _("Approved")
    REJECTED = "rejected", _("Rejected")
    CLOSED = "closed", _("Closed")


class ServiceRequest(BaseModel):
    """
    A citizen-submitted service request.

    The core transactional record in the portal. Each submission goes through
    a workflow (managed by apps.workflows) and generates audit log entries.
    """

    citizen = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="service_requests",
        verbose_name=_("Citizen"),
    )

    # What service this request is for
    service_page_id = models.IntegerField(
        null=True,
        blank=True,
        verbose_name=_("Service page ID"),
        help_text=_("Reference to the CMS ServicePage that initiated this request."),
    )
    service_name = models.CharField(
        max_length=255,
        verbose_name=_("Service name"),
        help_text=_("Snapshot of service name at time of submission."),
    )

    # Current state
    status = models.CharField(
        max_length=32,
        choices=ServiceRequestStatus.choices,
        default=ServiceRequestStatus.SUBMITTED,
        db_index=True,
        verbose_name=_("Status"),
    )

    # Reference number for citizen communication
    reference_number = models.CharField(
        max_length=20,
        unique=True,
        verbose_name=_("Reference number"),
    )

    # Submission data (structured JSON from the form)
    submission_data = models.JSONField(
        default=dict,
        verbose_name=_("Submission data"),
    )

    # Internal notes (staff only — never shown to citizen)
    internal_notes = models.TextField(
        blank=True,
        verbose_name=_("Internal notes"),
    )

    # Retention
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Expires at"),
    )

    class Meta:
        verbose_name = _("Service request")
        verbose_name_plural = _("Service requests")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["citizen", "status"]),
            models.Index(fields=["reference_number"]),
        ]

    def __str__(self) -> str:
        return f"{self.reference_number} — {self.service_name}"


class StatusUpdate(BaseModel):
    """
    An immutable record of a status change on a ServiceRequest.
    Provides the citizen-visible history of their request.
    """

    service_request = models.ForeignKey(
        ServiceRequest,
        on_delete=models.CASCADE,
        related_name="status_updates",
        verbose_name=_("Service request"),
    )
    old_status = models.CharField(
        max_length=32,
        choices=ServiceRequestStatus.choices,
        verbose_name=_("Previous status"),
    )
    new_status = models.CharField(
        max_length=32,
        choices=ServiceRequestStatus.choices,
        verbose_name=_("New status"),
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        verbose_name=_("Changed by"),
    )
    # Citizen-visible note (translated)
    public_note = models.TextField(
        blank=True,
        verbose_name=_("Public note"),
        help_text=_("Shown to the citizen in their portal. Keep plain and non-technical."),
    )

    class Meta:
        verbose_name = _("Status update")
        ordering = ["-created_at"]
