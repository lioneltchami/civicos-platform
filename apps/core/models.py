"""
Abstract base models for CivicOS.

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
        db_index=True,
        verbose_name=_("Created at"),
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_("Updated at"),
    )

    class Meta:
        abstract = True
        ordering = ["-created_at"]


class ActiveQuerySet(models.QuerySet):
    """QuerySet that filters out soft-deleted records by default."""

    def active(self):
        """Return only records that have not been soft-deleted."""
        return self.filter(deleted_at__isnull=True)

    def deleted(self):
        """Return only soft-deleted records."""
        return self.filter(deleted_at__isnull=False)


class SoftDeleteManager(models.Manager):
    """
    Default manager for SoftDeleteModel — returns ONLY active (non-deleted) records.

    To query including deleted records, use Model.objects_all.all() or
    Model.objects_all.deleted().
    """

    def get_queryset(self):
        return ActiveQuerySet(self.model, using=self._db).active()


class SoftDeleteModel(models.Model):
    """
    Adds soft deletion support.

    Deleted records are flagged with deleted_at rather than being removed from
    the database, which preserves the audit trail. Use the provided manager
    to filter out deleted records in queries.

    - `Model.objects.all()` — active records only (default)
    - `Model.objects_all.all()` — all records including soft-deleted
    - `Model.objects_all.deleted()` — only soft-deleted records

    WARNING: Hard deletion must still be enforced for PII after retention
    expiry. Soft deletion is for operational use, not compliance retention.
    """

    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Deleted at"),
        db_index=True,
    )

    objects = SoftDeleteManager()
    objects_all = ActiveQuerySet.as_manager()  # Unfiltered access when needed

    class Meta:
        abstract = True

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self, using=None) -> None:
        """Mark this record as deleted without removing it from the database."""
        from django.utils import timezone
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at"], using=using)

    def restore(self, using=None) -> None:
        """Undo a soft deletion."""
        self.deleted_at = None
        self.save(update_fields=["deleted_at"], using=using)


class BaseModel(UUIDModel, TimestampedModel):
    """
    Convenience base combining UUID primary key and timestamps.
    Most CivicOS models should extend this.
    """

    class Meta:
        abstract = True
