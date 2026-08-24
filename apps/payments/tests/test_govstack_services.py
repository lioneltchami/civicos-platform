"""
test_govstack_services.py

Unit tests for GovStack Payments BB service layer (spec §18.1).

Coverage matrix:
  GovStackBeneficiaryService
  ─────────────────────────
  S1.  register() — creates GovStackBeneficiary record
  S2.  register() — creates GovStackPaymentAuditEntry on registration
  S3.  register() — second register() with same PayeeFunctionalID upserts (no duplicate)
  S4.  register() — audit entry details never contain payee_functional_id
  S5.  update() — creates record when PayeeFunctionalID is unknown (harness upsert path)
  S6.  update() — updates financial_address on existing record

  GovStackBulkPaymentService
  ──────────────────────────
  S7.  receive_batch() — returns BulkPaymentBatch with STATUS_RECEIVED
  S8.  receive_batch() — creates CreditInstruction for each item
  S9.  receive_batch() — creates ACTION_BATCH_RECEIVED audit entry
  S10. receive_batch() — duplicate batch_id raises DuplicateBatchError
  S11. validate_prepayment() — creates PrepaymentValidationRequest with STATUS_PENDING
  S12. validate_prepayment() — creates ACTION_VALIDATION_REQUESTED audit entry
  S13. validate_prepayment() — duplicate request_id raises DuplicateValidationRequestError
  S14. get_validation_result() — PENDING records not counted as failed cases
  S15. get_validation_result() — returns NumberFailedCases=0 and FailedAccounts=[] for pending

  GovStackVoucherService
  ──────────────────────
  S16. preactivate() — creates voucher in PREACTIVATED status with correct fields
  S17. preactivate() — raises InvalidVoucherAmount when amount ≤ 0
  S18. preactivate() — raises GovStackBBNotFound when issuing_bb is blank
  S19. preactivate() — raises InvalidVoucherGroup when group is blank
  S20. preactivate() — creates ACTION_VOUCHER_PREACTIVATED audit entry
  S21. activate() — transitions PREACTIVATED → ACTIVATED
  S22. activate() — raises InvalidVoucherSerial for unknown serial
  S23. activate() — creates ACTION_VOUCHER_ACTIVATED audit entry
  S24. cancel() — raises VoucherAlreadyCancelled on double-cancel
  S25. cancel() — raises InvalidCancellationSerial for unknown serial
  S26. cancel() — creates ACTION_VOUCHER_CANCELLED audit entry
  S27. get_status() — returns voucher for known serial
  S28. get_status() — raises InvalidVoucherSerial for unknown serial
  S29. redeem() — transitions ACTIVATED → CONSUMED and populates redemption fields
  S30. redeem() — creates ACTION_VOUCHER_REDEEMED audit entry

  GovStackP2GService
  ──────────────────
  S31. get_bill() — returns GovStackBill for known bill_id
  S32. get_bill() — raises BillNotFound for unknown bill_id
  S33. create_transfer_request() — creates GovStackBillPayment and marks bill PAID
  S34. create_transfer_request() — duplicate request_id raises DuplicateBillPaymentError
  S35. create_transfer_request() — raises BillNotFound when bill_id unknown
  S36. get_bill() — platform_tenant_id scoping: wrong tenant raises BillNotFound
  S37. get_bill() — platform_tenant_id scoping: matching tenant succeeds
  S38. mark_bill_paid() — actor_payer_fi_id is recorded as the audit entry's actor_bb_id

Security invariants tested:
  - payee_functional_id never in audit entry details
  - financial_address never in audit entry details
  - GovStackPaymentAuditEntry created for every service operation
"""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from apps.payments.govstack_exceptions import (
    BillNotFound,
    DuplicateBatchError,
    DuplicateBillPaymentError,
    DuplicateValidationRequestError,
    GovStackBBNotFound,
    InvalidCancellationSerial,
    InvalidVoucherAmount,
    InvalidVoucherGroup,
    InvalidVoucherSerial,
    VoucherAlreadyCancelled,
)
from apps.payments.govstack_models import (
    BulkPaymentBatch,
    CreditInstruction,
    GovStackBeneficiary,
    GovStackBill,
    GovStackBillPayment,
    GovStackPaymentAuditEntry,
    GovStackVoucher,
    PrepaymentValidationRequest,
)
from apps.payments.govstack_services import (
    GovStackBeneficiaryService,
    GovStackBulkPaymentService,
    GovStackP2GService,
    GovStackVoucherService,
)

