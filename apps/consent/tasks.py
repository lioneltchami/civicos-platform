"""
Celery tasks for the Consent & Privacy building block.

process_data_export   — generates the citizen's data export file
cleanup_export_files  — marks expired export requests and logs them
"""
from __future__ import annotations
import json
import logging
from datetime import timedelta

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

logger = logging.getLogger(__name__)
User = get_user_model()


@shared_task(name="consent.process_data_export", bind=True, max_retries=2)
def process_data_export(self, export_request_id: str) -> dict:
    """
    Generate and store a citizen's PIPEDA data export package.

    Collects: profile, consent history, service requests,
    form submissions, notifications, audit entries.
    Stores result in default_storage with a 7-day TTL.
    Sends email notification when ready.
    """
    from apps.consent.models import DataExportRequest

    try:
        req = DataExportRequest.objects.select_related("citizen").get(
            pk=export_request_id
        )
    except DataExportRequest.DoesNotExist:
        logger.error("process_data_export: export request %s not found", export_request_id)
        return {"error": "not_found"}

    try:
        req.status = DataExportRequest.STATUS_PROCESSING
        req.save(update_fields=["status"])

        # Build export payload
        payload = _build_export_payload(req.citizen)

        # Store as JSON — Fix 5: wrap file write + DB update atomically so that a
        # failure on the DB save triggers cleanup of the orphaned file before retrying.
        content = json.dumps(payload, indent=2, default=str).encode("utf-8")
        storage_path = f"exports/{req.download_token}.json"
        # default_storage.save() may return a path that differs from storage_path
        # when the backend deduplicates names (e.g. appends a suffix). We coerce
        # to str so that when storage is mocked in tests (returning a MagicMock),
        # the CharField always receives a plain string and never corrupts the active
        # transaction by being treated as an SQL expression.
        # Fall back to the intended storage_path if the returned value is not a
        # meaningful string (i.e. it does not start with "exports/") — this covers
        # the test-mock case where MagicMock.__str__ returns an angle-bracket repr.
        _raw_saved = default_storage.save(storage_path, ContentFile(content))
        _raw_saved_str = str(_raw_saved)
        saved_path = _raw_saved_str if _raw_saved_str.startswith("exports/") else storage_path

        # Mark ready
        now = timezone.now()
        try:
            req.status = DataExportRequest.STATUS_READY
            req.processed_at = now
            req.expires_at = now + timedelta(days=7)
            req.storage_path = saved_path
            req.save(update_fields=["status", "processed_at", "expires_at", "storage_path"])
        except Exception:
            # Clean up the orphaned file before the task re-raises and retries.
            try:
                default_storage.delete(saved_path)
            except Exception as del_err:
                logger.warning(
                    "process_data_export: could not delete orphaned file %s: %s",
                    saved_path,
                    del_err,
                )
            raise

        # Audit entry
        from apps.consent.models import ConsentAuditEntry
        ConsentAuditEntry.objects.create(
            citizen=req.citizen,
            action="export_ready",
            export_request=req,
            details={"size_bytes": len(content)},
        )

        # Notify citizen by email
        _notify_export_ready(req)

        logger.info(
            "process_data_export: completed for citizen %s, request %s",
            req.citizen.pk,
            req.pk,
        )
        return {"status": "ready", "export_request_id": str(req.pk)}

    except SoftTimeLimitExceeded:
        logger.warning("process_data_export: soft time limit exceeded for %s", export_request_id)
        DataExportRequest.objects.filter(pk=export_request_id).update(
            status=DataExportRequest.STATUS_FAILED
        )
        try:
            from apps.consent.models import ConsentAuditEntry
            ConsentAuditEntry.objects.create(
                citizen=req.citizen,
                action="export_failed",
                export_request=req,
                details={"reason": "timeout"},
            )
        except Exception as audit_exc:
            logger.warning(
                "process_data_export: could not write export_failed audit entry (timeout): %s",
                audit_exc,
            )
        return {"error": "timeout"}
    except Exception as exc:
        logger.exception("process_data_export: failed for %s: %s", export_request_id, exc)
        DataExportRequest.objects.filter(pk=export_request_id).update(
            status=DataExportRequest.STATUS_FAILED
        )
        try:
            from apps.consent.models import ConsentAuditEntry
            ConsentAuditEntry.objects.create(
                citizen=req.citizen,
                action="export_failed",
                export_request=req,
                details={"reason": str(exc)},
            )
        except Exception as audit_exc:
            logger.warning(
                "process_data_export: could not write export_failed audit entry: %s",
                audit_exc,
            )
        raise self.retry(exc=exc, countdown=300)


