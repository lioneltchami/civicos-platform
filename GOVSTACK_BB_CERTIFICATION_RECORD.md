# GovStack Building Block Certification Record

**Status as of 2026-07-27:** ✅ **3 of 3 tracked Building Blocks are READY** for GovStack certification submission — Documents Management, Appointments/Scheduler, and Consent.

**Purpose of this document:** this is the durable answer to "are these BBs actually done, and how do we know?" If this question ever comes up again — a new team member asks, a regression is suspected, or a real GovStack certification submission needs supporting evidence — read this file first. It explains what "READY" means here, what was wrong before, what was fixed, how the fixes were verified, and exactly how to re-verify any of it yourself in a few commands.

Git commits for the record: `3362c49` (Documents Management fix pass), `b13f0aa` (Appointments/Scheduler fix pass), `91cd3e6` (Consent fix pass). Full per-finding detail lives in `MASTER_BB_CERTIFIABILITY_REPORT.md` at the repo root — this document is the plain-language summary of that report plus the methodology that produced it.

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

Each of the three fix passes followed the same disciplined sequence:

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

**Verification:** 1151/1151 tests pass (1066 original + 85 new). Cross-BB regression (Payments receipts, Consent tasks, Core): 163/163 pass. `makemigrations --check --dry-run documents`: clean.

**Full detail:** `MASTER_BB_CERTIFIABILITY_REPORT.md` → "Documents Management BB" section.

---

## 4. Appointments/Scheduler BB

**What it is:** GovStack Scheduler spec implementation — entities, resources, subscribers, events, appointments, affiliations, messages, alert schedules, and audit logs, with a hashed-credential BB-to-BB auth model.

**What was wrong (original audit):** 2 HIGH, 4 MEDIUM (one accepted as a documented deviation rather than fixed), 1 LOW group. Headline defects: 6 of 8 spec-defined array-typed `*_id` query filters rejected the literal spec-compliant array request with HTTP 400 (only 2 of 8 fields had ever been fixed to accept arrays); and every one of the 9 create endpoints returned HTTP 201 where the spec and its own Gherkin fixture require 200, with non-spec envelope wrapper fields (`truncated`, `status: success`) on every list/create response. Also found: `POST /event/new` silently discarded `host_entity_id` whenever no venue was supplied; a resource's canonical `R-<pk>` id round-tripped into `/affiliation/new` as a leaked raw Django `ValueError` in a 409 response; `request_token` (a bearer credential) was written in plaintext to nginx's default access log for all 37 endpoints; and a security-mode flag (`GOVSTACK_SCHEDULER_REQUIRE_TOKEN`) failed open by default because it was only ever defined in `production.py`.

**What was fixed:** all 8 findings closed by 3 parallel agents split by array-filters+status-codes+envelopes / event-affiliation-data-integrity+settings / nginx-log-leak+throttle-identity ownership. The one MEDIUM finding (`GovStackRegisteredBB.role` has no per-tenant scoping) was deliberately left as a documented architectural deviation — acceptable for CivicOS's current single-government deployment, flagged in the spec doc for a human decision before any future multi-tenant deployment, rather than reworked mid-fix-pass at disproportionate risk.

**Verification:** 848/848 tests pass (816 original + 32 new). Cross-BB regression (Payments, Documents, API/Documents, Consent tasks, Core): 2985/2985 pass. `makemigrations --check --dry-run appointments payments`: clean. Personal diff review covered the `AffiliationNewView.post` rewrite, `_resolve_location`'s per-org placeholder logic, the array-aware R-/S- prefix parser, the nginx config, the throttle class, and the settings addition.

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

**Verification:** 361/361 tests pass (296 original + 65 new; 1 pre-existing, documented, unrelated SQLite-concurrency skip). Cross-BB regression: Payments 1709/1709, Documents+API/Documents+Core 1234/1234 (1 expected skip), Appointments 848/848 — all clean. `makemigrations --check --dry-run consent`: clean. Personal line-by-line diff review covered every changed file: the `attach_signature`/`update_signature` rewrite, `redact_pii()` and its append-only-guard-bypass mechanism, the SSRF check and redirect handling, the envelope-detection fix, and the new required DataAgreement fields.

**Full detail:** `MASTER_BB_CERTIFIABILITY_REPORT.md` → "Consent BB" section.

---

## 6. Cross-cutting lessons (apply these to the next BB, too)

1. **"Green suite, spec-blind" is a systemic pattern, not a one-off.** Every BB's original tests were written against its own implementation, not the live spec — so status-code, envelope-shape, and identifier-type mismatches were invisible to `manage.py test` every time. Always re-derive test expectations from the live spec/harness for any BB being newly certified, not from what the code already does.
2. **SSRF protection now exists in 3 independent copies** (Appointments, Payments, Consent) rather than one shared helper. This is intentional, established precedent in this codebase, not an oversight — but if a 4th BB ever needs the same check, extracting it into a shared `apps.core` helper at that point (rather than a 4th copy) would be a reasonable point to revisit this.
3. **Settings-module fail-open/fail-closed asymmetry recurred 3 times** (Documents' `CLAMAV_REQUIRED`, Appointments' `GOVSTACK_SCHEDULER_REQUIRE_TOKEN`, Consent's mode-gating flag) — a security flag defined only in `production.py` with a safe default there, but silently absent (and therefore effectively off) in every other settings module. All three are now fixed by defining the flag in `base.py` with the secure default and letting `production.py` simply inherit.
4. **Identifier-type mismatches (int vs. UUID vs. prefixed string) are a recurring bug class**, not a one-off: Documents' integer PK misdeclared as `UUIDField`, Appointments' `R-<pk>` canonical form breaking at the Affiliation endpoint boundary, and Consent's Individual-detail 400s are three independent instances of the same root cause — an unverified assumption about an ID's real type. When touching any endpoint that accepts a path-segment or query-param ID, verify the actual model field type before assuming a parsing helper.
5. **Audit-trail completeness gaps concentrate exactly where they matter most**: Documents' missing malware-detection audit entry, Consent's missing signature-mutation audit entry. Both BBs are otherwise thoroughly audited — treat any newly-added mutation path as needing an audit entry by default, and treat its absence as a finding worth flagging even if nothing else is obviously wrong.

---

## 7. How to re-verify any of this yourself

```bash
# Per-BB test suite (add --keepdb after the first run in a session to avoid
# re-running the full migration set every invocation)
DJANGO_SETTINGS_MODULE=config.settings.test python3 manage.py test apps.documents apps.api.documents --keepdb -v 1
DJANGO_SETTINGS_MODULE=config.settings.test python3 manage.py test apps.appointments --keepdb -v 1
DJANGO_SETTINGS_MODULE=config.settings.test python3 manage.py test apps.consent --keepdb -v 1

# Cross-BB regression (run after any change to shared models/settings)
DJANGO_SETTINGS_MODULE=config.settings.test python3 manage.py test apps.payments --keepdb -v 1
DJANGO_SETTINGS_MODULE=config.settings.test python3 manage.py test apps.core --keepdb -v 1

# Migration drift check (run for whichever app(s) you touched)
DJANGO_SETTINGS_MODULE=config.settings.test python3 manage.py makemigrations --check --dry-run documents appointments consent payments
```

If all of the above come back green with no drift, and nothing in the master report's per-finding fix log has been touched since, these three BBs remain READY.
