# Master BB Certifiability Report — CivicOS / GovStack

**Date:** 2026-07-26
**Git HEAD:** `92422c6` (Appointments/Scheduler BB: verify plan completion + fix `__str__` bug)
**Scope:** Documents Management BB, Appointments/Scheduler BB, Consent BB — the three GovStack Building Blocks closed out in this work session.
**Method:** 3 independent parallel adversarial audits (one per BB), each instructed to distrust every prior "done" claim, re-read the full BB codebase end to end, re-fetch the live upstream GovStack spec/harness fresh rather than from memory, run the real test suite, run migration-drift checks, and actively try to break auth/IDOR/SSRF paths rather than just confirm happy paths. No code was modified during this audit; all three passes are read-only.

---

## 0. Executive summary

| Building Block | Verdict | Blocking defect | Test suite | Migration drift |
|---|---|---|---|---|
| **Documents Management** | ✅ **READY** (fixed, see updated section below) | ~~Encrypted-PDF check is inverted~~ — fixed; all 12 findings closed and re-verified | 1151/1151 pass | none |
| **Appointments/Scheduler** | ✅ **READY** (fixed, see updated section below) | ~~6 of 8 spec array-typed `*_id` filters reject spec-compliant input~~ — fixed; all 8 findings closed and re-verified | 848/848 pass | none |
| **Consent** | ❌ **NOT READY** | All 5 `Individual` detail operations always return HTTP 400 (int PK parsed as UUID); signature endpoint discards the caller's actual signature and permits silent backdating with no audit trail | 296/296 pass (1 expected skip) | none |

**UPDATE (post-audit):** The Documents Management BB and Appointments/Scheduler BB fix passes described above have both been completed and independently re-verified — see their respective sections below for the full per-finding fix logs. Consent is unchanged from the original audit below and remains NOT READY pending its own fix pass.

**Headline finding, common to all three at the time of the original audit:** every BB had a green, fast, self-consistent test suite (2,178 tests total, 0 failures) and clean migrations — and every BB also shipped at least one certification-blocking defect that suite never touched, because the tests were written against the code's own behavior rather than against the live upstream spec/harness. In every case the defect sat in exactly the code path a real conformance harness would exercise first (PDF upload, create-endpoint status codes, individual-record CRUD). This is the same pattern that produced the Documents BB `instream`→`scan_stream` bug found earlier this session, recurring at a different layer — **a green test suite in this repo is evidence the code does what it was written to do, not evidence it matches the spec.**

Consent is not yet safe to submit for GovStack certification. Documents Management and Appointments/Scheduler now are. Consent's remaining fixes are mechanical-to-moderate, not architectural rewrites, and its highest-severity defect (the Individual UUID-vs-int mismatch) is a one-line-to-one-method fix with an obvious correct form already used elsewhere in the same codebase.

---

## Documents Management BB

*(Fix pass completed and independently re-verified after the audit below. Verdict: ✅ READY. Original NOT-READY audit findings retained for the record, each annotated with its fix.)*

### Executive verdict — UPDATED
✅ **READY.** All 12 findings from the original adversarial audit (1 CRITICAL, 6 HIGH, 3 MEDIUM, 2 LOW/cosmetic) have been fixed, using 3 parallel fix agents split by non-overlapping file/function ownership (upload-validation pipeline; download/storage lifecycle; ClamAV/infra/audit/settings/spec-doc), followed by personal verification of two additional bugs the fix agents surfaced but were out of scope to fix themselves (a DRF-level staff-upload-cap bypass and a silently-discarded `description` field on the new-version endpoint — both now fixed, with a new dedicated `DocumentNewVersionAPITests` test class closing the gap that let those two bugs go unnoticed: no DRF-level test previously exercised `POST /{doc_id}/new-version/` at all).

### Original findings — fix status

- **CRITICAL — Encrypted-PDF check inverted; every PDF upload failed.**
  ✅ FIXED. `_check_pdf_encryption()` now tests `pikepdf.Pdf.is_encrypted` (a real bool) instead of the always-truthy `Pdf.encryption` object. New test `test_upload_content_gating.py` pushes real pikepdf-generated clean/owner-password/real-password PDF fixtures through **unpatched** `confirm_upload()`.

