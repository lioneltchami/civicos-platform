"""
test_govstack_wave4.py

Comprehensive tests for GovStack Payments BB — Wave 4 (Voucher Engine).

Coverage matrix:
  A. VoucherPreactivation view — harness scenarios
     A1:  Smoke POST → HTTP 200, correct shape
     A2:  Full fields → HTTP 200, all four response fields present
     A3:  Missing voucher_amount → HTTP 400 (DRF required field)
     A4:  Missing voucher_currency → HTTP 400
     A5:  Missing voucher_group → HTTP 400
     A6:  Missing Gov_Stack_BB → HTTP 400
     A7:  Non-numeric voucher_amount → HTTP 400
     A8:  Invalid voucher_currency format (2 chars) → HTTP 400
     A9:  Zero voucher_amount → HTTP 452
     A10: Negative voucher_amount → HTTP 452
     A11: Empty Gov_Stack_BB string → HTTP 460 (service raises GovStackBBNotFound)
     A12: Empty voucher_group string → HTTP 454 (service raises InvalidVoucherGroup)
     A13: X-Registering-Institution-Id stored on voucher
     A14: Response has message key in error shape ({"message": "..."})
     A15: voucher_secret NEVER in response body

  B. VoucherActivation view — harness scenarios
     B1:  Smoke PATCH with pre-created PREACTIVATED voucher → HTTP 200
     B2:  Response shape: {voucherNumber, voucherSerialNumber, voucherStatus, voucherGroup}
     B3:  voucherStatus is "activated" after activation
     B4:  Serial not found → HTTP 456
     B5:  Empty Gov_Stack_BB → HTTP 460
     B6:  Missing voucher_serial_number field → HTTP 400
     B7:  Missing Gov_Stack_BB field → HTTP 400
     B8:  Integer voucher_serial_number accepted (harness sends int)

  C. VoucherRedemption view — harness scenarios
     C1:  Smoke POST with pre-activated voucher → HTTP 200
     C2:  Response shape: {status, message, serialNumber, value, timestamp, transactionId}
     C3:  status in response is an integer (status_int for CONSUMED = 3)
     C4:  value matches voucher amount
     C5:  serialNumber matches the voucher serial
     C6:  transactionId is non-empty
     C7:  Missing Gov_Stack_BB → HTTP 400
     C8:  Missing voucher_number → HTTP 400
     C9:  Unknown Gov_Stack_BB (empty) → HTTP 460
     C10: Voucher not found → HTTP 456
     C11: Voucher not in ACTIVATED state → HTTP 456
     C12: Integer voucher_number accepted (harness sends int)
     C13: Double redemption → HTTP 456 (CONSUMED is terminal)

  D. VoucherStatusCheck view — GET scenarios
     D1:  GET with known serial → HTTP 200
     D2:  GET response shape: {status (int), serialNumber, value}
     D3:  status is an integer (not a string)
     D4:  GET unknown serial → HTTP 456
     D5:  value matches amount (string representation)

  E. VoucherCancellation view — PATCH scenarios
     E1:  PATCH cancel PREACTIVATED voucher → HTTP 200
     E2:  PATCH cancel ACTIVATED voucher → HTTP 200
     E3:  Response shape: {voucherSerialNumber, voucherStatus}
     E4:  voucherStatus is "cancelled" after cancellation
     E5:  Cancel unknown serial → HTTP 463
     E6:  Cancel already-cancelled voucher → HTTP 464
     E7:  Cancel CONSUMED voucher → HTTP 463 (terminal state, invalid transition)

  F. GovStackVoucherService — service layer unit tests
     F1:  preactivate() returns a GovStackVoucher with STATUS_PREACTIVATED
     F2:  preactivate() stores correct amount/currency/group
     F3:  preactivate() creates ACTION_VOUCHER_PREACTIVATED audit entry
     F4:  preactivate() audit details never contain payee_functional_id
     F5:  preactivate() raises InvalidVoucherAmount for zero amount
     F6:  preactivate() raises InvalidVoucherAmount for negative amount
     F7:  preactivate() raises InvalidVoucherGroup for empty group
     F8:  preactivate() raises GovStackBBNotFound for empty BB
     F9:  activate() transitions PREACTIVATED → ACTIVATED
     F10: activate() creates ACTION_VOUCHER_ACTIVATED audit entry
     F11: activate() raises InvalidVoucherSerial for unknown serial
     F12: activate() raises GovStackBBNotFound for empty BB
     F13: activate() raises InvalidVoucherSerial for CONSUMED voucher (invalid transition)
     F14: redeem() transitions ACTIVATED → CONSUMED
     F15: redeem() stores merchant details on the voucher
     F16: redeem() stores redeemed_at timestamp
     F17: redeem() stores redemption_transaction_id (non-empty, ≤20 chars)
     F18: redeem() creates ACTION_VOUCHER_REDEEMED audit entry
     F19: redeem() audit details never contain merchant_bank_details (potential PII)
     F20: redeem() raises InvalidVoucherSerial for unknown serial
     F21: redeem() raises GovStackBBNotFound for empty BB
     F22: cancel() transitions PREACTIVATED → CANCELLED
     F23: cancel() transitions ACTIVATED → CANCELLED
     F24: cancel() raises InvalidCancellationSerial for unknown serial
     F25: cancel() raises VoucherAlreadyCancelled for already-cancelled
     F26: cancel() raises InvalidCancellationSerial for CONSUMED (terminal) voucher
     F27: cancel() creates ACTION_VOUCHER_CANCELLED audit entry
     F28: get_status() returns voucher for known serial
     F29: get_status() raises InvalidVoucherSerial for unknown serial
     F30: preactivate() retries on serial collision and succeeds with second serial

  G. Security invariants
     G1:  voucher_secret never in any preactivation response
     G2:  payee_functional_id never in any response body
     G3:  voucher_secret never in audit entry details
     G4:  merchant_bank_details never in audit entry details
     G5:  Status check response never includes voucher_secret
     G6:  Exception messages for InvalidCancellationSerial are generic (no serial number)

  H. Model: GovStackVoucher
     H1:  status_int returns correct integer for each status
     H2:  is_terminal returns True for CONSUMED, CANCELLED, PURGED
     H3:  is_terminal returns False for PREACTIVATED, ACTIVATED
     H4:  transition_to raises ValueError for invalid transitions
     H5:  transition_to allows all transitions in ALLOWED_TRANSITIONS
"""
from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.payments.govstack_exceptions import (
    GovStackBBNotFound,
    InvalidCancellationSerial,
    InvalidVoucherAmount,
    InvalidVoucherGroup,
    InvalidVoucherSerial,
    VoucherAlreadyCancelled,
)
from apps.payments.govstack_models import GovStackPaymentAuditEntry, GovStackVoucher
from apps.payments.govstack_services import GovStackVoucherService

# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------
PREACTIVATION_URL = "/govstack/payments/vouchers/voucher_preactivation"
ACTIVATION_URL = "/govstack/payments/vouchers/voucher_activation"
REDEMPTION_URL = "/govstack/payments/vouchers/voucher_redemption"


def _status_url(serial: str) -> str:
    return f"/govstack/payments/vouchers/voucherstatuscheck/{serial}"


# ---------------------------------------------------------------------------
# Test data constants
# ---------------------------------------------------------------------------
BB_ID = "SOCIALWELFARE"
CURRENCY = "USD"
GROUP = "Payment Voucher"
AMOUNT = "100.00"

# Deterministic serial used in tests that pre-create vouchers.
# Patched via mock to avoid randomness.
FIXED_SERIAL = "500001"
FIXED_SERIAL_2 = "500002"
FIXED_SERIAL_3 = "500003"


def _preactivation_body(
    *,
    voucher_amount=AMOUNT,
    voucher_currency=CURRENCY,
    voucher_group=GROUP,
    gov_stack_bb=BB_ID,
) -> dict:
    return {
        "voucher_amount": voucher_amount,
        "voucher_currency": voucher_currency,
        "voucher_group": voucher_group,
        "Gov_Stack_BB": gov_stack_bb,
    }


def _activation_body(*, serial=FIXED_SERIAL, gov_stack_bb=BB_ID) -> dict:
    return {
        "voucher_serial_number": serial,
        "Gov_Stack_BB": gov_stack_bb,
    }


def _redemption_body(*, voucher_number=FIXED_SERIAL, gov_stack_bb=BB_ID) -> dict:
    return {
        "voucher_number": voucher_number,
        "Gov_Stack_BB": gov_stack_bb,
        "merchant_name": "Test Merchant",
        "merchant_bank_details": "BANK001",
        "merchant_voucher_group": GROUP,
        "override": False,
    }


def _make_voucher(
    serial: str = FIXED_SERIAL,
    status: str = GovStackVoucher.STATUS_PREACTIVATED,
    amount: str = AMOUNT,
    currency: str = CURRENCY,
    group_code: str = GROUP,
    issuing_bb: str = BB_ID,
) -> GovStackVoucher:
    """Create a GovStackVoucher directly in the DB (skips service layer)."""
    from django.utils import timezone
    from datetime import timedelta
    return GovStackVoucher.objects.create(
        serial_number=serial,
        amount=Decimal(amount),
        currency=currency,
        group_code=group_code,
        status=status,
        issuing_bb=issuing_bb,
        expiry_date=timezone.now() + timedelta(days=90),
    )


# ---------------------------------------------------------------------------
# A. VoucherPreactivation view
# ---------------------------------------------------------------------------

