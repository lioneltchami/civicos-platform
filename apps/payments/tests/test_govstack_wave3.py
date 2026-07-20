"""
test_govstack_wave3.py

Comprehensive tests for GovStack Payments BB — Wave 3
(G2P Bulk Payment + Prepayment Validation).

Coverage matrix:
  A. BulkPayment view — harness scenarios (7 total)
     A1: Smoke POST → HTTP 200
     A2: Full fields + Narration → HTTP 200, ResponseCode "00", RequestID echoed
     A3: Missing SourceBBID → HTTP 400, ResponseCode "01"
     A4: Missing BatchID → HTTP 400, ResponseCode "01"
     A5: Empty CreditInstructions array → HTTP 400, ResponseCode "01"
     A6: "invalid" SourceBBID (7 chars, fails min_length=10) → HTTP 400
     A7: "invalid" BatchID (7 chars, fails min_length=10) → HTTP 400

  B. PrepaymentValidation view — harness scenarios (15 total, ALL HTTP 200)
     B1:  Smoke POST → HTTP 200
     B2:  Full valid → HTTP 200, ResponseCode "00"
     B3:  Chained → POST /prepayment-validation-response → HTTP 200, correct schema
     B4:  Missing SourceBBID → HTTP 200, ResponseCode "01"
     B5:  Missing BatchID → HTTP 200, ResponseCode "01"
     B6:  Missing CreditInstructions → HTTP 200, ResponseCode "01"
     B7:  Missing InstructionID in instruction → HTTP 200, ResponseCode "01"
     B8:  Missing PayeeFunctionalID in instruction → HTTP 200, ResponseCode "01"
     B9:  Missing Amount → HTTP 200, ResponseCode "01"
     B10: Missing Currency → HTTP 200, ResponseCode "01"
     B11: Missing Narration (required in prepayment) → HTTP 200, ResponseCode "01"
     B12: "Invalid SourceBBID" partial body → HTTP 200, ResponseCode "01"
     B13: "Invalid BatchID" partial body → HTTP 200, ResponseCode "01"
     B14: Invalid Amount "100.10.1" (unparseable Decimal) → HTTP 200, ResponseCode "01"
     B15: Invalid Currency "US" (2 chars, fails ISO 4217) → HTTP 200, ResponseCode "01"

  C. G2P envelope invariants
     C1: BulkPayment success envelope has all three required keys
     C2: BulkPayment error envelope has all three required keys (HTTP 400)
     C3: PrepaymentValidation error is always HTTP 200, ResponseCode "01"
     C4: ResponseDescription is never empty (minLength: 1 per g2pResponseSchema)
     C5: RequestID is echoed verbatim from request body
     C6: PayeeFunctionalID never appears in any response body
     C7: PrepaymentValidationResponse always returns HTTP 200

  D. GovStackBulkPaymentService — service layer
     D1:  receive_batch() creates a BulkPaymentBatch record
     D2:  receive_batch() sets status = STATUS_RECEIVED
     D3:  receive_batch() creates one CreditInstruction per item
     D4:  receive_batch() computes total_amount correctly
     D5:  receive_batch() stores correlation_id
     D6:  receive_batch() stores callback_url
     D7:  receive_batch() creates an ACTION_BATCH_RECEIVED audit entry
     D8:  receive_batch() audit details never contain payee_functional_id
     D9:  validate_prepayment() creates a PrepaymentValidationRequest (status=PENDING)
     D10: validate_prepayment() stores all instruction fields
     D11: validate_prepayment() beneficiary_found is null (pending, not yet checked)
     D12: validate_prepayment() creates ACTION_VALIDATION_REQUESTED audit entry
     D13: validate_prepayment() audit details never contain payee_functional_id
     D14: get_validation_result() returns 0 failed cases for PENDING records
     D15: get_validation_result() falls back to batch_id when request_id not found
     D16: get_validation_result() COMPLETED + beneficiary_found=False → FailedAccounts (security: no PayeeFunctionalID)
     D17: get_validation_result() COMPLETED + financial_address_valid=False → FailedAccounts
     D18: get_validation_result() COMPLETED + both=True → not counted as failure

  E. Serializer tests
     E1:  BulkPaymentRequestSerializer accepts valid harness SourceBBID (12 chars)
     E2:  BulkPaymentRequestSerializer rejects "invalid" SourceBBID (7 chars)
     E3:  BulkPaymentRequestSerializer accepts valid harness BatchID (12 chars)
     E4:  BulkPaymentRequestSerializer rejects "invalid" BatchID (7 chars)
     E5:  BulkPaymentRequestSerializer rejects empty CreditInstructions
     E6:  PrepaymentCreditInstructionSerializer: Narration IS required
     E7:  CreditInstructionSerializer (bulk): Narration is optional
     E8:  PrepaymentValidationResponseAckSerializer: field is Source_BatchID (with underscore)
     E9:  PrepaymentValidationResponseAckSerializer: SourceBatchID (no underscore) is ignored

  F. Critical bug regression tests (post-review fixes)
     F1: UUID-format correlation_id (36 chars) accepted and persisted without truncation
     F2: duplicate BatchID → HTTP 400 G2P envelope, not 500 IntegrityError
     F2b: DuplicateBatchError raised by service (not IntegrityError)
     F3: multiple CreditInstructions in prepayment-validation → HTTP 200, ResponseCode "01"
     F4: 100-char correlation ID accepted end-to-end

Security invariants tested:
  - payee_functional_id NEVER in any response field (C6, D8, D13, D16)
  - Audit entry details never contain payee_functional_id key (D8, D13)
  - FailedAccounts uses InstructionID only, never PayeeFunctionalID (D16, D17)
  - Duplicate BatchID error message never leaks the BatchID value (F2)

Harness identifiers:
  Bulk smoke:   RequestID="RequestID111" SourceBBID="SourceBBID11" BatchID="BatchID11111"
  Bulk unit+:   RequestID="RequestID222" SourceBBID="SourceBBID22" BatchID="BatchID22222"
  Prepay smoke: RequestID="abcdef123456" SourceBBID="sourceBBID12" BatchID="batchID12345"
  Prepay unit+: RequestID="d5267933-c71" SourceBBID="e680db9f-223" BatchID="aa4533fc-018"
"""
from __future__ import annotations

