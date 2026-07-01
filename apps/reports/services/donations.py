"""
Donations & CRA compliance report query functions — Wave 3.

All aggregates are at campaign/month level — no individual donor PII is returned
by any function in this module.

CRA T3010 context: Canadian charities must file an annual information return
within 6 months of fiscal year end (CRA s.149.1(14)). This module provides
the preparatory data (totals, eligible amounts, breakdowns) needed to complete
Schedule 1 and Schedule 4 of the T3010 return.

Key CRA rules implemented:
  eligible_amount  = donation.amount - donation.advantage_amount  (CRA IT-110R3)
  T3010 line 4500  = total eligible_amount for all receipted donations in FY
  T3010 line 4510  = total eligible_amount where advantage_amount > 0
  Filing deadline  = 6 months after fiscal year end

  NOTE on Schedule 4 (large gifts from other charities):
  CRA requires individual disclosure of gifts ≥$10K from other registered
  charities. This system tracks donation amounts but does NOT have a
  donor_charity_registration_number field — that data must be collected manually
  from the donor record before filing. The `large_donation_count` field surfaces
  donations ≥$10K so finance staff know which to follow up on.

PIPEDA invariants:
  - No donor name, email, or address is returned by any function.
  - by_campaign breakdowns expose campaign_name_en (public fundraising data).
  - receipt serial_numbers are CRA-required identifiers that the donor received;
    their inclusion in administrative exports is required by CRA regulation.
  - Logs contain year/month/count only — never individual donor references.
"""
from __future__ import annotations

import calendar as _cal
import logging
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.db.models import Count, Exists, OuterRef, Q, Sum

logger = logging.getLogger("apps.reports.services.donations")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _month_utc_range(year: int, month: int) -> tuple[datetime, datetime]:
    """
    Half-open UTC [start, end) datetime interval for a calendar month.

    Filters on ``created_at`` / ``issued_at`` UTC timestamps. Consistent with
    ``apps.reports.services.financial._month_utc_range``.
    """
    start = datetime(year, month, 1, tzinfo=dt_timezone.utc)
    end = (
        datetime(year + 1, 1, 1, tzinfo=dt_timezone.utc)
        if month == 12
        else datetime(year, month + 1, 1, tzinfo=dt_timezone.utc)
    )
    return start, end


def _year_utc_range(year: int) -> tuple[datetime, datetime]:
    """Half-open UTC [start, end) interval for a full calendar year."""
    return (
        datetime(year, 1, 1, tzinfo=dt_timezone.utc),
        datetime(year + 1, 1, 1, tzinfo=dt_timezone.utc),
    )


def _fiscal_year_date_range(fiscal_year_end: date) -> tuple[date, date]:
    """
    Return (fiscal_year_start, fiscal_year_end) for the 12-month period
    ending on ``fiscal_year_end``.

    Examples:
        fiscal_year_end=2024-12-31  →  start=2024-01-01
        fiscal_year_end=2024-03-31  →  start=2023-04-01
        fiscal_year_end=2024-02-29  →  start=2023-03-01 (leap-year safe)
    """
    fy_end = fiscal_year_end
    try:
        one_year_ago = fy_end.replace(year=fy_end.year - 1)
    except ValueError:
        # Feb 29 in a leap year has no equivalent in previous year
        one_year_ago = fy_end.replace(year=fy_end.year - 1, day=28)
    return one_year_ago + timedelta(days=1), fy_end


def _filing_deadline(fiscal_year_end: date) -> date:
    """
    CRA T3010 filing deadline: 6 months after fiscal year end (s.149.1(14)).
    Clips to the last valid day of the target month.

    Examples:
        fiscal_year_end=2024-12-31  →  2025-06-30
        fiscal_year_end=2024-08-31  →  2025-02-28 (Feb has no 31st)
    """
    target_month = fiscal_year_end.month + 6
    target_year = fiscal_year_end.year
    if target_month > 12:
        target_month -= 12
        target_year += 1
    last_day = _cal.monthrange(target_year, target_month)[1]
    return date(target_year, target_month, min(fiscal_year_end.day, last_day))


def _dt_from_date_utc(d: date) -> datetime:
    """Calendar date → UTC midnight datetime (exclusive upper bounds use d + 1 day)."""
    return datetime(d.year, d.month, d.day, tzinfo=dt_timezone.utc)


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

