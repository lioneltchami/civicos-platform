from django.db import transaction
from django.test import TransactionTestCase

from apps.payments.govstack_models import PrepaymentExecution, PrepaymentValidationRequest
from apps.payments.prepayment_execution import (
    PrepaymentBeneficiaryNotFound,
    PrepaymentExecutionKeyConflict,
    PrepaymentFinancialAddressInvalid,
    PrepaymentValidationFailed,
    PrepaymentValidationNotCompleted,
    admit_prepayment_execution,
)


class PrepaymentExecutionGateTests(TransactionTestCase):
    def make_request(self, **changes):
        values = {
            "request_id": "REQ000000001",
            "source_bb_id": "source-bb",
            "batch_id": "batch-001",
            "instruction_id": "instruction-001",
            "payee_functional_id": "payee-001",
            "amount": "10.00",
            "currency": "USD",
            "status": PrepaymentValidationRequest.STATUS_COMPLETED,
            "beneficiary_found": True,
            "financial_address_valid": True,
        }
        values.update(changes)
        return PrepaymentValidationRequest.objects.create(**values)

    def test_success_and_same_key_replay_create_one_admission(self):
        request = self.make_request()
        first = admit_prepayment_execution(validation_request_id=request.pk, execution_key="execution-1")
        second = admit_prepayment_execution(validation_request_id=request.pk, execution_key="execution-1")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PrepaymentExecution.objects.count(), 1)

    def test_conflicting_key_and_all_validation_failures_create_nothing(self):
        request = self.make_request()
        admit_prepayment_execution(validation_request_id=request.pk, execution_key="execution-1")
        with self.assertRaises(PrepaymentExecutionKeyConflict):
            admit_prepayment_execution(validation_request_id=request.pk, execution_key="execution-2")

        cases = [
            (PrepaymentValidationRequest.STATUS_PENDING, True, True, PrepaymentValidationNotCompleted),
            (PrepaymentValidationRequest.STATUS_FAILED, True, True, PrepaymentValidationFailed),
            (PrepaymentValidationRequest.STATUS_COMPLETED, False, True, PrepaymentBeneficiaryNotFound),
            (PrepaymentValidationRequest.STATUS_COMPLETED, True, False, PrepaymentFinancialAddressInvalid),
        ]
        for offset, (status, beneficiary, address, error) in enumerate(cases, start=2):
            invalid = self.make_request(
                request_id=f"REQ{offset:09d}",
                status=status,
                beneficiary_found=beneficiary,
                financial_address_valid=address,
            )
            with self.assertRaises(error):
                admit_prepayment_execution(validation_request_id=invalid.pk, execution_key=f"execution-{offset}")
        self.assertEqual(PrepaymentExecution.objects.count(), 1)

    def test_rollback_leaves_no_execution_admission(self):
        request = self.make_request()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                admit_prepayment_execution(validation_request_id=request.pk, execution_key="rollback-key")
                raise RuntimeError("force rollback")
        self.assertFalse(PrepaymentExecution.objects.filter(execution_key="rollback-key").exists())
