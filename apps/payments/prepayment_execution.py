"""Durable internal gate between successful prepayment validation and execution."""

from __future__ import annotations

from django.db import IntegrityError, transaction

from .govstack_models import PrepaymentExecution, PrepaymentValidationRequest


class PrepaymentExecutionGateError(ValueError):
    pass


class PrepaymentValidationNotCompleted(PrepaymentExecutionGateError):  # noqa: N818
    pass


class PrepaymentValidationFailed(PrepaymentExecutionGateError):  # noqa: N818
    pass


class PrepaymentBeneficiaryNotFound(PrepaymentExecutionGateError):  # noqa: N818
    pass


class PrepaymentFinancialAddressInvalid(PrepaymentExecutionGateError):  # noqa: N818
    pass


class PrepaymentExecutionKeyConflict(PrepaymentExecutionGateError):  # noqa: N818
    pass


def admit_prepayment_execution(*, validation_request_id, execution_key: str) -> PrepaymentExecution:  # noqa: ANN001
    """Atomically admit one validated request; no provider I/O occurs here."""
    key = (execution_key or "").strip()
    if not key:
        raise PrepaymentExecutionKeyConflict("execution key is required")
    with transaction.atomic():
        request = PrepaymentValidationRequest.objects.select_for_update().get(
            pk=validation_request_id
        )
        if request.status == PrepaymentValidationRequest.STATUS_FAILED:
            raise PrepaymentValidationFailed("prepayment validation failed")
        if request.status != PrepaymentValidationRequest.STATUS_COMPLETED:
            raise PrepaymentValidationNotCompleted("prepayment validation is not completed")
        if request.beneficiary_found is not True:
            raise PrepaymentBeneficiaryNotFound("beneficiary was not found")
        if request.financial_address_valid is not True:
            raise PrepaymentFinancialAddressInvalid("financial address is invalid")

        existing = PrepaymentExecution.objects.filter(validation_request=request).first()
        if existing is not None:
            if existing.execution_key != key:
                raise PrepaymentExecutionKeyConflict(
                    "validation request already has another execution key"
                )
            return existing
        try:
            with transaction.atomic():
                return PrepaymentExecution.objects.create(
                    validation_request=request,
                    execution_key=key,
                )
        except IntegrityError:
            existing = PrepaymentExecution.objects.select_for_update().get(execution_key=key)
            if existing.validation_request_id != request.pk:
                raise PrepaymentExecutionKeyConflict(  # noqa: B904
                    "execution key belongs to another validation request"
                )
            return existing
