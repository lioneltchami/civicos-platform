"""
Serializers for the Consent & Privacy API.

Two layers:
  1. CivicOS (legacy) serializers — used by the original /api/v1/consent/ endpoints.
  2. GovStack serializers — used by the GovStack-namespaced endpoints and shaped
     to match the GovStack Consent BB OpenAPI spec field names (camelCase in JSON
     handled by the DRF settings or by explicit field naming).
"""

import json

from rest_framework import serializers

from .models import (
    ConsentAuditEntry,
    ConsentCategory,
    ConsentPolicy,
    ConsentRecord,
    ConsentRevision,
    ConsentSignature,
    ConsentWebhook,
    DataExportRequest,
)

# ===========================================================================
# CivicOS (legacy) serializers
# ===========================================================================


class ConsentCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsentCategory
        fields = [  # noqa: RUF012
            "id",
            "slug",
            "name_en",
            "name_fr",
            "purpose_en",
            "purpose_fr",
            "lawful_basis",
            "is_required",
            "sort_order",
        ]
        read_only_fields = fields


class ConsentRecordSerializer(serializers.ModelSerializer):
    category = ConsentCategorySerializer(read_only=True)
    category_slug = serializers.SlugRelatedField(
        source="category", slug_field="slug", read_only=True
    )

    class Meta:
        model = ConsentRecord
        fields = [  # noqa: RUF012
            "id",
            "category",
            "category_slug",
            "status",
            "granted_at",
            "withdrawn_at",
            "source",
        ]
        read_only_fields = [  # noqa: RUF012
            "id",
            "category",
            "category_slug",
            "granted_at",
            "withdrawn_at",
            "source",
        ]


class ConsentUpdateSerializer(serializers.Serializer):
    """Used for PATCH /api/v1/consent/records/<category_slug>/"""

    action = serializers.ChoiceField(choices=["grant", "withdraw"])


class DataExportRequestSerializer(serializers.ModelSerializer):
    # download_token is the bearer credential for downloading the export.
    # Only expose it once the export is ready — never for pending/processing/failed.
    download_token = serializers.SerializerMethodField()

    class Meta:
        model = DataExportRequest
        fields = [  # noqa: RUF012
            "id",
            "status",
            "format",
            "requested_at",
            "processed_at",
            "expires_at",
            "download_token",
        ]
        read_only_fields = [  # noqa: RUF012
            "id",
            "status",
            "format",
            "requested_at",
            "processed_at",
            "expires_at",
        ]

    def get_download_token(self, obj):  # noqa: ANN001, ANN201
        if obj.status == DataExportRequest.STATUS_READY:
            return str(obj.download_token)
        return None


class ConsentAuditEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsentAuditEntry
        fields = ["id", "action", "timestamp", "details"]  # noqa: RUF012
        read_only_fields = fields


# ===========================================================================
# GovStack serializers
# Field names follow the GovStack OpenAPI spec (camelCase mapped via source=).
# ===========================================================================


class PolicySerializer(serializers.ModelSerializer):
    """GovStack Policy object."""

    class Meta:
        model = ConsentPolicy
        fields = [  # noqa: RUF012
            "id",
            "name",
            "description",
            "version",
            "url",
            "jurisdiction",
            "industry_sector",
            "data_retention_period_days",
            "geographic_restriction",
            "storage_location",
            "third_party_data_sharing",
            "is_active",
        ]
        read_only_fields = ["id"]  # noqa: RUF012

    def to_representation(self, instance):  # noqa: ANN001, ANN201
        """
        Output in GovStack camelCase for the /config/ and /service/ API surfaces.

        F11 fix: only emit fields defined in the GovStack v23Q4 Policy schema.
        createdAt/updatedAt removed — they are CivicOS extensions that caused
        schema validation failures against the published OpenAPI spec.
        """
        data = super().to_representation(instance)
        return {
            "id": data["id"],
            "name": data["name"],
            "description": data["description"],
            "version": data["version"],
            "url": data["url"],
            "jurisdiction": data["jurisdiction"],
            "industrySector": data["industry_sector"],
            "dataRetentionPeriodDays": data["data_retention_period_days"],
            "geographicRestriction": data["geographic_restriction"],
            "storageLocation": data["storage_location"],
            "thirdPartyDataSharing": data["third_party_data_sharing"],
        }

    def to_internal_value(self, data):  # noqa: ANN001, ANN201
        """Accept both camelCase (GovStack) and snake_case inputs."""
        normalized = {}
        mapping = {
            "industrySector": "industry_sector",
            "dataRetentionPeriodDays": "data_retention_period_days",
            "geographicRestriction": "geographic_restriction",
            "storageLocation": "storage_location",
            "thirdPartyDataSharing": "third_party_data_sharing",
        }
        for key, val in data.items():
            normalized[mapping.get(key, key)] = val
        return super().to_internal_value(normalized)