class VoucherPreactivationHarnessTest(TestCase):
    """POST /govstack/payments/vouchers/voucher_preactivation"""

    def setUp(self):
        self.client = APIClient()

    def _post(self, body: dict, headers: dict | None = None) -> object:
        return self.client.post(
            PREACTIVATION_URL,
            data=json.dumps(body),
            content_type="application/json",
            **(headers or {}),
        )

    @patch(
        "apps.payments.govstack_services._generate_voucher_serial",
        return_value=FIXED_SERIAL,
    )
    def test_a1_smoke_returns_200(self, _mock):
        resp = self._post(_preactivation_body())
        self.assertEqual(resp.status_code, 200, resp.data)

    @patch(
        "apps.payments.govstack_services._generate_voucher_serial",
        return_value=FIXED_SERIAL,
    )
    def test_a2_full_fields_response_shape(self, _mock):
        resp = self._post(_preactivation_body())
        self.assertEqual(resp.status_code, 200)
        data = resp.data
        self.assertIn("voucherNumber", data)
        self.assertIn("voucherSerialNumber", data)
        self.assertIn("voucherGroup", data)
        self.assertIn("expiryDate", data)
        self.assertEqual(data["voucherGroup"], GROUP)
        self.assertIsNotNone(data["expiryDate"])

    def test_a3_missing_voucher_amount_returns_400(self):
        body = _preactivation_body()
        del body["voucher_amount"]
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("message", resp.data)

    def test_a4_missing_voucher_currency_returns_400(self):
        body = _preactivation_body()
        del body["voucher_currency"]
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("message", resp.data)

    def test_a5_missing_voucher_group_returns_400(self):
        body = _preactivation_body()
        del body["voucher_group"]
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("message", resp.data)

    def test_a6_missing_gov_stack_bb_returns_400(self):
        body = _preactivation_body()
        del body["Gov_Stack_BB"]
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("message", resp.data)

    def test_a7_non_numeric_amount_returns_400(self):
        resp = self._post(_preactivation_body(voucher_amount="abc"))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("message", resp.data)

    def test_a8_invalid_currency_format_returns_400(self):
        """2-char currency code fails the ISO 4217 regex in the serializer."""
        resp = self._post(_preactivation_body(voucher_currency="US"))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("message", resp.data)

    def test_a9_zero_amount_returns_452(self):
        resp = self._post(_preactivation_body(voucher_amount="0"))
        self.assertEqual(resp.status_code, 452)
        self.assertIn("message", resp.data)

    def test_a10_negative_amount_returns_452(self):
        resp = self._post(_preactivation_body(voucher_amount="-50.00"))
        self.assertEqual(resp.status_code, 452)
        self.assertIn("message", resp.data)

    def test_a11_whitespace_gov_stack_bb_returns_460(self):
        """Whitespace-only Gov_Stack_BB passes the serializer (allow_blank=True) but
        the service raises GovStackBBNotFound → HTTP 460."""
        resp = self._post(_preactivation_body(gov_stack_bb="   "))
        self.assertEqual(resp.status_code, 460)
        self.assertIn("message", resp.data)

    def test_a12_empty_voucher_group_string_raises_454(self):
        """
        voucher_group=" " (whitespace only) passes CharField (not blank) but
        the service's group.strip() check raises InvalidVoucherGroup → 454.
        """
        resp = self._post(_preactivation_body(voucher_group="   "))
        self.assertEqual(resp.status_code, 454)
        self.assertIn("message", resp.data)

    @patch(
        "apps.payments.govstack_services._generate_voucher_serial",
        return_value=FIXED_SERIAL,
    )
    def test_a13_registering_institution_id_stored(self, _mock):
        self._post(
            _preactivation_body(),
            {"HTTP_X_REGISTERING_INSTITUTION_ID": "INST001"},
        )
        voucher = GovStackVoucher.objects.get(serial_number=FIXED_SERIAL)
        self.assertEqual(voucher.registering_institution_id, "INST001")

    @patch(
        "apps.payments.govstack_services._generate_voucher_serial",
        return_value=FIXED_SERIAL,
    )
    def test_a14_error_response_has_message_key(self, _mock):
        """All error responses use {"message": "..."} shape."""
        resp = self._post(_preactivation_body(voucher_amount="0"))
        self.assertEqual(resp.status_code, 452)
        self.assertIn("message", resp.data)
        self.assertNotIn("detail", resp.data)

    @patch(
        "apps.payments.govstack_services._generate_voucher_serial",
        return_value=FIXED_SERIAL,
    )
    def test_a15_voucher_secret_never_in_response(self, _mock):
        resp = self._post(_preactivation_body())
        raw = json.dumps(resp.data if isinstance(resp.data, dict) else {})
        self.assertNotIn("voucher_secret", raw)
        self.assertNotIn("voucherSecret", raw)


# ---------------------------------------------------------------------------
# B. VoucherActivation view
# ---------------------------------------------------------------------------

class VoucherActivationHarnessTest(TestCase):
    """PATCH /govstack/payments/vouchers/voucher_activation"""

    def setUp(self):
        self.client = APIClient()
        # Pre-create a voucher in PREACTIVATED status.
        self.voucher = _make_voucher(serial=FIXED_SERIAL)

    def _patch(self, body: dict) -> object:
        return self.client.patch(
            ACTIVATION_URL,
            data=json.dumps(body),
            content_type="application/json",
        )

    def test_b1_smoke_returns_200(self):
        resp = self._patch(_activation_body(serial=FIXED_SERIAL))
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_b2_response_shape(self):
        resp = self._patch(_activation_body(serial=FIXED_SERIAL))
        self.assertEqual(resp.status_code, 200)
        data = resp.data
        self.assertIn("voucherNumber", data)
        self.assertIn("voucherSerialNumber", data)
        self.assertIn("voucherStatus", data)
        self.assertIn("voucherGroup", data)

    def test_b3_status_is_activated(self):
        resp = self._patch(_activation_body(serial=FIXED_SERIAL))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["voucherStatus"], GovStackVoucher.STATUS_ACTIVATED)
        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.status, GovStackVoucher.STATUS_ACTIVATED)

    def test_b4_unknown_serial_returns_456(self):
        resp = self._patch(_activation_body(serial="999999"))
        self.assertEqual(resp.status_code, 456)
        self.assertIn("message", resp.data)

    def test_b5_whitespace_bb_returns_460(self):
        """Whitespace-only Gov_Stack_BB passes serializer but service raises 460."""
        resp = self._patch({"voucher_serial_number": FIXED_SERIAL, "Gov_Stack_BB": "   "})
        self.assertEqual(resp.status_code, 460)

    def test_b6_missing_serial_returns_400(self):
        resp = self._patch({"Gov_Stack_BB": BB_ID})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("message", resp.data)

    def test_b7_missing_gov_stack_bb_returns_400(self):
        resp = self._patch({"voucher_serial_number": FIXED_SERIAL})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("message", resp.data)

    def test_b8_integer_serial_accepted(self):
        """Harness sends voucher_serial_number as an integer — must be accepted."""
        resp = self._patch({
            "voucher_serial_number": int(FIXED_SERIAL),
            "Gov_Stack_BB": BB_ID,
        })
        self.assertEqual(resp.status_code, 200, resp.data)


# ---------------------------------------------------------------------------
# C. VoucherRedemption view
# ---------------------------------------------------------------------------

