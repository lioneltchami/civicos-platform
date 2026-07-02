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
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, ListView

from apps.volunteers.forms import ApplicationReviewForm, HoursRejectForm, ShiftForm
from apps.volunteers.models import HoursLog, Opportunity, Shift, ShiftBooking, VolunteerApplication
from apps.volunteers.services.applications import approve_application, reject_application

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CoordinatorDashboardView
# ---------------------------------------------------------------------------

class CoordinatorDashboardView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
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

class ApplicationReviewView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
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

class CoordinatorApplicationListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
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

class ShiftListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
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

class ShiftDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
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
        context["bookings"] = (
            self.object.bookings
            .select_related("volunteer__user")
            .order_by("status", "waitlist_position", "created_at")
        )
        context["confirmed_count"] = context["bookings"].filter(
            status=ShiftBooking.STATUS_CONFIRMED
        ).count()
        return context


# ---------------------------------------------------------------------------
# ShiftCreateView
# ---------------------------------------------------------------------------

class ShiftCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
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

class ShiftCancelView(LoginRequiredMixin, PermissionRequiredMixin, View):
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

class BookingNoShowView(LoginRequiredMixin, PermissionRequiredMixin, View):
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

class BookingCompleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
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

class HoursApprovalListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
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

class HoursApproveView(LoginRequiredMixin, PermissionRequiredMixin, View):
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

class HoursRejectView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
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