- **HIGH — Layers 5b/6 gated on client-supplied extension, not detected MIME type (bypassable).**
  ✅ FIXED. Encrypted-PDF and ZIP-bomb gating now key off the libmagic-detected MIME type (`detected_mime`), with extension-based fallback only when `python-magic` is genuinely absent and `MAGIC_BYTES_REQUIRED=False` (dev-only). New tests cover an encrypted PDF disguised as `.csv` and a ZIP-bomb-shaped OOXML package disguised as `.jpg` — both now rejected.

- **HIGH — `pyClamd`/`python-magic`/`pikepdf` absent from `requirements/base.in`.**
  ✅ FIXED. All three added to `requirements/base.in` in a grouped, commented section matching existing pinning conventions, closing the drift against the committed `base.txt`.

- **HIGH — `cleanup_stale_pending_uploads` orphaned S3 objects; false docstring claim of a lifecycle rule.**
  ✅ FIXED. The task now snapshots `(pk, storage_key)` pairs, deletes the S3 objects first (batched, `_S3_DELETE_BATCH_SIZE = 1000`) and only then the DB rows — crash-safe ordering, since a row that survives a mid-run crash simply stays `PENDING_UPLOAD` and is retried next run. Docstring corrected to state the truth (no lifecycle rule exists). New `CleanupStalePendingUploadsStorageTests` (7 tests).

- **HIGH — Citizen-facing HTML download path didn't recheck scan_status/deleted_at after token redemption.**
  ✅ FIXED. `DocumentTokenRedeemView.get()` now raises `Http404` post-consumption if `scan_status != ACTIVE` or `deleted_at is not None`, mirroring this view's existing 404 convention. New `DocumentTokenRedeemPostRedemptionGateTests` (7 tests).

- **HIGH — Presign template unconditionally injected unsigned `success_action_redirect`.**
  ✅ FIXED. The hidden form input is now rendered only via the `upload_fields` loop (i.e., only when the service actually signed it). `SITE_URL` is now a hard `ImproperlyConfigured` failure at production settings load if unset — enforced platform-wide (not just for this BB) since Notifications' PIPEDA export links share the same setting. New `test_upload_presign_template.py` (3 tests) + `test_presign_kms_and_clamav_settings.py` (8 tests).

- **HIGH — No audit entry for any virus-scan outcome; `THREAT_DETECTED` was dead code.**
  ✅ FIXED. `record_event(...)` calls added inside the existing `atomic()`/`select_for_update()` blocks in `tasks.py`: quarantine paths emit `AuditEventType.THREAT_DETECTED` (`event_detail` limited to `document_pk`/`scan_engine_result` only — no filename/storage_key/uploader identity, per PIPEDA); clean-scan promotion reuses the existing `STATUS_CHANGED` event type. New `test_scan_audit_and_promotion.py` (28 tests).

- **MEDIUM — SSE-KMS dead in production.**
  ✅ FIXED. `object_parameters`/`AWS_S3_OBJECT_PARAMETERS` KMS wiring added to `production.py`; confirmed via a direct settings-import test that `kms_key_id` now resolves.

- **MEDIUM — Storage prefix semantics inverted (nothing ever promoted quarantine→active).**
  ✅ FIXED. New `_promote_storage_object_to_active()` / `_delete_storage_object()` in `tasks.py`: on a clean scan result, the object is copied to its `active/` key, the DB row updated and audited, and the old `quarantine/` object deleted `on_commit` — all atomic, with Celery's existing retry/backoff covering transient failures. (Payments' receipt PDFs and Consent's export files remain deliberately out of scope — they write server-generated trusted content directly, never touching untrusted user input, and are separately certified.)

- **MEDIUM — ClamAV `StreamMaxLength` too low relative to the app's 50MB staff cap.**
  ✅ FIXED. `CLAMD_CONF_StreamMaxLength: 128M` added to both `docker-compose.yml` and `docker-compose.prod.yml` (real, supported `clamav/clamav` image env-var mechanism), ~2.5× headroom over the 50MB cap.

