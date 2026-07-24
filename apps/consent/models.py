"""
Consent & Privacy models — PIPEDA-compliant + GovStack Consent BB v1.3.0 aligned.

GovStack model mapping:
  ConsentPolicy       → GovStack Policy
  ConsentCategory     → GovStack DataAgreement (+ DataAgreementAttribute via attributes JSON)
  ConsentRevision     → GovStack Revision  (tamper-proof serialized snapshot)
  ConsentRecord       → GovStack ConsentRecord
  ConsentSignature    → GovStack Signature  (attached to ConsentRecord)
  ConsentWebhook      → GovStack Webhook
  ConsentAuditEntry   → GovStack Auditor API data
  DataExportRequest   → PIPEDA s.4.9 Right of Access (no GovStack equivalent)
"""
from __future__ import annotations

import hashlib
import json
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.fields import EncryptedCharField
from apps.core.models import TimestampedModel, UUIDModel


# ---------------------------------------------------------------------------
# GovStack: Policy
# Governs Data Agreements in the realm of an organisation (data controller).
# ---------------------------------------------------------------------------

class ConsentPolicy(UUIDModel, TimestampedModel):
    """
    GovStack Policy object.

    A policy governs Data Agreements in the realm of an organisation that is
    often referred to as "data controller" (GDPR) and owner of referencing
    Data Agreements.

    Each update to a Policy creates a new ConsentRevision so the history
    is tamper-proof.
    """
    name = models.CharField(max_length=255)
    description = models.TextField(
        blank=True,
        help_text="Human-readable description of this policy (GovStack Policy.description).",
    )
    version = models.CharField(max_length=50)
    url = models.URLField(
        help_text="Permanent URL at which this version of the Policy can be read.",
    )
    jurisdiction = models.CharField(max_length=100, blank=True)
    industry_sector = models.CharField(max_length=100, blank=True)
    data_retention_period_days = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="How long personal data is retained (days).",
    )
    geographic_restriction = models.CharField(max_length=100, blank=True)
    storage_location = models.CharField(max_length=255, blank=True)
    third_party_data_sharing = models.BooleanField(
        default=False,
        help_text="True if data may be shared with third parties per this policy (GovStack Policy.thirdPartyDataSharing).",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Consent Policy"
        verbose_name_plural = "Consent Policies"

    def __str__(self) -> str:
        return f"{self.name} v{self.version}"


# ---------------------------------------------------------------------------
# GovStack: DataAgreement  (our ConsentCategory, extended)
# ---------------------------------------------------------------------------

class ConsentCategory(TimestampedModel):
    """
    A category of data processing that citizens can consent to.

    Maps to GovStack DataAgreement.  Each category corresponds to a distinct
    purpose (PIPEDA 4.2 / GovStack UC-C-PIC-A-001).

    Fields added for GovStack alignment:
      - policy             FK to ConsentPolicy
      - version            DataAgreement version string
      - data_use           null / data_source / data_using_service
      - dpia               Data Protection Impact Assessment text
      - forgettable        Records may be deleted on withdrawal (RTBF)
      - controller_name    Data controller name
      - controller_url     Data controller URL
      - attributes         JSON list of DataAgreementAttribute objects
    """

    LAWFUL_BASIS_CHOICES = [
        ("consent", "Consent"),
        ("legal_obligation", "Legal Obligation"),
        ("vital_interests", "Vital Interests"),
        ("contract", "Contract"),
        ("public_task", "Public Task"),
        ("legitimate_interests", "Legitimate Interests"),
        # Legacy alias kept for backwards compatibility
        ("legitimate_interest", "Legitimate Interest (legacy)"),
    ]

    DATA_USE_CHOICES = [
        ("", "Not specified"),
        ("data_source", "Data Source"),
        ("data-using-service", "Data Using Service"),
        # Legacy alias kept for backwards compatibility
        ("data_using_service", "Data Using Service (legacy)"),
    ]

    LIFECYCLE_CHOICES = [
        ("draft", "Draft"),
        ("published", "Published"),
        ("error_correction", "Error Correction"),
    ]

    # --- Existing PIPEDA fields ---
    slug = models.CharField(max_length=100, unique=True)
    name_en = models.CharField(max_length=255)
    name_fr = models.CharField(max_length=255)
    purpose_en = models.TextField(
        help_text="Plain-language explanation of why data is collected (PIPEDA 4.2)."
    )
    purpose_fr = models.TextField()
    lawful_basis = models.CharField(
        max_length=30,
        choices=LAWFUL_BASIS_CHOICES,
        default="consent",
    )
    is_required = models.BooleanField(
        default=False,
        help_text="If True, consent cannot be withdrawn — required for essential service delivery.",
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    # --- GovStack DataAgreement fields ---
    policy = models.ForeignKey(
        ConsentPolicy,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="data_agreements",
        help_text="GovStack Policy that governs this Data Agreement.",
    )
    version = models.CharField(
        max_length=50,
        blank=True,
        default="1.0.0",
        help_text="Data Agreement version string.",
    )
    data_use = models.CharField(
        max_length=30,
        choices=DATA_USE_CHOICES,
        blank=True,
        default="",
    )
    dpia = models.TextField(
        blank=True,
        help_text="Data Protection Impact Assessment (DPIA) summary.",
    )
    forgettable = models.BooleanField(
        default=False,
        help_text=(
            "If True, Consent Records may be deleted on withdrawal "
            "(RTBF / GovStack forgettable flag)."
        ),
    )
    controller_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Name of the data controller organisation.",
    )
    controller_url = models.URLField(
        blank=True,
        help_text="URL of the data controller organisation.",
    )
    attributes = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "List of DataAgreementAttribute objects: "
            "[{name, sensitivity, category}, ...]"
        ),
    )

    # --- GovStack DataAgreement additional fields ---
    language = models.CharField(
        max_length=10,
        blank=True,
        default="en",
        help_text="ISO 639-1 language code for this data agreement (GovStack DataAgreement.language).",
    )
    lifecycle = models.CharField(
        max_length=30,
        choices=LIFECYCLE_CHOICES,
        blank=True,
        default="published",
        help_text="Lifecycle state: draft / published / error_correction.",
    )
    dpia_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date the DPIA was conducted.",
    )
    dpia_evidence_url = models.URLField(
        blank=True,
        help_text="URL to DPIA evidence document.",
    )
    dpia_summary_url = models.URLField(
        blank=True,
        help_text="URL to DPIA summary document.",
    )
    dpia_url = models.URLField(
        blank=True,
        help_text="URL to the full DPIA document.",
    )

    # --- GovStack DataAgreement additional purpose/controller fields ---
    data_controller_logo_image_url = models.URLField(
        blank=True,
        help_text="URL to the data controller logo image (GovStack dataControllerLogoImageUrl).",
    )
    data_use_purpose = models.CharField(
        max_length=255,
        blank=True,
        help_text="Short purpose label (GovStack dataUsePurpose).",
    )
    data_use_purpose_description = models.TextField(
        blank=True,
        help_text="Detailed description of the data use purpose (GovStack dataUsePurposeDescription).",
    )
    data_use_purpose_restriction = models.TextField(
        blank=True,
        help_text="Restrictions on the data use purpose (GovStack dataUsePurposeRestriction).",
    )
    data_use_activity = models.TextField(
        blank=True,
        help_text="Description of data use activity (GovStack dataUseActivity).",
    )
    data_usage_policy = models.URLField(
        blank=True,
        help_text="URL to the data usage policy (GovStack dataUsagePolicy).",
    )
    purpose_description = models.TextField(
        blank=True,
        help_text="Human-readable description of the purpose (GovStack purposeDescription).",
    )

    class Meta:
        ordering = ["sort_order", "slug"]
        verbose_name = "Consent Category"
        verbose_name_plural = "Consent Categories"

    def __str__(self) -> str:
        return self.name_en

    def get_name(self) -> str:
        """Return the category name in the active language."""
        from django.utils.translation import get_language
        if get_language() and get_language().startswith("fr"):
            return self.name_fr or self.name_en
        return self.name_en

    def get_purpose(self) -> str:
        """Return the category purpose in the active language."""
        from django.utils.translation import get_language
        if get_language() and get_language().startswith("fr"):
            return self.purpose_fr or self.purpose_en
        return self.purpose_en