class VoucherRedemptionHarnessTest(TestCase):
    """POST /govstack/payments/vouchers/voucher_redemption"""

    def setUp(self):
        self.client = APIClient()
        self.voucher = _make_voucher(
            serial=FIXED_SERIAL,
            status=GovStackVoucher.STATUS_ACTIVATED,
        )

    def _post(self, body: dict) -> object:
        return self.client.post(
            REDEMPTION_URL,
            data=json.dumps(body),
            content_type="application/json",
        )

    def test_c1_smoke_returns_200(self):
        resp = self._post(_redemption_body())
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_c2_response_shape(self):
        resp = self._post(_redemption_body())
        self.assertEqual(resp.status_code, 200)
        data = resp.data
        self.assertIn("status", data)
        self.assertIn("message", data)
        self.assertIn("serialNumber", data)
        self.assertIn("value", data)
        self.assertIn("timestamp", data)
        self.assertIn("transactionId", data)

    def test_c3_status_is_integer(self):
        resp = self._post(_redemption_body())
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.data["status"], int)
        # CONSUMED = 3 in STATUS_INT_MAP
        self.assertEqual(resp.data["status"], GovStackVoucher.STATUS_INT_MAP[GovStackVoucher.STATUS_CONSUMED])

    def test_c4_value_matches_amount(self):
        resp = self._post(_redemption_body())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Decimal(resp.data["value"]), Decimal(AMOUNT))

    def test_c5_serial_number_matches(self):
        resp = self._post(_redemption_body())
        self.assertEqual(resp.data["serialNumber"], FIXED_SERIAL)

    def test_c6_transaction_id_non_empty(self):
        resp = self._post(_redemption_body())
        tid = resp.data["transactionId"]
        self.assertTrue(tid, "transactionId must be non-empty")
        self.assertLessEqual(len(tid), 20, "transactionId must fit max_length=20")

    def test_c7_missing_gov_stack_bb_returns_400(self):
        body = _redemption_body()
        del body["Gov_Stack_BB"]
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("message", resp.data)

    def test_c8_missing_voucher_number_returns_400(self):
        body = _redemption_body()
        del body["voucher_number"]
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("message", resp.data)

    def test_c9_unknown_bb_returns_460(self):
        """Whitespace-only Gov_Stack_BB passes serializer but service raises 460."""
        resp = self._post({
            "voucher_number": FIXED_SERIAL,
            "Gov_Stack_BB": "   ",  # all whitespace — strip() → ""
        })
        self.assertEqual(resp.status_code, 460, resp.data)

    def test_c10_unknown_voucher_returns_456(self):
        resp = self._post(_redemption_body(voucher_number="999999"))
        self.assertEqual(resp.status_code, 456)
        self.assertIn("message", resp.data)

    def test_c11_non_activated_voucher_returns_456(self):
        """PREACTIVATED voucher cannot be redeemed (must be ACTIVATED first)."""
        _make_voucher(serial=FIXED_SERIAL_2, status=GovStackVoucher.STATUS_PREACTIVATED)
        resp = self._post(_redemption_body(voucher_number=FIXED_SERIAL_2))
        self.assertEqual(resp.status_code, 456)

    def test_c12_integer_voucher_number_accepted(self):
        """Harness sends voucher_number as an integer."""
        resp = self._post({
            "voucher_number": int(FIXED_SERIAL),
            "Gov_Stack_BB": BB_ID,
        })
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_c13_double_redemption_returns_456(self):
        """
        Redeeming an already-CONSUMED voucher must return 456.

        This tests the most common real-world error path for a voucher system,
        and validates that the select_for_update() lock in redeem() leaves the
        voucher in CONSUMED state after the first redemption so the second
        attempt sees an invalid transition and raises InvalidVoucherSerial.
        """
        v = _make_voucher(serial=FIXED_SERIAL_3, status=GovStackVoucher.STATUS_ACTIVATED)
        # First redemption — must succeed.
        resp1 = self._post(_redemption_body(voucher_number=FIXED_SERIAL_3))
        self.assertEqual(resp1.status_code, 200, resp1.data)
        v.refresh_from_db()
        self.assertEqual(v.status, GovStackVoucher.STATUS_CONSUMED)
        # Second redemption — voucher is CONSUMED (terminal) → 456.
        resp2 = self._post(_redemption_body(voucher_number=FIXED_SERIAL_3))
        self.assertEqual(resp2.status_code, 456)
        self.assertIn("message", resp2.data)


# ---------------------------------------------------------------------------
# D. VoucherStatusCheck view — GET
# ---------------------------------------------------------------------------

class VoucherStatusCheckGetTest(TestCase):
    """GET /govstack/payments/vouchers/voucherstatuscheck/{serial}"""

    def setUp(self):
        self.client = APIClient()
        self.voucher = _make_voucher(
            serial=FIXED_SERIAL,
            status=GovStackVoucher.STATUS_ACTIVATED,
        )

    def test_d1_known_serial_returns_200(self):
        resp = self.client.get(_status_url(FIXED_SERIAL))
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_d2_response_shape(self):
        resp = self.client.get(_status_url(FIXED_SERIAL))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("status", resp.data)
        self.assertIn("serialNumber", resp.data)
        self.assertIn("value", resp.data)

    def test_d3_status_is_integer(self):
        resp = self.client.get(_status_url(FIXED_SERIAL))
        self.assertIsInstance(resp.data["status"], int)
        # ACTIVATED = 2 in STATUS_INT_MAP
        self.assertEqual(resp.data["status"], 2)

    def test_d4_unknown_serial_returns_456(self):
        resp = self.client.get(_status_url("999999"))
        self.assertEqual(resp.status_code, 456)
        self.assertIn("message", resp.data)

    def test_d5_value_matches_amount(self):
        resp = self.client.get(_status_url(FIXED_SERIAL))
        self.assertEqual(Decimal(resp.data["value"]), Decimal(AMOUNT))


# ---------------------------------------------------------------------------
# E. VoucherCancellation view — PATCH
# ---------------------------------------------------------------------------

