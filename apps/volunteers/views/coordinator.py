"""
Volunteer Management BB — Coordinator views.

All views in this module are protected by TWO mixins (in MRO order):
  1. ``LoginRequiredMixin``      — redirect to login if unauthenticated.
  2. ``PermissionRequiredMixin`` — raise 403 if authenticated but lacks permission.

The MRO order is CRITICAL: if PermissionRequiredMixin comes first, an
unauthenticated user gets a 403 instead of a login redirect, leaking that the
URL exists.  LoginRequiredMixin MUST always be listed first in the class bases.

Permission required: ``volunteers.change_volunteerapplication``

Security invariants:
  - ``rejection_reason`` IS included in coordinator context (they wrote it and
    need to see it for audit / correction purposes).  This is the ONLY place
    in the codebase where rejection_reason may appear in a template.
  - Queryset scope: coordinators see only applications for opportunities they
    coordinate (CoordinatorDashboardView) or all applications
    (CoordinatorApplicationListView, for programme managers).
  - No PII in logs — volunteer referenced by profile.pk only.

WCAG 2.1 AA compliance notes:
  - Table headers in list templates must use <th scope="col">.
  - Status badges must carry text labels (not colour alone).
  - Review form's RadioSelect gives visible choices without interaction.
"""
from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _t, gettext_lazy as _
from django.conf import settings
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, ListView, TemplateView

from django.db.models import Prefetch

from apps.volunteers.forms import ApplicationReviewForm, HoursRejectForm, ShiftForm
from apps.volunteers.models import (
    Certification,
    Honorarium,
    HoursLog,
    Opportunity,
    RecognitionMilestone,
    ScreeningRecord,
    Shift,
    ShiftBooking,
    VolunteerApplication,
    VolunteerNote,
    VolunteerProfile,
)
from apps.volunteers.services.applications import approve_application, reject_application

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Django 5.x LoginRequiredMixin compatibility
# ---------------------------------------------------------------------------

class _RedirectUnauthenticatedMixin(LoginRequiredMixin):
    """
    Django 5.x compatibility: raise_exception=True on PermissionRequiredMixin causes
    LoginRequiredMixin to also return 403 for unauthenticated users. Override to
    always redirect unauthenticated users to login regardless of raise_exception.
    """
    def handle_no_permission(self):
        from django.contrib.auth.views import redirect_to_login
        if not self.request.user.is_authenticated:
            return redirect_to_login(
                self.request.get_full_path(),
                self.get_login_url(),
                self.get_redirect_field_name(),
            )
        return super().handle_no_permission()


# ---------------------------------------------------------------------------
# CoordinatorDashboardView
# ---------------------------------------------------------------------------

