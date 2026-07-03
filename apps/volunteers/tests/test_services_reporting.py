"""
Tests for apps.volunteers.services.reporting.

IMPORTANT TEST INVARIANTS:
- No time.sleep()
- All monetary values compared as Decimal
- PIPEDA: assert no volunteer name/email appears in any return value
- All date filtering tested with explicit date fixtures
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.volunteers.models import (
    HoursLog,
    Opportunity,
    Program,
    SkillTag,
    VolunteerProfile,
)
from apps.volunteers.services.reporting import (
    hours_by_program,
    impact_value,
    t3010_volunteer_metrics,
)

User = get_user_model()

# ---------------------------------------------------------------------------
# PII fields to verify are NEVER present in reporting return values
# ---------------------------------------------------------------------------
_PII_KEYS = {"email", "name", "sin", "phone", "first_name", "last_name",
             "preferred_name", "emergency_contact_name", "emergency_contact_phone",
             "date_of_birth", "accommodation_notes"}

# ---------------------------------------------------------------------------
# Counter for unique slugs / emails
# ---------------------------------------------------------------------------
_counter = [0]


def _uid() -> int:
    _counter[0] += 1
    return _counter[0]


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def _make_user(email: str | None = None) -> User:
    n = _uid()
    email = email or f"vol{n}@test.gc.ca"
    return User.objects.create_user(email=email, password="pw")


def _make_coordinator(email: str | None = None) -> User:
    n = _uid()
    email = email or f"coord{n}@test.gc.ca"
    return User.objects.create_user(email=email, password="pw", is_staff=True)


def _make_profile(user: User) -> VolunteerProfile:
    return VolunteerProfile.objects.create(user=user)


def _make_program(coordinator: User, slug: str | None = None) -> Program:
    n = _uid()
    slug = slug or f"prog-{n}"
    return Program.objects.create(
        slug=slug,
        name_en=f"Program {n}",
        name_fr=f"Programme {n}",
        cra_category=Program.CRA_CATEGORY_OTHER,
        coordinator=coordinator,
    )


def _make_opportunity(program: Program, slug: str | None = None) -> Opportunity:
    n = _uid()
    slug = slug or f"opp-{n}"
    return Opportunity.objects.create(
        slug=slug,
        program=program,
        title_en=f"Opportunity {n}",
        title_fr=f"Opportunite {n}",
        description_en="Description",
        description_fr="Description FR",
        status=Opportunity.STATUS_PUBLISHED,
    )


def _make_approved_hours(
    profile: VolunteerProfile,
    opportunity: Opportunity,
    hours: float,
    date_val: datetime.date,
) -> HoursLog:
    h = HoursLog(
        volunteer=profile,
        opportunity=opportunity,
        hours=Decimal(str(hours)),
        date=date_val,
        status=HoursLog.STATUS_APPROVED,
        description="Test hours",
    )
    h.save()
    return h


def _make_hours(
    profile: VolunteerProfile,
    opportunity: Opportunity,
    hours: float,
    date_val: datetime.date,
    status: str = HoursLog.STATUS_PENDING,
) -> HoursLog:
    h = HoursLog(
        volunteer=profile,
        opportunity=opportunity,
        hours=Decimal(str(hours)),
        date=date_val,
        status=status,
        description="Test hours",
    )
    h.save()
    return h


# ===========================================================================
# HoursByProgramTests
# ===========================================================================

class HoursByProgramTests(TestCase):
    """Tests for hours_by_program(year, month)."""

    def setUp(self):
        self.coord = _make_coordinator()
        self.program = _make_program(self.coord)
        self.opp = _make_opportunity(self.program)

    def test_returns_approved_hours_only(self):
        """Only APPROVED logs are counted; PENDING and REJECTED are excluded."""
        vol1 = _make_profile(_make_user())
        vol2 = _make_profile(_make_user())
        vol3 = _make_profile(_make_user())
        date_val = datetime.date(2025, 6, 1)

        _make_hours(vol1, self.opp, 5, date_val, HoursLog.STATUS_PENDING)
        _make_hours(vol2, self.opp, 3, date_val, HoursLog.STATUS_REJECTED)
        _make_approved_hours(vol3, self.opp, 8, date_val)

        result = hours_by_program(2025)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["approved_hours"], Decimal("8.00"))

    def test_filters_by_year(self):
        """hours_by_program(2025) returns only 2025 data, not 2026."""
        vol = _make_profile(_make_user())
        _make_approved_hours(vol, self.opp, 10, datetime.date(2025, 3, 15))
        _make_approved_hours(vol, self.opp, 20, datetime.date(2026, 3, 15))

        result_2025 = hours_by_program(2025)
        self.assertEqual(len(result_2025), 1)
        self.assertEqual(result_2025[0]["approved_hours"], Decimal("10.00"))

        result_2026 = hours_by_program(2026)
        self.assertEqual(len(result_2026), 1)
        self.assertEqual(result_2026[0]["approved_hours"], Decimal("20.00"))

    def test_filters_by_month_when_given(self):
        """hours_by_program(2025, month=1) returns only January 2025 data."""
        vol = _make_profile(_make_user())
        _make_approved_hours(vol, self.opp, 5, datetime.date(2025, 1, 10))
        _make_approved_hours(vol, self.opp, 7, datetime.date(2025, 2, 10))

        result_jan = hours_by_program(2025, month=1)
        self.assertEqual(len(result_jan), 1)
        self.assertEqual(result_jan[0]["approved_hours"], Decimal("5.00"))

        result_feb = hours_by_program(2025, month=2)
        self.assertEqual(len(result_feb), 1)
        self.assertEqual(result_feb[0]["approved_hours"], Decimal("7.00"))

    def test_aggregates_per_opportunity(self):
        """Multiple volunteers logging to the same opportunity produce a single row with the correct sum."""
        date_val = datetime.date(2025, 4, 1)
        for _ in range(3):
            vol = _make_profile(_make_user())
            _make_approved_hours(vol, self.opp, 4, date_val)

        result = hours_by_program(2025)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["approved_hours"], Decimal("12.00"))

    def test_volunteer_count_distinct(self):
        """Same volunteer logging hours twice for the same opportunity is counted only once."""
        vol = _make_profile(_make_user())
        _make_approved_hours(vol, self.opp, 3, datetime.date(2025, 5, 1))
        _make_approved_hours(vol, self.opp, 4, datetime.date(2025, 5, 15))

        result = hours_by_program(2025)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["volunteer_count"], 1)

    def test_shift_count_uses_distinct_shifts(self):
        """shift_count is 0 when logs have no shift FK set."""
        vol = _make_profile(_make_user())
        _make_approved_hours(vol, self.opp, 4, datetime.date(2025, 6, 1))

        result = hours_by_program(2025)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["shift_count"], 0)

    def test_no_pii_in_return_value(self):
        """PIPEDA: No PII keys in any returned row."""
        vol = _make_profile(_make_user())
        _make_approved_hours(vol, self.opp, 5, datetime.date(2025, 7, 1))

        result = hours_by_program(2025)
        self.assertTrue(len(result) > 0)
        for row in result:
            overlap = _PII_KEYS & set(row.keys())
            self.assertEqual(overlap, set(), f"PII keys found in row: {overlap}")

    def test_empty_result_when_no_approved_logs(self):
        """Empty DB (no approved logs) returns an empty list."""
        result = hours_by_program(2025)
        self.assertEqual(result, [])

    def test_multiple_programs(self):
        """Hours across 2 different programs produce 2 separate rows."""
        prog2 = _make_program(self.coord)
        opp2 = _make_opportunity(prog2)

        vol = _make_profile(_make_user())
        _make_approved_hours(vol, self.opp, 6, datetime.date(2025, 8, 1))
        _make_approved_hours(vol, opp2, 9, datetime.date(2025, 8, 2))

        result = hours_by_program(2025)
        self.assertEqual(len(result), 2)
        slugs = {r["opportunity_slug"] for r in result}
        self.assertIn(self.opp.slug, slugs)
        self.assertIn(opp2.slug, slugs)


# ===========================================================================
# ImpactValueTests
# ===========================================================================

@override_settings(VOLUNTEER_MINIMUM_WAGES={
    "ON": 17.20,
    "BC": 17.40,
    "AB": 15.00,
})
class ImpactValueTests(TestCase):
    """Tests for impact_value(year, province)."""

    def setUp(self):
        self.coord = _make_coordinator()
        self.program = _make_program(self.coord)
        self.opp = _make_opportunity(self.program)

    def test_ontario_rate_used_by_default(self):
        """Default province='ON' uses the Ontario rate."""
        result = impact_value(2025)
        self.assertEqual(result["hourly_rate"], Decimal("17.20"))
        self.assertEqual(result["province"], "ON")

    def test_bc_rate(self):
        """province='BC' returns the BC rate."""
        result = impact_value(2025, province="BC")
        self.assertEqual(result["hourly_rate"], Decimal("17.40"))
        self.assertEqual(result["province"], "BC")

    def test_unknown_province_defaults_to_ontario(self):
        """Unknown province code 'ZZ' silently falls back to Ontario without raising."""
        result = impact_value(2025, province="ZZ")
        self.assertEqual(result["province"], "ON")
        self.assertEqual(result["hourly_rate"], Decimal("17.20"))

    def test_estimated_value_is_hours_times_rate(self):
        """10 approved hours in ON yields estimated_value_cad = 10 x 17.20 = 172.00."""
        vol = _make_profile(_make_user())
        _make_approved_hours(vol, self.opp, 10, datetime.date(2025, 3, 1))

        result = impact_value(2025, province="ON")
        self.assertEqual(result["total_approved_hours"], Decimal("10.00"))
        self.assertEqual(result["estimated_value_cad"], Decimal("172.00"))

    def test_zero_hours_when_no_approved_logs(self):
        """No approved logs: total_approved_hours=0 and estimated_value_cad=0."""
        result = impact_value(2025)
        self.assertEqual(result["total_approved_hours"], Decimal("0.00"))
        self.assertEqual(result["estimated_value_cad"], Decimal("0.00"))

    def test_volunteer_count_distinct(self):
        """5 distinct volunteers with approved hours yields volunteer_count=5."""
        for _ in range(5):
            vol = _make_profile(_make_user())
            _make_approved_hours(vol, self.opp, 2, datetime.date(2025, 4, 1))

        result = impact_value(2025)
        self.assertEqual(result["volunteer_count"], 5)

    def test_no_pii_in_return_keys(self):
        """PIPEDA: No PII keys appear in the return dict."""
        result = impact_value(2025)
        overlap = _PII_KEYS & set(result.keys())
        self.assertEqual(overlap, set(), f"PII keys found: {overlap}")

    def test_lowercase_province_code_falls_back_to_ontario(self):
        """Lowercase province code like 'on' (instead of 'ON') should not raise and falls back."""
        vol = _make_profile(_make_user())
        _make_approved_hours(vol, self.opp, Decimal("10.00"), datetime.date(2025, 6, 1))
        result = impact_value(2025, province="on")  # lowercase — not in VOLUNTEER_MINIMUM_WAGES
        # Should not raise; should fall back to Ontario rate
        self.assertIsNotNone(result["hourly_rate"])
        # Falls back to ON rate (17.20 per override_settings)
        self.assertEqual(result["hourly_rate"], Decimal("17.20"))

    def test_unknown_province_logs_warning(self):
        """Unknown province code should log a WARNING."""
        vol = _make_profile(_make_user())
        _make_approved_hours(vol, self.opp, Decimal("5.00"), datetime.date(2025, 6, 1))
        with self.assertLogs("apps.volunteers.services.reporting", level="WARNING") as cm:
            result = impact_value(2025, province="ZZ")
        self.assertTrue(
            any("ZZ" in msg or "unknown" in msg.lower() for msg in cm.output),
            f"Expected WARNING mentioning 'ZZ' or 'unknown', got: {cm.output}",
        )
        # Verify fallback to Ontario rate
        self.assertEqual(result["hourly_rate"], Decimal("17.20"))


# ===========================================================================
# T3010VolunteerMetricsTests
# ===========================================================================

@override_settings(VOLUNTEER_MINIMUM_WAGES={"ON": 17.20, "BC": 17.40})
class T3010VolunteerMetricsTests(TestCase):
    """Tests for t3010_volunteer_metrics(year)."""

    def setUp(self):
        self.coord = _make_coordinator()
        self.program = _make_program(self.coord)
        self.opp = _make_opportunity(self.program)

    def test_total_volunteers_distinct(self):
        """Same volunteer in multiple opportunities is counted only once."""
        opp2 = _make_opportunity(self.program)
        vol = _make_profile(_make_user())
        _make_approved_hours(vol, self.opp, 5, datetime.date(2025, 1, 1))
        _make_approved_hours(vol, opp2, 3, datetime.date(2025, 2, 1))

        result = t3010_volunteer_metrics(2025)
        self.assertEqual(result["total_volunteers"], 1)

    def test_total_hours_sum_approved_only(self):
        """Only approved HoursLog rows are summed in total_volunteer_hours."""
        vol = _make_profile(_make_user())
        _make_approved_hours(vol, self.opp, 10, datetime.date(2025, 3, 1))
        _make_hours(vol, self.opp, 8, datetime.date(2025, 3, 2), HoursLog.STATUS_PENDING)
        _make_hours(vol, self.opp, 6, datetime.date(2025, 3, 3), HoursLog.STATUS_REJECTED)

        result = t3010_volunteer_metrics(2025)
        self.assertEqual(result["total_volunteer_hours"], Decimal("10.00"))

    def test_num_programs_distinct(self):
        """Two opportunities in the same program count as 1 program."""
        opp2 = _make_opportunity(self.program)
        vol = _make_profile(_make_user())
        _make_approved_hours(vol, self.opp, 5, datetime.date(2025, 4, 1))
        _make_approved_hours(vol, opp2, 3, datetime.date(2025, 4, 2))

        result = t3010_volunteer_metrics(2025)
        self.assertEqual(result["num_programs"], 1)

    def test_categories_cover_all_hours(self):
        """Sum of all category hours equals total_volunteer_hours."""
        vol = _make_profile(_make_user())
        _make_approved_hours(vol, self.opp, 12, datetime.date(2025, 5, 1))

        result = t3010_volunteer_metrics(2025)
        total = result["total_volunteer_hours"]
        category_sum = sum(c["hours"] for c in result["categories"])
        self.assertEqual(category_sum, total)

    def test_general_category_is_default(self):
        """Untagged opportunity's hours appear in the 'general' category."""
        vol = _make_profile(_make_user())
        _make_approved_hours(vol, self.opp, 8, datetime.date(2025, 6, 1))

        result = t3010_volunteer_metrics(2025)
        general_cats = [c for c in result["categories"] if c["category"] == "general"]
        self.assertTrue(len(general_cats) > 0, "Expected a 'general' category entry")
        self.assertEqual(general_cats[0]["hours"], Decimal("8.00"))

    def test_governance_tag_maps_to_governance_category(self):
        """Opportunity tagged with slug 'governance' has hours in 'governance' category."""
        tag = SkillTag.objects.create(
            name_en="Governance",
            name_fr="Gouvernance",
            slug="governance-tag",
            category="governance",
        )
        opp_gov = _make_opportunity(self.program)
        opp_gov.required_skills.add(tag)

        vol = _make_profile(_make_user())
        _make_approved_hours(vol, opp_gov, 6, datetime.date(2025, 7, 1))

        result = t3010_volunteer_metrics(2025)
        gov_cats = [c for c in result["categories"] if c["category"] == "governance"]
        self.assertTrue(len(gov_cats) > 0, "Expected a 'governance' category entry")
        self.assertEqual(gov_cats[0]["hours"], Decimal("6.00"))

    def test_estimated_value_uses_ontario_rate(self):
        """T3010 always uses the Ontario rate for estimated_value_cad."""
        vol = _make_profile(_make_user())
        _make_approved_hours(vol, self.opp, 10, datetime.date(2025, 8, 1))

        result = t3010_volunteer_metrics(2025)
        expected = (Decimal("10.00") * Decimal("17.20")).quantize(Decimal("0.01"))
        self.assertEqual(result["estimated_value_cad"], expected)

    def test_empty_year_returns_zero_totals(self):
        """No logs in requested year returns all zeros and empty categories list."""
        result = t3010_volunteer_metrics(2099)
        self.assertEqual(result["total_volunteers"], 0)
        self.assertEqual(result["total_volunteer_hours"], Decimal("0.00"))
        self.assertEqual(result["estimated_value_cad"], Decimal("0.00"))
        self.assertEqual(result["num_programs"], 0)
        self.assertEqual(result["categories"], [])

    def test_no_pii_in_return_keys(self):
        """PIPEDA: No PII keys in top-level return dict."""
        result = t3010_volunteer_metrics(2025)
        overlap = _PII_KEYS & set(result.keys())
        self.assertEqual(overlap, set(), f"PII keys found: {overlap}")

    def test_multiple_programs_distinct_count(self):
        """2 distinct programs with approved hours yields num_programs=2."""
        prog2 = _make_program(self.coord)
        opp2 = _make_opportunity(prog2)
        vol = _make_profile(_make_user())
        _make_approved_hours(vol, self.opp, 5, datetime.date(2025, 9, 1))
        _make_approved_hours(vol, opp2, 3, datetime.date(2025, 9, 2))

        result = t3010_volunteer_metrics(2025)
        self.assertEqual(result["num_programs"], 2)
