# GovStack BB Master Certifiability Report — CivicOS
**Date:** 2026-07-25, round 3 (supersedes the round-2 content previously in this file, which is preserved as historical narrative inside each BB section below — do not treat any tier/status claim above the "ROUND 3" markers as current).
**Method:** 3 parallel deep-review agents, each independently fetching live GovStack specs fresh from GitHub (not from memory or prior reports) and re-running the actual test suites themselves, followed by personal verification of the highest-stakes findings — direct code reads and a fresh upstream `git clone`, not agent self-report — before this synthesis was written. Methodology: `GOVSTACK_BB_READINESS_ASSESSMENT_PROMPT.md` (10 dimensions, 4-tier scale, "never round up").
**Trigger for this round:** the user reported Payments, Consent, and Documents as previously assessed, and Appointments/Scheduler as "just finished," and asked for a fresh, skeptical, deep review to confirm what's actually done and what's next — explicitly not a rubber stamp.

---

## Overall Readiness Summary

| Building Block | Tier | Certification | Open Blockers |
|---|---|---|---|
| **Payments** | 🟡 Partial *(re-downgraded from 🟢 — see below)* | Not run | 3 real (1 severe: unauthenticated P2G; 1 severe: voucher schema length mismatch; 1 real: seed data gaps) |
| **Consent** | 🟢 Production-Ready (unchanged) | Not run | 5 (all hours-scale) + 1 new (concealed untested code path) |
| **Appointments / Scheduler** | 🟡 Partial *(NOT "just finished" — both prior blockers still open, plus 1 new severe finding)* | Not run | 3 (1 severe: auth-bypass-by-design; 2 mechanical, unchanged from round 2) |
| **Documents** (GovStack axis) | 🔴 Not Started, but re-scoped | N/A — real spec text now exists upstream but no test harness | A genuine gap-analysis target now exists (see below) — was previously believed literally empty |
| **Documents** (CivicOS-internal axis) | 🟡 Partial | N/A | 3 unwired notification signals (was reported as 2), plus a new encryption-at-rest gap |

**Headline: the round-2 "Payments is 🟢 Production-Ready" verdict was wrong, and the round-2 "Appointments just needs 2 mechanical fixes" verdict undersold a real security defect.** Both are corrected below with file:line evidence I personally re-verified myself (not just agent citations). Consent remains the strongest BB in this codebase. Documents' GovStack axis has new, real information (a spec document that didn't exist, or wasn't checked, before) but the practical conclusion — no viable harness-based certification path today — is unchanged.

---

## Payments BB — 🟡 Partial (re-downgraded)

### What's genuinely confirmed done (personally verified, not just re-read from the spec)
- **1,633/1,633 tests pass**, `makemigrations --check --dry-run payments` clean. Both reproduced independently this round.
- G2P auth mode-gating (P0) is correct: I re-confirmed `IsTrustedSourceBB` degrades properly and none of the live `g2p_*.js` step files send the auth header.
- The 462/463 redemption disambiguation rule (P1) is quoted correctly against a fresh parse of `mockoon-paymentsbbvoucher.json`.
- The `Gov_Stack_BB` blocklist/allowlist layering (P2) behaves exactly as documented, with an honest "not harness-verified" caveat for the allowlist layer.
- P2G genuinely still has zero harness coverage upstream (re-confirmed: still only 9 `.feature` files, all G2P/voucher).

### NEW findings this round (missed by every prior pass, including the P0–P3 remediation that was believed complete)

1. **🔴 Severe — the harness's own JSON schema requires 16–25 character voucher identifiers; CivicOS emits 6-digit numbers.** I personally re-fetched `test/openAPI/features/support/helpers/helpers.js` from `GovStackWorkingGroup/bb-payments` and confirmed lines 68–79 define the preactivation response schema as:
   ```
   voucher_number:        { type: 'string', minLength: 16, maxLength: 25 }
   voucher_serial_number: { type: 'string', minLength: 16, maxLength: 25 }
   ```
   I then read `apps/payments/govstack_models.py:77-85` myself: `_generate_voucher_serial()` returns `str(secrets.randbelow(900_000) + 100_000)` — always exactly 6 digits. **Every positive preactivation harness scenario that validates the JSON schema would fail**, independent of every other fix already made. This was never checked in P1 (which focused on field *names*, not value *shapes*) and is not mentioned anywhere in `SPEC_GOVSTACK_PAYMENTS_BB.md`. Also per the live spec, `voucher_number` is meant to be a *secret* distinct from the public `voucher_serial_number` — CivicOS returns the same value for both.

