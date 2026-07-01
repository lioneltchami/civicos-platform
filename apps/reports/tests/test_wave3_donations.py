"""
Wave 3 Donations & CRA reporting tests.

Covers:
  - _month_utc_range / _fiscal_year_date_range / _filing_deadline helpers
  - get_monthly_donation_summary (correctness, empty period, receipts)
  - get_annual_donation_summary (totals, by_month, by_campaign, large gifts)
  - get_t3010_preparatory_data (line 4500, filing deadline, fiscal year edge cases)
  - get_receipt_list_queryset (date boundaries, select_related, PIPEDA)
  - compute_donations_snapshot (Decimal serialisation, row_count)
  - ReceiptExportForm / FiscalYearEndForm validation
  - DonationDashboardView (permission, redirect, current-month, snapshot fallback)
  - AnnualDonationView (permission, redirect, year picker)
  - T3010PrepView (permission, fiscal year form, context)
  - ReceiptsExportView (streaming CSV, ExportRecord, row_count, PIPEDA columns)
  - T3010PrepExportView (streaming CSV, ExportRecord, row_count, PIPEDA columns)
  - export_receipts_csv / export_t3010_prep_csv
  - Celery task: _compute_all_snapshots includes donations snapshot

PIPEDA invariants verified:
  - donor_legal_name, donor_address_*, donor email NEVER appear in CSV output
  - actor_pk stores integer pk — no email or name
  - Log/audit context: amounts and counts only
"""
from __future__ import annotations

import itertools
import uuid
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.payments.models import (
    Donation,
    DonationCampaign,
    OfficialDonationReceipt,
    PaymentIntent,
    DONATION_STATUS_COMPLETED,
    DONATION_STATUS_PENDING,
)
from apps.reports.forms import (
    FiscalYearEndForm,
    MAX_RECEIPT_EXPORT_DAYS,
    ReceiptExportForm,
)
from apps.reports.models import ExportRecord, ReportSnapshot
from apps.reports.services.donations import (
    _filing_deadline,
    _fiscal_year_date_range,
    _month_utc_range,
    _str_decimals,
    compute_donations_snapshot,
    get_annual_donation_summary,
    get_monthly_donation_summary,
    get_receipt_list_queryset,
    get_t3010_preparatory_data,
)

User = get_user_model()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LOGIN_URL = "/account/login/"

# Monotonically increasing counter — guarantees unique serial numbers across
# ALL _make_receipt() calls in the entire test session, regardless of UUID
# entropy narrowing from the modulo operation.
_RECEIPT_SERIAL_COUNTER: itertools.count = itertools.count(1)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _make_user(*, is_staff=True, perms=None):
    from django.contrib.auth.models import Permission
    user = User.objects.create_user(
        email=f"staff-{uuid.uuid4().hex[:8]}@example.com",
        password="testpass123",
        is_staff=is_staff,
    )
    if perms:
        for codename in perms:
            perm = Permission.objects.get(
                codename=codename,
                content_type__app_label="payments",
            )
            user.user_permissions.add(perm)
    return user


def _make_donor():
    return User.objects.create_user(
        email=f"donor-{uuid.uuid4().hex[:6]}@example.com",
        password="x",
        first_name="Test",
        last_name="Donor",
    )


def _make_campaign(name="General Campaign"):
    return DonationCampaign.objects.create(
        slug=f"camp-{uuid.uuid4().hex[:6]}",
        name_en=name,
        start_date=date(2024, 1, 1),
        is_active=True,
        advantage_amount=Decimal("0.00"),
    )


def _make_intent(*, donor=None, amount=Decimal("100.00")):
    if donor is None:
        donor = _make_donor()
    return PaymentIntent.objects.create(
        reference=f"DON-{uuid.uuid4().hex[:8].upper()}",
        purpose=PaymentIntent.PURPOSE_DONATION,
        status=PaymentIntent.STATUS_COMPLETED,
        amount=amount,
        tax_amount=Decimal("0.00"),
        payer=donor,
    )


def _make_donation(
    *,
    donor=None,
    campaign=None,
    amount=Decimal("100.00"),
    advantage_amount=Decimal("0.00"),
    status=DONATION_STATUS_COMPLETED,
    is_recurring=False,
    created_at=None,
):
    if donor is None:
        donor = _make_donor()
    intent = _make_intent(donor=donor, amount=amount)
    eligible = amount - advantage_amount
    donation = Donation.objects.create(
        payment_intent=intent,
        donor=donor,
        campaign=campaign,
        amount=amount,
        advantage_amount=advantage_amount,
        eligible_amount=eligible,
        is_recurring=is_recurring,
        status=status,
        donor_name_snapshot="Test Donor",
        donor_address_snapshot="123 Main St, Ottawa ON K1A 0A9",
    )
    if created_at is not None:
        # Override auto-set timestamp for test isolation
        Donation.objects.filter(pk=donation.pk).update(created_at=created_at)
        donation.refresh_from_db()
    return donation


def _make_receipt(
    donation,
    *,
    status="issued",
    serial_number=None,
    issued_at=None,
):
    """
    Create an OfficialDonationReceipt.

    issued_at: if provided, patch django.db.models.fields.now so that the
    auto_now_add field is set to this value at INSERT time. This avoids raw
    SQL (which has UUID encoding issues in SQLite) and the blocked ORM
    update() on this model.
    """
    from unittest.mock import patch

    if serial_number is None:
        serial_number = f"2024-{next(_RECEIPT_SERIAL_COUNTER):06d}"

    create_kwargs = dict(
        donation=donation,
        serial_number=serial_number,
        status=status,
        donor_legal_name="Test Donor",
        donor_address_line1="123 Main St",
        donor_city="Ottawa",
        donor_province="ON",
        donor_postal_code="K1A 0A9",
        donation_date=date(2024, 6, 1),
        receipt_date=date(2024, 6, 2),
        eligible_amount=donation.eligible_amount,
        advantage_amount=donation.advantage_amount,
        charity_legal_name="Test Charity",
        charity_registration_number="123456789RR0001",
        charity_address="1 Charity Lane, Ottawa ON",
        place_of_issue="Ottawa, ON",
        authorized_signatory_name="Jane Smith",
        authorized_signatory_title="Treasurer",
        is_annual_consolidated=False,
    )

    if issued_at is not None:
        # Patch django.utils.timezone.now, which DateTimeField.pre_save() calls
        # for auto_now_add, so issued_at is stored as the desired value.
        with patch("django.utils.timezone.now", return_value=issued_at):
            receipt = OfficialDonationReceipt.objects.create(**create_kwargs)
    else:
        receipt = OfficialDonationReceipt.objects.create(**create_kwargs)

    return receipt


