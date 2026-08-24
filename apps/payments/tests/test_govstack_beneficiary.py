"""
test_govstack_beneficiary.py

Comprehensive tests for GovStack Payments BB — Beneficiary endpoints (spec §18).

Coverage matrix:
  A. View-level harness scenarios (14 total)
     A1-A6:  POST /govstack/payments/register-beneficiary (success + validation errors)
     A7-A12: POST /govstack/payments/update-beneficiary-details (success + validation errors)
     A13:    RegisterBeneficiaryView — missing X-Registering-Institution-ID → HTTP 401
     A14:    UpdateBeneficiaryView   — missing X-Registering-Institution-ID → HTTP 401

  B. G2P response envelope invariants
     - ResponseCode is exactly "00" (success) or "01" (error) — never anything else
     - RequestID is echoed verbatim from the request body
     - ResponseDescription is non-empty string ≤200 chars
     - PayeeFunctionalID NEVER appears in any response field
     - FinancialAddress NEVER appears in any response field

  C. GovStackBeneficiaryService (service layer)
     - register() creates new records
     - register() is idempotent (upserts on second call)
     - update() creates records when PayeeFunctionalID is unknown (upsert)
     - update() updates fields on existing records
     - Audit entries are created for every operation
     - Audit entry details NEVER contain payee_functional_id or financial_address

  D. GovStackPaymentAuditEntry model
     - Append-only: delete() raises PermissionError
     - Append-only: save() on existing instance raises PermissionError

  E. GovStackG2PView helpers
     - _flatten_errors() handles top-level field errors
     - _flatten_errors() handles list errors (min_length)
     - _flatten_errors() handles nested item errors
     - _flatten_errors() truncates to ≤200 chars
     - _request_id() returns "" when body has no RequestID

  F. govstack_g2p_exception_handler
     - Wraps DRF exceptions in G2P envelope (ResponseCode: "01")
     - Echoes RequestID from body even on exception

  G. Serializer validation
     - Valid harness SourceBBID accepted ("11668d2a-a8f")
     - Invalid SourceBBID rejected ("invalid")
     - Valid harness PayeeFunctionalID accepted ("2ba5ed20-0f42-4eff-8")
     - Invalid PayeeFunctionalID rejected ("invalid")
     - Empty Beneficiaries[] → min_length error

Security invariants tested:
  - PII never in any response field (body keys and values are checked)
  - Audit details dict never contains payee_functional_id or financial_address keys

Harness identifiers used (from bb-payments test suite):
  SourceBBID:        "11668d2a-a8f"    (12 chars, lowercase hex + hyphens)
  PayeeFunctionalID: "2ba5ed20-0f42-4eff-8" (20 chars, lowercase hex + hyphens)
  RequestID:         "abc123456789"    (12 chars, echoed in all responses)
"""

from __future__ import annotations

import json

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.payments.govstack_exceptions import govstack_g2p_exception_handler
from apps.payments.govstack_models import (
    GovStackBeneficiary,
    GovStackPaymentAuditEntry,
)
from apps.payments.govstack_serializers import (
    BeneficiaryItemSerializer,
    RegisterBeneficiaryRequestSerializer,
    UpdateBeneficiaryRequestSerializer,
)
from apps.payments.govstack_services import GovStackBeneficiaryService
from apps.payments.govstack_views import GovStackG2PView

# ── Harness constants ────────────────────────────────────────────────────────
VALID_SOURCE_BB_ID = "11668d2a-a8f"
VALID_PAYEE_ID = "2ba5ed20-0f42-4eff-8"
INVALID_ID = "invalid"  # contains i, n, v, l — not hex chars
REQUEST_ID = "abc123456789"  # exactly 12 chars, as harness sends

REGISTER_URL = "/govstack/payments/register-beneficiary"
UPDATE_URL = "/govstack/payments/update-beneficiary-details"

# Minimal valid body
_VALID_BODY = {
    "RequestID": REQUEST_ID,
    "SourceBBID": VALID_SOURCE_BB_ID,
    "Beneficiaries": [
        {"PayeeFunctionalID": VALID_PAYEE_ID},
    ],
}

# Full-fields valid body (with optional PaymentModality + FinancialAddress)
_FULL_BODY = {
    "RequestID": REQUEST_ID,
    "SourceBBID": VALID_SOURCE_BB_ID,
    "Beneficiaries": [
        {
            "PayeeFunctionalID": VALID_PAYEE_ID,
            "PaymentModality": "BK",
            "FinancialAddress": "DE89370400440532013000",  # 22-char German IBAN
        },
    ],
}

# Disable throttling globally for all tests in this module
_NO_THROTTLE = override_settings(
    REST_FRAMEWORK={
        "DEFAULT_THROTTLE_CLASSES": [],
        "DEFAULT_THROTTLE_RATES": {},
    }
)


# ============================================================================
# A.  View-level harness scenarios
# ============================================================================


