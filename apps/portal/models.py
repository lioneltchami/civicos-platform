"""
Citizen portal models for Govstack.

Provides authenticated self-service: submit requests, track status, receive updates.
"""

import secrets
import string

from django.conf import settings
from django.db import models
from django.utils import timezone
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

    @classmethod
    def generate_reference_number(cls) -> str:
        """
        Generate a unique reference number in format GS-YYYY-XXXXXX.
        GS = Govstack, YYYY = current year, XXXXXX = 6 random uppercase alphanumeric chars.
        Retries up to 10 times to avoid collision.
        Example: GS-2026-A3F9K2
        """
        year = timezone.now().year
        for _ in range(10):
            alphabet = string.ascii_uppercase + string.digits
            suffix = "".join(secrets.choice(alphabet) for _ in range(6))
            ref = f"GS-{year}-{suffix}"
            if not cls.objects.filter(reference_number=ref).exists():
                return ref
        raise ValueError("Could not generate unique reference number after 10 attempts")

    def save(self, *args, **kwargs) -> None:
        if not self.reference_number:
            self.reference_number = self.generate_reference_number()
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("portal:request-detail", kwargs={"pk": self.pk})

    def can_be_cancelled(self) -> bool:
        """Citizens can cancel requests that haven't been approved/rejected/closed."""
        return self.status in (
            ServiceRequestStatus.DRAFT,
            ServiceRequestStatus.SUBMITTED,
            ServiceRequestStatus.IN_REVIEW,
            ServiceRequestStatus.AWAITING_INFO,
        )

    def is_terminal(self) -> bool:
        """True if the request is in a final state."""
        return self.status in (
            ServiceRequestStatus.APPROVED,
            ServiceRequestStatus.REJECTED,
            ServiceRequestStatus.CLOSED,
        )

    @property
    def status_display_class(self) -> str:
        """CSS class for status badge — used in templates."""
        mapping = {
            ServiceRequestStatus.DRAFT: "badge--grey",
            ServiceRequestStatus.SUBMITTED: "badge--blue",
            ServiceRequestStatus.IN_REVIEW: "badge--yellow",
            ServiceRequestStatus.AWAITING_INFO: "badge--orange",
            ServiceRequestStatus.APPROVED: "badge--green",
            ServiceRequestStatus.REJECTED: "badge--red",
            ServiceRequestStatus.CLOSED: "badge--grey",
        }
        return mapping.get(self.status, "badge--grey")


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
        blank=True,  # blank for the initial "created" record where there is no previous status
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

    def __str__(self) -> str:
        return (
            f"{self.service_request.reference_number}: "
            f"{self.old_status} → {self.new_status}"
        )

    def save(self, *args, **kwargs) -> None:
        if self.pk:
            raise ValueError(
                "StatusUpdate records are immutable. Create a new record instead."
            )
        super().save(*args, **kwargs)