class CoordinatorDashboardView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, ListView):
    """
    Coordinator's at-a-glance dashboard: pending applications for their opportunities.

    Shows only STATUS_PENDING applications to keep the action queue focused.
    Coordinators managing many programmes should use ``CoordinatorApplicationListView``
    for a filtered full list.

    Queryset scope: opportunities where ``opportunity.program.coordinator`` is the
    current user.  This uses the ``Program`` coordinator FK rather than an
    ``Opportunity``-level coordinator field (the model does not have one).

    Context variables for templates:
      ``applications``     — QuerySet of pending VolunteerApplication instances,
                             select_related("volunteer", "opportunity").
                             WCAG: table must have <caption> or aria-label.
      ``pending_count``    — int. Used in <title> and heading for screen readers.
                             WCAG: update dynamically if using HTMX/AJAX.
    """

    # MRO: LoginRequiredMixin → PermissionRequiredMixin (login redirect before 403 check)
    permission_required = "volunteers.change_volunteerapplication"
    raise_exception = True  # authenticated users without permission get 403, not a login redirect
    template_name = "volunteers/coordinator/dashboard.html"
    context_object_name = "applications"
    paginate_by = 25

    def get_queryset(self):
        """
        Return pending applications for this coordinator's opportunities.

        Uses program__coordinator to scope to the current user's programmes.
        select_related prevents N+1 when templates render volunteer and opportunity info.

        NOTE: rejection_reason IS available here (coordinator view) but is not
        relevant for the pending queue.  Templates should only render it on the
        review detail page.
        """
        return (
            VolunteerApplication.objects.filter(
                status=VolunteerApplication.STATUS_PENDING,
                opportunity__program__coordinator=self.request.user,
            )
            .select_related(
                "volunteer",
                "volunteer__user",
                "opportunity",
                "opportunity__program",
            )
            .order_by("created_at")  # oldest first — FIFO review queue
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # paginator.count is already computed by ListView's pagination logic —
        # use it directly to avoid a redundant COUNT(*) query.
        context["pending_count"] = context["paginator"].count
        return context


# ---------------------------------------------------------------------------
# ApplicationReviewView
# ---------------------------------------------------------------------------

class ApplicationReviewView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, FormView):
    """
    Coordinator reviews (approves or rejects) a single application.

    GET:  Renders ``ApplicationReviewForm`` with the application detail.
    POST: Delegates to ``approve_application()`` or ``reject_application()``
          service depending on ``form.cleaned_data["action"]``.

    Context variables for templates:
      ``application``      — VolunteerApplication instance with all fields,
                             including ``rejection_reason`` (coordinator can see
                             and edit their prior notes).
                             THIS IS THE ONE PLACE rejection_reason may appear
                             in a template — it is strictly coordinator-only.
      ``volunteer``        — VolunteerProfile instance (via application.volunteer).
                             WCAG: use preferred_name for display; fallback to
                             "Volunteer #<pk>" when preferred_name is blank.
      ``opportunity``      — Opportunity instance (via application.opportunity).
      ``form``             — ApplicationReviewForm.

    Security:
      - ``PermissionDenied`` from the service (actor lacks permission, or
        permission was revoked between GET and POST) → 403.
      - ``ValidationError`` from the service (state transition not allowed,
        e.g. application already approved) → re-render form with error.
    """

    permission_required = "volunteers.change_volunteerapplication"
    raise_exception = True  # authenticated users without permission get 403, not a login redirect
    form_class = ApplicationReviewForm
    template_name = "volunteers/coordinator/application_review.html"
    success_url = reverse_lazy("volunteers:coordinator_dashboard")

    def _get_application(self):
        """Fetch the target application with related objects, or 404.

        Scoped to the current coordinator via opportunity__program__coordinator
        to prevent IDOR: a coordinator cannot access applications that belong
        to another coordinator's programme.
        """
        return get_object_or_404(
            VolunteerApplication.objects.select_related(
                "volunteer",
                "volunteer__user",
                "opportunity",
                "opportunity__program",
                "reviewed_by",
            ),
            pk=self.kwargs["pk"],
            opportunity__program__coordinator=self.request.user,
        )

    def dispatch(self, request, *args, **kwargs):
        # Let LoginRequiredMixin and PermissionRequiredMixin run first via
        # super().dispatch().  Both checks happen before get()/post() are called,
        # so the DB query in _get_application() only fires for authenticated,
        # permissioned users.
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        # Fetch self.application here — only reached after LoginRequiredMixin
        # and PermissionRequiredMixin have both passed.
        self.application = self._get_application()
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        # Fetch self.application here — only reached after LoginRequiredMixin
        # and PermissionRequiredMixin have both passed.
        self.application = self._get_application()
        return super().post(request, *args, **kwargs)

    def get_initial(self):
        """Pre-populate rejection_reason with any previously saved notes."""
        initial = super().get_initial()
        if hasattr(self, "application"):
            initial["rejection_reason"] = self.application.rejection_reason
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # self.application is guaranteed to exist — dispatch() set it before
        # calling get() or post(), which are the only paths into get_context_data().
        application = self.application
        context["application"] = application
        # rejection_reason IS exposed here — coordinator-only page.
        # Templates MUST NOT render this on any volunteer-facing page.
        context["volunteer"] = application.volunteer
        context["opportunity"] = application.opportunity
        return context

    def form_valid(self, form):
        """Delegate to the appropriate service based on ``action``."""
        action = form.cleaned_data["action"]
        rejection_reason = form.cleaned_data.get("rejection_reason", "").strip()

        try:
            if action == "approve":
                approve_application(
                    application=self.application,
                    actor=self.request.user,
                )
                messages.success(
                    self.request,
                    f"Application #{self.application.pk} has been approved.",
                )
            elif action == "reject":
                reject_application(
                    application=self.application,
                    rejection_reason=rejection_reason,
                    actor=self.request.user,
                )
                messages.success(
                    self.request,
                    f"Application #{self.application.pk} has been declined.",
                )
            else:
                # Should not occur — form.clean() validates action choices.
                messages.error(self.request, "Unknown action. No changes were made.")
                return self.form_invalid(form)

        except PermissionDenied:
            logger.warning(
                "volunteers.coordinator: PermissionDenied in %s() for "
                "user #%s on application #%s.",
                action,
                self.request.user.pk,
                self.application.pk,
            )
            raise  # Django's 403 handler.

        except ValidationError as exc:
            # State transition not allowed (e.g. already approved/rejected).
            # ValidationError can be raised as a plain string (no message_dict)
            # or as a dict. Handle both to avoid AttributeError on .message_dict.
            error_messages = (
                exc.message_dict
                if hasattr(exc, "message_dict")
                else {"__all__": exc.messages}
            )
            for field, errors in error_messages.items():
                for error in errors:
                    if field == "__all__":
                        form.add_error(None, error)
                    else:
                        form.add_error(None, error)  # Map all service errors to non-field
            return self.form_invalid(form)

        return redirect(self.success_url)

    def form_invalid(self, form):
        """Re-render with application context preserved."""
        return self.render_to_response(self.get_context_data(form=form))


# ---------------------------------------------------------------------------
# CoordinatorApplicationListView
# ---------------------------------------------------------------------------

class CoordinatorApplicationListView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, ListView):
    """
    Full application list for coordinator/programme-manager use.

    Filterable by ``status`` query parameter.  All statuses visible (unlike the
    dashboard which shows only pending).

    Context variables for templates:
      ``applications``     — QuerySet of VolunteerApplication (all statuses or
                             filtered by ``?status=``).
                             WCAG: table must have <caption> describing the filter.
      ``current_status``   — The active status filter value, or empty string.
                             Used to render filter buttons with aria-pressed.
      ``status_choices``   — VolunteerApplication.STATUS_CHOICES list for
                             rendering filter UI.

    Security:
      - rejection_reason IS available in the queryset (not deferred) because
        this is a coordinator-only view.
      - However, templates should only surface rejection_reason in the detail
        view (ApplicationReviewView), not in the list table, to minimise
        accidental screen-sharing exposure.
    """

    permission_required = "volunteers.change_volunteerapplication"
    raise_exception = True  # authenticated users without permission get 403, not a login redirect
    template_name = "volunteers/coordinator/application_list.html"
    context_object_name = "applications"
    paginate_by = 50

    # Valid status values for the filter query parameter.
    _VALID_STATUSES = {choice[0] for choice in VolunteerApplication.STATUS_CHOICES}

    def get_queryset(self):
        """
        Return all applications, optionally filtered by status.

        ``select_related`` prevents N+1 when templates render volunteer and
        opportunity names.  No ``.defer()`` here — coordinators have access to all
        fields in this view (rejection_reason included).
        """
        qs = VolunteerApplication.objects.filter(
            opportunity__program__coordinator=self.request.user,
        ).select_related(
            "volunteer",
            "volunteer__user",
            "opportunity",
            "opportunity__program",
            "reviewed_by",
        ).order_by("-created_at")

        status_filter = self.request.GET.get("status", "").strip()
        if status_filter and status_filter in self._VALID_STATUSES:
            qs = qs.filter(status=status_filter)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        status_filter = self.request.GET.get("status", "").strip()
        context["current_status"] = (
            status_filter if status_filter in self._VALID_STATUSES else ""
        )
        context["status_choices"] = VolunteerApplication.STATUS_CHOICES
        return context


# ---------------------------------------------------------------------------
# ShiftListView
# ---------------------------------------------------------------------------

