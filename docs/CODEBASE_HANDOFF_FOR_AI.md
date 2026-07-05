# CivicOS — Codebase Handoff for AI

**Last updated:** 2026-07-02  
**Git HEAD:** 9f86c52  
**Test count:** 3,131 collected (707 volunteers app)

This document is a complete technical reference for an AI agent or developer picking up this codebase. Read this before touching anything.

---

## 1. Project Layout

```
civicos/
├── config/
│   ├── celery.py               # Celery app + queue routing
│   ├── settings/
│   │   ├── base.py             # Shared settings
│   │   ├── development.py
│   │   ├── production.py       # Hardened; extends base
│   │   └── test.py             # SQLite, no Celery workers
│   ├── urls.py                 # Root URL conf
│   └── wsgi.py
├── apps/
│   ├── api/                    # REST API BB (includes api/volunteers/)
│   ├── audit/                  # Audit log
│   ├── auth/                   # (unused stub — real auth is auth_extension)
│   ├── auth_extension/         # Custom User model + MFA + allauth adapter
│   ├── backoffice/             # Staff dashboard BB
│   ├── cms/                    # Wagtail CMS BB
│   ├── consent/                # Consent management BB
│   ├── core/                   # Health checks, logging, Sentry hooks, middleware
│   ├── forms/                  # Dynamic forms BB
│   ├── notifications/          # Notifications BB
│   ├── payments/               # Payments BB (fees + donations + receipts)
│   ├── portal/                 # Citizen portal BB
│   ├── reports/                # Analytics & Reporting BB
│   ├── volunteers/             # Volunteer Management BB
│   └── workflows/              # Staff workflows BB
├── docs/
├── nginx/                      # nginx config for production
├── docker-compose.yml          # Development
├── docker-compose.prod.yml     # Production
├── Dockerfile                  # Multi-stage: builder / development / production
├── gunicorn.conf.py
├── .env.example
└── manage.py
```

---

## 2. Global Invariants

These rules apply everywhere. Violating them is a bug.

### 2.1 View mixin ordering
```python
# ALWAYS this order — LoginRequired must fire before PermissionRequired
class MyView(LoginRequiredMixin, PermissionRequiredMixin, View):
    ...
```

### 2.2 Transaction handling
- `ATOMIC_REQUESTS = True` globally.
- Streaming views must be decorated with `@transaction.non_atomic_requests`.
- All `task.delay()` calls inside views/services must be wrapped in `transaction.on_commit(lambda: task.delay(...))`.
- `on_commit()` itself must be called inside an atomic block.

### 2.3 PIPEDA invariants
- `actor_pk` in any audit model = integer user PK (`BigIntegerField`), never email.
- `actor_ip` = masked IP (IPv4 /24, IPv6 /48) — use `mask_ip()` from `apps/core/`.
- No donor name, email, address, or SIN in logs, filenames, or audit records.
- `WebhookEvent.payload` never logged or accessed outside the webhook handler.
- PDF files never written to disk — WeasyPrint → `BytesIO` → `StreamingHttpResponse`.
- Volunteer `sin_encrypted` must **never** appear in any API response, log, or serializer output.
- `rejection_reason` on `VolunteerApplication` is coordinator-only; blocked from volunteer-role API responses.
- `accommodation_notes` on `VolunteerProfile` is permission-gated (`volunteers.view_accommodation`).

### 2.4 Dates
- `TIME_ZONE = "America/Toronto"`.
- Local date: `timezone.localtime(timezone.now()).date()`.
- Filtering by date range: compute UTC boundaries from Toronto midnight, not naive dates.

### 2.5 CSV injection
- Any cell value that starts with `=`, `+`, `-`, `@`, `\t`, or `\r` must be prefixed with `\t`.
- Use `_sanitize_csv_cell()` from `apps/reports/exports/csv_export.py` — never roll your own.

