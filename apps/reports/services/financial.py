"""
Financial report service — Wave 2.

Provides aggregated financial metrics for the Analytics & Reporting BB.
All queries operate on the Payments BB models (Payment, Refund, PaymentIntent,
ServiceFeePayment). No PII is returned — amounts, counts, and fee codes only.

PIPEDA invariants:
- No payer name, email, or address is ever returned.
- Amounts are Decimal (CAD dollars); DecimalField values are already in dollar
  units with 2 d.p. (no cents-to-dollars conversion needed).
- Logs include year/month/count only — never individual payment references.

Currency: CAD only.
"""
from __future__ import annotations

import calendar
import logging
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Q, Sum
from django.db.models.functions import Coalesce

logger = logging.getLogger("apps.reports.services.financial")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _month_utc_range(year: int, month: int) -> tuple[datetime, datetime]:
    """
    Return a half-open UTC datetime interval [start, end) for the given calendar
    month — suitable for ``__gte`` / ``__lt`` filtering on DateTimeField columns.

    Using an exclusive upper bound avoids microsecond / leap-second edge-cases
    that affect ``__lte`` datetime filtering.

    ⚠️  UTC vs. Toronto asymmetry:
    This function uses UTC midnight boundaries, so a payment captured at
    23:30 Toronto time (= 03:30 UTC next day) falls in the *following* UTC
    month.  This is intentional for revenue aggregation — the gateway
    settles in UTC.

    ``get_reconciliation_queryset()`` deliberately uses the *opposite*
    convention: it converts ``start``/``end`` calendar dates to
    America/Toronto local midnight, so operators see payments by the local
    business date their customers paid.  Never mix the two for the same
    report without documenting the boundary choice.
    """
    month_start = datetime(year, month, 1, tzinfo=dt_timezone.utc)
    if month == 12:
        month_end = datetime(year + 1, 1, 1, tzinfo=dt_timezone.utc)
    else:
        month_end = datetime(year, month + 1, 1, tzinfo=dt_timezone.utc)
    return month_start, month_end


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

def get_monthly_revenue(year: int, month: int) -> dict:
    """
    Aggregate captured payment revenue for the given calendar month.

    Filters on ``Payment.paid_at`` UTC timestamps. Includes all payment
    purposes (service fees, donations, fines) — each is identified in the
    ``by_fee_code`` breakdown by its fee_code or purpose label.

    Returns:
    {
        "total_gross":          Decimal,  # sum of amount_paid
        "total_net":            Decimal,  # sum of net_amount (gross - processor_fee)
        "total_tax":            Decimal,  # sum of intent.tax_amount
        "total_processor_fees": Decimal,  # sum of processor_fee
        "payment_count":        int,
        "by_fee_code": {
            label: {
                "gross":          Decimal,
                "net":            Decimal,
                "tax":            Decimal,
                "processor_fees": Decimal,
                "count":          int,
            }
        }
    }
    """
    from apps.payments.models import Payment

    month_start, month_end = _month_utc_range(year, month)

    # ── Per-label breakdown (single query) ───────────────────────────────────
    # Use service_fee_payment.fee_code when present; fall back to intent.purpose
    # so donations / fines appear as "donation" / "fine" rows.
    # Totals are derived in Python from the breakdown to avoid a second DB hit.
    by_fee_code_qs = (
        Payment.objects.filter(
            paid_at__gte=month_start,
            paid_at__lt=month_end,
        )
        .annotate(
            label=Coalesce(
                "intent__service_fee_payment__fee_code",
                "intent__purpose",
            )
        )
        .values("label")
        .annotate(
            gross=Sum("amount_paid"),
            net=Sum("net_amount"),
            tax=Sum("intent__tax_amount"),
            processor_fees=Sum("processor_fee"),
            count=Count("id"),
        )
        .order_by("label")
    )

    by_fee_code: dict[str, dict] = {}
    total_gross = Decimal("0.00")
    total_net = Decimal("0.00")
    total_tax = Decimal("0.00")
    total_processor_fees = Decimal("0.00")
    payment_count = 0
    for row in by_fee_code_qs:
        label = row["label"] or "other"
        g = row["gross"] or Decimal("0.00")
        n = row["net"] or Decimal("0.00")
        t = row["tax"] or Decimal("0.00")
        pf = row["processor_fees"] or Decimal("0.00")
        cnt = row["count"]
        by_fee_code[label] = {"gross": g, "net": n, "tax": t, "processor_fees": pf, "count": cnt}
        total_gross += g
        total_net += n
        total_tax += t
        total_processor_fees += pf
        payment_count += cnt

    logger.debug(
        "reports.services.financial.get_monthly_revenue year=%s month=%s "
        "payment_count=%s total_gross=%s",
        year,
        month,
        payment_count,
        total_gross,
    )

    return {
        "total_gross": total_gross,
        "total_net": total_net,
        "total_tax": total_tax,
        "total_processor_fees": total_processor_fees,
        "payment_count": payment_count,
        "by_fee_code": by_fee_code,
    }