class ShiftListView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, ListView):
    """List all shifts across the coordinator's opportunities."""

    # MRO: LoginRequiredMixin → PermissionRequiredMixin (login redirect before 403 check)
    permission_required = "volunteers.view_shift"
    raise_exception = True
    template_name = "volunteers/coordinator/shift_list.html"
    context_object_name = "shifts"
    paginate_by = 25

    def get_queryset(self):
        return (
            Shift.objects
            .filter(opportunity__program__coordinator=self.request.user)
            .select_related("opportunity__program")
            .order_by("start_datetime")
        )


# ---------------------------------------------------------------------------
# ShiftDetailView
# ---------------------------------------------------------------------------

class ShiftDetailView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, DetailView):
    """
    Detail page for a single shift with full booking roster.

    Security: queryset scoped to coordinator's own opportunities to prevent IDOR.
    Non-owned PKs produce 404 (not 403) so callers cannot enumerate shift IDs
    across coordinators.
    """

    permission_required = "volunteers.view_shift"
    raise_exception = True
    template_name = "volunteers/coordinator/shift_detail.html"
    context_object_name = "shift"

    def get_queryset(self):
        return (
            Shift.objects
            .filter(opportunity__program__coordinator=self.request.user)
            .select_related("opportunity__program")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Force to a list in one DB round-trip, then compute counts in Python
        # to avoid a separate COUNT(*) query.
        bookings = list(
            self.object.bookings
            .select_related("volunteer__user")
            .order_by("status", "waitlist_position", "created_at")
        )
        context["bookings"] = bookings
        context["confirmed_count"] = sum(
            1 for b in bookings if b.status == ShiftBooking.STATUS_CONFIRMED
        )
        return context


# ---------------------------------------------------------------------------
# ShiftCreateView
# ---------------------------------------------------------------------------

class ShiftCreateView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, CreateView):
    """
    Coordinator creates a new shift under one of their opportunities.

    ``opportunity_pk`` URL kwarg is resolved to an Opportunity scoped to the
    current coordinator in dispatch() — an invalid or foreign PK returns 404.
    """

    permission_required = "volunteers.add_shift"
    raise_exception = True
    form_class = ShiftForm
    template_name = "volunteers/coordinator/shift_form.html"

    def _get_opportunity(self):
        return get_object_or_404(
            Opportunity,
            pk=self.kwargs["opportunity_pk"],
            program__coordinator=self.request.user,
        )

    def dispatch(self, request, *args, **kwargs):
        # Let LoginRequiredMixin and PermissionRequiredMixin run first via
        # super().dispatch().  The DB query in _get_opportunity() is deferred
        # to get()/post() so it only fires for authenticated, permissioned users.
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        self.opportunity = self._get_opportunity()
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.opportunity = self._get_opportunity()
        return super().post(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["opportunity"] = self.opportunity
        kwargs.pop("instance", None)
        return kwargs

    def form_valid(self, form):
        shift = form.save(commit=False)
        shift.opportunity = self.opportunity
        shift.coordinator = self.request.user
        try:
            shift.full_clean()
        except ValidationError as e:
            form.add_error(None, e)
            return self.form_invalid(form)
        shift.save()
        messages.success(self.request, _("Shift created."))
        return redirect(reverse("volunteers:shift_detail", kwargs={"pk": shift.pk}))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["opportunity"] = self.opportunity
        return context


# ---------------------------------------------------------------------------
# ShiftCancelView
# ---------------------------------------------------------------------------

class ShiftCancelView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, View):
    """
    POST-only. Coordinator cancels an entire shift via the cancel_shift() service.

    All confirmed/waitlisted bookings are bulk-cancelled and a post-commit signal
    fires for volunteer notifications.  A reason is required (enforced by the
    service).
    """

    permission_required = "volunteers.change_shift"
    raise_exception = True
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        shift = get_object_or_404(
            Shift,
            pk=self.kwargs["pk"],
            opportunity__program__coordinator=request.user,
        )
        reason = request.POST.get("reason", "").strip()
        try:
            from apps.volunteers.services.scheduling import cancel_shift
            cancel_shift(shift=shift, actor=request.user, reason=reason)
            messages.success(request, _("Shift cancelled. All bookings have been notified."))
        except (ValidationError, PermissionDenied) as exc:
            messages.error(
                request,
                exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc),
            )
        return redirect(reverse("volunteers:shift_detail", kwargs={"pk": self.kwargs["pk"]}))


# ---------------------------------------------------------------------------
# BookingNoShowView
# ---------------------------------------------------------------------------

class BookingNoShowView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, View):
    """
    POST-only. Coordinator marks a confirmed booking as no-show after the shift
    start time.

    IDOR prevention: the booking queryset is scoped to the coordinator's own
    opportunities; mismatched PKs return 404.
    """

    permission_required = "volunteers.change_shiftbooking"
    raise_exception = True
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        booking = get_object_or_404(
            ShiftBooking.objects.select_related("shift__opportunity__program"),
            pk=self.kwargs["pk"],
            shift__opportunity__program__coordinator=request.user,
        )
        try:
            from apps.volunteers.services.scheduling import mark_no_show
            mark_no_show(booking=booking, actor=request.user)
            messages.success(request, _("Marked as no-show."))
        except (ValidationError, PermissionDenied) as exc:
            messages.error(
                request,
                exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc),
            )
        return redirect(reverse("volunteers:shift_detail", kwargs={"pk": booking.shift_id}))


# ---------------------------------------------------------------------------
# BookingCompleteView
# ---------------------------------------------------------------------------

class BookingCompleteView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, View):
    """
    POST-only. Coordinator marks a confirmed booking as completed.

    Triggers auto-HoursLog creation (STATUS_PENDING) via the complete_booking()
    service so hours are queued for coordinator approval.

    IDOR prevention: booking queryset scoped to coordinator's own opportunities.
    """

    permission_required = "volunteers.change_shiftbooking"
    raise_exception = True
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        booking = get_object_or_404(
            ShiftBooking.objects.select_related("shift__opportunity__program"),
            pk=self.kwargs["pk"],
            shift__opportunity__program__coordinator=request.user,
        )
        try:
            from apps.volunteers.services.scheduling import complete_booking
            complete_booking(booking=booking, actor=request.user)
            messages.success(request, _("Booking marked complete. Hours log created."))
        except (ValidationError, PermissionDenied) as exc:
            messages.error(
                request,
                exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc),
            )
        return redirect(reverse("volunteers:shift_detail", kwargs={"pk": booking.shift_id}))