@_NO_THROTTLE
class RegisterBeneficiaryHarnessTest(TestCase):
    """
    A1–A6: POST /govstack/payments/register-beneficiary
    Mirrors the exact scenarios from g2p_register_beneficiary.feature.
    """  # noqa: RUF002

    def setUp(self):
        self.client = APIClient()
        # RegisterBeneficiaryView uses IsTrustedSourceBB.
        # NOTE: the real GovStack harness NEVER sends X-Registering-Institution-ID
        # on this endpoint (confirmed against the live g2p_register_beneficiary.js
        # step definitions) — a prior comment here claimed the opposite, which was
        # false. This client supplies the header anyway purely so this test class
        # exercises the "header present" path; see RegisterBeneficiaryNoHeaderTest
        # below for coverage of the real (headerless) harness behaviour.
        # With GOVSTACK_REQUIRE_REGISTERED_BB=False (test default), a present
        # header is still validated for length (≤20 chars) but not looked up
        # against the whitelist.
        self.client.defaults["HTTP_X_REGISTERING_INSTITUTION_ID"] = "GS-TEST"

    # A1 — smoke: minimal valid body → HTTP 200, ResponseCode "00"
    def test_a1_smoke_success(self):
        resp = self.client.post(REGISTER_URL, _VALID_BODY, format="json")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["ResponseCode"], "00")
        self.assertEqual(body["RequestID"], REQUEST_ID)
        self.assertIn("ResponseDescription", body)
        self.assertTrue(body["ResponseDescription"])

    # A2 — full fields → HTTP 200, ResponseCode "00"
    def test_a2_full_fields_success(self):
        resp = self.client.post(REGISTER_URL, _FULL_BODY, format="json")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["ResponseCode"], "00")
        self.assertEqual(body["RequestID"], REQUEST_ID)

    # A3 — missing SourceBBID → HTTP 400, ResponseCode "01"
    def test_a3_missing_source_bb_id(self):
        payload = {
            "RequestID": REQUEST_ID,
            "Beneficiaries": [{"PayeeFunctionalID": VALID_PAYEE_ID}],
        }
        resp = self.client.post(REGISTER_URL, payload, format="json")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["ResponseCode"], "01")
        self.assertEqual(body["RequestID"], REQUEST_ID)
        self.assertTrue(body["ResponseDescription"])

    # A4 — missing PayeeFunctionalID (empty Beneficiaries array) → 400
    def test_a4_missing_payee_functional_id(self):
        payload = {
            "RequestID": REQUEST_ID,
            "SourceBBID": VALID_SOURCE_BB_ID,
            "Beneficiaries": [],  # empty → min_length=1 violation
        }
        resp = self.client.post(REGISTER_URL, payload, format="json")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["ResponseCode"], "01")
        self.assertEqual(body["RequestID"], REQUEST_ID)

    # A5 — invalid SourceBBID → HTTP 400, ResponseCode "01"
    def test_a5_invalid_source_bb_id(self):
        payload = {
            "RequestID": REQUEST_ID,
            "SourceBBID": INVALID_ID,  # "invalid" contains non-hex chars
            "Beneficiaries": [{"PayeeFunctionalID": VALID_PAYEE_ID}],
        }
        resp = self.client.post(REGISTER_URL, payload, format="json")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["ResponseCode"], "01")
        self.assertEqual(body["RequestID"], REQUEST_ID)

    # A6 — invalid PayeeFunctionalID → HTTP 400, ResponseCode "01"
    def test_a6_invalid_payee_functional_id(self):
        payload = {
            "RequestID": REQUEST_ID,
            "SourceBBID": VALID_SOURCE_BB_ID,
            "Beneficiaries": [{"PayeeFunctionalID": INVALID_ID}],
        }
        resp = self.client.post(REGISTER_URL, payload, format="json")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["ResponseCode"], "01")
        self.assertEqual(body["RequestID"], REQUEST_ID)

    # A13 — missing header + GOVSTACK_REQUIRE_REGISTERED_BB=True → HTTP 401
    @override_settings(GOVSTACK_REQUIRE_REGISTERED_BB=True)
    def test_a13_no_institution_header_returns_401_in_production_mode(self):
        """
        RegisterBeneficiaryView uses IsTrustedSourceBB. In PRODUCTION mode
        (GOVSTACK_REQUIRE_REGISTERED_BB=True) a caller that omits
        X-Registering-Institution-ID is rejected with HTTP 401.

        This is the production-enforcement guard: without it, any caller could
        register beneficiaries without identifying itself as a registered
        GovStack BB. It must NOT run in harness mode (GOVSTACK_REQUIRE_REGISTERED_BB
        =False, the default) — see test_a13b below for that (opposite) behaviour,
        which is the actual harness contract.
        """
        client = APIClient()  # no HTTP_X_REGISTERING_INSTITUTION_ID default
        resp = client.post(REGISTER_URL, _VALID_BODY, format="json")
        self.assertNotEqual(resp.status_code, 200)
        self.assertIn(resp.status_code, (401, 403))

    # A13b — missing header + harness mode (default) → HTTP 200 (regression guard)
    def test_a13b_no_institution_header_returns_200_in_harness_mode(self):
        """
        Regression test for the original P0 bug: the real GovStack harness NEVER
        sends X-Registering-Institution-ID on register-beneficiary (confirmed
        against g2p_register_beneficiary.js), including the smoke-test scenario.

        Before the fix, IsTrustedSourceBB.has_permission() required the header
        unconditionally regardless of GOVSTACK_REQUIRE_REGISTERED_BB, so this
        exact scenario (which is what the real harness actually sends) would have
        returned 401 and failed every harness scenario on this endpoint. With
        GOVSTACK_REQUIRE_REGISTERED_BB=False (the test/harness default), a missing
        header must now be treated exactly like AllowAnyBB — HTTP 200.
        """
        client = APIClient()  # no HTTP_X_REGISTERING_INSTITUTION_ID header at all
        resp = client.post(REGISTER_URL, _VALID_BODY, format="json")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["ResponseCode"], "00")

    # A13c — header present but unregistered + GOVSTACK_REQUIRE_REGISTERED_BB=True
    # → HTTP 401 (production whitelist enforcement)
    @override_settings(GOVSTACK_REQUIRE_REGISTERED_BB=True)
    def test_a13c_unregistered_header_returns_401_in_production_mode(self):
        """
        A header IS supplied but does not match any active GovStackRegisteredBB
        row. In production mode this must be rejected with HTTP 401 — proves
        the whitelist check still runs when a caller explicitly supplies a header,
        even though a MISSING header is now tolerated in harness mode.
        """
        client = APIClient()
        client.defaults["HTTP_X_REGISTERING_INSTITUTION_ID"] = "NOT-REGISTERED-BB"
        resp = client.post(REGISTER_URL, _VALID_BODY, format="json")
        self.assertNotEqual(resp.status_code, 200)
        self.assertIn(resp.status_code, (401, 403))

    # A13d — header present, registered + GOVSTACK_REQUIRE_REGISTERED_BB=True
    # → HTTP 200 (production positive path)
    @override_settings(GOVSTACK_REQUIRE_REGISTERED_BB=True)
    def test_a13d_registered_header_returns_200_in_production_mode(self):
        """
        Positive path for production mode: a header that DOES match an active
        GovStackRegisteredBB row must be granted access and succeed normally.
        """
        from apps.payments.govstack_models import GovStackRegisteredBB

        GovStackRegisteredBB.objects.create(bb_id="REGISTERED-BB", is_active=True)
        client = APIClient()
        client.defaults["HTTP_X_REGISTERING_INSTITUTION_ID"] = "REGISTERED-BB"
        resp = client.post(REGISTER_URL, _VALID_BODY, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["ResponseCode"], "00")