def get_monthly_donation_summary(year: int, month: int) -> dict:
    """
    Return donation aggregates for a given calendar month.

    Filters on ``Donation.created_at`` UTC timestamps. Only
    ``status='completed'`` donations are included.

    Returns:
    {
        "total_donations":       Decimal,  # sum of amount
        "total_eligible_amount": Decimal,  # sum of eligible_amount
        "total_advantage_amount": Decimal, # sum of advantage_amount
        "donation_count":        int,
        "unique_donor_count":    int,      # distinct donor PKs
        "recurring_count":       int,      # is_recurring=True
        "one_time_count":        int,
        "average_donation":      Decimal,  # 0.00 if no donations
        "by_campaign": [
            {
                "campaign_name": str,       # "General Fund" when campaign is None
                "count": int,
                "total_amount": Decimal,
                "eligible_amount": Decimal,
            }
        ],
        "receipt_summary": {
            "issued": int,      # receipts issued_at in this month
            "cancelled": int,
            "superseded": int,
            "total": int,
        },
    }

    PIPEDA: no donor names, emails, or addresses in any returned value.
    """
    from apps.payments.models import Donation, OfficialDonationReceipt

    month_start, month_end = _month_utc_range(year, month)

    base_qs = Donation.objects.filter(
        status="completed",
        created_at__gte=month_start,
        created_at__lt=month_end,
    )

    # ── Aggregate totals (1 query) ────────────────────────────────────────────
    totals = base_qs.aggregate(
        total_donations=Sum("amount", default=Decimal("0.00")),
        total_eligible=Sum("eligible_amount", default=Decimal("0.00")),
        total_advantage=Sum("advantage_amount", default=Decimal("0.00")),
        donation_count=Count("id"),
        unique_donor_count=Count("donor", distinct=True),
        recurring_count=Count("id", filter=Q(is_recurring=True)),
    )

    donation_count = totals["donation_count"]
    total_donations = totals["total_donations"]
    average_donation = (
        (total_donations / donation_count).quantize(Decimal("0.01"))
        if donation_count
        else Decimal("0.00")
    )

    # ── Per-campaign breakdown (1 query) ──────────────────────────────────────
    by_campaign_qs = (
        base_qs
        .values("campaign__name_en")
        .annotate(
            count=Count("id"),
            total_amount=Sum("amount"),
            eligible_amount=Sum("eligible_amount"),
        )
        .order_by("-total_amount")
    )
    by_campaign = [
        {
            "campaign_name": row["campaign__name_en"] or "General Fund",
            "count": row["count"],
            "total_amount": row["total_amount"] or Decimal("0.00"),
            "eligible_amount": row["eligible_amount"] or Decimal("0.00"),
        }
        for row in by_campaign_qs
    ]

    # ── Receipt summary for this month (1 query on OfficialDonationReceipt) ───
    receipt_agg = OfficialDonationReceipt.objects.filter(
        issued_at__gte=month_start,
        issued_at__lt=month_end,
    ).aggregate(
        issued=Count("id", filter=Q(status="issued")),
        cancelled=Count("id", filter=Q(status="cancelled")),
        superseded=Count("id", filter=Q(status="superseded")),
    )
    receipt_summary = {
        "issued": receipt_agg["issued"],
        "cancelled": receipt_agg["cancelled"],
        "superseded": receipt_agg["superseded"],
        "total": receipt_agg["issued"] + receipt_agg["cancelled"] + receipt_agg["superseded"],
    }

    logger.debug(
        "reports.services.donations.get_monthly_donation_summary year=%s month=%s "
        "donation_count=%s total_donations=%s",
        year, month, donation_count, total_donations,
    )

    return {
        "total_donations": total_donations,
        "total_eligible_amount": totals["total_eligible"],
        "total_advantage_amount": totals["total_advantage"],
        "donation_count": donation_count,
        "unique_donor_count": totals["unique_donor_count"],
        "recurring_count": totals["recurring_count"],
        "one_time_count": donation_count - totals["recurring_count"],
        "average_donation": average_donation,
        "by_campaign": by_campaign,
        "receipt_summary": receipt_summary,
    }


