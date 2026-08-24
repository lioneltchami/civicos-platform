"""
test_govstack_vouchers.py

Comprehensive tests for GovStack Payments BB — Voucher Engine (spec §18).

Coverage matrix:
  A. VoucherPreactivation view — harness scenarios
     A1:  Smoke POST → HTTP 200, correct shape
     A2:  Full fields → HTTP 200, all four response fields present
     A3:  Missing voucher_amount → HTTP 400 (DRF required field)
     A4:  Missing voucher_currency → HTTP 400
     A5:  Missing voucher_group → HTTP 400
     A6:  Missing Gov_Stack_BB → HTTP 400
     A7:  Non-numeric voucher_amount → HTTP 400
     A8:  Invalid voucher_currency format (2 chars) → HTTP 453 (service raises InvalidVoucherCurrency)
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
     B3:  voucherStatus is "Activated" after activation
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
     D4:  GET unknown serial → HTTP 456 (the old "GAP-7" claim that spec §13.5
          required 400 was confirmed FALSE against the live harness; removed)
     D5:  value matches amount (JSON number — float, spec §13.5)
     D8:  GET unknown serial → HTTP 456, not 400 (harness negative path)
     D9:  GET unknown serial → body["status"] == 9 (STATUS_ERROR_INT)
     D10: GET unknown serial → body["serialNumber"] echoes the submitted serial
     D11: GET unknown serial → body["value"] == 0.0
     D12: GET unknown serial → body["message"] == "Voucher not found."

  E. VoucherCancellation view — PATCH scenarios
     E1:  PATCH cancel PREACTIVATED voucher → HTTP 200
     E2:  PATCH cancel ACTIVATED voucher → HTTP 200
     E3:  Response shape: {voucherSerialNumber, voucherStatus}
     E4:  voucherStatus is "Cancelled" after cancellation
     E5:  Cancel unknown serial → HTTP 463
     E6:  Cancel already-cancelled voucher → HTTP 464
     E7:  Cancel CONSUMED voucher → HTTP 463 (terminal state, invalid transition)

  F31–F36: seed_govstack_vouchers management command
  F37: GOVSTACK_VOUCHER_REQUIRE_JWT=True → unauthenticated POST to voucher_redemption → 401/403
  F38: GOVSTACK_VOUCHER_REQUIRE_JWT=True → unauthenticated PATCH to voucherstatuscheck → 401/403
  F39: GOVSTACK_VOUCHER_REQUIRE_JWT=True → unauthenticated GET to voucherstatuscheck → 401/403
  F40: GOVSTACK_VOUCHER_REQUIRE_JWT=False (harness mode) → unauthenticated GET to voucherstatuscheck → HTTP 200
  F41: GOVSTACK_VOUCHER_REQUIRE_JWT=False (harness mode) → unauthenticated POST to voucher_redemption → HTTP 200
  F42: GOVSTACK_VOUCHER_REQUIRE_JWT=True + authenticated user → POST to voucher_redemption → HTTP 200
  F43: GOVSTACK_VOUCHER_REQUIRE_JWT=True + authenticated user → GET to voucherstatuscheck → HTTP 200
  F44: GOVSTACK_VOUCHER_REQUIRE_JWT=False (harness mode) → unauthenticated PATCH to voucherstatuscheck → HTTP 200

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

  F (seed). SeedGovStackVouchersCommandTests — management command unit tests
     F31: seed command creates all 16 expected serials when DB is empty
     F32: seed command is idempotent — zero new rows on second run, no IntegrityError
     F33: each seeded voucher has its expected per-serial status — 13 rows
          STATUS_PREACTIVATED, 6001 CONSUMED, 6002/6004 ACTIVATED (6002 also
          has a past expiry_date; 6001 also has redemption metadata; both are
          exercised end-to-end via voucherstatuscheck → 458/459)
     F34: serial_number stored as exact string value (no zero-padding, no truncation)
     F35: --reset flag deletes all seed serials and recreates them in their
          expected per-serial states
     F36: issuing_bb is exactly "GS-HARNESS" on all seeded rows

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
"""  # noqa: E501, RUF002

from __future__ import annotations

import json
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.payments.govstack_exceptions import (
    CannotCreditMerchant,
    GovStackBBNotFound,
    InsufficientFunds,
    InvalidCancellationSerial,
    InvalidVoucherAmount,
    InvalidVoucherGroup,
    InvalidVoucherNumber,
    InvalidVoucherSerial,
    VoucherAlreadyCancelled,
    VoucherAlreadyUsed,
    VoucherExpired,
)
from apps.payments.govstack_models import (
    GovStackPaymentAuditEntry,
    GovStackRegisteredBB,
    GovStackVoucher,
)
from apps.payments.govstack_services import (
    GovStackVoucherService,
    _is_unregistered_gov_stack_bb,
)

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
# Patched via mock to avoid randomness. 18 digits (matching the real
# _generate_voucher_serial() output format) so that tests exercise the same
# length the harness's own schema (16-25 chars) and this codebase's
# max_length=20 request serializers actually enforce in production.
FIXED_SERIAL = "100000000000000001"
FIXED_SERIAL_2 = "100000000000000002"
FIXED_SERIAL_3 = "100000000000000003"


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


def _redemption_body(
    *,
    voucher_number=FIXED_SERIAL,
    gov_stack_bb=BB_ID,
    merchant_voucher_group=GROUP,
    merchant_name="Test Merchant",
    merchant_bank_details="BANK001",
) -> dict:
    return {
        "voucher_number": voucher_number,
        "Gov_Stack_BB": gov_stack_bb,
        "merchant_name": merchant_name,
        "merchant_bank_details": merchant_bank_details,
        "merchant_voucher_group": merchant_voucher_group,
        "override": False,
    }


