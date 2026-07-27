# GovStack Building Block Certification Record

**Status as of 2026-07-27:** ✅ **4 of 4 GovStack Building Blocks in this codebase are READY** for GovStack certification submission — Documents Management, Appointments/Scheduler, Consent, and Payments. (Repo-wide search confirmed `apps/api/{notifications,portal,volunteers,workflows}` have zero GovStack scope — these four are the complete set.)

**Purpose of this document:** this is the durable answer to "are these BBs actually done, and how do we know?" If this question ever comes up again — a new team member asks, a regression is suspected, or a real GovStack certification submission needs supporting evidence — read this file first. It explains what "READY" means here, what was wrong before, what was fixed, how the fixes were verified, and exactly how to re-verify any of it yourself in a few commands.

Git commits for the record: `3362c49` (Documents Management fix pass), `b13f0aa` (Appointments/Scheduler fix pass), `91cd3e6` (Consent fix pass), `a9a6eb5` (Payments fix pass), `77966f3` (Round 2 fix pass, all 4 BBs). Full per-finding detail lives in `MASTER_BB_CERTIFIABILITY_REPORT.md` at the repo root — this document is the plain-language summary of that report plus the methodology that produced it.

**Round 2 update:** after all four BBs above reached READY, a second full 3-agent adversarial re-audit was run against all of them — explicitly distrusting every prior "fixed" claim rather than assuming the pattern of "green suite, spec-blind gaps" from the first round couldn't recur. It found genuine, previously-missed gaps in all four BBs, including a second, unpatched door into the exact signature-backdating defect Consent's first fix pass had closed, and 2 of the 8 array-typed filter fields Appointments' first fix pass had claimed were all fixed. Every one of these was fixed using the same 3-parallel-agent method and personally re-verified — see `MASTER_BB_CERTIFIABILITY_REPORT.md`'s "Round 2" section for the full per-finding list and fix log. The one deliberate exception, tracked rather than silently dropped: a shared DNS-rebinding TOCTOU gap in the SSRF callback guard used by 3 of the 4 BBs, which is architectural and was deferred as its own follow-up. Final test counts after Round 2: Documents+API 1159/1159, Appointments 861/861, Consent 376/376 (2 expected skips), Payments 1760/1760 — all green, project-wide migration check clean.

---

## 1. What "READY" means here

A BB is marked READY only when all of the following are true simultaneously — not just "tests pass":