class RevisionSerializer(serializers.ModelSerializer):
    """GovStack Revision object (read-only)."""

    schemaName = serializers.CharField(source="schema_name", read_only=True)  # noqa: N815
    objectId = serializers.CharField(source="object_id", read_only=True)  # noqa: N815
    serializedSnapshot = serializers.SerializerMethodField()  # noqa: N815
    serializedHash = serializers.CharField(source="serialized_hash", read_only=True)  # noqa: N815
    predecessorHash = serializers.CharField(source="predecessor_hash", read_only=True)  # noqa: N815
    authorizedByIndividual = serializers.SerializerMethodField()  # noqa: N815
    authorizedByOther = serializers.CharField(source="authorized_by_other", read_only=True)  # noqa: N815
    successor = serializers.SerializerMethodField()
    # GovStack Revision schema optional properties (not in required[], but must be present in output)  # noqa: E501
    signedWithoutObjectId = serializers.SerializerMethodField()  # noqa: N815
    predecessorSignature = serializers.SerializerMethodField()  # noqa: N815

    class Meta:
        model = ConsentRevision
        fields = [  # noqa: RUF012
            "id",
            "schemaName",
            "objectId",
            "serializedSnapshot",
            "serializedHash",
            "timestamp",
            "predecessorHash",
            "authorizedByIndividual",
            "authorizedByOther",
            "successor",
            "signedWithoutObjectId",
            "predecessorSignature",
        ]
        read_only_fields = fields

    def get_serializedSnapshot(self, obj) -> str:  # noqa: ANN001, N802
        """
        GovStack spec: Revision.serializedSnapshot is type: string. Serialized
        with the EXACT same json.dumps(sort_keys=True, default=str) call used by
        ConsentRevision._compute_hash() (models.py), so a client can
        independently recompute serializedHash from serializedSnapshot and get a
        matching value.
        """
        return json.dumps(obj.serialized_snapshot, sort_keys=True, default=str)

    def get_authorizedByIndividual(self, obj):  # noqa: ANN001, ANN201, N802
        if obj.authorized_by_individual_id:
            return str(obj.authorized_by_individual_id)
        return None

    def get_successor(self, obj):  # noqa: ANN001, ANN201
        if obj.successor_id:
            return str(obj.successor_id)
        return None

    def get_signedWithoutObjectId(self, obj) -> None:  # noqa: ANN001, N802
        # Optional boolean per GovStack Revision schema — not yet stored on model
        return None

    def get_predecessorSignature(self, obj) -> None:  # noqa: ANN001, N802
        # Optional string per GovStack Revision schema — not yet stored on model
        return None


class ControllerSerializer(serializers.Serializer):
    """GovStack Controller embedded object (used inside DataAgreement)."""

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField()
    url = serializers.URLField()