# ---------------------------------------------------------------------------
# HoursApprovalListView
# ---------------------------------------------------------------------------

class HoursApprovalListView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, ListView):
    """
    Pending hours logs queued for the coordinator's review.

    Only STATUS_PENDING logs are shown so the action queue stays focused.
    Coordinators can see rejection_reason here (coordinator-only view) — it is
    NOT deferred in this queryset.
    """

    permission_required = "volunteers.change_hourslog"
    raise_exception = True
    template_name = "volunteers/coordinator/hours_approval_list.html"
    context_object_name = "hours_logs"
    paginate_by = 25

    def get_queryset(self):
        return (
            HoursLog.objects
            .filter(
                opportunity__program__coordinator=self.request.user,
                status=HoursLog.STATUS_PENDING,
            )
            .select_related("volunteer__user", "opportunity", "shift")
            .order_by("date", "created_at")
        )


# ---------------------------------------------------------------------------
# HoursApproveView
# ---------------------------------------------------------------------------

class HoursApproveView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, View):
    """
    POST-only. Coordinator approves a pending HoursLog via approve_hours() service.

    Side-effects (fired post-commit): hours_approved signal, total_hours_approved
    recompute, and RecognitionMilestone creation if a threshold is crossed.

    IDOR prevention: log queryset scoped to coordinator's own opportunities.
    """

    permission_required = "volunteers.change_hourslog"
    raise_exception = True
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        log = get_object_or_404(
            HoursLog.objects.select_related("opportunity__program"),
            pk=self.kwargs["pk"],
            opportunity__program__coordinator=request.user,
        )
        try:
            from apps.volunteers.services.hours import approve_hours
            approve_hours(hours_log=log, actor=request.user)
            messages.success(request, _("Hours approved."))
        except (ValidationError, PermissionDenied) as exc:
            messages.error(
                request,
                exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc),
            )
        return redirect(reverse("volunteers:hours_approval_list"))


# ---------------------------------------------------------------------------
# HoursRejectView
# ---------------------------------------------------------------------------

class HoursRejectView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, FormView):
    """
    POST with reason. Coordinator rejects a pending HoursLog via reject_hours().

    PIPEDA: rejection_reason is stored coordinator-side only.  The hours_rejected
    signal dispatched by the service deliberately omits rejection_reason from
    kwargs so volunteer-facing notifications cannot leak it.

    Re-renders the hours approval list template with an error if the form is
    invalid, keeping the coordinator in context without a full page redirect.

    IDOR prevention: log fetched in form_valid() scoped to coordinator's own
    opportunities.
    """

    permission_required = "volunteers.change_hourslog"
    raise_exception = True
    http_method_names = ["post"]  # GET → 405 Method Not Allowed
    form_class = HoursRejectForm
    # Re-render the list page so the coordinator stays in context after a
    # form error (e.g. blank reason).
    template_name = "volunteers/coordinator/hours_approval_list.html"

    def _get_log(self):
        return get_object_or_404(
            HoursLog.objects.select_related("opportunity__program"),
            pk=self.kwargs["pk"],
            opportunity__program__coordinator=self.request.user,
        )

    def form_valid(self, form):
        log = self._get_log()
        try:
            from apps.volunteers.services.hours import reject_hours
            reject_hours(
                hours_log=log,
                actor=self.request.user,
                reason=form.cleaned_data["reason"],
            )
            messages.success(self.request, _("Hours submission declined."))
        except (ValidationError, PermissionDenied) as exc:
            messages.error(
                self.request,
                exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc),
            )
        return redirect(reverse("volunteers:hours_approval_list"))

    def form_invalid(self, form):
        messages.error(self.request, _("Please provide a rejection reason."))
        return redirect(reverse("volunteers:hours_approval_list"))


# ---------------------------------------------------------------------------
# VolunteerRosterView
# ---------------------------------------------------------------------------

class VolunteerRosterView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, ListView):
    """
    Coordinator view of all volunteer profiles.
    Paginated, filterable by status and skill tag.
    """

    permission_required = "volunteers.change_volunteerapplication"
    raise_exception = True
    model = VolunteerProfile
    template_name = "volunteers/coordinator/volunteer_roster.html"
    context_object_name = "profiles"
    paginate_by = 30

    def get_queryset(self):
        qs = (
            VolunteerProfile.objects
            .filter(applications__opportunity__program__coordinator=self.request.user)
            .distinct()
            .select_related("user")
            .prefetch_related("skills")
            .order_by("-created_at")
        )
        status = self.request.GET.get("status", "").strip()
        if status in {c[0] for c in VolunteerProfile.STATUS_CHOICES}:
            qs = qs.filter(status=status)
        skill_pk = self.request.GET.get("skill", "").strip()
        if skill_pk.isdigit():
            qs = qs.filter(skills__pk=int(skill_pk))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.volunteers.models import SkillTag
        ctx["status_choices"] = VolunteerProfile.STATUS_CHOICES
        ctx["skill_tags"] = SkillTag.objects.order_by("name_en")
        ctx["current_status"] = self.request.GET.get("status", "")
        ctx["current_skill"] = self.request.GET.get("skill", "")
        return ctx


# ---------------------------------------------------------------------------
# VolunteerDetailView
# ---------------------------------------------------------------------------

