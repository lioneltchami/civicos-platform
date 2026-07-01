"""
Operational report query functions for municipal IT/SRE audience.

Returns infrastructure and task-queue health metrics. No donor or payer PII
is ever included in any function in this module.

Sources:
- django-celery-results TaskResult (task_id, task_name, status, date_done, traceback)
- apps.payments.models.WebhookEvent (event_type, status, received_at)
- django-celery-beat PeriodicTask (name, last_run_at, enabled)

PIPEDA invariants:
- No donor/payer name, email, or address is ever returned.
- Task tracebacks are never forwarded to the UI — only the first line of the
  traceback (``error_summary``) is included, stripped of any path or argument
  data that could contain PII.
- WebhookEvent.payload (may contain card holder data) is never accessed here.
"""
from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta, timezone as dt_timezone

from django.db.models import Count, Q

logger = logging.getLogger("apps.reports.services.operational")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utc_since(days: int) -> datetime:
    """Return UTC datetime ``days`` ago (inclusive lower bound for filtering)."""
    return datetime.now(tz=dt_timezone.utc) - timedelta(days=days)


def _safe_first_line(traceback_text: str | None) -> str:
    """
    Extract the first non-empty line of a traceback as the error_summary.

    Returns an empty string when no traceback is present. Full tracebacks are
    never surfaced in the UI because they may contain request arguments with PII.
    """
    if not traceback_text:
        return ""
    for line in traceback_text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]  # hard cap to prevent oversized rendering
    return ""


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------

def get_celery_task_summary(days: int = 30) -> dict:
    """
    Return Celery task execution metrics for the past ``days`` days.

    Keys returned:
        days                 (int)   — window used
        total_tasks          (int)
        succeeded            (int)
        failed               (int)
        retried              (int)
        failure_rate_pct     (float) — percentage, rounded to 2 d.p.
        avg_duration_seconds (float) — always 0.0; TaskResult stores no start time.
        by_task_name         (list[dict]) — top-20 by count:
            task_name (str), count (int), failure_count (int)

    Source: django-celery-results TaskResult.
    No PII — task names and status counts only; tracebacks never forwarded.
    """
    from django_celery_results.models import TaskResult

    since = _utc_since(days)
    qs = TaskResult.objects.filter(date_done__gte=since)

    totals = qs.aggregate(
        total=Count("id"),
        succeeded=Count("id", filter=Q(status="SUCCESS")),
        failed=Count("id", filter=Q(status="FAILURE")),
        retried=Count("id", filter=Q(status="RETRY")),
    )

    total = totals["total"] or 0
    succeeded = totals["succeeded"] or 0
    failed = totals["failed"] or 0
    retried = totals["retried"] or 0
    failure_rate = round(failed / total * 100, 2) if total > 0 else 0.0

    by_name_rows = (
        qs.values("task_name")
        .annotate(
            count=Count("id"),
            failure_count=Count("id", filter=Q(status="FAILURE")),
        )
        .order_by("-count")[:20]
    )

    by_task_name = [
        {
            "task_name": row["task_name"] or "(unknown)",
            "count": row["count"],
            "failure_count": row["failure_count"],
        }
        for row in by_name_rows
    ]

    logger.debug(
        "reports.services.operational.celery_task_summary days=%s total=%s failed=%s",
        days, total, failed,
    )

    return {
        "days": days,
        "total_tasks": total,
        "succeeded": succeeded,
        "failed": failed,
        "retried": retried,
        "failure_rate_pct": failure_rate,
        # TaskResult does not store task start time; duration is unavailable
        # without a custom result backend.  Callers should not rely on this field.
        "avg_duration_seconds": 0.0,
        "by_task_name": by_task_name,
    }