# ---------------------------------------------------------------------------
# GovStack: Revision
# Generic tamper-proof snapshot for any consent object.
# ---------------------------------------------------------------------------

class ConsentRevision(UUIDModel):
    """
    GovStack Revision object.

    A generic revision model captures the serialized contents of any schema's
    single row. This is then subject to (1) cryptographic hashing and
    (2) auditing.

    Aside from the ``successor`` relation, a revision is considered locked
    (append-only).
    """

    schema_name = models.CharField(
        max_length=100,
        db_index=True,
        help_text='E.g. "DataAgreement", "Policy", "ConsentRecord"',
    )
    object_id = models.CharField(
        max_length=64,
        db_index=True,
        help_text="PK of the object that was serialized.",
    )
    serialized_snapshot = models.JSONField(
        help_text="Full serialized content of the object at revision time.",
    )
    serialized_hash = models.CharField(
        max_length=64,
        help_text="SHA-256 hash of serialized_snapshot (JSON, sort_keys=True).",
    )
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    authorized_by_individual = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="consent_revisions_individual",
        help_text="The individual who authorized this revision.",
    )
    authorized_by_other = models.CharField(
        max_length=255,
        blank=True,
        help_text="Reference to an admin/system that created this revision.",
    )
    # Chain fields (GovStack Revision spec)
    predecessor_hash = models.CharField(
        max_length=64,
        blank=True,
        help_text="serializedHash of the previous revision for this object.",
    )
    # successor FK: points to the next revision (null = latest)
    successor = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="predecessor",
        help_text="This revision is no longer the latest — refer to successor.",
    )

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Consent Revision"
        verbose_name_plural = "Consent Revisions"
        indexes = [
            models.Index(fields=["schema_name", "object_id", "timestamp"]),
        ]

    def __str__(self) -> str:
        return f"Revision({self.schema_name}/{self.object_id}) @ {self.timestamp:%Y-%m-%d %H:%M:%S}"

    def save(self, *args, **kwargs):
        # _state.adding is True only on INSERT (new object), False on UPDATE.
        # We cannot use `if self.pk:` because UUIDModel sets the UUID default
        # before save() is called, so self.pk is already set even for new rows.
        if not self._state.adding:
            # Allow only the successor field to be updated (chain linking)
            allowed = kwargs.get("update_fields", None)
            if allowed and set(allowed) <= {"successor"}:
                super().save(*args, **kwargs)
                return
            raise ValueError("ConsentRevision is append-only. Only successor may be updated.")
        if not self.serialized_hash:
            self.serialized_hash = self._compute_hash()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("ConsentRevision records cannot be deleted.")

    def _compute_hash(self) -> str:
        payload = json.dumps(self.serialized_snapshot, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    @classmethod
    def create_for(
        cls,
        schema_name: str,
        obj,
        snapshot: dict,
        authorized_by=None,
        authorized_by_other: str = "",
    ) -> "ConsentRevision":
        """
        Create a new revision for ``obj``, correctly linking the predecessor chain.

        This is the canonical way to create a revision — do not call
        ``ConsentRevision(...)`` directly.
        """
        object_id = str(obj.pk)

        # Find current latest revision for this object (if any)
        prev = (
            cls.objects.filter(schema_name=schema_name, object_id=object_id, successor__isnull=True)
            .order_by("-timestamp")
            .first()
        )
        predecessor_hash = prev.serialized_hash if prev else ""

        # Do NOT inject _predecessor_hash into the snapshot dict.
        # The predecessor hash is stored in the dedicated predecessor_hash field.
        # The snapshot must be a clean serialization of the object — injecting
        # implementation-specific keys would pollute the spec-defined snapshot shape
        # and compute a hash over data that is not part of the object itself.
        rev = cls(
            schema_name=schema_name,
            object_id=object_id,
            serialized_snapshot=snapshot,
            predecessor_hash=predecessor_hash,
            authorized_by_individual=authorized_by,
            authorized_by_other=authorized_by_other,
        )
        rev.save()

        # Link the previous revision to this one
        if prev:
            prev.successor = rev
            prev.save(update_fields=["successor"])

        return rev


# ---------------------------------------------------------------------------
# GovStack: ConsentRecord (extended)
# ---------------------------------------------------------------------------

class ConsentRecord(UUIDModel, TimestampedModel):
    """
    One citizen's decision on one consent category / Data Agreement.

    GovStack fields added:
      - state                    unsigned / pending_signatures / signed
      - data_agreement_revision  FK to ConsentRevision (the revision agreed to)
      - data_agreement_revision_hash  copy of revision hash for tamper-proofing
    """

    STATUS_PENDING = "pending"
    STATUS_GRANTED = "granted"
    STATUS_WITHDRAWN = "withdrawn"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_GRANTED, "Granted"),
        (STATUS_WITHDRAWN, "Withdrawn"),
    ]

    # GovStack ConsentRecord.state values (spec: unsigned | pending | signed | revoked)
    STATE_UNSIGNED = "unsigned"
    STATE_PENDING = "pending"
    STATE_SIGNED = "signed"
    STATE_REVOKED = "revoked"

    STATE_CHOICES = [
        (STATE_UNSIGNED, "Unsigned"),
        (STATE_PENDING, "Pending"),
        (STATE_SIGNED, "Signed"),
        (STATE_REVOKED, "Revoked"),
    ]

    SOURCE_CHOICES = [
        ("web", "Web Portal"),
        ("api", "API"),
        ("admin", "Admin"),
    ]

    citizen = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="consent_records",
    )
    category = models.ForeignKey(
        ConsentCategory,
        on_delete=models.PROTECT,
        related_name="records",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    granted_at = models.DateTimeField(null=True, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    actor_ip = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="Always masked before storage.",
    )
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="web")
    consent_version = models.CharField(
        max_length=50,
        blank=True,
        help_text="Identifies which version of the consent text was shown.",
    )

    # GovStack alignment fields
    state = models.CharField(
        max_length=30,
        choices=STATE_CHOICES,
        default=STATE_UNSIGNED,
        db_index=True,
        help_text="GovStack signing state: unsigned / pending / signed / revoked.",
    )
    data_agreement_revision = models.ForeignKey(
        ConsentRevision,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="consent_records",
        help_text="Revision of the DataAgreement that was in effect when consent was given.",
    )
    data_agreement_revision_hash = models.CharField(
        max_length=64,
        blank=True,
        help_text=(
            "Copy of the Revision's hash at the time of consent. "
            "Ensures against tampering with the original Data Agreement."
        ),
    )

    # F4 fix: tracks which row represents the current (most-recent) consent
    # state for a citizen/category pair.  When a new record is created (re-
    # consent or withdraw → re-consent), all older rows are set to False so that
    # is_current=True acts as an efficient "active row" pointer.
    is_current = models.BooleanField(
        default=True,
        db_index=True,
        help_text=(
            "True for the most-recent ConsentRecord for this citizen/category. "
            "Older historical rows have is_current=False."
        ),
    )

    class Meta:
        # unique_together removed (F4): we now allow multiple rows per
        # citizen/category to preserve full consent history.  is_current=True
        # always points at the latest row.
        ordering = ["-created_at"]
        verbose_name = "Consent Record"
        verbose_name_plural = "Consent Records"
        indexes = [
            models.Index(
                fields=["citizen", "category", "is_current"],
                name="cr_citizen_cat_curr_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(state__in=["unsigned", "pending", "signed", "revoked"]),
                name="consent_record_state_valid",
                violation_error_message="state must be one of: unsigned, pending, signed, revoked",
            ),
        ]

    def __str__(self) -> str:
        return f"ConsentRecord #{self.pk} ({self.status})"

    @property
    def opt_in(self) -> bool:
        """GovStack ConsentRecord.optIn — True if status is 'granted'."""
        return self.status == self.STATUS_GRANTED

    def save(self, *args, **kwargs):
        if self.status == self.STATUS_GRANTED and self.granted_at is None:
            self.granted_at = timezone.now()
        if self.status == self.STATUS_WITHDRAWN and self.withdrawn_at is None:
            self.withdrawn_at = timezone.now()
        # Capture revision hash snapshot
        if self.data_agreement_revision_id and not self.data_agreement_revision_hash:
            self.data_agreement_revision_hash = self.data_agreement_revision.serialized_hash
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# PIPEDA: DataExportRequest (unchanged — no GovStack equivalent)
# ---------------------------------------------------------------------------