### 2.6 CRA honorarium thresholds (volunteers)
- Single honorarium payment: hard cap $450.
- Annual YTD soft limit: $500 (requires coordinator override flag to exceed).
- Annual YTD hard cap: $1,000 (raises `ValidationError` unconditionally).
- Threshold checks use `select_for_update()` inside an atomic block to prevent race conditions.

---

## 3. Settings

`config/settings/base.py` — all shared config.  
`config/settings/production.py` — extends base; raises `ImproperlyConfigured` if required env vars are missing.  
`config/settings/test.py` — SQLite, `CELERY_TASK_ALWAYS_EAGER`, Argon2 hasher override for speed.

Key settings to know:

| Setting | Value |
|---|---|
| `AUTH_USER_MODEL` | `"auth_extension.User"` |
| `ATOMIC_REQUESTS` | `True` |
| `TIME_ZONE` | `"America/Toronto"` |
| `CELERY_BEAT_SCHEDULER` | `"django_celery_beat.schedulers:DatabaseScheduler"` |
| `SIMPLE_JWT["ALGORITHM"]` | `"RS256"` |
| `STRIPE_SECRET_KEY` | `env("STRIPE_SECRET_KEY")` |
| `FERNET_KEYS` | `env("FERNET_KEYS")` — comma-separated; first key is active |

---

## 4. URL Namespaces

| Prefix | Namespace | App |
|---|---|---|
| `/account/` (two-factor) | (two_factor) | `two_factor` |
| `/account/` | `auth_extension` | `apps.auth_extension` |
| `/portal/` | `portal` | `apps.portal` |
| `/notifications/` | `notifications` | `apps.notifications` |
| `/forms/` | `forms` | `apps.forms` |
| `/workflows/` | `workflows` | `apps.workflows` |
| `/consent/` | `consent` | `apps.consent` |
| `/payments/` | `payments` | `apps.payments` |
| `/donate/` | `donate` | `apps.payments.donation_urls` |
| `/donate/portal/` | `donor_portal` | `apps.payments.portal_urls` |
| `/reports/` | `reports` | `apps.reports` |
| `/backoffice/` | `backoffice` | `apps.backoffice` |
| `/volunteers/` | `volunteers` | `apps.volunteers` |
| `/api/v1/` | `api-v1` | `apps.api` |
| `/health/` | (none) | `apps.core.urls.health` |
| `/` (catch-all) | (wagtail) | Wagtail CMS |

---

## 5. App-by-App Reference

### 5.1 `apps/core`
Health check endpoints at `/health/live/` and `/health/ready/`. JSON logging formatter. Sentry PII hooks in `sentry.py` (`before_send`, `before_breadcrumb` — strip donor fields). `mask_ip()` utility. Custom middleware.

### 5.2 `apps/audit`
`AuditLogEntry` model — immutable event log. Fields: `event_type`, `actor_pk` (BigInt), `actor_ip` (masked), `object_pk`, `data` (JSONField), `created_at`. Written via `log_event()` helper. 7-year retention; admin `has_delete_permission=False`.

### 5.3 `apps/auth_extension`
Custom `User` model (extends `AbstractUser`). Email-based login, `django-allauth` adapter, `django-otp` MFA. MFA enforced on `/django-admin/` via `OTPAdminSite`. `backup_codes` field is a list stored as JSON — the `_` loop variable bug (shadowing `gettext`) was fixed; do not reintroduce.

IP-based rate limiting on login. `get_full_name()` returns `""` (not email) when name fields are blank.

### 5.4 `apps/cms`
Wagtail page types: `HomePage`, `ContentPage`, `NewsIndexPage`, `NewsPage`. All use `TranslatableMixin` for i18n. Custom `CustomImage` and `CustomDocument` models. SVG upload blocked (no sanitizer). File extensions whitelist: images `gif jpg jpeg png webp`; docs `pdf docx xlsx csv txt`.