def get_annual_donation_summary(year: int) -> dict:
    """
    Return annual donation aggregates for a full calendar year.

    Used by the annual summary view and as a precursor to T3010 prep.

    Returns:
    {
        "year":                  int,
        "total_donations":       Decimal,
        "total_eligible_amount": Decimal,
        "total_advantage_amount": Decimal,
        "donation_count":        int,
        "unique_donor_count":    int,
        "recurring_count":       int,
        "large_donation_count":  int,    # donations ≥ $10,000 (CRA Schedule 4 signal)
        "receipts_issued":       int,    # issued receipts whose issued_at is in year
        "by_month": [
            {
                "month":            int,
                "month_label":      str,  # "January", "February", …
                "total_amount":     Decimal,
                "eligible_amount":  Decimal,
                "donation_count":   int,
            }
        ],
        "by_campaign": [
            {
                "campaign_name":  str,
                "count":          int,
                "total_amount":   Decimal,
                "eligible_amount": Decimal,
            }
        ],
    }

    PIPEDA: no donor PII — amounts and counts only.
    CRA T3010 line 4500: total eligible_amount is the key figure here.
    CRA Schedule 4: `large_donation_count` signals how many donations ≥ $10K
    exist — finance staff must manually identify those from other charities.
    """
    from apps.payments.models import Donation, OfficialDonationReceipt

    year_start, year_end = _year_utc_range(year)

    base_qs = Donation.objects.filter(
        status="completed",
        created_at__gte=year_start,
        created_at__lt=year_end,
    )

    # ── Annual totals (1 query) ───────────────────────────────────────────────
    totals = base_qs.aggregate(
        total_donations=Sum("amount", default=Decimal("0.00")),
        total_eligible=Sum("eligible_amount", default=Decimal("0.00")),
        total_advantage=Sum("advantage_amount", default=Decimal("0.00")),
        donation_count=Count("id"),
        unique_donor_count=Count("donor", distinct=True),
        recurring_count=Count("id", filter=Q(is_recurring=True)),
        large_donation_count=Count("id", filter=Q(amount__gte=Decimal("10000.00"))),
    )

    # ── By-month breakdown (1 query, derive in Python) ────────────────────────
    # TruncMonth is DB-agnostic (works on both SQLite/tests and PostgreSQL/prod).
    from django.db.models.functions import TruncMonth
    month_rows = (
        base_qs
        .annotate(month_trunc=TruncMonth("created_at"))
        .values("month_trunc")
        .annotate(
            total_amount=Sum("amount"),
            eligible_amount=Sum("eligible_amount"),
            count=Count("id"),
        )
        .order_by("month_trunc")
    )
    by_month = [
        {
            "month": row["month_trunc"].month,
            "month_label": _cal.month_name[row["month_trunc"].month],
            "total_amount": row["total_amount"] or Decimal("0.00"),
            "eligible_amount": row["eligible_amount"] or Decimal("0.00"),
            "donation_count": row["count"],
        }
        for row in month_rows
    ]

    # ── By-campaign breakdown (1 query) ───────────────────────────────────────
    by_campaign_qs = (
        base_qs
        .values("campaign__name_en")
        .annotate(
            count=Count("id"),
            total_amount=Sum("amount"),
            eligible_amount=Sum("eligible_amount"),
        )
        .order_by("-total_amount")
    )
    by_campaign = [
        {
            "campaign_name": row["campaign__name_en"] or "General Fund",
            "count": row["count"],
            "total_amount": row["total_amount"] or Decimal("0.00"),
            "eligible_amount": row["eligible_amount"] or Decimal("0.00"),
        }
        for row in by_campaign_qs
    ]

    # ── Receipts issued in this year (1 query) ────────────────────────────────
    receipts_issued = OfficialDonationReceipt.objects.filter(
        issued_at__gte=year_start,
        issued_at__lt=year_end,
        status="issued",
    ).count()

    logger.info(
        "reports.services.donations.get_annual_donation_summary year=%s "
        "donation_count=%s total_donations=%s receipts_issued=%s",
        year, totals["donation_count"], totals["total_donations"], receipts_issued,
    )

    return {
        "year": year,
        "total_donations": totals["total_donations"],
        "total_eligible_amount": totals["total_eligible"],
        "total_advantage_amount": totals["total_advantage"],
        "donation_count": totals["donation_count"],
        "unique_donor_count": totals["unique_donor_count"],
        "recurring_count": totals["recurring_count"],
        "large_donation_count": totals["large_donation_count"],
        "receipts_issued": receipts_issued,
        "by_month": by_month,
        "by_campaign": by_campaign,
    }


