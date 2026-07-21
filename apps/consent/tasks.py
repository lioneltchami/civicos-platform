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
        # Idempotency guard — do not reprocess terminal-state exports.
        if req.status in (
            DataExportRequest.STATUS_READY,
            DataExportRequest.STATUS_DELIVERED,
            DataExportRequest.STATUS_EXPIRED,
        ):
            logger.info(
                "process_data_export: %s is already %s — skipping.",
                export_request_id, req.status,
            )
            return {"skipped": True, "status": req.status}

        # M-06: idempotency — if a document was already written in a previous
        # attempt (failed after storage write but before status update), reuse it
        # rather than creating a duplicate file.
        if req.document_id:
            _ttl = getattr(settings, "DATA_EXPORT_TTL_DAYS", 7)
            now = timezone.now()
            req.status = DataExportRequest.STATUS_READY
            req.processed_at = now
            req.expires_at = now + timedelta(days=_ttl)
            req.save(update_fields=["status", "processed_at", "expires_at"])
            _notify_export_ready(req)
            return {"status": "ready", "export_request_id": str(req.pk)}

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

        # Documents BB integration: create a Document record for this export.
        # Category "pipeda-data-export" is transitory (is_transitory=True).
        # System-generated exports skip virus scan (scan_status=ACTIVE immediately).
        # PIPEDA: original_filename is NOT logged; storage_key is NOT logged.
        import uuid as _uuid
        from apps.documents.models import Document, DocumentCategory
        from apps.documents.services.retention import schedule_expiry as _schedule_expiry

        _doc = None
        _category = DocumentCategory.objects.filter(slug="pipeda-data-export").first()
        if _category is None:
            logger.warning(
                "process_data_export: DocumentCategory 'pipeda-data-export' not found; "
                "falling back to storage_path only (category not seeded yet)"
            )
        else:
            _doc_storage_key = f"documents/active/{req.pk}/{_uuid.uuid4().hex}.bin"
            # Write the export bytes under the Documents BB storage key.
            default_storage.save(_doc_storage_key, ContentFile(content))
            _doc = Document.objects.create(
                category=_category,
                uploaded_by=req.citizen,
                original_filename=f"export-{req.pk}.json",  # NOT logged per PIPEDA
                _storage_key=_doc_storage_key,
                mime_type="application/json",
                size_bytes=len(content),
                scan_status=Document.ScanStatus.ACTIVE,
                version_number=1,
                is_latest_version=True,
                security_classification="protected_b",
            )
            _schedule_expiry(document=_doc)

        # Mark ready — wrap save + audit in a single atomic block so that a failure
        # on either step leaves no committed state without a corresponding audit trail.
        from django.db import transaction as _transaction
        now = timezone.now()
        _update_fields = ["status", "processed_at", "expires_at"]
        if _doc is not None:
            _update_fields.append("document")
        _ttl = getattr(settings, "DATA_EXPORT_TTL_DAYS", 7)
        req.status = DataExportRequest.STATUS_READY
        req.processed_at = now
        req.expires_at = now + timedelta(days=_ttl)
        if _doc is not None:
            req.document = _doc
        try:
            from apps.consent.models import ConsentAuditEntry
            with _transaction.atomic():
                req.save(update_fields=_update_fields)
                ConsentAuditEntry.objects.create(
                    citizen=req.citizen,
                    action="export_ready",
                    export_request=req,
                    details={"size_bytes": len(content)},
                )
        except Exception:
            # Clean up the orphaned legacy file before the task re-raises and retries.
            try:
                default_storage.delete(saved_path)
            except Exception as del_err:
                logger.warning(
                    "process_data_export: could not delete orphaned file: %s",
                    type(del_err).__name__,  # Don't log the path (contains token)
                )
            # Clean up the orphaned Document BB record and its storage file.
            if _doc is not None:
                try:
                    default_storage.delete(_doc._storage_key)
                except Exception as del_err:
                    logger.warning(
                        "process_data_export: could not delete orphaned doc file: %s",
                        type(del_err).__name__,
                    )
                try:
                    _doc.delete()
                except Exception as del_err:
                    logger.warning(
                        "process_data_export: could not delete orphaned Document record: %s",
                        type(del_err).__name__,
                    )
            raise

        # Clean up the legacy storage file now that the Document BB record is the
        # authoritative copy. The file at saved_path (exports/{token}.json) is a
        # redundant copy written before the Documents BB integration. Once the DB
        # transaction succeeds, both copies exist; retaining both violates PIPEDA
        # data-minimisation (4.5.3) — the Documents BB lifecycle manages the
        # authoritative copy. If _doc is None (category not seeded), saved_path is
        # the ONLY copy and must not be deleted.
        if _doc is not None:
            try:
                default_storage.delete(saved_path)
            except Exception as _del_err:
                logger.warning(
                    "process_data_export: could not clean up legacy export file: %s",
                    type(_del_err).__name__,  # Don't log saved_path (contains download token)
                )

        # Notify citizen by email
        _notify_export_ready(req)

        logger.info("process_data_export: completed")
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
                details={"reason": type(exc).__name__},
            )
        except Exception as audit_exc:
            logger.warning(
                "process_data_export: could not write export_failed audit entry: %s",
                audit_exc,
            )
        raise self.retry(exc=exc, countdown=300)


@shared_task(name="consent.cleanup_export_files", bind=True, max_retries=3)
def cleanup_export_files(self) -> dict:
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
        for req in expired_qs.select_related("document__category", "citizen"):
            if req.document_id and req.document.category and req.document.category.is_transitory:
                try:
                    from apps.documents.services.retention import mark_purpose_fulfilled
                    # PIPEDA: actor is the citizen who owns the export request.
                    # Passing actor=None would crash at actor.pk inside mark_purpose_fulfilled()
                    # (the function signature is non-Optional). Using req.citizen records the
                    # correct disposal actor and keeps the audit trail coherent.
                    mark_purpose_fulfilled(document=req.document, actor=req.citizen)
                except Exception as exc:
                    logger.error(
                        "cleanup_export_files: mark_purpose_fulfilled failed for document pk=%s: %s",
                        req.document_id, type(exc).__name__,
                    )
            req.status = DataExportRequest.STATUS_EXPIRED
            req.save(update_fields=["status"])
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
    except Exception as exc:
        logger.exception("cleanup_export_files: failed: %s", exc)
        raise self.retry(exc=exc, countdown=600)


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

    consent_audit_entries = []
    try:
        from apps.consent.models import ConsentAuditEntry
        consent_audit_entries = list(
            ConsentAuditEntry.objects.filter(citizen=user).order_by("-timestamp").values(
                "action", "category__slug", "actor_ip", "timestamp", "details"
            )
        )
        # Convert datetime objects to strings for JSON serialization
        for entry in consent_audit_entries:
            if entry.get("timestamp"):
                entry["timestamp"] = str(entry["timestamp"])
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
        "consent_audit_entries": consent_audit_entries,
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
            "export-ready email will not include a portal link",
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
        logger.warning("_notify_export_ready: email failed: %s", type(e).__name__)

    # Fire signal for any connected receivers (Fix 6).
    try:
        export_ready_signal.send(
            sender=DataExportRequest,
            export_request=export_request,
        )
    except Exception as e:
        logger.warning("_notify_export_ready: export_ready signal failed: %s", e)
