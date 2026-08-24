"""
Analytics & Reporting BB — URL configuration.

All URLs are under the /reports/ prefix (wired in config/urls.py).
All views require staff login + explicit permission (enforced in each view class).

URL layout:
    /reports/financial/                      — financial dashboard
    /reports/financial/reconciliation/       — reconciliation table
    /reports/financial/reconciliation/export/ — reconciliation CSV/Excel export
    /reports/financial/revenue/export/       — monthly revenue export
    /reports/financial/<year>/<month>/pdf/   — monthly summary PDF (Wave 4)

    /reports/donations/                      — donations dashboard
    /reports/donations/annual/               — full-year summary
    /reports/donations/t3010/                — T3010 prep report
    /reports/donations/receipts/export/      — receipts list export
    /reports/donations/t3010/export/         — T3010 prep export

    /reports/operational/                    — operational dashboard
    /reports/operational/task-failures/      — task failure detail

Views are imported lazily inside urlpatterns to keep startup imports minimal.
"""

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.urls import path

from apps.reports.views.combined import CombinedImpactView
from apps.reports.views.donations import (
    AnnualDonationView,
    DonationDashboardView,
    ReceiptsExportView,
    T3010PrepExportView,
    T3010PrepView,
)
from apps.reports.views.financial import (
    FinancialDashboardView,
    MonthlySummaryPdfView,
    ReconciliationExportView,
    ReconciliationView,
    RevenueExportView,
)
from apps.reports.views.operational import (
    OperationalDashboardView,
    TaskFailureDetailView,
)
from apps.reports.views.volunteers import VolunteerImpactDashboardView

app_name = "reports"

urlpatterns = [
    # ── Financial ────────────────────────────────────────────────────────────
    path(
        "financial/",
        FinancialDashboardView.as_view(),
        name="financial-dashboard",
    ),
    path(
        "financial/reconciliation/",
        ReconciliationView.as_view(),
        name="reconciliation",
    ),
    path(
        "financial/reconciliation/export/",
        ReconciliationExportView.as_view(),
        name="reconciliation-export",
    ),
    path(
        "financial/revenue/export/",
        RevenueExportView.as_view(),
        name="revenue-export",
    ),
    path(
        "financial/<int:year>/<int:month>/pdf/",
        MonthlySummaryPdfView.as_view(),
        name="monthly-summary-pdf",
    ),
    # ── Donations / CRA ──────────────────────────────────────────────────────
    path(
        "donations/",
        DonationDashboardView.as_view(),
        name="donations-dashboard",
    ),
    path(
        "donations/annual/",
        AnnualDonationView.as_view(),
        name="donations-annual",
    ),
    path(
        "donations/t3010/",
        T3010PrepView.as_view(),
        name="t3010-prep",
    ),
    path(
        "donations/receipts/export/",
        # non_atomic_requests: streaming responses must not hold the DB connection
        # open for the full download. The ExportRecord is committed atomically
        # inside the view before streaming begins.
        transaction.non_atomic_requests(ReceiptsExportView.as_view()),
        name="receipts-export",
    ),
    path(
        "donations/t3010/export/",
        transaction.non_atomic_requests(T3010PrepExportView.as_view()),
        name="t3010-prep-export",
    ),
    # ── Operational ──────────────────────────────────────────────────────────
    path(
        "operational/",
        OperationalDashboardView.as_view(),
        name="operational-dashboard",
    ),
    path(
        "operational/task-failures/",
        TaskFailureDetailView.as_view(),
        name="task-failures",
    ),
    # ── Integration Wave: volunteers + combined ───────────────────────────────
    path(
        "volunteers/",
        login_required(VolunteerImpactDashboardView.as_view()),
        name="volunteers-dashboard",
    ),
    path(
        "combined/",
        login_required(CombinedImpactView.as_view()),
        name="combined-impact",
    ),
]