### 5.5 `apps/forms`
Dynamic form builder extending Wagtail's `AbstractForm`. `FormPage` stores field definitions as JSON. `FormSubmission` stores `submission_data` (JSONField with size + depth validation). On submit, fires `form_submission_received` signal via `send_robust()`, which triggers `notify_on_form_submission` Celery task inside `transaction.on_commit()`.

### 5.6 `apps/consent`
`ConsentRecord` model — tracks what a citizen consented to, when, and what version. `ConsentVersion` — versioned consent text (bilingual). Download endpoint streams a consent record as PDF; download is atomic (creates `ConsentDownloadRecord` in the same transaction). `ConsentRecord` is PROTECT-protected; deleting related objects requires explicit consent revocation.

### 5.7 `apps/portal`
Citizen-facing service request portal. `ServiceRequest` model with `reference_number` (auto-generated, human-readable slug). `StatusUpdate` model — immutable (save guard on `pk`; do not remove). Fires `portal_status_updated` signal on status change, which is handled by `apps/workflows` to create/update `WorkItem` rows.

Views: `DashboardView`, `ServiceRequestDetailView`, `ServiceRequestCreateView`, `ServiceRequestCancelView`. All require `LoginRequiredMixin`.

URL filter: `?status=` query param on dashboard — supported and tested.

### 5.8 `apps/notifications`
`Notification` model — `recipient` (FK to User), `channel` (in-app/email), `read_at` (nullable). Composite index on `(recipient, channel, read_at)`. `NotificationConfig` — per-user preferences. Delivery via `send_notification()` service. Retry task (`retry_pending_notifications`) runs every 15 minutes via Beat.

### 5.9 `apps/workflows`
`WorkItem` — internal staff task linked to a `ServiceRequest`. `WorkItemHistory` — immutable event log per work item. `WorkItemComment` — staff notes. SLA tracking via `due_at`; `check_sla_breaches` Beat task flags overdue items.

Signal handler in `apps/portal/receivers.py` creates `WorkItem` on `portal_status_updated`. `on_commit()` wrapping ensures the work item is created only after the portal transaction commits.

### 5.10 `apps/backoffice`
Staff-only views for: service request queue, work item queue, citizen management, audit log viewer, basic reports, staff notifications. All views require `is_staff=True` or explicit permissions. Uses `LoginRequiredMixin` + `PermissionRequiredMixin`.

### 5.11 `apps/api`
DRF REST API at `/api/v1/`. JWT authentication (RS256, 15-min access token). Token endpoint throttled at 5 req/min (anonymous). Standard CivicOS error envelope: `{"error": {"code": "...", "message": "...", "details": {}}}`. OpenAPI schema at `/api/v1/schema/`.

Endpoints: portal (service requests), notifications, workflows, consent, volunteers (20 endpoints under `/api/v1/volunteers/`).

`UNAUTHENTICATED_USER` is NOT set to `None` — this would cause 403 instead of 401; leave as default.

### 5.12 `apps/payments`

#### Models (`models.py`)
- `TenantPaymentConfig` — per-org Stripe keys, `webhook_endpoint_secret` (Fernet-encrypted `EncryptedCharField`), tax rate refs.
- `FeePayment` — government fee payment. Links to `PaymentIntent` via `stripe_payment_intent_id`.
- `Payment` — Stripe PaymentIntent result. `CheckConstraint` enforces non-negative amounts.
- `Refund` — partial or full refund. Distributed lock prevents concurrent double-refund.
- `Donation` — charitable donation. `eligible_amount` and `advantage_amount` (CRA).
- `RecurringGiftPlan` — Stripe Subscription wrapper.
- `TaxReceipt` — CRA-compliant tax receipt. `serial_number` follows CRA format, validated by regex. `has_pdf` property (no file stored). Immutable once `issued_at` is set.
- `WebhookEvent` — raw Stripe webhook log. `payload` never accessed outside webhook handler.

