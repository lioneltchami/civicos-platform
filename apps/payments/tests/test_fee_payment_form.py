"""
Wave 3 — test_fee_payment_form.py

Tests for FeePaymentForm.  All amount derivation happens server-side;
the form never trusts client-submitted monetary values.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.payments.forms import FeePaymentForm
from apps.payments.models import FeeSchedule, TaxRate

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def make_fee_schedule(**kwargs):
    defaults = {
        "fee_code": "PERMIT-TEST",
        "service_type": "permit",
        "province": "ON",
        "amount": Decimal("50.00"),
        "is_taxable": True,
        "description_en": "Test Permit Fee",
        "description_fr": "Frais de permis de test",
        "effective_date": date(2020, 1, 1),
        "is_active": True,
    }
    defaults.update(kwargs)
    return FeeSchedule.objects.create(**defaults)


def make_tax_rate(**kwargs):
    defaults = {
        "province": "ON",
        "federal_rate": Decimal("0.05000"),
        "provincial_rate": Decimal("0.08000"),
        "combined_rate": Decimal("0.13000"),
        "tax_name_en": "HST",
        "tax_name_fr": "TVH",
        "effective_date": date(2020, 1, 1),
    }
    defaults.update(kwargs)
    return TaxRate.objects.create(**defaults)


def _valid_data(**overrides):
    data = {
        "province": "ON",
        "fee_code": "PERMIT-TEST",
        "quantity": 1,
        "payer_reference": "",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# FeePaymentForm tests
# ---------------------------------------------------------------------------


class FeePaymentFormValidTests(TestCase):
    """Happy-path and boundary tests."""

    def setUp(self):
        self.fee = make_fee_schedule()
        self.tax = make_tax_rate()

    def test_valid_form_is_valid(self):
        form = FeePaymentForm(data=_valid_data())
        self.assertTrue(form.is_valid(), form.errors)

    def test_cleaned_fee_is_fee_schedule_instance(self):
        form = FeePaymentForm(data=_valid_data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["fee"], self.fee)

    def test_subtotal_equals_amount_times_quantity(self):
        form = FeePaymentForm(data=_valid_data(quantity=1))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["subtotal"], Decimal("50.00"))

    def test_tax_amount_computed_correctly(self):
        # 50.00 * 0.13 = 6.50
        form = FeePaymentForm(data=_valid_data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["tax_amount"], Decimal("6.50"))

    def test_total_equals_subtotal_plus_tax(self):
        form = FeePaymentForm(data=_valid_data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["total"], Decimal("56.50"))

    def test_quantity_2_doubles_subtotal(self):
        form = FeePaymentForm(data=_valid_data(quantity=2))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["subtotal"], Decimal("100.00"))
        self.assertEqual(form.cleaned_data["tax_amount"], Decimal("13.00"))
        self.assertEqual(form.cleaned_data["total"], Decimal("113.00"))

    def test_payer_reference_optional(self):
        form = FeePaymentForm(data=_valid_data(payer_reference=""))
        self.assertTrue(form.is_valid(), form.errors)

    def test_payer_reference_with_value(self):
        form = FeePaymentForm(data=_valid_data(payer_reference="REF-12345"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["payer_reference"], "REF-12345")

    def test_province_with_no_tax_rate_gives_zero_tax(self):
        """Province with no TaxRate row → tax_amount=0.00, total=subtotal."""
        make_fee_schedule(
            fee_code="PERMIT-TEST",
            province="NT",
            amount=Decimal("50.00"),
            effective_date=date(2020, 1, 2),
        )
        form = FeePaymentForm(data=_valid_data(province="NT"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["tax_amount"], Decimal("0.00"))
        self.assertEqual(form.cleaned_data["total"], Decimal("50.00"))

    def test_multiple_active_fee_schedules_uses_most_recent(self):
        """When two active FeeSchedules match, the later effective_date wins."""
        newer = make_fee_schedule(
            fee_code="PERMIT-TEST",
            province="ON",
            amount=Decimal("75.00"),
            effective_date=date(2023, 1, 1),
        )
        form = FeePaymentForm(data=_valid_data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["fee"], newer)
        self.assertEqual(form.cleaned_data["subtotal"], Decimal("75.00"))

    def test_non_taxable_fee_has_zero_tax(self):
        make_fee_schedule(
            fee_code="NOTAX-TEST",
            province="ON",
            amount=Decimal("30.00"),
            is_taxable=False,
            effective_date=date(2021, 1, 1),
        )
        form = FeePaymentForm(data=_valid_data(fee_code="NOTAX-TEST"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["tax_amount"], Decimal("0.00"))
        self.assertEqual(form.cleaned_data["total"], Decimal("30.00"))


class FeePaymentFormInvalidTests(TestCase):
    """Validation failure tests."""

    def setUp(self):
        self.fee = make_fee_schedule()
        self.tax = make_tax_rate()

    def test_missing_province_is_invalid(self):
        form = FeePaymentForm(data=_valid_data(province=""))
        self.assertFalse(form.is_valid())
        self.assertIn("province", form.errors)

    def test_missing_fee_code_is_invalid(self):
        form = FeePaymentForm(data=_valid_data(fee_code=""))
        self.assertFalse(form.is_valid())
        self.assertIn("fee_code", form.errors)

    def test_invalid_fee_code_non_field_error(self):
        """Unknown fee_code → non-field error 'No active fee found...'"""
        form = FeePaymentForm(data=_valid_data(fee_code="NO-SUCH-FEE"))
        self.assertFalse(form.is_valid())
        self.assertTrue(len(form.non_field_errors()) > 0)
        self.assertIn("No active fee found", str(form.non_field_errors()))

    def test_invalid_province_choice_is_invalid(self):
        form = FeePaymentForm(data=_valid_data(province="ZZ"))
        self.assertFalse(form.is_valid())
        self.assertIn("province", form.errors)

    def test_quantity_less_than_1_is_invalid(self):
        form = FeePaymentForm(data=_valid_data(quantity=0))
        self.assertFalse(form.is_valid())
        self.assertIn("quantity", form.errors)

    def test_quantity_greater_than_999_is_invalid(self):
        form = FeePaymentForm(data=_valid_data(quantity=1000))
        self.assertFalse(form.is_valid())
        self.assertIn("quantity", form.errors)

    def test_quantity_999_is_valid_boundary(self):
        form = FeePaymentForm(data=_valid_data(quantity=999))
        self.assertTrue(form.is_valid(), form.errors)

    def test_quantity_1_is_valid_boundary(self):
        form = FeePaymentForm(data=_valid_data(quantity=1))
        self.assertTrue(form.is_valid(), form.errors)
