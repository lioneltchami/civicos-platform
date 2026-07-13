# Codex Deep-Dive Audit: GovStack Consent Building Block
**Target codebase:** `/path/to/govstack` (root of the Django project — adjust to your checkout path)
**Purpose:** Produce a rigorous, finding-by-finding audit report that verifies the Consent BB is fully correct, spec-compliant, secure, well-tested, and production-ready. Report every flaw, gap, or improvement needed — no matter how small.

---

## 0. Context

This is a Django 5.2 + Django REST Framework (DRF) implementation of the [GovStack Consent Building Block v1.3.0](https://govstack.gitbook.io/bb-consent). It is a Canadian government consent management system (PIPEDA-compliant) that must pass certification at [testing.govstack.global](https://testing.govstack.global).

The implementation lives entirely in `apps/consent/`. A supporting audit-log infrastructure lives in `apps/audit/`. The custom User model lives in `apps/auth_extension/models.py`.

All file paths below are relative to the project root (the directory containing `manage.py`).

---

## 1. Files to Read (Read Every Single One — Do Not Skip)

Read each file in full before beginning any check:

### Core app files
```
apps/consent/models.py              # 925 lines — data models
apps/consent/services.py            # 775 lines — all business logic
apps/consent/govstack_views.py      # 1292 lines — GovStack API views
apps/consent/serializers.py         # 536 lines — DRF serializers
apps/consent/govstack_urls.py       # 134 lines — GovStack URL patterns
apps/consent/tasks.py               # 388 lines — Celery async tasks
apps/consent/signals.py             # 16 lines  — Django signals
apps/consent/receivers.py           # 126 lines — signal receivers
apps/consent/admin.py               # 119 lines — Django admin
apps/consent/api_views.py           # 129 lines — internal CivicOS REST API
apps/consent/api_urls.py            # 38 lines  — internal API URLs
apps/consent/views.py               # 268 lines — citizen dashboard HTML views
apps/consent/urls.py                # 22 lines  — HTML view URLs
apps/consent/forms.py               # 50 lines  — Django forms
apps/consent/apps.py                # 20 lines  — AppConfig (signal registration)
apps/consent/management/commands/seed_consent_categories.py
```

### All 11 migrations (read every one)
```
apps/consent/migrations/0001_initial.py
apps/consent/migrations/0002_alter_consentcategory_is_required_and_more.py
apps/consent/migrations/0003_protect_citizen_fk.py
apps/consent/migrations/0004_add_data_export_request_document_fk.py
apps/consent/migrations/0005_drop_data_export_request_storage_path.py
apps/consent/migrations/0006_alter_dataexportrequest_document.py
apps/consent/migrations/0007_govstack_alignment.py
apps/consent/migrations/0008_govstack_signature.py
apps/consent/migrations/0009_govstack_gap_fixes.py
apps/consent/migrations/0010_govstack_gap2_fixes.py
apps/consent/migrations/0011_consent_record_history.py
```

### All 7 test files (read every one)
```
apps/consent/tests/test_models.py          # model tests
apps/consent/tests/test_services.py        # service layer tests
apps/consent/tests/test_govstack_api.py    # GovStack API tests (1319 lines)
apps/consent/tests/test_views.py           # citizen dashboard HTML tests
apps/consent/tests/test_api.py             # internal CivicOS API tests
apps/consent/tests/test_tasks.py           # Celery task tests
```

### Supporting infrastructure
```
apps/audit/models.py                # audit log chain model
apps/audit/services.py              # audit log service
apps/audit/handlers.py              # audit event handlers
apps/auth_extension/models.py       # custom User model (auth)
config/settings/base.py             # global Django settings
config/urls.py                      # root URL conf (verify consent BB is mounted)
```

---

## 2. External Specifications to Fetch and Check Against

Fetch each URL and use its content as the authoritative reference:

### Primary spec
- **GovStack Consent BB OpenAPI spec (YAML):**
  `https://raw.githubusercontent.com/GovStackWorkingGroup/bb-consent/main/api/openapi.yaml`

- **GovStack Consent BB GitBook documentation:**
  `https://govstack.gitbook.io/bb-consent`

- **GovStack Consent BB GitHub repository (browse for README, changelog, issues):**
  `https://github.com/GovStackWorkingGroup/bb-consent`

- **GovStack Consent BB releases (check for v1.3.0 and any newer tags):**
  `https://github.com/GovStackWorkingGroup/bb-consent/releases`

- **GovStack Consent BB OpenAPI spec on GitHub (rendered):**
  `https://github.com/GovStackWorkingGroup/bb-consent/blob/main/api/openapi.yaml`

### Testing harness
- **GovStack testing portal (understand what tests are run during certification):**
  `https://testing.govstack.global`

- **GovStack test suite for Consent BB (if public):**
  `https://github.com/GovStackWorkingGroup/bb-consent/tree/main/test`

### Legal / compliance references
- **PIPEDA full text (Canadian privacy law this implementation must satisfy):**
  `https://laws-lois.justice.gc.ca/eng/acts/P-8.6/page-1.html`

- **OPC PIPEDA guidance on consent:**
  `https://www.priv.gc.ca/en/privacy-topics/collecting-personal-information/consent/gl_omc_201805/`

### Django / DRF
- **Django 5.2 release notes (check for deprecations affecting this code):**
  `https://docs.djangoproject.com/en/5.2/releases/5.2/`

- **Django 6.0 deprecation warnings (CheckConstraint.check argument):**
  `https://docs.djangoproject.com/en/dev/releases/6.0/`

- **DRF best practices:**
  `https://www.django-rest-framework.org/api-guide/`

---

## 3. Run the Full Test Suite First

Before any manual review, run all tests and capture output:

```bash
cd <project_root>
python manage.py test apps.consent --verbosity=2 2>&1 | tee /tmp/consent_test_output.txt
```

Also run with coverage:
```bash
coverage run --source=apps/consent manage.py test apps.consent
coverage report --show-missing 2>&1 | tee /tmp/consent_coverage.txt
```

Record in your report:
- Total test count
- Pass / fail / error counts
- Coverage percentage (line and branch)
- List of any failing tests with full tracebacks
- List of all lines / branches NOT covered

---

## 4. Check Category A: GovStack OpenAPI Spec Compliance

**Reference:** The OpenAPI YAML you fetched from GitHub in step 2.

For **every path** defined in `apps/consent/govstack_urls.py`, verify:

### A1. URL path exact match
Compare each `path()` entry in `govstack_urls.py` against the OpenAPI spec. Report any path that:
- Is present in the spec but missing from the implementation
- Is present in the implementation but not in the spec (undocumented extension — note it but do not flag as an error)
- Has a different path parameter name (e.g., `{id}` vs `{consent_record_id}`)
- Has a different HTTP method than the spec requires

### A2. Required endpoints — verify all are implemented
Cross-check that every endpoint in the spec's `paths:` block has a corresponding view class and URL pattern. Pay special attention to:
- `GET /config/policies/` — list
- `POST /config/policy/` — create
- `GET /config/policy/{id}/` — read
- `PUT /config/policy/{id}/` — update
- `DELETE /config/policy/{id}/` — delete
- `GET /config/policy/{id}/revisions/` — revision history
- `GET /config/data-agreements/` — list
- `POST /config/data-agreement/` — create
- `GET /config/data-agreement/{id}/` — read
- `PUT /config/data-agreement/{id}/` — update
- `DELETE /config/data-agreement/{id}/` — delete
- `GET /config/individuals/` — list
- `POST /config/individual/` — create
- `GET /config/individual/{id}/` — read
- `PUT /config/individual/{id}/` — update
- `DELETE /config/individual/{id}/` — delete
- `GET /config/webhooks/` — list
- `POST /config/webhook/` — create
- `GET /config/webhook/{id}/` — read
- `PUT /config/webhook/{id}/` — update
- `DELETE /config/webhook/{id}/` — delete
- `GET /config/webhook/{id}/payload/` — webhook payload
- All `/service/` endpoints
- All `/audit/` endpoints

### A3. Request body field names (camelCase)
The GovStack spec uses camelCase for all JSON keys. Check every `govstack_views.py` `POST`/`PUT` handler's `payload.get(...)` calls. Verify:
- Every field extracted from request body uses the exact camelCase name defined in the spec
- Where snake_case aliases are accepted (CivicOS backwards-compat), document which ones
- No required spec field is silently ignored

### A4. Response body field names and types
For every serializer in `serializers.py`, compare its output against the spec's response schema. Check:
- Every required field in the spec is present in the serializer output
- No extra fields leak out that the spec says must not be present
- Field types match (string vs integer vs UUID vs datetime ISO 8601)
- Nullable fields are correctly `allow_null=True`
- `optIn` boolean is returned as `true`/`false`, not `"true"`/`"false"`
- Timestamps use ISO 8601 format with timezone

### A5. HTTP status codes
Verify every view returns the exact HTTP status code the spec requires:
- `200` for GET success
- `201` for POST create success
- `204` for DELETE success (or `200` if spec says so)
- `400` for validation errors (not `422`)
- `401` for unauthenticated
- `403` for authenticated but unauthorized
- `404` for not found
- `409` for conflicts (duplicate create where spec specifies it)

### A6. Error response envelope
Verify all error responses use the GovStack/CivicOS error envelope format:
```json
{"error": {"code": "...", "message": "...", "details": {}}}
```
Check that DRF's default `{"field": ["error message"]}` format is never returned raw to the client.

### A7. Pagination
Verify all list endpoints implement the pagination parameters the spec defines:
- `offset` and `limit` (or `page` and `pageSize` — check what the spec says)
- Response envelope includes `total`, `items` (or equivalent)
- `_safe_int()` used on all pagination parameters (no bare `int()` calls on query params)

### A8. Filtering and sorting
Check each list endpoint for filter query params the spec defines (e.g., filtering consent records by `individualId`, filtering data agreements by `active`). Verify each filter is implemented.

### A9. Authentication scheme
The spec defines which endpoints require auth and which are public. Verify:
- `/config/` endpoints require staff/org authentication
- `/service/` endpoints require individual (citizen JWT) authentication
- `/audit/` endpoints require auditor authentication
- No endpoint is accidentally open (missing `authentication_classes` or `permission_classes`)
- No endpoint has weaker auth than the spec requires

### A10. ConsentRevision / audit trail
The spec requires a tamper-proof audit trail of all consent state changes. Verify:
- `ConsentRevision.create_for()` is called for every state transition in `services.py`
- Revision snapshots use camelCase keys matching the spec schema
- Predecessor chain (`predecessor` FK) is correctly set
- `_consent_record_snapshot()`, `_policy_snapshot()`, `_data_agreement_snapshot()` helpers produce schema-correct dicts

---

## 5. Check Category B: Data Model Correctness

### B1. ConsentRecord state machine
Verify the state machine transitions are:
- `unsigned → pending → signed → revoked` (in that order — no skipping)
- The `grant()` service method correctly moves through `STATE_UNSIGNED → STATE_SIGNED`
- The `withdraw()` service method correctly moves to `STATE_SIGNED` with `STATUS_WITHDRAWN`
- No code path sets `state=STATE_SIGNED` without first passing through `STATE_UNSIGNED`
- No code path sets `status=STATUS_GRANTED` on a record in `STATE_UNSIGNED`

### B2. `is_current` flag integrity
The `is_current` BooleanField replaced `unique_together`. Verify:
- When `grant()` creates a new record, it sets `is_current=False` on all previous records for that citizen/category via `update(is_current=False)` BEFORE creating the new one
- `withdraw()` does NOT set `is_current=False` — it updates the existing current record in-place
- `get_citizen_consents()` filters by `is_current=True`
- All `ConsentRecord.objects.get(citizen=..., category=...)` calls in views/services have `is_current=True` added — no call that could raise `MultipleObjectsReturned`
- `get_or_create()` calls for `is_current` records correctly include `is_current=True` in the lookup fields

### B3. Append-only models
Check that `ConsentRevision` and `ConsentAuditEntry` are append-only:
- Their `save()` methods raise an error if called on an existing (non-null PK) instance
- Their `delete()` methods are blocked
- No test or code path calls `.update()` on these querysets
- Check if Django admin accidentally exposes edit/delete actions for these models

### B4. `bootstrap_required_consents` receiver
The `receivers.py` `post_save` receiver creates ConsentRecords for required categories on new user creation. Verify:
- It uses `get_or_create` correctly (idempotent)
- It does NOT bypass the state machine (records are created with `STATUS_GRANTED` and no preceding `STATE_UNSIGNED` step — is this a spec violation?)
- It does NOT create a `ConsentRevision` for these auto-created records (if the spec requires a revision for every record, this is a gap)
- It is wrapped in error handling so that a failure does not prevent user registration
- It handles the case where required categories do not yet exist (e.g., fresh database before seed data)

### B5. Migration chain integrity
- Verify migrations form a linear chain with no gaps (`0001` through `0011`)
- Verify `0011_consent_record_history.py` correctly: removes `unique_together`, adds `is_current` field with `default=True`, adds `is_current` index
- Verify the `AlterField` for `ConsentSignature.verification_type` in `0011` includes `"pgp"` in choices
- Check for any `RunPython` operations that might fail on non-empty databases
- Check for missing `reverse_code=migrations.RunPython.noop` on irreversible operations
- Verify `CheckConstraint` usage — Django 6.0 deprecated `CheckConstraint(check=..., name=...)` in favour of `CheckConstraint(condition=..., name=...)`. List every occurrence and flag them.

### B6. Database indexes
Review all models and verify:
- `ConsentRecord`: indexes on `(citizen, category, is_current)` — without this a filter of `is_current=True` on large tables will be a seq scan
- `ConsentRevision`: indexed on `resource_type + resource_id` for efficient history lookups
- `ConsentAuditEntry`: indexed on `(citizen, timestamp)` for PIPEDA export queries
- `ConsentWebhook`: indexed on `is_active` if webhook delivery filters on it
- Any `select_related`/`prefetch_related` missing from list view querysets (N+1 risk)

### B7. ConsentSignature model
- Verify `ConsentSignature` is `OneToOneField` to `ConsentRecord` (not ForeignKey)
- Verify `verificationMethod` choices include all spec-required types: `string`, `rs256`, `ed25519`, `ps256`, `pgp`
- Verify `grant()` uses `ConsentSignature.objects.create()` (not `update_or_create`) since each grant now creates a new `ConsentRecord`

---

## 6. Check Category C: Security

### C1. Authorization — citizens can only access their own data
For every `/service/individual/` endpoint:
- Verify non-admin users cannot read or modify another citizen's consent records
- Verify the `individualId` query param is validated against `request.user.pk` for non-admin callers
- Check the F9 fix (draft endpoint) and F5 fix (consent record create) — are they both correctly gating non-admin access?
- Verify admin check uses `request.user.is_staff or request.user.groups.filter(name="consent_admins").exists()` (not just `is_staff`)

### C2. Webhook HMAC verification
- Verify outbound webhook delivery signs the payload with HMAC-SHA256 using the `webhook_endpoint_secret`
- Verify the secret is never logged or included in any API response
- Verify `ConsentWebhook.webhook_endpoint_secret` is stored as a Fernet-encrypted field (not plaintext)
- Verify there is no endpoint that returns the raw secret

### C3. Concurrent write safety
- Verify `select_for_update()` is used in `grant()` before the idempotency check
- Verify `select_for_update()` is used in `withdraw()` before the state update
- Verify these are inside `transaction.atomic()` blocks
- Check for any window between `is_current=False` bulk update and new record creation in `grant()` where a concurrent request could see no `is_current=True` record

### C4. Input validation
- Verify `_safe_int()` is used for ALL pagination `offset` and `limit` parameters — grep for any remaining bare `int(request.query_params.get(...))` calls
- Verify UUID path parameters are validated (Django does this via `<uuid:...>` converters — confirm all UUID params use this)
- Verify `dataAgreement` / `dataAgreementId` / `data_agreement_id` triple-alias handling does not allow injection via an unexpected field name
- Verify no raw SQL or ORM `.extra()` / `.RawSQL()` is used anywhere

### C5. Rate limiting
- Verify the GovStack endpoints use DRF throttling classes — check `DEFAULT_THROTTLE_CLASSES` in `config/settings/base.py`
- Verify per-citizen throttle applies to `/service/` endpoints
- Verify per-admin throttle applies to `/config/` endpoints

### C6. PIPEDA Data Minimisation in logs
- Grep for any `logger.info/debug/warning/error` calls in `govstack_views.py`, `services.py`, `tasks.py` that log citizen PK, email, or consent content — these are PII violations
- Verify `storage_key`, `download_token`, and export file paths are never logged
- Verify error messages do not leak internal field names or database IDs to API clients

### C7. JWT / token security
- Verify `ALGORITHM = "RS256"` in `config/settings/base.py` (not HS256)
- Verify `ACCESS_TOKEN_LIFETIME` is ≤ 30 minutes
- Verify `ROTATE_REFRESH_TOKENS = True`
- Verify token blacklist is enabled (`rest_framework_simplejwt.token_blacklist` in `INSTALLED_APPS`)
- Verify `/config/` endpoints do NOT accept citizen JWTs (they must require staff/service credentials)

### C8. CORS and CSRF
- Verify CSRF protection is applied to the citizen dashboard HTML views (`views.py`)
- Verify GovStack API views are exempt from CSRF (DRF handles this via session or JWT auth, not cookie)
- Verify `CORS_ALLOWED_ORIGINS` is set restrictively in production settings (not `CORS_ALLOW_ALL_ORIGINS = True`)

---

## 7. Check Category D: PIPEDA Compliance

### D1. Right of access (data export)
- Verify `process_data_export` Celery task collects: profile, consent history, service requests, form submissions, notifications, audit entries
- Verify the export JSON is stored with a 7-day TTL
- Verify the download token is single-use or time-limited
- Verify the export is delivered via secure link (not email attachment)
- Verify `DataExportRequest` status lifecycle: `pending → processing → ready → delivered / failed / expired`
- Verify a citizen can only have one active export request at a time (check `unique_active_export_per_citizen` constraint)

### D2. Right to withdraw
- Verify `withdraw()` correctly sets `status=STATUS_WITHDRAWN`
- Verify a withdrawal confirmation email is sent (via `consent_withdrawn` signal → `receivers.py`)
- Verify withdrawal is recorded in `ConsentAuditEntry`
- Verify a withdrawal `ConsentRevision` is created

### D3. Right to be forgotten
- Verify `ServiceIndividualRightToBeForgottenView` exists and is reachable via `DELETE /service/individual/record/`
- Verify it deletes or anonymizes only "forgettable" records (not records subject to legal hold)
- Verify it creates an audit entry documenting the deletion
- Verify it does NOT delete required consent categories (is_required=True records)
- Verify it does NOT delete ConsentRevision records (those are the immutable audit chain)

### D4. Consent version tracking
- Verify `ConsentRecord.consent_version` field exists and is populated
- Verify `CONSENT_CURRENT_VERSION` setting is used consistently

### D5. Breach of required consent
- Verify `ConsentService.has_consent()` or equivalent returns `True` for required categories regardless of whether the citizen explicitly consented (bootstrap creates these records)
- Verify a citizen cannot withdraw a required category

---

## 8. Check Category E: Test Coverage Quality

### E1. GovStack spec coverage (test_govstack_api.py — 1319 lines)
For each URL pattern in `govstack_urls.py`, verify there is at least one test class covering:
- Happy path (correct input → expected response)
- Authentication failure (no token → 401)
- Authorization failure (wrong user → 403)
- Not found (invalid ID → 404)
- Bad input (missing required field → 400)

List any endpoint that has zero test coverage.

### E2. State machine transition tests (test_services.py)
Verify tests exist for:
- `grant()` on a new citizen (first-time consent)
- `grant()` when already granted (idempotency — must return same record)
- `grant()` after a withdrawal (re-grant creates a new record, old record has `is_current=False`)
- `withdraw()` on a granted record
- `withdraw()` on an already-withdrawn record (error handling)
- `withdraw()` on a non-existent record (error handling)
- Concurrent `grant()` calls (race condition test with threading or mocking `select_for_update`)

### E3. `is_current` flag integrity tests
Verify tests exist that:
- Create a record, grant it, withdraw it, re-grant it — and check that only ONE record has `is_current=True`
- Check that historical records have `is_current=False`
- Check that `get_citizen_consents()` returns only `is_current=True` records

### E4. ConsentRevision chain tests
Verify tests check:
- Every state transition creates a revision
- Revisions form a chain (each has a `predecessor` pointing to the previous one)
- Revision snapshot keys are camelCase
- `authorizedByIndividual` is set for citizen actors, `authorizedByOther` for admin actors

### E5. Missing test scenarios (identify gaps)
List any scenario from the following that is NOT tested:
- `bootstrap_required_consents` receiver fires on user creation
- `bootstrap_required_consents` is idempotent (called twice for same user)
- `process_data_export` task collects data from all required sources
- `cleanup_export_files` task marks expired exports
- Webhook payload delivery with HMAC signature
- `DataExportRequest` uniqueness constraint (can't create two pending requests)
- `ConsentSignature` creation alongside `grant()`
- Right-to-be-forgotten deletes correct records only
- `ServiceIndividualConsentRecordDraftView` authorization (citizen can only draft for self)

### E6. Test isolation
- Verify every test uses `TestCase` (not `SimpleTestCase`) for database access
- Verify no tests rely on database state from other test methods (`setUp` vs `setUpClass`)
- Verify no tests use `time.sleep()` (use `freezegun` or `mock.patch` for time-dependent logic)
- Verify Celery tasks are tested with `CELERY_TASK_ALWAYS_EAGER = True` or `task.apply()` directly (not dispatched to a real broker)

---

## 9. Check Category F: Django / DRF Best Practices

### F1. N+1 queries
- Read every list view in `govstack_views.py` that returns multiple objects
- Verify `select_related()` is called for all ForeignKey fields accessed in serializers
- Verify `prefetch_related()` is called for all reverse FK or M2M fields accessed in serializers
- Pay special attention to: `ConsentRecord` → `citizen`, `category`, `data_agreement_revision`, `signature`

### F2. Transaction boundaries
- Verify all multi-step write operations (grant, withdraw, create policy+revision, etc.) are wrapped in `transaction.atomic()`
- Verify `on_commit()` is used for any side effects (emails, Celery tasks, webhooks) so they only fire after the transaction commits

### F3. Celery task robustness
- Verify `process_data_export` is idempotent (calling it twice for the same export request does not corrupt state)
- Verify `max_retries` is set on all tasks
- Verify tasks use `bind=True` and `self.retry(exc=..., countdown=...)` for retriable errors
- Verify `SoftTimeLimitExceeded` is caught in `process_data_export`
- Verify tasks use `select_related` / `get()` rather than passing whole model instances as task arguments

### F4. Serializer correctness
- Verify `read_only_fields` is set for auto-managed fields (id, created_at, granted_at, etc.)
- Verify `write_only_fields` is set for any fields that must not appear in GET responses
- Verify `to_representation()` overrides do not accidentally expose fields removed from `Meta.fields`
- Verify nested serializers do not trigger additional DB queries (use `source=` + `select_related` pattern)

### F5. Django admin
- Verify `ConsentRevision` and `ConsentAuditEntry` are registered as read-only in admin (no delete action, no edit action)
- Verify `ConsentRecord` admin shows the `is_current` column
- Verify sensitive fields (`webhook_endpoint_secret`, download tokens) are not displayed in admin list views

### F6. `CheckConstraint` deprecation
- Grep for `CheckConstraint(check=` in all consent models and migrations
- Django 6.0 renamed the `check` argument to `condition`
- List every occurrence — this will cause a `RemovedInDjango60Warning` and must be fixed before upgrading

### F7. Dead code and stale artifacts
- Look for any `.bak` files in `apps/consent/`
- Look for commented-out code blocks longer than 3 lines
- Look for functions or methods defined but never called
- Look for imports that are imported but never used (run `flake8 apps/consent/ --select=F401`)

---

## 10. Check Category G: Receiver and Signal Correctness

### G1. Signal registration
- Verify `ConsentConfig.ready()` in `apps.py` imports `receivers` so signals are connected at startup
- Verify all four signals (`consent_granted`, `consent_withdrawn`, `export_requested`, `export_ready`) are sent from the correct places in `services.py`
- Verify `consent_granted` is sent AFTER the transaction commits (i.e., inside `transaction.on_commit()`)
- Verify `consent_withdrawn` is sent AFTER the transaction commits

### G2. `bootstrap_required_consents` state machine bypass
The receiver in `receivers.py` creates ConsentRecords directly with `STATUS_GRANTED` without going through the `grant()` service method. This bypasses:
- The `STATE_UNSIGNED → STATE_SIGNED` state machine transition
- The `ConsentRevision` creation
- The `ConsentAuditEntry` creation
- The `ConsentSignature` creation

Assess whether this is acceptable (argue for or against) and whether the GovStack spec requires a revision/audit entry for system-bootstrapped consent records.

### G3. Email failure handling
- In `receivers.py`, `send_withdrawal_confirmation_email` uses `send_mail()` without `fail_silently=True` — an SMTP error will propagate and potentially roll back the withdrawal transaction if the receiver is called inside an atomic block. Verify this is safe.
- `send_export_request_received_email` uses `fail_silently=True` — this is acceptable but means SMTP failures are silently swallowed.

---

## 11. Check Category H: URL Routing Correctness

### H1. Mount point
- Verify in `config/urls.py` that the GovStack Consent BB is mounted at `/api/v1/consent/` (not a different prefix)
- Verify there is no namespace conflict between `govstack_urls.py` and `api_urls.py`

### H2. Routing priority
- The `govstack_urls.py` docstring notes: `/payload/` must come before `/{id}/` and `/draft/` must come before the list endpoint to avoid Django matching the literal string as a UUID
- Verify these ordering constraints are correctly observed in the final `urlpatterns` list
- Verify no URL accidentally matches a UUID where a literal string is expected

### H3. Double-routing check
- The `govstack_urls.py` defines both singular (`/config/policy/`) and plural (`/config/policies/`) paths for some resources, both pointing to the same view
- Verify the view correctly handles both GET (list) and POST (create) on the same view class by routing on `request.method`
- Verify this does not cause any OpenAPI schema generation issues

---

## 12. Check Category I: Configuration and Settings

Read `config/settings/base.py` and verify these consent-specific settings:

- `CONSENT_CURRENT_VERSION` — exists and is a string
- `DATA_EXPORT_TTL_DAYS` — exists (should be 7)
- `CELERY_TASK_ALWAYS_EAGER` — NOT set to True in production (only in test)
- `CELERY_BEAT_SCHEDULE` includes `consent.cleanup_export_files` task
- `CELERY_BEAT_SCHEDULE` includes `consent.process_data_export` retry task (if applicable)
- `DEFAULT_FROM_EMAIL` — not the Django default (`webmaster@localhost`)
- `EMAIL_BACKEND` — uses `anymail` in production, `django.core.mail.backends.locmem.EmailBackend` in test
- JWT settings: `SIMPLE_JWT.ALGORITHM = "RS256"`, `ACCESS_TOKEN_LIFETIME` ≤ 30 min
- `REST_FRAMEWORK.DEFAULT_THROTTLE_CLASSES` is set and includes per-scope throttles

---

## 13. Output Format

Produce a structured report with the following sections. Be brutally honest — do not soften findings, do not skip minor issues.

```markdown
# GovStack Consent BB — Deep Dive Audit Report
**Date:** YYYY-MM-DD
**Auditor:** Codex
**Spec version checked against:** GovStack Consent BB vX.X.X (state the exact tag/commit you fetched)
**Test suite result:** NNN tests, NNN pass, NNN fail, NNN error
**Line coverage:** NN%
**Branch coverage:** NN%

---

## CRITICAL FINDINGS (must fix before certification)
For each: finding number, category code (e.g. A3, C1), file and line number, exact description of the problem, and the exact fix required.

## HIGH FINDINGS (should fix before production)
Same format.

## MEDIUM FINDINGS (fix in next sprint)
Same format.

## LOW FINDINGS (nice to have / minor)
Same format.

## SPEC COMPLIANCE MATRIX
A table: Endpoint | Spec status | Implementation status | Notes
Cover every endpoint in the OpenAPI spec.

## ENDPOINT COVERAGE MATRIX
A table: Endpoint | Test class | Happy path ✓/✗ | Auth failure ✓/✗ | 403 ✓/✗ | 404 ✓/✗ | Bad input ✓/✗

## UNCOVERED LINES / BRANCHES
List file:line for every uncovered line from coverage report.

## DEPRECATION WARNINGS
List every Django/DRF deprecation warning found, with file and line.

## SUMMARY VERDICT
One of:
- ✅ READY FOR CERTIFICATION — no critical/high findings
- 🟡 NEARLY READY — N critical findings remain, list them
- 🔴 NOT READY — fundamental gaps, list them
```

---

## 14. Important Notes for Codex

1. **Do not infer. Read the actual code.** Do not assume a function does what its name implies — read every implementation.

2. **Do not trust the project's own documentation.** The `PROJECT_OVERVIEW_AND_STATUS.md` has been known to inflate readiness. Trust the code and tests only.

3. **Spec version matters.** If you find that the OpenAPI spec on GitHub has been updated past v1.3.0, note the delta and flag whether the implementation needs updating.

4. **Every `ConsentRecord.objects.get(citizen=..., category=...)` without `is_current=True` is a potential `MultipleObjectsReturned` bug** — because the `unique_together` constraint was removed in migration `0011`. Flag every such call.

5. **camelCase in snapshots is required.** Every snapshot helper (`_policy_snapshot`, `_data_agreement_snapshot`, `_consent_record_snapshot`) must produce dicts with camelCase keys. Flag any snake_case key.

6. **`authorized_by` vs `authorized_by_other`** — for admin/system actors, the ConsentRevision field `authorized_by` (FK to User) must be `None` and `authorized_by_other` must be set to the actor's PK as a string. For citizen actors, `authorized_by` must be the citizen User FK. Flag any inversion.

7. **The `bootstrap_required_consents` receiver creates records without going through the service layer.** This is a deliberate architectural choice but may violate the spec's requirement for an audit trail on every record. Assess this carefully.

8. **Check for `services.py.bak`** — a stale backup file was noted in the `apps/audit/` directory. Verify no similar stale files exist in `apps/consent/`.

9. **When checking test coverage, prioritise the service layer (`services.py`) and the GovStack views (`govstack_views.py`)** — these are the paths that will be exercised by the certification harness at `testing.govstack.global`.

10. **The `consent_granted` signal must NOT be sent before the transaction commits.** If a webhook fires and the receiving system calls back before the Django transaction commits, it will receive a 404. Verify `on_commit()` wrapping.