#### Gateway (`gateway.py`, `gateways/`)
`StripeGateway` implements `AbstractGateway`. Per-call `StripeClient` (never cached globally — avoids stale API version). Always passes explicit `stripe_version`. Charge lookup uses `latest_charge` from PaymentIntent, not a phantom `charges` key.

#### Tasks (`tasks.py`, `tasks_receipts.py`)
- `process_stripe_webhook` — queue `webhooks`; `acks_late=True`, `reject_on_worker_lost=True`.
- `generate_and_send_receipt` — queue `receipts`; idempotent (checks `email_sent` flag inside atomic block before sending).
- `generate_annual_receipts` — queue `receipts`; uses `.iterator(chunk_size=500)`; filter uses UTC boundaries derived from Toronto midnight for both eastern and western Canada donors.
- `kickoff_annual_receipts` — lightweight Beat trigger; delegates to `generate_annual_receipts.delay()`.

#### Views (`views/`)
- `fee_payment.py` — `FeePaymentCreateView`, `FeePaymentSuccessView`.
- `donation.py` — `DonationCreateView`, `DonationSuccessView` (IDOR-protected: checks `donation.user == request.user`).
- `refund.py` — `RefundDetailView` (tenant-scoped: checks payment belongs to requesting org).
- `portal.py` — `DonorHistoryView`, `RecurringGiftPlanDetailView`, `RecurringGiftPlanCancelView`.
- `webhook.py` — `StripeWebhookView` (`@csrf_exempt`, signature verified before any processing).

#### Receivers (`receivers.py`)
Signal handlers for Stripe events dispatched by the webhook task. `receipt_issued` signal fires after `TaxReceipt.issued_at` is set; triggers `generate_and_send_receipt.delay()` inside `transaction.on_commit()`.

Fernet key: `MultiFernet` via `lru_cache` on a stable function — not on the class. Supports key rotation by prepending new key to `FERNET_KEYS`.

#### Permissions
All payments views require `payments.view_*` or `payments.change_*` permissions. Donor portal requires `LoginRequiredMixin` only (no staff perm; scoped by `request.user`).

### 5.13 `apps/reports`

#### Models (`models.py`)
- `ReportSnapshot` — pre-computed monthly aggregate. Fields: `report_type` (financial/donations/operational), `period_year`, `period_month`, `data` (JSONField), `row_count`, `computed_at` (auto_now). `unique_together = [("report_type", "period_year", "period_month")]`. `ordering = ["-period_year", "-period_month", "report_type"]`. Admin: `has_delete_permission=False`.
- `ExportRecord` — audit trail per download. `actor_pk` = `BigIntegerField`, `actor_ip` = masked, `created_at` = auto. `ordering = ["-created_at"]`. Admin: no delete. Indexes on `(export_type, created_at)` and `(actor_pk, created_at)`.

#### Services (`services/`)
- `financial.py` — `compute_financial_snapshot(year, month)`, `get_reconciliation_queryset()`, `get_monthly_revenue()`.
- `donations.py` — `compute_donations_snapshot(year, month)`, `get_receipt_list_queryset()`, `get_t3010_preparatory_data()`.
- `operational.py` — `compute_operational_snapshot(year, month)`.

#### Tasks (`tasks.py`)
- `compute_monthly_snapshots` — Beat task; queue `reports`; computes previous calendar month in Toronto time; calls `_compute_all_snapshots()`.
- `recompute_snapshot(report_type, year, month)` — on-demand; used by admin actions.
- `_compute_all_snapshots(year, month)` — internal helper; writes all 3 snapshot types; idempotent via `update_or_create`.
- `_compute_single_snapshot(report_type, year, month)` — dispatches to correct service; raises `ValueError` for unknown types.

