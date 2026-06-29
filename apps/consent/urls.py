from django.urls import path

from . import views

app_name = "consent"

urlpatterns = [
    path("", views.ConsentDashboardView.as_view(), name="dashboard"),
    path("update/", views.ConsentUpdateView.as_view(), name="update"),
    path(
        "withdraw/<slug:category_slug>/confirm/",
        views.ConsentWithdrawConfirmView.as_view(),
        name="withdraw-confirm",
    ),
    path("export/request/", views.ExportRequestView.as_view(), name="export-request"),
    path("export/status/", views.ExportStatusView.as_view(), name="export-status"),
    path(
        "export/download/<uuid:token>/",
        views.ExportDownloadView.as_view(),
        name="export-download",
    ),
]
