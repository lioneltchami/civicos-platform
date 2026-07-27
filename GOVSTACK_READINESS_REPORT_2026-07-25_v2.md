# GovStack BB Master Certifiability Report — CivicOS

**Date:** 2026-07-26, round 4 (supersedes the round-3 content previously in this file, which is preserved as historical narrative inside each BB section below — do not treat any tier/status claim above the "ROUND 4" markers as current).

**Method:** 3 parallel deep-review agents (Opus-class, research-only, no edits), each independently fetching live GovStack specs fresh from GitHub (not from memory or prior reports), re-running the actual test suites themselves, and — for Payments and Consent — executing live HTTP probes against the running Django stack to settle disputed behavior empirically rather than by code reading alone. Followed by personal verification of the highest-stakes findings across all four BBs: direct code reads, a fresh `makemigrations --check` run against all three apps, a live test-harness probe of the Payments voucher status-check endpoint, and direct reads of the Consent permission classes, the Payments callback-URL dispatch code, the Documents requirements files, and the Appointments `qry`-nesting serializers. Methodology: `GOVSTACK_BB_READINESS_ASSESSMENT_PROMPT.md` (10 dimensions, 4-tier scale, "never round up").

**Trigger for this round:** the user reported Payments as "just finished" (following the §24 remediation round — Issues A/B/C plus both secondary findings, all committed), and Appointments, Documents, and Consent as previously assessed/"had finished," and asked for a fresh, skeptical, deep review to confirm what's actually done and what's next.

---

## Overall Readiness Summary

| Building Block | Tier | Certification | Open Blockers |
|---|---|---|---|
| **Payments** | 🟡 Partial *(re-downgraded again — §24 closure did not mean "done")* | Not run, and cannot be run as currently deployed | 9 new findings; 2 are real defects (a documented-but-unimplemented 400 case, an SSRF sink), the rest are contract/coverage gaps |
| **Consent** | 🟡 Partial *(downgraded from 🟢 — empirically proven to fail the live harness)* | Not run; would fail 3/3 scenarios if run today | 7 findings; 1 is a certification-blocking design gap (no harness-mode auth bypass), 2 are ID-type contract mismatches |
| **Appointments / Scheduler** | 🟡 Partial *(no work has landed since round 3; auth-bypass finding still fully open, plus 1 new severe finding)* | Not run — no harness exists upstream | Auth-bypass-by-design (unchanged, unaddressed) + a newly-found request-format bug that breaks all 9 create endpoints for any spec-literal caller |
| **Documents** | 🟡 Partial *(re-scoped again — strong service layer, but packaging/config gaps would break the BB on first real deployment)* | No harness/spec exists upstream (confirmed again this round) | 2 critical deployment blockers (undeclared scanner dependency, storage-key prefix mismatch) that would each independently cause every uploaded document to be quarantined or mishandled |

**Headline: every "done" claim in this repo remains provisional until it has been executed against something.** This round's single most important structural finding, true across all four BBs: **neither Payments' nor Consent's live GovStack harness has ever actually been run against this codebase.** All "harness-conformant" language anywhere in this repo's spec documents is a code-reading claim, not an execution claim. Payments' own harness cannot even be pointed at CivicOS today without a reverse-proxy shim (its base URL is hardcoded to `localhost:3333`); Consent's harness *can* be run and would fail on its very first request, because every Consent endpoint requires authentication the harness has no way to provide. Appointments and Documents have no upstream conformance harness to run at all — verification there is necessarily code-reading-only, which is exactly why this round found a request-format bug (Appointments) and undeclared runtime dependencies (Documents) that three prior review rounds missed.

The user's own framing — "Payments we just finished" — is **not wrong** about the work that was actually scoped (§24 Issues A/B/C plus both secondary findings are genuinely implemented and independently re-verified; see the Payments section). It is wrong as a claim about overall BB readiness, because §24's scope was itself derived from a prior round's findings, not from a fresh, adversarial pass across the whole BB. This round did that fresh pass and found 9 more things, 2 of which are real defects.

---

## Payments BB — 🟡 Partial (re-downgraded)

### What's genuinely confirmed done (personally re-verified this round, not just re-read from the spec)

- **1,697/1,697 tests pass.** Reproduced by both the review agent and by me directly.
- All 5 §24 items — the 18-digit voucher serial (Issue A), P2G caller-identity auth via `IsTrustedPayerFI`/`RequirePayerFI` (Issue B), the 3 corrected voucher seed states (Issue C), the tightened 12-char G2P `RequestID`, and the `X-Platform-TenantId` presence/length validation — are **genuinely implemented exactly as the prior round's commits claim.** The reviewing agent independently re-extracted every relevant harness schema constraint from a fresh clone and confirmed each one field-by-field.
- Voucher and G2P state machines, audit trail immutability (`GovStackPaymentAuditEntry.save()/delete()` raise `PermissionError`), and `select_for_update()` concurrency protection on every voucher write path are all correct and unchanged from prior rounds.

