"""
Donations & CRA compliance report views — Wave 3.

Audience: Charity admins (payments.view_donationreport permission).

Views:
  DonationDashboardView  — monthly KPI summary; prior months from ReportSnapshot.
  AnnualDonationView     — full calendar-year summary with by-month breakdown.
  T3010PrepView          — CRA T3010 preparatory data for any fiscal year.
  ReceiptsExportView     — streaming CSV of receipts list (PIPEDA whitelist).
  T3010PrepExportView    — streaming CSV of T3010 prep data (aggregates only).

All views require staff login + explicit permission. No donor PII (name,
address, email) is ever passed to templates or exported — amounts, counts,
receipt serial numbers and campaign names only.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, timedelta

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.utils import timezone
from django.views.generic import TemplateView, View

from apps.forms.utils import _mask_ip
from apps.reports.exports.csv_export import export_receipts_csv, export_t3010_prep_csv
from apps.reports.forms import FiscalYearEndForm, ReceiptExportForm
from apps.reports.models import ExportRecord
from apps.reports.services.donations import (
    get_annual_donation_summary,
    get_monthly_donation_summary,
    get_t3010_preparatory_data,
)

logger = logging.getLogger("apps.reports.views.donations")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _current_toronto_ym() -> tuple[int, int]:
    """Return (year, month) in America/Toronto local time."""
    now_local = timezone.localtime(timezone.now())
    return now_local.year, now_local.month


def _current_toronto_year() -> int:
    return timezone.localtime(timezone.now()).year


def _parse_year_month(request) -> tuple[int, int] | None:
    """Parse ?year=YYYY&month=M, return (year, month) or None on error."""
    try:
        year = int(request.GET["year"])
        month = int(request.GET["month"])
        if not (2000 <= year <= 2100) or not (1 <= month <= 12):
            return None
        return year, month
    except (KeyError, ValueError, TypeError):
        return None


def _parse_year(request) -> int | None:
    """Parse ?year=YYYY, return int or None on error."""
    try:
        year = int(request.GET["year"])
        if not (2000 <= year <= 2100):
            return None
        return year
    except (KeyError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Dashboard view
# ---------------------------------------------------------------------------

class DonationDashboardView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Monthly donation summary dashboard.

    Current month: real-time queries via get_monthly_donation_summary.
    Prior months:  served from ReportSnapshot (pre-computed by Celery Beat);
                   falls back to real-time if no snapshot exists.

    Permission: payments.view_donationreport
    Template:   reports/donations/dashboard.html
    """

    permission_required = "payments.view_donationreport"
    template_name = "reports/donations/dashboard.html"

    def get_context_data(self, **kwargs):
        from decimal import Decimal as _Dec
        from apps.reports.models import ReportSnapshot

        ctx = super().get_context_data(**kwargs)

        current_year, current_month = _current_toronto_ym()
        ym = _parse_year_month(self.request)
        year, month = ym if ym else (current_year, current_month)

        is_current_month = (year == current_year and month == current_month)

        source = "realtime"
        donations = None

        # ── Snapshot fallback for prior months ────────────────────────────────
        if not is_current_month:
            try:
                snapshot = ReportSnapshot.objects.get(
                    report_type=ReportSnapshot.REPORT_TYPE_DONATIONS,
                    period_year=year,
                    period_month=month,
                )

                def _d(v):
                    try:
                        return _Dec(v)
                    except Exception:
                        return _Dec("0.00")

                snap_don = snapshot.data.get("donations", {})
                snap_rec = snapshot.data.get("receipts", {})

                raw_campaigns = snap_don.get("by_campaign", [])
                by_campaign = [
                    {
                        "campaign_name": c.get("campaign_name", ""),
                        "count": c.get("count", 0),
                        "total_amount": _d(c.get("total_amount", "0")),
                        "eligible_amount": _d(c.get("eligible_amount", "0")),
                    }
                    for c in raw_campaigns
                ]

                donations = {
                    "total_donations": _d(snap_don.get("total_donations", "0")),
                    "total_eligible_amount": _d(snap_don.get("total_eligible_amount", "0")),
                    "total_advantage_amount": _d(snap_don.get("total_advantage_amount", "0")),
                    "donation_count": snap_don.get("donation_count", 0),
                    "unique_donor_count": snap_don.get("unique_donor_count", 0),
                    "recurring_count": snap_don.get("recurring_count", 0),
                    "one_time_count": snap_don.get("one_time_count", 0),
                    "average_donation": _d(snap_don.get("average_donation", "0")),
                    "by_campaign": by_campaign,
                    "receipt_summary": {
                        "issued": snap_rec.get("issued", 0),
                        "cancelled": snap_rec.get("cancelled", 0),
                        "superseded": snap_rec.get("superseded", 0),
                        "total": snap_rec.get("total", 0),
                    },
                }
                source = "snapshot"
            except ReportSnapshot.DoesNotExist:
                pass  # fall through to real-time

        # ── Real-time query ───────────────────────────────────────────────────
        if donations is None:
            donations = get_monthly_donation_summary(year, month)

        # ── Recent snapshots for the sidebar table ─────────────────────────────
        recent_snapshots = (
            ReportSnapshot.objects.filter(
                report_type=ReportSnapshot.REPORT_TYPE_DONATIONS,
            )
            .order_by("-period_year", "-period_month")[:6]
        )

        logger.info(
            "reports.views.donations.dashboard user_pk=%s year=%s month=%s source=%s",
            self.request.user.pk, year, month, source,
        )

        ctx.update({
            "year": year,
            "month": month,
            "month_label": f"{calendar.month_name[month]} {year}",
            # Last day of the current month — used by the receipts export URL
            # in the template. calendar.monthrange() is leap-year-safe.
            "month_last_day": calendar.monthrange(year, month)[1],
            "is_current_month": is_current_month,
            "data_source": source,
            "donations": donations,
            "recent_snapshots": recent_snapshots,
        })
        return ctx