import json
from decimal import Decimal

from django.test import TestCase

from rest_framework.test import APIClient

from apps.payments.govstack_exceptions import DuplicateBatchError
from apps.payments.govstack_models import (
    BulkPaymentBatch,
    CreditInstruction,
    GovStackPaymentAuditEntry,
    PrepaymentValidationRequest,
)
from apps.payments.govstack_serializers import (
    BulkPaymentRequestSerializer,
    CreditInstructionSerializer,
    PrepaymentCreditInstructionSerializer,
    PrepaymentValidationResponseAckSerializer,
)
from apps.payments.govstack_services import GovStackBulkPaymentService


# ---------------------------------------------------------------------------
# Constants  (mirror harness identifiers exactly)
# ---------------------------------------------------------------------------

# Bulk Payment harness
BP_REQUEST_ID_1  = "RequestID111"   # 12 chars
BP_SOURCE_BB_1   = "SourceBBID11"   # 12 chars, mixed-case alphanumeric
BP_BATCH_ID_1    = "BatchID11111"   # 12 chars
BP_INSTR_ID_1    = "InstructionID111"  # 16 chars
BP_PAYEE_ID_1    = "PayeeFunctionalID111"  # 20 chars

BP_REQUEST_ID_2  = "RequestID222"   # 12 chars
BP_SOURCE_BB_2   = "SourceBBID22"   # 12 chars
BP_BATCH_ID_2    = "BatchID22222"   # 12 chars
BP_INSTR_ID_2    = "InstructionID222"  # 16 chars
BP_PAYEE_ID_2    = "PayeeFunctionalID222"  # 20 chars

# Prepayment Validation harness (smoke)
PV_REQUEST_ID_1  = "abcdef123456"   # 12 chars
PV_SOURCE_BB_1   = "sourceBBID12"   # 12 chars
PV_BATCH_ID_1    = "batchID12345"   # 12 chars
PV_INSTR_ID_1    = "instructionID123"  # 16 chars
PV_PAYEE_ID_1    = "PayeeFunctionalID123"  # 20 chars

# Prepayment Validation harness (unit)
PV_REQUEST_ID_2  = "d5267933-c71"   # 12 chars
PV_SOURCE_BB_2   = "e680db9f-223"   # 12 chars
PV_BATCH_ID_2    = "aa4533fc-018"   # 12 chars
PV_INSTR_ID_2    = "0947d8d7-0bdb-40"  # 16 chars
PV_PAYEE_ID_2    = "a98bc16e-f724-410e-b"  # 20 chars

INVALID_ID       = "invalid"        # 7 chars — harness negative test value

BULK_PAYMENT_URL         = "/govstack/payments/bulk-payment"
PREPAY_VALIDATION_URL    = "/govstack/payments/prepayment-validation"
PREPAY_RESPONSE_URL      = "/govstack/payments/prepayment-validation-response"

# NOTE: No _NO_THROTTLE override needed.  The test settings (config.settings.test)
# already configure "govstack_bb": "10000/minute" in DEFAULT_THROTTLE_RATES, which
# is effectively unlimited for tests.  Using override_settings to replace the entire
# REST_FRAMEWORK dict would REMOVE "govstack_bb" from DEFAULT_THROTTLE_RATES and cause
# ScopedRateThrottle to raise KeyError when the view's throttle_classes are evaluated.


def _bulk_body(
    request_id=BP_REQUEST_ID_1,
    source_bb=BP_SOURCE_BB_1,
    batch_id=BP_BATCH_ID_1,
    instr_id=BP_INSTR_ID_1,
    payee_id=BP_PAYEE_ID_1,
    amount=100,
    currency="USD",
    narration=None,
) -> dict:
    """Build a valid BulkPayment request body."""
    instruction = {
        "InstructionID": instr_id,
        "PayeeFunctionalID": payee_id,
        "Amount": amount,
        "Currency": currency,
    }
    if narration is not None:
        instruction["Narration"] = narration
    return {
        "RequestID": request_id,
        "SourceBBID": source_bb,
        "BatchID": batch_id,
        "CreditInstructions": [instruction],
    }


def _prepay_body(
    request_id=PV_REQUEST_ID_1,
    source_bb=PV_SOURCE_BB_1,
    batch_id=PV_BATCH_ID_1,
    instr_id=PV_INSTR_ID_1,
    payee_id=PV_PAYEE_ID_1,
    amount=100,
    currency="USD",
    narration="Narration",
) -> dict:
    """Build a valid PrepaymentValidation request body."""
    return {
        "RequestID": request_id,
        "SourceBBID": source_bb,
        "BatchID": batch_id,
        "CreditInstructions": [{
            "InstructionID": instr_id,
            "PayeeFunctionalID": payee_id,
            "Amount": amount,
            "Currency": currency,
            "Narration": narration,
        }],
    }


# ============================================================================
# A.  BulkPayment — harness scenarios
# ============================================================================

