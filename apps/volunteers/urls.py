"""
Volunteer Management BB — URL configuration.

Namespace: ``volunteers``

URL groups
----------
Portal (volunteer-facing)
  /volunteer/opportunities/                  — opportunity_list
  /volunteer/opportunities/<pk>/             — opportunity_detail
  /volunteer/opportunities/<pk>/apply/       — apply
  /volunteer/applications/                   — my_applications
  /volunteer/applications/<pk>/withdraw/     — withdraw

Coordinator
  /coordinator/                              — coordinator_dashboard
  /coordinator/applications/                 — coordinator_application_list
  /coordinator/applications/<pk>/review/     — application_review

Security notes:
  - All portal views require login only (``LoginRequiredMixin``).
  - All coordinator views require login + ``volunteers.change_volunteerapplication``
    permission (``LoginRequiredMixin`` + ``PermissionRequiredMixin``, in that MRO order).
  - withdraw/ accepts POST only — the view enforces ``http_method_names = ["post"]``
    to prevent GET-triggered state changes.
"""
from django.urls import path

from apps.volunteers.views.coordinator import (
    ApplicationReviewView,
    CoordinatorApplicationListView,
    CoordinatorDashboardView,
)
from apps.volunteers.views.portal import (
    ApplicationFormView,
    MyApplicationsView,
    OpportunityDetailView,
    OpportunityListView,
    WithdrawApplicationView,
)

app_name = "volunteers"

urlpatterns = [
    # ------------------------------------------------------------------
    # Portal — volunteer-facing
    # ------------------------------------------------------------------

    # List of active opportunities open for applications.
    path(
        "volunteer/opportunities/",
        OpportunityListView.as_view(),
        name="opportunity_list",
    ),

    # Detail page for a single opportunity; includes Apply button.
    path(
        "volunteer/opportunities/<int:pk>/",
        OpportunityDetailView.as_view(),
        name="opportunity_detail",
    ),

    # Application submission form.
    path(
        "volunteer/opportunities/<int:pk>/apply/",
        ApplicationFormView.as_view(),
        name="apply",
    ),

    # Volunteer's own application history / status page.
    path(
        "volunteer/applications/",
        MyApplicationsView.as_view(),
        name="my_applications",
    ),

    # POST-only: withdraw a pending application.
    path(
        "volunteer/applications/<int:pk>/withdraw/",
        WithdrawApplicationView.as_view(),
        name="withdraw",
    ),

    # ------------------------------------------------------------------
    # Coordinator — staff/coordinator-facing (requires permission)
    # ------------------------------------------------------------------

    # Dashboard: pending applications for this coordinator's opportunities.
    path(
        "coordinator/",
        CoordinatorDashboardView.as_view(),
        name="coordinator_dashboard",
    ),

    # Full filterable application list (all statuses).
    path(
        "coordinator/applications/",
        CoordinatorApplicationListView.as_view(),
        name="coordinator_application_list",
    ),

    # Approve or reject a single application.
    path(
        "coordinator/applications/<int:pk>/review/",
        ApplicationReviewView.as_view(),
        name="application_review",
    ),
]
