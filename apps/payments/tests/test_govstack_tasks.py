"""
test_govstack_tasks.py

Tests for GovStack Payments BB Celery tasks (spec §18).

Covers: process_bulk_payment_batch + validate_prepayment_async

These tasks are dispatched via transaction.on_commit() from BulkPaymentView and
PrepaymentValidationView respectively.  The tests verify both the dispatch
mechanism (G1, G9) and the task execution logic (G2–G8, G10–G16).

Coverage matrix:
  G1:  BulkPaymentView.post() dispatches process_bulk_payment_batch via on_commit
  G2:  process_bulk_payment_batch transitions all CreditInstructions → COMPLETED
  G3:  process_bulk_payment_batch sets BulkPaymentBatch.status → COMPLETED
  G3b: process_bulk_payment_batch sets completed_amount + result_generated_at
  G4:  process_bulk_payment_batch writes ACTION_BATCH_COMPLETED audit entry
  G5:  process_bulk_payment_batch POSTs to callback_url when non-empty
  G6:  process_bulk_payment_batch does NOT post when callback_url is empty
  G7:  process_bulk_payment_batch callback payload never includes payee_functional_id
  G8:  process_bulk_payment_batch is idempotent (second call is a no-op)
  G9:  PrepaymentValidationView.post() dispatches validate_prepayment_async via on_commit
  G10: validate_prepayment_async sets beneficiary_found=True for registered payee_functional_id
  G11: validate_prepayment_async sets beneficiary_found=False for unknown payee_functional_id
  G12: validate_prepayment_async transitions PrepaymentValidationRequest.status → COMPLETED
  G13: validate_prepayment_async writes ACTION_VALIDATION_COMPLETED audit entry
  G14: validate_prepayment_async callback payload has correct shape (no payee_functional_id)
  G15: validate_prepayment_async callback POST failure is non-fatal (task still completes)
  G16: validate_prepayment_async is idempotent (second call is a no-op)

Security invariants tested:
  - Task callback payloads never include payee_functional_id (G7, G14)
  - Callback batch payloads never include batch_id raw value — only the batch PK (G7)

Harness dispatch identifiers (G1, G9):
  Bulk:   RequestID="RequestID111" SourceBBID="SourceBBID11" BatchID="BatchID11111"
  Prepay: RequestID="abcdef123456" SourceBBID="sourceBBID12" BatchID="batchID12345"
"""
from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase

from rest_framework.test import APIClient

from apps.payments.govstack_models import (
    BulkPaymentBatch,
    CreditInstruction,
    GovStackBeneficiary,
    GovStackPaymentAuditEntry,
    PrepaymentValidationRequest,
)
from apps.payments.govstack_tasks import (
    process_bulk_payment_batch,
    validate_prepayment_async,
)


# ---------------------------------------------------------------------------
# URL constants (harness endpoints used by G1, G9 dispatch tests)
# ---------------------------------------------------------------------------

BULK_PAYMENT_URL      = "/govstack/payments/bulk-payment"
PREPAY_VALIDATION_URL = "/govstack/payments/prepayment-validation"

# ---------------------------------------------------------------------------
# Body-builder helpers (mirrors test_govstack_bulk_payment.py constants)
# ---------------------------------------------------------------------------

# Harness identifiers — must match what the server accepts (min_length=10)
_BP_REQUEST_ID = "RequestID111"    # 12 chars
_BP_SOURCE_BB  = "SourceBBID11"    # 12 chars
_BP_BATCH_ID   = "BatchID11111"    # 12 chars
_BP_INSTR_ID   = "InstructionID111"   # 16 chars
_BP_PAYEE_ID   = "PayeeFunctionalID111"  # 20 chars

_PV_REQUEST_ID = "abcdef123456"    # 12 chars
_PV_SOURCE_BB  = "sourceBBID12"    # 12 chars
_PV_BATCH_ID   = "batchID12345"    # 12 chars
_PV_INSTR_ID   = "instructionID123"   # 16 chars
_PV_PAYEE_ID   = "PayeeFunctionalID123"  # 20 chars


