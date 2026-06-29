"""
Citizen portal views.

All views inherit from LoginRequiredMixin to enforce authentication.
Views are intentionally thin — business logic lives in services.py.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import DetailView, ListView, TemplateView

from apps.core.signals import pii_record_accessed

from .models import ServiceRequest


class DashboardView(LoginRequiredMixin, TemplateView):
    """
    Citizen dashboard — shows a summary of active service requests
    and any notifications.
    """

    template_name = "portal/dashboard.html"

    def get_context_data(self, **kwargs) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["recent_requests"] = (
            ServiceRequest.objects.filter(citizen=self.request.user)
            .select_related("citizen")
            .order_by("-created_at")[:5]
        )
        return ctx


class ServiceRequestListView(LoginRequiredMixin, ListView):
    """Full list of the citizen's service requests, paginated."""

    template_name = "portal/request_list.html"
    context_object_name = "requests"
    paginate_by = 20

    def get_queryset(self):
        return ServiceRequest.objects.filter(citizen=self.request.user).order_by("-created_at")


class ServiceRequestDetailView(LoginRequiredMixin, DetailView):
    """
    Detail view for a single service request.
    Enforces that citizens can only view their own requests.
    Records a PII access audit event.
    """

    template_name = "portal/request_detail.html"
    context_object_name = "service_request"

    def get_queryset(self):
        # Scoped to the authenticated user — prevents IDOR
        return ServiceRequest.objects.filter(citizen=self.request.user).prefetch_related(
            "status_updates"
        )

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        # Fire PII access signal for audit logging
        pii_record_accessed.send(
            sender=ServiceRequest,
            resource_type="portal.ServiceRequest",
            resource_id=obj.pk,
            actor=self.request.user,
        )
        return obj
