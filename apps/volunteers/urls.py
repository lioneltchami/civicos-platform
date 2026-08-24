"""
Volunteer Management BB — URL configuration.

Namespace: ``volunteers``

URL groups
----------
Portal (volunteer-facing)
  /volunteer/opportunities/                          — opportunity_list
  /volunteer/opportunities/<pk>/                     — opportunity_detail
  /volunteer/opportunities/<pk>/apply/               — apply
  /volunteer/opportunities/<pk>/log-hours/           — log_hours
  /volunteer/applications/                           — my_applications
  /volunteer/applications/<pk>/withdraw/             — withdraw
  /volunteer/shifts/                                 — my_shifts
  /volunteer/hours/                                  — my_hours
  /volunteer/bookings/<pk>/cancel/                   — cancel_booking

Coordinator
  /coordinator/                                      — coordinator_dashboard
  /coordinator/applications/                         — coordinator_application_list
  /coordinator/applications/<pk>/review/             — application_review
  /coordinator/shifts/                               — shift_list
  /coordinator/shifts/<pk>/                          — shift_detail
  /coordinator/opportunities/<pk>/shifts/new/        — shift_create
  /coordinator/shifts/<pk>/cancel/                   — shift_cancel
  /coordinator/bookings/<pk>/no-show/                — booking_no_show
  /coordinator/bookings/<pk>/complete/               — booking_complete
  /coordinator/hours/                                — hours_approval_list
  /coordinator/hours/<pk>/approve/                   — hours_approve
  /coordinator/hours/<pk>/reject/                    — hours_reject
  /coordinator/volunteers/                           — volunteer_roster
  /coordinator/volunteers/<pk>/                      — volunteer_detail
  /coordinator/volunteers/<pk>/status/               — volunteer_status_change
  /coordinator/volunteers/<pk>/note/                 — volunteer_add_note
  /coordinator/volunteers/<pk>/screening/new/        — record_screening
  /coordinator/screening/<pk>/complete/              — complete_screening
  /coordinator/volunteers/<pk>/honorarium/new/       — honorarium_create

Security notes:
  - All portal views require login only (``LoginRequiredMixin``).
  - All coordinator views require login + ``volunteers.change_volunteerapplication``
    permission (``LoginRequiredMixin`` + ``PermissionRequiredMixin``, in that MRO order).
  - withdraw/, cancel_booking, shift_cancel, booking_no_show, booking_complete,
    hours_approve, and hours_reject accept POST only — the views enforce
    ``http_method_names = ["post"]`` to prevent GET-triggered state changes.
"""

from django.urls import path

from apps.volunteers.views.coordinator import (
    AddVolunteerNoteView,
    ApplicationReviewView,
    BookingCompleteView,
    BookingNoShowView,
    CompleteScreeningView,
    CoordinatorApplicationListView,
    CoordinatorDashboardView,
    HonorariumCreateView,
    HoursApprovalListView,
    HoursApproveView,
    HoursRejectView,
    ImpactReportView,
    RecordScreeningView,
    ReferenceLetterPDFView,
    ShiftCancelView,
    ShiftCreateView,
    ShiftDetailView,
    ShiftListView,
    VolunteerDetailView,
    VolunteerHoursExportView,
    VolunteerRosterView,
    VolunteerStatusChangeView,
    VolunteerT3010ExportView,
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
    # ------------------------------------------------------------------
    # Coordinator — volunteer roster and profile management
    # ------------------------------------------------------------------
    # Paginated, filterable list of all volunteer profiles.
    path(
        "coordinator/volunteers/",
        VolunteerRosterView.as_view(),
        name="volunteer_roster",
    ),
    # Full coordinator profile view for a single volunteer (tabs: profile,
    # applications, bookings, hours, screenings, certifications, honoraria,
    # notes, milestones).
    path(
        "coordinator/volunteers/<int:pk>/",
        VolunteerDetailView.as_view(),
        name="volunteer_detail",
    ),
    # POST-only: change a volunteer's status (active/inactive/suspended).
    path(
        "coordinator/volunteers/<int:pk>/status/",
        VolunteerStatusChangeView.as_view(),
        name="volunteer_status_change",
    ),
    # POST-only: add an internal (append-only) note to a volunteer profile.
    path(
        "coordinator/volunteers/<int:pk>/note/",
        AddVolunteerNoteView.as_view(),
        name="volunteer_add_note",
    ),
    # GET/POST: record a new background check for a volunteer.
    path(
        "coordinator/volunteers/<int:pk>/screening/new/",
        RecordScreeningView.as_view(),
        name="record_screening",
    ),
    # POST-only: record the outcome (verified_clear) of an existing screening.
    path(
        "coordinator/screening/<int:pk>/complete/",
        CompleteScreeningView.as_view(),
        name="complete_screening",
    ),
    # GET/POST: create an honorarium or expense reimbursement for a volunteer.
    path(
        "coordinator/volunteers/<int:pk>/honorarium/new/",
        HonorariumCreateView.as_view(),
        name="honorarium_create",
    ),
    # ------------------------------------------------------------------
    # Coordinator — volunteer impact report (Wave 5 Phase A)
    # ------------------------------------------------------------------
    # GET: volunteer impact report — coordinator/admin only.
    # Supports ?year=YYYY and ?month=M query params.
    # Linked from coordinator sidebar; provides CSV download links.
    path(
        "coordinator/impact-report/",
        ImpactReportView.as_view(),
        name="impact_report",
    ),
    # GET: stream volunteer hours CSV (PIPEDA-safe, programme-level aggregates).
    # Supports ?year=YYYY and optionally ?month=M. Records ExportRecord audit log.
    path(
        "coordinator/impact-report/export/hours.csv",
        VolunteerHoursExportView.as_view(),
        name="volunteer_hours_export",
    ),
    # GET: stream T3010 Schedule 2 volunteer CSV (aggregates only).
    # Supports ?year=YYYY. Records ExportRecord audit log.
    path(
        "coordinator/impact-report/export/t3010.csv",
        VolunteerT3010ExportView.as_view(),
        name="volunteer_t3010_export",
    ),
    # GET: generate volunteer reference letter PDF (WeasyPrint).
    # IDOR-scoped: coordinator can only generate letters for their own volunteers.
    path(
        "coordinator/volunteers/<int:pk>/reference-letter.pdf",
        ReferenceLetterPDFView.as_view(),
        name="volunteer_reference_letter",
    ),
]
