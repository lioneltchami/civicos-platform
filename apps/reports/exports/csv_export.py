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
from collections.abc import Generator, Iterable
from datetime import date

from django.http import StreamingHttpResponse

logger = logging.getLogger("apps.reports.exports.csv_export")


class _EchoBuffer:
    """Minimal write buffer required by csv.writer to work with StreamingHttpResponse."""

    def write(self, value: str) -> str:
        return value


# Characters that cause spreadsheet applications (Excel, LibreOffice) to
# interpret a cell as a formula. Prefixing with a tab neutralises the trigger
# without altering the displayed value (leading whitespace is trimmed).
_FORMULA_TRIGGERS: frozenset[str] = frozenset({"=", "+", "-", "@", "\t", "\r"})


def _sanitize_csv_cell(value: object) -> str:
    """
    Neutralise CSV formula-injection for a single cell value.

    Any string whose first character could trigger formula evaluation in a
    spreadsheet is prefixed with a tab character (OWASP CSV injection defence).
    Non-string values are coerced to str first.
    """
    s = str(value) if not isinstance(value, str) else value
    if s and s[0] in _FORMULA_TRIGGERS:
        return "\t" + s
    return s


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
                yield writer.writerow([_sanitize_csv_cell(row.get(col, "")) for col in columns])
            else:
                yield writer.writerow([_sanitize_csv_cell(v) for v in row])

    # Filename: no PII, no internal IDs — period dates only.
    filename = f"{filename_prefix}_{period_start.isoformat()}_{period_end.isoformat()}.csv"

    response = StreamingHttpResponse(_stream(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def export_reconciliation_csv(start: date, end: date) -> StreamingHttpResponse:
    """
    Stream payment reconciliation export as CSV.

    PIPEDA column whitelist — no payer PII:
        reference, status, fee_code, amount_paid, refund_total, net, paid_at

    Uses Django's streaming response so 50K+ rows never buffer in memory.
    Rows are fetched via queryset.iterator(chunk_size=500) and piped directly
    into the CSV writer.
    """
    from apps.reports.services.financial import get_reconciliation_queryset

    COLUMNS = [  # noqa: N806
        "reference",
        "status",
        "fee_code",
        "amount_paid",
        "refund_total",
        "net",
        "paid_at",
    ]

    qs = get_reconciliation_queryset(start, end)

    def _rows():  # noqa: ANN202
        for payment in qs.iterator(chunk_size=500):
            net = payment.amount_paid - payment.refund_total
            yield {
                "reference": payment.intent.reference,
                "status": payment.intent.status,
                "fee_code": payment.fee_code or "",
                "amount_paid": str(payment.amount_paid),
                "refund_total": str(payment.refund_total),
                "net": str(net),
                "paid_at": payment.paid_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
            }

    return streaming_csv_response(_rows(), COLUMNS, "reconciliation", start, end)


def export_revenue_csv(
    year: int, month: int, *, revenue: dict | None = None
) -> StreamingHttpResponse:
    """
    Stream monthly revenue breakdown as CSV (one row per fee_code / purpose label).

    PIPEDA column whitelist — no payer PII:
        fee_code, gross_revenue, processor_fees, net_revenue, tax_collected, count

    Args:
        year, month: Calendar period.
        revenue: Optional pre-fetched result from ``get_monthly_revenue()``. If
                 supplied, avoids a second identical DB query when the caller
                 already fetched the data to compute ``row_count``.
    """
    import calendar as _cal

    from apps.reports.services.financial import get_monthly_revenue

    COLUMNS = [  # noqa: N806
        "fee_code",
        "gross_revenue",
        "processor_fees",
        "net_revenue",
        "tax_collected",
        "count",
    ]

    if revenue is None:
        revenue = get_monthly_revenue(year, month)

    def _fmt(v) -> str:  # noqa: ANN001
        """Format a Decimal (or numeric) to exactly 2 d.p."""
        from decimal import ROUND_HALF_UP
        from decimal import Decimal as _D  # noqa: N814

        try:
            return str(_D(str(v)).quantize(_D("0.01"), rounding=ROUND_HALF_UP))
        except Exception:
            return str(v)

    def _rows():  # noqa: ANN202
        for label, data in revenue["by_fee_code"].items():
            yield {
                "fee_code": label,
                "gross_revenue": _fmt(data["gross"]),
                "processor_fees": _fmt(data["processor_fees"]),
                "net_revenue": _fmt(data["net"]),
                "tax_collected": _fmt(data["tax"]),
                "count": data["count"],
            }

    last_day = _cal.monthrange(year, month)[1]
    period_start = date(year, month, 1)
    period_end = date(year, month, last_day)

    return streaming_csv_response(_rows(), COLUMNS, "revenue", period_start, period_end)


def export_receipts_csv(start: date, end: date) -> StreamingHttpResponse:
    """
    Stream donation receipts list as CSV.

    PIPEDA column whitelist — no donor name, address, or email:
        receipt_number, status, receipt_date, eligible_amount,
        advantage_amount, campaign_name

    CRA note: receipt_number (serial_number) is included because the donor
    received this number on their official tax receipt — it is a CRA-required
    administrative identifier, not PII in itself.

    Rows are fetched via queryset.iterator(chunk_size=500) — safe for full-year
    exports of 10 K+ receipts without buffering in memory.
    """
    from apps.reports.services.donations import get_receipt_list_queryset

    COLUMNS = [  # noqa: N806
        "receipt_number",
        "status",
        "receipt_date",
        "eligible_amount",
        "advantage_amount",
        "campaign_name",
    ]

    qs = get_receipt_list_queryset(start, end)

    def _rows():  # noqa: ANN202
        for r in qs.iterator(chunk_size=500):
            campaign_name = ""
            if r.donation_id and r.donation.campaign_id:
                campaign_name = r.donation.campaign.name_en or ""
            yield {
                "receipt_number": r.serial_number,
                "status": r.status,
                "receipt_date": r.receipt_date.isoformat() if r.receipt_date else "",
                "eligible_amount": str(r.eligible_amount),
                "advantage_amount": str(r.advantage_amount),
                "campaign_name": campaign_name,
            }

    return streaming_csv_response(_rows(), COLUMNS, "receipts", start, end)


def export_t3010_prep_csv(
    fiscal_year_end: date,
    data: dict | None = None,
) -> StreamingHttpResponse:
    """
    Stream T3010 preparatory data as CSV.

    Three sections (distinguished by the ``section`` column):
    - SUMMARY  — charity-level totals, filing deadline, CRA line references
    - MONTHLY  — one row per calendar month in the fiscal year
    - CAMPAIGN — one row per campaign

    PIPEDA column whitelist — aggregates only, no individual donor PII:
        section, period, donation_count, total_donated, eligible_amount,
        advantage_amount, receipts_issued

    Args:
        fiscal_year_end: Last day of the fiscal year.
        data: Optional pre-computed dict from get_t3010_preparatory_data().
              Pass this from the calling view to avoid a second DB round-trip.
              If None, the data is fetched here.
    """
    if data is None:
        from apps.reports.services.donations import get_t3010_preparatory_data

        data = get_t3010_preparatory_data(fiscal_year_end)

    COLUMNS = [  # noqa: N806
        "section",
        "period",
        "donation_count",
        "total_donated",
        "eligible_amount",
        "advantage_amount",
        "receipts_issued",
    ]

    def _fmt(v) -> str:  # noqa: ANN001
        from decimal import ROUND_HALF_UP
        from decimal import Decimal as _D  # noqa: N814

        try:
            return str(_D(str(v)).quantize(_D("0.01"), rounding=ROUND_HALF_UP))
        except Exception:
            return str(v)

    def _rows():  # noqa: ANN202
        # ── Summary ───────────────────────────────────────────────────────────
        yield {
            "section": "SUMMARY",
            "period": (
                f"FY {data['fiscal_year_start'].isoformat()} "
                f"to {data['fiscal_year_end'].isoformat()}"
            ),
            "donation_count": str(data["donation_count"]),
            "total_donated": _fmt(data["total_eligible_amount"] + data["total_advantage_amount"]),
            "eligible_amount": _fmt(data["total_eligible_amount"]),
            "advantage_amount": _fmt(data["total_advantage_amount"]),
            "receipts_issued": str(data["receipts_issued_in_year"]),
        }
        yield {
            "section": "SUMMARY",
            "period": "T3010 Line 4500 — Total Receipted Eligible Amount",
            "donation_count": "",
            "total_donated": "",
            "eligible_amount": _fmt(data["total_receipted_donations"]),
            "advantage_amount": "",
            "receipts_issued": "",
        }
        yield {
            "section": "SUMMARY",
            "period": "Filing Deadline (6 months after FY end)",
            "donation_count": "",
            "total_donated": "",
            "eligible_amount": "",
            "advantage_amount": "",
            "receipts_issued": data["filing_deadline"].isoformat(),
        }
        yield {
            "section": "SUMMARY",
            "period": "Large Donations >=10000 (review for Schedule 4)",
            "donation_count": str(data["large_donation_count"]),
            "total_donated": "",
            "eligible_amount": "",
            "advantage_amount": "",
            "receipts_issued": "",
        }
        # ── Monthly breakdown ─────────────────────────────────────────────────
        for row in data["by_month"]:
            adv = row["total_amount"] - row["eligible_amount"]
            yield {
                "section": "MONTHLY",
                "period": row["month_label"],
                "donation_count": str(row["donation_count"]),
                "total_donated": _fmt(row["total_amount"]),
                "eligible_amount": _fmt(row["eligible_amount"]),
                "advantage_amount": _fmt(adv),
                "receipts_issued": "",
            }
        # ── Campaign breakdown ────────────────────────────────────────────────
        for row in data["by_campaign"]:
            adv = row["total_amount"] - row["eligible_amount"]
            yield {
                "section": "CAMPAIGN",
                "period": row["campaign_name"],
                "donation_count": str(row["count"]),
                "total_donated": _fmt(row["total_amount"]),
                "eligible_amount": _fmt(row["eligible_amount"]),
                "advantage_amount": _fmt(adv),
                "receipts_issued": "",
            }

    period_start = data["fiscal_year_start"]
    period_end = data["fiscal_year_end"]
    return streaming_csv_response(_rows(), COLUMNS, "t3010_prep", period_start, period_end)