#### Exports (`exports/`)
- `csv_export.py` — `streaming_csv_response()`, `_EchoBuffer`, `_sanitize_csv_cell()`, `_FORMULA_TRIGGERS` (frozenset). Individual export functions: `export_reconciliation_csv`, `export_revenue_csv`, `export_receipts_csv`, `export_t3010_prep_csv`.
- `pdf_export.py` — `export_monthly_summary_pdf()`. WeasyPrint renders to `BytesIO`. **Patch target: `apps.reports.exports.pdf_export.export_monthly_summary_pdf`** (lazy import in view body — patch the source module, not the view module).

#### Views (`views/`)
- `financial.py` — `FinancialDashboardView`, `RevenueDetailView`, `ReconciliationExportView`, `RevenueExportView`, `MonthlySummaryPdfView`.
- `donations.py` — `DonationsDashboardView`, `T3010PrepView`, `ReceiptListExportView`, `T3010ExportView`.
- `operational.py` — `OperationalDashboardView`, `TaskFailureListView`.

Permissions (all defined on `payments.Permission`):
- `payments.view_financialreport`, `payments.export_financialreport`
- `payments.view_donationreport`, `payments.export_donationreport`
- `payments.view_operationalreport`

### 5.14 `apps/volunteers`

**Purpose:** Volunteer Management Building Block — complete lifecycle from profile creation through application screening, scheduling, hours logging, honoraria payments, and reporting.

#### Models (`models.py`)
- `VolunteerProfile` — extends `User` (1-to-1). Fields include `sin_encrypted` (Fernet-encrypted, never exposed), `accommodation_notes` (permission-gated).
- `Opportunity` — volunteer opportunity with capacity, dates, coordinator FK.
- `VolunteerApplication` — application from a volunteer to an opportunity. Fields: `status`, `rejection_reason` (coordinator-only), `screening_notes`.
- `ScreeningRecord` — criminal record check / reference decision log per application.
- `Shift` — scheduled block of volunteer time linked to an `Opportunity`. Capacity-constrained.
- `ShiftBooking` — volunteer-to-shift assignment. One active booking per volunteer per shift.
- `HoursLog` — logged hours per booking; states: `pending` → `approved` / `rejected`.
- `Honorarium` — payment record per volunteer per period; enforces CRA thresholds.
- `HonorariumMonthlySummary` — pre-aggregated monthly totals per volunteer (generated by Beat task).

#### Services (`services/`)
- `applications.py` — `submit_application()`, `withdraw_application()`, duplicate-application guard.
- `screening.py` — `record_screening_decision()`, approval/rejection with audit trail.
- `scheduling.py` — `create_shift()`, `book_shift()`, `cancel_booking()`, capacity enforcement, conflict detection.
- `hours.py` — `log_hours()`, `approve_hours()`, `reject_hours()`.
- `honoraria.py` — `create_honorarium()`, `void_honorarium()`, CRA threshold checks (`select_for_update()` inside atomic block).
- `reporting.py` — volunteer activity aggregates, coordinator summary querysets.

#### REST API (`apps/api/volunteers/`)
20 endpoints under `/api/v1/volunteers/`. JWT-authenticated (same RS256 tokens as rest of API). PIPEDA-safe serializers — `sin_encrypted` field excluded unconditionally; `rejection_reason` excluded for non-coordinator roles; `accommodation_notes` excluded without `volunteers.view_accommodation` permission.

Key endpoint groups:
- `/api/v1/volunteers/profiles/` — list/detail (coordinator only for list)
- `/api/v1/volunteers/applications/` — CRUD scoped to `request.user` for volunteers; full access for coordinators
- `/api/v1/volunteers/opportunities/` — public list, coordinator write
- `/api/v1/volunteers/shifts/` — list/book/cancel
- `/api/v1/volunteers/hours/` — log, approve, reject
- `/api/v1/volunteers/honoraria/` — create, void, list (coordinator only)
- `/api/v1/volunteers/screening/` — record decision (coordinator only)

