"""
Donations & CRA compliance report views.

Audience: Charity admins (payments.view_donationreport permission).

Views:
  DonationDashboardView  — monthly donation summary
  AnnualDonationView     — full-year donation summary (T3010 prep)
  T3010PrepView          — CRA T3010 preparatory data report
  ReceiptsExportView     — streaming CSV/Excel export of receipts list
  T3010PrepExportView    — streaming CSV export of T3010 prep data

All views require staff login + explicit permission. No donor PII is ever
passed to templates — amounts, counts, receipt numbers only.

Implemented in Wave 3.
"""
from __future__ import annotations

import logging

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.views.generic import TemplateView

logger = logging.getLogger("apps.reports.views.donations")


class DonationDashboardView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Monthly donation summary dashboard.

    Current month: real-time queries. Prior months: ReportSnapshot.

    Permission: payments.view_donationreport
    Template:   reports/donations/dashboard.html
    """

    permission_required = "payments.view_donationreport"
    template_name = "reports/donations/dashboard.html"

    def get_context_data(self, **kwargs):
        raise NotImplementedError("Implemented in Wave 3")


class AnnualDonationView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Full-year donation summary.

    Used to review annual totals before preparing T3010.

    Permission: payments.view_donationreport
    Template:   reports/donations/annual.html
    """

    permission_required = "payments.view_donationreport"
    template_name = "reports/donations/annual.html"

    def get_context_data(self, **kwargs):
        raise NotImplementedError("Implemented in Wave 3")


class T3010PrepView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    CRA T3010 preparatory data report.

    Shows the aggregated figures a charity needs to complete T3010:
    total receipted donations (line 4500), eligible amounts (line 4510),
    filing deadline, and by-month breakdown.

    No individual donor PII in template context.

    Permission: payments.view_donationreport
    Template:   reports/donations/t3010_prep.html
    """

    permission_required = "payments.view_donationreport"
    template_name = "reports/donations/t3010_prep.html"

    def get_context_data(self, **kwargs):
        raise NotImplementedError("Implemented in Wave 3")


class ReceiptsExportView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Streaming CSV/Excel export of donation receipts list.

    PIPEDA column whitelist enforced in csv_export.export_receipts_csv:
    no donor name/address/email in output.

    Creates ExportRecord before streaming.
    Permission: payments.export_donationreport
    """

    permission_required = "payments.export_donationreport"

    def get(self, request, *args, **kwargs):
        raise NotImplementedError("Implemented in Wave 3")


class T3010PrepExportView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Streaming CSV export of T3010 preparatory data.

    Aggregates only — no donor PII.
    Creates ExportRecord before streaming.
    Permission: payments.export_donationreport
    """

    permission_required = "payments.export_donationreport"

    def get(self, request, *args, **kwargs):
        raise NotImplementedError("Implemented in Wave 3")