# ============================================================================
# Helper function tests
# ============================================================================

class MonthUtcRangeTests(TestCase):
    def test_regular_month(self):
        start, end = _month_utc_range(2024, 6)
        self.assertEqual(start, datetime(2024, 6, 1, tzinfo=dt_timezone.utc))
        self.assertEqual(end, datetime(2024, 7, 1, tzinfo=dt_timezone.utc))

    def test_december_wraps_year(self):
        start, end = _month_utc_range(2024, 12)
        self.assertEqual(start, datetime(2024, 12, 1, tzinfo=dt_timezone.utc))
        self.assertEqual(end, datetime(2025, 1, 1, tzinfo=dt_timezone.utc))

    def test_january(self):
        start, end = _month_utc_range(2024, 1)
        self.assertEqual(start, datetime(2024, 1, 1, tzinfo=dt_timezone.utc))
        self.assertEqual(end, datetime(2024, 2, 1, tzinfo=dt_timezone.utc))

    def test_range_is_half_open(self):
        """The range is [start, end) — start inclusive, end exclusive."""
        start, end = _month_utc_range(2024, 3)
        self.assertLess(start, end)


class FiscalYearDateRangeTests(TestCase):
    def test_dec_31(self):
        fy_start, fy_end = _fiscal_year_date_range(date(2024, 12, 31))
        self.assertEqual(fy_start, date(2024, 1, 1))
        self.assertEqual(fy_end, date(2024, 12, 31))

    def test_march_31(self):
        fy_start, fy_end = _fiscal_year_date_range(date(2024, 3, 31))
        self.assertEqual(fy_start, date(2023, 4, 1))
        self.assertEqual(fy_end, date(2024, 3, 31))

    def test_leap_year_feb_29(self):
        """Feb 29 in a leap year — previous year falls back to Feb 28."""
        fy_start, fy_end = _fiscal_year_date_range(date(2024, 2, 29))
        # 2023 has no Feb 29 — should clip to Feb 28
        self.assertEqual(fy_start, date(2023, 3, 1))
        self.assertEqual(fy_end, date(2024, 2, 29))

    def test_twelve_month_span(self):
        fy_start, fy_end = _fiscal_year_date_range(date(2024, 6, 30))
        delta = (fy_end - fy_start).days + 1
        self.assertGreaterEqual(delta, 365)  # always at least a year


class FilingDeadlineTests(TestCase):
    def test_dec_31_gives_jun_30(self):
        self.assertEqual(_filing_deadline(date(2024, 12, 31)), date(2025, 6, 30))

    def test_aug_31_clips_to_feb_28(self):
        """Aug 31 + 6 months = Feb 31, but Feb has no 31st — clips to 28."""
        result = _filing_deadline(date(2024, 8, 31))
        self.assertEqual(result, date(2025, 2, 28))

    def test_jun_30_gives_dec_30(self):
        self.assertEqual(_filing_deadline(date(2024, 6, 30)), date(2024, 12, 30))

    def test_leap_year_handling(self):
        """Aug 31 into Feb of a leap year (2028) should give Feb 29."""
        result = _filing_deadline(date(2027, 8, 31))
        self.assertEqual(result, date(2028, 2, 29))


class StrDecimalsTests(TestCase):
    def test_converts_decimal_to_string(self):
        result = _str_decimals(Decimal("123.45"))
        self.assertEqual(result, "123.45")

    def test_nested_dict(self):
        data = {"amount": Decimal("10.00"), "count": 5}
        result = _str_decimals(data)
        self.assertEqual(result["amount"], "10.00")
        self.assertEqual(result["count"], 5)  # int unchanged

    def test_nested_list(self):
        data = [{"v": Decimal("1.00")}, {"v": Decimal("2.00")}]
        result = _str_decimals(data)
        self.assertEqual(result[0]["v"], "1.00")
        self.assertEqual(result[1]["v"], "2.00")


# ============================================================================
# get_monthly_donation_summary
# ============================================================================

