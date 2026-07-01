# Analytics & Reporting Building Block — Specification

**Status:** In progress  
**Last updated:** 2026-06-30  
**Author:** CivicOS Payments team  
**Conformance:** Custom BB — no dedicated CivicOS Analytics spec exists (CivicOS 2.0 treats analytics as an optional layer within the Security BB). This BB follows CivicOS cross-cutting principles: audit trail, multi-tenancy, PIPEDA data minimization.

---

## 1. Purpose

Provide finance staff, charity administrators, and municipal SRE teams with accurate, PIPEDA-compliant financial and operational reports across the CivicOS Payments BB. Supports CRA T3010 annual return preparation, month-end reconciliation, campaign performance tracking, and system health monitoring.

---

## 2. Consumers & Permissions

| Role | Permission | Reports |
|---|---|---|
| Finance staff | `reports.view_financial_report` | Revenue, refunds, failed payments, reconciliation export |
| Charity admin | `reports.view_donation_report` | T3010 prep data, campaign performance, receipt status, donor retention |
| Municipal IT / SRE | `reports.view_operational_report` | Webhook health, gateway success rate, task throughput |
| Superuser | All of the above | All reports |

> No public or anonymous access. All report views require `is_staff=True` **and** the relevant permission.

---

## 3. Architecture

### 3.1 New app: `apps/reports`

Dedicated building block — separate from `apps/backoffice` and `apps/payments` to avoid circular imports and to own its models cleanly.

```
apps/reports/
├── __init__.py
├── apps.py
├── models.py          # ReportSnapshot, ExportRecord
├── admin.py
├── views/
│   ├── __init__.py
│   ├── financial.py
│   ├── donations.py
│   └── operational.py
├── services/
│   ├── __init__.py
│   ├── financial.py   # Query functions for financial aggregates
│   ├── donations.py   # Query functions for donation/CRA aggregates
│   └── operational.py # Query functions for webhook/task metrics
├── tasks.py           # Celery Beat snapshot task
├── exports/
│   ├── __init__.py
│   ├── csv_export.py  # StreamingHttpResponse CSV/Excel builders
│   └── pdf_export.py  # WeasyPrint monthly summary PDFs
├── urls.py
├── templates/
│   └── reports/
│       ├── financial/
│       │   ├── revenue.html
│       │   ├── refunds.html
│       │   ├── failed_payments.html
│       │   └── reconciliation.html
│       ├── donations/
│       │   ├── t3010_prep.html
│       │   ├── campaign_performance.html
│       │   ├── receipt_status.html
│       │   └── donor_retention.html
│       └── operational/
│           ├── webhook_health.html
│           ├── gateway_success.html
│           └── task_throughput.html
├── tests/
│   ├── __init__.py
│   ├── test_models.py
│   ├── test_financial.py
│   ├── test_donations.py
│   ├── test_operational.py
│   ├── test_exports.py
│   └── test_tasks.py
└── migrations/
    └── 0001_initial.py
```

### 3.2 Integration with existing backoffice

The existing `/backoffice/reports/` page (service requests, work items, notifications) is untouched. New payment/donation/operational reports live at `/reports/` with their own sidebar, linked from the backoffice navigation. The backoffice `base.html` sidebar gains a "Reports" section pointing at `/reports/`.

### 3.3 URL structure

```
/reports/                          → redirect to /reports/financial/revenue/
/reports/financial/revenue/        → MonthlyRevenueView
/reports/financial/refunds/        → RefundSummaryView
/reports/financial/failed/         → FailedPaymentsView
/reports/financial/reconciliation/ → ReconciliationView (+ CSV export)
/reports/donations/t3010/          → T3010PrepView (+ CSV export)
/reports/donations/campaigns/      → CampaignPerformanceView
/reports/donations/receipts/       → ReceiptStatusView
/reports/donations/retention/      → DonorRetentionView
/reports/operational/webhooks/     → WebhookHealthView
/reports/operational/gateway/      → GatewaySuccessView
/reports/operational/tasks/        → TaskThroughputView
/reports/export/<token>/           → ExportDownloadView (time-limited download)
```

---

## 4. Models

### 4.1 `ReportSnapshot`

Pre-computed monthly aggregate. One row per `(report_type, period_year, period_month)`. Idempotent — recomputing a period overwrites the existing row.

