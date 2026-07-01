"""
Operational report views.

Audience: Municipal IT/SRE (payments.view_operationalreport permission).

Views:
  OperationalDashboardView — Celery task health + webhook processing metrics
  TaskFailureDetailView    — Drill-down into recent task failures (SRE use)

No payer/donor PII in any view — task names, counts, and error summaries only.
Error summaries are limited to the first line of the traceback (full tracebacks
are never sent to templates — they may contain request data with PII).

Implemented in Wave 4.
"""
from __future__ import annotations

import logging

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.views.generic import TemplateView

logger = logging.getLogger("apps.reports.views.operational")


class OperationalDashboardView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Celery and webhook operational health dashboard.

    Shows: task success/failure rates, webhook processing status,
    Celery Beat periodic task status.

    Permission: payments.view_operationalreport
    Template:   reports/operational/dashboard.html
    """

    permission_required = "payments.view_operationalreport"
    template_name = "reports/operational/dashboard.html"

    def get_context_data(self, **kwargs):
        raise NotImplementedError("Implemented in Wave 4")


class TaskFailureDetailView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Recent Celery task failure drill-down for SRE investigation.

    Shows: task_id, task_name, date_done, error_summary (first traceback line only).
    Full tracebacks are never exposed in the UI — they may contain request
    data with PII.

    Permission: payments.view_operationalreport
    Template:   reports/operational/task_failures.html
    """

    permission_required = "payments.view_operationalreport"
    template_name = "reports/operational/task_failures.html"

    def get_context_data(self, **kwargs):
        raise NotImplementedError("Implemented in Wave 4")
