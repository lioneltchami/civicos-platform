# GovStack Consent BB - Deep Dive Audit Report
**Date:** 2026-07-13  
**Auditor:** Codex  
**Target:** `/Users/lionel/builders/govstack`  
**Spec version checked against:** GovStack Consent BB GitBook 23Q4.1 and `bb-consent` latest release `v23Q4` / OpenAPI `info.version: 1.1.0-rc1`.

External reference notes:
- The prompt URL `https://raw.githubusercontent.com/GovStackWorkingGroup/bb-consent/main/api/openapi.yaml` returned 404. The live file is `https://raw.githubusercontent.com/GovStackWorkingGroup/bb-consent/main/api/consent-openapi.yaml`.
- `main` and `v23Q4` OpenAPI files currently expose the same 32 paths.
- GitHub releases show `v23Q4` as the latest release, not a visible `v1.3.0` release.
- GitBook redirects to `https://consent.govstack.global/` and displays `23Q4.1`.
- Upstream repo exposes a public `test/` directory containing `README.md`, `plan.md`, and `gherkin/`.

**Test suite result:** 246 tests, 228 pass, 0 fail, 17 error, 1 skipped.  
**Line coverage:** 82% from a failed coverage run.  
**Branch coverage:** Not measured; coverage was not configured with branch coverage.  

Runtime caveats:
- The exact requested command initially failed because `python` was not on PATH.
- `python3` failed because Django was not installed.
- I created `.venv` with Python 3.12 and installed `requirements/test.txt`; that still could not boot `manage.py test` because default development settings include `django_extensions`, which is omitted from `requirements/test.txt`.
- After installing `requirements/development.txt`, the exact command blocked on an existing `test_civicos` database prompt. I reran with `--keepdb` to complete the suite.

---

## CRITICAL FINDINGS

### C-01 - E6/I: The documented test command does not run cleanly and the suite is red
**Location:** `manage.py:10`, `config/settings/base.py:701-712`, `apps/api/urls.py:25-28`, `apps/api/throttling.py:37-50`, `apps/consent/tests/test_api.py:54`  
**Problem:** `manage.py` defaults to `config.settings.development`, so `manage.py test apps.consent` does not use `config.settings.test`. Development settings leave the URL-level `TokenObtainThrottle` active, and 17 API tests fail with `429 too_many_requests` while fetching JWTs. This makes the consent test suite nondeterministic and currently failing.  
**Evidence:** `/tmp/consent_test_output.txt` shows 246 tests run with 17 errors, all token-fetch throttling errors.  
**Fix:** Make Django test execution use `config.settings.test` by default when `test` is in `sys.argv`, or document/enforce `DJANGO_SETTINGS_MODULE=config.settings.test` in the test command. Also ensure `requirements/test.txt` includes every dependency needed for `manage.py test`.

### C-02 - A3/A4/B7: POST consent-record accepts a missing spec-required `signature` and fabricates one
**Location:** `apps/consent/govstack_views.py:778-808`, `apps/consent/services.py:188-208`  
**Spec requirement:** `POST /service/individual/record/consent-record/` request body requires both `consentRecord` and `signature`.  
**Problem:** The view only reads `consentRecord`; it ignores `request.data["signature"]` entirely. The service then creates a synthetic `verificationMethod="string"` signature from server-side data. This can pass a local "non-null signature" assertion while failing the actual contract that the caller supplies the signed payload.  
**Fix:** Validate the full request envelope with both `consentRecord` and `signature`; persist the supplied signature atomically with the grant; reject missing or malformed signature fields with the GovStack error envelope.

### C-03 - A10/B1/D2: Signature creation can mark a record signed without grant status, audit entry, or revision
**Location:** `apps/consent/govstack_views.py:1202-1207`  
**Problem:** `POST /service/individual/record/consent-record/{id}/signature/` saves a signature and, if the record is not signed, sets only `record.state = signed`. It does not set `status=granted`, does not create `ConsentRevision`, and does not create `ConsentAuditEntry`. That violates the "every state transition is auditable" rule and can create impossible records: `state=signed` with `status=pending`.  
**Fix:** Route signature creation through a service method that updates state/status together and writes a revision and audit entry in one `transaction.atomic()` block.