### NEW findings this round

1. **🟠 Real, self-inflicted — migration drift was re-introduced by the most recent commit.** I ran `python manage.py makemigrations --check --dry-run payments` myself: it reports a pending `AlterField` on `bulkpaymentbatch.request_id` and `prepaymentvalidationrequest.request_id` (the `RegexValidator` tightening from the G2P `RequestID` secondary-finding fix). This **directly contradicts §23's own claim** that `makemigrations --check` was clean "for the first time in this project's history" — adding a validator to a model field always changes its migration state, and this was missed before committing. Non-destructive, but the exact kind of drift this project has flagged as a problem in Consent and Appointments for three rounds running.

2. **🔴 Real defect, empirically confirmed by me — the voucher status-check endpoint doesn't implement its own documented 400 case.** `SPEC_GOVSTACK_PAYMENTS_BB.md` itself (written by a past round of this project's own work) states at line 1437: *"Malformed input (e.g. `voucherserialnumber=\"{}\"`) → `400`."* I wrote a throwaway test and ran it against the actual view: `GET /vouchers/voucherstatuscheck/{}` returns **456** (`"Voucher serial number not found."`), not 400. `VoucherStatusCheckView.get()` (`govstack_views.py:1080-1101`) performs zero format validation on the path segment before handing it to `get_status()` — a malformed value and an unknown-but-well-formed value are indistinguishable to the code, even though the spec document says they should not be. This is a requirement that was written down and never implemented — the reviewing agent independently found this by reading the harness feature file; I independently confirmed it by executing the actual code.

3. **🔴 Real security defect, confirmed by direct code read — the async callback dispatch is an unguarded SSRF sink.** I read `_post_callback()` (`govstack_tasks.py:352-393`) myself: it does `requests.post(url, json=payload, timeout=...)` where `url` is the caller-supplied `X-Callback-URL` header, taken verbatim from `request.headers.get("X-Callback-URL", "")` on `RegisterBeneficiaryView`, `BulkPaymentView`, `PrepaymentValidationView`, and `VoucherPreactivationView` (`govstack_views.py:468, 577, 690, 833`). There is no scheme allowlist, no private/link-local IP denylist, and `requests` follows redirects by default. In harness/default settings mode, `IsTrustedSourceBB` grants access to anonymous callers when no `X-Registering-Institution-ID` header is present — meaning an **unauthenticated** `POST /bulk-payment` (or any of the other 3 endpoints) can drive the Celery worker to POST to an arbitrary internal address (e.g. a cloud metadata endpoint) once the async task fires. This was never checked in any prior round because no prior round's threat model considered the callback dispatch path specifically.

4. **🟠 Real, P2G-scoped — the response contract diverges from the live spec in shape and status code.** Cross-checked against a fresh clone of `api/P2G API YAMLs/`: the live spec returns **HTTP 202** with `{responseCode, reason, requestID}` for `POST /billTransferRequests`, `GET /bills/{billId}`, and `GET /transferRequests/{id}`; CivicOS returns **200** with bespoke response bodies for all three. `GET /bills/{billId}` also requires a `fields=inquiry` query parameter per the live YAML, which `BillInquiryView` never reads. §24's Issue B work verified P2G *authentication* only — the response contract itself was never checked against the live spec, in this round or any prior one.

5. **🟠 Real — `GET /transferRequests/{id}` was given the wrong caller-identity header.** The live `rtpStatusUpdateRequest.yml` mandates `X-billerId` (`required: true, maxLength: 20`) and contains no PayerFI header at all; §24's Issue B applied `IsTrustedPayerFI` (the `X-PayerFI-Id` family) to this endpoint instead. §24.2's own "spelling variants" analysis catalogued 3 PayerFI spellings correctly but did not notice that one of the three endpoints it covered uses a structurally different header entirely.

6. **🟠 Real, previously flagged as out of scope, now more visible — tenant/caller data isolation is still absent, and the new TenantId validation is cosmetic.** `GovStackP2GService.get_bill()`/`get_transfer_request()` filter only by ID; the newly-added `X-Platform-TenantId` validation (secondary finding, §24.4) checks presence/length and then discards the value into an unused local variable in 3 of the 4 views. Any caller whitelisted for *any* PayerFI-Id can read every bill and every other FI's transfer request, regardless of what tenant ID they present. §24.2 explicitly flagged this as a deliberate, out-of-scope follow-up; confirmed still open, and the new tenant header now creates a false appearance of scoping that didn't exist before.

