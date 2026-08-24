# CivicOS — Project Overview & Status

**Last updated:** 2026-07-02  
**Git HEAD:** 9f86c52  
**Status:** CivicOS has 13 application modules in this dated V1 inventory. This is **not** a GovStack Building Block conformance, certification, official-harness, or production-readiness statement. The current GovStack evidence boundary is [`docs/govstack/SCOPE.md`](govstack/SCOPE.md): four local implementations are tracked separately, while Messaging/Workflow/CMS are local-only and remaining catalog Building Blocks are **Not Done Yet**.

---

## 1. What CivicOS Is

CivicOS is a modular digital government services platform built with Django 5.2, Wagtail, and Celery. It is designed for municipal and public-sector organizations that need a secure, accessible, bilingual (English/French) platform covering everything from public-facing CMS pages to internal staff workflows, payment processing, and financial reporting.

The platform is structured as independent application modules (historically called "building blocks") — one Django app per capability. This product-architecture terminology must not be confused with a GovStack Building Block claim; see [`docs/govstack/SCOPE.md`](govstack/SCOPE.md) for the authoritative GovStack boundary.

---

## 2. Platform Scope

### In scope (V1)

| Building Block | App(s) | Tests | Status |
|---|---|---|---|
| Core / Config / Audit | `apps/core`, `apps/audit` | 83 | ✅ Complete |
| CMS | `apps/cms` | (Wagtail integration) | ✅ Complete |
| Authentication & MFA | `apps/auth_extension` | 75 | ✅ Complete |
| Dynamic Forms | `apps/forms` | 103 | ✅ Complete |
| Consent Management | `apps/consent` | 141 | ✅ Complete |
| Citizen Portal | `apps/portal` | 125 | ✅ Complete |
| Notifications | `apps/notifications` | 77 | ✅ Complete |
| Workflows | `apps/workflows` | 113 | ✅ Complete |
| Backoffice | `apps/backoffice` | 121 | ✅ Complete |
| REST API | `apps/api` | 115 | ✅ Complete |
| Payments (fees + donations) | `apps/payments` | 1,115 | ✅ Complete |
| Analytics & Reporting | `apps/reports` | 356 | ✅ Complete |
| **Volunteer Management** | `apps/volunteers` | **707** | ✅ Complete |
| **Total** | | **3,131** | ✅ All collected |

### Out of scope (V1)

- Elasticsearch (Wagtail uses DB search backend; upgrade path documented in `production.py`)
- SMS delivery (GC Notify API key wired, SMS templates not implemented)
- Multi-tenancy beyond a single organization configuration
- Mobile native apps (the citizen portal is a responsive web app)
- Payment providers other than Stripe

---

## 3. Architecture

```
Browser / API client
        │
    nginx (TLS termination, static files)
        │
    gunicorn (Django WSGI)
        │
    Django 5.2 / Wagtail 6
        │
    ┌───┴───────────────────────────────┐
    │  13 building block Django apps    │
    └───────────────────────────────────┘
        │                │
   PostgreSQL 16      Redis 7
                         │
                  Celery workers + Beat
```

**Key technology choices:**

| Concern | Choice |
|---|---|
| Language | Python 3.12 |
| Framework | Django 5.2 + Wagtail 6 |
| Database | PostgreSQL 16 (SQLite for tests) |
| Cache / broker | Redis 7 |
| Task queue | Celery 5 with `django-celery-beat` |
| Auth | `django-allauth` + `django-otp` (MFA), RS256 JWT (`djangorestframework-simplejwt`) |
| Payments | Stripe (gateway abstraction in `apps/payments/gateway/`) |
| PDF export | WeasyPrint |
| File storage | AWS S3 (`django-storages`) in production; local `media/` in development |
| Email | `django-anymail` (SendGrid or Mailgun) |
| Error tracking | Sentry (with PII scrubbing hooks in `apps/core/sentry.py`) |
| Static files | Whitenoise |
| Container | Docker (multi-stage Dockerfile; `docker-compose.prod.yml` for production) |
| CI | GitHub Actions |

---

## 4. Key Design Invariants

These constraints apply across the entire codebase and must never be violated.

