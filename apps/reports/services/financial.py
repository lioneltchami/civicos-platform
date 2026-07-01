"""
Financial report query functions.

All functions return plain dicts suitable for template context or JSON serialisation.
No personal information is ever included in return values — amounts, counts, and
fee codes only.

Implemented in Wave 2.
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

logger = logging.getLogger("apps.reports.services.financial")


def get_monthly_revenue(year: int, month: int) -> dict:
    """
    Return fee payment revenue aggregates for a given calendar month.

    Keys:
        gross_revenue, processor_fees, net_revenue, tax_collected,
        fee_payment_count, rows (list of per-fee_code breakdown dicts)

    PIPEDA: no payer names, emails, or addresses in any returned value.
    """
    raise NotImplementedError("Implemented in Wave 2")


def get_refund_summary(year: int, month: int) -> dict:
    """
    Return refund aggregates for a given calendar month.

    Keys:
        refund_total, refund_count, refund_rate_pct, by_reason (list of dicts)

    Only rows with gateway_status != GATEWAY_STATUS_FAILED are counted
    (mirrors RefundForm and Refund.clean() logic).
    """
    raise NotImplementedError("Implemented in Wave 2")


def get_failed_payments(year: int, month: int) -> dict:
    """
    Return failed PaymentIntent counts and attempted values for a calendar month.

    Keys:
        failed_count, failed_value, by_error_code (list of dicts)

    No payer PII — counts and amounts only.
    """
    raise NotImplementedError("Implemented in Wave 2")


def get_reconciliation_queryset(start: date, end: date):
    """
    Return a QuerySet of PaymentIntents in [start, end] for streaming export.

    Max range: 92 days (enforced by the view) to prevent unbounded queries.
    Columns: reference, status, amount_paid, refund_total, net, fee_code, created_at.
    No payer name/email — reference + amounts only.
    """
    raise NotImplementedError("Implemented in Wave 2")


def compute_financial_snapshot(year: int, month: int) -> dict:
    """
    Compute the full financial aggregate dict for storage in ReportSnapshot.data.

    Called by the Celery Beat task. Must be idempotent.
    """
    raise NotImplementedError("Implemented in Wave 3 (Celery task completion)")