7. **🟡 Coverage gap, unchanged from §24.2's own flag** — the `govstack_bb` rate-limit scope has zero regression test coverage; test settings raise it to an effectively-unlimited rate, so it is never exercised anywhere in 1,697 tests.

8. **🟠 Operational — the harness cannot be pointed at CivicOS as currently deployed.** The upstream harness (`test/openAPI/features/support/helpers/helpers.js`) hardcodes its base URL to `http://localhost:3333/` with no environment override for anything except the healthcheck path; CivicOS mounts its GovStack routes at `/govstack/payments/` and its healthcheck at `/health/`. No proxy config, compose override, or runbook exists anywhere in this repo to reconcile the two. Every "harness-conformant" claim made across every round of this project — for this BB specifically — has therefore been a code-level claim; nothing has ever actually been executed end-to-end against the real harness tooling.

9. *(Minor)* A stale test docstring in `test_govstack_vouchers.py:53,55` still references a withdrawn GAP-7 claim, even though the test body itself asserts the correct (current) behavior.

### Blockers to genuine 🟢
1. Implement the documented 400-on-malformed-input case for `voucherserialnumber` (finding 2) — small, direct fix.
2. Add a scheme/IP allowlist (or at minimum a private/link-local denylist with no-redirect-follow) to `_post_callback()` (finding 3) — this is the highest-priority item, since it's a live security defect reachable without authentication.
3. Commit the pending migration (finding 1) — mechanical.
4. Build a harness runbook (finding 8) and **actually execute the harness** at least once. Until this happens, no conformance claim for this BB is empirical, in either direction.
5. Then: align the P2G response contract and header (findings 4–5), and decide whether to implement real tenant-scoped data isolation or explicitly document the current design as deliberately BB-scoped-only (finding 6).

---

## Consent BB — 🟡 Partial (downgraded from 🟢 — see below)

**Note on documentation:** unlike Payments, Consent has no dedicated `SPEC_*.md` gap ledger — its only tracking documents are a short submission checklist and the Consent section of this report. This absence is itself part of why the findings below survived three prior review rounds: there was no living document forcing each round to re-confirm the prior round's specific claims line by line.

### Confirmed still accurate
**286 tests pass** (0 fail, 2 skipped — reproduced by both the review agent and by me). State machine, append-only audit/revision models (Python-layer `save()`/`delete()` guards), composite indexes, and IDOR-safe owner-scoped querysets are all genuinely solid, as in every prior round.

### NEW findings this round — the harness-failure claim is empirically demonstrated, not inferred

1. **🔴 Certification-blocking, personally re-confirmed — every Consent view requires authentication, and the live harness has no way to authenticate.** I grepped every `permission_classes` declaration in `apps/consent/govstack_views.py` myself: every single one is `[IsAuthenticated, ...]` — there is no Consent-equivalent of Payments' mode-gating flags (`GOVSTACK_REQUIRE_REGISTERED_BB`, `GOVSTACK_VOUCHER_REQUIRE_JWT`, etc.); I confirmed this with a direct grep for any `GOVSTACK_CONSENT_*` setting, which returned nothing. The reviewing agent found that the live `bb-consent` harness (`test/gherkin/features/environment.py`) builds a bare, credential-free `requests.Session()`, and that the live `api/consent-openapi.yaml` has an empty `securitySchemes` block with every `security:` requirement commented out. **Every live-harness call to this BB would receive HTTP 401.** This is the single most consequential finding of this round for Consent — it means the BB, as currently built, cannot pass certification regardless of how correct its business logic is, because the auth model and the spec's auth model are fundamentally incompatible with no reconciliation mechanism.

2. **🔴 Even with authentication, the smoke-test scenario would still fail — a policy ID type mismatch.** The harness's smoke scenario fetches `service/policy/1/`; `ConsentPolicy` is a UUID-keyed model, and `_parse_uuid_param()` rejects the literal `"1"` with HTTP 400 before ever reaching the correct response shape.

3. **🟠 Even with authentication, the data-agreement positive scenario would still fail — a type mismatch, not a value mismatch.** The harness asserts `response["dataAgreement"]["id"] == "1"` (a string); `DataAgreement.id` serializes as the Python int `1`. `1 == "1"` is `False`.

4. **🟠 Confirmed still open, personally re-verified — migration drift, and it includes a real schema change, not just metadata.** I ran `makemigrations --check --dry-run consent` myself: pending `AlterField` on `ConsentRecord.state` (**`max_length` 32 → 30**, a genuine column-width narrowing), `ConsentSignature.verification_type`, and `ConsentWebhook.secret_key`. Flagged as open in round 3; this round adds that the `state` component is not purely cosmetic.

5. **🟠 Confirmed still open — the required-field gap remains 3 fields (`purpose`, `lawfulBasis`, `dpia`), and is now empirically demonstrated**, not just inferred from reading the serializer: `POST /config/data-agreement/` with all three fields blank returns 200.

