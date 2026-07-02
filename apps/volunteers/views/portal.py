"""
Volunteer Management BB — Portal views (volunteer-facing).

All views in this module require only a valid login (``LoginRequiredMixin``).
No additional Django permission is needed — access is scoped to the logged-in
volunteer's own data.

Security invariants enforced in every view:
  - ``rejection_reason`` is NEVER placed into template context.
    Use ``.only()`` or explicit field exclusion to enforce this at the ORM layer.
  - Volunteers may only act on their own profile / applications.
    ``request.user.volunteer_profile`` is the only safe way to obtain the profile.
  - PII (names, email) is never written to logs — use profile.pk only.
  - No profile? Show a friendly prompt rather than a 500 or generic 403.

WCAG 2.1 AA compliance notes:
  - Every context key that drives content visibility (``has_volunteer_profile``,
    ``existing_application``, ``user_applications``) has a comment below
    explaining how templates should use it accessibly.
  - Form error rendering must use Django's standard pattern so screen readers
    get aria-describedby association.

PIPEDA compliance:
  - ``MyApplicationsView`` fetches ``VolunteerApplication`` with ``.defer()``
    excluding ``rejection_reason`` so it can never leak into context accidentally.
"""
from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, DetailView, ListView

from apps.volunteers.forms import ApplicationForm, HoursLogForm
from apps.volunteers.models import (
    HoursLog,
    Opportunity,
    RecognitionMilestone,
    ShiftBooking,
    VolunteerApplication,
    VolunteerProfile,
)
from apps.volunteers.services.applications import apply, withdraw

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _get_volunteer_profile_or_none(user) -> VolunteerProfile | None:
    """
    Return the VolunteerProfile for ``user`` or ``None`` if it does not exist.

    Uses the reverse OneToOne accessor so no extra query is issued when the
    profile is already prefetched.  Never raises — callers handle the None case.
    """
    try:
        return user.volunteer_profile
    except VolunteerProfile.DoesNotExist:
        return None


# ---------------------------------------------------------------------------
# OpportunityListView
# ---------------------------------------------------------------------------