### C-04 - A1/H: OpenAPI parameter names are camelCase, but Django URL names are snake_case
**Location:** `apps/consent/govstack_urls.py:32-130`  
**Spec requirement:** OpenAPI paths use `{policyId}`, `{dataAgreementId}`, `{individualId}`, `{webhookId}`, and `{consentRecordId}`.  
**Problem:** Django routes use `<uuid:policy_id>`, `<int:data_agreement_id>`, `<uuid:individual_id>`, `<uuid:webhook_id>`, and `<uuid:consent_record_id>`. Runtime matching works, but schema generation/introspection will not match the published parameter names.  
**Fix:** Rename URL converter variables and view args to camelCase-compatible names, or explicitly override generated schema parameters for every affected endpoint.

## HIGH FINDINGS

### H-01 - A1/H3: Implementation exposes endpoints that are not in the fetched OpenAPI spec
**Location:** `apps/consent/govstack_urls.py:59-60`, `apps/consent/govstack_urls.py:132-133`, `apps/consent/govstack_views.py:410-426`  
**Problem:** The current upstream OpenAPI does not include `GET /config/webhook/{webhookId}/payload/`, `GET /audit/consent-log/`, or `PUT/DELETE /config/individual/{individualId}/`. The prompt asked to check payload, PUT, and DELETE, but the fetched authoritative OpenAPI does not list them.  
**Fix:** Keep these as CivicOS extensions only if they are hidden from GovStack schema/certification routing, or update the authoritative OpenAPI target if the project is intentionally implementing a newer unpublished contract.

### H-02 - A4: Several serializers still emit fields not present in the fetched schemas
**Location:** `apps/consent/serializers.py:124-136`, `apps/consent/serializers.py:277-308`, `apps/consent/serializers.py:333-343`, `apps/consent/serializers.py:453-463`  
**Problem:** The fetched `Policy` schema has no `description` or `thirdPartyDataSharing`; `DataAgreement` has no `slug`, `language`, `purposeDescription`, DPIA URL fields, `policyId`, or `attributes`; `Individual` has no `name`, `iamRef`, `phone`, `email`, or `consentRecordsCount`; `Webhook` has no `events`, `signatureHeader`, `skippedHeaders`, or `timeStamp`. These are useful CivicOS fields but not spec-strict GovStack fields.  
**Fix:** Split GovStack strict serializers from CivicOS extension serializers. Do not expose extension fields on certification endpoints unless the spec allows additional properties.

### H-03 - A4/A10: Revision serializer field types do not match the fetched schema
**Location:** `apps/consent/serializers.py:156-183`, `apps/consent/serializers.py:196-202`  
**Problem:** The fetched `Revision.serializedSnapshot` schema type is `string`, while this implementation returns JSON. `authorizedByIndividual` is schema-referenced as an `Individual`, but this implementation returns a UUID string. Optional booleans/strings `signedWithoutObjectId` and `predecessorSignature` are always `null`.  
**Fix:** Decide whether to conform to the fetched OpenAPI literally or update the target OpenAPI. If conforming, serialize `serializedSnapshot` as a canonical string and return the required referenced object shapes.

### H-04 - A5: Create/delete status codes are not explicitly aligned with the prompt's expected REST semantics
**Location:** `apps/consent/govstack_views.py:166-169`, `apps/consent/govstack_views.py:307-310`, `apps/consent/govstack_views.py:467`, `apps/consent/govstack_views.py:498-500`  
**Problem:** The fetched OpenAPI uses `200` for create operations, but the audit prompt expects `201` for POST create and `204` for DELETE unless the spec says otherwise. The implementation returns DRF default `200` for most creates and `200` for deletes. This is compatible with the fetched spec but conflicts with the prompt's certification checklist.  
**Fix:** Pin the exact certification spec. If certification expects conventional REST statuses, update create/delete responses and tests; otherwise document that v23Q4 OpenAPI requires `200`.

