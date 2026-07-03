"""
Citizen portal views for CivicOS.

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
    """Restricts view access to staff users only.
    - Anonymous users → 302 redirect to login
    - Authenticated non-staff → 403 Forbidden
    """
    raise_exception = True

    def test_func(self) -> bool:
        return self.request.user.is_authenticated and self.request.user.is_staff

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            from django.conf import settings as django_settings
            from django.shortcuts import redirect as django_redirect
            login_url = getattr(django_settings, "LOGIN_URL", "/account/login/")
            return django_redirect(f"{login_url}?next={self.request.get_full_path()}")
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied


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

        # ── Volunteer summary widget ──────────────────────────────────────────────
        # Imports are lazy (inside try) to prevent circular imports with apps.volunteers.
        # C-2: bare except logs at exception level so DB/import failures are observable.
        try:
            from apps.volunteers.models import VolunteerProfile, VolunteerApplication, ShiftBooking
            from django.db.models import Count as _Count  # noqa: PLC0415
            from django.utils import timezone as _tz_vol
            try:
                _profile = VolunteerProfile.objects.get(user=self.request.user)
                # C-1: FK field on VolunteerApplication is `volunteer`, not `applicant`.
                # C-5: STATUS_IN_REVIEW = "in_review", not "under_review".
                _active_apps = VolunteerApplication.objects.filter(
                    volunteer=_profile,
                    status__in=["pending", "in_review", "approved"],
                ).count()
                _upcoming_shifts = ShiftBooking.objects.filter(
                    volunteer=_profile,
                    status=ShiftBooking.STATUS_CONFIRMED,
                    shift__start_datetime__gte=_tz_vol.now(),
                ).count()
                ctx["volunteer_summary"] = {
                    "has_profile": True,
                    "active_application_count": _active_apps,
                    "upcoming_shift_count": _upcoming_shifts,
                }
            except VolunteerProfile.DoesNotExist:
                ctx["volunteer_summary"] = {"has_profile": False}
        except Exception:
            logger.exception(
                "portal.views.DashboardView: volunteer_summary failed for user_pk=%s",
                self.request.user.pk,
            )
            ctx["volunteer_summary"] = None

        # ── Donation summary widget ───────────────────────────────────────────────
        # Imports are lazy (inside try) to prevent circular imports with apps.payments.
        # C-2: bare except logs at exception level so DB/import failures are observable.
        try:
            from datetime import datetime as _dt  # noqa: PLC0415
            from decimal import Decimal as _Dec  # noqa: PLC0415
            from zoneinfo import ZoneInfo as _ZoneInfo  # noqa: PLC0415
            from django.db.models import Count as _Count, Sum as _Sum  # noqa: PLC0415
            from django.utils import timezone as _tz_don  # noqa: PLC0415
            from apps.payments.models import Donation, OfficialDonationReceipt  # noqa: PLC0415
            # H-C: Use explicit America/Toronto timezone for year-boundary arithmetic.
            # make_aware() without timezone= uses settings.TIME_ZONE which may be UTC
            # on the production server. A donation at 22:00 ET on Dec 31 (03:00 UTC
            # Jan 1) would be counted in the wrong CRA calendar year without this.
            _et = _ZoneInfo("America/Toronto")
            _current_year = _tz_don.localtime(_tz_don.now(), timezone=_et).year
            # M-4: created_at__year forces EXTRACT(year FROM ...) which prevents
            # the DB from using the created_at index. Use an explicit date range
            # (gte / lt) so the query planner can use an index range scan.
            _jan_1 = _dt(_current_year, 1, 1, tzinfo=_et)
            _jan_1_next = _dt(_current_year + 1, 1, 1, tzinfo=_et)
            _ytd = Donation.objects.filter(
                donor=self.request.user,
                status="completed",
                created_at__gte=_jan_1,
                created_at__lt=_jan_1_next,
            ).aggregate(total=_Sum("amount"), count=_Count("id"))
            _last_receipt = (
                OfficialDonationReceipt.objects
                .filter(donation__donor=self.request.user, status="issued")
                .order_by("-issued_at")
                .values("serial_number", "issued_at", "eligible_amount")
                .first()
            )
            ctx["donation_summary"] = {
                "ytd_total": _ytd["total"] or _Dec("0.00"),
                "ytd_count": _ytd["count"] or 0,
                "year": _current_year,
                "last_receipt": _last_receipt,
            }
        except Exception:
            logger.exception(
                "portal.views.DashboardView: donation_summary failed for user_pk=%s",
                self.request.user.pk,
            )
            ctx["donation_summary"] = None

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
        ctx["current_status"] = self.request.GET.get("status", "")
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

    def get_context_data(self, **kwargs) -> dict:
        ctx = super().get_context_data(**kwargs)
        # Used by template to display page heading and pre-fill read-only field
        ctx["service_name"] = self.request.GET.get("service", "")
        ctx["service_readonly"] = bool(ctx["service_name"])
        return ctx

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
        ctx["current_status"] = self.request.GET.get("status", ServiceRequestStatus.SUBMITTED)
        ctx["current_status_display"] = dict(ServiceRequestStatus.choices).get(ctx["current_status"], "")
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
