# CivicOS — Master BB Certifiability Report — GovStack

**Date:** 2026-07-25
**Reviewed by:** 3 parallel deep-review agents (10-dimension methodology per `GOVSTACK_BB_READINESS_ASSESSMENT_PROMPT.md`) + personal verification of the highest-stakes findings
**Codebase:** `/Users/lionel/builders/govstack`
**Supersedes:** `GOVSTACK_READINESS_REPORT_2026-07-22.md` for the four GovStack-spec BBs below. Tier-2 platform BBs (Workflows, Portal, Notifications, Forms, API, Backoffice, Volunteers, Reports, Auth, Audit, Core) were **not** re-reviewed in this pass — their entries are carried forward from 2026-07-22 and flagged as such.

**Method note:** every dimension score below is backed by a fresh spec fetch (where a live GovStack spec exists), a fresh test run, and direct code reading — not a restatement of prior reports. Four of the highest-impact claims (Consent's `serializedSnapshot` type bug, Payments' `AllowAnyBB` permissiveness, Documents' absent GovStack layer, Documents' unwired notification signals) were independently re-verified by me, outside the agents, by reading the exact files myself before this report was written.

---

## Executive Summary

Since the last full master report (2026-07-22), the single biggest change is that **Appointments/Scheduler BB went from ~38% complete with zero GovStack API surface to fully implemented, spec-verified, and 🟢 Production-Ready** — all 37 endpoints across 9 API groups, individually deep-reviewed wave-by-wave, then cross-checked as a complete surface in a final certifiability pass that caught and fixed a genuine certification-blocking regression (a `qry`-wrapper-key bug reintroduced in 3 of 9 groups). This is now the platform's **strongest** GovStack-spec BB.

The picture for the other three is more mixed than a surface read suggests:

- **Consent BB** — previously reported "PRODUCTION-READY," this pass found a real, harness-relevant regression that was missed before: `serializedSnapshot` is returned as a JSON object where the spec requires a string, and detail-route URL converters (`<int:>`/`<uuid:>`) reject malformed IDs with a raw Django 404 instead of the harness's expected JSON 400 — both independently confirmed in code. **Downgraded to 🟡 Partial.**
- **Payments BB** — the G2P layer (beneficiary, bulk-payment, prepayment-validation) is genuinely solid and the 2026-07-22 P0 blocker (missing voucher seed command) is fixed. But the Voucher engine — 5 of 9 harness-tested features — returns response bodies with field names that don't match the harness's own published schema (`voucherNumber` vs. required `voucher_number`, etc.) and is missing several required error codes. **Remains 🟡 Partial**, but the specific blockers have shifted from "voucher seeding" to "voucher response schema."
- **Document Management BB** — this is not one finding but a framing correction: there is **no live GovStack spec that matches this BB's domain** (`bb-file-management`'s spec files are empty; `bb-digital-registries` is a structured civil-registry API, not blob/file storage). CivicOS's internal implementation is excellent (1,066/1,066 tests, all three previously-flagged security items resolved or improved) but sits at 🔴 Not Started on the GovStack axis for a structural reason, not an implementation gap, and at 🟢 Production-Ready on the CivicOS-internal axis with one real open gap (2 of 4 notification triggers unwired).

**Platform-level risks are also mixed.** Three of four previously-flagged permissive BB-to-BB auth classes now have real `GovStackRegisteredBB`-backed whitelists with secure production defaults — but `AllowAnyBB`, which guards money-adjacent voucher preactivation/activation, remains unconditionally `return True` with no mode switch at all. Audit-log DB-level immutability is completely unchanged (still Python-only guard, 19 tests, no PostgreSQL trigger).

**Bottom line on "which are done":** none of the four GovStack-spec BBs has an actual passing harness run — that remains true across the board and is the one gap no further code review can close. Of the four, **Appointments is the closest to submission-ready**, Payments needs a contained 2–3 day voucher-schema fix, Consent needs a similarly contained ~3–4 day fix, and Documents should be explicitly reframed as CivicOS-internal-only rather than pursued against a mismatched GovStack spec.

