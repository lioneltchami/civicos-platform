"""
PDF export utilities (WeasyPrint — synchronous, already in requirements).

V1 scope: monthly financial summary PDF only.

Privacy/Compliance rules:
- PDFs are generated synchronously and streamed directly in the HTTP response.
- The PDF bytes are NEVER stored on disk, in S3, or in any database column.
  Storing PDFs cross-border would trigger PIPEDA cross-border transfer provisions.
- The pdf_path is never exposed in any API or template — the response is always
  a direct byte stream.
- Content is pre-aggregated — no donor/payer name, address, or email is rendered.

WeasyPrint is imported inside the function body to allow the module to load in
environments where WeasyPrint's C libraries (libcairo, libpango) may not be
available (e.g., some CI containers that only run unit tests without rendering).
"""

from __future__ import annotations

import calendar
import logging
from datetime import UTC, date, datetime
from decimal import Decimal

from django.http import HttpResponse
from django.template.loader import render_to_string

logger = logging.getLogger("apps.reports.exports.pdf_export")


def export_monthly_summary_pdf(year: int, month: int) -> HttpResponse:
    """
    Generate and stream a monthly financial summary PDF.

    Uses WeasyPrint to render the ``reports/financial/monthly_summary_pdf.html``
    template to PDF bytes, then returns an HttpResponse that streams those bytes
    to the client.

    The generated PDF is never stored on disk — it is produced in-process from
    an in-memory byte buffer and returned directly.

    Args:
        year:  Calendar year (e.g. 2025).
        month: Calendar month 1–12.

    Returns:
        HttpResponse with:
            Content-Type:        application/pdf
            Content-Disposition: attachment; filename="financial_summary_YYYY_MM.pdf"

    PIPEDA: all data rendered is pre-aggregated — no donor names, addresses,
    or emails appear in the PDF output.
    """  # noqa: RUF002
    from apps.reports.services.financial import (
        get_failed_payments,
        get_monthly_revenue,
        get_refund_summary,
    )

    # ── Gather data ──────────────────────────────────────────────────────────
    revenue = get_monthly_revenue(year, month)
    refunds = get_refund_summary(year, month)
    failed_count = get_failed_payments(year, month).count()

    last_day = calendar.monthrange(year, month)[1]
    period_start = date(year, month, 1)
    period_end = date(year, month, last_day)
    month_label = f"{calendar.month_name[month]} {year}"

    net_after_refunds = revenue.get("total_gross", Decimal("0.00")) - refunds.get(
        "total_refunded", Decimal("0.00")
    )

    generated_at = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")

    ctx = {
        "year": year,
        "month": month,
        "month_label": month_label,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "revenue": revenue,
        "refunds": refunds,
        "failed_count": failed_count,
        "net_after_refunds": net_after_refunds,
        "generated_at": generated_at,
    }

    # ── Render HTML → PDF ────────────────────────────────────────────────────
    html_string = render_to_string("reports/financial/monthly_summary_pdf.html", ctx)

    # Import WeasyPrint here so the module can still be imported in environments
    # where WeasyPrint's native libraries aren't present (unit test CI containers).
    try:
        import weasyprint
    except ImportError as exc:
        logger.error("reports.exports.pdf_export: WeasyPrint not available — %s", exc)
        raise

    pdf_bytes = weasyprint.HTML(string=html_string).write_pdf()

    logger.info(
        "reports.exports.pdf_export.monthly_summary year=%s month=%s bytes=%s",
        year,
        month,
        len(pdf_bytes),
    )

    # ── Build response ───────────────────────────────────────────────────────
    filename = f"financial_summary_{year}_{month:02d}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Content-Length"] = len(pdf_bytes)
    # No caching — financial data is time-sensitive and internal-only.
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response["X-Content-Type-Options"] = "nosniff"

    return response