def get_webhook_processing_summary(days: int = 30) -> dict:
    """
    Return webhook event processing metrics for the past ``days`` days.

    Keys returned:
        days              (int)
        total_events      (int)
        processed         (int)
        pending           (int)
        failed            (int)   — processed=False AND error non-empty
        failure_rate_pct  (float)
        by_event_type     (list[dict]):
            event_type (str), count (int), processed_count (int)

    Source: apps.payments.models.WebhookEvent.
    PIPEDA: WebhookEvent.payload (may contain card holder data) is never read.
    """
    from apps.payments.models import WebhookEvent

    since = _utc_since(days)
    qs = WebhookEvent.objects.filter(created_at__gte=since)

    totals = qs.aggregate(
        total=Count("id"),
        # Avoid kwarg name "processed" — it shadows the BooleanField and causes
        # Django to raise FieldError ("Cannot compute Count('processed')").
        proc_count=Count("id", filter=Q(processed=True)),
        fail_count=Count("id", filter=Q(processed=False, error__gt="")),
    )

    total = totals["total"] or 0
    processed_count = totals["proc_count"] or 0
    failed_count = totals["fail_count"] or 0
    pending_count = max(0, total - processed_count - failed_count)
    failure_rate = round(failed_count / total * 100, 2) if total > 0 else 0.0

    by_type_rows = (
        qs.values("event_type")
        .annotate(
            count=Count("id"),
            processed_count=Count("id", filter=Q(processed=True)),
        )
        .order_by("-count")[:20]
    )

    by_event_type = [
        {
            "event_type": row["event_type"] or "(unknown)",
            "count": row["count"],
            "processed_count": row["processed_count"],
            # Pre-computed so templates don't need arithmetic: Django's add filter
            # with "-" returns '' (empty string), not a subtraction result.
            "other_count": row["count"] - row["processed_count"],
        }
        for row in by_type_rows
    ]

    logger.debug(
        "reports.services.operational.webhook_summary days=%s total=%s failed=%s",
        days, total, failed_count,
    )

    return {
        "days": days,
        "total_events": total,
        "processed": processed_count,
        "pending": pending_count,
        "failed": failed_count,
        "failure_rate_pct": failure_rate,
        "by_event_type": by_event_type,
    }


def get_celery_beat_status() -> dict:
    """
    Return the current status of Celery Beat periodic tasks.

    Keys returned:
        tasks (list[dict]):
            name         (str)
            task         (str)  — dotted task path
            enabled      (bool)
            last_run_at  (datetime | None)
            total_run_count (int)
            schedule     (str)  — human-readable schedule string

    Source: django-celery-beat PeriodicTask model.
    """
    from django_celery_beat.models import PeriodicTask

    # Exclude the internal celery-beat housekeeping task.
    tasks_qs = (
        PeriodicTask.objects.exclude(name="celery.backend_cleanup")
        .select_related("interval", "crontab", "solar", "clocked")
        .order_by("name")
    )

    tasks = []
    for pt in tasks_qs:
        # Build a human-readable schedule description
        if pt.interval:
            schedule_str = str(pt.interval)
        elif pt.crontab:
            schedule_str = str(pt.crontab)
        elif pt.solar:
            schedule_str = str(pt.solar)
        elif pt.clocked:
            schedule_str = str(pt.clocked)
        else:
            schedule_str = "—"

        tasks.append({
            "name": pt.name,
            "task": pt.task,
            "enabled": pt.enabled,
            "last_run_at": pt.last_run_at,
            "total_run_count": pt.total_run_count,
            "schedule": schedule_str,
        })

    logger.debug(
        "reports.services.operational.celery_beat_status task_count=%s", len(tasks)
    )

    return {"tasks": tasks}