6. **🟠 Confirmed still open, more precisely scoped — the PIPEDA export is materially incomplete, and now confirmed to fail silently.** `_build_export_payload()` omits every `ConsentRevision` (the tamper-evidence chain the spec centers on), every `ConsentSignature`, webhook subscriptions, and several `ConsentRecord` fields. New this round: three sections of the export are wrapped in bare `except Exception: pass` — a DB error or schema change silently drops an entire category from a citizen's PIPEDA right-of-access export, with no error surfaced to the citizen and no log line.

7. **🟡 Confirmed still open, and independently re-verified against the live spec text** — `serializedHash` is SHA-256; the live spec's field description explicitly says SHA-1. No harness scenario checks the hash algorithm, so this is a spec-fidelity gap, not a harness-failing one — defensible on security grounds, but undocumented anywhere in this repo as a deliberate deviation. Also confirmed still open: no dedicated throttle scope (falls back to global citizen/anon rates), and `test_services.py:321`'s self-skipping test (the only guard for the `grant(revision=...)` fix) plus a second, newly-noticed skip — `ConcurrentFirstGrantTests` is skipped on SQLite, and `config/settings/test.py` uses SQLite, so the DB-level race guard has never actually run in CI.

### Why this is a downgrade, not just new findings
Round 3 rated Consent 🟢 Production-Ready and called it "the first candidate for testing.govstack.global." That verdict was reached by re-fetching the spec but not reading `test/gherkin/`, and without ever probing an unauthenticated request against the actual running code. This round did both, and found that the harness would fail on its very first HTTP call. Under this project's own "never round up" rule, and given that the assessment methodology treats the certification-harness dimension as the most consequential one, 🟡 is the honest tier.

### Blockers to certified
1. **Add harness-mode auth gating** — a Consent-specific flag mirroring the Payments pattern, so a harness deployment can open the read/write surface the live spec actually expects to be open. This is the only blocker that is architectural rather than a small fix, and it is the one that actually prevents certification.
2. Accept an integer-or-UUID policy lookup (or otherwise reconcile the ID scheme), and serialize `dataAgreement.id` as a string.
3. Make `purpose`/`lawfulBasis`/`dpia` required; commit the pending migration; un-skip the two hidden test gaps; replace the silent `except: pass` blocks in the PIPEDA export and widen it to cover Revisions and Signatures.
4. Decide and document the SHA-256-vs-SHA-1 deviation; add a dedicated throttle scope.
5. Create a `SPEC_GOVSTACK_CONSENT_BB.md` gap ledger, matching Payments' §22–§24 pattern — Consent's total absence of a living tracking document is a large part of why these findings survived three rounds undetected.

---

## Appointments / Scheduler BB — 🟡 Partial (unchanged tier, materially worse detail)

**No work has landed on this BB since round 3.** `git log -- apps/appointments/` confirms the last commit touching this app predates every commit made in this session's Payments-focused work. Test count is still exactly 799 — byte-identical across three rounds.

### What's genuinely solid (re-confirmed this round)
All 37 spec paths are routed and correctly named against a fresh clone of the live OpenAPI JSON. State machine correctness is strong: every booking transition runs under `select_for_update()` inside `transaction.atomic()`, writes an audit entry, and correctly reconciles the GovStack `exclusive` lock across reschedule and terminal transitions. `GovStackSchedulerRolePermission` is correctly wired into all 37 views.

### The auth-bypass finding — re-confirmed, unaddressed, and personally re-verified this round
I independently re-read `GovStackSchedulerAuth.authenticate()` (`govstack_auth.py:164-185`) and `GovStackRegisteredBB` (`apps/payments/govstack_models.py:1201-1265`) myself, from scratch:

- **`GovStackRegisteredBB` has exactly four fields** — `bb_id`, `description`, `is_active`, `role` — confirmed by direct read. No secret, token, or credential field exists. The model's own docstring states outright: *"Each row maps a bb_id (the exact string sent in the X-Registering-Institution-ID request header)"* — i.e., the field is self-documented as a public identifier, not a secret, by the same code that Appointments then uses as its production authentication credential.
- In production mode, `resolved_role = bb.role` is granted to any caller who supplies a `request_token` matching an active row's `bb_id` — with no separate secret check at all.
- The seed command's documented production bootstrap (`production.py:207-208` explicitly instructs operators to run it) creates a row with `bb_id="GS-HARNESS"` and `role="admin"` — a credential that is hardcoded, in cleartext, in this public GitHub repository, granting admin across all 37 Scheduler endpoints.
- `GovStackSchedulerRolePermission`, wired into every view, is strictly downstream of this broken authentication step and cannot mitigate it — role-based authorization after a compromised authentication step is a no-op against this specific attack.
- New context this round: `GovStackCitizenAuth.authenticate()` calls `GovStackSchedulerAuth` *first*, meaning a citizen booking their own appointment must also supply valid BB credentials — so any citizen-facing client application must ship a valid `request_token` (i.e., a valid `bb_id`) to every end-user device. This is not an accidental leak; it is a structural requirement of the current design.
- In non-production settings (the default everywhere except `production.py`), `resolved_role` is unconditionally `"admin"` for any request carrying two arbitrary non-empty query parameters — any staging environment running on default/dev settings with real data is fully open with zero credentials.