def _cancellation_body(*, serial=FIXED_SERIAL, gov_stack_bb="bb-digital-registries") -> dict:
    """
    Body for PATCH /voucherstatuscheck/{serial} (cancellation).

    The real harness ALWAYS sends this body on every cancellation PATCH, in
    addition to the URL path segment carrying the same serial — see
    VoucherCancellationRequestSerializer. "bb-digital-registries" is the
    harness's real positive-scenario value for this endpoint.
    """
    return {
        "voucherserialnumber": serial,
        "Gov_Stack_BB": gov_stack_bb,
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
    from datetime import timedelta

    from django.utils import timezone

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
        """
        Harness-required schema (test/openAPI/Payment_BB_Voucher_api_test.json):
        {voucher_number, voucher_serial_number, expiry_date_time} — all 3
        required, snake_case. Replaces the old camelCase
        {voucherNumber, voucherSerialNumber, voucherGroup, expiryDate} shape.
        """
        resp = self._post(_preactivation_body())
        self.assertEqual(resp.status_code, 200)
        data = resp.data
        self.assertIn("voucher_number", data)
        self.assertIn("voucher_serial_number", data)
        self.assertIn("expiry_date_time", data)
        self.assertEqual(data["voucher_number"], FIXED_SERIAL)
        self.assertEqual(data["voucher_serial_number"], FIXED_SERIAL)
        self.assertIsNotNone(data["expiry_date_time"])

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

    def test_a8_invalid_currency_format_returns_453(self):
        """
        2-char currency code passes serializer (non-empty) but fails ISO 4217
        format validation in GovStackVoucherService.preactivate() → HTTP 453.

        GAP-C1 regression guard: the GovStack Payments spec assigns status 453
        to invalid currency codes.  A serializer-level rejection would return
        HTTP 400, which is a spec violation.
        """
        resp = self._post(_preactivation_body(voucher_currency="US"))
        self.assertEqual(resp.status_code, 453)
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

    def test_a11b_not_exist_sentinel_gov_stack_bb_returns_460(self):
        """
        The real harness's preactivation negative scenario sends the literal
        sentinel "not_exist" (not just a blank string) to trigger 460 — this
        is the blocklist behaviour added in P1
        (_is_known_invalid_gov_stack_bb), replacing the old blank-only check.
        """
        resp = self._post(_preactivation_body(gov_stack_bb="not_exist"))
        self.assertEqual(resp.status_code, 460)
        self.assertIn("message", resp.data)

    @patch(
        "apps.payments.govstack_services._generate_voucher_serial",
        return_value=FIXED_SERIAL,
    )
    def test_a11c_harness_positive_fixture_literal_gov_stack_bb_accepted(self, _mock):
        """
        The real harness's preactivation POSITIVE scenarios send the literal
        string "Gov_Stack_BB" as the value (an odd but real fixture quirk).
        This is not on the blocklist, so it must be accepted, not rejected.
        """
        resp = self._post(_preactivation_body(gov_stack_bb="Gov_Stack_BB"))
        self.assertEqual(resp.status_code, 200, resp.data)

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

    def test_a16_real_generator_output_satisfies_harness_schema(self):
        """
        Regression test for Issue A (SPEC_GOVSTACK_PAYMENTS_BB.md section 24.1):
        every other preactivation test above patches _generate_voucher_serial()
        to a fixed value, so none of them ever exercised the REAL generator
        against the harness's own schema. This test deliberately does NOT
        patch the generator.

        The harness's authoritative JSON schema (test/openAPI/features/support/
        helpers/helpers.js) requires voucher_number and voucher_serial_number
        to be strings of 16-25 characters. This codebase's own request
        serializers (VoucherActivationRequestSerializer.voucher_serial_number,
        VoucherRedemptionRequestSerializer.voucher_number) cap max_length=20,
        so the effective window this codebase must hit is 16-20 inclusive.
        """
        resp = self._post(_preactivation_body())
        self.assertEqual(resp.status_code, 200, resp.data)
        data = resp.data

        voucher_number = data["voucher_number"]
        voucher_serial_number = data["voucher_serial_number"]

        for value in (voucher_number, voucher_serial_number):
            self.assertIsInstance(value, str)
            self.assertTrue(
                16 <= len(value) <= 20,
                msg=f"Generated voucher id {value!r} has length {len(value)}, "
                f"expected 16-20 (harness requires 16-25; our serializers "
                f"cap max_length=20).",
            )
            self.assertTrue(
                value.isdigit(),
                msg=f"Generated voucher id {value!r} must be purely numeric "
                f"so _is_numeric_voucher_number() continues to work.",
            )

    def test_a17_null_voucher_amount_returns_452_not_400(self):
        """
        Harness-confirmed defect fix: the real harness's "invalid voucher
        amount" negative scenario (voucher_preactivation.feature) sends a
        literal JSON `null` for voucher_amount, expecting HTTP 452
        (InvalidVoucherAmount) from GovStackVoucherService.preactivate().

        Before allow_null=True was added to the DecimalField, DRF rejected
        the null at the field level with a generic HTTP 400 — before the
        service's own `voucher_amount is None` check ever ran. This test
        pins the fix directly against a live GovStack harness finding
        (do not "fix" this back to 400 — that regresses a confirmed
        harness-observed failure).
        """
        resp = self._post(_preactivation_body(voucher_amount=None))
        self.assertEqual(resp.status_code, 452, resp.data)
        self.assertIn("message", resp.data)

    def test_a18_null_voucher_group_returns_454_not_400(self):
        """
        Harness-confirmed defect fix: the real harness's "invalid
        voucher_group" negative scenario sends a literal JSON `null` for
        voucher_group, expecting HTTP 454 (InvalidVoucherGroup).

        Before allow_null=True was added to the CharField, DRF rejected the
        null at the field level with a generic HTTP 400. The service's
        `not voucher_group` check already treats None as falsy, short-
        circuiting before the .strip() call that would otherwise crash on
        NoneType — so no service-layer change was needed, only the
        serializer's allow_null.
        """
        resp = self._post(_preactivation_body(voucher_group=None))
        self.assertEqual(resp.status_code, 454, resp.data)
        self.assertIn("message", resp.data)


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
        """
        Harness-required schema: {result_status} — a non-empty free-form
        string, no enum. Replaces the old camelCase
        {voucherNumber, voucherSerialNumber, voucherStatus, voucherGroup} shape.
        """
        resp = self._patch(_activation_body(serial=FIXED_SERIAL))
        self.assertEqual(resp.status_code, 200)
        data = resp.data
        self.assertIn("result_status", data)
        self.assertIsInstance(data["result_status"], str)
        self.assertTrue(data["result_status"], "result_status must be non-empty")

    def test_b3_voucher_actually_transitions_to_activated(self):
        """
        result_status is a free-form success string (no enum requirement per
        the harness schema) — the real assertion is that the underlying
        voucher genuinely transitioned to ACTIVATED in the DB.
        """
        resp = self._patch(_activation_body(serial=FIXED_SERIAL))
        self.assertEqual(resp.status_code, 200)
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

    def test_b5b_invalid_sentinel_bb_returns_460(self):
        """
        Activation's negative Gov_Stack_BB scenario uses an unspecified-but-
        clearly-invalid literal per the harness JS (a fixed step, not
        parameterized). We use "invalid_bb" here as a representative
        known-invalid sentinel from the shared blocklist.
        """
        resp = self._patch({"voucher_serial_number": FIXED_SERIAL, "Gov_Stack_BB": "invalid_bb"})
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
        resp = self._patch(
            {
                "voucher_serial_number": int(FIXED_SERIAL),
                "Gov_Stack_BB": BB_ID,
            }
        )
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_b9_harness_invalid_serial_literal_returns_456_not_400(self):
        """
        Harness-confirmed defect fix: the real harness's "invalid
        voucher_serial_number" negative scenario (voucher_activation.feature)
        sends the literal 'invalid_voucher_serial_number' (29 characters) and
        expects HTTP 456 (InvalidVoucherSerial — not found) from
        GovStackVoucherService.activate().

        Before max_length was widened from 20 to 100 on this field, DRF
        rejected the 29-char literal at the serializer level with a generic
        HTTP 400 — before the service's own "not found" lookup ever ran.
        A 29-char serial can never match a real voucher row (the model
        field itself is max_length=20), so this always reaches the service's
        InvalidVoucherSerial(456) path regardless of the wider serializer
        cap — it never gets a chance to falsely match anything.
        """
        literal = "invalid_voucher_serial_number"
        self.assertEqual(len(literal), 29)
        resp = self._patch({"voucher_serial_number": literal, "Gov_Stack_BB": BB_ID})
        self.assertEqual(resp.status_code, 456, resp.data)
        self.assertIn("message", resp.data)


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
        """
        Harness-required schema: {result_status} — non-empty free-form
        string, no enum. Replaces the old
        {status, message, serialNumber, value, timestamp, transactionId} shape.
        """
        resp = self._post(_redemption_body())
        self.assertEqual(resp.status_code, 200)
        data = resp.data
        self.assertIn("result_status", data)
        self.assertIsInstance(data["result_status"], str)
        self.assertTrue(data["result_status"], "result_status must be non-empty")

    def test_c3_voucher_actually_transitions_to_consumed(self):
        """
        The response no longer echoes a status int — the real assertion is
        that the underlying voucher genuinely transitioned to CONSUMED.
        """
        resp = self._post(_redemption_body())
        self.assertEqual(resp.status_code, 200)
        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.status, GovStackVoucher.STATUS_CONSUMED)

    def test_c4_redemption_records_merchant_and_amount_on_the_voucher(self):
        """
        "value"/"serialNumber" are no longer in the response body, but the
        underlying redemption bookkeeping (amount, merchant details) must
        still be recorded correctly on the model.
        """
        resp = self._post(_redemption_body())
        self.assertEqual(resp.status_code, 200)
        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.amount, Decimal(AMOUNT))
        self.assertEqual(self.voucher.serial_number, FIXED_SERIAL)

    def test_c5_serial_number_matches_voucher(self):
        resp = self._post(_redemption_body())
        self.assertEqual(resp.status_code, 200)
        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.serial_number, FIXED_SERIAL)

    def test_c6_transaction_id_non_empty(self):
        resp = self._post(_redemption_body())
        self.assertEqual(resp.status_code, 200)
        self.voucher.refresh_from_db()
        tid = self.voucher.redemption_transaction_id
        self.assertTrue(tid, "redemption_transaction_id must be non-empty")
        self.assertLessEqual(len(tid), 20, "redemption_transaction_id must fit max_length=20")

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
        resp = self._post(
            {
                "voucher_number": FIXED_SERIAL,
                "Gov_Stack_BB": "   ",  # all whitespace — strip() → ""
            }
        )
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
        resp = self._post(
            {
                "voucher_number": int(FIXED_SERIAL),
                "Gov_Stack_BB": BB_ID,
            }
        )
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

    def test_c14_non_numeric_voucher_number_returns_461(self):
        """
        The real harness sends the literal string "notAnumber" as
        voucher_number to exercise InvalidVoucherNumber (461). Unambiguous
        and fully confident per the P1 plan.
        """
        resp = self._post(_redemption_body(voucher_number="notAnumber"))
        self.assertEqual(resp.status_code, 461)
        self.assertIn("message", resp.data)

    def test_c14b_non_numeric_voucher_number_same_length_as_new_format_returns_461(self):
        """
        Regression test for Issue A (SPEC_GOVSTACK_PAYMENTS_BB.md section 24.1):
        _generate_voucher_serial() now produces 18-digit numeric strings
        instead of 6-digit ones. Confirms _is_numeric_voucher_number() still
        correctly rejects a non-numeric value of the SAME length as the new
        format (18 chars) — not just the harness's short "notAnumber" literal
        — so the 461 (InvalidVoucherNumber) path is unaffected by the format
        change.
        """
        non_numeric_18_chars = "notanumbernotanumb"
        self.assertEqual(len(non_numeric_18_chars), 18)
        resp = self._post(_redemption_body(voucher_number=non_numeric_18_chars))
        self.assertEqual(resp.status_code, 461)
        self.assertIn("message", resp.data)

    def test_c15_insufficient_funds_sentinel_returns_462(self):
        """
        merchant_voucher_group == "insufficient funds" (case-insensitive,
        stripped) is the fallback signal for InsufficientFunds (462) when the
        merchant_name/merchant_bank_details pair doesn't match either of the
        two definitive fixture pairs. See
        GovStackVoucherService._classify_redemption_decline() — the 462/463
        ambiguity is now fully resolved via the GovStack reference/
        certification server's own mock config (examples/mock-bb-payments/
        mockoon-paymentsbbvoucher.json), not a guess.
        """
        resp = self._post(_redemption_body(merchant_voucher_group="insufficient funds"))
        self.assertEqual(resp.status_code, 462)
        self.assertIn("message", resp.data)
        # Voucher must NOT have been consumed — the decline must be checked
        # before the transition, not after.
        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.status, GovStackVoucher.STATUS_ACTIVATED)

    def test_c15b_ronan_oliver_vigor_bank_returns_462(self):
        """
        Definitive fixture pair for InsufficientFunds (462), confirmed
        against the GovStack reference server's Mockoon config, the
        harness's voucher_redemption.js hardcoded When-steps, and
        test-data.json's merchant fixture comments (3 independent sources).
        """
        resp = self._post(
            _redemption_body(
                merchant_name="Ronan Oliver",
                merchant_bank_details="Vigor Bank Group",
                merchant_voucher_group="insufficient funds",
            )
        )
        self.assertEqual(resp.status_code, 462, resp.data)
        self.assertIn("message", resp.data)

    def test_c15c_annie_krueger_omega_holding_returns_463(self):
        """
        Definitive fixture pair for CannotCreditMerchant (463) — the twin
        scenario to test_c15b above. Same 3-source corroboration. This was
        previously undetectable from client-side Gherkin fixtures alone
        (both scenarios send merchant_voucher_group == "insufficient funds")
        until the GovStack reference server's Mockoon config was checked.
        """
        resp = self._post(
            _redemption_body(
                merchant_name="Annie Krueger",
                merchant_bank_details="Omega Holding Company",
                merchant_voucher_group="insufficient funds",
            )
        )
        self.assertEqual(resp.status_code, 463, resp.data)
        self.assertIn("message", resp.data)
        # Voucher must NOT have been consumed on a decline.
        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.status, GovStackVoucher.STATUS_ACTIVATED)

    def test_c16_insufficient_funds_sentinel_case_and_whitespace_insensitive(self):
        """The classifier normalises case and surrounding whitespace."""
        resp = self._post(_redemption_body(merchant_voucher_group="  Insufficient Funds  "))
        self.assertEqual(resp.status_code, 462)

    def test_c17_unknown_bb_sentinel_invalid_bb_returns_460(self):
        """
        Redemption's negative Gov_Stack_BB scenario uses the sentinel
        "invalid_bb" (per the shared blocklist), not just a blank string.
        """
        resp = self._post(_redemption_body(gov_stack_bb="invalid_bb"))
        self.assertEqual(resp.status_code, 460, resp.data)


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
        """
        Harness-required schema: {voucher_status (7-value enum string),
        voucher_amount (STRING)}. Replaces the old
        {status (int), serialNumber, value (float)} shape.
        """
        resp = self.client.get(_status_url(FIXED_SERIAL))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("voucher_status", resp.data)
        self.assertIn("voucher_amount", resp.data)

    def test_d3_voucher_status_is_activated_enum_string(self):
        resp = self.client.get(_status_url(FIXED_SERIAL))
        self.assertEqual(resp.data["voucher_status"], "Activated")

    def test_d3b_preactivated_voucher_returns_pre_activated_enum_string(self):
        _make_voucher(serial=FIXED_SERIAL_2, status=GovStackVoucher.STATUS_PREACTIVATED)
        resp = self.client.get(_status_url(FIXED_SERIAL_2))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["voucher_status"], "Pre-Activated")

    def test_d4_unknown_serial_returns_456(self):
        """
        The old "GAP-7" logic claiming spec §13.5 required 400 (not 456) was
        confirmed FALSE against the live harness and has been removed.
        InvalidVoucherSerial (456) now propagates normally.
        """
        resp = self.client.get(_status_url("999999"))
        self.assertEqual(resp.status_code, 456)
        self.assertIn("message", resp.data)

    def test_d5_voucher_amount_is_a_string_not_a_float(self):
        """
        Confirmed bug fix: voucher_amount must be str(voucher.amount), not
        float(voucher.amount).
        """
        resp = self.client.get(_status_url(FIXED_SERIAL))
        self.assertIsInstance(
            resp.data["voucher_amount"],
            str,
            "Status check response 'voucher_amount' must be a string, not a float/int.",
        )
        self.assertEqual(resp.data["voucher_amount"], str(Decimal(AMOUNT)))

    def test_d8_unknown_serial_returns_456_not_400(self):
        resp = self.client.get(_status_url("DOESNOTEXIST"))
        self.assertEqual(resp.status_code, 456, resp.data)

    def test_d9_consumed_voucher_returns_458(self):
        """
        VoucherAlreadyUsed (458) is derived from the voucher's REAL status
        being CONSUMED — not a hardcoded "test serial" literal — so this
        works for any consumed voucher, not just a specific fixture value.
        """
        _make_voucher(serial=FIXED_SERIAL_3, status=GovStackVoucher.STATUS_CONSUMED)
        resp = self.client.get(_status_url(FIXED_SERIAL_3))
        self.assertEqual(resp.status_code, 458)
        self.assertIn("message", resp.data)

    def test_d10_expired_voucher_returns_459(self):
        """
        VoucherExpired (459) is derived from a REAL comparison of
        expiry_date against timezone.now() — not a hardcoded "test serial"
        literal. GovStackVoucherService.get_status() previously never
        performed this comparison at all.
        """
        from datetime import timedelta

        from django.utils import timezone as tz

        expired_serial = "500099"
        v = _make_voucher(serial=expired_serial, status=GovStackVoucher.STATUS_ACTIVATED)
        v.expiry_date = tz.now() - timedelta(days=1)
        v.save(update_fields=["expiry_date"])

        resp = self.client.get(_status_url(expired_serial))
        self.assertEqual(resp.status_code, 459)
        self.assertIn("message", resp.data)

    def test_d11_consumed_and_expired_voucher_returns_458_not_459(self):
        """
        Precedence: when a voucher is BOTH consumed and expired, 458
        (already used) must win over 459 (expired) — "already used" is
        treated as the more definitive terminal state.
        """
        from datetime import timedelta

        from django.utils import timezone as tz

        serial = "500098"
        v = _make_voucher(serial=serial, status=GovStackVoucher.STATUS_CONSUMED)
        v.expiry_date = tz.now() - timedelta(days=1)
        v.save(update_fields=["expiry_date"])

        resp = self.client.get(_status_url(serial))
        self.assertEqual(resp.status_code, 458)

    def test_d12_non_expired_voucher_returns_200(self):
        """Sanity check: a voucher with a future expiry_date is unaffected."""
        resp = self.client.get(_status_url(FIXED_SERIAL))
        self.assertEqual(resp.status_code, 200)

    # ------------------------------------------------------------------
    # D13/D14 — genuinely malformed input returns 400 (spec §13.5, N2 finding)
    # ------------------------------------------------------------------
    #
    # SPEC_GOVSTACK_PAYMENTS_BB.md §13.5 documents voucherserialnumber="{}" as
    # its own example of input that must be rejected with 400 — distinct from
    # an unknown-but-syntactically-plausible serial like "DOESNOTEXIST" or
    # "999999" (test_d4/test_d8 above), which must stay 456. These two tests
    # pin down the boundary so it can never silently drift back to either
    # extreme (always-400, which the withdrawn GAP-7 entry did; or the
    # pre-fix always-456, which left the spec's own documented case
    # unimplemented).

    def test_d13_literal_curly_braces_returns_400(self):
        """The spec's own documented malformed-input example: '{}' → 400."""
        resp = self.client.get(_status_url("{}"))
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("message", resp.data)

    def test_d14_whitespace_only_serial_returns_400(self):
        resp = self.client.get(_status_url("   "))
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_d15_alphabetic_unknown_serial_still_returns_456_not_400(self):
        """
        Guards the boundary from the other side: an alphanumeric-but-unknown
        serial must NOT be swept up by the D13/D14 malformed-input fix.
        Duplicates test_d8's assertion deliberately — this is the exact
        scenario a careless "digits only" implementation of the malformed
        gate would break.
        """
        resp = self.client.get(_status_url("DOESNOTEXIST"))
        self.assertEqual(resp.status_code, 456, resp.data)


