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
from django.urls import reverse_lazy
from django.views.generic import FormView, ListView

from apps.volunteers.forms import ApplicationReviewForm
from apps.volunteers.models import Opportunity, VolunteerApplication
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
        context["pending_count"] = self.get_queryset().count()
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
    form_class = ApplicationReviewForm
    template_name = "volunteers/coordinator/application_review.html"
    success_url = reverse_lazy("volunteers:coordinator_dashboard")

    def _get_application(self):
        """Fetch the target application with related objects, or 404."""
        return get_object_or_404(
            VolunteerApplication.objects.select_related(
                "volunteer",
                "volunteer__user",
                "opportunity",
                "opportunity__program",
                "reviewed_by",
            ),
            pk=self.kwargs["pk"],
        )

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        # PermissionRequiredMixin.dispatch() checks self.permission_required.
        response = super().dispatch(request, *args, **kwargs)
        # Cache the application after permission checks pass.
        if hasattr(response, "status_code") and response.status_code in (302, 403):
            return response
        self.application = self._get_application()
        return response

    def get(self, request, *args, **kwargs):
        self.application = self._get_application()
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
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
        application = getattr(self, "application", None) or self._get_application()
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
            for field, errors in exc.message_dict.items():
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
        qs = VolunteerApplication.objects.select_related(
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
