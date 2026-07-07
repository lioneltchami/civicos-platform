# GovStack Consent BB v23Q4 compliance audit

Scope: `apps/consent/govstack_urls.py`, `apps/consent/govstack_views.py`, `apps/consent/serializers.py`, `apps/consent/models.py`, `apps/consent/services.py`.

1. CRITICAL: service verification endpoints expose arbitrary consent records to any authenticated user
   - LOCATION: `apps/consent/govstack_views.py:597-650` (`ServiceVerificationConsentRecordsView.get`, `ServiceVerificationConsentRecordDetailView.get`)
   - SPEC REQUIREMENT: `/service/` must require an authenticated individual, and consent records must be scoped to the authenticated user with IDOR protection.
   - ACTUAL BEHAVIOR: `GET /service/verification/consent-records/` returns all granted consent records, and `GET /service/verification/consent-record/{consentRecordId}/` returns any record by UUID to any authenticated user. There is no ownership check, no individual scoping, and no separate consumer/auditor gate.
   - FIX: require an individual-scoped permission or an explicit verifier role, and filter every consent-record lookup by the authenticated principal unless the endpoint is deliberately auditor-only.

2. MAJOR: the namespace role split is wrong for both config and audit
   - LOCATION: `apps/consent/govstack_views.py:86-123, 205-249, 298-366, 363-397, 432-433, 885-936, 953-954`
   - SPEC REQUIREMENT: `/config/` must require admin/org role, and `/audit/` must require a separate auditor role, not admin by default.
   - ACTUAL BEHAVIOR: every config view uses `IsAdminUser` only, so an org-role user cannot access the namespace at all. `IsAuditorUser.has_permission()` also grants access to any `is_staff` user, which collapses the audit role into admin/staff access instead of keeping it separate.
   - FIX: add an explicit org-role permission for config, remove the staff fallback from the auditor permission, and keep audit authorization separate from admin authorization.

3. MAJOR: ConsentRecord responses are incomplete, and the create path returns `signature: null`
   - LOCATION: `apps/consent/govstack_views.py:684-706, 726-765, 785-808, 906-915`
   - SPEC REQUIREMENT: ConsentRecord responses must use the `consentRecord`, `revision`, and `signature` envelope keys.
   - ACTUAL BEHAVIOR: the POST create flow calls `ConsentService.grant()` and then reads `record.signature_obj`, but `grant()` never creates a `ConsentSignature`, so create responses return `signature: null`. The single-record GET/PUT endpoints and the service/audit read endpoints omit `signature` entirely.
   - FIX: create and persist the paired `ConsentSignature` on the consent grant path, and return the full signature object from every ConsentRecord single-record response that the spec defines.

4. MAJOR: the draft endpoint ignores the requested individual and returns an incomplete signature stub
   - LOCATION: `apps/consent/govstack_views.py:811-859`
   - SPEC REQUIREMENT: `POST /service/individual/record/consent-record/draft/` must require both `individualId` and `dataAgreementId` query params, and the draft must reflect that individual.
   - ACTUAL BEHAVIOR: `individualId` is validated but never used. The draft is built from `request.user` instead of the queried individual. The returned `signature` object is only `{id, payload, verificationMethod, timestamp}` and is missing required fields such as `signature`, `verificationPayload`, `verificationPayloadHash`, and `verificationSignedBy`.
   - FIX: load the individual identified by the query param, build the draft from that user, and return a full draft signature object that matches the spec schema.

5. MAJOR: `/config/policy/{policyId}/revisions/` returns the wrong envelope shape
   - LOCATION: `apps/consent/govstack_views.py:174-196`
   - SPEC REQUIREMENT: the revision endpoint response shows `policy` only.
   - ACTUAL BEHAVIOR: the view returns `policy`, `revisions`, and `total`. That is not the spec shape, and it exposes a list payload where the spec shows a single object response.
   - FIX: return only the policy envelope the spec describes, and do not add the `revisions` array unless the OpenAPI response schema explicitly allows it.