---

## BB Inventory & Status

### Tier 1 — GovStack Specification BBs (fresh 10-dimension review this pass)

#### 1. Appointments / Scheduler BB — `apps/appointments/`
**Spec:** `bb-scheduler` — `api/Govstack_scheduler_BB_APIs.json` (confirmed live, fetched fresh)
**Verdict: 🟢 PRODUCTION-READY (GovStack code-complete) — harness not yet run**

All 37 endpoints across 9 API groups (Entity, Resource, Affiliation, Subscriber, Event, Appointment, AlertSchedule, Message, Log) are implemented, wired, and independently re-verified against the live spec this pass — not just transcribed from `SPEC_APPOINTMENTS_BB_GOVSTACK.md`. Spot-checked wrapper keys for Resource, Message, Affiliation, Subscriber, Log, and AlertSchedule directly against the fetched spec's `components.schemas`: all correct, including the two genuine upstream spec inconsistencies (`log_filter.category` vs. `log_details.log_category`; `log_details_required.logger_category` gating `logger_role`) faithfully preserved rather than "fixed." **799/799 tests passing** (re-run independently this pass). Two-tier auth/role architecture (`GovStackSchedulerAuth` for BB-to-BB, `GovStackCitizenAuth` composite for the 3 citizen-facing endpoints) confirmed real, not just documented. `GOVSTACK_SCHEDULER_REQUIRE_TOKEN` correctly defaults `True` in `config/settings/production.py`.

**Open items:**
- One pending, uncommitted migration (`govstacksubscriberprofile.alert_preference`) — cosmetic, causes `makemigrations --check` to fail. Under 1 hour to fix.
- PII-in-`log_data` is a documented assumption, not a code-enforced guarantee — a misbehaving BB-to-BB caller could write citizen PII into an audit log field.
- Two documented, deliberately-deferred policy decisions (not defects): `SubscriberListDetailsView` (PII-bearing) is gated at a lower trust tier than non-PII `EntityListDetailsView`; `BookingAuditLog.booking`/`GovStackAlertSchedule.slot` use `on_delete=CASCADE` while their models' own `.delete()` overrides encode compliance-critical invariants Django's cascade collector would bypass (not currently exploitable — no code path hard-deletes a `Booking`).
- No actual `testing.govstack.global` harness run — the only remaining path to ✅ Certified.

**Blockers to ✅ Certified:** (1) commit the pending migration, (2) submit to the harness, (3) resolve the two documented policy decisions above (optional, not harness-blocking).

---

#### 2. GovStack Consent BB — `apps/consent/`
**Spec:** `bb-consent` — `api/consent-openapi.yaml`, v1.1.0-rc1 (confirmed live, fetched fresh, same URL as before)
**Verdict: 🟡 PARTIAL — downgraded from the 2026-07-22 report's "PRODUCTION-READY"**

All 32 spec-defined endpoints exist, route correctly, and match HTTP methods/status codes (200 on create, matching the spec, not 201). Auth/authz (`IsConsentAdminUser`/`IsAuditorUser`/`IsConsumerUser`), audit trail (`ConsentRevision`/`ConsentAuditEntry` both raise on mutation), RTBF, and security hardening (encrypted webhook secrets, single-use download tokens, no PII in logs) all confirmed solid. **252/252 tests passing** (re-run independently this pass).

**Two real, previously-undetected regressions confirmed by direct code read (not just agent claim):**
- **`serializedSnapshot` type mismatch (H-03, previously reported as an "open pending issue" — now confirmed still genuinely broken, not fixed):** the spec requires `type: string`; `apps/consent/serializers.py:158` is `serializers.JSONField(source="serialized_snapshot", read_only=True)` — returns a nested JSON object. Affects every response containing a `revision` key (policy, data-agreement, and every consent-record grant/withdraw/signature flow). A strict GovStack-conformant client validating against the published schema would reject these responses.
- **Malformed-ID routes return the wrong error shape:** the live upstream Gherkin suite's own `data_agreement.feature` explicitly expects HTTP 400 for a malformed ID (e.g. `"invalid_id"`). CivicOS's routes use `<int:data_agreement_id>` (and equivalent `<int:>`/`<uuid:>` converters elsewhere) — a non-matching ID doesn't route at all, so Django returns its generic HTML 404, not the harness's expected JSON 400. This is a concrete, evidence-based prediction of a harness failure, not a hypothetical.