def get_t3010_preparatory_data(fiscal_year_end: date) -> dict:
    """
    Compile the data a charity needs to prepare CRA T3010.

    Covers the 12-month fiscal year ending on ``fiscal_year_end``.
    Filters ``Donation.created_at`` using UTC boundaries derived from
    the fiscal year start/end calendar dates.

    Returns:
    {
        "fiscal_year_start":         date,
        "fiscal_year_end":           date,
        "filing_deadline":           date,   # 6 months after FY end
        "total_receipted_donations": Decimal, # T3010 line 4500 — sum eligible_amount
                                              # for donations that have an issued receipt
        "total_eligible_amount":     Decimal, # T3010 line 4510 — all completed donations
        "total_advantage_amount":    Decimal, # advantage total (OQ-8 disclosure)
        "donation_count":            int,
        "large_donation_count":      int,    # ≥$10K — flag for Schedule 4 review
        "receipts_issued_in_year":   int,    # issued receipts with issued_at in FY
        "receipts_cancelled_in_year": int,
        "by_campaign": [
            {
                "campaign_name":  str,
                "count":          int,
                "total_amount":   Decimal,
                "eligible_amount": Decimal,
            }
        ],
        "by_month": [
            {
                "year":             int,
                "month":            int,
                "month_label":      str,
                "total_amount":     Decimal,
                "eligible_amount":  Decimal,
                "donation_count":   int,
            }
        ],
    }

    PIPEDA / CRA note:
        Individual donor names and addresses are never returned.
        T3010 itself does not require individual donor disclosure for gifts
        under $10K from individuals. Schedule 4 only applies to gifts ≥$10K
        from other registered charities — flagged via large_donation_count.

    CRA reference:
        line 4500: total eligible amount of all tax-receipted donations
        line 4510: total amount of gifts for which an advantage was received
        Schedule 4: gifts from other registered charities ≥$10K
    """
    from apps.payments.models import Donation, OfficialDonationReceipt

    fiscal_year_start, fy_end = _fiscal_year_date_range(fiscal_year_end)
    filing_deadline = _filing_deadline(fy_end)

    # UTC datetime boundaries for the fiscal year
    fy_start_dt = _dt_from_date_utc(fiscal_year_start)
    fy_end_dt = _dt_from_date_utc(fy_end + timedelta(days=1))  # exclusive upper bound

    base_qs = Donation.objects.filter(
        status="completed",
        created_at__gte=fy_start_dt,
        created_at__lt=fy_end_dt,
    )

    # ── Aggregate totals (2 queries) ─────────────────────────────────────────
    # IMPORTANT: total_receipted must be computed in a SEPARATE query.
    #
    # If total_receipted used filter=Q(receipts__status="issued") inside the
    # same .aggregate() call as the unfiltered sums, Django would emit a LEFT
    # JOIN on OfficialDonationReceipt for the whole query. Any donation that
    # has gone through a receipt correction cycle (one issued + one cancelled)
    # would appear twice in the JOIN result, doubling donation_count,
    # total_eligible, and total_advantage — corrupting CRA T3010 line 4500.
    #
    # Fix: run unfiltered aggregates first (no JOIN), then compute
    # total_receipted via an Exists subquery (correlated, no fan-out).
    from apps.payments.models import OfficialDonationReceipt as _Receipt  # local to avoid circular

    totals = base_qs.aggregate(
        total_eligible=Sum("eligible_amount", default=Decimal("0.00")),
        total_advantage=Sum("advantage_amount", default=Decimal("0.00")),
        donation_count=Count("id"),
        large_donation_count=Count("id", filter=Q(amount__gte=Decimal("10000.00"))),
    )

    # Separate query: sum eligible_amount only for donations that have an
    # issued receipt, using EXISTS (subquery) to avoid JOIN-based inflation.
    _has_issued = _Receipt.objects.filter(
        donation=OuterRef("pk"),
        status="issued",
    )
    total_receipted = (
        base_qs.filter(Exists(_has_issued))
        .aggregate(v=Sum("eligible_amount", default=Decimal("0.00")))["v"]
        or Decimal("0.00")
    )
    totals["total_receipted"] = total_receipted

    # ── By-campaign breakdown (1 query) ───────────────────────────────────────
    by_campaign_qs = (
        base_qs
        .values("campaign__name_en")
        .annotate(
            count=Count("id"),
            total_amount=Sum("amount"),
            eligible_amount=Sum("eligible_amount"),
        )
        .order_by("-total_amount")
    )
    by_campaign = [
        {
            "campaign_name": row["campaign__name_en"] or "General Fund",
            "count": row["count"],
            "total_amount": row["total_amount"] or Decimal("0.00"),
            "eligible_amount": row["eligible_amount"] or Decimal("0.00"),
        }
        for row in by_campaign_qs
    ]

    # ── By-month breakdown in Python (1 query) ────────────────────────────────
    # TruncMonth is DB-agnostic (works on both SQLite/tests and PostgreSQL/prod).
    from django.db.models.functions import TruncMonth
    month_rows = (
        base_qs
        .annotate(month_trunc=TruncMonth("created_at"))
        .values("month_trunc")
        .annotate(
            total_amount=Sum("amount"),
            eligible_amount=Sum("eligible_amount"),
            count=Count("id"),
        )
        .order_by("month_trunc")
    )
    by_month = [
        {
            "year": row["month_trunc"].year,
            "month": row["month_trunc"].month,
            "month_label": (
                f"{_cal.month_name[row['month_trunc'].month]} "
                f"{row['month_trunc'].year}"
            ),
            "total_amount": row["total_amount"] or Decimal("0.00"),
            "eligible_amount": row["eligible_amount"] or Decimal("0.00"),
            "donation_count": row["count"],
        }
        for row in month_rows
    ]

    # ── Receipts issued/cancelled in fiscal year (1 query) ────────────────────
    receipt_counts = OfficialDonationReceipt.objects.filter(
        issued_at__gte=fy_start_dt,
        issued_at__lt=fy_end_dt,
    ).aggregate(
        issued=Count("id", filter=Q(status="issued")),
        cancelled=Count("id", filter=Q(status="cancelled")),
        superseded=Count("id", filter=Q(status="superseded")),
    )

    logger.info(
        "reports.services.donations.get_t3010_preparatory_data "
        "fiscal_year_start=%s fiscal_year_end=%s donation_count=%s "
        "total_receipted=%s filing_deadline=%s",
        fiscal_year_start, fy_end,
        totals["donation_count"], totals["total_receipted"],
        filing_deadline,
    )

    return {
        "fiscal_year_start": fiscal_year_start,
        "fiscal_year_end": fy_end,
        "filing_deadline": filing_deadline,
        "total_receipted_donations": totals["total_receipted"],
        "total_eligible_amount": totals["total_eligible"],
        "total_advantage_amount": totals["total_advantage"],
        # total_donated = eligible + advantage (gross donation amount for all receipted gifts)
        "total_donated": totals["total_eligible"] + totals["total_advantage"],
        "donation_count": totals["donation_count"],
        "large_donation_count": totals["large_donation_count"],
        "receipts_issued_in_year": receipt_counts["issued"],
        "receipts_cancelled_in_year": receipt_counts["cancelled"],
        "receipts_superseded_in_year": receipt_counts["superseded"],
        "by_campaign": by_campaign,
        "by_month": by_month,
    }