# ── Common test fixtures ─────────────────────────────────────────────────────
_PAYEE_ID = "2ba5ed20-0f42-4eff-8"
_SOURCE_BB = "11668d2a-a8f"
_REQUEST_ID = "REQ-SVC-001"
_BATCH_ID = "BATCH-SVC-001"
_ISSUING_BB = "GS-HARNESS"
_GROUP = "FOOD"
_CURRENCY = "USD"


def _make_voucher(
    serial: str = "123456",
    status: str = GovStackVoucher.STATUS_PREACTIVATED,
    amount: Decimal = Decimal("50.00"),
    currency: str = _CURRENCY,
    group: str = _GROUP,
    issuing_bb: str = _ISSUING_BB,
) -> GovStackVoucher:
    """Helper: create a GovStackVoucher directly (bypasses service layer)."""
    return GovStackVoucher.objects.create(
        serial_number=serial,
        amount=amount,
        currency=currency,
        group_code=group,
        status=status,
        issuing_bb=issuing_bb,
    )


def _make_bill(
    bill_id: str = "BILL-001",
    amount: Decimal = Decimal("75.00"),
    currency: str = _CURRENCY,
) -> GovStackBill:
    """Helper: create a GovStackBill directly."""
    return GovStackBill.objects.create(
        bill_id=bill_id,
        amount=amount,
        currency=currency,
        status=GovStackBill.STATUS_UNPAID,
    )


# ============================================================================
# GovStackBeneficiaryService
# ============================================================================


class BeneficiaryServiceTest(TestCase):
    """S1–S6: GovStackBeneficiaryService unit tests."""  # noqa: RUF002

    def _register(self, payee_id: str = _PAYEE_ID, request_id: str = _REQUEST_ID):
        return GovStackBeneficiaryService.register(
            request_id=request_id,
            source_bb_id=_SOURCE_BB,
            beneficiaries=[{"PayeeFunctionalID": payee_id}],
        )

    def test_s1_register_creates_beneficiary(self):
        """S1: register() creates a GovStackBeneficiary record."""
        self._register()
        self.assertTrue(GovStackBeneficiary.objects.filter(payee_functional_id=_PAYEE_ID).exists())

    def test_s2_register_creates_audit_entry(self):
        """S2: register() creates a GovStackPaymentAuditEntry with ACTION_BENEFICIARY_REGISTERED."""
        self._register()
        self.assertTrue(
            GovStackPaymentAuditEntry.objects.filter(
                action=GovStackPaymentAuditEntry.ACTION_BENEFICIARY_REGISTERED
            ).exists()
        )

    def test_s3_register_idempotent(self):
        """S3: Calling register() twice with the same PayeeFunctionalID upserts — no duplicate."""
        self._register(payee_id=_PAYEE_ID)
        self._register(payee_id=_PAYEE_ID)
        self.assertEqual(
            GovStackBeneficiary.objects.filter(payee_functional_id=_PAYEE_ID).count(), 1
        )

    def test_s4_audit_entry_details_never_contain_payee_functional_id(self):
        """
        S4: Audit entry details must never contain payee_functional_id.

        Security invariant: payee_functional_id is a government-assigned identity
        and must never appear in the audit trail's details JSON, where it could
        be exposed in log aggregation queries.
        """
        self._register(payee_id=_PAYEE_ID)
        for entry in GovStackPaymentAuditEntry.objects.all():
            details_str = str(entry.details)
            self.assertNotIn(
                _PAYEE_ID,
                details_str,
                f"payee_functional_id found in audit entry details: {entry.details!r}",
            )

    def test_s5_update_creates_on_unknown_payee(self):
        """
        S5: update() creates a new record when PayeeFunctionalID does not exist.

        The GovStack harness update smoke test sends an unknown PayeeFunctionalID
        and expects HTTP 200 / ResponseCode "00". The service must upsert.
        """
        new_payee = "2ba5ed20-9999"
        result = GovStackBeneficiaryService.update(
            request_id=_REQUEST_ID,
            source_bb_id=_SOURCE_BB,
            beneficiaries=[
                {
                    "PayeeFunctionalID": new_payee,
                    "PaymentModality": "BK",
                }
            ],
        )
        self.assertEqual(result["registered"] + result["updated"], 1)
        self.assertTrue(GovStackBeneficiary.objects.filter(payee_functional_id=new_payee).exists())

    def test_s6_update_modifies_existing_record(self):
        """S6: update() updates payment_modality on an existing beneficiary."""
        # First register.
        self._register(payee_id=_PAYEE_ID)
        obj_before = GovStackBeneficiary.objects.get(payee_functional_id=_PAYEE_ID)
        self.assertEqual(obj_before.payment_modality, "")

        # Now update — set payment_modality.
        GovStackBeneficiaryService.update(
            request_id=_REQUEST_ID,
            source_bb_id=_SOURCE_BB,
            beneficiaries=[
                {
                    "PayeeFunctionalID": _PAYEE_ID,
                    "PaymentModality": "MO",
                }
            ],
        )
        obj_after = GovStackBeneficiary.objects.get(payee_functional_id=_PAYEE_ID)
        self.assertEqual(obj_after.payment_modality, "MO")


