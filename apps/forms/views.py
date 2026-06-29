"""
Forms building block staff views.

The citizen-facing form is rendered by Wagtail's page serving mechanism
(via FormPage.serve()). These views are staff-only:
  - Submission list with filtering
  - Submission detail
  - CSV export
  - PIPEDA redaction
"""
from __future__ import annotations
import csv
import logging

from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.http import HttpRequest, HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DetailView, ListView

from .models import FormPage, FormSubmission

logger = logging.getLogger(__name__)


class StaffRequiredMixin(UserPassesTestMixin):
    """Restrict access to staff users only. Returns 403 for authenticated non-staff."""
    raise_exception = True

    def test_func(self) -> bool:
        return self.request.user.is_authenticated and self.request.user.is_staff


class SubmissionListView(StaffRequiredMixin, ListView):
    """
    Staff view: paginated list of submissions for a specific FormPage.
    Supports filtering by date range and consent status.
    """
    template_name = "forms/staff/submission_list.html"
    context_object_name = "submissions"
    paginate_by = 30

    def get_form_page(self) -> FormPage:
        return get_object_or_404(FormPage, pk=self.kwargs["page_id"])

    def get_queryset(self):
        self.form_page = self.get_form_page()
        qs = FormSubmission.objects.filter(page=self.form_page).order_by("-submit_time")

        # Filter by consent
        consent = self.request.GET.get("consent", "")
        if consent == "yes":
            qs = qs.filter(consent_given=True)
        elif consent == "no":
            qs = qs.filter(consent_given=False)

        # Filter by date
        date_from = self.request.GET.get("from", "")
        date_to = self.request.GET.get("to", "")
        if date_from:
            try:
                from datetime import date
                qs = qs.filter(submit_time__date__gte=date_from)
            except (ValueError, TypeError):
                pass
        if date_to:
            try:
                qs = qs.filter(submit_time__date__lte=date_to)
            except (ValueError, TypeError):
                pass

        return qs

    def get_context_data(self, **kwargs) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["form_page"] = self.form_page
        ctx["field_names"] = list(
            self.form_page.form_fields.values_list("label", "clean_name")
        )
        ctx["pii_fields"] = set(
            self.form_page.form_fields.filter(is_pii=True)
            .values_list("clean_name", flat=True)
        )
        ctx["total_count"] = FormSubmission.objects.filter(page=self.form_page).count()
        ctx["consent_filter"] = self.request.GET.get("consent", "")
        ctx["date_from"] = self.request.GET.get("from", "")
        ctx["date_to"] = self.request.GET.get("to", "")
        return ctx


class SubmissionDetailView(StaffRequiredMixin, DetailView):
    """
    Staff view: full detail of a single submission.
    Renders all form fields with PII fields highlighted.
    """
    template_name = "forms/staff/submission_detail.html"
    context_object_name = "submission"
    queryset = FormSubmission.objects.select_related("page")

    def get_object(self, queryset=None):
        return get_object_or_404(
            FormSubmission,
            pk=self.kwargs["submission_id"],
            page__pk=self.kwargs["page_id"],
        )

    def get_context_data(self, **kwargs) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["form_page"] = self.object.page
        ctx["fields"] = list(self.object.page.form_fields.values("label", "clean_name", "is_pii"))
        ctx["form_data"] = self.object.form_data or {}
        ctx["pii_fields"] = {
            f["clean_name"] for f in ctx["fields"] if f["is_pii"]
        }
        # Mask IP: only show first two octets to staff (full IP is PII under PIPEDA)
        raw_ip = self.object.submitter_ip or ""
        if raw_ip:
            parts = raw_ip.split(".")
            ctx["submitter_ip_masked"] = ".".join(parts[:2]) + ".x.x" if len(parts) == 4 else "masked"
        else:
            ctx["submitter_ip_masked"] = ""
        return ctx


