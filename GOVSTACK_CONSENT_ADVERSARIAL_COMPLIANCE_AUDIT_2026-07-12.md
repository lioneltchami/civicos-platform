# GovStack Consent BB v23Q4 adversarial compliance audit

Date: 2026-07-12

Repo: `/Users/lionel/builders/govstack`

Scope reviewed:

- `apps/api/urls.py`
- `apps/consent/govstack_urls.py`
- `apps/consent/govstack_views.py`
- `apps/consent/serializers.py`
- `apps/consent/models.py`
- `apps/consent/services.py`
- `apps/consent/tests/test_govstack_api.py`

Sources checked:

- v23Q4 OpenAPI YAML: https://raw.githubusercontent.com/GovStackWorkingGroup/bb-consent/v23Q4/api/consent-openapi.yaml
- Live docs index page for Service APIs: https://consent.govstack.global/8-service-apis.md
- Reference mock fixtures: https://raw.githubusercontent.com/GovStackWorkingGroup/bb-consent/main/examples/mock/djangoapp/fixtures.json

Methodology note:

- All required routes from the prompt are registered and mounted under `/api/v1/consent/` via `apps/api/urls.py:59-61` and `apps/consent/govstack_urls.py:26-134`.
- I did not find any GovStack POST endpoint returning `201`; the GovStack views return default `200` or explicit `HTTP_200_OK`.
- The upstream YAML and the audit brief disagree in a few places:
  - `GET /config/data-agreements/` uses top-level key `dataAgreement` in the v23Q4 YAML, not `dataAgreements`.
  - Audit endpoints in the v23Q4 YAML are tagged `auditor` but use `security: [{OAuth2: []}]`, while the brief requires a dedicated auditor role.
  - Revision examples in the YAML/mock fixtures show SHA-1-length hashes, while the brief requires SHA-256.
- Findings below flag both strict spec mismatches and the stricter security/compliance requirements stated in the brief.

## Findings

1. SEVERITY: CRITICAL

   LOCATION: `apps/consent/govstack_views.py:980-1042` (`AuditConsentRecordListView`, `AuditConsentRecordDetailView`, `AuditDataAgreementListView`, `AuditDataAgreementDetailView`), `apps/consent/govstack_views.py:61-77` (`IsAuditorUser`)

   SPEC REQUIREMENT: The audit brief requires `/audit/` to require a dedicated auditor role, separate from normal authenticated users and separate from admin. Unauthorized authenticated users should get `403`; unauthenticated users should get `401`.

   ACTUAL BEHAVIOR: The four spec audit endpoints use `permission_classes = [IsAuthenticated]`, so any logged-in user can list and read all consent records and all data agreements. The dedicated `IsAuditorUser` permission exists but is not applied to the spec audit routes. Even if it were applied, it grants auditorship to `is_staff`, so auditor and admin are still collapsed.

   FIX: Put the four `/audit/` routes behind a dedicated auditor permission class, and remove the `is_staff` shortcut from that class unless you explicitly want staff to be auditors. Keep `IsAuthenticated` first so unauthenticated requests stay `401`, then return `403` for authenticated non-auditors.

2. SEVERITY: CRITICAL

   LOCATION: `apps/consent/services.py:70-155` (`ConsentService.grant`), `apps/consent/govstack_views.py:745-767` (`ServiceIndividualConsentRecordListView.post`), `apps/consent/govstack_views.py:1093-1130` (`ServiceConsentRecordSignatureView.post/put`), `apps/consent/govstack_views.py:800-824` (`ServiceIndividualConsentRecordDetailView.put`), `apps/consent/models.py:446-460`

   SPEC REQUIREMENT: ConsentRecord state machine must be `unsigned | pending | signed | revoked`. A new record starts `unsigned`. Signature attachment moves it to `signed`. Updating a signed record invalidates its signature. Signature create/update endpoints must implement the two-step signing flow described by the spec.

   ACTUAL BEHAVIOR: `grant()` creates or mutates records directly to `state="signed"` and auto-creates a synthetic signature before the explicit `/signature/` flow ever runs. `POST /service/individual/record/consent-record/{id}/signature/` accepts a caller-supplied final signature instead of creating an unsigned signature object “ready for signing”. `PUT /.../signature/` does not update the ConsentRecord state to `signed`, even though the spec says the Consent BB must do that. `PUT /service/individual/record/consent-record/{id}/` mutates signed records without invalidating or removing the existing signature. The model still allows legacy `pending_signatures`.

   FIX: Rework the consent workflow to match the spec:
   - create ConsentRecords in `unsigned` state;
   - create an unsigned Signature object or draft payload first;
   - only set `state="signed"` after the signature update step succeeds;
   - invalidate/delete/replace signatures whenever a signed ConsentRecord is updated;
   - remove `pending_signatures` from public state choices and migrate legacy rows.

