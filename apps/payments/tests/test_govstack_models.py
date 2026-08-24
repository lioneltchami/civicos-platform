"""
test_govstack_models.py

Unit tests for GovStack Payments BB model layer (spec §18.1).

Coverage matrix:
  M1.  _generate_voucher_serial() — 18-digit numeric string, no leading zero
  M2.  _generate_voucher_serial() — returns str, not int; length is 16-20
       chars (harness schema 16-25 intersected with our max_length=20)
  M3.  GovStackBeneficiary.__str__() — excludes payee_functional_id (security)
  M4.  GovStackBeneficiary.payee_functional_id — unique constraint enforced
  M5.  BulkPaymentBatch.batch_id — unique constraint enforced
  M6.  CreditInstruction — unique_together ("batch", "instruction_id") enforced
  M7.  GovStackVoucher.transition_to() — valid transition succeeds
  M8.  GovStackVoucher.transition_to() — invalid transition raises ValueError
  M9.  GovStackVoucher.status_int — maps STATUS_ACTIVATED to correct int
  M10. GovStackVoucher.status_int — unrecognised status returns STATUS_ERROR_INT
  M11. GovStackVoucher.is_terminal — CONSUMED is terminal
  M12. GovStackVoucher.is_terminal — PREACTIVATED is not terminal
  M13. GovStackVoucher.STATUS_ERROR_INT == 9
  M14. GovStackPaymentAuditEntry — save() after initial creation raises PermissionError
  M15. GovStackPaymentAuditEntry — delete() on instance raises PermissionError
  M16. GovStackPaymentAuditEntry — QuerySet.delete() raises PermissionError
  M17. GovStackPaymentAuditEntry — QuerySet.update() raises PermissionError
  M18. GovStackPaymentAuditEntry — initial creation (save()) succeeds
  M19. GovStackVoucher.__str__() — includes serial_number and status
  M20. GovStackBeneficiary.is_active defaults to True
  M21. BulkPaymentBatch.request_id — _REQUEST_ID_VALIDATOR enforces exactly
       12 chars via full_clean() (M21/M21b/M21c); .objects.create() bypasses
       model validators entirely, unaffected by this tightening (M21d)

Security invariants tested:
  - payee_functional_id NEVER appears in GovStackBeneficiary.__str__()
  - GovStackPaymentAuditEntry is truly append-only: no mutation path succeeds
"""

from __future__ import annotations

from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase

from apps.payments.govstack_models import (
    BulkPaymentBatch,
    CreditInstruction,
    GovStackBeneficiary,
    GovStackPaymentAuditEntry,
    GovStackVoucher,
    _generate_voucher_serial,
)

# ============================================================================
# M1–M2  _generate_voucher_serial  # noqa: RUF003
# ============================================================================


class GenerateVoucherSerialTest(TestCase):
    """Tests for the _generate_voucher_serial() module-level function."""

    def test_m1_serial_is_in_range(self):
        """
        M1: Generated serial is an 18-digit numeric string with no leading
        zero — i.e. its integer value is between 10**17 and (10**18 - 1)
        inclusive. Run several times to reduce the probability of a flaky
        pass and to sanity-check uniqueness across samples.
        """
        seen = set()
        for _ in range(20):
            serial = _generate_voucher_serial()
            self.assertEqual(len(serial), 18, f"Serial {serial!r} is not 18 chars")
            self.assertTrue(serial.isdigit(), f"Serial {serial!r} is not purely numeric")
            self.assertNotEqual(serial[0], "0", f"Serial {serial!r} has a leading zero")
            value = int(serial)
            self.assertGreaterEqual(value, 10**17, f"Serial {serial!r} < 10**17")
            self.assertLessEqual(value, 10**18 - 1, f"Serial {serial!r} > 10**18 - 1")
            seen.add(serial)
        self.assertGreater(
            len(seen), 1, "20 samples produced only 1 unique value — suspiciously non-random"
        )

    def test_m2_serial_is_string(self):
        """
        M2: _generate_voucher_serial() returns a str, not an int, and its
        length (16-20 chars) satisfies both the GovStack harness's own JSON
        schema (16-25 chars, test/openAPI/features/support/helpers/helpers.js)
        and this codebase's own max_length=20 request-serializer ceiling
        (VoucherActivationRequestSerializer.voucher_serial_number,
        VoucherRedemptionRequestSerializer.voucher_number).
        """
        serial = _generate_voucher_serial()
        self.assertIsInstance(serial, str)
        self.assertTrue(
            16 <= len(serial) <= 20, f"Serial {serial!r} length {len(serial)} not in 16-20"
        )
        self.assertEqual(len(serial), 18)


