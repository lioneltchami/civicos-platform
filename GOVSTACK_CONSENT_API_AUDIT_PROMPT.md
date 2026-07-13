You are an expert adversarial compliance auditor. Your job is to find every gap, 
bug, and non-compliance in a Django implementation of the GovStack Consent 
Building Block API spec. Be brutally honest — assume nothing is correct until 
you verify it line by line.

## Context

This is CivicOS, a Canadian government platform built on Django 5.2 / Wagtail.
It implements the GovStack Consent Building Block v23Q4 (OpenAPI spec at:
https://raw.githubusercontent.com/GovStackWorkingGroup/bb-consent/v23Q4/api/consent-openapi.yaml)

The live spec documentation is at: https://consent.govstack.global/8-service-apis.md

The reference mock Django app fixtures are at:
https://raw.githubusercontent.com/GovStackWorkingGroup/bb-consent/main/examples/mock/djangoapp/fixtures.json

The implementation will be submitted to testing.govstack.global for certification.

## What you must check — exhaustively

### 1. Every endpoint in the spec must exist in the code
The spec defines these paths. Verify each is implemented:

CONFIG namespace:
- POST   /config/policy/
- GET    /config/policy/{policyId}/
- PUT    /config/policy/{policyId}/
- DELETE /config/policy/{policyId}/
- GET    /config/policy/{policyId}/revisions/
- GET    /config/policies/
- POST   /config/data-agreement/
- GET    /config/data-agreement/{dataAgreementId}/
- PUT    /config/data-agreement/{dataAgreementId}/
- DELETE /config/data-agreement/{dataAgreementId}/
- GET    /config/data-agreements/
- POST   /config/individual/
- GET    /config/individual/{individualId}/
- GET    /config/individuals/
- POST   /config/webhook/
- GET    /config/webhook/{webhookId}/
- PUT    /config/webhook/{webhookId}/
- DELETE /config/webhook/{webhookId}/
- GET    /config/webhooks/

SERVICE namespace:
- POST   /service/individual/
- GET    /service/individual/{individualId}/
- PUT    /service/individual/{individualId}/
- GET    /service/individuals/
- GET    /service/data-agreement/{dataAgreementId}/
- GET    /service/policy/{policyId}/
- GET    /service/verification/data-agreements/
- GET    /service/verification/consent-records/
- GET    /service/verification/consent-record/{consentRecordId}/
- POST   /service/individual/record/consent-record/draft/
- POST   /service/individual/record/consent-record/
- GET    /service/individual/record/consent-record/
- PUT    /service/individual/record/consent-record/{consentRecordId}/
- POST   /service/individual/record/consent-record/{consentRecordId}/signature/
- PUT    /service/individual/record/consent-record/{consentRecordId}/signature/
- POST   /service/individual/record/data-agreement/{dataAgreementId}/
- GET    /service/individual/record/data-agreement/{dataAgreementId}/
- GET    /service/individual/record/data-agreement/{dataAgreementId}/all/
- DELETE /service/individual/record/

AUDIT namespace:
- GET    /audit/consent-records/
- GET    /audit/consent-record/{consentRecordId}/
- GET    /audit/data-agreements/
- GET    /audit/data-agreement/{dataAgreementId}/

### 2. HTTP status codes
The spec YAML shows '200' for ALL operations including POSTs. Verify no 
endpoint returns 201 for a POST that the spec defines as 200.

### 3. Response envelope keys
Every response must use the exact envelope key the spec shows:
- Policy responses: {"policy": ..., "revision": ...}
- DataAgreement responses: {"dataAgreement": ..., "revision": ...}
- Individual responses: {"individual": ...}
- Webhook responses: {"webhook": ...}
- ConsentRecord responses: {"consentRecord": ..., "revision": ..., "signature": ...}
- List responses use plural keys: {"policies": [...], "dataAgreements": [...], etc.}

### 4. Schema field names — every field on every object

**Policy schema** (spec required: id, name, version, url):
- id, name, version, url, jurisdiction, industrySector, dataRetentionPeriodDays,
  geographicRestriction, storageLocation

**DataAgreement schema** (spec required: id, version, purpose, lawfulBasis, dpia):
- id, version, controller (nested: id/name/url), policy (nested Policy),
  purpose, lawfulBasis, dataUse, dpia, active, forgettable, lifecycle

**Individual schema** (spec: id, externalId, externalIdType, identityProviderId):
- id, externalId, externalIdType, identityProviderId

**ConsentRecord schema** (spec required: id, dataAgreementRevisionHash, state):
- id, dataAgreement (FK id), dataAgreementRevision (FK id),
  dataAgreementRevisionHash, individual (FK id), optIn, state, signature (FK id)
  NOTE: Field names are dataAgreement/individual/signature — NOT dataAgreementId/individualId/signatureId

**Revision schema** (spec required: id, schemaName, objectId, serializedSnapshot, serializedHash, timestamp):
- id, schemaName, objectId, signedWithoutObjectId, serializedSnapshot,
  serializedHash, timestamp, authorizedByIndividual, authorizedByOther,
  successor, predecessorHash

**Signature schema** (spec required: id, payload, signature, verificationMethod,
  verificationPayload, verificationPayloadHash, verificationSignedBy, timestamp):
- id, payload, signature, verificationMethod (NOT verificationType),
  verificationPayload, verificationPayloadHash, verificationArtifact,
  verificationSignedBy, verificationSignedAs, verificationJwsHeader,
  timestamp, signedWithoutObjectReference (NOT signedWithoutObjectId),
  objectType, objectReference

**Webhook schema** (spec required: id, payloadUrl, contentType, disabled, secretKey):
- id, payloadUrl, contentType, disabled (boolean, NOT isActive), secretKey

### 5. ConsentRecord.state enum values
Valid states per spec: unsigned | pending | signed | revoked
The state machine: 
  - New record starts as "unsigned"
  - After signature attached → "signed"
  - After withdrawal → "revoked"

### 6. Draft endpoint specifics
- Must be POST (not GET)
- Both individualId AND dataAgreementId are required query params
- Returns {consentRecord: {id: null, state: "unsigned", optIn: false, ...}, signature: {...}}

### 7. Security / auth on each namespace
- /config/ → must require admin/org role
- /service/ → must require authenticated individual; records must be scoped to 
  the authenticated user (IDOR protection)
- /audit/ → must require auditor role (separate from admin)
- All unauthenticated requests → 401

### 8. Revision chain integrity
- Every Policy create/update must produce a ConsentRevision
- Every DataAgreement create/update must produce a ConsentRevision
- Each revision must have: schemaName, objectId, serializedSnapshot, serializedHash
- The serializedHash must be SHA-256 of JSON.dumps(serializedSnapshot, sort_keys=True)
- Revisions are append-only (no updates, no deletes)
- The predecessor chain: each revision stores predecessorHash = previous revision's serializedHash

### 9. Webhook dispatch
- Must fire on consent.granted and consent.withdrawn events
- Payload must include the event type in a header (X-GovStack-Event or equivalent)
- Signature must use HMAC-SHA256 with the webhook's secretKey
- Signature header name must be configurable (signatureHeader field on webhook)
- Must use transaction.on_commit() so webhook fires AFTER the DB transaction commits

### 10. Right to Be Forgotten (DELETE /service/individual/record/)
- Must delete ConsentRecords where the DataAgreement has forgettable=True
- Must NOT delete records for required or non-forgettable DataAgreements
- Must write an audit entry

### 11. Pagination
- All list endpoints must support ?offset= and ?limit= query params
- Response must include results array (not paginated DRF object)

### 12. /config/policy/{id}/revisions/ response
- Spec shows response with "policy" key only (not "revisions" array)
- Check what the implementation actually returns

### 13. Webhook subscription model
- The spec has a separate WebhookEvent + WebhookEventSubscription schema
- The implementation stores subscribed_events as a JSON array on the webhook
- This is acceptable as a denormalized implementation

## The actual code to review

Here are the key files. Review every line against the spec requirements above.

---

### apps/consent/govstack_urls.py

```python
from django.urls import path
from . import govstack_views as v

urlpatterns = [
    # CONFIG — Policy
    path("config/policies/",          v.ConfigPolicyListView.as_view(),      name="gs-policy-list"),
    path("config/policy/",            v.ConfigPolicyListView.as_view(),      name="gs-policy-create"),
    path("config/policy/<uuid:policy_id>/",
                                      v.ConfigPolicyDetailView.as_view(),    name="gs-policy-detail"),
    path("config/policy/<uuid:policy_id>/revisions/",
                                      v.ConfigPolicyRevisionsView.as_view(), name="gs-policy-revisions"),

    # CONFIG — DataAgreement
    path("config/data-agreements/",   v.ConfigDataAgreementListView.as_view(),   name="gs-da-list"),
    path("config/data-agreement/",    v.ConfigDataAgreementListView.as_view(),   name="gs-da-create"),
    path("config/data-agreement/<int:data_agreement_id>/",
                                      v.ConfigDataAgreementDetailView.as_view(), name="gs-da-detail"),

    # CONFIG — Individual
    path("config/individuals/",       v.ConfigIndividualListView.as_view(),   name="gs-config-individual-list"),
    path("config/individual/",        v.ConfigIndividualListView.as_view(),   name="gs-config-individual-create"),
    path("config/individual/<uuid:individual_id>/",
                                      v.ConfigIndividualDetailView.as_view(), name="gs-config-individual-detail"),

    # CONFIG — Webhook
    path("config/webhooks/",          v.ConfigWebhookListView.as_view(),    name="gs-webhook-list"),
    path("config/webhook/",           v.ConfigWebhookListView.as_view(),    name="gs-webhook-create"),
    path("config/webhook/<uuid:webhook_id>/payload/",
                                      v.ConfigWebhookPayloadView.as_view(), name="gs-webhook-payload"),
    path("config/webhook/<uuid:webhook_id>/",
                                      v.ConfigWebhookDetailView.as_view(),  name="gs-webhook-detail"),

    # SERVICE — Individual
    path("service/individuals/",      v.ServiceIndividualView.as_view(),    name="gs-service-individual-list"),
    path("service/individual/",       v.ServiceIndividualView.as_view(),    name="gs-service-individual-create"),
    path("service/individual/<uuid:individual_id>/",
                                      v.ServiceIndividualView.as_view(),    name="gs-service-individual-detail"),

    # SERVICE — DataAgreement / Policy read-only
    path("service/data-agreement/<int:data_agreement_id>/",
                                      v.ServiceDataAgreementDetailView.as_view(), name="gs-service-da-detail"),
    path("service/policy/<uuid:policy_id>/",
                                      v.ServicePolicyDetailView.as_view(),   name="gs-service-policy-detail"),

    # SERVICE — Verification
    path("service/verification/data-agreements/",
                                      v.ServiceVerificationDataAgreementsView.as_view(),
                                      name="gs-verification-da-list"),
    path("service/verification/consent-records/",
                                      v.ServiceVerificationConsentRecordsView.as_view(),
                                      name="gs-verification-cr-list"),
    path("service/verification/consent-record/<uuid:consent_record_id>/",
                                      v.ServiceVerificationConsentRecordDetailView.as_view(),
                                      name="gs-verification-cr-detail"),

    # SERVICE — Individual ConsentRecord CRUD
    path("service/individual/record/consent-record/draft/",
                                      v.ServiceIndividualConsentRecordDraftView.as_view(),
                                      name="gs-cr-draft"),
    path("service/individual/record/consent-record/",
                                      v.ServiceIndividualConsentRecordListView.as_view(),
                                      name="gs-cr-list-create"),
    path("service/individual/record/consent-record/<uuid:consent_record_id>/",
                                      v.ServiceIndividualConsentRecordDetailView.as_view(),
                                      name="gs-cr-detail"),
    path("service/individual/record/data-agreement/<int:data_agreement_id>/all/",
                                      v.ServiceIndividualDataAgreementAllConsentRecordsView.as_view(),
                                      name="gs-cr-by-da-all"),
    path("service/individual/record/data-agreement/<int:data_agreement_id>/",
                                      v.ServiceIndividualDataAgreementConsentRecordView.as_view(),
                                      name="gs-cr-by-da"),
    path("service/individual/record/consent-record/<uuid:consent_record_id>/signature/",
                                      v.ServiceConsentRecordSignatureView.as_view(),
                                      name="gs-cr-signature"),
    path("service/individual/record/",
                                      v.ServiceIndividualRightToBeForgottenView.as_view(),
                                      name="gs-rtbf"),

    # AUDIT
    path("audit/consent-records/",    v.AuditConsentRecordListView.as_view(),    name="gs-audit-cr-list"),
    path("audit/consent-record/<uuid:consent_record_id>/",
                                      v.AuditConsentRecordDetailView.as_view(),  name="gs-audit-cr-detail"),
    path("audit/data-agreements/",    v.AuditDataAgreementListView.as_view(),    name="gs-audit-da-list"),
    path("audit/data-agreement/<int:data_agreement_id>/",
                                      v.AuditDataAgreementDetailView.as_view(),  name="gs-audit-da-detail"),
    path("audit/consent-log/",        v.AuditConsentLogView.as_view(),           name="gs-audit-log"),
]
```

---

### apps/consent/serializers.py (GovStack section only)

```python
class PolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsentPolicy
        fields = ["id","name","description","version","url","jurisdiction",
                  "industry_sector","data_retention_period_days","geographic_restriction",
                  "storage_location","third_party_data_sharing","is_active"]
        read_only_fields = ["id"]

    def to_representation(self, instance):
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


class RevisionSerializer(serializers.ModelSerializer):
    schemaName = serializers.CharField(source="schema_name", read_only=True)
    objectId = serializers.CharField(source="object_id", read_only=True)
    serializedSnapshot = serializers.JSONField(source="serialized_snapshot", read_only=True)
    serializedHash = serializers.CharField(source="serialized_hash", read_only=True)
    predecessorHash = serializers.CharField(source="predecessor_hash", read_only=True)
    authorizedByOther = serializers.CharField(source="authorized_by_other", read_only=True)

    class Meta:
        model = ConsentRevision
        fields = ["id","schemaName","objectId","serializedSnapshot","serializedHash",
                  "timestamp","predecessorHash","authorizedByOther"]
        read_only_fields = fields


class DataAgreementSerializer(serializers.ModelSerializer):
    policy = PolicySerializer(read_only=True)
    policy_id = serializers.PrimaryKeyRelatedField(
        queryset=ConsentPolicy.objects.filter(is_active=True),
        source="policy", write_only=True, required=False, allow_null=True)
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
    policyId = serializers.PrimaryKeyRelatedField(source="policy", read_only=True, allow_null=True)
    dataRetentionPeriodDays = serializers.SerializerMethodField()
    dataControllerName = serializers.CharField(source="controller_name", required=False, allow_blank=True)
    dataControllerUrl = serializers.URLField(source="controller_url", required=False, allow_blank=True)
    dataControllerLogoImageUrl = serializers.URLField(
        source="data_controller_logo_image_url", required=False, allow_blank=True)
    dataUsePurpose = serializers.CharField(source="data_use_purpose", required=False, allow_blank=True)
    dataUsePurposeDescription = serializers.CharField(
        source="data_use_purpose_description", required=False, allow_blank=True)
    dataUsePurposeRestriction = serializers.CharField(
        source="data_use_purpose_restriction", required=False, allow_blank=True)
    dataUseActivity = serializers.CharField(source="data_use_activity", required=False, allow_blank=True)
    dataUsagePolicy = serializers.URLField(source="data_usage_policy", required=False, allow_blank=True)
    purposeDescription = serializers.CharField(
        source="purpose_description", required=False, allow_blank=True)
    controller = serializers.SerializerMethodField()

    def get_dataRetentionPeriodDays(self, obj):
        if obj.policy_id:
            return obj.policy.data_retention_period_days
        return None

    def get_controller(self, obj):
        if obj.controller_name:
            return {"id": str(obj.pk), "name": obj.controller_name, "url": obj.controller_url}
        return None

    class Meta:
        model = ConsentCategory
        fields = ["id","slug","version","language","lifecycle","purpose","purposeDescription",
                  "lawfulBasis","dataUse","dataUsePurpose","dataUsePurposeDescription",
                  "dataUsePurposeRestriction","dataUseActivity","dataUsagePolicy","dpia",
                  "dpiaDate","dpiaEvidenceUrl","dpiaSummaryUrl","dpiaUrl","active","forgettable",
                  "policyId","dataRetentionPeriodDays","policy","policy_id","dataControllerName",
                  "dataControllerUrl","dataControllerLogoImageUrl","controller","attributes",
                  "name_en","name_fr","purpose_fr"]
        read_only_fields = ["id"]


class IndividualSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.SerializerMethodField()
    iamRef = serializers.CharField(source="pk", read_only=True)
    phone = serializers.SerializerMethodField()
    email = serializers.EmailField(read_only=True)
    consentRecordsCount = serializers.SerializerMethodField()
    externalId = serializers.CharField(source="pk", read_only=True)
    externalIdType = serializers.SerializerMethodField()

    def get_name(self, obj):
        full = f"{obj.first_name} {obj.last_name}".strip()
        return full or obj.email

    def get_phone(self, obj): return getattr(obj, "phone", "") or ""
    def get_consentRecordsCount(self, obj): return obj.consent_records.count()
    def get_externalIdType(self, obj): return "civicos_user"


class ConsentRecordGovStackSerializer(serializers.ModelSerializer):
    optIn = serializers.BooleanField(source="opt_in", read_only=True)
    state = serializers.CharField()
    # Field names match spec exactly (no "Id" suffix)
    dataAgreement = serializers.PrimaryKeyRelatedField(source="category", read_only=True)
    individual = serializers.UUIDField(source="citizen_id", read_only=True)
    signature = serializers.SerializerMethodField()
    dataAgreementRevision = serializers.SerializerMethodField()
    dataAgreementRevisionHash = serializers.CharField(
        source="data_agreement_revision_hash", read_only=True)

    class Meta:
        model = ConsentRecord
        fields = ["id","dataAgreement","dataAgreementRevision","dataAgreementRevisionHash",
                  "individual","optIn","state","signature","granted_at","withdrawn_at","source"]

    def get_signature(self, obj):
        try: return str(obj.signature_obj.pk)
        except Exception: return None

    def get_dataAgreementRevision(self, obj):
        if obj.data_agreement_revision_id:
            return str(obj.data_agreement_revision_id)
        return None


class WebhookSerializer(serializers.ModelSerializer):
    payloadUrl = serializers.URLField(source="payload_url")
    contentType = serializers.CharField(source="content_type")
    disabled = serializers.BooleanField(source="is_disabled", required=False, default=False)
    isActive = serializers.SerializerMethodField()  # CivicOS extension
    secretKey = serializers.CharField(source="secret_key", write_only=True)
    events = serializers.JSONField(source="subscribed_events")
    signatureHeader = serializers.CharField(source="signature_header", required=False, allow_blank=True)
    skippedHeaders = serializers.JSONField(source="skipped_headers", required=False)
    timeStamp = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = ConsentWebhook
        fields = ["id","payloadUrl","contentType","disabled","isActive","secretKey",
                  "events","signatureHeader","skippedHeaders","timeStamp"]
        read_only_fields = ["id","timeStamp"]

    def get_isActive(self, obj): return not obj.is_disabled


class SignatureSerializer(serializers.ModelSerializer):
    verificationMethod = serializers.ChoiceField(
        source="verification_type",
        choices=["string", "rs256", "ed25519", "ps256"],
        default="string")
    verificationPayload = serializers.CharField(source="verification_payload")
    verificationPayloadHash = serializers.CharField(source="verification_payload_hash")
    verificationSignedBy = serializers.CharField(source="verification_signed_by")
    dataAgreementRevisionHash = serializers.CharField(
        source="data_agreement_revision_hash", required=False, allow_blank=True)
    dataAgreementRevisionSignedWithoutId = serializers.BooleanField(
        source="data_agreement_revision_signed_without_id", required=False)
    verificationArtifact = serializers.CharField(
        source="verification_artifact", required=False, allow_blank=True)
    verificationSignedAs = serializers.ChoiceField(
        source="verification_signed_as",
        choices=["individual","delegate","commissioner",""],
        required=False, allow_blank=True)
    verificationJwks = serializers.JSONField(
        source="verification_jwks", required=False, allow_null=True)
    verificationJwsHeader = serializers.CharField(
        source="verification_jws_header", required=False, allow_blank=True)
    signedWithoutObjectReference = serializers.BooleanField(
        source="signed_without_object_id", required=False)
    objectType = serializers.CharField(source="object_type", required=False, allow_blank=True)
    objectReference = serializers.CharField(source="object_reference", required=False, allow_blank=True)

    class Meta:
        model = ConsentSignature
        fields = ["id","payload","signature","verificationMethod","verificationPayload",
                  "verificationPayloadHash","verificationSignedBy","timestamp",
                  "dataAgreementRevisionHash","dataAgreementRevisionSignedWithoutId",
                  "verificationArtifact","verificationSignedAs","verificationJwks",
                  "verificationJwsHeader","signedWithoutObjectReference","objectType","objectReference"]
        read_only_fields = ["id"]
```

---

### Key model facts

- ConsentPolicy → UUID pk
- ConsentCategory (DataAgreement) → INTEGER pk
- ConsentRecord → UUID pk
- ConsentRevision → UUID pk
- ConsentWebhook → UUID pk
- ConsentSignature.consent_record FK has related_name="signature_obj"
- ConsentRecord.STATE_CHOICES: unsigned, pending, signed, revoked, pending_signatures (legacy)
- ConsentRecord.opt_in is a @property: returns status == "granted"

---

### ConsentService key behaviors

- grant(): creates or updates ConsentRecord, sets state="signed", creates 
  ConsentAuditEntry, fires webhook via transaction.on_commit() INSIDE atomic()
- withdraw(): sets state="revoked", status="withdrawn", fires webhook
- right_to_be_forgotten(): deletes ConsentRecords where category.forgettable=True

---

## Your task

Do a line-by-line, field-by-field, endpoint-by-endpoint compliance review.

For EACH finding report:
- SEVERITY: CRITICAL / MAJOR / MINOR
- LOCATION: exact file + line/method
- SPEC REQUIREMENT: what the spec says
- ACTUAL BEHAVIOR: what the code does
- FIX: concrete code change needed

Also check:
1. Are there any spec endpoints the URL routing DOESN'T register?
2. Are there any HTTP method mismatches (e.g. spec says POST, code handles GET)?
3. Are there any response fields the spec requires that the serializers DON'T include?
4. Are there any field names in the serializers that DON'T match the spec?
5. Does the /config/policy/{id}/revisions/ response include a "revisions" array or just "policy"? (spec only shows "policy" in the response schema)
6. Does the IndividualSerializer return the spec fields (id, externalId, externalIdType, identityProviderId) or CivicOS-specific ones?
7. Is pagination (offset/limit) applied consistently across ALL list endpoints?
8. Are all 401 (unauthenticated) and 403 (unauthorized) cases handled correctly?
9. Does the draft endpoint correctly handle the case where individualId and dataAgreementId are provided as query params on a POST?
10. Is there anything in the webhook dispatch that could cause silent failures that would show up in a test harness?

Be maximally skeptical. Find things the previous review rounds missed.
Output a numbered list of findings, sorted by severity. ---- OUTPUT THIS TO A MD FILE.