class VolunteerDetailView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, DetailView):
    """
    Full coordinator profile view for a single volunteer.

    Tabs: profile info, applications, shift bookings, hours, screenings,
    certifications, honoraria, notes, milestones.

    PIPEDA: accommodation_notes, emergency_contact_*, sin_last4 only exposed
    when request.user has volunteers.view_accommodation_notes permission.
    """

    permission_required = "volunteers.change_volunteerapplication"
    raise_exception = True
    model = VolunteerProfile
    template_name = "volunteers/coordinator/volunteer_detail.html"

    def get_queryset(self):
        return (
            VolunteerProfile.objects
            .filter(applications__opportunity__program__coordinator=self.request.user)
            .distinct()
            .select_related("user", "status_changed_by")
            .prefetch_related(
                Prefetch(
                    "applications",
                    queryset=VolunteerApplication.objects.select_related(
                        "opportunity__program"
                    ).order_by("-created_at"),
                    to_attr="_prefetched_applications",
                ),
                Prefetch(
                    "bookings",
                    queryset=ShiftBooking.objects.select_related(
                        "shift__opportunity"
                    ).order_by("-created_at"),
                    to_attr="_prefetched_bookings",
                ),
                Prefetch(
                    "hours_logs",
                    queryset=HoursLog.objects.defer("rejection_reason").select_related(
                        "opportunity"
                    ).order_by("-date"),
                    to_attr="_prefetched_hours_logs",
                ),
                Prefetch(
                    "screening_records",
                    queryset=ScreeningRecord.objects.select_related(
                        "opportunity", "verified_by"
                    ).order_by("-completed_date"),
                    to_attr="_prefetched_screenings",
                ),
                Prefetch(
                    "certifications",
                    queryset=Certification.objects.select_related(
                        "verified_by"
                    ).order_by("-issued_date"),
                    to_attr="_prefetched_certifications",
                ),
                Prefetch(
                    "honoraria",
                    queryset=Honorarium.objects.select_related(
                        "created_by"
                    ).order_by("-payment_date"),
                    to_attr="_prefetched_honoraria",
                ),
                Prefetch(
                    "notes",
                    queryset=VolunteerNote.objects.select_related("author").order_by("-created_at"),
                    to_attr="_prefetched_notes",
                ),
                Prefetch(
                    "milestones",
                    queryset=RecognitionMilestone.objects.order_by("hours_threshold"),
                    to_attr="_prefetched_milestones",
                ),
            )
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        profile = self.object
        can_view_sensitive = self.request.user.has_perm("volunteers.view_accommodation_notes")

        ctx["can_view_sensitive"] = can_view_sensitive

        # Server-side PIPEDA enforcement: if the coordinator lacks permission,
        # None-out sensitive fields on the profile object in context so that
        # even a template bug cannot expose them.
        if not can_view_sensitive:
            profile.accommodation_notes = None
            profile.emergency_contact_name = None
            profile.emergency_contact_phone = None
            profile.emergency_contact_relationship = None
            profile.sin_last4 = None
        else:
            # H4: Write an audit log entry when a coordinator accesses sensitive fields.
            # Sensitive field access must be recorded for PIPEDA accountability.
            try:
                from apps.volunteers.admin import _write_volunteer_audit
                _write_volunteer_audit(
                    event_type="data.viewed",
                    request=self.request,
                    resource_id=str(profile.pk),
                    detail={
                        "volunteer_profile_pk": profile.pk,
                        "sensitive_fields_accessed": [
                            "sin_last4",
                            "emergency_contact_name",
                            "emergency_contact_phone",
                            "emergency_contact_relationship",
                            "accommodation_notes",
                        ],
                    },
                )
            except Exception:
                logger.warning(
                    "VolunteerDetailView: failed to write audit log for "
                    "sensitive field access on profile #%s by user #%s.",
                    profile.pk,
                    self.request.user.pk,
                )

        # Use prefetched data (loaded by get_queryset) to avoid N+1 DB queries.
        # Each _prefetched_* attribute is a list populated by the Prefetch objects
        # in get_queryset(); accessing them costs zero additional DB round-trips.
        # Slicing happens here on the Python list — NOT in the Prefetch queryset
        # (Django raises ValueError: Cannot filter a query once a slice has been taken
        # when a sliced queryset is used with to_attr).
        ctx["applications"] = profile._prefetched_applications[:10]
        ctx["bookings"] = profile._prefetched_bookings[:10]
        ctx["hours_logs"] = profile._prefetched_hours_logs[:20]  # rejection_reason deferred (PIPEDA)
        ctx["screenings"] = profile._prefetched_screenings
        ctx["certifications"] = profile._prefetched_certifications
        ctx["honoraria"] = profile._prefetched_honoraria[:10]
        ctx["notes"] = profile._prefetched_notes[:20]
        ctx["milestones"] = profile._prefetched_milestones

        # Forms for inline actions
        from apps.volunteers.forms import VolunteerNoteForm, VolunteerStatusForm
        ctx["note_form"] = VolunteerNoteForm()
        ctx["status_form"] = VolunteerStatusForm(initial={"status": profile.status})

        # CRA: YTD honorarium total for current year.
        # cumulative_ytd() already returns Decimal("0") on empty, but guard
        # defensively in case the service contract changes or returns None.
        from decimal import Decimal
        from apps.volunteers.services.honoraria import cumulative_ytd
        ctx["ytd_honorarium"] = cumulative_ytd(
            profile, year=timezone.localtime(timezone.now()).year
        ) or Decimal("0")

        return ctx


# ---------------------------------------------------------------------------
# VolunteerStatusChangeView
# ---------------------------------------------------------------------------

class VolunteerStatusChangeView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, View):
    """
    POST-only: coordinator changes a volunteer's status (active/inactive/suspended).
    Logs the change with status_changed_by and status_changed_at.
    """

    permission_required = "volunteers.change_volunteerprofile"
    raise_exception = True
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        # H4+H5 (TOCTOU + lock-order fix): acquire the lock FIRST inside atomic(),
        # scoped to the coordinator, BEFORE any form validation or status checks.
        # Previously: profile was fetched outside atomic(), form validated, then lock
        # acquired without coordinator scope → two concurrent POSTs could both pass
        # validation before either held the lock; lock re-fetch also dropped the
        # coordinator scope so a deleted application could make it succeed on an
        # unowned profile.
        # Fix: the entire post() is wrapped in atomic(); lock + coordinator scope are
        # combined in one query; form validation happens after the lock is held.
        profile_pk = self.kwargs["pk"]

        with transaction.atomic():
            # Lock the profile and verify coordinator scope in one atomic step.
            # select_for_update() BEFORE any business logic (CivicOS invariant).
            locked = get_object_or_404(
                VolunteerProfile.objects.select_for_update().filter(
                    applications__opportunity__program__coordinator=request.user
                ).distinct(),
                pk=profile_pk,
            )

            from apps.volunteers.forms import VolunteerStatusForm
            form = VolunteerStatusForm(request.POST)
            if not form.is_valid():
                messages.error(request, _("Invalid status value."))
                return redirect(reverse("volunteers:volunteer_detail", kwargs={"pk": locked.pk}))

            new_status = form.cleaned_data["status"]
            old_status = locked.status
            locked.status = new_status
            locked.status_changed_at = timezone.now()
            locked.status_changed_by = request.user
            locked.save(update_fields=["status", "status_changed_at", "status_changed_by", "updated_at"])

        logger.info(
            "VolunteerStatusChangeView: profile #%s status changed from %s to %s by user #%s",
            locked.pk, old_status, new_status, request.user.pk,
        )
        messages.success(
            request,
            _("Volunteer status updated to %(status)s.") % {"status": locked.get_status_display()},
        )
        return redirect(reverse("volunteers:volunteer_detail", kwargs={"pk": locked.pk}))