# ---------------------------------------------------------------------------
# Annual summary view
# ---------------------------------------------------------------------------

class AnnualDonationView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Full-year donation summary.

    Shows total donations, eligible amounts, large-gift count, and a
    by-month / by-campaign breakdown for the selected calendar year.

    Always real-time (annual data not pre-computed in snapshots — cheap
    enough query for a once-or-twice-a-year T3010 prep workflow).

    Permission: payments.view_donationreport
    Template:   reports/donations/annual.html
    """

    permission_required = "payments.view_donationreport"
    template_name = "reports/donations/annual.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        current_year = _current_toronto_year()
        year = _parse_year(self.request) or current_year - 1  # default to previous year

        annual = get_annual_donation_summary(year)

        logger.info(
            "reports.views.donations.annual user_pk=%s year=%s donation_count=%s",
            self.request.user.pk, year, annual["donation_count"],
        )

        ctx.update({
            "year": year,
            "current_year": current_year,
            "annual": annual,
        })
        return ctx


# ---------------------------------------------------------------------------
# T3010 prep view
# ---------------------------------------------------------------------------

class T3010PrepView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    CRA T3010 preparatory data report.

    Shows the aggregated figures a charity needs to complete T3010:
    total receipted donations (line 4500), eligible amounts (line 4510),
    filing deadline, and by-month / by-campaign breakdown.

    Fiscal year end defaults to December 31 of the previous year (most common
    Canadian charity fiscal year end). Finance staff may override via the
    FiscalYearEndForm.

    No individual donor PII in template context.

    Permission: payments.view_donationreport
    Template:   reports/donations/t3010_prep.html
    """

    permission_required = "payments.view_donationreport"
    template_name = "reports/donations/t3010_prep.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # Default: December 31 of previous calendar year
        current_year = _current_toronto_year()
        default_fy_end = date(current_year - 1, 12, 31)

        form = FiscalYearEndForm(self.request.GET or None)
        if form.is_bound and form.is_valid():
            fiscal_year_end = form.cleaned_data["fiscal_year_end"]
        else:
            fiscal_year_end = default_fy_end
            # Pre-fill the form with the default value for display
            form = FiscalYearEndForm(
                initial={"fiscal_year_end": default_fy_end.isoformat()}
            )

        t3010 = get_t3010_preparatory_data(fiscal_year_end)

        logger.info(
            "reports.views.donations.t3010_prep user_pk=%s fiscal_year_end=%s "
            "donation_count=%s total_receipted=%s",
            self.request.user.pk, fiscal_year_end,
            t3010["donation_count"], t3010["total_receipted_donations"],
        )

        ctx.update({
            "form": form,
            "t3010": t3010,
            "fiscal_year_end": fiscal_year_end,
            "fiscal_year_start": t3010["fiscal_year_start"],
            "filing_deadline": t3010["filing_deadline"],
        })
        return ctx