# ---------------------------------------------------------------------------
# E. VoucherCancellation view — PATCH
# ---------------------------------------------------------------------------


class VoucherCancellationHarnessTest(TestCase):
    """
    PATCH /govstack/payments/vouchers/voucherstatuscheck/{serial}

    NEW (P1): the real harness ALWAYS sends a JSON body
    {voucherserialnumber, Gov_Stack_BB} on every cancellation PATCH, in
    addition to the URL path segment. This class's _patch() helper now
    sends that body by default (previously this endpoint didn't validate
    the body at all — see VoucherCancellationRequestSerializer).
    """

    def setUp(self):
        self.client = APIClient()

    def _patch(self, serial: str, body: dict | None = None) -> object:
        payload = _cancellation_body(serial=serial) if body is None else body
        return self.client.patch(
            _status_url(serial),
            data=json.dumps(payload),
            content_type="application/json",
        )

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
        """
        "message" is REQUIRED by the real harness schema — previously absent
        entirely. voucherSerialNumber/voucherStatus are kept additively.
        """
        _make_voucher(serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_PREACTIVATED)
        resp = self._patch(FIXED_SERIAL)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("voucherSerialNumber", resp.data)
        self.assertIn("voucherStatus", resp.data)
        self.assertIn("message", resp.data)
        self.assertIsInstance(resp.data["message"], str)
        self.assertTrue(resp.data["message"])

    def test_e4_voucher_status_is_cancelled(self):
        _make_voucher(serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_PREACTIVATED)
        resp = self._patch(FIXED_SERIAL)
        # GAP-6: spec requires title-case label, not raw DB constant.
        self.assertEqual(resp.data["voucherStatus"], "Cancelled")

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

    # ── New (P1): cancellation request-body validation ───────────────────────

    def test_e8_missing_voucherserialnumber_in_payload_returns_400(self):
        """The 2 harness 'missing X in payload' scenarios, confirmed via the
        real Gherkin: missing voucherserialnumber → 400."""
        _make_voucher(serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_PREACTIVATED)
        body = {"Gov_Stack_BB": "bb-digital-registries"}
        resp = self._patch(FIXED_SERIAL, body=body)
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("message", resp.data)
        # Voucher must be untouched — validation must fail before cancel() runs.
        v = GovStackVoucher.objects.get(serial_number=FIXED_SERIAL)
        self.assertEqual(v.status, GovStackVoucher.STATUS_PREACTIVATED)

    def test_e9_missing_gov_stack_bb_in_payload_returns_400(self):
        """Missing Gov_Stack_BB → 400."""
        _make_voucher(serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_PREACTIVATED)
        body = {"voucherserialnumber": FIXED_SERIAL}
        resp = self._patch(FIXED_SERIAL, body=body)
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("message", resp.data)
        v = GovStackVoucher.objects.get(serial_number=FIXED_SERIAL)
        self.assertEqual(v.status, GovStackVoucher.STATUS_PREACTIVATED)

    def test_e10_empty_payload_returns_400(self):
        """A payload-less PATCH request (request.data resolves to {}) → 400."""
        _make_voucher(serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_PREACTIVATED)
        resp = self.client.patch(_status_url(FIXED_SERIAL))
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("message", resp.data)

    def test_e11_invalid_gov_stack_bb_returns_463(self):
        """
        Invalid Gov_Stack_BB (e.g. "invalid_bb") on cancellation → 463 — NOT
        460 like every other voucher endpoint. This is a real, confirmed
        quirk of this specific endpoint per the live Gherkin scenarios.
        """
        _make_voucher(serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_PREACTIVATED)
        body = {"voucherserialnumber": FIXED_SERIAL, "Gov_Stack_BB": "invalid_bb"}
        resp = self._patch(FIXED_SERIAL, body=body)
        self.assertEqual(resp.status_code, 463, resp.data)
        self.assertIn("message", resp.data)
        v = GovStackVoucher.objects.get(serial_number=FIXED_SERIAL)
        self.assertEqual(
            v.status,
            GovStackVoucher.STATUS_PREACTIVATED,
            "An invalid Gov_Stack_BB must be rejected before cancel() runs.",
        )

    def test_e12_blank_gov_stack_bb_in_payload_returns_400(self):
        """
        An explicitly blank (not missing) Gov_Stack_BB is rejected by the
        serializer itself (CharField, allow_blank=False) → 400, distinct
        from the 463 case above (a non-blank but invalid sentinel).
        """
        _make_voucher(serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_PREACTIVATED)
        body = {"voucherserialnumber": FIXED_SERIAL, "Gov_Stack_BB": ""}
        resp = self._patch(FIXED_SERIAL, body=body)
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_e13_harness_invalid_serial_literal_returns_463_not_400(self):
        """
        Harness-confirmed defect fix: the real harness's "invalid
        voucherserialnumber" negative scenario (voucher_cancelation.feature)
        sends the literal 'invalid_serial_number' (21 characters) — in both
        the URL path segment and the request body — and expects HTTP 463
        (InvalidCancellationSerial — not found) from
        GovStackVoucherService.cancel().

        Before max_length was widened from 20 to 100 on
        VoucherCancellationRequestSerializer.voucherserialnumber, DRF
        rejected the 21-char literal in the body at the serializer level
        with a generic HTTP 400 — before the URL-driven cancel() lookup
        ever ran. A 21-char serial can never match a real voucher row (the
        model field itself is max_length=20), so this always reaches the
        service's InvalidCancellationSerial(463) path regardless of the
        wider serializer cap.
        """
        literal = "invalid_serial_number"
        self.assertEqual(len(literal), 21)
        resp = self._patch(literal)
        self.assertEqual(resp.status_code, 463, resp.data)
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
        PAYEE_ID = "3e4c9a1b-0f42-dead"  # noqa: N806
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
        self.assertEqual(
            GovStackVoucher.objects.filter(
                serial_number__in=[FIXED_SERIAL, FIXED_SERIAL_2]
            ).count(),
            2,
        )


