# GovStack Consent BB v23Q4 Compliance Audit

1. **CRITICAL** - Unsupported `DELETE /service/individual/record/consent-record/{consentRecordId}/` lets a user hard-delete consent records outside the spec RTBF flow.  
   **Location:** `apps/consent/govstack_views.py:735-765` (`ServiceIndividualConsentRecordDetailView.delete`)  
   **Spec requirement:** The only delete path in the service namespace is `DELETE /service/individual/record/`, and it must delete only `ConsentRecord` rows whose `DataAgreement.forgettable=True`, with an audit entry.  
   **Actual behavior:** This endpoint calls `record.delete()` directly for the authenticated user’s record. There is no forgettable check, no required/non-forgettable protection, and no audit entry. A non-forgettable or required consent record can be removed through an unsupported route.  
   **Fix:** Remove the method entirely or make it enforce the same forgettable-only delete logic and audit trail as `ServiceIndividualRightToBeForgottenView`. Ideally, do not expose any detail-level DELETE at all.

2. **MAJOR** - Auditor and consumer access is not actually separated from admin access.  
   **Location:** `apps/consent/govstack_views.py:61-77` (`IsAuditorUser`), `apps/consent/govstack_views.py:80-100` (`IsConsumerUser`), `apps/consent/govstack_views.py:917-986` (audit views), `apps/consent/govstack_views.py:602-660` (verification views)  
   **Spec requirement:** `/audit/` must require a distinct auditor role separate from admin, and `/service/verification/` must require the consumer role.  
   **Actual behavior:** Both permissions treat `request.user.is_staff` as automatically authorized. That makes admin users implicitly auditors/consumers, which violates the requested separation.  
   **Fix:** Remove the `is_staff` shortcut and enforce dedicated roles/claims for auditors and consumers.

3. **MAJOR** - The draft endpoint ignores the required `individualId` query parameter and returns the wrong object shape.  
   **Location:** `apps/consent/govstack_views.py:837-891` (`ServiceIndividualConsentRecordDraftView.post`)  
   **Spec requirement:** `POST /service/individual/record/consent-record/draft/` must require both `individualId` and `dataAgreementId` query parameters and return a draft `consentRecord` shaped like the spec, with flat FK ids.  
   **Actual behavior:** The method validates `individualId` but never uses it. The response serializes `request.user` instead of the requested individual, and it nests full `DataAgreementSerializer` and `IndividualSerializer` objects inside `consentRecord` instead of the flat FK ids required by the schema.  
   **Fix:** Load and validate the requested individual, and return a draft payload that matches the ConsentRecord schema exactly. Do not nest full serializer objects here.

4. **MAJOR** - `/config/policy/{policyId}/revisions/` returns the wrong envelope.  
   **Location:** `apps/consent/govstack_views.py:197-219` (`ConfigPolicyRevisionsView.get`)  
   **Spec requirement:** The response schema for this operation shows only a `policy` property.  
   **Actual behavior:** The endpoint returns `policy`, `revisions`, and `total`. That is a schema mismatch, not a harmless extension, because the spec explicitly narrows this response.  
   **Fix:** Return only the spec-shaped `policy` envelope. If revision history is needed internally, expose it through a separate documented surface.

5. **MAJOR** - Webhook responses do not match the required webhook schema.  
   **Location:** `apps/consent/serializers.py:421-455` (`WebhookSerializer`), `apps/consent/govstack_views.py:386-446` (`ConfigWebhookListView`, `ConfigWebhookDetailView`)  
   **Spec requirement:** Webhook schema must expose `id, payloadUrl, contentType, disabled, secretKey`.  
   **Actual behavior:** `secretKey` is `write_only`, so it never appears in responses. The serializer also emits `isActive`, `events`, `signatureHeader`, `skippedHeaders`, and `timeStamp`, none of which belong to the spec contract you provided.  
   **Fix:** Use separate read/write serializers or trim the public serializer to the exact webhook schema. If the test harness expects `secretKey` in GET/POST/PUT responses, it must be present.

