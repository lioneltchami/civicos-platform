# CivicOS — GovStack BB Certifiability Readiness Report

**Date:** 2026-07-22  
**Reviewed by:** 5 parallel deep-review agents  
**Codebase:** `/Users/lionel/builders/govstack`  
**Total tests found:** ~5,220 across 134 test files, 14 apps

---

## Executive Summary

CivicOS is a production-grade Django 5.2 + Wagtail government platform with 16 building block apps. The core platform is approximately **90% production-ready** with comprehensive models, service layers, views, and test coverage across most BBs. Two GovStack-specification BBs are fully implemented. One GovStack-specification BB (Appointments) is ~38% complete with no citizen-facing surface. The platform backbone (auth, audit, Celery, security hardening) is strong with a handful of specific risks to resolve before GovStack certification submission.

---

## BB Inventory & Status

### Tier 1 — GovStack Specification BBs (Have SPEC_*.md)

#### 1. GovStack Payments BB — `apps/payments/govstack_*.py`
**Verdict: NEEDS WORK — 1 Blocker, 1 High Risk**

Waves 1–5 fully implemented. All 14 GovStack endpoints registered. 337 GovStack-specific tests. All 9 harness-tested features architecturally sound. Security invariants solid (Fernet encryption, append-only audit, `select_for_update` on all voucher transitions).

**Blocker — P0 (2 hours):** `apps/payments/management/commands/seed_govstack_vouchers.py` does not exist. The GovStack harness expects pre-seeded voucher serials (`5550–5560`, `6004`, `60000–60001`) in PREACTIVATED state. Without this, all Wave 4 voucher activation/redemption/status harness scenarios will return HTTP 452 (InvalidVoucherSerial) and fail. The serial generator produces 6-digit numbers (100,000–999,999), so seed serials cannot be auto-generated — they must be created directly.

**High Risk — P1 (1–2 days):** `X-Callback-URL` is captured for bulk-payment and prepayment-validation but no Celery task (`govstack_tasks.py`) delivers the callback. If the harness verifies async callback POSTs (Gherkin scenarios B8/B9-class), Feature 4 will fail. If the harness only polls `prepayment-validation-response`, current behavior (PENDING with ResponseCode "00") passes.

**Medium — P2 (2 hours):** Test file names (`test_govstack_wave2.py` etc.) don't match spec naming convention. Does not affect harness results but affects spec audit.

**Not yet harness-tested (Wave 5 — P2G):** `BillInquiryView`, `BillTransferRequestView`, `TransferRequestStatusView`, `MarkBillPaidView` — fully implemented, 100 tests, ready when GovStack adds P2G coverage.

**GovStack Harness Feature Assessment:**

| Feature | Status | Risk |
|---|---|---|
| 1 — register-beneficiary | LIKELY PASS | None |
| 2 — update-beneficiary-details | LIKELY PASS | None |
| 3 — bulk-payment | LIKELY PASS | None |
| 4 — prepayment-validation | LIKELY PASS / FAIL | Depends on callback verification |
| 5 — voucher_preactivation | WILL FAIL if not seeded | P0 blocker |
| 6 — voucher_activation | WILL FAIL if not seeded | P0 blocker |
| 7 — voucher_redemption | WILL FAIL if not seeded | P0 blocker |
| 8 — voucherstatuscheck (GET) | WILL FAIL if not seeded | P0 blocker |
| 9 — voucherstatuscheck (PATCH/cancel) | WILL FAIL if not seeded | P0 blocker |

---

#### 2. Document Management BB — `apps/documents/`
**Verdict: COMPLETE — Tagged v0.8.0 — Must resolve 3 items before v1.0**

All 7 waves fully implemented. 969 tests across 21 test files. PIPEDA invariants enforced throughout with a dedicated `test_pipeda.py` module. Security model is defence-in-depth: TOCTOU guards, append-only audit, `select_for_update` on all status transitions, legal hold unconditional block.

**Wave completion:** Foundation → Upload pipeline (ClamAV, ZIP bomb, magic bytes) → Download (access tokens, IP masking) → Retention/disposal (dual-atomic-block hard delete, TOCTOU-safe soft delete) → Views/templates (citizen + staff, bilingual WCAG 2.1 AA) → Integration migrations (5 model FK replacements + 7 GenericRelation additions including bonus appointments integration) → Full test suite.

