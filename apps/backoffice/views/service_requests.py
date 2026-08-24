"""
Back-office service request management views.

Staff can: list/filter/search all service requests, view full detail including
submission data, advance request status (via service layer), and update internal notes.

URL namespace: backoffice
  sr-list    GET  /backoffice/service-requests/
  sr-detail  GET  /backoffice/service-requests/<ref>/
  sr-status  POST /backoffice/service-requests/<ref>/status/
  sr-notes   POST /backoffice/service-requests/<ref>/notes/

Security notes:
- All views require is_staff via StaffRequiredMixin.
- Log messages use request.user.pk only — never email or any other PII.
- POST views follow the POST-redirect-GET pattern; they never render on POST.
- Status transitions go through the service layer (update_request_status) which
  enforces state-machine rules and creates immutable audit records.
- Internal notes are updated directly via QuerySet.update() — this is intentional
  because internal_notes has no audit trail, no signal, and no side effects.
  The service layer is bypassed here only for this field.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DetailView, ListView

from apps.backoffice.forms.service_requests import (
    ServiceRequestNotesForm,
    ServiceRequestStatusForm,
)
from apps.backoffice.mixins import StaffRequiredMixin
from apps.portal.models import ServiceRequest, ServiceRequestStatus, StatusUpdate
from apps.portal.services import update_request_status
from apps.workflows.models import WorkItem

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# List view
# ---------------------------------------------------------------------------


class ServiceRequestListView(StaffRequiredMixin, ListView):
    """
    Paginated, filterable list of all service requests.

    Staff can filter by status, service name, or search by reference number
    or citizen email.  The base queryset is ordered newest-first.
    """

    template_name = "backoffice/service_requests/list.html"
    context_object_name = "service_requests"
    paginate_by = 25

    def get_queryset(self):  # noqa: ANN201
        qs = ServiceRequest.objects.select_related("citizen").order_by("-created_at")

        # Filter by status — only accept valid enum values to prevent injection
        status_param = self.request.GET.get("status", "").strip()
        if status_param and status_param in ServiceRequestStatus.values:
            qs = qs.filter(status=status_param)

        # Filter by service name (case-insensitive substring)
        service_param = self.request.GET.get("service", "").strip()
        if service_param:
            qs = qs.filter(service_name__icontains=service_param)

        # Free-text search across reference number and citizen email
        q_param = self.request.GET.get("q", "").strip()
        if q_param:
            qs = qs.filter(reference_number__icontains=q_param) | qs.filter(
                citizen__email__icontains=q_param
            )
            # Re-apply ordering after OR to ensure consistent sort
            qs = qs.order_by("-created_at")

        return qs

    def get_context_data(self, **kwargs):  # noqa: ANN003, ANN201
        ctx = super().get_context_data(**kwargs)
        ctx["status_choices"] = ServiceRequestStatus.choices
        ctx["filter_status"] = self.request.GET.get("status", "")
        ctx["filter_service"] = self.request.GET.get("service", "")
        ctx["filter_q"] = self.request.GET.get("q", "")
        # Total count across all pages (not just this page)
        ctx["total_count"] = self.object_list.count()
        return ctx


# ---------------------------------------------------------------------------
# Detail view
# ---------------------------------------------------------------------------


class ServiceRequestDetailView(StaffRequiredMixin, DetailView):
    """
    Full detail view for a single service request.

    Shows submission data, status history, linked work item, and action forms
    for updating status or adding internal notes.
    """

    template_name = "backoffice/service_requests/detail.html"
    context_object_name = "service_request"
    model = ServiceRequest

    def get_object(self, queryset=None):  # noqa: ANN001, ANN201
        return get_object_or_404(
            ServiceRequest.objects.select_related("citizen"),
            reference_number=self.kwargs["reference_number"],
        )

    def get_context_data(self, **kwargs):  # noqa: ANN003, ANN201
        ctx = super().get_context_data(**kwargs)
        obj = self.object

        # Status history — newest first, with the staff user who made each change
        ctx["status_updates"] = (
            StatusUpdate.objects.filter(service_request=obj)
            .select_related("changed_by")
            .order_by("-created_at")
        )

        # Linked WorkItem via GenericForeignKey (may be None if not yet created)
        from django.contrib.contenttypes.models import ContentType

        ct = ContentType.objects.get_for_model(ServiceRequest)
        ctx["work_item"] = WorkItem.objects.filter(content_type=ct, object_id=str(obj.pk)).first()

        # Status choices excluding the current status (no-op transitions disallowed)
        ctx["status_choices"] = [
            (value, label) for value, label in ServiceRequestStatus.choices if value != obj.status
        ]

        # Pre-built forms (empty for status, pre-filled for notes)
        ctx["status_form"] = ServiceRequestStatusForm()
        ctx["notes_form"] = ServiceRequestNotesForm(initial={"internal_notes": obj.internal_notes})

        # How many total requests does this citizen have?
        ctx["citizen_requests_count"] = ServiceRequest.objects.filter(citizen=obj.citizen).count()

        return ctx


# ---------------------------------------------------------------------------
# Status update view
# ---------------------------------------------------------------------------


class ServiceRequestStatusView(StaffRequiredMixin, View):
    """
    Handle status transition for a service request.

    Accepts POST only.  Delegates to the service layer which enforces
    state-machine rules and creates the immutable StatusUpdate record.
    Wrapped in transaction.atomic() so any failure rolls back the whole unit.
    """

    http_method_names = ["post"]  # noqa: RUF012

    def post(self, request, reference_number):  # noqa: ANN001, ANN201
        sr = get_object_or_404(ServiceRequest, reference_number=reference_number)
        form = ServiceRequestStatusForm(request.POST)

        if not form.is_valid():
            for field_errors in form.errors.values():
                for error in field_errors:
                    messages.error(request, error)
            return redirect("backoffice:sr-detail", reference_number=reference_number)

        new_status = form.cleaned_data["new_status"]
        public_note = form.cleaned_data.get("public_note", "")

        try:
            with transaction.atomic():
                update_request_status(sr, new_status, request.user, public_note)
                logger.info(
                    "backoffice: SR %s status changed to %s by staff pk=%d",
                    sr.reference_number,
                    new_status,
                    request.user.pk,
                )
            # Resolve the display label for the success message
            status_label = dict(ServiceRequestStatus.choices).get(new_status, new_status)
            messages.success(
                request,
                _("Status updated to %(status)s.") % {"status": status_label},
            )
        except ValueError as exc:
            messages.error(request, str(exc))

        return redirect("backoffice:sr-detail", reference_number=reference_number)

    def http_method_not_allowed(self, request, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN201
        return HttpResponseNotAllowed(["POST"])


# ---------------------------------------------------------------------------
# Internal notes view
# ---------------------------------------------------------------------------


class ServiceRequestNotesView(StaffRequiredMixin, View):
    """
    Update internal staff notes on a service request.

    Accepts POST only.  Internal notes have no audit trail, no signal, and no
    side effects, so we bypass the service layer and update the field directly
    via QuerySet.update().  This is explicitly documented and intentional.
    """

    http_method_names = ["post"]  # noqa: RUF012

    def post(self, request, reference_number):  # noqa: ANN001, ANN201
        sr = get_object_or_404(ServiceRequest, reference_number=reference_number)
        form = ServiceRequestNotesForm(request.POST)

        if not form.is_valid():
            for field_errors in form.errors.values():
                for error in field_errors:
                    messages.error(request, error)
            return redirect("backoffice:sr-detail", reference_number=reference_number)

        # Direct update: internal_notes has no audit/signal requirements.
        # The service layer is intentionally bypassed here — see module docstring.
        with transaction.atomic():
            ServiceRequest.objects.filter(pk=sr.pk).update(
                internal_notes=form.cleaned_data["internal_notes"]
            )
        logger.info(
            "backoffice: internal notes updated on SR %s by staff pk=%d",
            sr.reference_number,
            request.user.pk,
        )
        messages.success(request, _("Internal notes saved."))
        return redirect("backoffice:sr-detail", reference_number=reference_number)

    def http_method_not_allowed(self, request, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN201
        return HttpResponseNotAllowed(["POST"])