class BulkPaymentHarnessTest(TestCase):
    """Wave 3 BulkPayment: all 7 harness scenarios."""

    def setUp(self):
        self.client = APIClient()

    # A1 — Smoke: basic POST returns HTTP 200
    def test_a1_smoke_returns_200(self):
        resp = self.client.post(
            BULK_PAYMENT_URL,
            data=_bulk_body(),
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/json")

    # A2 — Full fields (Narration) + ResponseCode "00" + RequestID echoed
    def test_a2_full_fields_response_code_00(self):
        body = _bulk_body(
            request_id=BP_REQUEST_ID_2,
            source_bb=BP_SOURCE_BB_2,
            batch_id=BP_BATCH_ID_2,
            instr_id=BP_INSTR_ID_2,
            payee_id=BP_PAYEE_ID_2,
            narration="string",
        )
        resp = self.client.post(BULK_PAYMENT_URL, data=body, format="json")
        data = resp.json()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data["ResponseCode"], "00")
        self.assertEqual(data["RequestID"], BP_REQUEST_ID_2)
        self.assertIn("ResponseDescription", data)
        self.assertTrue(len(data["ResponseDescription"]) >= 1)

    # A3 — Missing SourceBBID → HTTP 400, ResponseCode "01"
    def test_a3_missing_source_bb_id_returns_400(self):
        body = {
            "RequestID": BP_REQUEST_ID_1,
            "BatchID": BP_BATCH_ID_1,
            "CreditInstructions": [{
                "InstructionID": BP_INSTR_ID_1,
                "PayeeFunctionalID": BP_PAYEE_ID_1,
                "Amount": 100,
                "Currency": "USD",
            }],
        }
        resp = self.client.post(BULK_PAYMENT_URL, data=body, format="json")
        data = resp.json()

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(data["ResponseCode"], "01")
        self.assertTrue(len(data["ResponseDescription"]) >= 1)

    # A4 — Missing BatchID → HTTP 400, ResponseCode "01"
    def test_a4_missing_batch_id_returns_400(self):
        body = {
            "RequestID": BP_REQUEST_ID_1,
            "SourceBBID": BP_SOURCE_BB_1,
            "CreditInstructions": [{
                "InstructionID": BP_INSTR_ID_1,
                "PayeeFunctionalID": BP_PAYEE_ID_1,
                "Amount": 100,
                "Currency": "USD",
            }],
        }
        resp = self.client.post(BULK_PAYMENT_URL, data=body, format="json")
        data = resp.json()

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(data["ResponseCode"], "01")

    # A5 — Empty CreditInstructions → HTTP 400, ResponseCode "01"
    def test_a5_empty_credit_instructions_returns_400(self):
        body = {
            "RequestID": BP_REQUEST_ID_1,
            "SourceBBID": BP_SOURCE_BB_1,
            "BatchID": BP_BATCH_ID_1,
            "CreditInstructions": [],
        }
        resp = self.client.post(BULK_PAYMENT_URL, data=body, format="json")
        data = resp.json()

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(data["ResponseCode"], "01")

    # A6 — "invalid" SourceBBID (7 chars, fails min_length=10) → HTTP 400
    def test_a6_invalid_source_bb_id_returns_400(self):
        body = _bulk_body(source_bb=INVALID_ID)  # "invalid" = 7 chars
        resp = self.client.post(BULK_PAYMENT_URL, data=body, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["ResponseCode"], "01")

    # A7 — "invalid" BatchID (7 chars, fails min_length=10) → HTTP 400
    def test_a7_invalid_batch_id_returns_400(self):
        body = _bulk_body(batch_id=INVALID_ID)  # "invalid" = 7 chars
        resp = self.client.post(BULK_PAYMENT_URL, data=body, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["ResponseCode"], "01")


# ============================================================================
# B.  PrepaymentValidation — harness scenarios (ALL HTTP 200)
# ============================================================================