class VoucherCancellationHarnessTest(TestCase):
    """PATCH /govstack/payments/vouchers/voucherstatuscheck/{serial}"""

    def setUp(self):
        self.client = APIClient()

    def _patch(self, serial: str) -> object:
        return self.client.patch(_status_url(serial))

    def test_e1_cancel_preactivated_voucher_returns_200(self):
        v = _make_voucher(serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_PREACTIVATED)
        resp = self._patch(FIXED_SERIAL)
        self.assertEqual(resp.status_code, 200, resp.data)
        v.refresh_from_db()
        self.assertEqual(v.status, GovStackVoucher.STATUS_CANCELLED)

    def test_e2_cancel_activated_voucher_returns_200(self):
        v = _make_voucher(serial=FIXED_SERIAL_2, status=GovStackVoucher.STATUS_ACTIVATED)
        resp = self._patch(FIXED_SERIAL_2)
        self.assertEqual(resp.status_code, 200, resp.data)
        v.refresh_from_db()
        self.assertEqual(v.status, GovStackVoucher.STATUS_CANCELLED)

    def test_e3_response_shape(self):
        _make_voucher(serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_PREACTIVATED)
        resp = self._patch(FIXED_SERIAL)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("voucherSerialNumber", resp.data)
        self.assertIn("voucherStatus", resp.data)

    def test_e4_voucher_status_is_cancelled(self):
        _make_voucher(serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_PREACTIVATED)
        resp = self._patch(FIXED_SERIAL)
        self.assertEqual(resp.data["voucherStatus"], GovStackVoucher.STATUS_CANCELLED)

    def test_e5_unknown_serial_returns_463(self):
        resp = self._patch("999999")
        self.assertEqual(resp.status_code, 463)
        self.assertIn("message", resp.data)

    def test_e6_already_cancelled_returns_464(self):
        _make_voucher(serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_CANCELLED)
        resp = self._patch(FIXED_SERIAL)
        self.assertEqual(resp.status_code, 464)
        self.assertIn("message", resp.data)

    def test_e7_consumed_voucher_returns_463(self):
        """CONSUMED is a terminal state — cannot cancel → 463."""
        _make_voucher(serial=FIXED_SERIAL_3, status=GovStackVoucher.STATUS_CONSUMED)
        resp = self._patch(FIXED_SERIAL_3)
        self.assertEqual(resp.status_code, 463)
        self.assertIn("message", resp.data)


# ---------------------------------------------------------------------------
# F. GovStackVoucherService — unit tests
# ---------------------------------------------------------------------------

class VoucherServicePreactivateTest(TestCase):
    """Service-layer tests for GovStackVoucherService.preactivate()."""

    @patch(
        "apps.payments.govstack_services._generate_voucher_serial",
        return_value=FIXED_SERIAL,
    )
    def test_f1_returns_preactivated_voucher(self, _mock):
        v = GovStackVoucherService.preactivate(
            voucher_amount=Decimal("100.00"),
            voucher_currency=CURRENCY,
            voucher_group=GROUP,
            issuing_bb=BB_ID,
        )
        self.assertEqual(v.status, GovStackVoucher.STATUS_PREACTIVATED)

    @patch(
        "apps.payments.govstack_services._generate_voucher_serial",
        return_value=FIXED_SERIAL,
    )
    def test_f2_stores_correct_fields(self, _mock):
        v = GovStackVoucherService.preactivate(
            voucher_amount=Decimal("75.50"),
            voucher_currency="AED",
            voucher_group="Food Voucher",
            issuing_bb=BB_ID,
        )
        self.assertEqual(v.amount, Decimal("75.50"))
        self.assertEqual(v.currency, "AED")
        self.assertEqual(v.group_code, "Food Voucher")
        self.assertEqual(v.issuing_bb, BB_ID)

    @patch(
        "apps.payments.govstack_services._generate_voucher_serial",
        return_value=FIXED_SERIAL,
    )
    def test_f3_creates_audit_entry(self, _mock):
        v = GovStackVoucherService.preactivate(
            voucher_amount=Decimal("100.00"),
            voucher_currency=CURRENCY,
            voucher_group=GROUP,
            issuing_bb=BB_ID,
        )
        entry = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_VOUCHER_PREACTIVATED,
            object_pk=str(v.pk),
        ).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor_bb_id, BB_ID)

    @patch(
        "apps.payments.govstack_services._generate_voucher_serial",
        return_value=FIXED_SERIAL,
    )
    def test_f4_audit_details_never_contain_payee_functional_id(self, _mock):
        PAYEE_ID = "3e4c9a1b-0f42-dead"
        GovStackVoucherService.preactivate(
            voucher_amount=Decimal("100.00"),
            voucher_currency=CURRENCY,
            voucher_group=GROUP,
            issuing_bb=BB_ID,
            payee_functional_id=PAYEE_ID,
        )
        for entry in GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_VOUCHER_PREACTIVATED,
        ):
            details_str = json.dumps(entry.details)
            self.assertNotIn(PAYEE_ID, details_str)

    def test_f5_zero_amount_raises_invalid_voucher_amount(self):
        with self.assertRaises(InvalidVoucherAmount):
            GovStackVoucherService.preactivate(
                voucher_amount=Decimal("0"),
                voucher_currency=CURRENCY,
                voucher_group=GROUP,
                issuing_bb=BB_ID,
            )

    def test_f6_negative_amount_raises_invalid_voucher_amount(self):
        with self.assertRaises(InvalidVoucherAmount):
            GovStackVoucherService.preactivate(
                voucher_amount=Decimal("-10.00"),
                voucher_currency=CURRENCY,
                voucher_group=GROUP,
                issuing_bb=BB_ID,
            )

    def test_f7_empty_group_raises_invalid_voucher_group(self):
        with self.assertRaises(InvalidVoucherGroup):
            GovStackVoucherService.preactivate(
                voucher_amount=Decimal("100.00"),
                voucher_currency=CURRENCY,
                voucher_group="   ",  # whitespace-only
                issuing_bb=BB_ID,
            )

    def test_f8_empty_bb_raises_govstack_bb_not_found(self):
        with self.assertRaises(GovStackBBNotFound):
            GovStackVoucherService.preactivate(
                voucher_amount=Decimal("100.00"),
                voucher_currency=CURRENCY,
                voucher_group=GROUP,
                issuing_bb="",
            )

    @patch(
        "apps.payments.govstack_services._generate_voucher_serial",
        side_effect=[FIXED_SERIAL, FIXED_SERIAL_2],
    )
    def test_f30_serial_collision_retry_uses_second_serial(self, mock_gen):
        """
        When _generate_voucher_serial() returns a serial that is already taken,
        preactivate() retries and succeeds with the second serial.

        Validates the _SERIAL_MAX_RETRIES collision loop in GovStackVoucherService.preactivate().
        """
        # Pre-occupy FIXED_SERIAL so the first attempt causes an IntegrityError.
        _make_voucher(serial=FIXED_SERIAL)

        # preactivate() should:
        #   attempt 1 → serial = FIXED_SERIAL → IntegrityError (unique constraint)
        #   attempt 2 → serial = FIXED_SERIAL_2 → success
        voucher = GovStackVoucherService.preactivate(
            voucher_amount=Decimal("100.00"),
            voucher_currency=CURRENCY,
            voucher_group=GROUP,
            issuing_bb=BB_ID,
        )

        self.assertEqual(voucher.serial_number, FIXED_SERIAL_2)
        self.assertEqual(voucher.status, GovStackVoucher.STATUS_PREACTIVATED)
        # Generator called exactly twice: once for the collision, once for the success.
        self.assertEqual(mock_gen.call_count, 2)
        # Both vouchers exist in the DB.
        self.assertEqual(GovStackVoucher.objects.filter(
            serial_number__in=[FIXED_SERIAL, FIXED_SERIAL_2]
        ).count(), 2)