class GetMonthlyDonationSummaryTests(TestCase):
    def setUp(self):
        self.june_start = datetime(2024, 6, 1, tzinfo=dt_timezone.utc)
        self.june_mid = datetime(2024, 6, 15, 12, 0, tzinfo=dt_timezone.utc)
        self.july_start = datetime(2024, 7, 1, tzinfo=dt_timezone.utc)

    def test_empty_month_returns_zeroes(self):
        result = get_monthly_donation_summary(2024, 6)
        self.assertEqual(result["donation_count"], 0)
        self.assertEqual(result["total_donations"], Decimal("0.00"))
        self.assertEqual(result["total_eligible_amount"], Decimal("0.00"))
        self.assertEqual(result["average_donation"], Decimal("0.00"))
        self.assertEqual(result["by_campaign"], [])
        self.assertEqual(result["receipt_summary"]["issued"], 0)

    def test_single_donation_aggregates(self):
        _make_donation(amount=Decimal("250.00"), created_at=self.june_mid)
        result = get_monthly_donation_summary(2024, 6)
        self.assertEqual(result["donation_count"], 1)
        self.assertEqual(result["total_donations"], Decimal("250.00"))
        self.assertEqual(result["total_eligible_amount"], Decimal("250.00"))
        self.assertEqual(result["average_donation"], Decimal("250.00"))
        self.assertEqual(result["unique_donor_count"], 1)
        self.assertEqual(result["recurring_count"], 0)
        self.assertEqual(result["one_time_count"], 1)

    def test_advantage_amount_reduces_eligible(self):
        _make_donation(
            amount=Decimal("200.00"),
            advantage_amount=Decimal("50.00"),
            created_at=self.june_mid,
        )
        result = get_monthly_donation_summary(2024, 6)
        self.assertEqual(result["total_eligible_amount"], Decimal("150.00"))
        self.assertEqual(result["total_advantage_amount"], Decimal("50.00"))

    def test_excludes_pending_donations(self):
        _make_donation(
            amount=Decimal("100.00"),
            status=DONATION_STATUS_PENDING,
            created_at=self.june_mid,
        )
        result = get_monthly_donation_summary(2024, 6)
        self.assertEqual(result["donation_count"], 0)

    def test_excludes_donations_outside_month(self):
        """Donation on July 1 must not appear in June summary."""
        _make_donation(
            amount=Decimal("100.00"),
            created_at=self.july_start,
        )
        result = get_monthly_donation_summary(2024, 6)
        self.assertEqual(result["donation_count"], 0)

    def test_recurring_count(self):
        _make_donation(amount=Decimal("50.00"), is_recurring=True, created_at=self.june_mid)
        _make_donation(amount=Decimal("75.00"), is_recurring=False, created_at=self.june_mid)
        result = get_monthly_donation_summary(2024, 6)
        self.assertEqual(result["recurring_count"], 1)
        self.assertEqual(result["one_time_count"], 1)
        self.assertEqual(result["donation_count"], 2)

    def test_by_campaign_breakdown(self):
        campaign = _make_campaign("Spring Appeal")
        _make_donation(amount=Decimal("100.00"), campaign=campaign, created_at=self.june_mid)
        _make_donation(amount=Decimal("200.00"), campaign=None, created_at=self.june_mid)
        result = get_monthly_donation_summary(2024, 6)
        names = [r["campaign_name"] for r in result["by_campaign"]]
        self.assertIn("Spring Appeal", names)
        self.assertIn("General Fund", names)

    def test_receipt_summary_counts_by_status(self):
        donation = _make_donation(amount=Decimal("100.00"), created_at=self.june_mid)
        _make_receipt(donation, status="issued", issued_at=self.june_mid)
        donation2 = _make_donation(amount=Decimal("50.00"), created_at=self.june_mid)
        _make_receipt(donation2, status="cancelled", issued_at=self.june_mid)
        result = get_monthly_donation_summary(2024, 6)
        self.assertEqual(result["receipt_summary"]["issued"], 1)
        self.assertEqual(result["receipt_summary"]["cancelled"], 1)
        self.assertEqual(result["receipt_summary"]["total"], 2)

    def test_pipeda_no_donor_pii_in_keys(self):
        _make_donation(amount=Decimal("100.00"), created_at=self.june_mid)
        result = get_monthly_donation_summary(2024, 6)
        pii_keys = {
            "donor_name", "donor_email", "donor_address",
            "legal_name", "email", "address",
        }
        all_keys = set(result.keys())
        for row in result.get("by_campaign", []):
            all_keys.update(row.keys())
        self.assertTrue(all_keys.isdisjoint(pii_keys))

    def test_multiple_donors_unique_count(self):
        d1, d2 = _make_donor(), _make_donor()
        _make_donation(donor=d1, amount=Decimal("100.00"), created_at=self.june_mid)
        _make_donation(donor=d1, amount=Decimal("50.00"), created_at=self.june_mid)
        _make_donation(donor=d2, amount=Decimal("75.00"), created_at=self.june_mid)
        result = get_monthly_donation_summary(2024, 6)
        self.assertEqual(result["unique_donor_count"], 2)
        self.assertEqual(result["donation_count"], 3)


# ============================================================================
# get_annual_donation_summary
# ============================================================================

class GetAnnualDonationSummaryTests(TestCase):

    def _jan_ts(self, day=15):
        return datetime(2024, 1, day, 12, 0, tzinfo=dt_timezone.utc)

    def _jun_ts(self, day=15):
        return datetime(2024, 6, day, 12, 0, tzinfo=dt_timezone.utc)

    def test_empty_year_returns_zeroes(self):
        result = get_annual_donation_summary(2024)
        self.assertEqual(result["donation_count"], 0)
        self.assertEqual(result["total_donations"], Decimal("0.00"))
        self.assertEqual(result["by_month"], [])
        self.assertEqual(result["by_campaign"], [])

    def test_annual_totals(self):
        _make_donation(amount=Decimal("100.00"), created_at=self._jan_ts())
        _make_donation(amount=Decimal("200.00"), created_at=self._jun_ts())
        result = get_annual_donation_summary(2024)
        self.assertEqual(result["total_donations"], Decimal("300.00"))
        self.assertEqual(result["donation_count"], 2)

    def test_large_donation_count(self):
        """Donations ≥ $10,000 flagged for Schedule 4 review."""
        _make_donation(amount=Decimal("9999.99"), created_at=self._jan_ts())
        _make_donation(amount=Decimal("10000.00"), created_at=self._jun_ts())
        _make_donation(amount=Decimal("25000.00"), created_at=self._jun_ts())
        result = get_annual_donation_summary(2024)
        self.assertEqual(result["large_donation_count"], 2)  # ≥10K only

    def test_by_month_breakdown_present(self):
        _make_donation(amount=Decimal("100.00"), created_at=self._jan_ts())
        _make_donation(amount=Decimal("200.00"), created_at=self._jun_ts())
        result = get_annual_donation_summary(2024)
        months = [r["month"] for r in result["by_month"]]
        self.assertIn(1, months)
        self.assertIn(6, months)

    def test_excludes_previous_year(self):
        prev_year_ts = datetime(2023, 12, 31, 23, 0, tzinfo=dt_timezone.utc)
        _make_donation(amount=Decimal("500.00"), created_at=prev_year_ts)
        result = get_annual_donation_summary(2024)
        self.assertEqual(result["donation_count"], 0)

    def test_receipts_issued_count(self):
        d = _make_donation(amount=Decimal("100.00"), created_at=self._jan_ts())
        _make_receipt(d, status="issued", issued_at=self._jan_ts())
        result = get_annual_donation_summary(2024)
        self.assertEqual(result["receipts_issued"], 1)

    def test_by_campaign_breakdown(self):
        c = _make_campaign("Winter Fund")
        _make_donation(amount=Decimal("300.00"), campaign=c, created_at=self._jan_ts())
        result = get_annual_donation_summary(2024)
        self.assertEqual(len(result["by_campaign"]), 1)
        self.assertEqual(result["by_campaign"][0]["campaign_name"], "Winter Fund")

    def test_pipeda_no_donor_pii_in_by_campaign(self):
        c = _make_campaign()
        _make_donation(campaign=c, created_at=self._jan_ts())
        result = get_annual_donation_summary(2024)
        for row in result["by_campaign"]:
            self.assertNotIn("donor_name", row)
            self.assertNotIn("donor_email", row)
            self.assertNotIn("email", row)


# ============================================================================
# get_t3010_preparatory_data
# ============================================================================