class DataAgreementSerializer(serializers.ModelSerializer):
    """
    GovStack DataAgreement object, backed by ConsentCategory.

    Maps CivicOS ConsentCategory fields to GovStack DataAgreement field names.
    """

    # GovStack's id is an opaque string in every other object in this API (Policy's
    # UUID pk already renders as a string) — ConsentCategory's plain AutoField pk
    # must not be the one exception that renders as a JSON number. This also fixes
    # a real harness-blocking bug: the upstream bb-consent reference harness's own
    # positive scenario (data_agreement.feature) asserts
    # ``response_data["dataAgreement"]["id"] == dataAgreementId`` where
    # dataAgreementId is always the URL path STRING ("1") — that comparison is
    # false in Python when the left side is the int 1, so without this override
    # the harness's own passing-status-code scenario would still fail its data
    # assertion.
    id = serializers.CharField(read_only=True)

    # Nested policy (read-only summary)
    policy = PolicySerializer(read_only=True)
    policy_id = serializers.PrimaryKeyRelatedField(
        queryset=ConsentPolicy.objects.filter(is_active=True),
        source="policy",
        write_only=True,
        required=False,
        allow_null=True,
    )

    # GovStack name mapping.
    # purpose/lawfulBasis are required (not required=False): the underlying model
    # fields (purpose_en, lawful_basis) are themselves non-blank, and the GovStack
    # DataAgreement schema lists both as required properties. A create request
    # (ConfigDataAgreementListView.post, full validation) omitting either must be
    # rejected at the serializer layer rather than silently creating a
    # ConsentCategory with purpose_en="" — a PIPEDA 4.2 plain-language-purpose
    # violation. Updates (ConfigDataAgreementDetailView.put) use partial=True,
    # which already exempts fields absent from the payload from this check, so
    # this does not break partial updates that omit purpose/lawfulBasis.
    purpose = serializers.CharField(source="purpose_en")
    lawfulBasis = serializers.CharField(source="lawful_basis")  # noqa: N815
    dataUse = serializers.CharField(source="data_use", required=False, allow_blank=True)  # noqa: N815

    # Bug 8 fix (MEDIUM, MASTER_BB_CERTIFIABILITY_REPORT.md "Consent BB"):
    # name_en/name_fr/purpose_fr previously had NO field at all on this
    # serializer, so a POST to /config/data-agreement/ could never populate
    # them — Django's non-null CharField/TextField silently defaults to ""
    # on ConsentCategory.objects.create(**data) when a kwarg is simply
    # omitted (blank=False is a serializer/form-level check only, never
    # enforced by the ORM's .create()), so every DataAgreement created
    # through this endpoint got permanently blank bilingual name/purpose
    # fields — a PIPEDA 4.2 plain-language-purpose gap for the exact fields
    # the model's own rationale depends on. These use their own snake_case
    # model attribute names directly as the FIELD name (no source= mapping
    # needed) since that's already the convention every other caller in this
    # codebase uses for these three keys (seed_consent_categories.py, the
    # test suite, ConsentService.create_data_agreement's own dict-based
    # callers) — unlike purpose/lawfulBasis above, there is no GovStack-spec
    # camelCase name for these CivicOS bilingual extensions. required=True,
    # allow_blank=False mirrors purpose/lawfulBasis: a create request omitting
    # or blanking any of the three must 400, not silently persist "".
    name_en = serializers.CharField(allow_blank=False)
    name_fr = serializers.CharField(allow_blank=False)
    purpose_fr = serializers.CharField(allow_blank=False)
    # dpia: required=True (key must be present on create) matches the live GovStack
    # v23Q4 DataAgreement schema's own `required: [id, version, purpose, lawfulBasis,
    # dpia]` list (confirmed by fetching api/consent-openapi.yaml directly) — but
    # allow_blank=True, NOT allow_blank=False like purpose/lawfulBasis above: the
    # schema's "required" here only means the key must be present in the payload
    # (no minLength is declared on this property), and the model field itself is
    # blank=True by design (not every DataAgreement has a completed DPIA — that's a
    # legitimate, common state, not an error). Forcing non-blank content here would
    # be stricter than both the live spec and the model's own truth.
    dpia = serializers.CharField(required=True, allow_blank=True)
    active = serializers.BooleanField(source="is_active", required=False)
    forgettable = serializers.BooleanField(required=False)
    lifecycle = serializers.CharField(required=False, allow_blank=True)
    language = serializers.CharField(required=False, allow_blank=True)
    dpiaDate = serializers.DateField(source="dpia_date", required=False, allow_null=True)  # noqa: N815
    dpiaEvidenceUrl = serializers.URLField(  # noqa: N815
        source="dpia_evidence_url", required=False, allow_blank=True
    )
    dpiaSummaryUrl = serializers.URLField(  # noqa: N815
        source="dpia_summary_url", required=False, allow_blank=True
    )
    dpiaUrl = serializers.URLField(source="dpia_url", required=False, allow_blank=True)  # noqa: N815

    # Flat policyId alongside nested policy object
    policyId = serializers.PrimaryKeyRelatedField(source="policy", read_only=True, allow_null=True)  # noqa: N815

    # dataRetentionPeriodDays lives on the linked Policy (spec requires it flat on DataAgreement)
    dataRetentionPeriodDays = serializers.SerializerMethodField()  # noqa: N815

    def get_dataRetentionPeriodDays(self, obj):  # noqa: ANN001, ANN201, N802
        if obj.policy_id:
            return obj.policy.data_retention_period_days
        return None

    # Additional GovStack DataAgreement spec fields
    dataControllerName = serializers.CharField(  # noqa: N815
        source="controller_name", required=False, allow_blank=True
    )
    dataControllerUrl = serializers.URLField(  # noqa: N815
        source="controller_url", required=False, allow_blank=True
    )
    dataControllerLogoImageUrl = serializers.URLField(  # noqa: N815
        source="data_controller_logo_image_url", required=False, allow_blank=True
    )
    dataUsePurpose = serializers.CharField(  # noqa: N815
        source="data_use_purpose", required=False, allow_blank=True
    )
    dataUsePurposeDescription = serializers.CharField(  # noqa: N815
        source="data_use_purpose_description", required=False, allow_blank=True
    )
    dataUsePurposeRestriction = serializers.CharField(  # noqa: N815
        source="data_use_purpose_restriction", required=False, allow_blank=True
    )
    dataUseActivity = serializers.CharField(  # noqa: N815
        source="data_use_activity", required=False, allow_blank=True
    )
    dataUsagePolicy = serializers.URLField(  # noqa: N815
        source="data_usage_policy", required=False, allow_blank=True
    )
    purposeDescription = serializers.CharField(  # noqa: N815
        source="purpose_description", required=False, allow_blank=True
    )

    # Controller embedded fields
    controller = serializers.SerializerMethodField()

    class Meta:
        model = ConsentCategory
        fields = [  # noqa: RUF012
            "id",
            "slug",
            "version",
            "language",
            "lifecycle",
            "name_en",
            "name_fr",
            "purpose",
            "purposeDescription",
            "purpose_fr",
            "lawfulBasis",
            "dataUse",
            "dataUsePurpose",
            "dataUsePurposeDescription",
            "dataUsePurposeRestriction",
            "dataUseActivity",
            "dataUsagePolicy",
            "dpia",
            "dpiaDate",
            "dpiaEvidenceUrl",
            "dpiaSummaryUrl",
            "dpiaUrl",
            "active",
            "forgettable",
            "policyId",
            "dataRetentionPeriodDays",
            "policy",
            "policy_id",
            "dataControllerName",
            "dataControllerUrl",
            "dataControllerLogoImageUrl",
            "controller",
            "attributes",
        ]
        read_only_fields = ["id"]  # noqa: RUF012

    def get_controller(self, obj):  # noqa: ANN001, ANN201
        if obj.controller_name:
            return {
                "id": str(obj.pk),
                "name": obj.controller_name,
                "url": obj.controller_url,
            }
        return None

    def to_representation(self, instance):  # noqa: ANN001, ANN201
        data = super().to_representation(instance)
        return data


