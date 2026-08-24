"""URL patterns for the citizen portal building block."""

from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    # Citizen views
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("requests/", views.ServiceRequestListView.as_view(), name="request-list"),
    path("requests/<uuid:pk>/", views.ServiceRequestDetailView.as_view(), name="request-detail"),
    path("submit/", views.SubmitRequestView.as_view(), name="submit-request"),
    path("requests/<uuid:pk>/cancel/", views.CancelRequestView.as_view(), name="cancel-request"),
    # Staff views
    path("staff/queue/", views.StaffQueueView.as_view(), name="staff-queue"),
    path(
        "staff/requests/<uuid:pk>/",
        views.StaffRequestDetailView.as_view(),
        name="staff-request-detail",
    ),
    path(
        "staff/requests/<uuid:pk>/update-status/",
        views.StaffStatusUpdateView.as_view(),
        name="staff-update-status",
    ),
]
