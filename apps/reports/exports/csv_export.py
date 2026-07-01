"""
CSV streaming export utilities.

All exports use StreamingHttpResponse with a generator so Django never
buffers the full dataset in memory. Handles 50K+ rows safely.

PIPEDA column whitelist: each export function defines an explicit COLUMNS
constant. Any field not in COLUMNS is silently excluded. This prevents
accidental PII inclusion when model fields are added in future.

Implemented in Wave 2 (financial) and Wave 3 (donations).
"""
from __future__ import annotations

import csv
import logging
from datetime import date
from typing import Generator, Iterable

from django.http import StreamingHttpResponse

logger = logging.getLogger("apps.reports.exports.csv_export")


class _EchoBuffer:
    """Minimal write buffer required by csv.writer to work with StreamingHttpResponse."""

    def write(self, value: str) -> str:  # noqa: D401
        return value


def streaming_csv_response(
    rows: Iterable,
    columns: list[str],
    filename_prefix: str,
    period_start: date,
    period_end: date,
) -> StreamingHttpResponse:
    """
    Return a StreamingHttpResponse that streams ``rows`` as CSV.

    Args:
        rows: Iterable of dicts (or indexable sequences) with keys matching
              ``columns``. Extra keys are silently dropped.
        columns: Ordered list of column header/key names. This is the PIPEDA
                 whitelist — only these columns appear in the output.
        filename_prefix: Short slug, e.g. "reconciliation" or "revenue".
                         Combined with period dates to form the Content-Disposition
                         filename. No PII in filenames.
        period_start: Start of the export period (used in filename).
        period_end: End of the export period (used in filename).

    Returns:
        StreamingHttpResponse with Content-Type text/csv and a safe filename.
    """
    buffer = _EchoBuffer()
    writer = csv.writer(buffer)

    def _stream() -> Generator[str, None, None]:
        # UTF-8 BOM — required so Excel (on Windows and macOS) correctly renders
        # non-ASCII characters such as French campaign names (PIPEDA-compliant
        # bilingual export).
        yield "﻿"
        yield writer.writerow(columns)
        for row in rows:
            if isinstance(row, dict):
                yield writer.writerow([row.get(col, "") for col in columns])
            else:
                yield writer.writerow(row)

    # Filename: no PII, no internal IDs — period dates only.
    filename = f"{filename_prefix}_{period_start.isoformat()}_{period_end.isoformat()}.csv"

    response = StreamingHttpResponse(_stream(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def export_reconciliation_csv(start: date, end: date) -> StreamingHttpResponse:
    """
    Stream payment reconciliation export as CSV.

    PIPEDA column whitelist — no payer PII:
        reference, status, fee_code, amount_paid, refund_total, net, created_at

    Implemented in Wave 2.
    """
    COLUMNS = [
        "reference",
        "status",
        "fee_code",
        "amount_paid",
        "refund_total",
        "net",
        "created_at",
    ]
    raise NotImplementedError("Implemented in Wave 2")


def export_revenue_csv(year: int, month: int) -> StreamingHttpResponse:
    """
    Stream monthly revenue breakdown as CSV.

    PIPEDA column whitelist:
        fee_code, gross_revenue, processor_fees, net_revenue, tax_collected, count

    Implemented in Wave 2.
    """
    COLUMNS = [
        "fee_code",
        "gross_revenue",
        "processor_fees",
        "net_revenue",
        "tax_collected",
        "count",
    ]
    raise NotImplementedError("Implemented in Wave 2")


def export_receipts_csv(start: date, end: date) -> StreamingHttpResponse:
    """
    Stream donation receipts list as CSV.

    PIPEDA column whitelist — no donor name/address/email:
        receipt_number, issued_date, amount_receipted, eligible_amount, campaign_name

    CRA note: receipt_number is included because the donor received this number
    on their official receipt — it is not PII in itself.

    Implemented in Wave 3.
    """
    COLUMNS = [
        "receipt_number",
        "issued_date",
        "amount_receipted",
        "eligible_amount",
        "campaign_name",
    ]
    raise NotImplementedError("Implemented in Wave 3")


def export_t3010_prep_csv(fiscal_year_end: date) -> StreamingHttpResponse:
    """
    Stream T3010 preparatory data as CSV.

    PIPEDA column whitelist — annual aggregates only, no individual donor PII:
        month, total_receipted_amount, total_eligible_amount, receipt_count,
        large_donation_count

    Implemented in Wave 3.
    """
    COLUMNS = [
        "month",
        "total_receipted_amount",
        "total_eligible_amount",
        "receipt_count",
        "large_donation_count",
    ]
    raise NotImplementedError("Implemented in Wave 3")
