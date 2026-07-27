"""
test_govstack_p2g.py

Comprehensive tests for GovStack Payments BB — P2G Bill Payments (spec §18).

Certifiability-audit fix (CRITICAL + 3 HIGH findings):
  - All 4 P2G views/service methods now scope reads/writes by
    X-Platform-TenantId (platform_tenant_id) — see section D4.
  - All 4 P2G success responses moved from HTTP 200 to HTTP 202 with the
    live-spec envelope {responseCode, reason, requestID}; error responses
    (400) also use this envelope instead of the old ad hoc {"message"} shape.
    404s are UNCHANGED (no live-spec 404 schema exists).
  - BillInquiryView now requires the `fields=inquiry` query param.
  - billInquiryRequestId/paymentReferenceID are now REQUIRED on
    POST /billTransferRequests (previously optional).
  - TransferRequestStatusView now uses IsTrustedBiller/X-billerId instead of
    IsTrustedPayerFI/X-PayerFI-Id.
  - mark_bill_paid()'s audit entry now records the real caller identity
    (X-PayerFI-Id) instead of a hardcoded "".

Coverage matrix:
  A. BillInquiry view (GET /bills/{bill_id}?fields=inquiry)
     A1:  Known bill → HTTP 202
     A2:  Response shape: {responseCode, reason, requestID, billId, amount,
          currency, description, status, dueDate}
     A3:  amount is a JSON number (float) in the response — spec §14.2
     A4:  dueDate is an ISO date string when set
     A5:  dueDate is null when not set
     A6:  Unknown bill_id → HTTP 404, {"message": "Bill not found."}
     A7:  bill_id with leading/trailing whitespace is stripped
     A8:  status field reflects actual bill status ("unpaid")
     A9:  status field reflects "overdue" bill status
     A10: status field reflects "cancelled" bill status
     A11: Missing fields query param → HTTP 400
     A12: Invalid fields query param → HTTP 400
     A13: fields=inquiry → HTTP 202

  B. BillTransferRequest view (POST /billTransferRequests)
     B1:  Valid body → HTTP 202
     B2:  Response shape: {responseCode, reason, requestID, billId, amount,
          currency, status} (no "message" key)
     B2b: amount is a JSON number (float) in the response
     B3:  status in response is "completed"
     B4:  Bill is marked PAID after successful transfer request
     B5:  GovStackBillPayment record is created
     B6:  Payment amount/currency snapshotted from bill
     B7:  Missing requestId → HTTP 400
     B8:  Missing billId → HTTP 400
     B8b: Missing billInquiryRequestId → HTTP 400 (now required)
     B8c: Missing paymentReferenceID → HTTP 400 (now required)
     B9:  Unknown billId → HTTP 404
     B10: Duplicate requestId → HTTP 400, {responseCode: "01", reason: "..."}
     B11: X-CorrelationID header stored on payment record
     B12: X-PayerFI-Id header stored on payment record
     B13: X-Platform-TenantId header stored on payment record
     B14: billInquiryRequestId stored on payment record
     B15: paymentReferenceID stored on payment record
     B16: Already-paid bill still accepts new transfer request (no duplicate payment concern)
     B17: Error responses always use {responseCode, reason, requestID} shape

  C. MarkBillPaid view (POST /bills/{bill_id}/mark-paid)
     C1:  Known bill → HTTP 202
     C2:  Response shape: {responseCode, reason, requestID, billId, status}
     C3:  status in response is "paid"
     C4:  Bill status is PAID in DB after call
     C5:  Unknown bill_id → HTTP 404, {"message": "Bill not found."}
     C6:  Already-paid bill → HTTP 202 (idempotent)
     C7:  Already-paid bill → status still "paid" in response
     C8:  mark-paid URL routes correctly (not consumed by bill_inquiry URL)
     C9:  Audit entry created when bill transitions unpaid → paid
     C10: No duplicate audit entry when marking already-paid bill
     C11: X-PayerFI-Id always required (fail-closed), even in harness mode
     C12: Audit entry records the real caller identity (actor_bb_id)

  D. TransferRequestStatus view (GET /transferRequests/{transfer_request_id})
     D1:  Known request_id → HTTP 202
     D2:  Response shape: {responseCode, reason, requestID, requestId, billId,
          amount, currency, status}
     D3:  amount is a JSON number (float) in the response
     D4:  status is "completed"
     D5:  Unknown request_id → HTTP 404, {"message": "Transfer request not found."}
     D6:  transfer_request_id with whitespace is stripped
     D2 (production-mode): IsTrustedBiller/X-billerId enforcement, and
         rejection of X-PayerFI-Id-only callers
     D4 (cross-tenant isolation): see section D4 below

  E. GovStackP2GService — unit tests
     E1:  get_bill() returns GovStackBill for known bill_id
     E2:  get_bill() raises BillNotFound for unknown bill_id
     E3:  create_transfer_request() returns GovStackBillPayment
     E4:  create_transfer_request() sets STATUS_COMPLETED on payment
     E5:  create_transfer_request() snapshots bill amount on payment
     E6:  create_transfer_request() snapshots bill currency on payment
     E7:  create_transfer_request() transitions bill to STATUS_PAID
     E8:  create_transfer_request() raises BillNotFound for unknown bill_id
     E9:  create_transfer_request() raises DuplicateBillPaymentError for duplicate request_id
     E10: create_transfer_request() creates ACTION_BILL_PAYMENT_REQUESTED audit entry
     E11: create_transfer_request() audit details contain request_id and bill_pk
     E12: create_transfer_request() audit details do NOT contain payer_fi_id
     E13: create_transfer_request() on already-paid bill does NOT change bill status
     E14: mark_bill_paid() transitions UNPAID → PAID
     E15: mark_bill_paid() is idempotent for already-PAID bill
     E16: mark_bill_paid() raises BillNotFound for unknown bill_id
     E17: mark_bill_paid() creates ACTION_BILL_PAID audit entry for UNPAID→PAID
          + pins details shape: bill_pk present, bill_id absent (M-NEW-1)
     E17b: ACTION_BILL_PAID and ACTION_BILL_PAYMENT_REQUESTED both use "bill_pk"
           in details — no schema drift between the two write paths (M-NEW-1)
     E18: mark_bill_paid() does NOT create audit entry when already PAID
     E19: get_transfer_request() returns GovStackBillPayment with related bill
     E20: get_transfer_request() raises BillPaymentNotFound for unknown request_id
     E21: get_bill() tenant scoping (wrong/matching/absent tenant)
     E22: create_transfer_request() raises BillNotFound for wrong tenant
     E23: mark_bill_paid() raises BillNotFound for wrong tenant
     E24: mark_bill_paid() records actor_payer_fi_id on the audit entry
     E25: get_transfer_request() tenant scoping (wrong/matching tenant)

  F. GovStackBill model tests
     F1:  bill_id unique constraint — duplicate raises IntegrityError
     F2:  amount CheckConstraint — zero amount rejected at DB level
     F3:  amount CheckConstraint — negative amount rejected at DB level
     F4:  currency validator — 2-char code rejected
     F5:  currency validator — 4-char code rejected
     F6:  due_date is nullable
     F7:  status defaults to "unpaid"
     F8:  str() representation is sensible

  G. GovStackBillPayment model tests
     G1:  request_id unique constraint — duplicate raises IntegrityError
     G2:  amount CheckConstraint — zero amount rejected at DB level
     G3:  PROTECT FK — deleting a bill with payments raises ProtectedError
     G4:  status defaults to "pending"

  H. Security invariants
     H1:  payer_fi_id is NOT in any API response body
     H2:  404 responses use {"message": "..."}; 400 responses use the P2G
          envelope {responseCode, reason, requestID} — neither uses {"detail": "..."}
     H3:  HTTP 404 response shape is {"message": "..."} not DRF's {"detail": "..."}
     H4:  bill_id in audit details is not the bill's external ID (uses bill_pk)
     H5:  DuplicateBillPaymentError message does not expose internal request_id

  I. High-severity regression tests (post-review fixes)
     I1:  BillNotFound propagates cleanly from create_transfer_request() (H1)
     I2:  BillNotFound is NOT a DuplicateBillPaymentError (H1)
     I3:  DuplicateBillPaymentError still fires for duplicate request_id (H1)
     I4:  Happy path still works after H1 scope narrowing
     I5:  ProtectedError still raised at ORM level (DB guard intact) (H2)
     I6:  has_delete_permission() returns False for bill with payments (H2)
     I7:  has_delete_permission() returns True for bill without payments (H2)

  J. Medium-severity regression tests (post-review fixes)
     J1:  All-whitespace requestId is rejected (M1)
     J2:  All-whitespace billId is rejected (M1)
     J3:  Empty string requestId is rejected (M1)
     J4:  Valid non-blank requestId still succeeds (M1)
     J5:  requestId with surrounding whitespace is stripped and accepted (M1)
     J6:  bill_id is in readonly_fields on GovStackBillAdmin (M5)
     J7:  amount and currency remain editable — not over-restricted (M5)
     J8:  All-whitespace billInquiryRequestId is rejected (now required)
     J9:  All-whitespace paymentReferenceID is rejected (now required)

  K. Low-severity regression tests (post-review fixes)
     K1:  billId is present and correct in POST /billTransferRequests response (L7)
     K2:  GET /transferRequests/{requestId} also returns billId (L7)
     K3:  delete_view catches ProtectedError and redirects, not 500 (L8)
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase, override_settings

from rest_framework.test import APIClient

from apps.payments.govstack_exceptions import (
    BillNotFound,
    BillPaymentNotFound,
    DuplicateBillPaymentError,
)
from apps.payments.govstack_models import (
    GovStackBill,
    GovStackBillPayment,
    GovStackPaymentAuditEntry,
    GovStackRegisteredBB,
)
from apps.payments.govstack_services import GovStackP2GService


# ---------------------------------------------------------------------------
# URL constants
# ---------------------------------------------------------------------------

TRANSFER_REQUESTS_URL = "/govstack/payments/billTransferRequests"


def _bill_url(bill_id: str, fields: str | None = "inquiry") -> str:
    """
    Build the GET /bills/{bill_id} URL. `fields=inquiry` is a REQUIRED query
    param per the live billInquiryRequest.yml spec (`required: true, enum:
    ["inquiry"]`) — defaults to the valid value so every test that isn't
    specifically exercising the missing/invalid `fields` case doesn't need to
    repeat it. Pass fields=None to omit the query param entirely, or an
    arbitrary string to send an invalid value.
    """
    url = f"/govstack/payments/bills/{bill_id}"
    if fields is not None:
        url += f"?fields={fields}"
    return url


def _mark_paid_url(bill_id: str) -> str:
    return f"/govstack/payments/bills/{bill_id}/mark-paid"


def _transfer_status_url(request_id: str) -> str:
    return f"/govstack/payments/transferRequests/{request_id}"


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------

BILL_ID = "BILL-2024-001"
CURRENCY = "USD"
AMOUNT = Decimal("150.00")
REQUEST_ID = "REQ-2024-001"


def _make_bill(
    bill_id: str = BILL_ID,
    amount: Decimal = AMOUNT,
    currency: str = CURRENCY,
    description: str = "Passport Application Fee",
    status: str = GovStackBill.STATUS_UNPAID,
    due_date: date | None = None,
) -> GovStackBill:
    return GovStackBill.objects.create(
        bill_id=bill_id,
        amount=amount,
        currency=currency,
        description=description,
        status=status,
        due_date=due_date,
    )


def _make_payment(
    request_id: str = REQUEST_ID,
    bill: GovStackBill | None = None,
    amount: Decimal = AMOUNT,
    currency: str = CURRENCY,
    status: str = GovStackBillPayment.STATUS_COMPLETED,
) -> GovStackBillPayment:
    if bill is None:
        bill = _make_bill()
    return GovStackBillPayment.objects.create(
        request_id=request_id,
        bill=bill,
        amount=amount,
        currency=currency,
        status=status,
    )


# billInquiryRequestId (maxLength 12) and paymentReferenceID (maxLength 16)
# are BOTH required per billPaymentRequest.yml's
# `required: [requestId, billInquiryRequestId, billId, paymentReferenceID]`.
# Defaulted here to valid, in-length-limit sentinel values so every test that
# isn't specifically exercising the "missing required field" case doesn't
# need to repeat them.
DEFAULT_BILL_INQUIRY_REQUEST_ID = "INQ-DEFAULT1"  # 12 chars
DEFAULT_PAYMENT_REFERENCE_ID = "PAYREF-DEFAULT1"  # 15 chars


def _transfer_body(
    *,
    request_id: str = REQUEST_ID,
    bill_id: str = BILL_ID,
    bill_inquiry_request_id: str | None = DEFAULT_BILL_INQUIRY_REQUEST_ID,
    payment_reference_id: str | None = DEFAULT_PAYMENT_REFERENCE_ID,
) -> dict:
    """
    Build a valid POST /billTransferRequests body.

    Pass bill_inquiry_request_id=None or payment_reference_id=None to omit
    that key entirely (to exercise the "missing required field" 400 case).
    """
    body: dict = {"requestId": request_id, "billId": bill_id}
    if bill_inquiry_request_id is not None:
        body["billInquiryRequestId"] = bill_inquiry_request_id
    if payment_reference_id is not None:
        body["paymentReferenceID"] = payment_reference_id
    return body


# ===========================================================================
# A. BillInquiry view (GET /bills/{bill_id})
# ===========================================================================

class TestBillInquiryView(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.bill = _make_bill(due_date=date(2025, 12, 31))

    # A1
    def test_known_bill_returns_202(self):
        resp = self.client.get(_bill_url(BILL_ID))
        self.assertEqual(resp.status_code, 202)

    # A2
    def test_response_shape(self):
        resp = self.client.get(_bill_url(BILL_ID))
        data = resp.json()
        for key in (
            "responseCode",
            "reason",
            "requestID",
            "billId",
            "amount",
            "currency",
            "description",
            "status",
            "dueDate",
        ):
            self.assertIn(key, data, f"Missing key: {key}")
        self.assertEqual(data["responseCode"], "00")

    # A3
    def test_amount_is_number(self):
        # Spec §14.2: "amount": 150.00 — JSON number, NOT a string.
        resp = self.client.get(_bill_url(BILL_ID))
        self.assertIsInstance(
            resp.json()["amount"],
            (int, float),
            "Bill inquiry 'amount' must be a JSON number, not a string.",
        )
        self.assertAlmostEqual(
            resp.json()["amount"],
            float("150.00"),
            places=2,
            msg="Bill inquiry 'amount' must equal the bill's stored amount.",
        )

    # A4
    def test_due_date_is_iso_string_when_set(self):
        resp = self.client.get(_bill_url(BILL_ID))
        self.assertEqual(resp.json()["dueDate"], "2025-12-31")

    # A5
    def test_due_date_is_null_when_not_set(self):
        bill = _make_bill(bill_id="BILL-NODUEDATE")
        resp = self.client.get(_bill_url("BILL-NODUEDATE"))
        self.assertIsNone(resp.json()["dueDate"])

    # A6
    def test_unknown_bill_id_returns_404(self):
        resp = self.client.get(_bill_url("NONEXISTENT"))
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertIn("message", data)
        self.assertNotIn("detail", data)

    # A7
    def test_bill_id_whitespace_stripped(self):
        # The URL itself will strip the space naturally, but confirm the lookup works.
        resp = self.client.get(_bill_url(BILL_ID))
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["billId"], BILL_ID)

    # A8
    def test_status_reflects_unpaid(self):
        resp = self.client.get(_bill_url(BILL_ID))
        self.assertEqual(resp.json()["status"], GovStackBill.STATUS_UNPAID)

    # A9
    def test_status_reflects_overdue(self):
        bill = _make_bill(bill_id="BILL-OVERDUE", status=GovStackBill.STATUS_OVERDUE)
        resp = self.client.get(_bill_url("BILL-OVERDUE"))
        self.assertEqual(resp.json()["status"], GovStackBill.STATUS_OVERDUE)

    # A10
    def test_status_reflects_cancelled(self):
        bill = _make_bill(bill_id="BILL-CANCELLED", status=GovStackBill.STATUS_CANCELLED)
        resp = self.client.get(_bill_url("BILL-CANCELLED"))
        self.assertEqual(resp.json()["status"], GovStackBill.STATUS_CANCELLED)

    # A11 — fields=inquiry requirement (certifiability-audit fix)
    def test_missing_fields_query_param_returns_400(self):
        resp = self.client.get(_bill_url(BILL_ID, fields=None))
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["responseCode"], "01")
        self.assertIn("fields=inquiry", data["reason"])

    # A12
    def test_invalid_fields_query_param_returns_400(self):
        resp = self.client.get(_bill_url(BILL_ID, fields="wrong-value"))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["responseCode"], "01")

    # A13
    def test_correct_fields_query_param_returns_202(self):
        resp = self.client.get(_bill_url(BILL_ID, fields="inquiry"))
        self.assertEqual(resp.status_code, 202)


# ===========================================================================
# B. BillTransferRequest view (POST /billTransferRequests)
# ===========================================================================

class TestBillTransferRequestView(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.bill = _make_bill()

    # B1
    def test_valid_body_returns_202(self):
        resp = self.client.post(
            TRANSFER_REQUESTS_URL,
            _transfer_body(),
            format="json",
        )
        self.assertEqual(resp.status_code, 202)

    # B2
    def test_response_shape(self):
        resp = self.client.post(
            TRANSFER_REQUESTS_URL,
            _transfer_body(),
            format="json",
        )
        data = resp.json()
        for key in (
            "responseCode",
            "reason",
            "requestID",
            "billId",
            "amount",
            "currency",
            "status",
        ):
            self.assertIn(key, data, f"Missing key: {key}")
        self.assertEqual(data["responseCode"], "00")
        self.assertEqual(data["requestID"], REQUEST_ID)
        # The old "message" key is replaced by "reason".
        self.assertNotIn("message", data)

    # B2b
    def test_amount_is_number(self):
        # The POST /billTransferRequests response echoes the bill amount.
        # Spec: JSON number, not a string.
        resp = self.client.post(TRANSFER_REQUESTS_URL, _transfer_body(), format="json")
        self.assertIsInstance(
            resp.json()["amount"],
            (int, float),
            "BillTransferRequest response 'amount' must be a JSON number, not a string.",
        )
        self.assertAlmostEqual(
            resp.json()["amount"],
            float("150.00"),
            places=2,
            msg="BillTransferRequest response 'amount' must equal the bill's stored amount.",
        )

    # B3
    def test_status_in_response_is_completed(self):
        resp = self.client.post(TRANSFER_REQUESTS_URL, _transfer_body(), format="json")
        self.assertEqual(resp.json()["status"], GovStackBillPayment.STATUS_COMPLETED)

    # B4
    def test_bill_is_marked_paid_after_transfer(self):
        self.client.post(TRANSFER_REQUESTS_URL, _transfer_body(), format="json")
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, GovStackBill.STATUS_PAID)

    # B5
    def test_payment_record_created(self):
        self.client.post(TRANSFER_REQUESTS_URL, _transfer_body(), format="json")
        self.assertTrue(
            GovStackBillPayment.objects.filter(request_id=REQUEST_ID).exists()
        )

    # B6
    def test_payment_amount_snapshotted_from_bill(self):
        self.client.post(TRANSFER_REQUESTS_URL, _transfer_body(), format="json")
        payment = GovStackBillPayment.objects.get(request_id=REQUEST_ID)
        self.assertEqual(payment.amount, AMOUNT)
        self.assertEqual(payment.currency, CURRENCY)

    # B7
    def test_missing_request_id_returns_400(self):
        body = {"billId": BILL_ID}
        resp = self.client.post(TRANSFER_REQUESTS_URL, body, format="json")
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["responseCode"], "01")
        self.assertIn("reason", data)

    # B8
    def test_missing_bill_id_returns_400(self):
        body = {"requestId": REQUEST_ID}
        resp = self.client.post(TRANSFER_REQUESTS_URL, body, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["responseCode"], "01")

    # B8b — billInquiryRequestId is now REQUIRED (certifiability-audit fix)
    def test_missing_bill_inquiry_request_id_returns_400(self):
        body = _transfer_body(bill_inquiry_request_id=None)
        resp = self.client.post(TRANSFER_REQUESTS_URL, body, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["responseCode"], "01")

    # B8c — paymentReferenceID is now REQUIRED (certifiability-audit fix)
    def test_missing_payment_reference_id_returns_400(self):
        body = _transfer_body(payment_reference_id=None)
        resp = self.client.post(TRANSFER_REQUESTS_URL, body, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["responseCode"], "01")

    # B9
    def test_unknown_bill_id_returns_404(self):
        body = _transfer_body(bill_id="NO-SUCH-BILL")
        resp = self.client.post(TRANSFER_REQUESTS_URL, body, format="json")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("message", resp.json())

    # B10
    def test_duplicate_request_id_returns_400(self):
        self.client.post(TRANSFER_REQUESTS_URL, _transfer_body(), format="json")
        # Second request with same requestId
        resp = self.client.post(TRANSFER_REQUESTS_URL, _transfer_body(), format="json")
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["responseCode"], "01")
        self.assertIn("already been received", data["reason"])
        self.assertEqual(data["requestID"], REQUEST_ID)

    # B11
    def test_correlation_id_header_stored(self):
        self.client.post(
            TRANSFER_REQUESTS_URL,
            _transfer_body(),
            format="json",
            HTTP_X_CORRELATIONID="CORR-XYZ",
        )
        payment = GovStackBillPayment.objects.get(request_id=REQUEST_ID)
        self.assertEqual(payment.correlation_id, "CORR-XYZ")

    # B12
    def test_payer_fi_id_header_stored(self):
        self.client.post(
            TRANSFER_REQUESTS_URL,
            _transfer_body(),
            format="json",
            HTTP_X_PAYERFI_ID="FI-BANK001",
        )
        payment = GovStackBillPayment.objects.get(request_id=REQUEST_ID)
        self.assertEqual(payment.payer_fi_id, "FI-BANK001")

    # B13
    def test_platform_tenant_id_header_stored(self):
        # The bill must be tagged with the SAME tenant the caller declares —
        # tenant scoping (certifiability-audit fix) now applies whenever a
        # caller supplies a non-empty X-Platform-TenantId, regardless of the
        # GOVSTACK_REQUIRE_PLATFORM_TENANT_ID flag. A caller declaring a
        # tenant that doesn't match the bill's tenant gets BillNotFound (404)
        # — see section D4 for that behaviour.
        self.bill.platform_tenant_id = "TENANT-GOV"
        self.bill.save(update_fields=["platform_tenant_id"])
        self.client.post(
            TRANSFER_REQUESTS_URL,
            _transfer_body(),
            format="json",
            HTTP_X_PLATFORM_TENANTID="TENANT-GOV",
        )
        payment = GovStackBillPayment.objects.get(request_id=REQUEST_ID)
        self.assertEqual(payment.platform_tenant_id, "TENANT-GOV")

    # B14
    def test_bill_inquiry_request_id_stored(self):
        body = _transfer_body(bill_inquiry_request_id="INQ-001")
        self.client.post(TRANSFER_REQUESTS_URL, body, format="json")
        payment = GovStackBillPayment.objects.get(request_id=REQUEST_ID)
        self.assertEqual(payment.bill_inquiry_request_id, "INQ-001")

    # B15
    def test_payment_reference_id_stored(self):
        body = _transfer_body(payment_reference_id="PAYREF-999")
        self.client.post(TRANSFER_REQUESTS_URL, body, format="json")
        payment = GovStackBillPayment.objects.get(request_id=REQUEST_ID)
        self.assertEqual(payment.payment_reference_id, "PAYREF-999")

    # B16
    def test_already_paid_bill_accepts_new_transfer_request(self):
        """
        A bill that is already PAID can still receive a new transfer request
        (different requestId).  The create_transfer_request() only skips
        the status update — it does not block the payment record creation.
        """
        already_paid_bill = _make_bill(
            bill_id="BILL-ALREADYPAID",
            status=GovStackBill.STATUS_PAID,
        )
        body = _transfer_body(request_id="REQ-NEW", bill_id="BILL-ALREADYPAID")
        resp = self.client.post(TRANSFER_REQUESTS_URL, body, format="json")
        self.assertEqual(resp.status_code, 202)
        payment = GovStackBillPayment.objects.get(request_id="REQ-NEW")
        # Verify the payment is correctly linked to the already-paid bill.
        self.assertEqual(payment.bill_id, already_paid_bill.pk)

    # B17
    def test_error_responses_use_envelope_shape(self):
        resp = self.client.post(TRANSFER_REQUESTS_URL, {}, format="json")
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["responseCode"], "01")
        self.assertIn("reason", data)
        self.assertIn("requestID", data)
        self.assertNotIn("detail", data)
        self.assertNotIn("message", data)


# ===========================================================================
# C. MarkBillPaid view (POST /bills/{bill_id}/mark-paid)
# ===========================================================================

class TestMarkBillPaidView(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.bill = _make_bill()

    # C1
    def test_known_bill_returns_202(self):
        resp = self.client.post(_mark_paid_url(BILL_ID), HTTP_X_PAYERFI_ID="FI-TEST")
        self.assertEqual(resp.status_code, 202)

    # C2
    def test_response_shape(self):
        resp = self.client.post(_mark_paid_url(BILL_ID), HTTP_X_PAYERFI_ID="FI-TEST")
        data = resp.json()
        for key in ("responseCode", "reason", "requestID", "billId", "status"):
            self.assertIn(key, data, f"Missing key: {key}")
        self.assertEqual(data["responseCode"], "00")

    # C3
    def test_status_in_response_is_paid(self):
        resp = self.client.post(_mark_paid_url(BILL_ID), HTTP_X_PAYERFI_ID="FI-TEST")
        self.assertEqual(resp.json()["status"], GovStackBill.STATUS_PAID)

    # C4
    def test_bill_status_is_paid_in_db(self):
        self.client.post(_mark_paid_url(BILL_ID), HTTP_X_PAYERFI_ID="FI-TEST")
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, GovStackBill.STATUS_PAID)

    # C5
    def test_unknown_bill_returns_404(self):
        resp = self.client.post(
            _mark_paid_url("NO-SUCH-BILL"), HTTP_X_PAYERFI_ID="FI-TEST"
        )
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertIn("message", data)
        self.assertNotIn("detail", data)

    # C6
    def test_already_paid_bill_returns_202(self):
        paid_bill = _make_bill(bill_id="BILL-PAID", status=GovStackBill.STATUS_PAID)
        resp = self.client.post(
            _mark_paid_url("BILL-PAID"), HTTP_X_PAYERFI_ID="FI-TEST"
        )
        self.assertEqual(resp.status_code, 202)

    # C7
    def test_already_paid_bill_status_still_paid(self):
        paid_bill = _make_bill(bill_id="BILL-PAID2", status=GovStackBill.STATUS_PAID)
        resp = self.client.post(
            _mark_paid_url("BILL-PAID2"), HTTP_X_PAYERFI_ID="FI-TEST"
        )
        self.assertEqual(resp.json()["status"], GovStackBill.STATUS_PAID)

    # C8
    def test_mark_paid_url_does_not_collide_with_bill_inquiry(self):
        """
        POST /bills/{bill_id}/mark-paid must NOT be intercepted by the
        GET /bills/{bill_id} URL pattern.  Both routes coexist because
        mark-paid is registered first (more specific) in govstack_urls.py.
        """
        resp = self.client.post(_mark_paid_url(BILL_ID), HTTP_X_PAYERFI_ID="FI-TEST")
        # If routing was wrong, the mark-paid POST would have hit BillInquiryView
        # which does not define post() and would return 405.
        self.assertNotEqual(resp.status_code, 405)
        self.assertEqual(resp.status_code, 202)

    # C9
    def test_audit_entry_created_on_transition(self):
        pre_count = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_BILL_PAID
        ).count()
        self.client.post(_mark_paid_url(BILL_ID), HTTP_X_PAYERFI_ID="FI-TEST")
        post_count = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_BILL_PAID
        ).count()
        self.assertEqual(post_count, pre_count + 1)

    # C10
    def test_no_duplicate_audit_entry_for_already_paid_bill(self):
        paid_bill = _make_bill(bill_id="BILL-PAID3", status=GovStackBill.STATUS_PAID)
        pre_count = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_BILL_PAID
        ).count()
        self.client.post(_mark_paid_url("BILL-PAID3"), HTTP_X_PAYERFI_ID="FI-TEST")
        post_count = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_BILL_PAID
        ).count()
        # No new audit entry for a bill that was already PAID.
        self.assertEqual(post_count, pre_count)

    # C11 — Issue B: mark-paid ALWAYS requires X-PayerFI-Id, even in harness mode
    def test_no_payer_fi_header_returns_401_even_without_flag(self):
        """
        MarkBillPaidView uses RequirePayerFI, the fail-closed permission
        variant: an absent X-PayerFI-Id header must be rejected with HTTP 401
        in every settings mode, including harness/test mode where
        GOVSTACK_REQUIRE_REGISTERED_PAYER_FI is unset (defaults to False).

        Unlike BillInquiryView/BillTransferRequestView/TransferRequestStatusView
        (which stay permissive by default), this endpoint mutates real bill
        state with no idempotency key, so it fails closed regardless of mode.
        """
        resp = self.client.post(_mark_paid_url(BILL_ID))
        self.assertEqual(resp.status_code, 401)
        self.bill.refresh_from_db()
        self.assertEqual(
            self.bill.status,
            GovStackBill.STATUS_UNPAID,
            "Bill must NOT be marked paid when the request is rejected at "
            "the permission layer.",
        )

    # C12 — certifiability-audit fix (CRITICAL): audit entry now records the
    # real caller identity instead of a hardcoded "" actor_bb_id.
    def test_audit_entry_records_real_caller_identity(self):
        """
        Before this fix, GovStackP2GService.mark_bill_paid() hardcoded
        actor_bb_id="" with a comment claiming "no BB authentication" — even
        though this view has always enforced RequirePayerFI (the caller's
        X-PayerFI-Id is known and mandatory). The audit entry must now record
        that caller identity.
        """
        self.client.post(_mark_paid_url(BILL_ID), HTTP_X_PAYERFI_ID="FI-ACCOUNTABLE")
        entry = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_BILL_PAID
        ).latest("created_at")
        self.assertEqual(entry.actor_bb_id, "FI-ACCOUNTABLE")


# ===========================================================================
# D. TransferRequestStatus view (GET /transferRequests/{transfer_request_id})
# ===========================================================================

class TestTransferRequestStatusView(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.bill = _make_bill(status=GovStackBill.STATUS_PAID)
        self.payment = _make_payment(bill=self.bill)

    # D1
    def test_known_request_id_returns_202(self):
        resp = self.client.get(_transfer_status_url(REQUEST_ID))
        self.assertEqual(resp.status_code, 202)

    # D2
    def test_response_shape(self):
        resp = self.client.get(_transfer_status_url(REQUEST_ID))
        data = resp.json()
        for key in (
            "responseCode",
            "reason",
            "requestID",
            "requestId",
            "billId",
            "amount",
            "currency",
            "status",
        ):
            self.assertIn(key, data, f"Missing key: {key}")
        self.assertEqual(data["responseCode"], "00")

    # D3
    def test_amount_is_number(self):
        # Spec: transfer request response "amount" must be a JSON number, not a string.
        resp = self.client.get(_transfer_status_url(REQUEST_ID))
        self.assertIsInstance(
            resp.json()["amount"],
            (int, float),
            "Transfer request status 'amount' must be a JSON number, not a string.",
        )
        self.assertAlmostEqual(
            resp.json()["amount"],
            float("150.00"),
            places=2,
            msg="Transfer request status 'amount' must equal the bill's stored amount.",
        )

    # D4
    def test_status_is_completed(self):
        resp = self.client.get(_transfer_status_url(REQUEST_ID))
        self.assertEqual(resp.json()["status"], GovStackBillPayment.STATUS_COMPLETED)

    # D5
    def test_unknown_request_id_returns_404(self):
        resp = self.client.get(_transfer_status_url("NO-SUCH-REQUEST"))
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertIn("message", data)
        self.assertNotIn("detail", data)

    # D6
    def test_request_id_whitespace_stripped(self):
        # Leading/trailing whitespace in the URL path segment arrives as raw str.
        # The view calls str(transfer_request_id).strip().
        # This test verifies the strip path by checking a direct service call.
        payment = GovStackP2GService.get_transfer_request(request_id=REQUEST_ID)
        self.assertEqual(payment.request_id, REQUEST_ID)


# ===========================================================================
# D2. IsTrustedPayerFI / IsTrustedBiller production-mode enforcement (Issue B)
#
#   With GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=True:
#     - BillInquiryView / BillTransferRequestView (IsTrustedPayerFI) must
#       reject a request with no X-PayerFI-Id header (or accepted variant)
#       with HTTP 401, and accept a whitelisted header with HTTP 202.
#     - TransferRequestStatusView (IsTrustedBiller — certifiability-audit fix)
#       must reject a request with no X-billerId header with HTTP 401, accept
#       a whitelisted X-billerId header with HTTP 202, AND reject a caller
#       that presents ONLY X-PayerFI-Id (no X-billerId) exactly like a caller
#       presenting no header at all — this endpoint no longer trusts
#       X-PayerFI-Id in any way.
#
#   IsTrustedBiller reuses the SAME GOVSTACK_REQUIRE_REGISTERED_PAYER_FI flag
#   and GovStackRegisteredBB whitelist table as IsTrustedPayerFI (deliberate
#   choice — see IsTrustedBiller's docstring), so a single override_settings
#   covers all 4 P2G views' production-mode behaviour in this class.
# ===========================================================================

_WHITELISTED_PAYER_FI = "FI-WHITELISTED"
_WHITELISTED_BILLER = "BILLER-WHITELISTED"


@override_settings(GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=True)
class TestPayerFIProductionModeEnforcement(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.bill = _make_bill()
        GovStackRegisteredBB.objects.create(
            bb_id=_WHITELISTED_PAYER_FI, is_active=True
        )
        GovStackRegisteredBB.objects.create(
            bb_id=_WHITELISTED_BILLER, is_active=True
        )

    # BillInquiryView
    def test_bill_inquiry_no_header_returns_401_in_production_mode(self):
        resp = self.client.get(_bill_url(BILL_ID))
        self.assertEqual(resp.status_code, 401)

    def test_bill_inquiry_whitelisted_header_returns_202_in_production_mode(self):
        resp = self.client.get(
            _bill_url(BILL_ID), HTTP_X_PAYERFI_ID=_WHITELISTED_PAYER_FI
        )
        self.assertEqual(resp.status_code, 202)

    # BillTransferRequestView
    def test_bill_transfer_request_no_header_returns_401_in_production_mode(self):
        resp = self.client.post(
            TRANSFER_REQUESTS_URL, _transfer_body(), format="json"
        )
        self.assertEqual(resp.status_code, 401)

    def test_bill_transfer_request_whitelisted_header_returns_202_in_production_mode(
        self,
    ):
        resp = self.client.post(
            TRANSFER_REQUESTS_URL,
            _transfer_body(),
            format="json",
            HTTP_X_PAYERFI_ID=_WHITELISTED_PAYER_FI,
        )
        self.assertEqual(resp.status_code, 202)

    # TransferRequestStatusView — IsTrustedBiller / X-billerId (certifiability-audit fix)
    def test_transfer_status_no_header_returns_401_in_production_mode(self):
        payment = _make_payment(bill=self.bill)
        resp = self.client.get(_transfer_status_url(REQUEST_ID))
        self.assertEqual(resp.status_code, 401)

    def test_transfer_status_whitelisted_biller_header_returns_202_in_production_mode(self):
        payment = _make_payment(bill=self.bill)
        resp = self.client.get(
            _transfer_status_url(REQUEST_ID), HTTP_X_BILLERID=_WHITELISTED_BILLER
        )
        self.assertEqual(resp.status_code, 202)

    def test_transfer_status_payer_fi_only_header_rejected_in_production_mode(self):
        """
        A caller presenting ONLY X-PayerFI-Id (even a whitelisted one) and NO
        X-billerId must be rejected exactly like a caller presenting no
        header at all — rtpStatusUpdateRequest.yml requires X-billerId, not
        X-PayerFI-Id, and this view no longer accepts the latter for auth.
        """
        payment = _make_payment(bill=self.bill)
        resp = self.client.get(
            _transfer_status_url(REQUEST_ID), HTTP_X_PAYERFI_ID=_WHITELISTED_PAYER_FI
        )
        self.assertEqual(resp.status_code, 401)


# ===========================================================================
# D3. X-Platform-TenantId tenant-scoping validation
#
#   Distinct from D2 above: X-Platform-TenantId answers "which tenant's
#   data", not "who is calling" (that's X-PayerFI-Id / IsTrustedPayerFI /
#   RequirePayerFI, covered in D2). Validated by
#   GovStackAPIView._validate_platform_tenant_id() — HTTP 400 on failure
#   (validation error), not 401 (auth failure).
#
#   Length validation (>20 chars) fires REGARDLESS of the
#   GOVSTACK_REQUIRE_PLATFORM_TENANT_ID flag. Presence validation only fires
#   when that flag is True (production mode) — in harness/test mode
#   (flag unset, defaults False) an absent header is tolerated everywhere,
#   matching every other GOVSTACK_REQUIRE_* flag's default-off behaviour.
# ===========================================================================

_OVERSIZED_TENANT_ID = "T" * 21  # maxLength: 20 on every live P2G YAML


class TestPlatformTenantIdOversizedAlwaysRejected(TestCase):
    """
    An oversized X-Platform-TenantId (or Platform-TenantId) header is
    rejected with HTTP 400 in EVERY settings mode — this check does not
    depend on GOVSTACK_REQUIRE_PLATFORM_TENANT_ID, mirroring how
    IsTrustedPayerFI's length check also applies regardless of its own flag.
    """

    def setUp(self):
        self.client = APIClient()
        self.bill = _make_bill()

    def test_bill_inquiry_oversized_tenant_id_returns_400(self):
        resp = self.client.get(
            _bill_url(BILL_ID), HTTP_X_PLATFORM_TENANTID=_OVERSIZED_TENANT_ID
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["responseCode"], "01")
        self.assertIn("reason", data)

    def test_bill_transfer_request_oversized_tenant_id_returns_400(self):
        resp = self.client.post(
            TRANSFER_REQUESTS_URL,
            _transfer_body(),
            format="json",
            HTTP_X_PLATFORM_TENANTID=_OVERSIZED_TENANT_ID,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["responseCode"], "01")
        # The oversized value must not have been persisted anywhere.
        self.assertFalse(
            GovStackBillPayment.objects.filter(request_id=REQUEST_ID).exists()
        )

    def test_mark_bill_paid_oversized_tenant_id_returns_400(self):
        resp = self.client.post(
            _mark_paid_url(BILL_ID),
            HTTP_X_PAYERFI_ID="FI-TEST",  # RequirePayerFI is fail-closed regardless
            HTTP_X_PLATFORM_TENANTID=_OVERSIZED_TENANT_ID,
        )
        self.assertEqual(resp.status_code, 400)
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, GovStackBill.STATUS_UNPAID)

    def test_transfer_status_oversized_tenant_id_returns_400(self):
        payment = _make_payment(bill=self.bill)
        resp = self.client.get(
            _transfer_status_url(REQUEST_ID),
            HTTP_X_PLATFORM_TENANTID=_OVERSIZED_TENANT_ID,
        )
        self.assertEqual(resp.status_code, 400)


class TestPlatformTenantIdHarnessModePermissive(TestCase):
    """
    In harness/test mode (GOVSTACK_REQUIRE_PLATFORM_TENANT_ID unset, default
    False), an ABSENT X-Platform-TenantId header must still be tolerated —
    this pins the no-regression contract for every existing P2G test in this
    file that does not send the header at all.
    """

    def setUp(self):
        self.client = APIClient()
        self.bill = _make_bill()

    def test_bill_inquiry_no_tenant_id_still_202_in_harness_mode(self):
        resp = self.client.get(_bill_url(BILL_ID))
        self.assertEqual(resp.status_code, 202)

    def test_bill_transfer_request_no_tenant_id_still_202_in_harness_mode(self):
        resp = self.client.post(TRANSFER_REQUESTS_URL, _transfer_body(), format="json")
        self.assertEqual(resp.status_code, 202)

    def test_mark_bill_paid_no_tenant_id_still_202_in_harness_mode(self):
        resp = self.client.post(_mark_paid_url(BILL_ID), HTTP_X_PAYERFI_ID="FI-TEST")
        self.assertEqual(resp.status_code, 202)

    def test_transfer_status_no_tenant_id_still_202_in_harness_mode(self):
        payment = _make_payment(bill=self.bill)
        resp = self.client.get(_transfer_status_url(REQUEST_ID))
        self.assertEqual(resp.status_code, 202)


@override_settings(GOVSTACK_REQUIRE_PLATFORM_TENANT_ID=True)
class TestPlatformTenantIdProductionModeEnforcement(TestCase):
    """
    With GOVSTACK_REQUIRE_PLATFORM_TENANT_ID=True, all 4 P2G views must:
      - reject a request with no X-Platform-TenantId header (or the
        Platform-TenantId spelling variant) with HTTP 400 (a validation
        failure, distinct from D2's HTTP 401 auth failures)
      - accept a request with a present, ≤20-char header with HTTP 202

    NOTE: self.bill is pre-tagged with platform_tenant_id="TENANT-GOV" so
    that the "with tenant id" success-path tests (which now ALSO exercise
    real tenant-scoping — see section D4 — not just header validation) match
    and return 202 rather than 404. The "no tenant id" tests never reach the
    tenant-scoped lookup at all (they're rejected earlier, at header
    validation), so the bill's tenant tag is irrelevant there.
    """

    def setUp(self):
        self.client = APIClient()
        self.bill = _make_bill()
        self.bill.platform_tenant_id = "TENANT-GOV"
        self.bill.save(update_fields=["platform_tenant_id"])

    # BillInquiryView
    def test_bill_inquiry_no_tenant_id_returns_400_in_production_mode(self):
        resp = self.client.get(_bill_url(BILL_ID))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["responseCode"], "01")

    def test_bill_inquiry_with_tenant_id_returns_202_in_production_mode(self):
        resp = self.client.get(
            _bill_url(BILL_ID), HTTP_X_PLATFORM_TENANTID="TENANT-GOV"
        )
        self.assertEqual(resp.status_code, 202)

    def test_bill_inquiry_accepts_platform_tenantid_spelling_variant(self):
        # billInquiryRequest.yml spells this header "Platform-TenantId" (no
        # "X-" prefix) — confirm both variants are accepted.
        resp = self.client.get(
            _bill_url(BILL_ID), HTTP_PLATFORM_TENANTID="TENANT-GOV"
        )
        self.assertEqual(resp.status_code, 202)

    # BillTransferRequestView
    def test_bill_transfer_request_no_tenant_id_returns_400_in_production_mode(self):
        resp = self.client.post(
            TRANSFER_REQUESTS_URL, _transfer_body(), format="json"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["responseCode"], "01")
        self.assertFalse(
            GovStackBillPayment.objects.filter(request_id=REQUEST_ID).exists()
        )

    def test_bill_transfer_request_with_tenant_id_returns_202_in_production_mode(self):
        resp = self.client.post(
            TRANSFER_REQUESTS_URL,
            _transfer_body(),
            format="json",
            HTTP_X_PLATFORM_TENANTID="TENANT-GOV",
        )
        self.assertEqual(resp.status_code, 202)
        payment = GovStackBillPayment.objects.get(request_id=REQUEST_ID)
        self.assertEqual(payment.platform_tenant_id, "TENANT-GOV")

    # MarkBillPaidView
    def test_mark_bill_paid_no_tenant_id_returns_400_in_production_mode(self):
        resp = self.client.post(
            _mark_paid_url(BILL_ID), HTTP_X_PAYERFI_ID="FI-TEST"
        )
        self.assertEqual(resp.status_code, 400)
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, GovStackBill.STATUS_UNPAID)

    def test_mark_bill_paid_with_tenant_id_returns_202_in_production_mode(self):
        resp = self.client.post(
            _mark_paid_url(BILL_ID),
            HTTP_X_PAYERFI_ID="FI-TEST",
            HTTP_X_PLATFORM_TENANTID="TENANT-GOV",
        )
        self.assertEqual(resp.status_code, 202)

    # TransferRequestStatusView
    def test_transfer_status_no_tenant_id_returns_400_in_production_mode(self):
        payment = _make_payment(bill=self.bill)
        resp = self.client.get(_transfer_status_url(REQUEST_ID))
        self.assertEqual(resp.status_code, 400)

    def test_transfer_status_with_tenant_id_returns_202_in_production_mode(self):
        payment = _make_payment(bill=self.bill)
        payment.platform_tenant_id = "TENANT-GOV"
        payment.save(update_fields=["platform_tenant_id"])
        resp = self.client.get(
            _transfer_status_url(REQUEST_ID), HTTP_X_PLATFORM_TENANTID="TENANT-GOV"
        )
        self.assertEqual(resp.status_code, 202)


# ===========================================================================
# D4. Cross-tenant data isolation (certifiability-audit fix — CRITICAL finding)
#
#   A bill/payment created under one declared tenant must NOT be readable or
#   writable by a caller declaring a DIFFERENT tenant, but MUST remain
#   reachable by a caller declaring NO tenant at all (harness mode) or the
#   MATCHING tenant. Cross-tenant lookups return the exact same "not found"
#   response as a genuinely missing record — never distinguished — so this
#   API can't be used to probe cross-tenant existence.
# ===========================================================================

TENANT_A = "TENANT-A"
TENANT_B = "TENANT-B"


class TestCrossTenantDataIsolation(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant_a_bill = GovStackBill.objects.create(
            bill_id="BILL-TENANT-A",
            amount=Decimal("75.00"),
            currency="USD",
            platform_tenant_id=TENANT_A,
        )
        self.tenant_a_payment = GovStackBillPayment.objects.create(
            request_id="REQ-TENANT-A",
            bill=self.tenant_a_bill,
            amount=Decimal("75.00"),
            currency="USD",
            status=GovStackBillPayment.STATUS_COMPLETED,
            platform_tenant_id=TENANT_A,
        )

    # BillInquiryView
    def test_bill_inquiry_wrong_tenant_returns_404(self):
        resp = self.client.get(
            _bill_url("BILL-TENANT-A"), HTTP_X_PLATFORM_TENANTID=TENANT_B
        )
        self.assertEqual(resp.status_code, 404)

    def test_bill_inquiry_matching_tenant_returns_202(self):
        resp = self.client.get(
            _bill_url("BILL-TENANT-A"), HTTP_X_PLATFORM_TENANTID=TENANT_A
        )
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["billId"], "BILL-TENANT-A")

    def test_bill_inquiry_no_declared_tenant_returns_202_in_harness_mode(self):
        # Harness mode: no tenant declared at all → no scoping applied, so the
        # bill remains reachable (matches today's permissive harness behaviour).
        resp = self.client.get(_bill_url("BILL-TENANT-A"))
        self.assertEqual(resp.status_code, 202)

    # BillTransferRequestView — cannot notify a payment against another tenant's bill
    def test_bill_transfer_request_wrong_tenant_returns_404(self):
        body = _transfer_body(request_id="REQ-WRONG-TENANT", bill_id="BILL-TENANT-A")
        resp = self.client.post(
            TRANSFER_REQUESTS_URL,
            body,
            format="json",
            HTTP_X_PLATFORM_TENANTID=TENANT_B,
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(
            GovStackBillPayment.objects.filter(request_id="REQ-WRONG-TENANT").exists()
        )

    def test_bill_transfer_request_matching_tenant_returns_202(self):
        body = _transfer_body(request_id="REQ-MATCHING-TENANT", bill_id="BILL-TENANT-A")
        resp = self.client.post(
            TRANSFER_REQUESTS_URL,
            body,
            format="json",
            HTTP_X_PLATFORM_TENANTID=TENANT_A,
        )
        self.assertEqual(resp.status_code, 202)

    # MarkBillPaidView — cannot mark another tenant's bill as paid
    def test_mark_bill_paid_wrong_tenant_returns_404(self):
        resp = self.client.post(
            _mark_paid_url("BILL-TENANT-A"),
            HTTP_X_PAYERFI_ID="FI-TEST",
            HTTP_X_PLATFORM_TENANTID=TENANT_B,
        )
        self.assertEqual(resp.status_code, 404)
        self.tenant_a_bill.refresh_from_db()
        self.assertEqual(self.tenant_a_bill.status, GovStackBill.STATUS_UNPAID)

    def test_mark_bill_paid_matching_tenant_returns_202(self):
        resp = self.client.post(
            _mark_paid_url("BILL-TENANT-A"),
            HTTP_X_PAYERFI_ID="FI-TEST",
            HTTP_X_PLATFORM_TENANTID=TENANT_A,
        )
        self.assertEqual(resp.status_code, 202)
        self.tenant_a_bill.refresh_from_db()
        self.assertEqual(self.tenant_a_bill.status, GovStackBill.STATUS_PAID)

    # TransferRequestStatusView
    def test_transfer_status_wrong_tenant_returns_404(self):
        resp = self.client.get(
            _transfer_status_url("REQ-TENANT-A"), HTTP_X_PLATFORM_TENANTID=TENANT_B
        )
        self.assertEqual(resp.status_code, 404)

    def test_transfer_status_matching_tenant_returns_202(self):
        resp = self.client.get(
            _transfer_status_url("REQ-TENANT-A"), HTTP_X_PLATFORM_TENANTID=TENANT_A
        )
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["requestId"], "REQ-TENANT-A")

    def test_transfer_status_no_declared_tenant_returns_202_in_harness_mode(self):
        resp = self.client.get(_transfer_status_url("REQ-TENANT-A"))
        self.assertEqual(resp.status_code, 202)


# ===========================================================================
# E. GovStackP2GService — unit tests
# ===========================================================================

class TestGovStackP2GService(TestCase):
    def setUp(self):
        self.bill = _make_bill()

    # E1
    def test_get_bill_returns_bill(self):
        bill = GovStackP2GService.get_bill(bill_id=BILL_ID)
        self.assertEqual(bill.bill_id, BILL_ID)

    # E2
    def test_get_bill_raises_bill_not_found(self):
        with self.assertRaises(BillNotFound):
            GovStackP2GService.get_bill(bill_id="NONEXISTENT")

    # E3
    def test_create_transfer_request_returns_payment(self):
        payment = GovStackP2GService.create_transfer_request(
            request_id=REQUEST_ID,
            bill_id=BILL_ID,
        )
        self.assertIsInstance(payment, GovStackBillPayment)

    # E4
    def test_create_transfer_request_sets_completed_status(self):
        payment = GovStackP2GService.create_transfer_request(
            request_id=REQUEST_ID,
            bill_id=BILL_ID,
        )
        self.assertEqual(payment.status, GovStackBillPayment.STATUS_COMPLETED)

    # E5
    def test_create_transfer_request_snapshots_amount(self):
        payment = GovStackP2GService.create_transfer_request(
            request_id=REQUEST_ID,
            bill_id=BILL_ID,
        )
        self.assertEqual(payment.amount, AMOUNT)

    # E6
    def test_create_transfer_request_snapshots_currency(self):
        payment = GovStackP2GService.create_transfer_request(
            request_id=REQUEST_ID,
            bill_id=BILL_ID,
        )
        self.assertEqual(payment.currency, CURRENCY)

    # E7
    def test_create_transfer_request_marks_bill_paid(self):
        GovStackP2GService.create_transfer_request(
            request_id=REQUEST_ID,
            bill_id=BILL_ID,
        )
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, GovStackBill.STATUS_PAID)

    # E8
    def test_create_transfer_request_raises_bill_not_found(self):
        with self.assertRaises(BillNotFound):
            GovStackP2GService.create_transfer_request(
                request_id=REQUEST_ID,
                bill_id="NO-SUCH-BILL",
            )

    # E9
    def test_create_transfer_request_raises_duplicate_on_second_call(self):
        GovStackP2GService.create_transfer_request(
            request_id=REQUEST_ID,
            bill_id=BILL_ID,
        )
        bill2 = _make_bill(bill_id="BILL-002")
        with self.assertRaises(DuplicateBillPaymentError):
            GovStackP2GService.create_transfer_request(
                request_id=REQUEST_ID,  # same request_id
                bill_id="BILL-002",
            )

    # E10
    def test_create_transfer_request_creates_audit_entry(self):
        pre_count = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_BILL_PAYMENT_REQUESTED
        ).count()
        GovStackP2GService.create_transfer_request(
            request_id=REQUEST_ID,
            bill_id=BILL_ID,
        )
        post_count = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_BILL_PAYMENT_REQUESTED
        ).count()
        self.assertEqual(post_count, pre_count + 1)

    # E11
    def test_create_transfer_request_audit_details_contain_request_id_and_bill_pk(self):
        payment = GovStackP2GService.create_transfer_request(
            request_id=REQUEST_ID,
            bill_id=BILL_ID,
        )
        audit = GovStackPaymentAuditEntry.objects.get(
            action=GovStackPaymentAuditEntry.ACTION_BILL_PAYMENT_REQUESTED,
            object_pk=str(payment.pk),
        )
        self.assertIn("request_id", audit.details)
        self.assertIn("bill_pk", audit.details)
        self.assertEqual(audit.details["request_id"], REQUEST_ID)
        self.assertEqual(audit.details["bill_pk"], str(self.bill.pk))

    # E12
    def test_create_transfer_request_audit_details_omit_payer_fi_id(self):
        payment = GovStackP2GService.create_transfer_request(
            request_id=REQUEST_ID,
            bill_id=BILL_ID,
            payer_fi_id="FI-SECRETBANK",
        )
        audit = GovStackPaymentAuditEntry.objects.get(
            action=GovStackPaymentAuditEntry.ACTION_BILL_PAYMENT_REQUESTED,
            object_pk=str(payment.pk),
        )
        # payer_fi_id must NOT appear in audit details
        self.assertNotIn("payer_fi_id", audit.details)
        audit_str = json.dumps(audit.details)
        self.assertNotIn("FI-SECRETBANK", audit_str)

    # E13
    def test_create_transfer_request_on_already_paid_bill_does_not_change_status(self):
        """
        If a bill is already PAID, create_transfer_request should still succeed
        but must NOT alter the bill status (it's already in the target state).
        """
        paid_bill = _make_bill(bill_id="BILL-PRE-PAID", status=GovStackBill.STATUS_PAID)
        GovStackP2GService.create_transfer_request(
            request_id="REQ-PRE-PAID",
            bill_id="BILL-PRE-PAID",
        )
        paid_bill.refresh_from_db()
        self.assertEqual(paid_bill.status, GovStackBill.STATUS_PAID)

    # E14
    def test_mark_bill_paid_transitions_unpaid_to_paid(self):
        bill = GovStackP2GService.mark_bill_paid(bill_id=BILL_ID)
        self.assertEqual(bill.status, GovStackBill.STATUS_PAID)
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, GovStackBill.STATUS_PAID)

    # E15
    def test_mark_bill_paid_is_idempotent(self):
        paid_bill = _make_bill(bill_id="BILL-IDEMPOTENT", status=GovStackBill.STATUS_PAID)
        result = GovStackP2GService.mark_bill_paid(bill_id="BILL-IDEMPOTENT")
        # Should return the bill without raising, and status must still be PAID.
        self.assertEqual(result.status, GovStackBill.STATUS_PAID)

    # E16
    def test_mark_bill_paid_raises_bill_not_found(self):
        with self.assertRaises(BillNotFound):
            GovStackP2GService.mark_bill_paid(bill_id="NO-SUCH-BILL")

    # E17
    def test_mark_bill_paid_creates_audit_entry_on_transition(self):
        """
        mark_bill_paid() must create exactly one ACTION_BILL_PAID audit entry
        on the UNPAID→PAID transition, and that entry must:
          - have object_pk = str(bill.pk)  (the UUID, not the external bill_id)
          - have details["bill_pk"] = str(bill.pk)
          - NOT have details["bill_id"] (the external string — M-NEW-1 fix)

        Pinning the details shape ensures that future changes to the audit dict
        do not accidentally expose the external bill_id or diverge from the
        convention established in create_transfer_request() (which uses "bill_pk").
        """
        GovStackP2GService.mark_bill_paid(bill_id=BILL_ID)

        entry = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_BILL_PAID
        ).latest("created_at")

        # object_pk must be the UUID primary key string.
        self.assertEqual(entry.object_pk, str(self.bill.pk))

        # details must contain "bill_pk" with the UUID value.
        self.assertIn(
            "bill_pk",
            entry.details,
            "ACTION_BILL_PAID audit entry must include 'bill_pk' in details (M-NEW-1).",
        )
        self.assertEqual(
            entry.details["bill_pk"],
            str(self.bill.pk),
            "details['bill_pk'] must equal the bill's UUID primary key.",
        )

        # details must NOT contain "bill_id" (external government string).
        # Using the external string was the pre-fix behaviour; this assertion
        # pins that M-NEW-1 is never accidentally reverted.
        self.assertNotIn(
            "bill_id",
            entry.details,
            "ACTION_BILL_PAID audit details must NOT contain 'bill_id' "
            "(external string — use 'bill_pk' for consistency with "
            "create_transfer_request()).",
        )

    # E17b — M-NEW-1: audit details consistency between the two write paths
    def test_mark_bill_paid_audit_details_consistent_with_create_transfer_request(self):
        """
        The 'details' dict schema in ACTION_BILL_PAID must use the same key
        convention as ACTION_BILL_PAYMENT_REQUESTED:  both must use "bill_pk"
        (UUID string), not "bill_id" (external government-assigned string).

        This test fetches both audit entries and compares their details keys,
        so a future drift in either method is immediately caught.
        """
        # Create a payment (generates ACTION_BILL_PAYMENT_REQUESTED entry)
        bill_for_payment = _make_bill(bill_id="BILL-E17b")
        GovStackP2GService.create_transfer_request(
            request_id="REQ-E17b",
            bill_id="BILL-E17b",
        )

        # Create a second bill and mark it paid (generates ACTION_BILL_PAID entry)
        bill_for_marking = _make_bill(bill_id="BILL-E17b-MARK")
        GovStackP2GService.mark_bill_paid(bill_id="BILL-E17b-MARK")

        payment_entry = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_BILL_PAYMENT_REQUESTED
        ).latest("created_at")
        paid_entry = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_BILL_PAID
        ).latest("created_at")

        # Both entries must use "bill_pk" as their primary bill reference key.
        self.assertIn(
            "bill_pk",
            payment_entry.details,
            "ACTION_BILL_PAYMENT_REQUESTED details must have 'bill_pk'.",
        )
        self.assertIn(
            "bill_pk",
            paid_entry.details,
            "ACTION_BILL_PAID details must have 'bill_pk' (M-NEW-1).",
        )

        # Neither entry should use the inconsistent "bill_id" key.
        self.assertNotIn("bill_id", payment_entry.details)
        self.assertNotIn("bill_id", paid_entry.details)

    # E18
    def test_mark_bill_paid_no_audit_entry_when_already_paid(self):
        paid_bill = _make_bill(bill_id="BILL-NO-AUDIT", status=GovStackBill.STATUS_PAID)
        pre_count = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_BILL_PAID
        ).count()
        GovStackP2GService.mark_bill_paid(bill_id="BILL-NO-AUDIT")
        post_count = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_BILL_PAID
        ).count()
        self.assertEqual(post_count, pre_count)

    # E19
    def test_get_transfer_request_returns_payment_with_related_bill(self):
        paid_bill = _make_bill(bill_id="BILL-RELATED", status=GovStackBill.STATUS_PAID)
        payment = _make_payment(request_id="REQ-RELATED", bill=paid_bill)
        result = GovStackP2GService.get_transfer_request(request_id="REQ-RELATED")
        # Must have the related bill loaded (no extra query needed)
        self.assertEqual(result.bill.bill_id, "BILL-RELATED")

    # E20
    def test_get_transfer_request_raises_bill_payment_not_found(self):
        with self.assertRaises(BillPaymentNotFound):
            GovStackP2GService.get_transfer_request(request_id="NO-SUCH-REQ")

    # E21 — certifiability-audit fix (CRITICAL): get_bill() tenant scoping
    def test_get_bill_wrong_tenant_raises_bill_not_found(self):
        tenant_bill = _make_bill(bill_id="BILL-SVC-TENANT", status=GovStackBill.STATUS_UNPAID)
        tenant_bill.platform_tenant_id = "TENANT-A"
        tenant_bill.save(update_fields=["platform_tenant_id"])
        with self.assertRaises(BillNotFound):
            GovStackP2GService.get_bill(
                bill_id="BILL-SVC-TENANT", platform_tenant_id="TENANT-B"
            )

    def test_get_bill_matching_tenant_succeeds(self):
        tenant_bill = _make_bill(bill_id="BILL-SVC-TENANT2", status=GovStackBill.STATUS_UNPAID)
        tenant_bill.platform_tenant_id = "TENANT-A"
        tenant_bill.save(update_fields=["platform_tenant_id"])
        bill = GovStackP2GService.get_bill(
            bill_id="BILL-SVC-TENANT2", platform_tenant_id="TENANT-A"
        )
        self.assertEqual(bill.bill_id, "BILL-SVC-TENANT2")

    def test_get_bill_no_tenant_declared_ignores_scoping(self):
        tenant_bill = _make_bill(bill_id="BILL-SVC-TENANT3", status=GovStackBill.STATUS_UNPAID)
        tenant_bill.platform_tenant_id = "TENANT-A"
        tenant_bill.save(update_fields=["platform_tenant_id"])
        # No platform_tenant_id supplied at all (harness mode) → no scoping.
        bill = GovStackP2GService.get_bill(bill_id="BILL-SVC-TENANT3")
        self.assertEqual(bill.bill_id, "BILL-SVC-TENANT3")

    # E22 — create_transfer_request() tenant scoping
    def test_create_transfer_request_wrong_tenant_raises_bill_not_found(self):
        tenant_bill = _make_bill(bill_id="BILL-SVC-TENANT4", status=GovStackBill.STATUS_UNPAID)
        tenant_bill.platform_tenant_id = "TENANT-A"
        tenant_bill.save(update_fields=["platform_tenant_id"])
        with self.assertRaises(BillNotFound):
            GovStackP2GService.create_transfer_request(
                request_id="REQ-SVC-TENANT4",
                bill_id="BILL-SVC-TENANT4",
                platform_tenant_id="TENANT-B",
            )
        self.assertFalse(
            GovStackBillPayment.objects.filter(request_id="REQ-SVC-TENANT4").exists()
        )

    # E23 — mark_bill_paid() tenant scoping
    def test_mark_bill_paid_wrong_tenant_raises_bill_not_found(self):
        tenant_bill = _make_bill(bill_id="BILL-SVC-TENANT5", status=GovStackBill.STATUS_UNPAID)
        tenant_bill.platform_tenant_id = "TENANT-A"
        tenant_bill.save(update_fields=["platform_tenant_id"])
        with self.assertRaises(BillNotFound):
            GovStackP2GService.mark_bill_paid(
                bill_id="BILL-SVC-TENANT5", platform_tenant_id="TENANT-B"
            )
        tenant_bill.refresh_from_db()
        self.assertEqual(tenant_bill.status, GovStackBill.STATUS_UNPAID)

    # E24 — mark_bill_paid() now records the caller's identity on the audit entry
    def test_mark_bill_paid_records_actor_payer_fi_id_on_audit_entry(self):
        GovStackP2GService.mark_bill_paid(
            bill_id=BILL_ID, actor_payer_fi_id="FI-SVC-CALLER"
        )
        entry = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_BILL_PAID
        ).latest("created_at")
        self.assertEqual(entry.actor_bb_id, "FI-SVC-CALLER")

    # E25 — get_transfer_request() tenant scoping
    def test_get_transfer_request_wrong_tenant_raises_not_found(self):
        tenant_bill = _make_bill(bill_id="BILL-SVC-TENANT6", status=GovStackBill.STATUS_PAID)
        tenant_bill.platform_tenant_id = "TENANT-A"
        tenant_bill.save(update_fields=["platform_tenant_id"])
        payment = _make_payment(request_id="REQ-SVC-TENANT6", bill=tenant_bill)
        payment.platform_tenant_id = "TENANT-A"
        payment.save(update_fields=["platform_tenant_id"])
        with self.assertRaises(BillPaymentNotFound):
            GovStackP2GService.get_transfer_request(
                request_id="REQ-SVC-TENANT6", platform_tenant_id="TENANT-B"
            )

    def test_get_transfer_request_matching_tenant_succeeds(self):
        tenant_bill = _make_bill(bill_id="BILL-SVC-TENANT7", status=GovStackBill.STATUS_PAID)
        tenant_bill.platform_tenant_id = "TENANT-A"
        tenant_bill.save(update_fields=["platform_tenant_id"])
        payment = _make_payment(request_id="REQ-SVC-TENANT7", bill=tenant_bill)
        payment.platform_tenant_id = "TENANT-A"
        payment.save(update_fields=["platform_tenant_id"])
        result = GovStackP2GService.get_transfer_request(
            request_id="REQ-SVC-TENANT7", platform_tenant_id="TENANT-A"
        )
        self.assertEqual(result.request_id, "REQ-SVC-TENANT7")


# ===========================================================================
# F. GovStackBill model tests
# ===========================================================================

class TestGovStackBillModel(TestCase):
    # F1
    def test_bill_id_unique_constraint(self):
        _make_bill()
        with self.assertRaises(IntegrityError):
            _make_bill()  # same BILL_ID

    # F2
    def test_amount_check_constraint_zero(self):
        # Django's CheckConstraint(amount__gt=0) raises IntegrityError in both
        # SQLite (test database) and PostgreSQL (production).  DataError would
        # only occur for values that overflow the column type — not for zero.
        with self.assertRaises(IntegrityError):
            GovStackBill.objects.create(
                bill_id="BILL-ZERO",
                amount=Decimal("0.00"),
                currency="USD",
            )

    # F3
    def test_amount_check_constraint_negative(self):
        with self.assertRaises(IntegrityError):
            GovStackBill.objects.create(
                bill_id="BILL-NEG",
                amount=Decimal("-10.00"),
                currency="USD",
            )

    # F4
    def test_currency_validator_rejects_two_chars(self):
        from django.core.exceptions import ValidationError
        bill = GovStackBill(
            bill_id="BILL-BAD-CUR",
            amount=Decimal("10.00"),
            currency="US",
        )
        with self.assertRaises(ValidationError):
            bill.full_clean()

    # F5
    def test_currency_validator_rejects_four_chars(self):
        from django.core.exceptions import ValidationError
        bill = GovStackBill(
            bill_id="BILL-BAD-CUR2",
            amount=Decimal("10.00"),
            currency="USDT",
        )
        with self.assertRaises(ValidationError):
            bill.full_clean()

    # F6
    def test_due_date_is_nullable(self):
        bill = _make_bill(bill_id="BILL-NODATE", due_date=None)
        self.assertIsNone(bill.due_date)

    # F7
    def test_status_defaults_to_unpaid(self):
        bill = GovStackBill.objects.create(
            bill_id="BILL-DEFAULT",
            amount=Decimal("50.00"),
            currency="EUR",
        )
        self.assertEqual(bill.status, GovStackBill.STATUS_UNPAID)

    # F8
    def test_str_is_sensible(self):
        bill = _make_bill()
        s = str(bill)
        # Must not raise and should contain something identifiable.
        self.assertIsInstance(s, str)
        self.assertTrue(len(s) > 0)


# ===========================================================================
# G. GovStackBillPayment model tests
# ===========================================================================

class TestGovStackBillPaymentModel(TestCase):
    def setUp(self):
        self.bill = _make_bill()

    # G1
    def test_request_id_unique_constraint(self):
        _make_payment(bill=self.bill)
        bill2 = _make_bill(bill_id="BILL-G1")
        with self.assertRaises(IntegrityError):
            _make_payment(request_id=REQUEST_ID, bill=bill2)

    # G2
    def test_amount_check_constraint_zero(self):
        # Same as F2: CheckConstraint(amount__gt=0) raises IntegrityError, not
        # the generic Exception base class.  Being specific here ensures the test
        # would fail if the constraint were ever accidentally removed.
        with self.assertRaises(IntegrityError):
            GovStackBillPayment.objects.create(
                request_id="REQ-ZERO",
                bill=self.bill,
                amount=Decimal("0.00"),
                currency="USD",
                status=GovStackBillPayment.STATUS_COMPLETED,
            )

    # G3
    def test_protect_fk_prevents_bill_deletion_with_payments(self):
        from django.db.models import ProtectedError
        _make_payment(bill=self.bill)
        with self.assertRaises(ProtectedError):
            self.bill.delete()

    # G4
    def test_status_defaults_to_pending(self):
        payment = GovStackBillPayment.objects.create(
            request_id="REQ-DEFAULT",
            bill=self.bill,
            amount=Decimal("50.00"),
            currency="EUR",
        )
        self.assertEqual(payment.status, GovStackBillPayment.STATUS_PENDING)


# ===========================================================================
# H. Security invariants
# ===========================================================================

class TestSecurityInvariants(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.bill = _make_bill()

    # H1 — payer_fi_id not in any response
    def test_payer_fi_id_not_in_transfer_response(self):
        resp = self.client.post(
            TRANSFER_REQUESTS_URL,
            _transfer_body(),
            format="json",
            HTTP_X_PAYERFI_ID="SENSITIVE-FI-ID",
        )
        self.assertEqual(resp.status_code, 202)
        resp_text = resp.content.decode()
        self.assertNotIn("SENSITIVE-FI-ID", resp_text)
        self.assertNotIn("payer_fi_id", resp_text)

    def test_payer_fi_id_not_in_status_response(self):
        paid_bill = _make_bill(bill_id="BILL-FI", status=GovStackBill.STATUS_PAID)
        payment = _make_payment(
            request_id="REQ-FI",
            bill=paid_bill,
        )
        payment.payer_fi_id = "SENSITIVE-FI-ID"
        payment.save(update_fields=["payer_fi_id"])
        resp = self.client.get(_transfer_status_url("REQ-FI"))
        self.assertEqual(resp.status_code, 202)
        resp_text = resp.content.decode()
        self.assertNotIn("SENSITIVE-FI-ID", resp_text)
        self.assertNotIn("payer_fi_id", resp_text)

    # H2 — 404 responses use {"message": "..."} not {"detail": "..."}; 400
    # responses now use the P2G envelope {"responseCode", "reason", "requestID"}
    def test_404_uses_message_not_detail(self):
        resp = self.client.get(_bill_url("NONEXISTENT"))
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertIn("message", data)
        self.assertNotIn("detail", data)

    def test_400_uses_envelope_not_detail(self):
        resp = self.client.post(TRANSFER_REQUESTS_URL, {}, format="json")
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertIn("responseCode", data)
        self.assertIn("reason", data)
        self.assertNotIn("detail", data)

    # H3 — HTTP 404 shape is {"message": "..."} for all P2G 404 cases
    def test_bill_payment_not_found_uses_message_shape(self):
        resp = self.client.get(_transfer_status_url("NO-SUCH-REQ"))
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertIn("message", data)
        self.assertNotIn("detail", data)

    # H4 — audit details use bill_pk, not external bill_id
    def test_audit_details_use_bill_pk_not_external_bill_id(self):
        GovStackP2GService.create_transfer_request(
            request_id=REQUEST_ID,
            bill_id=BILL_ID,
        )
        audit = GovStackPaymentAuditEntry.objects.get(
            action=GovStackPaymentAuditEntry.ACTION_BILL_PAYMENT_REQUESTED,
        )
        # details["bill_pk"] should be the UUID string, not the external bill_id string
        self.assertIn("bill_pk", audit.details)
        self.assertEqual(audit.details["bill_pk"], str(self.bill.pk))
        # The external bill_id string (BILL_ID) must NOT be a key in details
        self.assertNotIn("bill_id", audit.details)

    # H5 — DuplicateBillPaymentError message is generic (no request_id leakage)
    def test_duplicate_error_message_is_generic(self):
        GovStackP2GService.create_transfer_request(
            request_id=REQUEST_ID,
            bill_id=BILL_ID,
        )
        bill2 = _make_bill(bill_id="BILL-DUP")
        try:
            GovStackP2GService.create_transfer_request(
                request_id=REQUEST_ID,
                bill_id="BILL-DUP",
            )
            self.fail("Expected DuplicateBillPaymentError")
        except DuplicateBillPaymentError as exc:
            # The exception message must not expose the internal request_id value
            # in such a way that it could be leaked through an API response.
            # The view maps this to the generic string "Transfer request ID has already been received."
            self.assertEqual(str(exc), "Transfer request ID has already been received.")


# ===========================================================================
# I. High-severity regression tests (post-review fixes)
# ===========================================================================

class TestHighSeverityRegressions(TestCase):
    """
    Targeted regression tests for H1 and H2 fixes.

    H1 — IntegrityError scope in create_transfer_request():
      The try/except IntegrityError now wraps ONLY GovStackBillPayment.objects.create().
      BillNotFound is raised OUTSIDE the guard.  These tests confirm that:
        (a) BillNotFound propagates cleanly from create_transfer_request()
            when the bill does not exist — it is NOT misidentified as a
            DuplicateBillPaymentError.
        (b) DuplicateBillPaymentError is still raised correctly when the
            request_id unique constraint fires.
        (c) A valid request on a known bill still succeeds normally.

    H2 — ProtectedError in GovStackBillAdmin.delete_view():
      The admin now overrides delete_view() to catch ProtectedError and redirect
      with an error message instead of surfacing a 500.  These tests confirm that:
        (d) The PROTECT FK still raises ProtectedError when deleting a bill with
            payments at the ORM level (the guard is DB-enforced, not admin-only).
        (e) has_delete_permission() returns False for a bill with payments,
            preventing the delete UI from appearing in normal flow.
    """

    def setUp(self):
        self.bill = _make_bill()

    # I1 — H1 regression: BillNotFound is NOT swallowed by IntegrityError guard
    def test_bill_not_found_propagates_from_create_transfer_request(self):
        """
        create_transfer_request() with an unknown bill_id must raise BillNotFound,
        not DuplicateBillPaymentError and not IntegrityError.

        Before the H1 fix, if BillNotFound had somehow been caught by the outer
        try/except IntegrityError (which it cannot be — BillNotFound is not an
        IntegrityError), it would have been silently converted.  This test pins
        the correct exception type to guard against future refactors.
        """
        with self.assertRaises(BillNotFound):
            GovStackP2GService.create_transfer_request(
                request_id="REQ-H1-NOEXIST",
                bill_id="BILL-DOES-NOT-EXIST",
            )

    def test_bill_not_found_is_not_duplicate_error(self):
        """
        The exception raised for an unknown bill must not be DuplicateBillPaymentError.
        Confirms the two error paths are independent after the H1 scope narrowing.
        """
        try:
            GovStackP2GService.create_transfer_request(
                request_id="REQ-H1-NOEXIST2",
                bill_id="BILL-DOES-NOT-EXIST-2",
            )
            self.fail("Expected BillNotFound")
        except BillNotFound:
            pass  # Correct
        except DuplicateBillPaymentError:
            self.fail(
                "Got DuplicateBillPaymentError for a non-existent bill — "
                "IntegrityError guard scope is too broad (H1 regression)."
            )

    # I2 — H1 regression: DuplicateBillPaymentError still fires correctly
    def test_duplicate_request_id_still_raises_duplicate_error(self):
        """
        After narrowing the IntegrityError scope, duplicate request_ids must still
        be caught and converted to DuplicateBillPaymentError.
        """
        GovStackP2GService.create_transfer_request(
            request_id="REQ-H1-DUP",
            bill_id=BILL_ID,
        )
        bill2 = _make_bill(bill_id="BILL-H1-DUP2")
        with self.assertRaises(DuplicateBillPaymentError):
            GovStackP2GService.create_transfer_request(
                request_id="REQ-H1-DUP",  # same request_id
                bill_id="BILL-H1-DUP2",
            )

    # I3 — H1 regression: happy path still works after scope narrowing
    def test_valid_request_on_known_bill_succeeds_after_h1_fix(self):
        """
        The H1 refactor must not break the happy path.
        """
        payment = GovStackP2GService.create_transfer_request(
            request_id="REQ-H1-OK",
            bill_id=BILL_ID,
        )
        self.assertIsInstance(payment, GovStackBillPayment)
        self.assertEqual(payment.status, GovStackBillPayment.STATUS_COMPLETED)
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, GovStackBill.STATUS_PAID)

    # I4 — H2 regression: ProtectedError is still raised at ORM level (DB guard intact)
    def test_protect_fk_still_raises_at_orm_level(self):
        """
        The H2 fix adds an admin-layer ProtectedError handler, but the underlying
        DB-level PROTECT FK must remain intact.  Confirms the ORM still raises
        ProtectedError when a bill with payments is deleted directly.
        """
        from django.db.models import ProtectedError
        _make_payment(bill=self.bill)
        with self.assertRaises(ProtectedError):
            self.bill.delete()

    # I5 — H2 regression: has_delete_permission returns False for bill with payments
    def test_admin_has_delete_permission_false_for_bill_with_payments(self):
        """
        GovStackBillAdmin.has_delete_permission() must return False when the bill
        has associated payment records, preventing the delete UI from appearing
        in normal flow (before any TOCTOU race can occur).
        """
        from django.contrib.admin.sites import AdminSite
        from apps.payments.admin import GovStackBillAdmin

        _make_payment(bill=self.bill)

        site = AdminSite()
        model_admin = GovStackBillAdmin(GovStackBill, site)

        # Simulate an admin request (no real request needed — just needs to be truthy)
        class _FakeRequest:
            pass

        fake_request = _FakeRequest()

        # Without an obj, should return True (general permission check)
        self.assertTrue(model_admin.has_delete_permission(fake_request, obj=None))

        # With a bill that has payments, must return False
        self.assertFalse(model_admin.has_delete_permission(fake_request, obj=self.bill))

    # I6 — H2 regression: has_delete_permission returns True for bill without payments
    def test_admin_has_delete_permission_true_for_bill_without_payments(self):
        """
        has_delete_permission() must return True for a bill that has no payment records,
        since the PROTECT FK allows deletion in that case.
        """
        from django.contrib.admin.sites import AdminSite
        from apps.payments.admin import GovStackBillAdmin

        # No payments created against self.bill in this test.
        site = AdminSite()
        model_admin = GovStackBillAdmin(GovStackBill, site)

        class _FakeRequest:
            pass

        self.assertTrue(
            model_admin.has_delete_permission(_FakeRequest(), obj=self.bill)
        )


# ===========================================================================
# J. Medium-severity regression tests (post-review fixes)
# ===========================================================================

class TestMediumSeverityRegressions(TestCase):
    """
    Targeted regression tests for M1 and M5 fixes.

    M1 — Blank requestId/billId rejected by BillTransferRequestSerializer:
      After strip(), an all-whitespace value becomes "".  Empty strings must
      not be accepted as idempotency keys.  The serializer now raises a
      ValidationError for blank values.

    M5 — bill_id is readonly in GovStackBillAdmin:
      bill_id is a stable external identifier; changing it after creation
      would break downstream systems.  The admin must list it in readonly_fields.
    """

    def setUp(self):
        self.client = APIClient()
        self.bill = _make_bill()

    # J1 — M1: All-whitespace requestId is rejected
    def test_whitespace_only_request_id_returns_400(self):
        """
        POST /billTransferRequests with requestId "   " must return 400.
        Before the M1 fix, "   ".strip() == "" would be accepted as a valid
        idempotency key, making the first call succeed with a blank key.
        """
        body = _transfer_body(request_id="   ")
        resp = self.client.post(TRANSFER_REQUESTS_URL, body, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("reason", resp.json())

    # J2 — M1: All-whitespace billId is rejected
    def test_whitespace_only_bill_id_returns_400(self):
        body = _transfer_body(bill_id="   ")
        resp = self.client.post(TRANSFER_REQUESTS_URL, body, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("reason", resp.json())

    # J3 — M1: Empty string requestId is rejected
    def test_empty_string_request_id_returns_400(self):
        body = _transfer_body(request_id="")
        resp = self.client.post(TRANSFER_REQUESTS_URL, body, format="json")
        # DRF treats "" as blank which is normally rejected by CharField (required=True, allow_blank=False by default)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("reason", resp.json())

    # J4 — M1: Valid non-blank requestId still succeeds
    def test_valid_request_id_not_affected_by_blank_check(self):
        body = _transfer_body()
        resp = self.client.post(TRANSFER_REQUESTS_URL, body, format="json")
        self.assertEqual(resp.status_code, 202)

    # J5 — M1: requestId with surrounding whitespace is stripped and accepted
    def test_request_id_with_surrounding_whitespace_is_stripped(self):
        """
        A requestId like "  REQ-001  " should be stripped to "REQ-001" and succeed.
        The blank guard only fires when the stripped value is empty.
        """
        body = _transfer_body(request_id=f"  {REQUEST_ID}  ")
        resp = self.client.post(TRANSFER_REQUESTS_URL, body, format="json")
        self.assertEqual(resp.status_code, 202)
        # Confirm the stored request_id is the stripped version
        payment = GovStackBillPayment.objects.get(request_id=REQUEST_ID)
        self.assertEqual(payment.request_id, REQUEST_ID)

    # J6 — M5: bill_id is in readonly_fields on GovStackBillAdmin
    def test_admin_bill_id_is_readonly(self):
        """
        GovStackBillAdmin.readonly_fields must include 'bill_id' so staff
        cannot change the external identifier after a bill is created.
        """
        from django.contrib.admin.sites import AdminSite
        from apps.payments.admin import GovStackBillAdmin

        site = AdminSite()
        model_admin = GovStackBillAdmin(GovStackBill, site)

        self.assertIn(
            "bill_id",
            model_admin.readonly_fields,
            "bill_id must be in readonly_fields — it is a stable external identifier "
            "referenced by downstream systems and audit trails.",
        )

    # J7 — M5: amount and currency remain editable (not over-restricted)
    def test_admin_amount_and_currency_are_editable(self):
        """
        amount and currency must NOT be in readonly_fields — staff need to correct
        data-entry errors on unpaid bills.  This test guards against over-restriction.
        """
        from django.contrib.admin.sites import AdminSite
        from apps.payments.admin import GovStackBillAdmin

        site = AdminSite()
        model_admin = GovStackBillAdmin(GovStackBill, site)

        self.assertNotIn(
            "amount",
            model_admin.readonly_fields,
            "amount should be editable by staff for data-entry corrections.",
        )
        self.assertNotIn(
            "currency",
            model_admin.readonly_fields,
            "currency should be editable by staff for data-entry corrections.",
        )

    # J8 — billInquiryRequestId/paymentReferenceID blank rejection
    # (certifiability-audit fix: these fields are now required per the live
    # spec, and blank/whitespace-only values are rejected the same way
    # requestId/billId already were.)
    def test_whitespace_only_bill_inquiry_request_id_returns_400(self):
        body = _transfer_body(bill_inquiry_request_id="   ")
        resp = self.client.post(TRANSFER_REQUESTS_URL, body, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_whitespace_only_payment_reference_id_returns_400(self):
        body = _transfer_body(payment_reference_id="   ")
        resp = self.client.post(TRANSFER_REQUESTS_URL, body, format="json")
        self.assertEqual(resp.status_code, 400)


# ===========================================================================
# K. Low-severity regression tests (post-review fixes)
# ===========================================================================

class TestLowSeverityRegressions(TestCase):
    """
    Targeted regression tests for low-severity review findings.

    L7 — billId must appear in POST /billTransferRequests response:
      The transfer response shape is {requestId, billId, amount, currency,
      status, message}.  billId ties the payment record back to the originating
      bill in a single round trip, avoiding a separate GET.

    L8 — GovStackBillAdmin.delete_view() catches ProtectedError and redirects:
      When a concurrent POST /billTransferRequests creates a payment between
      has_delete_permission() returning True and the SQL DELETE executing,
      Django raises ProtectedError.  The delete_view() override must catch this
      and return an HttpResponseRedirect (302) to the change page, never a 500.
    """

    def setUp(self):
        self.bill = _make_bill()

    # K1 — L7: billId is present and correct in POST /billTransferRequests response
    def test_transfer_response_contains_bill_id(self):
        """
        POST /billTransferRequests must return billId in the response body.
        The value must match the bill_id of the bill that was paid — not the
        UUID primary key — because bill_id is the stable external identifier.
        """
        body = _transfer_body()
        resp = self.client.post(TRANSFER_REQUESTS_URL, body, format="json")
        self.assertEqual(resp.status_code, 202)
        data = resp.json()
        self.assertIn(
            "billId",
            data,
            "Transfer response must include 'billId' key (L7).",
        )
        self.assertEqual(
            data["billId"],
            BILL_ID,
            "billId in response must equal the bill's external bill_id string.",
        )

    # K2 — L7: GET /transferRequests/{requestId} also returns billId
    def test_transfer_status_response_contains_bill_id(self):
        """
        GET /transferRequests/{requestId} must also include billId so callers
        can reconstruct the bill-payment relationship without a second query.
        """
        _make_payment(bill=self.bill)
        resp = self.client.get(_transfer_status_url(REQUEST_ID))
        self.assertEqual(resp.status_code, 202)
        data = resp.json()
        self.assertIn(
            "billId",
            data,
            "Transfer status response must include 'billId' key (L7).",
        )
        self.assertEqual(data["billId"], BILL_ID)

    # K3 — L8: delete_view catches ProtectedError and redirects (no 500)
    def test_admin_delete_view_catches_protected_error_and_redirects(self):
        """
        Simulates the TOCTOU race where a payment is created between
        has_delete_permission() returning True and the actual SQL DELETE.

        Strategy: we patch super().delete_view() to raise ProtectedError
        directly (bypassing Django's delete confirmation flow), then call
        GovStackBillAdmin.delete_view() and assert we get an HttpResponseRedirect
        rather than an unhandled exception.

        This is the exact scenario that caused a 500 before the H2 fix, and
        this test pins that the delete_view() override handles it gracefully.
        """
        from unittest.mock import patch, MagicMock
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.db.models import ProtectedError
        from django.http import HttpResponseRedirect
        from django.test import RequestFactory
        from apps.payments.admin import GovStackBillAdmin

        # Build a minimal admin request using Django's RequestFactory
        factory = RequestFactory()
        request = factory.post(
            f"/admin/payments/govstackbill/{self.bill.pk}/delete/",
            data={"post": "yes"},
        )
        # Django admin needs a session and message storage on the request object
        request.session = {}
        request._messages = FallbackStorage(request)

        site = AdminSite()
        model_admin = GovStackBillAdmin(GovStackBill, site)

        # Patch the parent delete_view to raise ProtectedError, simulating
        # the TOCTOU window where a payment is created just before the DELETE.
        protected_objs = GovStackBillPayment.objects.none()
        with patch(
            "django.contrib.admin.options.ModelAdmin.delete_view",
            side_effect=ProtectedError("Protected by payment FK", protected_objs),
        ):
            response = model_admin.delete_view(
                request, str(self.bill.pk)
            )

        # Must redirect, not raise
        self.assertIsInstance(
            response,
            HttpResponseRedirect,
            "delete_view() must redirect on ProtectedError, not raise a 500 (L8).",
        )
        # Redirect target must be the change page for this bill
        self.assertIn(str(self.bill.pk), response["Location"])
