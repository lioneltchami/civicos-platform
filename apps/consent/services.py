"""
ConsentService — the single place all consent business logic lives.

PIPEDA requirements enforced here:
  * Required categories (is_required=True) cannot be withdrawn
  * Every state change is recorded in ConsentAuditEntry
  * IPs are masked before storage
  * Only one pending DataExportRequest per citizen at a time
"""
from __future__ import annotations

import logging
from typing import Optional

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.forms.utils import _mask_ip

logger = logging.getLogger(__name__)


class ConsentService:

    @staticmethod
    def get_or_create_record(citizen, category):
        """Get existing ConsentRecord or create one in 'pending' status."""
        from apps.consent.models import ConsentRecord

        record, _ = ConsentRecord.objects.get_or_create(
            citizen=citizen,
            category=category,
            defaults={"status": ConsentRecord.STATUS_PENDING},
        )
        return record

    @staticmethod
    def grant(citizen, category_slug: str, request=None, consent_version: str = ""):
        """
        Grant consent for a category. Idempotent if already granted.
        Raises ValueError if category does not exist or is inactive.

        consent_version: identifies which version of the consent text was shown
        to the citizen. Pass the value of settings.CONSENT_CURRENT_VERSION from
        the calling view. Defaults to "" (unknown) for backwards compatibility.
        """
        from apps.consent.models import ConsentAuditEntry, ConsentCategory, ConsentRecord
        from apps.consent.signals import consent_granted

        category = ConsentCategory.objects.filter(slug=category_slug, is_active=True).first()
        if not category:
            raise ValueError(f"Unknown or inactive consent category: {category_slug!r}")

        ip = _mask_ip(_get_ip(request) or "")

        with transaction.atomic():
            record, created = ConsentRecord.objects.get_or_create(
                citizen=citizen,
                category=category,
                defaults={
                    "status": ConsentRecord.STATUS_GRANTED,
                    "granted_at": timezone.now(),
                    "actor_ip": ip,
                    "source": _get_source(request),
                    "consent_version": consent_version,
                },
            )
            state_changed = created or record.status != ConsentRecord.STATUS_GRANTED
            if not created and record.status != ConsentRecord.STATUS_GRANTED:
                record.status = ConsentRecord.STATUS_GRANTED
                record.granted_at = timezone.now()
                record.actor_ip = ip
                record.source = _get_source(request)
                update_fields = ["status", "granted_at", "actor_ip", "source"]
                if consent_version:
                    record.consent_version = consent_version
                    update_fields.append("consent_version")
                record.save(update_fields=update_fields)

            # Only write an audit entry when the state actually changed to avoid
            # phantom entries on idempotent re-grant calls.
            if state_changed:
                ConsentAuditEntry.objects.create(
                    citizen=citizen,
                    actor=citizen,
                    action="granted",
                    category=category,
                    actor_ip=ip,
                    details={"category_slug": category_slug, "source": _get_source(request)},
                )

        consent_granted.send(sender=ConsentRecord, consent_record=record, request=request)
        return record

    @staticmethod
    def withdraw(citizen, category_slug: str, request=None):
        """
        Withdraw consent for a category.
        Raises ValueError if category is required (cannot be withdrawn).
        Raises ValueError if no existing record found.
        """
        from apps.consent.models import ConsentAuditEntry, ConsentCategory, ConsentRecord
        from apps.consent.signals import consent_withdrawn

        category = ConsentCategory.objects.filter(slug=category_slug, is_active=True).first()
        if not category:
            raise ValueError(f"Unknown or inactive consent category: {category_slug!r}")
        if category.is_required:
            raise ValueError(
                f"Consent category {category_slug!r} is required and cannot be withdrawn."
            )

        ip = _mask_ip(_get_ip(request) or "")

        with transaction.atomic():
            try:
                record = ConsentRecord.objects.get(citizen=citizen, category=category)
            except ConsentRecord.DoesNotExist:
                raise ValueError(f"No consent record found for category {category_slug!r}")

            # Refuse to withdraw a PENDING (never-granted) record — there is nothing to undo.
            if record.status == ConsentRecord.STATUS_PENDING:
                raise ValueError("Cannot withdraw consent that was never granted.")

            # Idempotent: if already withdrawn, return without writing a duplicate audit entry.
            if record.status != ConsentRecord.STATUS_GRANTED:
                return record

            record.status = ConsentRecord.STATUS_WITHDRAWN
            record.withdrawn_at = timezone.now()
            record.actor_ip = ip
            record.source = _get_source(request)
            record.save(update_fields=["status", "withdrawn_at", "actor_ip", "source"])

            ConsentAuditEntry.objects.create(
                citizen=citizen,
                actor=citizen,
                action="withdrawn",
                category=category,
                actor_ip=ip,
                details={"category_slug": category_slug, "source": _get_source(request)},
            )

        consent_withdrawn.send(sender=ConsentRecord, consent_record=record, request=request)
        return record

    @staticmethod
    def has_consent(citizen, category_slug: str) -> bool:
        """Return True if citizen has an active 'granted' consent for this category."""
        from apps.consent.models import ConsentRecord

        return ConsentRecord.objects.filter(
            citizen=citizen,
            category__slug=category_slug,
            status=ConsentRecord.STATUS_GRANTED,
        ).exists()

    @staticmethod
    def get_citizen_consents(citizen):
        """Return all ConsentRecord objects for a citizen, with category prefetched."""
        from apps.consent.models import ConsentRecord

        return (
            ConsentRecord.objects.filter(citizen=citizen)
            .select_related("category")
            .order_by("category__sort_order")
        )

    @staticmethod
    def get_active_categories():
        """Return all active consent categories, ordered."""
        from apps.consent.models import ConsentCategory

        return ConsentCategory.objects.filter(is_active=True).order_by("sort_order", "slug")

    @staticmethod
    def request_export(citizen, request=None):
        """
        Create a PIPEDA s.4.9 data export request.
        Raises ValueError if a pending/processing request already exists.
        Queues the Celery task.
        """
        from apps.consent.models import ConsentAuditEntry, DataExportRequest
        from apps.consent.signals import export_requested
        from apps.consent.tasks import process_data_export

        ip = _mask_ip(_get_ip(request) or "")

        with transaction.atomic():
            # The DB-level UniqueConstraint(fields=["citizen"], condition=Q(status__in=["pending",
            # "processing"]), name="unique_active_export_per_citizen") is the single authoritative
            # gate. We catch IntegrityError here so concurrent requests produce a clean ValueError
            # (shown to the citizen as a flash message) rather than an unhandled 500.
            try:
                export_req = DataExportRequest.objects.create(
                    citizen=citizen,
                    status=DataExportRequest.STATUS_PENDING,
                    format="json",
                )
            except IntegrityError:
                raise ValueError(
                    "A data export request is already in progress. "
                    "Please wait for the current request to complete."
                )

            ConsentAuditEntry.objects.create(
                citizen=citizen,
                actor=citizen,
                action="export_requested",
                export_request=export_req,
                actor_ip=ip,
                details={"format": "json"},
            )

        # Both the Celery task dispatch and the signal fire inside on_commit so that receivers
        # can safely read the DataExportRequest row — the row is guaranteed committed before
        # either callback runs.
        def _send_export_requested():
            export_requested.send(
                sender=DataExportRequest,
                export_request=export_req,
                request=request,
            )
            process_data_export.delay(str(export_req.pk))

        transaction.on_commit(_send_export_requested)
        return export_req

    @staticmethod
    def get_citizen_exports(citizen):
        """Return all DataExportRequests for citizen, newest first.

        Uses .only() to prevent accidental exposure of storage_path and notes
        to citizen-facing views — both fields are internal/staff-only.
        """
        from apps.consent.models import DataExportRequest

        return DataExportRequest.objects.filter(citizen=citizen).only(
            "id", "status", "format", "requested_at", "processed_at",
            "expires_at", "download_token",
        ).order_by("-requested_at")


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _get_ip(request) -> Optional[str]:
    """
    Extract client IP address.

    Always use REMOTE_ADDR as the authoritative source. This is set by the
    WSGI server (gunicorn) based on the TCP connection, which cannot be
    spoofed by clients. In production behind nginx, gunicorn's
    forwarded_allow_ips="*" means gunicorn rewrites REMOTE_ADDR from the
    X-Forwarded-For header before Django sees it — so REMOTE_ADDR is already
    the real client IP when a trusted proxy is in front.

    Never trust HTTP_X_FORWARDED_FOR directly in Django — it can be injected
    by any client that reaches the server without going through nginx.
    """
    if request is None:
        return None
    return request.META.get("REMOTE_ADDR")


def _get_source(request) -> str:
    if request is None:
        return "api"
    return "api" if getattr(request, "is_api_request", False) else "web"