class VoucherServiceActivateTest(TestCase):
    """Service-layer tests for GovStackVoucherService.activate()."""

    def setUp(self):
        self.voucher = _make_voucher(
            serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_PREACTIVATED
        )

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
        _make_voucher(serial=FIXED_SERIAL_2, status=GovStackVoucher.STATUS_CONSUMED)
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
        SECRET_BANK = "TOP_SECRET_ACCOUNT_1234567890"  # noqa: N806
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

    def test_f21b_non_numeric_voucher_number_raises_invalid_voucher_number(self):
        """The harness sends the literal string 'notAnumber' for this scenario."""
        with self.assertRaises(InvalidVoucherNumber):
            GovStackVoucherService.redeem(voucher_number="notAnumber", issuing_bb=BB_ID)

    def test_f21c_insufficient_funds_sentinel_raises_insufficient_funds(self):
        with self.assertRaises(InsufficientFunds):
            GovStackVoucherService.redeem(
                voucher_number=FIXED_SERIAL,
                issuing_bb=BB_ID,
                merchant_voucher_group="insufficient funds",
            )

    def test_f21d_insufficient_funds_does_not_consume_voucher(self):
        """The decline check must happen BEFORE the ACTIVATED → CONSUMED transition."""
        with self.assertRaises(InsufficientFunds):
            GovStackVoucherService.redeem(
                voucher_number=FIXED_SERIAL,
                issuing_bb=BB_ID,
                merchant_voucher_group="insufficient funds",
            )
        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.status, GovStackVoucher.STATUS_ACTIVATED)

    def test_f21e_invalid_bb_sentinel_raises_govstack_bb_not_found(self):
        with self.assertRaises(GovStackBBNotFound):
            GovStackVoucherService.redeem(voucher_number=FIXED_SERIAL, issuing_bb="invalid_bb")

    def test_f21f_ronan_oliver_vigor_bank_raises_insufficient_funds(self):
        """
        Definitive fixture pair for InsufficientFunds (462), confirmed against
        the GovStack reference server's Mockoon config (examples/mock-bb-
        payments/mockoon-paymentsbbvoucher.json), the harness's
        voucher_redemption.js hardcoded When-steps, and test-data.json's
        merchant fixture comments — 3 independent corroborating sources.
        """
        with self.assertRaises(InsufficientFunds):
            GovStackVoucherService.redeem(
                voucher_number=FIXED_SERIAL,
                issuing_bb=BB_ID,
                merchant_name="Ronan Oliver",
                merchant_bank_details="Vigor Bank Group",
                merchant_voucher_group="insufficient funds",
            )

    def test_f21g_annie_krueger_omega_holding_raises_cannot_credit_merchant(self):
        """
        Definitive fixture pair for CannotCreditMerchant (463) — the twin
        scenario to test_f21f above. Previously unreachable from client-side
        Gherkin fixtures alone; now resolved via the GovStack reference
        server's own Mockoon config.
        """
        with self.assertRaises(CannotCreditMerchant):
            GovStackVoucherService.redeem(
                voucher_number=FIXED_SERIAL,
                issuing_bb=BB_ID,
                merchant_name="Annie Krueger",
                merchant_bank_details="Omega Holding Company",
                merchant_voucher_group="insufficient funds",
            )
        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.status, GovStackVoucher.STATUS_ACTIVATED)


class VoucherServiceCancelTest(TestCase):
    """Service-layer tests for GovStackVoucherService.cancel()."""

    def test_f22_cancel_preactivated(self):
        v = _make_voucher(serial=FIXED_SERIAL, status=GovStackVoucher.STATUS_PREACTIVATED)
        result = GovStackVoucherService.cancel(voucher_serial_number=FIXED_SERIAL)
        self.assertEqual(result.status, GovStackVoucher.STATUS_CANCELLED)
        v.refresh_from_db()
        self.assertEqual(v.status, GovStackVoucher.STATUS_CANCELLED)

    def test_f23_cancel_activated(self):
        _make_voucher(serial=FIXED_SERIAL_2, status=GovStackVoucher.STATUS_ACTIVATED)
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

    def test_f29b_consumed_voucher_raises_voucher_already_used(self):
        """
        Derived from REAL voucher.status == CONSUMED, not a hardcoded
        "test serial" literal.
        """
        _make_voucher(serial=FIXED_SERIAL_2, status=GovStackVoucher.STATUS_CONSUMED)
        with self.assertRaises(VoucherAlreadyUsed):
            GovStackVoucherService.get_status(serial_number=FIXED_SERIAL_2)

    def test_f29c_expired_voucher_raises_voucher_expired(self):
        """Derived from a REAL expiry_date comparison against timezone.now()."""
        from datetime import timedelta

        from django.utils import timezone as tz

        v = _make_voucher(serial=FIXED_SERIAL_2, status=GovStackVoucher.STATUS_ACTIVATED)
        v.expiry_date = tz.now() - timedelta(days=1)
        v.save(update_fields=["expiry_date"])

        with self.assertRaises(VoucherExpired):
            GovStackVoucherService.get_status(serial_number=FIXED_SERIAL_2)

    def test_f29d_consumed_and_expired_raises_voucher_already_used_not_expired(self):
        """Precedence: CONSUMED (458) wins over expired (459)."""
        from datetime import timedelta

        from django.utils import timezone as tz

        v = _make_voucher(serial=FIXED_SERIAL_2, status=GovStackVoucher.STATUS_CONSUMED)
        v.expiry_date = tz.now() - timedelta(days=1)
        v.save(update_fields=["expiry_date"])

        with self.assertRaises(VoucherAlreadyUsed):
            GovStackVoucherService.get_status(serial_number=FIXED_SERIAL_2)

    def test_f29e_non_expired_voucher_does_not_raise(self):
        """Sanity check: a future expiry_date must not raise VoucherExpired."""
        v = GovStackVoucherService.get_status(serial_number=FIXED_SERIAL)
        self.assertEqual(v.status, GovStackVoucher.STATUS_ACTIVATED)


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
        PAYEE = "deadbeef-1234-5678"  # noqa: N806
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
        BANK = "SUPER_SECRET_BANK_ACCOUNT_99"  # noqa: N806
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
            self.assertTrue(
                self._voucher(status).is_terminal, f"Expected {status!r} to be terminal"
            )

    def test_h3_is_terminal_false_for_non_terminal_states(self):
        for status in (
            GovStackVoucher.STATUS_PREACTIVATED,
            GovStackVoucher.STATUS_ACTIVATED,
            GovStackVoucher.STATUS_BLOCKED,
            GovStackVoucher.STATUS_SUSPENDED,
        ):
            self.assertFalse(
                self._voucher(status).is_terminal, f"Expected {status!r} to be non-terminal"
            )

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
      3. GET /voucherstatuscheck → voucher_status = "Activated"
      4. POST /voucher_redemption → voucher moves to CONSUMED
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
        serial = resp1.data["voucher_serial_number"]
        self.assertEqual(serial, FIXED_SERIAL)

        # Step 2: Activate
        resp2 = self.client.patch(
            ACTIVATION_URL,
            data=json.dumps(_activation_body(serial=serial)),
            content_type="application/json",
        )
        self.assertEqual(resp2.status_code, 200, resp2.data)
        self.assertTrue(resp2.data["result_status"])

        # Step 3: Status check (ACTIVATED)
        resp3 = self.client.get(_status_url(serial))
        self.assertEqual(resp3.status_code, 200)
        self.assertEqual(resp3.data["voucher_status"], "Activated")

        # Step 4: Redeem
        resp4 = self.client.post(
            REDEMPTION_URL,
            data=json.dumps(_redemption_body(voucher_number=serial)),
            content_type="application/json",
        )
        self.assertEqual(resp4.status_code, 200, resp4.data)
        self.assertTrue(resp4.data["result_status"])
        voucher = GovStackVoucher.objects.get(serial_number=serial)
        self.assertEqual(voucher.status, GovStackVoucher.STATUS_CONSUMED)

        # Step 5: Attempt cancel on consumed voucher → 463
        resp5 = self.client.patch(
            _status_url(serial),
            data=json.dumps(_cancellation_body(serial=serial)),
            content_type="application/json",
        )
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
        serial = resp1.data["voucher_serial_number"]

        # Cancel
        resp2 = self.client.patch(
            _status_url(serial),
            data=json.dumps(_cancellation_body(serial=serial)),
            content_type="application/json",
        )
        self.assertEqual(resp2.status_code, 200)
        # GAP-6: spec requires title-case label, not raw DB value "cancelled".
        self.assertEqual(resp2.data["voucherStatus"], "Cancelled")
        self.assertIn("message", resp2.data)

        # Double-cancel → 464
        resp3 = self.client.patch(
            _status_url(serial),
            data=json.dumps(_cancellation_body(serial=serial)),
            content_type="application/json",
        )
        self.assertEqual(resp3.status_code, 464)


# ---------------------------------------------------------------------------
# F31–F36  seed_govstack_vouchers management command  # noqa: RUF003
# ---------------------------------------------------------------------------


