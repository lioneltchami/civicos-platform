"""
Streaming CSV exports for volunteer data.

PIPEDA COLUMN WHITELIST — this file MUST NOT export:
  - volunteer email addresses
  - volunteer phone numbers
  - volunteer SIN (any form)
  - emergency contact fields
  - accommodation_notes
  - motivation / personal statement text
  - home address fields

Permitted columns: programme-level aggregates, opportunity names (not volunteer
names), approved hours totals, date ranges, and anonymised row counts.

Uses the shared streaming_csv_response() helper from csv_export.py so export
rows never buffer in memory.
"""
from __future__ import annotations

import logging
from datetime import date

from django.utils.translation import gettext_lazy as _

from apps.reports.exports.csv_export import streaming_csv_response

logger = logging.getLogger("apps.reports.exports.volunteer_export")

# ── PIPEDA column whitelists ─────────────────────────────────────────────────
# These lists are the ONLY columns that may appear in each export.
# Adding a new column requires explicit approval and a PIPEDA review note here.

VOLUNTEER_HOURS_COLUMNS = [
    "program",
    "opportunity",
    "year",
    "month",
    "approved_hours",
    "volunteer_count",
    "shift_count",
]

VOLUNTEER_T3010_COLUMNS = [
    "section",
    "label",
    "value",
]


def export_volunteer_hours_csv(
    year: int,
    month: int | None = None,
) -> "django.http.StreamingHttpResponse":
    """
    Stream approved volunteer hours as CSV, grouped by (program, opportunity).

    Columns (PIPEDA whitelist):
        program, opportunity, year, month, approved_hours, volunteer_count, shift_count

    Args:
        year:  Calendar year (e.g. 2026).
        month: Optional calendar month 1–12. If omitted, all months in year are included.

    Returns:
        StreamingHttpResponse — suitable for direct return from a Django view.
        Content-Disposition filename: volunteer_hours_<year>[-<month>].csv
    """
    from apps.volunteers.services.reporting import hours_by_program

    rows_data = hours_by_program(year, month)
    month_label = f"{month:02d}" if month else "all"

    def _rows():
        for row in rows_data:
            yield {
                "program":        row["program_title_en"],
                "opportunity":    row["opportunity_title_en"],
                "year":           str(year),
                "month":          month_label,
                "approved_hours": str(row["approved_hours"]),
                "volunteer_count": str(row["volunteer_count"]),
                "shift_count":    str(row["shift_count"]),
            }

    # Filename period — use full year when no month given.
    period_start = date(year, month or 1, 1)
    import calendar as _cal
    last_month = month or 12
    period_end = date(year, last_month, _cal.monthrange(year, last_month)[1])

    logger.info(
        "volunteer_export.export_volunteer_hours_csv: streaming for year=%d month=%s "
        "(%d rows).",
        year,
        month_label,
        len(rows_data),
    )

    return streaming_csv_response(
        _rows(),
        VOLUNTEER_HOURS_COLUMNS,
        "volunteer_hours",
        period_start,
        period_end,
    )


def export_t3010_volunteer_csv(year: int) -> "django.http.StreamingHttpResponse":
    """
    Stream T3010 Schedule 2 volunteer section data as CSV.

    Sections in the output:
        SUMMARY  — charity-level totals (total volunteers, hours, estimated value)
        CATEGORY — one row per volunteer category breakdown

    Columns (PIPEDA whitelist):
        section, label, value

    PIPEDA: all values are aggregated; no individual volunteer data included.

    Args:
        year: Calendar year for the T3010 return (e.g. 2026).

    Returns:
        StreamingHttpResponse with Content-Disposition attachment.
    """
    from apps.volunteers.services.reporting import t3010_volunteer_metrics

    metrics = t3010_volunteer_metrics(year)

    def _rows():
        # ── SUMMARY section ────────────────────────────────────────────────
        yield {"section": "SUMMARY", "label": "Year",                 "value": str(metrics["year"])}
        yield {"section": "SUMMARY", "label": "Total Volunteers",     "value": str(metrics["total_volunteers"])}
        yield {"section": "SUMMARY", "label": "Total Approved Hours", "value": str(metrics["total_volunteer_hours"])}
        yield {"section": "SUMMARY", "label": "Programs Active",      "value": str(metrics["num_programs"])}
        yield {"section": "SUMMARY", "label": "Estimated Value (CAD)", "value": str(metrics["estimated_value_cad"])}
        yield {"section": "SUMMARY", "label": "Rate Province",        "value": "ON (T3010 national estimate)"}

        # ── CATEGORY section ───────────────────────────────────────────────
        for cat in metrics["categories"]:
            yield {
                "section": "CATEGORY",
                "label":   cat["category"],
                "value":   f"hours={cat['hours']} volunteers={cat['volunteers']}",
            }

    period_start = date(year, 1, 1)
    period_end = date(year, 12, 31)

    logger.info(
        "volunteer_export.export_t3010_volunteer_csv: streaming T3010 data for year=%d.",
        year,
    )

    return streaming_csv_response(
        _rows(),
        VOLUNTEER_T3010_COLUMNS,
        "volunteer_t3010",
        period_start,
        period_end,
    )