3. SEVERITY: CRITICAL

   LOCATION: `apps/consent/services.py:53-155` (`ConsentService.grant`), `apps/consent/services.py:158-215` (`ConsentService.withdraw`), `apps/consent/govstack_views.py:761-766`, `apps/consent/govstack_views.py:794-823`

   SPEC REQUIREMENT: ConsentRecord create/update flows generate and expose ConsentRecord revisions. The reference fixtures show separate ConsentRecord revisions for unsigned and signed states. `PUT /service/individual/record/consent-record/{consentRecordId}/` explicitly says it generates a new Revision object.

   ACTUAL BEHAVIOR: No ConsentRevision is ever created for a ConsentRecord. The `revision` returned by service consent-record views is `record.data_agreement_revision`, i.e. the DataAgreement revision, not a ConsentRecord revision. This means the service API is returning the wrong revision object and is missing the revision trail for consent-state changes entirely.

   FIX: Create ConsentRevision rows for ConsentRecord lifecycle transitions, persist the current ConsentRecord revision separately from the linked DataAgreement revision, and return the ConsentRecord revision from the service create/update flows. Align the signature payload to sign that ConsentRecord revision.

4. SEVERITY: CRITICAL

   LOCATION: `apps/consent/models.py:523-525`, `apps/consent/services.py:74-103`, `apps/consent/services.py:177-194`, `apps/consent/govstack_views.py:1153-1172`

   SPEC REQUIREMENT: The ConsentRecord model is revision-oriented; the schema description says uniqueness is on `(dataAgreementRevision, individual)`, and `/service/individual/record/data-agreement/{dataAgreementId}/all/` is supposed to expose historical records.

   ACTUAL BEHAVIOR: The data model enforces `unique_together = [("citizen", "category")]`, so there can only ever be one record per individual/data-agreement pair. `grant()` and `withdraw()` mutate that single row in place. As a result, the `/all/` endpoint can never return true history; it can only return the current row. Re-consenting after a new DataAgreement revision also mutates the old row instead of creating a new record for the new revision.

   FIX: Change the uniqueness model to be revision-aware, introduce append-only ConsentRecord history rows, and stop mutating the same consent row in place for every lifecycle transition.

5. SEVERITY: CRITICAL

   LOCATION: `apps/consent/govstack_views.py:883-906` (`ServiceIndividualConsentRecordDraftView.post`)

   SPEC REQUIREMENT: Service-namespace individual records must be scoped to the authenticated individual. The brief explicitly requires IDOR protection.

   ACTUAL BEHAVIOR: The draft endpoint accepts any `individualId` query parameter, loads that user, and returns a draft ConsentRecord for that user without checking that `individualId == request.user.pk` or that the caller has a privileged org role. Any authenticated user can generate drafts for other users.

   FIX: Enforce `individualId == request.user.pk` for normal individual tokens. If you need delegated draft generation, introduce a distinct org/admin permission path and document it separately from the individual-scoped service surface.

6. SEVERITY: MAJOR

   LOCATION: `apps/consent/govstack_views.py:115`, `apps/consent/govstack_views.py:146`, `apps/consent/govstack_views.py:246`, `apps/consent/govstack_views.py:287`, `apps/consent/govstack_views.py:339`, `apps/consent/govstack_views.py:365`, `apps/consent/govstack_views.py:404`, `apps/consent/govstack_views.py:440`

   SPEC REQUIREMENT: `/config/` must require org/admin capability. The brief explicitly asks for admin/org role handling.

   ACTUAL BEHAVIOR: Every `/config/` view is hard-wired to `IsAdminUser`, which in DRF means staff-only. There is no org-role permission implementation, despite the module docstring claiming “IsAdminUser or IsOrgUser”.

   FIX: Implement a real org-role permission and apply it where the YAML uses `org`. Keep admin/staff as an explicit bypass only if intended. At minimum, stop documenting `IsOrgUser` until it exists.