# ---------------------------------------------------------------------------
# AddVolunteerNoteView
# ---------------------------------------------------------------------------

class AddVolunteerNoteView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, View):
    """
    POST-only: coordinator adds an internal (append-only) note to a volunteer profile.
    Notes are NEVER shown to the volunteer.
    """

    permission_required = "volunteers.change_volunteerprofile"
    raise_exception = True
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        profile = get_object_or_404(
            VolunteerProfile.objects.filter(
                applications__opportunity__program__coordinator=request.user
            ).distinct(),
            pk=self.kwargs["pk"],
        )
        from apps.volunteers.forms import VolunteerNoteForm
        from apps.volunteers.models import VolunteerNote
        form = VolunteerNoteForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Note cannot be empty."))
            return redirect(reverse("volunteers:volunteer_detail", kwargs={"pk": profile.pk}))

        VolunteerNote.objects.create(
            volunteer=profile,
            author=request.user,
            body=form.cleaned_data["body"],
        )
        logger.info(
            "AddVolunteerNoteView: note added to volunteer profile #%s by user #%s",
            profile.pk, request.user.pk,
        )
        messages.success(request, _("Note added."))
        return redirect(reverse("volunteers:volunteer_detail", kwargs={"pk": profile.pk}))


# ---------------------------------------------------------------------------
# RecordScreeningView
# ---------------------------------------------------------------------------

class RecordScreeningView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, View):
    """
    GET: show form to record a new background check for a volunteer.
    POST: create ScreeningRecord via record_check() service.

    PIPEDA: notes field has enforced content restrictions for VSC records.
    The actual criminal record result is NEVER stored — only verified_clear flag.
    """

    permission_required = "volunteers.add_screeningrecord"
    raise_exception = True

    def _get_profile(self):
        return get_object_or_404(
            VolunteerProfile.objects.filter(
                applications__opportunity__program__coordinator=self.request.user
            ).distinct(),
            pk=self.kwargs["pk"],
        )

    def get(self, request, *args, **kwargs):
        profile = self._get_profile()
        from apps.volunteers.forms import ScreeningForm
        form = ScreeningForm(volunteer=profile)
        return render(request, "volunteers/coordinator/screening_form.html", {
            "form": form,
            "profile": profile,
        })

    def post(self, request, *args, **kwargs):
        profile = self._get_profile()
        from apps.volunteers.forms import ScreeningForm
        from apps.volunteers.services.screening import record_check
        form = ScreeningForm(request.POST, volunteer=profile)
        if not form.is_valid():
            return render(request, "volunteers/coordinator/screening_form.html", {
                "form": form,
                "profile": profile,
            })
        try:
            record_check(
                volunteer_profile=profile,
                check_type=form.cleaned_data["check_type"],
                opportunity=form.cleaned_data.get("opportunity"),
                requested_by=request.user,
                notes=form.cleaned_data.get("notes", ""),
                expiry_date=form.cleaned_data.get("expires_date"),
                completed_date=form.cleaned_data.get("completed_date"),
            )
            messages.success(request, _("Screening record created."))
            return redirect(reverse("volunteers:volunteer_detail", kwargs={"pk": profile.pk}))
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(request, "volunteers/coordinator/screening_form.html", {
                "form": form,
                "profile": profile,
            })


# ---------------------------------------------------------------------------
# CompleteScreeningView
# ---------------------------------------------------------------------------

class CompleteScreeningView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, View):
    """
    POST-only: coordinator records the outcome (verified_clear) of a screening.

    PIPEDA: For VSC records, notes are restricted to logistical content only.
    Criminal record details must NEVER be stored.
    """

    permission_required = "volunteers.change_screeningrecord"
    raise_exception = True
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        from apps.volunteers.models import ScreeningRecord
        from apps.volunteers.forms import CompleteScreeningForm
        from apps.volunteers.services.screening import complete_check
        from django.db.models import Q

        # Scope: the screening's own opportunity must belong to this coordinator's
        # programs (when opportunity is set), OR the volunteer must have an
        # application in this coordinator's programs (when opportunity is null/general).
        # This prevents coordinator A completing a screening created by coordinator B
        # for the same volunteer across programs.
        screening = get_object_or_404(
            ScreeningRecord.objects.filter(
                Q(opportunity__program__coordinator=request.user) |
                Q(opportunity__isnull=True, volunteer__applications__opportunity__program__coordinator=request.user)
            ).distinct(),
            pk=self.kwargs["pk"],
        )
        form = CompleteScreeningForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Please correct the form errors."))
            return redirect(
                reverse("volunteers:volunteer_detail", kwargs={"pk": screening.volunteer_id})
            )
        try:
            complete_check(
                screening_record=screening,
                verified_clear=form.cleaned_data["verified_clear"],
                completed_by=request.user,
                notes=form.cleaned_data.get("notes", ""),
            )
            messages.success(request, _("Screening record updated."))
        except ValidationError as exc:
            for msg in exc.messages:
                messages.error(request, msg)
        return redirect(
            reverse("volunteers:volunteer_detail", kwargs={"pk": screening.volunteer_id})
        )


# ---------------------------------------------------------------------------
# HonorariumCreateView
# ---------------------------------------------------------------------------

