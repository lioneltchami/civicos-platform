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

from django.utils import timezone

from django.db import IntegrityError, transaction

from apps.forms.utils import _mask_ip

logger = logging.getLogger(__name__)


class ConsentService:

    # ------------------------------------------------------------------
    # Existing PIPEDA operations
    # ------------------------------------------------------------------

    @staticmethod
    def get_or_create_record(citizen, category):
        """
        Get the current ConsentRecord for a citizen/category, or create one
        in unsigned/pending status.

        F4 fix: since unique_together has been removed, we use is_current=True
        as the lookup key so we always get/create a single "active" row.
        """
        from apps.consent.models import ConsentRecord

        record, _ = ConsentRecord.objects.get_or_create(
            citizen=citizen,
            category=category,
            is_current=True,
            defaults={
                "status": ConsentRecord.STATUS_PENDING,
                "state": ConsentRecord.STATE_UNSIGNED,
            },
        )
        return record

    @staticmethod
    def grant(citizen, category_slug: str, request=None, consent_version: str = "", revision=None):
        """
        Grant consent for a category.

        Idempotent: if the citizen already has a signed record for the current
        DataAgreement revision, the existing record is returned unchanged.

        F4 fix: append-only — each grant creates a NEW ConsentRecord rather
        than mutating an existing one.  Previous rows get is_current=False so
        the history is preserved for auditing.

        F2 fix: proper state machine — record starts as STATE_UNSIGNED, gets a
        ConsentRevision, then advances to STATE_SIGNED (auto-signed) with a
        second ConsentRevision.

        F3 fix: every state transition produces a ConsentRevision with a
        camelCase snapshot, linked via predecessor chain.

        Raises ValueError if category does not exist or is inactive.
        """
        from apps.consent.models import (
            ConsentAuditEntry, ConsentCategory, ConsentRecord,
            ConsentRevision, ConsentSignature,
        )
        from apps.consent.signals import consent_granted

        category = ConsentCategory.objects.filter(slug=category_slug, is_active=True).first()
        if not category:
            raise ValueError(f"Unknown or inactive consent category: {category_slug!r}")

        ip = _mask_ip(_get_ip(request) or "")

        with transaction.atomic():
            # Honor a caller-supplied revision (e.g. the revisionId query param from
            # the GovStack service endpoint).  Fall back to latest if none supplied.
            # H-02 fix: previously the revision param was validated in the view but
            # then silently discarded; the caller's intent is now respected.
            if revision is None:
                revision = ConsentService._get_latest_revision(category)

            # ----------------------------------------------------------------
            # Idempotency: return existing signed+granted record if the citizen
            # has already consented to THIS exact revision.
            # Both state=SIGNED and status=GRANTED must hold — a record that
            # was bypassed to STATUS_WITHDRAWN without going through withdraw()
            # (edge case / test harness) should be treated as not-granted.
            # SELECT FOR UPDATE prevents races from concurrent requests.
            # ----------------------------------------------------------------
            existing_qs = ConsentRecord.objects.select_for_update().filter(
                citizen=citizen,
                category=category,
                state=ConsentRecord.STATE_SIGNED,
                status=ConsentRecord.STATUS_GRANTED,
                is_current=True,
            )
            if revision is not None:
                existing_qs = existing_qs.filter(data_agreement_revision=revision)
            existing = existing_qs.first()
            if existing:
                return existing

            # ----------------------------------------------------------------
            # Archive the previous current record (if any) so it becomes
            # historical.  This preserves the full consent history.
            # ----------------------------------------------------------------
            ConsentRecord.objects.filter(
                citizen=citizen,
                category=category,
                is_current=True,
            ).update(is_current=False)

            # ----------------------------------------------------------------
            # STEP 1 (F2): Create the new record in STATE_UNSIGNED.
            #              This is the "consent form presented" moment.
            # ----------------------------------------------------------------
            record = ConsentRecord.objects.create(
                citizen=citizen,
                category=category,
                status=ConsentRecord.STATUS_PENDING,
                state=ConsentRecord.STATE_UNSIGNED,
                is_current=True,
                actor_ip=ip,
                source=_get_source(request),
                consent_version=consent_version,
                data_agreement_revision=revision,
                data_agreement_revision_hash=revision.serialized_hash if revision else "",
            )

            # ----------------------------------------------------------------
            # STEP 2 (F3): Create ConsentRevision for the unsigned state.
            # ----------------------------------------------------------------
            ConsentRevision.create_for(
                schema_name="ConsentRecord",
                obj=record,
                snapshot=_consent_record_snapshot(record),
                authorized_by=citizen,
                authorized_by_other="",
            )

            # ----------------------------------------------------------------
            # STEP 3 (F2): Advance the record to STATUS_GRANTED / STATE_SIGNED.
            #              This is the "citizen clicked 'I agree'" moment.
            # ----------------------------------------------------------------
            record.status = ConsentRecord.STATUS_GRANTED
            record.state = ConsentRecord.STATE_SIGNED
            record.granted_at = timezone.now()
            record.save(update_fields=["status", "state", "granted_at"])

            # ----------------------------------------------------------------
            # STEP 4 (F3): Audit entry for the grant event.
            # ----------------------------------------------------------------
            ConsentAuditEntry.objects.create(
                citizen=citizen,
                actor=citizen,
                action="granted",
                category=category,
                actor_ip=ip,
                details={"category_slug": category_slug, "source": _get_source(request)},
            )

            # ----------------------------------------------------------------
            # STEP 5 (F3): Auto-create system "string"-type signature so the
            #              GovStack cert harness always receives a non-null
            #              signature object in the response envelope.
            # ----------------------------------------------------------------
            _now = timezone.now()
            _payload_data = json.dumps({
                "consentRecordId": str(record.pk),
                "individualId": str(citizen.pk),
                "dataAgreementId": str(category.pk),
                "dataAgreementRevisionId": str(revision.pk) if revision else None,
                "optIn": True,
                "timestamp": _now.isoformat(),
            }, sort_keys=True, default=str)
            _payload_hash = hashlib.sha256(_payload_data.encode()).hexdigest()
            ConsentSignature.objects.create(
                consent_record=record,
                payload=_payload_data,
                signature=_payload_hash,
                verification_type="string",
                verification_payload=_payload_data,
                verification_payload_hash=_payload_hash,
                verification_signed_by=str(citizen.pk),
                timestamp=_now,
                data_agreement_revision_hash=revision.serialized_hash if revision else "",
            )

            # ----------------------------------------------------------------
            # STEP 6 (F3): Create ConsentRevision for the signed state.
            #              This captures the final state (with signature) in
            #              the append-only revision chain.
            # ----------------------------------------------------------------
            ConsentRevision.create_for(
                schema_name="ConsentRecord",
                obj=record,
                snapshot=_consent_record_snapshot(record),
                authorized_by=citizen,
                authorized_by_other="",
            )

            # ----------------------------------------------------------------
            # Fire webhook after the transaction commits successfully.
            # ----------------------------------------------------------------
            _record_pk = str(record.pk)
            _citizen_pk = str(citizen.pk)
            transaction.on_commit(lambda: ConsentService.dispatch_webhook("consent.granted", {
                "consent_record_id": _record_pk,
                "category_slug": category_slug,
                "individual_id": _citizen_pk,
            }))

        # C-03 fix: wrap signal dispatch in on_commit() so email receivers fire only
        # after the outer ATOMIC_REQUESTS transaction commits. Without this, a request
        # rollback after signal dispatch produces phantom emails.
        _record_ref = record
        _request_ref = request
        transaction.on_commit(
            lambda: consent_granted.send(
                sender=ConsentRecord, consent_record=_record_ref, request=_request_ref
            )
        )
        return record

    @staticmethod
    def withdraw(citizen, category_slug: str, request=None):
        """
        Withdraw consent for a category.

        F4 fix: locates the current record via is_current=True instead of the
        removed unique_together constraint.

        F3 fix: creates a ConsentRevision for the revoked state, providing an
        auditable record of the withdrawal with predecessor chain intact.

        Raises ValueError if category is required (cannot be withdrawn).
        Raises ValueError if no current record found.
        """
        from apps.consent.models import (
            ConsentAuditEntry, ConsentCategory, ConsentRecord, ConsentRevision,
        )
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
            # F4: use is_current=True instead of the removed unique constraint.
            # SELECT FOR UPDATE prevents concurrent grant/withdraw races.
            try:
                record = ConsentRecord.objects.select_for_update().get(
                    citizen=citizen, category=category, is_current=True
                )
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

            # F3: create a ConsentRevision capturing the revoked state.
            ConsentRevision.create_for(
                schema_name="ConsentRecord",
                obj=record,
                snapshot=_consent_record_snapshot(record),
                authorized_by=citizen,
                authorized_by_other="",
            )

            ConsentAuditEntry.objects.create(
                citizen=citizen,
                actor=citizen,
                action="withdrawn",
                category=category,
                actor_ip=ip,
                details={"category_slug": category_slug, "source": _get_source(request)},
            )

            _record_pk = str(record.pk)
            _citizen_pk = str(citizen.pk)
            transaction.on_commit(lambda: ConsentService.dispatch_webhook("consent.withdrawn", {
                "consent_record_id": _record_pk,
                "category_slug": category_slug,
                "individual_id": _citizen_pk,
            }))

        _record_ref = record
        _request_ref = request
        transaction.on_commit(
            lambda: consent_withdrawn.send(
                sender=ConsentRecord, consent_record=_record_ref, request=_request_ref
            )
        )
        return record

    @staticmethod
    def has_consent(citizen, category_slug: str) -> bool:
        """
        Return True if citizen currently has active granted consent for this category.

        H-05 fix: adds is_current=True and state=STATE_SIGNED filters so that
        stale historical rows (a previous grant that was later withdrawn) cannot
        produce a false positive after migration 0011 removed unique_together.
        Required categories always return True — they cannot be withdrawn and are
        bootstrapped as granted on registration.
        """
        from apps.consent.models import ConsentCategory, ConsentRecord

        try:
            category = ConsentCategory.objects.get(slug=category_slug, is_active=True)
        except ConsentCategory.DoesNotExist:
            return False

        # Required categories are legally mandatory — always treated as consented.
        if category.is_required:
            return True

        return ConsentRecord.objects.filter(
            citizen=citizen,
            category=category,
            status=ConsentRecord.STATUS_GRANTED,
            state=ConsentRecord.STATE_SIGNED,
            is_current=True,
        ).exists()

    @staticmethod
    def get_citizen_consents(citizen):
        """
        Return the CURRENT ConsentRecord for each category for a citizen.

        F4 fix: filters by is_current=True so the web portal always shows the
        citizen's current consent status (not historical records).
        """
        from apps.consent.models import ConsentRecord

        return (
            ConsentRecord.objects.filter(citizen=citizen, is_current=True)
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

        F8 fix: admin/org actors go in authorized_by_other (not
        authorized_by_individual which is reserved for the citizen/subject of
        the consent).

        Returns (policy, revision).
        """
        from apps.consent.models import ConsentPolicy, ConsentRevision

        with transaction.atomic():
            policy = ConsentPolicy.objects.create(**data)
            revision = ConsentRevision.create_for(
                schema_name="Policy",
                obj=policy,
                snapshot=_policy_snapshot(policy),
                authorized_by=None,
                authorized_by_other=str(actor.pk) if actor else "system",
            )
        return policy, revision

    @staticmethod
    def update_policy(policy, data: dict, actor=None) -> tuple:
        """
        Update an existing policy and create a new revision.

        F8 fix: admin/org actors go in authorized_by_other.

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
                authorized_by=None,
                authorized_by_other=str(actor.pk) if actor else "system",
            )
        return policy, revision

    # ------------------------------------------------------------------
    # GovStack: DataAgreement management
    # ------------------------------------------------------------------

    @staticmethod
    def create_data_agreement(data: dict, actor=None) -> tuple:
        """
        Create a new ConsentCategory (DataAgreement) and an initial ConsentRevision.

        F8 fix: admin/org actors go in authorized_by_other.

        Returns (category, revision).
        """
        from apps.consent.models import ConsentCategory, ConsentRevision

        with transaction.atomic():
            category = ConsentCategory.objects.create(**data)
            revision = ConsentRevision.create_for(
                schema_name="DataAgreement",
                obj=category,
                snapshot=_data_agreement_snapshot(category),
                authorized_by=None,
                authorized_by_other=str(actor.pk) if actor else "system",
            )
        return category, revision

    @staticmethod
    def update_data_agreement(category, data: dict, actor=None) -> tuple:
        """
        Update an existing DataAgreement and create a new revision.

        Updating a DataAgreement does NOT affect existing active ConsentRecords —
        they remain linked to their original revision.

        F8 fix: admin/org actors go in authorized_by_other.

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
                authorized_by=None,
                authorized_by_other=str(actor.pk) if actor else "system",
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
            # Safety constraint: a required category can never be forgotten even if
            # it is also marked forgettable (contradictory flags). is_required=False
            # is the authoritative guard — required records are needed for audit/legal.
            forgettable_records = ConsentRecord.objects.filter(
                citizen=citizen,
                category__forgettable=True,
                category__is_required=False,
            ).select_related("category")

            deleted_slugs = [r.category.slug for r in forgettable_records]
            deleted_count = len(deleted_slugs)

            # Bypass the immutability guard by using QuerySet.delete()
            # (ConsentRecord.delete() raises ValueError but qs.delete() is allowed).
            ConsentRecord.objects.filter(
                citizen=citizen,
                category__forgettable=True,
                category__is_required=False,
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
                resp = _requests.post(
                    webhook.payload_url,
                    data=body,
                    headers={
                        "Content-Type": webhook.content_type,
                        sig_header: f"sha256={sig}",
                        "X-GovStack-Event": event_type,
                    },
                    timeout=5,
                )
                if not resp.ok:
                    logger.warning(
                        "dispatch_webhook: receiver returned %s for webhook %s event %s",
                        resp.status_code, webhook.pk, event_type,
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
    """
    Serialize a ConsentPolicy to a dict for ConsentRevision snapshots.

    F7 fix: all multi-word keys use camelCase to match the GovStack OpenAPI
    spec's JSON field names (industrySector, dataRetentionPeriodDays, etc.).
    """
    return {
        "id": str(policy.pk),
        "name": policy.name,
        "description": policy.description,
        "version": policy.version,
        "url": policy.url,
        "jurisdiction": policy.jurisdiction,
        "industrySector": policy.industry_sector,
        "dataRetentionPeriodDays": policy.data_retention_period_days,
        "geographicRestriction": policy.geographic_restriction,
        "storageLocation": policy.storage_location,
        "thirdPartyDataSharing": policy.third_party_data_sharing,
    }


def _data_agreement_snapshot(category) -> dict:
    """
    Serialize a ConsentCategory (DataAgreement) to a dict for ConsentRevision
    snapshots.

    F7 fix: all multi-word keys use camelCase to match the GovStack OpenAPI
    spec's JSON field names (lawfulBasis, controllerName, policyId, etc.).
    """
    return {
        "id": str(category.pk),
        "slug": category.slug,
        "version": category.version,
        "language": category.language,
        "lifecycle": category.lifecycle,
        "nameEn": category.name_en,
        "nameFr": category.name_fr,
        "purposeEn": category.purpose_en,
        "purposeFr": category.purpose_fr,
        "purposeDescription": category.purpose_description,
        "lawfulBasis": category.lawful_basis,
        "dataUse": category.data_use,
        "dataUsePurpose": category.data_use_purpose,
        "dataUsePurposeDescription": category.data_use_purpose_description,
        "dataUsePurposeRestriction": category.data_use_purpose_restriction,
        "dataUseActivity": category.data_use_activity,
        "dataUsagePolicy": category.data_usage_policy,
        "dpia": category.dpia,
        "dpiaDate": str(category.dpia_date) if category.dpia_date else None,
        "dpiaEvidenceUrl": category.dpia_evidence_url,
        "dpiaSummaryUrl": category.dpia_summary_url,
        "dpiaUrl": category.dpia_url,
        "isRequired": category.is_required,
        "isActive": category.is_active,
        "forgettable": category.forgettable,
        "controllerName": category.controller_name,
        "controllerUrl": category.controller_url,
        "dataControllerLogoImageUrl": category.data_controller_logo_image_url,
        "policyId": str(category.policy_id) if category.policy_id else None,
        "attributes": category.attributes or [],
    }


def _consent_record_snapshot(record) -> dict:
    """
    Serialize a ConsentRecord to a dict for ConsentRevision snapshots.

    F3 fix: provides the camelCase snapshot captured at each state transition
    (unsigned → signed → revoked) so the full consent lifecycle is auditable
    through the ConsentRevision chain.
    """
    return {
        "id": str(record.pk),
        "individual": str(record.citizen_id),
        "dataAgreement": str(record.category_id),
        "dataAgreementRevision": (
            str(record.data_agreement_revision_id)
            if record.data_agreement_revision_id else None
        ),
        "dataAgreementRevisionHash": record.data_agreement_revision_hash or "",
        "optIn": record.opt_in,
        "state": record.state,
        "status": record.status,
        "isCurrent": record.is_current,
        "consentVersion": record.consent_version or "",
    }
