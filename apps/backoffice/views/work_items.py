"""
Back-office work item queue views.

Proxies to apps.workflows.services — no direct ORM mutations.
All action views use POST-redirect-GET pattern.

URL namespace: backoffice
  wi-list    GET  /backoffice/work-items/
  wi-detail  GET  /backoffice/work-items/<uuid:pk>/
  wi-claim   POST /backoffice/work-items/<uuid:pk>/claim/
  wi-assign  POST /backoffice/work-items/<uuid:pk>/assign/
  wi-status  POST /backoffice/work-items/<uuid:pk>/status/
  wi-comment POST /backoffice/work-items/<uuid:pk>/comment/

Security notes:
- All views require is_staff via StaffRequiredMixin.
- Log messages use user.pk only — never email or any other PII.
- POST views follow POST-redirect-GET; they never render on POST.
- All mutations go through the service layer which enforces state machine rules.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DetailView, ListView

from apps.backoffice.mixins import StaffRequiredMixin
from apps.portal.models import ServiceRequest
from apps.workflows.models import (
    TERMINAL_STATUSES,
    VALID_TRANSITIONS,
    WorkItem,
    WorkItemHistory,
    WorkItemComment,
    WorkItemPriority,
    WorkItemStatus,
)
from apps.workflows.services import (
    add_comment,
    assign_work_item,
    claim_work_item,
    get_staff_queue,
    update_work_item_status,
)

logger = logging.getLogger(__name__)
User = get_user_model()


# ---------------------------------------------------------------------------
# List view
# ---------------------------------------------------------------------------


class WorkItemListView(StaffRequiredMixin, ListView):
    """
    Paginated, filterable list of work items for the current staff user.

    Delegates queryset construction to the service layer's get_staff_queue()
    so that all filtering logic lives in one place.
    """

    template_name = "backoffice/work_items/list.html"
    context_object_name = "work_items"
    paginate_by = 25

    def get_queryset(self):
        user = self.request.user
        params = self.request.GET

        status_filter = params.get("status") or None
        assigned_to_me = params.get("mine") == "1"
        unassigned_only = params.get("unassigned") == "1"

        priority_raw = params.get("priority") or None
        priority_filter = None
        if priority_raw and priority_raw.isdigit():
            priority_filter = int(priority_raw)

        qs = get_staff_queue(
            user,
            status_filter=status_filter,
            assigned_to_me=assigned_to_me,
            unassigned_only=unassigned_only,
            priority_filter=priority_filter,
        )
        return qs.select_related("assigned_to")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # Count badges
        all_open = get_staff_queue(self.request.user)
        ctx["wi_open"] = all_open.count()
        ctx["wi_mine"] = get_staff_queue(self.request.user, assigned_to_me=True).count()

        overdue_qs = all_open.filter(sla_breached_at__isnull=False)
        ctx["wi_overdue"] = overdue_qs.count()

        # Filter state for template
        ctx["status_choices"] = WorkItemStatus.choices
        ctx["priority_choices"] = WorkItemPriority.choices
        ctx["current_status"] = self.request.GET.get("status", "")
        ctx["current_priority"] = self.request.GET.get("priority", "")
        ctx["current_mine"] = self.request.GET.get("mine", "")
        ctx["current_unassigned"] = self.request.GET.get("unassigned", "")

        return ctx


# ---------------------------------------------------------------------------
# Detail view
# ---------------------------------------------------------------------------


class WorkItemDetailView(StaffRequiredMixin, DetailView):
    """
    Full detail of a single work item, including history, comments, and action forms.
    """

    template_name = "backoffice/work_items/detail.html"
    context_object_name = "work_item"
    model = WorkItem

    def get_queryset(self):
        return WorkItem.objects.select_related("assigned_to")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        obj = self.object

        ctx["history"] = (
            WorkItemHistory.objects.filter(work_item=obj)
            .select_related("actor")
            .order_by("-created_at")
        )
        ctx["comments"] = (
            WorkItemComment.objects.filter(work_item=obj)
            .select_related("author")
            .order_by("created_at")
        )
        ctx["staff_users"] = (
            User.objects.filter(is_staff=True, is_active=True).order_by("email")
        )

        valid_transitions = VALID_TRANSITIONS.get(obj.status, set())
        ctx["valid_transitions"] = valid_transitions
        ctx["status_choices"] = [
            (s, label)
            for s, label in WorkItemStatus.choices
            if s in valid_transitions
        ]
        ctx["is_terminal"] = obj.status in TERMINAL_STATUSES

        # Linked service request via GenericForeignKey
        linked_sr = None
        try:
            sr_ct = ContentType.objects.get_for_model(ServiceRequest)
            if obj.content_type_id == sr_ct.pk:
                linked_sr = ServiceRequest.objects.filter(pk=obj.object_id).first()
        except Exception:
            pass
        ctx["linked_service_request"] = linked_sr

        return ctx


# ---------------------------------------------------------------------------
# Action views (POST-redirect-GET)
# ---------------------------------------------------------------------------


class WorkItemClaimView(StaffRequiredMixin, View):
    """Claim an unassigned work item for the current staff user."""

    http_method_names = ["post"]

    def post(self, request, pk):
        work_item = get_object_or_404(WorkItem, pk=pk)
        try:
            with transaction.atomic():
                claim_work_item(work_item, request.user)
            messages.success(request, _("Work item claimed successfully."))
        except (ValueError, PermissionError) as exc:
            messages.error(request, str(exc))
        return redirect("backoffice:wi-detail", pk=pk)


class WorkItemAssignView(StaffRequiredMixin, View):
    """Assign a work item to a specified staff user."""

    http_method_names = ["post"]

    def post(self, request, pk):
        work_item = get_object_or_404(WorkItem, pk=pk)
        assignee_id = request.POST.get("assignee_id")
        if not assignee_id:
            messages.error(request, _("Please select a staff member to assign."))
            return redirect("backoffice:wi-detail", pk=pk)

        assignee = get_object_or_404(User, pk=assignee_id, is_staff=True, is_active=True)
        try:
            with transaction.atomic():
                assign_work_item(work_item, assignee, request.user)
            messages.success(
                request,
                _("Work item assigned to %(email)s.") % {"email": assignee.email},
            )
        except (ValueError, PermissionError) as exc:
            messages.error(request, str(exc))
        return redirect("backoffice:wi-detail", pk=pk)


class WorkItemStatusView(StaffRequiredMixin, View):
    """Advance the status of a work item via a validated state-machine transition."""

    http_method_names = ["post"]

    def post(self, request, pk):
        work_item = get_object_or_404(WorkItem, pk=pk)
        new_status = request.POST.get("new_status", "").strip()
        notes = request.POST.get("notes", "").strip()

        valid = VALID_TRANSITIONS.get(work_item.status, set())
        if new_status not in valid:
            messages.error(
                request,
                _("Invalid status transition from %(from)s to %(to)s.")
                % {"from": work_item.status, "to": new_status},
            )
            return redirect("backoffice:wi-detail", pk=pk)

        try:
            with transaction.atomic():
                update_work_item_status(work_item, new_status, request.user, notes=notes)
            messages.success(
                request,
                _("Status updated to %(status)s.") % {"status": new_status},
            )
        except (ValueError, PermissionError) as exc:
            messages.error(request, str(exc))
        return redirect("backoffice:wi-detail", pk=pk)


class WorkItemCommentView(StaffRequiredMixin, View):
    """Add an internal staff comment to a work item."""

    http_method_names = ["post"]

    def post(self, request, pk):
        work_item = get_object_or_404(WorkItem, pk=pk)
        body = request.POST.get("body", "").strip()

        if not body:
            messages.error(request, _("Comment body cannot be empty."))
            return redirect("backoffice:wi-detail", pk=pk)

        if len(body) > 5000:
            messages.error(
                request,
                _("Comment is too long (maximum 5,000 characters)."),
            )
            return redirect("backoffice:wi-detail", pk=pk)

        try:
            with transaction.atomic():
                add_comment(work_item, request.user, body)
            messages.success(request, _("Comment added."))
        except (ValueError, PermissionError) as exc:
            messages.error(request, str(exc))
        return redirect("backoffice:wi-detail", pk=pk)
