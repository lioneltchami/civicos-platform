"""
GovStack Payments BB — service layer.

All business logic lives here. Views call services; services never call views.
Services may call Celery tasks but never call each other across domain boundaries
(G2P services do not call Voucher services and vice versa).

Design principles:
  - Each public method is a single atomic unit of work.
  - Methods that trigger async processing return immediately after
    persisting the request; Celery tasks handle the rest.
  - No HTTP request/response objects in this layer — services receive
    plain Python values and return model instances or raise exceptions.
  - PII (payee_functional_id, financial_address) never written to logs.
    Use str(obj.pk) in all log messages.

Wave status:
  Wave 1: Stubs only — method signatures defined, NotImplementedError raised.
  Wave 2: GovStackBeneficiaryService fully implemented.
  Wave 3: GovStackBulkPaymentService fully implemented.
  Wave 4: GovStackVoucherService fully implemented.
  Wave 5: GovStackP2GService fully implemented.
"""
from __future__ import annotations

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GovStackBeneficiaryService  (Wave 2)
# ---------------------------------------------------------------------------

class GovStackBeneficiaryService:
    """
    G2P ID Mapper: register and update beneficiaries.

    GovStack spec: RegisterBeneficiaryRequest.yml, UpdateBeneficiaryRequest.yml
    Harness features: g2p_register_beneficiary, g2p_update_beneficiary_details
    """

    @staticmethod
    def register(
        request_id: str,
        source_bb_id: str,
        beneficiaries: list[dict],
        registering_institution_id: str = "",
        callback_url: str = "",
    ) -> dict:
        """
        Register one or more beneficiaries in the ID Mapper.

        Idempotent: if a PayeeFunctionalID already exists, the record is
        updated (upsert). Returns a summary dict for the response.

        Args:
            request_id: RequestID from the request body.
            source_bb_id: SourceBBID from the request body.
            beneficiaries: list of dicts with PayeeFunctionalID,
                           optional PaymentModality, optional FinancialAddress.
            registering_institution_id: X-Registering-Institution-ID header.
            callback_url: X-Callback-URL header.

        Returns:
            {"registered": N, "updated": M}

        Raises:
            NotImplementedError: until Wave 2 implementation.
        """
        raise NotImplementedError("GovStackBeneficiaryService.register — implement in Wave 2.")

    @staticmethod
    def update(
        request_id: str,
        source_bb_id: str,
        beneficiaries: list[dict],
    ) -> dict:
        """
        Update payment_modality and/or financial_address for existing beneficiaries.

        If a PayeeFunctionalID does not exist, returns a failure marker for
        that entry (ResponseCode 01 for the whole request if any entry fails).

        Args:
            request_id: RequestID from the request body.
            source_bb_id: SourceBBID from the request body.
            beneficiaries: list of dicts with PayeeFunctionalID (required),
                           optional PaymentModality, optional FinancialAddress.

        Returns:
            {"updated": N, "not_found": M}

        Raises:
            NotImplementedError: until Wave 2 implementation.
        """
        raise NotImplementedError("GovStackBeneficiaryService.update — implement in Wave 2.")


# ---------------------------------------------------------------------------
# GovStackBulkPaymentService  (Wave 3)
# ---------------------------------------------------------------------------

class GovStackBulkPaymentService:
    """
    G2P Bulk Disbursement: receive and validate batches.

    GovStack spec: BulkPayment.yml, PrePaymentValidation.yml
    Harness features: g2p_bulk_payment, g2p_prepayment_validation
    """

    @staticmethod
    def receive_batch(
        request_id: str,
        source_bb_id: str,
        batch_id: str,
        instructions: list[dict],
        callback_url: str = "",
        correlation_id: str = "",
    ):
        """
        Accept a batch of credit instructions.

        Persists BulkPaymentBatch + CreditInstruction rows and enqueues
        the process_bulk_payment_batch Celery task.

        Returns the BulkPaymentBatch instance.

        Raises:
            NotImplementedError: until Wave 3 implementation.
        """
        raise NotImplementedError("GovStackBulkPaymentService.receive_batch — implement in Wave 3.")

    @staticmethod
    def validate_prepayment(
        request_id: str,
        source_bb_id: str,
        batch_id: str,
        instruction_id: str,
        payee_functional_id: str,
        amount: Decimal,
        currency: str,
        narration: str = "",
        callback_url: str = "",
    ):
        """
        Validate a single PayeeFunctionalID against the ID Mapper before disbursement.

        Returns a PrepaymentValidationRequest instance.

        Raises:
            NotImplementedError: until Wave 3 implementation.
        """
        raise NotImplementedError(
            "GovStackBulkPaymentService.validate_prepayment — implement in Wave 3."
        )


# ---------------------------------------------------------------------------
# GovStackVoucherService  (Wave 4)
# ---------------------------------------------------------------------------