7. SEVERITY: MAJOR

   LOCATION: `apps/consent/models.py:304-310`, `apps/consent/services.py:577-627`, `apps/consent/serializers.py:152-179`

   SPEC REQUIREMENT: Revision objects must expose `serializedSnapshot` in the GovStack shape. The reference fixtures serialize it as a JSON string containing `schemaName`, `objectId`, `timestamp`, authorization fields, and nested `objectData`.

   ACTUAL BEHAVIOR: `serialized_snapshot` is a `JSONField`, not a serialized string. `_policy_snapshot()` and `_data_agreement_snapshot()` produce flattened internal object dicts, not the GovStack wrapper shape, and they use internal snake_case names like `industry_sector`, `lawful_basis`, and `policy_id`. `RevisionSerializer` then emits that JSON object directly.

   FIX: Serialize snapshots in the GovStack/reference-fixture shape, store the canonical serialized JSON string that is actually hashed, and ensure `objectData` uses GovStack field names rather than CivicOS internal names.

8. SEVERITY: MAJOR

   LOCATION: `apps/consent/services.py:321-326`, `apps/consent/services.py:343-348`, `apps/consent/services.py:367-372`, `apps/consent/services.py:392-397`, `apps/consent/serializers.py:182-185`

   SPEC REQUIREMENT: Admin/system-created revisions should record the actor in `authorizedByOther`; `authorizedByIndividual` is for the consenting individual.

   ACTUAL BEHAVIOR: Policy and DataAgreement revisions pass the acting admin user as `authorized_by_individual`, while `authorized_by_other` is left blank whenever an actor exists. The serializer then exposes that admin user ID as `authorizedByIndividual`.

   FIX: Store config/admin actors in `authorized_by_other` and reserve `authorized_by_individual` for individual consent actions.

9. SEVERITY: MAJOR

   LOCATION: `apps/consent/govstack_views.py:853-867` (`ServiceIndividualDataAgreementConsentRecordView.post`)

   SPEC REQUIREMENT: `POST /service/individual/record/data-agreement/{dataAgreementId}/` accepts required `individualId` and optional `revisionId` query parameters.

   ACTUAL BEHAVIOR: The view ignores both parameters. It always uses `request.user` and always grants against the service’s latest DataAgreement revision.

   FIX: Parse and validate `individualId` and `revisionId` per the spec. For individual tokens, enforce that `individualId` matches the caller. If `revisionId` is supplied, bind the new ConsentRecord to that exact revision or reject mismatches.

10. SEVERITY: MAJOR

   LOCATION: `apps/consent/govstack_views.py:747-754` (`ServiceIndividualConsentRecordListView.post`)

   SPEC REQUIREMENT: ConsentRecord field names are `dataAgreement`, `dataAgreementRevision`, `individual`, and `signature` — not `dataAgreementId` / `individualId` / `signatureId`.

   ACTUAL BEHAVIOR: The create endpoint only accepts `dataAgreementId` or `data_agreement_id`. A spec-canonical body using `consentRecord.dataAgreement` is ignored and rejected as missing.

   FIX: Accept the GovStack field name `dataAgreement` on input, then normalize legacy aliases only as backwards compatibility.

11. SEVERITY: MAJOR

   LOCATION: `apps/consent/serializers.py:97-146` (`PolicySerializer`)

   SPEC REQUIREMENT: Policy schema fields are `id`, `name`, `version`, `url`, `jurisdiction`, `industrySector`, `dataRetentionPeriodDays`, `geographicRestriction`, `storageLocation`.

   ACTUAL BEHAVIOR: The serializer emits extra non-spec fields: `description`, `thirdPartyDataSharing`, `createdAt`, and `updatedAt`.

   FIX: Make the GovStack serializer spec-strict. If CivicOS needs richer policy output, keep a separate internal serializer instead of overloading the GovStack transport.

12. SEVERITY: MAJOR

   LOCATION: `apps/consent/serializers.py:208-323` (`DataAgreementSerializer`)

   SPEC REQUIREMENT: DataAgreement must expose the GovStack fields and naming. The reference fixture includes `controller`, `policy`, `purpose`, `lawfulBasis`, `dataUse`, `dpia`, `active`, `forgettable`, `compatibleWithVersion`, `lifecycle`, and `signature`.

   ACTUAL BEHAVIOR: The serializer adds many CivicOS-only fields (`slug`, `language`, `policyId`, `dataRetentionPeriodDays`, `dataControllerName`, `dataControllerLogoImageUrl`, `attributes`, `name_en`, `name_fr`, `purpose_fr`, etc.), omits `compatibleWithVersion` and `signature`, and fabricates `controller.id` from the DataAgreement PK instead of a controller entity.

   FIX: Split internal/category serialization from GovStack serialization. Emit only GovStack fields on the GovStack surface, add the missing fields, and model controller identity explicitly instead of reusing the DataAgreement PK.