class HonorariumCreateView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, View):
    """
    GET: show form to create an honorarium for a volunteer.
    POST: create Honorarium via create_honorarium() service (CRA threshold enforcement).

    CRA PC-025: Service enforces cumulative $450/$500/$1000 thresholds.
    If ValidationError (hard block), form is re-rendered with error message.
    """

    permission_required = "volunteers.add_honorarium"
    raise_exception = True

    def _get_profile(self):
        return get_object_or_404(
            VolunteerProfile.objects.filter(
                applications__opportunity__program__coordinator=self.request.user
            ).distinct(),
            pk=self.kwargs["pk"],
        )

    def get(self, request, *args, **kwargs):
        profile = self._get_profile()
        from apps.volunteers.forms import HonorariumForm
        from apps.volunteers.services.honoraria import cumulative_ytd
        form = HonorariumForm()
        ytd = cumulative_ytd(profile, year=timezone.localtime(timezone.now()).year)
        return render(request, "volunteers/coordinator/honorarium_form.html", {
            "form": form,
            "profile": profile,
            "ytd_honorarium": ytd,
        })

    def post(self, request, *args, **kwargs):
        profile = self._get_profile()
        from apps.volunteers.forms import HonorariumForm
        from apps.volunteers.models import Honorarium as HonorariumModel
        from apps.volunteers.services.honoraria import create_honorarium, cumulative_ytd
        form = HonorariumForm(request.POST, instance=HonorariumModel(volunteer=profile))
        if not form.is_valid():
            ytd = cumulative_ytd(profile, year=timezone.localtime(timezone.now()).year)
            return render(request, "volunteers/coordinator/honorarium_form.html", {
                "form": form,
                "profile": profile,
                "ytd_honorarium": ytd,
            })
        try:
            create_honorarium(
                volunteer_profile=profile,
                payment_type=form.cleaned_data["payment_type"],
                amount=form.cleaned_data["amount"],
                description=form.cleaned_data["description"],
                payment_date=form.cleaned_data["payment_date"],
                created_by=request.user,
            )
            messages.success(request, _("Honorarium recorded."))
            return redirect(reverse("volunteers:volunteer_detail", kwargs={"pk": profile.pk}))
        except ValidationError as exc:
            form.add_error(None, exc)
            ytd = cumulative_ytd(profile, year=timezone.localtime(timezone.now()).year)
            return render(request, "volunteers/coordinator/honorarium_form.html", {
                "form": form,
                "profile": profile,
                "ytd_honorarium": ytd,
            })


# ---------------------------------------------------------------------------
# ImpactReportView — Wave 5 Phase A
# ---------------------------------------------------------------------------

class ImpactReportView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, TemplateView):
    """
    Monthly volunteer impact report — coordinator and admin only.

    Displays:
      - Summary cards: total volunteers, total approved hours, estimated value (CAD)
      - Hours-by-program table (PIPEDA: no volunteer names)
      - T3010 category breakdown table
      - Download links for CSV exports (volunteer hours + T3010)

    Filtering: GET params ?year=YYYY and optionally ?month=M.

    Permission: volunteers.change_volunteerapplication
    Template:   volunteers/coordinator/impact_report.html
    WCAG 2.1 AA: all tables have <caption>; filter controls have labels.
    """

    permission_required = "volunteers.change_volunteerapplication"
    raise_exception = True
    template_name = "volunteers/coordinator/impact_report.html"

    def get_context_data(self, **kwargs):
        from apps.volunteers.services.reporting import (
            hours_by_program,
            impact_value,
            t3010_volunteer_metrics,
        )
        from apps.volunteers.models import Program

        ctx = super().get_context_data(**kwargs)

        current_year = timezone.localtime(timezone.now()).year
        year_str = self.request.GET.get("year", "")
        month_str = self.request.GET.get("month", "")

        try:
            year = int(year_str) if year_str.isdigit() else current_year
            year = max(2000, min(year, current_year + 1))
        except (ValueError, AttributeError):
            year = current_year

        month = int(month_str) if month_str.isdigit() and 1 <= int(month_str) <= 12 else None

        # H6: Scope report data to the coordinator's own programs.
        # Superusers see all programs; regular coordinators see only their own.
        if self.request.user.is_superuser:
            program_ids = None  # superuser sees all
        else:
            program_ids = list(
                Program.objects.filter(coordinator=self.request.user).values_list("pk", flat=True)
            )

        ctx["year"] = year
        ctx["month"] = month
        ctx["hours_by_program"] = hours_by_program(year, month, program_ids=program_ids)
        ctx["impact"] = impact_value(year, program_ids=program_ids)
        ctx["t3010"] = t3010_volunteer_metrics(year, program_ids=program_ids)
        # Year selector: current year back 5 years
        ctx["years"] = list(range(current_year, current_year - 6, -1))

        logger.info(
            "volunteers.views.ImpactReportView: rendered for year=%d month=%s "
            "by user #%s.",
            year,
            month,
            self.request.user.pk,
        )
        return ctx


# ---------------------------------------------------------------------------
# Volunteer CSV export views — Wave 5 Phase A
# ---------------------------------------------------------------------------

class VolunteerHoursExportView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, View):
    """
    Stream approved volunteer hours as a PIPEDA-safe CSV.

    GET params: ?year=YYYY and optionally ?month=M.

    Permission: volunteers.change_volunteerapplication
    Audit: records download in ExportRecord (actor_pk, masked IP, row count).
    """

    permission_required = "volunteers.change_volunteerapplication"
    raise_exception = True
    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        from apps.reports.exports.volunteer_export import export_volunteer_hours_csv
        from apps.reports.models import ExportRecord
        from apps.volunteers.services.reporting import hours_by_program
        from apps.forms.utils import _mask_ip
        from datetime import date as _date
        import calendar as _cal

        current_year = timezone.localtime(timezone.now()).year
        year_str = request.GET.get("year", "")
        month_str = request.GET.get("month", "")

        try:
            year = int(year_str) if year_str.isdigit() else current_year
            year = max(2000, min(year, current_year + 1))
        except (ValueError, AttributeError):
            year = current_year

        month = int(month_str) if month_str.isdigit() and 1 <= int(month_str) <= 12 else None

        # Compute row count for audit record (re-uses cached service result
        # so it is one extra tiny query, not a full re-export).
        rows = hours_by_program(year, month)
        row_count = len(rows)

        # Audit record — PIPEDA: actor_pk not email; IP masked.
        raw_ip = (
            request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
            or request.META.get("REMOTE_ADDR", "")
        )
        try:
            masked_ip = _mask_ip(raw_ip)
        except Exception:
            masked_ip = None

        period_start = _date(year, month or 1, 1)
        last_month = month or 12
        period_end = _date(year, last_month, _cal.monthrange(year, last_month)[1])

        ExportRecord.objects.create(
            export_type=ExportRecord.EXPORT_TYPE_VOLUNTEER_HOURS,
            format=ExportRecord.FORMAT_CSV,
            period_start=period_start,
            period_end=period_end,
            actor_pk=request.user.pk,
            actor_ip=masked_ip,
            row_count=row_count,
        )

        logger.info(
            "volunteers.views.VolunteerHoursExportView: user #%s downloaded "
            "volunteer_hours CSV year=%d month=%s rows=%d.",
            request.user.pk, year, month, row_count,
        )

        return export_volunteer_hours_csv(year, month, rows=rows)


