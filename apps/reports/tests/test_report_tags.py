"""
M12: Unit tests for the cad_money templatetag filter.

Covers:
  - None input → locale-aware zero
  - Decimal("0") → locale-aware zero
  - Negative values → formatted with sign
  - Large numbers → locale-aware grouping
  - Invalid input (str "abc", float NaN, Infinity) → locale-aware zero
  - decimal_pos argument: 0, 1, 2, 4
  - en-CA locale: non-breaking-space thousands, period decimal
  - fr-CA locale: non-breaking-space thousands, comma decimal
  - Decimal vs float vs int vs numeric string inputs

Locale data note (Django 5.2 + Django's bundled locale files):
  en-CA: THOUSAND_SEPARATOR = '\\xa0' (non-breaking space), DECIMAL_SEPARATOR = '.'
  fr-CA: THOUSAND_SEPARATOR = '\\xa0' (non-breaking space), DECIMAL_SEPARATOR = ','

This is the correct Django locale data for Canada; tests assert structural
properties (digit presence, decimal separator character) rather than assuming
a specific thousands separator character.

Settings: --settings=config.settings.test
"""
from __future__ import annotations

from decimal import Decimal

from django.test import TestCase, override_settings

from apps.reports.templatetags.report_tags import cad_money

# Thousands separator used by both en-CA and fr-CA in Django's locale files.
_THOU = "\xa0"  # U+00A0 NON-BREAKING SPACE


class CadMoneyNoneAndZeroTests(TestCase):
    """cad_money(None) and cad_money(0) must return locale-aware zero."""

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_none_returns_zero_en_ca(self):
        result = cad_money(None)
        # en-CA zero: decimal separator is '.'
        self.assertIn("0", result)
        self.assertIn(".", result, "en-CA zero must use period as decimal separator")
        self.assertNotIn(",", result, "en-CA zero must not use comma as decimal separator")

    @override_settings(LANGUAGE_CODE="fr-CA")
    def test_none_returns_zero_fr_ca(self):
        result = cad_money(None)
        # fr-CA zero: decimal separator is ','
        self.assertIn("0", result)
        self.assertIn(",", result, "fr-CA zero must use comma as decimal separator")
        self.assertNotIn(".", result, "fr-CA zero must not use period as decimal separator")

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_decimal_zero_en_ca(self):
        result = cad_money(Decimal("0"))
        self.assertTrue(result.startswith("0") or "0" in result)
        self.assertIn(".", result)

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_decimal_zero_with_decimal_pos_0(self):
        result = cad_money(Decimal("0"), decimal_pos=0)
        self.assertEqual(result, "0")

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_none_with_decimal_pos_0(self):
        result = cad_money(None, decimal_pos=0)
        self.assertEqual(result, "0")


class CadMoneyPositiveValueTests(TestCase):
    """cad_money with positive Decimal values."""

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_simple_value_en_ca(self):
        result = cad_money(Decimal("1234.56"))
        # Digit groups must be present; en-CA decimal separator is '.'.
        self.assertIn("1", result)
        self.assertIn("234", result)
        self.assertIn(".56", result, "en-CA must use period as decimal separator")
        self.assertNotIn(",56", result, "en-CA must not use comma as decimal separator")

    @override_settings(LANGUAGE_CODE="fr-CA")
    def test_simple_value_fr_ca(self):
        result = cad_money(Decimal("1234.56"))
        # fr-CA: comma decimal.
        self.assertIn("234", result)
        self.assertIn(",56", result, "fr-CA must use comma as decimal separator")
        self.assertNotIn(".56", result, "fr-CA must not use period as decimal separator")

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_large_number_grouping_en_ca(self):
        result = cad_money(Decimal("1234567.89"))
        # Thousands grouping present (non-breaking space separator in en-CA).
        self.assertIn(_THOU, result, "Large number must have thousands separator in en-CA")
        self.assertIn(".89", result, "Large number decimal must use period in en-CA")

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_value_below_thousand_no_grouping(self):
        result = cad_money(Decimal("999.99"))
        self.assertIn("999", result)
        self.assertIn(".99", result)
        self.assertNotIn(_THOU, result,
            "Values below 1000 must not have a thousands separator")

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_exactly_1000_has_grouping(self):
        result = cad_money(Decimal("1000.00"))
        self.assertIn(_THOU, result, "1000 must have thousands grouping")
        self.assertIn(".00", result)

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_locale_grouping_differs_from_fr_ca(self):
        """en-CA and fr-CA must produce different decimal separators for the same value."""
        en = cad_money(Decimal("1.50"))
        with override_settings(LANGUAGE_CODE="fr-CA"):
            fr = cad_money(Decimal("1.50"))
        # en-CA decimal separator is '.', fr-CA is ','
        self.assertIn(".50", en)
        self.assertIn(",50", fr)
        self.assertNotEqual(en, fr, "en-CA and fr-CA must produce different output")