**Must resolve before v1.0:**
- **Encrypted/password-protected file rejection (active security gap):** Password-protected ZIPs and PDFs pass all validation layers — ClamAV cannot scan them. No rejection logic in `confirm_upload()`. A malicious actor could upload encrypted malware that reaches ACTIVE status.
- **Notification email templates missing:** `notify_expiring_documents`, `document_scan_clean`, and quarantine alerts reference `subject_key` values but no template files found under `templates/notifications/email/document_*/`. Notifications will silently fail on first production run.
- **DRF REST API not delivered:** Spec §18 defined `/api/v1/documents/` JSON endpoints. Implementation delivers HTML Django views only. Mobile clients or external integrations expecting JSON need this addressed or explicitly documented out of scope.

**Security invariants — all PASS:**
- `_storage_key` never in templates/context/responses ✅
- `original_filename` never in audit event_detail ✅
- IDOR: Citizens receive 404 (not 403) for non-owned PKs ✅
- `legal_hold=True` blocks all automated disposal unconditionally ✅
- Quarantine signal contains no uploader PII ✅
- PIPEDA retention/disposal pipeline with Privacy Act s.6(1) `retain_until` floor ✅

**Open items from §26:** Encrypted ZIP/PDF rejection (active gap), PDF CDR deferred to v1.1, e-signing deferred to Signatures BB, full-text search deferred to Search BB, multi-file upload deferred to v0.8.1.

---

#### 3. Appointments BB — `apps/appointments/`
**Verdict: PARTIAL — ~38% complete — No citizen-facing surface exists**

The data layer and booking service logic (the hardest work) are solid. Citizens cannot interact with the system in any channel today.

**Wave completion:**

| Wave | Description | Status |
|---|---|---|
| 1 | Foundation models + admin | COMPLETE |
| 2 | Availability engine (slot generation, templates) | COMPLETE |
| 3 | Core booking service layer | ~85% (receivers are stubs) |
| 4 | Waitlist + Queue services | ~20% (models only, no services) |
| 5 | Views + Templates | NOT STARTED (urlpatterns = []) |
| 6 | Notifications + iCal + Reminders | NOT STARTED (0 email templates) |
| 7 | Virtual Appointments | NOT STARTED (no services/video.py) |
| 8 | DRF API + Integration Tests | NOT STARTED (no serializers) |
| 9 | Review, Hardening, Tag | NOT STARTED |

**What IS complete:** `Organization`, `SchedulingPolicy`, `ServiceType`, `AppointmentType`, `Location`, `Resource`, `StaffProfile`, `AvailabilityTemplate`, `StaffException`, `Slot`, `Booking`, `Attendee`, `BookingAuditLog`, `ClientNoShowRecord`, `WaitlistEntry`, `QueueEntry` — all modelled and migrated. Full booking service layer (`create_booking`, `confirm_booking`, `cancel_booking`, `reschedule_booking`, `mark_no_show`, `complete_booking`, `reject_booking`) with `transaction.atomic()`, `SELECT FOR UPDATE`, audit logs, and signals. 359 tests (models, availability, booking service).

**What is missing (blocks citizen booking):** No views, no templates, no forms.py, no URL handlers (`urlpatterns = []`), no notification emails, no waitlist promotion on cancellation, no DRF API, no consent gate in `create_booking()`.

**Known deviations:** `Organization` lives in `apps.appointments` not `apps.core`; `PENDING_BOOKING_TIMEOUT_MINUTES` is 15 (spec: 30); `booking.work_item_id` not back-written after WorkItem creation in receiver.

**Work remaining:** Waves 5–9 (views, templates, notifications, iCal, virtual appointments, DRF API, full test suite, hardening). Estimated: comparable to Waves 1–4 in scope.

---

### Tier 2 — Core Platform BBs (Internal, No GovStack Spec)

#### 4. GovStack Consent BB — `apps/consent/`
**Verdict: PRODUCTION-READY — 252 tests**

Full GovStack v1.3.0 API surface (`/config/`, `/service/`, `/audit/` namespaces). 8 models including append-only `ConsentRevision` chain, HMAC-SHA256 webhooks, `right_to_be_forgotten()` (PIPEDA s.4.3.6). 15 migrations. Dual OpenAPI schemas. `DataExportRequest` → Documents BB FK. **Gap:** `dispatch_webhook()` is synchronous HTTP — should move to Celery task.

#### 5. Workflows BB — `apps/workflows/`
**Verdict: PRODUCTION-READY — 113 tests**

Full state machine enforcement via `VALID_TRANSITIONS` dict. `WorkItemHistory` is append-only (raises on UPDATE). `select_for_update` on assignment. SLA breach detection via `skip_locked=True` bulk update. Signal-driven notifications. GenericFK for cross-BB attachment.

#### 6. Citizen Portal BB — `apps/portal/`
**Verdict: PRODUCTION-READY — 172 tests**