```python
class ReportSnapshot(models.Model):
    REPORT_TYPE_FINANCIAL   = "financial"
    REPORT_TYPE_DONATIONS   = "donations"
    REPORT_TYPE_OPERATIONAL = "operational"
    REPORT_TYPE_CHOICES = [...]

    report_type  = models.CharField(max_length=20, choices=REPORT_TYPE_CHOICES)
    period_year  = models.PositiveSmallIntegerField()   # e.g. 2025
    period_month = models.PositiveSmallIntegerField()   # 1–12
    data         = models.JSONField()                   # Pre-aggregated metrics dict
    row_count    = models.PositiveIntegerField()        # Number of source rows aggregated
    computed_at  = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("report_type", "period_year", "period_month")]
        indexes = [models.Index(fields=["report_type", "period_year", "period_month"])]
        # Financial record — never auto-deleted. has_delete_permission=False in admin.
```

`data` schema per `report_type`:

**financial:**
```json
{
  "gross_revenue": "12345.67",
  "processor_fees": "432.10",
  "net_revenue": "11913.57",
  "tax_collected": "987.65",
  "fee_payment_count": 142,
  "refund_total": "234.50",
  "refund_count": 3,
  "failed_payment_count": 7,
  "failed_payment_value": "890.00"
}
```

**donations:**
```json
{
  "total_donations": "45678.90",
  "total_eligible_amount": "44678.90",
  "total_advantage_amount": "1000.00",
  "donation_count": 312,
  "recurring_count": 87,
  "one_time_count": 225,
  "new_donor_count": 45,
  "returning_donor_count": 267,
  "receipts_issued": 310,
  "receipts_cancelled": 2,
  "large_donations_10k_plus": 1,
  "campaigns": {
    "campaign-slug-1": {"total": "23456.78", "count": 156},
    "campaign-slug-2": {"total": "22222.12", "count": 156}
  }
}
```

**operational:**
```json
{
  "webhook_total": 890,
  "webhook_processed": 882,
  "webhook_failed": 8,
  "webhook_retry_total": 12,
  "payment_intent_total": 312,
  "payment_intent_succeeded": 305,
  "payment_intent_failed": 7,
  "task_success_count": 4521,
  "task_failure_count": 3,
  "task_p95_duration_seconds": 12.4
}
```

### 4.2 `ExportRecord`

Audit trail only — records every export download. No file stored server-side beyond the request/response cycle (streaming).

```python
class ExportRecord(models.Model):
    EXPORT_TYPE_RECONCILIATION = "reconciliation"
    EXPORT_TYPE_T3010          = "t3010_prep"
    EXPORT_TYPE_RECEIPTS       = "receipts"
    EXPORT_TYPE_CHOICES = [...]

    FORMAT_CSV   = "csv"
    FORMAT_EXCEL = "xlsx"
    FORMAT_PDF   = "pdf"
    FORMAT_CHOICES = [...]

    export_type  = models.CharField(max_length=30, choices=EXPORT_TYPE_CHOICES)
    format       = models.CharField(max_length=5, choices=FORMAT_CHOICES)
    period_start = models.DateField()
    period_end   = models.DateField()
    actor_pk     = models.UUIDField()          # Never actor email — PIPEDA
    actor_ip     = models.GenericIPAddressField(null=True)
    row_count    = models.PositiveIntegerField(default=0)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["export_type", "created_at"])]
```

---

## 5. Computation Strategy

| Data | Strategy | Trigger |
|---|---|---|
| Current month (rolling) | Real-time ORM aggregation | On page load |
| Prior months | Read from `ReportSnapshot` | Written by Celery Beat |
| Snapshot backfill | `compute_monthly_snapshots` Celery task | Nightly at 02:00 America/Toronto |
| On-demand snapshot | `compute_monthly_snapshots.apply_async(month=X)` | Admin action |

The Celery Beat task is **idempotent**: `ReportSnapshot.objects.update_or_create(report_type=..., period_year=..., period_month=..., defaults={"data": ..., "row_count": ...})`. Re-running for a prior month safely overwrites. Snapshots are **never auto-deleted** (7-year CRA retention rule).

---

## 6. Report Definitions

### 6.1 Financial Reports

#### Monthly Revenue (`financial/revenue/`)
**Source models:** `Payment`, `ServiceFeePayment`, `PaymentIntent`  
**Filters:** Year + month picker (defaults to current month)  
**Columns:** fee_code, province, quantity, subtotal, tax, gross total, processor fee, net total  
**Aggregates:** Total gross, total net (gross − processor fees), total tax, payment count  
**Export:** Streaming CSV

#### Refund Summary (`financial/refunds/`)
**Source models:** `Refund`, `Payment`  
**Filters:** Date range, reason  
**Columns:** Period, refund count, refund total, refund rate (% of gross revenue), by reason breakdown  
**Note:** Only `gateway_status != GATEWAY_STATUS_FAILED` rows count (mirrors forms.py logic)