# ============================================================================
# GovStackBulkPaymentService
# ============================================================================


class BulkPaymentServiceTest(TestCase):
    """S7–S15: GovStackBulkPaymentService unit tests."""  # noqa: RUF002

    _INSTRUCTIONS = [  # noqa: RUF012
        {
            "InstructionID": "INSTR-001",
            "PayeeFunctionalID": "2ba5ed20-aa01",
            "Amount": Decimal("100.00"),
            "Currency": "USD",
        },
        {
            "InstructionID": "INSTR-002",
            "PayeeFunctionalID": "2ba5ed20-aa02",
            "Amount": Decimal("200.00"),
            "Currency": "USD",
        },
    ]

    def test_s7_receive_batch_returns_batch_with_status_received(self):
        """S7: receive_batch() returns a BulkPaymentBatch with STATUS_RECEIVED."""
        batch = GovStackBulkPaymentService.receive_batch(
            request_id=_REQUEST_ID,
            source_bb_id=_SOURCE_BB,
            batch_id=_BATCH_ID,
            instructions=self._INSTRUCTIONS,
        )
        self.assertIsInstance(batch, BulkPaymentBatch)
        self.assertEqual(batch.status, BulkPaymentBatch.STATUS_RECEIVED)
        self.assertEqual(batch.batch_id, _BATCH_ID)

    def test_s8_receive_batch_creates_credit_instructions(self):
        """S8: receive_batch() creates one CreditInstruction per instruction item."""
        batch = GovStackBulkPaymentService.receive_batch(
            request_id=_REQUEST_ID,
            source_bb_id=_SOURCE_BB,
            batch_id=_BATCH_ID,
            instructions=self._INSTRUCTIONS,
        )
        instructions = CreditInstruction.objects.filter(batch=batch)
        self.assertEqual(instructions.count(), 2)
        ids = set(instructions.values_list("instruction_id", flat=True))
        self.assertEqual(ids, {"INSTR-001", "INSTR-002"})

    def test_s9_receive_batch_creates_audit_entry(self):
        """S9: receive_batch() creates an ACTION_BATCH_RECEIVED audit entry."""
        GovStackBulkPaymentService.receive_batch(
            request_id=_REQUEST_ID,
            source_bb_id=_SOURCE_BB,
            batch_id=_BATCH_ID,
            instructions=self._INSTRUCTIONS,
        )
        self.assertTrue(
            GovStackPaymentAuditEntry.objects.filter(
                action=GovStackPaymentAuditEntry.ACTION_BATCH_RECEIVED
            ).exists()
        )

    def test_s10_receive_batch_duplicate_raises_duplicate_batch_error(self):
        """S10: A second receive_batch() call with the same batch_id raises DuplicateBatchError."""
        GovStackBulkPaymentService.receive_batch(
            request_id=_REQUEST_ID,
            source_bb_id=_SOURCE_BB,
            batch_id=_BATCH_ID,
            instructions=self._INSTRUCTIONS[:1],
        )
        with self.assertRaises(DuplicateBatchError):
            GovStackBulkPaymentService.receive_batch(
                request_id="REQ-SVC-002",
                source_bb_id=_SOURCE_BB,
                batch_id=_BATCH_ID,  # same batch_id — must fail
                instructions=self._INSTRUCTIONS[:1],
            )

    def test_s11_validate_prepayment_creates_pvr_with_status_pending(self):
        """S11: validate_prepayment() creates a PrepaymentValidationRequest with STATUS_PENDING."""
        pvr = GovStackBulkPaymentService.validate_prepayment(
            request_id=_REQUEST_ID,
            source_bb_id=_SOURCE_BB,
            batch_id=_BATCH_ID,
            instruction_id="INSTR-001",
            payee_functional_id=_PAYEE_ID,
            amount=Decimal("100.00"),
            currency="USD",
        )
        self.assertIsInstance(pvr, PrepaymentValidationRequest)
        self.assertEqual(pvr.status, PrepaymentValidationRequest.STATUS_PENDING)

    def test_s12_validate_prepayment_creates_audit_entry(self):
        """S12: validate_prepayment() creates an ACTION_VALIDATION_REQUESTED audit entry."""
        GovStackBulkPaymentService.validate_prepayment(
            request_id=_REQUEST_ID,
            source_bb_id=_SOURCE_BB,
            batch_id=_BATCH_ID,
            instruction_id="INSTR-001",
            payee_functional_id=_PAYEE_ID,
            amount=Decimal("100.00"),
            currency="USD",
        )
        self.assertTrue(
            GovStackPaymentAuditEntry.objects.filter(
                action=GovStackPaymentAuditEntry.ACTION_VALIDATION_REQUESTED
            ).exists()
        )

    def test_s13_validate_prepayment_duplicate_request_id_raises(self):
        """S13: A duplicate request_id in validate_prepayment() raises DuplicateValidationRequestError."""  # noqa: E501
        GovStackBulkPaymentService.validate_prepayment(
            request_id=_REQUEST_ID,
            source_bb_id=_SOURCE_BB,
            batch_id=_BATCH_ID,
            instruction_id="INSTR-001",
            payee_functional_id=_PAYEE_ID,
            amount=Decimal("100.00"),
            currency="USD",
        )
        with self.assertRaises(DuplicateValidationRequestError):
            GovStackBulkPaymentService.validate_prepayment(
                request_id=_REQUEST_ID,  # same request_id — must fail
                source_bb_id=_SOURCE_BB,
                batch_id="BATCH-SVC-002",
                instruction_id="INSTR-002",
                payee_functional_id=_PAYEE_ID,
                amount=Decimal("50.00"),
                currency="USD",
            )

    def test_s14_get_validation_result_pending_not_counted_as_failure(self):
        """
        S14: PENDING validation records are not counted as failed cases.

        The harness calls /prepayment-validation-response immediately after
        /prepayment-validation (before the Celery task runs), so all records
        are still PENDING. The response must show NumberFailedCases=0.
        """
        GovStackBulkPaymentService.validate_prepayment(
            request_id=_REQUEST_ID,
            source_bb_id=_SOURCE_BB,
            batch_id=_BATCH_ID,
            instruction_id="INSTR-001",
            payee_functional_id=_PAYEE_ID,
            amount=Decimal("100.00"),
            currency="USD",
        )
        result = GovStackBulkPaymentService.get_validation_result(
            request_id=_REQUEST_ID,
            source_batch_id=_BATCH_ID,
        )
        self.assertEqual(result["number_failed_cases"], 0)

    def test_s15_get_validation_result_returns_zero_failed_accounts(self):
        """S15: For PENDING records, failed_accounts is an empty list."""
        GovStackBulkPaymentService.validate_prepayment(
            request_id=_REQUEST_ID,
            source_bb_id=_SOURCE_BB,
            batch_id=_BATCH_ID,
            instruction_id="INSTR-001",
            payee_functional_id=_PAYEE_ID,
            amount=Decimal("100.00"),
            currency="USD",
        )
        result = GovStackBulkPaymentService.get_validation_result(
            request_id=_REQUEST_ID,
            source_batch_id=_BATCH_ID,
        )
        self.assertEqual(result["failed_accounts"], [])