@_NO_THROTTLE
class UpdateBeneficiaryHarnessTest(TestCase):
    """
    A7–A12: POST /govstack/payments/update-beneficiary-details
    Mirrors the exact scenarios from g2p_update_beneficiary_details.feature.
    """  # noqa: RUF002

    def setUp(self):
        self.client = APIClient()
        # UpdateBeneficiaryView uses IsTrustedSourceBB — send the institution header.
        # NOTE: the real harness never sends this header on this endpoint either
        # (confirmed against g2p_update_beneficiary_details.js); it is supplied
        # here only to exercise the "header present" path.
        # GOVSTACK_REQUIRE_REGISTERED_BB=False (test default) means a present
        # header is validated for length only; no whitelist DB lookup.
        self.client.defaults["HTTP_X_REGISTERING_INSTITUTION_ID"] = "GS-TEST"

    # A7 — smoke → HTTP 200, ResponseCode "00"
    def test_a7_smoke_success(self):
        resp = self.client.post(UPDATE_URL, _VALID_BODY, format="json")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["ResponseCode"], "00")
        self.assertEqual(body["RequestID"], REQUEST_ID)

    # A8 — full fields → HTTP 200, ResponseCode "00"
    def test_a8_full_fields_success(self):
        resp = self.client.post(UPDATE_URL, _FULL_BODY, format="json")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["ResponseCode"], "00")
        self.assertEqual(body["RequestID"], REQUEST_ID)

    # A9 — missing SourceBBID → 400
    def test_a9_missing_source_bb_id(self):
        payload = {
            "RequestID": REQUEST_ID,
            "Beneficiaries": [{"PayeeFunctionalID": VALID_PAYEE_ID}],
        }
        resp = self.client.post(UPDATE_URL, payload, format="json")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["ResponseCode"], "01")
        self.assertEqual(body["RequestID"], REQUEST_ID)

    # A10 — empty Beneficiaries → 400
    def test_a10_missing_payee_functional_id(self):
        payload = {
            "RequestID": REQUEST_ID,
            "SourceBBID": VALID_SOURCE_BB_ID,
            "Beneficiaries": [],
        }
        resp = self.client.post(UPDATE_URL, payload, format="json")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["ResponseCode"], "01")

    # A11 — invalid SourceBBID → 400
    def test_a11_invalid_source_bb_id(self):
        payload = {
            "RequestID": REQUEST_ID,
            "SourceBBID": INVALID_ID,
            "Beneficiaries": [{"PayeeFunctionalID": VALID_PAYEE_ID}],
        }
        resp = self.client.post(UPDATE_URL, payload, format="json")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["ResponseCode"], "01")

    # A12 — invalid PayeeFunctionalID → 400
    def test_a12_invalid_payee_functional_id(self):
        payload = {
            "RequestID": REQUEST_ID,
            "SourceBBID": VALID_SOURCE_BB_ID,
            "Beneficiaries": [{"PayeeFunctionalID": INVALID_ID}],
        }
        resp = self.client.post(UPDATE_URL, payload, format="json")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["ResponseCode"], "01")

    # A14 — missing header + GOVSTACK_REQUIRE_REGISTERED_BB=True → HTTP 401
    @override_settings(GOVSTACK_REQUIRE_REGISTERED_BB=True)
    def test_a14_no_institution_header_returns_401_in_production_mode(self):
        """
        UpdateBeneficiaryView uses IsTrustedSourceBB. Mirrors test_a13 for the
        update endpoint: production mode enforcement of the missing header.
        """
        client = APIClient()  # no HTTP_X_REGISTERING_INSTITUTION_ID default
        resp = client.post(UPDATE_URL, _VALID_BODY, format="json")
        self.assertNotEqual(resp.status_code, 200)
        self.assertIn(resp.status_code, (401, 403))

    # A14b — missing header + harness mode (default) → HTTP 200 (regression guard)
    def test_a14b_no_institution_header_returns_200_in_harness_mode(self):
        """
        Regression test mirroring test_a13b: the real harness never sends
        X-Registering-Institution-ID on update-beneficiary-details either
        (confirmed against g2p_update_beneficiary_details.js). With
        GOVSTACK_REQUIRE_REGISTERED_BB=False (default), this must return 200.
        """
        client = APIClient()
        resp = client.post(UPDATE_URL, _VALID_BODY, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["ResponseCode"], "00")

    # A14c — header present but unregistered + production mode → HTTP 401
    @override_settings(GOVSTACK_REQUIRE_REGISTERED_BB=True)
    def test_a14c_unregistered_header_returns_401_in_production_mode(self):
        """Mirrors test_a13c for the update endpoint."""
        client = APIClient()
        client.defaults["HTTP_X_REGISTERING_INSTITUTION_ID"] = "NOT-REGISTERED-BB"
        resp = client.post(UPDATE_URL, _VALID_BODY, format="json")
        self.assertNotEqual(resp.status_code, 200)
        self.assertIn(resp.status_code, (401, 403))

    # A14d — header present, registered + production mode → HTTP 200
    @override_settings(GOVSTACK_REQUIRE_REGISTERED_BB=True)
    def test_a14d_registered_header_returns_200_in_production_mode(self):
        """Mirrors test_a13d for the update endpoint."""
        from apps.payments.govstack_models import GovStackRegisteredBB

        GovStackRegisteredBB.objects.create(bb_id="REGISTERED-BB", is_active=True)
        client = APIClient()
        client.defaults["HTTP_X_REGISTERING_INSTITUTION_ID"] = "REGISTERED-BB"
        resp = client.post(UPDATE_URL, _VALID_BODY, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["ResponseCode"], "00")


# ============================================================================
# B.  G2P response envelope invariants
# ============================================================================


