# GovStack BB Master Certifiability Report — CivicOS
**Date:** 2026-07-25 (v2 — supersedes `GOVSTACK_READINESS_REPORT_2026-07-25.md` draft and the 2026-07-22 report)
**Method:** 3 parallel deep-review agents, each fetching live GovStack specs fresh from GitHub and running the actual test suite, followed by personal verification of the highest-stakes findings (direct code reads, not agent self-report) before this synthesis was written. Methodology: `GOVSTACK_BB_READINESS_ASSESSMENT_PROMPT.md` (10 dimensions, 4-tier scale, "never round up").

---

## Overall Readiness Summary

| Building Block | Tier | Certification | Open Blockers |
|---|---|---|---|
| **Consent** | 🟢 Production-Ready | Not run (harness) | 5 (all hours-scale) |
| **Payments** | 🟢 Production-Ready *(updated later same day — see note below)* | Not run | 0 code blockers; live harness run still pending |
| **Appointments / Scheduler** | 🟡 Partial | Not run | 2 (both mechanical, <1 day) |
| **Documents** (GovStack axis) | 🔴 Not Started | N/A — no usable upstream spec exists | Structural, not owned by this repo |
| **Documents** (CivicOS-internal axis) | 🟡 Partial | N/A | 2 (unwired notification signals) |

**Headline change since the last report:** Consent has moved from 🟡 Partial (2026-07-22) → 🟢 Production-Ready today, following the two rounds of fixes closed out this session (commits `770a8d7`, `9dd29c1`). Payments has since ALSO moved to 🟢 Production-Ready, later the same day this report was written — the 7 blockers listed below in the original Payments section (unauthenticated bulk-payment, voucher schema mismatch, missing error codes, no Gov_Stack_BB whitelist, wrong seed data, migration drift, stale docstring) were all closed via a dedicated P0–P3 remediation plan run after this report. **The Payments section below is preserved as the historical record of what that plan started from; it is not current.** The authoritative, up-to-date Payments status now lives entirely in `SPEC_GOVSTACK_PAYMENTS_BB.md` (§22 GAP list, §23 completion log) — see the update note at the top of that section below.

---

## Consent BB — 🟢 Production-Ready

### Verdict
All 32 GovStack spec endpoints (spec re-fetched fresh, `bb-consent` v1.1.0-rc1) are implemented and field-correct on every dimension that was previously broken. 286 tests pass clean (`manage.py test apps.consent`, re-run independently). Service layer is atomic, audited, and append-only throughout. No blocker found this round is more than an hours-scale fix — this is a materially different result from every other BB reviewed.

### What's newly confirmed fixed (from this session's round 1 + round 2 work)
- `serializedSnapshot` is a JSON **string** (not object), matching spec type.
- `serialized_snapshot` storage now matches the spec's literal envelope shape (`objectData`/`schemaName`/`objectId`/`signedWithoutObjectId`/`timestamp`/`authorizedByIndividual`/`authorizedByOther`) — verified via 3 new tests that read the real model field and the real HTTP response, not a mock.
- Malformed-ID routing (`<uuid:>`/`<int:>` → `<str:>` + validation) returns 400, not a raw Django 404, on all 16 path-ID-consuming views — including a permanent regression test pinned to the literal upstream harness Gherkin values (`"invalid_id"`, `"123!@#"`).
- `grant()`'s `is_current` race-recovery logic is narrowed to the specific DB constraint and, critically, now has a test that actually executes the recovery branch on SQLite (previously the only such test was silently skipped on this project's own test backend, meaning "279 passing" concealed an unexercised code path).