class GovStackVoucherService:
    """
    Voucher Engine: preactivate, activate, redeem, cancel, and check vouchers.

    GovStack spec: api/Voucher API YAMLs/
    Harness features (5):
      voucher_preactivation, voucher_activation, voucher_redemption,
      voucher_cancelation, voucher_status_check
    """

    @staticmethod
    def preactivate(
        voucher_amount: Decimal,
        voucher_currency: str,
        voucher_group: str,
        issuing_bb: str,
        registering_institution_id: str = "",
        batch_id: str = "",
        payee_functional_id: str = "",
        callback_url: str = "",
    ):
        """
        Create a GovStackVoucher in PREACTIVATED status.

        Generates a unique serial_number and voucher_secret.
        Returns the GovStackVoucher instance.

        Raises:
            apps.payments.govstack_exceptions.InvalidVoucherAmount: HTTP 452
            apps.payments.govstack_exceptions.InvalidVoucherCurrency: HTTP 453
            apps.payments.govstack_exceptions.InvalidVoucherGroup: HTTP 454
            apps.payments.govstack_exceptions.GovStackBBNotFound: HTTP 460
            NotImplementedError: until Wave 4 implementation.
        """
        raise NotImplementedError("GovStackVoucherService.preactivate — implement in Wave 4.")

    @staticmethod
    def activate(voucher_serial_number: str, issuing_bb: str):
        """
        Transition PREACTIVATED → ACTIVATED.

        Returns the updated GovStackVoucher instance.

        Raises:
            apps.payments.govstack_exceptions.InvalidVoucherSerial: HTTP 456
            apps.payments.govstack_exceptions.GovStackBBNotFound: HTTP 460
            NotImplementedError: until Wave 4 implementation.
        """
        raise NotImplementedError("GovStackVoucherService.activate — implement in Wave 4.")

    @staticmethod
    def redeem(
        voucher_number: str,
        issuing_bb: str,
        merchant_name: str = "",
        merchant_bank_details: str = "",
        merchant_voucher_group: str = "",
        override: bool = False,
        agent_id: str = "",
        voucher_secret_number: str = "",
    ):
        """
        Transition ACTIVATED → CONSUMED. Records merchant redemption details.

        Returns the updated GovStackVoucher instance.

        Raises:
            apps.payments.govstack_exceptions.GovStackBBNotFound: HTTP 460
            apps.payments.govstack_exceptions.InvalidVoucherSerial: if not found
            NotImplementedError: until Wave 4 implementation.
        """
        raise NotImplementedError("GovStackVoucherService.redeem — implement in Wave 4.")

    @staticmethod
    def cancel(voucher_serial_number: str):
        """
        Transition PREACTIVATED | ACTIVATED → CANCELLED.

        Returns the updated GovStackVoucher instance.

        Raises:
            apps.payments.govstack_exceptions.InvalidCancellationSerial: HTTP 463 (not found)
            apps.payments.govstack_exceptions.VoucherAlreadyCancelled: HTTP 464 (already cancelled)
            NotImplementedError: until Wave 4 implementation.
        """
        raise NotImplementedError("GovStackVoucherService.cancel — implement in Wave 4.")

    @staticmethod
    def get_status(serial_number: str):
        """
        Retrieve a GovStackVoucher by serial number.

        Returns the GovStackVoucher instance.

        Raises:
            apps.payments.govstack_exceptions.InvalidVoucherSerial: if not found
            NotImplementedError: until Wave 4 implementation.
        """
        raise NotImplementedError("GovStackVoucherService.get_status — implement in Wave 4.")


# ---------------------------------------------------------------------------
# GovStackP2GService  (Wave 5)
# ---------------------------------------------------------------------------

class GovStackP2GService:
    """
    P2G — Person to Government: bill inquiry and payment notification.

    Adapts existing CivicOS FeeSchedule + PaymentIntent models to the
    GovStack P2G API surface.

    GovStack spec: api/P2G API YAMLs/
    No harness features yet for P2G.
    """

    @staticmethod
    def get_bill(bill_id: str) -> dict:
        """
        Retrieve bill details for a given fee code.

        Maps CivicOS FeeSchedule.fee_code → GovStack bill shape.

        Raises:
            NotImplementedError: until Wave 5 implementation.
        """
        raise NotImplementedError("GovStackP2GService.get_bill — implement in Wave 5.")

    @staticmethod
    def receive_transfer_notification(
        request_id: str,
        bill_id: str,
        bill_inquiry_request_id: str = "",
        payment_reference_id: str = "",
    ) -> dict:
        """
        Receive a mobile money payment notification for a bill.

        Creates or updates a CivicOS PaymentIntent to COMPLETED.

        Raises:
            NotImplementedError: until Wave 5 implementation.
        """
        raise NotImplementedError(
            "GovStackP2GService.receive_transfer_notification — implement in Wave 5."
        )
