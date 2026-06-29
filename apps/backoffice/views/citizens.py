"""
Back-office citizen management views.

Read-only views + deactivate/reactivate action.
Never exposes PII in logs — uses user.pk only.

URL namespace: backoffice
  citizen-list       GET  /backoffice/citizens/
  citizen-detail     GET  /backoffice/citizens/<int:pk>/
  citizen-deactivate POST /backoffice/citizens/<int:pk>/deactivate/

Security notes:
- All views require is_staff via StaffRequiredMixin.
- citizen-detail and citizen-deactivate filter is_staff=False to prevent
  staff accounts being managed via the citizen interface.
- Deactivate/reactivate uses QuerySet.update() — intentional for atomicity;
  no service layer function exists for this operation.
- Log messages use pk only — never email or any other PII.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DetailView, ListView

from apps.backoffice.mixins import StaffRequiredMixin
from apps.notifications.models import Notification
from apps.portal.models import ServiceRequest

logger = logging.getLogger(__name__)
User = get_user_model()


# ---------------------------------------------------------------------------
# List view
# ---------------------------------------------------------------------------


class CitizenListView(StaffRequiredMixin, ListView):
    """
    Paginated list of citizen accounts (non-staff users).

    Supports search by email and filtering by active/inactive status.
    Staff accounts are never shown here.
    """

    template_name = "backoffice/citizens/list.html"
    context_object_name = "citizens"
    paginate_by = 25

    def get_queryset(self):
        qs = (
            User.objects.filter(is_staff=False)
            .annotate(request_count=Count("service_requests"))
            .order_by("-date_joined")
        )

        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(email__icontains=q)

        active_filter = self.request.GET.get("active", "")
        if active_filter == "1":
            qs = qs.filter(is_active=True)
        elif active_filter == "0":
            qs = qs.filter(is_active=False)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["search_query"] = self.request.GET.get("q", "")
        ctx["active_filter"] = self.request.GET.get("active", "")
        ctx["total_count"] = self.get_queryset().count()
        return ctx


# ---------------------------------------------------------------------------
# Detail view
# ---------------------------------------------------------------------------


class CitizenDetailView(StaffRequiredMixin, DetailView):
    """
    Full detail page for a single citizen account.

    Filtered to is_staff=False so staff accounts cannot be viewed or managed
    here — any attempt returns 404.
    """

    template_name = "backoffice/citizens/detail.html"
    context_object_name = "citizen"

    def get_object(self, queryset=None):
        return get_object_or_404(User, pk=self.kwargs["pk"], is_staff=False)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        obj = self.object

        ctx["service_requests"] = (
            ServiceRequest.objects.filter(citizen=obj).order_by("-created_at")[:20]
        )
        ctx["notifications"] = (
            Notification.objects.filter(recipient=obj).order_by("-created_at")[:10]
        )
        ctx["request_count"] = ServiceRequest.objects.filter(citizen=obj).count()

        return ctx


# ---------------------------------------------------------------------------
# Deactivate / reactivate action (POST-redirect-GET)
# ---------------------------------------------------------------------------


class CitizenDeactivateView(StaffRequiredMixin, View):
    """
    Toggle a citizen's is_active flag.

    Uses QuerySet.update() inside a transaction for atomicity.
    Staff members cannot deactivate their own accounts via this route.
    The route is restricted to non-staff users (is_staff=False 404 guard).
    """

    http_method_names = ["post"]

    def post(self, request, pk):
        citizen = get_object_or_404(User, pk=pk, is_staff=False)

        # Prevent self-deactivation
        if citizen.pk == request.user.pk:
            messages.error(
                request,
                _("You cannot deactivate your own account."),
            )
            return redirect("backoffice:citizen-detail", pk=pk)

        new_value = not citizen.is_active
        action = "reactivated" if new_value else "deactivated"

        with transaction.atomic():
            User.objects.filter(pk=citizen.pk).update(is_active=new_value)

        # Log using pk only — no PII in log messages
        logger.warning(
            "backoffice: citizen pk=%d %s by staff pk=%d",
            citizen.pk,
            action,
            request.user.pk,
        )

        if new_value:
            messages.success(
                request,
                _("Citizen account reactivated successfully."),
            )
        else:
            messages.success(
                request,
                _("Citizen account deactivated successfully."),
            )

        return redirect("backoffice:citizen-detail", pk=pk)