### New findings from this fresh pass (not previously known)
1. **`serializedHash` uses SHA-256; the live spec text says SHA-1** (`apps/consent/models.py:371-373`, `ConsentRevision._compute_hash()`). Not exploitable, but a harness that checks hash length (40 vs. 64 hex chars) would flag it. Needs an explicit decision: match the spec literally, or document the deviation.
2. **`DataAgreementSerializer.purpose`/`.dpia` are wrongly optional** (`serializers.py:241,244`) despite the spec listing `purpose`, `lawfulBasis`, `dpia` as required fields on DataAgreement — lets a DataAgreement be created with blank purpose/DPIA text.
3. **PIPEDA data export is incomplete** — `_build_export_payload()` (`tasks.py:460-540`) only exports `ConsentRecord` fields; it omits `ConsentSignature` and `ConsentRevision` data, so a citizen's right-of-access export doesn't include the cryptographic evidence of their own consent.
4. **Uncommitted migration drift**, personally re-confirmed: `makemigrations consent --check --dry-run` detects an unmigrated `AlterField` on `ConsentRecord.state`, `ConsentSignature.verification_type`, and `ConsentWebhook.secret_key`. This is pre-existing (flagged, never resolved, in migration 0017's own docstring) — not introduced by this session's fixes, but still open.
5. No Consent-specific throttle scope — config/audit views fall back to generic citizen/anon throttle rates rather than the `govstack_bb` scope already defined in settings and used by other BBs.

### Blockers to ✅ Certified
1. Run `makemigrations consent`, review, and commit the resulting migration (state/verification_type/secret_key drift). *Under 1 hour.*
2. Decide SHA-1 vs. SHA-256 for `serializedHash` and document or fix. *Under 1 hour.*
3. Make `purpose`/`dpia` actually required in `DataAgreementSerializer`. *Under 1 hour.*
4. Extend the PIPEDA export to include signatures + revisions, with a test. *~half day.*
5. Submit to `testing.govstack.global` for an actual harness run — this is the only remaining dimension that cannot be self-certified.

### Cross-cutting platform assessment (reviewed alongside Consent)
Everything checked here **passed**: shared error envelope (`apps/api/exceptions.py`), rate limiting (`apps/api/throttling.py`), BB URL mounting, `EncryptedCharField` (Fernet, key-rotation-capable), the platform-wide hash-chained `AuditLogEntry` append-only log, Celery Beat schedule, `GOVSTACK_REQUIRE_REGISTERED_BB`/`GOVSTACK_VOUCHER_REQUIRE_JWT` defaulting safely to `True` in production with no hardcoded bypass, and general Django security hardening (`DEBUG`, `ALLOWED_HOSTS`, HSTS, CSRF, CSP, Sentry PII filtering). One informational note: no CORS configuration exists anywhere — appears intentional (same-origin architecture) but worth confirming explicitly if any BB needs cross-origin browser calls from a harness client.

---

## Payments BB — 🟡 Partial *(as of this report; see update below)*

> **UPDATE, later same day (2026-07-25):** every blocker in this section was resolved via a dedicated P0–P3 remediation plan run immediately after this report was written, each phase implemented and independently re-verified against the live GovStack harness source, then personally verified before commit. Payments BB is now 🟢 Production-Ready. This section is kept as-is below for the historical record of what the plan started from — do not treat it as current. For the real, up-to-date status, findings, and full commit history, see `SPEC_GOVSTACK_PAYMENTS_BB.md` §22 (GAP list) and §23 (P0–P3 completion log). In brief: bulk-payment/prepayment-validation are now authenticated (P0), all 5 voucher endpoints return the real harness schema and all missing error codes are implemented including a defensible 462/463 resolution (P1), a real production `Gov_Stack_BB` allowlist now layers on top of the harness-compatible blocklist (P2), and the migration drift plus this very spec's staleness were both closed out (P3). Full `apps/payments/` suite: 1,633/1,633 passing.

### Verdict
G2P and P2G layers are structurally sound (atomic, audited, race-safe), but **the Voucher engine's 5 endpoints don't match the harness's actual JSON-schema contract on response field names or error codes**, and — the most serious finding across all three reports this round — **`BulkPaymentView` and both `PrepaymentValidation*` views are unauthenticated in every environment**, directly contradicting the auth module's own docstring, which claims they're protected. I independently confirmed this by reading `govstack_views.py:180-410` and `govstack_auth.py:1-70` myself: `GovStackG2PView.permission_classes = [AllowAnyBB]` (line 205) is the base class default; `RegisterBeneficiaryView`/`UpdateBeneficiaryView` explicitly override it with `[IsTrustedSourceBB]` (lines 292, 335), but `BulkPaymentView` never does — it inherits `AllowAnyBB` unchanged, while `govstack_auth.py`'s module docstring (lines 10-11) and `IsTrustedSourceBB`'s own class docstring (lines 51-56) both explicitly list `bulk-payment` and `prepayment-validation` as endpoints that class protects. The documentation and the code disagree, and the code is the one that's live.

### Spec-sourcing correction (important context)
The agent found that `api/openapi.yaml` on `bb-payments` 404s — the real spec is split across `api/G2P API YAMLs/`, `api/Voucher API YAMLs/`, `api/P2G API YAMLs/`, and critically, the Voucher YAMLs describe an **internal** Payment-Hub↔Voucher-Engine protocol, not the harness's actual test contract, which lives separately at `test/openAPI/Payment_BB_Voucher_api_test.json` plus 5 Gherkin `.feature` files. CivicOS's code comments cite the internal YAMLs — that's the root cause of the schema mismatch below, not a simple oversight.

### Findings (re-verified fresh; test suite re-run: 1,572 tests pass)
1. **Security — unauthenticated bulk disbursement endpoint.** *(Personally verified — see above.)* `bulk-payment` instructs government-to-person fund disbursement and has no auth in any environment.
2. **Voucher response schemas don't match the harness contract.** E.g. preactivation returns `{voucherNumber, voucherSerialNumber, voucherGroup, expiryDate}` where the harness schema requires `voucher_number`, `voucher_serial_number`, `expiry_date_time`; activation/redemption never emit the required `result_status` key; status-check returns `status`(int)/`value`(float) instead of `voucher_status`(string enum)/`voucher_amount`(string). The BB's own 457 GovStack-specific tests pass because they assert against the code's own (non-conformant) field names — 100% internal pass rate here does not indicate harness conformance.
3. **5 missing/wrong voucher error codes**: 455, 458, 459, 461, 462 absent from `govstack_exceptions.py` entirely (prior report had only flagged 4 of these — 455 was missed). Status-check additionally returns the *wrong* code (400 instead of the harness-required 456) for an invalid serial, per a stale internal-spec code comment that doesn't match the live Gherkin feature file.
4. **`Gov_Stack_BB` is never validated against a whitelist** — only checked for blankness. The harness's own negative scenarios send non-empty invalid values (`"not_exist"`, `"invalid_bb"`) expecting HTTP 460; current code would return 200 for these.
5. **Seed data uses the wrong BB ID.** `seed_govstack_vouchers` seeds `bb_id="GS-HARNESS"`, but the harness's own fetched `test-data.json` and Gherkin fixtures consistently use `"bb-digital-registries"` — the seeded whitelist entry doesn't match what the harness will actually send.
6. **Uncommitted migration drift**: `makemigrations payments --check --dry-run` reproducibly detects a pending, cosmetic `verbose_name`-only migration (`0023_alter_govstackbill_created_at_and_more.py` equivalent) — not destructive, but unresolved.
7. **`AllowAnyBB`'s docstring is stale/misleading** relative to what it actually guards — needs correcting regardless of what the auth decision ends up being.

### Blockers to 🟢 Production-Ready
1. Resolve the `AllowAnyBB` scope decision for `bulk-payment`/`prepayment-validation`: add a settings-gated auth mode (mirroring the pattern already used for `IsTrustedSourceBB`/`HasVoucherJWT`), or make an explicit, documented risk-acceptance decision — but the current silently-contradictory docstring must be fixed either way. *This is the highest-priority item in this entire report.*
2. Rewrite the 5 voucher response bodies to the harness's real schema (`voucher_number`, `voucher_serial_number`, `expiry_date_time`, `result_status`, `voucher_status`, `voucher_amount`, `message`). *~1-2 days.*
3. Implement the 5 missing error codes plus real expiry-date and insufficient-funds/cannot-credit-merchant logic (currently unbuilt, not merely miswired — the relevant service parameters are explicitly marked "Reserved... Not used"). *Folds into item 2.*
4. Fix status-check to return 456 (not 400) for an invalid/not-found serial.
5. Add real `Gov_Stack_BB` whitelist validation on all 5 voucher endpoints.
6. Correct the seed command to use `bb_id="bb-digital-registries"`.
7. Commit the pending cosmetic migration.

### Risk if deployed today
A real harness run fails all 5 voucher scenarios on schema mismatch despite green internal tests. Separately: any external caller who finds the URL can submit government payment disbursement instructions with zero authentication — this is not visible to a future reviewer relying on the auth module's own docstring, which currently states the opposite.

### Estimated effort
~3-4 engineer-days total. No structural rework needed — this is schema/wiring/auth-decision work on top of an otherwise solid state machine and audit trail.

---

## Appointments / Scheduler BB — 🟡 Partial

### Verdict
The strongest-built BB in this codebase on substance — all 37 endpoints (spec re-fetched fresh, confirmed exact 1:1 match including HTTP methods and the `qry`/`requestor_id`/`request_token` query convention), 799/799 tests passing, comprehensive locking (`select_for_update()` on every race-prone path), and append-only audit enforcement — but it fails two purely mechanical Production-Ready criteria that were not caught by the prior wave-review or final certifiability pass, and I personally reproduced both.

### Findings (personally re-verified)
1. **Uncommitted migration drift**, confirmed myself via `makemigrations --check --dry-run appointments`: a pending `AlterField` on `GovStackSubscriberProfile.alert_preference` (adding `choices=`) that migration `0016_govstack_appointment_fields.py`'s own comments say was deliberately deferred as "separate tech debt... out of scope" and never subsequently generated. Metadata-only (no DB schema/data risk), but real and unresolved.
2. **No GovStack scheduler models registered in Django admin** — `GovStackAffiliation`, `GovStackAlertSchedule`, `GovStackMessage`, `GovStackSubscriberProfile` have zero admin visibility (only `BookingAuditLog` is admin-visible, as a read-only inline). Ops/support staff have no GUI path to inspect these records; direct DB or API access would be required today.

### Blockers to 🟢 Production-Ready
1. Run `manage.py makemigrations appointments`, confirm the generated migration is a no-op at the DB level (choices= is Python-only metadata), and commit it. *~15 min.*
2. Register the 4 missing models in `apps/appointments/admin.py` with appropriate read-only/sensitive-field handling. *~1-2 hours.*
3. (Non-blocking) Differentiate the flat `100/minute` `govstack_bb` throttle scope by actor role.

### Estimated effort
Under 1 day — both real blockers are mechanical, not structural.

---

## Documents BB — 🔴 Not Started (GovStack axis) / 🟡 Partial (CivicOS-internal axis)

### Verdict
Confirmed, via fresh fetch, that **no usable upstream GovStack spec exists for this domain**: `bb-file-management`'s spec files are still literally 0 bytes (git empty-blob hash, last commit 2026-06-08), and `bb-digital-registries` is a generic key-value registry CRUD API with no upload/MIME/storage/scan concepts — a genuine domain mismatch, not a document-management API. This is a structural gap that is not owned by this codebase; it requires GovStack to publish a real spec, or CivicOS to adopt a different reference point.

On its own internal merits, the DRF REST API (`apps/api/documents/`, Wave 8) is well-built: 1,066 tests pass, dedicated PIPEDA test suite covers IDOR (404 not 403), scan-status gating, storage-key non-leakage, IP masking, legal hold, and single-use download tokens. All 11 recently-tracked open items (quarantined-list endpoint, 410 on expired token, confirm-upload/attach alignment, download endpoint rename, general list endpoint) are confirmed present in code.

### New finding — worse than previously understood
Of **8** domain signals declared in `apps/documents/signals.py`, only **3** have connected receivers (`document_soft_deleted`, `document_hard_deleted`, `document_legal_hold_changed`) — I personally confirmed this via grep. The **5 unwired** are `document_upload_initiated`, `document_confirmed`, `document_scan_clean`, `document_quarantined`, and `document_version_created`. This is worse than the "2 of 4" figure carried over from an earlier task list — it's 5 of 8, and critically includes the two signals whose own docstrings say they should drive notifications: `document_scan_clean` ("Receivers may notify the citizen uploader") and `document_quarantined` ("Receivers notify the system admin only"). Concretely: a citizen whose upload clears virus scanning gets no notification, and if malware is found and a document is quarantined, no admin is automatically alerted — this only surfaces if someone proactively polls `GET /quarantined/`. For a government platform, silent malware quarantine with no push alert is an operational blind spot (though the document itself is correctly blocked from citizen access regardless).

### Blockers
1. **GovStack axis**: not actionable from this codebase — needs an upstream spec.
2. **Internal axis**: wire receivers for `document_scan_clean` and `document_quarantined` (patterns already exist for the 3 connected signals; the "Future:" TODO is already in the receiver docstrings). *~4-8 hours incl. tests.*
3. Explicitly decide and document whether the other 3 unwired signals are intentional future cross-BB hooks or an oversight.
4. (Minor, non-blocking) Consider hashing `DocumentAccessToken.token` at rest instead of storing it in plaintext, readonly-visible in admin.

---

## Certification Priority Order

1. **Payments** — *(updated)* all code blockers resolved via the P0–P3 plan (see update note in its section above); now tied with Consent for closest to harness submission.
2. **Consent** — closest to done; 5 remaining items are all hours-scale. Recommend closing these out and being among the first BBs submitted to `testing.govstack.global`.
3. **Appointments/Scheduler** — also very close (both blockers are same-day mechanical fixes); next in line for harness submission once Consent's/Payments' items are closed.
4. **Documents** — internal-axis fixes (signal wiring) are cheap and worth doing regardless, but GovStack-axis certification is blocked on an external dependency (a real upstream spec) and shouldn't be sequenced against the others.

## Cross-BB Integration Gaps
- `GovStackRegisteredBB` (the shared BB-whitelist model, defined in `apps/payments/govstack_models.py`) is consumed by Payments and Appointments/Scheduler auth, but **not** by Consent, which uses citizen JWT/token auth exclusively. This is architecturally fine today (Consent doesn't need BB-to-BB org auth yet) but worth flagging if Consent ever grows org-to-org config/audit calls.
- The platform-wide hash-chained `AuditLogEntry` (in `apps/audit/`) and Consent's own `ConsentAuditEntry` are separate, non-unified audit trails by design — each BB owns its domain-specific chain. Not a defect, but an investigator using `AuditLogEntry.verify_chain()` alone would not see Consent events.
- ~~Payments' `seed_govstack_vouchers` and the actual harness fixtures disagree on the whitelist BB ID~~ — **resolved in P2** (see `SPEC_GOVSTACK_PAYMENTS_BB.md` §13.7/§23); the command now also seeds the production allowlist table.
- **Still open, not addressed by the Payments P0–P3 plan (different subsystem):** `GovStackHeaderMiddleware` injects the `X-GovStack-BB-Version` response header only for requests matching Consent's URL prefixes — Payments (`/govstack/payments/`) and Appointments (`/govstack/scheduler/`) responses never receive it, even though the platform requirements table below marks this a GovStack-wide expectation. This was carried forward from the now-deleted `GOVSTACK_READINESS_REPORT_2026-07-25.md` draft specifically because it is a real, still-unfixed regression that appears nowhere else in this report.

---

## Appendix — Platform-Wide Findings Carried Forward (from the deleted 2026-07-25 draft report)

These sections were produced by the same 2026-07-25 review round as the rest of this report but lived in a since-deleted draft (`GOVSTACK_READINESS_REPORT_2026-07-25.md`). They cover ground this report's per-BB sections don't (a Tier-2 platform-BB snapshot, a cross-cutting auth/requirements audit, and a prioritized action list spanning all BBs) and are preserved here rather than lost.

### Tier 2 — Core Platform BBs (carried forward from 2026-07-22 — not re-verified since)
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
| Audit Trail | `apps/audit/` | 19 | DB-level immutability gap unchanged (see below) |
| Core Utilities | `apps/core/` | 83 | PRODUCTION-READY |
| CMS | `apps/cms/` | 0 | No tests |

### BB-to-BB auth robustness (as of 2026-07-25, before the Payments P0 fix)
| Class | File | 2026-07-22 finding | State as of this report |
|---|---|---|---|
| `IsTrustedSourceBB` | `apps/payments/govstack_auth.py` | Accepted any non-empty header | Fixed — whitelist-backed via `GovStackRegisteredBB` |
| `HasVoucherJWT` | `apps/payments/govstack_auth.py` | Not enforced in prod settings | Fixed — defaults `True` in `production.py` |
| `GovStackSchedulerAuth` | `apps/appointments/govstack_auth.py` | N/A (didn't exist) | New, secure by default — same whitelist pattern |
| `AllowAnyBB` | `apps/payments/govstack_auth.py` | Unconditionally `True` | Was unconditionally `True`, guarding bulk-payment/prepayment-validation/voucher preactivation-activation — **this specific gap was the Payments P0 fix** (see update note above); now uses `IsTrustedSourceBB` on the two beneficiary-money endpoints. Voucher preactivation/activation intentionally remain `AllowAnyBB` — identity there is carried by the `Gov_Stack_BB` body field, not a header (see `SPEC_GOVSTACK_PAYMENTS_BB.md` §8.2). |

### Audit trail DB-level immutability — still open
`apps/audit/models.py`'s `AuditLogEntry.save()`/`delete()` enforce immutability only via Python-level `raise ValueError`; no PostgreSQL trigger or `CheckConstraint` exists in any migration. `QuerySet.update()`/`QuerySet.delete()` bypass the guard entirely. Consent's own `ConsentRevision`/`ConsentAuditEntry`, and by the same pattern Appointments' `BookingAuditLog`, share this architecture platform-wide. Not fixed by any work in this session; still the correct next hardening step before production go-live.

### GovStack Platform Requirements Table (as of 2026-07-25)
| Requirement | Status | Notes |
|---|---|---|
| `/health/` endpoint | ✅ | Checks DB, cache, Stripe; 503 on failure |
| OpenAPI schema | ✅ | `drf-spectacular` at `/api/v1/schema/`, `/api/v1/consent/schema/`. No dedicated schema for `/govstack/payments/` or `/govstack/scheduler/` namespaces. |
| Rate limiting | ✅ | `govstack_bb` scope 100/min on Payments/Appointments BB-to-BB views; Consent correctly uses citizen/anon scopes instead |
| HTTPS enforced | ✅ | `SECURE_SSL_REDIRECT=True`, HSTS 1yr+preload |
| `X-Request-ID` header | ✅ | Applies globally |
| `X-GovStack-BB-Version` header | ⚠️ **still open** | Only injected for Consent's URL prefixes, not Payments/Appointments — see the Cross-BB Integration Gaps bullet above |
| `ATOMIC_REQUESTS` | ✅ | Correctly exempted on health probe |
| CSP headers | ✅ | `django-csp` with nonces |
| `X-Frame-Options DENY` | ✅ | |
| `SECURE_REFERRER_POLICY` | ✅ | |
| BB-to-BB auth whitelist | ✅ *(updated)* | All 4 permission classes now real and mode-gated as of the Payments P0 fix |
| CORS policy | ⚠️ | No `django-cors-headers`; acceptable for server-to-server, fails browser-based harness/Swagger cross-origin use |
| Audit log DB-level immutability | ❌ | Unchanged — Python-only guard |

---

## Verification note
This report was produced by 3 independent agents that each fetched live GovStack specs fresh (not from memory or prior reports) and re-ran the relevant test suites themselves. Before finalizing, the single most consequential finding (Payments' unauthenticated bulk-payment endpoint) plus the Consent, Appointments, and Documents migration/signal-wiring findings were independently re-confirmed by direct code reads and command re-runs, not accepted on agent self-report alone. Payments' section was subsequently superseded the same day by a full P0–P3 remediation round — see the update note at the top of that section.