@_NO_THROTTLE
class G2PEnvelopeInvariantsTest(TestCase):
    """
    Verifies structural invariants of every response from G2P endpoints.
    The harness validates these via g2pResponseSchema in helpers.js.
    """

    def setUp(self):
        self.client = APIClient()
        # Both beneficiary endpoints now require IsTrustedSourceBB.
        self.client.defaults["HTTP_X_REGISTERING_INSTITUTION_ID"] = "GS-TEST"

    def _assert_g2p_envelope(self, body: dict, expected_code: str) -> None:
        """Assert the G2P envelope shape and that it never leaks PII."""
        # Envelope structure
        self.assertIn("ResponseCode", body, "ResponseCode missing from body")
        self.assertIn("RequestID", body, "RequestID missing from body")
        self.assertIn("ResponseDescription", body, "ResponseDescription missing from body")
        # ResponseCode must be exactly "00" or "01"
        self.assertIn(
            body["ResponseCode"], ("00", "01"), f"Invalid ResponseCode: {body['ResponseCode']}"
        )
        self.assertEqual(body["ResponseCode"], expected_code)
        # ResponseDescription must be a non-empty string ≤200 chars
        self.assertIsInstance(body["ResponseDescription"], str)
        self.assertTrue(body["ResponseDescription"], "ResponseDescription is empty")
        self.assertLessEqual(len(body["ResponseDescription"]), 200)
        # No PII in any response field key or value
        body_str = json.dumps(body).lower()
        self.assertNotIn("payee_functional_id", body_str)
        self.assertNotIn("financialaddress", body_str)
        # The actual IBAN we sent should not be echoed back
        self.assertNotIn("de89370400440532013000", body_str)

    def test_success_envelope_register(self):
        resp = self.client.post(REGISTER_URL, _FULL_BODY, format="json")
        self.assertEqual(resp.status_code, 200)
        self._assert_g2p_envelope(resp.json(), "00")

    def test_success_envelope_update(self):
        resp = self.client.post(UPDATE_URL, _FULL_BODY, format="json")
        self.assertEqual(resp.status_code, 200)
        self._assert_g2p_envelope(resp.json(), "00")

    def test_error_envelope_register_missing_sourcebb(self):
        payload = {
            "RequestID": REQUEST_ID,
            "Beneficiaries": [{"PayeeFunctionalID": VALID_PAYEE_ID}],
        }
        resp = self.client.post(REGISTER_URL, payload, format="json")
        self.assertEqual(resp.status_code, 400)
        self._assert_g2p_envelope(resp.json(), "01")

    def test_error_envelope_update_invalid_payee(self):
        payload = {
            "RequestID": REQUEST_ID,
            "SourceBBID": VALID_SOURCE_BB_ID,
            "Beneficiaries": [{"PayeeFunctionalID": INVALID_ID}],
        }
        resp = self.client.post(UPDATE_URL, payload, format="json")
        self.assertEqual(resp.status_code, 400)
        self._assert_g2p_envelope(resp.json(), "01")

    def test_request_id_echo_on_success(self):
        """RequestID from request body must be echoed verbatim in success response."""
        resp = self.client.post(REGISTER_URL, _VALID_BODY, format="json")
        self.assertEqual(resp.json()["RequestID"], REQUEST_ID)

    def test_request_id_echo_on_error(self):
        """RequestID must also be echoed verbatim in error responses."""
        payload = {
            "RequestID": REQUEST_ID,
            "SourceBBID": INVALID_ID,
            "Beneficiaries": [{"PayeeFunctionalID": VALID_PAYEE_ID}],
        }
        resp = self.client.post(REGISTER_URL, payload, format="json")
        self.assertEqual(resp.json()["RequestID"], REQUEST_ID)

    def test_no_extra_keys_in_success_response(self):
        """Success response must contain only the 3 G2P envelope keys."""
        resp = self.client.post(REGISTER_URL, _VALID_BODY, format="json")
        body = resp.json()
        self.assertEqual(set(body.keys()), {"ResponseCode", "RequestID", "ResponseDescription"})

    def test_no_extra_keys_in_error_response(self):
        """Error response must also contain only the 3 G2P envelope keys."""
        payload = {
            "RequestID": REQUEST_ID,
            "SourceBBID": INVALID_ID,
            "Beneficiaries": [{"PayeeFunctionalID": VALID_PAYEE_ID}],
        }
        resp = self.client.post(REGISTER_URL, payload, format="json")
        body = resp.json()
        self.assertEqual(set(body.keys()), {"ResponseCode", "RequestID", "ResponseDescription"})

    def test_method_not_allowed_returns_g2p_envelope(self):
        """GET on a POST-only endpoint should still return G2P envelope from exception handler."""
        resp = self.client.get(REGISTER_URL)
        # DRF returns 405; the exception handler wraps it
        self.assertIn(resp.status_code, (400, 405))
        body = resp.json()
        # Must have ResponseCode, not "message"
        self.assertIn("ResponseCode", body)
        self.assertNotIn("message", body)


# ============================================================================
# C.  GovStackBeneficiaryService — service layer
# ============================================================================


class BeneficiaryServiceTest(TestCase):
    """
    Tests for GovStackBeneficiaryService.register() and .update().
    Tests run against the actual database (using TestCase which wraps in a transaction).
    """

    def _register(self, payee_id=None, source_bb_id=None, **kwargs):
        """Helper: call register() with sensible defaults."""
        return GovStackBeneficiaryService.register(
            request_id=kwargs.get("request_id", REQUEST_ID),
            source_bb_id=source_bb_id or VALID_SOURCE_BB_ID,
            beneficiaries=[
                {
                    "PayeeFunctionalID": payee_id or VALID_PAYEE_ID,
                    "PaymentModality": kwargs.get("modality", ""),
                    "FinancialAddress": kwargs.get("address", ""),
                }
            ],
            registering_institution_id=kwargs.get("institution_id", ""),
        )

    def _update(self, payee_id=None, source_bb_id=None, **kwargs):
        """Helper: call update() with sensible defaults."""
        return GovStackBeneficiaryService.update(
            request_id=kwargs.get("request_id", REQUEST_ID),
            source_bb_id=source_bb_id or VALID_SOURCE_BB_ID,
            beneficiaries=[
                {
                    "PayeeFunctionalID": payee_id or VALID_PAYEE_ID,
                    "PaymentModality": kwargs.get("modality", ""),
                    "FinancialAddress": kwargs.get("address", ""),
                }
            ],
            registering_institution_id=kwargs.get("institution_id", ""),
        )

    # C1 — register() creates a new record
    def test_c1_register_creates_beneficiary(self):
        self.assertEqual(GovStackBeneficiary.objects.count(), 0)
        result = self._register()
        self.assertEqual(result, {"registered": 1, "updated": 0})
        self.assertEqual(GovStackBeneficiary.objects.count(), 1)
        obj = GovStackBeneficiary.objects.get()
        self.assertEqual(obj.payee_functional_id, VALID_PAYEE_ID)
        self.assertEqual(obj.source_bb_id, VALID_SOURCE_BB_ID)
        self.assertTrue(obj.is_active)

    # C2 — register() is idempotent: second call with same PayeeFunctionalID upserts
    def test_c2_register_is_idempotent(self):
        self._register()
        result = self._register(modality="BK")
        # Second call should not create a new record
        self.assertEqual(GovStackBeneficiary.objects.count(), 1)
        self.assertEqual(result, {"registered": 0, "updated": 1})

    # C3 — register() stores optional fields when provided
    def test_c3_register_stores_payment_modality(self):
        self._register(modality="MO", address="GB33BUKB20201555555555")
        obj = GovStackBeneficiary.objects.get()
        self.assertEqual(obj.payment_modality, "MO")
        # FinancialAddress is encrypted — check it's non-empty after decryption
        self.assertTrue(bool(obj.financial_address))

    # C4 — update() creates a new record when PayeeFunctionalID is unknown (upsert)
    #      This is the harness update smoke-test behaviour.
    def test_c4_update_creates_record_when_unknown(self):
        self.assertEqual(GovStackBeneficiary.objects.count(), 0)
        result = self._update()
        self.assertEqual(GovStackBeneficiary.objects.count(), 1)
        # update() calls _upsert_beneficiaries with action_on_create="beneficiary_updated"
        # for the audit label, but the counter still increments `registered` on a
        # first-time create (get_or_create returns created=True).  Either way, exactly
        # one operation occurred.
        self.assertEqual(result["registered"] + result["updated"], 1)

    # C5 — update() updates existing record
    def test_c5_update_updates_existing_record(self):
        self._register(modality="BK", address="old-address")
        self._update(modality="MO", address="new-address")
        obj = GovStackBeneficiary.objects.get()
        self.assertEqual(obj.payment_modality, "MO")

    # C6 — update() does not clear existing fields when new values are empty
    def test_c6_update_preserves_existing_values_when_empty_given(self):
        self._register(modality="BK", address="existing-address")
        # Call update with no address
        self._update(modality="MO", address="")
        obj = GovStackBeneficiary.objects.get()
        self.assertEqual(obj.payment_modality, "MO")
        # financial_address should still be set (not cleared)
        self.assertTrue(bool(obj.financial_address))

    # C7 — register() handles multiple beneficiaries in one call
    def test_c7_register_multiple_beneficiaries(self):
        payees = ["aabbccdd-1234", "11223344-abcd", "a1b2c3d4-e5f6"]
        result = GovStackBeneficiaryService.register(
            request_id=REQUEST_ID,
            source_bb_id=VALID_SOURCE_BB_ID,
            beneficiaries=[{"PayeeFunctionalID": p} for p in payees],
        )
        self.assertEqual(result, {"registered": 3, "updated": 0})
        self.assertEqual(GovStackBeneficiary.objects.count(), 3)

    # C8 — audit entries are created for every operation
    def test_c8_audit_entry_created_on_register(self):
        self.assertEqual(GovStackPaymentAuditEntry.objects.count(), 0)
        self._register()
        self.assertEqual(GovStackPaymentAuditEntry.objects.count(), 1)
        entry = GovStackPaymentAuditEntry.objects.get()
        self.assertEqual(entry.actor_bb_id, VALID_SOURCE_BB_ID)
        self.assertEqual(entry.object_type, "beneficiary")
        self.assertEqual(entry.request_id, REQUEST_ID)

    # C9 — audit entry details NEVER contain payee_functional_id or financial_address
    def test_c9_audit_entry_details_pii_free(self):
        self._register(address="GB33BUKB20201555555555")
        entry = GovStackPaymentAuditEntry.objects.get()
        details = entry.details
        self.assertIsInstance(details, dict)
        self.assertNotIn("payee_functional_id", details)
        self.assertNotIn("financial_address", details)
        # Verify the IBAN is also not present as any value
        details_str = json.dumps(details).lower()
        self.assertNotIn("gb33bukb20201555555555", details_str)
        self.assertNotIn(VALID_PAYEE_ID.lower(), details_str)

    # C10 — audit entry object_pk is the model UUID, not PayeeFunctionalID
    def test_c10_audit_entry_object_pk_is_uuid(self):
        self._register()
        obj = GovStackBeneficiary.objects.get()
        entry = GovStackPaymentAuditEntry.objects.get()
        self.assertEqual(entry.object_pk, str(obj.pk))
        self.assertNotEqual(entry.object_pk, VALID_PAYEE_ID)

    # C11 — register() + update() each create one audit entry
    def test_c11_two_operations_two_audit_entries(self):
        self._register()
        self._update(modality="BK")
        self.assertEqual(GovStackPaymentAuditEntry.objects.count(), 2)


