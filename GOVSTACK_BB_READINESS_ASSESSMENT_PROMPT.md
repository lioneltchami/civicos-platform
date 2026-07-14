# GovStack BB Readiness Assessment — Reusable Audit Prompt

**Purpose:** Paste this entire file as a prompt to get an honest, evidence-based readiness
assessment for one or all GovStack Building Blocks in this codebase. Replace the
`[TARGET]` placeholder at the bottom with your specific request before running.

**Author:** CivicOS project  
**Last updated:** 2026-07-13  
**Applies to:** `/Users/lionel/builders/govstack`

---

## INSTRUCTIONS FOR THE AUDITOR

You are performing a rigorous, evidence-based readiness assessment of GovStack Building
Block implementations in this Django/Wagtail codebase. Your job is to be **honest and
precise** — not encouraging, not pessimistic, but accurate. Every claim you make must
be backed by specific file paths, line numbers, or test names. Do not infer production
readiness from the presence of a model or a route — verify the full stack.

### Ground Rules

1. **Read the actual code before forming any opinion.** Do not trust file names, comments,
   or previous assessments. Open every relevant file and read it.

2. **Check against the authoritative spec, not your memory.** For each BB, fetch the live
   OpenAPI spec from the official URL listed below. Compare field names, HTTP methods,
   status codes, error envelopes, and request/response shapes against what the code
   actually produces.

3. **Distinguish these four tiers precisely:**

   | Tier | Definition | Honest criteria |
   |------|-----------|-----------------|
   | 🔴 **Not Started** | No GovStack-specific implementation exists | No govstack_views.py, no BB-specific URL routing, no spec-aligned models |
   | 🟡 **Partial** | Implementation exists but has at least ONE of: missing required endpoints, incorrect spec compliance on >10% of endpoints, no service layer (direct ORM in views), missing audit trail, no tests, critical security gap | |
   | 🟢 **Production-Ready** | ALL required endpoints implemented and spec-compliant, service layer with atomic transactions, full audit trail, security hardened (auth/authz/throttle), 80%+ test coverage on happy path + critical error paths, migrations safe, admin registered | |
   | ✅ **Certified** | Has actually passed the GovStack certification harness at testing.govstack.global. NOT self-declared — only mark this if there is documented evidence of a passing harness run | |

4. **Never round up.** If 9 of 10 endpoints work but 1 critical one is broken, the BB
   is Partial, not Production-Ready. A single unaudited state transition makes a BB
   Partial. A missing index on a high-traffic query path is a Production-Ready blocker
   if it will cause performance failure at scale.

5. **Separate "it runs" from "it is correct."** A view that returns HTTP 200 but with
   wrong field names, wrong state transitions, or missing audit entries is not compliant.

---

## AUTHORITATIVE SPEC URLS

Fetch these before assessing each BB. If a URL 404s, note it and find the correct URL.

| BB | Spec URL | Notes |
|----|----------|-------|
| Consent | `https://raw.githubusercontent.com/GovStackWorkingGroup/bb-consent/main/api/consent-openapi.yaml` | v23Q4 / info.version 1.1.0-rc1. GitBook: consent.govstack.global |
| Payments | `https://raw.githubusercontent.com/GovStackWorkingGroup/bb-payments/main/api/openapi.yaml` | Check for latest release tag |
| Appointments / Scheduling | `https://raw.githubusercontent.com/GovStackWorkingGroup/bb-scheduler/main/api/openapi.yaml` | Also check bb-scheduling |
| Digital Registries / Documents | `https://raw.githubusercontent.com/GovStackWorkingGroup/bb-digital-registries/main/api/openapi.yaml` | |
| e-Services / Forms | `https://raw.githubusercontent.com/GovStackWorkingGroup/bb-eservices/main/api/openapi.yaml` | |
| Messaging | `https://raw.githubusercontent.com/GovStackWorkingGroup/bb-messaging/main/api/openapi.yaml` | |
| CMS / Information Mediator | `https://raw.githubusercontent.com/GovStackWorkingGroup/bb-information-mediator/main/api/openapi.yaml` | |

If a spec URL returns 404, search `https://github.com/GovStackWorkingGroup/` for the
correct repository and path. The canonical spec is always in `api/openapi.yaml` or
`api/<bb-name>-openapi.yaml` on the `main` branch. Note the discrepancy.