**Severity: unchanged from round 3 — critical.** This is a live, unauthenticated, remote privilege-escalation-to-admin path against a citizen-PII-bearing government API in the documented production configuration, and it remains completely unaddressed.

### Both previously-open blockers — re-confirmed still open
1. **Migration still not committed** — `makemigrations --check --dry-run appointments` still reports the pending `alert_preference` change, exactly as in round 3.
2. **4 models still unregistered in Django admin** (`GovStackSubscriberProfile`, `GovStackMessage`, `GovStackAffiliation`, `GovStackAlertSchedule`).

### NEW findings this round

1. **🔴 Certification-blocking, personally re-confirmed — the `qry` create-parameter is double-nested, and no spec-literal caller can use any of the 9 create endpoints.** I read `EntityCreateQrySerializer` (`govstack_serializers.py:145-148`) directly: it declares `qry = _EntityQryDetailsSerializer(required=True)`, meaning the *decoded contents* of the `qry` query parameter must themselves contain an outer `"qry"` key — i.e. the actual wire format required is `?qry={"qry":{"details":{...}}}`, doubly-nested. The corresponding modify/list serializers (`EntityModifySerializer`, `EntityListQrySerializer`) do not have this extra wrapping. The live spec's `entity_new_qry` schema is `{"details": {...}}` — the same single-nesting shape the modify/list endpoints correctly expect. **Every one of the 9 `POST /*/new` endpoints therefore rejects the spec-literal request body with a 400 validation error.** None of the 799 existing tests catch this, because they are written against the implementation's own (incorrect) double-nested shape rather than against the spec.

2. **🟠 Real, functional (not just conformance) — the Resource group uses three mutually incompatible `resource_id` formats across its own 5 endpoints**, such that a `create → list → modify` or `list → delete` round-trip is impossible: create returns a plain integer, list returns a prefixed string (`"R-12"`/`"S-7"`), and modify/delete require a plain integer again — so a caller cannot reuse an ID it just received from the list endpoint. StaffProfile-backed resources are consequently readable but permanently unaddressable for write.

3. **🟠 Real — array-typed `*_id` filters reject the spec-literal array format with 400 on 8 of 9 list endpoints.** The live spec types these fields as `array[string]`; the implementation uses scalar `CharField`, so a spec-literal filter value triggers a validation error rather than the narrowed result set the spec describes.

4. **🟡 Documentation-integrity — this project's own spec doc overstates a security control that only half exists.** `SPEC_APPOINTMENTS_BB_GOVSTACK.md` claims the Wave A/C SSRF invariant was satisfied; three `# TODO (Wave F)` comments remain in `govstack_resource.py:147, 216, 230` where Resource `alert_url`/`status_poll_url` are stored with zero validation (the Subscriber side of the same concern was genuinely fixed). Not currently exploitable, because outbound dispatch is separately guarded at send-time (`tasks.py`'s `_is_safe_outbound_url`), but the spec doc's claim of completeness is inaccurate.

5. **🟡 Minor, newly noticed** — all 9 create endpoints return HTTP 201 where the live spec enumerates only 200; the list response envelope and item shape (a wrapper object with flat items) diverges uniformly from the spec's bare-array shape across all 9 resource groups, in a way that is internally documented as a deliberate house convention but was never checked against the live spec until this round; `govstack_bb` throttling keys on IP rather than the calling BB, so a single misbehaving BB cannot be individually rate-limited.

### Blockers to genuine 🟢
1. **Fix the authentication design** (unchanged top priority since round 3) — add a real, hashed, high-entropy credential distinct from `bb_id`, and separately decide whether citizen self-service should require BB credentials at all, since the current design structurally requires distributing that credential to end-user devices.
2. **Fix the `qry` double-nesting on all 9 create endpoints** — newly found this round, and arguably as urgent as the auth fix from a pure functionality standpoint, since it means no spec-literal client can create anything through this BB today.
3. Fix the Resource `resource_id` format inconsistency (a functional break, not a conformance nicety) and the array-filter rejection.
4. Commit the pending migration; register the 4 missing models in admin.
5. Correct the spec doc's SSRF-completeness claim and close the 3 remaining TODOs.