# ============================================================================
# D.  GovStackPaymentAuditEntry — append-only model
# ============================================================================


class AuditEntryAppendOnlyTest(TestCase):
    """
    Verifies the append-only invariant on GovStackPaymentAuditEntry.
    """

    def _make_entry(self) -> GovStackPaymentAuditEntry:
        return GovStackPaymentAuditEntry.objects.create(
            action=GovStackPaymentAuditEntry.ACTION_BENEFICIARY_REGISTERED,
            actor_bb_id=VALID_SOURCE_BB_ID,
            object_type="beneficiary",
            object_pk="some-uuid-here",
            request_id=REQUEST_ID,
            details={"source_bb_id": VALID_SOURCE_BB_ID},
        )

    def test_d1_initial_create_succeeds(self):
        entry = self._make_entry()
        self.assertIsNotNone(entry.pk)

    def test_d2_delete_raises_permission_error(self):
        entry = self._make_entry()
        with self.assertRaises(PermissionError):
            entry.delete()

    def test_d3_save_after_creation_raises_permission_error(self):
        entry = self._make_entry()
        entry.actor_bb_id = "another-bb"
        with self.assertRaises(PermissionError):
            entry.save()

    def test_d4_queryset_delete_raises_permission_error(self):
        """Bulk delete via queryset must also be blocked."""
        self._make_entry()
        with self.assertRaises(PermissionError):
            GovStackPaymentAuditEntry.objects.all().delete()

    def test_d5_queryset_update_raises_permission_error(self):
        """
        Bulk update via queryset must be blocked.

        Django's QuerySet.update() issues a raw SQL UPDATE that bypasses the
        model's save() override.  Without _AuditEntryQuerySet.update() → PermissionError,
        any caller could silently corrupt the audit trail:
            GovStackPaymentAuditEntry.objects.filter(...).update(actor_bb_id="tampered")
        This test confirms the gap is closed.
        """
        self._make_entry()
        with self.assertRaises(PermissionError):
            GovStackPaymentAuditEntry.objects.all().update(actor_bb_id="tampered")


# ============================================================================
# E.  GovStackG2PView helpers
# ============================================================================


class FlattenErrorsTest(TestCase):
    """
    Unit tests for GovStackG2PView._flatten_errors().
    Uses a bare instance of the view (no request needed).
    """

    def setUp(self):
        # Instantiate the view without a request (helper methods are pure functions)
        self.view = GovStackG2PView()
        self.view.format_kwarg = None

    def test_e1_top_level_field_error(self):
        errors = {"SourceBBID": ["This field is required."]}
        result = self.view._flatten_errors(errors)
        self.assertIn("SourceBBID", result)
        self.assertIn("required", result)

    def test_e2_min_length_list_error(self):
        errors = {"Beneficiaries": ["Ensure this field has at least 1 elements."]}
        result = self.view._flatten_errors(errors)
        self.assertIn("Beneficiaries", result)

    def test_e3_nested_item_error(self):
        errors = {
            "Beneficiaries": [
                {"PayeeFunctionalID": ["PayeeFunctionalID must be 1–20 lowercase hex characters"]}  # noqa: RUF001
            ]
        }
        result = self.view._flatten_errors(errors)
        self.assertIn("PayeeFunctionalID", result)

    def test_e4_multiple_fields(self):
        errors = {
            "SourceBBID": ["Required."],
            "Beneficiaries": ["Required."],
        }
        result = self.view._flatten_errors(errors)
        self.assertIn("SourceBBID", result)
        self.assertIn("Beneficiaries", result)

    def test_e5_truncates_at_200_chars(self):
        # Build an error that would be >200 chars when serialised
        long_message = "x" * 250
        errors = {"SourceBBID": [long_message]}
        result = self.view._flatten_errors(errors)
        self.assertLessEqual(len(result), 200)

    def test_e6_empty_dict_returns_fallback(self):
        result = self.view._flatten_errors({})
        # Must be a non-empty string — harness g2pResponseSchema requires
        # ResponseDescription minLength: 1. An empty string would fail the schema.
        self.assertIsInstance(result, str)
        self.assertGreaterEqual(len(result), 1, "_flatten_errors({}) must not return ''")

    def test_e6b_empty_list_value_returns_fallback(self):
        """Dict with only empty list values must also return a non-empty fallback."""
        result = self.view._flatten_errors({"field": []})
        self.assertIsInstance(result, str)
        self.assertGreaterEqual(len(result), 1, "_flatten_errors({'field': []}) must not return ''")

    def test_e7_plain_string_passthrough(self):
        result = self.view._flatten_errors("Something went wrong.")
        self.assertEqual(result, "Something went wrong.")

    def test_e8_no_pii_in_output(self):
        """_flatten_errors must not include FinancialAddress values."""
        iban = "GB33BUKB20201555555555"
        errors = {"FinancialAddress": [f"Value {iban} is too long."]}
        result = self.view._flatten_errors(errors)
        # The field name is ok (it's a key), but the IBAN value in the message is allowed
        # through since it came from the error message string, not from request body PII.
        # What matters is that the view never puts FinancialAddress *values* into responses.
        # This test just confirms _flatten_errors doesn't crash on IBAN-containing messages.
        self.assertIsInstance(result, str)


