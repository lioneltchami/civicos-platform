"""
Citizen-facing views for the Consent & Privacy building block.

All views require LoginRequiredMixin.
Citizens can only access and modify their own data.

View inventory:
  ConsentDashboardView        GET  /consent/
  ConsentUpdateView           POST /consent/update/
  ConsentWithdrawConfirmView  GET/POST /consent/withdraw/<slug>/confirm/
  ExportRequestView           GET/POST /consent/export/request/
  ExportStatusView            GET  /consent/export/status/
  ExportDownloadView          GET  /consent/export/download/<uuid>/
"""
from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.storage import default_storage
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import FormView, TemplateView

from .forms import ConsentUpdateForm, ExportRequestForm
from .services import ConsentService

logger = logging.getLogger(__name__)


class ConsentDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "consent/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        citizen = self.request.user

        existing = {
            r.category_id: r
            for r in ConsentService.get_citizen_consents(citizen)
        }

        consent_items = []
        for category in ConsentService.get_active_categories():
            record = existing.get(category.pk)
            consent_items.append(
                {
                    "category": category,
                    "record": record,
                    "can_withdraw": (
                        not category.is_required
                        and record is not None
                        and record.status == "granted"
                    ),
                }
            )

        ctx["consent_items"] = consent_items

        from apps.consent.models import DataExportRequest

        ctx["pending_export"] = (
            DataExportRequest.objects.filter(
                citizen=citizen,
                status__in=[DataExportRequest.STATUS_PENDING, "processing"],
            )
            .order_by("-requested_at")
            .first()
        )

        return ctx


class ConsentUpdateView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        form = ConsentUpdateForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Invalid consent form submission."))
            return redirect("consent:dashboard")

        action = form.cleaned_data["action"]
        slug = form.cleaned_data["category_slug"]

        try:
            if action == ConsentUpdateForm.ACTION_GRANT:
                ConsentService.grant(request.user, slug, request=request)
                messages.success(request, _("Consent granted successfully."))
            elif action == ConsentUpdateForm.ACTION_WITHDRAW:
                ConsentService.withdraw(request.user, slug, request=request)
                messages.success(request, _("Consent withdrawn successfully."))
            else:
                messages.error(request, _("Invalid action."))
                return redirect("consent:dashboard")
        except ValueError as exc:
            messages.error(request, str(exc))

        return redirect("consent:dashboard")


class ConsentWithdrawConfirmView(LoginRequiredMixin, TemplateView):
    template_name = "consent/withdraw_confirm.html"

    def _get_category(self, category_slug: str):
        from apps.consent.models import ConsentCategory

        return get_object_or_404(ConsentCategory, slug=category_slug, is_active=True)

    def get(self, request, category_slug: str):
        category = self._get_category(category_slug)
        if category.is_required:
            return HttpResponseBadRequest(
                _("This consent is required for service delivery and cannot be withdrawn.")
            )
        return self.render_to_response({"category": category})

    def post(self, request, category_slug: str):
        category = self._get_category(category_slug)
        if category.is_required:
            return HttpResponseBadRequest(
                _("This consent is required for service delivery and cannot be withdrawn.")
            )
        try:
            ConsentService.withdraw(request.user, category_slug, request=request)
            messages.success(request, _("Consent withdrawn successfully."))
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect("consent:dashboard")


class ExportRequestView(LoginRequiredMixin, FormView):
    template_name = "consent/export_request.html"
    form_class = ExportRequestForm
    success_url = reverse_lazy("consent:export-status")

    def form_valid(self, form):
        try:
            ConsentService.request_export(self.request.user, request=self.request)
            messages.success(
                self.request,
                _(
                    "Your data export request has been submitted. "
                    "You will be notified when it is ready to download."
                ),
            )
        except ValueError as exc:
            messages.error(self.request, str(exc))
            return self.form_invalid(form)
        return super().form_valid(form)


class ExportStatusView(LoginRequiredMixin, TemplateView):
    template_name = "consent/export_status.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["exports"] = ConsentService.get_citizen_exports(self.request.user)
        return ctx


class ExportDownloadView(LoginRequiredMixin, View):
    def get(self, request, token):
        from apps.consent.models import ConsentAuditEntry, DataExportRequest

        storage_path_to_serve = None
        export_pk = None

        # Phase 1: all DB work — IDOR check, expiry, status transition, audit — inside one transaction.
        # File I/O is deliberately deferred to Phase 2 so we never hold a DB lock during network/disk I/O.
        with transaction.atomic():
            try:
                req = DataExportRequest.objects.select_for_update().get(
                    download_token=token,
                )
            except DataExportRequest.DoesNotExist:
                raise Http404

            # IDOR guard — 403 if a citizen tries to access another citizen's export
            if req.citizen_id != request.user.pk:
                return HttpResponseForbidden(
                    _("You do not have permission to download this export.")
                )

            now = timezone.now()
            if req.status == DataExportRequest.STATUS_READY and req.expires_at and req.expires_at < now:
                # Mark expired inside the lock so concurrent requests can't double-deliver
                old_path = req.storage_path
                req.status = DataExportRequest.STATUS_EXPIRED
                req.storage_path = ""
                req.save(update_fields=["status", "storage_path"])
                if old_path:
                    try:
                        default_storage.delete(old_path)
                    except Exception:
                        pass
                # Raise Http404 — the link is gone. A redirect would re-issue a 302
                # to the export-status page, which the test (and correct HTTP semantics)
                # reject: an expired token URL should be a dead end, not a soft redirect.
                raise Http404

            if req.status != DataExportRequest.STATUS_READY:
                raise Http404

            if not req.storage_path or not default_storage.exists(req.storage_path):
                logger.error(
                    "ExportDownloadView: storage_path missing or not found for export pk=%s", req.pk
                )
                messages.error(request, _("Export file not found. Please contact support."))
                return redirect("consent:export-status")

            # Mark delivered inside the transaction — concurrent requests hit STATUS_READY guard above
            req.status = DataExportRequest.STATUS_DELIVERED
            req.save(update_fields=["status"])

            masked_ip = request.META.get("REMOTE_ADDR") or ""
            ConsentAuditEntry.objects.create(
                citizen=request.user,
                actor=request.user,
                action="export_downloaded",
                export_request=req,
                actor_ip=masked_ip,
                details={"download_initiated": True},
            )

            storage_path_to_serve = req.storage_path
            export_pk = req.pk

        # Phase 2: open and stream the file OUTSIDE the transaction — no DB lock held during I/O
        try:
            file_handle = default_storage.open(storage_path_to_serve, "rb")
        except Exception:
            logger.exception(
                "ExportDownloadView: failed to open file for export pk=%s", export_pk
            )
            messages.error(request, _("Export file could not be read. Please contact support."))
            return redirect("consent:export-status")

        return FileResponse(
            file_handle,
            as_attachment=True,
            filename=f"civicos-data-export-{export_pk}.json",
        )