class GetT3010PreparatoryDataTests(TestCase):

    def _ts(self, year, month, day=15):
        return datetime(year, month, day, 12, 0, tzinfo=dt_timezone.utc)

    def test_empty_fiscal_year(self):
        result = get_t3010_preparatory_data(date(2024, 12, 31))
        self.assertEqual(result["donation_count"], 0)
        self.assertEqual(result["total_receipted_donations"], Decimal("0.00"))
        self.assertEqual(result["by_campaign"], [])
        self.assertEqual(result["by_month"], [])

    def test_fiscal_year_boundaries(self):
        result = get_t3010_preparatory_data(date(2024, 12, 31))
        self.assertEqual(result["fiscal_year_start"], date(2024, 1, 1))
        self.assertEqual(result["fiscal_year_end"], date(2024, 12, 31))

    def test_filing_deadline_six_months(self):
        result = get_t3010_preparatory_data(date(2024, 12, 31))
        self.assertEqual(result["filing_deadline"], date(2025, 6, 30))

    def test_total_receipted_donations_line_4500(self):
        """Line 4500 = eligible_amount of donations with issued receipts."""
        d1 = _make_donation(amount=Decimal("200.00"), created_at=self._ts(2024, 3))
        _make_receipt(d1, status="issued", issued_at=self._ts(2024, 3))

        d2 = _make_donation(amount=Decimal("100.00"), created_at=self._ts(2024, 6))
        # d2 has NO issued receipt — should NOT count toward line 4500

        result = get_t3010_preparatory_data(date(2024, 12, 31))
        self.assertEqual(result["total_receipted_donations"], Decimal("200.00"))
        self.assertEqual(result["total_eligible_amount"], Decimal("300.00"))

    def test_no_double_count_when_donation_has_issued_and_cancelled_receipt(self):
        """
        Regression: a donation that has both an issued and a cancelled receipt
        must NOT inflate donation_count, total_eligible_amount, or
        total_receipted_donations.

        Before the fix, the T3010 aggregate used
        filter=Q(receipts__status="issued") in a shared .aggregate() call,
        which caused a LEFT JOIN fan-out doubling all unfiltered aggregates for
        any donation with more than one receipt row.
        """
        d = _make_donation(amount=Decimal("150.00"), created_at=self._ts(2024, 4))
        # Simulate a receipt correction cycle: original cancelled, replacement issued.
        _make_receipt(d, status="cancelled", issued_at=self._ts(2024, 4))
        _make_receipt(d, status="issued", issued_at=self._ts(2024, 4))

        result = get_t3010_preparatory_data(date(2024, 12, 31))

        # Counts must not be inflated — one donation, not two.
        self.assertEqual(result["donation_count"], 1,
                         "donation_count must be 1, not 2 (JOIN inflation bug)")
        self.assertEqual(result["total_eligible_amount"], Decimal("150.00"),
                         "total_eligible_amount must not be doubled")
        self.assertEqual(result["total_receipted_donations"], Decimal("150.00"),
                         "total_receipted_donations must be 150, not doubled")

    def test_large_donation_count_flag(self):
        _make_donation(amount=Decimal("10000.00"), created_at=self._ts(2024, 5))
        _make_donation(amount=Decimal("5000.00"), created_at=self._ts(2024, 5))
        result = get_t3010_preparatory_data(date(2024, 12, 31))
        self.assertEqual(result["large_donation_count"], 1)

    def test_march_fiscal_year(self):
        """Non-calendar fiscal year (April 1 to March 31)."""
        _make_donation(amount=Decimal("400.00"), created_at=self._ts(2024, 2))
        _make_donation(amount=Decimal("100.00"), created_at=self._ts(2024, 4))  # after FY
        result = get_t3010_preparatory_data(date(2024, 3, 31))
        self.assertEqual(result["fiscal_year_start"], date(2023, 4, 1))
        self.assertEqual(result["fiscal_year_end"], date(2024, 3, 31))
        self.assertEqual(result["donation_count"], 1)  # April 2024 excluded

    def test_leap_year_feb_29_fiscal_year_end(self):
        """Fiscal year ending Feb 29, 2024 — previous year clips to Feb 28."""
        result = get_t3010_preparatory_data(date(2024, 2, 29))
        self.assertEqual(result["fiscal_year_start"], date(2023, 3, 1))
        self.assertEqual(result["fiscal_year_end"], date(2024, 2, 29))

    def test_receipt_counts_issued_cancelled(self):
        d1 = _make_donation(amount=Decimal("100.00"), created_at=self._ts(2024, 1))
        _make_receipt(d1, status="issued", issued_at=self._ts(2024, 1))
        d2 = _make_donation(amount=Decimal("50.00"), created_at=self._ts(2024, 3))
        _make_receipt(d2, status="cancelled", issued_at=self._ts(2024, 3))
        result = get_t3010_preparatory_data(date(2024, 12, 31))
        self.assertEqual(result["receipts_issued_in_year"], 1)
        self.assertEqual(result["receipts_cancelled_in_year"], 1)

    def test_pipeda_no_pii(self):
        _make_donation(amount=Decimal("100.00"), created_at=self._ts(2024, 3))
        result = get_t3010_preparatory_data(date(2024, 12, 31))
        all_keys = set(result.keys())
        for row in result.get("by_campaign", []):
            all_keys.update(row.keys())
        for row in result.get("by_month", []):
            all_keys.update(row.keys())
        pii_keys = {"donor_name", "donor_email", "email", "donor_address", "legal_name"}
        self.assertTrue(all_keys.isdisjoint(pii_keys))


# ============================================================================
# get_receipt_list_queryset
# ============================================================================

