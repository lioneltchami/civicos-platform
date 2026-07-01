"""
PDF export utilities (WeasyPrint — synchronous, already in requirements).

V1 scope: monthly financial summary PDF only. No async S3 presigned URLs
(cross-border Canadian data → PIPEDA risk). PDFs are generated synchronously
and streamed directly in the HTTP response, same as CSV exports.

The pdf_path is NEVER exposed in any API or template — the response is always
a direct file stream.

Implemented in Wave 4.
"""
from __future__ import annotations

import logging

from django.http import HttpResponse

logger = logging.getLogger("apps.reports.exports.pdf_export")


def export_monthly_summary_pdf(year: int, month: int) -> HttpResponse:
    """
    Generate and stream a monthly financial summary PDF.

    Uses WeasyPrint to render a Django template to PDF and returns it as an
    HttpResponse with Content-Type application/pdf.

    The generated PDF is never stored on disk — it is streamed directly from
    memory. No pdf_path is created or exposed.

    Args:
        year: Calendar year (e.g. 2025).
        month: Calendar month 1–12.

    Returns:
        HttpResponse with Content-Type application/pdf and a safe
        Content-Disposition filename (no PII).

    PIPEDA: all data rendered in the PDF is pre-aggregated — no donor names,
    addresses, or emails appear in the output.

    Implemented in Wave 4.
    """
    raise NotImplementedError("Implemented in Wave 4")
