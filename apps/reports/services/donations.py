"""
Donations & CRA compliance report query functions.

All aggregates are at campaign/month level — no individual donor PII is returned
by any function in this module.

CRA T3010 context: Canadian charities must file an annual information return
within 6 months of fiscal year end. This module provides the preparatory data
(totals, eligible amounts, ≥$10K donors by charity number) needed to complete
Schedule 1 and Schedule 4 of T3010.

Implemented in Wave 3.
"""
from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger("apps.reports.services.donations")


def get_monthly_donation_summary(year: int, month: int) -> dict:
    """
    Return donation aggregates for a given calendar month.

    Keys:
        total_donations, total_receipted_amount, total_eligible_amount,
        receipt_count, campaign_count, by_campaign (list of dicts with
        campaign_name, count, total_amount, receipted_amount)

    PIPEDA: no donor names, addresses, or emails in any returned value.
    """
    raise NotImplementedError("Implemented in Wave 3")


def get_annual_donation_summary(year: int) -> dict:
    """
    Return annual donation aggregates for a full calendar year.

    Used by the T3010 prep report. Keys:
        total_donations, total_receipted_amount, total_eligible_amount,
        receipt_count, large_donation_count (≥$10K), by_month (list of dicts)

    PIPEDA: no donor PII — amounts and counts only.
    CRA T3010 line 4500: total receipted donations for the year.
    CRA T3010 Schedule 4: individual donations ≥$10K (donor charity number required,
        not name/address — only applies to gifts from other charities, not individuals).
    """
    raise NotImplementedError("Implemented in Wave 3")


def get_t3010_preparatory_data(fiscal_year_end: date) -> dict:
    """
    Compile the data a charity needs to complete CRA T3010.

    Covers the 12-month fiscal year ending on ``fiscal_year_end``.

    Keys:
        fiscal_year_start, fiscal_year_end,
        total_receipted_donations,      # T3010 line 4500
        total_eligible_amount,          # T3010 line 4510
        receipt_count,
        receipts_issued_in_year,        # may differ from donations in year
        large_donations (list of dicts with amount, charity_registration_number)
            — ONLY applies to gifts from other registered charities (CRA Schedule 4)
            — for individual donors, Schedule 4 is NOT required
        by_campaign (list of dicts)
        filing_deadline,                # 6 months after fiscal_year_end

    PIPEDA / CRA note:
        Individual donor names and addresses are not included here.
        T3010 itself does not require individual donor disclosure for gifts under
        $10K from individuals. This preparatory export gives finance staff the
        aggregate figures needed to complete the form without exposing PII.
    """
    raise NotImplementedError("Implemented in Wave 3")


def get_receipt_list_queryset(start: date, end: date):
    """
    Return a QuerySet of OfficialDonationReceipts issued in [start, end].

    Used for streaming CSV/Excel export. Columns: receipt_number, issued_date,
    amount_receipted, eligible_amount, campaign_name.

    PIPEDA column whitelist: NO donor_name_snapshot, donor_address_snapshot, or
    donor_email_snapshot. These fields must never appear in a streamed export.
    Receipt number is the only donor-linkable field; its presence is required by
    CRA regulations (receipts must reference a number the donor received).
    """
    raise NotImplementedError("Implemented in Wave 3")


def compute_donations_snapshot(year: int, month: int) -> dict:
    """
    Compute the full donations aggregate dict for storage in ReportSnapshot.data.

    Called by the Celery Beat task. Must be idempotent.
    """
    raise NotImplementedError("Implemented in Wave 3")