class PrepaymentValidationHarnessTest(TestCase):
    """Wave 3 PrepaymentValidation: all 15 harness scenarios (always HTTP 200)."""

    def setUp(self):
        self.client = APIClient()

    def _post(self, body: dict) -> tuple[int, dict]:
        resp = self.client.post(PREPAY_VALIDATION_URL, data=body, format="json")
        return resp.status_code, resp.json()

    # B1 — Smoke: basic POST → HTTP 200, JSON content type
    def test_b1_smoke_returns_200(self):
        resp = self.client.post(PREPAY_VALIDATION_URL, data=_prepay_body(), format="json")
        self.assertEqual(resp.status_code, 200)
        # Verify Content-Type on the HTTP response object (not inside the JSON body).
        self.assertIn("application/json", resp["Content-Type"])
        self.assertIn("ResponseCode", resp.json())

    # B2 — Full valid → HTTP 200, ResponseCode "00"
    def test_b2_full_valid_response_code_00(self):
        body = _prepay_body(
            request_id=PV_REQUEST_ID_2,
            source_bb=PV_SOURCE_BB_2,
            batch_id=PV_BATCH_ID_2,
            instr_id=PV_INSTR_ID_2,
            payee_id=PV_PAYEE_ID_2,
        )
        status, data = self._post(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["ResponseCode"], "00")
        self.assertEqual(data["RequestID"], PV_REQUEST_ID_2)

    # B3 — Chained two-step: POST /prepayment-validation then /prepayment-validation-response
    def test_b3_chained_two_step_response(self):
        # Step 1: POST /prepayment-validation
        self._post(_prepay_body(
            request_id=PV_REQUEST_ID_2,
            source_bb=PV_SOURCE_BB_2,
            batch_id=PV_BATCH_ID_2,
            instr_id=PV_INSTR_ID_2,
            payee_id=PV_PAYEE_ID_2,
        ))

        # Step 2: POST /prepayment-validation-response
        resp = self.client.post(
            PREPAY_RESPONSE_URL,
            data={"RequestID": PV_REQUEST_ID_2, "Source_BatchID": PV_BATCH_ID_2},
            format="json",
        )
        data = resp.json()

        self.assertEqual(resp.status_code, 200)
        self.assertIn("RequestID", data)
        self.assertIn("Source_BatchID", data)
        # NumberFailedCases and FailedAccounts are optional in the schema but we always emit them.
        self.assertIn("NumberFailedCases", data)
        self.assertIn("FailedAccounts", data)
        self.assertEqual(data["RequestID"], PV_REQUEST_ID_2)
        self.assertEqual(data["Source_BatchID"], PV_BATCH_ID_2)
        # Records are PENDING (Celery hasn't run) → 0 failed cases
        self.assertEqual(data["NumberFailedCases"], 0)
        self.assertIsInstance(data["FailedAccounts"], list)

    # B4 — Missing SourceBBID → HTTP 200, ResponseCode "01"
    def test_b4_missing_source_bb_id(self):
        body = {
            "BatchID": PV_BATCH_ID_1,
            "CreditInstructions": [{
                "InstructionID": PV_INSTR_ID_1,
                "PayeeFunctionalID": PV_PAYEE_ID_1,
                "Amount": 100,
                "Currency": "USD",
                "Narration": "Narration",
            }],
        }
        status, data = self._post(body)
        self.assertEqual(status, 200)          # ALWAYS 200 for prepayment-validation
        self.assertEqual(data["ResponseCode"], "01")
        self.assertTrue(len(data["ResponseDescription"]) >= 1)

    # B5 — Missing BatchID → HTTP 200, ResponseCode "01"
    def test_b5_missing_batch_id(self):
        body = {
            "SourceBBID": PV_SOURCE_BB_1,
            "CreditInstructions": [{
                "InstructionID": PV_INSTR_ID_1,
                "PayeeFunctionalID": PV_PAYEE_ID_1,
                "Amount": 100,
                "Currency": "USD",
                "Narration": "Narration",
            }],
        }
        status, data = self._post(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["ResponseCode"], "01")

    # B6 — Missing CreditInstructions → HTTP 200, ResponseCode "01"
    def test_b6_missing_credit_instructions(self):
        body = {
            "RequestID": PV_REQUEST_ID_1,
            "SourceBBID": PV_SOURCE_BB_1,
            "BatchID": PV_BATCH_ID_1,
        }
        status, data = self._post(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["ResponseCode"], "01")

    # B7 — Missing InstructionID in instruction → HTTP 200, ResponseCode "01"
    def test_b7_missing_instruction_id(self):
        body = _prepay_body()
        del body["CreditInstructions"][0]["InstructionID"]
        status, data = self._post(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["ResponseCode"], "01")

    # B8 — Missing PayeeFunctionalID → HTTP 200, ResponseCode "01"
    def test_b8_missing_payee_functional_id(self):
        body = _prepay_body()
        del body["CreditInstructions"][0]["PayeeFunctionalID"]
        status, data = self._post(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["ResponseCode"], "01")

    # B9 — Missing Amount → HTTP 200, ResponseCode "01"
    def test_b9_missing_amount(self):
        body = _prepay_body()
        del body["CreditInstructions"][0]["Amount"]
        status, data = self._post(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["ResponseCode"], "01")

    # B10 — Missing Currency → HTTP 200, ResponseCode "01"
    def test_b10_missing_currency(self):
        body = _prepay_body()
        del body["CreditInstructions"][0]["Currency"]
        status, data = self._post(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["ResponseCode"], "01")

    # B11 — Missing Narration (required for prepayment, optional for bulk)
    def test_b11_missing_narration_returns_200_response_code_01(self):
        body = {
            "RequestID": PV_REQUEST_ID_1,
            "SourceBBID": PV_SOURCE_BB_1,
            "BatchID": PV_BATCH_ID_1,
            "CreditInstructions": [{
                "InstructionID": PV_INSTR_ID_1,
                "PayeeFunctionalID": PV_PAYEE_ID_1,
                "Amount": 100,
                "Currency": "USD",
                # Narration intentionally omitted
            }],
        }
        status, data = self._post(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["ResponseCode"], "01")
        self.assertIn("Narration", data["ResponseDescription"])

    # B12 — "Invalid SourceBBID" (harness sends partial body → missing other fields)
    def test_b12_invalid_source_bb_id_partial_body(self):
        # Harness sends only SourceBBID (partial body) — missing BatchID, CreditInstructions
        body = {"SourceBBID": "sourceBBID"}   # 10 chars, passes _validate_bb_id
        status, data = self._post(body)
        # Failure is due to MISSING required fields, not invalid SourceBBID value
        self.assertEqual(status, 200)
        self.assertEqual(data["ResponseCode"], "01")

    # B13 — "Invalid BatchID" partial body
    def test_b13_invalid_batch_id_partial_body(self):
        body = {"BatchID": "batchID"}   # 7 chars, passes _validate_bb_id
        status, data = self._post(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["ResponseCode"], "01")

    # B14 — Invalid Amount "100.10.1" (unparseable Decimal)
    def test_b14_invalid_amount_string(self):
        body = _prepay_body(amount="100.10.1")  # type: ignore[arg-type]
        status, data = self._post(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["ResponseCode"], "01")

    # B15 — Invalid Currency "US" (2 chars, fails ISO 4217 _validate_iso4217)
    def test_b15_invalid_currency_two_chars(self):
        body = _prepay_body(currency="US")
        status, data = self._post(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["ResponseCode"], "01")


# ============================================================================
# C.  G2P envelope invariants
# ============================================================================

class G2PEnvelopeInvariantsTest(TestCase):
    """Verify G2P response envelope structure and security constraints."""

    def setUp(self):
        self.client = APIClient()

    # C1 — BulkPayment success has all three required keys
    def test_c1_bulk_payment_success_has_all_keys(self):
        resp = self.client.post(BULK_PAYMENT_URL, data=_bulk_body(), format="json")
        data = resp.json()
        for key in ("ResponseCode", "RequestID", "ResponseDescription"):
            self.assertIn(key, data, msg=f"Key '{key}' missing from BulkPayment success response")

    # C2 — BulkPayment error envelope has all three required keys (HTTP 400)
    def test_c2_bulk_payment_error_has_all_keys(self):
        resp = self.client.post(
            BULK_PAYMENT_URL,
            data={"BatchID": BP_BATCH_ID_1},  # missing SourceBBID
            format="json",
        )
        data = resp.json()
        self.assertEqual(resp.status_code, 400)
        for key in ("ResponseCode", "RequestID", "ResponseDescription"):
            self.assertIn(key, data, msg=f"Key '{key}' missing from BulkPayment error response")

    # C3 — PrepaymentValidation error is always HTTP 200, ResponseCode "01"
    def test_c3_prepayment_error_is_always_200(self):
        resp = self.client.post(
            PREPAY_VALIDATION_URL,
            data={},   # empty body — all required fields missing
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["ResponseCode"], "01")

    # C4 — ResponseDescription is never empty (g2pResponseSchema minLength: 1)
    def test_c4_response_description_never_empty(self):
        cases = [
            (BULK_PAYMENT_URL, _bulk_body()),
            (BULK_PAYMENT_URL, {"SourceBBID": INVALID_ID, "BatchID": BP_BATCH_ID_2, "CreditInstructions": []}),
        ]
        for url, body in cases:
            with self.subTest(url=url):
                resp = self.client.post(url, data=body, format="json")
                desc = resp.json().get("ResponseDescription", "")
                self.assertGreater(len(desc), 0, msg=f"ResponseDescription is empty for {url}")

    # C5 — RequestID is echoed verbatim
    def test_c5_request_id_echoed(self):
        resp = self.client.post(BULK_PAYMENT_URL, data=_bulk_body(), format="json")
        self.assertEqual(resp.json()["RequestID"], BP_REQUEST_ID_1)

    # C6 — PayeeFunctionalID NEVER appears in any response field
    def test_c6_payee_functional_id_never_in_response(self):
        resp = self.client.post(BULK_PAYMENT_URL, data=_bulk_body(), format="json")
        raw = resp.content.decode()
        self.assertNotIn(BP_PAYEE_ID_1, raw,
                         msg="PayeeFunctionalID appeared in BulkPayment response body")

        resp2 = self.client.post(PREPAY_VALIDATION_URL, data=_prepay_body(), format="json")
        raw2 = resp2.content.decode()
        self.assertNotIn(PV_PAYEE_ID_1, raw2,
                         msg="PayeeFunctionalID appeared in PrepaymentValidation response body")

    # C7 — PrepaymentValidationResponse always returns HTTP 200
    def test_c7_prepayment_response_always_200(self):
        # Even with empty body, this endpoint returns 200
        resp = self.client.post(PREPAY_RESPONSE_URL, data={}, format="json")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("RequestID", data)
        self.assertIn("Source_BatchID", data)


