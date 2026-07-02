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
    BookingCompleteView,
    BookingNoShowView,
    CoordinatorApplicationListView,
    CoordinatorDashboardView,
    HoursApprovalListView,
    HoursApproveView,
    HoursRejectView,
    ShiftCancelView,
    ShiftCreateView,
    ShiftDetailView,
    ShiftListView,
)
from apps.volunteers.views.portal import (
    ApplicationFormView,
    CancelBookingView,
    LogHoursView,
    MyApplicationsView,
    MyHoursView,
    MyShiftsView,
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

    # ------------------------------------------------------------------
    # Coordinator — shift management
    # ------------------------------------------------------------------

    # List all shifts across this coordinator's opportunities.
    path(
        "coordinator/shifts/",
        ShiftListView.as_view(),
        name="shift_list",
    ),

    # Detail page for a single shift with booking roster.
    path(
        "coordinator/shifts/<int:pk>/",
        ShiftDetailView.as_view(),
        name="shift_detail",
    ),

    # Create a new shift under a specific opportunity.
    path(
        "coordinator/opportunities/<int:opportunity_pk>/shifts/new/",
        ShiftCreateView.as_view(),
        name="shift_create",
    ),

    # POST-only: cancel an entire shift and notify all booked volunteers.
    path(
        "coordinator/shifts/<int:pk>/cancel/",
        ShiftCancelView.as_view(),
        name="shift_cancel",
    ),

    # ------------------------------------------------------------------
    # Coordinator — booking actions
    # ------------------------------------------------------------------

    # POST-only: mark a confirmed booking as no-show after shift start.
    path(
        "coordinator/bookings/<int:pk>/no-show/",
        BookingNoShowView.as_view(),
        name="booking_no_show",
    ),

    # POST-only: mark a confirmed booking as completed; auto-creates HoursLog.
    path(
        "coordinator/bookings/<int:pk>/complete/",
        BookingCompleteView.as_view(),
        name="booking_complete",
    ),

    # ------------------------------------------------------------------
    # Coordinator — hours approval
    # ------------------------------------------------------------------

    # List of pending hours logs awaiting coordinator review.
    path(
        "coordinator/hours/",
        HoursApprovalListView.as_view(),
        name="hours_approval_list",
    ),

    # POST-only: approve a pending hours log.
    path(
        "coordinator/hours/<int:pk>/approve/",
        HoursApproveView.as_view(),
        name="hours_approve",
    ),

    # POST with reason: reject a pending hours log.
    path(
        "coordinator/hours/<int:pk>/reject/",
        HoursRejectView.as_view(),
        name="hours_reject",
    ),

    # ------------------------------------------------------------------
    # Portal — volunteer shift and hours views
    # ------------------------------------------------------------------

    # Volunteer's own upcoming and past shift bookings.
    path(
        "volunteer/shifts/",
        MyShiftsView.as_view(),
        name="my_shifts",
    ),

    # Volunteer's hours log history with cumulative total and milestones.
    path(
        "volunteer/hours/",
        MyHoursView.as_view(),
        name="my_hours",
    ),

    # Volunteer manually logs hours against an approved opportunity.
    path(
        "volunteer/opportunities/<int:pk>/log-hours/",
        LogHoursView.as_view(),
        name="log_hours",
    ),

    # POST-only: volunteer cancels one of their own shift bookings.
    path(
        "volunteer/bookings/<int:pk>/cancel/",
        CancelBookingView.as_view(),
        name="cancel_booking",
    ),
]