---

## ASSESSMENT DIMENSIONS

For each BB being assessed, evaluate all dimensions below. Skip dimensions that
genuinely do not apply (e.g., "Celery tasks" for a BB with no async work), but
explain why you skipped them. Do not skip them to inflate the score.

---

### DIMENSION 1 — Spec Endpoint Coverage

**What to check:**
- Fetch the authoritative OpenAPI spec.
- List every path + method combination the spec defines.
- For each one: find the corresponding view in the codebase (check govstack_urls.py
  and govstack_views.py). Mark as: ✓ Present | ⚠ Present but wrong | ✗ Missing.
- Check HTTP methods: does the view support exactly the methods the spec requires?
- Check status codes: does the view return the exact HTTP status the spec defines?
  (GovStack v23Q4 uses 200 for creates, not 201 — verify per spec.)
- Check path parameter names: do URL kwargs align with spec parameter names?
- Check request body field names: camelCase per spec? Or snake_case?
- Check response envelope shapes: does the response nest under the spec-defined key?

**Scoring:**
- 100% coverage + correct methods + correct status codes = PASS
- Any missing required endpoint = FAIL (Partial at best)
- Wrong status code on a certified endpoint = FAIL

---

### DIMENSION 2 — Request / Response Schema Fidelity

**What to check:**
- For each endpoint, compare the serializer output to the spec schema.
- Required fields present? Optional fields handled? No required fields missing?
- Extra fields in response: are they additive (OK) or do they conflict with spec types?
- Field type mismatches: e.g., spec says `string` but code returns JSON object.
  Look especially at: `serializedSnapshot`, `authorizedByIndividual`, revision hashes,
  pagination wrappers (`total`, `items` vs. raw arrays).
- List endpoints: does the pagination envelope match the spec? Consistent across all
  list endpoints in this BB?
- Error envelope: does it match the GovStack error format exactly?
  Expected: `{"error": {"code": "...", "message": "...", "details": {}}}`

**Evidence required:** Quote specific serializer class name and field that mismatches,
with the spec field name/type alongside it.

---

### DIMENSION 3 — State Machine Correctness

**What to check:**
- Read the GovStack spec's description of the entity lifecycle for this BB.
- Read the model's state/status field choices.
- For each state transition: is there a corresponding service method? Does that method:
  a) Run inside `transaction.atomic()`?
  b) Write a `ConsentRevision` (or equivalent BB revision/audit object)?
  c) Write an audit entry?
  d) Emit a signal/webhook inside `transaction.on_commit()`?
  e) Update `is_current` or equivalent pointer correctly?
- Are there any direct `model.save()` calls in views that bypass the service layer?
  These are always wrong.