2. **🔴 Severe — the entire P2G surface is unauthenticated in every environment, including the money-moving endpoint.** I personally read `apps/payments/govstack_views.py:1010-1183` and confirmed all four P2G views — `BillInquiryView`, `BillTransferRequestView`, `MarkBillPaidView`, `TransferRequestStatusView` — declare `permission_classes = [AllowAnyBB]`, with no mode-gating and no `Gov_Stack_BB` body validation at all. `POST /bills/{id}/mark-paid` marks a government bill paid with zero authentication. P0's fix (mode-gating `IsTrustedSourceBB`) was scoped only to the 5 G2P views and was never extended to P2G — this is a real gap in the P0–P3 plan's scope, not a new regression, but it was never flagged because no prior round's threat model covered P2G.

3. **🟠 Real — seed data doesn't cover 3 harness scenarios the spec itself documents.** `voucher_status_check.feature` requires serial `6001` to be `CONSUMED` (→ 458) and `6002` to be `EXPIRED` (→ 459); `seed_govstack_vouchers.py` seeds neither in those states, so both currently return 456 instead. Separately, seeded serial `6004` is left `PREACTIVATED` but no harness scenario ever activates it before the redemption smoke test expects to redeem it — that scenario would also fail. `SPEC_GOVSTACK_PAYMENTS_BB.md` §13.5 already documents the 6001/6002 requirement; the seed command was simply never updated to match.

4. *(Minor)* `IsTrustedSourceBB`'s docstring inconsistently says "4 G2P endpoints" while listing 5. No upstream commit hash is cited anywhere in the spec doc, which makes future staleness harder to detect.

### Blockers to genuine 🟢
1. Widen `GovStackVoucher.serial_number`/`voucher_number` generation to produce 16–25 character values (or otherwise satisfy the schema), and update every response/test that currently assumes 6 digits. This is likely the single highest-value fix — it affects all 5 voucher scenarios' schema validation, not just one code path.
2. Add real authentication to the 4 P2G views, mirroring the G2P pattern (mode-gated `IsTrustedSourceBB` or equivalent), with a settings flag consistent with the rest of the codebase.
3. Fix `seed_govstack_vouchers` to seed `6001`→CONSUMED, `6002`→EXPIRED (with a past `expiry_date`), and either activate `6004` at seed time or confirm which scenario actually exercises it.
4. Re-run `manage.py test apps.payments` and add regression tests for all of the above before considering this closed again.

### Risk if submitted to the harness today
Voucher preactivation fails on schema validation alone, independent of every other fix. Separately, and more seriously: a real deployment's P2G bill-payment endpoint accepts unauthenticated requests to mark government bills as paid — this is a production security defect, not just a harness-conformance gap.

---

## Consent BB — 🟢 Production-Ready (verdict unchanged, new finding added)