**PIPEDA compliance**
- No donor name, email, address, or SIN ever appears in logs, audit records, or export filenames.
- `ExportRecord.actor_pk` is a `BigIntegerField` storing the user's integer PK — never email.
- `actor_ip` is masked to IPv4 /24 or IPv6 /48 before storage.
- All CSV exports enforce a PIPEDA column whitelist; extra keys are silently dropped.
- WeasyPrint PDFs are never persisted to disk; streamed directly in the HTTP response.
- `WebhookEvent.payload` is never read or logged outside the webhook handler.
- Volunteer `sin_encrypted` is never exposed via API or logs; `rejection_reason` is coordinator-only; `accommodation_notes` is permission-gated.

**Security**
- `LoginRequiredMixin` always comes before `PermissionRequiredMixin` in MRO.
- `ATOMIC_REQUESTS = True` globally. Streaming views use `@transaction.non_atomic_requests`.
- MFA enforced for `/django-admin/` via `django-otp` middleware.
- Stripe webhook secrets stored encrypted (Fernet AES-128-CBC via `FERNET_KEYS`).
- Full tracebacks are never surfaced to clients; Sentry PII hooks strip donor data from breadcrumbs.
- CSP headers restrict `script-src` to `'self'` and `js.stripe.com` only.

**CRA / financial retention**
- `ReportSnapshot` rows have `has_delete_permission = False` in admin — 7-year minimum retention.
- `ExportRecord` is an immutable audit trail; no delete permission.
- Tax receipt serial numbers follow CRA format and are never reused.
- Volunteer honoraria respect CRA thresholds: $450 single-payment cap, $500 annual soft limit, $1,000 hard annual cap.

**Dates and timezones**
- `TIME_ZONE = "America/Toronto"`. Local-date logic always uses `timezone.localtime(timezone.now()).date()`.
- Annual receipt run filters by UTC boundaries computed from Toronto midnight to include BC and western Canada donors.

**CSV injection defence**
- `_sanitize_csv_cell()` in `apps/reports/exports/csv_export.py` prefixes a tab before any cell starting with `=`, `+`, `-`, `@`, `\t`, or `\r` (OWASP CSV injection defence).

---

## 5. Celery Queue Design

Three named queues prevent slow batch jobs from starving latency-sensitive tasks:

| Queue | Purpose |
|---|---|
| `webhooks` | Stripe webhook processing — must respond within Stripe's 30 s retry window |
| `receipts` | Annual receipt generation, PDF email delivery |
| `reports` | Nightly snapshot computation |
| `payments` | General payments tasks |
| `volunteers` | Volunteer honorarium summaries, hours reminders, shift notifications |
| `default` | Fallback for all other tasks |

**Celery Beat tasks (seeded by `seed_periodic_tasks` management command):**

| Task | Schedule | Queue |
|---|---|---|
| `compute_monthly_snapshots` | 02:00 on 2nd of month (Toronto) | `reports` |
| `kickoff_annual_receipts` | Jan 2, 03:00 Toronto | `default` |
| `check_sla_breaches` | Hourly | `default` |
| `flush_expired_tokens` | Daily | `default` |
| `retry_pending_notifications` | Every 15 min | `default` |
| `send_monthly_honorarium_summary` | 1st of month, 08:00 Toronto | `volunteers` |
| `send_hours_reminder` | Weekly (configurable) | `volunteers` |

---

## 6. Payments BB Highlights

The largest and most complex building block (1,115 tests).

- **Fee payments** — government service fees via Stripe PaymentIntent, 3DS-aware, with `TenantPaymentConfig` per-org settings and Fernet-encrypted webhook secret.
- **Donations** — one-time and recurring (Stripe Subscription), CRA-eligible donations with `advantage_amount` tracking.
- **Tax receipts** — PDF generation (WeasyPrint), emailed to donors, CRA serial numbers, idempotent `save_receipt_pdf`.
- **Annual receipt run** — Celery batch task, `.iterator(chunk_size=500)` for memory safety, UTC-boundary-aware filter for all Canadian time zones.
- **Partial refunds** — idempotent via distributed lock, double-race prevention, `charge_refunded` webhook handler.
- **Donor portal** — IDOR-protected views, donation history, recurring plan management.

---

## 7. Analytics & Reporting BB Highlights