def get_receipt_list_queryset(start: date, end: date):
    """
    Return a QuerySet of OfficialDonationReceipts issued during [start, end].

    Dates are interpreted as Toronto local calendar dates (same convention as
    get_reconciliation_queryset) and converted to UTC for ``issued_at`` filtering.

    The returned QS is annotated with ``campaign_name`` for use in the CSV
    export. Ordered by ``issued_at`` ascending (chronological).

    PIPEDA column whitelist: receipt_number (serial_number), receipt_date,
    eligible_amount, advantage_amount, campaign_name, status.

    DO NOT include: donor_legal_name, donor_address*, donor_city,
    donor_province, donor_postal_code, donor email. These fields must never
    appear in a streamed administrative export.
    """
    import pytz
    from datetime import datetime as dt
    from django.utils.timezone import make_aware
    from apps.payments.models import OfficialDonationReceipt

    toronto = pytz.timezone("America/Toronto")
    dt_start = make_aware(dt.combine(start, dt.min.time()), toronto)
    dt_end = make_aware(dt.combine(end + timedelta(days=1), dt.min.time()), toronto)

    return (
        OfficialDonationReceipt.objects.filter(
            issued_at__gte=dt_start,
            issued_at__lt=dt_end,
        )
        .select_related("donation__campaign")
        .order_by("issued_at")
    )


def compute_donations_snapshot(year: int, month: int) -> dict:
    """
    Compute the full donations aggregate dict for storage in ReportSnapshot.data.

    Called by the Celery Beat task. Must be idempotent.
    Decimal values are serialised to strings for JSONB storage.

    Returns a dict with key ``row_count`` (donation_count) consumed by the
    Celery task for monitoring.
    """
    summary = get_monthly_donation_summary(year, month)

    snapshot = {
        "year": year,
        "month": month,
        "donations": _str_decimals({
            "total_donations": summary["total_donations"],
            "total_eligible_amount": summary["total_eligible_amount"],
            "total_advantage_amount": summary["total_advantage_amount"],
            "donation_count": summary["donation_count"],
            "unique_donor_count": summary["unique_donor_count"],
            "recurring_count": summary["recurring_count"],
            "one_time_count": summary["one_time_count"],
            "average_donation": summary["average_donation"],
            "by_campaign": summary["by_campaign"],
        }),
        "receipts": summary["receipt_summary"],
        "row_count": summary["donation_count"],
    }

    logger.info(
        "reports.services.donations.compute_donations_snapshot year=%s month=%s "
        "donation_count=%s",
        year, month, summary["donation_count"],
    )

    return snapshot
