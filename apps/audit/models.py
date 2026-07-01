"""
Immutable audit log model.

Design principles:
- Append-only: no update or delete operations ever. Enforced at the Django
  model layer by overriding save() and delete() to raise ValueError.
  Note: QuerySet.update() and QuerySet.delete() bypass these guards.
  For maximum assurance, add a PostgreSQL trigger in a future migration.
- Chain of custody: each entry includes a hash of the previous entry to detect
  tampering with the log sequence.
- No PII in log messages: PII lives in resource_id references, not in the
  event_detail JSON. Investigators query the referenced record for details.
- Retention: minimum 7 years (configurable in CIVICOS settings).
"""

import hashlib
import json

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class AuditEventType(models.TextChoices):
    # Authentication
    LOGIN_SUCCESS = "auth.login.success", _("Login succeeded")
    LOGIN_FAILED = "auth.login.failed", _("Login failed")
    LOGOUT = "auth.logout", _("Logged out")
    PASSWORD_CHANGED = "auth.password.changed", _("Password changed")
    MFA_ENABLED = "auth.mfa.enabled", _("MFA enabled")
    ACCOUNT_LOCKED = "auth.account.locked", _("Account locked")

    # Authorization
    ACCESS_DENIED = "authz.denied", _("Access denied")
    ROLE_GRANTED = "authz.role.granted", _("Role granted")
    ROLE_REVOKED = "authz.role.revoked", _("Role revoked")

    # Data access
    RECORD_VIEWED = "data.viewed", _("Record viewed")
    RECORD_EXPORTED = "data.exported", _("Record exported")

    # Data modification
    RECORD_CREATED = "data.created", _("Record created")
    RECORD_UPDATED = "data.updated", _("Record updated")
    RECORD_DELETED = "data.deleted", _("Record deleted")

    # Workflow
    SUBMISSION_RECEIVED = "workflow.submission.received", _("Submission received")
    STATUS_CHANGED = "workflow.status.changed", _("Status changed")
    ITEM_ASSIGNED = "workflow.item.assigned", _("Item assigned")
    ITEM_APPROVED = "workflow.item.approved", _("Item approved")
    ITEM_REJECTED = "workflow.item.rejected", _("Item rejected")

    # System
    SETTINGS_CHANGED = "system.settings.changed", _("Settings changed")
    USER_CREATED = "system.user.created", _("User created")


class AuditLogEntry(models.Model):
    """
    A single immutable audit log entry.

    Once created, this record must never be modified or deleted.
    The before_state / after_state JSON fields capture the full state
    transition for data modification events.
    """

    # Timestamp — indexed for time-range queries
    timestamp = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name=_("Timestamp"),
    )

    # Event classification
    event_type = models.CharField(
        max_length=64,
        choices=AuditEventType.choices,
        db_index=True,
        verbose_name=_("Event type"),
    )

    outcome = models.CharField(
        max_length=16,
        choices=[("success", _("Success")), ("failure", _("Failure"))],
        default="success",
        db_index=True,
        verbose_name=_("Outcome"),
    )

    # Actor (who performed the action)
    actor_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Actor ID"),
        help_text=_("User UUID, or null for anonymous/system actions"),
    )
    actor_email = models.EmailField(
        blank=True,
        verbose_name=_("Actor email"),
        help_text=_("Snapshot of email at time of event — user may be deleted later"),
    )
    actor_ip = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name=_("Actor IP"),
    )
    actor_user_agent = models.CharField(
        max_length=512,
        blank=True,
        verbose_name=_("User agent"),
    )

    # Resource (what was acted upon)
    resource_type = models.CharField(
        max_length=128,
        blank=True,
        db_index=True,
        verbose_name=_("Resource type"),
        help_text=_("Django app_label.ModelName format"),
    )
    resource_id = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        verbose_name=_("Resource ID"),
    )

    # State snapshots (for data modification events)
    before_state = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("Before state"),
    )
    after_state = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("After state"),
    )

    # Additional event-specific context (non-PII)
    event_detail = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("Event detail"),
    )

    # Request correlation
    request_id = models.CharField(
        max_length=64,
        blank=True,
        verbose_name=_("Request ID"),
    )
    session_id = models.CharField(
        max_length=64,
        blank=True,
        verbose_name=_("Session ID"),
    )

    # Chain of custody — links to previous entry's hash
    prev_hash = models.CharField(
        max_length=64,
        blank=True,
        verbose_name=_("Previous entry hash"),
    )
    entry_hash = models.CharField(
        max_length=64,
        blank=True,
        verbose_name=_("Entry hash"),
    )

    class Meta:
        verbose_name = _("Audit log entry")
        verbose_name_plural = _("Audit log entries")
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["resource_type", "resource_id"]),
            models.Index(fields=["actor_id", "timestamp"]),
            models.Index(fields=["event_type", "timestamp"]),
        ]

    def __str__(self) -> str:
        return f"{self.timestamp:%Y-%m-%d %H:%M:%S} | {self.event_type} | {self.actor_email or 'system'}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError(
                "Audit log entries are immutable. "
                "Attempted update on AuditLogEntry pk=%s." % self.pk
            )
        self.entry_hash = self._compute_hash()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Audit log entries are immutable and cannot be deleted.")

    def _compute_hash(self) -> str:
        """Compute a SHA-256 hash of this entry's content for tamper detection."""
        payload = {
            "event_type": self.event_type,
            "outcome": self.outcome,
            "actor_id": self.actor_id,
            "actor_ip": self.actor_ip,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "event_detail": self.event_detail,
            "prev_hash": self.prev_hash,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()
