"""
Tests for M-K: eligible_amount and advantage_amount negative-value floor guard.

Architecture note: Donation.save() always recomputes eligible_amount as
  max(0, amount - advantage_amount)
so the eligible_amount value passed by the task into Donation.objects.create()
is overridden at save() time.

The real M-K risk is: a negative advantage_amount from metadata causes
Donation.save() to compute eligible_amount = max(0, amount - negative) which
exceeds amount_paid — an illegal CRA receipt value.  The floor guard on
advantage_amount in _handle_one_time_donation prevents this.

The task-level floor on eligible_amount metadata is a defence-in-depth guard
for any future path where save() may not override (e.g. QuerySet.update()).
Its tests are written against the task's computed value, not what ends up in
the DB after save().

Covers:
- Negative advantage_amount from metadata → clamped to 0.00 (M-K primary risk)
- Negative advantage_amount does not inflate eligible_amount above amount_paid
- Zero / positive values pass through unchanged
- Task-level eligible_amount floor guard: max(0.00, ...) is applied before save()
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.payments.models import (
    Donation,
    DonationCampaign,
    Payment,
    PaymentIntent,
)
from apps.payments.tasks import _handle_one_time_donation

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_user(**kwargs):
    email = kwargs.pop("email", f"donor_{uuid.uuid4().hex[:6]}@example.com")
    return User.objects.create_user(email=email, password="testpass123", **kwargs)


def _make_campaign(**kwargs):
    defaults = {
        "slug": f"camp-{uuid.uuid4().hex[:6]}",
        "name_en": "Test Campaign",
        "start_date": date(2024, 1, 1),
        "is_active": True,
        "sort_order": 0,
        "advantage_amount": Decimal("0.00"),
    }
    defaults.update(kwargs)
    return DonationCampaign.objects.create(**defaults)


def _make_intent(payer, metadata=None, amount=Decimal("100.00"), **kwargs):
    defaults = {
        "payer": payer,
        "amount": amount,
        "tax_amount": Decimal("0.00"),
        "currency": "CAD",
        "purpose": PaymentIntent.PURPOSE_DONATION,
        "status": PaymentIntent.STATUS_COMPLETED,
        "gateway": PaymentIntent.GATEWAY_STRIPE,
        "gateway_intent_id": f"pi_test_{uuid.uuid4().hex[:8]}",
        "metadata": metadata or {},
    }
    defaults.update(kwargs)
    return PaymentIntent.objects.create(**defaults)


def _make_payment(intent, amount_paid=Decimal("100.00")):
    return Payment.objects.create(
        intent=intent,
        gateway_charge_id=f"ch_{uuid.uuid4().hex[:8]}",
        amount_paid=amount_paid,
        processor_fee=Decimal("0.00"),
        payment_method_type="card",
        paid_at=datetime(2024, 6, 1, tzinfo=UTC),
    )


def _make_webhook_event():
    mock_event = MagicMock()
    mock_event.gateway_event_id = f"evt_{uuid.uuid4().hex[:8]}"
    return mock_event


# ---------------------------------------------------------------------------
# M-K tests: advantage_amount floor guard (primary risk)
# ---------------------------------------------------------------------------


class AdvantageAmountNegativeFloorTests(TestCase):
    """
    M-K (primary): advantage_amount read from PaymentIntent metadata must be
    floored at Decimal("0.00").

    A negative advantage_amount causes Donation.save() to compute
      eligible_amount = max(0, amount - negative) > amount_paid
    which is an illegal CRA receipt value and causes the receipt service to
    reject the row.
    """

    def setUp(self):
        self.donor = _make_user()
        self.webhook_event = _make_webhook_event()

    def test_advantage_amount_negative_clamped_to_zero(self):
        """Negative advantage_amount from metadata must be floored at 0.00."""
        intent = _make_intent(
            self.donor,
            metadata={
                "is_recurring": "0",
                "advantage_amount": "-5.00",  # tampered / buggy upstream value
                "eligible_amount": "100.00",
                "donor_legal_name": "Test Donor",
            },
        )
        payment = _make_payment(intent, amount_paid=Decimal("100.00"))

        _handle_one_time_donation(intent, payment, self.webhook_event)

        donation = Donation.objects.get(payment_intent=intent)
        self.assertEqual(
            donation.advantage_amount,
            Decimal("0.00"),
            "Negative advantage_amount from metadata must be clamped to 0.00.",
        )

    def test_negative_advantage_does_not_inflate_eligible_above_payment(self):
        """
        A negative advantage_amount must not cause eligible_amount to exceed amount_paid.

        Donation.save() computes eligible_amount = max(0, amount - advantage_amount).
        If advantage_amount were -50.00 and amount is 100.00, that gives 150.00
        which exceeds the actual payment — an illegal CRA receipt value.
        The floor guard on advantage_amount prevents this.
        """
        intent = _make_intent(
            self.donor,
            metadata={
                "is_recurring": "0",
                "advantage_amount": "-50.00",
                # No eligible_amount key — Donation.save() will compute from amount - advantage
                "donor_legal_name": "Test Donor",
            },
        )
        payment = _make_payment(intent, amount_paid=Decimal("100.00"))

        _handle_one_time_donation(intent, payment, self.webhook_event)

        donation = Donation.objects.get(payment_intent=intent)
        # advantage_amount must be clamped: stored as 0.00
        self.assertEqual(donation.advantage_amount, Decimal("0.00"))
        # eligible_amount = max(0, 100.00 - 0.00) = 100.00, not 150.00
        self.assertLessEqual(
            donation.eligible_amount,
            payment.amount_paid,
            "eligible_amount must never exceed amount_paid.",
        )
        self.assertEqual(donation.eligible_amount, Decimal("100.00"))

    def test_advantage_amount_large_negative_clamped_to_zero(self):
        """Even a very large negative advantage_amount is clamped to 0.00."""
        intent = _make_intent(
            self.donor,
            metadata={
                "is_recurring": "0",
                "advantage_amount": "-9999.99",
                "donor_legal_name": "Test Donor",
            },
        )
        payment = _make_payment(intent, amount_paid=Decimal("100.00"))

        _handle_one_time_donation(intent, payment, self.webhook_event)

        donation = Donation.objects.get(payment_intent=intent)
        self.assertEqual(donation.advantage_amount, Decimal("0.00"))
        self.assertLessEqual(donation.eligible_amount, payment.amount_paid)

    def test_advantage_amount_zero_not_disturbed(self):
        """advantage_amount of 0.00 is unchanged."""
        intent = _make_intent(
            self.donor,
            metadata={
                "is_recurring": "0",
                "advantage_amount": "0.00",
                "eligible_amount": "100.00",
                "donor_legal_name": "Test Donor",
            },
        )
        payment = _make_payment(intent, amount_paid=Decimal("100.00"))

        _handle_one_time_donation(intent, payment, self.webhook_event)

        donation = Donation.objects.get(payment_intent=intent)
        self.assertEqual(donation.advantage_amount, Decimal("0.00"))

    def test_advantage_amount_positive_not_disturbed(self):
        """Positive advantage_amount from metadata passes through unchanged."""
        intent = _make_intent(
            self.donor,
            metadata={
                "is_recurring": "0",
                "advantage_amount": "25.00",
                "eligible_amount": "75.00",
                "donor_legal_name": "Test Donor",
            },
        )
        payment = _make_payment(intent, amount_paid=Decimal("100.00"))

        _handle_one_time_donation(intent, payment, self.webhook_event)

        donation = Donation.objects.get(payment_intent=intent)
        self.assertEqual(donation.advantage_amount, Decimal("25.00"))
        # Donation.save() recomputes: max(0, 100.00 - 25.00) = 75.00
        self.assertEqual(donation.eligible_amount, Decimal("75.00"))


# ---------------------------------------------------------------------------
# M-K tests: eligible_amount task-level floor guard (defence-in-depth)
# ---------------------------------------------------------------------------


class EligibleAmountTaskLevelFloorTests(TestCase):
    """
    M-K (defence-in-depth): Verify the task-level max(0, ...) on eligible_amount
    is applied before the value is passed into Donation.objects.create().

    Note: Donation.save() always recomputes eligible_amount as
      max(0, amount - advantage_amount)
    so the DB value will reflect that computation regardless of what the task
    passes in.  These tests verify the task-level floor using a patched save()
    so we can observe the pre-save value.

    This guards against future code paths (e.g. QuerySet.update()) that bypass
    Donation.save() and would store the raw metadata value directly.
    """

    def setUp(self):
        self.donor = _make_user()
        self.webhook_event = _make_webhook_event()

    def test_eligible_amount_negative_clamped_before_create(self):
        """
        When metadata contains a negative eligible_amount, the task must pass
        max(0, ...) = 0.00 to Donation.objects.create(), not the raw negative.

        We intercept the create() call to capture the actual argument.
        """
        intent = _make_intent(
            self.donor,
            metadata={
                "is_recurring": "0",
                "advantage_amount": "0.00",
                "eligible_amount": "-10.00",
                "donor_legal_name": "Test Donor",
            },
        )
        payment = _make_payment(intent, amount_paid=Decimal("100.00"))

        captured_kwargs = {}
        original_create = Donation.objects.create

        def capturing_create(**kwargs):
            captured_kwargs.update(kwargs)
            return original_create(**kwargs)

        with patch.object(Donation.objects, "create", side_effect=capturing_create):
            _handle_one_time_donation(intent, payment, self.webhook_event)

        self.assertIn("eligible_amount", captured_kwargs)
        self.assertGreaterEqual(
            captured_kwargs["eligible_amount"],
            Decimal("0.00"),
            "Task must pass eligible_amount >= 0 to Donation.objects.create(). "
            "Raw negative value from metadata must be clamped before create().",
        )

    def test_eligible_amount_positive_passes_through_unchanged(self):
        """A positive eligible_amount from metadata is passed to create() unchanged."""
        intent = _make_intent(
            self.donor,
            metadata={
                "is_recurring": "0",
                "advantage_amount": "25.00",
                "eligible_amount": "75.00",
                "donor_legal_name": "Test Donor",
            },
        )
        payment = _make_payment(intent, amount_paid=Decimal("100.00"))

        captured_kwargs = {}
        original_create = Donation.objects.create

        def capturing_create(**kwargs):
            captured_kwargs.update(kwargs)
            return original_create(**kwargs)

        with patch.object(Donation.objects, "create", side_effect=capturing_create):
            _handle_one_time_donation(intent, payment, self.webhook_event)

        self.assertEqual(captured_kwargs.get("eligible_amount"), Decimal("75.00"))
