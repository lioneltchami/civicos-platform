"""
Integration Wave — Portal dashboard integration tests.

Covers:
  - DashboardView auth (anonymous → 302)
  - volunteer_summary widget context (has_profile, active_application_count,
    upcoming_shift_count)
  - donation_summary widget context (ytd_total, ytd_count, last_receipt)
  - PIPEDA receipt isolation (cross-user, ordering)
  - Template rendering of widget sections

Settings: --settings=config.settings.test
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

User = get_user_model()

DASHBOARD_URL = "/portal/"
VALID_PASSWORD = "SecureTestPass123!"

# ---------------------------------------------------------------------------
# Shared factory helpers
# ---------------------------------------------------------------------------

_counter = [0]


def _uid():
    _counter[0] += 1
    return _counter[0]


def _make_user(email=None, **kwargs):
    n = _uid()
    email = email or f"portal_user_{n}@example.gc.ca"
    return User.objects.create_user(email=email, password=VALID_PASSWORD, **kwargs)


def _make_program(**kwargs):
    from apps.volunteers.models import Program

    n = _uid()
    defaults = {
        "name_en": f"Program {n}",
        "name_fr": f"Programme {n}",
        "slug": f"pdprog-{n}",
        "cra_category": "welfare",
    }
    defaults.update(kwargs)
    return Program.objects.create(**defaults)


def _make_opportunity(program, **kwargs):
    from apps.volunteers.models import Opportunity

    n = _uid()
    defaults = {
        "title_en": f"Opportunity {n}",
        "title_fr": f"Opportunité {n}",
        "slug": f"pdopp-{n}",
        "description_en": "Desc",
        "description_fr": "Desc FR",
        "program": program,
        "status": "published",
    }
    defaults.update(kwargs)
    return Opportunity.objects.create(**defaults)


def _make_profile(user):
    from apps.volunteers.models import VolunteerProfile

    return VolunteerProfile.objects.create(user=user)


def _make_application(profile, opportunity, *, status="pending"):
    from apps.volunteers.models import VolunteerApplication

    return VolunteerApplication.objects.create(
        volunteer=profile,
        opportunity=opportunity,
        status=status,
    )


def _make_shift(opportunity, *, start_datetime=None, end_datetime=None):
    from apps.volunteers.models import Shift

    now = timezone.now()
    start = start_datetime or now
    # Ensure end > start
    from datetime import timedelta

    end = end_datetime or (start + timedelta(hours=2))
    return Shift.objects.create(
        opportunity=opportunity,
        start_datetime=start,
        end_datetime=end,
        title_en="Shift",
        title_fr="Quart",
    )


def _make_shift_booking(profile, shift, *, status="confirmed"):
    from apps.volunteers.models import ShiftBooking

    return ShiftBooking.objects.create(
        volunteer=profile,
        shift=shift,
        status=status,
    )


def _make_donation(user, *, amount="50.00", status="completed", year=None):
    from apps.payments.models import Donation, PaymentIntent

    current_year = timezone.localtime(timezone.now()).year
    year = year or current_year
    pi = PaymentIntent.objects.create(
        payer=user,
        amount=Decimal(amount),
        currency="CAD",
        purpose=PaymentIntent.PURPOSE_DONATION,
        status=PaymentIntent.STATUS_COMPLETED,
        gateway=PaymentIntent.GATEWAY_STRIPE,
        gateway_intent_id=f"pi_test_{_uid()}",
    )
    donation = Donation.objects.create(
        payment_intent=pi,
        donor=user,
        amount=Decimal(amount),
        eligible_amount=Decimal(amount),
        status=status,
        donor_name_snapshot="Test Donor",
        donor_address_snapshot="123 Test St, Toronto, ON",
    )
    # Set created_at to target year if different from current
    if year != current_year:
        from datetime import datetime as _datetime

        target_dt = timezone.make_aware(_datetime(year, 6, 15, 12, 0, 0))
        Donation.objects.filter(pk=donation.pk).update(created_at=target_dt)
    return donation


def _make_receipt(donation, *, serial_number=None, eligible_amount=None):
    from datetime import date as _date

    from apps.payments.models import OfficialDonationReceipt

    n = _uid()
    # serial_number must match r"^\d{4}-\d{6}$"
    sn = serial_number or f"2024-{n:06d}"
    today = _date.today()
    return OfficialDonationReceipt.objects.create(
        donation=donation,
        serial_number=sn,
        eligible_amount=eligible_amount or donation.amount,
        status="issued",
        donor_legal_name="Test Donor",
        donor_address_line1="123 Test St",
        donor_city="Toronto",
        donor_province="ON",
        donor_postal_code="M5V 0A1",
        donation_date=today,
        receipt_date=today,
        charity_legal_name="Test Charity",
        charity_registration_number="123456789RR0001",
        charity_address="456 Charity Ave, Toronto, ON",
        place_of_issue="Toronto, Ontario",
        authorized_signatory_name="Jane Smith",
        authorized_signatory_title="Executive Director",
    )


# ---------------------------------------------------------------------------
# DashboardAuthTests
# ---------------------------------------------------------------------------


class DashboardAuthTests(TestCase):
    """Authentication and basic access tests for the dashboard."""

    def test_anonymous_redirects_to_login(self):
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_authenticated_user_gets_200(self):
        user = _make_user()
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)

    def test_dashboard_uses_correct_template(self):
        user = _make_user()
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertTemplateUsed(response, "portal/dashboard.html")

    def test_staff_user_gets_200(self):
        user = _make_user(is_staff=True)
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)

    def test_context_has_expected_service_request_keys(self):
        user = _make_user()
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertIn("total_requests", response.context)
        self.assertIn("active_requests", response.context)
        self.assertIn("completed_requests", response.context)

    def test_context_has_volunteer_summary_key(self):
        """volunteer_summary key must always be present in context (may be None)."""
        user = _make_user()
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertIn("volunteer_summary", response.context)

    def test_context_has_donation_summary_key(self):
        """donation_summary key must always be present in context (may be None)."""
        user = _make_user()
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertIn("donation_summary", response.context)


# ---------------------------------------------------------------------------
# DashboardVolunteerWidgetTests
# ---------------------------------------------------------------------------


class DashboardVolunteerWidgetTests(TestCase):
    """
    Tests for the volunteer_summary widget.

    M-E: The original docstring referenced a shift__start_time field-name bug
    that caused a FieldError and set volunteer_summary=None. That bug is fixed
    (the view now uses shift__start_datetime). Tests are now unconditional:
    volunteer_summary must be a dict, not None, for authenticated users.
    """

    def test_user_with_no_volunteer_profile_volunteer_summary_has_profile_false(self):
        """
        M-E: User with no VolunteerProfile must receive volunteer_summary with
        has_profile=False — NOT None. The inner DoesNotExist handler sets the
        fallback and the outer try must not swallow it as an exception.
        """
        user = _make_user()
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)
        vol_sum = response.context.get("volunteer_summary")
        # M-E: must be a dict (not None) with has_profile=False
        self.assertIsNotNone(vol_sum, "volunteer_summary must not be None for authenticated user")
        self.assertFalse(
            vol_sum.get("has_profile", True), "has_profile must be False when no profile exists"
        )

    def test_user_without_profile_volunteer_summary_has_profile_false(self):
        """No VolunteerProfile → has_profile=False (unconditional)."""
        user = _make_user()
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        vol_sum = response.context.get("volunteer_summary")
        self.assertIsNotNone(vol_sum)
        self.assertFalse(vol_sum["has_profile"])

    def test_volunteer_summary_key_present_in_context(self):
        user = _make_user()
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertIn("volunteer_summary", response.context)

    def test_user_with_volunteer_profile_volunteer_summary_present(self):
        """
        C-1 fix: with volunteer=_profile (correct FK name) and in_review status,
        the view succeeds and volunteer_summary is a dict with has_profile=True.
        """
        user = _make_user()
        _make_profile(user)
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)
        vol_sum = response.context.get("volunteer_summary")
        self.assertIsNotNone(vol_sum)
        self.assertTrue(vol_sum["has_profile"])
        self.assertIn("active_application_count", vol_sum)
        self.assertIn("upcoming_shift_count", vol_sum)

    def test_volunteer_summary_none_does_not_crash_page(self):
        """Page must render 200 even when volunteer_summary is None."""
        user = _make_user()
        _make_profile(user)
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)

    def test_pending_application_counted_in_active(self):
        """
        C-1 fix: with volunteer=_profile (correct FK name), a pending application
        is correctly reflected in active_application_count.
        """
        user = _make_user()
        profile = _make_profile(user)
        program = _make_program()
        opp = _make_opportunity(program)
        _make_application(profile, opp, status="pending")
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        vol_sum = response.context.get("volunteer_summary")
        self.assertIsNotNone(vol_sum)
        self.assertTrue(vol_sum["has_profile"])
        self.assertEqual(vol_sum["active_application_count"], 1)

    def test_rejected_application_not_counted(self):
        """
        C-1 fix: rejected application must NOT appear in active_application_count.
        Status 'rejected' is excluded from the filter (pending/in_review/approved only).
        """
        user = _make_user()
        profile = _make_profile(user)
        program = _make_program()
        opp = _make_opportunity(program)
        _make_application(profile, opp, status="rejected")
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)
        vol_sum = response.context.get("volunteer_summary")
        self.assertIsNotNone(vol_sum)
        self.assertTrue(vol_sum["has_profile"])
        # Rejected application must not be counted
        self.assertEqual(vol_sum["active_application_count"], 0)

    def test_volunteer_summary_none_means_no_crash(self):
        """Graceful degradation: when volunteer_summary = None, template must not raise."""
        user = _make_user()
        _make_profile(user)
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# DashboardDonationWidgetTests
# ---------------------------------------------------------------------------


class DashboardDonationWidgetTests(TestCase):
    """Tests for the donation_summary widget context."""

    def _current_year(self):
        return timezone.localtime(timezone.now()).year

    def test_user_with_no_donations_ytd_total_is_zero(self):
        user = _make_user()
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)
        # P2-3: assertIsNotNone first — the if-guard was vacuous (passes silently
        # when the view returns None, hiding the regression it was supposed to catch).
        don_sum = response.context.get("donation_summary")
        self.assertIsNotNone(don_sum, "donation_summary must be present in context")
        self.assertEqual(don_sum["ytd_total"], Decimal("0.00"))

    def test_user_with_no_donations_ytd_count_is_zero(self):
        user = _make_user()
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        don_sum = response.context.get("donation_summary")
        self.assertIsNotNone(don_sum, "donation_summary must be present in context")
        self.assertEqual(don_sum["ytd_count"], 0)

    def test_user_with_no_donations_last_receipt_is_none(self):
        user = _make_user()
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        don_sum = response.context.get("donation_summary")
        self.assertIsNotNone(don_sum, "donation_summary must be present in context")
        self.assertIsNone(don_sum["last_receipt"])

    def test_donation_summary_key_present(self):
        user = _make_user()
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertIn("donation_summary", response.context)

    def test_completed_donation_appears_in_ytd_total(self):
        user = _make_user()
        _make_donation(user, amount="75.00", status="completed")
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        don_sum = response.context.get("donation_summary")
        self.assertIsNotNone(don_sum)
        self.assertEqual(don_sum["ytd_total"], Decimal("75.00"))

    def test_completed_donation_counted_in_ytd_count(self):
        user = _make_user()
        _make_donation(user, amount="50.00", status="completed")
        _make_donation(user, amount="25.00", status="completed")
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        don_sum = response.context.get("donation_summary")
        self.assertIsNotNone(don_sum)
        self.assertEqual(don_sum["ytd_count"], 2)

    def test_ytd_total_sums_multiple_completed_donations(self):
        user = _make_user()
        _make_donation(user, amount="100.00", status="completed")
        _make_donation(user, amount="200.00", status="completed")
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        don_sum = response.context.get("donation_summary")
        self.assertIsNotNone(don_sum)
        self.assertEqual(don_sum["ytd_total"], Decimal("300.00"))

    def test_pending_donation_not_counted(self):
        """Pending donations must NOT appear in YTD total."""
        user = _make_user()
        _make_donation(user, amount="99.00", status="pending")
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        don_sum = response.context.get("donation_summary")
        self.assertIsNotNone(don_sum)
        self.assertEqual(don_sum["ytd_total"], Decimal("0.00"))
        self.assertEqual(don_sum["ytd_count"], 0)

    def test_failed_donation_not_counted(self):
        user = _make_user()
        _make_donation(user, amount="50.00", status="failed")
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        don_sum = response.context.get("donation_summary")
        self.assertIsNotNone(don_sum)
        self.assertEqual(don_sum["ytd_total"], Decimal("0.00"))

    def test_prior_year_donation_not_counted(self):
        """Prior year donations must not appear in current-year YTD."""
        user = _make_user()
        prior_year = self._current_year() - 1
        _make_donation(user, amount="500.00", status="completed", year=prior_year)
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        don_sum = response.context.get("donation_summary")
        self.assertIsNotNone(don_sum)
        self.assertEqual(don_sum["ytd_total"], Decimal("0.00"))

    def test_only_own_donations_counted(self):
        """Donations from other users must not appear."""
        user = _make_user()
        other_user = _make_user()
        _make_donation(other_user, amount="999.00", status="completed")
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        don_sum = response.context.get("donation_summary")
        self.assertIsNotNone(don_sum)
        self.assertEqual(don_sum["ytd_total"], Decimal("0.00"))

    def test_donation_summary_has_year_key(self):
        user = _make_user()
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        don_sum = response.context.get("donation_summary")
        # P2-3: unconditional — vacuous if-guard removed.
        self.assertIsNotNone(don_sum, "donation_summary must be present in context")
        self.assertIn("year", don_sum)
        self.assertEqual(don_sum["year"], self._current_year())

    def test_last_receipt_none_when_no_receipts(self):
        user = _make_user()
        _make_donation(user, amount="50.00", status="completed")
        # No receipt created
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        don_sum = response.context.get("donation_summary")
        self.assertIsNotNone(don_sum)
        self.assertIsNone(don_sum["last_receipt"])

    def test_last_receipt_has_expected_keys_when_receipt_exists(self):
        user = _make_user()
        donation = _make_donation(user, amount="100.00", status="completed")
        _make_receipt(donation, serial_number="2024-000001", eligible_amount=Decimal("100.00"))
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        don_sum = response.context.get("donation_summary")
        self.assertIsNotNone(don_sum)
        last_receipt = don_sum.get("last_receipt")
        self.assertIsNotNone(last_receipt)
        self.assertIn("serial_number", last_receipt)
        self.assertIn("issued_at", last_receipt)
        self.assertIn("eligible_amount", last_receipt)

    def test_last_receipt_serial_number_correct(self):
        user = _make_user()
        donation = _make_donation(user, amount="200.00", status="completed")
        # C-4: serial_number must match r"^\d{4}-\d{6}$" production constraint.
        _make_receipt(donation, serial_number="2026-000999")
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        don_sum = response.context.get("donation_summary")
        self.assertIsNotNone(don_sum)
        last_receipt = don_sum.get("last_receipt")
        self.assertIsNotNone(last_receipt)
        self.assertEqual(last_receipt["serial_number"], "2026-000999")

    def test_donation_summary_graceful_degradation_when_exception(self):
        """
        When Donation.objects.filter raises (DB outage, etc.), the outer
        except Exception in DashboardView sets donation_summary=None and
        the page still renders 200.

        C-3 fix: patch Donation.objects.filter (not Donation.objects) so the
        side_effect actually fires when .filter() is called on the manager.
        """
        from unittest.mock import patch

        user = _make_user()
        self.client.force_login(user)
        with patch(
            "apps.payments.models.Donation.objects.filter",
            side_effect=Exception("DB unavailable"),
        ):
            response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)
        # The exception should have been caught; donation_summary is None.
        don_sum = response.context.get("donation_summary")
        self.assertIsNone(don_sum)


# ---------------------------------------------------------------------------
# DashboardTemplateRenderTests
# ---------------------------------------------------------------------------


class DashboardTemplateRenderTests(TestCase):
    """Tests for template rendering of widget sections."""

    def test_page_renders_200_without_any_data(self):
        user = _make_user()
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)

    def test_page_renders_with_donation_data(self):
        user = _make_user()
        _make_donation(user, amount="50.00", status="completed")
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)

    def test_volunteer_summary_none_renders_without_error(self):
        """Template must handle volunteer_summary=None gracefully (no TemplateSyntaxError)."""
        user = _make_user()
        _make_profile(user)
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)
        # If volunteer_summary is None (due to FieldError), page still renders
        self.assertNotIn(b"TemplateSyntaxError", response.content)
        self.assertNotIn(b"FieldError", response.content)

    def test_donation_summary_none_renders_without_error(self):
        """Template must handle donation_summary=None gracefully."""
        from unittest.mock import patch

        user = _make_user()
        self.client.force_login(user)
        # Patch to force donation_summary = None by raising inside the view
        with patch("apps.payments.models.Donation.objects.filter", side_effect=Exception("forced")):
            response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)

    def test_ytd_total_rendered_in_page_when_donation_exists(self):
        """When donation_summary has data, the YTD amount should appear in rendered HTML."""
        user = _make_user()
        _make_donation(user, amount="123.00", status="completed")
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)
        don_sum = response.context.get("donation_summary")
        # P2-3: unconditional — double vacuous guard (None check + truthiness) removed.
        self.assertIsNotNone(don_sum, "donation_summary must be present when donation exists")
        self.assertTrue(don_sum.get("ytd_total"), "ytd_total must be non-zero for a $123 donation")
        # The template renders ytd_total with floatformat:2
        self.assertContains(response, "123.00")

    def test_page_title_or_heading_present(self):
        """Basic smoke test that the dashboard HTML is non-empty and meaningful."""
        user = _make_user()
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertGreater(len(response.content), 200)

    def test_recent_requests_section_present_when_context_set(self):
        user = _make_user()
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertIn("recent_requests", response.context)

    def test_total_requests_is_zero_for_new_user(self):
        user = _make_user()
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.context["total_requests"], 0)

    def test_active_requests_is_zero_for_new_user(self):
        user = _make_user()
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.context["active_requests"], 0)


# ---------------------------------------------------------------------------
# DashboardReceiptPipedaTests — M-10
# ---------------------------------------------------------------------------


class DashboardReceiptPipedaTests(TestCase):
    """
    PIPEDA compliance: last_receipt must expose ONLY the three fields fetched
    by .values("serial_number", "issued_at", "eligible_amount") in DashboardView.
    No donor PII (name, address, postal code, etc.) must leak into the context.
    """

    # Full set of PII field names that must never appear in last_receipt.
    _PII_FIELDS = {  # noqa: RUF012
        "donor_legal_name",
        "donor_address_line1",
        "donor_address_line2",
        "donor_city",
        "donor_province",
        "donor_postal_code",
        "donor_email",
        "charity_legal_name",
        "charity_registration_number",
        "charity_address",
        "authorized_signatory_name",
        "authorized_signatory_title",
    }

    _ALLOWED_KEYS = {"serial_number", "issued_at", "eligible_amount"}  # noqa: RUF012

    def test_last_receipt_exposes_only_three_allowed_keys(self):
        """last_receipt dict must have exactly the three .values() fields — no more, no less."""
        user = _make_user()
        donation = _make_donation(user, amount="100.00", status="completed")
        _make_receipt(donation, serial_number="2026-000042", eligible_amount=Decimal("100.00"))
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        don_sum = response.context.get("donation_summary")
        self.assertIsNotNone(
            don_sum, "donation_summary must not be None when Payments BB is installed"
        )
        last_receipt = don_sum.get("last_receipt")
        self.assertIsNotNone(last_receipt, "last_receipt must be present when a receipt exists")
        # L-6: assertIsInstance catches the case where a developer replaces
        # .values("serial_number", ...) with a full ORM instance — last_receipt.keys()
        # would raise AttributeError rather than a clean AssertionError without this guard.
        self.assertIsInstance(
            last_receipt,
            dict,
            "last_receipt must be a plain dict from .values(), not an ORM instance",
        )
        actual_keys = set(last_receipt.keys())
        self.assertEqual(
            actual_keys,
            self._ALLOWED_KEYS,
            f"last_receipt must expose only {self._ALLOWED_KEYS!r}, got {actual_keys!r}",
        )

    def test_last_receipt_contains_no_pii_fields(self):
        """None of the donor PII fields must appear as keys in last_receipt."""
        user = _make_user()
        donation = _make_donation(user, amount="50.00", status="completed")
        _make_receipt(donation, serial_number="2026-000043", eligible_amount=Decimal("50.00"))
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        don_sum = response.context.get("donation_summary")
        self.assertIsNotNone(don_sum)
        last_receipt = don_sum.get("last_receipt")
        self.assertIsNotNone(last_receipt)
        leaked = self._PII_FIELDS & set(last_receipt.keys())
        self.assertFalse(
            leaked,
            f"PIPEDA violation: last_receipt leaks PII fields: {leaked!r}",
        )

    def test_last_receipt_serial_number_value_is_correct(self):
        """serial_number value must match what was stored."""
        user = _make_user()
        donation = _make_donation(user, amount="75.00", status="completed")
        _make_receipt(donation, serial_number="2026-000099", eligible_amount=Decimal("75.00"))
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        don_sum = response.context.get("donation_summary")
        self.assertIsNotNone(don_sum)
        last_receipt = don_sum.get("last_receipt")
        self.assertIsNotNone(last_receipt)
        self.assertEqual(last_receipt["serial_number"], "2026-000099")

    def test_last_receipt_eligible_amount_value_is_correct(self):
        """eligible_amount value must match what was stored."""
        user = _make_user()
        donation = _make_donation(user, amount="250.00", status="completed")
        _make_receipt(donation, serial_number="2026-000100", eligible_amount=Decimal("250.00"))
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        don_sum = response.context.get("donation_summary")
        self.assertIsNotNone(don_sum)
        last_receipt = don_sum.get("last_receipt")
        self.assertIsNotNone(last_receipt)
        self.assertEqual(last_receipt["eligible_amount"], Decimal("250.00"))

    def test_last_receipt_none_when_no_receipts_issued(self):
        """No issued receipt → last_receipt must be None (no empty dict, no crash)."""
        user = _make_user()
        _make_donation(user, amount="50.00", status="completed")
        # Deliberately no receipt created
        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        don_sum = response.context.get("donation_summary")
        self.assertIsNotNone(don_sum)
        self.assertIsNone(
            don_sum["last_receipt"],
            "last_receipt must be None when no issued receipt exists",
        )

    def test_last_receipt_ordering_returns_most_recent(self):
        """
        M-J: When a donor has multiple issued receipts, last_receipt must be the
        most recently issued one (order_by("-issued_at").first()).

        This is the most operationally important behaviour in the donation widget:
        showing the wrong (older) receipt to a citizen is a UX defect and may
        cause confusion during tax filing. Yet it was previously untested.
        """
        from datetime import timedelta as _td

        from apps.payments.models import OfficialDonationReceipt

        user = _make_user()
        donation_old = _make_donation(user, amount="100.00", status="completed")
        donation_new = _make_donation(user, amount="200.00", status="completed")

        from django.utils import timezone as _tz

        today = _tz.now()
        # Older receipt
        older = _make_receipt(
            donation_old,
            serial_number="2026-000010",
            eligible_amount=Decimal("100.00"),
        )
        # Use _base_manager to bypass the custom QuerySet.update() guard —
        # the same technique used by OfficialDonationReceipt.cancel().
        OfficialDonationReceipt._base_manager.filter(pk=older.pk).update(
            issued_at=today - _td(days=30)
        )
        # Newer receipt — must be the one returned
        newer = _make_receipt(
            donation_new,
            serial_number="2026-000020",
            eligible_amount=Decimal("200.00"),
        )
        OfficialDonationReceipt._base_manager.filter(pk=newer.pk).update(issued_at=today)

        self.client.force_login(user)
        response = self.client.get(DASHBOARD_URL)
        don_sum = response.context.get("donation_summary")
        self.assertIsNotNone(don_sum)
        last_receipt = don_sum.get("last_receipt")
        self.assertIsNotNone(last_receipt, "last_receipt must not be None when receipts exist")
        self.assertEqual(
            last_receipt["serial_number"],
            "2026-000020",
            "last_receipt must be the most recently issued receipt (serial 2026-000020), "
            "not the older one (2026-000010)",
        )
        self.assertEqual(last_receipt["eligible_amount"], Decimal("200.00"))

    def test_cross_user_receipt_isolation(self):
        """
        H-E: PIPEDA cross-user isolation — user A must not see user B's receipt.

        The view filters by `donation__donor=self.request.user`. This test guards
        against a regression that removes or widens that filter, which would expose
        another citizen's donation history.
        """
        # User B has a receipt; user A has none.
        user_a = _make_user()
        user_b = _make_user()
        donation_b = _make_donation(user_b, amount="500.00", status="completed")
        _make_receipt(
            donation_b,
            serial_number="2026-000888",
            eligible_amount=Decimal("500.00"),
        )
        # Log in as user A — must see last_receipt=None despite user B having a receipt.
        self.client.force_login(user_a)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)
        don_sum = response.context.get("donation_summary")
        self.assertIsNotNone(
            don_sum,
            "donation_summary must be present for authenticated user",
        )
        self.assertIsNone(
            don_sum["last_receipt"],
            "PIPEDA violation: user A's last_receipt must be None — "
            "user B's receipt must not appear in user A's context",
        )