class SeedGovStackVouchersCommandTests(TestCase):
    """
    Tests for the ``seed_govstack_vouchers`` management command.

    These tests validate that the command:
      - creates exactly the 16 GovStack harness vouchers (F31)
      - is idempotent on repeated runs (F32)
      - seeds each row in its expected per-serial status (F33) — most in
        STATUS_PREACTIVATED, but 6001=CONSUMED and 6002/6004=ACTIVATED
      - preserves exact serial number strings without padding or truncation (F34)
      - supports --reset to delete and recreate seed rows (F35)
      - tags all rows with issuing_bb == "GS-HARNESS" (F36)

    The full seed set uses 4-digit serials (5550–6004) and 5-digit serials
    (60000–60001) — values outside the auto-generation range (100 000–999 999)
    and therefore guaranteed not to conflict with production vouchers.
    """  # noqa: RUF002

    # Canonical list of all serial numbers the seed command must create.
    # Order matches _SEED_VOUCHERS in the management command.
    EXPECTED_SERIALS: list[str] = [  # noqa: RUF012
        "5550",
        "5551",
        "5552",
        "5553",
        "5554",
        "5555",
        "5556",
        "5557",
        "5558",
        "5559",
        "5560",
        "6001",
        "6002",
        "6004",
        "60000",
        "60001",
    ]

    # Expected group_code + amount_str + currency + "expiry must be in the
    # future" for spot-check rows (serial → (group_code, amount_str,
    # currency, expiry_in_future)). 6002 is deliberately NOT included here —
    # its expiry_date is intentionally in the PAST (see test_f33 and the
    # dedicated 6001/6002 assertions below), so it doesn't fit this table's
    # "expiry must be in the future" invariant.
    _EXPECTED_DATA: dict[str, tuple[str, str, str, bool]] = {  # noqa: RUF012
        "5550": ("FOOD", "100.00", "CAD", True),
        "5552": ("FOOD", "200.00", "CAD", True),
        "5556": ("HEALTH", "75.00", "CAD", True),
        "6001": ("FOOD", "100.00", "CAD", True),
        "6004": ("HEALTH", "200.00", "CAD", True),
        "60000": ("TRANSPORT", "50.00", "CAD", True),
        "60001": ("TRANSPORT", "50.00", "CAD", True),
    }

    # Per-serial expected status after a plain seed run (F33).
    # All serials default to STATUS_PREACTIVATED except the 3 special cases.
    _EXPECTED_STATUS: dict[str, str] = {
        serial: GovStackVoucher.STATUS_PREACTIVATED
        for serial in [
            "5550",
            "5551",
            "5552",
            "5553",
            "5554",
            "5555",
            "5556",
            "5557",
            "5558",
            "5559",
            "5560",
            "60000",
            "60001",
        ]
    } | {
        "6001": GovStackVoucher.STATUS_CONSUMED,
        "6002": GovStackVoucher.STATUS_ACTIVATED,
        "6004": GovStackVoucher.STATUS_ACTIVATED,
    }

    def _call_seed(self, *, reset: bool = False, verbosity: int = 0) -> str:
        """Invoke the seed command and return captured stdout."""
        out = StringIO()
        call_command(
            "seed_govstack_vouchers",
            reset=reset,
            verbosity=verbosity,
            stdout=out,
        )
        return out.getvalue()

    # ── F31 ──────────────────────────────────────────────────────────────────

    def test_f31_creates_all_16_serials_when_db_is_empty(self):
        """Command creates exactly 16 seed vouchers starting from an empty DB."""
        self.assertEqual(GovStackVoucher.objects.count(), 0)

        self._call_seed()

        qs = GovStackVoucher.objects.all()
        self.assertEqual(qs.count(), len(self.EXPECTED_SERIALS))
        created_serials = set(qs.values_list("serial_number", flat=True))
        self.assertEqual(created_serials, set(self.EXPECTED_SERIALS))

    # ── F32 ──────────────────────────────────────────────────────────────────

    def test_f32_command_is_idempotent(self):
        """Running the command twice creates 0 additional rows, raises no errors."""
        self._call_seed()
        count_after_first = GovStackVoucher.objects.count()
        self.assertEqual(count_after_first, len(self.EXPECTED_SERIALS))

        # Second run must not raise IntegrityError or create duplicates
        self._call_seed()
        count_after_second = GovStackVoucher.objects.count()
        self.assertEqual(count_after_second, count_after_first)

    # ── F33 ──────────────────────────────────────────────────────────────────

    def test_f33_seeded_vouchers_have_expected_per_serial_status(self):
        """
        Each seed voucher has its EXPECTED per-serial status after seeding —
        NOT a blanket STATUS_PREACTIVATED for every row.  13 of the 16 rows
        default to STATUS_PREACTIVATED, but 3 are deliberately special-cased:
          - 6001 → STATUS_CONSUMED   (voucherstatuscheck harness → 458)
          - 6002 → STATUS_ACTIVATED, with a past expiry_date (→ 459)
          - 6004 → STATUS_ACTIVATED  (redemption smoke test needs ACTIVATED,
                    since redeem() only permits ACTIVATED → CONSUMED)
        """
        self._call_seed()

        mismatches = []
        for serial, expected_status in self._EXPECTED_STATUS.items():
            actual_status = GovStackVoucher.objects.get(serial_number=serial).status
            if actual_status != expected_status:
                mismatches.append((serial, expected_status, actual_status))

        self.assertEqual(
            mismatches,
            [],
            msg=(
                "Some seed vouchers have unexpected status "
                "(serial, expected, actual): " + str(mismatches)
            ),
        )

    def test_f33b_serial_6001_is_consumed_with_redemption_metadata_populated(self):
        """
        6001 must be seeded STATUS_CONSUMED with redemption audit-trail
        fields populated (redeemed_at, redeemed_merchant_name) — a CONSUMED
        row with blank redemption metadata looks like a data bug.
        """
        self._call_seed()

        v = GovStackVoucher.objects.get(serial_number="6001")
        self.assertEqual(v.status, GovStackVoucher.STATUS_CONSUMED)
        self.assertIsNotNone(
            v.redeemed_at,
            msg="6001 is seeded CONSUMED — redeemed_at must be populated.",
        )
        self.assertNotEqual(
            v.redeemed_merchant_name,
            "",
            msg="6001 is seeded CONSUMED — redeemed_merchant_name must be populated.",
        )

    def test_f33c_serial_6002_expiry_date_is_genuinely_in_the_past(self):
        """
        6002 must be seeded STATUS_ACTIVATED (not CONSUMED) with a genuinely
        past expiry_date, so the voucherstatuscheck harness's 459
        (VoucherExpired) scenario is reachable — get_status() checks CONSUMED
        (→458) before expiry (→459), so 6002 must not be CONSUMED.
        """
        from django.utils import timezone as tz

        self._call_seed()

        v = GovStackVoucher.objects.get(serial_number="6002")
        self.assertEqual(v.status, GovStackVoucher.STATUS_ACTIVATED)
        self.assertIsNotNone(v.expiry_date)
        self.assertLess(
            v.expiry_date,
            tz.now(),
            msg="6002's expiry_date must be in the past.",
        )

    def test_f33d_status_check_view_returns_458_for_6001_and_459_for_6002(self):
        """
        End-to-end confirmation of the actual harness-facing behavior this
        seed fix exists to enable: GET .../voucherstatuscheck/{serial} must
        return 458 (VoucherAlreadyUsed) for 6001 and 459 (VoucherExpired) for
        6002, exercised through the real view/URL — not just raw DB state.
        """
        self._call_seed()

        client = APIClient()

        resp_6001 = client.get(_status_url("6001"))
        self.assertEqual(resp_6001.status_code, 458, resp_6001.data)

        resp_6002 = client.get(_status_url("6002"))
        self.assertEqual(resp_6002.status_code, 459, resp_6002.data)

    # ── F34 ──────────────────────────────────────────────────────────────────

    def test_f34_serial_numbers_are_exact_strings(self):
        """
        serial_number is the exact string value specified in _SEED_VOUCHERS —
        no zero-padding, no truncation, no integer coercion.

        Spot-checks 4-digit serials (5550, 5560, 6004) and 5-digit serials
        (60000, 60001).  Also verifies that zero-padded variants do NOT exist.
        """
        self._call_seed()

        # Exact 4-digit serials must exist
        for serial in ("5550", "5560", "6004"):
            self.assertTrue(
                GovStackVoucher.objects.filter(serial_number=serial).exists(),
                msg=f"Expected exact serial_number={serial!r} to exist in DB.",
            )
            # Zero-padded form (e.g. "005550") must NOT be created
            padded = serial.zfill(6)
            if padded != serial:
                self.assertFalse(
                    GovStackVoucher.objects.filter(serial_number=padded).exists(),
                    msg=(
                        f"Zero-padded serial_number={padded!r} must NOT exist — "
                        f"seed uses exact value {serial!r}."
                    ),
                )

        # Exact 5-digit serials must exist
        for serial in ("60000", "60001"):
            self.assertTrue(
                GovStackVoucher.objects.filter(serial_number=serial).exists(),
                msg=f"Expected exact serial_number={serial!r} to exist in DB.",
            )
            # 6-digit zero-padded form must NOT exist
            padded = serial.zfill(6)
            if padded != serial:
                self.assertFalse(
                    GovStackVoucher.objects.filter(serial_number=padded).exists(),
                    msg=(
                        f"Zero-padded serial_number={padded!r} must NOT exist — "
                        f"seed uses exact value {serial!r}."
                    ),
                )

    # ── F35 ──────────────────────────────────────────────────────────────────

    def test_f35_reset_flag_deletes_and_recreates_seed_rows(self):
        """
        --reset deletes ALL vouchers whose serial_number is in the seed list
        (regardless of status or issuing_bb) and then recreates them all in
        STATUS_PREACTIVATED with the correct group_code and amount.

        Scenario: simulates a harness run that consumed serial 5550 and
        cancelled serial 60001, then the operator runs --reset to restore
        them before the next harness submission.  Also pre-creates 6004 in
        the OLD (pre-fix) PREACTIVATED state to simulate a database seeded by
        an older version of this command — exactly the repair scenario
        --reset exists to fix (see module docstring, "Idempotency and
        --reset"): a plain re-run would NOT fix this row (get_or_create only
        creates missing rows), so --reset is required.
        """
        from datetime import timedelta

        from django.utils import timezone

        # Pre-create two seed serials in terminal/modified states
        GovStackVoucher.objects.create(
            serial_number="5550",
            amount=Decimal("999.00"),
            currency="USD",  # Wrong currency — will be deleted and replaced by seed value
            group_code="WRONG",  # Wrong group — will be deleted and replaced by seed value
            status=GovStackVoucher.STATUS_CONSUMED,
            issuing_bb="SOME-OTHER-BB",  # Not GS-HARNESS — reset must still delete it
            expiry_date=timezone.now() + timedelta(days=1),
        )
        GovStackVoucher.objects.create(
            serial_number="60001",
            amount=Decimal("1.00"),
            currency="USD",
            group_code="WRONG",
            status=GovStackVoucher.STATUS_CANCELLED,
            issuing_bb="GS-HARNESS",
            expiry_date=timezone.now() + timedelta(days=1),
        )
        # Simulates a pre-fix DB: 6004 sitting at the OLD wrong status.
        GovStackVoucher.objects.create(
            serial_number="6004",
            amount=Decimal("200.00"),
            currency="CAD",
            group_code="HEALTH",
            status=GovStackVoucher.STATUS_PREACTIVATED,
            issuing_bb="GS-HARNESS",
            expiry_date=timezone.now() + timedelta(days=1),
        )
        self.assertEqual(GovStackVoucher.objects.count(), 3)

        # Run seed with --reset
        self._call_seed(reset=True, verbosity=1)

        # All 16 seed rows must now be present
        self.assertEqual(
            GovStackVoucher.objects.count(),
            len(self.EXPECTED_SERIALS),
        )

        # Serial 5550 must be freshly created in PREACTIVATED state with correct data
        v5550 = GovStackVoucher.objects.get(serial_number="5550")
        self.assertEqual(v5550.status, GovStackVoucher.STATUS_PREACTIVATED)
        self.assertEqual(v5550.group_code, "FOOD")
        self.assertEqual(v5550.amount, Decimal("100.00"))
        self.assertEqual(v5550.currency, "CAD")
        self.assertEqual(v5550.issuing_bb, "GS-HARNESS")

        # Serial 60001 must be freshly created in PREACTIVATED state with correct data
        v60001 = GovStackVoucher.objects.get(serial_number="60001")
        self.assertEqual(v60001.status, GovStackVoucher.STATUS_PREACTIVATED)
        self.assertEqual(v60001.group_code, "TRANSPORT")
        self.assertEqual(v60001.amount, Decimal("50.00"))
        self.assertEqual(v60001.currency, "CAD")
        self.assertEqual(v60001.issuing_bb, "GS-HARNESS")

        # Serial 6004 must be REPAIRED to ACTIVATED by --reset (was PREACTIVATED
        # pre-fix) — this is the exact repair scenario --reset exists for.
        v6004 = GovStackVoucher.objects.get(serial_number="6004")
        self.assertEqual(v6004.status, GovStackVoucher.STATUS_ACTIVATED)
        self.assertEqual(v6004.group_code, "HEALTH")
        self.assertEqual(v6004.amount, Decimal("200.00"))
        self.assertEqual(v6004.currency, "CAD")
        self.assertEqual(v6004.issuing_bb, "GS-HARNESS")

        # Serials 6001/6002, previously absent entirely, must also be created
        # fresh by --reset in their special-cased states.
        v6001 = GovStackVoucher.objects.get(serial_number="6001")
        self.assertEqual(v6001.status, GovStackVoucher.STATUS_CONSUMED)

        v6002 = GovStackVoucher.objects.get(serial_number="6002")
        self.assertEqual(v6002.status, GovStackVoucher.STATUS_ACTIVATED)
        self.assertLess(v6002.expiry_date, timezone.now())

    # ── F36 ──────────────────────────────────────────────────────────────────

    def test_f36_issuing_bb_is_gs_harness(self):
        """All 16 seed vouchers have issuing_bb == 'GS-HARNESS'."""
        self._call_seed()

        wrong_bb = GovStackVoucher.objects.filter(
            serial_number__in=self.EXPECTED_SERIALS,
        ).exclude(issuing_bb="GS-HARNESS")

        self.assertEqual(
            wrong_bb.count(),
            0,
            msg=(
                "Some seed vouchers have wrong issuing_bb: "
                + str(list(wrong_bb.values_list("serial_number", "issuing_bb")))
            ),
        )

    # ── Bonus: spot-check group_code, amount, currency per seed spec ─────────

    def test_f36b_seed_data_matches_spec_values(self):
        """
        Spot-checks that group_code, amount, currency, and expiry_date match
        the expected values from the seed spec for a representative sample of
        serials — including the new 6001 special-cased serial.

        expiry_date direction is asserted per-row via the 4th element of
        _EXPECTED_DATA: True means "must be in the future" (the harness needs
        live, unexpired vouchers for most scenarios), which holds for every
        row in this table. 6002 is deliberately excluded from this table
        (see the _EXPECTED_DATA comment) because its expiry is intentionally
        in the past — that is covered by test_f33c instead.
        """
        from django.utils import timezone as tz

        self._call_seed()

        for serial, (
            expected_group,
            expected_amount_str,
            expected_currency,
            expiry_in_future,
        ) in self._EXPECTED_DATA.items():
            v = GovStackVoucher.objects.get(serial_number=serial)
            self.assertEqual(
                v.group_code,
                expected_group,
                msg=f"serial={serial!r}: expected group_code={expected_group!r}",
            )
            self.assertEqual(
                v.amount,
                Decimal(expected_amount_str),
                msg=f"serial={serial!r}: expected amount={expected_amount_str}",
            )
            self.assertEqual(
                v.currency,
                expected_currency,
                msg=f"serial={serial!r}: expected currency={expected_currency!r}",
            )
            self.assertIsNotNone(
                v.expiry_date,
                msg=f"serial={serial!r}: expiry_date must not be None.",
            )
            if expiry_in_future:
                self.assertGreater(
                    v.expiry_date,
                    tz.now(),
                    msg=f"serial={serial!r}: expiry_date must be in the future.",
                )
            else:
                self.assertLess(
                    v.expiry_date,
                    tz.now(),
                    msg=f"serial={serial!r}: expiry_date must be in the past.",
                )

    # ── Security invariant: seed rows carry no PII ────────────────────────────

    def test_f36c_seed_rows_have_no_payee_functional_id(self):
        """
        Seed vouchers must have an empty payee_functional_id.
        They carry no PII and no financial address.
        """
        self._call_seed()

        with_pii = GovStackVoucher.objects.filter(
            serial_number__in=self.EXPECTED_SERIALS,
        ).exclude(payee_functional_id="")

        self.assertEqual(
            with_pii.count(),
            0,
            msg=(
                "Seed vouchers must not carry payee_functional_id: "
                + str(list(with_pii.values_list("serial_number", "payee_functional_id")))
            ),
        )

    # ── GovStackRegisteredBB allowlist rows (AUTH-8 / P2) ─────────────────────

    def test_f36d_seed_creates_registered_bb_allowlist_rows_idempotently(self):
        """
        AUTH-8 / P2: the command seeds every entry in _SEED_REGISTERED_BBS as an
        active GovStackRegisteredBB row, and repeated runs neither duplicate nor
        mutate them. These rows only matter when GOVSTACK_REQUIRE_REGISTERED_BB
        (G2P header) or GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB (voucher
        Gov_Stack_BB body field) is True — i.e. in production only.
        """
        from apps.payments.management.commands.seed_govstack_vouchers import (
            _SEED_REGISTERED_BBS,
        )

        expected_ids = {bb_id for bb_id, _desc in _SEED_REGISTERED_BBS}
        self.assertIn("GS-HARNESS", expected_ids)

        # verbosity=2 exercises the per-row stdout branch of the seeding loop.
        out = self._call_seed(verbosity=2)
        for bb_id in expected_ids:
            self.assertIn(repr(bb_id), out)

        rows = GovStackRegisteredBB.objects.filter(bb_id__in=expected_ids)
        self.assertEqual(set(rows.values_list("bb_id", flat=True)), expected_ids)
        for row in rows:
            self.assertTrue(row.is_active, f"{row.bb_id} must be seeded active.")
            self.assertEqual(row.role, "admin")
            # Every seeded id must satisfy the model's own bb_id validator —
            # this is what rules out the harness's positive Gov_Stack_BB fixture
            # values ("Gov_Stack_BB", "bb-digital-registries"). See the comment
            # block above _SEED_REGISTERED_BBS.
            row.full_clean()

        # Idempotency: a second run must not duplicate rows.
        self._call_seed()
        self.assertEqual(
            GovStackRegisteredBB.objects.filter(bb_id__in=expected_ids).count(),
            len(expected_ids),
        )