class IndividualSerializer(serializers.Serializer):
    """
    GovStack Individual object.

    Maps to CivicOS User. Exposes all GovStack-required fields:
    id, externalId, externalIdType, identityProviderId.
    CivicOS extensions: name, iamRef, phone, email, consentRecordsCount.
    """

    id = serializers.UUIDField(read_only=True)
    # GovStack spec required fields
    externalId = serializers.CharField(source="pk", read_only=True)  # noqa: N815
    externalIdType = serializers.SerializerMethodField()  # noqa: N815
    identityProviderId = serializers.SerializerMethodField()  # noqa: N815
    # CivicOS extensions
    name = serializers.SerializerMethodField()
    iamRef = serializers.CharField(source="pk", read_only=True)  # noqa: N815
    phone = serializers.SerializerMethodField()
    email = serializers.EmailField(read_only=True)
    consentRecordsCount = serializers.SerializerMethodField()  # noqa: N815

    def get_name(self, obj):  # noqa: ANN001, ANN201
        full = f"{obj.first_name} {obj.last_name}".strip()
        return full or obj.email

    def get_phone(self, obj):  # noqa: ANN001, ANN201
        # phone stored on profile if exists; default blank string per spec
        return getattr(obj, "phone", "") or ""

    def get_consentRecordsCount(self, obj):  # noqa: ANN001, ANN201, N802
        return obj.consent_records.count()

    def get_externalIdType(self, obj) -> str:  # noqa: ANN001, N802
        return "civicos_user"

    def get_identityProviderId(self, obj):  # noqa: ANN001, ANN201, N802
        # CivicOS uses its own identity provider; return the provider slug.
        # In deployments with an external IdP (Keycloak, etc.) this would be
        # the IdP's identifier for this user.
        return getattr(obj, "identity_provider_id", None) or "civicos"