# ============================================================================
# D.  GovStackBulkPaymentService — service layer
# ============================================================================

class BulkPaymentServiceTest(TestCase):
    """Unit tests for GovStackBulkPaymentService.receive_batch() and friends."""

    _INSTRUCTIONS = [
        {
            "InstructionID": BP_INSTR_ID_1,
            "PayeeFunctionalID": BP_PAYEE_ID_1,
            "Amount": Decimal("100.00"),
            "Currency": "USD",
            "Narration": "welfare payment",
        }
    ]

    def _receive(self, **kwargs) -> BulkPaymentBatch:
        defaults = dict(
            request_id=BP_REQUEST_ID_1,
            source_bb_id=BP_SOURCE_BB_1,
            batch_id=BP_BATCH_ID_1,
            instructions=self._INSTRUCTIONS,
        )
        defaults.update(kwargs)
        return GovStackBulkPaymentService.receive_batch(**defaults)

    # D1 — receive_batch() creates a BulkPaymentBatch record
    def test_d1_creates_batch_record(self):
        batch = self._receive()
        self.assertIsNotNone(batch.pk)
        self.assertTrue(BulkPaymentBatch.objects.filter(pk=batch.pk).exists())

    # D2 — receive_batch() sets status = STATUS_RECEIVED
    def test_d2_initial_status_is_received(self):
        batch = self._receive()
        self.assertEqual(batch.status, BulkPaymentBatch.STATUS_RECEIVED)

    # D3 — receive_batch() creates one CreditInstruction per item
    def test_d3_creates_credit_instructions(self):
        instructions = [
            {"InstructionID": "inst1", "PayeeFunctionalID": "payee001", "Amount": Decimal("50.00"), "Currency": "USD"},
            {"InstructionID": "inst2", "PayeeFunctionalID": "payee002", "Amount": Decimal("75.00"), "Currency": "USD"},
        ]
        batch = self._receive(
            batch_id="BatchID99999",
            instructions=instructions,
        )
        self.assertEqual(CreditInstruction.objects.filter(batch=batch).count(), 2)

    # D4 — receive_batch() computes total_amount correctly
    def test_d4_total_amount_computed(self):
        instructions = [
            {"InstructionID": "inst1", "PayeeFunctionalID": "payee001", "Amount": Decimal("100.00"), "Currency": "USD"},
            {"InstructionID": "inst2", "PayeeFunctionalID": "payee002", "Amount": Decimal("250.50"), "Currency": "USD"},
        ]
        batch = self._receive(
            batch_id="BatchIDtotal",
            instructions=instructions,
        )
        self.assertEqual(batch.total_amount, Decimal("350.50"))

    # D5 — receive_batch() stores correlation_id
    def test_d5_stores_correlation_id(self):
        batch = self._receive(correlation_id="corr-abc-123")
        self.assertEqual(batch.correlation_id, "corr-abc-123")

    # D6 — receive_batch() stores callback_url
    def test_d6_stores_callback_url(self):
        batch = self._receive(callback_url="https://sourcebb.example.com/callback")
        self.assertEqual(batch.callback_url, "https://sourcebb.example.com/callback")

    # D7 — receive_batch() creates an ACTION_BATCH_RECEIVED audit entry
    def test_d7_creates_audit_entry(self):
        batch = self._receive()
        entry = GovStackPaymentAuditEntry.objects.get(
            action=GovStackPaymentAuditEntry.ACTION_BATCH_RECEIVED,
            object_pk=str(batch.pk),
        )
        self.assertEqual(entry.actor_bb_id, BP_SOURCE_BB_1)
        self.assertEqual(entry.object_type, "batch")
        self.assertEqual(entry.request_id, BP_REQUEST_ID_1)

    # D8 — receive_batch() audit details NEVER contain payee_functional_id
    def test_d8_audit_details_no_pii(self):
        batch = self._receive()
        entry = GovStackPaymentAuditEntry.objects.get(
            action=GovStackPaymentAuditEntry.ACTION_BATCH_RECEIVED,
            object_pk=str(batch.pk),
        )
        details = entry.details
        self.assertNotIn("payee_functional_id", details,
                         msg="payee_functional_id must NEVER appear in audit details")
        # Verify it's not hidden in any nested value either
        raw = json.dumps(details)
        self.assertNotIn(BP_PAYEE_ID_1, raw,
                         msg="PayeeFunctionalID value must not appear in audit details")


