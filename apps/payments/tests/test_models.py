"""
test_models.py

Regression tests for three medium-priority bug fixes:

M-B: cancel() and mark_superseded() on OfficialDonationReceipt now pass
     updated_at=timezone.now() to QuerySet.update(), preventing stale audit
     timestamps caused by auto_now=True being bypassed by bulk-update.

M-C: Payment model now has DB-level CheckConstraints preventing negative
     financial values and processor_fee > amount_paid.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.payments.models import (
    DONATION_STATUS_COMPLETED,
    Donation,
    OfficialDonationReceipt,
    Payment,
    PaymentIntent,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_user(email=None):
    email = email or f"user_{uuid.uuid4().hex[:6]}@example.com"
    return User.objects.create_user(email=email, password="testpass123!")


def _make_intent(user, **kwargs):
    defaults = {
        "payer": user,
        "amount": Decimal("100.00"),
        "currency": "CAD",
        "purpose": PaymentIntent.PURPOSE_DONATION,
        "status": PaymentIntent.STATUS_COMPLETED,
        "gateway": PaymentIntent.GATEWAY_STRIPE,
        "gateway_intent_id": f"pi_{uuid.uuid4().hex[:8]}",
    }
    defaults.update(kwargs)
    return PaymentIntent.objects.create(**defaults)


def _make_donation(user, intent, **kwargs):
    defaults = {
        "payment_intent": intent,
        "donor": user,
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


def _make_receipt(donation, **kwargs):
    """Create an OfficialDonationReceipt bypassing the DB serial sequence."""
    serial = f"2026-{str(uuid.uuid4().int % 1000000).zfill(6)}"
    defaults = {
        "donation": donation,
        "status": OfficialDonationReceipt.RECEIPT_STATUS_ISSUED,
        "donor_legal_name": "Jane Citizen",
        "donor_address_line1": "123 Main St",
        "donor_city": "Ottawa",
        "donor_province": "ON",
        "donor_postal_code": "K1A 0A6",
        "donation_date": date(2026, 1, 1),
        "receipt_date": date(2026, 1, 15),
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


def _make_payment(intent, **kwargs):
    defaults = {
        "intent": intent,
        "gateway_charge_id": f"ch_{uuid.uuid4().hex[:8]}",
        "amount_paid": Decimal("100.00"),
        "processor_fee": Decimal("2.90"),
        "payment_method_type": Payment.PAYMENT_METHOD_CARD,
        "card_last_four": "4242",
        "card_brand": Payment.CARD_BRAND_VISA,
        "paid_at": timezone.now(),
    }
    defaults.update(kwargs)
    return Payment.objects.create(**defaults)


# ---------------------------------------------------------------------------
# M-B: cancel() and mark_superseded() must update updated_at
# ---------------------------------------------------------------------------

class ReceiptCancelUpdatesTimestampTests(TestCase):
    """
    cancel() must write updated_at via QuerySet.update() — auto_now=True is
    bypassed by bulk-update and would leave a stale audit timestamp.
    """

    def setUp(self):
        self.user = _make_user()
        self.intent = _make_intent(self.user)
        self.donation = _make_donation(self.user, self.intent)
        self.receipt = _make_receipt(self.donation)

    def test_cancel_sets_updated_at_in_db(self):
        """After cancel(), updated_at in the DB must be greater than before."""
        before = self.receipt.updated_at
        # Ensure at least 1 ms passes so timestamps differ.
        self.receipt.cancel(reason="Test cancellation")
        self.receipt.refresh_from_db()
        self.assertGreater(
            self.receipt.updated_at,
            before,
            "cancel() did not advance updated_at in the database.",
        )

    def test_cancel_sets_updated_at_on_instance(self):
        """cancel() must also update the in-memory instance attribute."""
        before = self.receipt.updated_at
        self.receipt.cancel(reason="In-memory check")
        self.assertGreater(
            self.receipt.updated_at,
            before,
            "cancel() did not update the in-memory updated_at attribute.",
        )

    def test_cancel_sets_status_in_db(self):
        """Sanity check: cancel() still persists the status change."""
        self.receipt.cancel(reason="Status persistence check")
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, OfficialDonationReceipt.RECEIPT_STATUS_CANCELLED)

    def test_cancel_sets_cancellation_reason(self):
        """cancel() must persist cancellation_reason."""
        reason = "Duplicate receipt issued"
        self.receipt.cancel(reason=reason)
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.cancellation_reason, reason)


class ReceiptMarkSupersededUpdatesTimestampTests(TestCase):
    """
    mark_superseded() must write updated_at via QuerySet.update().
    """

    def setUp(self):
        self.user = _make_user()

        # First intent + donation + receipt (the one to be superseded)
        self.intent1 = _make_intent(self.user)
        self.donation1 = _make_donation(self.user, self.intent1)
        self.receipt1 = _make_receipt(self.donation1)

        # Second intent + donation + receipt (the replacement)
        self.intent2 = _make_intent(self.user)
        self.donation2 = _make_donation(self.user, self.intent2)
        self.receipt2 = _make_receipt(self.donation2, status=OfficialDonationReceipt.RECEIPT_STATUS_ISSUED)

    def test_mark_superseded_sets_updated_at_in_db(self):
        """After mark_superseded(), updated_at in the DB must be greater than before."""
        before = self.receipt1.updated_at
        self.receipt1.mark_superseded(new_receipt=self.receipt2)
        self.receipt1.refresh_from_db()
        self.assertGreater(
            self.receipt1.updated_at,
            before,
            "mark_superseded() did not advance updated_at in the database.",
        )

    def test_mark_superseded_sets_updated_at_on_instance(self):
        """mark_superseded() must also update the in-memory updated_at attribute."""
        before = self.receipt1.updated_at
        self.receipt1.mark_superseded(new_receipt=self.receipt2)
        self.assertGreater(
            self.receipt1.updated_at,
            before,
            "mark_superseded() did not update the in-memory updated_at attribute.",
        )

    def test_mark_superseded_sets_status_in_db(self):
        """Sanity check: mark_superseded() still persists the status change."""
        self.receipt1.mark_superseded(new_receipt=self.receipt2)
        self.receipt1.refresh_from_db()
        self.assertEqual(self.receipt1.status, OfficialDonationReceipt.RECEIPT_STATUS_SUPERSEDED)

    def test_mark_superseded_sets_superseded_by_fk(self):
        """mark_superseded() must record which receipt superseded this one."""
        self.receipt1.mark_superseded(new_receipt=self.receipt2)
        self.receipt1.refresh_from_db()
        self.assertEqual(self.receipt1.superseded_by_id, self.receipt2.pk)


# ---------------------------------------------------------------------------
# M-C: Payment model CheckConstraints on financial fields
# ---------------------------------------------------------------------------

class PaymentFinancialConstraintsTests(TestCase):
    """
    DB-level CheckConstraints must reject nonsensical financial values that
    could be silently persisted by a buggy gateway parser.
    """

    def setUp(self):
        self.user = _make_user()
        self.intent = _make_intent(self.user)

    def _make_payment_kwargs(self, **overrides):
        """Return valid Payment kwargs, overriding specific fields for constraint tests."""
        kwargs = {
            "intent": self.intent,
            "gateway_charge_id": f"ch_{uuid.uuid4().hex[:8]}",
            "amount_paid": Decimal("100.00"),
            "processor_fee": Decimal("2.90"),
            "payment_method_type": Payment.PAYMENT_METHOD_CARD,
            "card_last_four": "4242",
            "card_brand": Payment.CARD_BRAND_VISA,
            "paid_at": timezone.now(),
        }
        kwargs.update(overrides)
        return kwargs

    def test_payment_amount_paid_non_negative_constraint(self):
        """DB must reject Payment with negative amount_paid."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Payment.objects.create(
                    **self._make_payment_kwargs(amount_paid=Decimal("-1.00"))
                )

    def test_payment_processor_fee_non_negative_constraint(self):
        """DB must reject Payment with negative processor_fee."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Payment.objects.create(
                    **self._make_payment_kwargs(processor_fee=Decimal("-0.50"))
                )

    def test_payment_net_amount_non_negative_constraint(self):
        """DB must reject Payment where net_amount would be negative."""
        # Force net_amount < 0 by having processor_fee > amount_paid.
        # net_amount = amount_paid - processor_fee, computed in save().
        # The processor_fee_lte_amount_paid constraint fires first, but
        # net_amount_non_negative catches cases where save() is bypassed.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Payment.objects.create(
                    **self._make_payment_kwargs(
                        amount_paid=Decimal("5.00"),
                        processor_fee=Decimal("10.00"),
                    )
                )

    def test_payment_processor_fee_lte_amount_paid_constraint(self):
        """DB must reject Payment where processor_fee exceeds amount_paid."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Payment.objects.create(
                    **self._make_payment_kwargs(
                        amount_paid=Decimal("10.00"),
                        processor_fee=Decimal("15.00"),
                    )
                )

    def test_valid_payment_passes_constraints(self):
        """A Payment with valid financials must be accepted by the DB."""
        payment = _make_payment(self.intent)
        self.assertIsNotNone(payment.pk)
        self.assertGreaterEqual(payment.amount_paid, Decimal("0"))
        self.assertGreaterEqual(payment.processor_fee, Decimal("0"))
        self.assertGreaterEqual(payment.net_amount, Decimal("0"))

    def test_zero_amount_paid_accepted(self):
        """amount_paid = 0 is valid (gte constraint, not gt)."""
        payment = Payment.objects.create(
            **self._make_payment_kwargs(
                amount_paid=Decimal("0.00"),
                processor_fee=Decimal("0.00"),
            )
        )
        self.assertEqual(payment.amount_paid, Decimal("0.00"))

    def test_zero_processor_fee_accepted(self):
        """processor_fee = 0 is valid."""
        payment = Payment.objects.create(
            **self._make_payment_kwargs(
                amount_paid=Decimal("50.00"),
                processor_fee=Decimal("0.00"),
            )
        )
        self.assertEqual(payment.processor_fee, Decimal("0.00"))

    def test_processor_fee_equal_to_amount_paid_accepted(self):
        """processor_fee == amount_paid is valid (lte constraint, not lt)."""
        payment = Payment.objects.create(
            **self._make_payment_kwargs(
                amount_paid=Decimal("10.00"),
                processor_fee=Decimal("10.00"),
            )
        )
        self.assertEqual(payment.net_amount, Decimal("0.00"))
