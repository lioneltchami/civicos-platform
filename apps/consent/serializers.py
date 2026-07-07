"""
Serializers for the Consent & Privacy API.

Two layers:
  1. CivicOS (legacy) serializers — used by the original /api/v1/consent/ endpoints.
  2. GovStack serializers — used by the GovStack-namespaced endpoints and shaped
     to match the GovStack Consent BB OpenAPI spec field names (camelCase in JSON
     handled by the DRF settings or by explicit field naming).
"""
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
        fields = [
            "id", "slug", "name_en", "name_fr", "purpose_en", "purpose_fr",
            "lawful_basis", "is_required", "sort_order",
        ]
        read_only_fields = fields


class ConsentRecordSerializer(serializers.ModelSerializer):
    category = ConsentCategorySerializer(read_only=True)
    category_slug = serializers.SlugRelatedField(
        source="category", slug_field="slug", read_only=True
    )

    class Meta:
        model = ConsentRecord
        fields = [
            "id", "category", "category_slug", "status", "granted_at",
            "withdrawn_at", "source",
        ]
        read_only_fields = [
            "id", "category", "category_slug", "granted_at",
            "withdrawn_at", "source",
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
        fields = [
            "id", "status", "format", "requested_at", "processed_at",
            "expires_at", "download_token",
        ]
        read_only_fields = [
            "id", "status", "format", "requested_at", "processed_at", "expires_at",
        ]

    def get_download_token(self, obj):
        if obj.status == DataExportRequest.STATUS_READY:
            return str(obj.download_token)
        return None


class ConsentAuditEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsentAuditEntry
        fields = ["id", "action", "timestamp", "details"]
        read_only_fields = fields


# ===========================================================================
# GovStack serializers
# Field names follow the GovStack OpenAPI spec (camelCase mapped via source=).
# ===========================================================================

class PolicySerializer(serializers.ModelSerializer):
    """GovStack Policy object."""

    class Meta:
        model = ConsentPolicy
        fields = [
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
        read_only_fields = ["id"]

    def to_representation(self, instance):
        """Output in GovStack camelCase for the /config/ and /service/ API surfaces."""
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
            "createdAt": instance.created_at.isoformat() if instance.created_at else None,
            "updatedAt": instance.updated_at.isoformat() if instance.updated_at else None,
        }

    def to_internal_value(self, data):
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

    schemaName = serializers.CharField(source="schema_name", read_only=True)
    objectId = serializers.CharField(source="object_id", read_only=True)
    serializedSnapshot = serializers.JSONField(source="serialized_snapshot", read_only=True)
    serializedHash = serializers.CharField(source="serialized_hash", read_only=True)
    predecessorHash = serializers.CharField(source="predecessor_hash", read_only=True)
    authorizedByIndividual = serializers.SerializerMethodField()
    authorizedByOther = serializers.CharField(source="authorized_by_other", read_only=True)
    successor = serializers.SerializerMethodField()

    class Meta:
        model = ConsentRevision
        fields = [
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
        ]
        read_only_fields = fields

    def get_authorizedByIndividual(self, obj):
        if obj.authorized_by_individual_id:
            return str(obj.authorized_by_individual_id)
        return None

    def get_successor(self, obj):
        if obj.successor_id:
            return str(obj.successor_id)
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
    # Nested policy (read-only summary)
    policy = PolicySerializer(read_only=True)
    policy_id = serializers.PrimaryKeyRelatedField(
        queryset=ConsentPolicy.objects.filter(is_active=True),
        source="policy",
        write_only=True,
        required=False,
        allow_null=True,
    )

    # GovStack name mapping
    purpose = serializers.CharField(source="purpose_en", required=False)
    lawfulBasis = serializers.CharField(source="lawful_basis", required=False)
    dataUse = serializers.CharField(source="data_use", required=False, allow_blank=True)
    dpia = serializers.CharField(required=False, allow_blank=True)
    active = serializers.BooleanField(source="is_active", required=False)
    forgettable = serializers.BooleanField(required=False)
    lifecycle = serializers.CharField(required=False, allow_blank=True)
    language = serializers.CharField(required=False, allow_blank=True)
    dpiaDate = serializers.DateField(source="dpia_date", required=False, allow_null=True)
    dpiaEvidenceUrl = serializers.URLField(source="dpia_evidence_url", required=False, allow_blank=True)
    dpiaSummaryUrl = serializers.URLField(source="dpia_summary_url", required=False, allow_blank=True)
    dpiaUrl = serializers.URLField(source="dpia_url", required=False, allow_blank=True)

    # Flat policyId alongside nested policy object
    policyId = serializers.PrimaryKeyRelatedField(source="policy", read_only=True, allow_null=True)

    # dataRetentionPeriodDays lives on the linked Policy (spec requires it flat on DataAgreement)
    dataRetentionPeriodDays = serializers.SerializerMethodField()

    def get_dataRetentionPeriodDays(self, obj):
        if obj.policy_id:
            return obj.policy.data_retention_period_days
        return None

    # Additional GovStack DataAgreement spec fields
    dataControllerName = serializers.CharField(source="controller_name", required=False, allow_blank=True)
    dataControllerUrl = serializers.URLField(source="controller_url", required=False, allow_blank=True)
    dataControllerLogoImageUrl = serializers.URLField(
        source="data_controller_logo_image_url", required=False, allow_blank=True
    )
    dataUsePurpose = serializers.CharField(source="data_use_purpose", required=False, allow_blank=True)
    dataUsePurposeDescription = serializers.CharField(
        source="data_use_purpose_description", required=False, allow_blank=True
    )
    dataUsePurposeRestriction = serializers.CharField(
        source="data_use_purpose_restriction", required=False, allow_blank=True
    )
    dataUseActivity = serializers.CharField(source="data_use_activity", required=False, allow_blank=True)
    dataUsagePolicy = serializers.URLField(source="data_usage_policy", required=False, allow_blank=True)
    purposeDescription = serializers.CharField(
        source="purpose_description", required=False, allow_blank=True
    )

    # Controller embedded fields
    controller = serializers.SerializerMethodField()

    class Meta:
        model = ConsentCategory
        fields = [
            "id",
            "slug",
            "version",
            "language",
            "lifecycle",
            "purpose",
            "purposeDescription",
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
            # Bilingual fields (CivicOS extension)
            "name_en",
            "name_fr",
            "purpose_fr",
        ]
        read_only_fields = ["id"]

    def get_controller(self, obj):
        if obj.controller_name:
            return {
                "id": str(obj.pk),
                "name": obj.controller_name,
                "url": obj.controller_url,
            }
        return None

    def to_representation(self, instance):
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
    externalId = serializers.CharField(source="pk", read_only=True)
    externalIdType = serializers.SerializerMethodField()
    identityProviderId = serializers.SerializerMethodField()
    # CivicOS extensions
    name = serializers.SerializerMethodField()
    iamRef = serializers.CharField(source="pk", read_only=True)
    phone = serializers.SerializerMethodField()
    email = serializers.EmailField(read_only=True)
    consentRecordsCount = serializers.SerializerMethodField()

    def get_name(self, obj):
        full = f"{obj.first_name} {obj.last_name}".strip()
        return full or obj.email

    def get_phone(self, obj):
        # phone stored on profile if exists; default blank string per spec
        return getattr(obj, "phone", "") or ""

    def get_consentRecordsCount(self, obj):
        return obj.consent_records.count()

    def get_externalIdType(self, obj):
        return "civicos_user"

    def get_identityProviderId(self, obj):
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
    optIn = serializers.BooleanField(source="opt_in", read_only=True)
    state = serializers.CharField()
    # Flat IDs using GovStack spec field names (no "Id" suffix — matches spec schema)
    dataAgreement = serializers.PrimaryKeyRelatedField(source="category", read_only=True)
    individual = serializers.UUIDField(source="citizen_id", read_only=True)
    signature = serializers.SerializerMethodField()
    dataAgreementRevision = serializers.SerializerMethodField()
    dataAgreementRevisionHash = serializers.CharField(
        source="data_agreement_revision_hash",
        read_only=True,
    )

    class Meta:
        model = ConsentRecord
        fields = [
            "id",
            "dataAgreement",
            "dataAgreementRevision",
            "dataAgreementRevisionHash",
            "individual",
            "optIn",
            "state",
            "signature",
            "granted_at",
            "withdrawn_at",
            "source",
        ]
        read_only_fields = [
            "id", "dataAgreement", "dataAgreementRevision", "dataAgreementRevisionHash",
            "individual", "optIn", "signature", "granted_at", "withdrawn_at",
        ]

    def get_signature(self, obj):
        # related_name on ConsentSignature.consent_record is "signature_obj"
        try:
            return str(obj.signature_obj.pk)
        except Exception:
            return None

    def get_dataAgreementRevision(self, obj):
        if obj.data_agreement_revision_id:
            return str(obj.data_agreement_revision_id)
        return None


class WebhookSerializer(serializers.ModelSerializer):
    """GovStack Webhook object."""

    payloadUrl = serializers.URLField(source="payload_url")
    contentType = serializers.CharField(source="content_type")
    # GovStack spec required field: "disabled" (boolean, = is_disabled)
    disabled = serializers.BooleanField(source="is_disabled", required=False, default=False)
    # CivicOS extension: isActive = inverted disabled (kept for backwards compat)
    isActive = serializers.SerializerMethodField()
    secretKey = serializers.CharField(source="secret_key", write_only=True)
    # GovStack spec field name is "events" (not subscribedEvents)
    events = serializers.JSONField(source="subscribed_events")
    signatureHeader = serializers.CharField(source="signature_header", required=False, allow_blank=True)
    skippedHeaders = serializers.JSONField(source="skipped_headers", required=False)
    timeStamp = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = ConsentWebhook
        fields = [
            "id",
            "payloadUrl",
            "contentType",
            "disabled",
            "isActive",
            "secretKey",
            "events",
            "signatureHeader",
            "skippedHeaders",
            "timeStamp",
        ]
        read_only_fields = ["id", "timeStamp"]

    def get_isActive(self, obj):
        return not obj.is_disabled


class SignatureSerializer(serializers.ModelSerializer):
    """
    GovStack Signature object.

    Accepts and returns camelCase field names per the GovStack OpenAPI spec.
    Required fields: payload, signature, verificationMethod, verificationPayload,
    verificationPayloadHash, verificationSignedBy, timestamp.
    """

    # Required fields — GovStack spec field name is "verificationMethod"
    verificationMethod = serializers.ChoiceField(
        source="verification_type",
        choices=["string", "rs256", "ed25519", "ps256"],
        default="string",
    )
    verificationPayload = serializers.CharField(source="verification_payload")
    verificationPayloadHash = serializers.CharField(source="verification_payload_hash")
    verificationSignedBy = serializers.CharField(source="verification_signed_by")
    dataAgreementRevisionHash = serializers.CharField(
        source="data_agreement_revision_hash", required=False, allow_blank=True
    )
    dataAgreementRevisionSignedWithoutId = serializers.BooleanField(
        source="data_agreement_revision_signed_without_id", required=False
    )

    # Optional fields
    verificationArtifact = serializers.CharField(
        source="verification_artifact", required=False, allow_blank=True
    )
    verificationSignedAs = serializers.ChoiceField(
        source="verification_signed_as",
        choices=["individual", "delegate", "commissioner", ""],
        required=False,
        allow_blank=True,
    )
    verificationJwks = serializers.JSONField(
        source="verification_jwks", required=False, allow_null=True
    )
    verificationJwsHeader = serializers.CharField(
        source="verification_jws_header", required=False, allow_blank=True
    )
    # GovStack spec field name is "signedWithoutObjectReference"
    signedWithoutObjectReference = serializers.BooleanField(
        source="signed_without_object_id", required=False
    )
    objectType = serializers.CharField(
        source="object_type", required=False, allow_blank=True
    )
    objectReference = serializers.CharField(
        source="object_reference", required=False, allow_blank=True
    )

    class Meta:
        model = ConsentSignature
        fields = [
            "id",
            "payload",
            "signature",
            "verificationMethod",
            "verificationPayload",
            "verificationPayloadHash",
            "verificationSignedBy",
            "timestamp",
            "dataAgreementRevisionHash",
            "dataAgreementRevisionSignedWithoutId",
            "verificationArtifact",
            "verificationSignedAs",
            "verificationJwks",
            "verificationJwsHeader",
            "signedWithoutObjectReference",
            "objectType",
            "objectReference",
        ]
        read_only_fields = ["id"]
