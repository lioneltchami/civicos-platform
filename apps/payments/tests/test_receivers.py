"""
H-H regression tests for apps/payments/receivers.py

Bug: get_or_create(donation=donation) without status='issued' filter returned
cancelled or superseded receipts, causing the receiver to skip replacement
receipt creation entirely.

Fix: use .get(status=RECEIPT_STATUS_ISSUED) + .create() instead of
get_or_create so that non-issued receipts do not block new receipt generation.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.payments.models import (
    DONATION_STATUS_COMPLETED,
    CharitySettings,
    Donation,
    DonationCampaign,
    OfficialDonationReceipt,
    Payment,
    PaymentIntent,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixture helpers (mirrors test_donation_receivers.py to keep tests independent)
# ---------------------------------------------------------------------------


def _make_user(email=None, **kwargs):
    email = email or f"user_{uuid.uuid4().hex[:6]}@example.com"
    return User.objects.create_user(email=email, password="testpass123", **kwargs)


def _make_campaign(**kwargs):
    defaults = {
        "slug": f"campaign-{uuid.uuid4().hex[:6]}",
        "name_en": "H-H Test Campaign",
        "start_date": date(2024, 1, 1),
        "is_active": True,
        "sort_order": 0,
        "advantage_amount": Decimal("0.00"),
    }
    defaults.update(kwargs)
    return DonationCampaign.objects.create(**defaults)


def _make_charity_settings(**kwargs):
    defaults = {
        "charity_legal_name": "H-H Test Charity Inc.",
        "charity_registration_number": "123456789 RR 0001",
        "charity_address_line1": "100 Charity Ave",
        "charity_city": "Ottawa",
        "charity_province": "ON",
        "charity_postal_code": "K2A 1B2",
        "place_of_issue": "Ottawa",
        "authorized_signatory_name": "Jane Smith",
        "authorized_signatory_title": "Executive Director",
        "is_active": True,
    }
    defaults.update(kwargs)
    return CharitySettings.objects.create(**defaults)


def _make_payment_intent(payer, **kwargs):
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


def _make_donation(donor, payment_intent, eligible_amount=Decimal("100.00"), **kwargs):
    amount = eligible_amount if "amount" not in kwargs else kwargs.pop("amount")
    defaults = {
        "payment_intent": payment_intent,
        "donor": donor,
        "amount": amount,
        "advantage_amount": Decimal("0.00"),
        "eligible_amount": eligible_amount,
        "is_recurring": False,
        "is_anonymous": False,
        "status": DONATION_STATUS_COMPLETED,
        "donor_name_snapshot": "Jane Citizen",
        "donor_address_snapshot": "123 Main St\nOttawa, ON  K1A 0A6",
    }
    defaults.update(kwargs)
    return Donation.objects.create(**defaults)


def _make_payment(intent, **kwargs):
    from django.utils import timezone

    defaults = {
        "intent": intent,
        "gateway_charge_id": f"ch_{uuid.uuid4().hex[:8]}",
        "amount_paid": Decimal("100.00"),
        "processor_fee": Decimal("0.00"),
        "net_amount": Decimal("100.00"),
        "payment_method_type": Payment.PAYMENT_METHOD_CARD,
        "paid_at": timezone.now(),
    }
    defaults.update(kwargs)
    return Payment.objects.create(**defaults)


def _make_bare_receipt(donation, status, serial=None, **kwargs):
    """Create an OfficialDonationReceipt with the given status bypassing serial sequence."""
    serial = serial or f"2024-{str(uuid.uuid4().int % 1000000).zfill(6)}"
    defaults = {
        "donation": donation,
        "status": status,
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
        "charity_legal_name": "H-H Test Charity Inc.",
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
# H-H regression test class
# ---------------------------------------------------------------------------


class ReceiptStatusFilterRegressionTests(TestCase):
    """
    H-H: on_donation_completed must look at status='issued' only.

    Before the fix, get_or_create(donation=donation) matched any existing row —
    including CANCELLED and SUPERSEDED ones — and returned created=False,
    silently skipping replacement receipt generation.
    """

    def setUp(self):
        self.user = _make_user()
        self.campaign = _make_campaign()
        self.charity = _make_charity_settings()
        self.intent = _make_payment_intent(self.user)
        self.donation = _make_donation(self.user, self.intent)
        self.payment = _make_payment(self.intent)

    def _fire(self, donation=None):
        """
        Invoke on_donation_completed with:
        - OfficialDonationReceipt.save patched to assign a fake serial number
          (bypasses PostgreSQL nextval() sequence absent in SQLite test DB)
        - generate_and_send_receipt.delay patched to a no-op
        """
        from apps.payments.receivers import on_donation_completed
        from apps.payments.tasks_receipts import generate_and_send_receipt

        _counter = [0]

        def _fake_save(receipt_instance, *args, **kwargs):
            if not receipt_instance.serial_number:
                _counter[0] += 1
                receipt_instance.serial_number = f"2026-{str(_counter[0]).zfill(6)}"
            from django.db.models import Model

            Model.save(receipt_instance, *args, **kwargs)

        d = donation or self.donation
        with patch.object(OfficialDonationReceipt, "save", _fake_save):
            with patch.object(generate_and_send_receipt, "delay", return_value=None):
                on_donation_completed(sender=Donation, donation=d, payment=self.payment)

    # ------------------------------------------------------------------
    # Test 1 — cancelled receipt must NOT block replacement
    # ------------------------------------------------------------------

    def test_cancelled_receipt_does_not_block_replacement(self):
        """
        H-H regression: a CANCELLED receipt for this donation must not prevent
        the receiver from issuing a new ISSUED receipt.

        Before fix: get_or_create matched the cancelled row, returned
        created=False, logged receipt_already_exists, and returned — donor
        never received a replacement receipt.
        """
        # Simulate a previously issued-then-cancelled receipt
        _make_bare_receipt(
            self.donation,
            status=OfficialDonationReceipt.RECEIPT_STATUS_CANCELLED,
            serial="2024-000001",
        )

        self._fire()

        issued = OfficialDonationReceipt.objects.filter(
            donation=self.donation,
            status=OfficialDonationReceipt.RECEIPT_STATUS_ISSUED,
        )
        self.assertEqual(
            issued.count(),
            1,
            "A cancelled receipt must not block replacement: one ISSUED receipt "
            "must be created after firing on_donation_completed.",
        )

    # ------------------------------------------------------------------
    # Test 2 — superseded receipt must NOT block replacement
    # ------------------------------------------------------------------

    def test_superseded_receipt_does_not_block_replacement(self):
        """
        H-H regression: a SUPERSEDED receipt must not prevent issuance of a
        new replacement receipt.
        """
        _make_bare_receipt(
            self.donation,
            status=OfficialDonationReceipt.RECEIPT_STATUS_SUPERSEDED,
            serial="2024-000002",
        )

        self._fire()

        issued = OfficialDonationReceipt.objects.filter(
            donation=self.donation,
            status=OfficialDonationReceipt.RECEIPT_STATUS_ISSUED,
        )
        self.assertEqual(
            issued.count(),
            1,
            "A superseded receipt must not block replacement: one ISSUED receipt "
            "must be created after firing on_donation_completed.",
        )

    # ------------------------------------------------------------------
    # Test 3 — existing ISSUED receipt must NOT be duplicated
    # ------------------------------------------------------------------

    def test_existing_issued_receipt_is_not_duplicated(self):
        """
        H-H regression: if a valid ISSUED receipt already exists for this
        donation, a second call to on_donation_completed must be idempotent —
        no duplicate receipt created.
        """
        existing = _make_bare_receipt(
            self.donation,
            status=OfficialDonationReceipt.RECEIPT_STATUS_ISSUED,
            serial="2024-000003",
        )

        self._fire()

        total = OfficialDonationReceipt.objects.filter(donation=self.donation).count()
        self.assertEqual(
            total,
            1,
            "Calling on_donation_completed when an ISSUED receipt already exists "
            "must be idempotent — receipt count must remain 1.",
        )
        # Verify the original receipt was not replaced
        still_exists = OfficialDonationReceipt.objects.filter(pk=existing.pk).exists()
        self.assertTrue(still_exists, "The original ISSUED receipt must not be deleted.")

    # ------------------------------------------------------------------
    # Test 4 — cancelled + superseded mix: still creates one issued receipt
    # ------------------------------------------------------------------

    def test_multiple_non_issued_receipts_do_not_block_replacement(self):
        """
        Edge case: donation has both a cancelled and a superseded receipt.
        Neither should block a fresh ISSUED receipt from being created.
        """
        _make_bare_receipt(
            self.donation,
            status=OfficialDonationReceipt.RECEIPT_STATUS_CANCELLED,
            serial="2024-000010",
        )
        _make_bare_receipt(
            self.donation,
            status=OfficialDonationReceipt.RECEIPT_STATUS_SUPERSEDED,
            serial="2024-000011",
        )

        self._fire()

        issued = OfficialDonationReceipt.objects.filter(
            donation=self.donation,
            status=OfficialDonationReceipt.RECEIPT_STATUS_ISSUED,
        )
        self.assertEqual(
            issued.count(),
            1,
            "With cancelled + superseded receipts present, one new ISSUED receipt "
            "must still be created.",
        )

    # ------------------------------------------------------------------
    # Test 5 — idempotent when fired with only issued receipt present logs correctly
    # ------------------------------------------------------------------

    def test_existing_issued_receipt_logs_already_issued(self):
        """
        When an ISSUED receipt already exists, on_donation_completed must log
        receipt_already_issued (not the old receipt_already_exists key).
        """
        from apps.payments.receivers import on_donation_completed
        from apps.payments.tasks_receipts import generate_and_send_receipt

        _make_bare_receipt(
            self.donation,
            status=OfficialDonationReceipt.RECEIPT_STATUS_ISSUED,
            serial="2024-000020",
        )

        with patch.object(generate_and_send_receipt, "delay", return_value=None):
            with self.assertLogs("apps.payments.receivers", level="INFO") as log_ctx:
                on_donation_completed(
                    sender=Donation,
                    donation=self.donation,
                    payment=self.payment,
                )

        log_output = "\n".join(log_ctx.output)
        self.assertIn(
            "receipt_already_issued",
            log_output,
            "When an issued receipt exists the log key must be receipt_already_issued.",
        )
        self.assertNotIn(
            "receipt_already_exists",
            log_output,
            "Old log key receipt_already_exists must not appear (replaced by H-H fix).",
        )