### H-05 - C5: `/config/`, `/service/`, and `/audit/` endpoints do not apply per-scope throttle classes
**Location:** `apps/consent/govstack_views.py:142-1225`, `config/settings/base.py:701-712`  
**Problem:** The global DRF throttle includes `CitizenRateThrottle` and `AnonRateThrottle`, but the GovStack views do not set `throttle_scope` or `throttle_classes` by namespace. There is no distinct admin/auditor/consumer throttle on config/audit/verification endpoints.  
**Fix:** Add explicit throttle classes/scopes per namespace: staff/org for `/config/`, citizen for individual `/service/`, consumer for `/service/verification/`, auditor for `/audit/`.

### H-06 - C6/D1: Logs include internal identifiers for export requests/documents
**Location:** `apps/consent/tasks.py:41-42`, `apps/consent/tasks.py:51-55`, `apps/consent/tasks.py:188`, `apps/consent/tasks.py:229-231`, `apps/consent/tasks.py:272-274`, `apps/consent/views.py:206-208`, `apps/consent/views.py:238-260`  
**Problem:** Export request UUIDs and document primary keys are repeatedly logged. They are not raw storage keys, but they are still identifiers tied to PIPEDA data export workflows. This increases breach blast radius in centralized logs.  
**Fix:** Replace exact IDs with event correlation IDs or short hashes, and keep the full identifiers only in database audit records with access control.

## MEDIUM FINDINGS

### M-01 - B1: The model still permits legacy `pending_signatures` state
**Location:** `apps/consent/models.py:447-461`  
**Problem:** The spec state machine is `unsigned -> pending -> signed -> revoked`. The model still accepts `pending_signatures`. The comment says it is not exposed, but the database accepts it and tests/serializers can still encounter it.  
**Fix:** Add a data migration converting legacy values, remove the enum choice, and add a database constraint for the four spec states.

### M-02 - B1: `withdraw()` transitions directly from signed to revoked, not "signed with withdrawn status"
**Location:** `apps/consent/services.py:291-296`  
**Problem:** The prompt explicitly asks that `withdraw()` move to `STATE_SIGNED` with `STATUS_WITHDRAWN`, but the code sets `state=STATE_REVOKED`. This may be correct for the fetched OpenAPI state enum, but it conflicts with the requested state-machine interpretation.  
**Fix:** Resolve the domain rule. If withdrawal is represented by `revoked`, update the audit checklist/tests; if withdrawal must preserve `signed`, change the service and serializer expectations.

### M-03 - C3: `grant()` has a read window with no current record
**Location:** `apps/consent/services.py:128-149`  
**Problem:** Inside one transaction, `grant()` bulk-updates previous rows to `is_current=False` before creating the new current row. Other transactions under weaker isolation can observe no current row until commit depending on database isolation/read pattern.  
**Fix:** Prefer creating the new row first, then flipping older rows false with an exclusion on the new PK, or add a partial unique constraint and rely on transaction serialization.

### M-04 - D1: PIPEDA export intentionally omits form submissions
**Location:** `apps/consent/tasks.py:348-357`  
**Problem:** The audit prompt requires form submissions in the export. The implementation explicitly returns `form_submissions = []` because the existing form model lacks a citizen FK. The explanation is technically sound, but the compliance capability is incomplete.  
**Fix:** Add an authenticated citizen FK or durable submitter identifier to form submissions, backfill where possible, and include those records in exports.

### M-05 - D1: Download token is time-limited but not single-use
**Location:** `apps/consent/models.py:619`, `apps/consent/views.py:191-214`  
**Problem:** The token expires after TTL and the first successful download marks the request delivered, so repeat downloads are blocked by status. However, the token value is not rotated or consumed independently.  
**Fix:** Add a consumed timestamp or rotate/null the token on first successful delivery, so token reuse remains impossible even if status is later changed manually.

