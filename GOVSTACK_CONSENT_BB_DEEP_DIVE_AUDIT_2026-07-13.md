# GovStack Consent BB — Deep Dive Audit Report
**Date:** 2026-07-13  
**Auditor:** Codex  
**Target:** `/Users/lionel/builders/govstack`  
**Spec version checked against:** GitHub release/tag `v23Q4`, commit `3f7d2e2fa2b55b1f36890bfc20316227c9ee98ca`, OpenAPI file `api/consent-openapi.yaml`. The fetched YAML declares `info.version: 1.1.0-rc1`. The prompt's `main/api/openapi.yaml` URL currently returns `404`, and GitHub releases expose `v23Q4` as the latest release, not a visible `v1.3.0` release.  
**Test suite result:** Not runnable in this shell. `python3 manage.py test apps.consent --verbosity=2` fails before test discovery with `ModuleNotFoundError: No module named 'django'`. Static inventory found 245 `def test_...` methods under `apps/consent/tests/`, but this is not a runtime pass count.  
**Line coverage:** Not available. `coverage` is not installed (`zsh: command not found: coverage`).  
**Branch coverage:** Not available.

---

## Source And Runtime Notes

- Required code files in `apps/consent/`, `apps/audit/`, `apps/auth_extension/models.py`, `config/settings/base.py`, `config/settings/test.py`, `config/settings/production.py`, `apps/api/urls.py`, `apps/api/exceptions.py`, and `config/urls.py` were read.
- The current tree contains an untracked `apps/consent/migrations/0012_codex_audit_fixes.py`; the prompt listed migrations only through `0011`. I audited the current tree, and I call out where `0012` changes the migration story.
- Existing worktree was dirty before this audit was written. I did not revert any changes.
- `flake8 apps/consent/ --select=F401` was not run because `flake8` is not installed.

---

## CRITICAL FINDINGS (must fix before certification)

### C-01 — C2/A4/F4: Webhook HMAC secrets are still returned by every GovStack webhook response
**File and line:** `apps/consent/serializers.py:435`  
**Problem:** `WebhookSerializer.secretKey = serializers.CharField(source="secret_key")` exposes the raw shared HMAC secret in POST, GET, PUT, and list responses. The model now uses `EncryptedCharField` at `apps/consent/models.py:689`, so at-rest encryption is improved, but API exposure remains a credential leak.  
**Exact fix required:** Make `secretKey` write-only for create/update or return it only once on creation if the certification schema absolutely requires it. For list/detail/update responses, return a non-secret fingerprint such as `secretKeySet: true` or a masked value only if the spec harness permits extensions. Add tests proving `/config/webhooks/` and `/config/webhook/{id}/` never return reusable plaintext secrets.

### C-02 — D1/C6: PIPEDA export task omits required data and logs citizen identifiers
**File and line:** `apps/consent/tasks.py:337`  
**Problem:** `_build_export_payload()` explicitly sets `form_submissions = []` and never includes `ConsentAuditEntry` rows, even though the audit request requires profile, consent history, service requests, form submissions, notifications, and audit entries. The task also logs citizen PKs and export request IDs at `apps/consent/tasks.py:165`, `apps/consent/tasks.py:372`, and `apps/consent/tasks.py:395`.  
**Exact fix required:** Add consent audit entries to the export payload. Add form submissions once the forms app has a reliable citizen FK; until then, document the limitation in product behavior and schema. Remove citizen/export identifiers from operational logs or replace them with non-PII request correlation IDs.

### C-03 — Verification Blocker: Tests and coverage cannot run in the current environment
**File and line:** runtime environment  
**Problem:** The required `python manage.py test apps.consent --verbosity=2` equivalent fails immediately because Django is not installed in the active `python3` environment. Coverage cannot run because `coverage` is not installed. Certification readiness cannot be claimed without executable tests and coverage.  
**Exact fix required:** Activate or recreate the project virtualenv, install dependencies and coverage tooling, then run:

```bash
python3 manage.py test apps.consent --verbosity=2
coverage run --source=apps/consent manage.py test apps.consent
coverage report --show-missing
```