class VoucherServiceActivateTest(TestCase):
    """Service-layer tests for GovStackVoucherService.activate()."""

    def setUp(self):
        self.voucher = _make_voucher(serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_PREACTIVATED)

    def test_f9_transitions_to_activated(self):
        v = GovStackVoucherService.activate(
            voucher_serial_number=FIXED_SERIAL,
            issuing_bb=BB_ID,
        )
        self.assertEqual(v.status, GovStackVoucher.STATUS_ACTIVATED)
        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.status, GovStackVoucher.STATUS_ACTIVATED)

    def test_f10_creates_audit_entry(self):
        GovStackVoucherService.activate(voucher_serial_number=FIXED_SERIAL, issuing_bb=BB_ID)
        entry = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_VOUCHER_ACTIVATED,
            object_pk=str(self.voucher.pk),
        ).first()
        self.assertIsNotNone(entry)

    def test_f11_unknown_serial_raises_invalid_voucher_serial(self):
        with self.assertRaises(InvalidVoucherSerial):
            GovStackVoucherService.activate(voucher_serial_number="999999", issuing_bb=BB_ID)

    def test_f12_empty_bb_raises_govstack_bb_not_found(self):
        with self.assertRaises(GovStackBBNotFound):
            GovStackVoucherService.activate(voucher_serial_number=FIXED_SERIAL, issuing_bb="")

    def test_f13_consumed_voucher_raises_invalid_voucher_serial(self):
        """CONSUMED → ACTIVATED is not in ALLOWED_TRANSITIONS."""
        v = _make_voucher(serial=FIXED_SERIAL_2, status=GovStackVoucher.STATUS_CONSUMED)
        with self.assertRaises(InvalidVoucherSerial):
            GovStackVoucherService.activate(voucher_serial_number=FIXED_SERIAL_2, issuing_bb=BB_ID)


class VoucherServiceRedeemTest(TestCase):
    """Service-layer tests for GovStackVoucherService.redeem()."""

    def setUp(self):
        self.voucher = _make_voucher(serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_ACTIVATED)

    def test_f14_transitions_to_consumed(self):
        v = GovStackVoucherService.redeem(voucher_number=FIXED_SERIAL, issuing_bb=BB_ID)
        self.assertEqual(v.status, GovStackVoucher.STATUS_CONSUMED)
        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.status, GovStackVoucher.STATUS_CONSUMED)

    def test_f15_stores_merchant_details(self):
        GovStackVoucherService.redeem(
            voucher_number=FIXED_SERIAL,
            issuing_bb=BB_ID,
            merchant_name="Corner Shop",
            merchant_bank_details="ACCT-1234",
            merchant_voucher_group="Food",
        )
        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.redeemed_merchant_name, "Corner Shop")
        self.assertEqual(self.voucher.redeemed_merchant_bank_details, "ACCT-1234")
        self.assertEqual(self.voucher.redeemed_merchant_voucher_group, "Food")

    def test_f16_stores_redeemed_at_timestamp(self):
        GovStackVoucherService.redeem(voucher_number=FIXED_SERIAL, issuing_bb=BB_ID)
        self.voucher.refresh_from_db()
        self.assertIsNotNone(self.voucher.redeemed_at)

    def test_f17_transaction_id_non_empty_and_fits_field(self):
        v = GovStackVoucherService.redeem(voucher_number=FIXED_SERIAL, issuing_bb=BB_ID)
        self.assertTrue(v.redemption_transaction_id)
        self.assertLessEqual(len(v.redemption_transaction_id), 20)

    def test_f18_creates_audit_entry(self):
        GovStackVoucherService.redeem(voucher_number=FIXED_SERIAL, issuing_bb=BB_ID)
        entry = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_VOUCHER_REDEEMED,
            object_pk=str(self.voucher.pk),
        ).first()
        self.assertIsNotNone(entry)

    def test_f19_audit_details_never_contain_merchant_bank_details(self):
        SECRET_BANK = "TOP_SECRET_ACCOUNT_1234567890"
        GovStackVoucherService.redeem(
            voucher_number=FIXED_SERIAL,
            issuing_bb=BB_ID,
            merchant_bank_details=SECRET_BANK,
        )
        for entry in GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_VOUCHER_REDEEMED,
        ):
            details_str = json.dumps(entry.details)
            self.assertNotIn(SECRET_BANK, details_str)

    def test_f20_unknown_serial_raises_invalid_voucher_serial(self):
        with self.assertRaises(InvalidVoucherSerial):
            GovStackVoucherService.redeem(voucher_number="999999", issuing_bb=BB_ID)

    def test_f21_empty_bb_raises_govstack_bb_not_found(self):
        with self.assertRaises(GovStackBBNotFound):
            GovStackVoucherService.redeem(voucher_number=FIXED_SERIAL, issuing_bb="")