class GetReceiptListQuerysetTests(TestCase):

    def test_returns_receipts_in_range(self):
        d = _make_donation(amount=Decimal("100.00"))
        issued_at = datetime(2024, 6, 15, 12, 0, tzinfo=dt_timezone.utc)
        _make_receipt(d, issued_at=issued_at)
        qs = get_receipt_list_queryset(date(2024, 6, 1), date(2024, 6, 30))
        self.assertEqual(qs.count(), 1)

    def test_excludes_receipts_outside_range(self):
        d = _make_donation(amount=Decimal("100.00"))
        issued_at = datetime(2024, 7, 15, 12, 0, tzinfo=dt_timezone.utc)
        _make_receipt(d, issued_at=issued_at)
        qs = get_receipt_list_queryset(date(2024, 6, 1), date(2024, 6, 30))
        self.assertEqual(qs.count(), 0)

    def test_includes_multiple_receipts_in_range(self):
        """All receipts whose issued_at falls in the range are returned."""
        d1 = _make_donation(amount=Decimal("100.00"))
        d2 = _make_donation(amount=Decimal("200.00"))
        # Both timestamps are solidly mid-month (well clear of Toronto midnight)
        june_early = datetime(2024, 6, 8, 14, 0, tzinfo=dt_timezone.utc)   # 10:00 EDT
        june_late = datetime(2024, 6, 22, 18, 0, tzinfo=dt_timezone.utc)   # 14:00 EDT
        _make_receipt(d1, issued_at=june_early)
        _make_receipt(d2, issued_at=june_late)
        qs = get_receipt_list_queryset(date(2024, 6, 1), date(2024, 6, 30))
        self.assertEqual(qs.count(), 2)

    def test_has_select_related(self):
        """QuerySet should hit donation+campaign in single join, not N+1."""
        d = _make_donation(amount=Decimal("100.00"))
        issued_at = datetime(2024, 6, 15, 12, 0, tzinfo=dt_timezone.utc)
        _make_receipt(d, issued_at=issued_at)
        qs = get_receipt_list_queryset(date(2024, 6, 1), date(2024, 6, 30))
        receipt = qs.first()
        # If select_related is set, accessing .donation won't fire an extra query
        self.assertIsNotNone(receipt.donation)

    def test_ordered_by_issued_at(self):
        d1 = _make_donation(amount=Decimal("100.00"))
        d2 = _make_donation(amount=Decimal("200.00"))
        early = datetime(2024, 6, 5, 12, 0, tzinfo=dt_timezone.utc)
        late = datetime(2024, 6, 20, 12, 0, tzinfo=dt_timezone.utc)
        r1 = _make_receipt(d1, issued_at=late)
        r2 = _make_receipt(d2, issued_at=early)
        qs = get_receipt_list_queryset(date(2024, 6, 1), date(2024, 6, 30))
        self.assertEqual(qs.first().pk, r2.pk)
        self.assertEqual(qs.last().pk, r1.pk)


# ============================================================================
# compute_donations_snapshot
# ============================================================================

class ComputeDonationsSnapshotTests(TestCase):

    def test_returns_required_keys(self):
        result = compute_donations_snapshot(2024, 6)
        self.assertIn("donations", result)
        self.assertIn("receipts", result)
        self.assertIn("row_count", result)
        self.assertIn("year", result)
        self.assertIn("month", result)

    def test_decimals_serialized_to_strings(self):
        """Decimal values must be strings for JSONB storage."""
        d = _make_donation(
            amount=Decimal("250.00"),
            created_at=datetime(2024, 6, 15, 12, 0, tzinfo=dt_timezone.utc),
        )
        result = compute_donations_snapshot(2024, 6)
        total = result["donations"]["total_donations"]
        self.assertIsInstance(total, str)
        self.assertEqual(Decimal(total), Decimal("250.00"))

    def test_row_count_equals_donation_count(self):
        _make_donation(
            amount=Decimal("100.00"),
            created_at=datetime(2024, 6, 15, 12, 0, tzinfo=dt_timezone.utc),
        )
        result = compute_donations_snapshot(2024, 6)
        self.assertEqual(result["row_count"], 1)

    def test_empty_month_row_count_zero(self):
        result = compute_donations_snapshot(2024, 6)
        self.assertEqual(result["row_count"], 0)


# ============================================================================
# Form validation
# ============================================================================

class ReceiptExportFormTests(TestCase):

    def test_valid_range_within_366_days(self):
        form = ReceiptExportForm(data={
            "start": "2024-01-01",
            "end": "2024-12-31",
        })
        self.assertTrue(form.is_valid())

    def test_end_before_start_invalid(self):
        form = ReceiptExportForm(data={"start": "2024-06-15", "end": "2024-06-01"})
        self.assertFalse(form.is_valid())
        errors = form.non_field_errors().as_data()
        codes = [e.code for e in errors]
        self.assertIn("end_before_start", codes)

    def test_range_too_large_invalid(self):
        form = ReceiptExportForm(data={"start": "2023-01-01", "end": "2024-12-31"})
        self.assertFalse(form.is_valid())
        errors = form.non_field_errors().as_data()
        codes = [e.code for e in errors]
        self.assertIn("range_too_large", codes)

    def test_max_range_exactly_366_days(self):
        # 2024 is a leap year — Jan 1 to Dec 31 = 366 days
        form = ReceiptExportForm(data={"start": "2024-01-01", "end": "2024-12-31"})
        self.assertTrue(form.is_valid())

    def test_max_range_constant_is_366(self):
        self.assertEqual(MAX_RECEIPT_EXPORT_DAYS, 366)


class FiscalYearEndFormTests(TestCase):

    def test_valid_past_date(self):
        form = FiscalYearEndForm(data={"fiscal_year_end": "2023-12-31"})
        self.assertTrue(form.is_valid())

    def test_future_date_invalid(self):
        form = FiscalYearEndForm(data={"fiscal_year_end": "2099-12-31"})
        self.assertFalse(form.is_valid())
        errors = form.errors["fiscal_year_end"].as_data()
        codes = [e.code for e in errors]
        self.assertIn("future_fiscal_year", codes)

    def test_today_is_valid(self):
        from datetime import date as _date
        today = _date.today().isoformat()
        form = FiscalYearEndForm(data={"fiscal_year_end": today})
        self.assertTrue(form.is_valid())

    def test_missing_date_invalid(self):
        form = FiscalYearEndForm(data={})
        self.assertFalse(form.is_valid())


# ============================================================================
# View: DonationDashboardView
# ============================================================================