#### Failed Payments (`financial/failed/`)
**Source models:** `PaymentIntent`  
**Filters:** Date range  
**Columns:** Date, count of failed intents, total attempted value, breakdown by `gateway_error_code`  
**Note:** No payer PII — counts and amounts only

#### Reconciliation (`financial/reconciliation/`)
**Source models:** `PaymentIntent`, `Payment`, `Refund`, `ServiceFeePayment`  
**Filters:** Date range (max 92 days to prevent unbounded queries)  
**Columns:** `reference`, `status`, `amount_paid`, `refund_total`, `net`, `fee_code`, `created_at`  
**Export:** Streaming CSV / Excel (no payer name/email — reference + amounts only)

---

### 6.2 Donation & CRA Compliance Reports

#### T3010 Preparatory Data (`donations/t3010/`)
**Source models:** `Donation`, `OfficialDonationReceipt`  
**Filters:** Fiscal year (defaults to prior calendar year — matching CRA T3010 cycle)  
**Output:**
- Total donation revenue
- Total eligible gift amounts (for Line 4500 of T3010)
- Total advantage amounts
- Donation count
- Receipts issued / cancelled
- **Schedule 4 flag:** count of donations ≥ $10,000 (CRA requires confidential disclosure)
- Month-by-month breakdown table
**Export:** CSV (aggregates only — no donor PII)  
**Note:** This is preparatory data to feed into the charity's T3010 filing. It is not the T3010 itself.

#### Campaign Performance (`donations/campaigns/`)
**Source models:** `Donation`, `DonationCampaign`  
**Filters:** Date range, campaign  
**Columns:** Campaign name, total raised, eligible total, donor count (unique PKs), avg gift, recurring vs. one-time split, top month  
**Note:** "Donor count" uses COUNT(DISTINCT donor_id) — no names

#### Receipt Issuance Status (`donations/receipts/`)
**Source models:** `OfficialDonationReceipt`  
**Filters:** Year, status  
**Columns:** Month, issued count, cancelled count, pending count, annual consolidated count, email_sent rate  
**Export:** CSV (serial_number, status, receipt_date, eligible_amount — no donor name/address)

#### Donor Retention (`donations/retention/`)
**Source models:** `Donation`  
**Filters:** Year  
**Columns:** Month, new donors (first-ever donation), returning donors, total donors, retention rate  
**Definition:** "New donor" = donor_id with no prior `Donation` record before this month  
**Note:** Counts only — no names, emails, or addresses ever

---

### 6.3 Operational Reports

#### Webhook Health (`operational/webhooks/`)
**Source models:** `WebhookEvent`  
**Filters:** Date range (default: last 30 days)  
**Columns:** Event type, total received, processed, failed, avg retry count, failure rate  
**Aggregates:** Overall health score (processed/total), events requiring manual intervention (retry_count ≥ 3)

#### Gateway Success Rate (`operational/gateway/`)
**Source models:** `PaymentIntent`  
**Filters:** Date range (default: last 30 days)  
**Columns:** Day, total intents, succeeded, failed, abandoned (pending > 24h), success rate  
**Chart data:** Daily success rate time series (JSON for inline sparkline)

#### Task Throughput (`operational/tasks/`)
**Source models:** `django_celery_results.TaskResult`  
**Filters:** Date range, task name  
**Columns:** task_name, total runs, success, failure, failure rate, avg duration (seconds), p95 duration  
**Aggregates:** Top 10 slowest tasks, top 5 most-failed tasks  
**Note:** `traceback` field used for error grouping by exception type — truncated at 200 chars, no PII

---

## 7. Export Pipeline

### Strategy: Synchronous Streaming (V1)

No async job queue for V1. All exports use `StreamingHttpResponse` (CSV/Excel) or synchronous WeasyPrint (PDF). Adequate for expected data volumes (< 50K rows / month).

### PIPEDA Controls on All Exports

- Never include: donor name, email, address, postal code, SIN, phone
- Allowed: PKs (UUIDs), reference numbers, serial numbers, amounts, dates, status codes
- Every export logged to `ExportRecord` (actor_pk, export_type, period, row_count, actor_ip)
- No server-side file storage — response is streamed directly
- Filename never contains PII: `civicos-reconciliation-2025-03.csv`, not `john-smith-2025.csv`

### CSV/Excel (StreamingHttpResponse)

```python
# Pattern for all CSV exports
def stream_csv(queryset, columns, filename):
    def rows():
        yield columns  # header row
        for obj in queryset.iterator(chunk_size=500):
            yield [getattr(obj, col) for col in columns]
    response = StreamingHttpResponse(
        (csv_writer(row) for row in rows()),
        content_type="text/csv",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
```

### PDF Monthly Summary (WeasyPrint)