### Confirmed still accurate
286 tests pass (0 fail; this round's re-run correctly reports `skipped=2` — one is a legitimate PostgreSQL-only row-locking test, previously omitted from the reported figure). Spec re-fetched fresh (`bb-consent` HEAD unchanged since the last check). All 5 previously-flagged remaining items are **still open, not regressed**: migration drift (`state`/`verification_type`/`secret_key`), `serializedHash` SHA-256-vs-spec's-SHA-1 (now additionally corroborated by the live OpenAPI spec text and the upstream reference CSV-to-OpenAPI generator, both of which say SHA-1 explicitly), `purpose`/`dpia` wrongly optional, incomplete PIPEDA export, and no dedicated throttle scope.

### NEW findings this round
1. **The required-field gap is 3 fields, not 2.** The live `DataAgreement` schema requires `id`, `version`, `purpose`, `lawfulBasis`, `dpia` — `DataAgreementSerializer` also leaves `lawfulBasis` optional (`serializers.py:241`), a field the round-2 report didn't check.
2. **A test hides an unexercised fix, the same bug class round-2 believed it had already eliminated.** `apps/consent/tests/test_services.py:321-327` — the only test for `grant()` respecting a caller-supplied `revision` — calls `self.skipTest(...)` because its own setup never creates a `DataAgreement` revision to test against. The underlying code path has never actually run in CI. This is the identical failure mode (a silently-skipped test concealing dead coverage) that round-2's own fixes were supposed to have closed out on a different code path.

### Blockers to certified (unchanged, now 6 items)
Same 5 as before, plus: fix `test_services.py`'s skipped revision test so the `grant()` fix it's meant to guard is actually exercised.

---

## Appointments / Scheduler BB — 🟡 Partial (the "just finished" claim does not hold)

### What's genuinely solid (personally spot-checked, not just re-read)
Spec conformance is real: I independently diffed the live `Govstack_scheduler_BB_APIs.json` (fetched fresh, upstream unchanged since the last check) against `govstack_urls.py` — all 37 operations match 1:1. I also checked the 9 endpoint groups' JSON envelope wrapper keys against the fetched spec myself and found no camelCase/snake_case mismatches anywhere, including two easy-to-miss quirks (a capital-`E` `Entity_id` field, and endpoint-specific wrapper key names) that the code honors correctly. 799/799 tests pass — identical to the round-2 count, which itself is telling (see below). Role-based auth (`GovStackSchedulerAuth`/`GovStackSchedulerRolePermission`) is applied to all 37 views with no gaps, and IDOR on citizen appointment access is properly closed via `caller_citizen_id` ownership checks.

Important scoping note: unlike Payments, `bb-scheduler` has **no test harness at all** upstream — `test/plan.md` is an unfilled template, there are no `.feature` files, no JSON test schemas, no fixture data. Spec conformance here can only be checked against the raw OpenAPI JSON, not against harness behavior, which is a materially different (and weaker) form of verification than what's possible for Payments or Consent.

### Confirmed: both round-2 blockers are still open — no work has landed since
1. **Migration still not committed.** `makemigrations --check --dry-run appointments` still reports the pending `alert_preference` `choices=` change. Since the test count (799) is byte-for-byte identical to round-2's figure, this corroborates that no work happened on this BB between the two rounds — contradicting the "just finished" framing.
2. **4 models still unregistered in admin** (`GovStackSubscriberProfile`, `GovStackMessage`, `GovStackAffiliation`, `GovStackAlertSchedule`) — unchanged.

### NEW finding — 🔴 severe, auth-bypass-by-design
I personally read `apps/appointments/govstack_auth.py:164-185` and confirmed: in production mode, `GovStackSchedulerAuth` authenticates a caller by checking whether the `request_token` query parameter matches `GovStackRegisteredBB.bb_id`. I then confirmed `GovStackRegisteredBB` (`apps/payments/govstack_models.py`) has exactly four fields — `bb_id`, `description`, `is_active`, `role` — **no secret or credential field at all**. `bb_id` is, by design, a *public* identifier: it's the same value sent in cleartext as Payments' `X-Registering-Institution-ID` header and `Gov_Stack_BB` body field, and the harness's own fixtures use predictable/known values (`"bb-digital-registries"`, `"GS-HARNESS"`, even the literal string `"Gov_Stack_BB"`). **Scheduler is reusing a public infrastructure identifier as if it were a secret authentication token.** Concretely: `seed_govstack_vouchers.py` creates a `GovStackRegisteredBB` row with `bb_id="GS-HARNESS"` and `role="admin"` — anyone who knows or guesses this publicly-referenced value can authenticate to all 37 Scheduler endpoints as an admin-tier caller, with read access to every citizen's appointments and subscriber PII (name/email/phone) and the ability to cancel any appointment. This is a design flaw, not a wiring bug, and was not caught by the round-2 report or by Wave A–G's own deep-review rounds because none of them checked whether `bb_id` was ever meant to double as a secret.

### Blockers to genuine 🟢
1. Give `GovStackRegisteredBB` (or a new Scheduler-specific model) a real secret/token field, distinct from the public `bb_id`, and validate `request_token` against that instead. This is the highest-priority item — it's a live authentication bypass in production mode, not a harness-conformance nicety.
2. Commit the pending migration (mechanical, ~15 min).
3. Register the 4 missing models in admin (~1-2 hours).

### Risk if deployed today
Any party who has ever seen a registered BB's public identifier (which by design is exchanged in plaintext across multiple other endpoints) can impersonate that BB against every Scheduler endpoint at its full role tier, including admin. This is more severe than anything found in Payments' P2G gap, because it defeats an auth layer that appears, on the surface, to be correctly and consistently applied everywhere.

---

## Documents BB — 🔴 Not Started (GovStack axis, re-scoped) / 🟡 Partial (CivicOS-internal axis)

### GovStack axis — practical conclusion unchanged, but the evidence behind it was wrong
Round 2 said `bb-file-management`'s spec files were "literally 0 bytes." That's **only true of `api/swagger.json`/`api/swagger.yaml`**. I confirmed, via a fresh clone, that `spec/4-key-digital-functionalities.md` is **15KB of real, substantive document-management requirements** — lifecycle/versioning, MoReq2010/OAIS archival concepts, SHA-256 checksum verification, legal hold, dual-approval deletion, ABAC + break-glass access, AES-256-at-rest, GDPR Art. 17, immutable audit logging, WCAG 2.1 AA. **A genuine gap-analysis target exists that was previously missed entirely.** However, the practical certification conclusion doesn't change: there is still no `test/openAPI/features/` directory, no test-data fixtures, and no API schema to validate against — `test/plan.md` remains an unfilled template. So: a real spec to gap-analyze against now exists, but no harness-based certification path exists yet. Recommend a follow-up pass that treats `4-key-digital-functionalities.md` as a genuine target for a written gap analysis (similar to what exists for the other 3 BBs), even without a harness to run against. Also confirmed by an exhaustive org listing: no other upstream repo (`bb-digital-registries`, `bb-wallet`, `bb-cms`, `bb-esignature`, etc.) is a better domain match; "domain-mismatched" for `bb-digital-registries` is a fair characterization (its API is a generic keyed-value CRUD registry, with zero upload/MIME/scan concepts).

### CivicOS-internal axis — confirmed solid, with 2 new findings
1,066 tests pass. PIPEDA-specific protections (IDOR-safe 404s, scan-gating, storage-key non-leakage, IP masking) are real and tested, not just claimed.

**New findings:**
1. **The notification gap is 5 of 8 signals unwired, not "2 of 4" as previously tracked.** Only `document_soft_deleted`, `document_hard_deleted`, and `document_legal_hold_changed` have connected receivers; `document_upload_initiated`, `document_confirmed`, `document_scan_clean`, `document_quarantined`, and `document_version_created` do not. The two with the clearest operational impact — clean-scan citizen notification and quarantine admin alerting — remain silent, meaning a detected-malware event currently has no automatic alert path (the document is still correctly blocked from access; only the *notification* is missing).
2. **🟠 The claimed encryption-at-rest is not actually enforced.** Production settings never set an `SSEKMSKeyId`, which means the code path that would attach server-side encryption parameters to S3 uploads is dead — presigned upload policies are issued with no encryption enforcement, despite the (real, substantive) upstream spec mandating `aws:kms` + customer-managed keys for this class of document sensitivity.

### Blockers
1. GovStack axis: write a gap analysis against `4-key-digital-functionalities.md` even without a harness (new, actionable follow-up); real API-conformance certification remains blocked on GovStack publishing test scaffolding.
2. Internal axis: wire the 2 highest-impact notification receivers (scan-clean, quarantine); fix the S3 encryption-at-rest gap; decide/document intent for the other 3 unwired signals.

---

## Certification Priority Order (revised this round)

1. **Consent** — genuinely closest to done; all remaining items are hours-to-half-day scale and none involve a security defect. First candidate for `testing.govstack.global`.
2. **Payments** — was believed done; now has 2 severe findings (voucher ID schema shape, unauthenticated P2G) plus 1 real seed-data gap. Estimated 2-3 more engineer-days before genuinely ready.
3. **Appointments/Scheduler** — has the single most severe finding in this round (auth-bypass-by-design via public `bb_id` reused as a secret). This should be treated as urgent regardless of harness-submission timing, since it's a live production security defect, not a conformance nicety. No upstream harness exists to even validate against once fixed.
4. **Documents** — internal-axis fixes are cheap and worth doing regardless (notification wiring, S3 encryption); GovStack-axis now has a real spec to analyze against for the first time, which is new, actionable work, but a genuine certification path still doesn't exist.

---

## Cross-BB Integration Gaps

- **`GovStackRegisteredBB.bb_id` is used as BOTH a public identifier (Payments' header/body fields) AND, in Appointments/Scheduler, as if it were a secret credential.** This is the round's most important cross-cutting finding — it means the same model field carries two incompatible trust assumptions depending on which BB reads it. Any future BB that consumes this table needs an explicit decision about which of the two patterns it's following.
- `X-GovStack-BB-Version` response header is still only injected for Consent's URL prefixes, not Payments (`/govstack/payments/`) or Appointments (`/govstack/scheduler/`) — carried forward, unchanged, from round 2.
- Audit trail DB-level immutability (`apps/audit/`, and by the same pattern Consent's `ConsentRevision`/`ConsentAuditEntry` and Appointments' `BookingAuditLog`) remains Python-only (`raise ValueError` in `save()`/`delete()`), with no PostgreSQL trigger or `CheckConstraint` — unchanged from round 2.
- Payments' P0-era auth fix (mode-gated `IsTrustedSourceBB`) was scoped only to G2P; it was never extended to P2G, which is architecturally the same kind of gap Appointments has (an endpoint surface added after the auth pattern was established, without the pattern being re-applied).

---

## Appendix — Round-2 Platform-Wide Findings (still not re-verified this round)

Carried forward unchanged from the previous version of this report; not re-checked in round 3.

### Tier 2 — Core Platform BBs (from 2026-07-22 — stale by 3 days at this point, flag for a future pass)
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
| Rate limiting | ✅ | `govstack_bb` scope 100/min on Payments/Appointments BB-to-BB views; Consent correctly uses citizen/anon scopes instead |
| HTTPS enforced | ✅ | `SECURE_SSL_REDIRECT=True`, HSTS 1yr+preload |
| `X-Request-ID` header | ✅ | Applies globally |
| `X-GovStack-BB-Version` header | ⚠️ still open | Only injected for Consent's URL prefixes |
| `ATOMIC_REQUESTS` | ✅ | Correctly exempted on health probe |
| CSP headers | ✅ | `django-csp` with nonces |
| `X-Frame-Options DENY` | ✅ | |
| `SECURE_REFERRER_POLICY` | ✅ | |
| BB-to-BB auth whitelist | ⚠️ *(re-opened this round)* | Believed fully real as of round 2; round 3 found Appointments' variant is a bypass-by-design (see above) |
| CORS policy | ⚠️ | No `django-cors-headers`; acceptable for server-to-server, fails browser-based harness/Swagger cross-origin use |
| Audit log DB-level immutability | ❌ | Unchanged — Python-only guard |

---

## Verification note

This round was produced by 3 independent agents, each fetching live GovStack specs fresh (fresh `git clone`, not cached/memory) and re-running the relevant test suites themselves. Before finalizing, the 3 most consequential findings — Payments' voucher-ID schema length mismatch, Payments' unauthenticated P2G surface, and Appointments' `bb_id`-as-secret auth bypass — were independently re-confirmed by me directly: a fresh upstream clone and grep for the schema claim, and direct file:line reads of the relevant CivicOS source for the other two. Everything reported above without an explicit "taken on faith" caveat in the underlying agent transcripts reflects either my own direct verification or a specific, cited file:line/command-output the agent captured — not agent self-report alone.
