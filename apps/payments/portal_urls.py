"""
URL configuration for the donor payments portal.
Wave 5 — GovStack Payments Building Block.

Namespace: donor_portal
Mount point: /donate/portal/ (configured in config/urls.py)

Note: the existing citizen portal at apps.portal uses namespace "portal"
(mounted at /portal/). This payments donor portal uses a distinct namespace
"donor_portal" to avoid collision.
"""
from django.urls import path

from apps.payments.views.portal import (
    DonationHistoryView,
    DonorPortalDashboardView,
    ReceiptDownloadView,
    ReceiptListView,
    RecurringGiftDetailView,
    RecurringGiftListView,
)

app_name = "donor_portal"

urlpatterns = [
    # Dashboard — summary of giving history
    path("", DonorPortalDashboardView.as_view(), name="dashboard"),
    # Paginated donation history with optional year filter
    path("donations/", DonationHistoryView.as_view(), name="donation_history"),
    # Paginated receipt list with optional year filter
    path("receipts/", ReceiptListView.as_view(), name="receipt_list"),
    # Secure PDF download — UUID only in URL, never storage path
    path(
        "receipts/<uuid:receipt_pk>/download/",
        ReceiptDownloadView.as_view(),
        name="receipt_download",
    ),
    # Recurring gift plans — list and detail
    path("recurring/", RecurringGiftListView.as_view(), name="recurring_list"),
    path(
        "recurring/<uuid:pk>/",
        RecurringGiftDetailView.as_view(),
        name="recurring_detail",
    ),
]
