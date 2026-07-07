"""
ConsentService — the single place all consent business logic lives.

PIPEDA requirements enforced here:
  * Required categories (is_required=True) cannot be withdrawn
  * Every state change is recorded in ConsentAuditEntry
  * IPs are masked before storage
  * Only one pending DataExportRequest per citizen at a time

GovStack Consent BB v1.3.0 extensions:
  * create_policy()           — Policy CRUD
  * create_data_agreement()   — DataAgreement CRUD (wraps ConsentCategory)
  * create_revision()         — Snapshot any object into a ConsentRevision
  * right_to_be_forgotten()   — RTBF / cascading delete of forgettable records
  * dispatch_webhook()        — Fire webhooks for a given event type
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Optional

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.forms.utils import _mask_ip

logger = logging.getLogger(__name__)


class ConsentService:

    # ------------------------------------------------------------------
    # Existing PIPEDA operations
    # ------------------------------------------------------------------

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
        to the citizen.
        """
        from apps.consent.models import ConsentAuditEntry, ConsentCategory, ConsentRecord
        from apps.consent.signals import consent_granted

        category = ConsentCategory.objects.filter(slug=category_slug, is_active=True).first()
        if not category:
            raise ValueError(f"Unknown or inactive consent category: {category_slug!r}")

        ip = _mask_ip(_get_ip(request) or "")

        with transaction.atomic():
            # Get the current DataAgreement revision for this category
            revision = ConsentService._get_latest_revision(category)

            record, created = ConsentRecord.objects.get_or_create(
                citizen=citizen,
                category=category,
                defaults={
                    "status": ConsentRecord.STATUS_GRANTED,
                    "granted_at": timezone.now(),
                    "actor_ip": ip,
                    "source": _get_source(request),
                    "consent_version": consent_version,
                    "data_agreement_revision": revision,
                    "data_agreement_revision_hash": revision.serialized_hash if revision else "",
                    "state": ConsentRecord.STATE_SIGNED,
                },
            )
            state_changed = created or record.status != ConsentRecord.STATUS_GRANTED
            if not created and record.status != ConsentRecord.STATUS_GRANTED:
                record.status = ConsentRecord.STATUS_GRANTED
                record.granted_at = timezone.now()
                record.actor_ip = ip
                record.source = _get_source(request)
                record.state = ConsentRecord.STATE_SIGNED
                update_fields = ["status", "granted_at", "actor_ip", "source", "state"]
                if consent_version:
                    record.consent_version = consent_version
                    update_fields.append("consent_version")
                if revision and not record.data_agreement_revision_id:
                    record.data_agreement_revision = revision
                    record.data_agreement_revision_hash = revision.serialized_hash
                    update_fields += ["data_agreement_revision", "data_agreement_revision_hash"]
                record.save(update_fields=update_fields)

            if state_changed:
                ConsentAuditEntry.objects.create(
                    citizen=citizen,
                    actor=citizen,
                    action="granted",
                    category=category,
                    actor_ip=ip,
                    details={"category_slug": category_slug, "source": _get_source(request)},
                )

            # Fire webhook INSIDE atomic block so on_commit defers until transaction commits.
            # If called outside atomic(), on_commit fires immediately (Django docs).
            _record_pk = str(record.pk)
            _citizen_pk = str(citizen.pk)
            transaction.on_commit(lambda: ConsentService.dispatch_webhook("consent.granted", {
                "consent_record_id": _record_pk,
                "category_slug": category_slug,
                "individual_id": _citizen_pk,
            }))

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

            if record.status == ConsentRecord.STATUS_PENDING:
                raise ValueError("Cannot withdraw consent that was never granted.")

            if record.status != ConsentRecord.STATUS_GRANTED:
                return record

            record.status = ConsentRecord.STATUS_WITHDRAWN
            record.state = ConsentRecord.STATE_REVOKED
            record.withdrawn_at = timezone.now()
            record.actor_ip = ip
            record.source = _get_source(request)
            record.save(update_fields=["status", "state", "withdrawn_at", "actor_ip", "source"])

            ConsentAuditEntry.objects.create(
                citizen=citizen,
                actor=citizen,
                action="withdrawn",
                category=category,
                actor_ip=ip,
                details={"category_slug": category_slug, "source": _get_source(request)},
            )

            # Fire webhook INSIDE atomic block so on_commit defers until transaction commits.
            _record_pk = str(record.pk)
            _citizen_pk = str(citizen.pk)
            transaction.on_commit(lambda: ConsentService.dispatch_webhook("consent.withdrawn", {
                "consent_record_id": _record_pk,
                "category_slug": category_slug,
                "individual_id": _citizen_pk,
            }))

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

        def _send_export_requested():
            export_requested.send(
                sender=DataExportRequest,
                export_request=export_req,
                request=request,
            )
            process_data_export.delay(str(export_req.pk))
            ConsentService.dispatch_webhook("consent.export.requested", {
                "export_request_id": str(export_req.pk),
                "individual_id": str(citizen.pk),
            })

        transaction.on_commit(_send_export_requested)
        return export_req

    @staticmethod
    def get_citizen_exports(citizen):
        """Return all DataExportRequests for citizen, newest first."""
        from apps.consent.models import DataExportRequest

        return DataExportRequest.objects.filter(citizen=citizen).only(
            "id", "status", "format", "requested_at", "processed_at",
            "expires_at", "download_token",
        ).order_by("-requested_at")

    # ------------------------------------------------------------------
    # GovStack: Policy management
    # ------------------------------------------------------------------

    @staticmethod
    def create_policy(data: dict, actor=None) -> tuple:
        """
        Create a new ConsentPolicy and an initial ConsentRevision.

        Returns (policy, revision).
        """
        from apps.consent.models import ConsentPolicy, ConsentRevision

        with transaction.atomic():
            policy = ConsentPolicy.objects.create(**data)
            revision = ConsentRevision.create_for(
                schema_name="Policy",
                obj=policy,
                snapshot=_policy_snapshot(policy),
                authorized_by=actor,
                authorized_by_other="" if actor else "system",
            )
        return policy, revision

    @staticmethod
    def update_policy(policy, data: dict, actor=None) -> tuple:
        """
        Update an existing policy and create a new revision.

        Returns (updated_policy, new_revision).
        """
        from apps.consent.models import ConsentRevision

        with transaction.atomic():
            for field, value in data.items():
                setattr(policy, field, value)
            policy.save()
            revision = ConsentRevision.create_for(
                schema_name="Policy",
                obj=policy,
                snapshot=_policy_snapshot(policy),
                authorized_by=actor,
                authorized_by_other="" if actor else "system",
            )
        return policy, revision

    # ------------------------------------------------------------------
    # GovStack: DataAgreement management
    # ------------------------------------------------------------------

    @staticmethod
    def create_data_agreement(data: dict, actor=None) -> tuple:
        """
        Create a new ConsentCategory (DataAgreement) and an initial ConsentRevision.

        Returns (category, revision).
        """
        from apps.consent.models import ConsentCategory, ConsentRevision

        with transaction.atomic():
            category = ConsentCategory.objects.create(**data)
            revision = ConsentRevision.create_for(
                schema_name="DataAgreement",
                obj=category,
                snapshot=_data_agreement_snapshot(category),
                authorized_by=actor,
                authorized_by_other="" if actor else "system",
            )
        return category, revision

    @staticmethod
    def update_data_agreement(category, data: dict, actor=None) -> tuple:
        """
        Update an existing DataAgreement and create a new revision.

        Updating a DataAgreement does NOT affect existing active ConsentRecords —
        they remain linked to their original revision.

        Returns (updated_category, new_revision).
        """
        from apps.consent.models import ConsentRevision

        with transaction.atomic():
            for field, value in data.items():
                setattr(category, field, value)
            category.save()
            revision = ConsentRevision.create_for(
                schema_name="DataAgreement",
                obj=category,
                snapshot=_data_agreement_snapshot(category),
                authorized_by=actor,
                authorized_by_other="" if actor else "system",
            )
        return category, revision

    # ------------------------------------------------------------------
    # GovStack: Revision helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_latest_revision(category):
        """Return the latest ConsentRevision for a DataAgreement (or None)."""
        from apps.consent.models import ConsentRevision

        return (
            ConsentRevision.objects.filter(
                schema_name="DataAgreement",
                object_id=str(category.pk),
                successor__isnull=True,
            )
            .order_by("-timestamp")
            .first()
        )

    # ------------------------------------------------------------------
    # GovStack: Right to Be Forgotten (RTBF)
    # ------------------------------------------------------------------

    @staticmethod
    def right_to_be_forgotten(citizen, request=None) -> dict:
        """
        GovStack service/individual/record/ DELETE — right to be forgotten.

        Deletes all ConsentRecord rows where the associated ConsentCategory
        has forgettable=True. Records for required or non-forgettable
        categories are NOT deleted (they are needed for audit/legal purposes).

        Also writes a ConsentAuditEntry for the RTBF action.

        Returns a summary dict: {deleted_count, retained_count, category_slugs_deleted}.
        """
        from apps.consent.models import ConsentAuditEntry, ConsentRecord

        ip = _mask_ip(_get_ip(request) or "")

        with transaction.atomic():
            forgettable_records = ConsentRecord.objects.filter(
                citizen=citizen,
                category__forgettable=True,
            ).select_related("category")

            deleted_slugs = [r.category.slug for r in forgettable_records]
            deleted_count = len(deleted_slugs)

            # Bypass the immutability guard by using QuerySet.delete()
            # (ConsentRecord.delete() raises ValueError but qs.delete() is allowed).
            ConsentRecord.objects.filter(
                citizen=citizen,
                category__forgettable=True,
            ).delete()

            retained_count = ConsentRecord.objects.filter(citizen=citizen).count()

            ConsentAuditEntry.objects.create(
                citizen=citizen,
                actor=citizen,
                action="rtbf_requested",
                actor_ip=ip,
                details={
                    "categories_deleted": deleted_slugs,
                    "deleted_count": deleted_count,
                },
            )
            if deleted_count > 0:
                ConsentAuditEntry.objects.create(
                    citizen=citizen,
                    actor=citizen,
                    action="rtbf_completed",
                    actor_ip=ip,
                    details={
                        "categories_deleted": deleted_slugs,
                        "deleted_count": deleted_count,
                        "retained_count": retained_count,
                    },
                )

        return {
            "deleted_count": deleted_count,
            "retained_count": retained_count,
            "category_slugs_deleted": deleted_slugs,
        }

    # ------------------------------------------------------------------
    # GovStack: Webhook dispatch
    # ------------------------------------------------------------------

    @staticmethod
    def dispatch_webhook(event_type: str, payload: dict) -> None:
        """
        Fire all active webhooks subscribed to ``event_type``.

        Uses HMAC-SHA256 to sign the payload with each webhook's secret_key.
        Failures are logged but never re-raised — webhook errors must not
        affect the main request flow.
        """
        from apps.consent.models import ConsentWebhook

        webhooks = ConsentWebhook.objects.filter(is_disabled=False)
        for webhook in webhooks:
            if not webhook.is_subscribed_to(event_type):
                continue
            try:
                body = json.dumps({
                    "event": event_type,
                    "timestamp": str(timezone.now()),
                    "payload": payload,
                }, default=str)
                sig = hmac.new(
                    webhook.secret_key.encode(),
                    body.encode(),
                    hashlib.sha256,
                ).hexdigest()
                import requests as _requests
                sig_header = webhook.signature_header or "X-GovStack-Signature"
                _requests.post(
                    webhook.payload_url,
                    data=body,
                    headers={
                        "Content-Type": webhook.content_type,
                        sig_header: f"sha256={sig}",
                        "X-GovStack-Event": event_type,
                    },
                    timeout=5,
                )
            except Exception as exc:
                logger.warning(
                    "dispatch_webhook: failed for webhook %s event %s: %s",
                    webhook.pk, event_type, exc,
                )


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