# ============================================================================
# M3–M4  GovStackBeneficiary  # noqa: RUF003
# ============================================================================


class GovStackBeneficiaryModelTest(TestCase):
    """Tests for the GovStackBeneficiary model."""

    def _make(self, payee_id: str = "2ba5ed20-a1b2", **kwargs) -> GovStackBeneficiary:
        defaults = {
            "payee_functional_id": payee_id,
            "source_bb_id": "gs-bb-01",
            "financial_address": "DE89370400440532013000",
            "payment_modality": "BK",
            "is_active": True,
        }
        defaults.update(kwargs)
        return GovStackBeneficiary.objects.create(**defaults)

    def test_m3_str_excludes_payee_functional_id(self):
        """
        M3: GovStackBeneficiary.__str__() must NOT expose payee_functional_id.

        Security invariant: the payee_functional_id is a government-assigned
        identity and must never appear in log lines (which often call str() on
        model instances).
        """
        payee_id = "2ba5ed20-0f42-4eff-8"
        b = self._make(payee_id=payee_id)
        result = str(b)
        self.assertNotIn(
            payee_id,
            result,
            (f"payee_functional_id {payee_id!r} must not appear in __str__(). " f"Got: {result!r}"),
        )

    def test_m4_payee_functional_id_unique(self):
        """M4: payee_functional_id has a unique constraint — duplicate raises IntegrityError."""
        payee_id = "2ba5ed20-a1b2"
        self._make(payee_id=payee_id)
        with self.assertRaises(IntegrityError):
            self._make(payee_id=payee_id)

    def test_m20_is_active_defaults_to_true(self):
        """M20: GovStackBeneficiary.is_active defaults to True."""
        b = GovStackBeneficiary.objects.create(
            payee_functional_id="2ba5ed20-ffff",
            source_bb_id="gs-bb-01",
        )
        self.assertTrue(b.is_active)


# ============================================================================
# M5  BulkPaymentBatch
# ============================================================================


class BulkPaymentBatchModelTest(TestCase):
    """Tests for the BulkPaymentBatch model."""

    def _make(self, batch_id: str = "BATCH001", **kwargs) -> BulkPaymentBatch:
        defaults = {
            "batch_id": batch_id,
            "request_id": "REQ001",
            "source_bb_id": "gs-bb-01",
            "status": BulkPaymentBatch.STATUS_RECEIVED,
            "total_amount": Decimal("100.00"),
        }
        defaults.update(kwargs)
        return BulkPaymentBatch.objects.create(**defaults)

    def test_m5_batch_id_unique(self):
        """M5: BulkPaymentBatch.batch_id has unique=True — duplicate raises IntegrityError."""
        self._make(batch_id="BATCH-DUPE")
        with self.assertRaises(IntegrityError):
            self._make(batch_id="BATCH-DUPE")


# ============================================================================
# M21  BulkPaymentBatch.request_id — _REQUEST_ID_VALIDATOR (exactly 12 chars)
# ============================================================================


