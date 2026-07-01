"""
Financial report views — Wave 2.

Audience: Finance staff (payments.view_financialreport permission).

Views:
  FinancialDashboardView   — monthly revenue overview.
                             Current month: real-time service queries.
                             Prior months:  ReportSnapshot (pre-computed),
                             falling back to real-time if snapshot missing.
  ReconciliationView       — date-range payment reconciliation table.
  ReconciliationExportView — streaming CSV export of reconciliation data.
  RevenueExportView        — monthly revenue CSV export.
  MonthlySummaryPdfView    — monthly financial summary PDF (Wave 4 stub).

All views require staff login + explicit permission. No PII is passed to
templates — amounts, counts, and fee codes only. Actor PK (UUID) is logged,
never actor email (PIPEDA).
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, timedelta

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.http import HttpResponseBadRequest
from django.utils import timezone
from django.views.generic import TemplateView, View

from apps.forms.utils import _mask_ip
from apps.reports.exports.csv_export import export_reconciliation_csv, export_revenue_csv
from apps.reports.forms import DateRangeForm, MAX_RANGE_DAYS
from apps.reports.models import ExportRecord
from apps.reports.services.financial import (
    get_failed_payments,
    get_monthly_revenue,
    get_reconciliation_queryset,
    get_refund_summary,
)

logger = logging.getLogger("apps.reports.views.financial")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _current_toronto_ym() -> tuple[int, int]:
    """Return (year, month) in America/Toronto local time."""
    now_local = timezone.localtime(timezone.now())
    return now_local.year, now_local.month


def _parse_year_month(request) -> tuple[int, int] | None:
    """
    Parse ?year=YYYY&month=M from request.GET.

    Returns (year, month) if both are present and valid integers in sensible
    ranges, else None (caller defaults to current month).
    """
    try:
        year = int(request.GET["year"])
        month = int(request.GET["month"])
        if not (2000 <= year <= 2100) or not (1 <= month <= 12):
            return None
        return year, month
    except (KeyError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Dashboard view
# ---------------------------------------------------------------------------

class FinancialDashboardView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Monthly financial summary dashboard.

    Current month: queries source models in real-time.
    Prior months:  served from ReportSnapshot (pre-computed by Celery Beat);
                   falls back to real-time if no snapshot exists yet.

    Permission: payments.view_financialreport
    Template:   reports/financial/dashboard.html
    """

    permission_required = "payments.view_financialreport"
    template_name = "reports/financial/dashboard.html"

    def get_context_data(self, **kwargs):
        from apps.reports.models import ReportSnapshot

        ctx = super().get_context_data(**kwargs)

        current_year, current_month = _current_toronto_ym()
        ym = _parse_year_month(self.request)
        year, month = ym if ym else (current_year, current_month)

        is_current_month = (year == current_year and month == current_month)

        # ── Determine data source ─────────────────────────────────────────────
        source = "realtime"
        revenue = None
        refunds = None

        if not is_current_month:
            # Try to load a pre-computed snapshot
            try:
                snapshot = ReportSnapshot.objects.get(
                    report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
                    period_year=year,
                    period_month=month,
                )
                # Deserialise Decimal strings stored in JSONB
                from decimal import Decimal as _Dec

                def _d(v):
                    try:
                        return _Dec(v)
                    except Exception:
                        return _Dec("0.00")

                snap_rev = snapshot.data.get("revenue", {})
                snap_ref = snapshot.data.get("refunds", {})

                by_fc_raw = snap_rev.get("by_fee_code", {})
                by_fee_code = {}
                for label, d in by_fc_raw.items():
                    by_fee_code[label] = {
                        "gross": _d(d.get("gross", "0")),
                        "net": _d(d.get("net", "0")),
                        "tax": _d(d.get("tax", "0")),
                        "processor_fees": _d(d.get("processor_fees", "0")),
                        "count": d.get("count", 0),
                    }

                revenue = {
                    "total_gross": _d(snap_rev.get("total_gross", "0")),
                    "total_net": _d(snap_rev.get("total_net", "0")),
                    "total_tax": _d(snap_rev.get("total_tax", "0")),
                    "total_processor_fees": _d(snap_rev.get("total_processor_fees", "0")),
                    "payment_count": snap_rev.get("payment_count", 0),
                    "by_fee_code": by_fee_code,
                }
                refunds = {
                    "total_refunded": _d(snap_ref.get("total_refunded", "0")),
                    "refund_count": snap_ref.get("refund_count", 0),
                    "by_reason": {
                        k: {
                            "amount": _d(v.get("amount", "0")),
                            "count": v.get("count", 0),
                        }
                        for k, v in snap_ref.get("by_reason", {}).items()
                    },
                }
                source = "snapshot"
            except ReportSnapshot.DoesNotExist:
                pass  # fall through to real-time below

        if revenue is None:
            revenue = get_monthly_revenue(year, month)
        if refunds is None:
            refunds = get_refund_summary(year, month)

        failed_count = get_failed_payments(year, month).count()

        # ── Recent monthly snapshots (summary table / sparkline) ──────────────
        recent_snapshots = (
            ReportSnapshot.objects.filter(
                report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
            )
            .order_by("-period_year", "-period_month")[:6]
        )

        logger.info(
            "reports.views.financial.dashboard user_pk=%s year=%s month=%s source=%s",
            self.request.user.pk,
            year,
            month,
            source,
        )

        ctx.update({
            "year": year,
            "month": month,
            "month_label": f"{calendar.month_name[month]} {year}",
            "is_current_month": is_current_month,
            "data_source": source,
            "revenue": revenue,
            "refunds": refunds,
            "failed_count": failed_count,
            "recent_snapshots": recent_snapshots,
        })
        return ctx