- `ReportSnapshot` — pre-computed monthly aggregates (financial, donations, operational), upserted nightly by Celery Beat.
- `ExportRecord` — immutable audit trail per download; created only after successful generation.
- Streaming CSV exports via `StreamingHttpResponse` + `_EchoBuffer` — safe for 50K+ rows, never buffered in memory.
- PDF export (WeasyPrint) — `ExportRecord` only created after PDF generation succeeds.
- Operational dashboard — Celery task failure rate, SLA breach counts, form submission volume.

---

## 8. Volunteer Management BB Highlights

Built across six waves; 707 tests covering models, services, views, API, and key invariants.

### Wave 1 — Models, Admin, Migrations (`apps/volunteers/`)
Core models: `VolunteerProfile`, `VolunteerApplication`, `Opportunity`, `ScreeningRecord`. Fernet-encrypted `sin_encrypted` field. Full Django admin registration. Initial migrations.

### Wave 2 — Applications & Screening
`services/applications.py` — `submit_application()`, `withdraw_application()`, duplicate-application guard. `services/screening.py` — `record_screening_decision()`, approval/rejection flow with audit trail. Volunteer-facing portal views (apply, withdraw, status). Coordinator views (application queue, screening form). All views: `LoginRequiredMixin` before `PermissionRequiredMixin`.

### Wave 3 — Scheduling & Hours
`services/scheduling.py` — `create_shift()`, `book_shift()`, `cancel_booking()`, capacity enforcement, conflict detection. `services/hours.py` — `log_hours()`, `approve_hours()`, `reject_hours()`. Models: `Shift`, `ShiftBooking`, `HoursLog`. Celery tasks: shift reminders, hours approval notifications.

### Wave 4 — Honoraria & Coordinator Views
`services/honoraria.py` — `create_honorarium()`, `void_honorarium()`, CRA threshold enforcement ($450 per payment, $500/$1,000 annual soft/hard caps). `HonorariumMonthlySummary` model. Beat task `send_monthly_honorarium_summary`. Extended coordinator views: honorarium list, void, monthly summary.

### Wave 5 — Reporting, REST API, CSV & PDF Export
`services/reporting.py` — volunteer activity aggregates, coordinator summary data. REST API (`apps/api/volunteers/`) — 20 endpoints under `/api/v1/volunteers/` (profiles, applications, shifts, bookings, hours logs, honoraria, screening). JWT-authenticated, DRF serializers with PIPEDA-safe field sets. CSV export for hours and honoraria. Reference letter PDF (WeasyPrint, streamed, never persisted).

### Wave 6 — Hardening
Bilingual QA (English/French string coverage). WCAG 2.1 AA audit on all volunteer templates. Key test invariants (`test_invariants.py`): `sin_encrypted` never in API response, `rejection_reason` blocked from volunteer-role requests, `accommodation_notes` gated by permission. Documentation and inline docstrings completed.

---

## 9. Test Strategy

All tests run with `DJANGO_SETTINGS_MODULE=config.settings.test` (SQLite, no Redis, no Celery workers).

- `TransactionTestCase` used where `on_commit()` callbacks must fire.
- `patch("django.utils.timezone.now", return_value=...)` used for deterministic timestamp assertions.
- No `time.sleep()` anywhere in the test suite.
- Celery tasks tested synchronously via `task.apply()`.

```bash
# Run the full suite
python manage.py test --settings=config.settings.test
# Run volunteers only
python manage.py test apps.volunteers --settings=config.settings.test
# or via Docker
docker compose run --rm web python manage.py test
```

---

## 10. What Comes Next

The platform is code-complete for V1. Remaining work before going live:

1. **Deploy** — follow `docs/DEPLOY_NOTES.md` to provision the production environment, set required secrets, and run `seed_periodic_tasks`.
2. **Stripe live keys** — configure `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, and `STRIPE_WEBHOOK_SECRET` (or re-enter via Django admin after the Fernet migration).
3. **DNS + TLS** — point domain at nginx, set `DJANGO_ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`.
4. **S3 bucket** — create the bucket in `ca-central-1`, set IAM credentials in environment.
5. **Sentry project** — create project, set `SENTRY_DSN`.
6. **CRA registration** — ensure the charity's CRA business number is seeded in `TenantPaymentConfig`.