class VoucherServiceCancelTest(TestCase):
    """Service-layer tests for GovStackVoucherService.cancel()."""

    def test_f22_cancel_preactivated(self):
        v = _make_voucher(serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_PREACTIVATED)
        result = GovStackVoucherService.cancel(voucher_serial_number=FIXED_SERIAL)
        self.assertEqual(result.status, GovStackVoucher.STATUS_CANCELLED)
        v.refresh_from_db()
        self.assertEqual(v.status, GovStackVoucher.STATUS_CANCELLED)

    def test_f23_cancel_activated(self):
        v = _make_voucher(serial=FIXED_SERIAL_2, status=GovStackVoucher.STATUS_ACTIVATED)
        result = GovStackVoucherService.cancel(voucher_serial_number=FIXED_SERIAL_2)
        self.assertEqual(result.status, GovStackVoucher.STATUS_CANCELLED)

    def test_f24_unknown_serial_raises_invalid_cancellation_serial(self):
        with self.assertRaises(InvalidCancellationSerial):
            GovStackVoucherService.cancel(voucher_serial_number="999999")

    def test_f25_already_cancelled_raises_voucher_already_cancelled(self):
        _make_voucher(serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_CANCELLED)
        with self.assertRaises(VoucherAlreadyCancelled):
            GovStackVoucherService.cancel(voucher_serial_number=FIXED_SERIAL)

    def test_f26_consumed_raises_invalid_cancellation_serial(self):
        """CONSUMED is terminal — cannot cancel."""
        _make_voucher(serial=FIXED_SERIAL_3, status=GovStackVoucher.STATUS_CONSUMED)
        with self.assertRaises(InvalidCancellationSerial):
            GovStackVoucherService.cancel(voucher_serial_number=FIXED_SERIAL_3)

    def test_f27_creates_audit_entry(self):
        v = _make_voucher(serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_PREACTIVATED)
        GovStackVoucherService.cancel(voucher_serial_number=FIXED_SERIAL)
        entry = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_VOUCHER_CANCELLED,
            object_pk=str(v.pk),
        ).first()
        self.assertIsNotNone(entry)


class VoucherServiceGetStatusTest(TestCase):
    """Service-layer tests for GovStackVoucherService.get_status()."""

    def setUp(self):
        self.voucher = _make_voucher(serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_ACTIVATED)

    def test_f28_returns_voucher_for_known_serial(self):
        v = GovStackVoucherService.get_status(serial_number=FIXED_SERIAL)
        self.assertEqual(v.pk, self.voucher.pk)
        self.assertEqual(v.serial_number, FIXED_SERIAL)

    def test_f29_unknown_serial_raises_invalid_voucher_serial(self):
        with self.assertRaises(InvalidVoucherSerial):
            GovStackVoucherService.get_status(serial_number="999999")


# ---------------------------------------------------------------------------
# G. Security invariants
# ---------------------------------------------------------------------------

class VoucherSecurityInvariantsTest(TestCase):
    """
    Security: voucher_secret, payee_functional_id must NEVER appear in
    response bodies, logs, or audit entry details.
    """

    def setUp(self):
        self.client = APIClient()

    @patch(
        "apps.payments.govstack_services._generate_voucher_serial",
        return_value=FIXED_SERIAL,
    )
    def test_g1_preactivation_response_never_has_voucher_secret(self, _mock):
        resp = self.client.post(
            PREACTIVATION_URL,
            data=json.dumps(_preactivation_body()),
            content_type="application/json",
        )
        raw = json.dumps(resp.data if isinstance(resp.data, dict) else {})
        self.assertNotIn("voucher_secret", raw)
        self.assertNotIn("voucherSecret", raw)

    @patch(
        "apps.payments.govstack_services._generate_voucher_serial",
        return_value=FIXED_SERIAL,
    )
    def test_g2_payee_functional_id_never_in_preactivation_response(self, _mock):
        PAYEE = "deadbeef-1234-5678"
        v = GovStackVoucherService.preactivate(
            voucher_amount=Decimal("100.00"),
            voucher_currency=CURRENCY,
            voucher_group=GROUP,
            issuing_bb=BB_ID,
            payee_functional_id=PAYEE,
        )
        # Status check response must not include payee_functional_id
        resp = self.client.get(_status_url(v.serial_number))
        raw = json.dumps(resp.data if isinstance(resp.data, dict) else {})
        self.assertNotIn(PAYEE, raw)
        self.assertNotIn("payee_functional_id", raw)

    @patch(
        "apps.payments.govstack_services._generate_voucher_serial",
        return_value=FIXED_SERIAL,
    )
    def test_g3_voucher_secret_never_in_audit_details(self, _mock):
        GovStackVoucherService.preactivate(
            voucher_amount=Decimal("100.00"),
            voucher_currency=CURRENCY,
            voucher_group=GROUP,
            issuing_bb=BB_ID,
        )
        for entry in GovStackPaymentAuditEntry.objects.all():
            details_str = json.dumps(entry.details)
            self.assertNotIn("voucher_secret", details_str)
            self.assertNotIn("voucherSecret", details_str)

    def test_g4_merchant_bank_details_never_in_audit_details(self):
        v = _make_voucher(serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_ACTIVATED)
        BANK = "SUPER_SECRET_BANK_ACCOUNT_99"
        GovStackVoucherService.redeem(
            voucher_number=FIXED_SERIAL,
            issuing_bb=BB_ID,
            merchant_bank_details=BANK,
        )
        for entry in GovStackPaymentAuditEntry.objects.filter(
            object_pk=str(v.pk),
        ):
            details_str = json.dumps(entry.details)
            self.assertNotIn(BANK, details_str)

    def test_g5_status_check_response_has_no_voucher_secret(self):
        _make_voucher(serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_PREACTIVATED)
        resp = self.client.get(_status_url(FIXED_SERIAL))
        raw = json.dumps(resp.data if isinstance(resp.data, dict) else {})
        self.assertNotIn("voucher_secret", raw)
        self.assertNotIn("voucherSecret", raw)

    def test_g6_cancellation_error_message_is_generic(self):
        """InvalidCancellationSerial exception message must not include the serial number."""
        exc = InvalidCancellationSerial()
        self.assertNotIn("999999", str(exc))
        self.assertNotIn(FIXED_SERIAL, str(exc))