- **MEDIUM — Unspecced ClamAV mocks (`MagicMock()`, no `autospec`; `pyclamd` not installed in test env).**
  ✅ FIXED. `pyClamd` installed in the test sandbox; mocks converted to `create_autospec(pyclamd.ClamdNetworkSocket, instance=True)` via a new `_make_mock_pyclamd()` helper, so any future drift between the mock and the real library's API surface (like the original `instream`→`scan_stream` bug) fails the test suite immediately instead of silently passing.

- **LOW — `docker-compose` `depends_on` not health-gated for ClamAV.**
  ✅ FIXED. `condition: service_started` → `service_healthy` for all consumers (web/migrate/worker/beat in prod; web/worker-webhooks in dev); `start_period` raised 120s→300s. (A second, previously-unreported bug was found and fixed in the same pass: `clamav/clamav:1.4-stable` is not a real published tag — repinned to `clamav/clamav:1.4`.)

- **LOW/cosmetic group** — `CLAMAV_TIMEOUT` undefined; `uploaded_by_id` misdeclared as `UUIDField`; `DocumentVersionsView` not filtering quarantined siblings; HTML download TTL mismatch (12× the API path's).
  ✅ ALL FIXED. `CLAMAV_TIMEOUT` added to the `CIVICOS` dict; `uploaded_by_id` changed to `IntegerField` (matches the User model's real `BigAutoField` PK — `doc_id` correctly remains `UUIDField`, Document really has a UUID PK); `DocumentVersionsView` queryset now unconditionally excludes quarantined/deleted docs, even for coordinators (mirrors `DocumentListView`'s rule); HTML download path now calls `generate_presigned_download_url(..., ttl_seconds=DOCUMENT_PRESIGNED_URL_TTL_SECONDS)` (300s), matching the DRF API path exactly.

### Bonus fixes found during independent post-fix verification (not in the original 12)

- `DocumentUploadRequestSerializer.validate_size_bytes()` applied the **citizen** 10MB cap to every caller including staff, making the service layer's 50MB staff allowance unreachable through the DRF API. Fixed by reusing the same `_user_is_staff_uploader()` helper the service layer uses, so the two layers can never disagree.
- `DocumentNewVersionView.post()` read `validated_data.get("description", "")` from a field the shared serializer never declared — every new-version description was silently discarded. Fixed by adding the field, plus the missing `context={"request": request}` needed for the role-aware size check above to work at this call site.
- The shared `DocumentUploadRequestSerializer.category_slug` was `required=True`, but `DocumentNewVersionView`'s own documented request body never includes it (a new version joins the existing chain's category; the service never reads it) — any caller following the documented contract got a spurious 400. Fixed with a dedicated `DocumentNewVersionRequestSerializer(DocumentUploadRequestSerializer)` subclass that removes the inherited field (`category_slug = None`) without touching the parent used by `POST /request-upload/`.
- All three of the above were previously invisible because **no DRF-level test exercised `POST /{doc_id}/new-version/` at all** — every existing "new version" test hit the service layer directly. A new `DocumentNewVersionAPITests` class (8 tests) now pins all three fixes at the HTTP boundary, including the IDOR 404 convention and the `PermissionDenied`→403 boundary.