#### Views
- Volunteer portal views (`views/portal.py`) — `ApplicationListView`, `ApplicationCreateView`, `ApplicationWithdrawView`, `ShiftListView`, `ShiftBookView`, `HoursLogCreateView`.
- Coordinator views (`views/coordinator.py`) — application queue, screening form, shift management, hours approval queue, honorarium management, monthly summary.
- All CBVs: `LoginRequiredMixin` before `PermissionRequiredMixin` (global invariant 2.1).

#### Celery Tasks (`tasks.py`)
- `send_monthly_honorarium_summary` — Beat task, queue `volunteers`, 1st of month 08:00 Toronto.
- `send_hours_reminder` — weekly reminder to volunteers with pending hours to log.
- `notify_shift_booking_confirmed` / `notify_shift_cancelled` — on_commit-wrapped after booking changes.

#### Key Constraints (never violate)
- `sin_encrypted` is write-only via service layer; **never** included in any serializer `fields` list or log output.
- `rejection_reason` visible only to users with `volunteers.view_application_rejection`.
- `accommodation_notes` visible only to users with `volunteers.view_accommodation`.
- Honorarium CRA caps: $450 per payment (hard), $500 YTD (soft — requires `override_soft_cap=True`), $1,000 YTD (hard unconditional).
- `select_for_update()` used in `create_honorarium()` to prevent concurrent cap bypass.

#### Test Files
| File | Coverage |
|---|---|
| `test_models.py` | Model validation, constraints, field behaviour |
| `test_services_applications.py` | Application lifecycle, duplicate guard |
| `test_services_screening.py` | Screening decision flow, audit trail |
| `test_services_scheduling.py` | Shift creation, booking, capacity, conflict detection |
| `test_services_hours.py` | Hours log states, approval/rejection |
| `test_services_honoraria.py` | CRA threshold enforcement, concurrent cap bypass prevention |
| `test_services_reporting.py` | Reporting aggregates |
| `test_views_portal.py` | Volunteer-facing views, auth, IDOR protection |
| `test_views_coordinator.py` | Coordinator views, permission checks |
| `test_views_coordinator_wave4.py` | Honorarium and monthly summary coordinator views |
| `test_api.py` | All 20 REST endpoints, PIPEDA field exclusions |
| `test_tasks.py` | Celery tasks (synchronous via `task.apply()`) |
| `test_forms.py` | Django form validation |
| `test_receivers.py` | Signal handler behaviour |
| `test_invariants.py` | Key security invariants: sin_encrypted never exposed, rejection_reason gated, accommodation_notes gated |

**Current wave:** 6 (hardening complete). **707 tests** collected.

---

## 6. Cross-Cutting Patterns

### Streaming CSV
```python
# Always use this — never buffer CSVs in memory
from apps.reports.exports.csv_export import streaming_csv_response

response = streaming_csv_response(
    rows,           # Iterable of dicts or sequences
    columns,        # PIPEDA whitelist — extra keys silently dropped
    "my_export",    # filename prefix — no PII
    period_start,
    period_end,
)
```

### Celery task + on_commit
```python
# Correct pattern — task fires only after DB commit
def some_service_function(...):
    with transaction.atomic():
        obj = MyModel.objects.create(...)
        transaction.on_commit(lambda: my_task.delay(obj.pk))
```

### Frozen-time testing (preferred over time.sleep)
```python
from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

t1 = datetime(2025, 6, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
with patch("django.utils.timezone.now", return_value=t1):
    obj = MyModel.objects.create(...)
```

### Patching lazy imports
```python
# If a view does `from apps.X import func` inside a method body,
# patch the SOURCE module, not the view module:
with patch("apps.X.func") as mock_func:
    ...
```

### ExportRecord creation
```python
# ALWAYS create ExportRecord AFTER the export succeeds, never before
pdf_bytes = export_monthly_summary_pdf(...)   # raises on failure
ExportRecord.objects.create(...)              # only if above didn't raise
```