# ---------------------------------------------------------------------------
# F37–F40  GOVSTACK_VOUCHER_REQUIRE_JWT enforcement (GAP-3)  # noqa: RUF003
# ---------------------------------------------------------------------------


class VoucherJWTEnforcementTest(TestCase):
    """
    Tests for the GOVSTACK_VOUCHER_REQUIRE_JWT production guard on voucher
    endpoints.

    Affected endpoints (both handled by HasVoucherJWT permission):
      POST  /govstack/payments/vouchers/voucher_redemption   (VoucherRedemptionView)
      GET   /govstack/payments/vouchers/voucherstatuscheck/{serial} (VoucherStatusCheckView)
      PATCH /govstack/payments/vouchers/voucherstatuscheck/{serial} (VoucherStatusCheckView)

    Test matrix (7 rejection + bypass + positive-path cases):
      F37: JWT=True  → POST  voucher_redemption      unauthenticated → 401/403
      F38: JWT=True  → PATCH voucherstatuscheck      unauthenticated → 401/403
      F39: JWT=True  → GET   voucherstatuscheck      unauthenticated → 401/403
      F40: JWT=False → GET   voucherstatuscheck      unauthenticated → 200
      F41: JWT=False → POST  voucher_redemption      unauthenticated → 200
      F42: JWT=True  → POST  voucher_redemption      authenticated   → 200
      F43: JWT=True  → GET   voucherstatuscheck      authenticated   → 200
      F44: JWT=False → PATCH voucherstatuscheck      unauthenticated → 200

    Mode semantics:
      GOVSTACK_VOUCHER_REQUIRE_JWT=True  (production default) →
          unauthenticated requests are rejected (HTTP 401 or 403).
          DRF returns 401 when the request has no successful authenticator
          (NotAuthenticated), which is the standard Django REST Framework
          behaviour for anonymous API clients.

      GOVSTACK_VOUCHER_REQUIRE_JWT=False (harness / test mode) →
          HasVoucherJWT.has_permission() returns True unconditionally,
          so unauthenticated requests succeed.

    Note on 401 vs 403:
      The spec (GAP-3) lists HTTP 403 for F37–F39 as a shorthand for "access
      denied". DRF raises NotAuthenticated (→ 401) when authentication was not
      attempted and PermissionDenied (→ 403) when it was attempted but failed.
      For an anonymous APIClient with DRF's default authentication classes,
      the actual code is 401.  The tests therefore assert
      ``status_code in (401, 403)`` to cover both semantics without
      over-specifying DRF internals.
    """  # noqa: RUF002

    def setUp(self):
        self.client = APIClient()
        # A PREACTIVATED voucher for cancellation / status-check tests.
        self.preactivated = _make_voucher(
            serial=FIXED_SERIAL,
            status=GovStackVoucher.STATUS_PREACTIVATED,
        )
        # An ACTIVATED voucher for redemption tests (redemption requires ACTIVATED).
        self.activated = _make_voucher(
            serial=FIXED_SERIAL_2,
            status=GovStackVoucher.STATUS_ACTIVATED,
        )

    # ── F37 ──────────────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_VOUCHER_REQUIRE_JWT=True)
    def test_f37_require_jwt_true_rejects_unauthenticated_redemption(self):
        """
        GOVSTACK_VOUCHER_REQUIRE_JWT=True: unauthenticated POST to
        voucher_redemption must be rejected.

        DRF returns HTTP 401 (NotAuthenticated) for anonymous callers when
        authentication classes are configured, which is the case here.
        Asserts membership in (401, 403) to be robust to configuration changes.
        """
        resp = self.client.post(
            REDEMPTION_URL,
            data=json.dumps(_redemption_body(voucher_number=FIXED_SERIAL_2)),
            content_type="application/json",
        )
        self.assertIn(
            resp.status_code,
            (401, 403),
            f"Expected 401 or 403 when GOVSTACK_VOUCHER_REQUIRE_JWT=True, "
            f"got {resp.status_code}: {resp.data}",
        )

    # ── F38 ──────────────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_VOUCHER_REQUIRE_JWT=True)
    def test_f38_require_jwt_true_rejects_unauthenticated_cancellation(self):
        """
        GOVSTACK_VOUCHER_REQUIRE_JWT=True: unauthenticated PATCH to
        /voucherstatuscheck/{serial} (cancellation) must be rejected.

        VoucherStatusCheckView.permission_classes = [HasVoucherJWT] ensures
        this setting is honoured for both GET and PATCH on that view.
        """
        resp = self.client.patch(_status_url(FIXED_SERIAL))
        self.assertIn(
            resp.status_code,
            (401, 403),
            f"Expected 401 or 403 when GOVSTACK_VOUCHER_REQUIRE_JWT=True, "
            f"got {resp.status_code}: {resp.data}",
        )
        # Voucher must NOT have been cancelled (request was rejected before service).
        self.preactivated.refresh_from_db()
        self.assertEqual(
            self.preactivated.status,
            GovStackVoucher.STATUS_PREACTIVATED,
            "Unauthenticated PATCH must not mutate the voucher.",
        )

    # ── F39 ──────────────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_VOUCHER_REQUIRE_JWT=True)
    def test_f39_require_jwt_true_rejects_unauthenticated_status_check(self):
        """
        GOVSTACK_VOUCHER_REQUIRE_JWT=True: unauthenticated GET to
        /voucherstatuscheck/{serial} (status inquiry) must be rejected.
        """
        resp = self.client.get(_status_url(FIXED_SERIAL))
        self.assertIn(
            resp.status_code,
            (401, 403),
            f"Expected 401 or 403 when GOVSTACK_VOUCHER_REQUIRE_JWT=True, "
            f"got {resp.status_code}: {resp.data}",
        )

    # ── F40 ──────────────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_VOUCHER_REQUIRE_JWT=False)
    def test_f40_require_jwt_false_allows_unauthenticated_status_check(self):
        """
        GOVSTACK_VOUCHER_REQUIRE_JWT=False (harness mode): unauthenticated GET
        to /voucherstatuscheck/{serial} must succeed (HTTP 200).

        This confirms the harness can call the status-check endpoint without
        a Bearer JWT when the env var is cleared for the harness run.
        """
        resp = self.client.get(_status_url(FIXED_SERIAL))
        self.assertEqual(
            resp.status_code,
            200,
            f"Expected 200 when GOVSTACK_VOUCHER_REQUIRE_JWT=False, "
            f"got {resp.status_code}: {resp.data}",
        )
        # Response shape must be intact — the setting must only gate auth,
        # not corrupt the business logic.
        self.assertIn("voucher_status", resp.data)
        self.assertIn("voucher_amount", resp.data)

    # ── F41 ──────────────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_VOUCHER_REQUIRE_JWT=False)
    def test_f41_require_jwt_false_allows_unauthenticated_redemption(self):
        """
        GOVSTACK_VOUCHER_REQUIRE_JWT=False (harness mode): unauthenticated POST
        to voucher_redemption must succeed (HTTP 200).

        Redemption is the higher-risk endpoint; this explicitly confirms that
        the harness mode bypass applies there too, not just to the status check.
        Complements F40 which only covers the GET status-check path.
        """
        resp = self.client.post(
            REDEMPTION_URL,
            data=json.dumps(_redemption_body(voucher_number=FIXED_SERIAL_2)),
            content_type="application/json",
        )
        self.assertEqual(
            resp.status_code,
            200,
            f"Expected 200 when GOVSTACK_VOUCHER_REQUIRE_JWT=False, "
            f"got {resp.status_code}: {resp.data}",
        )
        # Confirm the voucher was actually consumed (not just a stub 200).
        self.activated.refresh_from_db()
        self.assertEqual(self.activated.status, GovStackVoucher.STATUS_CONSUMED)

    # ── F42 ──────────────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_VOUCHER_REQUIRE_JWT=True)
    def test_f42_require_jwt_true_authenticated_user_can_redeem(self):
        """
        GOVSTACK_VOUCHER_REQUIRE_JWT=True + authenticated user:
        POST to voucher_redemption must succeed (HTTP 200).

        This is the positive production-path test. It verifies that
        HasVoucherJWT.has_permission() returns True for request.user.is_authenticated,
        not just that unauthenticated requests are rejected (F37).

        Without this test, a misconfiguration that removed JWTAuthentication
        from authentication_classes or broke request.user population would
        leave all three rejection tests (F37–F39) green while the endpoint
        would reject every real authenticated user in production.
        """  # noqa: RUF002
        user = get_user_model().objects.create_user(
            email="voucher_jwt_test@example.com",
            password="testpass123",
        )
        self.client.force_authenticate(user=user)
        resp = self.client.post(
            REDEMPTION_URL,
            data=json.dumps(_redemption_body(voucher_number=FIXED_SERIAL_2)),
            content_type="application/json",
        )
        self.assertEqual(
            resp.status_code,
            200,
            f"Expected 200 for authenticated user when GOVSTACK_VOUCHER_REQUIRE_JWT=True, "
            f"got {resp.status_code}: {resp.data}",
        )
        # Confirm service ran: voucher is now CONSUMED.
        self.activated.refresh_from_db()
        self.assertEqual(self.activated.status, GovStackVoucher.STATUS_CONSUMED)

    # ── F43 ──────────────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_VOUCHER_REQUIRE_JWT=True)
    def test_f43_require_jwt_true_authenticated_user_can_check_status(self):
        """
        GOVSTACK_VOUCHER_REQUIRE_JWT=True + authenticated user:
        GET to /voucherstatuscheck/{serial} must succeed (HTTP 200).

        Positive production-path test for VoucherStatusCheckView.
        Complements F39 (unauthenticated GET rejected) to confirm the permission
        gate is correctly binary: reject anonymous, allow authenticated.
        """
        user = get_user_model().objects.create_user(
            email="voucher_status_test@example.com",
            password="testpass123",
        )
        self.client.force_authenticate(user=user)
        resp = self.client.get(_status_url(FIXED_SERIAL))
        self.assertEqual(
            resp.status_code,
            200,
            f"Expected 200 for authenticated user when GOVSTACK_VOUCHER_REQUIRE_JWT=True, "
            f"got {resp.status_code}: {resp.data}",
        )
        self.assertIn("voucher_status", resp.data)
        self.assertIn("voucher_amount", resp.data)

    # ── F44 ──────────────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_VOUCHER_REQUIRE_JWT=False)
    def test_f44_require_jwt_false_allows_unauthenticated_cancellation(self):
        """
        GOVSTACK_VOUCHER_REQUIRE_JWT=False (harness mode): unauthenticated PATCH
        to /voucherstatuscheck/{serial} (cancellation) must succeed (HTTP 200).

        Completes the harness-mode bypass matrix:
          F40: GET  voucherstatuscheck  JWT=False → 200
          F41: POST voucher_redemption  JWT=False → 200
          F44: PATCH voucherstatuscheck JWT=False → 200  ← this test

        Although GET and PATCH share the same permission class on VoucherStatusCheckView,
        an explicit PATCH test is necessary because:
          1. PATCH is the higher-risk operation (it mutates state).
          2. It proves the bypass applies to the mutation path, not just read paths.
          3. It confirms the harness cancel feature works end-to-end without JWT.
        """
        resp = self.client.patch(
            _status_url(FIXED_SERIAL),
            data=json.dumps(_cancellation_body(serial=FIXED_SERIAL)),
            content_type="application/json",
        )
        self.assertEqual(
            resp.status_code,
            200,
            f"Expected 200 when GOVSTACK_VOUCHER_REQUIRE_JWT=False, "
            f"got {resp.status_code}: {getattr(resp, 'data', resp.content)}",
        )
        # Response shape must be intact.
        self.assertIn("voucherSerialNumber", resp.data)
        self.assertIn("voucherStatus", resp.data)
        self.assertIn("message", resp.data)
        # Voucher must actually be cancelled — not just a stub 200.
        self.preactivated.refresh_from_db()
        self.assertEqual(
            self.preactivated.status,
            GovStackVoucher.STATUS_CANCELLED,
            "Voucher must transition to STATUS_CANCELLED after successful unauthenticated PATCH.",
        )