Retry-safe `GS-YYYY-XXXXXX` reference number generation. `StatusUpdate` append-only. IDOR-safe `get_citizen_requests()` always scoped to citizen. `ATOMIC_REQUESTS`-safe notification dispatch via `on_commit`. Transaction tests confirm no phantom side effects.

#### 7. Notifications BB — `apps/notifications/`
**Verdict: SOLID — 77 tests — SMS delivery absent**

Email delivery via GC Notify / SendGrid with language switching via `translation.override()`. Signal receivers wired for portal, workflows, consent events. `NotificationChannel.SMS` defined and settings keys present but no templates or delivery path implemented.

#### 8. Dynamic Forms BB — `apps/forms/`
**Verdict: PRODUCTION-READY — 103 tests**

`FormPage.process_form_submission()` captures IP + consent + expiry. `FormSubmission.redact_pii()` replaces PII fields with `[REDACTED]`. Auto-injects mandatory consent BooleanField when `consent_text` is set. Fires `form_submission_received` signal for workflow integration.

#### 9. REST API BB — `apps/api/`
**Verdict: PRODUCTION-READY — 115 tests**

RS256 JWT (asymmetric). `CivicOSTokenAuthentication` with active-user guard. `drf-spectacular` OpenAPI schema generation. Cursor pagination. Scoped throttling (citizen: 300/hr, anon: 60/hr, GovStack: 100/min). Sub-modules for portal, notifications, workflows, volunteers.

#### 10. Backoffice Admin BB — `apps/backoffice/`
**Verdict: PRODUCTION-READY — 121 tests**

Pure controller layer over other BBs' models. Staff dashboard, service request management, work item queue, citizen management, audit log viewer, reports, consent data request views, staff notifications. `StaffRequiredMixin` enforces `is_staff` on all views.

#### 11. Volunteers BB — `apps/volunteers/`
**Verdict: PRODUCTION-READY — 831 tests (most comprehensively tested BB)**

13 models. 6 service modules. CRA T3010 category mapping. Honorarium hard enforcement: $450 alert, $500 T4A threshold, $1,000 hard block in `model.clean()`. `ScreeningRecord` stores only `verified_clear` boolean (never the check itself). Document BB FK on Certification, Honorarium, ScreeningRecord. Consent BB FK on `photo_consent`. 16 test files covering all service modules + integration.

#### 12. Reports BB — `apps/reports/`
**Verdict: PRODUCTION-READY — 485 tests**

CRA 7-year retention enforced in admin (blocks deletion). `ExportRecord` stores `actor_pk` (not email), masked IP, row count — no file content server-side. WeasyPrint PDF export. Nightly Celery task for `ReportSnapshot` pre-computation. `unique_together` on `(report_type, period_year, period_month)`.

#### 13. Authentication BB — `apps/auth_extension/`
**Verdict: PRODUCTION-READY — 75 tests**

Email-only login (`USERNAME_FIELD = "email"`, `username = None`). Argon2 primary hashing, 12-char minimum. django-otp MFA enforced on Django admin (`OTPAdminSite`) and Wagtail CMS (`WagtailMFAMiddleware`). JWT RS256 with refresh rotation + blacklisting. 30-minute session timeout.

#### 14. Audit Trail — `apps/audit/`
**Verdict: SOLID — 19 tests (under-tested for criticality)**

SHA-256 hash chain with `select_for_update` serialization. `verify_audit_chain` management command. 27 `AuditEventType` enum values. `AuditLogEntry.save()` raises `ValueError` on UPDATE. **Gap:** `QuerySet.update()` and `QuerySet.delete()` bypass immutability guards — no PostgreSQL trigger exists yet. 19 tests is insufficient for a security-critical tamper-evident log.

#### 15. Core Utilities — `apps/core/`
**Verdict: PRODUCTION-READY — 83 tests**

`EncryptedCharField` (Fernet AES-128-CBC, `MultiFernet` key rotation). `RequestIDMiddleware`, `AuditMiddleware`, `WagtailMFAMiddleware`, `GovStackHeaderMiddleware`. Health check endpoints (`/health/`, `/health/live/`, `/health/ready/`). CMS has **zero tests** (`apps/cms/tests/__init__.py` only).

---

## Platform Infrastructure

### Test Suite
- **~5,220 total test functions** across 134 files
- **Coverage threshold:** 80% (`fail_under = 80` in `pyproject.toml`)
- **Test DB:** SQLite `:memory:` (fast but masks PostgreSQL-specific behavior — `SELECT FOR UPDATE`, concurrent chain integrity)
- **Known gaps:** CMS has zero tests; `apps/core/middleware.py` has no dedicated tests

