"""
Consent & Privacy models — PIPEDA-compliant.
"""
from __future__ import annotations

import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimestampedModel, UUIDModel


class ConsentCategory(TimestampedModel):
    """
    A category of data processing that citizens can consent to.
    Each category corresponds to a distinct purpose (PIPEDA 4.2).
    """

    LAWFUL_BASIS_CHOICES = [
        ("consent", "Consent"),
        ("legal_obligation", "Legal Obligation"),
        ("vital_interests", "Vital Interests"),
    ]

    slug = models.CharField(max_length=100, unique=True)
    name_en = models.CharField(max_length=255)
    name_fr = models.CharField(max_length=255)
    purpose_en = models.TextField(
        help_text="Plain-language explanation of why data is collected (PIPEDA 4.2)."
    )
    purpose_fr = models.TextField()
    lawful_basis = models.CharField(
        max_length=30,
        choices=LAWFUL_BASIS_CHOICES,
        default="consent",
    )
    is_required = models.BooleanField(
        default=False,
        help_text="If True, consent cannot be withdrawn — required for essential service delivery.",
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "slug"]
        verbose_name = "Consent Category"
        verbose_name_plural = "Consent Categories"

    def __str__(self) -> str:
        return self.name_en

    def get_name(self) -> str:
        """Return the category name in the active language (Fix 5)."""
        from django.utils.translation import get_language
        if get_language() and get_language().startswith("fr"):
            return self.name_fr or self.name_en
        return self.name_en

    def get_purpose(self) -> str:
        """Return the category purpose in the active language (Fix 5)."""
        from django.utils.translation import get_language
        if get_language() and get_language().startswith("fr"):
            return self.purpose_fr or self.purpose_en
        return self.purpose_en


class ConsentRecord(UUIDModel, TimestampedModel):
    """
    One citizen's decision on one consent category.
    Each citizen has at most one record per category (unique_together).
    """

    STATUS_PENDING = "pending"
    STATUS_GRANTED = "granted"
    STATUS_WITHDRAWN = "withdrawn"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_GRANTED, "Granted"),
        (STATUS_WITHDRAWN, "Withdrawn"),
    ]

    SOURCE_CHOICES = [
        ("web", "Web Portal"),
        ("api", "API"),
        ("admin", "Admin"),
    ]

    citizen = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="consent_records",
    )
    category = models.ForeignKey(
        ConsentCategory,
        on_delete=models.PROTECT,
        related_name="records",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    granted_at = models.DateTimeField(null=True, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    actor_ip = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="Always masked before storage.",
    )
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="web")
    consent_version = models.CharField(
        max_length=50,
        blank=True,
        help_text="Identifies which version of the consent text was shown.",
    )

    class Meta:
        unique_together = [("citizen", "category")]
        ordering = ["-created_at"]
        verbose_name = "Consent Record"
        verbose_name_plural = "Consent Records"

    def __str__(self) -> str:
        return f"{self.citizen} — {self.category} ({self.status})"

    def save(self, *args, **kwargs):
        if self.status == self.STATUS_GRANTED and self.granted_at is None:
            self.granted_at = timezone.now()
        if self.status == self.STATUS_WITHDRAWN and self.withdrawn_at is None:
            self.withdrawn_at = timezone.now()
        super().save(*args, **kwargs)


class DataExportRequest(UUIDModel):
    """
    PIPEDA s.4.9 right of access — a citizen's request for all their data.
    Must be fulfilled within 30 days.
    """

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_READY = "ready"
    STATUS_DELIVERED = "delivered"
    STATUS_FAILED = "failed"
    STATUS_EXPIRED = "expired"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_READY, "Ready"),
        (STATUS_DELIVERED, "Delivered"),
        (STATUS_FAILED, "Failed"),
        (STATUS_EXPIRED, "Expired"),
    ]

    FORMAT_CHOICES = [
        ("json", "JSON"),
    ]

    citizen = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="export_requests",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    format = models.CharField(max_length=10, choices=FORMAT_CHOICES, default="json")
    requested_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="7 days after processed_at.",
    )
    download_token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    storage_path = models.CharField(
        max_length=500,
        blank=True,
        help_text="Internal file path — do not expose to citizens.",
    )
    notes = models.TextField(blank=True, help_text="Staff notes.")

    class Meta:
        ordering = ["-requested_at"]
        verbose_name = "Data Export Request"
        verbose_name_plural = "Data Export Requests"
        constraints = [
            models.UniqueConstraint(
                fields=["citizen"],
                condition=models.Q(status__in=["pending", "processing"]),
                name="unique_active_export_per_citizen",
            )
        ]

    def __str__(self) -> str:
        return f"Export {self.pk} — {self.citizen} ({self.status})"


class ConsentAuditEntry(models.Model):
    """
    Append-only audit trail for all consent and export events.
    Records may never be updated or deleted.
    """

    ACTION_CHOICES = [
        ("granted", "Granted"),
        ("withdrawn", "Withdrawn"),
        ("export_requested", "Export Requested"),
        ("export_ready", "Export Ready"),
        ("export_delivered", "Export Delivered"),
        ("export_expired", "Export Expired"),
        ("export_failed", "Export Failed"),
        ("export_downloaded", "Export Downloaded by Citizen"),
        ("export_marked_delivered", "Marked Delivered by Staff"),
    ]

    citizen = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        related_name="consent_audit_entries",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="consent_audit_actions",
    )
    action = models.CharField(max_length=30, choices=ACTION_CHOICES, db_index=True)
    category = models.ForeignKey(
        ConsentCategory,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    export_request = models.ForeignKey(
        DataExportRequest,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    actor_ip = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="Always masked before storage.",
    )
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Consent Audit Entry"
        verbose_name_plural = "Consent Audit Entries"
        constraints = [
            models.CheckConstraint(
                check=models.Q(action__in=[
                    "granted", "withdrawn",
                    "export_requested", "export_ready",
                    "export_delivered", "export_expired", "export_failed",
                    "export_downloaded", "export_marked_delivered",
                ]),
                name="consent_audit_valid_action",
            )
        ]

    def __str__(self) -> str:
        return f"{self.action} — {self.citizen} @ {self.timestamp}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("ConsentAuditEntry is append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("ConsentAuditEntry records cannot be deleted.")