# ---------------------------------------------------------------------------
# G. Gov_Stack_BB production allowlist (P2)
# ---------------------------------------------------------------------------


class VoucherRegisteredBBAllowlistTest(TestCase):
    """
    Tests for the GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB production guard added
    in P2: an OPTIONAL allowlist check of the ``Gov_Stack_BB`` request-body
    field against the GovStackRegisteredBB table, layered on top of the
    always-on sentinel blocklist.

    Affected endpoints (the GET status-check endpoint has no Gov_Stack_BB field
    at all and is therefore absent from this matrix by design):
      POST  /govstack/payments/vouchers/voucher_preactivation  → 460 on reject
      PATCH /govstack/payments/vouchers/voucher_activation     → 460 on reject
      POST  /govstack/payments/vouchers/voucher_redemption     → 460 on reject
      PATCH /govstack/payments/vouchers/voucherstatuscheck/{s} → 463 on reject
        (this endpoint uniquely reuses 463 for BB problems — see
         VoucherStatusCheckView's docstring; that is deliberate, not a bug)

    Test matrix:
      G1–G4   flag OFF (test-settings default) → unregistered BB accepted (200)
              on all 4 endpoints, i.e. the allowlist is a genuine no-op and
              every pre-existing blocklist test keeps its original meaning.
      G5      flag OFF → the blocklist sentinels "not_exist" / "invalid_bb" are
              STILL rejected on all 4 endpoints. This is the regression guard
              proving blocklist enforcement was NOT made conditional on the new
              flag: harness conformance must not depend on a production flag.
      G6–G9   flag ON  → well-formed-but-unregistered BB rejected (460/460/460/463).
      G10     flag ON  → a registered, active BB is accepted (200) on all 4.
      G11     flag ON  → a registered but is_active=False row is rejected.
      G12     flag ON  → the blocklist sentinels are still rejected (both layers
              active simultaneously; the blocklist fires first).

    HONEST SCOPE NOTE — none of the "unregistered → rejected" behaviour below is
    harness-verified, and it must never be presented as such. The live GovStack
    harness has no scenario anywhere that exercises genuine "well-formed but
    unregistered BB" rejection: every upstream negative Gov_Stack_BB scenario
    sends one of two fixed sentinel strings, both of which the unconditional
    blocklist already handles. This is a production-hardening feature only.
    Conversely, the harness's own POSITIVE fixture values ("Gov_Stack_BB" on
    preactivation, "bb-digital-registries" elsewhere) cannot even be stored in
    GovStackRegisteredBB.bb_id today (underscores / 21 chars vs. the field's
    validator and max_length=20), which is exactly why this flag is absent
    (→ False) from every non-production settings module.
    """  # noqa: RUF002

    # Well-formed, validator-compatible, on nobody's blocklist, and with no
    # GovStackRegisteredBB row — the only way to reach the allowlist branch.
    UNREGISTERED_BB = "UNREGISTERED-BB"

    def setUp(self):
        self.client = APIClient()

        # The registered/active BB used by the positive-path tests. BB_ID
        # ("SOCIALWELFARE") is alphanumeric and ≤ 20 chars, so it satisfies
        # _BB_ID_VALIDATOR — unlike the harness's own fixture values.
        self.registered = GovStackRegisteredBB.objects.create(
            bb_id=BB_ID,
            description="Registered test BB for the P2 allowlist tests.",
            is_active=True,
            role="admin",
        )

        # One voucher per mutating endpoint so the tests never interfere.
        self.preactivated = _make_voucher(
            serial=FIXED_SERIAL,
            status=GovStackVoucher.STATUS_PREACTIVATED,
        )
        self.activated = _make_voucher(
            serial=FIXED_SERIAL_2,
            status=GovStackVoucher.STATUS_ACTIVATED,
        )
        self.cancellable = _make_voucher(
            serial=FIXED_SERIAL_3,
            status=GovStackVoucher.STATUS_PREACTIVATED,
        )

    # ── Request helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _json(body: dict) -> str:
        return json.dumps(body)

    def _preactivate(self, bb: str):
        with patch(
            "apps.payments.govstack_services._generate_voucher_serial",
            return_value="590001",
        ):
            return self.client.post(
                PREACTIVATION_URL,
                data=self._json(_preactivation_body(gov_stack_bb=bb)),
                content_type="application/json",
            )

    def _activate(self, bb: str):
        return self.client.patch(
            ACTIVATION_URL,
            data=self._json(_activation_body(serial=FIXED_SERIAL, gov_stack_bb=bb)),
            content_type="application/json",
        )

    def _redeem(self, bb: str):
        return self.client.post(
            REDEMPTION_URL,
            data=self._json(_redemption_body(voucher_number=FIXED_SERIAL_2, gov_stack_bb=bb)),
            content_type="application/json",
        )

    def _cancel(self, bb: str):
        return self.client.patch(
            _status_url(FIXED_SERIAL_3),
            data=self._json(_cancellation_body(serial=FIXED_SERIAL_3, gov_stack_bb=bb)),
            content_type="application/json",
        )

    # ── G1–G4: flag OFF → allowlist is a no-op ───────────────────────────────  # noqa: RUF003

    def test_g1_flag_off_unregistered_bb_accepted_on_preactivation(self):
        """
        Default test settings leave GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB
        unset, so getattr(...) resolves False and the allowlist must not fire —
        even for a Gov_Stack_BB with no GovStackRegisteredBB row at all.
        """
        self.assertFalse(
            GovStackRegisteredBB.objects.filter(bb_id=self.UNREGISTERED_BB).exists(),
            "Precondition: the test BB must genuinely be unregistered.",
        )
        resp = self._preactivate(self.UNREGISTERED_BB)
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_g2_flag_off_unregistered_bb_accepted_on_activation(self):
        resp = self._activate(self.UNREGISTERED_BB)
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_g3_flag_off_unregistered_bb_accepted_on_redemption(self):
        resp = self._redeem(self.UNREGISTERED_BB)
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_g4_flag_off_unregistered_bb_accepted_on_cancellation(self):
        resp = self._cancel(self.UNREGISTERED_BB)
        self.assertEqual(resp.status_code, 200, resp.data)

    # ── G5: flag OFF → blocklist STILL unconditional (regression guard) ──────

    def test_g5_flag_off_blocklist_sentinels_still_rejected(self):
        """
        THE key regression guard for P2: adding the allowlist must not have made
        blocklist enforcement conditional on the new flag. With the flag OFF
        (harness mode) the sentinels must still be rejected exactly as they were
        before P2 — this is the only half of Gov_Stack_BB validation the live
        GovStack harness actually exercises, so it must never depend on a
        production-only setting.
        """
        self.assertEqual(self._preactivate("not_exist").status_code, 460)
        self.assertEqual(self._activate("not_exist").status_code, 460)
        self.assertEqual(self._redeem("invalid_bb").status_code, 460)
        # Cancellation uniquely uses 463, not 460, for a bad Gov_Stack_BB.
        self.assertEqual(self._cancel("invalid_bb").status_code, 463)

    # ── G6–G9: flag ON → unregistered rejected ───────────────────────────────  # noqa: RUF003

    @override_settings(GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB=True)
    def test_g6_flag_on_unregistered_bb_rejected_on_preactivation(self):
        resp = self._preactivate(self.UNREGISTERED_BB)
        self.assertEqual(resp.status_code, 460, resp.data)
        self.assertIn("message", resp.data)

    @override_settings(GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB=True)
    def test_g7_flag_on_unregistered_bb_rejected_on_activation(self):
        resp = self._activate(self.UNREGISTERED_BB)
        self.assertEqual(resp.status_code, 460, resp.data)
        # Rejection must happen BEFORE any state change.
        self.preactivated.refresh_from_db()
        self.assertEqual(self.preactivated.status, GovStackVoucher.STATUS_PREACTIVATED)

    @override_settings(GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB=True)
    def test_g8_flag_on_unregistered_bb_rejected_on_redemption(self):
        resp = self._redeem(self.UNREGISTERED_BB)
        self.assertEqual(resp.status_code, 460, resp.data)
        self.activated.refresh_from_db()
        self.assertEqual(self.activated.status, GovStackVoucher.STATUS_ACTIVATED)

    @override_settings(GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB=True)
    def test_g9_flag_on_unregistered_bb_rejected_on_cancellation(self):
        """Cancellation reuses 463 for BB problems — must apply to the allowlist too."""
        resp = self._cancel(self.UNREGISTERED_BB)
        self.assertEqual(resp.status_code, 463, resp.data)
        self.cancellable.refresh_from_db()
        self.assertEqual(self.cancellable.status, GovStackVoucher.STATUS_PREACTIVATED)

    # ── G10: flag ON → registered BB accepted ────────────────────────────────

    @override_settings(GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB=True)
    def test_g10_flag_on_registered_bb_accepted_on_all_four_endpoints(self):
        """
        Positive production-path test: an active GovStackRegisteredBB row makes
        the allowlist transparent, so all 4 endpoints behave exactly as they do
        with the flag off. Complements G6–G9 by proving the gate is binary
        rather than a blanket rejection.
        """  # noqa: RUF002
        self.assertEqual(self._preactivate(BB_ID).status_code, 200)
        self.assertEqual(self._activate(BB_ID).status_code, 200)
        self.assertEqual(self._redeem(BB_ID).status_code, 200)
        self.assertEqual(self._cancel(BB_ID).status_code, 200)

    # ── G11: flag ON → inactive row rejected ─────────────────────────────────

    @override_settings(GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB=True)
    def test_g11_flag_on_inactive_registered_bb_rejected(self):
        """
        is_active=False must suspend a BB's voucher access without deleting the
        row, matching IsTrustedSourceBB's treatment of the same column for the
        G2P header path.
        """
        self.registered.is_active = False
        self.registered.save(update_fields=["is_active"])

        self.assertEqual(self._preactivate(BB_ID).status_code, 460)
        self.assertEqual(self._activate(BB_ID).status_code, 460)
        self.assertEqual(self._redeem(BB_ID).status_code, 460)
        self.assertEqual(self._cancel(BB_ID).status_code, 463)

    # ── G12: flag ON → blocklist still fires ─────────────────────────────────

    @override_settings(GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB=True)
    def test_g12_flag_on_blocklist_sentinels_still_rejected(self):
        """Both layers active at once; the blocklist runs first, same codes."""
        self.assertEqual(self._preactivate("not_exist").status_code, 460)
        self.assertEqual(self._activate("not_exist").status_code, 460)
        self.assertEqual(self._redeem("invalid_bb").status_code, 460)
        self.assertEqual(self._cancel("invalid_bb").status_code, 463)