# ---------------------------------------------------------------------------
# Export views
# ---------------------------------------------------------------------------

class ReceiptsExportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """
    Streaming CSV export of donation receipts list.

    PIPEDA column whitelist enforced in csv_export.export_receipts_csv:
    no donor name, address, or email in output.

    Registered in urls.py with transaction.non_atomic_requests() so that the
    streaming response does not hold the DB connection open for the full
    download duration (ATOMIC_REQUESTS=True would otherwise keep the
    transaction open until the last chunk reaches the client). The ExportRecord
    is created atomically inside get() before streaming begins.

    Validates via ReceiptExportForm (MAX_RECEIPT_EXPORT_DAYS = 366) so a full
    tax year can be exported in one request.

    Creates ExportRecord before streaming.
    Permission: payments.export_donationreport
    """

    permission_required = "payments.export_donationreport"

    def get(self, request, *args, **kwargs):
        form = ReceiptExportForm(request.GET)
        if not form.is_valid():
            errors = "; ".join(
                str(e) for field_errors in form.errors.values() for e in field_errors
            )
            return HttpResponseBadRequest(f"Invalid parameters: {errors}")
        start, end = form.cleaned_data["start"], form.cleaned_data["end"]

        from apps.reports.services.donations import get_receipt_list_queryset
        row_count = get_receipt_list_queryset(start, end).count()

        masked_ip = _mask_ip(request.META.get("REMOTE_ADDR") or "")
        # Explicit atomic block: non_atomic_requests removed the outer
        # transaction, so the ExportRecord must be committed before streaming.
        with transaction.atomic():
            ExportRecord.objects.create(
                export_type=ExportRecord.EXPORT_TYPE_RECEIPTS,
                format=ExportRecord.FORMAT_CSV,
                period_start=start,
                period_end=end,
                actor_pk=request.user.pk,
                actor_ip=masked_ip or None,
                row_count=row_count,
            )

        logger.info(
            "reports.views.donations.receipts_export actor_pk=%s start=%s end=%s row_count=%s",
            request.user.pk, start, end, row_count,
        )

        return export_receipts_csv(start, end)


class T3010PrepExportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """
    Streaming CSV export of T3010 preparatory data.

    Aggregates only — no donor PII. Creates ExportRecord before streaming.
    Registered in urls.py with transaction.non_atomic_requests() (same reason
    as ReceiptsExportView — avoids holding the DB transaction for the stream).
    Permission: payments.export_donationreport
    """

    permission_required = "payments.export_donationreport"

    def get(self, request, *args, **kwargs):
        form = FiscalYearEndForm(request.GET)
        if not form.is_valid():
            errors = "; ".join(
                str(e) for field_errors in form.errors.values() for e in field_errors
            )
            return HttpResponseBadRequest(f"Invalid parameters: {errors}")
        fiscal_year_end: date = form.cleaned_data["fiscal_year_end"]

        # row_count = monthly rows + campaign rows + summary rows
        # Computed once here; passed into export_t3010_prep_csv to avoid a
        # second DB round-trip (previously get_t3010_preparatory_data was
        # called twice — once here and once inside the export function).
        from apps.reports.services.donations import get_t3010_preparatory_data
        data = get_t3010_preparatory_data(fiscal_year_end)
        row_count = len(data["by_month"]) + len(data["by_campaign"]) + 4  # 4 summary rows

        masked_ip = _mask_ip(request.META.get("REMOTE_ADDR") or "")
        with transaction.atomic():
            ExportRecord.objects.create(
                export_type=ExportRecord.EXPORT_TYPE_T3010,
                format=ExportRecord.FORMAT_CSV,
                period_start=data["fiscal_year_start"],
                period_end=data["fiscal_year_end"],
                actor_pk=request.user.pk,
                actor_ip=masked_ip or None,
                row_count=row_count,
            )

        logger.info(
            "reports.views.donations.t3010_prep_export actor_pk=%s fiscal_year_end=%s",
            request.user.pk, fiscal_year_end,
        )

        # Pass pre-computed data to avoid a second DB call inside the exporter.
        return export_t3010_prep_csv(fiscal_year_end, data=data)