13. SEVERITY: MAJOR

   LOCATION: `apps/consent/serializers.py:325-364` (`IndividualSerializer`), `apps/consent/govstack_views.py:348-359`, `apps/consent/govstack_views.py:529-543`

   SPEC REQUIREMENT: Individual schema is `id`, `externalId`, `externalIdType`, `identityProviderId`. The create/update flows should operate on that schema, not on CivicOS account-profile extensions.

   ACTUAL BEHAVIOR: Responses include CivicOS-only fields (`name`, `iamRef`, `phone`, `email`, `consentRecordsCount`). `externalId` is just the Django user PK, `externalIdType` is hardcoded to `civicos_user`, and `identityProviderId` is hardcoded to `civicos` unless an attribute happens to exist. The create endpoints are email-driven proxies, not schema-driven Individual creation.

   FIX: Make the GovStack Individual object a first-class mapping layer with explicit external identity fields, and keep CivicOS account/profile fields out of the GovStack serializer.

14. SEVERITY: MAJOR

   LOCATION: `apps/consent/serializers.py:366-425` (`ConsentRecordGovStackSerializer`), `apps/consent/services.py:71-85`

   SPEC REQUIREMENT: ConsentRecord fields are `id`, `dataAgreement`, `dataAgreementRevision`, `dataAgreementRevisionHash`, `individual`, `optIn`, `state`, `signature`. `dataAgreementRevisionHash` is required.

   ACTUAL BEHAVIOR: The serializer adds non-spec fields `granted_at`, `withdrawn_at`, and `source`. The service allows `revision = None`, which means a ConsentRecord can be created with blank `dataAgreementRevisionHash` even though that field is required by the schema.

   FIX: Remove the CivicOS-only fields from the GovStack serializer and hard-fail consent creation if the target DataAgreement has no current revision.

15. SEVERITY: MAJOR

   LOCATION: `apps/consent/serializers.py:428-468` (`WebhookSerializer`), `apps/consent/govstack_urls.py:58-62`, `apps/consent/govstack_views.py:470-489`

   SPEC REQUIREMENT: Webhook schema fields are `id`, `payloadUrl`, `contentType`, `disabled`, `secretKey`. The brief accepts denormalized event subscriptions but still requires exact GovStack transport compliance.

   ACTUAL BEHAVIOR: The serializer emits extra fields `isActive`, `events`, `signatureHeader`, `skippedHeaders`, and `timeStamp`. The router also exposes non-spec endpoint `GET /config/webhook/{webhookId}/payload/`.

   FIX: Keep GovStack webhook transport strict, and move replay/debug payload inspection to a separate CivicOS-only API namespace if needed.

16. SEVERITY: MAJOR

   LOCATION: `apps/consent/services.py:498-544`

   SPEC REQUIREMENT: Webhook delivery must fire on `consent.granted` and `consent.withdrawn`, use `transaction.on_commit()`, and be robust enough not to disappear silently during certification runs.

   ACTUAL BEHAVIOR: `grant()` and `withdraw()` do use `transaction.on_commit()`, which is correct. But `dispatch_webhook()` swallows every transport exception and every non-2xx response into a warning log only. There is no retry, no dead-lettering, no persisted delivery status, and no surfaced failure. In a test harness, webhook failure can become silent drift rather than a visible test failure.

   FIX: Persist delivery attempts/results, surface failures in an auditable model, and consider retry/backoff or a task queue. If failures must stay non-fatal to the main request, they still should not be invisible.

17. SEVERITY: MAJOR

   LOCATION: `apps/consent/serializers.py:471-544` (`SignatureSerializer`), `apps/consent/models.py:814-819`, `apps/consent/govstack_views.py:944-953`

   SPEC REQUIREMENT: Signature schema uses GovStack names, and the reference fixtures use values like `verificationMethod: "pgp"`. The draft endpoint returns a Signature object shape paired with the draft ConsentRecord.

   ACTUAL BEHAVIOR: The serializer exposes extra non-spec fields (`dataAgreementRevisionHash`, `dataAgreementRevisionSignedWithoutId`, `verificationJwks`). Its allowed `verificationMethod` values omit `pgp`, which the reference fixture uses. The draft stub only includes the eight required fields, not the fuller Signature shape used by the reference mock.

   FIX: Align the allowed signature method values with the reference implementation and make the GovStack serializer output match the GovStack/reference Signature object rather than CivicOS-specific signing metadata.