**Also confirmed still-unresolved from 2026-07-22:** pagination envelope inconsistency (6 of 13 list endpoints include `"total"`, 7 don't — spec doesn't require it either way, but it's a real client-facing inconsistency).

**New finding this pass:** no DB-level uniqueness constraint guarantees a single `is_current=True` `ConsentRecord` row per citizen/category — the `select_for_update()` in `grant()` only locks *existing* signed/granted rows, so a citizen's very first grant to a category has nothing to lock, and two concurrent first-grant requests could theoretically both `.create()` with `is_current=True`. Untested concurrency path.

**Blockers to 🟢 Production-Ready:** (1) fix `serializedSnapshot` to serialize as a string, updating ~10 dependent test assertions (~1–2 hrs); (2) fix malformed-ID routing to return JSON 400 (~1 day, touches every detail route); (3) standardize the pagination envelope (~2–3 hrs); (4) add a DB-level partial unique constraint on `is_current` (~3–4 hrs + migration + Postgres-backed concurrency test); (5) add webhook-dispatch tests (HMAC correctness, retry behavior — currently zero coverage of `dispatch_consent_webhook` itself). **Total: ~3–4 engineer-days.**

---

#### 3. GovStack Payments BB — `apps/payments/`
**Spec:** `bb-payments` — no single `api/openapi.yaml` exists (confirmed 404); real definitions live in `api/G2P API YAMLs/`, `api/Voucher API YAMLs/`, `api/P2G API YAMLs/`, cross-checked against the harness's own machine-checked schema `test/openAPI/Payment_BB_Voucher_api_test.json` and 9 Gherkin feature files, all fetched fresh this pass.
**Verdict: 🟡 PARTIAL — same tier as 2026-07-22, but the specific blocker has moved**

The 2026-07-22 P0 blocker (`seed_govstack_vouchers.py` didn't exist) is **fixed** — the command exists and seeds the exact serials the internal spec cites, idempotently. The previously-flagged "no Celery callback delivery" high risk is **fixed** — `govstack_tasks.py` fully implements batch processing and callback dispatch via `transaction.on_commit()`. **457 GovStack-specific tests passing** (1,572 total for the whole payments app, including out-of-scope Stripe tests) — re-run independently this pass.

**New, more significant blocker found this pass — Voucher engine schema mismatch:** the harness's own published OpenAPI schema requires specific field names CivicOS does not return. Preactivation: harness wants `{voucher_number, voucher_serial_number, expiry_date_time}`; code returns `{voucherNumber, voucherSerialNumber, voucherGroup, expiryDate}`. Activation/redemption: harness wants `{result_status}`; code returns entirely different camelCase fields with no `result_status` at all. GET status: harness wants `{voucher_status: <string enum>, voucher_amount: <string>}`; code returns `{status: <int>, value}`. This means **5 of the 9 harness-certified features (all Voucher features) would very likely fail schema validation on an actual harness run**, despite passing 100% of CivicOS's own tests — because CivicOS's own voucher tests assert against the code's own (non-conformant) field names, not the harness's.

Also missing: 3 of the harness's 12 domain error codes (458 voucher-already-used, 459 voucher-expired, 461/462 invalid-voucher-number / insufficient-funds on redemption) — `get_status()` never checks `expiry_date` against `timezone.now()`, and the redemption path's `override`/`voucher_secret_number` params are explicitly marked "not used" in the code's own comments.

**Confirmed independently by me:** `AllowAnyBB.has_permission()` (`apps/payments/govstack_auth.py:149-150`) is literally `return True` with no settings-driven mode switch, unlike its three sibling permission classes (`IsTrustedSourceBB`, `HasVoucherJWT`, `GovStackSchedulerAuth`), which all now correctly whitelist against `GovStackRegisteredBB` in production. This guards voucher preactivation/activation and the G2P bulk-payment/prepayment-validation endpoints — money-adjacent surfaces that remain permanently open in every environment, by design per the class's own docstring ("the harness does not send a pre-registered auth token"). This is an intentional harness-compatibility choice documented in the code, not an oversight — but it means this permission class provides no security boundary at all, and that tradeoff should be explicitly reviewed before production go-live.

**New operational finding:** `seed_govstack_vouchers` tags all seeded vouchers `issuing_bb="GS-HARNESS"`, but the live harness feature files use `Gov_Stack_BB="bb-digital-registries"` for activation/redemption test scenarios — if `GOVSTACK_REQUIRE_REGISTERED_BB=True` is active during the actual harness run, those calls would be rejected by the whitelist since `"bb-digital-registries"` is never seeded as a registered BB.

**Blockers to 🟢 Production-Ready:** (1) rewrite all 5 voucher response bodies to match the harness's exact field names/types (~1–2 days); (2) implement the 4 missing error codes including real expiry-checking logic (~folded into the above); (3) seed/whitelist the correct harness BB IDs (~1–2 hrs); (4) resolve `makemigrations --check` drift — a cosmetic but currently-failing migration for `verbose_name`/choices changes (~under 1 hr); (5) verify against actual harness behavior whether `X-Registering-Institution-ID` is really required on G2P beneficiary calls (unverifiable from the public repo alone — code currently rejects any call missing it). **Total: ~2–3 engineer-days**, contained mostly to `govstack_views.py`/`govstack_services.py`/`govstack_exceptions.py`.

---

#### 4. Document Management BB — `apps/documents/`
**Spec target:** unresolved — **no live GovStack spec matches this BB's domain.** `bb-file-management`'s spec files (`api/swagger.json`, `api/swagger.yaml`) are confirmed **0-byte, empty scaffolding** — a genuine dead end, not a fetch failure. `bb-digital-registries` (mature, 310 commits, real spec) turns out on direct inspection to be a **generic structured civil-registry CRUD API** (`/data/{registryName}/{version}/...` for record types like `MCC`/`Child`/`Caretaker`, birth-certificate-style records) where documents are only a sub-field (a `url` inside a record), not the subject of the API — a fundamentally different domain from CivicOS's controlled blob-storage/virus-scanning/retention system.

**Verdict on GovStack axis: 🔴 NOT STARTED — for a structural, spec-availability reason, not an implementation defect.** Confirmed directly: `apps/documents/` has no `govstack_views.py`/`govstack_urls.py` at all.

**Verdict on CivicOS-internal axis (a genuinely different metric — see `GOVSTACK_BB_HONEST_READINESS_ASSESSMENT.md`'s framing): 🟢 PRODUCTION-READY.** All three items flagged open in the 2026-07-22 report were checked directly:
- **Encrypted/password-protected file rejection — FIXED.** `_check_pdf_encryption()` and `_check_zip_bomb()`'s encrypted-entry guard are both wired into `confirm_upload()`.
- **DRF REST API — FIXED, and exceeds the original spec.** `apps/api/documents/` exists with 93 dedicated tests, including several endpoints (`GET /quarantined/`, `POST /{doc_id}/new-version/`, a renamed `POST /request-download/`) that CivicOS's own task history had left in an ambiguous "in progress" state — directly verified as complete and wired.
- **Missing notification templates — PARTIALLY FIXED, and I independently confirmed the remaining gap myself.** The `document_expiring_soon` templates exist and are used. But `document_scan_clean` and `document_quarantined` signals (confirmed via `grep`: both fired with `.send_robust()` in `apps/documents/tasks.py`) have **zero registered receivers anywhere in the codebase** — confirmed by searching for `@receiver` decorators against either signal name and finding none. Citizens are not notified when a scan completes; admins are not emailed on quarantine (detection currently depends on someone checking `GET /quarantined/`).

**1,066/1,066 tests passing** (re-run independently this pass, up from prior counts, confirming continued work).

**Recommendation, not a code blocker:** make an explicit documented decision that Document Management should be framed as CivicOS-internal-only rather than pursued as a GovStack-certifiable BB — repeating the "excellent internal BB, wrongly labeled as GovStack-ready" confusion that `GOVSTACK_BB_HONEST_READINESS_ASSESSMENT.md` already corrected once (2026-07-17) risks recurring if this isn't stated plainly in `SPEC_DOCUMENT_MANAGEMENT_BB.md`.

**Blockers to close the one real remaining internal gap:** wire `document_scan_clean`/`document_quarantined` receivers to actual email dispatch, following the existing `document_expiring_soon` pattern exactly (~4–8 hrs).

---

### Tier 2 — Core Platform BBs (carried forward from 2026-07-22 — NOT re-verified in this pass)

These BBs were not in scope for this pass's 3 agents (Consent, Payments, Documents, Appointments only, per the user's explicit request). Their tier assignments below are unchanged from the last full report and should not be treated as freshly re-verified — flag for a future pass if a status check is needed.

| BB | App | Tests (2026-07-22) | Verdict (2026-07-22, unverified this pass) |
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
| Audit Trail | `apps/audit/` | 19 | **Confirmed still accurate this pass** — see Cross-Cutting Findings below; DB-level immutability gap unchanged. |
| Core Utilities | `apps/core/` | 83 | PRODUCTION-READY |
| CMS | `apps/cms/` | 0 | No tests |

---

## Cross-Cutting Platform Findings (re-verified this pass)

### GovStack API layer inventory (fresh check)
```
apps/appointments/govstack_urls.py + govstack_views.py
apps/consent/govstack_urls.py + govstack_views.py
apps/payments/govstack_urls.py + govstack_views.py
```
Exactly 3 of the platform's apps have a real GovStack-spec API surface. `apps/documents` confirmed to have none (see above) — this is the authoritative confirmation for the whole report, not an assumption.

### BB-to-BB auth robustness — partially resolved since 2026-07-22

| Class | File | 2026-07-22 finding | Current state (confirmed) |
|---|---|---|---|
| `IsTrustedSourceBB` | `apps/payments/govstack_auth.py` | Accepted any non-empty header | **Fixed** — whitelist-backed via `GovStackRegisteredBB`, `GOVSTACK_REQUIRE_REGISTERED_BB` defaults `True` in production |
| `HasVoucherJWT` | `apps/payments/govstack_auth.py` | `GOVSTACK_VOUCHER_REQUIRE_JWT` not set in prod | **Fixed** — defaults `True` in `production.py` |
| `GovStackSchedulerAuth` | `apps/appointments/govstack_auth.py` | N/A (didn't exist) | **New, secure by default** — same whitelist pattern, `GOVSTACK_SCHEDULER_REQUIRE_TOKEN` defaults `True` |
| `AllowAnyBB` | `apps/payments/govstack_auth.py` | Unconditionally `True` | **Confirmed still unconditionally `True`** — no mode switch at all, unlike its 3 siblings. Guards voucher preactivation/activation and G2P bulk-payment/prepayment-validation. Documented in-code as an intentional harness-compatibility tradeoff, not an oversight — but it remains a real production go-live risk that needs an explicit decision, not a silent carry-forward. |

**Net assessment:** the platform's original CRITICAL finding (Risk 1, 2026-07-22) is now **partially resolved** — 3 of 4 flagged permission classes are fixed; the 4th, guarding money-adjacent endpoints, is unchanged.

### Audit trail DB-level immutability — unchanged
`apps/audit/models.py`'s `AuditLogEntry.save()`/`delete()` still enforce immutability only via Python-level `raise ValueError`; the module's own docstring still says a PostgreSQL trigger is a "future" addition. No trigger, `CheckConstraint`, or equivalent found in any migration. Still 19 tests. `QuerySet.update()`/`QuerySet.delete()` still bypass the guard entirely. (Consent's own `ConsentRevision`/`ConsentAuditEntry` share this same architecture — consistent platform-wide, not a regression specific to any one BB.)

### New regression found this pass, missed by 2026-07-22
`GovStackHeaderMiddleware` injects the `X-GovStack-BB-Version` header only for requests matching Consent's URL prefixes (`/api/v1/consent/config/`, `/service/`, `/audit/`). Payments (`/govstack/payments/`) and Appointments (`/govstack/scheduler/`) responses never receive this header — the 2026-07-22 report marked this requirement ✅ platform-wide but had only actually checked Consent.

### GovStack Platform Requirements Table (re-verified 2026-07-25)

| Requirement | Status | Notes |
|---|---|---|
| `/health/` endpoint | ✅ | Checks DB, cache, Stripe; 503 on failure |
| OpenAPI schema | ✅ | `drf-spectacular` at `/api/v1/schema/`, `/api/v1/consent/schema/`. No dedicated schema for `/govstack/payments/` or `/govstack/scheduler/` namespaces. |
| Rate limiting | ✅ | `govstack_bb` scope 100/min on Payments/Appointments BB-to-BB views; Consent correctly uses citizen/anon scopes instead (principal-authenticated) |
| HTTPS enforced | ✅ | `SECURE_SSL_REDIRECT=True`, HSTS 1yr+preload |
| `X-Request-ID` header | ✅ | Applies globally |
| `X-GovStack-BB-Version` header | ⚠️ | **Regression found** — only injected for Consent's URL prefixes, not Payments/Appointments |
| `ATOMIC_REQUESTS` | ✅ | Correctly exempted on health probe |
| CSP headers | ✅ | `django-csp` with nonces |
| `X-Frame-Options DENY` | ✅ | |
| `SECURE_REFERRER_POLICY` | ✅ | |
| BB-to-BB auth whitelist | ⚠️ | 3 of 4 permission classes now real; `AllowAnyBB` still fully open (see above) |
| CORS policy | ⚠️ | Unchanged — no `django-cors-headers`; acceptable for server-to-server, fails browser-based harness/Swagger cross-origin use |
| Audit log DB-level immutability | ❌ | Unchanged — Python-only guard |

---

## Top Platform-Level Certification Risks (updated)

### Risk 1 — MEDIUM (downgraded from CRITICAL): `AllowAnyBB` remains fully open
Guards voucher preactivation/activation and G2P bulk-payment/prepayment-validation. Documented as an intentional harness-compatibility choice, but provides zero security boundary. Needs an explicit go-live decision (accept the risk with compensating controls, or add a mode switch mirroring its 3 siblings) before production deployment — not silently carried forward again.

### Risk 2 — HIGH (unchanged): Voucher response schema does not match the GovStack harness contract
5 of 9 Payments harness features would very likely fail on an actual harness run due to field-name mismatches, independent of the underlying business logic being correct. This is the single highest-value fix across all four BBs reviewed this pass — contained, well-understood, ~1–2 days.

### Risk 3 — HIGH (new, this pass): Consent BB has two real, previously-undetected harness-relevant defects
`serializedSnapshot` type mismatch and malformed-ID routing both directly contradict the live spec/harness expectations. The prior "PRODUCTION-READY" verdict was not supported by evidence that actually checked these two things.

### Risk 4 — MEDIUM (unchanged): Audit log immutability not enforced at the database level
Still true platform-wide (Consent, core `apps.audit`, and by the same pattern, Appointments' `BookingAuditLog`). A PostgreSQL trigger migration remains the correct fix and has not been started.

### Risk 5 — LOW (new, this pass): Document Management notification gap
2 of 4 designed notification triggers (scan-clean, quarantine) are unwired — a UX/operational gap, not a security defect, but worth closing given how close the rest of the BB is to complete.

### Risk 6 — clarified, not new: Document Management BB has no viable GovStack certification path
This is a spec-availability fact (empty `bb-file-management` spec, domain-mismatched `bb-digital-registries`), not a CivicOS implementation gap. Recommend explicitly documenting this rather than leaving it ambiguous.

---

## Prioritized Action Plan

### P0 — Highest-value, most contained fixes (2–4 days total across both)
1. **Payments:** rewrite the 5 voucher response bodies to match the harness's exact field names (`voucher_number`, `result_status`, `voucher_status`, `voucher_amount`, `message`) and implement the 4 missing error codes (458/459/461/462), including real expiry-date checking. (~1–2 days)
2. **Consent:** fix `serializedSnapshot` to serialize as a JSON string per spec; fix malformed-ID routing to return a JSON 400 instead of a raw Django 404. (~1–2 days combined)

### P1 — Contained fixes, before any harness submission
3. **Payments:** align `seed_govstack_vouchers`'s registered-BB IDs with what the live harness feature files actually use (`bb-digital-registries` and others), and resolve the `makemigrations --check` drift. (~half day)
4. **Consent:** standardize list-endpoint pagination envelopes; add a DB-level partial-unique constraint on `ConsentRecord.is_current`; add webhook-dispatch test coverage. (~1–1.5 days)
5. **Appointments:** commit the one pending migration (`alert_preference`). (~under 1 hour)
6. **Platform:** extend `GovStackHeaderMiddleware`'s prefix list to cover `/govstack/payments/` and `/govstack/scheduler/`. (~under 1 hour)

### P2 — Before production go-live (not harness-blocking, but real risk)
7. Make an explicit decision on `AllowAnyBB`'s permissiveness — accept with compensating controls, or add a settings-gated mode switch matching its 3 siblings.
8. Add a PostgreSQL trigger (or equivalent) to enforce `AuditLogEntry`/`ConsentRevision`/`ConsentAuditEntry`/`BookingAuditLog` immutability at the DB level, not just in Python `save()`/`delete()` overrides.
9. Wire `document_scan_clean`/`document_quarantined` signal receivers to real email dispatch (Documents BB).

### P3 — Documentation / framing corrections (no code change)
10. State explicitly in `SPEC_DOCUMENT_MANAGEMENT_BB.md` that Document Management BB has no viable GovStack certification path given current upstream spec availability, and should be tracked as CivicOS-internal-only.

### P4 — Submission readiness
11. Once P0/P1 items are closed for a given BB, submit to `testing.govstack.global`. **Appointments is closest to submission-ready today** (only the migration-commit item stands between it and a harness run); Payments and Consent need their respective P0 fixes first.

---

## Summary Table

| Building Block | GovStack Spec | Live Spec Confirmed? | Tests (this pass) | GovStack Tier | Blockers to next tier |
|---|---|---|---|---|---|
| **Appointments / Scheduler** | `bb-scheduler` | ✅ Yes | 799 | 🟢 Production-Ready | 1 migration commit, then harness submission |
| **GovStack Consent** | `bb-consent` | ✅ Yes | 252 | 🟡 Partial (downgraded from 🟢) | 5 items, ~3–4 days |
| **GovStack Payments** | `bb-payments` | ✅ Yes (3-part API dir, not single file) | 457 (GovStack subset) | 🟡 Partial | 5 items, ~2–3 days (mostly voucher schema) |
| **Document Management** | `bb-file-management` / `bb-digital-registries` | ❌ No usable/matching spec | 1,066 | 🔴 Not Started (GovStack axis) / 🟢 Production-Ready (CivicOS-internal axis) | Not applicable on GovStack axis; 1 internal item (notifications) |

**Certified BBs (documented passing harness run): zero.** This is unchanged from every prior report and remains the single fact that determines actual GovStack certification status regardless of how much code-level conformance work is done.

---

*This report was produced by 3 parallel deep-review agents, each fetching live GovStack specs fresh and running the actual test suite, following the 10-dimension methodology in `GOVSTACK_BB_READINESS_ASSESSMENT_PROMPT.md`. Four of the highest-impact findings were independently re-verified by direct code inspection before this report was finalized. Tier-2 platform BBs were not re-scanned this pass; see `GOVSTACK_READINESS_REPORT_2026-07-22.md` for their last full assessment.*