class RequestIdValidatorTest(TestCase):
    """
    Tests for _REQUEST_ID_VALIDATOR, applied to BulkPaymentBatch.request_id.

    Verified against a fresh clone of GovStackWorkingGroup/bb-payments
    (test/openAPI/features/support/helpers/helpers.js): g2pResponseSchema.RequestID
    is {minLength: 12, maxLength: 12} — this validator was previously 1-16 chars,
    which was over-permissive relative to the live spec.

    NOTE: .objects.create() does NOT call full_clean(), so these Django model
    validators only fire when full_clean() is explicitly invoked (they are not
    DB-level constraints). BulkPaymentBatchModelTest above legitimately uses
    non-12-char request_id values like "REQ001" via .objects.create() — that
    is unaffected by this tightened validator and continues to pass, because
    the validator is never invoked on that code path (the real request-time
    enforcement is in BulkPaymentRequestSerializer.RequestID's field-level
    validator, not the model layer).
    """

    def _make_unsaved(self, request_id: str) -> BulkPaymentBatch:
        return BulkPaymentBatch(
            batch_id="BATCH-VALIDATOR-TEST",
            request_id=request_id,
            source_bb_id="gs-bb-01",
            status=BulkPaymentBatch.STATUS_RECEIVED,
            total_amount=Decimal("100.00"),
        )

    def test_m21_exactly_12_chars_passes_full_clean(self):
        """M21: exactly-12-char request_id passes full_clean()."""
        batch = self._make_unsaved("RequestID111")  # 12 chars
        batch.full_clean()  # must not raise

    def test_m21b_eleven_chars_fails_full_clean(self):
        """M21b: an 11-char request_id fails full_clean() with a ValidationError."""
        from django.core.exceptions import ValidationError

        batch = self._make_unsaved("RequestID11")  # 11 chars
        with self.assertRaises(ValidationError):
            batch.full_clean()

    def test_m21c_thirteen_chars_fails_full_clean(self):
        """M21c: a 13-char request_id fails full_clean() with a ValidationError."""
        from django.core.exceptions import ValidationError

        batch = self._make_unsaved("RequestID1111")  # 13 chars
        with self.assertRaises(ValidationError):
            batch.full_clean()

    def test_m21d_objects_create_bypasses_validator_for_short_id(self):
        """
        M21d: .objects.create() with a non-12-char request_id succeeds because
        Django model validators are not enforced by .save()/.create() — only by
        full_clean(). This documents (not merely asserts) that the model-layer
        validator tightening in this change cannot break any existing
        .objects.create()-based test or code path.
        """
        batch = BulkPaymentBatch.objects.create(
            batch_id="BATCH-BYPASS-TEST",
            request_id="REQ001",  # 6 chars — would fail full_clean(), but not create()
            source_bb_id="gs-bb-01",
            status=BulkPaymentBatch.STATUS_RECEIVED,
            total_amount=Decimal("100.00"),
        )
        self.assertEqual(batch.request_id, "REQ001")


# ============================================================================
# M6  CreditInstruction
# ============================================================================


class CreditInstructionModelTest(TestCase):
    """Tests for the CreditInstruction model."""

    def setUp(self):
        self.batch = BulkPaymentBatch.objects.create(
            batch_id="BATCH-CI-001",
            request_id="REQ-CI-001",
            source_bb_id="gs-bb-01",
            status=BulkPaymentBatch.STATUS_RECEIVED,
            total_amount=Decimal("200.00"),
        )

    def test_m6_unique_together_batch_instruction_id(self):
        """
        M6: CreditInstruction.Meta.unique_together = [("batch", "instruction_id")].
        Same (batch, instruction_id) pair raises IntegrityError.
        Different instruction_id within the same batch is allowed.
        """
        CreditInstruction.objects.create(
            batch=self.batch,
            instruction_id="INSTR-001",
            payee_functional_id="2ba5ed20-aabb",
            amount=Decimal("100.00"),
            currency="USD",
            status=CreditInstruction.STATUS_PENDING,
        )
        # Duplicate (batch, instruction_id) must fail.
        with self.assertRaises(IntegrityError):
            CreditInstruction.objects.create(
                batch=self.batch,
                instruction_id="INSTR-001",  # same — must fail
                payee_functional_id="2ba5ed20-ccdd",
                amount=Decimal("50.00"),
                currency="USD",
                status=CreditInstruction.STATUS_PENDING,
            )

    def test_m6b_different_instruction_id_same_batch_allowed(self):
        """M6b: Two instructions with different instruction_ids in the same batch are allowed."""
        CreditInstruction.objects.create(
            batch=self.batch,
            instruction_id="INSTR-A",
            payee_functional_id="2ba5ed20-aa00",
            amount=Decimal("100.00"),
            currency="USD",
            status=CreditInstruction.STATUS_PENDING,
        )
        # Different instruction_id — must succeed.
        CreditInstruction.objects.create(
            batch=self.batch,
            instruction_id="INSTR-B",
            payee_functional_id="2ba5ed20-bb00",
            amount=Decimal("100.00"),
            currency="USD",
            status=CreditInstruction.STATUS_PENDING,
        )
        self.assertEqual(CreditInstruction.objects.filter(batch=self.batch).count(), 2)