1. Every finding from a **fresh, adversarial audit against the live upstream GovStack spec** (not against the code's own assumptions, and not against memory of the spec) has been fixed or, where a finding was a deliberate accepted trade-off, explicitly documented as such rather than silently left alone.
2. The BB's own test suite is 100% green, including new tests written specifically to pin the fixes and prevent regression.
3. `makemigrations --check --dry-run` shows no drift — the fix didn't leave an unmigrated model change behind.
4. **Every other previously-certified BB's test suite still passes** — a fix pass is not allowed to destabilize prior work, even indirectly (shared models, shared settings, shared middleware).
5. A human (not just an agent's self-report) has read the actual diff of the riskiest changes line-by-line and confirmed the fix is correct, well-scoped, and doesn't quietly break an invariant elsewhere.

This bar exists because of a pattern that showed up in all three BBs during the original audits: **a green test suite is evidence the code does what it was written to do, not evidence it matches the spec.** Every one of the original blocking defects had a fully green, self-consistent test suite sitting right on top of it — the tests were written against the code's own behavior, so a code/spec mismatch was invisible to them. Certification-readiness has to be checked against the outside world (the live spec, a real harness, an adversarial re-read), not just against the repo's own tests.

---

## 2. Methodology (repeatable — use this again next time)

Each of the four fix passes followed the same disciplined sequence:

1. **Fresh adversarial audit first.** Before touching any code, a full read-only pass re-fetches the live upstream GovStack OpenAPI spec / Gherkin harness (not from memory), re-reads the BB's actual code end-to-end, and actively tries to break auth/IDOR/SSRF paths rather than just confirm happy paths. This produces a severity-ranked findings list.
2. **3 parallel fix agents, non-overlapping ownership.** The findings are split across 3 agents by strict file/function ownership — never by finding, since two findings often land in the same function. Each agent is told explicitly which files/classes/methods it owns and instructed to never touch anything outside that list, and to Read a file before Editing it. All 3 are launched in a single message so they run concurrently against the same working tree (confirmed this session by agents observing each other's in-flight changes).
3. **Personal line-by-line diff review — not just agent self-reports.** After all 3 agents report done, every changed file's actual diff is read in full, not sampled from summaries. This is the step that catches an agent overstating what it did, or a fix that's correct in isolation but wrong in context.
4. **Full BB test suite run.** Every test file, not a subset, with the new tests included.
5. **Migration drift check.** `makemigrations --check --dry-run <app>` must show no changes.
6. **Cross-BB regression run.** Every other previously-certified BB's test suite is re-run in isolation to prove nothing shared (models, settings, middleware, throttle classes) was destabilized.
7. **Master report + spec doc updated, then committed** with a commit message that documents every fix and every judgment call made — so a future reader never has to re-derive the reasoning from the diff alone.

**Sandbox note for future runs:** this repo's full migration set (~19 apps, heavy Wagtail CMS chain) can take 35–40+ seconds to apply from scratch, which can exceed a tool's per-command time budget and look like a hung test run when it's actually just migration overhead. Use `--keepdb` to reuse an already-migrated test database across separate command invocations, and split large test runs into smaller per-file-group chunks. This is a tooling artifact, not a code defect — confirmed via `PYTHONFAULTHANDLER=1` + `timeout --signal=ABRT` stack dumps during this session that showed the process still inside Django migration internals, not stuck test code.

---

## 3. Documents Management BB

**What it is:** citizen/staff document upload, virus scanning (ClamAV), encrypted-PDF and ZIP-bomb detection, S3-backed storage with quarantine→active promotion, presigned download URLs, and retention/audit logging — all PIPEDA-scoped.

**What was wrong (original audit):** 1 CRITICAL, 6 HIGH, 3 MEDIUM, 2 LOW findings. Headline defect: the encrypted-PDF check was inverted (`pikepdf.Pdf.encryption` is always truthy as an object; the code needed `.is_encrypted`), so **every single PDF upload failed** — the most common document type in the system was completely broken. Other findings included: malware/ZIP-bomb gating keyed on the client-supplied file extension rather than the detected MIME type (bypassable by renaming a file); `pyClamd`/`python-magic`/`pikepdf` missing from `requirements/base.in` entirely; a cleanup task that could orphan S3 objects; a citizen-facing download path that didn't recheck scan status after token redemption; an unconditional unsigned form field injected into the upload template; and no audit-log entry for any virus-scan outcome (a real gap for a compliance product where "was this ever scanned" needs an audit trail).

**What was fixed:** all 12 original findings closed by 3 parallel agents split by upload-validation pipeline / download-storage lifecycle / ClamAV-infra-audit-settings ownership, plus 2 bonus bugs the fix agents surfaced and fixed (a DRF-level staff-upload-cap bypass, and a silently-discarded `description` field on the new-version endpoint) that were invisible before because no DRF-level test exercised that endpoint at all.

**Verification:** 1151/1151 tests pass (1066 original + 85 new). Cross-BB regression (Payments receipts, Consent tasks, Core): 163/163 pass. `makemigrations --check --dry-run documents`: clean. **Round 2 update:** fixed a spoofable `X-Forwarded-For` IP in the DRF API's audit trail (now uses `REMOTE_ADDR` only) and added a missing `ACCESS_DENIED` audit event for IDOR-deny paths (per spec §13) — now 1159/1159 tests pass.

**Full detail:** `MASTER_BB_CERTIFIABILITY_REPORT.md` → "Documents Management BB" section.

---

## 4. Appointments/Scheduler BB

**What it is:** GovStack Scheduler spec implementation — entities, resources, subscribers, events, appointments, affiliations, messages, alert schedules, and audit logs, with a hashed-credential BB-to-BB auth model.

**What was wrong (original audit):** 2 HIGH, 4 MEDIUM (one accepted as a documented deviation rather than fixed), 1 LOW group. Headline defects: 6 of 8 spec-defined array-typed `*_id` query filters rejected the literal spec-compliant array request with HTTP 400 (only 2 of 8 fields had ever been fixed to accept arrays); and every one of the 9 create endpoints returned HTTP 201 where the spec and its own Gherkin fixture require 200, with non-spec envelope wrapper fields (`truncated`, `status: success`) on every list/create response. Also found: `POST /event/new` silently discarded `host_entity_id` whenever no venue was supplied; a resource's canonical `R-<pk>` id round-tripped into `/affiliation/new` as a leaked raw Django `ValueError` in a 409 response; `request_token` (a bearer credential) was written in plaintext to nginx's default access log for all 37 endpoints; and a security-mode flag (`GOVSTACK_SCHEDULER_REQUIRE_TOKEN`) failed open by default because it was only ever defined in `production.py`.

**What was fixed:** all 8 findings closed by 3 parallel agents split by array-filters+status-codes+envelopes / event-affiliation-data-integrity+settings / nginx-log-leak+throttle-identity ownership. The one MEDIUM finding (`GovStackRegisteredBB.role` has no per-tenant scoping) was deliberately left as a documented architectural deviation — acceptable for CivicOS's current single-government deployment, flagged in the spec doc for a human decision before any future multi-tenant deployment, rather than reworked mid-fix-pass at disproportionate risk.

**Verification:** 848/848 tests pass (816 original + 32 new). Cross-BB regression (Payments, Documents, API/Documents, Consent tasks, Core): 2985/2985 pass. `makemigrations --check --dry-run appointments payments`: clean. Personal diff review covered the `AffiliationNewView.post` rewrite, `_resolve_location`'s per-org placeholder logic, the array-aware R-/S- prefix parser, the nginx config, the throttle class, and the settings addition. **Round 2 update:** a fresh audit found 2 of the 8 array-typed filter fields (`event_id`, `affiliation_id`) had NOT actually been fixed despite the "all 8 fields" claim above — both converted to `StringOrListField` and their service-layer filters switched to `pk__in`. Also added an admin audit trail for Entity/Resource/Affiliation mutations and BB-credential creation/rotation (via a nullable `BookingAuditLog.booking` FK) — now 861/861 tests pass.

**Full detail:** `MASTER_BB_CERTIFIABILITY_REPORT.md` → "Appointments/Scheduler BB" section, and `SPEC_APPOINTMENTS_BB_GOVSTACK.md` §14.

---

## 5. Consent BB

**What it is:** citizen consent capture, data-agreement management, digital signature attachment for non-repudiation, webhook notifications to registered data consumers, and PIPEDA right-to-be-forgotten (RTBF) erasure.

**What was wrong (original audit):** 1 CRITICAL, 3 HIGH, 4 MEDIUM, 1 LOW group. Headline defect: **all 5 `Individual` detail operations always returned HTTP 400** — the code parsed the path segment as a UUID, but the real user model's primary key is a plain integer (`BigAutoField`); the module's own docstring claim that the Individual id type was `uuid` was simply false, and zero tests existed for either Individual detail path so this had never been caught. Two more HIGH findings hit the BB's core trust guarantee: `POST .../signature/` **discarded the caller's actual signature** and replaced it with a server-computed SHA-256 hash that verifies against nothing, directly contradicting the code's own docstring claim that it "stores whatever signature the caller provides"; and `PUT .../signature/` let a citizen **silently rewrite their own signature's timestamp** with zero revision or audit trail (backdating was reproduced live to the year 1999) — the one hole in an otherwise consistently-audited BB. A fourth HIGH finding: `ConsentWebhook.payload_url` had **no SSRF protection at all**, confirmed live by pointing a webhook at the cloud metadata IP `169.254.169.254` and having it accepted. MEDIUM findings: 3 list/filter endpoints raised uncaught HTTP 500s on malformed query params instead of a clean 400; the signature endpoints rejected the spec-conformant flat request body, only accepting a CivicOS-specific nested envelope; right-to-be-forgotten deleted the citizen's `ConsentRecord` but left their identifying data re-identifiable inside undeletable `ConsentRevision` audit snapshots (both an erasure gap and, since the PIPEDA export only queries revisions of still-existing records, an access-right gap too); and `POST /config/data-agreement/` accepted blank bilingual `name_en`/`name_fr`/`purpose_fr` fields.

**What was fixed:** all 9 findings closed by 3 parallel agents split by Individual-detail+list-filter ownership / signature-integrity+envelope+DataAgreement-validation ownership / webhook-SSRF+RTBF-redaction ownership:

- Individual detail 400s fixed by switching to `_parse_int_param()`, with the docstring corrected and 16 new tests covering the exact paths that previously had none.
- Signature integrity restored: `attach_signature()` now requires and persists the caller's actual signature verbatim; a new `update_signature()` service method gives `PUT` the same revision-and-audit discipline every other mutation in this BB already had, closing the silent-backdating hole.
- Webhook SSRF closed by reusing the exact DNS-resolved private-IP-range-blocking + no-redirect-follow pattern already proven in the Appointments and Payments BBs (a third independent copy, since no shared helper module exists yet for this check across the codebase — confirmed by repo-wide search, and per-BB duplication remains this codebase's established precedent rather than inventing a new shared-module pattern mid-fix).
- The 3 malformed-filter 500s, the flat-signature-envelope rejection, the RTBF PII-residue gap (via a new `ConsentRevision.redact_pii()` method that scrubs identifying data in place while preserving the audit-trail fact that a consent event occurred — a narrowly-scoped, explicitly-documented, regression-tested exception to the model's own append-only invariant), and the blank-bilingual-field gap were all fixed.
- The LOW group (no per-consumer DataAgreement scoping, technically-writable `state` field, plaintext webhook secrets on read) was left as documented, not code-changed — these match the spec's literal permission model rather than being defects.

One judgment call is flagged for a future, low-priority follow-up rather than silently absorbed: `ConsentAuditEntry.ACTION_CHOICES` has no dedicated "signature updated" value yet, so the update-signature audit entry reuses `"granted"` with a `details.trigger="signature_updated"` disambiguator. A proper dedicated choice requires a model migration and was deliberately left out of this fix pass's scope.

**Verification:** 361/361 tests pass (296 original + 65 new; 1 pre-existing, documented, unrelated SQLite-concurrency skip). Cross-BB regression: Payments 1709/1709, Documents+API/Documents+Core 1234/1234 (1 expected skip), Appointments 848/848 — all clean. `makemigrations --check --dry-run consent`: clean. Personal line-by-line diff review covered every changed file: the `attach_signature`/`update_signature` rewrite, `redact_pii()` and its append-only-guard-bypass mechanism, the SSRF check and redirect handling, the envelope-detection fix, and the new required DataAgreement fields. **Round 2 update:** a fresh audit found the `PUT .../signature/` backdating fix above had NOT closed a second door — `ServiceIndividualConsentRecordListView.post()` (the CREATE endpoint) had its own unpatched signature-mutation code path, since fixed by routing it through the same `attach_signature()`/`update_signature()` service methods. Also added an audit trail for webhook CRUD and policy/data-agreement config mutations (new `ACTION_CHOICES` values), and row locking (`select_for_update()`) in the signature service methods to close a revision-chain fork race — now 376/376 tests pass (2 expected skips).

**Full detail:** `MASTER_BB_CERTIFIABILITY_REPORT.md` → "Consent BB" section.

---

## 6. Payments BB

**What it is:** the GovStack Payments spec implementation — G2P beneficiary registration, bulk payments, prepayment validation, vouchers (preactivate/activate/redeem/cancel/status-check), and P2G bill payments (bill inquiry, transfer requests, mark-paid, transfer-request status).

**What was wrong (original audit):** Payments had by far the deepest prior remediation history of any BB in this repo (9 build-out waves plus many subsequent rounds of gap-fixing), and most of that history held up under fresh, independent re-verification — but the P2G (bill payment) surface had never been brought to the same bar as the G2P/voucher surfaces. 1 CRITICAL, 3 HIGH, 2 MEDIUM, 1 MEDIUM/needs-confirmation, 1 LOW group. Headline defect: **P2G endpoints had no tenant/ownership data scoping at all** — `GovStackBill` didn't even have a tenant field, so any caller holding a valid whitelisted `X-PayerFI-Id` could read another institution's bill or transfer request, and, more severely, could mark **any tenant's bill as paid** with no audit trail created at all, directly contradicting the live spec's own description of the tenant-id header as required "for Data scoping." Three more findings were spec-conformance gaps confirmed by re-fetching the live GovStack P2G OpenAPI YAMLs directly rather than trusting old paraphrase: the P2G response envelope/status-code didn't match the spec (200 + bespoke body instead of 202 + `{responseCode, reason, requestID}`); the transfer-request-status endpoint required the wrong auth header family (`X-PayerFI-Id` instead of the spec-mandated `X-billerId`); and the bill-inquiry endpoint didn't implement the spec-required `fields=inquiry` query parameter. Payments' SSRF callback guard was also confirmed to be a materially weaker, stale copy of the exact pattern already hardened in the Appointments and Consent BBs this session (allowed plain HTTP, missing CGNAT/IETF-protocol-assignment range blocking, no 3xx-as-failure check).

**What was fixed:** all 8 findings closed by 3 parallel agents split by P2G service+view-rewrite ownership / SSRF-hardening+throttle+settings+async-dispatch-investigation ownership / docs-refresh+independent-missed-issue-sweep ownership:

- **CRITICAL tenant-isolation gap closed.** `platform_tenant_id` added to `GovStackBill` (via a new migration; `GovStackBillPayment` already had the field). All 4 P2G service methods now scope every query by it when the caller supplies a non-empty value, mirroring the BB's existing mode-gating convention (harness/test mode permissive, `GOVSTACK_REQUIRE_PLATFORM_TENANT_ID=True` mandatory in production). A wrong-tenant lookup and a genuinely-missing record now both raise the identical "not found" response — no side channel for enumerating other tenants' record existence. `mark_bill_paid`'s audit entry now also records the real calling institution's identity instead of a hardcoded blank.
- **P2G spec-conformance gaps closed**, each confirmed against a fresh read of the live OpenAPI YAMLs (not memory): all 4 P2G endpoints now return HTTP 202 with the spec-required `{responseCode, reason, requestID}` envelope alongside the existing useful data fields; the transfer-request-status endpoint now requires `X-billerId` via a new `IsTrustedBiller` permission class; the bill-inquiry endpoint now requires and validates the spec-mandated `fields=inquiry` query parameter. A deliberate, explicitly-documented architectural choice was made to keep P2G synchronous rather than build out the async-callback pattern the response schema's shape implies, since no P2G request YAML has a callback-registration field and no P2G harness exists upstream to validate an async implementation against.
- **SSRF guard brought up to the Appointments/Consent standard**: HTTPS-only, fails closed on DNS-resolution failure, blocks the CGNAT and IETF-protocol-assignment ranges, and treats any 3xx response as a failed delivery rather than a silent success — now the third independent, consistent copy of this mitigation in the codebase.
- **BB-identity-keyed throttle added** (`GovStackPaymentsIdentityThrottle`), matching the pattern already added to Appointments and Consent.
- **The 4 Payments-specific settings flags moved into `base.py`** with safe explicit defaults, closing the same fail-open-by-omission trap already fixed for 3 other flags across the other BBs this session.
- **The async-dispatch defect, investigated to its true root cause.** What one fix agent initially scoped as a Payments-specific bug turned out, on personal investigation, to be a **platform-wide** defect: `config/__init__.py` was completely empty, so it never imported the Celery app from `config/celery.py` — meaning every `@shared_task` across the *entire platform*, not just Payments, would lazily bind to Celery's own unconfigured default app (a real, unreachable AMQP broker) on its first `.delay()` call in production, rather than to this project's Django-settings-configured app. This was invisible to every BB's test suite because nearly every test mocks `.delay()` out entirely. Fixed with a one-line import added to `config/__init__.py`, confirmed by direct reproduction: a real, unmocked `.delay()` call raised a genuine connection-refused error before the fix and executed synchronously after. Fixing this surfaced one further, narrow side effect in an already-certified BB — a Consent test's assumption that eager-mode `self.retry()` respects `throw=False` turned out to be wrong (Celery's own documented behavior: it doesn't, in eager mode, regardless of `throw`) — the old, broken bootstrap had been accidentally masking this. The Consent application code itself was never wrong; only the test's assumption was, and it's now fixed.
- **A documentation gap self-identified and closed.** One fix agent's documentation update claimed a new regression test existed for the Celery-bootstrap fix; it did not. A dedicated `CeleryAppBootstrapTest` class (4 tests, including a real unmocked `.delay()` call) was written personally during verification to make that claim actually true and close the real coverage gap it had pointed at.

**Verification:** 1750/1750 tests pass (1709 original + 41 new/updated). Cross-BB regression: Consent 361/361 (1 expected skip, after the one test-assumption fix described above), Appointments 848/848, Documents+API/Documents 1151/1151 — all clean. `makemigrations --check --dry-run payments`: clean. Personal line-by-line diff review covered every changed file across all 3 agents plus the platform-wide `config/__init__.py` fix and its one downstream test fix. **Round 2 update:** a fresh audit found the `GET /bills/{billId}` response envelope used the wrong casing (`requestID` instead of spec-mandated `requestId`) because the original fix cited the wrong spec file — fixed with a per-call-site `request_id_key` parameter. Also closed the "tenant scoping is self-asserted" gap with a real, opt-in `GovStackRegisteredBB.allowed_platform_tenant_ids` registry; scoped `GovStackBill`/`GovStackBillPayment`'s uniqueness constraints by tenant; and added an audit entry for mark-paid calls against already-paid bills — now 1760/1760 tests pass.

**Full detail:** `MASTER_BB_CERTIFIABILITY_REPORT.md` → "Payments BB" section, and `SPEC_GOVSTACK_PAYMENTS_BB.md` §26.

---

## 7. Cross-cutting lessons (apply these to the next BB, too)

1. **"Green suite, spec-blind" is a systemic pattern, not a one-off.** Every BB's original tests were written against its own implementation, not the live spec — so status-code, envelope-shape, and identifier-type mismatches were invisible to `manage.py test` every time. Always re-derive test expectations from the live spec/harness for any BB being newly certified, not from what the code already does.
2. **SSRF protection now exists in 3 independent copies** (Appointments, Payments, Consent) rather than one shared helper. This is intentional, established precedent in this codebase, not an oversight — but if a 4th BB ever needs the same check, extracting it into a shared `apps.core` helper at that point (rather than a 4th copy) would be a reasonable point to revisit this.
3. **Settings-module fail-open/fail-closed asymmetry recurred 3 times** (Documents' `CLAMAV_REQUIRED`, Appointments' `GOVSTACK_SCHEDULER_REQUIRE_TOKEN`, Consent's mode-gating flag) — a security flag defined only in `production.py` with a safe default there, but silently absent (and therefore effectively off) in every other settings module. All three are now fixed by defining the flag in `base.py` with the secure default and letting `production.py` simply inherit.
4. **Identifier-type mismatches (int vs. UUID vs. prefixed string) are a recurring bug class**, not a one-off: Documents' integer PK misdeclared as `UUIDField`, Appointments' `R-<pk>` canonical form breaking at the Affiliation endpoint boundary, and Consent's Individual-detail 400s are three independent instances of the same root cause — an unverified assumption about an ID's real type. When touching any endpoint that accepts a path-segment or query-param ID, verify the actual model field type before assuming a parsing helper.
5. **Audit-trail completeness gaps concentrate exactly where they matter most**: Documents' missing malware-detection audit entry, Consent's missing signature-mutation audit entry. Both BBs are otherwise thoroughly audited — treat any newly-added mutation path as needing an audit entry by default, and treat its absence as a finding worth flagging even if nothing else is obviously wrong.

---

## 8. How to re-verify any of this yourself

```bash
# Per-BB test suite (add --keepdb after the first run in a session to avoid
# re-running the full migration set every invocation)
DJANGO_SETTINGS_MODULE=config.settings.test python3 manage.py test apps.documents apps.api.documents --keepdb -v 1
DJANGO_SETTINGS_MODULE=config.settings.test python3 manage.py test apps.appointments --keepdb -v 1
DJANGO_SETTINGS_MODULE=config.settings.test python3 manage.py test apps.consent --keepdb -v 1
DJANGO_SETTINGS_MODULE=config.settings.test python3 manage.py test apps.payments --keepdb -v 1

# Platform-wide Celery bootstrap regression check (config/__init__.py must
# import the Celery app — this test would catch a future accidental revert)
DJANGO_SETTINGS_MODULE=config.settings.test python3 manage.py test apps.payments.tests.test_apps_config.CeleryAppBootstrapTest --keepdb -v 1

# Cross-BB regression (run after any change to shared models/settings)
DJANGO_SETTINGS_MODULE=config.settings.test python3 manage.py test apps.core --keepdb -v 1

# Migration drift check (run for whichever app(s) you touched)
DJANGO_SETTINGS_MODULE=config.settings.test python3 manage.py makemigrations --check --dry-run documents appointments consent payments
```

If all of the above come back green with no drift, and nothing in the master report's per-finding fix log has been touched since, all four BBs remain READY.