class PrepaymentValidationServiceTest(TestCase):
    """Unit tests for GovStackBulkPaymentService.validate_prepayment() and get_validation_result()."""

    def _validate(self, **kwargs) -> PrepaymentValidationRequest:
        defaults = dict(
            request_id=PV_REQUEST_ID_1,
            source_bb_id=PV_SOURCE_BB_1,
            batch_id=PV_BATCH_ID_1,
            instruction_id=PV_INSTR_ID_1,
            payee_functional_id=PV_PAYEE_ID_1,
            amount=Decimal("100.00"),
            currency="USD",
            narration="Narration",
        )
        defaults.update(kwargs)
        return GovStackBulkPaymentService.validate_prepayment(**defaults)

    # D9 — validate_prepayment() creates a PrepaymentValidationRequest (status=PENDING)
    def test_d9_creates_pvr_pending(self):
        pvr = self._validate()
        self.assertIsNotNone(pvr.pk)
        self.assertEqual(pvr.status, PrepaymentValidationRequest.STATUS_PENDING)
        self.assertTrue(PrepaymentValidationRequest.objects.filter(pk=pvr.pk).exists())

    # D10 — validate_prepayment() stores all instruction fields
    def test_d10_stores_instruction_fields(self):
        pvr = self._validate(
            instruction_id="instr-xyz",
            amount=Decimal("250.00"),
            currency="EUR",
            narration="school fees",
        )
        db = PrepaymentValidationRequest.objects.get(pk=pvr.pk)
        self.assertEqual(db.source_bb_id, PV_SOURCE_BB_1)
        self.assertEqual(db.batch_id, PV_BATCH_ID_1)
        self.assertEqual(db.instruction_id, "instr-xyz")
        self.assertEqual(db.amount, Decimal("250.00"))
        self.assertEqual(db.currency, "EUR")
        self.assertEqual(db.narration, "school fees")

    # D11 — validate_prepayment() beneficiary_found is null (pending, not yet checked)
    def test_d11_beneficiary_found_is_null_when_pending(self):
        pvr = self._validate()
        self.assertIsNone(pvr.beneficiary_found)
        self.assertIsNone(pvr.financial_address_valid)

    # D12 — validate_prepayment() creates ACTION_VALIDATION_REQUESTED audit entry
    def test_d12_creates_audit_entry(self):
        pvr = self._validate()
        entry = GovStackPaymentAuditEntry.objects.get(
            action=GovStackPaymentAuditEntry.ACTION_VALIDATION_REQUESTED,
            object_pk=str(pvr.pk),
        )
        self.assertEqual(entry.actor_bb_id, PV_SOURCE_BB_1)
        self.assertEqual(entry.object_type, "validation")
        self.assertEqual(entry.request_id, PV_REQUEST_ID_1)

    # D13 — validate_prepayment() audit details NEVER contain payee_functional_id
    def test_d13_audit_details_no_pii(self):
        pvr = self._validate()
        entry = GovStackPaymentAuditEntry.objects.get(
            action=GovStackPaymentAuditEntry.ACTION_VALIDATION_REQUESTED,
            object_pk=str(pvr.pk),
        )
        details = entry.details
        self.assertNotIn("payee_functional_id", details,
                         msg="payee_functional_id must NEVER appear in audit details")
        raw = json.dumps(details)
        self.assertNotIn(PV_PAYEE_ID_1, raw,
                         msg="PayeeFunctionalID value must not appear in audit details")

    # D14 — get_validation_result() returns 0 failed cases for PENDING records
    def test_d14_pending_records_are_not_counted_as_failures(self):
        self._validate(request_id=PV_REQUEST_ID_2, batch_id=PV_BATCH_ID_2)
        result = GovStackBulkPaymentService.get_validation_result(
            request_id=PV_REQUEST_ID_2,
            source_batch_id=PV_BATCH_ID_2,
        )
        self.assertEqual(result["number_failed_cases"], 0)
        self.assertEqual(result["failed_accounts"], [])

    # D15 — get_validation_result() falls back to batch_id when request_id not found
    def test_d15_batch_id_fallback(self):
        # Store a record with a known batch_id
        self._validate(
            request_id="req-fallback1",
            batch_id="batch-fallback1",
        )
        # Look up by a DIFFERENT request_id but same batch_id
        result = GovStackBulkPaymentService.get_validation_result(
            request_id="req-nonexistent",
            source_batch_id="batch-fallback1",
        )
        # batch_id fallback should find the record
        self.assertIsNotNone(result)
        # PENDING record → 0 failures
        self.assertEqual(result["number_failed_cases"], 0)

    # D16 — get_validation_result() counts COMPLETED records with beneficiary_found=False
    def test_d16_completed_failed_record_appears_in_failed_accounts(self):
        """
        Security: FailedAccounts must use InstructionID, NEVER PayeeFunctionalID.
        This test validates the actual failure path of get_validation_result(),
        which was entirely untested before this fix (all prior tests used PENDING records).
        """
        pvr = self._validate(
            request_id="req-comp-001",
            batch_id="batch-comp-001",
            instruction_id=PV_INSTR_ID_2,
            payee_functional_id=PV_PAYEE_ID_2,
        )
        # Simulate Celery task completing with a failed beneficiary lookup.
        pvr.status = PrepaymentValidationRequest.STATUS_COMPLETED
        pvr.beneficiary_found = False
        pvr.financial_address_valid = None
        pvr.save()

        result = GovStackBulkPaymentService.get_validation_result(
            request_id="req-comp-001",
            source_batch_id="batch-comp-001",
        )

        self.assertEqual(result["number_failed_cases"], 1)
        self.assertEqual(len(result["failed_accounts"]), 1)
        failed = result["failed_accounts"][0]

        # Must use InstructionID — NEVER PayeeFunctionalID
        self.assertEqual(failed["InstructionID"], PV_INSTR_ID_2)
        self.assertIn("FailureReason", failed)
        self.assertNotIn("PayeeFunctionalID", failed,
                         msg="PayeeFunctionalID must NEVER appear in FailedAccounts")
        # PV_PAYEE_ID_2 must not appear anywhere in the result
        raw = json.dumps(result)
        self.assertNotIn(PV_PAYEE_ID_2, raw,
                         msg="PayeeFunctionalID value must never appear in validation result")

    # D17 — get_validation_result() counts COMPLETED records with financial_address_valid=False
    def test_d17_invalid_financial_address_appears_in_failed_accounts(self):
        """
        Second failure mode: beneficiary exists (beneficiary_found=True) but has no
        valid financial address.
        """
        pvr = self._validate(
            request_id="req-comp-002",
            batch_id="batch-comp-002",
            instruction_id="instrFinAddr001",
        )
        pvr.status = PrepaymentValidationRequest.STATUS_COMPLETED
        pvr.beneficiary_found = True
        pvr.financial_address_valid = False
        pvr.save()

        result = GovStackBulkPaymentService.get_validation_result(
            request_id="req-comp-002",
            source_batch_id="batch-comp-002",
        )

        self.assertEqual(result["number_failed_cases"], 1)
        failed = result["failed_accounts"][0]
        self.assertEqual(failed["InstructionID"], "instrFinAddr001")
        self.assertIn("financial address", failed["FailureReason"].lower())

    # D18 — get_validation_result() does NOT count COMPLETED + both=True as failure
    def test_d18_completed_valid_record_not_counted_as_failure(self):
        """A record that passed validation must not appear in FailedAccounts."""
        pvr = self._validate(
            request_id="req-comp-003",
            batch_id="batch-comp-003",
            instruction_id="instrOK001",
        )
        pvr.status = PrepaymentValidationRequest.STATUS_COMPLETED
        pvr.beneficiary_found = True
        pvr.financial_address_valid = True
        pvr.save()

        result = GovStackBulkPaymentService.get_validation_result(
            request_id="req-comp-003",
            source_batch_id="batch-comp-003",
        )

        self.assertEqual(result["number_failed_cases"], 0)
        self.assertEqual(result["failed_accounts"], [])