18. SEVERITY: MAJOR

   LOCATION: `apps/consent/govstack_views.py:214-231`

   SPEC REQUIREMENT: `GET /config/policy/{policyId}/revisions/` returns a response schema with `policy` only.

   ACTUAL BEHAVIOR: The view returns `{"policy": ..., "revisions": [...], "total": ...}`.

   FIX: Match the response envelope exactly. If you need a revision array for CivicOS use, expose it on a non-GovStack endpoint or change the GovStack response only if you have authoritative evidence that the certification harness expects the richer envelope instead of the published v23Q4 YAML.

19. SEVERITY: MAJOR

   LOCATION: `apps/consent/govstack_views.py:117-123`, `apps/consent/govstack_views.py:248-260`, `apps/consent/govstack_views.py:227-230`, `apps/consent/govstack_views.py:988-996`, `apps/consent/govstack_views.py:1020-1028`, `apps/consent/govstack_views.py:1055-1065`

   SPEC REQUIREMENT: List responses use the schema-defined array key only. The brief also asks for a results array rather than a DRF pagination wrapper.

   ACTUAL BEHAVIOR: Several list endpoints append a non-spec `total` property. The array itself is not wrapped in DRF pagination metadata, which is good, but the extra `total` key is still envelope drift.

   FIX: Remove `total` from GovStack responses or confirm the certification harness tolerates additional top-level properties before shipping.

20. SEVERITY: MINOR

   LOCATION: `apps/consent/govstack_urls.py:50-51`, `apps/consent/govstack_views.py:377-393`, `apps/consent/govstack_urls.py:59-60`, `apps/consent/govstack_urls.py:133`, `apps/consent/govstack_views.py:1055-1065`

   SPEC REQUIREMENT: The GovStack surface should not advertise unsupported extra operations as if they were part of the BB contract.

   ACTUAL BEHAVIOR: The implementation exposes extra non-spec operations:
   - `PUT` and `DELETE /config/individual/{individualId}/`
   - `GET /config/webhook/{webhookId}/payload/`
   - `GET /audit/consent-log/`

   FIX: Move CivicOS-only operations to a separate non-GovStack namespace or clearly segregate them from the cert-facing surface.

21. SEVERITY: MINOR

   LOCATION: `apps/consent/govstack_views.py:117-123`, `apps/consent/govstack_views.py:406-411`

   SPEC REQUIREMENT: Optional query parameters declared by the spec should either be implemented or rejected deliberately.

   ACTUAL BEHAVIOR: `GET /config/policies/` and `GET /config/webhooks/` ignore the spec-declared `revisionId` query parameter entirely.

   FIX: Either implement the intended revision filter semantics or reject unsupported `revisionId` values with a `400` instead of silently ignoring them.

22. SEVERITY: MINOR

   LOCATION: many list views, e.g. `apps/consent/govstack_views.py:120-122`, `224-226`, `251-253`, `343-345`, `409-410`, `525-526`, `647-648`, `680-681`, `739-740`, `991-992`, `1023-1024`, `1057-1058`, `1165-1166`

   SPEC REQUIREMENT: Pagination parameters should be supported predictably.

   ACTUAL BEHAVIOR: The views cast `offset` and `limit` with raw `int(...)`. Non-numeric input will raise `ValueError` and likely bubble as a `500`, not a clean `400`.

   FIX: Validate pagination params centrally and return structured `400` responses for invalid values.

## Bottom line

This implementation is not certification-ready for the GovStack Consent BB as reviewed.

The highest-risk blockers are:

- audit-route overexposure;
- broken consent/signature state machine;
- missing ConsentRecord revisioning;
- non-historical ConsentRecord data model;
- draft endpoint IDOR;
- revision snapshot shape drift from the GovStack/reference model.

If I had to sequence remediation, I would fix the data model and lifecycle first, then the audit/config auth boundaries, then serializer/envelope exactness, then the remaining query-param and extra-route cleanup.