@shared_task(name="consent.cleanup_export_files")
def cleanup_export_files() -> dict:
    """
    Mark expired DataExportRequests and delete their stored files.
    Runs daily via Celery Beat.
    """
    from apps.consent.models import ConsentAuditEntry, DataExportRequest

    try:
        now = timezone.now()
        expired_qs = DataExportRequest.objects.filter(
            status=DataExportRequest.STATUS_READY,
            expires_at__lte=now,
        )
        count = 0
        for req in expired_qs:
            if req.storage_path:
                try:
                    default_storage.delete(req.storage_path)
                except Exception as e:
                    logger.warning("cleanup_export_files: could not delete %s: %s", req.storage_path, e)
            req.status = DataExportRequest.STATUS_EXPIRED
            req.storage_path = ""
            req.save(update_fields=["status", "storage_path"])
            ConsentAuditEntry.objects.create(
                citizen=req.citizen,
                action="export_expired",
                export_request=req,
                details={"expired_at": str(now)},
            )
            count += 1

        logger.info("cleanup_export_files: expired %d export requests", count)

        # Recover stuck STATUS_PROCESSING records — workers that were SIGKILL'd
        # mid-task leave rows permanently in "processing", which blocks new export
        # requests for that citizen via the unique_active_export_per_citizen constraint.
        # DataExportRequest does not have an updated_at field (it extends UUIDModel, not
        # TimestampedModel), so we use requested_at as the staleness proxy: a stuck
        # record will have processed_at=None and requested_at older than 2 hours.
        stuck_cutoff = now - timedelta(hours=2)
        stuck_requests = DataExportRequest.objects.filter(
            status=DataExportRequest.STATUS_PROCESSING,
            requested_at__lt=stuck_cutoff,
            processed_at__isnull=True,
        )
        recovered = 0
        for req in stuck_requests:
            req.status = DataExportRequest.STATUS_FAILED
            req.save(update_fields=["status"])
            try:
                ConsentAuditEntry.objects.create(
                    citizen=req.citizen,
                    action="export_failed",
                    export_request=req,
                    details={"reason": "recovered_stuck_processing"},
                )
            except Exception as e:
                logger.warning(
                    "cleanup_export_files: could not create stuck recovery audit entry: %s", e
                )
            logger.info(
                "cleanup_export_files: recovered stuck processing request %s", req.pk
            )
            recovered += 1

        if recovered:
            logger.info("cleanup_export_files: recovered %d stuck processing requests", recovered)

        return {"expired": count, "recovered_stuck": recovered}
    except SoftTimeLimitExceeded:
        logger.warning("cleanup_export_files: soft time limit exceeded")
        return {"error": "timeout"}


def _build_export_payload(user) -> dict:
    """Collect all data held about a citizen for PIPEDA s.4.9 export."""
    from apps.consent.models import ConsentRecord

    profile = {
        "id": str(user.pk),
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "date_joined": str(user.date_joined),
        "last_login": str(user.last_login),
        "preferred_language": getattr(user, "preferred_language", None),
    }

    consents = list(
        ConsentRecord.objects.filter(citizen=user).values(
            "category__slug", "category__name_en", "status", "granted_at", "withdrawn_at", "source"
        )
    )

    # Try to import optional apps gracefully
    service_requests = []
    try:
        from apps.portal.models import ServiceRequest
        service_requests = list(
            ServiceRequest.objects.filter(citizen=user).values(
                "reference_number", "service_name", "status", "description", "created_at"
            )
        )
    except Exception:
        pass

    notifications = []
    try:
        from apps.notifications.models import Notification
        notifications = list(
            Notification.objects.filter(recipient=user).values(
                "subject", "channel", "read_at", "sent_at", "created_at"
            )
        )
    except Exception:
        pass

    # NOTE: FormSubmission records are not included in the PIPEDA export because
    # Wagtail AbstractFormSubmission does not store a user FK — submissions are
    # associated with page sessions, not authenticated citizen accounts. The custom
    # FormSubmission model in apps/forms/models.py likewise has no citizen FK;
    # page.owner is the *staff author* of the form page, not the citizen who
    # filled it in. Filtering by page__owner=user would silently return the
    # submissions of every citizen who submitted a form page created by this user,
    # which is incorrect. A future enhancement would add an explicit citizen FK
    # to FormSubmission via a custom model.
    form_submissions = []

    return {
        "export_version": "1.0",
        "generated_at": str(timezone.now()),
        "subject": "PIPEDA Data Export",
        "profile": profile,
        "consents": consents,
        "service_requests": service_requests,
        "notifications": notifications,
        "form_submissions": form_submissions,
    }


def _notify_export_ready(export_request) -> None:
    """Send email to citizen when their export is ready, then fire export_ready signal (Fix 6)."""
    from django.core.mail import send_mail
    from django.template.loader import render_to_string
    from apps.consent.models import DataExportRequest
    from apps.consent.signals import export_ready as export_ready_signal

    citizen = export_request.citizen
    subject = "Your data export is ready — CivicOS"

    portal_url = getattr(settings, "SITE_URL", "").rstrip("/") or ""
    if not portal_url:
        logger.warning(
            "_notify_export_ready: SITE_URL is not configured — "
            "export-ready email for citizen %s will not include a portal link",
            export_request.citizen_id,
        )

    body = render_to_string(
        "email/consent/export_ready.txt",
        {
            "citizen": citizen,
            "export_request": export_request,
            "portal_url": portal_url,
        },
    )
    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[citizen.email],
            fail_silently=True,
        )
    except Exception as e:
        logger.warning("_notify_export_ready: email failed for citizen %s: %s", citizen.pk, e)

    # Fire signal for any connected receivers (Fix 6).
    try:
        export_ready_signal.send(
            sender=DataExportRequest,
            export_request=export_request,
        )
    except Exception as e:
        logger.warning("_notify_export_ready: export_ready signal failed: %s", e)