# ============================================================================
# F.  Critical bug regression tests (post-review fixes)
# ============================================================================

class BulkPaymentCriticalFixTests(TestCase):
    """
    Regression tests for the two critical bugs fixed after the Wave 3 code review:
      F1: correlation_id max_length=100 (was 12 — caused DataError on UUID-format IDs)
      F2: duplicate BatchID handled gracefully (was IntegrityError 500, now G2P 400)
      F3: multiple CreditInstructions in prepayment-validation rejected cleanly
      F4: long X-CorrelationID header accepted (view → service → DB with no truncation)
    """

    def setUp(self):
        self.client = APIClient()

    # F1 — correlation_id accepts UUID-format values (36 chars)
    def test_f1_uuid_correlation_id_accepted(self):
        """
        BulkPaymentBatch.correlation_id was max_length=12.  A standard UUID (36 chars)
        would cause DataError at the DB level.  After the fix (max_length=100), this
        must persist without error.
        """
        uuid_corr_id = "550e8400-e29b-41d4-a716-446655440000"  # 36 chars
        self.assertEqual(len(uuid_corr_id), 36)

        resp = self.client.post(
            BULK_PAYMENT_URL,
            data=_bulk_body(
                request_id=BP_REQUEST_ID_1,
                source_bb=BP_SOURCE_BB_1,
                batch_id="BatchIDcorrF1",   # unique for this test
            ),
            HTTP_X_CORRELATIONID=uuid_corr_id,
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["ResponseCode"], "00")

        # Verify the full value was persisted without truncation
        batch = BulkPaymentBatch.objects.get(batch_id="BatchIDcorrF1")
        self.assertEqual(batch.correlation_id, uuid_corr_id)

    # F2 — duplicate BatchID returns G2P 400 envelope, not 500
    def test_f2_duplicate_batch_id_returns_g2p_400(self):
        """
        Before the fix: second request with same BatchID raised IntegrityError → 500.
        After the fix: DuplicateBatchError caught in view → HTTP 400, ResponseCode "01".
        """
        body = _bulk_body(
            request_id=BP_REQUEST_ID_1,
            source_bb=BP_SOURCE_BB_1,
            batch_id="BatchIDdupF2",
        )

        # First request — succeeds
        resp1 = self.client.post(BULK_PAYMENT_URL, data=body, format="json")
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp1.json()["ResponseCode"], "00")

        # Second request with identical BatchID — must be a G2P error, not 500
        resp2 = self.client.post(BULK_PAYMENT_URL, data=body, format="json")
        self.assertEqual(resp2.status_code, 400)
        data2 = resp2.json()
        # Must still be a valid G2P envelope
        self.assertEqual(data2["ResponseCode"], "01")
        self.assertIn("ResponseDescription", data2)
        self.assertTrue(len(data2["ResponseDescription"]) >= 1)
        # BatchID must NOT appear in the error response (PII-adjacent)
        raw = resp2.content.decode()
        self.assertNotIn("BatchIDdupF2", raw)

    # F2b — DuplicateBatchError raised by service directly
    def test_f2b_service_raises_duplicate_batch_error(self):
        """Service raises DuplicateBatchError (not IntegrityError) on duplicate batch_id."""
        instructions = [{
            "InstructionID": BP_INSTR_ID_1,
            "PayeeFunctionalID": BP_PAYEE_ID_1,
            "Amount": Decimal("100.00"),
            "Currency": "USD",
        }]
        GovStackBulkPaymentService.receive_batch(
            request_id=BP_REQUEST_ID_1,
            source_bb_id=BP_SOURCE_BB_1,
            batch_id="BatchIDsvcdup1",
            instructions=instructions,
        )
        with self.assertRaises(DuplicateBatchError) as ctx:
            GovStackBulkPaymentService.receive_batch(
                request_id=BP_REQUEST_ID_2,
                source_bb_id=BP_SOURCE_BB_2,
                batch_id="BatchIDsvcdup1",   # same batch_id
                instructions=instructions,
            )
        self.assertIn("BatchIDsvcdup1", str(ctx.exception))

    # F3 — multiple CreditInstructions in prepayment-validation → HTTP 200, ResponseCode "01"
    def test_f3_multiple_credit_instructions_rejected(self):
        """
        PrepaymentValidationView now rejects > 1 CreditInstruction with a clean error
        rather than silently processing only the first.
        """
        body = {
            "RequestID": PV_REQUEST_ID_1,
            "SourceBBID": PV_SOURCE_BB_1,
            "BatchID": PV_BATCH_ID_1,
            "CreditInstructions": [
                {
                    "InstructionID": PV_INSTR_ID_1,
                    "PayeeFunctionalID": PV_PAYEE_ID_1,
                    "Amount": 100,
                    "Currency": "USD",
                    "Narration": "first",
                },
                {
                    "InstructionID": PV_INSTR_ID_2,
                    "PayeeFunctionalID": PV_PAYEE_ID_2,
                    "Amount": 200,
                    "Currency": "USD",
                    "Narration": "second",
                },
            ],
        }
        resp = self.client.post(PREPAY_VALIDATION_URL, data=body, format="json")
        # Prepayment-validation ALWAYS returns HTTP 200
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["ResponseCode"], "01")
        self.assertIn("exactly one", data["ResponseDescription"])
        # Confirm no PrepaymentValidationRequest was created (both instructions rejected)
        self.assertFalse(
            PrepaymentValidationRequest.objects.filter(
                request_id=PV_REQUEST_ID_1
            ).exists()
        )

    # F4 — long correlation ID (100 chars) is accepted end-to-end
    def test_f4_100_char_correlation_id_accepted(self):
        """Max boundary test: a 100-char correlation ID must persist without truncation."""
        long_corr = "x" * 100
        resp = self.client.post(
            BULK_PAYMENT_URL,
            data=_bulk_body(
                request_id=BP_REQUEST_ID_2,
                source_bb=BP_SOURCE_BB_2,
                batch_id="BatchIDcorrF4",
            ),
            HTTP_X_CORRELATIONID=long_corr,
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        batch = BulkPaymentBatch.objects.get(batch_id="BatchIDcorrF4")
        self.assertEqual(batch.correlation_id, long_corr)
        self.assertEqual(len(batch.correlation_id), 100)


# ============================================================================
# E.  Serializer tests
# ============================================================================

class BulkPaymentSerializerTest(TestCase):
    """Tests for BulkPaymentRequestSerializer field constraints."""

    def _valid_instr(self):
        return {
            "InstructionID": BP_INSTR_ID_1,
            "PayeeFunctionalID": BP_PAYEE_ID_1,
            "Amount": "100.00",
            "Currency": "USD",
        }

    def _ser(self, **kwargs) -> BulkPaymentRequestSerializer:
        data = {
            "RequestID": BP_REQUEST_ID_1,
            "SourceBBID": BP_SOURCE_BB_1,
            "BatchID": BP_BATCH_ID_1,
            "CreditInstructions": [self._valid_instr()],
        }
        data.update(kwargs)
        return BulkPaymentRequestSerializer(data=data)

    # E1 — Accepts valid harness SourceBBID (12 chars, mixed case)
    def test_e1_accepts_valid_source_bb_id(self):
        ser = self._ser(SourceBBID=BP_SOURCE_BB_1)   # "SourceBBID11" = 12 chars
        self.assertTrue(ser.is_valid(), ser.errors)

    # E2 — Rejects "invalid" SourceBBID (7 chars, fails min_length=10)
    def test_e2_rejects_invalid_source_bb_id(self):
        ser = self._ser(SourceBBID=INVALID_ID)
        self.assertFalse(ser.is_valid())
        self.assertIn("SourceBBID", ser.errors)

    # E3 — Accepts valid harness BatchID (12 chars)
    def test_e3_accepts_valid_batch_id(self):
        ser = self._ser(BatchID=BP_BATCH_ID_1)   # "BatchID11111" = 12 chars
        self.assertTrue(ser.is_valid(), ser.errors)

    # E4 — Rejects "invalid" BatchID (7 chars, fails min_length=10)
    def test_e4_rejects_invalid_batch_id(self):
        ser = self._ser(BatchID=INVALID_ID)
        self.assertFalse(ser.is_valid())
        self.assertIn("BatchID", ser.errors)

    # E5 — Rejects empty CreditInstructions array
    def test_e5_rejects_empty_credit_instructions(self):
        ser = self._ser(CreditInstructions=[])
        self.assertFalse(ser.is_valid())
        self.assertIn("CreditInstructions", ser.errors)

    # Also verify 10-char boundary: exactly 10 chars should be accepted
    def test_e1b_accepts_ten_char_source_bb_id(self):
        ser = self._ser(SourceBBID="aBcD123456")   # exactly 10 chars
        self.assertTrue(ser.is_valid(), ser.errors)

    # 9 chars should be rejected
    def test_e2b_rejects_nine_char_source_bb_id(self):
        ser = self._ser(SourceBBID="aBcD12345")    # 9 chars
        self.assertFalse(ser.is_valid())
        self.assertIn("SourceBBID", ser.errors)


class PrepaymentSerializerTest(TestCase):
    """Tests for PrepaymentCreditInstructionSerializer and PrepaymentValidationResponseAckSerializer."""

    # E6 — PrepaymentCreditInstructionSerializer: Narration IS required
    def test_e6_prepayment_narration_is_required(self):
        ser = PrepaymentCreditInstructionSerializer(data={
            "InstructionID": PV_INSTR_ID_1,
            "PayeeFunctionalID": PV_PAYEE_ID_1,
            "Amount": "100.00",
            "Currency": "USD",
            # Narration omitted
        })
        self.assertFalse(ser.is_valid())
        self.assertIn("Narration", ser.errors)

    # E7 — CreditInstructionSerializer (used by bulk): Narration is optional
    def test_e7_bulk_narration_is_optional(self):
        ser = CreditInstructionSerializer(data={
            "InstructionID": BP_INSTR_ID_1,
            "PayeeFunctionalID": BP_PAYEE_ID_1,
            "Amount": "100.00",
            "Currency": "USD",
            # Narration omitted — should still be valid
        })
        self.assertTrue(ser.is_valid(), ser.errors)
        self.assertEqual(ser.validated_data["Narration"], "")

    # E8 — PrepaymentValidationResponseAckSerializer: field is Source_BatchID (with underscore)
    def test_e8_ack_serializer_has_source_underscore_batch_id(self):
        ser = PrepaymentValidationResponseAckSerializer(data={
            "RequestID": PV_REQUEST_ID_2,
            "Source_BatchID": PV_BATCH_ID_2,   # correct: underscore
        })
        self.assertTrue(ser.is_valid(), ser.errors)
        # The validated data contains Source_BatchID
        self.assertEqual(ser.validated_data["Source_BatchID"], PV_BATCH_ID_2)

    # E9 — SourceBatchID (no underscore) is not recognised — silently drops
    def test_e9_ack_serializer_ignores_source_batch_id_no_underscore(self):
        ser = PrepaymentValidationResponseAckSerializer(data={
            "RequestID": PV_REQUEST_ID_2,
            "SourceBatchID": PV_BATCH_ID_2,   # wrong: no underscore
        })
        # Serializer is still valid (Source_BatchID is optional, defaults to "")
        self.assertTrue(ser.is_valid(), ser.errors)
        # But Source_BatchID defaults to "" because the key didn't match
        self.assertEqual(ser.validated_data.get("Source_BatchID", ""), "")