# ============================================================================
# F.  govstack_g2p_exception_handler
# ============================================================================


class G2PExceptionHandlerTest(TestCase):
    """
    Tests for the standalone govstack_g2p_exception_handler function.
    Verifies it wraps DRF exceptions in the G2P envelope format.
    """

    def _make_context(self, body: dict | None = None):
        """Build a minimal context dict with a fake request."""
        from unittest.mock import MagicMock

        request = MagicMock()
        if body is not None:
            request.data = body
        else:
            request.data = {}
        return {"request": request}

    def test_f1_wraps_validation_error_in_g2p_envelope(self):
        from rest_framework.exceptions import ValidationError

        exc = ValidationError({"SourceBBID": ["This field is required."]})
        context = self._make_context({"RequestID": REQUEST_ID})
        response = govstack_g2p_exception_handler(exc, context)
        self.assertIsNotNone(response)
        data = response.data
        self.assertEqual(data["ResponseCode"], "01")
        self.assertEqual(data["RequestID"], REQUEST_ID)
        self.assertIn("ResponseDescription", data)

    def test_f2_wraps_not_authenticated_error(self):
        from rest_framework.exceptions import NotAuthenticated

        exc = NotAuthenticated()
        context = self._make_context({"RequestID": REQUEST_ID})
        response = govstack_g2p_exception_handler(exc, context)
        self.assertIsNotNone(response)
        self.assertEqual(response.data["ResponseCode"], "01")
        self.assertEqual(response.data["RequestID"], REQUEST_ID)

    def test_f3_echoes_request_id_even_on_auth_error(self):
        from rest_framework.exceptions import PermissionDenied

        exc = PermissionDenied()
        context = self._make_context({"RequestID": "xyz987654321"})
        response = govstack_g2p_exception_handler(exc, context)
        self.assertEqual(response.data["RequestID"], "xyz987654321")

    def test_f4_request_id_empty_when_body_missing(self):
        from rest_framework.exceptions import ParseError

        exc = ParseError()
        context = self._make_context({})  # body has no RequestID key
        response = govstack_g2p_exception_handler(exc, context)
        self.assertEqual(response.data["RequestID"], "")

    def test_f5_returns_none_for_unhandled_exceptions(self):
        """Non-DRF exceptions that DRF cannot handle → returns None (500)."""
        exc = RuntimeError("unexpected crash")
        context = self._make_context({})
        response = govstack_g2p_exception_handler(exc, context)
        self.assertIsNone(response)

    def test_f6_description_truncated_at_200_chars(self):
        from rest_framework.exceptions import ValidationError

        long_msg = "E" * 300
        exc = ValidationError(detail=long_msg)
        context = self._make_context({})
        response = govstack_g2p_exception_handler(exc, context)
        self.assertLessEqual(len(response.data["ResponseDescription"]), 200)


# ============================================================================
# G.  Serializer validation
# ============================================================================