class SubmissionExportView(StaffRequiredMixin, View):
    """
    GET: Download all submissions for a FormPage as CSV.
    Masks IP addresses — keeps first two octets only.
    Adds UTF-8 BOM for Excel compatibility.
    """
    http_method_names = ["get"]

    @staticmethod
    def _csv_safe(value: str) -> str:
        """Prevent CSV formula injection (Excel/LibreOffice Calc)."""
        s = str(value) if value is not None else ""
        if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
            s = "'" + s
        return s

    def get(self, request: HttpRequest, page_id: int) -> HttpResponse:
        form_page = get_object_or_404(FormPage, pk=page_id)
        submissions = FormSubmission.objects.filter(page=form_page).order_by("-submit_time")
        field_specs = list(form_page.form_fields.values_list("label", "clean_name"))

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        filename = f"submissions_{form_page.slug}_{timezone.now().date()}.csv"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.write("﻿")  # UTF-8 BOM

        writer = csv.writer(response)
        writer.writerow(
            ["ID", "Submitted at", "Consent given", "IP (masked)", "Expires"]
            + [label for label, _ in field_specs]
        )

        for sub in submissions:
            data = sub.form_data or {}
            ip = sub.submitter_ip or ""
            if ip:
                parts = ip.split(".")
                ip = ".".join(parts[:2]) + ".x.x" if len(parts) == 4 else "masked"

            writer.writerow(
                [
                    sub.pk,
                    sub.submit_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "Yes" if sub.consent_given else "No",
                    ip,
                    sub.expires_at.date().isoformat() if sub.expires_at else "",
                ]
                + [self._csv_safe(data.get(clean_name, "")) for _, clean_name in field_specs]
            )

        logger.info(
            "CSV export: page_id=%s user_id=%s rows=%d",
            page_id, request.user.pk, submissions.count()
        )
        return response


class SubmissionRedactView(StaffRequiredMixin, View):
    """
    POST: Redact PII fields from a single submission (PIPEDA right-to-erasure).
    The submission record is retained; only PII-flagged field values are replaced
    with [REDACTED].
    """
    http_method_names = ["post"]

    def post(self, request: HttpRequest, page_id: int, submission_id: int) -> HttpResponse:
        form_page = get_object_or_404(FormPage, pk=page_id)
        submission = get_object_or_404(FormSubmission, pk=submission_id, page=form_page)

        try:
            submission.redact_pii(redacted_by=request.user)
            messages.success(
                request,
                _(
                    "Personal information has been redacted from submission #%(id)s. / "
                    "Les renseignements personnels ont été supprimés de la soumission nº %(id)s."
                ) % {"id": submission_id},
            )
            self._write_audit(request, submission, form_page)
        except Exception:
            logger.exception("Redaction failed for submission_id=%s", submission_id)
            messages.error(
                request,
                _("Redaction failed. Please try again or contact your system administrator."),
            )

        return redirect("forms:submission-list", page_id=page_id)

    def _write_audit(self, request, submission, form_page) -> None:
        try:
            from apps.audit.models import AuditLogEntry
            AuditLogEntry.objects.create(
                event_type="admin.pii.redacted",
                outcome="success",
                actor_id=str(request.user.pk),
                actor_email="",
                actor_ip=request.META.get("REMOTE_ADDR"),
                actor_user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
                resource_type="forms.FormSubmission",
                resource_id=str(submission.pk),
                event_detail={"page_id": form_page.pk, "page_slug": form_page.slug},
                prev_hash="",
                entry_hash="",
                request_id="",
                session_id=(request.session.session_key or "") if hasattr(request, "session") else "",
            )
        except Exception:
            logger.exception("Audit log failed for PII redaction submission_id=%s", submission.pk)


class SubmissionDeleteView(StaffRequiredMixin, View):
    """
    POST: Permanently delete a submission (use only for test data; prefer redact for production).
    Requires superuser — staff alone cannot delete.
    """
    http_method_names = ["post"]

    def test_func(self) -> bool:
        return self.request.user.is_authenticated and self.request.user.is_superuser

    def post(self, request: HttpRequest, page_id: int, submission_id: int) -> HttpResponse:
        form_page = get_object_or_404(FormPage, pk=page_id)
        submission = get_object_or_404(FormSubmission, pk=submission_id, page=form_page)
        submission.delete()
        messages.success(
            request,
            _("Submission #%(id)s permanently deleted.") % {"id": submission_id},
        )
        return redirect("forms:submission-list", page_id=page_id)