class ConsentRecordGovStackSerializer(serializers.ModelSerializer):
    """
    GovStack ConsentRecord shape.

    Field names match the GovStack OpenAPI spec and mock app exactly:
      dataAgreement, individual, signature, dataAgreementRevision, dataAgreementRevisionHash.

    Values are flat scalars (FK IDs) — the spec uses $ref for the type but the
    reference implementation stores and returns integer/UUID IDs, not nested objects.
    """

    optIn = serializers.BooleanField(source="opt_in", read_only=True)  # noqa: N815
    state = serializers.CharField()
    # Flat IDs using GovStack spec field names (no "Id" suffix — matches spec schema).
    # dataAgreement is a SerializerMethodField (not PrimaryKeyRelatedField) so it
    # renders as a string — ConsentCategory's plain AutoField pk would otherwise
    # serialize as a JSON number, the one inconsistent ID type in this whole API
    # (every other ID here — Policy's UUID pk, individual's UUID — is already a
    # string). See DataAgreementSerializer.id for the same fix applied there.
    dataAgreement = serializers.SerializerMethodField()  # noqa: N815
    individual = serializers.UUIDField(source="citizen_id", read_only=True)
    signature = serializers.SerializerMethodField()
    dataAgreementRevision = serializers.SerializerMethodField()  # noqa: N815
    dataAgreementRevisionHash = serializers.CharField(  # noqa: N815
        source="data_agreement_revision_hash",
        read_only=True,
    )

    class Meta:
        model = ConsentRecord
        # F14 fix: only emit fields defined in the GovStack v23Q4 ConsentRecord
        # schema.  granted_at, withdrawn_at, source removed — they are CivicOS
        # extensions that caused additionalProperties schema violations.
        fields = [  # noqa: RUF012
            "id",
            "dataAgreement",
            "dataAgreementRevision",
            "dataAgreementRevisionHash",
            "individual",
            "optIn",
            "state",
            "signature",
        ]
        read_only_fields = [  # noqa: RUF012
            "id",
            "dataAgreement",
            "dataAgreementRevision",
            "dataAgreementRevisionHash",
            "individual",
            "optIn",
            "signature",
        ]

    def get_signature(self, obj):  # noqa: ANN001, ANN201
        # related_name on ConsentSignature.consent_record is "signature_obj"
        try:
            return str(obj.signature_obj.pk)
        except ConsentSignature.DoesNotExist:
            return None
        except Exception:
            # Unexpected error (DB issue, wrong related_name, etc.) — log but don't swallow
            import logging

            logging.getLogger(__name__).warning(
                "get_signature: unexpected error for record %s", obj.pk, exc_info=True
            )
            return None

    def get_dataAgreementRevision(self, obj):  # noqa: ANN001, ANN201, N802
        if obj.data_agreement_revision_id:
            return str(obj.data_agreement_revision_id)
        return None

    def get_dataAgreement(self, obj):  # noqa: ANN001, ANN201, N802
        if obj.category_id is not None:
            return str(obj.category_id)
        return None