class CadMoneyNegativeValueTests(TestCase):
    """cad_money with negative Decimal values."""

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_negative_value_en_ca(self):
        result = cad_money(Decimal("-1234.56"))
        self.assertIn("-", result, "Negative values must include a minus sign")
        self.assertIn("234", result)
        self.assertIn(".56", result)

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_negative_zero(self):
        from decimal import Decimal
        # -0.00 is identical to 0.00 in Python/Decimal arithmetic
        result = cad_money(Decimal("-0.00"))
        self.assertEqual(result, "0.00",
            "Negative zero must format identically to positive zero")


class CadMoneyInvalidInputTests(TestCase):
    """cad_money with invalid inputs must return locale-aware zero, not crash."""

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_string_abc_returns_en_ca_zero(self):
        result = cad_money("abc")
        self.assertIn("0", result)
        self.assertIn(".", result, "Invalid input must return en-CA zero with period decimal")
        self.assertNotIn(",", result)

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_nan_string_returns_zero(self):
        result = cad_money("NaN")
        self.assertIn("0", result)
        self.assertIn(".", result)

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_infinity_string_returns_zero(self):
        result = cad_money("Infinity")
        self.assertIn("0", result)
        self.assertIn(".", result)

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_float_nan_returns_zero(self):
        result = cad_money(float("nan"))
        self.assertIn("0", result)
        self.assertIn(".", result)

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_float_infinity_returns_zero(self):
        result = cad_money(float("inf"))
        self.assertIn("0", result)
        self.assertIn(".", result)

    @override_settings(LANGUAGE_CODE="fr-CA")
    def test_invalid_input_returns_fr_ca_zero(self):
        result = cad_money("abc")
        # fr-CA zero: comma decimal.
        self.assertIn("0", result)
        self.assertIn(",", result, "Invalid input must return fr-CA zero with comma decimal")
        self.assertNotIn(".", result)


class CadMoneyDecimalPosTests(TestCase):
    """cad_money decimal_pos argument."""

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_decimal_pos_0(self):
        result = cad_money(Decimal("1234.56"), decimal_pos=0)
        # No decimal separator; grouping present.
        self.assertIn("1", result)
        self.assertIn("234", result)
        self.assertNotIn(".", result)
        self.assertNotIn(",5", result)  # no decimal digits

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_decimal_pos_1(self):
        # Use a value with only 1 significant decimal so the result is unambiguous
        # regardless of Django's rounding mode (truncation vs. half-even).
        result = cad_money(Decimal("1234.50"), decimal_pos=1)
        self.assertIn(".5", result, "decimal_pos=1 must produce exactly 1 decimal place")
        self.assertNotIn(".50", result)

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_decimal_pos_2_default(self):
        result = cad_money(Decimal("1234.5"))
        self.assertIn(".50", result, "Default decimal_pos=2 must pad to 2 places")

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_decimal_pos_4(self):
        result = cad_money(Decimal("1.5"), decimal_pos=4)
        self.assertIn(".5000", result, "decimal_pos=4 must pad to 4 decimal places")

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_invalid_decimal_pos_falls_back_to_2(self):
        # Non-integer decimal_pos should fall back to 2.
        result = cad_money(Decimal("1234.5"), decimal_pos="bad")
        self.assertIn(".50", result, "Non-integer decimal_pos must fall back to 2")