### M-06 - F3: `process_data_export` can duplicate files/documents on retry
**Location:** `apps/consent/tasks.py:75-110`, `apps/consent/tasks.py:187-205`  
**Problem:** The task marks the request failed and retries after generic exceptions. If a failure occurs after either storage write but before the request reaches `ready`, a retry can create another storage object/document. Some orphan cleanup exists only for the final DB/audit block.  
**Fix:** Use an idempotency key derived from export request ID for storage/document creation, and wrap document creation state with a retry-safe "already exists" check.

### M-07 - F5: `ConsentAuditEntry.save()` blocks normal admin edits, but QuerySet updates are not blocked
**Location:** `apps/consent/models.py:812-818`, `apps/consent/admin.py:197-271`  
**Problem:** Instance `save()`/`delete()` are blocked, but Django `QuerySet.update()` can still mutate append-only audit rows. Admin appears read-only, but model-level immutability is not complete.  
**Fix:** Add database-level protection where possible, remove update permissions for audit/revision models, and test that admin has no change/delete actions.

### M-08 - B6/F1: Some list endpoints lack `total` and consistent pagination envelopes
**Location:** `apps/consent/govstack_views.py:374-379`, `apps/consent/govstack_views.py:439-444`, `apps/consent/govstack_views.py:677-684`, `apps/consent/govstack_views.py:767-776`  
**Problem:** Some list endpoints return `total`, others only arrays. The fetched OpenAPI does not define offset/limit metadata, but the prompt requires consistent pagination envelopes.  
**Fix:** Either conform exactly to fetched OpenAPI with no extra metadata, or standardize list response envelopes and update the OpenAPI used for certification.

## LOW FINDINGS

### L-01 - F7: Stale backup file exists outside consent
**Location:** `apps/audit/services.py.bak`  
**Problem:** No `.bak` file exists in `apps/consent/`, but there is a stale backup in `apps/audit/`, which supports the consent audit trail.  
**Fix:** Remove it or move it out of the repo after confirming it is not needed.

### L-02 - F7: Unused imports remain
**Location:** `apps/consent/govstack_views.py:24`, `apps/consent/migrations/0011_consent_record_history.py:13`, test files  
**Evidence:** `.venv/bin/ruff check apps/consent --select=F401` reports 7 unused imports.  
**Fix:** Run Ruff with `--fix` or remove the imports manually.

### L-03 - I: Static Celery Beat schedule has cleanup but no process retry task
**Location:** `config/settings/base.py:371-393`  
**Problem:** `consent.cleanup_export_files` is scheduled. There is no scheduled `consent.process_data_export` retry/recovery task; retries rely on Celery task retry behavior and cleanup recovery of stuck processing rows.  
**Fix:** If operational requirements expect periodic retry/requeue, add a dedicated Beat task to requeue failed/stuck pending exports.

### L-04 - A1: Prompt and fetched spec disagree on several required endpoints
**Location:** external spec vs prompt  
**Problem:** The prompt lists `GET /config/webhook/{id}/payload/`, `PUT/DELETE /config/individual/{id}/`, and `DELETE /config/individual/{id}/` as required. The fetched OpenAPI does not include these.  
**Fix:** Pin the certification artifact by URL/tag and stop mixing prompt-era requirements with the latest published spec.

---

## SPEC COMPLIANCE MATRIX

