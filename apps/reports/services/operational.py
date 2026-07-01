"""
Operational report query functions for municipal IT/SRE audience.

Returns infrastructure and task-queue health metrics. No donor or payer PII
is ever included in any function in this module.

Sources:
- django-celery-results TaskResult (task_id, task_name, status, date_done, traceback)
- apps.payments.models.WebhookEvent (event_type, status, received_at)
- Django cache (Celery Beat idempotency lock status)

Implemented in Wave 4.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("apps.reports.services.operational")


def get_celery_task_summary(days: int = 30) -> dict:
    """
    Return Celery task execution metrics for the past ``days`` days.

    Keys:
        total_tasks, succeeded, failed, retried,
        failure_rate_pct, avg_duration_seconds,
        by_task_name (list of dicts with task_name, count, failure_count)

    Source: django-celery-results TaskResult model.
    No PII — task names and counts only; tracebacks are never forwarded to UI.
    """
    raise NotImplementedError("Implemented in Wave 4")


def get_webhook_processing_summary(days: int = 30) -> dict:
    """
    Return webhook event processing metrics for the past ``days`` days.

    Keys:
        total_events, processed, failed, pending,
        failure_rate_pct, by_event_type (list of dicts)

    Source: apps.payments.models.WebhookEvent.
    No PII — event types and statuses only. Payload column excluded (may contain PII).
    """
    raise NotImplementedError("Implemented in Wave 4")


def get_celery_beat_status() -> dict:
    """
    Return the current status of Celery Beat periodic tasks.

    Keys:
        tasks (list of dicts with task_name, last_run_at, next_run_at, enabled)

    Source: django-celery-beat PeriodicTask model.
    """
    raise NotImplementedError("Implemented in Wave 4")


def get_task_failure_details(task_name: str | None = None, limit: int = 50) -> list[dict]:
    """
    Return recent task failures for SRE investigation.

    Each dict: task_id, task_name, date_done, status, error_summary.
    ``error_summary`` is the first line of the traceback only — never the full
    traceback (which may contain request data with PII).

    No payer/donor PII — task failure details only.
    """
    raise NotImplementedError("Implemented in Wave 4")


def compute_operational_snapshot(year: int, month: int) -> dict:
    """
    Compute the operational aggregate dict for storage in ReportSnapshot.data.

    Called by the Celery Beat task. Must be idempotent.
    """
    raise NotImplementedError("Implemented in Wave 4")
