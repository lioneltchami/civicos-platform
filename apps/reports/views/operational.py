"""
Operational report views.

Audience: Municipal IT/SRE (payments.view_operationalreport permission).

Views:
  OperationalDashboardView — Celery task health + webhook processing metrics.
  TaskFailureDetailView    — Drill-down into recent task failures (SRE use).

No payer/donor PII in any view — task names, counts, and error summaries only.
Error summaries are limited to the first line of the traceback (full tracebacks
are never sent to templates — they may contain request data with PII).

Permission: payments.view_operationalreport (custom permission on the Payment
model — see apps/payments/models.py).
"""
from __future__ import annotations

import logging

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.views.generic import TemplateView

from apps.reports.services.operational import (
    get_celery_beat_status,
    get_celery_task_summary,
    get_task_failure_details,
    get_webhook_processing_summary,
)

logger = logging.getLogger("apps.reports.views.operational")

# Number of days to show in the operational dashboard by default.
_DEFAULT_DAYS = 30
_MAX_DAYS = 90
_DEFAULT_FAILURE_LIMIT = 50


class OperationalDashboardView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Celery and webhook operational health dashboard.

    Shows:
    - Celery task success/failure rates for the past N days.
    - Webhook event processing status for the past N days.
    - Celery Beat periodic task schedule and last-run timestamps.

    Query parameter: ?days=N (default 30, max 90).

    Permission: payments.view_operationalreport
    Template:   reports/operational/dashboard.html
    """

    permission_required = "payments.view_operationalreport"
    template_name = "reports/operational/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # Parse optional ?days= parameter
        try:
            days = int(self.request.GET.get("days", _DEFAULT_DAYS))
            days = max(1, min(days, _MAX_DAYS))
        except (ValueError, TypeError):
            days = _DEFAULT_DAYS

        task_summary = get_celery_task_summary(days=days)
        webhook_summary = get_webhook_processing_summary(days=days)
        beat_status = get_celery_beat_status()

        logger.info(
            "reports.views.operational.dashboard user_pk=%s days=%s "
            "tasks=%s webhooks=%s",
            self.request.user.pk,
            days,
            task_summary["total_tasks"],
            webhook_summary["total_events"],
        )

        ctx.update({
            "days": days,
            "task_summary": task_summary,
            "webhook_summary": webhook_summary,
            "beat_status": beat_status,
        })
        return ctx


class TaskFailureDetailView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Recent Celery task failure drill-down for SRE investigation.

    Shows: task_id, task_name, date_done, error_summary (first traceback line only).
    Full tracebacks are never exposed in the UI — they may contain request
    data with PII (PIPEDA §4.7).

    Query parameters:
        ?task_name=<name>  — filter to a specific task (optional).
        ?limit=N           — max rows returned (default 50, max 200).

    Permission: payments.view_operationalreport
    Template:   reports/operational/task_failures.html
    """

    permission_required = "payments.view_operationalreport"
    template_name = "reports/operational/task_failures.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        task_name_filter = self.request.GET.get("task_name", "").strip() or None

        try:
            limit = int(self.request.GET.get("limit", _DEFAULT_FAILURE_LIMIT))
            limit = max(1, min(limit, 200))
        except (ValueError, TypeError):
            limit = _DEFAULT_FAILURE_LIMIT

        failures = get_task_failure_details(task_name=task_name_filter, limit=limit)

        logger.info(
            "reports.views.operational.task_failures user_pk=%s task_name=%s count=%s",
            self.request.user.pk,
            task_name_filter,
            len(failures),
        )

        ctx.update({
            "failures": failures,
            "task_name_filter": task_name_filter or "",
            "limit": limit,
        })
        return ctx