def get_task_failure_details(task_name: str | None = None, limit: int = 50) -> list[dict]:
    """
    Return recent Celery task failures for SRE investigation.

    Each dict contains:
        task_id       (str)
        task_name     (str)
        date_done     (datetime)
        error_summary (str)  — FIRST LINE of traceback only; never full traceback.
            Full tracebacks are omitted because they may contain request arguments
            (URL paths, form data) that include PII (PIPEDA §4.7).

    Args:
        task_name: If provided, filter to this task name only.
        limit:     Maximum rows returned (default 50, max 200).

    Source: django-celery-results TaskResult (status=FAILURE).
    """
    from django_celery_results.models import TaskResult

    limit = min(limit, 200)  # hard cap — prevents accidental oversized responses

    qs = (
        TaskResult.objects
        .filter(status="FAILURE")
        # Only fetch the columns we actually use — traceback can be multi-KB;
        # deferring unused columns (result, meta, etc.) cuts memory significantly
        # when a failure spike produces many rows.
        .only("task_id", "task_name", "date_done", "traceback")
        .order_by("-date_done")
    )
    if task_name:
        qs = qs.filter(task_name=task_name)

    failures = []
    for row in qs[:limit]:
        failures.append({
            "task_id": row.task_id,
            "task_name": row.task_name or "(unknown)",
            "date_done": row.date_done,
            # First line only — full traceback withheld (PIPEDA).
            "error_summary": _safe_first_line(row.traceback),
        })

    logger.debug(
        "reports.services.operational.task_failure_details task_name=%s count=%s",
        task_name, len(failures),
    )

    return failures


def compute_operational_snapshot(year: int, month: int) -> dict:
    """
    Compute the operational aggregate dict for storage in ReportSnapshot.data.

    Uses a 30-day lookback window anchored at the end of the given month so
    that historical snapshots remain stable when re-computed.

    Called by the Celery Beat ``compute_monthly_snapshots`` task. Must be
    idempotent — safe to re-run for the same (year, month).

    Returns a dict with keys: task_summary, webhook_summary, row_count
    (total events in window). Beat status is intentionally excluded — it
    is not time-series data and would not be meaningful as a historical snapshot.
    """
    # Anchor the lookback window at midnight on the last day of the given month
    # (UTC) so historical re-computation returns consistent values.
    last_day = calendar.monthrange(year, month)[1]
    anchor = datetime(year, month, last_day, 23, 59, 59, tzinfo=dt_timezone.utc)
    days_window = 30

    # Import heavy models lazily — this function is called from a Celery worker
    # and keeping the module-level import clean avoids worker startup overhead.
    from django_celery_results.models import TaskResult
    from apps.payments.models import WebhookEvent

    since = anchor - timedelta(days=days_window)

    # Task summary
    task_qs = TaskResult.objects.filter(date_done__gte=since, date_done__lte=anchor)
    task_totals = task_qs.aggregate(
        total=Count("id"),
        succeeded=Count("id", filter=Q(status="SUCCESS")),
        failed=Count("id", filter=Q(status="FAILURE")),
        retried=Count("id", filter=Q(status="RETRY")),
    )
    task_total = task_totals["total"] or 0
    task_failed = task_totals["failed"] or 0

    task_summary = {
        "days": days_window,
        "total_tasks": task_total,
        "succeeded": task_totals["succeeded"] or 0,
        "failed": task_failed,
        "retried": task_totals["retried"] or 0,
        "failure_rate_pct": round(task_failed / task_total * 100, 2) if task_total > 0 else 0.0,
    }

    # Webhook summary
    wh_qs = WebhookEvent.objects.filter(created_at__gte=since, created_at__lte=anchor)
    wh_totals = wh_qs.aggregate(
        total=Count("id"),
        proc_count=Count("id", filter=Q(processed=True)),
        fail_count=Count("id", filter=Q(processed=False, error__gt="")),
    )
    wh_total = wh_totals["total"] or 0
    wh_failed = wh_totals["fail_count"] or 0

    webhook_summary = {
        "days": days_window,
        "total_events": wh_total,
        "processed": wh_totals["proc_count"] or 0,
        "pending": max(0, wh_total - (wh_totals["proc_count"] or 0) - wh_failed),
        "failed": wh_failed,
        "failure_rate_pct": round(wh_failed / wh_total * 100, 2) if wh_total > 0 else 0.0,
    }

    row_count = task_total + wh_total

    logger.info(
        "reports.services.operational.compute_snapshot year=%s month=%s "
        "tasks=%s webhooks=%s",
        year, month, task_total, wh_total,
    )

    return {
        "task_summary": task_summary,
        "webhook_summary": webhook_summary,
        "row_count": row_count,
    }
