"""
Volunteer impact report query functions — PIPEDA-safe.

All aggregates are at programme-level only — no volunteer PII is returned
by any function in this module.

Integration Wave: wraps apps.volunteers.services.reporting with snapshot
plumbing compatible with the Reports BB (ReportSnapshot.data JSONField).

All imports from apps.volunteers.* are deferred to function bodies to
prevent circular import chains at Django startup.
"""
from __future__ import annotations

import logging
from decimal import Decimal

logger = logging.getLogger("apps.reports.services.volunteers")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _str_decimals(obj):
    """Recursively convert Decimal values to strings for JSON/JSONB storage."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _str_decimals(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_str_decimals(i) for i in obj]
    return obj


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

def get_monthly_volunteer_summary(year: int, month: int) -> dict:
    """
    Return volunteer aggregates for a given calendar month.

    Calls apps.volunteers.services.reporting.hours_by_program (lazy import).
    All data is programme-level — no volunteer names or contact details.

    Returns:
    {
        "total_approved_hours": Decimal,  # sum of row["approved_hours"]
        "volunteer_count":      int,      # sum of row["volunteer_count"]
        "opportunity_count":   int,       # len(rows) — distinct opportunities
        "program_count":       int,       # distinct program_slugs
        "by_program":          list[dict], # passthrough from hours_by_program
    }

    PIPEDA: programme-level aggregates only — no volunteer PII.
    """
    from apps.volunteers.services.reporting import hours_by_program

    rows = hours_by_program(year, month)

    total_approved_hours = sum(
        (row["approved_hours"] for row in rows), Decimal("0.00")
    )
    volunteer_count = sum(row["volunteer_count"] for row in rows)
    opportunity_count = len(rows)
    program_count = len({row["program_slug"] for row in rows if row["program_slug"]})

    logger.debug(
        "reports.services.volunteers.get_monthly_volunteer_summary "
        "year=%s month=%s opportunity_count=%s volunteer_count=%s total_approved_hours=%s",
        year, month, opportunity_count, volunteer_count, total_approved_hours,
    )

    return {
        "total_approved_hours": total_approved_hours,
        "volunteer_count": volunteer_count,
        "opportunity_count": opportunity_count,
        "program_count": program_count,
        "by_program": rows,
    }


def compute_volunteer_snapshot(year: int, month: int) -> dict:
    """
    Compute the full volunteer aggregate dict for storage in ReportSnapshot.data.

    Called by the Celery Beat task. Must be idempotent.
    Decimal values are serialised to strings for JSONB storage.

    Returns a dict with key ``row_count`` (total_approved_hours as int)
    consumed by the Celery task for monitoring.

    Calls:
    - get_monthly_volunteer_summary(year, month) for monthly programme data.
    - apps.volunteers.services.reporting.impact_value(year) for annual
      economic impact estimate (lazy import).

    PIPEDA: no volunteer names, emails, or addresses in any value.
    """
    from apps.volunteers.services.reporting import impact_value

    summary = get_monthly_volunteer_summary(year, month)
    impact = impact_value(year)

    snapshot = {
        "year": year,
        "month": month,
        "hours": _str_decimals({
            "total_approved_hours": summary["total_approved_hours"],
            "volunteer_count": summary["volunteer_count"],
            "opportunity_count": summary["opportunity_count"],
            "program_count": summary["program_count"],
            "by_program": summary["by_program"],
        }),
        "impact": _str_decimals({
            "total_approved_hours": impact["total_approved_hours"],
            "estimated_value_cad": impact["estimated_value_cad"],
            "volunteer_count": impact["volunteer_count"],
            "hourly_rate": impact["hourly_rate"],
            "province": impact["province"],
        }),
        "row_count": int(summary["total_approved_hours"]),
    }

    logger.info(
        "reports.services.volunteers.compute_volunteer_snapshot "
        "year=%s month=%s volunteer_count=%s total_approved_hours=%s",
        year, month, summary["volunteer_count"], summary["total_approved_hours"],
    )

    return snapshot
