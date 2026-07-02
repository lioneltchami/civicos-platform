"""
URL configuration for the Volunteer Management BB API.

Mounted at: /api/v1/volunteers/

Volunteer-facing endpoints (IsAuthenticated):
  GET    /opportunities/                        — paginated open opportunities
  GET    /opportunities/<slug>/                 — single opportunity detail
  GET    /applications/                         — volunteer's own applications
  POST   /applications/                         — submit an application
  GET    /applications/<id>/                    — single application (own only)
  DELETE /applications/<id>/                    — withdraw application
  GET    /shifts/                               — shift list (filter by ?opportunity=slug)
  POST   /shifts/<id>/book/                     — book a shift
  POST   /shifts/<id>/cancel-booking/           — cancel a shift booking
  GET    /hours/                                — volunteer's own hours log
  POST   /hours/                                — log hours
  GET    /hours/summary/                        — total approved hours
  GET    /profile/                              — retrieve own profile
  PATCH  /profile/                              — update own profile

Coordinator-facing endpoints (IsAuthenticated + IsCoordinator group):
  GET    /admin/applications/                   — all applications for coordinator's programs
  PATCH  /admin/applications/<id>/approve/      — approve an application
  PATCH  /admin/applications/<id>/reject/       — reject an application
  GET    /admin/hours/pending/                  — pending hours logs
  PATCH  /admin/hours/<id>/approve/             — approve hours
  PATCH  /admin/hours/<id>/reject/              — reject hours
  GET    /admin/reports/hours/                  — hours-by-program report
  GET    /admin/reports/impact/                 — impact value + T3010 metrics

Total: 20 endpoints.
"""
from django.urls import path

from . import views

app_name = "volunteers"

urlpatterns = [
    # ------------------------------------------------------------------
    # Volunteer-facing — Opportunities
    # ------------------------------------------------------------------
    path(
        "opportunities/",
        views.OpportunityListView.as_view(),
        name="opportunity-list",
    ),
    path(
        "opportunities/<slug:slug>/",
        views.OpportunityDetailView.as_view(),
        name="opportunity-detail",
    ),

    # ------------------------------------------------------------------
    # Volunteer-facing — Applications
    # NOTE: hours/summary/ MUST come before hours/<id>/ to avoid the
    # "summary" literal being interpreted as a pk. Same pattern here:
    # applications/ handles both GET (list) and POST (create).
    # ------------------------------------------------------------------
    path(
        "applications/",
        views.ApplicationListCreateView.as_view(),
        name="application-list-create",
    ),
    path(
        "applications/<int:pk>/",
        views.ApplicationDetailView.as_view(),
        name="application-detail",
    ),
    path(
        "applications/<int:pk>/withdraw/",
        views.WithdrawApplicationView.as_view(),
        name="application-withdraw",
    ),

    # ------------------------------------------------------------------
    # Volunteer-facing — Shifts & Bookings
    # ------------------------------------------------------------------
    path(
        "shifts/",
        views.ShiftListView.as_view(),
        name="shift-list",
    ),
    path(
        "shifts/<int:pk>/book/",
        views.BookShiftView.as_view(),
        name="shift-book",
    ),
    path(
        "shifts/<int:pk>/cancel-booking/",
        views.CancelBookingView.as_view(),
        name="shift-cancel-booking",
    ),

    # ------------------------------------------------------------------
    # Volunteer-facing — Hours
    # IMPORTANT: summary/ must appear BEFORE <int:pk>/ to prevent Django
    # from treating the literal "summary" as an integer lookup.
    # ------------------------------------------------------------------
    path(
        "hours/summary/",
        views.MyHoursSummaryView.as_view(),
        name="hours-summary",
    ),
    path(
        "hours/",
        views.HoursListCreateView.as_view(),
        name="hours-list-create",
    ),

    # ------------------------------------------------------------------
    # Volunteer-facing — Profile
    # ------------------------------------------------------------------
    path(
        "profile/",
        views.MyProfileView.as_view(),
        name="profile",
    ),

    # ------------------------------------------------------------------
    # Coordinator-facing — Applications
    # ------------------------------------------------------------------
    path(
        "admin/applications/",
        views.AllApplicationsView.as_view(),
        name="admin-application-list",
    ),
    path(
        "admin/applications/<int:pk>/approve/",
        views.ApproveApplicationView.as_view(),
        name="admin-application-approve",
    ),
    path(
        "admin/applications/<int:pk>/reject/",
        views.RejectApplicationView.as_view(),
        name="admin-application-reject",
    ),

    # ------------------------------------------------------------------
    # Coordinator-facing — Hours
    # ------------------------------------------------------------------
    path(
        "admin/hours/pending/",
        views.PendingHoursView.as_view(),
        name="admin-hours-pending",
    ),
    path(
        "admin/hours/<int:pk>/approve/",
        views.ApproveHoursView.as_view(),
        name="admin-hours-approve",
    ),
    path(
        "admin/hours/<int:pk>/reject/",
        views.RejectHoursView.as_view(),
        name="admin-hours-reject",
    ),

    # ------------------------------------------------------------------
    # Coordinator-facing — Reports
    # ------------------------------------------------------------------
    path(
        "admin/reports/hours/",
        views.HoursReportView.as_view(),
        name="admin-report-hours",
    ),
    path(
        "admin/reports/impact/",
        views.ImpactReportView.as_view(),
        name="admin-report-impact",
    ),
]
