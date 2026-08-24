"""
Celery tasks for the Consent & Privacy building block.

dispatch_consent_webhook — async HTTP POST for individual webhook deliveries
process_data_export      — generates the citizen's data export file
cleanup_export_files     — marks expired export requests and logs them
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
from datetime import timedelta
from urllib.parse import urlparse

import requests as _requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

logger = logging.getLogger(__name__)
User = get_user_model()

# Bug 4 (SSRF hardening) — same two extra ranges as
# apps.appointments.tasks._is_safe_outbound_url (see that module for the full
# rationale): RFC 6598 Shared Address Space / CGNAT and the IANA IETF
# Protocol Assignments block are NOT covered by ipaddress.ip_address's
# is_private/is_loopback/is_link_local/is_reserved/is_multicast/
# is_unspecified properties, yet are routable inside many cloud VPC /
# Kubernetes overlay networks and can reach internal infrastructure.
_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")
_IETF_PROTOCOL_ASSIGNMENTS = ipaddress.ip_network("192.0.0.0/24")


def _is_safe_outbound_url(url: str) -> bool:
    """
    Return True only if ``url`` is safe to issue an outbound HTTP POST to.

    This is a deliberate near-verbatim copy of
    ``apps.appointments.tasks._is_safe_outbound_url`` (also duplicated as
    ``apps.payments.govstack_tasks._is_safe_callback_url``) — there is no
    shared ``apps.core`` helper for this yet (confirmed via repo-wide
    search), and this per-BB duplication is the established precedent in
    this codebase for this exact class of check, so a third copy here is
    consistent rather than inventing a new pattern.

    Checks, in order (fails closed on ANY failure):
      1. scheme must be exactly "https" and a hostname must be present.
      2. The hostname is resolved via DNS (socket.getaddrinfo) — this is the
         TOCTOU-safe step: registration-time validation
         (``WebhookSerializer.validate_payloadUrl``, which only checks the
         HTTPS scheme) cannot catch a hostname that resolves to a private IP
         *at dispatch time*, since DNS can be repointed after registration.
      3. EVERY resolved IP address (a hostname may have multiple A/AAAA
         records) must be public and routable. Rejected ranges: private,
         loopback, link-local, reserved, multicast, and unspecified (the six
         ipaddress.ip_address properties), PLUS RFC 6598 Shared Address
         Space / CGNAT (100.64.0.0/10) and the IANA IETF Protocol
         Assignments block (192.0.0.0/24), which those six properties do
         NOT cover. A single unsafe address among several resolved
         addresses is enough to reject the whole URL.
      4. Any exception at all (malformed URL, DNS resolution failure, no
         addresses returned) is treated as unsafe.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            return False

        addrinfo = socket.getaddrinfo(parsed.hostname, None)
        if not addrinfo:
            return False

        for info in addrinfo:
            ip = ipaddress.ip_address(info[4][0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
                or ip in _SHARED_ADDRESS_SPACE
                or ip in _IETF_PROTOCOL_ASSIGNMENTS
            ):
                return False

        return True
    except Exception:
        return False


@shared_task(
    name="consent.dispatch_consent_webhook",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
    autoretry_for=(_requests.exceptions.RequestException,),
    retry_backoff=True,
    retry_backoff_max=60,
)
def dispatch_consent_webhook(
    self,  # noqa: ANN001
    webhook_pk: str,
    event_type: str,
    payload_dict: dict,
    event_timestamp: str,
) -> dict:
    """
    Deliver a single webhook payload via HTTP POST.

    Called by ``ConsentService.dispatch_webhook()`` via
    ``transaction.on_commit()`` for every active webhook subscribed to
    ``event_type``.

    Security invariants:
      - ``webhook.secret_key`` is fetched from DB inside the task (Fernet-
        decrypted at read time). It is NEVER passed as a task argument.
      - No PII is written to task arguments or log messages. Only
        ``webhook_pk`` (UUID) and ``event_type`` (string constant) are logged.

    Retry behaviour:
      - Retries up to 3 times on any ``requests.exceptions.RequestException``
        (network errors, timeouts) with exponential backoff.
      - ``acks_late=True`` + ``reject_on_worker_lost=True`` guarantee
        at-least-once delivery even if the worker is killed mid-task.

    On successful delivery (2xx response):
      - Persists ``last_payload``, ``last_delivery_at``, and
        ``last_delivery_status="success"`` on the ``ConsentWebhook`` row so
        that ``GET /config/webhook/{id}/payload/`` can replay it.

    On final failure (retries exhausted):
      - Updates ``last_delivery_status="failed"`` without overwriting
        ``last_payload`` (preserves the last successfully delivered payload).

    Args:
        webhook_pk:       PK (UUID str) of the ``ConsentWebhook`` to deliver to.
        event_type:       GovStack event type string (e.g. "consent.granted").
        payload_dict:     The event-specific payload dict (not the full body).
        event_timestamp:  ISO-8601 timestamp string set at dispatch time so
                          the body is consistent even if the task is delayed.
    """
    from apps.consent.models import ConsentWebhook

    # Fetch the webhook inside the task so the Fernet-decrypted secret_key
    # is never passed as a plain-text task argument.
    try:
        webhook = ConsentWebhook.objects.get(pk=webhook_pk)
    except ConsentWebhook.DoesNotExist:
        # Webhook was deleted between enqueue and execution — safe to discard.
        logger.info("dispatch_consent_webhook: webhook %s not found; skipping.", webhook_pk)
        return {"status": "skipped", "reason": "webhook_not_found"}

    if webhook.is_disabled:
        logger.info("dispatch_consent_webhook: webhook %s is disabled; skipping.", webhook_pk)
        return {"status": "skipped", "reason": "webhook_disabled"}

    # Bug 4 (SSRF hardening): re-validate the destination immediately before
    # the outbound call, since DNS can be repointed at any time after the
    # webhook was registered (registration-time validation in
    # WebhookSerializer.validate_payloadUrl only checks the HTTPS scheme).
    # A URL that fails this check is skipped — logged as a warning, never
    # raised — so a malicious/misconfigured webhook cannot crash or retry
    # the task; it simply never gets dispatched.
    if not _is_safe_outbound_url(webhook.payload_url):
        logger.warning(
            "dispatch_consent_webhook: webhook %s payload_url failed SSRF safety "
            "check; skipping dispatch.",
            webhook_pk,
        )
        return {"status": "skipped", "reason": "unsafe_url"}

    # Build the signed body using the original event timestamp so that the
    # HMAC signature matches what the subscriber would expect regardless of
    # Celery task queue delay.
    body = json.dumps(
        {
            "event": event_type,
            "timestamp": event_timestamp,
            "payload": payload_dict,
        },
        default=str,
    )
    sig = hmac.new(
        webhook.secret_key.encode(),
        body.encode(),
        hashlib.sha256,
    ).hexdigest()

    sig_header = webhook.signature_header or "X-GovStack-Signature"
    try:
        resp = _requests.post(
            webhook.payload_url,
            data=body,
            headers={
                "Content-Type": webhook.content_type,
                sig_header: f"sha256={sig}",
                "X-GovStack-Event": event_type,
            },
            timeout=10,
            # Bug 4 (SSRF hardening): never follow redirects. _is_safe_outbound_url()
            # only validates the ORIGINAL url's scheme/DNS/IP; it has no visibility
            # into a response's Location header. Since requests follows redirects
            # by default, a webhook registered against a public HTTPS host that
            # passes validation could have that host respond with a 3xx redirecting
            # to a private IP or the cloud metadata endpoint (169.254.169.254),
            # transparently defeating the SSRF control. allow_redirects=False closes
            # this — the redirect is never followed. See the explicit 3xx check
            # immediately below: requests.Response.ok is True for ANY status code
            # < 400 (including 3xx — it is not a "2xx only" check), so without
            # this explicit check a 3xx response would be silently recorded as a
            # successful delivery, defeating the point of not following it.
            allow_redirects=False,
        )
    except _requests.exceptions.RequestException:
        # Will be retried via autoretry_for; update status to "failed" only on
        # final exhaustion (handled in on_failure below).
        raise

    if 300 <= resp.status_code < 400:
        # A validated-safe URL that 3xx-redirects to an internal target must
        # never be silently treated as delivered — see allow_redirects=False
        # comment above. resp.ok would NOT catch this (it is only False for
        # >= 400), so this is deliberately checked before the resp.ok branch.
        ConsentWebhook.objects.filter(pk=webhook_pk).update(
            last_delivery_status="failed",
        )
        logger.warning(
            "dispatch_consent_webhook: webhook %s event %s received a %s redirect "
            "response; not followed (allow_redirects=False), treated as failed.",
            webhook_pk,
            event_type,
            resp.status_code,
        )
        return {"status": "receiver_error", "http_status": resp.status_code}

    if resp.ok:
        # Persist the last successfully delivered payload for replay via
        # GET /config/webhook/{id}/payload/.
        # Use .update() to avoid a full model save; only touch the 3 new fields.
        ConsentWebhook.objects.filter(pk=webhook_pk).update(
            last_payload=json.loads(body),
            last_delivery_at=timezone.now(),
            last_delivery_status="success",
        )
        logger.debug(
            "dispatch_consent_webhook: delivered event %s to webhook %s (HTTP %s).",
            event_type,
            webhook_pk,
            resp.status_code,
        )
        return {"status": "delivered", "http_status": resp.status_code}
    else:
        # Non-2xx response from the receiver.  Do NOT autoretry on non-2xx —
        # that is a receiver-side logic error, not a network error.
        # Log it and record the failure in the replay log.
        ConsentWebhook.objects.filter(pk=webhook_pk).update(
            last_delivery_status="failed",
        )
        logger.warning(
            "dispatch_consent_webhook: receiver returned HTTP %s for webhook %s event %s.",
            resp.status_code,
            webhook_pk,
            event_type,
        )
        return {"status": "receiver_error", "http_status": resp.status_code}


@shared_task(name="consent.process_data_export", bind=True, max_retries=2)
def process_data_export(self, export_request_id: str) -> dict:  # noqa: ANN001
    """
    Generate and store a citizen's PIPEDA data export package.

    Collects: profile, consent history, service requests,
    form submissions, notifications, audit entries.
    Stores result in default_storage with a 7-day TTL.
    Sends email notification when ready.
    """
    from apps.consent.models import DataExportRequest

    try:
        req = DataExportRequest.objects.select_related("citizen").get(pk=export_request_id)
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
                export_request_id,
                req.status,
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
            # DELIBERATE, DOCUMENTED EXCEPTION to the Documents BB's prefix
            # semantics. For that BB's untrusted-upload pipeline,
            # "documents/active/" means "this object was streamed through ClamAV
            # and came back clean" — see
            # apps/documents/tasks._promote_storage_object_to_active(), the only
            # code path that promotes an upload out of "documents/quarantine/".
            # This export file never enters that pipeline and is never virus
            # scanned, by design: `content` is JSON this task just serialised
            # from the citizen's own database rows (_build_export_payload), not
            # client-supplied file content. Writing straight to
            # "documents/active/" is therefore correct here — but do NOT take
            # this as evidence that "active/" simply means "generally available"
            # for citizen/staff uploads. It does not.
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
        raise self.retry(exc=exc, countdown=300)  # noqa: B904


@shared_task(name="consent.cleanup_export_files", bind=True, max_retries=3)
def cleanup_export_files(self) -> dict:  # noqa: ANN001
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
                        "cleanup_export_files: mark_purpose_fulfilled failed for document pk=%s: %s",  # noqa: E501
                        req.document_id,
                        type(exc).__name__,
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
            logger.info("cleanup_export_files: recovered stuck processing request %s", req.pk)
            recovered += 1

        if recovered:
            logger.info("cleanup_export_files: recovered %d stuck processing requests", recovered)

        return {"expired": count, "recovered_stuck": recovered}
    except SoftTimeLimitExceeded:
        logger.warning("cleanup_export_files: soft time limit exceeded")
        return {"error": "timeout"}
    except Exception as exc:
        logger.exception("cleanup_export_files: failed: %s", exc)
        raise self.retry(exc=exc, countdown=600)  # noqa: B904


def _build_export_payload(user) -> dict:  # noqa: ANN001
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

    consent_records = list(ConsentRecord.objects.filter(citizen=user))
    consents = [
        {
            "category__slug": r.category.slug,
            "category__name_en": r.category.name_en,
            "status": r.status,
            "granted_at": r.granted_at,
            "withdrawn_at": r.withdrawn_at,
            "source": r.source,
        }
        for r in consent_records
    ]

    # BUGFIX (item 6 hardening round): the three sections below each import an
    # optional app (apps.portal / apps.notifications may not be installed in
    # every CivicOS deployment) and query it. The previous code used a bare
    # `except Exception: pass` for all three — meaning a genuine query bug
    # (a renamed field, a broken migration, a DB error) would be silently
    # swallowed exactly like a legitimate "app not installed" ImportError,
    # producing a PIPEDA s.4.9 access-request export that is silently
    # INCOMPLETE with no trace in the logs. For a legally-mandated data
    # export this is a real defect, not just noisy logging — the citizen
    # (and the org responding to their request) would have no way to know
    # data was missing. Fixed by only swallowing the two "expected, optional
    # app" exception types (ImportError / ModuleNotFoundError) silently, and
    # logging (not swallowing) anything else so a real bug surfaces instead
    # of vanishing.  A single section's genuine failure still does not abort
    # the whole export — the citizen's other data is more useful delivered
    # incomplete-but-flagged than not delivered at all — but it is now always
    # visible in the logs.
    service_requests = []
    try:
        from apps.portal.models import ServiceRequest

        service_requests = list(
            ServiceRequest.objects.filter(citizen=user).values(
                "reference_number", "service_name", "status", "description", "created_at"
            )
        )
    except ImportError:
        pass
    except Exception:
        logger.exception(
            "_build_export_payload: service_requests section failed for user pk=%s "
            "(NOT an ImportError — this is a genuine bug, export will omit this section)",
            user.pk,
        )

    notifications = []
    try:
        from apps.notifications.models import Notification

        notifications = list(
            Notification.objects.filter(recipient=user).values(
                "subject", "channel", "read_at", "sent_at", "created_at"
            )
        )
    except ImportError:
        pass
    except Exception:
        logger.exception(
            "_build_export_payload: notifications section failed for user pk=%s "
            "(NOT an ImportError — this is a genuine bug, export will omit this section)",
            user.pk,
        )

    consent_audit_entries = []
    try:
        from apps.consent.models import ConsentAuditEntry

        consent_audit_entries = list(
            ConsentAuditEntry.objects.filter(citizen=user)
            .order_by("-timestamp")
            .values("action", "category__slug", "actor_ip", "timestamp", "details")
        )
        # Convert datetime objects to strings for JSON serialization
        for entry in consent_audit_entries:
            if entry.get("timestamp"):
                entry["timestamp"] = str(entry["timestamp"])
    except ImportError:
        pass
    except Exception:
        logger.exception(
            "_build_export_payload: consent_audit_entries section failed for user pk=%s "
            "(NOT an ImportError — this is a genuine bug, export will omit this section)",
            user.pk,
        )

    # GAP FIX (item 6): the export previously included ConsentRecord rows
    # (`consents` above) and ConsentAuditEntry rows, but neither the
    # ConsentRevision snapshots (the tamper-evident, hashed history of every
    # state transition each of the citizen's ConsentRecords went through) nor
    # the ConsentSignature rows (the actual signature payloads GovStack
    # attaches to a signed ConsentRecord) were ever included. Per PIPEDA
    # s.4.9, a citizen is entitled to the actual record of what they signed
    # and when — the revision chain and signature are exactly that record,
    # not just the current-state summary `consents` provides.
    revisions = []
    signatures = []
    try:
        from apps.consent.models import ConsentRevision, ConsentSignature

        record_ids = [str(r.pk) for r in consent_records]
        if record_ids:
            # NOTE (Bug 7, RTBF/PIPEDA erasure): this only queries revisions
            # for ConsentRecords that STILL EXIST for this citizen. If a
            # citizen previously exercised their Right to be Forgotten
            # (ConsentService.right_to_be_forgotten), the underlying
            # ConsentRecord rows for forgettable categories were deleted, and
            # any ConsentRevision snapshots that referenced them were
            # explicitly redacted in place via ConsentRevision.redact_pii()
            # at RTBF time (their citizen-identifying content was scrubbed,
            # not the rows themselves — see models.py's redact_pii()
            # docstring). Those revisions are deliberately excluded here
            # because there is no more PII left in them to export — the
            # citizen explicitly requested erasure of exactly that data, not
            # access to it. Their absence from this export is therefore
            # correct/expected, not an access-right gap.
            revisions = list(
                ConsentRevision.objects.filter(
                    schema_name="ConsentRecord", object_id__in=record_ids
                )
                .order_by("-timestamp")
                .values(
                    "id",
                    "object_id",
                    "serialized_snapshot",
                    "serialized_hash",
                    "timestamp",
                    "predecessor_hash",
                )
            )
            for rev in revisions:
                rev["id"] = str(rev["id"])
                rev["timestamp"] = str(rev["timestamp"])

            signatures = list(
                ConsentSignature.objects.filter(consent_record__in=consent_records)
                .order_by("-timestamp")
                .values(
                    "id",
                    "consent_record_id",
                    "payload",
                    "signature",
                    "verification_type",
                    "verification_signed_as",
                    "verification_signed_by",
                    "timestamp",
                )
            )
            for sig in signatures:
                sig["id"] = str(sig["id"])
                sig["consent_record_id"] = str(sig["consent_record_id"])
                sig["timestamp"] = str(sig["timestamp"])
    except ImportError:
        pass
    except Exception:
        logger.exception(
            "_build_export_payload: revisions/signatures section failed for user pk=%s "
            "(NOT an ImportError — this is a genuine bug, export will omit this section)",
            user.pk,
        )

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
        "consent_revisions": revisions,
        "consent_signatures": signatures,
        "service_requests": service_requests,
        "notifications": notifications,
        "consent_audit_entries": consent_audit_entries,
        "form_submissions": form_submissions,
    }


def _notify_export_ready(export_request) -> None:  # noqa: ANN001
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