class CadMoneyInputTypeTests(TestCase):
    """cad_money must accept Decimal, float, int, and numeric strings."""

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_integer_input(self):
        result = cad_money(1234)
        self.assertIn("1", result)
        self.assertIn("234", result)
        self.assertIn(".00", result)

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_float_input(self):
        result = cad_money(1234.56)
        self.assertIn("1", result)
        self.assertIn("234", result)
        self.assertIn(".56", result)

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_numeric_string_input(self):
        result = cad_money("1234.56")
        self.assertIn("234", result)
        self.assertIn(".56", result)

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_decimal_input(self):
        result = cad_money(Decimal("1234.56"))
        self.assertIn("234", result)
        self.assertIn(".56", result)

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_zero_integer(self):
        result = cad_money(0)
        self.assertIn("0", result)
        self.assertIn(".", result)


class CadMoneyHoursFormattingTests(TestCase):
    """
    cad_money used with decimal_pos=1 for volunteer hours display
    (as in the dashboard and impact templates).
    """

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_hours_format_en_ca(self):
        result = cad_money(Decimal("1234.5"), decimal_pos=1)
        # en-CA: period decimal, non-breaking-space thousands.
        self.assertIn("234", result)
        self.assertIn(".5", result, "Hours display must use period decimal in en-CA")
        self.assertNotIn(",5", result)

    @override_settings(LANGUAGE_CODE="fr-CA")
    def test_hours_format_fr_ca(self):
        result = cad_money(Decimal("1234.5"), decimal_pos=1)
        # fr-CA: comma decimal.
        self.assertIn("234", result)
        self.assertIn(",5", result, "Hours display must use comma decimal in fr-CA")
        self.assertNotIn(".5", result)

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_none_hours_returns_zero_en_ca(self):
        result = cad_money(None, decimal_pos=1)
        self.assertIn("0", result)
        self.assertIn(".0", result, "None hours must return '0.0' in en-CA with decimal_pos=1")

    @override_settings(LANGUAGE_CODE="fr-CA")
    def test_none_hours_returns_zero_fr_ca(self):
        result = cad_money(None, decimal_pos=1)
        self.assertIn("0", result)
        self.assertIn(",0", result, "None hours must return '0,0' in fr-CA with decimal_pos=1")


class CadMoneyLocaleConsistencyTests(TestCase):
    """
    Verify that cad_money produces locale-correct output for the two locales
    used in CivicOS: en-CA (period decimal) and fr-CA (comma decimal).
    """

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_en_ca_uses_period_decimal_separator(self):
        result = cad_money(Decimal("1.50"))
        self.assertIn(".", result)
        self.assertNotIn(",", result)

    @override_settings(LANGUAGE_CODE="fr-CA")
    def test_fr_ca_uses_comma_decimal_separator(self):
        result = cad_money(Decimal("1.50"))
        self.assertIn(",", result)
        self.assertNotIn(".", result)

    @override_settings(LANGUAGE_CODE="en-CA")
    def test_en_ca_thousands_grouping_character(self):
        """en-CA uses non-breaking space (U+00A0) as the thousands separator."""
        result = cad_money(Decimal("1000.00"))
        self.assertIn(_THOU, result,
            "en-CA thousands separator must be non-breaking space (U+00A0)")

    @override_settings(LANGUAGE_CODE="fr-CA")
    def test_fr_ca_thousands_grouping_character(self):
        """fr-CA uses non-breaking space (U+00A0) as the thousands separator."""
        result = cad_money(Decimal("1000.00"))
        self.assertIn(_THOU, result,
            "fr-CA thousands separator must be non-breaking space (U+00A0)")
