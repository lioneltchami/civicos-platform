"""
Analytics & Reporting BB — Celery Tasks.

Tasks:
  compute_monthly_snapshots — Celery Beat task. Runs at 02:00 on the 2nd of
    each month (America/Toronto). Computes ReportSnapshot rows for the previous
    calendar month (and optionally backfills earlier months if missing).
    Idempotent: uses update_or_create so re-runs are safe.

  recompute_snapshot — On-demand recomputation for a single (report_type,
    year, month). Used by admin actions and manual recovery.

Queue: "reports" (see CELERY_TASK_ROUTES in config/settings/base.py).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from celery import shared_task

logger = logging.getLogger("apps.reports.tasks")


@shared_task(
    bind=True,
    name="apps.reports.tasks.compute_monthly_snapshots",
    queue="reports",
    max_retries=3,
    default_retry_delay=300,  # 5 min between retries
    autoretry_for=(Exception,),
    acks_late=True,  # Don't ack until task completes (safer on failure)
)
def compute_monthly_snapshots(self) -> dict:  # noqa: ANN001
    """
    Celery Beat task — computes ReportSnapshot rows for the previous calendar
    month (America/Toronto time).

    Runs at 02:00 on the 2nd of each month (Toronto time). By that time all
    midnight-triggered tasks from the previous day have completed, ensuring a
    complete month of source data.

    Returns a summary dict with keys: year, month, snapshots_written.

    Idempotent: safe to re-run; existing rows are updated, not duplicated.
    """
    # Determine "previous month" in Toronto local time.
    now_local = timezone.localtime(timezone.now())
    first_of_current_month = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_end = first_of_current_month - timedelta(days=1)
    year = last_month_end.year
    month = last_month_end.month

    logger.info(
        "reports.tasks.compute_monthly_snapshots.start year=%s month=%s",
        year,
        month,
    )

    snapshots_written = _compute_all_snapshots(year, month)

    logger.info(
        "reports.tasks.compute_monthly_snapshots.done year=%s month=%s snapshots_written=%s",
        year,
        month,
        snapshots_written,
    )
    return {"year": year, "month": month, "snapshots_written": snapshots_written}


@shared_task(
    bind=True,
    name="apps.reports.tasks.recompute_snapshot",
    queue="reports",
    max_retries=2,
    default_retry_delay=60,
    acks_late=True,
)
def recompute_snapshot(self, report_type: str, year: int, month: int) -> dict:  # noqa: ANN001
    """
    On-demand recomputation for a single (report_type, year, month) snapshot.

    Used by admin actions and manual recovery scripts. Safe to call at any time.
    Returns: {report_type, year, month, success, row_count}
    """
    from apps.reports.models import ReportSnapshot

    logger.info(
        "reports.tasks.recompute_snapshot.start report_type=%s year=%s month=%s",
        report_type,
        year,
        month,
    )

    data, row_count = _compute_single_snapshot(report_type, year, month)

    ReportSnapshot.objects.update_or_create(
        report_type=report_type,
        period_year=year,
        period_month=month,
        defaults={"data": data, "row_count": row_count},
    )

    logger.info(
        "reports.tasks.recompute_snapshot.done report_type=%s year=%s month=%s row_count=%s",
        report_type,
        year,
        month,
        row_count,
    )
    return {
        "report_type": report_type,
        "year": year,
        "month": month,
        "success": True,
        "row_count": row_count,
    }


# ---------------------------------------------------------------------------
# Internal helpers (not Celery tasks — called from task bodies above)
# ---------------------------------------------------------------------------


def _compute_all_snapshots(year: int, month: int) -> int:
    """
    Compute and persist all report type snapshots for (year, month).

    Returns the number of ReportSnapshot rows written/updated.

    Implemented in Wave 2/3/4 as each service module is completed.
    """
    from apps.reports.models import ReportSnapshot
    from apps.reports.services.financial import compute_financial_snapshot

    written = 0

    # ── Wave 2: financial ─────────────────────────────────────────────────────
    try:
        data = compute_financial_snapshot(year, month)
        ReportSnapshot.objects.update_or_create(
            report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
            period_year=year,
            period_month=month,
            defaults={"data": data, "row_count": data.get("row_count", 0)},
        )
        written += 1
        logger.info(
            "reports.tasks._compute_all_snapshots.financial_ok year=%s month=%s",
            year,
            month,
        )
    except Exception:
        logger.exception(
            "reports.tasks._compute_all_snapshots.financial_failed year=%s month=%s",
            year,
            month,
        )

    # ── Wave 3: donations ─────────────────────────────────────────────────────
    try:
        from apps.reports.services.donations import compute_donations_snapshot

        don_data = compute_donations_snapshot(year, month)
        ReportSnapshot.objects.update_or_create(
            report_type=ReportSnapshot.REPORT_TYPE_DONATIONS,
            period_year=year,
            period_month=month,
            defaults={"data": don_data, "row_count": don_data.get("row_count", 0)},
        )
        written += 1
        logger.info(
            "reports.tasks._compute_all_snapshots.donations_ok year=%s month=%s",
            year,
            month,
        )
    except Exception:
        logger.exception(
            "reports.tasks._compute_all_snapshots.donations_failed year=%s month=%s",
            year,
            month,
        )

    # ── Wave 4: operational ───────────────────────────────────────────────────
    try:
        from apps.reports.services.operational import compute_operational_snapshot

        op_data = compute_operational_snapshot(year, month)
        ReportSnapshot.objects.update_or_create(
            report_type=ReportSnapshot.REPORT_TYPE_OPERATIONAL,
            period_year=year,
            period_month=month,
            defaults={"data": op_data, "row_count": op_data.get("row_count", 0)},
        )
        written += 1
        logger.info(
            "reports.tasks._compute_all_snapshots.operational_ok year=%s month=%s",
            year,
            month,
        )
    except Exception:
        logger.exception(
            "reports.tasks._compute_all_snapshots.operational_failed year=%s month=%s",
            year,
            month,
        )

    # ── Integration Wave: volunteers ──────────────────────────────────────────
    try:
        from apps.reports.services.volunteers import compute_volunteer_snapshot

        vol_data = compute_volunteer_snapshot(year, month)
        ReportSnapshot.objects.update_or_create(
            report_type=ReportSnapshot.REPORT_TYPE_VOLUNTEERS,
            period_year=year,
            period_month=month,
            defaults={"data": vol_data, "row_count": vol_data.get("row_count", 0)},
        )
        written += 1
        logger.info(
            "reports.tasks._compute_all_snapshots.volunteers_ok year=%s month=%s",
            year,
            month,
        )
    except Exception:
        logger.exception(
            "reports.tasks._compute_all_snapshots.volunteers_failed year=%s month=%s",
            year,
            month,
        )

    return written


def _compute_single_snapshot(report_type: str, year: int, month: int) -> tuple[dict, int]:
    """
    Dispatch to the correct service module for a single report_type.

    Returns (data_dict, row_count).

    Implemented in Wave 2/3/4.
    """
    from apps.reports.models import ReportSnapshot

    if report_type == ReportSnapshot.REPORT_TYPE_FINANCIAL:
        from apps.reports.services.financial import compute_financial_snapshot

        data = compute_financial_snapshot(year, month)
    elif report_type == ReportSnapshot.REPORT_TYPE_DONATIONS:
        from apps.reports.services.donations import compute_donations_snapshot

        data = compute_donations_snapshot(year, month)
    elif report_type == ReportSnapshot.REPORT_TYPE_OPERATIONAL:
        from apps.reports.services.operational import compute_operational_snapshot

        data = compute_operational_snapshot(year, month)
    elif report_type == ReportSnapshot.REPORT_TYPE_VOLUNTEERS:
        from apps.reports.services.volunteers import compute_volunteer_snapshot

        data = compute_volunteer_snapshot(year, month)
    else:
        raise ValueError(f"Unknown report_type: {report_type!r}")

    row_count = data.get("row_count", 0)
    return data, row_count