### Risk if deployed today
Unchanged from round 3: any party who has ever observed a registered BB's public identifier can impersonate that BB at its full role tier, including admin, across every Scheduler endpoint. This round adds that even a legitimate, correctly-authenticated caller following the published spec exactly would be unable to create a single Entity, Resource, Subscriber, Event, Appointment, Affiliation, AlertSchedule, Message, or Log entry, because the create-endpoint request format itself doesn't match the spec.

---

## Documents BB — 🟡 Partial (re-scoped again — strong design, but would break on first real deployment)

### GovStack axis — confirmed again this round: no certifiable contract exists upstream
Re-confirmed by a fresh clone of `GovStackWorkingGroup/bb-file-management`: `api/swagger.json`/`api/swagger.yaml` are still 0 bytes, `test/plan.md` is still an unfilled template, and there is still no `test/openAPI/features/` directory anywhere in the upstream repo. `spec/4-key-digital-functionalities.md` remains the only substantive upstream document (SHA-256 checksum management and dual-admin-approval deletion are its two clearest concrete, currently-unimplemented requirements). No certification path exists to prioritize against; the only defensible GovStack-axis work is a written gap analysis against that one document.

### CivicOS-internal axis — 1,066 tests pass, and the design is genuinely strong, but 2 findings are deployment-critical

1. **🔴 Critical, personally confirmed — three runtime dependencies the malware-scanning and file-type-detection pipeline depends on are declared nowhere in this repo.** I grepped every file in `requirements/` myself for `pyclamd`, `python-magic`, and `pikepdf`: zero matches across `base.txt`, `production.txt`, `development.txt`, and `test.txt`. `_scan_with_clamav()` does `import pyclamd` directly inside the Celery task (`tasks.py:447`); on a clean production install this raises `ModuleNotFoundError`, which is caught by the task's broad exception handler, retried 5 times, and then routed to `_quarantine_on_scan_failure()`. **The practical consequence: on an image built strictly from this repo's own requirements files, every single uploaded document ends up quarantined, and no file is ever actually virus-scanned** — the scanning code exists and looks correct, but it can never successfully run. `libmagic1` (needed by `python-magic`) is also absent from the Dockerfile, and no ClamAV service exists in any compose file.

2. **🔴 Critical, confirmed by the reviewing agent via direct code trace across the storage layer — a storage-key prefix mismatch means the scanner, quarantine cleanup, and hard-delete paths are looking at the wrong S3 objects.** `config/settings/production.py` sets `STORAGES["default"]["OPTIONS"]["location"] = "media"`, which makes Django's `default_storage` transparently prepend `media/` to every key. The upload/presign/download code paths use raw `boto3` calls with the *unprefixed* key (`doc.storage_key`); the scan, quarantine-delete, and hard-delete code paths use `default_storage` with the *same* unprefixed key, which then gets the `media/` prefix silently added underneath it. The browser uploads to `documents/quarantine/…`; the scanner looks for `media/documents/quarantine/…`, doesn't find it, and — combined with finding 1 — the document is quarantined regardless. Hard delete would silently delete the wrong (nonexistent) key, leaving the real file behind. This cannot be caught by the existing test suite because it runs against `FileSystemStorage` with no `location` prefix configured at all.

3. **🟠 Real — virus-scan outcomes produce no audit record.** Neither the clean-scan path nor the quarantine path calls `record_event()`; `AuditEventType.THREAT_DETECTED` exists in the audit app's model but is referenced nowhere in this BB. A malware detection currently produces a log line and nothing else — no audit trail entry, and (per the pre-existing, still-open notification-wiring gap) no alert either.

4. **🟠 Real — AWS region is hardcoded to `ca-central-1`** in every raw `boto3` call in this BB, ignoring the `AWS_S3_REGION_NAME` environment variable that the rest of the codebase already reads. Any deployment outside that region would get SigV4 signature mismatches on presigned URLs.

5. **🟠 Real — the HTML download route is missing the post-token-redemption scan-status re-check that the DRF API route has.** A document quarantined or soft-deleted within its token's 5-minute TTL is still downloadable through the legacy HTML redemption view.

6. **🟡 Real, lower severity** — no SHA-256 checksum field or periodic corruption-verification job exists, despite the upstream spec mandating it; no S3 lifecycle policy exists anywhere in the repo, despite a cleanup task's own comment stating one is required to dispose of orphaned pending-upload objects; the admin interface renders the raw single-use access token on its read-only detail page; `scan_engine_result` is shown in an ungated admin fieldset despite this repo's own spec calling for a permission gate.

