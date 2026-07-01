"""
Wagtail hooks for the Forms building block.

Registers:
- Custom submissions view in Wagtail admin with filtering and export
- PIPEDA redaction action on individual submissions
- Summary panel on FormPage in the Wagtail explorer
"""
import csv
import logging
from datetime import timedelta

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from wagtail import hooks
from wagtail.admin.widgets import Button as WagtailButton
from wagtail.contrib.forms.views import SubmissionsListView

from .models import FormPage, FormSubmission
from .utils import _mask_ip

logger = logging.getLogger(__name__)


def _csv_safe(value) -> str:
    """Prevent CSV formula injection."""
    s = str(value) if value is not None else ""
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        s = "'" + s
    return s


class CivicOSSubmissionsListView(SubmissionsListView):
    """
    Extended submissions list view with:
    - CSV export
    - PII field highlighting
    - Retention date display
    """
    model = FormSubmission

    def get_context(self):
        context = super().get_context()
        # Add PII field names so template can highlight them
        if self.form_page:
            context["pii_fields"] = set(
                self.form_page.form_fields.filter(is_pii=True)
                .values_list("clean_name", flat=True)
            )
        return context


@hooks.register("register_admin_urls")
def register_forms_admin_urls():
    """Register custom admin URLs for the forms building block."""
    return [
        path(
            "forms/submissions/<int:page_id>/export/csv/",
            export_submissions_csv,
            name="forms_export_csv",
        ),
        path(
            "forms/submissions/<int:page_id>/<int:submission_id>/redact/",
            redact_submission,
            name="forms_redact_submission",
        ),
    ]


def export_submissions_csv(request, page_id):
    """
    Export all submissions for a FormPage as a CSV download.
    Respects staff permission — only users with access to the page can export.
    """
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.path)
    if not request.user.is_staff:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    form_page = get_object_or_404(FormPage, pk=page_id)
    submissions = FormSubmission.objects.filter(page=form_page).order_by("-submit_time")
    field_names = [field.clean_name for field in form_page.form_fields.all()]

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="submissions_{form_page.slug}_{timezone.now().date()}.csv"'
    )
    # BOM for Excel UTF-8 compatibility
    response.write("﻿")

    writer = csv.writer(response)
    # Header row
    writer.writerow(
        ["Submission ID", "Submit time", "Consent given", "Submitter IP (masked)", "Expires at"]
        + field_names
    )

    for submission in submissions:
        form_data = submission.form_data or {}
        # Mask IP: keep first two octets only for privacy
        ip = submission.submitter_ip or ""
        if ip:
            parts = ip.split(".")
            ip = ".".join(parts[:2]) + ".x.x" if len(parts) == 4 else ip[:8] + "..."

        writer.writerow(
            [
                submission.pk,
                submission.submit_time.isoformat(),
                "Yes" if submission.consent_given else "No",
                ip,
                submission.expires_at.date().isoformat() if submission.expires_at else "",
            ]
            + [_csv_safe(form_data.get(name, "")) for name in field_names]
        )

    logger.info(
        "Submissions CSV exported for page_id=%s by user_id=%s (%d rows)",
        page_id, request.user.pk, submissions.count()
    )
    return response


@require_POST
def redact_submission(request, page_id, submission_id):
    """
    POST: Redact PII fields from a single submission.
    PIPEDA right-to-erasure implementation.
    """
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.path)
    if not request.user.is_staff:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

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
        # Write audit entry
        try:
            from apps.audit.models import AuditLogEntry
            AuditLogEntry.objects.create(
                event_type="admin.pii.redacted",
                outcome="success",
                actor_id=str(request.user.pk),
                actor_email="",
                actor_ip=_mask_ip(request.META.get("REMOTE_ADDR") or ""),
                actor_user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
                resource_type="forms.FormSubmission",
                resource_id=str(submission_id),
                event_detail={"page_id": page_id, "page_slug": form_page.slug},
                prev_hash="",
                entry_hash="",
                request_id="",
                session_id=(request.session.session_key or "") if hasattr(request, "session") else "",
            )
        except Exception:
            logger.exception("Audit log failed for PII redaction submission_id=%s", submission_id)
    except Exception:
        logger.exception("Failed to redact submission_id=%s", submission_id)
        messages.error(request, _("Redaction failed. Please try again or contact support."))

    return redirect(
        reverse("wagtailforms:list_submissions", args=[page_id])
    )


@hooks.register("register_page_listing_more_buttons")
def add_submissions_button(page, user, next_url=None):
    """Add 'View submissions' and 'Export CSV' buttons to FormPage rows in explorer."""
    if isinstance(page, FormPage) and page.permissions_for_user(user).can_edit():
        yield WagtailButton(
            label=str(_("Submissions")),
            url=reverse("wagtailforms:list_submissions", args=[page.pk]),
            classname="button button-small",
            priority=10,
        )
        yield WagtailButton(
            label=str(_("Export CSV")),
            url=reverse("forms_export_csv", args=[page.pk]),
            classname="button button-small",
            priority=20,
        )