### Celery / Async
- Redis broker (DB 1), `django-celery-results` backend (DB)
- 7 queue routing: `default`, `webhooks`, `receipts`, `payments`, `reports`, `volunteers`, `documents`, `appointments`
- 32 tasks across 11 task files
- Global soft limit 300s / hard limit 360s; critical tasks use `acks_late=True`, `reject_on_worker_lost=True`
- Beat schedule: only 4 entries registered statically (Wave 5 appointment tasks + consent cleanup). 5 Wave 4 appointment tasks missing from Beat.

### Settings Architecture
Clean 4-way split: `base.py → development.py / test.py / production.py`. `production.py` raises `ImproperlyConfigured` for missing JWT keys, allowed hosts, Wagtail base URL. `DEBUG=False` hardcoded. Fernet keys use separate `FERNET_KEYS` and `VOLUNTEER_SIN_FERNET_KEYS` — never `SECRET_KEY`.

### Security Hardening
HSTS 1-year + preload, `SECURE_SSL_REDIRECT`, `X_FRAME_OPTIONS=DENY`, `SECURE_CONTENT_TYPE_NOSNIFF`, `django-csp` with nonces, Argon2, `SESSION_COOKIE_HTTPONLY`, `SESSION_COOKIE_SECURE`, Sentry `send_default_pii=False`.

---

## GovStack Platform Requirements

| Requirement | Status | Notes |
|---|---|---|
| `/health/` endpoint | ✅ | Checks DB, cache, Stripe; returns 503 on failure |
| OpenAPI schema | ✅ | `drf-spectacular` at `/api/v1/schema/` and `/api/v1/consent/schema/` |
| Rate limiting | ✅ | Citizen 300/hr, anon 60/hr, GovStack 100/min |
| HTTPS enforced | ✅ | `SECURE_SSL_REDIRECT=True`, HSTS 1yr |
| `X-Request-ID` header | ✅ | `RequestIDMiddleware` (UUID, echoed in response) |
| `X-GovStack-BB-Version` | ✅ | `GovStackHeaderMiddleware` injects `1.3.0` |
| `ATOMIC_REQUESTS` | ✅ | All requests transactional |
| CSP headers | ✅ | `django-csp`, strict policy, nonces |
| `X-Frame-Options DENY` | ✅ | Configured |
| `SECURE_REFERRER_POLICY` | ✅ | `strict-origin-when-cross-origin` |
| BB-to-BB auth whitelist | ⚠️ | `IsTrustedSourceBB` accepts ANY non-empty header — no registered BB table yet |
| Voucher JWT enforcement | ⚠️ | `GOVSTACK_VOUCHER_REQUIRE_JWT` defaults `False` — not overridden in `production.py` |
| `AllowAnyBB` (voucher pre/activate) | ⚠️ | Unconditionally `True` — harness-only stub, must harden before production |
| CORS policy | ⚠️ | No `django-cors-headers` — BB-to-BB is server-to-server so acceptable, but Swagger UI / browser-based harness will fail cross-origin |
| Audit log DB-level immutability | ❌ | No PostgreSQL trigger; `QuerySet.update()` bypasses model guard |

---

## Top 5 Platform-Level Certification Risks

### Risk 1 — CRITICAL: GovStack BB-to-BB auth is harness-mode permissive
`AllowAnyBB.has_permission()` → always `True`. `IsTrustedSourceBB` → any non-empty string passes. `GOVSTACK_VOUCHER_REQUIRE_JWT` → not set to `True` in `production.py`. GovStack BB registry table does not exist. These must be secured before production deployment. The harness correctly works with these stubs but this is a go-live blocker.

### Risk 2 — HIGH: GovStack Payments and Volunteers have zero audit trail coverage
Neither `apps/payments/` (Stripe and GovStack flows) nor `apps/volunteers/` call `record_event()` for any domain operations. Beneficiary registration, honoraria creation, voucher preactivation, PaymentIntent creation — none produce `AuditLogEntry` records. GovStack Payments BB spec §17 requires audit of these events. This will generate certification findings.

### Risk 3 — HIGH: Audit log immutability not enforced at database level
`AuditLogEntry.models.py` explicitly documents: "QuerySet.update() and QuerySet.delete() bypass these guards." A data migration or admin queryset bulk operation could silently modify or delete audit entries without detection until `verify_audit_chain` is run. PostgreSQL trigger migration needed.