---

## HIGH FINDINGS (should fix before production)

### H-01 — A4: GovStack schemas still leak many non-spec response fields
**File and line:** `apps/consent/serializers.py:212`  
**Problem:** The fetched `v23Q4` `DataAgreement` schema contains only `id`, `version`, `controller`, `policy`, `purpose`, `lawfulBasis`, `dataUse`, `dpia`, `active`, `forgettable`, `compatibleWithVersion`, `lifecycle`, and `signature`. `DataAgreementSerializer` emits many extra fields: `slug`, `language`, `purposeDescription`, `dataUsePurpose`, `dataUsePurposeDescription`, `dataUsePurposeRestriction`, `dataUseActivity`, `dataUsagePolicy`, `dpiaDate`, `dpiaEvidenceUrl`, `dpiaSummaryUrl`, `dpiaUrl`, `policyId`, `dataRetentionPeriodDays`, `dataControllerName`, `dataControllerUrl`, `dataControllerLogoImageUrl`, and `attributes`. `IndividualSerializer` at `apps/consent/serializers.py:325` also emits `name`, `iamRef`, `phone`, `email`, and `consentRecordsCount` beyond the spec's `id`, `externalId`, `externalIdType`, and `identityProviderId`. `WebhookSerializer` emits `events`, `signatureHeader`, `skippedHeaders`, and `timeStamp` beyond the spec's `id`, `payloadUrl`, `contentType`, `disabled`, and `secretKey`.  
**Exact fix required:** Split GovStack-strict serializers from CivicOS extension serializers. The certification-facing serializers should emit only OpenAPI properties unless the harness explicitly tolerates `additionalProperties`.

### H-02 — A3/A8: Several spec query/body parameters are accepted but ignored or under-implemented
**File and line:** `apps/consent/govstack_views.py:898`  
**Problem:** `ServiceIndividualDataAgreementConsentRecordView.post()` validates `revisionId` but then always grants against `ConsentService._get_latest_revision(category)`, so the requested revision is ignored. `ConfigPolicyListView.get()` and `ConfigWebhookListView.get()` ignore the spec's optional `revisionId` query parameter. `ServiceVerificationConsentRecordsView.get()` implements useful `individualId` and `dataAgreementId` filters, but the fetched spec only advertises `offset` and `limit`; this is an undocumented extension.  
**Exact fix required:** Either honor `revisionId` exactly where the spec defines it, or reject stale revisions with a clear `400` rather than silently using latest. Implement or explicitly remove unsupported `revisionId` query parameters from list endpoints.

### H-03 — C3/B1: Concurrent grant can expose a no-current-record window
**File and line:** `apps/consent/services.py:124`  
**Problem:** `grant()` bulk-updates all current records to `is_current=False` before creating the new row. This happens inside `transaction.atomic()`, but readers outside the transaction can still see no `is_current=True` row depending on isolation and database timing. The method uses `select_for_update()` on existing signed/current rows, but it does not lock all current rows for the citizen/category before the archive step.  
**Exact fix required:** Lock the current citizen/category row set before the idempotency check and archive step, or use a deferrable partial unique/indexed current-row strategy with transactionally consistent replacement. Add a concurrent grant/withdraw test using two transactions, not just idempotent sequential calls.

### H-04 — G2/B4: Required-consent bootstrap now uses the service layer, but it conflates legal necessity with citizen opt-in
**File and line:** `apps/consent/receivers.py:89`  
**Problem:** The old direct-write bypass has been fixed: `bootstrap_required_consents` now calls `ConsentService.grant()`, which creates revisions, audit entries, and signatures. However, this still records required categories as citizen `optIn=True` grants even when the basis is `legal_obligation`. That can misrepresent mandatory processing as voluntary consent under PIPEDA and GovStack semantics.  
**Exact fix required:** Model required/legal-obligation processing separately from optional consent, or add a system-authorized bootstrap path whose revision clearly indicates legal basis and `authorizedByOther="system"` rather than treating it as a citizen signature.

