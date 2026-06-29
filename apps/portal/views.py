"""
Citizen portal views for Govstack.

All citizen-facing views enforce LoginRequiredMixin.
Business logic is delegated to apps.portal.services — views stay thin.

View inventory:
  Citizen:
    DashboardView          GET  /portal/
    ServiceRequestListView GET  /portal/requests/
    ServiceRequestDetailView GET /portal/requests/<uuid>/
    SubmitRequestView      GET/POST /portal/submit/
    CancelRequestView      GET/POST /portal/requests/<uuid>/cancel/

  Staff (is_staff required):
    StaffQueueView         GET  /portal/staff/queue/
    StaffRequestDetailView GET  /portal/staff/requests/<uuid>/
    StaffStatusUpdateView  POST /portal/staff/requests/<uuid>/update-status/
"""
from __future__ import annotations
import logging
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView, FormView

from apps.core.signals import pii_record_accessed

from .forms import RequestCancelForm, ServiceRequestSubmitForm, StatusUpdateForm
from .models import ServiceRequest, ServiceRequestStatus
from .services import (
    cancel_service_request,
    create_service_request,
    get_citizen_requests,
    update_request_status,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mixins
# ---------------------------------------------------------------------------

class StaffRequiredMixin(UserPassesTestMixin):
    """Restricts view access to staff users only. Returns 403 for authenticated non-staff."""
    raise_exception = True

    def test_func(self) -> bool:
        return self.request.user.is_authenticated and self.request.user.is_staff


class OwnRequestMixin:
    """
    Mixin for views operating on a single ServiceRequest.
    Enforces that the citizen can only access their own requests (prevents IDOR).
    """
    def get_service_request(self, pk: str) -> ServiceRequest:
        return get_object_or_404(
            ServiceRequest,
            pk=pk,
            citizen=self.request.user,
        )


# ---------------------------------------------------------------------------
# Citizen views
# ---------------------------------------------------------------------------

class DashboardView(LoginRequiredMixin, TemplateView):
    """
    Citizen dashboard — landing page after login.
    Shows active request summary and recent activity.
    """
    template_name = "portal/dashboard.html"

    def get_context_data(self, **kwargs: Any) -> dict:
        ctx = super().get_context_data(**kwargs)
        all_requests = get_citizen_requests(self.request.user)
        ctx["recent_requests"] = all_requests[:5]
        # Match template variable names: total_requests, active_requests, completed_requests
        total = all_requests.count()
        ctx["total_requests"] = total
        ctx["active_requests"] = all_requests.exclude(
            status__in=[ServiceRequestStatus.APPROVED,
                        ServiceRequestStatus.REJECTED,
                        ServiceRequestStatus.CLOSED]
        ).count()
        ctx["completed_requests"] = all_requests.filter(
            status__in=[ServiceRequestStatus.APPROVED,
                        ServiceRequestStatus.REJECTED,
                        ServiceRequestStatus.CLOSED]
        ).count()
        ctx["status_choices"] = ServiceRequestStatus.choices
        # Unread notifications — context key matches template {% if unread_notification_count %}
        try:
            from apps.notifications.models import Notification
            ctx["unread_notification_count"] = Notification.objects.filter(
                recipient=self.request.user, read_at__isnull=True
            ).count()
        except Exception:
            ctx["unread_notification_count"] = 0
        return ctx


class ServiceRequestListView(LoginRequiredMixin, ListView):
    """
    Paginated list of all citizen's service requests.
    Supports status filtering via GET param ?status=.
    """
    template_name = "portal/request_list.html"
    context_object_name = "requests"
    paginate_by = 20

    def get_queryset(self):
        status_filter = self.request.GET.get("status", "")
        return get_citizen_requests(self.request.user, status_filter or None)

    def get_context_data(self, **kwargs: Any) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["status_choices"] = ServiceRequestStatus.choices
        ctx["active_status"] = self.request.GET.get("status", "")
        return ctx


class ServiceRequestDetailView(LoginRequiredMixin, OwnRequestMixin, DetailView):
    """
    Single service request with full status timeline.
    Fires a PII access audit event.
    """
    template_name = "portal/request_detail.html"
    context_object_name = "service_request"

    def get_object(self, queryset=None) -> ServiceRequest:
        obj = self.get_service_request(self.kwargs["pk"])
        # Audit PII access
        pii_record_accessed.send(
            sender=ServiceRequest,
            resource_type="portal.ServiceRequest",
            resource_id=str(obj.pk),
            actor=self.request.user,
        )
        return obj

    def get_context_data(self, **kwargs: Any) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["status_updates"] = self.object.status_updates.order_by("created_at")
        ctx["can_cancel"] = self.object.can_be_cancelled()
        ctx["cancel_form"] = RequestCancelForm()
        return ctx


class SubmitRequestView(LoginRequiredMixin, FormView):
    """
    Generic service request submission.
    Citizens fill in service name + description when not coming from a CMS ServicePage.
    """
    template_name = "portal/submit_request.html"
    form_class = ServiceRequestSubmitForm

    def get_form_kwargs(self) -> dict:
        kwargs = super().get_form_kwargs()
        # Pre-fill service_name if coming from a CMS service page
        if self.request.method == "GET":
            initial = kwargs.get("initial", {})
            initial["service_name"] = self.request.GET.get("service", "")
            kwargs["initial"] = initial
        return kwargs

    def form_valid(self, form: ServiceRequestSubmitForm) -> HttpResponse:
        try:
            raw_page_id = self.request.GET.get("page_id")
            try:
                service_page_id = int(raw_page_id) if raw_page_id else None
            except (ValueError, TypeError):
                service_page_id = None
            service_request = create_service_request(
                citizen=self.request.user,
                service_name=form.cleaned_data["service_name"],
                submission_data=form.get_submission_data(),
                service_page_id=service_page_id,
            )
            messages.success(
                self.request,
                # Translators: %(ref)s is the request reference number, e.g. REQ-2026-001234
                _("Your request has been submitted. Reference number: %(ref)s / "
                  "Votre demande a été soumise. Numéro de référence : %(ref)s")
                % {"ref": service_request.reference_number},
            )
            return redirect("portal:request-detail", pk=service_request.pk)
        except Exception:
            logger.exception("Failed to create service request for user_id=%s", self.request.user.pk)
            messages.error(
                self.request,
                _("An error occurred while submitting your request. Please try again. / "
                  "Une erreur s'est produite lors de la soumission de votre demande. Veuillez réessayer."),
            )
            return self.form_invalid(form)


class CancelRequestView(LoginRequiredMixin, OwnRequestMixin, View):
    """
    POST-only: citizen cancels their own request.
    GET returns the confirmation form rendered on the detail page.
    """
    http_method_names = ["post"]

    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        service_request = self.get_service_request(pk)
        form = RequestCancelForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Please confirm the cancellation. / Veuillez confirmer l'annulation."))
            return redirect("portal:request-detail", pk=pk)
        try:
            cancel_service_request(
                service_request=service_request,
                citizen=request.user,
                reason=form.cleaned_data.get("reason", ""),
            )
            messages.success(
                request,
                # Translators: %(ref)s is the request reference number, e.g. REQ-2026-001234
                _("Request %(ref)s has been cancelled. / "
                  "La demande %(ref)s a été annulée.")
                % {"ref": service_request.reference_number},
            )
        except (ValueError, PermissionError) as exc:
            messages.error(request, str(exc))
        return redirect("portal:request-list")


# ---------------------------------------------------------------------------
# Staff views
# ---------------------------------------------------------------------------

class StaffQueueView(StaffRequiredMixin, ListView):
    """
    Staff case management queue — all requests across all citizens.
    Filtered by status and sorted by oldest first (work the queue).
    """
    template_name = "portal/staff/queue.html"
    context_object_name = "requests"
    paginate_by = 30

    def get_queryset(self):
        qs = ServiceRequest.objects.select_related("citizen").prefetch_related("status_updates")
        status_filter = self.request.GET.get("status", ServiceRequestStatus.SUBMITTED)
        if status_filter and status_filter in ServiceRequestStatus.values:
            qs = qs.filter(status=status_filter)
        return qs.order_by("created_at")  # Oldest first — FIFO queue

    def get_context_data(self, **kwargs: Any) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["status_choices"] = ServiceRequestStatus.choices
        ctx["active_status"] = self.request.GET.get("status", ServiceRequestStatus.SUBMITTED)
        ctx["counts"] = {
            s: ServiceRequest.objects.filter(status=s).count()
            for s, _ in ServiceRequestStatus.choices
        }
        return ctx


class StaffRequestDetailView(StaffRequiredMixin, DetailView):
    """
    Staff view of a single request — shows all data including internal notes.
    """
    template_name = "portal/staff/request_detail.html"
    context_object_name = "service_request"
    queryset = ServiceRequest.objects.select_related("citizen").prefetch_related("status_updates__changed_by")

    def get_context_data(self, **kwargs: Any) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["status_form"] = StatusUpdateForm(initial={"new_status": self.object.status})
        ctx["status_updates"] = self.object.status_updates.order_by("created_at")
        return ctx


class StaffStatusUpdateView(StaffRequiredMixin, View):
    """
    POST-only: staff updates a service request status.
    """
    http_method_names = ["post"]

    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        service_request = get_object_or_404(ServiceRequest, pk=pk)
        form = StatusUpdateForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Please correct the form errors."))
            return redirect("portal:staff-request-detail", pk=pk)
        try:
            update_request_status(
                service_request=service_request,
                new_status=form.cleaned_data["new_status"],
                changed_by=request.user,
                public_note=form.cleaned_data.get("public_note", ""),
            )
            messages.success(
                request,
                # Translators: %(status)s is the new status value, e.g. "in_review"
                _("Status updated to %(status)s. / Statut mis à jour à %(status)s.")
                % {"status": form.cleaned_data["new_status"]},
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect("portal:staff-request-detail", pk=pk)
