"""
URL configuration for the back-office application.

All routes are staff-only; access control is enforced at the view level via
StaffRequiredMixin.  Mount this module under /backoffice/ in config/urls.py.

Namespace: ``backoffice``
"""

from django.urls import path

from apps.backoffice.views.audit import AuditLogListView
from apps.backoffice.views.citizens import (
    CitizenDeactivateView,
    CitizenDetailView,
    CitizenListView,
)
from apps.backoffice.views.consent import (
    CitizenConsentHistoryView,
    ConsentDataRequestDetailView,
    ConsentDataRequestListView,
)
from apps.backoffice.views.dashboard import DashboardView
from apps.backoffice.views.reports import ReportsView
from apps.backoffice.views.service_requests import (
    ServiceRequestDetailView,
    ServiceRequestListView,
    ServiceRequestNotesView,
    ServiceRequestStatusView,
)
from apps.backoffice.views.staff_notifications import (
    StaffNotificationListView,
    StaffNotificationSendView,
)
from apps.backoffice.views.work_items import (
    WorkItemAssignView,
    WorkItemClaimView,
    WorkItemCommentView,
    WorkItemDetailView,
    WorkItemListView,
    WorkItemStatusView,
)

app_name = "backoffice"

urlpatterns = [
    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------
    path("", DashboardView.as_view(), name="dashboard"),
    # ------------------------------------------------------------------
    # Service requests
    # ------------------------------------------------------------------
    path("service-requests/", ServiceRequestListView.as_view(), name="sr-list"),
    path(
        "service-requests/<str:reference_number>/",
        ServiceRequestDetailView.as_view(),
        name="sr-detail",
    ),
    path(
        "service-requests/<str:reference_number>/status/",
        ServiceRequestStatusView.as_view(),
        name="sr-status",
    ),
    path(
        "service-requests/<str:reference_number>/notes/",
        ServiceRequestNotesView.as_view(),
        name="sr-notes",
    ),
    # ------------------------------------------------------------------
    # Work items
    # ------------------------------------------------------------------
    path("work-items/", WorkItemListView.as_view(), name="wi-list"),
    path("work-items/<uuid:pk>/", WorkItemDetailView.as_view(), name="wi-detail"),
    path(
        "work-items/<uuid:pk>/claim/",
        WorkItemClaimView.as_view(),
        name="wi-claim",
    ),
    path(
        "work-items/<uuid:pk>/assign/",
        WorkItemAssignView.as_view(),
        name="wi-assign",
    ),
    path(
        "work-items/<uuid:pk>/status/",
        WorkItemStatusView.as_view(),
        name="wi-status",
    ),
    path(
        "work-items/<uuid:pk>/comment/",
        WorkItemCommentView.as_view(),
        name="wi-comment",
    ),
    # ------------------------------------------------------------------
    # Citizens
    # ------------------------------------------------------------------
    path("citizens/", CitizenListView.as_view(), name="citizen-list"),
    path("citizens/<int:pk>/", CitizenDetailView.as_view(), name="citizen-detail"),
    path(
        "citizens/<int:pk>/deactivate/",
        CitizenDeactivateView.as_view(),
        name="citizen-deactivate",
    ),
    # ------------------------------------------------------------------
    # Consent & Privacy (PIPEDA)
    # ------------------------------------------------------------------
    path("data-requests/", ConsentDataRequestListView.as_view(), name="data-request-list"),
    path(
        "data-requests/<uuid:pk>/",
        ConsentDataRequestDetailView.as_view(),
        name="data-request-detail",
    ),
    path(
        "citizens/<int:pk>/consent/",
        CitizenConsentHistoryView.as_view(),
        name="citizen-consent-history",
    ),
    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------
    path("audit/", AuditLogListView.as_view(), name="audit-list"),
    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------
    path("reports/", ReportsView.as_view(), name="reports"),
    # ------------------------------------------------------------------
    # Staff notifications
    # ------------------------------------------------------------------
    path(
        "notifications/",
        StaffNotificationListView.as_view(),
        name="notification-list",
    ),
    path(
        "notifications/send/",
        StaffNotificationSendView.as_view(),
        name="notification-send",
    ),
]