# ============================================================================
# M7–M13, M19  GovStackVoucher  # noqa: RUF003
# ============================================================================


class GovStackVoucherModelTest(TestCase):
    """Tests for the GovStackVoucher model."""

    def _make(
        self, serial: str = "123456", status: str = GovStackVoucher.STATUS_PREACTIVATED
    ) -> GovStackVoucher:
        return GovStackVoucher.objects.create(
            serial_number=serial,
            amount=Decimal("50.00"),
            currency="USD",
            group_code="FOOD",
            status=status,
            issuing_bb="gs-bb-01",
        )

    def test_m7_valid_transition_succeeds(self):
        """M7: transition_to() with a valid next status updates in-memory status."""
        v = self._make(status=GovStackVoucher.STATUS_PREACTIVATED)
        v.transition_to(GovStackVoucher.STATUS_ACTIVATED)
        self.assertEqual(v.status, GovStackVoucher.STATUS_ACTIVATED)

    def test_m8_invalid_transition_raises_value_error(self):
        """M8: transition_to() raises ValueError when the transition is not allowed."""
        v = self._make(status=GovStackVoucher.STATUS_PREACTIVATED)
        # PREACTIVATED → CONSUMED is not in ALLOWED_TRANSITIONS.
        with self.assertRaises(ValueError):
            v.transition_to(GovStackVoucher.STATUS_CONSUMED)

    def test_m9_status_int_activated(self):
        """M9: status_int returns the integer code for STATUS_ACTIVATED (2)."""
        v = self._make(status=GovStackVoucher.STATUS_ACTIVATED)
        # STATUS_INT_MAP: activated → 2
        self.assertEqual(
            v.status_int, GovStackVoucher.STATUS_INT_MAP[GovStackVoucher.STATUS_ACTIVATED]
        )
        self.assertIsInstance(v.status_int, int)

    def test_m10_status_int_unrecognised_returns_error_int(self):
        """M10: status_int returns STATUS_ERROR_INT (9) for an unrecognised status string."""
        v = self._make()
        # Manually inject an unknown status bypassing the state machine.
        v.status = "__unknown__"
        self.assertEqual(v.status_int, GovStackVoucher.STATUS_ERROR_INT)

    def test_m11_is_terminal_consumed(self):
        """M11: CONSUMED is a terminal state."""
        v = self._make(status=GovStackVoucher.STATUS_CONSUMED)
        self.assertTrue(v.is_terminal)

    def test_m12_is_terminal_preactivated_false(self):
        """M12: PREACTIVATED is not terminal — further transitions are possible."""
        v = self._make(status=GovStackVoucher.STATUS_PREACTIVATED)
        self.assertFalse(v.is_terminal)

    def test_m13_status_error_int_is_nine(self):
        """M13: GovStackVoucher.STATUS_ERROR_INT must equal 9 (spec §13.5)."""
        self.assertEqual(GovStackVoucher.STATUS_ERROR_INT, 9)

    def test_m19_str_includes_serial_and_status(self):
        """M19: __str__() includes the serial_number and status for debug readability."""
        v = self._make(serial="777888", status=GovStackVoucher.STATUS_PREACTIVATED)
        result = str(v)
        self.assertIn("777888", result)
        self.assertIn(GovStackVoucher.STATUS_PREACTIVATED, result)

    def test_m19b_cancelled_is_terminal(self):
        """M19b: CANCELLED is terminal (no further transitions allowed)."""
        v = self._make(status=GovStackVoucher.STATUS_CANCELLED)
        self.assertTrue(v.is_terminal)


