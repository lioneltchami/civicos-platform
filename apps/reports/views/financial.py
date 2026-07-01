"""
Financial report views.

Audience: Finance staff (payments.view_financialreport permission).

Views:
  FinancialDashboardView   — monthly revenue overview (current month: real-time;
                             prior months: ReportSnapshot)
  ReconciliationView       — date-range payment reconciliation table
  ReconciliationExportView — streaming CSV/Excel export of reconciliation data
  RevenueExportView        — monthly revenue CSV/Excel export
  MonthlySummaryPdfView    — monthly financial summary PDF (Wave 4)

All views require staff login + explicit permission. No PII is ever passed to
templates — amounts, counts, and fee codes only.

Implemented in Wave 2.
"""
from __future__ import annotations

import logging

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.views.generic import TemplateView

logger = logging.getLogger("apps.reports.views.financial")


class FinancialDashboardView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Monthly financial summary dashboard.

    Current month: queries source models in real-time.
    Prior months:  served from ReportSnapshot (pre-computed nightly).

    Permission: payments.view_financialreport
    Template:   reports/financial/dashboard.html
    """

    permission_required = "payments.view_financialreport"
    template_name = "reports/financial/dashboard.html"

    def get_context_data(self, **kwargs):
        raise NotImplementedError("Implemented in Wave 2")


class ReconciliationView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Date-range payment reconciliation table.

    Max range: 92 days (enforced by the view — prevents unbounded queries).
    No payer PII in template context.

    Permission: payments.view_financialreport
    Template:   reports/financial/reconciliation.html
    """

    permission_required = "payments.view_financialreport"
    template_name = "reports/financial/reconciliation.html"

    def get_context_data(self, **kwargs):
        raise NotImplementedError("Implemented in Wave 2")


class ReconciliationExportView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Streaming CSV/Excel export of payment reconciliation data.

    Creates an ExportRecord (audit trail) before streaming. Actor PK logged,
    not email (PIPEDA).

    Permission: payments.export_financialreport
    """

    permission_required = "payments.export_financialreport"

    def get(self, request, *args, **kwargs):
        raise NotImplementedError("Implemented in Wave 2")


class RevenueExportView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Monthly revenue CSV/Excel export.

    Creates an ExportRecord before streaming.
    Permission: payments.export_financialreport
    """

    permission_required = "payments.export_financialreport"

    def get(self, request, *args, **kwargs):
        raise NotImplementedError("Implemented in Wave 2")


class MonthlySummaryPdfView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Monthly financial summary PDF (WeasyPrint).

    Creates an ExportRecord before streaming. PDF is never stored on disk.
    Permission: payments.export_financialreport

    Implemented in Wave 4.
    """

    permission_required = "payments.export_financialreport"

    def get(self, request, *args, **kwargs):
        raise NotImplementedError("Implemented in Wave 4")