### H-05 — C6/G3: Email receivers and tasks log citizen PKs
**File and line:** `apps/consent/receivers.py:43`  
**Problem:** `send_withdrawal_confirmation_email`, `send_export_request_received_email`, `_notify_export_ready`, and task failure handlers log citizen PKs and sometimes export IDs. UUID PKs are still personal identifiers in this context.  
**Exact fix required:** Remove citizen and export IDs from logs. Use request IDs, task IDs, event type, and non-identifying status only. Keep the detailed evidence in `ConsentAuditEntry`, not application logs.

### H-06 — F5: Admin registration is incomplete for GovStack immutable and sensitive models
**File and line:** `apps/consent/admin.py:12`  
**Problem:** `ConsentRevision`, `ConsentWebhook`, `ConsentPolicy`, and `ConsentSignature` are not registered. The request specifically required `ConsentRevision` read-only admin handling, `ConsentRecord` to show `is_current`, and sensitive secret/token fields not to be displayed. Current `ConsentRecordAdmin.list_display` omits `is_current`, and `DataExportRequestAdmin.fields` exposes `download_token`.  
**Exact fix required:** Register `ConsentRevision` read-only with no add/change/delete. Register or intentionally hide `ConsentWebhook` with masked secrets. Add `is_current` to `ConsentRecordAdmin.list_display` and readonly fields. Remove `download_token` from normal admin fields or mask it.

### H-07 — D3/F2: Right-to-be-forgotten deletes ConsentRecord rows directly
**File and line:** `apps/consent/services.py:600`  
**Problem:** `right_to_be_forgotten()` intentionally uses `QuerySet.delete()` to bypass model immutability comments and hard-deletes all forgettable `ConsentRecord` rows. It preserves `ConsentRevision`, but deletion breaks FK relationships if revisions or signatures are expected to remain explainable, and it removes the primary consent evidence rather than anonymizing it.  
**Exact fix required:** Prefer anonymization/tombstoning for consent records, or add a dedicated legal basis for hard-delete with full pre-delete audit snapshots. If hard-delete remains required, add DB-level constraints and tests proving signatures, revisions, and audit exports stay coherent.

---

## MEDIUM FINDINGS (fix in next sprint)

### M-01 — A6: Error envelope includes an extra `status` field and raw validation structure
**File and line:** `apps/api/exceptions.py:94`  
**Problem:** The handler now emits `code`, `message`, and `details`, which matches the requested shape better than prior code, but it also emits `status`. Validation errors still preserve raw DRF field names in `details`; that may be acceptable for CivicOS but can expose implementation-specific snake_case fields.  
**Exact fix required:** For GovStack endpoints, standardize validation details to canonical camelCase field names and remove `status` if the harness enforces exact schema.

### M-02 — B5/F6: Migration history still contains deprecated `CheckConstraint(check=...)`
**File and line:** `apps/consent/migrations/0007_govstack_alignment.py:246`  
**Problem:** Current models and migration `0012` use `condition=`, but historical migration `0007` still contains `models.CheckConstraint(check=...)`. A fresh install under Django 6.0 can still execute historical migrations and emit or fail on deprecated/removed arguments.  
**Exact fix required:** Edit/squash historical migrations before a Django 6.0 upgrade, or preserve compatibility through a migration squashing plan. Do not assume fixing only current model code is enough.

### M-03 — B5: Untracked `0012` migration changes production data semantics without a real data migration
**File and line:** `apps/consent/migrations/0012_codex_audit_fixes.py:61`  
**Problem:** `0012` changes `ConsentWebhook.secret_key` storage to `BinaryField` and comments that existing plaintext values will not auto-migrate. The suggested manual shell loop cannot decrypt/re-encrypt plaintext if the field descriptor now expects encrypted binary on read.  
**Exact fix required:** Replace the comment with a real `RunPython` migration that safely reads old plaintext before altering storage, encrypts it, and defines a tested reverse/no-op strategy. If this is not possible, force administrators to rotate all webhook secrets and document it as a breaking migration.

