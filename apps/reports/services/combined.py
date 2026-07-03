"""
Combined non-profit impact service — volunteer hours + donation giving.

Provides a unified view of organisational community value for CRA T3010
reporting: volunteer time (Schedule 2) and eligible donations (line 4500).

All imports from apps.volunteers.* are deferred to function bodies to
prevent circular import chains at Django startup.

PIPEDA: no volunteer or donor PII returned — economic aggregates only.
"""
from __future__ import annotations

import logging
from decimal import Decimal

logger = logging.getLogger("apps.reports.services.combined")


def combined_nonprofit_impact(year: int) -> dict:
    """
    Return combined volunteer + donation impact for a full calendar year.

    Each data source is fetched independently. If either source fails, it
    is replaced with zero-valued defaults so the view always renders.

    Returns:
    {
        "year": int,
        "volunteer": {
            "total_approved_hours": Decimal,
            "estimated_value_cad":  Decimal,
            "volunteer_count":      int,
            "hourly_rate":          Decimal,
            "province":             str,
        },
        "donations": {
            "total_donations":       Decimal,
            "total_eligible_amount": Decimal,
            "donation_count":        int,
            "unique_donor_count":    int,
            "receipts_issued":       int,
        },
        "combined_value_cad": Decimal,  # volunteer value + eligible donations
        "t3010_notes": str,
    }

    CRA context:
      volunteer.estimated_value_cad  → T3010 Schedule 2 (volunteer labour value)
      donations.total_eligible_amount → T3010 line 4500 (eligible donations)
      combined_value_cad             → total community economic value

    PIPEDA: no volunteer or donor names, emails, or addresses.
    """
    # ── Volunteer impact ──────────────────────────────────────────────────────
    _volunteer_zero = {
        "total_approved_hours": Decimal("0.00"),
        "estimated_value_cad": Decimal("0.00"),
        "volunteer_count": 0,
        "hourly_rate": Decimal("0.00"),
        "province": "ON",
    }
    try:
        from apps.volunteers.services.reporting import impact_value
        vol_raw = impact_value(year)
        volunteer = {
            "total_approved_hours": vol_raw["total_approved_hours"],
            "estimated_value_cad": vol_raw["estimated_value_cad"],
            "volunteer_count": vol_raw["volunteer_count"],
            "hourly_rate": vol_raw["hourly_rate"],
            "province": vol_raw["province"],
        }
    except Exception:
        logger.warning(
            "reports.services.combined.combined_nonprofit_impact.volunteer_failed "
            "year=%s — returning zeros",
            year,
        )
        volunteer = _volunteer_zero

    # ── Donation summary ──────────────────────────────────────────────────────
    _donations_zero = {
        "total_donations": Decimal("0.00"),
        "total_eligible_amount": Decimal("0.00"),
        "donation_count": 0,
        "unique_donor_count": 0,
        "receipts_issued": 0,
    }
    try:
        from apps.reports.services.donations import get_annual_donation_summary
        don_raw = get_annual_donation_summary(year)
        donations = {
            "total_donations": don_raw["total_donations"],
            "total_eligible_amount": don_raw["total_eligible_amount"],
            "donation_count": don_raw["donation_count"],
            "unique_donor_count": don_raw["unique_donor_count"],
            "receipts_issued": don_raw["receipts_issued"],
        }
    except Exception:
        logger.warning(
            "reports.services.combined.combined_nonprofit_impact.donations_failed "
            "year=%s — returning zeros",
            year,
        )
        donations = _donations_zero

    combined_value_cad = (
        volunteer["estimated_value_cad"] + donations["total_eligible_amount"]
    )

    logger.info(
        "reports.services.combined.combined_nonprofit_impact "
        "year=%s combined_value_cad=%s volunteer_hours=%s donation_eligible=%s",
        year,
        combined_value_cad,
        volunteer["total_approved_hours"],
        donations["total_eligible_amount"],
    )

    return {
        "year": year,
        "volunteer": volunteer,
        "donations": donations,
        "combined_value_cad": combined_value_cad,
        "t3010_notes": (
            "T3010 Schedule 2 (volunteers) + line 4500 (donations eligible amount)"
        ),
    }
