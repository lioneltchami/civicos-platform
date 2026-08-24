"""
Notification models for CivicOS.

Tracks outbound notifications (email, SMS, in-app) sent to citizens and staff.
Provides delivery status tracking and a citizen-facing notification inbox.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel


class NotificationChannel(models.TextChoices):
    EMAIL = "email", _("Email")
    SMS = "sms", _("SMS")
    IN_APP = "in_app", _("In-app")


class NotificationStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    SENT = "sent", _("Sent")
    DELIVERED = "delivered", _("Delivered")
    FAILED = "failed", _("Failed")
    BOUNCED = "bounced", _("Bounced")


class Notification(BaseModel):
    """
    A record of a notification sent or queued to be sent.

    Created by the notifications service; updated by delivery webhooks.
    Subject and body are stored for audit and resend purposes.
    """

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name=_("Recipient"),
    )
    channel = models.CharField(
        max_length=16,
        choices=NotificationChannel.choices,
        verbose_name=_("Channel"),
    )
    subject = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Subject"),
    )
    body = models.TextField(
        verbose_name=_("Body"),
    )
    language = models.CharField(
        max_length=10,
        default="en",
        verbose_name=_("Language"),
    )
    status = models.CharField(
        max_length=16,
        choices=NotificationStatus.choices,
        default=NotificationStatus.PENDING,
        db_index=True,
        verbose_name=_("Status"),
    )
    sent_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name=_("Sent at"))
    external_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("External ID"),
        help_text=_(
            "ID from the sending provider (GC Notify, SendGrid, etc.) for status tracking."
        ),
    )
    # Whether the citizen has read this notification in the portal inbox
    read_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Read at"))

    class Meta:
        verbose_name = _("Notification")
        verbose_name_plural = _("Notifications")
        ordering = ["-created_at"]  # noqa: RUF012
        indexes = [  # noqa: RUF012
            models.Index(fields=["recipient", "read_at"]),
            # Optimised index for the in-app inbox query pattern:
            # filter(recipient=X, channel=IN_APP).filter(read_at__isnull=True)
            models.Index(fields=["recipient", "channel", "read_at"]),
            models.Index(fields=["status", "channel"]),
        ]

    def __str__(self) -> str:
        return f"{self.channel} → {self.recipient.email}: {self.subject or '(no subject)'}"