class WebhookSerializer(serializers.ModelSerializer):
    """GovStack Webhook object."""

    payloadUrl = serializers.URLField(source="payload_url")  # noqa: N815
    # Bug 4 (SSRF hardening) cheap registration-time check: reject non-HTTPS
    # URLs immediately, before any network call is ever made. This mirrors
    # apps.appointments.services.govstack_subscriber._validate_url /
    # _https_validator (HTTPS-scheme-only, no DNS resolution here — that is
    # deliberately deferred to dispatch time in tasks.dispatch_consent_webhook,
    # where a live network call is already happening and DNS can be checked
    # against the CURRENT resolution rather than a potentially stale one
    # captured at registration time). See validate_payloadUrl() below.
    contentType = serializers.CharField(source="content_type")  # noqa: N815
    # GovStack spec required field: "disabled" (boolean, = is_disabled)
    disabled = serializers.BooleanField(source="is_disabled", required=False, default=False)
    # secretKey is a required field in the GovStack Webhook schema and must appear
    # in all responses (GET/POST/PUT). It is the HMAC signing secret shared with
    # the webhook subscriber — conceptually an API key, not a user password.
    secretKey = serializers.CharField(source="secret_key")  # noqa: N815
    # GovStack spec field name is "events" (not subscribedEvents).
    # required=False: the spec Webhook schema has no "events" property — the cert
    # harness sends only [payloadUrl, contentType, disabled, secretKey]. Without
    # required=False, DRF would reject the request with a 400 because explicitly
    # declared JSONField does NOT inherit the model's default=list.
    events = serializers.JSONField(source="subscribed_events", required=False, default=list)
    signatureHeader = serializers.CharField(  # noqa: N815
        source="signature_header", required=False, allow_blank=True
    )
    skippedHeaders = serializers.JSONField(source="skipped_headers", required=False)  # noqa: N815
    timeStamp = serializers.DateTimeField(source="created_at", read_only=True)  # noqa: N815

    class Meta:
        model = ConsentWebhook
        # F15 fix: isActive removed — it is a CivicOS extension (inverted alias
        # for disabled) that caused additionalProperties schema violations.
        fields = [  # noqa: RUF012
            "id",
            "payloadUrl",
            "contentType",
            "disabled",
            "secretKey",
            "events",
            "signatureHeader",
            "skippedHeaders",
            "timeStamp",
        ]
        read_only_fields = ["id", "timeStamp"]  # noqa: RUF012

    def validate_payloadUrl(self, value: str) -> str:  # noqa: N802
        """
        Reject any ``payloadUrl`` that is not a well-formed HTTPS URL.

        This is the cheap, registration-time half of the Bug 4 SSRF fix: it
        catches plain-HTTP and malformed URLs immediately with a 400,
        before a webhook is ever persisted. It intentionally does NOT
        attempt DNS resolution / private-IP-range checking — a hostname
        can be re-pointed via DNS at any time after registration (TOCTOU),
        so that expensive, authoritative check lives at dispatch time in
        ``tasks.dispatch_consent_webhook`` / ``_is_safe_outbound_url``,
        where a live network call is already being made anyway.
        """
        from django.core.exceptions import ValidationError as DjangoValidationError
        from django.core.validators import URLValidator

        https_validator = URLValidator(schemes=["https"])
        try:
            https_validator(value)
        except DjangoValidationError:
            raise serializers.ValidationError(  # noqa: B904
                "payloadUrl must be a valid HTTPS URL. Plain HTTP and malformed URLs are not permitted."  # noqa: E501
            )
        return value


class SignatureSerializer(serializers.ModelSerializer):
    """
    GovStack Signature object.

    Accepts and returns camelCase field names per the GovStack OpenAPI spec.
    Required fields: payload, signature, verificationMethod, verificationPayload,
    verificationPayloadHash, verificationSignedBy, timestamp.
    """

    # Required fields — GovStack spec field name is "verificationMethod"
    # F17 fix: added "pgp" to choices (matches GovStack v23Q4 spec).
    verificationMethod = serializers.ChoiceField(  # noqa: N815
        source="verification_type",
        choices=["string", "rs256", "ed25519", "ps256", "pgp"],
        default="string",
    )
    verificationPayload = serializers.CharField(source="verification_payload")  # noqa: N815
    verificationPayloadHash = serializers.CharField(source="verification_payload_hash")  # noqa: N815
    verificationSignedBy = serializers.CharField(source="verification_signed_by")  # noqa: N815

    # Optional spec fields
    verificationArtifact = serializers.CharField(  # noqa: N815
        source="verification_artifact", required=False, allow_blank=True
    )
    verificationSignedAs = serializers.ChoiceField(  # noqa: N815
        source="verification_signed_as",
        choices=["individual", "delegate", "commissioner", ""],
        required=False,
        allow_blank=True,
    )
    verificationJwks = serializers.JSONField(  # noqa: N815
        source="verification_jwks", required=False, allow_null=True
    )
    verificationJwsHeader = serializers.CharField(  # noqa: N815
        source="verification_jws_header", required=False, allow_blank=True
    )
    # GovStack spec field name is "signedWithoutObjectReference"
    signedWithoutObjectReference = serializers.BooleanField(  # noqa: N815
        source="signed_without_object_id", required=False
    )
    objectType = serializers.CharField(source="object_type", required=False, allow_blank=True)  # noqa: N815
    objectReference = serializers.CharField(  # noqa: N815
        source="object_reference", required=False, allow_blank=True
    )

    class Meta:
        model = ConsentSignature
        # F17 fix: dataAgreementRevisionHash and dataAgreementRevisionSignedWithoutId
        # removed — they are CivicOS extensions not present in the GovStack v23Q4
        # Signature schema and caused additionalProperties validation failures.
        fields = [  # noqa: RUF012
            "id",
            "payload",
            "signature",
            "verificationMethod",
            "verificationPayload",
            "verificationPayloadHash",
            "verificationSignedBy",
            "timestamp",
            "verificationArtifact",
            "verificationSignedAs",
            "verificationJwks",
            "verificationJwsHeader",
            "signedWithoutObjectReference",
            "objectType",
            "objectReference",
        ]
        read_only_fields = ["id"]  # noqa: RUF012
