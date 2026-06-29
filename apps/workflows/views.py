"""
Staff-facing views for the workflows building block.

All views require is_staff. Citizens never reach this module.

URL layout (mounted at /workflows/ in config/urls.py):
  GET  /workflows/                         → WorkItemQueueView
  GET  /workflows/<uuid>/                  → WorkItemDetailView
  POST /workflows/<uuid>/claim/            → ClaimWorkItemView
  POST /workflows/<uuid>/assign/           → AssignWorkItemView
  POST /workflows/<uuid>/status/           → AdvanceStatusView
  POST /workflows/<uuid>/escalate/         → EscalateWorkItemView
  POST /workflows/<uuid>/comment/          → AddCommentView
"""

import logging
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, ListView, View

from apps.workflows.forms import AdvanceStatusForm, AssignForm, CommentForm, EscalateForm
from apps.workflows.models import WorkItem, WorkItemStatus
from apps.workflows.services import (
    add_comment,
    assign_work_item,
    claim_work_item,
    escalate_work_item,
    get_staff_queue,
    update_work_item_status,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Access control mixin
# ---------------------------------------------------------------------------

class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Restricts all workflow views to is_staff users only."""

    raise_exception = True

    def test_func(self) -> bool:
        return self.request.user.is_authenticated and self.request.user.is_staff

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            from django.conf import settings as django_settings
            from django.shortcuts import redirect as django_redirect
            login_url = getattr(django_settings, "LOGIN_URL", "/account/login/")
            return django_redirect(f"{login_url}?next={self.request.get_full_path()}")
        raise PermissionDenied


# ---------------------------------------------------------------------------
# Queue view
# ---------------------------------------------------------------------------

class WorkItemQueueView(StaffRequiredMixin, ListView):
    """
    Staff queue — shows all open WorkItems with filter controls.

    Query params:
      ?status=pending|in_progress|waiting
      ?mine=1  — only items assigned to the current user
      ?unassigned=1  — only unassigned items
      ?priority=1|2|3|4
    """

    template_name = "workflows/queue.html"
    context_object_name = "work_items"
    paginate_by = 25

    def get_queryset(self):
        params = self.request.GET
        status_filter = params.get("status")
        assigned_to_me = bool(params.get("mine"))
        unassigned_only = bool(params.get("unassigned"))
        priority_raw = params.get("priority")
        priority_filter = int(priority_raw) if priority_raw and priority_raw.isdigit() else None

        return get_staff_queue(
            self.request.user,
            status_filter=status_filter,
            assigned_to_me=assigned_to_me,
            unassigned_only=unassigned_only,
            priority_filter=priority_filter,
        )

    def get_context_data(self, **kwargs) -> dict:
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET
        ctx["current_status"] = params.get("status", "")
        ctx["mine_only"] = bool(params.get("mine"))
        ctx["unassigned_only"] = bool(params.get("unassigned"))
        ctx["current_priority"] = params.get("priority", "")
        ctx["status_choices"] = WorkItemStatus.choices
        ctx["total_open"] = get_staff_queue(self.request.user).count()
        ctx["my_items_count"] = get_staff_queue(
            self.request.user, assigned_to_me=True
        ).count()
        return ctx


# ---------------------------------------------------------------------------
# Detail view
# ---------------------------------------------------------------------------

class WorkItemDetailView(StaffRequiredMixin, DetailView):
    """Full work item with history timeline, comments, and action forms."""

    template_name = "workflows/detail.html"
    model = WorkItem
    context_object_name = "work_item"

    def get_queryset(self):
        return WorkItem.objects.select_related(
            "assigned_to", "content_type"
        ).prefetch_related("history__actor", "comments__author")

    def get_context_data(self, **kwargs) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["advance_form"] = AdvanceStatusForm()
        ctx["assign_form"] = AssignForm()
        ctx["escalate_form"] = EscalateForm()
        ctx["comment_form"] = CommentForm()
        ctx["history"] = self.object.history.order_by("created_at")
        ctx["comments"] = self.object.comments.order_by("created_at")
        return ctx


# ---------------------------------------------------------------------------
# Action views (all POST-only)
# ---------------------------------------------------------------------------

class _WorkItemActionView(StaffRequiredMixin, View):
    """Base for single-action POST views on a WorkItem."""

    http_method_names = ["post"]

    def get_work_item(self, pk: str) -> WorkItem:
        return get_object_or_404(WorkItem, pk=pk)

    def redirect_to_detail(self, pk: str) -> HttpResponse:
        return redirect("workflows:detail", pk=pk)


class ClaimWorkItemView(_WorkItemActionView):
    """Staff self-assigns and starts an unclaimed WorkItem."""

    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        work_item = self.get_work_item(pk)
        try:
            claim_work_item(work_item, request.user)
            messages.success(
                request,
                _("Work item claimed. / Tâche réclamée."),
            )
        except (ValueError, PermissionError) as exc:
            messages.error(request, str(exc))
        except Exception:
            logger.exception("Unexpected error claiming work_item_id=%s", pk)
            messages.error(request, _("An unexpected error occurred. / Une erreur inattendue s'est produite."))
        return self.redirect_to_detail(pk)


class AssignWorkItemView(_WorkItemActionView):
    """Supervisor assigns a WorkItem to a staff member."""

    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        work_item = self.get_work_item(pk)
        form = AssignForm(request.POST)
        if form.is_valid():
            try:
                assign_work_item(work_item, form.cleaned_data["assignee"], request.user)
                messages.success(
                    request,
                    _("Work item assigned. / Tâche assignée."),
                )
            except (ValueError, PermissionError) as exc:
                messages.error(request, str(exc))
            except Exception:
                logger.exception("Unexpected error assigning work_item_id=%s", pk)
                messages.error(request, _("An unexpected error occurred. / Une erreur inattendue s'est produite."))
        else:
            messages.error(request, _("Invalid assignment. Please select a staff user."))
        return self.redirect_to_detail(pk)


class AdvanceStatusView(_WorkItemActionView):
    """Staff transitions a WorkItem to a new status."""

    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        work_item = self.get_work_item(pk)
        form = AdvanceStatusForm(request.POST)
        if form.is_valid():
            try:
                update_work_item_status(
                    work_item,
                    form.cleaned_data["new_status"],
                    request.user,
                    notes=form.cleaned_data.get("notes", ""),
                )
                messages.success(
                    request,
                    _("Status updated. / Statut mis à jour."),
                )
            except (ValueError, PermissionError) as exc:
                messages.error(request, str(exc))
            except Exception:
                logger.exception("Unexpected error advancing status for work_item_id=%s", pk)
                messages.error(request, _("An unexpected error occurred. / Une erreur inattendue s'est produite."))
        else:
            messages.error(request, _("Invalid status change."))
        return self.redirect_to_detail(pk)


class EscalateWorkItemView(_WorkItemActionView):
    """Staff escalates a WorkItem to the next level."""

    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        work_item = self.get_work_item(pk)
        form = EscalateForm(request.POST)
        if form.is_valid():
            try:
                escalate_work_item(
                    work_item, request.user,
                    reason=form.cleaned_data.get("reason", ""),
                )
                messages.success(
                    request,
                    _("Work item escalated. / Tâche escaladée."),
                )
            except (ValueError, PermissionError) as exc:
                messages.error(request, str(exc))
            except Exception:
                logger.exception("Unexpected error escalating work_item_id=%s", pk)
                messages.error(request, _("An unexpected error occurred. / Une erreur inattendue s'est produite."))
        else:
            messages.error(request, _("Invalid escalation."))
        return self.redirect_to_detail(pk)


class AddCommentView(_WorkItemActionView):
    """Staff adds an internal comment to a WorkItem."""

    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        work_item = self.get_work_item(pk)
        form = CommentForm(request.POST)
        if form.is_valid():
            try:
                add_comment(work_item, request.user, form.cleaned_data["body"])
                messages.success(
                    request,
                    _("Comment added. / Commentaire ajouté."),
                )
            except (ValueError, PermissionError) as exc:
                messages.error(request, str(exc))
            except Exception:
                logger.exception("Unexpected error adding comment to work_item_id=%s", pk)
                messages.error(request, _("An unexpected error occurred. / Une erreur inattendue s'est produite."))
        else:
            messages.error(request, _("Comment cannot be empty."))
        return self.redirect_to_detail(pk)
