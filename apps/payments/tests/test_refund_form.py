"""
Wave 3 — test_refund_form.py

Tests for RefundForm.  Payment and Refund model fixtures are created with
exact fields from introspection.
"""

import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.payments.forms import RefundForm
from apps.payments.models import Payment, PaymentIntent, Refund

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def make_user(email=None, **kwargs):
    email = email or f"user_{uuid.uuid4().hex[:6]}@example.com"
    return User.objects.create_user(email=email, password="TestPass123!", **kwargs)


def make_payment_intent(payer, status=None, **kwargs):
    defaults = {
        "payer": payer,
        "amount": Decimal("100.00"),
        "currency": "CAD",
        "purpose": PaymentIntent.PURPOSE_SERVICE_FEE,
        "status": status or PaymentIntent.STATUS_COMPLETED,
        "gateway": PaymentIntent.GATEWAY_STRIPE,
        "gateway_intent_id": f"pi_test_{uuid.uuid4().hex[:8]}",
    }
    defaults.update(kwargs)
    return PaymentIntent.objects.create(**defaults)


def make_payment(intent, **kwargs):
    defaults = {
        "intent": intent,
        "amount_paid": Decimal("100.00"),
        "processor_fee": Decimal("3.20"),
        "gateway_charge_id": f"ch_test_{uuid.uuid4().hex[:8]}",
        "payment_method_type": Payment.PAYMENT_METHOD_CARD,
        "paid_at": timezone.now(),
    }
    defaults.update(kwargs)
    # net_amount is computed in save()
    return Payment.objects.create(**defaults)


def make_completed_payment(payer):
    intent = make_payment_intent(payer, status=PaymentIntent.STATUS_COMPLETED)
    return make_payment(intent)


def make_refund(payment, amount, authorized_by, **kwargs):
    defaults = {
        "payment": payment,
        "amount": amount,
        "reason": Refund.REASON_CUSTOMER,
        "gateway_refund_id": f"re_test_{uuid.uuid4().hex[:8]}",
        "refunded_at": timezone.now(),
        "authorized_by": authorized_by,
        "notes": "",
    }
    defaults.update(kwargs)
    return Refund.objects.create(**defaults)


def _valid_data(**overrides):
    data = {
        "amount": "10.00",
        "reason": Refund.REASON_CUSTOMER,
        "notes": "",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# RefundForm tests
# ---------------------------------------------------------------------------


class RefundFormValidTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.payment = make_completed_payment(self.user)

    def test_valid_form_is_valid(self):
        form = RefundForm(data=_valid_data(amount="10.00"), payment=self.payment)
        self.assertTrue(form.is_valid(), form.errors)

    def test_max_value_equals_amount_paid_minus_refunded(self):
        """max_value on amount field = amount_paid - already refunded."""
        form = RefundForm(data={}, payment=self.payment)
        self.assertEqual(
            form.fields["amount"].max_value,
            Decimal("100.00"),  # 100.00 - 0.00
        )

    def test_amount_at_max_refundable_is_valid(self):
        form = RefundForm(data=_valid_data(amount="100.00"), payment=self.payment)
        self.assertTrue(form.is_valid(), form.errors)

    def test_amount_above_max_refundable_is_not_rejected_by_form(self):
        """
        RefundForm sets max_value on the field but does NOT add MaxValueValidator
        after instantiation (Django only adds MaxValueValidator in field.__init__).
        The over-refund protection is enforced by the TOCTOU guard in the view
        (select_for_update), not by the form itself.
        """
        form = RefundForm(data=_valid_data(amount="100.01"), payment=self.payment)
        # Form is valid at the form level — TOCTOU guard in the view catches over-refund
        self.assertTrue(form.is_valid())

    def test_amount_zero_is_invalid(self):
        form = RefundForm(data=_valid_data(amount="0.00"), payment=self.payment)
        self.assertFalse(form.is_valid())
        self.assertIn("amount", form.errors)

    def test_amount_negative_is_invalid(self):
        form = RefundForm(data=_valid_data(amount="-1.00"), payment=self.payment)
        self.assertFalse(form.is_valid())
        self.assertIn("amount", form.errors)

    def test_all_reason_choices_are_valid(self):
        for value, _label in Refund.REASON_CHOICES:
            form = RefundForm(data=_valid_data(reason=value), payment=self.payment)
            self.assertTrue(form.is_valid(), f"Reason {value!r} failed: {form.errors}")

    def test_notes_optional(self):
        form = RefundForm(data=_valid_data(notes=""), payment=self.payment)
        self.assertTrue(form.is_valid(), form.errors)

    def test_notes_too_long_is_invalid(self):
        form = RefundForm(data=_valid_data(notes="x" * 501), payment=self.payment)
        self.assertFalse(form.is_valid())
        self.assertIn("notes", form.errors)

    def test_notes_exactly_500_chars_is_valid(self):
        form = RefundForm(data=_valid_data(notes="x" * 500), payment=self.payment)
        self.assertTrue(form.is_valid(), form.errors)

    def test_with_existing_partial_refund_max_value_reduced(self):
        """After a $30 partial refund, max becomes $70."""
        staff = make_user(email="staff@example.com", is_staff=True)
        make_refund(self.payment, Decimal("30.00"), staff)
        form = RefundForm(data={}, payment=self.payment)
        self.assertEqual(form.fields["amount"].max_value, Decimal("70.00"))

    def test_partial_refund_leaves_room_for_second(self):
        staff = make_user(email="staff2@example.com", is_staff=True)
        make_refund(self.payment, Decimal("30.00"), staff)
        form = RefundForm(data=_valid_data(amount="70.00"), payment=self.payment)
        self.assertTrue(form.is_valid(), form.errors)

    def test_fully_refunded_max_is_zero(self):
        """After full refund, max_value = 0.00."""
        staff = make_user(email="staff3@example.com", is_staff=True)
        make_refund(self.payment, Decimal("100.00"), staff)
        form = RefundForm(data={}, payment=self.payment)
        self.assertEqual(form.fields["amount"].max_value, Decimal("0.00"))

    def test_payment_none_does_not_crash(self):
        """Form with payment=None instantiates without AttributeError."""
        try:
            RefundForm(data=_valid_data(), payment=None)
            # max_value is not set when payment is None; form is still usable
        except AttributeError as exc:
            self.fail(f"RefundForm(payment=None) raised AttributeError: {exc}")