### Confirmed still accurate from prior rounds
Retention/legal-hold/disposal logic remains the strongest part of this BB — correct precondition re-checks under lock, audit trail inside the same transaction, storage-delete-failure aborts without writing a false audit record. IDOR protection (owner-scoped querysets, 404-not-403 throughout) is real and confirmed by direct testing. The previously-flagged gaps are all reconfirmed still open: encryption at rest is not actually configured (dead KMS code path), and 5 of 8 notification signals remain unwired (scan-clean and quarantine-admin alerting — the two with the clearest operational impact — still have no receiver or template).

### Blockers to genuine 🟢
1. **Pin `pyclamd`, `python-magic`, and `pikepdf` in the appropriate requirements files, add `libmagic1` to the Dockerfile runtime stage, and stand up a ClamAV service** — without this, the scanning pipeline cannot function at all on a clean deployment (finding 1).
2. **Resolve the storage `location="media"` prefix mismatch** — either remove it from `STORAGES.OPTIONS` or make every raw `boto3` call in this BB prefix-aware (finding 2). This must be verified against a real S3 bucket; the test suite cannot catch it.
3. Add the 2 missing `record_event()` calls for scan outcomes (finding 3) — small, direct fix.
4. Thread `AWS_S3_REGION_NAME` through the raw boto3 calls (finding 4); add the missing scan-status re-check to the HTML download route (finding 5).
5. Then: encryption at rest, the 2 highest-impact notification receivers, SHA-256 checksums, and the S3 lifecycle policy.

### Risk if deployed today
Findings 1 and 2 are independently disqualifying for a real deployment — between them, they mean a clean production install would either fail to scan any document at all (defaulting every upload to quarantined) or silently operate on the wrong S3 objects, or both simultaneously. This is a different class of risk than the other three BBs' findings: it is not a harness-conformance gap or even a security defect in the traditional sense, but a packaging/configuration gap that the existing 1,066-test suite structurally cannot detect, because it never runs against real cloud storage.

---

## Certification Priority Order (revised this round)

1. **Consent** — was believed closest to done; this round found the harness-blocking auth-model gap is architectural, not a small fix, so it is no longer clearly the fastest path to a real submission. Still likely the least total effort of the four once the auth-gating flag is added, since everything else is hours-scale.
2. **Payments** — has one real production security defect (the callback SSRF sink) and one documented-but-unimplemented requirement (the 400 case), both small fixes; the P2G response-contract and data-isolation gaps are larger but non-blocking for a first submission. The harness cannot be run against this BB at all today without a proxy shim — building that shim and actually executing the harness once should be treated as its own priority, independent of any code fix, since it is the only way to convert any of this round's "confirmed by reading" findings into "confirmed by execution."
3. **Appointments/Scheduler** — still has the single most severe finding across all four BBs (the auth-bypass-by-design), now joined by a request-format bug that breaks every create endpoint for a spec-literal caller. No work has landed on this BB in three rounds; it should not continue to be deprioritized behind Payments/Consent iteration, since the auth finding is a live production security risk independent of certification timing.
4. **Documents** — no certification path exists upstream regardless of code quality, so GovStack-axis prioritization is moot; but the 2 critical internal-axis findings (undeclared scanner dependencies, storage-key prefix mismatch) mean this BB would malfunction on first real deployment even outside any certification context, and should be treated as urgent on that basis alone.

---

## Cross-BB Integration Gaps

- **`GovStackRegisteredBB.bb_id` is still used as both a public identifier (Payments) and a de facto secret credential (Appointments/Scheduler)** — unchanged from round 3, still fully unaddressed, still the most consequential cross-cutting finding in this codebase.
- **Neither Payments' nor Consent's live GovStack harness has ever been executed against this codebase, in any round of this project.** Every "harness-conformant" claim across every spec document in this repo is a code-reading claim. This round is the first to attempt (and fail, for principled reasons in both cases) to actually run either harness, which is precisely how it surfaced findings — the voucher status-check 400 case, the Consent 401-on-everything problem, the Appointments `qry`-nesting bug — that direct code review alone had missed across multiple prior rounds.
- **Migration drift now exists in three separate apps simultaneously** (`payments`, `consent`, `appointments`) — a `makemigrations --check` CI gate would have caught all three, and its absence has now let drift accumulate independently in every GovStack-touching app in this project.
- Audit trail DB-level immutability (`apps/audit/`, and by the same pattern Consent's `ConsentRevision`/`ConsentAuditEntry`, Appointments' `BookingAuditLog`, and the still-open Documents scan-audit gap) remains Python-only (`raise ValueError`/`PermissionError` in `save()`/`delete()`), with no PostgreSQL trigger or `CheckConstraint` anywhere in the codebase — unchanged across all 4 rounds.
- `X-GovStack-BB-Version` response header is still only injected for Consent's URL prefixes, not Payments or Appointments — unchanged, carried forward from round 2.

---