| Endpoint | Spec status | Implementation status | Notes |
|---|---|---|---|
| POST `/config/policy/` | Present | Present | Returns 200 per fetched spec. |
| GET/PUT/DELETE `/config/policy/{policyId}/` | Present | Present as `{policy_id}` | Parameter name mismatch. |
| GET `/config/policy/{policyId}/revisions/` | Present | Present as `{policy_id}` | Response includes extra `revisions` and `total`; fetched schema only declares `policy`. |
| GET `/config/policies/` | Present | Present | Adds `total`. |
| POST `/config/data-agreement/` | Present | Present | Serializer emits extra fields. |
| GET/PUT/DELETE `/config/data-agreement/{dataAgreementId}/` | Present | Present as `{data_agreement_id}` | Parameter name mismatch. |
| GET `/config/data-agreements/` | Present | Present | Adds `total`. |
| POST `/config/individual/` | Present | Present | Accepts `email`/`externalId`; fetched Individual only requires `id`. |
| GET `/config/individual/{individualId}/` | Present | Present as `{individual_id}` | Parameter name mismatch. |
| PUT/DELETE `/config/individual/{individualId}/` | Not in fetched spec | Implemented | Extension or prompt/spec drift. |
| GET `/config/individuals/` | Present | Present | No `total`. |
| POST `/config/webhook/` | Present | Present | Serializer emits extra webhook fields. |
| GET/PUT/DELETE `/config/webhook/{webhookId}/` | Present | Present as `{webhook_id}` | Parameter name mismatch. |
| GET `/config/webhook/{webhookId}/payload/` | Not in fetched spec | Implemented | Extension or prompt/spec drift. |
| GET `/config/webhooks/` | Present | Present | No `total`. |
| POST `/service/individual/` | Present | Present | Creates account from email. |
| GET/PUT `/service/individual/{individualId}/` | Present | Present as `{individual_id}` | Non-staff self-scope enforced. |
| GET `/service/individuals/` | Present | Present | Citizens see only self. |
| GET `/service/data-agreement/{dataAgreementId}/` | Present | Present as `{data_agreement_id}` | Serializer emits extra fields. |
| GET `/service/policy/{policyId}/` | Present | Present as `{policy_id}` | Parameter name mismatch. |
| GET `/service/verification/data-agreements/` | Present | Present | Requires `data_consumers` or staff. |
| GET `/service/verification/consent-records/` | Present | Present | Filters accepted. |
| GET `/service/verification/consent-record/{consentRecordId}/` | Present | Present as `{consent_record_id}` | Does not filter current records only. |
| POST/GET `/service/individual/record/data-agreement/{dataAgreementId}/` | Present | Present as `{data_agreement_id}` | POST validates `individualId` for non-admin callers. |
| POST `/service/individual/record/consent-record/draft/` | Present | Present | Requires query params. |
| POST/GET `/service/individual/record/consent-record/` | Present | Present | POST ignores spec-required supplied `signature`. |
| PUT `/service/individual/record/consent-record/{consentRecordId}/` | Present | Present as `{consent_record_id}` | Scoped to owner. |
| POST/PUT `/service/individual/record/consent-record/{consentRecordId}/signature/` | Present | Present as `{consent_record_id}` | POST can create unaudited state transition. |
| GET `/service/individual/record/data-agreement/{dataAgreementId}/all/` | Present | Present as `{data_agreement_id}` | Owner-scoped despite name. |
| DELETE `/service/individual/record/` | Present | Present | RTBF deletes forgettable, non-required records. |
| GET `/audit/consent-records/` | Present | Present | Requires auditor/staff, not any citizen token. |
| GET `/audit/consent-record/{consentRecordId}/` | Present | Present as `{consent_record_id}` | Parameter name mismatch. |
| GET `/audit/data-agreements/` | Present | Present | Requires auditor/staff. |
| GET `/audit/data-agreement/{dataAgreementId}/` | Present | Present as `{data_agreement_id}` | Parameter name mismatch. |
| GET `/audit/consent-log/` | Not in fetched spec | Implemented | CivicOS extension. |

---

## ENDPOINT COVERAGE MATRIX

| Endpoint group | Test class | Happy path | 401 | 403 | 404 | Bad input |
|---|---|---:|---:|---:|---:|---:|
| Config policy | `ConfigPolicyTests` | yes | yes | yes | yes | partial |
| Config data agreement | `ConfigDataAgreementTests` | yes | no | no | no | partial |
| Config individual | `ServiceIndividualTests` partly | partial | yes | partial | no | no |
| Config webhook | `ConfigWebhookTests`, `Round9WebhookDisabledFieldTests` | yes | no | no | no | partial |
| Service consent record list/create/update | `ServiceConsentRecordTests`, `Round9GrantAutoSignatureTests` | yes | yes | partial | no | yes |
| Service consent draft | `ServiceConsentRecordDraftTests` | yes | no | yes | no | yes |
| Service DA consent record | `ServiceDataAgreementConsentRecordTests` | yes | no | no | yes | partial |
| Service RTBF | `ServiceRightToBeForgottenTests`, `Round9RTBFRequiredGuardTests` | yes | yes | n/a | no | no |
| Service individual | `ServiceIndividualTests` | yes | yes | partial | no | no |
| Service verification | `ServiceVerificationTests` | yes | no | yes | no | no |
| Audit | `AuditAPITests`, `AuditEndpointAuthorizationTests` | yes | yes | yes | partial | no |
| Signature | `ConsentRecordSignatureTests` | yes | yes | yes/404 owner hiding | yes | yes |
| Revision chain | `ConsentRevisionTests` | yes | n/a | n/a | n/a | n/a |