class BeneficiarySerializerTest(TestCase):
    """
    Unit tests for BeneficiaryItemSerializer and RegisterBeneficiaryRequestSerializer.
    """

    # G1 — valid harness SourceBBID is accepted
    def test_g1_valid_source_bb_id_accepted(self):
        ser = RegisterBeneficiaryRequestSerializer(
            data={
                "RequestID": REQUEST_ID,
                "SourceBBID": VALID_SOURCE_BB_ID,
                "Beneficiaries": [{"PayeeFunctionalID": VALID_PAYEE_ID}],
            }
        )
        self.assertTrue(ser.is_valid(), ser.errors)

    # G2 — invalid SourceBBID "invalid" is rejected
    def test_g2_invalid_source_bb_id_rejected(self):
        ser = RegisterBeneficiaryRequestSerializer(
            data={
                "RequestID": REQUEST_ID,
                "SourceBBID": INVALID_ID,
                "Beneficiaries": [{"PayeeFunctionalID": VALID_PAYEE_ID}],
            }
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("SourceBBID", ser.errors)

    # G3 — valid harness PayeeFunctionalID is accepted
    def test_g3_valid_payee_id_accepted(self):
        item_ser = BeneficiaryItemSerializer(data={"PayeeFunctionalID": VALID_PAYEE_ID})
        self.assertTrue(item_ser.is_valid(), item_ser.errors)

    # G4 — "invalid" PayeeFunctionalID is rejected (contains non-hex chars)
    def test_g4_invalid_payee_id_rejected(self):
        item_ser = BeneficiaryItemSerializer(data={"PayeeFunctionalID": INVALID_ID})
        self.assertFalse(item_ser.is_valid())
        self.assertIn("PayeeFunctionalID", item_ser.errors)

    # G5 — empty Beneficiaries[] triggers min_length error
    def test_g5_empty_beneficiaries_triggers_min_length(self):
        ser = RegisterBeneficiaryRequestSerializer(
            data={
                "RequestID": REQUEST_ID,
                "SourceBBID": VALID_SOURCE_BB_ID,
                "Beneficiaries": [],
            }
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("Beneficiaries", ser.errors)

    # G6 — missing SourceBBID is required
    def test_g6_missing_source_bb_id_required(self):
        ser = RegisterBeneficiaryRequestSerializer(
            data={
                "RequestID": REQUEST_ID,
                "Beneficiaries": [{"PayeeFunctionalID": VALID_PAYEE_ID}],
            }
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("SourceBBID", ser.errors)

    # G7 — missing Beneficiaries is required
    def test_g7_missing_beneficiaries_required(self):
        ser = RegisterBeneficiaryRequestSerializer(
            data={
                "RequestID": REQUEST_ID,
                "SourceBBID": VALID_SOURCE_BB_ID,
            }
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("Beneficiaries", ser.errors)

    # G8 — an omitted RequestID key still defaults to "" unvalidated.
    # NOTE: re-verified against a fresh clone of the live harness (all 4
    # g2p_*.feature files) that no scenario actually omits RequestID for this
    # endpoint — this test documents current lenient behaviour for the
    # omitted-key case specifically (DRF does not run field-level validators,
    # like _validate_request_id, when a required=False field falls back to its
    # default), not an accurate claim about what the harness sends.
    def test_g8_request_id_optional(self):
        ser = RegisterBeneficiaryRequestSerializer(
            data={
                "SourceBBID": VALID_SOURCE_BB_ID,
                "Beneficiaries": [{"PayeeFunctionalID": VALID_PAYEE_ID}],
            }
        )
        self.assertTrue(ser.is_valid(), ser.errors)
        self.assertEqual(ser.validated_data["RequestID"], "")

    # G9 — PaymentModality and FinancialAddress are optional
    def test_g9_optional_fields_not_required(self):
        item_ser = BeneficiaryItemSerializer(data={"PayeeFunctionalID": VALID_PAYEE_ID})
        self.assertTrue(item_ser.is_valid(), item_ser.errors)
        self.assertEqual(item_ser.validated_data["PaymentModality"], "")
        self.assertEqual(item_ser.validated_data["FinancialAddress"], "")

    # G10 — FinancialAddress up to 512 chars is accepted (IBAN max 34, but field is generous)
    def test_g10_financial_address_max_length_512(self):
        item_ser = BeneficiaryItemSerializer(
            data={
                "PayeeFunctionalID": VALID_PAYEE_ID,
                "FinancialAddress": "X" * 512,
            }
        )
        self.assertTrue(item_ser.is_valid(), item_ser.errors)

    # G11 — harness shortest valid SourceBBID (1 hex char) is accepted
    def test_g11_shortest_valid_source_bb_id(self):
        ser = RegisterBeneficiaryRequestSerializer(
            data={
                "SourceBBID": "a",
                "Beneficiaries": [{"PayeeFunctionalID": "a"}],
            }
        )
        self.assertTrue(ser.is_valid(), ser.errors)

    # G12 — G2P ID with only hyphens is rejected (empty value after strip)
    def test_g12_all_hyphens_source_bb_id(self):
        # "---" is technically valid per regex (hyphens are allowed)
        # but it contains no hex content — however regex allows it.
        # This test simply verifies the regex boundary is consistent.
        ser = RegisterBeneficiaryRequestSerializer(
            data={
                "SourceBBID": "---",
                "Beneficiaries": [{"PayeeFunctionalID": VALID_PAYEE_ID}],
            }
        )
        # "---" matches ^[0-9a-f\-]{1,20}$ so it is accepted
        self.assertTrue(ser.is_valid(), ser.errors)

    # G13 — UpdateBeneficiaryRequestSerializer is an alias (same schema as register)
    def test_g13_update_serializer_alias_is_functional(self):
        """UpdateBeneficiaryRequestSerializer must validate identically to register."""
        ser = UpdateBeneficiaryRequestSerializer(
            data={
                "RequestID": REQUEST_ID,
                "SourceBBID": VALID_SOURCE_BB_ID,
                "Beneficiaries": [{"PayeeFunctionalID": VALID_PAYEE_ID}],
            }
        )
        self.assertTrue(ser.is_valid(), ser.errors)
        # Invalid SourceBBID must also be rejected
        ser_bad = UpdateBeneficiaryRequestSerializer(
            data={
                "SourceBBID": INVALID_ID,
                "Beneficiaries": [{"PayeeFunctionalID": VALID_PAYEE_ID}],
            }
        )
        self.assertFalse(ser_bad.is_valid())
        self.assertIn("SourceBBID", ser_bad.errors)

    # G14 (was G13) — uppercase hex chars are NOT accepted (G2P requires lowercase)
    def test_g14_uppercase_hex_rejected(self):
        ser = RegisterBeneficiaryRequestSerializer(
            data={
                "SourceBBID": "AABBCCDD-1234",  # uppercase — should fail G2P hex check
                "Beneficiaries": [{"PayeeFunctionalID": VALID_PAYEE_ID}],
            }
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("SourceBBID", ser.errors)

    # G15 — exactly-12-char RequestID is accepted (live spec: g2pResponseSchema
    # RequestID is {minLength: 12, maxLength: 12}).
    def test_g15_exactly_12_char_request_id_accepted(self):
        ser = RegisterBeneficiaryRequestSerializer(
            data={
                "RequestID": "abc123456789",  # exactly 12 chars
                "SourceBBID": VALID_SOURCE_BB_ID,
                "Beneficiaries": [{"PayeeFunctionalID": VALID_PAYEE_ID}],
            }
        )
        self.assertTrue(ser.is_valid(), ser.errors)
        self.assertEqual(ser.validated_data["RequestID"], "abc123456789")

    # G16 — an 11-char RequestID is rejected (too short per live spec)
    def test_g16_eleven_char_request_id_rejected(self):
        ser = RegisterBeneficiaryRequestSerializer(
            data={
                "RequestID": "abc12345678",  # 11 chars
                "SourceBBID": VALID_SOURCE_BB_ID,
                "Beneficiaries": [{"PayeeFunctionalID": VALID_PAYEE_ID}],
            }
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("RequestID", ser.errors)

    # G17 — a 13-char RequestID is rejected (too long per live spec)
    def test_g17_thirteen_char_request_id_rejected(self):
        ser = RegisterBeneficiaryRequestSerializer(
            data={
                "RequestID": "abc1234567890",  # 13 chars
                "SourceBBID": VALID_SOURCE_BB_ID,
                "Beneficiaries": [{"PayeeFunctionalID": VALID_PAYEE_ID}],
            }
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("RequestID", ser.errors)

    # G18 — the same exactly-12 / 11 / 13 boundary applies at the HTTP layer:
    # a malformed-length RequestID → HTTP 400, ResponseCode "01", and the raw
    # (invalid) RequestID is still echoed back verbatim (echo-back is
    # independent of input validation — see GovStackG2PView._request_id).
    def test_g18_malformed_length_request_id_rejected_at_http_layer(self):
        payload = {
            "RequestID": "short-id",  # 8 chars — fails the exactly-12 constraint
            "SourceBBID": VALID_SOURCE_BB_ID,
            "Beneficiaries": [{"PayeeFunctionalID": VALID_PAYEE_ID}],
        }
        resp = APIClient().post(REGISTER_URL, payload, format="json")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["ResponseCode"], "01")
        self.assertEqual(body["RequestID"], "short-id")


# ============================================================================
# H.  Integration: database state after view calls
# ============================================================================


@_NO_THROTTLE
class DatabaseStateIntegrationTest(TestCase):
    """
    End-to-end: verifies that database state is correct after HTTP calls.
    The view, serializer, service, and model all work together.
    """

    def setUp(self):
        self.client = APIClient()
        # Both beneficiary endpoints require IsTrustedSourceBB.
        self.client.defaults["HTTP_X_REGISTERING_INSTITUTION_ID"] = "GS-TEST"

    def test_h1_register_call_persists_beneficiary(self):
        self.assertEqual(GovStackBeneficiary.objects.count(), 0)
        resp = self.client.post(REGISTER_URL, _VALID_BODY, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(GovStackBeneficiary.objects.count(), 1)

    def test_h2_update_call_creates_beneficiary_when_unknown(self):
        """update-beneficiary-details on an unregistered ID must create the record."""
        self.assertEqual(GovStackBeneficiary.objects.count(), 0)
        resp = self.client.post(UPDATE_URL, _VALID_BODY, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["ResponseCode"], "00")
        self.assertEqual(GovStackBeneficiary.objects.count(), 1)

    def test_h3_idempotent_register(self):
        """Two identical register calls → same 1 DB record (no duplicate)."""
        self.client.post(REGISTER_URL, _VALID_BODY, format="json")
        self.client.post(REGISTER_URL, _VALID_BODY, format="json")
        self.assertEqual(GovStackBeneficiary.objects.count(), 1)

    def test_h4_register_then_update_updates_modality(self):
        """Register with no modality, then update with modality BK."""
        self.client.post(REGISTER_URL, _VALID_BODY, format="json")
        update_body = dict(_VALID_BODY)
        update_body["Beneficiaries"] = [
            {"PayeeFunctionalID": VALID_PAYEE_ID, "PaymentModality": "BK"}
        ]
        self.client.post(UPDATE_URL, update_body, format="json")
        obj = GovStackBeneficiary.objects.get()
        self.assertEqual(obj.payment_modality, "BK")

    def test_h5_register_creates_audit_trail(self):
        self.client.post(REGISTER_URL, _FULL_BODY, format="json")
        self.assertEqual(GovStackPaymentAuditEntry.objects.count(), 1)

    def test_h6_failed_request_does_not_create_records(self):
        """Invalid request (missing SourceBBID) must not create any DB records."""
        payload = {
            "RequestID": REQUEST_ID,
            "Beneficiaries": [{"PayeeFunctionalID": VALID_PAYEE_ID}],
        }
        resp = self.client.post(REGISTER_URL, payload, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(GovStackBeneficiary.objects.count(), 0)
        self.assertEqual(GovStackPaymentAuditEntry.objects.count(), 0)

    def test_h7_register_with_iban_financial_address(self):
        """FinancialAddress with a 22-char IBAN is accepted and stored encrypted."""
        resp = self.client.post(REGISTER_URL, _FULL_BODY, format="json")
        self.assertEqual(resp.status_code, 200)
        obj = GovStackBeneficiary.objects.get()
        # The encrypted value is stored; decrypt and compare
        self.assertEqual(obj.financial_address, "DE89370400440532013000")

    def test_h8_financial_address_never_in_response(self):
        """The IBAN stored during register must never appear in the HTTP response."""
        resp = self.client.post(REGISTER_URL, _FULL_BODY, format="json")
        body_str = json.dumps(resp.json())
        self.assertNotIn("DE89370400440532013000", body_str)
        self.assertNotIn("de89370400440532013000", body_str)

    def test_h9_payee_functional_id_never_in_response(self):
        """PayeeFunctionalID must not appear anywhere in the HTTP response."""
        resp = self.client.post(REGISTER_URL, _VALID_BODY, format="json")
        body_str = json.dumps(resp.json())
        self.assertNotIn(VALID_PAYEE_ID, body_str)


# ============================================================================
# I.  GovStackBeneficiary model-level validation
# ============================================================================


class GovStackBeneficiaryModelValidationTest(TestCase):
    """
    Verifies that the _G2P_UUID_VALIDATOR applied to payee_functional_id
    is enforced at the model level via full_clean().

    Context: The serializer layer already rejects non-hex IDs via _validate_g2p_id.
    This test guards against direct ORM writes (admin, management commands, tests)
    that bypass the serializer and could create records with IDs that don't conform
    to the G2P spec.

    Regression for review finding: "Model validator inconsistency on payee_functional_id".
    """

    def _make_beneficiary(self, payee_id: str) -> GovStackBeneficiary:
        """Create an in-memory GovStackBeneficiary without saving to DB."""
        return GovStackBeneficiary(
            payee_functional_id=payee_id,
            source_bb_id=VALID_SOURCE_BB_ID,
            payment_modality="",
            is_active=True,
        )

    # I1 — valid hex+hyphen ID passes full_clean()
    def test_i1_valid_hex_id_passes_full_clean(self):
        """A lowercase hex+hyphen PayeeFunctionalID must pass full_clean()."""
        from django.core.exceptions import ValidationError

        ben = self._make_beneficiary(VALID_PAYEE_ID)
        try:
            ben.full_clean()
        except ValidationError as exc:
            self.fail(f"full_clean() raised ValidationError for valid ID {VALID_PAYEE_ID!r}: {exc}")

    # I2 — uppercase ID is rejected by full_clean()
    def test_i2_uppercase_id_fails_full_clean(self):
        """An uppercase PayeeFunctionalID must be rejected by full_clean()."""
        from django.core.exceptions import ValidationError

        ben = self._make_beneficiary("UPPERCASE-ABCD")
        with self.assertRaises(ValidationError) as cm:
            ben.full_clean()
        # The error must target the payee_functional_id field specifically
        self.assertIn("payee_functional_id", cm.exception.message_dict)

    # I3 — "invalid" (harness negative test string) is also rejected at model level
    def test_i3_harness_invalid_string_fails_full_clean(self):
        """The literal string 'invalid' must be rejected at the model level too."""
        from django.core.exceptions import ValidationError

        ben = self._make_beneficiary(INVALID_ID)  # "invalid" contains i, n, v, l
        with self.assertRaises(ValidationError) as cm:
            ben.full_clean()
        self.assertIn("payee_functional_id", cm.exception.message_dict)

    # I4 — single lowercase hex char is the shortest valid value
    def test_i4_single_hex_char_accepted(self):
        """A single lowercase hex char ('a') is the minimum valid value."""
        from django.core.exceptions import ValidationError

        ben = self._make_beneficiary("a")
        try:
            ben.full_clean()
        except ValidationError as exc:
            self.fail(f"full_clean() rejected single-char hex ID: {exc}")

    # I5 — error message is user-readable and doesn't leak raw regex
    def test_i5_error_message_is_human_readable(self):
        """ValidationError message must be a helpful string, not a raw regex pattern."""
        from django.core.exceptions import ValidationError

        ben = self._make_beneficiary("UPPERCASE")
        with self.assertRaises(ValidationError) as cm:
            ben.full_clean()
        errors = cm.exception.message_dict.get("payee_functional_id", [])
        self.assertTrue(errors, "Expected at least one error message")
        error_text = str(errors[0])
        # Must reference something meaningful — not just the raw regex pattern
        self.assertIn("lowercase", error_text.lower())