## Appendix — Round-2 Platform-Wide Findings (still not re-verified since round 2; now 4 days stale)

Carried forward unchanged; flag for a dedicated platform-wide re-verification pass, independent of the 4 GovStack BBs this report focuses on.

### Tier 2 — Core Platform BBs (from 2026-07-22)
| BB | App | Tests (2026-07-22) | Verdict (2026-07-22, unverified since) |
|---|---|---|---|
| Workflows | `apps/workflows/` | 113 | PRODUCTION-READY |
| Citizen Portal | `apps/portal/` | 172 | PRODUCTION-READY |
| Notifications | `apps/notifications/` | 77 | SOLID — SMS delivery absent |
| Dynamic Forms | `apps/forms/` | 103 | PRODUCTION-READY |
| REST API | `apps/api/` | 115 | PRODUCTION-READY |
| Backoffice Admin | `apps/backoffice/` | 121 | PRODUCTION-READY |
| Volunteers | `apps/volunteers/` | 831 | SOLID (audit gap) |
| Reports | `apps/reports/` | 485 | PRODUCTION-READY |
| Authentication | `apps/auth_extension/` | 75 | PRODUCTION-READY |
| Audit Trail | `apps/audit/` | 19 | DB-level immutability gap unchanged |
| Core Utilities | `apps/core/` | 83 | PRODUCTION-READY |
| CMS | `apps/cms/` | 0 | No tests |

### GovStack Platform Requirements Table (as of round 2, 2026-07-25 — not re-checked this round)
| Requirement | Status | Notes |
|---|---|---|
| `/health/` endpoint | ✅ | Checks DB, cache, Stripe; 503 on failure |
| OpenAPI schema | ✅ | `drf-spectacular` at `/api/v1/schema/`, `/api/v1/consent/schema/`. No dedicated schema for `/govstack/payments/` or `/govstack/scheduler/` namespaces. |
| Rate limiting | ✅ | `govstack_bb` scope 100/min on Payments/Appointments BB-to-BB views (now confirmed keying on IP not caller identity — see Appointments findings); Consent correctly uses citizen/anon scopes instead |
| HTTPS enforced | ✅ | `SECURE_SSL_REDIRECT=True`, HSTS 1yr+preload |
| `X-Request-ID` header | ✅ | Applies globally |
| `X-GovStack-BB-Version` header | ⚠️ still open | Only injected for Consent's URL prefixes |
| `ATOMIC_REQUESTS` | ✅ | Correctly exempted on health probe |
| CSP headers | ✅ | `django-csp` with nonces |
| `X-Frame-Options DENY` | ✅ | |
| `SECURE_REFERRER_POLICY` | ✅ | |
| BB-to-BB auth whitelist | ⚠️ | Appointments' variant remains a bypass-by-design, unaddressed across 2 full rounds since discovery |
| CORS policy | ⚠️ | No `django-cors-headers`; acceptable for server-to-server, fails browser-based harness/Swagger cross-origin use |
| Audit log DB-level immutability | ❌ | Unchanged — Python-only guard, now confirmed to also apply to Documents' scan-audit gap |

---

## Verification note

This round was produced by 3 independent Opus-class agents, each fetching live GovStack specs fresh (fresh `git clone`, not cached/memory), re-running the relevant test suites themselves, and — for Payments and Consent — executing live HTTP probes against the running Django stack rather than relying on code inspection alone. Before finalizing this report, I personally re-verified the highest-stakes claims directly, independent of the agents' own citations:

- Ran `makemigrations --check --dry-run` myself against all three affected apps (`payments`, `consent`, `appointments`) and confirmed all three report pending changes.
- Wrote and executed a throwaway Django test hitting the live `voucherstatuscheck` endpoint with a malformed serial (`"{}"`) and confirmed it returns 456, not the spec-documented 400.
- Read `_post_callback()` and its 4 call sites directly and confirmed the SSRF sink is real and unauthenticated in default settings mode.
- Grepped every `permission_classes` declaration in `apps/consent/govstack_views.py` and confirmed all are `IsAuthenticated`-gated with no mode-gating flag anywhere in the app or settings.
- Grepped every requirements file for `pyclamd`/`python-magic`/`pikepdf` and confirmed all three are genuinely absent.
- Read `GovStackRegisteredBB`'s full field list directly and confirmed no secret/credential field exists, re-confirming the Appointments auth-bypass finding from first principles rather than trusting the round-3 citation.
- Read `EntityCreateQrySerializer` directly and confirmed the double-nesting claim against the corresponding modify/list serializers' shapes.

Every finding reported above without an explicit "per the reviewing agent" caveat reflects either my own direct verification (code read, live test execution, or command output) or a specific, cited file:line/command-output the agent captured that I judged consistent with the surrounding evidence — not agent self-report alone.