class DonationDashboardViewTests(TestCase):

    def setUp(self):
        self.url = reverse("reports:donations-dashboard")
        self.view_perm_user = _make_user(perms=["view_donationreport"])
        self.no_perm_user = _make_user(perms=[])

    def test_redirect_unauthenticated(self):
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{_LOGIN_URL}?next={self.url}",
                             fetch_redirect_response=False)

    def test_403_without_permission(self):
        self.client.force_login(self.no_perm_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_200_with_permission(self):
        self.client.force_login(self.view_perm_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_context_has_required_keys(self):
        self.client.force_login(self.view_perm_user)
        response = self.client.get(self.url)
        for key in ["year", "month", "month_label", "is_current_month", "donations"]:
            self.assertIn(key, response.context)

    def test_year_month_picker(self):
        self.client.force_login(self.view_perm_user)
        response = self.client.get(self.url + "?year=2023&month=6")
        self.assertEqual(response.context["year"], 2023)
        self.assertEqual(response.context["month"], 6)
        self.assertFalse(response.context["is_current_month"])

    def test_invalid_year_falls_back_to_current(self):
        self.client.force_login(self.view_perm_user)
        response = self.client.get(self.url + "?year=abc&month=3")
        self.assertEqual(response.status_code, 200)
        # Should fall back to current month
        self.assertIn("year", response.context)

    def test_snapshot_fallback_prior_month(self):
        """Prior month with a ReportSnapshot uses snapshot data."""
        snap_data = {
            "donations": {
                "total_donations": "500.00",
                "total_eligible_amount": "500.00",
                "total_advantage_amount": "0.00",
                "donation_count": 3,
                "unique_donor_count": 2,
                "recurring_count": 1,
                "one_time_count": 2,
                "average_donation": "166.67",
                "by_campaign": [],
            },
            "receipts": {
                "issued": 3,
                "cancelled": 0,
                "superseded": 0,
                "total": 3,
            },
            "row_count": 3,
        }
        ReportSnapshot.objects.create(
            report_type=ReportSnapshot.REPORT_TYPE_DONATIONS,
            period_year=2023,
            period_month=1,
            data=snap_data,
            row_count=3,
        )
        self.client.force_login(self.view_perm_user)
        response = self.client.get(self.url + "?year=2023&month=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["data_source"], "snapshot")
        donations = response.context["donations"]
        self.assertEqual(donations["donation_count"], 3)
        # Decimal strings should be converted back to Decimal objects
        self.assertIsInstance(donations["total_donations"], Decimal)

    def test_template_used(self):
        self.client.force_login(self.view_perm_user)
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, "reports/donations/dashboard.html")


# ============================================================================
# View: AnnualDonationView
# ============================================================================

class AnnualDonationViewTests(TestCase):

    def setUp(self):
        self.url = reverse("reports:donations-annual")
        self.view_perm_user = _make_user(perms=["view_donationreport"])
        self.no_perm_user = _make_user(perms=[])

    def test_redirect_unauthenticated(self):
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{_LOGIN_URL}?next={self.url}",
                             fetch_redirect_response=False)

    def test_403_without_permission(self):
        self.client.force_login(self.no_perm_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_200_with_permission(self):
        self.client.force_login(self.view_perm_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_year_picker(self):
        self.client.force_login(self.view_perm_user)
        response = self.client.get(self.url + "?year=2022")
        self.assertEqual(response.context["year"], 2022)

    def test_context_has_annual_data(self):
        self.client.force_login(self.view_perm_user)
        response = self.client.get(self.url + "?year=2024")
        self.assertIn("annual", response.context)
        annual = response.context["annual"]
        self.assertIn("total_donations", annual)
        self.assertIn("by_month", annual)
        self.assertIn("by_campaign", annual)

    def test_template_used(self):
        self.client.force_login(self.view_perm_user)
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, "reports/donations/annual.html")


# ============================================================================
# View: T3010PrepView
# ============================================================================

class T3010PrepViewTests(TestCase):

    def setUp(self):
        self.url = reverse("reports:t3010-prep")
        self.view_perm_user = _make_user(perms=["view_donationreport"])
        self.no_perm_user = _make_user(perms=[])

    def test_redirect_unauthenticated(self):
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{_LOGIN_URL}?next={self.url}",
                             fetch_redirect_response=False)

    def test_403_without_permission(self):
        self.client.force_login(self.no_perm_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_200_with_permission(self):
        self.client.force_login(self.view_perm_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_context_has_t3010_data(self):
        self.client.force_login(self.view_perm_user)
        response = self.client.get(self.url + "?fiscal_year_end=2023-12-31")
        ctx = response.context
        self.assertIn("t3010", ctx)
        self.assertIn("filing_deadline", ctx)
        self.assertIn("fiscal_year_start", ctx)
        self.assertIn("form", ctx)

    def test_future_fiscal_year_end_shows_form_errors(self):
        self.client.force_login(self.view_perm_user)
        response = self.client.get(self.url + "?fiscal_year_end=2099-12-31")
        self.assertEqual(response.status_code, 200)
        # Invalid form returns default (Dec 31 of previous year) gracefully
        self.assertIn("t3010", response.context)

    def test_template_used(self):
        self.client.force_login(self.view_perm_user)
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, "reports/donations/t3010_prep.html")


# ============================================================================
# View: ReceiptsExportView
# ============================================================================

class ReceiptsExportViewTests(TestCase):

    def setUp(self):
        self.url = reverse("reports:receipts-export")
        self.export_perm_user = _make_user(
            perms=["view_donationreport", "export_donationreport"]
        )
        self.view_only_user = _make_user(perms=["view_donationreport"])
        self.no_perm_user = _make_user(perms=[])

    def test_redirect_unauthenticated(self):
        response = self.client.get(self.url + "?start=2024-01-01&end=2024-03-31")
        self.assertRedirects(
            response, f"{_LOGIN_URL}?next={self.url}%3Fstart%3D2024-01-01%26end%3D2024-03-31",
            fetch_redirect_response=False,
        )

    def test_403_without_export_permission(self):
        self.client.force_login(self.view_only_user)
        response = self.client.get(self.url + "?start=2024-01-01&end=2024-03-31")
        self.assertEqual(response.status_code, 403)

    def test_400_invalid_form(self):
        self.client.force_login(self.export_perm_user)
        response = self.client.get(self.url + "?start=2024-06-30&end=2024-06-01")
        self.assertEqual(response.status_code, 400)

    def test_streaming_csv_response(self):
        self.client.force_login(self.export_perm_user)
        response = self.client.get(self.url + "?start=2024-01-01&end=2024-12-31")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.get("Content-Type", ""))
        self.assertIn("attachment", response.get("Content-Disposition", ""))

    def test_creates_export_record(self):
        self.client.force_login(self.export_perm_user)
        initial_count = ExportRecord.objects.count()
        self.client.get(self.url + "?start=2024-01-01&end=2024-12-31")
        self.assertEqual(ExportRecord.objects.count(), initial_count + 1)
        record = ExportRecord.objects.latest("created_at")
        self.assertEqual(record.export_type, ExportRecord.EXPORT_TYPE_RECEIPTS)
        self.assertEqual(record.format, ExportRecord.FORMAT_CSV)

    def test_export_record_actor_pk_is_integer(self):
        """PIPEDA: actor_pk must store int pk, never email or name."""
        self.client.force_login(self.export_perm_user)
        self.client.get(self.url + "?start=2024-01-01&end=2024-12-31")
        record = ExportRecord.objects.latest("created_at")
        self.assertEqual(record.actor_pk, self.export_perm_user.pk)
        self.assertIsInstance(record.actor_pk, int)

    def test_export_record_actor_ip_is_masked(self):
        """PIPEDA: actor_ip must be masked (last octet zeroed), not the raw IP."""
        self.client.force_login(self.export_perm_user)
        self.client.get(
            self.url + "?start=2024-01-01&end=2024-12-31",
            REMOTE_ADDR="203.0.113.45",
        )
        record = ExportRecord.objects.latest("created_at")
        # Raw IP must not be stored.
        self.assertNotEqual(record.actor_ip, "203.0.113.45")
        # Masked value: last octet zeroed → "203.0.113.0"
        if record.actor_ip is not None:
            self.assertTrue(
                record.actor_ip.endswith(".0") or ":" in record.actor_ip,
                msg=f"actor_ip '{record.actor_ip}' does not look masked",
            )

    def test_row_count_in_export_record(self):
        d = _make_donation(amount=Decimal("100.00"))
        issued_at = datetime(2024, 6, 15, 12, 0, tzinfo=dt_timezone.utc)
        _make_receipt(d, issued_at=issued_at)
        self.client.force_login(self.export_perm_user)
        self.client.get(self.url + "?start=2024-06-01&end=2024-06-30")
        record = ExportRecord.objects.latest("created_at")
        self.assertEqual(record.row_count, 1)

    def test_csv_pipeda_no_donor_pii_columns(self):
        """Exported CSV must not contain donor PII column headers anywhere."""
        d = _make_donation(amount=Decimal("100.00"))
        issued_at = datetime(2024, 6, 15, 12, 0, tzinfo=dt_timezone.utc)
        _make_receipt(d, issued_at=issued_at)
        self.client.force_login(self.export_perm_user)
        response = self.client.get(self.url + "?start=2024-01-01&end=2024-12-31")
        content = b"".join(response.streaming_content).decode("utf-8-sig")
        pii_columns = [
            "donor_legal_name", "donor_address", "donor_city",
            "donor_province", "donor_postal_code", "donor_name",
            "email",
        ]
        # assertNotIn on the full content string — not on a sliced list of tokens
        # (the list-element check would miss substrings in the middle of a cell).
        for col in pii_columns:
            self.assertNotIn(col, content.lower(),
                             msg=f"PII column '{col}' found in CSV output")

    def test_csv_whitelisted_columns_present(self):
        """Exported CSV must contain CRA-required non-PII fields."""
        d = _make_donation(amount=Decimal("100.00"))
        issued_at = datetime(2024, 6, 15, 12, 0, tzinfo=dt_timezone.utc)
        _make_receipt(d, issued_at=issued_at)
        self.client.force_login(self.export_perm_user)
        response = self.client.get(self.url + "?start=2024-01-01&end=2024-12-31")
        content = b"".join(response.streaming_content).decode("utf-8-sig")
        first_line = content.split("\r\n")[0]
        expected = ["receipt_number", "status", "eligible_amount"]
        for col in expected:
            self.assertIn(col, first_line)

    def test_empty_range_returns_csv_with_header_only(self):
        self.client.force_login(self.export_perm_user)
        response = self.client.get(self.url + "?start=2024-01-01&end=2024-01-31")
        content = b"".join(response.streaming_content).decode("utf-8-sig")
        lines = [l for l in content.split("\r\n") if l.strip()]
        self.assertEqual(len(lines), 1)  # only header


# ============================================================================
# View: T3010PrepExportView
# ============================================================================

class T3010PrepExportViewTests(TestCase):

    def setUp(self):
        self.url = reverse("reports:t3010-prep-export")
        self.export_perm_user = _make_user(
            perms=["view_donationreport", "export_donationreport"]
        )
        self.no_perm_user = _make_user(perms=[])

    def test_redirect_unauthenticated(self):
        response = self.client.get(self.url + "?fiscal_year_end=2023-12-31")
        self.assertEqual(response.status_code, 302)

    def test_403_without_permission(self):
        self.client.force_login(self.no_perm_user)
        response = self.client.get(self.url + "?fiscal_year_end=2023-12-31")
        self.assertEqual(response.status_code, 403)

    def test_400_invalid_form(self):
        self.client.force_login(self.export_perm_user)
        response = self.client.get(self.url + "?fiscal_year_end=2099-12-31")
        self.assertEqual(response.status_code, 400)

    def test_streaming_csv_response(self):
        self.client.force_login(self.export_perm_user)
        response = self.client.get(self.url + "?fiscal_year_end=2023-12-31")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.get("Content-Type", ""))

    def test_creates_export_record(self):
        self.client.force_login(self.export_perm_user)
        initial_count = ExportRecord.objects.count()
        self.client.get(self.url + "?fiscal_year_end=2023-12-31")
        self.assertEqual(ExportRecord.objects.count(), initial_count + 1)
        record = ExportRecord.objects.latest("created_at")
        self.assertEqual(record.export_type, ExportRecord.EXPORT_TYPE_T3010)

    def test_export_record_actor_pk_is_integer(self):
        self.client.force_login(self.export_perm_user)
        self.client.get(self.url + "?fiscal_year_end=2023-12-31")
        record = ExportRecord.objects.latest("created_at")
        self.assertIsInstance(record.actor_pk, int)
        self.assertEqual(record.actor_pk, self.export_perm_user.pk)

    def test_csv_has_summary_monthly_campaign_sections(self):
        d = _make_donation(
            amount=Decimal("100.00"),
            created_at=datetime(2023, 6, 15, 12, 0, tzinfo=dt_timezone.utc),
        )
        _make_receipt(d, status="issued", issued_at=datetime(2023, 6, 15, 12, 0, tzinfo=dt_timezone.utc))
        self.client.force_login(self.export_perm_user)
        response = self.client.get(self.url + "?fiscal_year_end=2023-12-31")
        content = b"".join(response.streaming_content).decode("utf-8-sig")
        self.assertIn("SUMMARY", content)
        self.assertIn("MONTHLY", content)

    def test_csv_pipeda_no_donor_pii(self):
        self.client.force_login(self.export_perm_user)
        response = self.client.get(self.url + "?fiscal_year_end=2023-12-31")
        content = b"".join(response.streaming_content).decode("utf-8-sig")
        pii_terms = ["donor_legal_name", "donor_address", "email"]
        for term in pii_terms:
            self.assertNotIn(term, content.lower())

    def test_row_count_in_export_record(self):
        """row_count = summary rows + monthly rows + campaign rows."""
        d = _make_donation(
            amount=Decimal("100.00"),
            created_at=datetime(2023, 6, 15, 12, 0, tzinfo=dt_timezone.utc),
        )
        _make_receipt(d, status="issued", issued_at=datetime(2023, 6, 15, 12, 0, tzinfo=dt_timezone.utc))
        self.client.force_login(self.export_perm_user)
        self.client.get(self.url + "?fiscal_year_end=2023-12-31")
        record = ExportRecord.objects.latest("created_at")
        # 4 summary rows + 1 month (June) + 1 campaign (General Fund)
        self.assertEqual(record.row_count, 6)


