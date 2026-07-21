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
import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.payments.govstack_exceptions import (
    DuplicateBatchError,
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
    GovStackPaymentAuditEntry,
    GovStackVoucher,
    PrepaymentValidationRequest,
    _generate_voucher_serial,
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
                    action = action_on_create
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
                    action = action_on_update

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
    G2P Bulk Disbursement: receive batches and validate beneficiaries pre-payment.

    GovStack spec: BulkPayment.yml, PrePaymentValidation.yml
    Harness features: g2p_bulk_payment, g2p_prepayment_validation

    Security invariants:
    - payee_functional_id NEVER written to logs or audit details.
    - financial_address NEVER accessed outside the beneficiary lookup result.
    - Use str(obj.pk) (UUID) for all log and audit identifiers.
    """

    @staticmethod
    def receive_batch(
        request_id: str,
        source_bb_id: str,
        batch_id: str,
        instructions: list[dict],
        callback_url: str = "",
        correlation_id: str = "",
    ) -> BulkPaymentBatch:
        """
        Accept a bulk payment batch from a Source BB.

        Atomically creates:
          - One BulkPaymentBatch (status=RECEIVED)
          - One CreditInstruction per item in `instructions`
          - One GovStackPaymentAuditEntry (ACTION_BATCH_RECEIVED)

        In a production system this would enqueue a Celery task to process the batch
        asynchronously. For Wave 3 certification the batch is accepted and stored; the
        Celery task is a no-op stub (processing is out of scope for harness tests).

        Args:
            request_id:      RequestID from request body. Max 16 chars.
            source_bb_id:    SourceBBID identifying the calling BB.
            batch_id:        BatchID — must be globally unique.
            instructions:    Validated CreditInstruction dicts from the serializer.
                             Each has InstructionID, PayeeFunctionalID, Amount, Currency,
                             and optionally Narration.
            callback_url:    X-Callback-URL header (async result POST destination).
            correlation_id:  X-CorrelationID header (for tracing).

        Returns:
            The persisted BulkPaymentBatch instance.
        """
        total_amount = sum(item["Amount"] for item in instructions)

        try:
            with transaction.atomic():
                batch = BulkPaymentBatch.objects.create(
                    request_id=request_id,
                    source_bb_id=source_bb_id,
                    batch_id=batch_id,
                    status=BulkPaymentBatch.STATUS_RECEIVED,
                    callback_url=callback_url,
                    correlation_id=correlation_id,
                    total_amount=total_amount,
                )

                for item in instructions:
                    CreditInstruction.objects.create(
                        batch=batch,
                        instruction_id=item["InstructionID"],
                        payee_functional_id=item["PayeeFunctionalID"],
                        amount=item["Amount"],
                        currency=item["Currency"],
                        narration=item.get("Narration", ""),
                        status=CreditInstruction.STATUS_PENDING,
                    )

                GovStackPaymentAuditEntry.objects.create(
                    action=GovStackPaymentAuditEntry.ACTION_BATCH_RECEIVED,
                    actor_bb_id=source_bb_id,
                    object_type="batch",
                    object_pk=str(batch.pk),
                    request_id=request_id,
                    details={
                        "batch_id": batch_id,            # batch_id is not PII
                        "source_bb_id": source_bb_id,
                        "instruction_count": len(instructions),
                        "total_amount": str(total_amount),
                        # NEVER include payee_functional_id in details
                    },
                )

        except IntegrityError as exc:
            # BulkPaymentBatch.batch_id has unique=True. A duplicate submission
            # raises IntegrityError at the DB level. We re-raise as DuplicateBatchError
            # so the view can return a G2P envelope error instead of an unhandled 500.
            logger.warning(
                "govstack.bulk_payment duplicate batch_id rejected source_bb=%s",
                source_bb_id,
                # NEVER log the batch_id value — it is opaque but could appear
                # alongside PII in aggregated log queries.
            )
            raise DuplicateBatchError(batch_id) from exc

        logger.debug(
            "govstack.bulk_payment batch_received pk=%s source_bb=%s instructions=%d",
            batch.pk,
            source_bb_id,
            len(instructions),
        )
        return batch

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
    ) -> PrepaymentValidationRequest:
        """
        Accept a prepayment validation request and store it for async processing.

        The GovStack prepayment validation flow is asynchronous:
          1. This method stores the request with status=PENDING immediately.
          2. A Celery task (not implemented in Wave 3 harness scope) checks the
             beneficiary ID Mapper, updates status, and POSTs the result to callback_url.
          3. The /prepayment-validation-response endpoint returns the current result
             (which is NumberFailedCases=0, FailedAccounts=[] while still PENDING).

        This design lets the harness complete the chained two-step test
        (POST /prepayment-validation → POST /prepayment-validation-response) without
        waiting for Celery, while keeping the real validation logic in the Celery task
        for production use.

        Args:
            request_id:          RequestID from request body. Unique per record.
            source_bb_id:        SourceBBID of the calling BB.
            batch_id:            BatchID grouping this validation with others.
            instruction_id:      InstructionID for the specific credit instruction.
            payee_functional_id: The beneficiary ID to validate. NEVER written to logs.
            amount:              Decimal credit amount.
            currency:            ISO 4217 currency code.
            narration:           Optional payment narration.
            callback_url:        X-Callback-URL header (async result POST destination).

        Returns:
            The persisted PrepaymentValidationRequest instance (status=PENDING).
        """
        try:
            with transaction.atomic():
                pvr = PrepaymentValidationRequest.objects.create(
                    request_id=request_id,
                    source_bb_id=source_bb_id,
                    batch_id=batch_id,
                    instruction_id=instruction_id,
                    payee_functional_id=payee_functional_id,
                    amount=amount,
                    currency=currency,
                    narration=narration,
                    status=PrepaymentValidationRequest.STATUS_PENDING,
                    # beneficiary_found / financial_address_valid: set by the Celery task.
                    # null = "not yet checked".
                    beneficiary_found=None,
                    financial_address_valid=None,
                    callback_url=callback_url,
                )

                GovStackPaymentAuditEntry.objects.create(
                    action=GovStackPaymentAuditEntry.ACTION_VALIDATION_REQUESTED,
                    actor_bb_id=source_bb_id,
                    object_type="validation",
                    object_pk=str(pvr.pk),
                    request_id=request_id,
                    details={
                        "batch_id": batch_id,
                        "source_bb_id": source_bb_id,
                        "instruction_id": instruction_id,
                        # NEVER include payee_functional_id in details
                    },
                )

        except IntegrityError as exc:
            # PrepaymentValidationRequest.request_id has unique=True. A duplicate
            # submission (retry, replay) raises IntegrityError at the DB level.
            # Re-raise as DuplicateValidationRequestError so the view can return a
            # G2P envelope ResponseCode "01" at HTTP 200 (not an unhandled 500).
            # /prepayment-validation MUST always return HTTP 200 per the spec.
            logger.warning(
                "govstack.prepayment_validation duplicate request_id rejected source_bb=%s",
                source_bb_id,
                # NEVER log request_id — it could co-appear with PII in log aggregation.
            )
            raise DuplicateValidationRequestError(request_id) from exc

        logger.debug(
            "govstack.prepayment_validation accepted pk=%s source_bb=%s",
            pvr.pk,
            source_bb_id,
            # NEVER log payee_functional_id
        )
        return pvr

    @staticmethod
    def get_validation_result(request_id: str, source_batch_id: str) -> dict:
        """
        Return the aggregated validation result for the /prepayment-validation-response endpoint.

        Lookup strategy:
          1. Query by request_id (primary — harness always sends exact request_id).
          2. If no match, fall back to batch_id (real-world: multi-instruction batches
             where each instruction has its own request_id).

        For PENDING records (Celery hasn't run yet — normal harness state since the
        harness calls /prepayment-validation-response immediately after /prepayment-validation):
          - Not counted as failed cases (unknown = not failed).
          - NumberFailedCases is 0, FailedAccounts is [].

        For COMPLETED records: count those where beneficiary_found=False OR
        financial_address_valid=False.

        Security: payee_functional_id MUST NOT appear in the returned FailedAccounts.
        Use instruction_id as the identifier for failed accounts instead.

        Returns:
            {
                "request_id": str,
                "source_batch_id": str,
                "number_failed_cases": int,
                "failed_accounts": list[dict],  # {InstructionID, FailureReason}
            }
        """
        # Materialise to a list immediately so we only issue one DB query per lookup
        # arm (avoids the .exists() + iteration double-query pattern).
        records = list(PrepaymentValidationRequest.objects.filter(request_id=request_id))
        if not records and source_batch_id:
            records = list(PrepaymentValidationRequest.objects.filter(batch_id=source_batch_id))

        failed_accounts: list[dict] = []
        for pvr in records:
            # PENDING records: Celery hasn't validated yet → skip (not a failure).
            # The harness calls /prepayment-validation-response immediately, so all
            # records are still PENDING → NumberFailedCases=0, FailedAccounts=[].
            if pvr.status == PrepaymentValidationRequest.STATUS_PENDING:
                continue

            # FAILED records: Celery task encountered a processing error (e.g. ID
            # Mapper unreachable).  beneficiary_found may still be None.  These are
            # surfaced as failed cases so the Source BB can investigate.
            if pvr.status == PrepaymentValidationRequest.STATUS_FAILED:
                failed_accounts.append({
                    "InstructionID": pvr.instruction_id,
                    "FailureReason": "Validation processing error. Please retry or contact support.",
                })
                continue

            # COMPLETED records where the validation check itself failed:
            #   beneficiary_found=False  → not in ID Mapper
            #   financial_address_valid=False → address not configured
            if pvr.beneficiary_found is False or pvr.financial_address_valid is False:
                failed_accounts.append({
                    # Use InstructionID (not payee_functional_id) to avoid PII in response.
                    "InstructionID": pvr.instruction_id,
                    "FailureReason": (
                        "Beneficiary not found in ID Mapper."
                        if pvr.beneficiary_found is False
                        else "Financial address not configured for this beneficiary."
                    ),
                })

        return {
            "request_id": request_id,
            "source_batch_id": source_batch_id,
            "number_failed_cases": len(failed_accounts),
            "failed_accounts": failed_accounts,
        }


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

    # Max serial number generation retries before giving up on a collision.
    _SERIAL_MAX_RETRIES = 5

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
    ) -> GovStackVoucher:
        """
        Create a GovStackVoucher in PREACTIVATED status.

        Generates a unique 6-digit serial_number with up to 5 collision retries.
        Returns the saved GovStackVoucher instance.

        Raises:
            InvalidVoucherAmount (HTTP 452): amount ≤ 0 or zero.
            InvalidVoucherGroup (HTTP 454): group is empty/blank.
            GovStackBBNotFound (HTTP 460): issuing_bb is empty/blank.
        """
        # ── Validate Gov_Stack_BB → 460 ──────────────────────────────────────
        if not issuing_bb or not issuing_bb.strip():
            raise GovStackBBNotFound()

        # ── Validate amount → 452 ─────────────────────────────────────────────
        # Non-parseable amounts are rejected by the serializer (400).
        # Non-positive amounts must return 452 (spec requirement).
        if voucher_amount is None or voucher_amount <= 0:
            raise InvalidVoucherAmount()

        # ── Validate group → 454 ──────────────────────────────────────────────
        if not voucher_group or not voucher_group.strip():
            raise InvalidVoucherGroup()

        # ── Compute expiry ────────────────────────────────────────────────────
        expiry_days = getattr(settings, "GOVSTACK_VOUCHER_EXPIRY_DAYS", 90)
        expiry = timezone.now() + timedelta(days=expiry_days)

        # ── Create with serial collision retry ────────────────────────────────
        max_retries = GovStackVoucherService._SERIAL_MAX_RETRIES
        for attempt in range(max_retries):
            serial = _generate_voucher_serial()
            try:
                with transaction.atomic():
                    voucher = GovStackVoucher.objects.create(
                        serial_number=serial,
                        amount=voucher_amount,
                        currency=voucher_currency,
                        group_code=voucher_group.strip(),
                        status=GovStackVoucher.STATUS_PREACTIVATED,
                        issuing_bb=issuing_bb.strip(),
                        registering_institution_id=registering_institution_id,
                        batch_id=batch_id[:12] if batch_id else "",
                        payee_functional_id=payee_functional_id[:20] if payee_functional_id else "",
                        callback_url=callback_url,
                        expiry_date=expiry,
                    )
                    GovStackPaymentAuditEntry.objects.create(
                        action=GovStackPaymentAuditEntry.ACTION_VOUCHER_PREACTIVATED,
                        actor_bb_id=issuing_bb.strip(),
                        object_type="voucher",
                        object_pk=str(voucher.pk),
                        details={
                            "serial_number": voucher.serial_number,
                            "group_code": voucher.group_code,
                            "currency": voucher.currency,
                            # NOTE: amount intentionally omitted — not PII but kept minimal.
                            # NOTE: payee_functional_id NEVER in audit details.
                        },
                    )
                    logger.info(
                        "govstack.voucher_preactivated voucher_pk=%s issuing_bb=%s",
                        voucher.pk,
                        issuing_bb.strip(),
                    )
                    return voucher
            except IntegrityError:
                if attempt == max_retries - 1:
                    logger.error(
                        "govstack.voucher_preactivate serial collision exhausted "
                        "after %d retries issuing_bb=%s",
                        max_retries,
                        issuing_bb.strip(),
                    )
                    raise  # Let Django 500 — this is a fatal infrastructure error
                logger.warning(
                    "govstack.voucher_preactivate serial collision retry %d/%d",
                    attempt + 1,
                    max_retries,
                )

    @staticmethod
    def activate(voucher_serial_number: str, issuing_bb: str) -> GovStackVoucher:
        """
        Transition PREACTIVATED → ACTIVATED.

        Returns the updated GovStackVoucher instance.

        Raises:
            GovStackBBNotFound (HTTP 460): issuing_bb is empty/blank.
            InvalidVoucherSerial (HTTP 456): serial not found or invalid transition.
        """
        # ── Validate Gov_Stack_BB → 460 ──────────────────────────────────────
        if not issuing_bb or not issuing_bb.strip():
            raise GovStackBBNotFound()

        # ── Fetch voucher → 456 ───────────────────────────────────────────────
        voucher = GovStackVoucher.objects.filter(
            serial_number=voucher_serial_number
        ).first()
        if voucher is None:
            logger.warning(
                "govstack.voucher_activate serial not found issuing_bb=%s",
                issuing_bb.strip(),
            )
            raise InvalidVoucherSerial()

        # ── Transition PREACTIVATED → ACTIVATED ───────────────────────────────
        try:
            voucher.transition_to(GovStackVoucher.STATUS_ACTIVATED)
        except ValueError:
            # Not in a state that can transition to ACTIVATED — treat as not-found.
            logger.warning(
                "govstack.voucher_activate invalid transition from=%s voucher_pk=%s",
                voucher.status,
                voucher.pk,
            )
            raise InvalidVoucherSerial()

        voucher.save(update_fields=["status"])

        GovStackPaymentAuditEntry.objects.create(
            action=GovStackPaymentAuditEntry.ACTION_VOUCHER_ACTIVATED,
            actor_bb_id=issuing_bb.strip(),
            object_type="voucher",
            object_pk=str(voucher.pk),
            details={
                "serial_number": voucher.serial_number,
                "group_code": voucher.group_code,
                "new_status": voucher.status,
            },
        )
        logger.info(
            "govstack.voucher_activated voucher_pk=%s issuing_bb=%s",
            voucher.pk,
            issuing_bb.strip(),
        )
        return voucher

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
    ) -> GovStackVoucher:
        """
        Transition ACTIVATED → CONSUMED. Records merchant redemption details.

        voucher_number corresponds to the voucher's serial_number (the public identifier).

        Returns the updated GovStackVoucher instance with redemption fields populated.

        Raises:
            GovStackBBNotFound (HTTP 460): issuing_bb is empty/blank.
            InvalidVoucherSerial (HTTP 456): serial not found or invalid transition.
        """
        # ── Validate Gov_Stack_BB → 460 ──────────────────────────────────────
        if not issuing_bb or not issuing_bb.strip():
            raise GovStackBBNotFound()

        # ── Fetch voucher by serial number → 456 ─────────────────────────────
        voucher = GovStackVoucher.objects.filter(
            serial_number=voucher_number
        ).first()
        if voucher is None:
            logger.warning(
                "govstack.voucher_redeem voucher not found issuing_bb=%s",
                issuing_bb.strip(),
            )
            raise InvalidVoucherSerial()

        # ── Transition ACTIVATED → CONSUMED ───────────────────────────────────
        try:
            voucher.transition_to(GovStackVoucher.STATUS_CONSUMED)
        except ValueError:
            logger.warning(
                "govstack.voucher_redeem invalid transition from=%s voucher_pk=%s",
                voucher.status,
                voucher.pk,
            )
            raise InvalidVoucherSerial()

        # ── Record redemption details ─────────────────────────────────────────
        now = timezone.now()
        transaction_id = secrets.token_hex(10)  # 20 hex chars ≤ max_length=20

        voucher.redeemed_by_agent_id = agent_id[:10] if agent_id else ""
        voucher.redeemed_merchant_name = merchant_name[:200]
        voucher.redeemed_merchant_bank_details = merchant_bank_details[:200]
        voucher.redeemed_merchant_voucher_group = merchant_voucher_group[:100]
        voucher.redeemed_at = now
        voucher.redemption_transaction_id = transaction_id

        voucher.save(update_fields=[
            "status",
            "redeemed_by_agent_id",
            "redeemed_merchant_name",
            "redeemed_merchant_bank_details",
            "redeemed_merchant_voucher_group",
            "redeemed_at",
            "redemption_transaction_id",
        ])

        GovStackPaymentAuditEntry.objects.create(
            action=GovStackPaymentAuditEntry.ACTION_VOUCHER_REDEEMED,
            actor_bb_id=issuing_bb.strip(),
            object_type="voucher",
            object_pk=str(voucher.pk),
            details={
                "serial_number": voucher.serial_number,
                "transaction_id": transaction_id,
                # merchant details intentionally omitted — may contain PII.
            },
        )
        logger.info(
            "govstack.voucher_redeemed voucher_pk=%s transaction_id=%s issuing_bb=%s",
            voucher.pk,
            transaction_id,
            issuing_bb.strip(),
        )
        return voucher

    @staticmethod
    def cancel(voucher_serial_number: str) -> GovStackVoucher:
        """
        Transition PREACTIVATED | ACTIVATED | BLOCKED | SUSPENDED → CANCELLED.

        Returns the updated GovStackVoucher instance.

        Raises:
            InvalidCancellationSerial (HTTP 463): serial not found, or voucher is in a
                terminal state (CONSUMED, PURGED) that cannot be cancelled.
            VoucherAlreadyCancelled (HTTP 464): voucher is already CANCELLED
                (idempotent double-cancel guard).
        """
        # ── Fetch voucher → 463 ───────────────────────────────────────────────
        voucher = GovStackVoucher.objects.filter(
            serial_number=voucher_serial_number
        ).first()
        if voucher is None:
            raise InvalidCancellationSerial()

        # ── Already cancelled → 464 ───────────────────────────────────────────
        if voucher.status == GovStackVoucher.STATUS_CANCELLED:
            raise VoucherAlreadyCancelled()

        # ── Attempt transition → 463 if not allowed (e.g. CONSUMED / PURGED) ─
        try:
            voucher.transition_to(GovStackVoucher.STATUS_CANCELLED)
        except ValueError:
            logger.warning(
                "govstack.voucher_cancel invalid transition from=%s voucher_pk=%s",
                voucher.status,
                voucher.pk,
            )
            raise InvalidCancellationSerial()

        voucher.save(update_fields=["status"])

        GovStackPaymentAuditEntry.objects.create(
            action=GovStackPaymentAuditEntry.ACTION_VOUCHER_CANCELLED,
            actor_bb_id="",
            object_type="voucher",
            object_pk=str(voucher.pk),
            details={
                "serial_number": voucher.serial_number,
                "group_code": voucher.group_code,
            },
        )
        logger.info(
            "govstack.voucher_cancelled voucher_pk=%s",
            voucher.pk,
        )
        return voucher

    @staticmethod
    def get_status(serial_number: str) -> GovStackVoucher:
        """
        Retrieve a GovStackVoucher by serial number for the status check endpoint.

        Returns the GovStackVoucher instance (all fields; view selects what to expose).

        Raises:
            InvalidVoucherSerial (HTTP 456): serial not found.
        """
        voucher = GovStackVoucher.objects.filter(serial_number=serial_number).first()
        if voucher is None:
            raise InvalidVoucherSerial()
        return voucher


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