### M-04 — B6/F1: Missing indexes and prefetches remain
**File and line:** `apps/consent/models.py:715`  
**Problem:** `ConsentWebhook` dispatch filters `is_disabled=False`, but there is no index on `is_disabled`. `ConsentRecordGovStackSerializer.get_signature()` can cause N+1 queries for list endpoints unless `signature_obj` is selected/prefetched; list querysets generally select `category` and `citizen` but not the one-to-one signature. `DataAgreementSerializer` accesses `policy` but not all list querysets use `select_related("policy")`.  
**Exact fix required:** Add an index on `ConsentWebhook.is_disabled`. Use `select_related("signature_obj", "data_agreement_revision")` for consent-record lists and `select_related("policy")` for data-agreement lists.

### M-05 — E2/E3/E4: Service-layer transition tests are still incomplete
**File and line:** `apps/consent/tests/test_services.py:103`  
**Problem:** Existing tests cover basic grant/withdraw/idempotency but still use manual mutation in places and do not fully assert the required state machine: `unsigned -> signed -> revoked`, one `is_current=True` after grant-withdraw-regrant, historical records `is_current=False`, revision snapshot camelCase keys, or concurrent grant calls.  
**Exact fix required:** Add direct service tests for grant-withdraw-regrant history, current-row uniqueness, revision chain predecessor/successor, `authorizedByIndividual`/`authorizedByOther`, and concurrent locking.

### M-06 — E1: Endpoint tests do not provide full per-endpoint auth/403/404/bad-input coverage
**File and line:** `apps/consent/tests/test_govstack_api.py:110`  
**Problem:** `test_govstack_api.py` has broad happy-path coverage, but many endpoints lack the requested five-way coverage matrix. Examples: `ConfigDataAgreementTests` lacks unauthenticated and non-admin tests for each CRUD endpoint; `ConfigWebhookTests` lacks not-found and bad-input tests for read/update/delete; service policy/data-agreement detail endpoints are not visibly covered with 404/bad-input; `/config/webhook/{id}/payload/` has no visible tests.  
**Exact fix required:** Generate a test matrix from `govstack_urls.py` and add one happy, 401, 403, 404, and bad-input test per endpoint/method where applicable.

### M-07 — I: Consent cleanup schedule exists, but no retry beat entry exists
**File and line:** `config/settings/base.py:368`  
**Problem:** `CELERY_BEAT_SCHEDULE` includes `consent.cleanup_export_files`, satisfying the daily cleanup requirement. There is no scheduled retry task for failed/pending export processing; only stuck `processing` rows are recovered during cleanup.  
**Exact fix required:** Either add a retry/recovery beat entry for stale pending/failed exports or document why Celery's task retry policy plus cleanup is sufficient.

---

## LOW FINDINGS (nice to have / minor)

### L-01 — A1/H3: Implementation has documented extensions beyond the OpenAPI spec
**File and line:** `apps/consent/govstack_urls.py:128`  
**Problem:** `/config/webhook/{id}/payload/` and `/audit/consent-log/` are implemented but are not present in the fetched `v23Q4` OpenAPI paths. This is not necessarily wrong, but certification harnesses may reject undocumented endpoints if schema generation exposes them as part of the GovStack surface.  
**Exact fix required:** Keep extensions out of the GovStack certification schema or document them under a CivicOS namespace.

### L-02 — A5: Create/delete endpoints use `200`, which matches fetched spec but not normal REST expectations
**File and line:** `apps/consent/govstack_views.py:166`  
**Problem:** The implementation generally returns `200` for create and delete. The fetched spec also lists only `200` and `400`, so this is spec-compatible. It is still worth documenting because maintainers may be tempted to "fix" creates to `201` or deletes to `204`, which would break this specific contract.  
**Exact fix required:** Add a short comment/test asserting the GovStack v23Q4 status-code convention.

### L-03 — F7: Stale backup artifact exists outside Consent
**File and line:** `apps/audit/services.py.bak`  
**Problem:** No `.bak` files were found under `apps/consent/`, but `apps/audit/services.py.bak` still exists. It can confuse code search and audits.  
**Exact fix required:** Remove or archive the stale backup outside the import path after confirming it has no unique content.