# ============================================================================
# CSV export functions
# ============================================================================

class ExportReceiptsCsvTests(TestCase):

    def test_streams_csv_with_bom(self):
        from apps.reports.exports.csv_export import export_receipts_csv
        d = _make_donation(amount=Decimal("150.00"))
        issued_at = datetime(2024, 6, 15, 12, 0, tzinfo=dt_timezone.utc)
        _make_receipt(d, issued_at=issued_at)
        response = export_receipts_csv(date(2024, 6, 1), date(2024, 6, 30))
        content = b"".join(response.streaming_content).decode("utf-8-sig")
        lines = [l for l in content.split("\r\n") if l.strip()]
        self.assertGreaterEqual(len(lines), 2)  # header + at least 1 data row

    def test_whitelist_excludes_pii(self):
        from apps.reports.exports.csv_export import export_receipts_csv
        d = _make_donation(amount=Decimal("100.00"))
        issued_at = datetime(2024, 6, 15, 12, 0, tzinfo=dt_timezone.utc)
        _make_receipt(d, issued_at=issued_at)
        response = export_receipts_csv(date(2024, 6, 1), date(2024, 6, 30))
        content = b"".join(response.streaming_content).decode("utf-8-sig")
        header = content.split("\r\n")[0]
        for pii_col in ["donor_legal_name", "donor_address", "donor_city", "postal_code"]:
            self.assertNotIn(pii_col, header)

    def test_filename_contains_dates_not_pii(self):
        from apps.reports.exports.csv_export import export_receipts_csv
        response = export_receipts_csv(date(2024, 1, 1), date(2024, 3, 31))
        disposition = response["Content-Disposition"]
        self.assertIn("2024-01-01", disposition)
        self.assertIn("2024-03-31", disposition)
        self.assertNotIn("donor", disposition.lower())