class OpportunityListView(LoginRequiredMixin, ListView):
    """
    Public-facing list of active volunteer opportunities.

    Context variables for templates:
      ``object_list``       — QuerySet of published Opportunities (is_active alias
                              used here is status=STATUS_PUBLISHED and accepts applications).
                              WCAG: each card should carry an <h2> with the opportunity
                              title and an aria-label on the apply link.
      ``has_volunteer_profile``       — bool. When False, template should show a prominent
                              "Create your volunteer profile" call-to-action instead
                              of Apply buttons.  Use role="alert" so screen readers
                              announce this on page load.
      ``user_applications`` — dict mapping opportunity.pk → VolunteerApplication.
                              Enables templates to show "Applied" badge without
                              N+1 queries.  Present even when empty ({}).
    """

    model = Opportunity
    template_name = "volunteers/portal/opportunity_list.html"
    context_object_name = "opportunities"
    paginate_by = 20

    def get_queryset(self):
        """
        Return published opportunities accepting applications, newest first.

        select_related("program") avoids N+1 when templates render program names.

        Excludes opportunities whose closes_at has passed — a NULL closes_at
        means no deadline (always open).  Mirrors Opportunity.is_accepting_applications.
        """
        return (
            Opportunity.objects.filter(status=Opportunity.STATUS_PUBLISHED)
            .filter(Q(closes_at__isnull=True) | Q(closes_at__gte=timezone.now()))
            .select_related("program")
            .order_by("-published_at", "title_en")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = _get_volunteer_profile_or_none(self.request.user)
        context["has_volunteer_profile"] = profile is not None

        if profile is not None:
            # Build a lookup dict so templates can show per-opportunity status
            # without issuing individual queries.
            # PIPEDA: rejection_reason deliberately excluded via .only().
            applications = (
                VolunteerApplication.objects.filter(volunteer=profile)
                .only("pk", "opportunity_id", "status")
            )
            context["user_applications"] = {
                app.opportunity_id: app for app in applications
            }
        else:
            context["user_applications"] = {}

        return context


# ---------------------------------------------------------------------------
# OpportunityDetailView
# ---------------------------------------------------------------------------

class OpportunityDetailView(LoginRequiredMixin, DetailView):
    """
    Detail page for a single volunteer opportunity.

    Only published (STATUS_PUBLISHED) opportunities are accessible — draft,
    closed, and archived opportunities return 404 for volunteers.

    Context variables for templates:
      ``opportunity``           — the Opportunity instance.
      ``existing_application``  — VolunteerApplication or None.  When not None,
                                  template should show application status rather
                                  than the Apply button.
                                  WCAG: status badge must carry sufficient colour
                                  contrast (4.5:1 minimum) and a text label.
      ``has_volunteer_profile``           — bool. When False, template should hide the Apply
                                  button and show a profile-creation CTA.
      ``screening_required``    — Human-readable string summarising screening
                                  requirements, or empty string.  Used in an
                                  informational aside/callout.
    """

    model = Opportunity
    template_name = "volunteers/portal/opportunity_detail.html"
    context_object_name = "opportunity"

    def get_queryset(self):
        """Restrict to published, non-expired opportunities only.

        Applying closes_at filter here ensures a direct URL to an expired
        opportunity returns 404 rather than showing an Apply button that
        immediately rejects the submission.  NULL closes_at means no deadline.
        """
        return (
            Opportunity.objects.filter(status=Opportunity.STATUS_PUBLISHED)
            .filter(Q(closes_at__isnull=True) | Q(closes_at__gte=timezone.now()))
            .select_related("program")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        opportunity = self.object
        profile = _get_volunteer_profile_or_none(self.request.user)
        context["has_volunteer_profile"] = profile is not None

        # Existing application — exclude rejection_reason (PIPEDA).
        existing_application = None
        if profile is not None:
            existing_application = (
                VolunteerApplication.objects.filter(
                    volunteer=profile,
                    opportunity=opportunity,
                )
                .defer("rejection_reason", "screening_notes")
                .first()
            )
        context["existing_application"] = existing_application

        # Build a plain-text summary of screening requirements for the template.
        # Templates should render this inside a <p> or <ul> — not raw HTML from
        # the model — to avoid XSS risks.
        screening_parts = []
        if opportunity.requires_vulnerable_sector_check:
            screening_parts.append("Vulnerable Sector Check")
        if opportunity.requires_police_record_check:
            screening_parts.append("Police Record Check")
        if opportunity.requires_reference_check:
            screening_parts.append("Reference check")
        if opportunity.minimum_age:
            screening_parts.append(f"Minimum age {opportunity.minimum_age}")
        context["screening_required"] = ", ".join(screening_parts)

        return context


# ---------------------------------------------------------------------------
# ApplicationFormView
# ---------------------------------------------------------------------------

class ApplicationFormView(LoginRequiredMixin, CreateView):
    """
    Volunteer submits an application to an opportunity.

    GET:  Renders ``ApplicationForm`` pre-configured with the opportunity and
          volunteer profile.
    POST: Delegates to ``apply()`` service (never writes directly to the model).

    Security:
      - Profile is always fetched via ``request.user.volunteer_profile`` —
        a volunteer cannot apply on behalf of someone else.
      - ``opportunity`` is fetched with ``status=STATUS_PUBLISHED`` so closed
        opportunities return 404.
      - ``ValidationError`` from the service is caught and re-rendered on the
        form so the volunteer sees a helpful message.
      - ``PermissionDenied`` from the service returns HTTP 403.

    WCAG notes:
      - On ``ValidationError``, the form is re-rendered with errors.  Templates
        must scroll to / focus the first error field.  Use ``autofocus`` on the
        first error element or a ``<div role="alert">`` summary.
      - ``motivation`` textarea is required — label must be visible (not
        placeholder-only).

    Template: ``volunteers/portal/application_form.html``
    """

    form_class = ApplicationForm
    template_name = "volunteers/portal/application_form.html"

    def _get_opportunity(self):
        """
        Return the target Opportunity or 404.

        Only published opportunities are accessible — prevents applying to draft
        or closed roles by manipulating the URL.
        """
        return get_object_or_404(
            Opportunity.objects.select_related("program"),
            pk=self.kwargs["pk"],
            status=Opportunity.STATUS_PUBLISHED,
        )

    def _get_profile(self):
        """
        Return the volunteer's own profile or None.

        A missing profile is handled in dispatch() before GET/POST is called.
        """
        return _get_volunteer_profile_or_none(self.request.user)

    def dispatch(self, request, *args, **kwargs):
        """
        Guard: redirect to profile creation if user has no VolunteerProfile.

        We do this in dispatch() (before get/post) so neither branch has to
        repeat the check.
        """
        self.opportunity = self._get_opportunity()
        self.volunteer_profile = self._get_profile()

        if self.volunteer_profile is None:
            messages.info(
                request,
                "Please create your volunteer profile before applying for an opportunity.",
            )
            return redirect(reverse("volunteers:opportunity_list"))

        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # Inject opportunity and volunteer_profile for dynamic field injection
        # and cross-field validation in ApplicationForm.__init__().
        kwargs["opportunity"] = self.opportunity
        kwargs["volunteer_profile"] = self.volunteer_profile
        # Remove 'instance' — ApplicationForm is used in create mode only.
        kwargs.pop("instance", None)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # WCAG: template should render opportunity.get_title() in <h1> so the
        # page heading gives screen readers context for the form.
        context["opportunity"] = self.opportunity
        return context

    def form_valid(self, form):
        """
        Delegate application creation to the ``apply()`` service.

        Never saves the model directly — the service handles status, audit log,
        and post-commit side-effects (signals, work-item creation).
        """
        motivation = form.cleaned_data.get("motivation", "")

        try:
            application = apply(
                volunteer_profile=self.volunteer_profile,
                opportunity=self.opportunity,
                motivation=motivation,
                actor=self.request.user,
            )
        except PermissionDenied:
            # Should not occur (dispatch guards profile ownership) but be safe.
            logger.warning(
                "volunteers.portal: PermissionDenied in apply() for "
                "volunteer profile #%s, opportunity #%s.",
                self.volunteer_profile.pk,
                self.opportunity.pk,
            )
            raise  # Let Django's 403 handler take over.
        except ValidationError as exc:
            # Re-render the form with service-layer error messages.
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
                        # Map service field names back to form fields where possible.
                        if field in form.fields:
                            form.add_error(field, error)
                        else:
                            form.add_error(None, error)
            return self.form_invalid(form)

        messages.success(
            self.request,
            "Your application has been submitted successfully. "
            "You will be notified when it has been reviewed.",
        )
        return redirect(
            reverse("volunteers:my_applications")
        )

    def form_invalid(self, form):
        """Re-render the form preserving the opportunity context."""
        return self.render_to_response(self.get_context_data(form=form))


# ---------------------------------------------------------------------------
# MyApplicationsView
# ---------------------------------------------------------------------------

class MyApplicationsView(LoginRequiredMixin, ListView):
    """
    Volunteer's own application history.

    Lists all applications for the logged-in volunteer, ordered newest first.

    SECURITY / PIPEDA:
      - ``rejection_reason`` is excluded at the ORM level via ``.defer()``.
        It must NEVER appear in context, template variables, or JSON serialization.
        Even if a future developer adds it to an API serializer, the deferred field
        will trigger a fresh DB query that returns the value — so this alone is not
        sufficient; the template must also not reference it.
      - Volunteers see the display label "Not selected" (from STATUS_REJECTED choice)
        rather than any internal reason.

    Context variables for templates:
      ``applications``  — QuerySet of VolunteerApplication instances, ``rejection_reason``
                          deferred.  Each has ``.opportunity`` pre-fetched.
                          WCAG: status badges must carry text labels, not colour only.
      ``has_volunteer_profile``   — bool. When False, template shows a profile-creation CTA.
    """

    template_name = "volunteers/portal/application_status.html"
    context_object_name = "applications"

    def get_queryset(self):
        """
        Return the volunteer's own applications, rejection_reason deferred.

        Ordered newest-first so the most recent action is immediately visible.
        ``select_related("opportunity")`` avoids N+1 when templates render
        opportunity titles.
        """
        return (
            VolunteerApplication.objects.filter(volunteer__user=self.request.user)
            .select_related("opportunity", "opportunity__program")
            .defer(
                # PIPEDA: internal coordinator field — never expose to volunteer.
                "rejection_reason",
                # screening_notes is also coordinator-only.
                "screening_notes",
            )
            .order_by("-created_at")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = _get_volunteer_profile_or_none(self.request.user)
        context["has_volunteer_profile"] = profile is not None
        return context


# ---------------------------------------------------------------------------
# WithdrawApplicationView
# ---------------------------------------------------------------------------

class WithdrawApplicationView(LoginRequiredMixin, View):
    """
    Volunteer withdraws their own pending application.

    POST-only: withdrawing is a destructive state change and must not be
    triggered by a GET request (CSRF and accidental-click safety).

    Security:
      - Ownership is verified: the application's volunteer must be the logged-in
        user's profile.  Mismatched ownership → 404 (not 403), so that callers
        cannot use the response code to confirm that application #N exists under
        a different account (IDOR prevention).
      - The ``withdraw()`` service enforces business rules (only pending
        applications may be withdrawn) and raises ``ValidationError`` for
        invalid transitions.

    WCAG notes:
      - The template's "Withdraw" button must be inside a ``<form method="post">``
        with a visible label and a confirmation step (e.g. ``onclick="return confirm(...)"``
        or a modal).  This prevents accidental withdrawal by screen-reader users.
    """

    http_method_names = ["post"]  # GET → 405 Method Not Allowed

    def post(self, request, *args, **kwargs):
        # Resolve the volunteer profile first.  A missing profile means there
        # can be no owned application — return 404 immediately so callers cannot
        # use the 403/404 distinction to confirm whether application #N exists.
        profile = _get_volunteer_profile_or_none(request.user)
        if profile is None:
            raise Http404

        # Ownership is folded directly into the lookup: non-owned PKs produce
        # 404 (not 403), which prevents enumeration of application IDs.
        application = get_object_or_404(
            VolunteerApplication.objects.select_related("volunteer"),
            pk=self.kwargs["pk"],
            volunteer=profile,
        )

        try:
            withdraw(application=application, actor=request.user)
        except ValidationError as exc:
            # Business rule violation (e.g. trying to withdraw an approved application).
            messages.error(
                request,
                exc.messages[0] if exc.messages else "Unable to withdraw application.",
            )
        else:
            messages.success(
                request,
                "Your application has been withdrawn.",
            )

        return redirect(reverse_lazy("volunteers:my_applications"))


# ---------------------------------------------------------------------------
# MyShiftsView
# ---------------------------------------------------------------------------

class MyShiftsView(LoginRequiredMixin, ListView):
    """
    Volunteer's upcoming and past shift bookings, newest shift first.

    IDOR prevention: queryset scoped to the logged-in volunteer's own profile.
    No profile → empty queryset (not 404 or 403) with a CTA rendered by the
    template.

    Context variables for templates:
      ``bookings``              — QuerySet of ShiftBooking instances (all statuses).
                                  WCAG: status badges must carry text labels.
      ``has_volunteer_profile`` — bool. When False, template shows profile-creation CTA.
    """

    template_name = "volunteers/portal/my_shifts.html"
    context_object_name = "bookings"

    def get_queryset(self):
        profile = _get_volunteer_profile_or_none(self.request.user)
        if profile is None:
            return ShiftBooking.objects.none()
        return (
            ShiftBooking.objects
            .filter(volunteer=profile)
            .select_related("shift__opportunity__program")
            .order_by("-shift__start_datetime")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_volunteer_profile"] = (
            _get_volunteer_profile_or_none(self.request.user) is not None
        )
        now = timezone.now()
        # Evaluate the queryset once in Python so we can split without a second DB hit.
        all_bookings = list(context.get("object_list") or self.get_queryset())
        context["upcoming_bookings"] = [
            b for b in all_bookings if b.shift.end_datetime >= now
        ]
        context["past_bookings"] = [
            b for b in all_bookings if b.shift.end_datetime < now
        ]
        return context


# ---------------------------------------------------------------------------
# MyHoursView
# ---------------------------------------------------------------------------

class MyHoursView(LoginRequiredMixin, ListView):
    """
    Volunteer's hours log history and cumulative approved total.

    PIPEDA: ``rejection_reason`` is deferred at the ORM layer — it is a
    coordinator-internal field and must never appear in volunteer-facing context
    or templates.

    Context variables for templates:
      ``hours_logs``            — QuerySet of HoursLog (rejection_reason deferred).
                                  WCAG: status badges must carry text labels.
      ``has_volunteer_profile`` — bool.
      ``total_hours_approved``  — Decimal denormalized total (from VolunteerProfile).
      ``milestones``            — QuerySet of RecognitionMilestone ordered by threshold.
    """

    template_name = "volunteers/portal/my_hours.html"
    context_object_name = "hours_logs"

    def get_queryset(self):
        profile = _get_volunteer_profile_or_none(self.request.user)
        if profile is None:
            return HoursLog.objects.none()
        return (
            HoursLog.objects
            .filter(volunteer=profile)
            .defer("rejection_reason")  # PIPEDA: coordinator-internal
            .select_related("opportunity", "shift")
            .order_by("-date", "-created_at")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = _get_volunteer_profile_or_none(self.request.user)
        context["has_volunteer_profile"] = profile is not None
        context["total_hours_approved"] = profile.total_hours_approved if profile else 0
        context["milestones"] = (
            RecognitionMilestone.objects.filter(volunteer=profile).order_by("hours_threshold")
            if profile else []
        )
        return context


# ---------------------------------------------------------------------------
# LogHoursView
# ---------------------------------------------------------------------------

class LogHoursView(LoginRequiredMixin, CreateView):
    """
    Volunteer logs hours against an approved opportunity.

    The opportunity is resolved via the volunteer's own approved application —
    a volunteer without an approved application for the given opportunity receives
    404 (IDOR prevention).  A missing volunteer profile redirects to the
    opportunity list with an informational message.

    Context variables for templates:
      ``opportunity`` — Opportunity instance. WCAG: use as <h1> context for the form.
    """

    form_class = HoursLogForm
    template_name = "volunteers/portal/log_hours.html"

    def _get_opportunity(self):
        return get_object_or_404(
            Opportunity.objects.select_related("program").distinct(),
            pk=self.kwargs["pk"],
            applications__volunteer__user=self.request.user,
            applications__status=VolunteerApplication.STATUS_APPROVED,
        )

    def _setup_opportunity_and_profile(self, request):
        """
        Resolve and cache ``self.volunteer_profile`` and ``self.opportunity``.

        Called at the top of ``get()`` and ``post()`` — both of which are only
        reached after ``LoginRequiredMixin.dispatch()`` has verified
        authentication.  This guarantees ``request.user`` is an authenticated
        User, not ``AnonymousUser``.

        Returns ``True`` if setup succeeded and the caller may continue, or
        ``False`` if the caller should return the redirect response stored in
        ``self._profile_missing_response``.
        """
        self.volunteer_profile = _get_volunteer_profile_or_none(request.user)
        if self.volunteer_profile is None:
            messages.info(request, _("Please create your volunteer profile first."))
            self._profile_missing_response = redirect(reverse("volunteers:opportunity_list"))
            return False
        self.opportunity = self._get_opportunity()
        return True

    def get(self, request, *args, **kwargs):
        if not self._setup_opportunity_and_profile(request):
            return self._profile_missing_response
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if not self._setup_opportunity_and_profile(request):
            return self._profile_missing_response
        return super().post(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["volunteer_profile"] = self.volunteer_profile
        kwargs["opportunity"] = self.opportunity
        kwargs.pop("instance", None)
        return kwargs

    def form_valid(self, form):
        from apps.volunteers.services.hours import log_hours
        try:
            log_hours(
                volunteer_profile=self.volunteer_profile,
                opportunity=self.opportunity,
                hours=form.cleaned_data["hours"],
                date=form.cleaned_data["date"],
                description=form.cleaned_data.get("description", ""),
                shift=form.cleaned_data.get("shift"),
                actor=self.request.user,
            )
            messages.success(self.request, _("Hours submitted for review."))
        except (ValidationError, PermissionDenied) as exc:
            for msg in (exc.messages if hasattr(exc, "messages") else [str(exc)]):
                form.add_error(None, msg)
            return self.form_invalid(form)
        return redirect(reverse("volunteers:my_hours"))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["opportunity"] = self.opportunity
        return context


# ---------------------------------------------------------------------------
# CancelBookingView
# ---------------------------------------------------------------------------

class CancelBookingView(LoginRequiredMixin, View):
    """
    Volunteer cancels their own shift booking.

    POST-only: cancellation is a destructive state change — GET requests return
    405 Method Not Allowed.

    IDOR prevention: the booking queryset is scoped to the volunteer's own
    profile; a mismatched PK returns 404 so callers cannot use the response
    code to enumerate booking IDs under other accounts.

    If the cancelled booking was confirmed and the shift has a waitlist, the
    cancel_booking() service automatically promotes the next waitlisted volunteer.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        profile = _get_volunteer_profile_or_none(request.user)
        if profile is None:
            raise Http404

        booking = get_object_or_404(
            ShiftBooking.objects.select_related("volunteer"),
            pk=self.kwargs["pk"],
            volunteer=profile,
        )
        try:
            from apps.volunteers.services.scheduling import cancel_booking
            cancel_booking(booking=booking, actor=request.user)
            messages.success(request, _("Your booking has been cancelled."))
        except (ValidationError, PermissionDenied) as exc:
            messages.error(
                request,
                exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc),
            )
        return redirect(reverse("volunteers:my_shifts"))
