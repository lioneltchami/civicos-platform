"""
test_immutable_fields.py

Tests for OfficialDonationReceipt append-only field enforcement:
  - Each immutable field cannot be changed after first save
  - document FK is explicitly mutable (has_pdf reflects document_id state)
  - has_pdf property reflects document_id state (True when document_id is not None)
  - status can be changed (mutable field)
  - cancellation_reason can be changed (mutable field)

Uses the same make_* helper pattern as test_receipt_services.py.
Bypasses PostgreSQL serial sequence by setting serial_number before save().
"""

import uuid
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.payments.models import (
    DONATION_STATUS_COMPLETED,
    Donation,
    DonationCampaign,
    OfficialDonationReceipt,
    PaymentIntent,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixture helpers (mirrors test_receipt_services.py pattern)
# ---------------------------------------------------------------------------


def make_user(email=None, **kwargs):
    email = email or f"user_{uuid.uuid4().hex[:6]}@example.com"
    return User.objects.create_user(email=email, password="testpass123", **kwargs)


def make_campaign(**kwargs):
    defaults = {
        "slug": f"campaign-{uuid.uuid4().hex[:6]}",
        "name_en": "Test Campaign",
        "start_date": date(2024, 1, 1),
        "is_active": True,
        "sort_order": 0,
        "advantage_amount": Decimal("0.00"),
    }
    defaults.update(kwargs)
    return DonationCampaign.objects.create(**defaults)


def make_payment_intent(payer, **kwargs):
    defaults = {
        "payer": payer,
        "amount": Decimal("100.00"),
        "currency": "CAD",
        "purpose": PaymentIntent.PURPOSE_DONATION,
        "status": PaymentIntent.STATUS_COMPLETED,
        "gateway": PaymentIntent.GATEWAY_STRIPE,
        "gateway_intent_id": f"pi_{uuid.uuid4().hex[:8]}",
    }
    defaults.update(kwargs)
    return PaymentIntent.objects.create(**defaults)


def make_donation(donor, payment_intent, **kwargs):
    defaults = {
        "payment_intent": payment_intent,
        "donor": donor,
        "amount": Decimal("100.00"),
        "advantage_amount": Decimal("0.00"),
        "eligible_amount": Decimal("100.00"),
        "is_recurring": False,
        "is_anonymous": False,
        "status": DONATION_STATUS_COMPLETED,
        "donor_name_snapshot": "Jane Citizen",
        "donor_address_snapshot": "123 Main St\nOttawa, ON  K1A 0A6",
    }
    defaults.update(kwargs)
    return Donation.objects.create(**defaults)


def make_receipt(donation, **kwargs):
    """Create an OfficialDonationReceipt bypassing serial_number DB sequence."""
    serial = f"2024-{str(uuid.uuid4().int % 1000000).zfill(6)}"
    defaults = {
        "donation": donation,
        "status": OfficialDonationReceipt.RECEIPT_STATUS_ISSUED,
        "donor_legal_name": "Jane Citizen",
        "donor_address_line1": "123 Main St",
        "donor_city": "Ottawa",
        "donor_province": "ON",
        "donor_postal_code": "K1A 0A6",
        "donation_date": date(2024, 6, 1),
        "receipt_date": date(2024, 6, 15),
        "eligible_amount": Decimal("100.00"),
        "advantage_amount": Decimal("0.00"),
        "advantage_description": "",
        "charity_legal_name": "Test Charity Inc.",
        "charity_registration_number": "123456789 RR 0001",
        "charity_address": "100 Charity Ave, Ottawa, ON K2A 1B2",
        "place_of_issue": "Ottawa",
        "authorized_signatory_name": "Jane Smith",
        "authorized_signatory_title": "Executive Director",
        "is_annual_consolidated": False,
    }
    defaults.update(kwargs)
    receipt = OfficialDonationReceipt(**defaults)
    receipt.serial_number = serial
    receipt.save()
    return receipt


# ---------------------------------------------------------------------------
# has_pdf property tests (no DB required, but we use TestCase for consistency)
# ---------------------------------------------------------------------------


class HasPdfPropertyTests(TestCase):
    """Unit tests for has_pdf property — no DB interaction needed.

    Wave 6: has_pdf is now bool(self.document_id), not bool(self.pdf_path).
    """

    def test_has_pdf_false_when_document_id_is_none(self):
        r = OfficialDonationReceipt()
        # document_id defaults to None when no Document is linked
        self.assertIsNone(r.document_id)
        self.assertFalse(r.has_pdf)

    def test_has_pdf_false_by_default_on_new_instance(self):
        r = OfficialDonationReceipt()
        # A fresh unsaved instance has no linked Document
        self.assertFalse(r.has_pdf)

    def test_has_pdf_true_when_document_id_set(self):
        r = OfficialDonationReceipt()
        r.document_id = uuid.uuid4()
        self.assertTrue(r.has_pdf)

    def test_has_pdf_true_with_any_non_none_document_id(self):
        r = OfficialDonationReceipt()
        r.document_id = uuid.uuid4()
        self.assertTrue(r.has_pdf)


# ---------------------------------------------------------------------------
# Immutable field enforcement tests
# ---------------------------------------------------------------------------


class ImmutableFieldsTests(TestCase):
    """Each field in _IMMUTABLE_FIELDS raises ValueError on attempted mutation."""

    def setUp(self):
        self.user = make_user()
        self.campaign = make_campaign()
        self.intent = make_payment_intent(self.user)
        self.donation = make_donation(self.user, self.intent)
        self.receipt = make_receipt(self.donation)

    def _attempt_field_change(self, field_name, new_value):
        """Set field to new_value and call save(); expects ValueError."""
        self.receipt.refresh_from_db()
        setattr(self.receipt, field_name, new_value)
        with self.assertRaises(ValueError) as ctx:
            self.receipt.save()
        # Error message should name the field
        self.assertIn(field_name, str(ctx.exception))

    def test_serial_number_immutable(self):
        self._attempt_field_change("serial_number", "9999-999999")

    def test_donor_legal_name_immutable(self):
        self._attempt_field_change("donor_legal_name", "Changed Name")

    def test_donor_address_line1_immutable(self):
        self._attempt_field_change("donor_address_line1", "999 Other St")

    def test_donor_city_immutable(self):
        self._attempt_field_change("donor_city", "Toronto")

    def test_donor_province_immutable(self):
        self._attempt_field_change("donor_province", "BC")

    def test_donor_postal_code_immutable(self):
        self._attempt_field_change("donor_postal_code", "V5K 0A1")

    def test_donation_date_immutable(self):
        self._attempt_field_change("donation_date", date(2020, 1, 1))

    def test_receipt_date_immutable(self):
        # Use a date >= donation_date (2024-06-01) to avoid the date-order check
        self._attempt_field_change("receipt_date", date(2024, 7, 1))

    def test_eligible_amount_immutable(self):
        self._attempt_field_change("eligible_amount", Decimal("999.99"))

    def test_advantage_amount_immutable(self):
        self._attempt_field_change("advantage_amount", Decimal("50.00"))

    def test_advantage_description_immutable(self):
        self._attempt_field_change("advantage_description", "Changed description")

    def test_charity_legal_name_immutable(self):
        self._attempt_field_change("charity_legal_name", "Different Charity")

    def test_charity_registration_number_immutable(self):
        self._attempt_field_change("charity_registration_number", "999999999 RR 0001")

    def test_charity_address_immutable(self):
        self._attempt_field_change("charity_address", "1 New Address, Toronto, ON M5V 1A1")

    def test_place_of_issue_immutable(self):
        self._attempt_field_change("place_of_issue", "Toronto")

    def test_authorized_signatory_name_immutable(self):
        self._attempt_field_change("authorized_signatory_name", "John Doe")

    def test_authorized_signatory_title_immutable(self):
        self._attempt_field_change("authorized_signatory_title", "CEO")

    def test_is_annual_consolidated_immutable(self):
        self._attempt_field_change("is_annual_consolidated", True)

    def test_donation_id_immutable(self):
        """donation FK (tracked as donation_id) cannot be changed."""
        # Create a second donation to swap in
        intent2 = make_payment_intent(self.user)
        donation2 = make_donation(self.user, intent2)
        self.receipt.refresh_from_db()
        self.receipt.donation = donation2
        with self.assertRaises(ValueError) as ctx:
            self.receipt.save()
        self.assertIn("donation_id", str(ctx.exception))

    def test_error_message_says_cancel_and_reissue(self):
        """Error message guides users toward the correct workflow."""
        self.receipt.refresh_from_db()
        self.receipt.donor_legal_name = "Wrong Name"
        with self.assertRaises(ValueError) as ctx:
            self.receipt.save()
        self.assertIn("Cancel", str(ctx.exception))

    def test_all_immutable_fields_are_declared(self):
        """Verify the frozenset has the expected cardinality (sanity check)."""
        # Currently 19 immutable fields
        self.assertEqual(len(OfficialDonationReceipt._IMMUTABLE_FIELDS), 19)


# ---------------------------------------------------------------------------
# Mutable field tests — these MUST succeed (no exception raised)
# ---------------------------------------------------------------------------


class MutableFieldsTests(TestCase):
    """Fields that are explicitly allowed to change after first save."""

    def setUp(self):
        self.user = make_user()
        self.campaign = make_campaign()
        self.intent = make_payment_intent(self.user)
        self.donation = make_donation(self.user, self.intent)
        self.receipt = make_receipt(self.donation)

    def test_document_can_be_changed(self):
        """document FK is NOT in _IMMUTABLE_FIELDS — it must be updatable via _base_manager."""
        self.assertNotIn("document", OfficialDonationReceipt._IMMUTABLE_FIELDS)
        self.assertNotIn("document_id", OfficialDonationReceipt._IMMUTABLE_FIELDS)

    def test_document_sets_has_pdf_true(self):
        """Setting document_id causes has_pdf to return True (in-memory check only)."""
        self.receipt.refresh_from_db()
        self.assertFalse(self.receipt.has_pdf)
        # Simulate a linked Document by setting document_id directly (no DB write needed)
        self.receipt.document_id = uuid.uuid4()
        self.assertTrue(self.receipt.has_pdf)

    def test_cancellation_reason_can_be_set(self):
        """cancellation_reason is mutable (needed for cancel workflow)."""
        self.assertNotIn("cancellation_reason", OfficialDonationReceipt._IMMUTABLE_FIELDS)

    def test_status_is_not_immutable(self):
        """status is NOT in _IMMUTABLE_FIELDS (changed via cancel/mark_superseded)."""
        self.assertNotIn("status", OfficialDonationReceipt._IMMUTABLE_FIELDS)

    def test_superseded_by_is_not_immutable(self):
        """superseded_by_id is NOT in _IMMUTABLE_FIELDS."""
        self.assertNotIn("superseded_by_id", OfficialDonationReceipt._IMMUTABLE_FIELDS)
        self.assertNotIn("superseded_by", OfficialDonationReceipt._IMMUTABLE_FIELDS)


# ---------------------------------------------------------------------------
# Delete guard tests
# ---------------------------------------------------------------------------


class DeleteGuardTests(TestCase):
    """OfficialDonationReceipt.delete() raises ValueError — records are permanent."""

    def setUp(self):
        self.user = make_user()
        self.intent = make_payment_intent(self.user)
        self.donation = make_donation(self.user, self.intent)
        self.receipt = make_receipt(self.donation)

    def test_delete_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.receipt.delete()

    def test_record_still_exists_after_delete_attempt(self):
        try:
            self.receipt.delete()
        except ValueError:
            pass
        self.assertTrue(OfficialDonationReceipt.objects.filter(pk=self.receipt.pk).exists())
