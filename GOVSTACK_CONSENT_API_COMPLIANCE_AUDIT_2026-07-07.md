# GovStack Consent BB Compliance Audit

Reviewed against:
- Spec: https://raw.githubusercontent.com/GovStackWorkingGroup/bb-consent/v23Q4/api/consent-openapi.yaml
- Service docs: https://consent.govstack.global/8-service-apis.md

## Findings

1. CRITICAL - Audit namespace is open to any authenticated user
   LOCATION: [apps/consent/govstack_views.py:935](/Users/lionel/builders/govstack/apps/consent/govstack_views.py#L935), [apps/consent/govstack_views.py:954](/Users/lionel/builders/govstack/apps/consent/govstack_views.py#L954), [apps/consent/govstack_views.py:970](/Users/lionel/builders/govstack/apps/consent/govstack_views.py#L970), [apps/consent/govstack_views.py:986](/Users/lionel/builders/govstack/apps/consent/govstack_views.py#L986)
   SPEC REQUIREMENT: `/audit/` must require an auditor role separate from admin; unauthenticated callers must get 401 and unauthorized callers must not be able to read audit records.
   ACTUAL BEHAVIOR: `AuditConsentRecordListView`, `AuditConsentRecordDetailView`, `AuditDataAgreementListView`, and `AuditDataAgreementDetailView` all use `permission_classes = [IsAuthenticated]`. Any authenticated token can read every consent record and data agreement. The only auditor-gated view in this module is the non-spec `audit/consent-log/` extension.
   FIX: Apply an auditor permission class to the four spec audit views and keep any internal diagnostics on a separate route.

2. MAJOR - Config namespace is staff-only; there is no org-role mapping
   LOCATION: [apps/consent/govstack_views.py:109](/Users/lionel/builders/govstack/apps/consent/govstack_views.py#L109), [apps/consent/govstack_views.py:228](/Users/lionel/builders/govstack/apps/consent/govstack_views.py#L228), [apps/consent/govstack_views.py:321](/Users/lionel/builders/govstack/apps/consent/govstack_views.py#L321), [apps/consent/govstack_views.py:386](/Users/lionel/builders/govstack/apps/consent/govstack_views.py#L386), [apps/consent/govstack_views.py:417](/Users/lionel/builders/govstack/apps/consent/govstack_views.py#L417), [apps/consent/govstack_views.py:449](/Users/lionel/builders/govstack/apps/consent/govstack_views.py#L449)
   SPEC REQUIREMENT: `/config/` must be available to an admin/org role, not only Django staff/superuser.
   ACTUAL BEHAVIOR: Every config view uses `IsAdminUser`. There is no `IsOrgUser` or equivalent org-scope permission in the consent app, so valid org-integrated callers are rejected unless they are staff.
   FIX: Implement an org-role permission and apply `admin OR org` gating consistently across the config namespace.

3. MAJOR - Optional `revisionId` selectors are ignored on policy read and draft generation
   LOCATION: [apps/consent/govstack_views.py:154](/Users/lionel/builders/govstack/apps/consent/govstack_views.py#L154), [apps/consent/govstack_views.py:576](/Users/lionel/builders/govstack/apps/consent/govstack_views.py#L576), [apps/consent/govstack_views.py:850](/Users/lionel/builders/govstack/apps/consent/govstack_views.py#L850)
   SPEC REQUIREMENT: Policy read endpoints and the draft endpoint accept an optional `revisionId` query parameter and should honor it when present.
   ACTUAL BEHAVIOR: Both policy detail views always return the latest revision (`successor__isnull=True`), and the draft endpoint always uses the latest DataAgreement revision. The supplied `revisionId` is never read.
   FIX: Resolve the requested revision when `revisionId` is supplied; only fall back to the latest revision when the parameter is absent.

4. MAJOR - `/config/policy/{policyId}/revisions/` returns the wrong envelope
   LOCATION: [apps/consent/govstack_views.py:215](/Users/lionel/builders/govstack/apps/consent/govstack_views.py#L215)
   SPEC REQUIREMENT: The spec shows this response with a `policy` key only.
   ACTUAL BEHAVIOR: The view returns `{"policy", "revisions", "total"}`. That is a schema mismatch, not just an extra convenience field.
   FIX: Return the exact schema the spec defines. If the harness expects only `policy`, remove the revisions array and pagination count from this endpoint.

5. MAJOR - Policy responses are not schema-pure
   LOCATION: [apps/consent/serializers.py:94](/Users/lionel/builders/govstack/apps/consent/serializers.py#L94), [apps/consent/serializers.py:115](/Users/lionel/builders/govstack/apps/consent/serializers.py#L115)
   SPEC REQUIREMENT: Policy objects only expose `id`, `name`, `version`, `url`, `jurisdiction`, `industrySector`, `dataRetentionPeriodDays`, `geographicRestriction`, and `storageLocation`.
   ACTUAL BEHAVIOR: `PolicySerializer` adds `description`, `thirdPartyDataSharing`, `createdAt`, and `updatedAt` to every policy response, and these extras flow through config and service policy endpoints.
   FIX: Trim the serializer to the spec fields only. Keep internal metadata out of the GovStack response envelope.

6. MAJOR - DataAgreement responses expose many unsupported fields and mis-model the controller
   LOCATION: [apps/consent/serializers.py:208](/Users/lionel/builders/govstack/apps/consent/serializers.py#L208), [apps/consent/serializers.py:311](/Users/lionel/builders/govstack/apps/consent/serializers.py#L311), [apps/consent/services.py:554](/Users/lionel/builders/govstack/apps/consent/services.py#L554)
   SPEC REQUIREMENT: DataAgreement responses should only expose the schema fields around `id`, `version`, `controller`, `policy`, `purpose`, `lawfulBasis`, `dataUse`, `dpia`, `active`, `forgettable`, and `lifecycle`.
   ACTUAL BEHAVIOR: `DataAgreementSerializer` returns a large CivicOS-only payload (`slug`, `language`, `purposeDescription`, `dataUsePurpose*`, `dataController*`, `policyId`, `dataRetentionPeriodDays`, `attributes`, bilingual fields, etc.). Its `controller.id` is also set to the data-agreement PK, not a controller identifier.
   FIX: Reduce the serializer to the spec fields and populate `controller` from a real controller identifier or omit it when unavailable.

7. MAJOR - Individual endpoints do not implement the spec identity fields
   LOCATION: [apps/consent/serializers.py:325](/Users/lionel/builders/govstack/apps/consent/serializers.py#L325), [apps/consent/govstack_views.py:333](/Users/lionel/builders/govstack/apps/consent/govstack_views.py#L333), [apps/consent/govstack_views.py:508](/Users/lionel/builders/govstack/apps/consent/govstack_views.py#L508)
   SPEC REQUIREMENT: Individual objects must expose `id`, `externalId`, `externalIdType`, and `identityProviderId`.
   ACTUAL BEHAVIOR: The serializer adds `name`, `iamRef`, `phone`, `email`, and `consentRecordsCount`, hardcodes `externalIdType` to `civicos_user`, and derives `identityProviderId` from a local sentinel. The create endpoints persist only an email/name proxy and never store the spec identity fields.
   FIX: Persist the spec identity values in the data model or a dedicated profile table, return only the spec fields, and stop hardcoding CivicOS-specific sentinel values.

8. MAJOR - Webhook CRUD is not spec-pure and create ignores `disabled`
   LOCATION: [apps/consent/serializers.py:428](/Users/lionel/builders/govstack/apps/consent/serializers.py#L428), [apps/consent/govstack_views.py:398](/Users/lionel/builders/govstack/apps/consent/govstack_views.py#L398), [apps/consent/govstack_views.py:431](/Users/lionel/builders/govstack/apps/consent/govstack_views.py#L431)
   SPEC REQUIREMENT: Webhook objects should honor the `disabled` boolean and return the schema fields the spec defines.
   ACTUAL BEHAVIOR: `WebhookSerializer` adds non-spec fields (`isActive`, `events`, `skippedHeaders`, `timeStamp`), and `ConfigWebhookListView.post()` infers enabled/disabled from `isActive` instead of `disabled`. A caller can submit `disabled: true` and still create an enabled webhook.
   FIX: Make `disabled` the source of truth on create/update and strip the response down to the spec shape unless an extension is explicitly allowed by the cert harness.

9. MAJOR - Paired consent-record create returns `signature: null`
   LOCATION: [apps/consent/govstack_views.py:712](/Users/lionel/builders/govstack/apps/consent/govstack_views.py#L712), [apps/consent/services.py:51](/Users/lionel/builders/govstack/apps/consent/services.py#L51)
   SPEC REQUIREMENT: `POST /service/individual/record/consent-record/` creates a paired ConsentRecord and Signature object and returns both.
   ACTUAL BEHAVIOR: `ConsentService.grant()` creates or updates the record, audits it, and schedules webhook dispatch, but it never creates a `ConsentSignature`. The view then serializes `getattr(record, "signature_obj", None)`, so the response can be `signature: null`. The ConsentRecord serializer also emits extra fields (`granted_at`, `withdrawn_at`, `source`) that are not in the schema.
   FIX: Create the Signature object in the paired-create flow, or change the endpoint contract if the spec cannot be matched exactly.

10. MAJOR - ConsentRecord updates do not invalidate the existing signature and legacy states still leak
   LOCATION: [apps/consent/govstack_views.py:767](/Users/lionel/builders/govstack/apps/consent/govstack_views.py#L767), [apps/consent/services.py:128](/Users/lionel/builders/govstack/apps/consent/services.py#L128), [apps/consent/models.py:446](/Users/lionel/builders/govstack/apps/consent/models.py#L446)
   SPEC REQUIREMENT: A signed ConsentRecord should have its signature invalidated when the record is updated, and the public state enum is only `unsigned`, `pending`, `signed`, and `revoked`.
   ACTUAL BEHAVIOR: `grant()` and `withdraw()` change the consent state but never invalidate or remove an attached signature. The model still carries the legacy `pending_signatures` state, which can leak through API responses or future data.
   FIX: Delete or invalidate the attached signature when the record changes, and remove legacy states from the public API surface.

11. MAJOR - Revision rows are not truly append-only and the spec-required `signedWithoutObjectId` is not stored
   LOCATION: [apps/consent/models.py:282](/Users/lionel/builders/govstack/apps/consent/models.py#L282), [apps/consent/models.py:352](/Users/lionel/builders/govstack/apps/consent/models.py#L352), [apps/consent/serializers.py:149](/Users/lionel/builders/govstack/apps/consent/serializers.py#L149)
   SPEC REQUIREMENT: Revision records are append-only, and the schema includes `signedWithoutObjectId`.
   ACTUAL BEHAVIOR: `ConsentRevision.create_for()` updates the previous row to set `successor`, so the revision table is not strictly append-only. The model has no stored `signedWithoutObjectId` field, and the serializer returns that property as `null`.
   FIX: Add a real persisted `signedWithoutObjectId` field if the spec requires it, and avoid mutating existing revision rows if append-only semantics are mandatory.

12. MAJOR - Right to be Forgotten can delete required agreements if `forgettable` is mis-set
   LOCATION: [apps/consent/services.py:394](/Users/lionel/builders/govstack/apps/consent/services.py#L394)
   SPEC REQUIREMENT: RTBF must delete forgettable records only and must not delete required or non-forgettable agreements.
   ACTUAL BEHAVIOR: The delete query filters only on `category__forgettable=True`. It does not exclude `category__is_required=True`, so a required agreement marked forgettable would be deleted.
   FIX: Add an explicit required-agreement guard and enforce the intended `is_required`/`forgettable` relationship at write time.

13. MAJOR - Webhook dispatch can fail silently
   LOCATION: [apps/consent/services.py:463](/Users/lionel/builders/govstack/apps/consent/services.py#L463)
   SPEC REQUIREMENT: Webhooks should be dispatched after commit with the expected event header and HMAC-SHA256 signature.
   ACTUAL BEHAVIOR: The dispatcher swallows every exception and never checks the HTTP status code returned by the receiver. A 4xx/5xx from the webhook target is treated as success, so a test harness can see a passing API call even though delivery failed.
   FIX: Inspect the response status, call `raise_for_status()` or equivalent, and surface or retry delivery failures instead of hiding them.

## Checks That Passed

- I did not find any spec endpoints that are completely missing from `apps/consent/govstack_urls.py`.
- I did not find any POST endpoint that explicitly returns HTTP 201; the code paths I checked return 200 by default.