---

## 7. Running Tests

```bash
# Full suite (SQLite — fast)
python manage.py test --settings=config.settings.test

# Single app
python manage.py test apps.reports --settings=config.settings.test
python manage.py test apps.volunteers --settings=config.settings.test

# Single test class
python manage.py test apps.reports.tests.test_wave5_suite.SanitizeCsvCellTest \
    --settings=config.settings.test

# Via Docker
docker compose run --rm web python manage.py test
```

Test files live at `apps/<appname>/tests/test_*.py`.

Reports BB test files:
- `tests/test_wave2_financial.py` — financial service + views
- `tests/test_wave3_donations.py` — donations service + views
- `tests/test_wave4_operational.py` — operational service + PDF + Celery
- `tests/test_wave5_suite.py` — model tests, CSV utilities, task dispatch, regression (65 tests)

Volunteers BB test files: see Section 5.14 table above.

---

## 8. Migrations

```bash
# Apply all migrations
python manage.py migrate

# Create a new migration for an app
python manage.py makemigrations <app>

# Seed Celery Beat periodic tasks (run once per deployment)
python manage.py seed_periodic_tasks
```

**Wave 9 migration note:** `TenantPaymentConfig.webhook_endpoint_secret` changed from plaintext `CharField` to Fernet-encrypted `EncryptedCharField`. After migrating, existing rows must be re-saved in Django admin to re-encrypt. See `docs/DEPLOY_NOTES.md`.

---

## 9. Admin

`/django-admin/` — MFA-enforced via `OTPAdminSite`. All staff must have a TOTP device registered before admin access is granted.

Key admin registrations:
- `ReportSnapshot` — read-only; no delete.
- `ExportRecord` — read-only; no delete.
- `TaxReceipt` — no delete once `issued_at` is set.
- `AuditLogEntry` — read-only; no delete.
- `TenantPaymentConfig` — edit restricted; webhook secret re-entry required post-Wave-9 migration.
- `VolunteerProfile` — `sin_encrypted` excluded from all admin fieldsets.
- `Honorarium` — no delete once `paid_at` is set.

---

## 10. Known Gaps / Future Work

- SMS delivery via GC Notify is wired (`GC_NOTIFY_API_KEY`) but notification templates for SMS are not implemented.
- Wagtail search uses the DB backend; Elasticsearch config is commented out in `production.py` for when search volume warrants it.
- No automated smoke test suite for the live production environment (would need a staging Stripe key).

---

## 11. Known Technical Debt

### Organization model location (appointments → core)

**File:** `apps/appointments/models.py` — `Organization` class
**Status:** Wave 1 structural debt — tracked in source code docstring

`Organization` was created inside `apps.appointments` for Wave 1 speed.
The spec places it in `apps.core` as the canonical cross-BB organisation
identity. No other BB currently imports it, so migration is still low-risk.

**Action required before Wave 3** (when `Booking` model adds org-scoped queries):
move `Organization` to `apps/core/models.py` using Django's
`SeparateDatabaseAndState` migration pattern to avoid dropping the table.
See the docstring on the `Organization` class for the full migration checklist.

### appointments – StaffProfileAdmin filter_horizontal org-scoping gap
**Severity:** HIGH (multi-tenant security gap)
**Location:** `apps/appointments/admin.py` — `StaffProfileAdmin`
**Problem:** The `filter_horizontal` widget for `appointment_types` uses the
default autocomplete endpoint, which is not scoped by organization. An
`is_staff` admin from Org A can see and assign `AppointmentType` records
from Org B when editing a `StaffProfile`.
**Fix required:** Override `formfield_for_manytomany()` in `StaffProfileAdmin`
to filter the `AppointmentType` queryset by
`location__organization == request.user.staff_profile.location.organization`.
**Prerequisite:** Wave 3 fine-grained permission matrix.