- Cover page: report title, period, generated_at, charity/municipality name
- Tables: top-level aggregates only (no row-level detail)
- Rendered from an HTML template, converted to PDF in-process
- Returned as `HttpResponse(content_type="application/pdf")`

---

## 8. Celery Beat Task

```python
@shared_task(
    bind=True,
    name="reports.compute_monthly_snapshots",
    acks_late=True,
    reject_on_worker_lost=True,
    queue="reports",
)
def compute_monthly_snapshots(self, year=None, month=None):
    """
    Idempotent nightly snapshot computation.
    If year/month omitted: computes previous calendar month.
    Backfills any missing months back to the first PaymentIntent.
    """
```

Scheduled in `setup_periodic_tasks`: `crontab(hour=2, minute=0)` America/Toronto.

---

## 9. Security & Compliance

| Control | Implementation |
|---|---|
| Auth | `is_staff=True` + specific permission per report type |
| PIPEDA — no PII in exports | Column whitelist enforced in all `stream_csv()` calls |
| PIPEDA — no PII in logs | `ExportRecord` stores only actor_pk (UUID), never email |
| Audit trail | Every export creates an `ExportRecord` row |
| Query safety | All aggregates via ORM — no raw SQL |
| DoS protection | Date range capped at 92 days for reconciliation export |
| Snapshot retention | `has_delete_permission=False` in admin; no auto-cleanup task |
| 7-year retention | `ExportRecord` and `ReportSnapshot` retained per CRA requirement |

---

## 10. Wave Plan

### Wave 1 — `apps/reports` scaffold + models + admin ⬜
- `apps/reports/` app structure (as above)
- `ReportSnapshot` + `ExportRecord` models + migration
- Admin (read-only for both models, has_delete_permission=False on ReportSnapshot)
- `apps.py` + add to `INSTALLED_APPS` + `LOCAL_APPS`
- Celery Beat task skeleton (`compute_monthly_snapshots`) + register in `setup_periodic_tasks`
- Service layer stubs (empty functions with docstrings)
- URL skeleton wired into main `urls.py`

### Wave 2 — Financial reports ⬜
- `services/financial.py` — query functions for revenue, refunds, failed payments, reconciliation
- `views/financial.py` — 4 views (real-time current month + snapshot prior months)
- Templates — 4 HTML report pages with WCAG-accessible tables
- Streaming CSV export for reconciliation
- Integration: backoffice sidebar `Reports` link → `/reports/`

### Wave 3 — Donations & CRA compliance reports ⬜
- `services/donations.py` — T3010 prep, campaign performance, receipt status, donor retention
- `views/donations.py` — 4 views
- Templates — 4 HTML report pages
- Streaming CSV export for T3010 prep data and receipts list
- Celery Beat task fully implemented (financial + donations snapshot computation)

### Wave 4 — Operational reports + snapshot task + PDF ⬜
- `services/operational.py` — webhook health, gateway success, task throughput
- `views/operational.py` — 3 views
- Templates — 3 HTML report pages
- Operational snapshot computation added to Celery task
- PDF export (WeasyPrint monthly summary) for financial + donation reports

### Wave 5 — Test suite + adversarial review + commit ⬜
- Full test suite (target 100+ tests)
  - Model tests (snapshot upsert idempotency, ExportRecord logging)
  - Service tests (aggregate correctness, edge cases — zero data, single row)
  - View tests (permission gates, filter params, response codes)
  - Export tests (CSV column whitelist, no PII, streaming)
  - Task tests (idempotency, backfill, lock guard)
- Adversarial security review
- Full suite run (2068+ existing + 100+ new = green)
- Commit

---

## 11. Out of Scope (V1)

- Async export jobs (S3 presigned URLs) — add in V2 if data volumes demand it
- REST API for BI tools — direct read-only PostgreSQL replica is simpler and more powerful; add if explicitly requested
- Real-time WebSocket dashboard updates — Django Channels not yet in stack
- Cross-tenant aggregate reports — each view scopes to the active tenant only
- T3010 e-filing — preparatory data only; actual CRA electronic filing is out of scope

---

## 12. Open Questions

- [ ] Should the `/reports/` URL namespace be under `/backoffice/reports/payments/` (sub-section of backoffice) or standalone at `/reports/`? Leaning standalone to keep backoffice focused on case management.
- [ ] PDF export — full-page portrait or landscape for wide tables? (Landscape preferred for reconciliation.)
- [ ] Should `compute_monthly_snapshots` backfill ALL missing months on first run, or only the prior month? (All missing months is safer for new deployments.)
- [ ] Donor retention definition — should "returning donor" mean donated in the same campaign, or any donation to the organization? (Any donation = simpler and more useful.)