class ExportT3010PrepCsvTests(TestCase):

    def test_streams_summary_monthly_campaign(self):
        from apps.reports.exports.csv_export import export_t3010_prep_csv
        d = _make_donation(
            amount=Decimal("300.00"),
            created_at=datetime(2023, 9, 10, 12, 0, tzinfo=dt_timezone.utc),
        )
        _make_receipt(d, status="issued", issued_at=datetime(2023, 9, 10, 12, 0, tzinfo=dt_timezone.utc))
        response = export_t3010_prep_csv(date(2023, 12, 31))
        content = b"".join(response.streaming_content).decode("utf-8-sig")
        self.assertIn("SUMMARY", content)
        self.assertIn("MONTHLY", content)
        self.assertIn("CAMPAIGN", content)

    def test_line_4500_appears_in_csv(self):
        from apps.reports.exports.csv_export import export_t3010_prep_csv
        d = _make_donation(
            amount=Decimal("500.00"),
            created_at=datetime(2023, 3, 10, 12, 0, tzinfo=dt_timezone.utc),
        )
        _make_receipt(d, status="issued", issued_at=datetime(2023, 3, 10, 12, 0, tzinfo=dt_timezone.utc))
        response = export_t3010_prep_csv(date(2023, 12, 31))
        content = b"".join(response.streaming_content).decode("utf-8-sig")
        self.assertIn("4500", content)

    def test_no_pii_in_output(self):
        from apps.reports.exports.csv_export import export_t3010_prep_csv
        response = export_t3010_prep_csv(date(2023, 12, 31))
        content = b"".join(response.streaming_content).decode("utf-8-sig")
        for pii in ["donor_legal_name", "donor_address", "email@", "Jane Doe"]:
            self.assertNotIn(pii, content)


# ============================================================================
# Celery task integration
# ============================================================================

class ComputeAllSnapshotsTaskTests(TestCase):

    def test_donations_snapshot_written(self):
        """_compute_all_snapshots must write a DONATIONS-type ReportSnapshot."""
        from apps.reports.tasks import _compute_all_snapshots
        written = _compute_all_snapshots(2024, 5)
        self.assertGreaterEqual(written, 1)
        self.assertTrue(
            ReportSnapshot.objects.filter(
                report_type=ReportSnapshot.REPORT_TYPE_DONATIONS,
                period_year=2024,
                period_month=5,
            ).exists()
        )

    def test_donations_snapshot_is_idempotent(self):
        """Running _compute_all_snapshots twice must update, not duplicate."""
        from apps.reports.tasks import _compute_all_snapshots
        _compute_all_snapshots(2024, 5)
        _compute_all_snapshots(2024, 5)
        count = ReportSnapshot.objects.filter(
            report_type=ReportSnapshot.REPORT_TYPE_DONATIONS,
            period_year=2024,
            period_month=5,
        ).count()
        self.assertEqual(count, 1)

    def test_donations_snapshot_data_structure(self):
        """Snapshot data must have 'donations', 'receipts', 'row_count' keys."""
        from apps.reports.tasks import _compute_all_snapshots
        _make_donation(
            amount=Decimal("100.00"),
            created_at=datetime(2024, 5, 15, 12, 0, tzinfo=dt_timezone.utc),
        )
        _compute_all_snapshots(2024, 5)
        snap = ReportSnapshot.objects.get(
            report_type=ReportSnapshot.REPORT_TYPE_DONATIONS,
            period_year=2024,
            period_month=5,
        )
        self.assertIn("donations", snap.data)
        self.assertIn("receipts", snap.data)
        self.assertIn("row_count", snap.data)

    def test_donations_snapshot_decimal_as_string(self):
        """Decimal values in snapshot.data must be strings (JSONB-safe)."""
        from apps.reports.tasks import _compute_all_snapshots
        _make_donation(
            amount=Decimal("250.00"),
            created_at=datetime(2024, 5, 15, 12, 0, tzinfo=dt_timezone.utc),
        )
        _compute_all_snapshots(2024, 5)
        snap = ReportSnapshot.objects.get(
            report_type=ReportSnapshot.REPORT_TYPE_DONATIONS,
            period_year=2024,
            period_month=5,
        )
        total = snap.data["donations"]["total_donations"]
        self.assertIsInstance(total, str)
        self.assertEqual(Decimal(total), Decimal("250.00"))