def get_refund_summary(year: int, month: int) -> dict:
    """
    Aggregate completed refunds processed during the given calendar month.

    Filters on ``Refund.refunded_at`` UTC timestamps and
    ``gateway_status == 'succeeded'`` to exclude pending / failed refunds.

    Returns:
    {
        "total_refunded": Decimal,
        "refund_count":   int,
        "by_reason": {
            reason_code: {"amount": Decimal, "count": int}
        }
    }
    """
    from apps.payments.models import Refund

    month_start, month_end = _month_utc_range(year, month)

    # ── Per-reason breakdown (single query) ───────────────────────────────────
    # Totals are derived in Python from the breakdown to avoid a second DB hit.
    by_reason_qs = (
        Refund.objects.filter(
            refunded_at__gte=month_start,
            refunded_at__lt=month_end,
            gateway_status=Refund.GATEWAY_STATUS_SUCCEEDED,
        )
        .values("reason")
        .annotate(amount=Sum("amount"), count=Count("id"))
        .order_by("reason")
    )

    by_reason: dict[str, dict] = {}
    total_refunded = Decimal("0.00")
    refund_count = 0
    for row in by_reason_qs:
        reason = row["reason"] or "other"
        a = row["amount"] or Decimal("0.00")
        cnt = row["count"]
        by_reason[reason] = {"amount": a, "count": cnt}
        total_refunded += a
        refund_count += cnt

    return {
        "total_refunded": total_refunded,
        "refund_count": refund_count,
        "by_reason": by_reason,
    }


def get_failed_payments(year: int, month: int):
    """
    Return a QuerySet of failed PaymentIntents for the given month.

    Scoped to service-fee payments (PURPOSE_SERVICE_FEE) only. Donation
    failures are covered in the Wave 3 donations service.

    ⚠️  Do NOT use this count as a total "failed payments" figure — it
    deliberately excludes donation intents. The dashboard KPI card label
    reflects this scope ("Failed Service-Fee Payments").

    Returns a QuerySet (unevaluated) so callers can chain ``.count()``
    cheaply or iterate without loading all rows.
    """
    from apps.payments.models import PaymentIntent

    month_start, month_end = _month_utc_range(year, month)

    return (
        PaymentIntent.objects.filter(
            status=PaymentIntent.STATUS_FAILED,
            created_at__gte=month_start,
            created_at__lt=month_end,
            purpose=PaymentIntent.PURPOSE_SERVICE_FEE,
        )
        .select_related("service_fee_payment")
        .order_by("-created_at")
    )


def get_reconciliation_queryset(start, end):
    """
    Return an annotated Payment QuerySet for the inclusive date range [start, end].

    Dates are interpreted as Toronto local calendar dates and converted to UTC
    for the ``paid_at`` filter so DST is handled correctly.

    Annotations added to each Payment object:
    - ``refund_total``: sum of succeeded Refund amounts for that payment
    - ``fee_code``:     service_fee_payment.fee_code when present,
                        else intent.purpose as a fallback label

    Net (amount_paid - refund_total) is intentionally computed in Python by the
    caller to avoid Django's dependent-annotation ordering ambiguity.
    """
    from datetime import date as date_type, datetime as dt, timedelta

    import pytz
    from django.utils.timezone import make_aware

    from apps.payments.models import Payment, Refund

    toronto = pytz.timezone("America/Toronto")
    dt_start = make_aware(dt.combine(start, dt.min.time()), toronto)
    # Exclusive upper bound: midnight at start of the day AFTER end in Toronto.
    dt_end = make_aware(
        dt.combine(end + timedelta(days=1), dt.min.time()), toronto
    )

    return (
        Payment.objects.filter(
            paid_at__gte=dt_start,
            paid_at__lt=dt_end,
        )
        .select_related("intent__service_fee_payment")
        .annotate(
            refund_total=Sum(
                "refunds__amount",
                filter=Q(refunds__gateway_status=Refund.GATEWAY_STATUS_SUCCEEDED),
                default=Decimal("0.00"),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            fee_code=Coalesce(
                "intent__service_fee_payment__fee_code",
                "intent__purpose",
            ),
        )
        .order_by("paid_at")
    )


def compute_financial_snapshot(year: int, month: int) -> dict:
    """
    Compute the full financial snapshot dict for (year, month).

    Combines revenue and refund summaries into the JSON structure stored in
    ``ReportSnapshot.data``. Decimal values are serialised to strings so the
    JSONField can store them without type loss.

    Called by:
    - ``apps.reports.tasks._compute_all_snapshots`` (Celery Beat, nightly)
    - ``apps.reports.tasks.recompute_snapshot`` (on-demand admin action)

    Raises on error — the Celery task wraps this in try/except.
    """
    revenue = get_monthly_revenue(year, month)
    refunds = get_refund_summary(year, month)

    def _str_decimals(d: dict) -> dict:
        """Recursively convert Decimal → str for JSON storage."""
        out = {}
        for k, v in d.items():
            if isinstance(v, Decimal):
                out[k] = str(v)
            elif isinstance(v, dict):
                out[k] = _str_decimals(v)
            else:
                out[k] = v
        return out

    snapshot = {
        "year": year,
        "month": month,
        "revenue": _str_decimals({
            "total_gross": revenue["total_gross"],
            "total_net": revenue["total_net"],
            "total_tax": revenue["total_tax"],
            "total_processor_fees": revenue["total_processor_fees"],
            "payment_count": revenue["payment_count"],
            "by_fee_code": revenue["by_fee_code"],
        }),
        "refunds": _str_decimals({
            "total_refunded": refunds["total_refunded"],
            "refund_count": refunds["refund_count"],
            "by_reason": refunds["by_reason"],
        }),
        # row_count is used by the Celery task for monitoring
        "row_count": revenue["payment_count"],
    }

    logger.info(
        "reports.services.financial.compute_financial_snapshot year=%s month=%s "
        "payment_count=%s total_gross=%s",
        year,
        month,
        revenue["payment_count"],
        revenue["total_gross"],
    )

    return snapshot