# ---------------------------------------------------------------------------
# Reconciliation table view
# ---------------------------------------------------------------------------

class ReconciliationView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Date-range payment reconciliation table.

    Max range: 92 days (enforced by DateRangeForm and this view). No payer PII
    in template context — reference numbers, amounts, and fee codes only.

    Permission: payments.view_financialreport
    Template:   reports/financial/reconciliation.html
    """

    permission_required = "payments.view_financialreport"
    template_name = "reports/financial/reconciliation.html"

    # Maximum rows shown in the HTML table to avoid overwhelming the browser.
    # Full dataset is always available via the CSV export.
    MAX_DISPLAY_ROWS = 200

    def get_context_data(self, **kwargs):
        from decimal import Decimal

        ctx = super().get_context_data(**kwargs)

        form = DateRangeForm(self.request.GET or None)
        payments = None
        period_start = period_end = None
        total_paid = total_refunded = net = Decimal("0.00")
        count = 0
        truncated = False

        if form.is_bound and form.is_valid():
            from django.db.models import Sum as _Sum

            period_start, period_end = form.cleaned_data["start"], form.cleaned_data["end"]
            qs = get_reconciliation_queryset(period_start, period_end)

            # Full totals from DB (single aggregation over the annotated QS).
            full_agg = qs.aggregate(
                tp=_Sum("amount_paid", default=Decimal("0.00")),
                tr=_Sum("refund_total", default=Decimal("0.00")),
            )
            total_paid = full_agg["tp"]
            total_refunded = full_agg["tr"]
            net = total_paid - total_refunded
            count = qs.count()
            truncated = count > self.MAX_DISPLAY_ROWS

            # Slice for HTML display only; exports get the full dataset via streaming.
            # Convert to plain dicts so the template can compute net without a
            # custom filter (amount_paid - refund_total).
            payments = [
                {
                    "reference": p.intent.reference,
                    "status": p.intent.status,
                    "fee_code": p.fee_code or "",
                    "amount_paid": p.amount_paid,
                    "refund_total": p.refund_total,
                    "net": p.amount_paid - p.refund_total,
                    "paid_at": p.paid_at,
                }
                for p in qs[: self.MAX_DISPLAY_ROWS]
            ]

        ctx.update({
            "form": form,
            "payments": payments,
            "period_start": period_start,
            "period_end": period_end,
            "total_paid": total_paid,
            "total_refunded": total_refunded,
            "net": net,
            "count": count,
            "truncated": truncated,
            "max_display_rows": self.MAX_DISPLAY_ROWS,
        })
        return ctx


# ---------------------------------------------------------------------------
# Export views
# ---------------------------------------------------------------------------

class ReconciliationExportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """
    Streaming CSV export of payment reconciliation data.

    Creates an ExportRecord (audit trail) before streaming. Actor PK logged,
    never actor email (PIPEDA). IP is masked before storage.

    Permission: payments.export_financialreport
    """

    permission_required = "payments.export_financialreport"

    def get(self, request, *args, **kwargs):
        # ── Parse and validate inputs via DateRangeForm (single source of truth) ──
        # DateRangeForm validates start/end presence, ISO format, end≥start, and
        # the MAX_RANGE_DAYS cap — no need to duplicate that logic here.
        form = DateRangeForm(request.GET)
        if not form.is_valid():
            errors = "; ".join(
                str(e) for field_errors in form.errors.values() for e in field_errors
            )
            return HttpResponseBadRequest(f"Invalid parameters: {errors}")
        start, end = form.cleaned_data["start"], form.cleaned_data["end"]

        # ── Audit record ──────────────────────────────────────────────────────
        # COUNT(*) is cheap (index scan on paid_at); do it before streaming so
        # the ExportRecord has an accurate row_count for the audit trail.
        row_count = get_reconciliation_queryset(start, end).count()
        masked_ip = _mask_ip(request.META.get("REMOTE_ADDR") or "")
        ExportRecord.objects.create(
            export_type=ExportRecord.EXPORT_TYPE_RECONCILIATION,
            format=ExportRecord.FORMAT_CSV,
            period_start=start,
            period_end=end,
            actor_pk=request.user.pk,
            actor_ip=masked_ip or None,
            row_count=row_count,
        )

        logger.info(
            "reports.views.financial.reconciliation_export actor_pk=%s start=%s end=%s",
            request.user.pk,
            start,
            end,
        )

        return export_reconciliation_csv(start, end)


class RevenueExportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """
    Monthly revenue CSV export.

    Creates an ExportRecord before streaming.
    Permission: payments.export_financialreport
    """

    permission_required = "payments.export_financialreport"

    def get(self, request, *args, **kwargs):
        # ── Parse inputs ──────────────────────────────────────────────────────
        try:
            year = int(request.GET["year"])
            month = int(request.GET["month"])
            if not (2000 <= year <= 2100) or not (1 <= month <= 12):
                raise ValueError("Out of range")
        except (KeyError, ValueError, TypeError):
            return HttpResponseBadRequest(
                "Missing or invalid 'year'/'month' parameters."
            )

        # ── Audit record ──────────────────────────────────────────────────────
        # row_count = number of distinct fee-code rows in the CSV (one per label).
        revenue_data = get_monthly_revenue(year, month)
        row_count = len(revenue_data["by_fee_code"])
        last_day = calendar.monthrange(year, month)[1]
        masked_ip = _mask_ip(request.META.get("REMOTE_ADDR") or "")
        ExportRecord.objects.create(
            export_type=ExportRecord.EXPORT_TYPE_REVENUE,
            format=ExportRecord.FORMAT_CSV,
            period_start=date(year, month, 1),
            period_end=date(year, month, last_day),
            actor_pk=request.user.pk,
            actor_ip=masked_ip or None,
            row_count=row_count,
        )

        logger.info(
            "reports.views.financial.revenue_export actor_pk=%s year=%s month=%s",
            request.user.pk,
            year,
            month,
        )

        return export_revenue_csv(year, month, revenue=revenue_data)


class MonthlySummaryPdfView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """
    Monthly financial summary PDF (WeasyPrint).

    URL pattern: /reports/financial/<year>/<month>/pdf/
    URL kwargs:  year (int), month (int)

    Creates an ExportRecord (audit trail) before generating the PDF. PDF bytes
    are generated in-process and streamed directly in the response — never
    stored on disk or in S3 (PIPEDA cross-border risk).

    Permission: payments.export_financialreport
    """

    permission_required = "payments.export_financialreport"

    def get(self, request, *args, **kwargs):
        year = kwargs.get("year")
        month = kwargs.get("month")

        if not year or not month or not (2000 <= year <= 2100) or not (1 <= month <= 12):
            return HttpResponseBadRequest("Invalid year or month.")

        # ── Generate PDF first — only write audit record on success ──────────
        # ExportRecord creation is intentionally deferred until after PDF bytes
        # are produced. If WeasyPrint raises (missing C libs, OOM, template error)
        # we must not record a completed export that never reached the client.
        from apps.reports.exports.pdf_export import export_monthly_summary_pdf
        response = export_monthly_summary_pdf(year, month)

        # ── Audit record (written only if PDF generation succeeded) ──────────
        last_day = calendar.monthrange(year, month)[1]
        masked_ip = _mask_ip(request.META.get("REMOTE_ADDR") or "")
        ExportRecord.objects.create(
            export_type=ExportRecord.EXPORT_TYPE_REVENUE,
            format=ExportRecord.FORMAT_PDF,
            period_start=date(year, month, 1),
            period_end=date(year, month, last_day),
            actor_pk=request.user.pk,
            actor_ip=masked_ip or None,
            row_count=0,  # aggregated PDF — row count not meaningful
        )

        logger.info(
            "reports.views.financial.monthly_summary_pdf actor_pk=%s year=%s month=%s",
            request.user.pk, year, month,
        )

        return response