---

## SPEC COMPLIANCE MATRIX

| Endpoint | Spec status | Implementation status | Notes |
|---|---|---|---|
| `POST /config/policy/` | Required | Implemented | Returns `200` per spec; creates policy and revision. |
| `GET /config/policy/{policyId}/` | Required | Implemented | Supports `revisionId`. |
| `PUT /config/policy/{policyId}/` | Required | Implemented | Creates revision. |
| `DELETE /config/policy/{policyId}/` | Required | Implemented | Soft delete; returns revision. |
| `GET /config/policy/{policyId}/revisions/` | Required | Implemented | Paginated with `offset`/`limit`. |
| `GET /config/policies/` | Required | Implemented | Ignores spec `revisionId` query param. |
| `POST /config/data-agreement/` | Required | Implemented | Response has extra serializer fields. |
| `GET /config/data-agreement/{dataAgreementId}/` | Required | Implemented | Uses integer path converter as model PK. |
| `PUT /config/data-agreement/{dataAgreementId}/` | Required | Implemented | Creates revision. |
| `DELETE /config/data-agreement/{dataAgreementId}/` | Required | Implemented | Soft deactivates. |
| `GET /config/data-agreements/` | Required | Implemented | Response key `dataAgreement`; extra fields. |
| `POST /config/individual/` | Required | Implemented | Minimal Django user proxy. |
| `GET /config/individual/{individualId}/` | Required | Implemented | UUID converter. |
| `GET /config/individuals/` | Required | Implemented | Lacks total count; serializer has extra fields. |
| `POST /config/webhook/` | Required | Implemented | Exposes `secretKey`; extra fields. |
| `GET /config/webhook/{webhookId}/` | Required | Implemented | Exposes `secretKey`; ignores `revisionId`. |
| `PUT /config/webhook/{webhookId}/` | Required | Implemented | Exposes `secretKey`. |
| `DELETE /config/webhook/{webhookId}/` | Required | Implemented | Hard deletes webhook. |
| `GET /config/webhooks/` | Required | Implemented | Exposes all secrets in list; ignores `revisionId`. |
| `POST /service/individual/` | Required | Implemented | Requires auth; spec says org scope. |
| `GET /service/individual/{individualId}/` | Required | Implemented | Citizen self-scope plus staff override. |
| `PUT /service/individual/{individualId}/` | Required | Implemented | Limited fields. |
| `GET /service/individuals/` | Required | Implemented | Citizen sees own row, staff sees all. |
| `GET /service/data-agreement/{dataAgreementId}/` | Required | Implemented | Extra serializer fields. |
| `GET /service/policy/{policyId}/` | Required | Implemented | Supports `revisionId`. |
| `GET /service/verification/data-agreements/` | Required | Implemented | Consumer group/staff permission. |
| `GET /service/verification/consent-records/` | Required | Implemented | Adds undocumented filters. |
| `GET /service/verification/consent-record/{consentRecordId}/` | Required | Implemented | Consumer group/staff permission. |
| `POST /service/individual/record/data-agreement/{dataAgreementId}/` | Required | Implemented | `revisionId` validated but not used. |
| `GET /service/individual/record/data-agreement/{dataAgreementId}/` | Required | Implemented | Current record only. |
| `POST /service/individual/record/consent-record/draft/` | Required | Implemented | Self-scope guard exists. |
| `POST /service/individual/record/consent-record/` | Required | Implemented | Auto-creates signature. |
| `GET /service/individual/record/consent-record/` | Required | Implemented | Own records only. |
| `PUT /service/individual/record/consent-record/{consentRecordId}/` | Required | Implemented | Own records only. |
| `POST /service/individual/record/consent-record/{consentRecordId}/signature/` | Required | Implemented | No revision is created when signature changes record state. |
| `PUT /service/individual/record/consent-record/{consentRecordId}/signature/` | Required | Implemented | Updates signature; no revision/audit entry. |
| `GET /service/individual/record/data-agreement/{dataAgreementId}/all/` | Required | Implemented | Own records only. |
| `DELETE /service/individual/record/` | Required | Implemented | Hard-deletes forgettable records. |
| `GET /audit/consent-records/` | Required | Implemented | Auditor/staff-only, stricter than spec `OAuth2: []`. |
| `GET /audit/consent-record/{consentRecordId}/` | Required | Implemented | Auditor/staff-only. |
| `GET /audit/data-agreements/` | Required | Implemented | Auditor/staff-only. |
| `GET /audit/data-agreement/{dataAgreementId}/` | Required | Implemented | Auditor/staff-only. |
| `GET /config/webhook/{webhookId}/payload/` | Not in fetched spec | Implemented extension | Keep out of certification schema. |
| `GET /audit/consent-log/` | Not in fetched spec | Implemented extension | CivicOS extension. |