def _bulk_body() -> dict:
    """Minimal valid BulkPayment request body for G1 dispatch test."""
    return {
        "RequestID": _BP_REQUEST_ID,
        "SourceBBID": _BP_SOURCE_BB,
        "BatchID": _BP_BATCH_ID,
        "CreditInstructions": [{
            "InstructionID": _BP_INSTR_ID,
            "PayeeFunctionalID": _BP_PAYEE_ID,
            "Amount": 100,
            "Currency": "USD",
        }],
    }


def _prepay_body() -> dict:
    """Minimal valid PrepaymentValidation request body for G9 dispatch test."""
    return {
        "RequestID": _PV_REQUEST_ID,
        "SourceBBID": _PV_SOURCE_BB,
        "BatchID": _PV_BATCH_ID,
        "CreditInstructions": [{
            "InstructionID": _PV_INSTR_ID,
            "PayeeFunctionalID": _PV_PAYEE_ID,
            "Amount": 100,
            "Currency": "USD",
            "Narration": "Narration",
        }],
    }


# ---------------------------------------------------------------------------
# Constants used by _make_pvr() and G10 to set up GovStackBeneficiary rows.
# The payee_functional_id must satisfy GovStackBeneficiary._G2P_UUID_VALIDATOR
# (lowercase hex characters and hyphens, 1–20 chars).
# ---------------------------------------------------------------------------

_G_PAYEE_ID     = "2ba5ed20-0f42-4eff-8"  # 20 chars — mirrors VALID_PAYEE_ID in test_govstack_beneficiary
_G_SOURCE_BB_ID = "11668d2a-a8f"           # 13 chars — mirrors VALID_SOURCE_BB_ID in test_govstack_beneficiary


# ============================================================================
# G.  GovStack Celery tasks — async batch processing and prepayment validation
# ============================================================================

