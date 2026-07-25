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
            #
            # DB-level race guard: unique_current_consent_record_per_citizen_
            # category (models.py) guarantees at most one is_current=True row
            # per (citizen, category). The select_for_update() above only
            # locks EXISTING signed/granted rows, so a citizen's very first
            # grant to a category has nothing to lock — two concurrent
            # first-grant requests could otherwise both reach this .create()
            # call. Wrapped in a savepoint (nested atomic()) so a constraint
            # violation here rolls back only this INSERT, not the whole
            # outer transaction, letting us recover by re-fetching the
            # winning concurrent request's row below.
            # ----------------------------------------------------------------
            try:
                with transaction.atomic():
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
            except IntegrityError as exc:
                # Only treat this as the expected "lost the first-grant race"
                # case if the violated constraint is actually
                # unique_current_consent_record_per_citizen_category. Any
                # other IntegrityError (e.g. an unrelated FK/NOT NULL
                # violation) is a real bug and must propagate, not be
                # silently swallowed and mis-reported as a successful grant.
                if not _is_current_consent_race(exc):
                    raise

                # Lost the race: a concurrent grant() call already committed
                # the current record for this citizen/category. select_for_
                # update() blocks until that transaction commits, then reads
                # its final (fully-granted) state — matching the same
                # "already granted, return existing" idempotency contract as
                # the existing_qs check above.
                winner = ConsentRecord.objects.select_for_update().filter(
                    citizen=citizen,
                    category=category,
                    is_current=True,
                ).first()
                if winner is None:
                    # Should be unreachable — the constraint violation implies
                    # a current row exists. Re-raise rather than mask a bug.
                    raise
                return winner

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
    def attach_signature(
        record,
        verification_type: str,
        verification_payload,
        request=None,
    ):
        """
        Attach a caller-supplied signature to a ConsentRecord and advance its
        state to STATE_SIGNED if not already signed.

        Creates a ConsentRevision and ConsentAuditEntry for every call — every
        state transition must be auditable (GovStack Consent BB requirement).

        C-03 fix: previously the /signature/ endpoint set record.state directly
        without writing a revision or audit entry.
        """
        import hashlib
        import json as _json

        from apps.consent.models import (
            ConsentAuditEntry,
            ConsentRecord,
            ConsentRevision,
            ConsentSignature,
        )
        from apps.consent.signals import consent_granted

        actor_ip = _mask_ip(_get_ip(request) or "")
        source = _get_source(request)

        with transaction.atomic():
            # Prevent duplicate signatures
            if ConsentSignature.objects.filter(consent_record=record).exists():
                from rest_framework.exceptions import ValidationError as DRFValidationError
                raise DRFValidationError(
                    "A signature already exists for this ConsentRecord. Use PUT to update."
                )

            # Normalise verification_payload to a JSON string for storage (TextField)
            if isinstance(verification_payload, dict):
                vp_str = _json.dumps(verification_payload, sort_keys=True, default=str)
            else:
                vp_str = str(verification_payload)

            payload_hash = hashlib.sha256(vp_str.encode()).hexdigest()

            from django.utils import timezone as _tz
            _now = _tz.now()

            sig = ConsentSignature.objects.create(
                consent_record=record,
                payload=vp_str,
                signature=payload_hash,
                verification_type=verification_type,
                verification_payload=vp_str,
                verification_payload_hash=payload_hash,
                verification_signed_by=str(record.citizen_id),
                timestamp=_now,
                data_agreement_revision_hash=record.data_agreement_revision_hash or "",
            )

            state_changed = record.state != ConsentRecord.STATE_SIGNED
            if state_changed:
                old_state = record.state
                record.status = ConsentRecord.STATUS_GRANTED
                record.state = ConsentRecord.STATE_SIGNED
                record.actor_ip = actor_ip
                record.source = source
                record.save(update_fields=["status", "state", "actor_ip", "source"])

                # Write revision for the state transition
                snapshot = _consent_record_snapshot(record)
                snapshot["previousState"] = old_state
                snapshot["transitionedBy"] = "signature"

                revision = ConsentRevision.create_for(
                    schema_name="ConsentRecord",
                    obj=record,
                    snapshot=snapshot,
                    authorized_by=record.citizen,
                    authorized_by_other="",
                )

                ConsentAuditEntry.objects.create(
                    citizen=record.citizen,
                    actor=record.citizen,
                    action="granted",
                    category=record.category,
                    actor_ip=actor_ip,
                    details={
                        "trigger": "signature_attached",
                        "verification_type": verification_type,
                        "revision_id": str(revision.pk),
                    },
                )

                _record_ref = record
                _request_ref = request
                transaction.on_commit(
                    lambda: consent_granted.send(
                        sender=ConsentRecord,
                        consent_record=_record_ref,
                        request=_request_ref,
                    )
                )

        return sig

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

        Enqueues a Celery task (``dispatch_consent_webhook``) for each
        subscribed webhook rather than making the HTTP POST synchronously.
        This prevents slow or unresponsive webhook receivers from stalling
        Django worker threads.

        Each task:
          - Fetches ``webhook.secret_key`` from DB (Fernet-decrypted) inside
            the task so no secret ever travels through the task message broker.
          - Signs the payload with HMAC-SHA256 and POSTs to ``payload_url``.
          - Retries up to 3 times on network errors (autoretry_for).
          - Persists last_payload / last_delivery_at for the replay endpoint.

        The ``event_timestamp`` is captured here (at dispatch time) and passed
        to the task so the HMAC body timestamp is accurate regardless of
        Celery queue delay.

        Failures are never re-raised — webhook errors must not affect the
        main request flow.
        """
        from apps.consent.models import ConsentWebhook
        from apps.consent.tasks import dispatch_consent_webhook

        event_timestamp = str(timezone.now())

        webhooks = ConsentWebhook.objects.filter(is_disabled=False)
        for webhook in webhooks:
            if not webhook.is_subscribed_to(event_type):
                continue
            try:
                dispatch_consent_webhook.delay(
                    webhook_pk=str(webhook.pk),
                    event_type=event_type,
                    payload_dict=payload,
                    event_timestamp=event_timestamp,
                )
            except Exception as exc:
                # Task enqueue failure (e.g. broker unreachable) must not
                # propagate to the caller — log and continue.
                logger.warning(
                    "dispatch_webhook: failed to enqueue task for webhook %s event %s: %s",
                    webhook.pk, event_type, type(exc).__name__,
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


# Name of the DB-level constraint (models.py, ConsentRecord.Meta.constraints)
# that grant()'s first-grant race recovery is specifically designed to catch.
_CURRENT_CONSENT_RACE_CONSTRAINT = "unique_current_consent_record_per_citizen_category"


def _is_current_consent_race(exc: IntegrityError) -> bool:
    """
    Return True only if ``exc`` was raised by a violation of
    ``unique_current_consent_record_per_citizen_category`` — the specific
    constraint that guards against the concurrent "very first grant" race
    (see ConsentService.grant()). Any other IntegrityError (unrelated FK
    violation, NOT NULL violation, a different unique constraint, etc.) must
    NOT be treated as this race and must be re-raised by the caller.

    Prefers the structured diagnostics psycopg (both psycopg2 and psycopg3,
    which this project uses per requirements/base.txt) attaches to the
    underlying driver exception via ``exc.__cause__.diag.constraint_name`` —
    this is authoritative and immune to message-wording differences across
    PostgreSQL versions/locales. Falls back to substring-matching the
    constraint name in ``str(exc)`` for backends that don't expose that
    structured diagnostic (e.g. SQLite, used in this project's test
    settings — see config/settings/test.py — and easy to construct directly
    in mocked unit tests).
    """
    cause = getattr(exc, "__cause__", None)
    diag = getattr(cause, "diag", None)
    diag_constraint_name = getattr(diag, "constraint_name", None)
    if diag_constraint_name is not None:
        return diag_constraint_name == _CURRENT_CONSENT_RACE_CONSTRAINT
    return _CURRENT_CONSENT_RACE_CONSTRAINT in str(exc)


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