# ============================================================================
# M14–M18  GovStackPaymentAuditEntry (append-only enforcement)  # noqa: RUF003
# ============================================================================


class AuditEntryAppendOnlyTest(TestCase):
    """
    Tests that GovStackPaymentAuditEntry is truly append-only:
      - Initial create (save with no pk) succeeds.
      - save() on an existing instance (with pk) raises PermissionError.
      - instance.delete() raises PermissionError.
      - QuerySet.delete() raises PermissionError.
      - QuerySet.update() raises PermissionError.
    """

    def _make_entry(
        self, action: str = GovStackPaymentAuditEntry.ACTION_BENEFICIARY_REGISTERED
    ) -> GovStackPaymentAuditEntry:
        """Create a fresh audit entry. Should always succeed."""
        return GovStackPaymentAuditEntry.objects.create(
            action=action,
            actor_bb_id="gs-bb-test",
            object_type="beneficiary",
            object_pk="test-pk-001",
            request_id="REQ-TEST-01",
            details={"test": True},
        )

    def test_m18_initial_create_succeeds(self):
        """M18: Creating a new audit entry (save without pk) succeeds."""
        entry = self._make_entry()
        self.assertIsNotNone(entry.pk)
        self.assertEqual(entry.object_pk, "test-pk-001")

    def test_m14_save_after_creation_raises_permission_error(self):
        """
        M14: save() on an already-persisted audit entry raises PermissionError.
        This enforces append-only semantics — the audit trail cannot be tampered with.
        """
        entry = self._make_entry()
        # Attempt to mutate after initial creation.
        entry.actor_bb_id = "tampered-bb"
        with self.assertRaises(PermissionError):
            entry.save()

    def test_m15_instance_delete_raises_permission_error(self):
        """
        M15: instance.delete() raises PermissionError.
        Audit records are permanent and cannot be deleted.
        """
        entry = self._make_entry()
        with self.assertRaises(PermissionError):
            entry.delete()

    def test_m16_queryset_delete_raises_permission_error(self):
        """
        M16: QuerySet.delete() raises PermissionError.

        Django's standard QuerySet.delete() bypasses the model's delete() override
        and issues raw SQL DELETE. _AuditEntryQuerySet.delete() guards against this.
        """
        self._make_entry()
        with self.assertRaises(PermissionError):
            GovStackPaymentAuditEntry.objects.filter(actor_bb_id="gs-bb-test").delete()

    def test_m17_queryset_update_raises_permission_error(self):
        """
        M17: QuerySet.update() raises PermissionError.

        Django's QuerySet.update() issues raw SQL UPDATE bypassing save() overrides.
        _AuditEntryQuerySet.update() guards against this so the audit trail cannot
        be silently mutated by bulk-update calls.
        """
        self._make_entry()
        with self.assertRaises(PermissionError):
            GovStackPaymentAuditEntry.objects.filter(actor_bb_id="gs-bb-test").update(
                actor_bb_id="tampered"
            )

    def test_m17b_multiple_entries_same_object_allowed(self):
        """
        M17b: Multiple distinct audit entries for the same object_pk are allowed
        (idiomatic audit log — one row per event, not one row per object).
        """
        self._make_entry(action=GovStackPaymentAuditEntry.ACTION_BENEFICIARY_REGISTERED)
        self._make_entry(action=GovStackPaymentAuditEntry.ACTION_BENEFICIARY_UPDATED)
        count = GovStackPaymentAuditEntry.objects.filter(object_pk="test-pk-001").count()
        self.assertEqual(count, 2)