class DataExportRequest(UUIDModel):
    """
    PIPEDA s.4.9 right of access — a citizen's request for all their data.
    Must be fulfilled within 30 days.
    """

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_READY = "ready"
    STATUS_DELIVERED = "delivered"
    STATUS_FAILED = "failed"
    STATUS_EXPIRED = "expired"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_READY, "Ready"),
        (STATUS_DELIVERED, "Delivered"),
        (STATUS_FAILED, "Failed"),
        (STATUS_EXPIRED, "Expired"),
    ]

    FORMAT_CHOICES = [
        ("json", "JSON"),
    ]

    citizen = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="export_requests",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    format = models.CharField(max_length=10, choices=FORMAT_CHOICES, default="json")
    requested_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="7 days after processed_at.",
    )
    download_token = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        null=True,
        blank=True,
        unique=True,
        help_text="Single-use download token. Nulled after first successful delivery.",
    )
    document = models.OneToOneField(
        "documents.Document",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="data_export",
        help_text="Documents BB record for the export archive. "
                  "PIPEDA transitory — disposed after delivery.",
    )
    notes = models.TextField(blank=True, help_text="Staff notes.")

    class Meta:
        ordering = ["-requested_at"]
        verbose_name = "Data Export Request"
        verbose_name_plural = "Data Export Requests"
        constraints = [
            models.UniqueConstraint(
                fields=["citizen"],
                condition=models.Q(status__in=["pending", "processing"]),
                name="unique_active_export_per_citizen",
            )
        ]

    def __str__(self) -> str:
        return f"DataExportRequest #{self.pk} ({self.status})"