class VolunteerT3010ExportView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, View):
    """
    Stream T3010 Schedule 2 volunteer section as a PIPEDA-safe CSV.

    GET param: ?year=YYYY.

    Permission: volunteers.change_volunteerapplication
    Audit: records download in ExportRecord.
    """

    permission_required = "volunteers.change_volunteerapplication"
    raise_exception = True
    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        from apps.reports.exports.volunteer_export import export_t3010_volunteer_csv
        from apps.reports.models import ExportRecord
        from apps.forms.utils import _mask_ip
        from datetime import date as _date

        current_year = timezone.localtime(timezone.now()).year
        year_str = request.GET.get("year", "")

        try:
            year = int(year_str) if year_str.isdigit() else current_year
            year = max(2000, min(year, current_year + 1))
        except (ValueError, AttributeError):
            year = current_year

        raw_ip = (
            request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
            or request.META.get("REMOTE_ADDR", "")
        )
        try:
            masked_ip = _mask_ip(raw_ip)
        except Exception:
            masked_ip = None

        ExportRecord.objects.create(
            export_type=ExportRecord.EXPORT_TYPE_VOLUNTEER_T3010,
            format=ExportRecord.FORMAT_CSV,
            period_start=_date(year, 1, 1),
            period_end=_date(year, 12, 31),
            actor_pk=request.user.pk,
            actor_ip=masked_ip,
            row_count=0,  # T3010 export row count is fixed / small; not meaningful
        )

        logger.info(
            "volunteers.views.VolunteerT3010ExportView: user #%s downloaded "
            "volunteer_t3010 CSV year=%d.",
            request.user.pk, year,
        )

        return export_t3010_volunteer_csv(year)


# ---------------------------------------------------------------------------
# ReferenceLetterPDFView
# ---------------------------------------------------------------------------

class ReferenceLetterPDFView(_RedirectUnauthenticatedMixin, PermissionRequiredMixin, View):
    """
    Generate a volunteer reference letter PDF for a specific volunteer profile.

    GET /volunteers/volunteers/<pk>/reference-letter.pdf

    IDOR scope: coordinator can only generate letters for volunteers who have
    applied to an opportunity in one of their programs.

    Requires: volunteers.view_volunteerprofile permission.
    Renders: templates/volunteers/pdf/reference_letter.html → WeasyPrint PDF.
    """
    permission_required = "volunteers.view_volunteerprofile"
    raise_exception = True

    def get(self, request, pk):
        from django.db.models import Sum
        from django.http import HttpResponse
        from django.template.loader import render_to_string
        import uuid

        # IDOR scope: only volunteers with applications in coordinator's programs
        profile = get_object_or_404(
            VolunteerProfile.objects.filter(
                applications__opportunity__program__coordinator=request.user
            ).distinct(),
            pk=pk,
        )

        # Approved hours per opportunity for this volunteer — scoped to
        # coordinator's own programs (IDOR: prevents including hours earned
        # under other coordinators' programs in this reference letter).
        hours_qs = HoursLog.objects.filter(
            volunteer=profile,
            status=HoursLog.STATUS_APPROVED,
            opportunity__program__coordinator=request.user,
        )
        service_rows_qs = (
            hours_qs
            .values("opportunity__title_en", "date__year")
            .annotate(approved_hours=Sum("hours"))
            .order_by("date__year", "opportunity__title_en")
        )
        formatted_rows = [
            {
                "opportunity_title": row["opportunity__title_en"] or "",
                "period": str(row["date__year"]),
                "approved_hours": str(row["approved_hours"]),
            }
            for row in service_rows_qs
        ]
        total_agg = hours_qs.aggregate(t=Sum("hours"))
        total_hours = str(total_agg["t"] or 0)

        context = {
            "volunteer_display_name": profile.display_name,
            "org_name": getattr(settings, "WAGTAIL_SITE_NAME", "CivicOS"),
            "org_address": "",
            "org_email": "",
            "letter_date": date_format(timezone.localdate(), format="N j, Y"),
            "service_rows": formatted_rows,
            "total_hours": total_hours,
            "coordinator_name": request.user.get_full_name() or f"Coordinator #{request.user.pk}",
            "coordinator_title": _t("Program Coordinator"),
            "reference_id": str(uuid.uuid4())[:8].upper(),
        }

        html = render_to_string(
            "volunteers/pdf/reference_letter.html",
            context,
            request=request,
        )

        try:
            from weasyprint import HTML as WP_HTML
            pdf_bytes = WP_HTML(
                string=html,
                base_url=request.build_absolute_uri("/"),
            ).write_pdf()
        except ImportError:
            return HttpResponse(
                "WeasyPrint is not installed. Contact your system administrator.",
                status=500,
                content_type="text/plain",
            )

        fname = (
            f"volunteer_reference_{profile.pk}_{timezone.now().strftime('%Y%m%d')}.pdf"
        )
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{fname}"'
        logger.info(
            "ReferenceLetterPDFView: coordinator #%s generated letter for profile #%s",
            request.user.pk,
            profile.pk,
        )
        return response