class GovStackCeleryTasksTest(TestCase):
    """
    G (tasks). process_bulk_payment_batch + validate_prepayment_async Celery tasks.

    G1:  BulkPaymentView.post() dispatches process_bulk_payment_batch via on_commit
    G2:  process_bulk_payment_batch transitions all CreditInstructions → COMPLETED
    G3:  process_bulk_payment_batch sets BulkPaymentBatch.status → COMPLETED
    G4:  process_bulk_payment_batch writes ACTION_BATCH_COMPLETED audit entry
    G5:  process_bulk_payment_batch POSTs to callback_url when non-empty
    G6:  process_bulk_payment_batch does NOT post when callback_url is empty
    G7:  process_bulk_payment_batch callback payload never includes payee_functional_id
    G8:  process_bulk_payment_batch is idempotent (second call is a no-op)
    G9:  PrepaymentValidationView.post() dispatches validate_prepayment_async via on_commit
    G10: validate_prepayment_async sets beneficiary_found=True for registered payee_functional_id
    G11: validate_prepayment_async sets beneficiary_found=False for unknown payee_functional_id
    G12: validate_prepayment_async transitions PrepaymentValidationRequest.status → COMPLETED
    G13: validate_prepayment_async writes ACTION_VALIDATION_COMPLETED audit entry
    G14: validate_prepayment_async callback payload has correct shape (no payee_functional_id)
    G15: validate_prepayment_async callback POST failure is non-fatal (task still completes)
    G16: validate_prepayment_async is idempotent (second call is a no-op)
    """

    def setUp(self):
        self.client = APIClient()

    # ------------------------------------------------------------------
    # Model-level helpers
    # ------------------------------------------------------------------

    def _make_batch(self, *, batch_id: str = "GBatchIDtask1", callback_url: str = "") -> BulkPaymentBatch:
        """
        Create a BulkPaymentBatch in STATUS_RECEIVED with one CreditInstruction.
        Used for direct task invocation in G2–G8.
        """
        batch = BulkPaymentBatch.objects.create(
            request_id="GReqIDtask01",
            source_bb_id="GSourceBBtask",
            batch_id=batch_id,
            status=BulkPaymentBatch.STATUS_RECEIVED,
            callback_url=callback_url,
            total_amount=Decimal("100.00"),  # matches the single CreditInstruction below
        )
        CreditInstruction.objects.create(
            batch=batch,
            instruction_id="GInstrIDtask01",
            # payee_functional_id has no validator on CreditInstruction — any string
            payee_functional_id="GPayeeIDtask1234",
            amount=Decimal("100.00"),
            currency="USD",
            narration="G-task test instruction",
            status=CreditInstruction.STATUS_PENDING,
        )
        return batch

    def _make_pvr(
        self,
        *,
        request_id: str = "GpvrReqID1234",
        payee_functional_id: str = _G_PAYEE_ID,
        callback_url: str = "",
    ) -> PrepaymentValidationRequest:
        """
        Create a PrepaymentValidationRequest in STATUS_PENDING.
        Used for direct task invocation in G10–G16.
        """
        return PrepaymentValidationRequest.objects.create(
            request_id=request_id,
            source_bb_id="GSourceBBtask",
            batch_id="GpvrBatchID12",
            instruction_id="GpvrInstrID12",
            payee_functional_id=payee_functional_id,
            amount=Decimal("50.00"),
            currency="USD",
            narration="G-task prepayment test",
            status=PrepaymentValidationRequest.STATUS_PENDING,
            callback_url=callback_url,
        )

    # ------------------------------------------------------------------
    # G1 — BulkPaymentView dispatches task via on_commit
    # ------------------------------------------------------------------

    def test_g1_bulk_view_dispatches_task_via_on_commit(self):
        """
        BulkPaymentView.post() must schedule process_bulk_payment_batch.delay()
        via transaction.on_commit() — not inline — so the task only runs after
        the BulkPaymentBatch record has been committed to the database.
        """
        with patch.object(process_bulk_payment_batch, "delay") as mock_delay:
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.client.post(
                    BULK_PAYMENT_URL,
                    data=json.dumps(_bulk_body()),
                    content_type="application/json",
                )
        self.assertEqual(resp.status_code, 200)
        mock_delay.assert_called_once()
        # The single argument must be a UUID string for an existing BulkPaymentBatch.
        batch_pk_str = mock_delay.call_args[0][0]
        self.assertIsInstance(batch_pk_str, str)
        self.assertTrue(
            BulkPaymentBatch.objects.filter(pk=batch_pk_str).exists(),
            f"No BulkPaymentBatch found for pk={batch_pk_str!r}",
        )

    # ------------------------------------------------------------------
    # G2 — Task transitions CreditInstructions to COMPLETED
    # ------------------------------------------------------------------

    def test_g2_process_batch_transitions_instructions_to_completed(self):
        batch = self._make_batch()
        process_bulk_payment_batch.apply(args=[str(batch.pk)])
        completed = CreditInstruction.objects.filter(
            batch=batch,
            status=CreditInstruction.STATUS_COMPLETED,
        ).count()
        self.assertEqual(completed, 1)

    # ------------------------------------------------------------------
    # G3 — Task sets BulkPaymentBatch.status → COMPLETED
    # ------------------------------------------------------------------

    def test_g3_process_batch_sets_batch_status_completed(self):
        batch = self._make_batch(batch_id="GBatchIDtask3")
        process_bulk_payment_batch.apply(args=[str(batch.pk)])
        batch.refresh_from_db()
        self.assertEqual(batch.status, BulkPaymentBatch.STATUS_COMPLETED)

    # ------------------------------------------------------------------
    # G3b — Task sets completed_amount and result_generated_at (M1 fix)
    # ------------------------------------------------------------------

    def test_g3b_process_batch_sets_accounting_fields(self):
        """
        process_bulk_payment_batch must update completed_amount (to total_amount)
        and result_generated_at (to a non-None timestamp) so that monitoring
        queries can detect stuck or unprocessed batches.
        """
        batch = self._make_batch(batch_id="GBatchIDtask3b")
        # Verify pre-conditions
        self.assertIsNone(batch.result_generated_at)

        process_bulk_payment_batch.apply(args=[str(batch.pk)])
        batch.refresh_from_db()

        self.assertIsNotNone(
            batch.result_generated_at,
            "result_generated_at must be non-None after task runs",
        )
        # completed_amount must equal the sum of all CreditInstruction.amount values.
        self.assertEqual(
            batch.completed_amount,
            Decimal("100.00"),
            "completed_amount must equal the sum of all processed instruction amounts",
        )

    # ------------------------------------------------------------------
    # G4 — Task writes ACTION_BATCH_COMPLETED audit entry
    # ------------------------------------------------------------------

    def test_g4_process_batch_writes_audit_entry(self):
        batch = self._make_batch(batch_id="GBatchIDtask4")
        process_bulk_payment_batch.apply(args=[str(batch.pk)])
        entry_count = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_BATCH_COMPLETED,
            object_pk=str(batch.pk),
        ).count()
        self.assertEqual(entry_count, 1)

    # ------------------------------------------------------------------
    # G5 — Task POSTs to callback_url when non-empty
    # ------------------------------------------------------------------

    def test_g5_process_batch_posts_to_callback_url(self):
        batch = self._make_batch(
            batch_id="GBatchIDtask5",
            callback_url="https://example.com/callback",
        )
        with patch("apps.payments.govstack_tasks.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            process_bulk_payment_batch.apply(args=[str(batch.pk)])

        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        self.assertEqual(call_url, "https://example.com/callback")

    # ------------------------------------------------------------------
    # G6 — Task does NOT post when callback_url is empty
    # ------------------------------------------------------------------

    def test_g6_process_batch_no_post_when_callback_empty(self):
        batch = self._make_batch(batch_id="GBatchIDtask6", callback_url="")
        with patch("apps.payments.govstack_tasks.requests.post") as mock_post:
            process_bulk_payment_batch.apply(args=[str(batch.pk)])

        mock_post.assert_not_called()

    # ------------------------------------------------------------------
    # G7 — Callback payload never includes payee_functional_id
    # ------------------------------------------------------------------

    def test_g7_process_batch_callback_no_payee_functional_id(self):
        """
        The callback POST payload must never include payee_functional_id or
        financial_address values for any CreditInstruction — only the
        InstructionID and status are allowed to appear in the payload.

        Security: payee_functional_id is a PII field that must never leave
        the system via callbacks.
        """
        batch = self._make_batch(
            batch_id="GBatchIDtask7",
            callback_url="https://example.com/callback",
        )
        with patch("apps.payments.govstack_tasks.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            process_bulk_payment_batch.apply(args=[str(batch.pk)])

        mock_post.assert_called_once()
        # Extract the JSON body from the call — it is passed as `json=` kwarg.
        payload = mock_post.call_args[1].get("json") or mock_post.call_args[0][1]
        payload_str = json.dumps(payload)
        self.assertNotIn(
            "payee_functional_id",
            payload_str.lower(),
            "Callback payload must never contain payee_functional_id.",
        )
        self.assertNotIn(
            "financial_address",
            payload_str.lower(),
            "Callback payload must never contain financial_address.",
        )
        # The batch_id business key must appear — confirms the callback is meaningful.
        self.assertIn(batch.batch_id, payload_str)

    # ------------------------------------------------------------------
    # G8 — process_bulk_payment_batch is idempotent
    # ------------------------------------------------------------------

    def test_g8_process_batch_is_idempotent(self):
        """
        A second invocation of process_bulk_payment_batch for the same batch
        must be a no-op: status stays COMPLETED, no duplicate audit entries.
        """
        batch = self._make_batch(batch_id="GBatchIDtask8")
        process_bulk_payment_batch.apply(args=[str(batch.pk)])
        process_bulk_payment_batch.apply(args=[str(batch.pk)])  # second call

        batch.refresh_from_db()
        self.assertEqual(batch.status, BulkPaymentBatch.STATUS_COMPLETED)

        # Exactly one ACTION_BATCH_COMPLETED audit entry.
        completed_entries = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_BATCH_COMPLETED,
            object_pk=str(batch.pk),
        ).count()
        self.assertEqual(
            completed_entries,
            1,
            f"Expected exactly 1 ACTION_BATCH_COMPLETED audit entry, found {completed_entries}.",
        )

    # ------------------------------------------------------------------
    # G9 — PrepaymentValidationView dispatches validate_prepayment_async via on_commit
    # ------------------------------------------------------------------

    def test_g9_prepayment_view_dispatches_task_via_on_commit(self):
        """
        PrepaymentValidationView.post() must schedule validate_prepayment_async.delay()
        via transaction.on_commit() so the task only fires after the
        PrepaymentValidationRequest has been committed to the database.
        """
        with patch.object(validate_prepayment_async, "delay") as mock_delay:
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.client.post(
                    PREPAY_VALIDATION_URL,
                    data=json.dumps(_prepay_body()),
                    content_type="application/json",
                )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["ResponseCode"], "00")
        mock_delay.assert_called_once()
        # The single argument must be a UUID string for an existing PVR.
        pvr_pk_str = mock_delay.call_args[0][0]
        self.assertIsInstance(pvr_pk_str, str)
        self.assertTrue(
            PrepaymentValidationRequest.objects.filter(pk=pvr_pk_str).exists(),
            f"No PrepaymentValidationRequest found for pk={pvr_pk_str!r}",
        )

    # ------------------------------------------------------------------
    # G10 — Task sets beneficiary_found=True for a registered payee
    # ------------------------------------------------------------------

    def test_g10_validate_prepayment_finds_registered_beneficiary(self):
        """
        validate_prepayment_async must set beneficiary_found=True (and
        financial_address_valid=True) when an active GovStackBeneficiary
        exists for the pvr.payee_functional_id.
        """
        GovStackBeneficiary.objects.create(
            payee_functional_id=_G_PAYEE_ID,
            source_bb_id=_G_SOURCE_BB_ID,
            is_active=True,
        )
        pvr = self._make_pvr(request_id="GpvrReqID1001")
        validate_prepayment_async.apply(args=[str(pvr.pk)])
        pvr.refresh_from_db()
        self.assertTrue(pvr.beneficiary_found)
        self.assertTrue(pvr.financial_address_valid)

    # ------------------------------------------------------------------
    # G11 — Task sets beneficiary_found=False for unknown payee
    # ------------------------------------------------------------------

    def test_g11_validate_prepayment_unknown_beneficiary(self):
        """
        validate_prepayment_async must set beneficiary_found=False when no
        GovStackBeneficiary exists for pvr.payee_functional_id.
        """
        pvr = self._make_pvr(
            request_id="GpvrReqID1101",
            payee_functional_id="unknown-payee",
        )
        validate_prepayment_async.apply(args=[str(pvr.pk)])
        pvr.refresh_from_db()
        self.assertFalse(pvr.beneficiary_found)

    # ------------------------------------------------------------------
    # G12 — Task transitions PrepaymentValidationRequest.status → COMPLETED
    # ------------------------------------------------------------------

    def test_g12_validate_prepayment_transitions_to_completed(self):
        pvr = self._make_pvr(request_id="GpvrReqID1201")
        validate_prepayment_async.apply(args=[str(pvr.pk)])
        pvr.refresh_from_db()
        self.assertEqual(pvr.status, PrepaymentValidationRequest.STATUS_COMPLETED)

    # ------------------------------------------------------------------
    # G13 — Task writes ACTION_VALIDATION_COMPLETED audit entry
    # ------------------------------------------------------------------

    def test_g13_validate_prepayment_writes_audit_entry(self):
        pvr = self._make_pvr(request_id="GpvrReqID1301")
        validate_prepayment_async.apply(args=[str(pvr.pk)])
        entry_count = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_VALIDATION_COMPLETED,
            object_pk=str(pvr.pk),
        ).count()
        self.assertEqual(entry_count, 1)

    # ------------------------------------------------------------------
    # G14 — Callback payload has correct shape (no payee_functional_id)
    # ------------------------------------------------------------------

    def test_g14_validate_prepayment_callback_payload_correct_shape(self):
        """
        validate_prepayment_async callback must:
          - include RequestID, BatchID, NumberFailedCases, FailedAccounts
          - NEVER include PayeeFunctionalID in any field or value
          - Use InstructionID (not payee_functional_id) in FailedAccounts
        """
        GovStackBeneficiary.objects.create(
            payee_functional_id=_G_PAYEE_ID,
            source_bb_id=_G_SOURCE_BB_ID,
            is_active=True,
        )
        pvr = self._make_pvr(
            request_id="GpvrReqID1401",
            payee_functional_id=_G_PAYEE_ID,
            callback_url="https://example.com/prepay-callback",
        )
        with patch("apps.payments.govstack_tasks.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            validate_prepayment_async.apply(args=[str(pvr.pk)])

        mock_post.assert_called_once()
        payload = mock_post.call_args[1].get("json") or mock_post.call_args[0][1]

        # Required top-level keys (prepayment callback uses Source_BatchID, not BatchID)
        self.assertIn("RequestID", payload)
        self.assertIn("Source_BatchID", payload)
        self.assertIn("NumberFailedCases", payload)
        self.assertIn("FailedAccounts", payload)

        # payee_functional_id must NEVER appear in any field
        payload_str = json.dumps(payload)
        self.assertNotIn(
            "payee_functional_id",
            payload_str.lower(),
            "Callback payload must never contain payee_functional_id.",
        )
        self.assertNotIn(
            _G_PAYEE_ID,
            payload_str,
            "Actual payee_functional_id value must not appear in callback payload.",
        )

        # beneficiary was found, so NumberFailedCases should be 0
        self.assertEqual(payload["NumberFailedCases"], 0)
        self.assertEqual(payload["FailedAccounts"], [])

    # ------------------------------------------------------------------
    # G15 — Callback POST failure is non-fatal
    # ------------------------------------------------------------------

    def test_g15_validate_prepayment_callback_failure_is_nonfatal(self):
        """
        If the callback POST raises an exception (network error, timeout, etc.),
        validate_prepayment_async must still complete and mark the PVR as COMPLETED.
        The exception must not propagate out of the task.
        """
        pvr = self._make_pvr(
            request_id="GpvrReqID1501",
            callback_url="https://example.com/broken-callback",
        )
        with patch(
            "apps.payments.govstack_tasks.requests.post",
            side_effect=Exception("Network error"),
        ):
            # Task must NOT raise.
            validate_prepayment_async.apply(args=[str(pvr.pk)])

        pvr.refresh_from_db()
        self.assertEqual(
            pvr.status,
            PrepaymentValidationRequest.STATUS_COMPLETED,
            "PVR must be COMPLETED even if callback POST fails.",
        )

    # ------------------------------------------------------------------
    # G16 — validate_prepayment_async is idempotent
    # ------------------------------------------------------------------

    def test_g16_validate_prepayment_is_idempotent(self):
        pvr = self._make_pvr(request_id="GpvrReqID1601")
        validate_prepayment_async.apply(args=[str(pvr.pk)])
        validate_prepayment_async.apply(args=[str(pvr.pk)])  # second call

        pvr.refresh_from_db()
        self.assertEqual(pvr.status, PrepaymentValidationRequest.STATUS_COMPLETED)

        # Exactly one ACTION_VALIDATION_COMPLETED audit entry — the second invocation
        # exits early (idempotency guard) and must not write a duplicate entry.
        completed_entries = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_VALIDATION_COMPLETED,
            object_pk=str(pvr.pk),
        ).count()
        self.assertEqual(
            completed_entries,
            1,
            f"Expected exactly 1 ACTION_VALIDATION_COMPLETED audit entry, found {completed_entries}.",
        )
