"""
Tests for payments management commands.

Covers:
- check_missing_donor_addresses: finds issued receipts with placeholder/blank
  donor addresses and reports them without PII.
- postal_address field: verifiable on the User model after migration.
"""

import uuid
from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from apps.payments.models import (
    DONATION_STATUS_COMPLETED,
    Donation,
    OfficialDonationReceipt,
    PaymentIntent,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_user(email=None, postal_address=""):
    email = email or f"user_{uuid.uuid4().hex[:6]}@example.com"
    return User.objects.create_user(
        email=email,
        password="TestPass123!",
        postal_address=postal_address,
    )


def _make_intent(user):
    return PaymentIntent.objects.create(
        payer=user,
        amount=Decimal("100.00"),
        currency="CAD",
        purpose=PaymentIntent.PURPOSE_DONATION,
        status=PaymentIntent.STATUS_COMPLETED,
        gateway=PaymentIntent.GATEWAY_STRIPE,
        gateway_intent_id=f"pi_{uuid.uuid4().hex[:8]}",
    )


def _make_donation(user, intent, address_snapshot="123 Main St, Ottawa, ON K1A 0A6"):
    return Donation.objects.create(
        payment_intent=intent,
        donor=user,
        campaign=None,
        amount=Decimal("100.00"),
        advantage_amount=Decimal("0.00"),
        eligible_amount=Decimal("100.00"),
        is_recurring=False,
        is_anonymous=False,
        status=DONATION_STATUS_COMPLETED,
        donor_name_snapshot="Jane Citizen",
        donor_address_snapshot=address_snapshot,
    )


PLACEHOLDER = "[Address required - update donor profile]"


def _make_receipt(donation, address_line1="123 Main St"):
    serial = f"2026-{str(uuid.uuid4().int % 1000000).zfill(6)}"
    receipt = OfficialDonationReceipt(
        donation=donation,
        status=OfficialDonationReceipt.RECEIPT_STATUS_ISSUED,
        donor_legal_name="Jane Citizen",
        donor_address_line1=address_line1,
        donor_city="Ottawa",
        donor_province="ON",
        donor_postal_code="K1A 0A6",
        donation_date=date(2026, 6, 1),
        receipt_date=date(2026, 6, 15),
        eligible_amount=Decimal("100.00"),
        advantage_amount=Decimal("0.00"),
        advantage_description="",
        charity_legal_name="Test Charity Inc.",
        charity_registration_number="123456789 RR 0001",
        charity_address="100 Charity Ave, Ottawa, ON K2A 1B2",
        place_of_issue="Ottawa",
        authorized_signatory_name="Jane Smith",
        authorized_signatory_title="Executive Director",
        is_annual_consolidated=False,
    )
    receipt.serial_number = serial
    receipt.save()
    return receipt


# ---------------------------------------------------------------------------
# Tests: postal_address field on User model
# ---------------------------------------------------------------------------


class UserPostalAddressFieldTest(TestCase):
    """Verify the postal_address field exists and persists after migration."""

    def test_field_exists_and_defaults_blank(self):
        user = _make_user()
        self.assertEqual(user.postal_address, "")

    def test_field_saves_and_retrieves(self):
        address = "456 Maple Ave, Toronto, ON M5V 2H1"
        user = _make_user(postal_address=address)
        user.refresh_from_db()
        self.assertEqual(user.postal_address, address)

    def test_field_max_length_500(self):
        from django.db import models as djmodels

        field = User._meta.get_field("postal_address")
        self.assertIsInstance(field, djmodels.CharField)
        self.assertEqual(field.max_length, 500)
        self.assertTrue(field.blank)


# ---------------------------------------------------------------------------
# Tests: check_missing_donor_addresses management command
# ---------------------------------------------------------------------------


class CheckMissingDonorAddressesTest(TestCase):
    def _call_cmd(self, *args, **kwargs):
        out = StringIO()
        call_command("check_missing_donor_addresses", *args, stdout=out, **kwargs)
        return out.getvalue()

    def test_no_receipts_outputs_success(self):
        output = self._call_cmd()
        self.assertIn("No receipts", output)

    def test_placeholder_address_detected(self):
        user = _make_user()
        intent = _make_intent(user)
        donation = _make_donation(user, intent, address_snapshot=PLACEHOLDER)
        receipt = _make_receipt(donation, address_line1=PLACEHOLDER)
        output = self._call_cmd()
        self.assertIn("Found 1", output)
        self.assertIn(receipt.serial_number, output)
        self.assertNotIn(user.email, output)
        self.assertNotIn("Jane Citizen", output)

    def test_empty_address_line1_detected(self):
        user = _make_user()
        intent = _make_intent(user)
        donation = _make_donation(user, intent)
        receipt = _make_receipt(donation, address_line1="")
        output = self._call_cmd()
        self.assertIn("Found 1", output)
        self.assertIn(receipt.serial_number, output)

    def test_good_address_not_detected(self):
        user = _make_user()
        intent = _make_intent(user)
        donation = _make_donation(user, intent)
        _make_receipt(donation, address_line1="789 Oak St")
        output = self._call_cmd()
        self.assertIn("No receipts", output)

    def test_cancelled_receipt_excluded(self):
        user = _make_user()
        intent = _make_intent(user)
        donation = _make_donation(user, intent, address_snapshot=PLACEHOLDER)
        receipt = _make_receipt(donation, address_line1=PLACEHOLDER)
        # Use the model's .cancel() method which bypasses the custom manager
        receipt.cancel(reason="Test cancellation")
        output = self._call_cmd()
        self.assertIn("No receipts", output)

    def test_csv_output_format(self):
        user = _make_user()
        intent = _make_intent(user)
        donation = _make_donation(user, intent, address_snapshot=PLACEHOLDER)
        receipt = _make_receipt(donation, address_line1=PLACEHOLDER)
        output = self._call_cmd("--csv")
        self.assertIn("serial_number,donor_pk,donation_date", output)
        self.assertIn(receipt.serial_number, output)
        matching = [l for l in output.splitlines() if receipt.serial_number in l]  # noqa: E741
        self.assertTrue(len(matching) >= 1)
        self.assertIn(",", matching[0])

    def test_tax_year_filter_matches(self):
        user = _make_user()
        intent = _make_intent(user)
        donation = _make_donation(user, intent, address_snapshot=PLACEHOLDER)
        _make_receipt(donation, address_line1=PLACEHOLDER)
        output = self._call_cmd("--tax-year", "2026")
        self.assertIn("Found 1", output)

    def test_tax_year_filter_excludes_other_years(self):
        user = _make_user()
        intent = _make_intent(user)
        donation = _make_donation(user, intent, address_snapshot=PLACEHOLDER)
        _make_receipt(donation, address_line1=PLACEHOLDER)
        output = self._call_cmd("--tax-year", "2024")
        self.assertIn("No receipts", output)

    def test_multiple_receipts_all_reported(self):
        for _ in range(3):
            user = _make_user()
            intent = _make_intent(user)
            donation = _make_donation(user, intent, address_snapshot=PLACEHOLDER)
            _make_receipt(donation, address_line1=PLACEHOLDER)
        output = self._call_cmd()
        self.assertIn("Found 3", output)