def _policy_snapshot(policy) -> dict:
    """Serialize a ConsentPolicy to a dict for revision snapshots."""
    return {
        "id": str(policy.pk),
        "name": policy.name,
        "description": policy.description,
        "version": policy.version,
        "url": policy.url,
        "jurisdiction": policy.jurisdiction,
        "industry_sector": policy.industry_sector,
        "data_retention_period_days": policy.data_retention_period_days,
        "geographic_restriction": policy.geographic_restriction,
        "storage_location": policy.storage_location,
        "third_party_data_sharing": policy.third_party_data_sharing,
    }


def _data_agreement_snapshot(category) -> dict:
    """Serialize a ConsentCategory (DataAgreement) to a dict for revision snapshots."""
    return {
        "id": str(category.pk),
        "slug": category.slug,
        "version": category.version,
        "language": category.language,
        "lifecycle": category.lifecycle,
        "name_en": category.name_en,
        "name_fr": category.name_fr,
        "purpose_en": category.purpose_en,
        "purpose_fr": category.purpose_fr,
        "purpose_description": category.purpose_description,
        "lawful_basis": category.lawful_basis,
        "data_use": category.data_use,
        "data_use_purpose": category.data_use_purpose,
        "data_use_purpose_description": category.data_use_purpose_description,
        "data_use_purpose_restriction": category.data_use_purpose_restriction,
        "data_use_activity": category.data_use_activity,
        "data_usage_policy": category.data_usage_policy,
        "dpia": category.dpia,
        "dpia_date": str(category.dpia_date) if category.dpia_date else None,
        "dpia_evidence_url": category.dpia_evidence_url,
        "dpia_summary_url": category.dpia_summary_url,
        "dpia_url": category.dpia_url,
        "is_required": category.is_required,
        "is_active": category.is_active,
        "forgettable": category.forgettable,
        "controller_name": category.controller_name,
        "controller_url": category.controller_url,
        "data_controller_logo_image_url": category.data_controller_logo_image_url,
        "policy_id": str(category.policy_id) if category.policy_id else None,
        "attributes": category.attributes or [],
    }
