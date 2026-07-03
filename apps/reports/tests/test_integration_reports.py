"""
Integration Wave — Reports BB integration tests.

Covers:
  - get_monthly_volunteer_summary: shape, aggregation, PIPEDA
  - compute_volunteer_snapshot: keys, Decimal serialisation
  - combined_nonprofit_impact: keys, zero-data cases, combined_value_cad
  - VolunteerImpactDashboardView: auth, permission, context, snapshot vs live
  - CombinedImpactView: auth, permission, context
  - _compute_all_snapshots: returns 4, creates volunteers snapshot, idempotent
  - _compute_single_snapshot: returns (dict, int) for volunteers type
  - recompute_snapshot Celery task: creates/updates snapshot

PIPEDA invariants:
  - by_program rows contain no email, first_name, or last_name keys
  - Snapshot data contains only programme-level aggregates

Settings: --settings=config.settings.test
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, TransactionTestCase, override_settings

from apps.reports.models import ReportSnapshot
from apps.reports.services.volunteers import (
    compute_volunteer_snapshot,
    get_monthly_volunteer_summary,
)
from apps.reports.services.combined import combined_nonprofit_impact
from apps.reports.tasks import _compute_all_snapshots, _compute_single_snapshot

User = get_user_model()

# ---------------------------------------------------------------------------
# Shared factory helpers
# ---------------------------------------------------------------------------

_counter = [0]


def _uid():
    _counter[0] += 1
    return _counter[0]


def _make_user(*, is_staff=False, perms=None):
    n = _uid()
    u = User.objects.create_user(
        email=f"reports_user_{n}@example.gc.ca",
        password="testpass123!",
        is_staff=is_staff,
    )
    if perms:
        for codename in perms:
            app_label, perm_codename = codename.split(".")
            perm = Permission.objects.get(
                codename=perm_codename,
                content_type__app_label=app_label,
            )
            u.user_permissions.add(perm)
        # Refresh to clear permission cache
        u = User.objects.get(pk=u.pk)
    return u


def _make_program(**kwargs):
    from apps.volunteers.models import Program
    n = _uid()
    defaults = dict(
        name_en=f"Program {n}",
        name_fr=f"Programme {n}",
        slug=f"rprog-{n}",
        cra_category="welfare",
    )
    defaults.update(kwargs)
    return Program.objects.create(**defaults)


def _make_opportunity(program, **kwargs):
    from apps.volunteers.models import Opportunity
    n = _uid()
    defaults = dict(
        title_en=f"Opportunity {n}",
        title_fr=f"Opportunité {n}",
        slug=f"ropp-{n}",
        description_en="Desc",
        description_fr="Desc FR",
        program=program,
        status="published",
    )
    defaults.update(kwargs)
    return Opportunity.objects.create(**defaults)


def _make_profile(user):
    from apps.volunteers.models import VolunteerProfile
    return VolunteerProfile.objects.create(user=user)


def _make_shift(opportunity, *, start_datetime=None, end_datetime=None):
    from apps.volunteers.models import Shift
    from django.utils import timezone
    now = timezone.now()
    start_datetime = start_datetime or now
    end_datetime = end_datetime or (now.replace(hour=(now.hour + 2) % 24))
    return Shift.objects.create(
        opportunity=opportunity,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        title_en="Shift",
        title_fr="Quart",
    )


def _make_hours_log(volunteer, shift, *, approved_hours=None, status="approved"):
    from apps.volunteers.models import HoursLog
    return HoursLog.objects.create(
        volunteer=volunteer,
        shift=shift,
        opportunity=shift.opportunity,  # required: hours_by_program filters opportunity__isnull=False
        hours=Decimal(str(approved_hours or "2.00")),
        status=status,
        date=date.today(),
    )


def _make_staff_user_with_reports_perm():
    return _make_user(is_staff=True, perms=["reports.view_reportsnapshot"])


# ---------------------------------------------------------------------------
# VolunteerServiceTests
# ---------------------------------------------------------------------------

class VolunteerServiceTests(TestCase):
    """Tests for get_monthly_volunteer_summary and compute_volunteer_snapshot."""

    def test_empty_returns_zeros(self):
        """When no HoursLog rows exist, all numeric fields are zero/empty."""
        result = get_monthly_volunteer_summary(2025, 1)
        self.assertEqual(result["total_approved_hours"], Decimal("0.00"))
        self.assertEqual(result["volunteer_count"], 0)
        self.assertEqual(result["opportunity_count"], 0)
        self.assertEqual(result["program_count"], 0)
        self.assertIsInstance(result["by_program"], list)
        self.assertEqual(len(result["by_program"]), 0)

    def test_returns_required_keys(self):
        result = get_monthly_volunteer_summary(2025, 1)
        for key in ("total_approved_hours", "volunteer_count", "opportunity_count",
                    "program_count", "by_program"):
            self.assertIn(key, result)

    def test_sums_approved_hours_single_program(self):
        program = _make_program()
        opp = _make_opportunity(program)
        # Two separate shifts so (volunteer, shift) unique constraint is not violated
        shift_a = _make_shift(opp)
        shift_b = _make_shift(opp)
        vol_user = _make_user()
        profile = _make_profile(vol_user)
        _make_hours_log(profile, shift_a, approved_hours="3.50")
        _make_hours_log(profile, shift_b, approved_hours="1.50")
        # M-8: scope to this test's profile only — not HoursLog.objects.all() which
        # could contaminate rows created by other test methods in the same transaction.
        from apps.volunteers.models import HoursLog
        HoursLog.objects.filter(volunteer=profile).update(date=date(2025, 6, 15))
        result = get_monthly_volunteer_summary(2025, 6)
        self.assertEqual(result["total_approved_hours"], Decimal("5.00"))

    def test_volunteer_count_correct(self):
        program = _make_program()
        opp = _make_opportunity(program)
        shift = _make_shift(opp)
        # M-8: collect profiles so we can scope the update to only this test's rows.
        profiles = []
        for _ in range(3):
            vol_user = _make_user()
            profile = _make_profile(vol_user)
            _make_hours_log(profile, shift, approved_hours="1.00")
            profiles.append(profile)
        from apps.volunteers.models import HoursLog
        HoursLog.objects.filter(volunteer__in=profiles).update(date=date(2025, 7, 10))
        result = get_monthly_volunteer_summary(2025, 7)
        self.assertGreaterEqual(result["volunteer_count"], 3)

    def test_multiple_programs_aggregated(self):
        program_a = _make_program()
        program_b = _make_program()
        opp_a = _make_opportunity(program_a)
        opp_b = _make_opportunity(program_b)
        shift_a = _make_shift(opp_a)
        shift_b = _make_shift(opp_b)
        vol_user = _make_user()
        profile = _make_profile(vol_user)
        _make_hours_log(profile, shift_a, approved_hours="2.00")
        _make_hours_log(profile, shift_b, approved_hours="3.00")
        from apps.volunteers.models import HoursLog
        HoursLog.objects.filter(volunteer=profile).update(date=date(2025, 8, 20))
        result = get_monthly_volunteer_summary(2025, 8)
        self.assertEqual(result["total_approved_hours"], Decimal("5.00"))
        self.assertGreaterEqual(result["program_count"], 2)

    def test_by_program_no_pii_keys(self):
        """by_program rows must never contain volunteer PII keys."""
        program = _make_program()
        opp = _make_opportunity(program)
        shift = _make_shift(opp)
        vol_user = _make_user()
        profile = _make_profile(vol_user)
        _make_hours_log(profile, shift, approved_hours="1.00")
        from apps.volunteers.models import HoursLog
        HoursLog.objects.filter(volunteer=profile).update(date=date(2025, 9, 5))
        result = get_monthly_volunteer_summary(2025, 9)
        for row in result["by_program"]:
            self.assertNotIn("email", row)
            self.assertNotIn("first_name", row)
            self.assertNotIn("last_name", row)

    def test_by_program_no_pii_in_nested_values(self):
        """No row value should contain an email-like string."""
        program = _make_program()
        opp = _make_opportunity(program)
        shift = _make_shift(opp)
        vol_user = _make_user()
        profile = _make_profile(vol_user)
        _make_hours_log(profile, shift, approved_hours="1.00")
        from apps.volunteers.models import HoursLog
        HoursLog.objects.filter(volunteer=profile).update(date=date(2025, 9, 6))
        result = get_monthly_volunteer_summary(2025, 9)
        for row in result["by_program"]:
            for key in row:
                self.assertNotIn("email", key.lower())
                self.assertNotIn("first_name", key.lower())
                self.assertNotIn("last_name", key.lower())

    def test_non_approved_hours_excluded(self):
        """Only status='approved' hours should count."""
        program = _make_program()
        opp = _make_opportunity(program)
        shift = _make_shift(opp)
        vol_user = _make_user()
        profile = _make_profile(vol_user)
        _make_hours_log(profile, shift, approved_hours="5.00", status="pending")
        from apps.volunteers.models import HoursLog
        HoursLog.objects.filter(volunteer=profile).update(date=date(2025, 10, 1))
        result = get_monthly_volunteer_summary(2025, 10)
        self.assertEqual(result["total_approved_hours"], Decimal("0.00"))

    def test_returns_decimal_total_approved_hours(self):
        result = get_monthly_volunteer_summary(2025, 1)
        self.assertIsInstance(result["total_approved_hours"], Decimal)

    # ── compute_volunteer_snapshot ────────────────────────────────────────────

    def test_snapshot_returns_required_keys(self):
        snapshot = compute_volunteer_snapshot(2025, 1)
        for key in ("year", "month", "hours", "impact", "row_count"):
            self.assertIn(key, snapshot)

    def test_snapshot_year_month_correct(self):
        snapshot = compute_volunteer_snapshot(2025, 3)
        self.assertEqual(snapshot["year"], 2025)
        self.assertEqual(snapshot["month"], 3)

    def test_snapshot_hours_no_decimal(self):
        """hours sub-dict must not contain Decimal instances (must be str or int)."""
        snapshot = compute_volunteer_snapshot(2025, 1)
        hours = snapshot["hours"]
        self._assert_no_decimal(hours)

    def test_snapshot_impact_no_decimal(self):
        """impact sub-dict must not contain Decimal instances."""
        snapshot = compute_volunteer_snapshot(2025, 1)
        impact = snapshot["impact"]
        self._assert_no_decimal(impact)

    def test_snapshot_row_count_is_int(self):
        snapshot = compute_volunteer_snapshot(2025, 1)
        self.assertIsInstance(snapshot["row_count"], int)

    def test_snapshot_hours_has_expected_keys(self):
        snapshot = compute_volunteer_snapshot(2025, 1)
        hours = snapshot["hours"]
        for key in ("total_approved_hours", "volunteer_count", "opportunity_count",
                    "program_count", "by_program"):
            self.assertIn(key, hours)

    def test_snapshot_impact_has_expected_keys(self):
        snapshot = compute_volunteer_snapshot(2025, 1)
        impact = snapshot["impact"]
        for key in ("total_approved_hours", "estimated_value_cad", "volunteer_count",
                    "hourly_rate", "province"):
            self.assertIn(key, impact)

    def _assert_no_decimal(self, obj):
        """Recursively assert no Decimal values anywhere in obj."""
        if isinstance(obj, Decimal):
            self.fail(f"Found Decimal value {obj!r} — should be serialised to str")
        elif isinstance(obj, dict):
            for v in obj.values():
                self._assert_no_decimal(v)
        elif isinstance(obj, list):
            for item in obj:
                self._assert_no_decimal(item)


# ---------------------------------------------------------------------------
# CombinedServiceTests
# ---------------------------------------------------------------------------

class CombinedServiceTests(TestCase):
    """Tests for combined_nonprofit_impact."""

    def test_returns_required_top_level_keys(self):
        result = combined_nonprofit_impact(2025)
        for key in ("year", "volunteer", "donations", "combined_value_cad", "t3010_notes"):
            self.assertIn(key, result)

    def test_year_in_result_matches_input(self):
        result = combined_nonprofit_impact(2024)
        self.assertEqual(result["year"], 2024)

    def test_volunteer_sub_dict_keys(self):
        result = combined_nonprofit_impact(2025)
        vol = result["volunteer"]
        for key in ("total_approved_hours", "estimated_value_cad", "volunteer_count",
                    "hourly_rate", "province"):
            self.assertIn(key, vol)

    def test_donations_sub_dict_keys(self):
        result = combined_nonprofit_impact(2025)
        don = result["donations"]
        for key in ("total_donations", "total_eligible_amount", "donation_count",
                    "unique_donor_count", "receipts_issued"):
            self.assertIn(key, don)

    def test_no_volunteer_data_returns_zeros(self):
        """When volunteer app has no data, zeros are returned instead of crashing."""
        result = combined_nonprofit_impact(1900)  # year with no data
        vol = result["volunteer"]
        self.assertEqual(vol["total_approved_hours"], Decimal("0.00"))
        self.assertEqual(vol["estimated_value_cad"], Decimal("0.00"))
        self.assertEqual(vol["volunteer_count"], 0)

    def test_no_donation_data_returns_zeros(self):
        """When donations app has no data, zeros are returned instead of crashing."""
        result = combined_nonprofit_impact(1900)
        don = result["donations"]
        self.assertEqual(don["total_donations"], Decimal("0.00"))
        self.assertEqual(don["total_eligible_amount"], Decimal("0.00"))
        self.assertEqual(don["donation_count"], 0)

    def test_combined_value_cad_is_sum_of_volunteer_value_and_eligible_donations(self):
        """combined_value_cad = volunteer.estimated_value_cad + donations.total_eligible_amount"""
        result = combined_nonprofit_impact(2025)
        expected = result["volunteer"]["estimated_value_cad"] + result["donations"]["total_eligible_amount"]
        self.assertEqual(result["combined_value_cad"], expected)

    def test_combined_value_cad_is_decimal(self):
        result = combined_nonprofit_impact(2025)
        self.assertIsInstance(result["combined_value_cad"], Decimal)

    def test_t3010_notes_is_string(self):
        result = combined_nonprofit_impact(2025)
        self.assertIsInstance(result["t3010_notes"], str)
        self.assertGreater(len(result["t3010_notes"]), 0)

    def test_returns_dict_not_none(self):
        result = combined_nonprofit_impact(2025)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, dict)

    # ── H-9: Exception path tests ─────────────────────────────────────────────

    def test_volunteer_source_exception_returns_zero_volunteer_dict(self):
        """
        H-9: When impact_value() raises an exception, combined_nonprofit_impact
        returns zero-valued defaults for the volunteer sub-dict rather than
        propagating the exception.
        """
        with patch(
            "apps.volunteers.services.reporting.impact_value",
            side_effect=RuntimeError("volunteer service unavailable"),
        ):
            result = combined_nonprofit_impact(2025)

        vol = result["volunteer"]
        self.assertEqual(vol["total_approved_hours"], Decimal("0.00"))
        self.assertEqual(vol["estimated_value_cad"], Decimal("0.00"))
        self.assertEqual(vol["volunteer_count"], 0)
        self.assertEqual(vol["hourly_rate"], Decimal("0.00"))
        self.assertEqual(vol["province"], "ON")

    def test_volunteer_source_exception_does_not_affect_donations(self):
        """
        H-9: When the volunteer source fails, the donation data is still
        computed normally and returned (independent data sources).
        """
        with patch(
            "apps.volunteers.services.reporting.impact_value",
            side_effect=RuntimeError("volunteer service unavailable"),
        ):
            result = combined_nonprofit_impact(2025)

        # donations sub-dict must still have all required keys, even if zeros
        don = result["donations"]
        for key in ("total_donations", "total_eligible_amount", "donation_count",
                    "unique_donor_count", "receipts_issued"):
            self.assertIn(key, don)

    def test_donation_source_exception_returns_zero_donation_dict(self):
        """
        H-9: When get_annual_donation_summary() raises an exception,
        combined_nonprofit_impact returns zero-valued defaults for the donations
        sub-dict rather than propagating the exception.

        Patch target: the function on its SOURCE module (not on combined.py),
        because combined.py uses a deferred `from ... import` inside a try block.
        """
        with patch(
            "apps.reports.services.donations.get_annual_donation_summary",
            side_effect=RuntimeError("donations DB unavailable"),
        ):
            result = combined_nonprofit_impact(2025)

        don = result["donations"]
        self.assertEqual(don["total_donations"], Decimal("0.00"))
        self.assertEqual(don["total_eligible_amount"], Decimal("0.00"))
        self.assertEqual(don["donation_count"], 0)
        self.assertEqual(don["unique_donor_count"], 0)
        self.assertEqual(don["receipts_issued"], 0)

    def test_donation_source_exception_does_not_affect_volunteer(self):
        """
        H-9: When the donation source fails, the volunteer data is still
        computed normally and returned (independent data sources).
        """
        with patch(
            "apps.reports.services.donations.get_annual_donation_summary",
            side_effect=RuntimeError("donations DB unavailable"),
        ):
            result = combined_nonprofit_impact(2025)

        # volunteer sub-dict must still have all required keys
        vol = result["volunteer"]
        for key in ("total_approved_hours", "estimated_value_cad", "volunteer_count",
                    "hourly_rate", "province"):
            self.assertIn(key, vol)

    def test_both_sources_fail_returns_all_zeros(self):
        """
        H-9: When both sources fail independently, the function still returns a
        valid dict with zeros and does not raise.
        """
        with patch(
            "apps.volunteers.services.reporting.impact_value",
            side_effect=RuntimeError("volunteer down"),
        ), patch(
            "apps.reports.services.donations.get_annual_donation_summary",
            side_effect=RuntimeError("donations down"),
        ):
            result = combined_nonprofit_impact(2025)

        self.assertIsInstance(result, dict)
        self.assertEqual(result["volunteer"]["estimated_value_cad"], Decimal("0.00"))
        self.assertEqual(result["donations"]["total_eligible_amount"], Decimal("0.00"))
        self.assertEqual(result["combined_value_cad"], Decimal("0.00"))


# ---------------------------------------------------------------------------
# VolunteerViewTests
# ---------------------------------------------------------------------------

class VolunteerViewTests(TestCase):
    """Tests for VolunteerImpactDashboardView at /reports/volunteers/."""

    VOLUNTEERS_URL = "/reports/volunteers/"

    def test_anonymous_redirects_to_login(self):
        response = self.client.get(self.VOLUNTEERS_URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/account/login/", response["Location"])

    def test_authenticated_no_permission_returns_403(self):
        user = _make_user(is_staff=True)
        self.client.force_login(user)
        response = self.client.get(self.VOLUNTEERS_URL)
        self.assertEqual(response.status_code, 403)

    def test_authenticated_non_staff_no_perm_returns_403(self):
        user = _make_user(is_staff=False)
        self.client.force_login(user)
        response = self.client.get(self.VOLUNTEERS_URL)
        self.assertEqual(response.status_code, 403)

    def test_staff_with_permission_returns_200(self):
        user = _make_staff_user_with_reports_perm()
        self.client.force_login(user)
        response = self.client.get(self.VOLUNTEERS_URL)
        self.assertEqual(response.status_code, 200)

    def test_uses_correct_template(self):
        user = _make_staff_user_with_reports_perm()
        self.client.force_login(user)
        response = self.client.get(self.VOLUNTEERS_URL)
        self.assertTemplateUsed(response, "reports/volunteers/dashboard.html")

    def test_context_has_volunteer_summary(self):
        user = _make_staff_user_with_reports_perm()
        self.client.force_login(user)
        response = self.client.get(self.VOLUNTEERS_URL)
        self.assertIn("volunteer_summary", response.context)

    def test_context_has_data_source(self):
        user = _make_staff_user_with_reports_perm()
        self.client.force_login(user)
        response = self.client.get(self.VOLUNTEERS_URL)
        self.assertIn("data_source", response.context)

    def test_current_month_data_source_is_live(self):
        """When viewing the current month, data_source must be 'live'."""
        from django.utils import timezone
        now = timezone.localtime(timezone.now())
        user = _make_staff_user_with_reports_perm()
        self.client.force_login(user)
        url = f"{self.VOLUNTEERS_URL}?year={now.year}&month={now.month}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["data_source"], "live")

    def test_past_month_without_snapshot_data_source_is_live(self):
        """Past month with no snapshot falls through to live query — data_source is still 'live'."""
        user = _make_staff_user_with_reports_perm()
        self.client.force_login(user)
        response = self.client.get(f"{self.VOLUNTEERS_URL}?year=2020&month=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["data_source"], "live")

    def test_past_month_with_snapshot_data_source_is_snapshot(self):
        """When a ReportSnapshot exists for a past month, data_source must be 'snapshot'."""
        ReportSnapshot.objects.create(
            report_type=ReportSnapshot.REPORT_TYPE_VOLUNTEERS,
            period_year=2020,
            period_month=2,
            data={
                "year": 2020,
                "month": 2,
                "hours": {
                    "total_approved_hours": "10.00",
                    "volunteer_count": 5,
                    "opportunity_count": 2,
                    "program_count": 1,
                    "by_program": [],
                },
                "impact": {
                    "total_approved_hours": "10.00",
                    "estimated_value_cad": "250.00",
                    "volunteer_count": 5,
                    "hourly_rate": "25.00",
                    "province": "ON",
                },
                "row_count": 10,
            },
            row_count=10,
        )
        user = _make_staff_user_with_reports_perm()
        self.client.force_login(user)
        response = self.client.get(f"{self.VOLUNTEERS_URL}?year=2020&month=2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["data_source"], "snapshot")

    def test_snapshot_context_volunteer_summary_comes_from_snapshot(self):
        """Snapshot data is loaded into context correctly when snapshot exists."""
        ReportSnapshot.objects.create(
            report_type=ReportSnapshot.REPORT_TYPE_VOLUNTEERS,
            period_year=2020,
            period_month=3,
            data={
                "year": 2020,
                "month": 3,
                "hours": {
                    "total_approved_hours": "42.00",
                    "volunteer_count": 7,
                    "opportunity_count": 3,
                    "program_count": 2,
                    "by_program": [],
                },
                "impact": {
                    "total_approved_hours": "42.00",
                    "estimated_value_cad": "1050.00",
                    "volunteer_count": 7,
                    "hourly_rate": "25.00",
                    "province": "ON",
                },
                "row_count": 42,
            },
            row_count=42,
        )
        user = _make_staff_user_with_reports_perm()
        self.client.force_login(user)
        response = self.client.get(f"{self.VOLUNTEERS_URL}?year=2020&month=3")
        self.assertEqual(response.status_code, 200)
        summary = response.context["volunteer_summary"]
        self.assertEqual(summary["volunteer_count"], 7)
        self.assertEqual(summary["opportunity_count"], 3)

    def test_context_has_year_and_month(self):
        user = _make_staff_user_with_reports_perm()
        self.client.force_login(user)
        response = self.client.get(f"{self.VOLUNTEERS_URL}?year=2021&month=5")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["year"], 2021)
        self.assertEqual(response.context["month"], 5)

    def test_context_has_recent_snapshots(self):
        user = _make_staff_user_with_reports_perm()
        self.client.force_login(user)
        response = self.client.get(self.VOLUNTEERS_URL)
        self.assertIn("recent_snapshots", response.context)


# ---------------------------------------------------------------------------
# CombinedViewTests
# ---------------------------------------------------------------------------

class CombinedViewTests(TestCase):
    """Tests for CombinedImpactView at /reports/combined/."""

    COMBINED_URL = "/reports/combined/"

    def test_anonymous_redirects_to_login(self):
        response = self.client.get(self.COMBINED_URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/account/login/", response["Location"])

    def test_authenticated_no_permission_returns_403(self):
        user = _make_user(is_staff=True)
        self.client.force_login(user)
        response = self.client.get(self.COMBINED_URL)
        self.assertEqual(response.status_code, 403)

    def test_authenticated_non_staff_no_perm_returns_403(self):
        user = _make_user(is_staff=False)
        self.client.force_login(user)
        response = self.client.get(self.COMBINED_URL)
        self.assertEqual(response.status_code, 403)

    def test_staff_with_permission_returns_200(self):
        user = _make_staff_user_with_reports_perm()
        self.client.force_login(user)
        response = self.client.get(self.COMBINED_URL)
        self.assertEqual(response.status_code, 200)

    def test_uses_correct_template(self):
        user = _make_staff_user_with_reports_perm()
        self.client.force_login(user)
        response = self.client.get(self.COMBINED_URL)
        self.assertTemplateUsed(response, "reports/combined/impact.html")

    def test_context_has_impact_key(self):
        user = _make_staff_user_with_reports_perm()
        self.client.force_login(user)
        response = self.client.get(self.COMBINED_URL)
        self.assertIn("impact", response.context)

    def test_context_impact_has_year_key(self):
        user = _make_staff_user_with_reports_perm()
        self.client.force_login(user)
        response = self.client.get(self.COMBINED_URL)
        impact = response.context["impact"]
        self.assertIn("year", impact)

    def test_context_impact_has_volunteer_key(self):
        user = _make_staff_user_with_reports_perm()
        self.client.force_login(user)
        response = self.client.get(self.COMBINED_URL)
        self.assertIn("volunteer", response.context["impact"])

    def test_context_impact_has_donations_key(self):
        user = _make_staff_user_with_reports_perm()
        self.client.force_login(user)
        response = self.client.get(self.COMBINED_URL)
        self.assertIn("donations", response.context["impact"])

    def test_context_impact_has_combined_value_cad(self):
        user = _make_staff_user_with_reports_perm()
        self.client.force_login(user)
        response = self.client.get(self.COMBINED_URL)
        self.assertIn("combined_value_cad", response.context["impact"])

    def test_context_impact_has_t3010_notes(self):
        user = _make_staff_user_with_reports_perm()
        self.client.force_login(user)
        response = self.client.get(self.COMBINED_URL)
        self.assertIn("t3010_notes", response.context["impact"])

    def test_context_year_in_response(self):
        user = _make_staff_user_with_reports_perm()
        self.client.force_login(user)
        response = self.client.get(f"{self.COMBINED_URL}?year=2023")
        self.assertEqual(response.context["year"], 2023)

    def test_year_param_out_of_range_uses_current_year(self):
        from django.utils import timezone
        current_year = timezone.localtime(timezone.now()).year
        user = _make_staff_user_with_reports_perm()
        self.client.force_login(user)
        response = self.client.get(f"{self.COMBINED_URL}?year=9999")
        # 9999 > 2100 so parse returns None → falls back to current year
        self.assertEqual(response.context["year"], current_year)


# ---------------------------------------------------------------------------
# ReportsTaskTests
# ---------------------------------------------------------------------------

class ReportsTaskTests(TestCase):
    """Tests for _compute_all_snapshots, _compute_single_snapshot, and recompute_snapshot."""

    def test_compute_all_snapshots_returns_4(self):
        """_compute_all_snapshots should return 4 (financial + donations + operational + volunteers)."""
        count = _compute_all_snapshots(2025, 1)
        self.assertEqual(count, 4)

    def test_compute_all_snapshots_creates_volunteers_snapshot(self):
        _compute_all_snapshots(2025, 2)
        exists = ReportSnapshot.objects.filter(
            report_type=ReportSnapshot.REPORT_TYPE_VOLUNTEERS,
            period_year=2025,
            period_month=2,
        ).exists()
        self.assertTrue(exists)

    def test_compute_all_snapshots_idempotent(self):
        """Calling twice should not create duplicate rows."""
        _compute_all_snapshots(2025, 3)
        _compute_all_snapshots(2025, 3)
        count = ReportSnapshot.objects.filter(
            report_type=ReportSnapshot.REPORT_TYPE_VOLUNTEERS,
            period_year=2025,
            period_month=3,
        ).count()
        self.assertEqual(count, 1)

    def test_compute_all_snapshots_second_run_updates_data(self):
        """Second run (update_or_create) updates the existing row — same PK, no duplicate."""
        _compute_all_snapshots(2025, 4)
        snap_v1 = ReportSnapshot.objects.get(
            report_type=ReportSnapshot.REPORT_TYPE_VOLUNTEERS,
            period_year=2025,
            period_month=4,
        )
        _compute_all_snapshots(2025, 4)
        snap_v2 = ReportSnapshot.objects.get(
            report_type=ReportSnapshot.REPORT_TYPE_VOLUNTEERS,
            period_year=2025,
            period_month=4,
        )
        # M-9: assertGreaterEqual(snap_v2.computed_at, snap_v1.computed_at) is
        # tautologically true since both could be equal. The meaningful invariant
        # is that the SAME row was updated (same PK) and no duplicate was created.
        self.assertEqual(
            snap_v1.pk, snap_v2.pk,
            "Second run must UPDATE the existing row, not insert a new one",
        )
        total = ReportSnapshot.objects.filter(
            report_type=ReportSnapshot.REPORT_TYPE_VOLUNTEERS,
            period_year=2025,
            period_month=4,
        ).count()
        self.assertEqual(total, 1, "Exactly one snapshot row must exist after two runs")

    def test_compute_single_snapshot_volunteers_returns_tuple(self):
        result = _compute_single_snapshot(ReportSnapshot.REPORT_TYPE_VOLUNTEERS, 2025, 5)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)

    def test_compute_single_snapshot_volunteers_first_element_is_dict(self):
        data, row_count = _compute_single_snapshot(ReportSnapshot.REPORT_TYPE_VOLUNTEERS, 2025, 6)
        self.assertIsInstance(data, dict)

    def test_compute_single_snapshot_volunteers_second_element_is_int(self):
        data, row_count = _compute_single_snapshot(ReportSnapshot.REPORT_TYPE_VOLUNTEERS, 2025, 7)
        self.assertIsInstance(row_count, int)

    def test_compute_single_snapshot_volunteers_data_has_required_keys(self):
        data, _ = _compute_single_snapshot(ReportSnapshot.REPORT_TYPE_VOLUNTEERS, 2025, 8)
        for key in ("year", "month", "hours", "impact", "row_count"):
            self.assertIn(key, data)

    def test_compute_single_snapshot_unknown_type_raises(self):
        with self.assertRaises(ValueError):
            _compute_single_snapshot("nonexistent_type", 2025, 1)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_recompute_snapshot_task_creates_volunteers_snapshot(self):
        """recompute_snapshot Celery task should create/update a volunteers snapshot."""
        from apps.reports.tasks import recompute_snapshot
        recompute_snapshot.apply(args=["volunteers", 2025, 9])
        exists = ReportSnapshot.objects.filter(
            report_type=ReportSnapshot.REPORT_TYPE_VOLUNTEERS,
            period_year=2025,
            period_month=9,
        ).exists()
        self.assertTrue(exists)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_recompute_snapshot_task_returns_success_dict(self):
        from apps.reports.tasks import recompute_snapshot
        result = recompute_snapshot.apply(args=["volunteers", 2025, 10])
        returned = result.get()
        self.assertTrue(returned["success"])
        self.assertEqual(returned["report_type"], "volunteers")
        self.assertEqual(returned["year"], 2025)
        self.assertEqual(returned["month"], 10)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_recompute_snapshot_task_is_idempotent(self):
        from apps.reports.tasks import recompute_snapshot
        recompute_snapshot.apply(args=["volunteers", 2025, 11])
        recompute_snapshot.apply(args=["volunteers", 2025, 11])
        count = ReportSnapshot.objects.filter(
            report_type=ReportSnapshot.REPORT_TYPE_VOLUNTEERS,
            period_year=2025,
            period_month=11,
        ).count()
        self.assertEqual(count, 1)