Zero/weak coverage endpoints:
- `GET /config/webhook/{webhookId}/payload/`
- `PUT/DELETE /config/individual/{individualId}/`
- `GET /service/data-agreement/{dataAgreementId}/`
- `GET /service/policy/{policyId}/`
- Bad-input and 404 coverage is uneven across config/webhook/individual/list endpoints.

---

## UNCOVERED LINES / BRANCHES

Coverage was generated from a failed run; treat these as approximate.

```text
apps/consent/admin.py: 77, 80, 83, 86, 120, 123, 126, 149, 152, 155, 187-189, 192, 225, 228, 231, 256, 260, 263, 266, 269
apps/consent/api_views.py: 76-92, 106-114
apps/consent/forms.py: 27, 34
apps/consent/govstack_views.py: 87, 110, 130, 233, 251-252, 325-326, 376-379, 383-392, 401-404, 408, 412-419, 423-426, 457, 478-479, 482, 494, 513-519, 544-551, 564-576, 580-594, 609-614, 632-655, 711, 736-737, 740, 791, 829-830, 834-836, 847, 850, 882-883, 912, 920-926, 937-944, 981, 985-986, 995, 999-1000, 1005-1012, 1105-1106, 1135-1136, 1156, 1206-1207
apps/consent/management/commands/seed_consent_categories.py: 8-129
apps/consent/models.py: 72, 267, 274, 351, 369, 566, 722, 725, 814, 818, 942
apps/consent/receivers.py: 29-45, 69-70, 104-112, 119
apps/consent/serializers.py: 77-79, 188, 250, 313, 412-420, 424
apps/consent/services.py: 49-60, 119, 286, 289, 660-691
apps/consent/tasks.py: 51-55, 90, 135-160, 169-186, 200-201, 228-229, 259-275, 278, 281-286, 330-331, 345-346, 384, 405-406, 414-415
apps/consent/views.py: 100-101, 133-134, 199, 206-210, 235-242, 248-249, 257-262
```

Branch coverage: not available.

---

## DEPRECATION WARNINGS

- No `CheckConstraint(check=...)` occurrences remain in `apps/consent`, `apps/audit`, `apps/auth_extension`, or `config`.
- The active consent model uses `CheckConstraint(condition=...)` at `apps/consent/models.py:791-800`.
- Migration `apps/consent/migrations/0003_protect_citizen_fk.py:33` also uses `condition=`.

Runtime warnings observed:
- `FERNET_KEYS` not set; `EncryptedCharField` falls back to `SECRET_KEY`.
- `VOLUNTEER_SIN_FERNET_KEYS` not set.
- `CIVICOS['CLAMAV_HOST']` not configured.
- Fontconfig cache directories are not writable.

---

## SUMMARY VERDICT

**NOT READY - fundamental gaps remain.**

Certification should not proceed until:
1. The consent test suite runs green under the documented command or an officially documented equivalent.
2. The POST consent-record flow validates and persists the caller-supplied spec-required `signature`.
3. Signature creation cannot mutate consent state without status, audit entry, and revision.
4. The project pins one authoritative GovStack spec version and removes/hides endpoint and serializer extensions from the certification surface.
5. GovStack path parameter names, serializer fields, and pagination/status-code behavior are brought into exact alignment with that pinned spec.