# ---------------------------------------------------------------------------
# H. Model: GovStackVoucher
# ---------------------------------------------------------------------------

class GovStackVoucherModelTest(TestCase):
    """Unit tests for GovStackVoucher model methods and properties."""

    def _voucher(self, status: str) -> GovStackVoucher:
        return GovStackVoucher(
            serial_number="000001",
            amount=Decimal("100.00"),
            currency="USD",
            group_code="Test",
            status=status,
            issuing_bb="TEST_BB",
        )

    def test_h1_status_int_map(self):
        for status, expected_int in GovStackVoucher.STATUS_INT_MAP.items():
            v = self._voucher(status)
            self.assertEqual(
                v.status_int,
                expected_int,
                f"status_int for {status!r} should be {expected_int}",
            )

    def test_h2_is_terminal_true_for_terminal_states(self):
        for status in (
            GovStackVoucher.STATUS_CONSUMED,
            GovStackVoucher.STATUS_CANCELLED,
            GovStackVoucher.STATUS_PURGED,
        ):
            self.assertTrue(self._voucher(status).is_terminal, f"Expected {status!r} to be terminal")

    def test_h3_is_terminal_false_for_non_terminal_states(self):
        for status in (
            GovStackVoucher.STATUS_PREACTIVATED,
            GovStackVoucher.STATUS_ACTIVATED,
            GovStackVoucher.STATUS_BLOCKED,
            GovStackVoucher.STATUS_SUSPENDED,
        ):
            self.assertFalse(self._voucher(status).is_terminal, f"Expected {status!r} to be non-terminal")

    def test_h4_transition_to_raises_for_invalid_transitions(self):
        # CONSUMED is terminal — no transitions allowed
        v = self._voucher(GovStackVoucher.STATUS_CONSUMED)
        with self.assertRaises(ValueError):
            v.transition_to(GovStackVoucher.STATUS_ACTIVATED)

        # PREACTIVATED cannot go directly to CONSUMED
        v2 = self._voucher(GovStackVoucher.STATUS_PREACTIVATED)
        with self.assertRaises(ValueError):
            v2.transition_to(GovStackVoucher.STATUS_CONSUMED)

    def test_h5_allowed_transitions_all_succeed(self):
        for from_status, to_statuses in GovStackVoucher.ALLOWED_TRANSITIONS.items():
            for to_status in to_statuses:
                v = self._voucher(from_status)
                v.transition_to(to_status)  # Must not raise
                self.assertEqual(v.status, to_status)


# ---------------------------------------------------------------------------
# I. Full chained harness flow
# ---------------------------------------------------------------------------

class VoucherFullChainedFlowTest(TestCase):
    """
    End-to-end chained flow matching the GovStack harness test sequence:
      1. POST /voucher_preactivation → get serial
      2. PATCH /voucher_activation → serial moves to ACTIVATED
      3. GET /voucherstatuscheck → status = 2 (ACTIVATED)
      4. POST /voucher_redemption → status = 3 (CONSUMED)
      5. PATCH /voucherstatuscheck/{serial} → should fail 463 (CONSUMED is terminal)
    """

    def setUp(self):
        self.client = APIClient()

    @patch(
        "apps.payments.govstack_services._generate_voucher_serial",
        return_value=FIXED_SERIAL,
    )
    def test_i1_full_chained_flow(self, _mock):
        # Step 1: Pre-activate
        resp1 = self.client.post(
            PREACTIVATION_URL,
            data=json.dumps(_preactivation_body()),
            content_type="application/json",
        )
        self.assertEqual(resp1.status_code, 200, resp1.data)
        serial = resp1.data["voucherSerialNumber"]
        self.assertEqual(serial, FIXED_SERIAL)

        # Step 2: Activate
        resp2 = self.client.patch(
            ACTIVATION_URL,
            data=json.dumps(_activation_body(serial=serial)),
            content_type="application/json",
        )
        self.assertEqual(resp2.status_code, 200, resp2.data)
        self.assertEqual(resp2.data["voucherStatus"], GovStackVoucher.STATUS_ACTIVATED)

        # Step 3: Status check (ACTIVATED)
        resp3 = self.client.get(_status_url(serial))
        self.assertEqual(resp3.status_code, 200)
        self.assertEqual(resp3.data["status"], 2)  # STATUS_INT_MAP["activated"] = 2

        # Step 4: Redeem
        resp4 = self.client.post(
            REDEMPTION_URL,
            data=json.dumps(_redemption_body(voucher_number=serial)),
            content_type="application/json",
        )
        self.assertEqual(resp4.status_code, 200, resp4.data)
        self.assertEqual(resp4.data["status"], 3)  # STATUS_INT_MAP["consumed"] = 3

        # Step 5: Attempt cancel on consumed voucher → 463
        resp5 = self.client.patch(_status_url(serial))
        self.assertEqual(resp5.status_code, 463)

    @patch(
        "apps.payments.govstack_services._generate_voucher_serial",
        return_value=FIXED_SERIAL,
    )
    def test_i2_preactivate_then_cancel(self, _mock):
        """Harness: cancellation flow — preactivate → cancel → confirm cancelled."""
        # Preactivate
        resp1 = self.client.post(
            PREACTIVATION_URL,
            data=json.dumps(_preactivation_body()),
            content_type="application/json",
        )
        self.assertEqual(resp1.status_code, 200)
        serial = resp1.data["voucherSerialNumber"]

        # Cancel
        resp2 = self.client.patch(_status_url(serial))
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.data["voucherStatus"], GovStackVoucher.STATUS_CANCELLED)

        # Double-cancel → 464
        resp3 = self.client.patch(_status_url(serial))
        self.assertEqual(resp3.status_code, 464)