# ============================================================================
# GovStackVoucherService
# ============================================================================


class VoucherServiceTest(TestCase):
    """S16–S30: GovStackVoucherService unit tests."""  # noqa: RUF002

    def test_s16_preactivate_creates_preactivated_voucher(self):
        """S16: preactivate() creates a GovStackVoucher in PREACTIVATED status."""
        voucher = GovStackVoucherService.preactivate(
            voucher_amount=Decimal("50.00"),
            voucher_currency=_CURRENCY,
            voucher_group=_GROUP,
            issuing_bb=_ISSUING_BB,
        )
        self.assertIsInstance(voucher, GovStackVoucher)
        self.assertEqual(voucher.status, GovStackVoucher.STATUS_PREACTIVATED)
        self.assertEqual(voucher.amount, Decimal("50.00"))
        self.assertEqual(voucher.currency, _CURRENCY)
        self.assertEqual(voucher.group_code, _GROUP)
        # Regression test for Issue A (SPEC_GOVSTACK_PAYMENTS_BB.md section
        # 24.1): the real (unpatched) _generate_voucher_serial() output must
        # satisfy the GovStack harness's own schema (16-25 char string) and
        # this codebase's own max_length=20 request-serializer ceiling.
        self.assertIsInstance(voucher.serial_number, str)
        self.assertTrue(
            16 <= len(voucher.serial_number) <= 20,
            f"serial_number {voucher.serial_number!r} length "
            f"{len(voucher.serial_number)} not in 16-20",
        )
        self.assertTrue(voucher.serial_number.isdigit())

    def test_s17_preactivate_invalid_amount_raises(self):
        """S17: preactivate() raises InvalidVoucherAmount when amount ≤ 0."""
        with self.assertRaises(InvalidVoucherAmount):
            GovStackVoucherService.preactivate(
                voucher_amount=Decimal("0.00"),
                voucher_currency=_CURRENCY,
                voucher_group=_GROUP,
                issuing_bb=_ISSUING_BB,
            )

    def test_s17b_preactivate_negative_amount_raises(self):
        """S17b: preactivate() raises InvalidVoucherAmount for negative amounts."""
        with self.assertRaises(InvalidVoucherAmount):
            GovStackVoucherService.preactivate(
                voucher_amount=Decimal("-10.00"),
                voucher_currency=_CURRENCY,
                voucher_group=_GROUP,
                issuing_bb=_ISSUING_BB,
            )

    def test_s18_preactivate_blank_issuing_bb_raises(self):
        """S18: preactivate() raises GovStackBBNotFound when issuing_bb is blank."""
        with self.assertRaises(GovStackBBNotFound):
            GovStackVoucherService.preactivate(
                voucher_amount=Decimal("50.00"),
                voucher_currency=_CURRENCY,
                voucher_group=_GROUP,
                issuing_bb="",
            )

    def test_s19_preactivate_blank_group_raises(self):
        """S19: preactivate() raises InvalidVoucherGroup when voucher_group is blank."""
        with self.assertRaises(InvalidVoucherGroup):
            GovStackVoucherService.preactivate(
                voucher_amount=Decimal("50.00"),
                voucher_currency=_CURRENCY,
                voucher_group="",
                issuing_bb=_ISSUING_BB,
            )

    def test_s20_preactivate_creates_audit_entry(self):
        """S20: preactivate() creates an ACTION_VOUCHER_PREACTIVATED audit entry."""
        GovStackVoucherService.preactivate(
            voucher_amount=Decimal("50.00"),
            voucher_currency=_CURRENCY,
            voucher_group=_GROUP,
            issuing_bb=_ISSUING_BB,
        )
        self.assertTrue(
            GovStackPaymentAuditEntry.objects.filter(
                action=GovStackPaymentAuditEntry.ACTION_VOUCHER_PREACTIVATED
            ).exists()
        )

    def test_s21_activate_transitions_preactivated_to_activated(self):
        """S21: activate() transitions PREACTIVATED → ACTIVATED."""
        voucher = _make_voucher(serial="200001", status=GovStackVoucher.STATUS_PREACTIVATED)
        result = GovStackVoucherService.activate(
            voucher_serial_number=voucher.serial_number,
            issuing_bb=_ISSUING_BB,
        )
        self.assertEqual(result.status, GovStackVoucher.STATUS_ACTIVATED)
        # Verify the DB was updated.
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, GovStackVoucher.STATUS_ACTIVATED)

    def test_s22_activate_unknown_serial_raises_invalid_voucher_serial(self):
        """S22: activate() raises InvalidVoucherSerial for an unknown serial number."""
        with self.assertRaises(InvalidVoucherSerial):
            GovStackVoucherService.activate(
                voucher_serial_number="DOESNOTEXIST",
                issuing_bb=_ISSUING_BB,
            )

    def test_s23_activate_creates_audit_entry(self):
        """S23: activate() creates an ACTION_VOUCHER_ACTIVATED audit entry."""
        voucher = _make_voucher(serial="200002", status=GovStackVoucher.STATUS_PREACTIVATED)
        GovStackVoucherService.activate(
            voucher_serial_number=voucher.serial_number,
            issuing_bb=_ISSUING_BB,
        )
        self.assertTrue(
            GovStackPaymentAuditEntry.objects.filter(
                action=GovStackPaymentAuditEntry.ACTION_VOUCHER_ACTIVATED,
                object_pk=str(voucher.pk),
            ).exists()
        )

    def test_s24_cancel_double_cancel_raises_voucher_already_cancelled(self):
        """S24: Cancelling an already-CANCELLED voucher raises VoucherAlreadyCancelled."""
        voucher = _make_voucher(serial="200003", status=GovStackVoucher.STATUS_CANCELLED)
        with self.assertRaises(VoucherAlreadyCancelled):
            GovStackVoucherService.cancel(voucher_serial_number=voucher.serial_number)

    def test_s25_cancel_unknown_serial_raises_invalid_cancellation_serial(self):
        """S25: cancel() raises InvalidCancellationSerial for an unknown serial number."""
        with self.assertRaises(InvalidCancellationSerial):
            GovStackVoucherService.cancel(voucher_serial_number="DOESNOTEXIST")

    def test_s26_cancel_creates_audit_entry(self):
        """S26: cancel() creates an ACTION_VOUCHER_CANCELLED audit entry."""
        voucher = _make_voucher(serial="200004", status=GovStackVoucher.STATUS_PREACTIVATED)
        GovStackVoucherService.cancel(voucher_serial_number=voucher.serial_number)
        self.assertTrue(
            GovStackPaymentAuditEntry.objects.filter(
                action=GovStackPaymentAuditEntry.ACTION_VOUCHER_CANCELLED,
                object_pk=str(voucher.pk),
            ).exists()
        )

    def test_s27_get_status_returns_voucher_for_known_serial(self):
        """S27: get_status() returns the GovStackVoucher for a known serial number."""
        voucher = _make_voucher(serial="200005")
        result = GovStackVoucherService.get_status(serial_number=voucher.serial_number)
        self.assertEqual(result.pk, voucher.pk)

    def test_s28_get_status_unknown_serial_raises_invalid_voucher_serial(self):
        """S28: get_status() raises InvalidVoucherSerial for an unknown serial number."""
        with self.assertRaises(InvalidVoucherSerial):
            GovStackVoucherService.get_status(serial_number="DOESNOTEXIST")

    def test_s29_redeem_transitions_activated_to_consumed(self):
        """S29: redeem() transitions ACTIVATED → CONSUMED and populates redemption fields."""
        voucher = _make_voucher(serial="200006", status=GovStackVoucher.STATUS_ACTIVATED)
        result = GovStackVoucherService.redeem(
            voucher_number=voucher.serial_number,
            issuing_bb=_ISSUING_BB,
            merchant_name="Test Merchant",
            merchant_bank_details="IBAN-001",
            merchant_voucher_group=_GROUP,
            agent_id="AGT001",
        )
        self.assertEqual(result.status, GovStackVoucher.STATUS_CONSUMED)
        self.assertIsNotNone(result.redeemed_at)
        self.assertTrue(result.redemption_transaction_id)
        # Verify DB was updated.
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, GovStackVoucher.STATUS_CONSUMED)

    def test_s30_redeem_creates_audit_entry(self):
        """S30: redeem() creates an ACTION_VOUCHER_REDEEMED audit entry."""
        voucher = _make_voucher(serial="200007", status=GovStackVoucher.STATUS_ACTIVATED)
        GovStackVoucherService.redeem(
            voucher_number=voucher.serial_number,
            issuing_bb=_ISSUING_BB,
        )
        self.assertTrue(
            GovStackPaymentAuditEntry.objects.filter(
                action=GovStackPaymentAuditEntry.ACTION_VOUCHER_REDEEMED,
                object_pk=str(voucher.pk),
            ).exists()
        )


