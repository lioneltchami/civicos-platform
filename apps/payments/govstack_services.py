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

from django.db import transaction

from apps.payments.govstack_models import (
    GovStackBeneficiary,
    GovStackPaymentAuditEntry,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GovStackBeneficiaryService  (Wave 2)
# ---------------------------------------------------------------------------

class GovStackBeneficiaryService:
    """
    G2P ID Mapper: register and update beneficiaries.

    GovStack spec: RegisterBeneficiaryRequest.yml, UpdateBeneficiaryRequest.yml
    Harness features: g2p_register_beneficiary, g2p_update_beneficiary_details

    Both register() and update() perform an upsert (create-or-update) on
    GovStackBeneficiary records keyed by PayeeFunctionalID. This matches the
    harness behaviour: the update smoke test sends a PayeeFunctionalID that
    does not exist in the DB and still expects HTTP 200 / ResponseCode "00".

    Security invariants:
    - PayeeFunctionalID is NEVER written to logs or audit details.
    - FinancialAddress is NEVER written to logs or audit details.
    - Audit entries use str(obj.pk) (UUID) as the object identifier.
    """

    @staticmethod
    def _upsert_beneficiaries(
        *,
        request_id: str,
        source_bb_id: str,
        beneficiaries: list[dict],
        registering_institution_id: str = "",
        action_on_create: str = "",
        action_on_update: str = "",
    ) -> dict:
        """
        Internal upsert helper shared by register() and update().

        For each entry in `beneficiaries`:
          - If a GovStackBeneficiary with that PayeeFunctionalID exists:
            update payment_modality and/or financial_address if provided.
          - Otherwise: create a new GovStackBeneficiary.
          - Create a GovStackPaymentAuditEntry for every operation.

        Returns {"registered": int, "updated": int}.
        """
        registered = 0
        updated = 0

        with transaction.atomic():
            for item in beneficiaries:
                payee_id = item["PayeeFunctionalID"]
                payment_modality = item.get("PaymentModality", "") or ""
                financial_address = item.get("FinancialAddress", "") or ""

                obj, created = GovStackBeneficiary.objects.get_or_create(
                    payee_functional_id=payee_id,
                    defaults={
                        "source_bb_id": source_bb_id,
                        "registering_institution_id": registering_institution_id,
                        "payment_modality": payment_modality,
                        "financial_address": financial_address,
                        "is_active": True,
                    },
                )

                if created:
                    registered += 1
                    action = action_on_create or GovStackPaymentAuditEntry.ACTION_BENEFICIARY_REGISTERED
                else:
                    # Update provided fields only; never clear existing values.
                    changed = False
                    if payment_modality and obj.payment_modality != payment_modality:
                        obj.payment_modality = payment_modality
                        changed = True
                    if financial_address:
                        # EncryptedCharField: always overwrite — we cannot compare
                        # encrypted values without decrypting, and that's fine for an update.
                        obj.financial_address = financial_address
                        changed = True
                    if obj.source_bb_id != source_bb_id:
                        obj.source_bb_id = source_bb_id
                        changed = True
                    if registering_institution_id and obj.registering_institution_id != registering_institution_id:
                        obj.registering_institution_id = registering_institution_id
                        changed = True
                    if changed:
                        obj.save()  # updates updated_at via auto_now=True on TimestampedModel

                    updated += 1
                    action = action_on_update or GovStackPaymentAuditEntry.ACTION_BENEFICIARY_UPDATED

                # Audit entry — never log payee_functional_id or financial_address.
                GovStackPaymentAuditEntry.objects.create(
                    action=action,
                    actor_bb_id=source_bb_id,
                    object_type="beneficiary",
                    object_pk=str(obj.pk),   # UUID — not the payee_functional_id
                    request_id=request_id,
                    details={
                        "source_bb_id": source_bb_id,
                        "payment_modality": payment_modality,
                        "has_financial_address": bool(financial_address),
                        # NEVER include payee_functional_id or financial_address
                    },
                )

                logger.debug(
                    "govstack.beneficiary action=%s pk=%s source_bb=%s",
                    action, obj.pk, source_bb_id,
                    # NEVER log payee_functional_id
                )

        return {"registered": registered, "updated": updated}

    @staticmethod
    def register(
        request_id: str,
        source_bb_id: str,
        beneficiaries: list[dict],
        registering_institution_id: str = "",
        callback_url: str = "",
    ) -> dict:
        """
        Register one or more beneficiaries in the ID Mapper (upsert).

        Idempotent: if a PayeeFunctionalID already exists, the record is
        updated rather than duplicated.

        Args:
            request_id: RequestID from the request body (echoed in response).
            source_bb_id: SourceBBID identifying the registering BB.
            beneficiaries: list of validated dicts from BeneficiaryItemSerializer.
                           Each has PayeeFunctionalID (required),
                           PaymentModality (optional), FinancialAddress (optional).
            registering_institution_id: Value of X-Registering-Institution-ID header.
            callback_url: Value of X-Callback-URL header (reserved for Wave 3+ async).

        Returns:
            {"registered": N, "updated": M}
        """
        return GovStackBeneficiaryService._upsert_beneficiaries(
            request_id=request_id,
            source_bb_id=source_bb_id,
            beneficiaries=beneficiaries,
            registering_institution_id=registering_institution_id,
            action_on_create=GovStackPaymentAuditEntry.ACTION_BENEFICIARY_REGISTERED,
            action_on_update=GovStackPaymentAuditEntry.ACTION_BENEFICIARY_UPDATED,
        )

    @staticmethod
    def update(
        request_id: str,
        source_bb_id: str,
        beneficiaries: list[dict],
        registering_institution_id: str = "",
        callback_url: str = "",
    ) -> dict:
        """
        Update payment modality and/or financial address for beneficiaries (upsert).

        The GovStack harness update smoke test sends a PayeeFunctionalID that has
        never been registered and still expects HTTP 200 / ResponseCode "00".
        Therefore update() also creates records if they don't exist, making it
        behaviourally identical to register() for the harness.

        Args:
            request_id: RequestID from the request body.
            source_bb_id: SourceBBID from the request body.
            beneficiaries: list of validated dicts from BeneficiaryItemSerializer.
            registering_institution_id: X-Registering-Institution-ID header.
            callback_url: X-Callback-URL header (reserved for async callback).

        Returns:
            {"registered": N, "updated": M}
        """
        return GovStackBeneficiaryService._upsert_beneficiaries(
            request_id=request_id,
            source_bb_id=source_bb_id,
            beneficiaries=beneficiaries,
            registering_institution_id=registering_institution_id,
            # For the update flow, use ACTION_BENEFICIARY_UPDATED even on first-time
            # creates to reflect the caller's intent (they called update-beneficiary-details).
            action_on_create=GovStackPaymentAuditEntry.ACTION_BENEFICIARY_UPDATED,
            action_on_update=GovStackPaymentAuditEntry.ACTION_BENEFICIARY_UPDATED,
        )


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