---

## ENDPOINT COVERAGE MATRIX

Runtime coverage could not be measured. Static review of `apps/consent/tests/test_govstack_api.py` shows broad but uneven coverage.

| Endpoint group | Test class | Happy path | Auth failure | 403 | 404 | Bad input |
|---|---|---:|---:|---:|---:|---:|
| Config policy CRUD/revisions | `ConfigPolicyTests` | Yes | List only | List only | Read only | Partial |
| Config data-agreement CRUD | `ConfigDataAgreementTests` | Yes | No | No | Not complete | Not complete |
| Config individual list/detail/create | `ServiceIndividualTests` partially, config-specific sparse | Partial | List only | No | No | Partial |
| Config webhook CRUD | `ConfigWebhookTests`, `Round9WebhookDisabledFieldTests` | Yes | No | No | No | Partial |
| Config webhook payload | none visible | No | No | No | No | No |
| Service consent-record list/create/detail/update | `ServiceConsentRecordTests` | Yes | Yes | Partial | Partial | Yes |
| Service draft | `ServiceConsentRecordDraftTests` | Yes | Not explicit | Yes | Partial | Yes |
| Service RTBF | `ServiceRightToBeForgottenTests`, `Round9RTBFRequiredGuardTests` | Yes | Yes | N/A | N/A | Partial |
| Service individual endpoints | `ServiceIndividualTests` | Partial | Yes | Partial | Not complete | Not complete |
| Service verification endpoints | `ServiceVerificationTests` | Yes | Not all | Yes | Partial | Partial |
| Service signature endpoints | `ConsentRecordSignatureTests` | Yes | Yes | By 404 self-scope | Yes | Yes |
| DA all records endpoint | `DataAgreementAllConsentRecordsTests` | Yes | Yes | N/A | Yes | Pagination only |
| Audit endpoints | `AuditAPITests`, `AuditEndpointAuthorizationTests` | Yes | Yes | Yes | Partial | No |

---

## UNCOVERED LINES / BRANCHES

Coverage was not available because the local environment lacks both Django and `coverage`. Required output cannot be produced until the test environment is restored.

---

## DEPRECATION WARNINGS

- `apps/consent/migrations/0007_govstack_alignment.py:246` uses `models.CheckConstraint(check=...)`. Django 6.0 replaces `check` with `condition`.
- Current model code at `apps/consent/models.py:790` uses `condition=`, and untracked migration `0012` also re-adds the constraint with `condition=`, but historical migrations still matter for fresh installs and upgrades.

---

## SUMMARY VERDICT

🔴 **NOT READY** — 3 critical findings remain:

1. Webhook shared secrets are returned raw by GovStack webhook responses.
2. PIPEDA export is incomplete and logs personal identifiers.
3. Tests and coverage cannot run in the current environment, so certification readiness is unproven.

The implementation is much closer than earlier audit snapshots: the route table is largely complete, the audit namespace is now auditor-gated, required-consent bootstrap now goes through the service layer, `is_current` is consistently used in the main service paths, and cleanup scheduling exists. The remaining blockers are mostly around strict schema output, secret handling, PIPEDA export completeness, concurrency proof, and executable verification.