### Risk 4 — MEDIUM: Appointments BB audit is deferred with TODO comments
`apps/appointments/services/slots.py` lines 217 and 293 contain `# TODO Wave 3: pass actor to AuditLog.record_event(`. Booking creation, cancellation, and slot operations are not audited. This is a PIPEDA 4.5.3 gap (accountability principle — actions involving personal information must be traceable).

### Risk 5 — MEDIUM: SQLite test database masks PostgreSQL-specific behaviors
`SELECT FOR UPDATE` tests are `@skipUnlessDBFeature("has_select_for_update")` — skipped in CI. PostgreSQL CHECK constraints, transaction isolation semantics, and concurrent audit chain integrity are not validated in the standard test run. A PostgreSQL-backed CI target (Docker) is needed before certification submission.

---

## Prioritized Action Plan

### P0 — Must fix before GovStack harness run (~4 hours)
1. Write `apps/payments/management/commands/seed_govstack_vouchers.py` (creates pre-seeded serials `5550–5560`, `6004`, `60000–60001` in PREACTIVATED state)

### P1 — Assess/fix before harness run (~1–2 days)
2. Determine whether GovStack harness verifies `X-Callback-URL` delivery for `prepayment-validation`. If yes, implement `apps/payments/govstack_tasks.py` Celery task for async batch processing and callback dispatch.

### P2 — Must fix before v1.0 production deployment
3. Add encrypted/password-protected file rejection in Documents BB `confirm_upload()` (§26 item 3 — active security gap)
4. Create notification email template files for Documents BB (expiry, scan-clean, quarantine alerts)
5. Set `GOVSTACK_VOUCHER_REQUIRE_JWT=True` in `config/settings/production.py`
6. Implement GovStack registered-BB whitelist table to replace `IsTrustedSourceBB` stub
7. Move `apps/consent/services.dispatch_webhook()` to a Celery task (currently synchronous, blocks request thread)

### P3 — Before certification audit submission
8. Add PostgreSQL trigger migration for `AuditLogEntry` to enforce immutability at DB level
9. Wire `apps.audit.record_event()` calls into GovStack Payments views (beneficiary register/update, voucher preactivation/activation/redemption, P2G transfers)
10. Wire `apps.audit.record_event()` calls into Volunteers BB (honorarium creation, application approval, screening records)
11. Add PostgreSQL CI target to validate `SELECT FOR UPDATE` concurrency tests that are currently skipped

### P4 — Ongoing (Appointments BB, Waves 5–9)
12. Implement `apps/appointments/services/waitlist.py` and `services/queue.py` (Wave 4 service layer)
13. Implement `apps/appointments/views/` (citizen + staff), forms.py, templates (Wave 5)
14. Implement notifications, iCal builder, reminder tasks, SMS (Wave 6)
15. Implement virtual appointment join link via video provider integration (Wave 7)
16. Implement DRF serializers + API views for appointments (Wave 8)
17. Full adversarial review, fix defects, tag v0.9.0 (Wave 9)

---

## Summary Table

| Building Block | Spec | Tests | Completion | GovStack Certifiable |
|---|---|---|---|---|
| GovStack Payments BB | SPEC_GOVSTACK_PAYMENTS_BB.md | 337 | 100% (5 waves) | NEEDS WORK (1 blocker) |
| Document Management BB | SPEC_DOCUMENT_MANAGEMENT_BB.md | 969 | 100% (7 waves) | N/A (internal BB) |
| Appointments BB | SPEC_APPOINTMENTS_BB.md | 359 | ~38% (waves 1-3) | NOT READY |
| GovStack Consent BB | (inline docstrings) | 252 | 100% | SOLID |
| Workflows BB | (inline docstrings) | 113 | 100% | SOLID |
| Citizen Portal BB | (inline docstrings) | 172 | 100% | SOLID |
| Notifications BB | (inline docstrings) | 77 | 90% (SMS absent) | SOLID |
| Dynamic Forms BB | (inline docstrings) | 103 | 100% | SOLID |
| REST API BB | (inline docstrings) | 115 | 100% | SOLID |
| Backoffice Admin BB | (inline docstrings) | 121 | 100% | SOLID |
| Volunteers BB | docs/volunteer-spec.md | 831 | 100% | SOLID (audit gap) |
| Reports BB | docs/analytics-spec.md | 485 | 100% | SOLID |
| Authentication BB | (inline docstrings) | 75 | 100% | SOLID |
| Audit Trail BB | (inline docstrings) | 19 | 90% (no DB trigger) | NEEDS HARDENING |
| Core Utilities | (inline docstrings) | 83 | 100% | SOLID |
| CMS | (Wagtail built-in) | 0 | N/A | N/A |
| **TOTAL** | | **~5,220** | | |