# ---------------------------------------------------------------------------
# GovStack: Webhook
# ---------------------------------------------------------------------------

class ConsentWebhook(UUIDModel, TimestampedModel):
    """
    GovStack Webhook object.

    Used to notify third-party systems of consent events (grant, withdraw,
    export requests, etc.).
    """

    CONTENT_TYPE_CHOICES = [
        ("application/json", "application/json"),
        ("application/x-www-form-urlencoded", "application/x-www-form-urlencoded"),
    ]

    # Well-known event type strings
    EVENT_CONSENT_GRANTED = "consent.granted"
    EVENT_CONSENT_WITHDRAWN = "consent.withdrawn"
    EVENT_EXPORT_REQUESTED = "consent.export.requested"
    EVENT_EXPORT_READY = "consent.export.ready"

    KNOWN_EVENTS = [
        EVENT_CONSENT_GRANTED,
        EVENT_CONSENT_WITHDRAWN,
        EVENT_EXPORT_REQUESTED,
        EVENT_EXPORT_READY,
    ]

    payload_url = models.URLField(
        help_text="The URL to which the webhook payload will be POSTed.",
    )
    content_type = models.CharField(
        max_length=50,
        choices=CONTENT_TYPE_CHOICES,
        default="application/json",
    )
    is_disabled = models.BooleanField(
        default=False,
        db_index=True,
        help_text="If True, this webhook will not receive events.",
    )
    secret_key = EncryptedCharField(
        max_length=500,
        help_text=(
            "HMAC secret key used to sign payloads (SHA-256). "
            "Stored Fernet-encrypted at rest; returned in API responses as required by GovStack spec."
        ),
    )
    subscribed_events = models.JSONField(
        default=list,
        help_text=(
            "List of event type strings this webhook is subscribed to. "
            f"Known events: {KNOWN_EVENTS}"
        ),
    )
    signature_header = models.CharField(
        max_length=100,
        blank=True,
        default="X-GovStack-Signature",
        help_text="HTTP header name used for the HMAC-SHA256 signature (GovStack signatureHeader).",
    )
    skipped_headers = models.JSONField(
        default=list,
        blank=True,
        help_text="List of HTTP headers to omit when sending the webhook payload (GovStack skippedHeaders).",
    )

    # ── Webhook delivery replay log ───────────────────────────────────────────
    # Stores the most recent successfully delivered payload so that
    # GET /config/webhook/{id}/payload/ can return it for debugging.
    # Written by the dispatch_consent_webhook Celery task on success.
    last_payload = models.JSONField(
        null=True,
        blank=True,
        default=None,
        help_text=(
            "The full body (event + timestamp + payload) of the most recently "
            "delivered webhook POST.  Null until the first successful delivery."
        ),
    )
    last_delivery_at = models.DateTimeField(
        null=True,
        blank=True,
        default=None,
        help_text="Timestamp of the most recent successful webhook delivery.",
    )
    last_delivery_status = models.CharField(
        max_length=10,
        choices=[("success", "Success"), ("failed", "Failed")],
        null=True,
        blank=True,
        default=None,
        help_text="Result of the most recent delivery attempt.",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Consent Webhook"
        verbose_name_plural = "Consent Webhooks"

    def __str__(self) -> str:
        return f"Webhook → {self.payload_url}"

    def is_subscribed_to(self, event_type: str) -> bool:
        return not self.is_disabled and event_type in (self.subscribed_events or [])


# ---------------------------------------------------------------------------
# ConsentAuditEntry (unchanged — append-only audit trail)
# ---------------------------------------------------------------------------

class ConsentAuditEntry(models.Model):
    """
    Append-only audit trail for all consent and export events.
    Records may never be updated or deleted.
    """

    ACTION_CHOICES = [
        ("granted", "Granted"),
        ("withdrawn", "Withdrawn"),
        ("export_requested", "Export Requested"),
        ("export_ready", "Export Ready"),
        ("export_delivered", "Export Delivered"),
        ("export_expired", "Export Expired"),
        ("export_failed", "Export Failed"),
        ("export_downloaded", "Export Downloaded by Citizen"),
        ("export_marked_delivered", "Marked Delivered by Staff"),
        ("rtbf_requested", "Right to Be Forgotten Requested"),
        ("rtbf_completed", "Right to Be Forgotten Completed"),
    ]

    citizen = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        related_name="consent_audit_entries",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="consent_audit_actions",
    )
    action = models.CharField(max_length=30, choices=ACTION_CHOICES, db_index=True)
    category = models.ForeignKey(
        ConsentCategory,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    export_request = models.ForeignKey(
        DataExportRequest,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    actor_ip = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="Always masked before storage.",
    )
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Consent Audit Entry"
        verbose_name_plural = "Consent Audit Entries"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(action__in=[
                    "granted", "withdrawn",
                    "export_requested", "export_ready",
                    "export_delivered", "export_expired", "export_failed",
                    "export_downloaded", "export_marked_delivered",
                    "rtbf_requested", "rtbf_completed",
                ]),
                name="consent_audit_valid_action",
            )
        ]
        indexes = [
            models.Index(
                fields=["citizen", "timestamp"],
                name="consent_audit_citizen_ts_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"ConsentAuditEntry #{self.pk}: {self.action} @ {self.timestamp}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("ConsentAuditEntry is append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("ConsentAuditEntry records cannot be deleted.")


# ---------------------------------------------------------------------------
# GovStack: Signature
# Cryptographic or non-cryptographic signature attached to a ConsentRecord.
# ---------------------------------------------------------------------------

class ConsentSignature(UUIDModel):
    """
    GovStack Signature object attached to a ConsentRecord.

    Stores whatever cryptographic / non-cryptographic signature the calling
    system provides.  The Consent BB itself does NOT verify signatures — it
    stores and exposes them so that third-party verifiers can check them.

    Required fields per GovStack spec:
      id, payload, signature, verificationMethod, verificationPayload,
      verificationPayloadHash, verificationSignedBy, timestamp
    """

    consent_record = models.OneToOneField(
        ConsentRecord,
        on_delete=models.CASCADE,
        related_name="signature_obj",
        help_text="The ConsentRecord this signature covers.",
    )

    VERIFICATION_TYPE_CHOICES = [
        ("string", "String (non-cryptographic)"),
        ("rs256", "RS256 (RSA + SHA-256)"),
        ("ed25519", "Ed25519"),
        ("ps256", "PS256 (RSA-PSS + SHA-256)"),
        ("pgp", "PGP"),  # F17 fix: GovStack spec includes pgp
    ]

    SIGNED_AS_CHOICES = [
        ("individual", "Individual"),
        ("delegate", "Delegate"),
        ("commissioner", "Commissioner"),
    ]

    # --- Required GovStack fields ---
    payload = models.TextField(
        help_text=(
            "JSON serialization of the fields that were signed, per GovStack spec."
        ),
    )
    signature = models.TextField(
        help_text="Signature of payload hash.",
    )
    verification_type = models.CharField(
        max_length=10,
        choices=VERIFICATION_TYPE_CHOICES,
        default="string",
        help_text="Signing algorithm (GovStack verificationType enum: string/rs256/ed25519/ps256).",
    )
    verification_payload = models.TextField(
        help_text="Internally generated serialized version of the signed data.",
    )
    verification_payload_hash = models.CharField(
        max_length=255,
        help_text="Cryptographic hash of verificationPayload.",
    )
    verification_signed_by = models.CharField(
        max_length=500,
        help_text=(
            "Identifier for the signing party at the time of signing "
            "(e.g. fingerprint, email, user ID)."
        ),
    )
    timestamp = models.DateTimeField(
        help_text="ISO 8601 timestamp of when the signature was created.",
    )
    data_agreement_revision_hash = models.CharField(
        max_length=64,
        blank=True,
        help_text="Hash of the DataAgreement revision that was signed (GovStack dataAgreementRevisionHash).",
    )
    data_agreement_revision_signed_without_id = models.BooleanField(
        default=False,
        help_text="True if the DataAgreement revision was signed without its object ID (GovStack dataAgreementRevisionSignedWithoutId).",
    )

    # --- Optional GovStack fields ---
    verification_artifact = models.TextField(
        blank=True,
        help_text="Scanned object, image, or other verification artefact.",
    )
    verification_signed_as = models.CharField(
        max_length=20,
        choices=SIGNED_AS_CHOICES,
        blank=True,
        help_text="Relationship of the signer: individual / delegate / commissioner.",
    )
    verification_jwks = models.JSONField(
        null=True,
        blank=True,
        help_text="JSON Web Key Set (JWKS) used for signature verification (GovStack verificationJwks).",
    )
    verification_jws_header = models.TextField(
        blank=True,
        help_text="JWS serialized object (RFC7515) — alternative to verificationType.",
    )
    signed_without_object_id = models.BooleanField(
        default=False,
        help_text="True if objectId was omitted from the signed payload (GovStack signedWithoutObjectId).",
    )
    object_type = models.CharField(
        max_length=50,
        blank=True,
        help_text="Name of the schema model that objectReference points to ('signature' or 'revision').",
    )
    object_reference = models.CharField(
        max_length=255,
        blank=True,
        help_text="Back-reference to the objectType that was signed.",
    )

    class Meta:
        verbose_name = "Consent Signature"
        verbose_name_plural = "Consent Signatures"

    def __str__(self) -> str:
        return f"ConsentSignature for record #{self.consent_record_id}"
