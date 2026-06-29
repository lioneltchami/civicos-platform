"""
Abstract base models for Govstack.

All concrete models should extend one or more of these base classes to ensure
consistent behaviour across the platform: UUIDs, timestamps, soft deletion,
and PII tagging.
"""

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _


class UUIDModel(models.Model):
    """
    Replaces the default integer primary key with a UUID.

    Use for any model that may be referenced externally (API, URLs, emails)
    to avoid sequential ID enumeration attacks.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )

    class Meta:
        abstract = True


class TimestampedModel(models.Model):
    """
    Adds created_at and updated_at timestamps to a model.
    """

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("Created at"),
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_("Updated at"),
    )

    class Meta:
        abstract = True
        ordering = ["-created_at"]


class SoftDeleteModel(models.Model):
    """
    Adds soft deletion support.

    Deleted records are flagged with deleted_at rather than being removed from
    the database, which preserves the audit trail. Use the provided manager
    to filter out deleted records in queries.

    WARNING: Hard deletion must still be enforced for PII after retention
    expiry. Soft deletion is for operational use, not compliance retention.
    """

    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Deleted at"),
        db_index=True,
    )

    class Meta:
        abstract = True

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class BaseModel(UUIDModel, TimestampedModel):
    """
    Convenience base combining UUID primary key and timestamps.
    Most Govstack models should extend this.
    """

    class Meta:
        abstract = True