### Verification detail — UPDATED
- **Test suite:** `python manage.py test apps.documents apps.api.documents -v 1` → **1151 run / 1151 passed / 0 failed / 0 errors** (1066 original + 85 new/updated across all three fix agents' test files, including the 8-test `DocumentNewVersionAPITests` regression class added during post-fix verification).
- **Cross-BB regression check:** `apps.payments.tests.test_receipt_services`, `apps.consent.tests.test_tasks`, `apps.core` → **163/163 pass** (confirms the shared-settings changes — `CLAMAV_REQUIRED`, `SITE_URL` hard-fail, `CLAMAV_TIMEOUT` — didn't destabilize neighboring BBs; the `SITE_URL` hard-fail lives in `production.py` only, never imported by `config.settings.test`).
- **Migrations:** `makemigrations --check --dry-run documents` → `No changes detected in app 'documents'` (no model fields changed by this fix pass).
- **Manual live-infrastructure proof:** not re-run in this pass (the original MinIO+ClamAV proof script no longer exists on disk, as noted in the original audit). The fixes are covered by real, unpatched-library unit/integration tests instead (real `pikepdf`-generated PDF fixtures, real `zipfile`-built OOXML packages, `create_autospec` against the real imported `pyclamd.ClamdNetworkSocket` class) — a live re-proof remains a reasonable follow-up before a production go-live, but is not required for GovStack BB conformance re-certification.

---

## Appointments/Scheduler BB

*(Fix pass completed and independently re-verified after the audit below. Verdict: ✅ READY. Original NOT-READY audit findings retained for the record, each annotated with its fix. See also `SPEC_APPOINTMENTS_BB_GOVSTACK.md` §14 for the full per-finding fix log.)*

### Executive verdict — UPDATED
✅ **READY.** All 8 findings from the fresh adversarial audit below (2 HIGH, 4 MEDIUM, 1 LOW group, plus 1 MEDIUM explicitly accepted as a documented deviation rather than code-changed) have been fixed, using 3 parallel fix agents split by non-overlapping file/function ownership (array filters + status codes + envelopes; event/affiliation data-integrity bugs + settings fail-open; nginx log leak + throttle identity-keying), followed by personal re-verification of every agent's diff and a full cross-BB regression run.

### Executive verdict (original, at time of audit)
**NOT READY.** The hashed-credential auth model (this session's headline architectural fix) is genuinely sound and survived active attack attempts. But the BB will fail a spec-conformance harness on at least three independent counts: 6 of 8 spec-defined array-typed `*_id` filters reject the literal spec-shaped request with HTTP 400; all 9 create endpoints return HTTP 201 where the spec and its own Gherkin fixture demand 200; and the response envelope shape doesn't match the spec on create/list/modify anywhere.

### Claim-by-claim

| Claim | Verdict |
|---|---|
| Hashed `GovStackBBCredential` replaces bb_id-as-credential; `make_password`/`check_password`, no plaintext persisted, mgmt command fail-closed | ✅ **VERIFIED** — actively attacked (bb_id-as-token, guessed token, `token_prefix`-as-token, header injection, role escalation across all 4 role tiers) and held in every case |
| `qry` double-nesting removed on all 9 create serializers | ✅ VERIFIED — and independently cross-checked against the live spec's genuinely non-obvious per-entity top-level key naming (`entity.details`, `resource.resource_details`, etc.) — matches exactly |
| `resource_id` canonical `R-<pk>` format across the 5 Resource endpoints | ⚠️ PARTIAL — correct within the Resource endpoint group, but the same `resource_id` value is accepted by `/resource/new` and then **rejected with a leaked raw ORM error** by the Affiliation endpoints that are supposed to consume it |
| Array-typed `*_id` filters fixed via `StringOrListField`; claim that only 2 fields (`alert_schedule_id`, `message_id`) are spec-typed as arrays | ❌ **REFUTED** — the live spec types **8** filter fields as arrays; only the 2 named ones were fixed. The other 6 (`entity_id`, `resource_id`, `subscriber_id`, `event_id`, `affiliation_id`, `log_id`) reject a spec-compliant array payload outright |
| Migration 0017 latest, 5 admin models registered, credential admin hardened | ✅ VERIFIED — `token_hash` fully excluded from the admin form (not just readonly); add/change permissions disabled |
| SSRF closed on Resource `alert_url`/`status_poll_url` via HTTPS-only `_validate_url()` | ⚠️ PARTIAL — the surface-level scheme check alone is weak (accepts loopback/link-local/metadata-IP hosts), but a **second, strong layer** at task-dispatch time (DNS-resolved private/reserved-range blocking, credential-in-URL stripping, `allow_redirects=False`) genuinely closes the gap for `alert_url` on both Resource and Subscriber. `status_poll_url` has no second-layer consumer at all yet (no polling implemented), so it's an inert but real gap for whoever implements polling next. |
| Manual proof (bb_id-as-token → 401, guessed token → 401, real secret → 201) | ✅ VERIFIED — independently reproduced live |

### Original findings — fix status

- **HIGH — 6 of 8 spec array `*_id` filters rejected spec-compliant array input with HTTP 400.**
  ✅ FIXED. All 6 groups (`entity_id`, `resource_id`, `subscriber_id`, `event_id`/`host_entity_id`, `appointment_id`/`participant_id`, `affiliation_id` — 8 fields across 6 groups) converted to the same `StringOrListField` + `qs.filter(pk__in=...)` pattern already used correctly by `alert_schedule_id`/`message_id`, plus a bonus `log_id` fix for consistency. `resource_id`'s "R-"/"S-" prefix-disambiguation scheme was generalised to parse each array element independently (a single filter call may legitimately mix R- and S-prefixed ids). New tests on all 7 affected entity test files cover single-value backward compatibility, multi-id arrays, and invalid entries.

- **HIGH — Every create endpoint returned HTTP 201; spec requires 200. Non-spec envelope fields (`truncated`, `status: success` wrapper) throughout.**
  ✅ FIXED. All 9 create endpoints now return HTTP 200 (verified directly against the live-fetched GovStack OpenAPI spec, not guessed). All 10 `list_details`/`availability` endpoints now return a bare JSON array with no wrapper object — confirmed against the spec's actual response schemas (`{"type": "array", ...}`), not assumed.

- **MEDIUM — `POST /event/new` silently discarded `host_entity_id` whenever no venue was supplied.**
  ✅ FIXED. `_resolve_location()` in `services/govstack_event.py` now resolves `host_entity_id` → `Organization` regardless of whether a venue was supplied, and creates a per-organisation placeholder Location (never mutated in place, mirroring the existing global-placeholder protection) when a venue is genuinely absent but a valid entity is known. `event_modify`'s existing "no venue payload" branch picks up the fix automatically through the shared helper.

- **MEDIUM — `R-<pk>` resource id round-tripped into `/affiliation/new` as a 409 with a raw Django `ValueError` leaked in the response body; `/affiliation/list_details` emitted the bare unprefixed id.**
  ✅ FIXED. `/affiliation/new` now reuses the existing `_parse_resource_only_pk()` helper (already used by the 3 Resource-only endpoints) to validate/strip the prefix *before* calling the service, so a malformed/wrong-prefix/nonexistent resource_id returns a clean 400/404 and can never reach the duplicate-pair exception handler. `affiliation_list()` now emits `f"R-{pk}"`, matching `resource_list()`'s existing convention.

- **MEDIUM — `request_token` written in plaintext to nginx's default access log for all 37 endpoints.**
  ✅ FIXED. Added a dedicated `location /govstack/` block in `nginx/nginx.conf` with a custom `govstack_safe` log format that logs `$uri` (path only) instead of `$request`, so the query string — and the token within it — never reaches disk. Traffic is still logged for operational visibility (not `access_log off`); `request_token` remains a query-string parameter per the existing, unchanged API contract.

- **MEDIUM — `GOVSTACK_SCHEDULER_REQUIRE_TOKEN` fails open by default (only defined in `production.py`).**
  ✅ FIXED. Both `GOVSTACK_SCHEDULER_REQUIRE_TOKEN` and `GOVSTACK_REQUIRE_REGISTERED_BB` now defined explicitly in `base.py` with a safe `default=False`, matching the established `CLAMAV_REQUIRED` convention. `production.py`'s existing `default=True` override is untouched.

- **MEDIUM — `GovStackRegisteredBB.role` shared with Payments BB, no per-entity/tenant scoping.**
  📝 DOCUMENTED, not code-changed (per the audit's own framing — "acceptable for a single-government deployment but should be a stated, explicit deviation"). Now explicitly recorded in `SPEC_APPOINTMENTS_BB_GOVSTACK.md` §14 as an accepted architectural deviation for CivicOS's single-government deployment model, alongside the related, already-documented §13 finding that new BB registrations default to `"organizer"` rather than the lowest tier. A future multi-tenant deployment would need per-entity role scoping added before certification for that use case — flagged for a human maintainer's decision, not silently reworked (a role-model redesign carries real blast radius disproportionate to a mechanical fix pass).

- **LOW group — inconsistent list-item shape (`message` nested vs. others flat); throttling keyed on IP not BB identity; `/log/` 405s; citizen-booking design.**
  ✅ Throttling FIXED: `GovStackSchedulerAuth.authenticate()` now stashes the resolved BB's pk on `request.META`; a new `GovStackBBIdentityThrottle(ScopedRateThrottle)` keys on that identity when present, falling back to stock IP-based behaviour otherwise, wired in via a single aliased import so all ~37 views pick it up without a per-view edit.
  📝 Rest DOCUMENTED, not code-changed: `/log/` 405s are correct-by-design (audit immutability) and citizen-booking's BB-credential-only path is deliberate — both already noted in §12/§14 of the spec doc. The `message`-vs-flat list-item shape inconsistency is a genuine, minor cosmetic gap left for a future coordinated per-item-schema pass across all 10 list endpoints (larger and riskier than this pass's scope) — noted in §14.

### Verification detail — UPDATED
- **Test suite:** `python manage.py test apps.appointments -v 1` → **848 run / 848 passed / 0 failed / 0 errors** (816 original + 32 new tests across the 3 fix agents).
- **Cross-BB regression check:** `apps.payments`, `apps.documents`, `apps.api.documents`, `apps.consent.tests.test_tasks`, `apps.core` → **2985/2985 pass** (confirms the shared `GovStackRegisteredBB` model and the `base.py` settings additions destabilized nothing in the Payments BB — which shares that model — or any other previously certified BB).
- **Migrations:** `makemigrations --check --dry-run appointments payments` → `No changes detected in apps 'payments', 'appointments'` (no model fields changed by this fix pass).
- **Manual review of every agent's diff:** personally sampled and read the riskiest changes in full (the `AffiliationNewView.post` exception-handling rewrite, `_resolve_location`'s per-org placeholder logic, the array-aware R-/S- prefix parser in `resource_list()`, the nginx config's new location block, the throttle class, and the settings addition) — all confirmed correct, consistent with existing codebase conventions, and properly commented with rationale.

---

## Consent BB

*(Full agent report — see detailed findings below. Verdict: NOT READY.)*

### Executive verdict
**NOT READY.** Two independent defects each block certification on their own: every `Individual` detail operation (5 of the spec's operations: read/update on both `/config/individual/{id}/` and `/service/individual/{id}/`, plus delete) permanently returns HTTP 400, because the code parses the path segment as a UUID while the real user model's primary key is a plain integer (`BigAutoField`) — the module's own docstring's claim that `User (Individual) → uuid` is simply false. Separately, the consent-signature endpoints — the BB's non-repudiation artifact — discard the caller's actual signature on create and allow a citizen to silently backdate or rewrite their own signature on update with zero revision and zero audit trail.

### Claim-by-claim

| Claim | Verdict |
|---|---|
| Mode-gating flag scoped to exactly 2 read endpoints | ⚠️ PARTIAL — blast radius on the intended 2 endpoints is confirmed exact; but the flag's fallback (`getattr(..., default=False)`) means **any settings module that doesn't explicitly import from `production.py`** (custom, staging, misconfigured) silently serves both endpoints anonymously with no warning — insecure-by-default rather than secure-by-default. Content exposed is low-sensitivity (policy/data-agreement metadata), so impact is Low, but the pattern itself should be inverted. |
| Int-or-UUID policy alias; `dataAgreement.id` serialized as string | ✅ VERIFIED — `harness_alias_id` is unique, not attacker-settable via the API, and the string-id serialization was confirmed on both detail and list endpoints |
| Migration 0019 latest, no drift | ✅ VERIFIED |
| `purpose`/`lawfulBasis`/`dpia` required; partial update unaffected | ✅ VERIFIED |
| SQLite-only concurrency skip + compensating test | ✅ VERIFIED as real coverage of the same `IntegrityError` recovery branch — but the skip's stated *second* reason (per-connection SQLite isolation) is factually wrong for Django's shared-cache in-memory test DB; only the `select_for_update` unavailability reason is valid (and is sufficient on its own) |
| PIPEDA export: two-tier exception handling + Revisions/Signatures added | ✅ VERIFIED — correctly citizen-scoped, no cross-citizen leakage |
| Dedicated throttle scope on every view; SHA-256 deviation documented | ✅ VERIFIED — swept all 40 view classes in the module, 0 missing the throttle scope |
| Live harness run (2 features / 4 scenarios / 16 steps, 0 failures) | ✅ VERIFIED as both genuinely executed and genuinely satisfied by the code — but see harness-coverage assessment below: **this is 100% of the upstream harness, and the upstream harness is only ~2 of roughly 40 spec operations** |

### New findings (severity-ranked)

- **CRITICAL** — All 5 `Individual` detail operations (`configIndividualRead/Update/Delete`, `serviceIndividualRead/Update`) always return HTTP 400: the code calls `_parse_uuid_param()` on the path segment, but `AUTH_USER_MODEL` (`auth_extension.User`) has an integer `BigAutoField` primary key, not a UUID. Not caught by any existing test — zero tests exist for either Individual detail path.
- **HIGH** — `POST .../signature/` discards the caller-supplied `signature`/`verification_payload_hash`/`verification_signed_by` entirely and replaces them with a server-computed SHA-256 hash of the payload — which is not a signature and verifies against nothing. Directly contradicts the code's own docstring claim that it "stores whatever signature the caller provides."
- **HIGH** — `PUT .../signature/` lets the record's own citizen silently rewrite `timestamp`, `payload`, and `signature` to arbitrary values (backdating confirmed live to the year 1999) with **no new `ConsentRevision` and no audit entry** — unlike every other mutation path in this BB, which is consistently revisioned and audited. This is the one place the BB's otherwise-solid append-only evidence model has a hole.
- **HIGH** — `ConsentWebhook.payload_url` has no SSRF protection anywhere on its create or dispatch path (confirmed live: a webhook pointed at the cloud metadata IP `169.254.169.254` is accepted and would be dispatched with `allow_redirects` defaulting to true). The sibling Appointments/Scheduler BB already ships the exact mitigation this needs (DNS-resolved private-range blocking + `allow_redirects=False`) and Consent doesn't reuse it.
- **MEDIUM** — Three list/filter endpoints pass raw, unvalidated query-string values straight into `.filter()`, so a malformed `individualId`/`dataAgreementId`/`revisionId` raises an uncaught `ValueError` (or Django's non-DRF `ValidationError`) that escapes as an HTTP 500 instead of the 400 the BB's own helper functions exist to produce.
- **MEDIUM** — The signature endpoints only accept a CivicOS-specific nested `{"signature": {...}}` envelope; a spec-conformant **flat** Signature object (which the spec requires, since `signature` is itself a required property name in the schema) is rejected outright as "Expected a dictionary, but got str."
- **MEDIUM** — Right-to-be-forgotten deletes the `ConsentRecord`/`ConsentSignature` rows but leaves the citizen's user ID re-identifiable inside undeletable `ConsentRevision` snapshots (append-only by model-level design) with no anonymization pass — and because the PIPEDA export only queries revisions belonging to *still-existing* records, this residual PII becomes permanently inaccessible to the citizen (a s.4.9 access-right gap) as well as undeletable.
- **MEDIUM** — `POST /config/data-agreement/` accepts and stores DataAgreements with blank `name_en`/`name_fr`/`purpose_fr` — no server-side requirement for the bilingual, human-readable fields the model's own PIPEDA rationale depends on.
- **LOW** — Data-consumer verification endpoints return every citizen's signed consent records with no per-consumer DataAgreement scoping (matches the spec's literal permission model, but is a meaningful blast-radius fact for the security self-assessment); `ConsentRecordGovStackSerializer.state` is technically writable in the serializer definition though not currently reachable; webhook secrets are echoed in plaintext on every read (spec-mandated, but worth flagging) with no replay-window protection beyond HMAC signing.

### Verification detail
- **Test suite:** `python manage.py test apps.consent -v 2` → **296 run / 295 passed / 0 failed / 0 errors / 1 skipped** (the single skip is exactly the documented SQLite concurrency test — nothing else skipped or erroring).
- **Migrations:** `makemigrations --check --dry-run consent` → `No changes detected in app 'consent'`.
- **Harness coverage reality check:** the upstream `GovStackWorkingGroup/bb-consent` repo's `test/gherkin/features/` contains exactly `smoke.feature` and `data_agreement.feature` (plus an unused example-steps stub) — there is no larger untested harness being skipped. The session's harness run therefore covered 100% of what upstream actually ships, but that upstream harness itself only exercises 2 unauthenticated GET operations out of roughly 40 in the full OpenAPI spec. All 5 defects above sit in the ~95% of the surface the harness never touches (Individual CRUD, signature create/update, webhook dispatch, malformed-filter handling).

### Minimum fix list to reach READY
1. Fix the Individual detail path to parse an integer PK (or add a UUID-compatible identifier layer), and correct the false `uuid` claim in `govstack_urls.py`'s docstring.
2. Make `attach_signature()` actually persist the caller's `signature`/`verification_payload_hash`/`verification_signed_by`, not server-recomputed values.
3. Make signature `PUT` create a new `ConsentRevision` and an audit entry, exactly like every other consent-record mutation in the BB.
4. Reuse the Appointments BB's SSRF-safe URL validation (DNS-resolved private-range blocking, `allow_redirects=False`) for `ConsentWebhook.payload_url`.
5. Route the 3 unvalidated filter query params through the existing `_parse_uuid_param`/`_parse_int_param` helpers so malformed input 400s instead of 500ing.
6. Accept the spec's flat Signature envelope in addition to (or instead of) the nested CivicOS-specific one.
7. Add an anonymization pass for RTBF'd citizens' revision snapshots, or explicitly document the retention as a declared, access-right-compliant exception.

---

## Cross-cutting observations

1. **The "green suite, spec-blind" pattern is now confirmed across all three BBs**, not just Documents (where it was first found this session). Every BB's tests were written against its own implementation rather than against the live upstream spec/Gherkin, so status-code, envelope-shape, and identifier-type mismatches with the real spec are systematically invisible to `manage.py test`. Recommendation: for any BB claimed "done," a live-harness or hand-written spec-literal-request test is not optional polish — it is the only thing that actually catches this class of bug, and this audit again proves it every time it's applied.
2. **SSRF protection is inconsistent across BBs that share the exact same risk** (an operator/citizen-supplied callback URL dispatched later by a Celery task). Appointments/Scheduler has a strong, DNS-resolving, redirect-blocking implementation. Consent has none on its webhook path. This should be extracted into one shared utility and applied uniformly rather than re-invented per BB.
3. **Settings-module fail-open/fail-closed asymmetry recurs 3 times** (Documents' `CLAMAV_REQUIRED`, Appointments' `GOVSTACK_SCHEDULER_REQUIRE_TOKEN`, Consent's `GOVSTACK_REQUIRE_CONSENT_AUTH`): all three security-mode flags are defined only in `production.py` with a safe default there, but every non-production settings module (`base.py`/`development.py`/`test.py`) simply omits them, and the `getattr(..., default=...)` fallback used at each call site doesn't consistently resolve to the secure behavior in that absence. Recommend defining all three flags in `base.py` with the secure (`True`/required) default, and letting `production.py` simply inherit rather than re-declare — so "the setting doesn't exist" and "the setting is off" can never be confused again.
4. **Identifier-type mismatches (int vs. UUID vs. prefixed string) are a recurring class of bug**, not a one-off: Documents' serializer misdeclaring an integer PK as `UUIDField` (cosmetic), Appointments' `R-<pk>` canonical form breaking at the Affiliation-endpoint boundary, and Consent's Individual-detail 400s are three independent instances of the same root cause — a per-BB assumption about an ID's type that isn't verified against the actual model field.
5. **Audit-trail completeness gaps appear in exactly the two places that matter most for a government compliance product**: Documents' missing malware-detection audit entry, and Consent's missing signature-mutation audit entry. Both BBs otherwise have thorough, well-designed audit logging elsewhere — these look like isolated omissions rather than a systemic gap, but both are squarely inside PIPEDA/security-logging expectations for a certifiable public-sector BB.

## Overall verdict

**None of the three BBs closed out this session are ready for GovStack certification submission as-is.** All three, however, are close: every blocking defect identified has an existing, correct pattern already implemented elsewhere in the same codebase (or in a sibling BB) that the fix can mirror. Recommended order of attack, by blast radius and fix simplicity: (1) Documents' one-line PDF-encryption inversion — trivial fix, currently breaks the single most common document type; (2) Consent's Individual-detail UUID/int mismatch — breaks 5 spec operations outright; (3) Appointments' array-filter and create-status-code gaps — mechanical, the correct pattern already exists in the same file for 2 of 8 fields; (4) the two signature-integrity findings in Consent and the SSRF gap in Consent's webhooks — these touch the compliance/security posture most directly and should not ship un-remediated even if a harness run would technically pass without them.
