"""
Back-office views for Consent & Privacy management.

Read-only audit access for staff. Never exposes citizen email — uses pk only
in log messages. ConsentDataRequestDetailView allows marking an export as
delivered.

URL names (namespace: backoffice):
  data-request-list       GET  /backoffice/data-requests/
  data-request-detail     GET/POST /backoffice/data-requests/<uuid>/
  citizen-consent-history GET  /backoffice/citizens/<int:pk>/consent/
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import DetailView, ListView, TemplateView

from apps.backoffice.mixins import StaffRequiredMixin

logger = logging.getLogger(__name__)
User = get_user_model()


class ConsentDataRequestListView(StaffRequiredMixin, ListView):
    """
    Lists all DataExportRequests across all citizens.

    Supports ?status= filtering. Shows citizen PK, not email.
    """

    template_name = "backoffice/consent/data_request_list.html"
    context_object_name = "export_requests"
    paginate_by = 30

    def get_queryset(self):  # noqa: ANN201
        from apps.consent.models import DataExportRequest

        qs = DataExportRequest.objects.select_related("citizen").order_by("-requested_at")
        VALID_STATUSES = {choice[0] for choice in DataExportRequest.STATUS_CHOICES}  # noqa: N806
        status_filter = self.request.GET.get("status", "").strip()
        if status_filter:
            if status_filter in VALID_STATUSES:
                qs = qs.filter(status=status_filter)
            else:
                # Invalid status — ignore filter silently (user may have bookmarked an old URL)
                status_filter = ""
        self.status_filter = status_filter
        return qs

    def get_context_data(self, **kwargs):  # noqa: ANN003, ANN201
        from apps.consent.models import DataExportRequest

        ctx = super().get_context_data(**kwargs)
        ctx["status_choices"] = DataExportRequest.STATUS_CHOICES
        ctx["status_filter"] = getattr(self, "status_filter", "")
        return ctx


class ConsentDataRequestDetailView(StaffRequiredMixin, DetailView):
    """
    Shows a single DataExportRequest with its ConsentAuditEntry trail.

    POST marks the request as 'delivered' (for manual fulfilment workflows).
    """

    template_name = "backoffice/consent/data_request_detail.html"
    context_object_name = "export_request"

    def get_object(self, queryset=None):  # noqa: ANN001, ANN201
        from apps.consent.models import DataExportRequest

        return get_object_or_404(
            DataExportRequest.objects.select_related("citizen"),
            pk=self.kwargs["pk"],
        )

    def get_context_data(self, **kwargs):  # noqa: ANN003, ANN201
        from apps.consent.models import ConsentAuditEntry

        ctx = super().get_context_data(**kwargs)
        ctx["audit_trail"] = ConsentAuditEntry.objects.filter(export_request=self.object).order_by(
            "timestamp"
        )
        return ctx

    def post(self, request, pk):  # noqa: ANN001, ANN201
        from apps.consent.models import ConsentAuditEntry, DataExportRequest

        with transaction.atomic():
            req = get_object_or_404(DataExportRequest.objects.select_for_update(), pk=pk)
            if req.status != DataExportRequest.STATUS_READY:
                messages.error(
                    request,
                    "This request cannot be marked delivered — it is not in 'Ready' status.",
                )
                return redirect("backoffice:data-request-detail", pk=pk)

            req.status = DataExportRequest.STATUS_DELIVERED
            req.save(update_fields=["status"])

            ConsentAuditEntry.objects.create(
                citizen=req.citizen,
                actor=request.user,
                action="export_marked_delivered",
                export_request=req,
                details={"marked_by_staff_pk": request.user.pk},
            )

        logger.info(
            "backoffice: export pk=%s marked delivered by staff pk=%s",
            pk,
            request.user.pk,
        )
        messages.success(request, "Export request marked as delivered.")
        return redirect("backoffice:data-request-list")


class CitizenConsentHistoryView(StaffRequiredMixin, TemplateView):
    """
    Read-only view of a citizen's consent records and audit trail.

    Scoped to non-staff citizens only (is_staff=False guard → 404).
    """

    template_name = "backoffice/consent/citizen_consent_history.html"

    def get_context_data(self, **kwargs):  # noqa: ANN003, ANN201
        from apps.consent.models import ConsentAuditEntry, ConsentRecord

        ctx = super().get_context_data(**kwargs)
        citizen = get_object_or_404(User, pk=self.kwargs["pk"], is_staff=False)

        ctx["citizen"] = citizen
        ctx["consent_records"] = (
            ConsentRecord.objects.filter(citizen=citizen)
            .select_related("category")
            .order_by("category__sort_order")
        )
        ctx["audit_trail"] = (
            ConsentAuditEntry.objects.filter(citizen=citizen)
            .select_related("category", "export_request")
            .order_by("-timestamp")[:100]
        )
        return ctx