- Are there legacy state values in the choices that the spec doesn't recognize?
  (e.g., `pending_signatures` that was removed — verify it's actually gone.)
- Can a record reach an inconsistent state (e.g., `state=signed` with `status=pending`,
  or `is_current=True` on two rows for the same citizen+category)?

**Evidence required:** For each transition, name the service method and the specific
lines that write revision + audit entry.

---

### DIMENSION 4 — Authentication and Authorization

**What to check:**
- For every endpoint, what `permission_classes` are applied?
- Does the spec say who can call this endpoint? (admin, individual/citizen, consumer,
  auditor, unauthenticated?)
- Does the implementation enforce exactly that? Or is it over-permissive
  (e.g., `IsAuthenticated` where spec requires admin) or under-permissive
  (e.g., `IsAdminUser` where spec allows any authenticated user)?
- Are custom permission classes (`IsConsentAdminUser`, `IsAuditorUser`, etc.) tested
  with: unauthenticated → 401, wrong role → 403, correct role → 200?
- Is there throttling on this BB's views? Are the throttle rates appropriate per scope
  (admin vs citizen vs consumer vs unauthenticated)?
- For owner-scoped endpoints (citizen can only access their own records): is the
  owner filter applied in the queryset, not just in application logic?

**Evidence required:** Quote the `permission_classes` line for at least 3 endpoints
across different namespaces (config, service, audit).

---

### DIMENSION 5 — Data Integrity and Audit Trail

**What to check:**
- Every state transition: is it inside `transaction.atomic()`? If `ATOMIC_REQUESTS=True`
  is set, are signals wrapped in `transaction.on_commit()` to prevent premature dispatch?
- Is there an append-only audit log model? Is it truly append-only?
  Check: `save()` blocks modifications, `delete()` blocks deletion. But also check:
  does `QuerySet.update()` bypass these guards? (It always does at the ORM level —
  is this acknowledged and mitigated by admin permissions?)
- For PIPEDA / GDPR right-of-access: does the data export include ALL relevant
  entity types (records, audit entries, signatures, revisions, documents)?
- For right-to-be-forgotten: does deletion correctly skip required/non-forgettable
  records? Is this tested?
- Are cryptographic hashes on revisions/signatures actually computed and stored,
  or are they placeholder fields?
- Is idempotency handled on retryable async tasks (Celery)?

**Evidence required:** Name the audit model and the specific `save()` override method
that enforces append-only. Name one endpoint that verifies the audit entry is written.

---

### DIMENSION 6 — Security Hardening

**What to check:**
- Are secrets (webhook `secret_key`, download tokens, API keys) stored encrypted?
  Check: `EncryptedCharField` or equivalent, not plaintext `CharField`.
- Are secrets ever logged? Grep for `logger.*secret`, `logger.*token`,
  `logger.*password`, `logger.*key` in this BB's files.
- Is PII (citizen ID, email, phone) present in application logs?
  Grep for `logger.*citizen.pk`, `logger.*citizen_id`, `logger.*email`.
- Are file upload paths predictable? Can a user guess another user's download URL?
- Is there SQL injection risk? (DRF ORM is safe, but check raw queries.)
- Are webhook payloads validated with HMAC before processing inbound webhooks?
- Is there a download token that is single-use? Or can it be replayed?
- Does the admin expose sensitive fields (`secret_key`, `download_token`, raw tokens)?

**Evidence required:** For each issue found, quote the file path and line number.
For each check that passes, name the model field or method that enforces it.

---

### DIMENSION 7 — Test Coverage

**What to check:**
- List all test files for this BB (find `apps/<bb_name>/tests/`).
- Count test methods total.
- For each test class, state what it covers (which endpoint/scenario).
- Identify which critical scenarios have NO test:
  - Unauthenticated access (expects 401)
  - Wrong-role access (expects 403)
  - Owner isolation (citizen A cannot see citizen B's records)
  - State transition correctness (granted → withdrawn → re-granted)
  - Idempotency (calling grant twice returns same record)
  - Invalid input (400 with correct error envelope)
  - 404 for non-existent resources
  - Pagination (empty list, single page, multi-page)
  - Webhook dispatch (signal fires, not fires inside uncommitted transaction)
- Does `python manage.py test apps.<bb_name> --settings=config.settings.test` run
  clean? Zero errors, zero unexpected failures?

**Evidence required:** Run the test suite. Report exact pass/fail/error counts.
Name specific missing test scenarios by endpoint.

---

### DIMENSION 8 — Performance and Scalability

**What to check:**
- Do all list endpoints that query `ConsentRecord` (or equivalent) filter by
  `is_current=True` (or equivalent "latest row" pointer)? Forgetting this causes
  returning stale/historical rows after state transitions.
- Are there N+1 queries on list endpoints? Check for missing `select_related` /
  `prefetch_related` on any FK or OneToOne accessed in a serializer.
  Common misses: `record.category`, `record.citizen`, `record.signature_obj`,
  `record.data_agreement_revision`, `category.policy`.
- Are there composite indexes on the most common query patterns?
  For Consent: `(citizen, category, is_current)`, `(citizen, timestamp)`.
  For other BBs: identify the equivalent high-traffic filter columns.
- Are there unbounded querysets (no pagination) on any list endpoint?
- Is `select_for_update()` used on concurrent-write paths (e.g., grant + withdraw
  racing on the same citizen+category)?
- Is the Celery task retry behavior idempotent? Can retrying a failed task create
  duplicate records, files, or charges?

**Evidence required:** Quote the queryset for at least 2 list endpoints and confirm
whether `select_related` covers all serialized FK fields.

---

### DIMENSION 9 — Operations Readiness

**What to check:**
- Are all migrations present and in order? Run `python manage.py migrate --check
  --settings=config.settings.test` and report result.
- Are there data migrations for destructive field changes (e.g., changing CharField
  to BinaryField requires a RunPython to re-encode existing data)?
- Is there a management command or seed fixture to bootstrap this BB's required
  configuration (e.g., `seed_consent_categories` for Consent)?
- Is the Django admin registration complete? All BB models registered? No sensitive
  fields exposed (tokens, keys)? Append-only models correctly set to read-only?
- Is there a Celery Beat schedule for any recurring tasks this BB needs
  (cleanup, retry, TTL expiry)?
- Are all required settings documented in `config/settings/base.py` with clear names
  and defaults? (e.g., `DATA_EXPORT_TTL_DAYS`, `FERNET_KEYS`)
- Does the BB emit monitoring hooks (Sentry breadcrumbs, structured log events) for
  production observability?

**Evidence required:** Run `manage.py migrate --check`. Name the seed command if it
exists. List what's missing from admin.

---

### DIMENSION 10 — GovStack Certification Harness

**What to check (this is the most important dimension for certification):**
- Has this BB been run against the live GovStack certification harness at
  `https://testing.govstack.global`? If yes, provide the test run date and result.
- Are the Gherkin test scenarios from the upstream repo reviewed?
  Path: `https://github.com/GovStackWorkingGroup/bb-<name>/tree/main/test/`
  Check `test/README.md`, `test/plan.md`, and `test/gherkin/` for required scenarios.
- Is the BB's base URL mounted exactly as the harness expects it?
- Does the BB support the exact authentication scheme the harness uses?
  (Usually API key or JWT — check harness docs.)
- Are there any known harness-failing behaviors (wrong status code, wrong field name,
  missing required field in response)?

**Evidence required:** Either a harness run result, or a gap analysis against the
Gherkin scenarios listing each scenario as: ✓ Covered by tests | ⚠ Partially covered
| ✗ Not tested.

---

## OUTPUT FORMAT

Produce the following sections for each BB assessed:

```
## [BB Name] — [Tier: 🔴 / 🟡 / 🟢 / ✅]

### Verdict in one sentence
[Precise statement of why this tier was assigned.]

### Dimension Scores
| Dimension | Score | Key Evidence |
|-----------|-------|--------------|
| 1. Endpoint Coverage | PASS / PARTIAL / FAIL | [specific finding] |
| 2. Schema Fidelity | PASS / PARTIAL / FAIL | [specific finding] |
| 3. State Machine | PASS / PARTIAL / FAIL | [specific finding] |
| 4. Auth / Authz | PASS / PARTIAL / FAIL | [specific finding] |
| 5. Data Integrity | PASS / PARTIAL / FAIL | [specific finding] |
| 6. Security | PASS / PARTIAL / FAIL | [specific finding] |
| 7. Test Coverage | PASS / PARTIAL / FAIL | [N tests, missing: ...] |
| 8. Performance | PASS / PARTIAL / FAIL | [specific finding] |
| 9. Operations | PASS / PARTIAL / FAIL | [specific finding] |
| 10. Harness | CERTIFIED / NOT RUN / FAIL | [evidence] |

### Blockers to next tier
[Numbered list of exactly what must be fixed before this BB can advance one tier.
Be specific: file name, line number, what must change. No vague "improve tests".]

### Risks if deployed today
[What could go wrong for a real citizen if this BB were deployed to production now?]

### Estimated effort to production-ready
[Rough effort: hours for small fixes, days for structural gaps, weeks for missing BBs.]
```

After all individual BB assessments, produce:

```
## Overall CivicOS GovStack BB Readiness Summary

| Building Block | Tier | Certification | Blockers |
|----------------|------|---------------|---------|
| Consent | [tier] | [status] | [count] |
| Payments | [tier] | [status] | [count] |
| Appointments | [tier] | [status] | [count] |
| Documents | [tier] | [status] | [count] |
| Forms / e-Services | [tier] | [status] | [count] |
| Messaging | [tier] | [status] | [count] |
| CMS | [tier] | [status] | [count] |

## Certification Priority Order
[Which BB should be certified first, second, third — and why.]

## Cross-BB Integration Gaps
[Identify any place where BBs depend on each other and that dependency is broken or untested.]
```

---

## FILE LOCATIONS IN THIS CODEBASE

Use these as your starting points for each BB. Read every file listed — do not skip any.

### Consent BB
- `apps/consent/govstack_urls.py` — URL routing
- `apps/consent/govstack_views.py` — All API views
- `apps/consent/services.py` — Business logic service layer
- `apps/consent/models.py` — Data models, state machines, constraints
- `apps/consent/serializers.py` — Request/response serialization
- `apps/consent/tasks.py` — Async Celery tasks (export, webhooks, cleanup)
- `apps/consent/receivers.py` — Django signal receivers
- `apps/consent/admin.py` — Django admin registration
- `apps/consent/migrations/` — All migration files (check last 3 for correctness)
- `apps/consent/tests/test_govstack_api.py` — GovStack endpoint tests
- `apps/consent/tests/test_services.py` — Service layer tests
- `apps/consent/tests/test_models.py` — Model tests
- `apps/consent/tests/test_tasks.py` — Async task tests
- `apps/api/exceptions.py` — Shared error envelope (used by all BBs)
- `apps/core/fields.py` — Shared EncryptedCharField

### Payments BB
- `apps/payments/models.py`
- `apps/payments/gateway.py`
- `apps/payments/tasks.py`
- `apps/payments/receivers.py`
- `apps/payments/tests/` (all test files)
- Check: does it have `govstack_views.py`? If not, it has no GovStack API layer.

### Appointments BB
- `apps/appointments/models.py`
- `apps/appointments/tasks.py`
- `apps/appointments/receivers.py`
- `apps/appointments/tests/`
- Check: does it have `govstack_views.py`? If not, no GovStack API layer.

### Documents BB
- `apps/documents/models.py`
- `apps/documents/tasks.py`
- `apps/documents/tests/`
- Check: does it have `govstack_views.py`? If not, no GovStack API layer.

### Forms / e-Services BB
- `apps/forms/models.py`
- `apps/forms/views.py`
- `apps/forms/tests/`
- Check: does it have `govstack_views.py`? If not, no GovStack API layer.

### Messaging BB
- `apps/notifications/models.py`
- `apps/notifications/services.py`
- `apps/notifications/views.py`
- `apps/notifications/tests/`
- Check: does it have `govstack_views.py`? If not, no GovStack API layer.

### CMS BB
- `apps/cms/models.py`
- `apps/cms/wagtail_hooks.py`
- Check: does it have `govstack_views.py`? If not, no GovStack API layer.

### Shared Infrastructure (check for all BBs)
- `config/settings/base.py` — Settings, Celery Beat schedule, throttle rates
- `apps/api/urls.py` — How BBs are mounted
- `apps/api/throttling.py` — Rate limiting classes
- `apps/audit/` — If a shared audit chain exists across BBs

---

## HONESTY CALIBRATION GUIDE

Use this to avoid common inflation errors:

| Temptation | Correct response |
|------------|-----------------|
| "The model exists, so it's started" | Not Started means no GovStack API layer. A Wagtail page model is not a GovStack BB. |
| "Most endpoints work, a couple are missing" | If any required endpoint is missing, it's Partial, not Production-Ready. |
| "Tests exist" | How many? What do they cover? 3 happy-path tests ≠ Production-Ready. |
| "The code looks good" | Run it. Check the spec. Quote specific lines. Looking good ≠ correct. |
| "It was production-ready in a previous assessment" | Code changes. Re-verify everything from scratch every time. |
| "The migration runs" | Does it also correctly handle existing data? A `RunPython` that corrupts rows is worse than no migration. |
| "Security looks fine" | Did you grep for logged secrets? Did you check every admin ModelAdmin for exposed sensitive fields? |
| "Certified" | Only if there is documented evidence of a passing harness run. Never self-declare. |

---

## [TARGET] — REPLACE THIS SECTION BEFORE RUNNING

Assess the following (delete options that don't apply):

- [ ] ALL building blocks — full readiness matrix
- [ ] Consent BB only
- [ ] Payments BB only
- [ ] Appointments BB only
- [ ] Documents BB only
- [ ] Forms / e-Services BB only
- [ ] Messaging BB only
- [ ] CMS BB only
- [ ] Specific question: _______________

Additional context for this run:
- Recent changes since last assessment: _______________
- Specific concern to investigate: _______________
- Is this pre-certification (about to submit to testing.govstack.global)? YES / NO

---

*End of prompt. Paste everything above this line into a new session, fill in [TARGET], and run.*