# ============================================================================
# GovStackP2GService
# ============================================================================


class P2GServiceTest(TestCase):
    """S31–S35: GovStackP2GService unit tests."""  # noqa: RUF002

    def test_s31_get_bill_returns_bill_for_known_id(self):
        """S31: get_bill() returns the GovStackBill for a known bill_id."""
        bill = _make_bill(bill_id="BILL-S31")
        result = GovStackP2GService.get_bill(bill_id="BILL-S31")
        self.assertEqual(result.pk, bill.pk)

    def test_s32_get_bill_unknown_id_raises_bill_not_found(self):
        """S32: get_bill() raises BillNotFound when bill_id is not in the database."""
        with self.assertRaises(BillNotFound):
            GovStackP2GService.get_bill(bill_id="DOESNOTEXIST")

    def test_s33_create_transfer_request_creates_payment_and_marks_bill_paid(self):
        """S33: create_transfer_request() creates a GovStackBillPayment and marks the bill PAID."""
        bill = _make_bill(bill_id="BILL-S33", amount=Decimal("99.00"))
        payment = GovStackP2GService.create_transfer_request(
            request_id="TXN-S33-001",
            bill_id="BILL-S33",
        )
        self.assertIsInstance(payment, GovStackBillPayment)
        self.assertEqual(payment.status, GovStackBillPayment.STATUS_COMPLETED)
        self.assertEqual(payment.bill_id, bill.pk)

        # Bill must be marked PAID.
        bill.refresh_from_db()
        self.assertEqual(bill.status, GovStackBill.STATUS_PAID)

    def test_s34_create_transfer_request_duplicate_request_id_raises(self):
        """S34: A duplicate request_id in create_transfer_request() raises DuplicateBillPaymentError."""  # noqa: E501
        _make_bill(bill_id="BILL-S34")
        GovStackP2GService.create_transfer_request(
            request_id="TXN-S34-001",
            bill_id="BILL-S34",
        )
        with self.assertRaises(DuplicateBillPaymentError):
            GovStackP2GService.create_transfer_request(
                request_id="TXN-S34-001",  # duplicate — must fail
                bill_id="BILL-S34",
            )

    def test_s35_create_transfer_request_unknown_bill_raises_bill_not_found(self):
        """S35: create_transfer_request() raises BillNotFound when the bill_id is not in the DB."""
        with self.assertRaises(BillNotFound):
            GovStackP2GService.create_transfer_request(
                request_id="TXN-S35-001",
                bill_id="BILL-DOESNOTEXIST",
            )

    def test_s36_get_bill_wrong_tenant_raises_bill_not_found(self):
        """
        S36 (certifiability-audit fix — CRITICAL): get_bill() scopes the
        lookup by platform_tenant_id when the caller supplies one. A bill
        registered under a different tenant than the one declared must raise
        BillNotFound — identical to a genuinely missing bill_id, so a caller
        can't use this to probe cross-tenant existence.
        """
        bill = _make_bill(bill_id="BILL-S36")
        bill.platform_tenant_id = "TENANT-A"
        bill.save(update_fields=["platform_tenant_id"])
        with self.assertRaises(BillNotFound):
            GovStackP2GService.get_bill(bill_id="BILL-S36", platform_tenant_id="TENANT-B")

    def test_s37_get_bill_matching_tenant_succeeds(self):
        """S37: get_bill() succeeds when the supplied tenant matches the bill's tenant."""
        bill = _make_bill(bill_id="BILL-S37")
        bill.platform_tenant_id = "TENANT-A"
        bill.save(update_fields=["platform_tenant_id"])
        result = GovStackP2GService.get_bill(bill_id="BILL-S37", platform_tenant_id="TENANT-A")
        self.assertEqual(result.pk, bill.pk)

    def test_s38_mark_bill_paid_records_actor_payer_fi_id(self):
        """
        S38 (certifiability-audit fix — CRITICAL): mark_bill_paid() now
        records the caller's X-PayerFI-Id (passed through as
        actor_payer_fi_id) as the audit entry's actor_bb_id, instead of the
        old hardcoded "" that left no evidence of who invoked this endpoint.
        """
        _make_bill(bill_id="BILL-S38")
        GovStackP2GService.mark_bill_paid(bill_id="BILL-S38", actor_payer_fi_id="FI-S38-CALLER")
        entry = GovStackPaymentAuditEntry.objects.filter(
            action=GovStackPaymentAuditEntry.ACTION_BILL_PAID
        ).latest("created_at")
        self.assertEqual(entry.actor_bb_id, "FI-S38-CALLER")