# ---------------------------------------------------------------------------
# G(b). Gov_Stack_BB allowlist — service-layer helper unit tests (P2)
# ---------------------------------------------------------------------------


class IsUnregisteredGovStackBBHelperTest(TestCase):
    """
    Direct unit tests for govstack_services._is_unregistered_gov_stack_bb().

    Polarity mirrors the blocklist helper: True means "reject this BB".
    """

    def setUp(self):
        GovStackRegisteredBB.objects.create(
            bb_id=BB_ID,
            is_active=True,
            role="admin",
        )

    def test_g13_returns_false_for_everything_when_flag_off(self):
        """Flag off ⇒ pure no-op, regardless of what the registry contains."""
        for value in (BB_ID, "UNREGISTERED-BB", "", "   ", None):
            with self.subTest(value=value):
                self.assertFalse(_is_unregistered_gov_stack_bb(value))

    @override_settings(GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB=True)
    def test_g14_flag_on_registered_active_bb_passes(self):
        self.assertFalse(_is_unregistered_gov_stack_bb(BB_ID))
        # Surrounding whitespace is stripped before the lookup.
        self.assertFalse(_is_unregistered_gov_stack_bb(f"  {BB_ID}  "))

    @override_settings(GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB=True)
    def test_g15_flag_on_unknown_blank_and_inactive_bbs_are_rejected(self):
        self.assertTrue(_is_unregistered_gov_stack_bb("UNREGISTERED-BB"))
        self.assertTrue(_is_unregistered_gov_stack_bb(""))
        self.assertTrue(_is_unregistered_gov_stack_bb("   "))
        self.assertTrue(_is_unregistered_gov_stack_bb(None))

        GovStackRegisteredBB.objects.filter(bb_id=BB_ID).update(is_active=False)
        self.assertTrue(_is_unregistered_gov_stack_bb(BB_ID))

    @override_settings(GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB=True)
    def test_g16_flag_on_lookup_is_case_sensitive(self):
        """
        bb_id is documented as case-sensitive on the model. Unlike the
        blocklist (which lower-cases defensively), the allowlist must NOT
        silently widen a registered id to its case variants.
        """
        self.assertTrue(_is_unregistered_gov_stack_bb(BB_ID.lower()))

    @override_settings(GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB=True)
    def test_g17_harness_positive_fixture_values_are_not_registrable(self):
        """
        Documents the P2 limitation discovered while implementing it, so a
        future pass cannot quietly "fix" the seed data and think it worked:
        neither of the harness's positive Gov_Stack_BB fixture values can be
        stored in GovStackRegisteredBB.bb_id as that field is defined today.

        Consequence: GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB must stay False in
        any environment pointed at the GovStack harness. It is absent (→ False)
        outside config/settings/production.py, so this holds by default.
        """
        from django.core.exceptions import ValidationError

        # Underscores are rejected by _BB_ID_VALIDATOR.
        with self.assertRaises(ValidationError):
            GovStackRegisteredBB(bb_id="Gov_Stack_BB").full_clean()

        # 21 chars — exceeds both max_length=20 and the validator's {1,20}.
        self.assertEqual(len("bb-digital-registries"), 21)
        with self.assertRaises(ValidationError):
            GovStackRegisteredBB(bb_id="bb-digital-registries").full_clean()

        # ...and therefore both are rejected by the allowlist at runtime.
        self.assertTrue(_is_unregistered_gov_stack_bb("Gov_Stack_BB"))
        self.assertTrue(_is_unregistered_gov_stack_bb("bb-digital-registries"))