6. MAJOR: webhook responses are not spec-strict and hide the required `secretKey`
   - LOCATION: `apps/consent/serializers.py:408-438` and `apps/consent/govstack_views.py:363-419`
   - SPEC REQUIREMENT: webhook schema requires `id`, `payloadUrl`, `contentType`, `disabled`, and `secretKey`.
   - ACTUAL BEHAVIOR: `secretKey` is `write_only=True`, so GET/POST/PUT responses never return it. The serializer also emits `isActive`, `events`, `signatureHeader`, `skippedHeaders`, and `timeStamp`, which are not in the required schema.
   - FIX: make the webhook response serializer spec-strict. If the test harness expects the secret to be returned, expose it in the response contract; otherwise split create-input and read-output serializers instead of reusing one drifted schema.

7. MAJOR: the Revision schema is missing `signedWithoutObjectId`
   - LOCATION: `apps/consent/models.py:282-340` and `apps/consent/serializers.py:149-175`
   - SPEC REQUIREMENT: Revision must include `signedWithoutObjectId` in addition to `id`, `schemaName`, `objectId`, `serializedSnapshot`, `serializedHash`, and `timestamp`.
   - ACTUAL BEHAVIOR: the model has no `signed_without_object_id` field at all, and the serializer cannot emit it. The response shape is therefore incomplete relative to the spec.
   - FIX: add the field to `ConsentRevision`, migrate it, populate it in `create_for()`, and expose it in `RevisionSerializer`.

8. MAJOR: the GovStack serializers are not spec-strict, and list endpoints add unsupported `total` metadata
   - LOCATION: `apps/consent/serializers.py:94-132, 195-309, 312-350, 353-405, 444-516` and `apps/consent/govstack_views.py:96-100, 219-222, 192-196, 895-898, 927-930`
   - SPEC REQUIREMENT: object schemas must use the exact field sets defined in the spec, and list responses use the plural envelope key only.
   - ACTUAL BEHAVIOR: the serializers emit a long tail of CivicOS-only fields:
     - `PolicySerializer`: `description`, `thirdPartyDataSharing`, `createdAt`, `updatedAt`
     - `DataAgreementSerializer`: `slug`, `language`, `dataController*`, `policyId`, `attributes`, `name_en`, `name_fr`, `purpose_fr`, and more
     - `IndividualSerializer`: `name`, `iamRef`, `phone`, `email`, `consentRecordsCount`
     - `ConsentRecordGovStackSerializer`: `granted_at`, `withdrawn_at`, `source`
     - `SignatureSerializer`: `dataAgreementRevisionHash`, `dataAgreementRevisionSignedWithoutId`, `verificationJwks`
     The list views also add `total`, which is outside the envelope shape the spec shows.
   - FIX: trim each serializer down to the exact field set the spec requires, and remove `total` from list responses unless the OpenAPI response schema explicitly includes it.

9. MAJOR: `ConsentRecord.state` still exposes a legacy enum that is outside the spec
   - LOCATION: `apps/consent/models.py:446-460` and `apps/consent/serializers.py:363-365`
   - SPEC REQUIREMENT: valid states are exactly `unsigned`, `pending`, `signed`, and `revoked`.
   - ACTUAL BEHAVIOR: the model still carries `pending_signatures` as a public choice, and the serializer accepts any string. That keeps an out-of-spec state alive in the public API surface.
   - FIX: remove the legacy choice from the public path and enforce the four spec states in validation and serialization.

10. MAJOR: webhook delivery failures can disappear silently
   - LOCATION: `apps/consent/services.py:463-504`
   - SPEC REQUIREMENT: webhook dispatch must be reliable enough for certification and must not fail silently.
   - ACTUAL BEHAVIOR: `dispatch_webhook()` logs exceptions and never re-raises, and it never checks the HTTP response status. A 4xx/5xx from the receiver is treated the same as success by this code path.
   - FIX: inspect the response, raise or persist non-2xx failures, and surface delivery problems in a way the test harness can detect.