6. **MAJOR** - Policy responses leak non-spec fields.  
   **Location:** `apps/consent/serializers.py:94-132` (`PolicySerializer`)  
   **Spec requirement:** Policy object fields are `id, name, version, url, jurisdiction, industrySector, dataRetentionPeriodDays, geographicRestriction, storageLocation`.  
   **Actual behavior:** The serializer also returns `description`, `thirdPartyDataSharing`, `createdAt`, and `updatedAt`. Those extra keys make the response non-compliant if the harness checks the schema strictly.  
   **Fix:** Restrict the GovStack serializer output to the exact spec fields only.

7. **MAJOR** - DataAgreement serialization is not spec-accurate, especially the embedded controller object.  
   **Location:** `apps/consent/serializers.py:208-322` (`DataAgreementSerializer`)  
   **Spec requirement:** DataAgreement must include a nested `controller {id, name, url}` and a nested `policy`, plus the listed scalar fields.  
   **Actual behavior:** The `controller.id` value is `obj.pk` from the DataAgreement itself, not a controller identifier. The serializer also emits many CivicOS-only keys such as `slug`, `language`, `purposeDescription`, `policyId`, `dataControllerName`, `dataControllerUrl`, `dataControllerLogoImageUrl`, and others.  
   **Fix:** Define a real controller representation and strip the serializer down to the spec contract. The controller object cannot reuse the DataAgreement primary key as its id.

8. **MAJOR** - Individual responses expose CivicOS-only fields instead of the spec-only schema.  
   **Location:** `apps/consent/serializers.py:325-364` (`IndividualSerializer`)  
   **Spec requirement:** Individual schema is `id, externalId, externalIdType, identityProviderId`.  
   **Actual behavior:** The serializer also returns `name, iamRef, phone, email, consentRecordsCount`, and it sets `externalId` to the user pk. That is a broader CivicOS shape, not the GovStack shape you asked to certify.  
   **Fix:** Remove the extra keys from the GovStack serializer and populate `externalId` from the actual external identifier instead of duplicating the primary key.

9. **MAJOR** - ConsentRecord responses allow and emit non-spec state and metadata.  
   **Location:** `apps/consent/serializers.py:366-418` (`ConsentRecordGovStackSerializer`), `apps/consent/models.py:446-460` (`ConsentRecord.STATE_CHOICES`)  
   **Spec requirement:** `ConsentRecord.state` must be one of `unsigned`, `pending`, `signed`, or `revoked`, and the response shape should stay on the flat spec fields.  
   **Actual behavior:** The model still permits the legacy `pending_signatures` state, the serializer does not constrain the enum, and responses include extra fields like `granted_at`, `withdrawn_at`, and `source`.  
   **Fix:** Enforce the four-value enum at the API boundary and remove the non-spec metadata from GovStack responses.

10. **MAJOR** - All list endpoints use the wrong pagination envelope.  
    **Location:** `apps/consent/govstack_views.py:117-123, 236-245, 326-331, 391-396, 504-506, 611-618, 631-650, 699-708, 922-930, 954-962, 988-997, 1071-1110`  
    **Spec requirement:** List responses must support `offset` and `limit` and include a `results` array, not a DRF-style paginated wrapper.  
    **Actual behavior:** Every list endpoint returns a plural-key envelope such as `policies`, `dataAgreements`, `individuals`, `webhooks`, `consentRecords`, or `consentLog`, sometimes with `total`, but never a `results` array.  
    **Fix:** Normalize all list endpoints to the required pagination shape. If the spec expects a `results` array, put the list there and keep any other metadata out of the public envelope.

11. **MINOR** - Webhook delivery failures can be silent.  
    **Location:** `apps/consent/services.py:463-499` (`ConsentService.dispatch_webhook`)  
    **Spec requirement:** Webhooks must fire after commit and reach subscribers reliably.  
    **Actual behavior:** The code suppresses all exceptions and does not check HTTP status codes, so a 4xx/5xx webhook response is treated as success. In a harness, that looks like “the app sent the event” when the receiver actually rejected it.  
    **Fix:** Inspect the response status, log or surface non-2xx results, and keep the on-commit dispatch behavior.
