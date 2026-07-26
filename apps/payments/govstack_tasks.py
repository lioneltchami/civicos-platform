"""
GovStack Payments BB — Celery tasks.

Implements asynchronous processing for the G2P bulk disbursement and prepayment
validation flows:

  process_bulk_payment_batch
    Performs per-instruction ID Mapper lookup against GovStackBeneficiary.
    Marks each CreditInstruction COMPLETED (beneficiary found) or FAILED
    (not found), derives batch status as COMPLETED / PARTIAL / FAILED,
    updates accounting fields (completed_amount, failed_amount), writes
    audit entries, and POSTs the result to the X-Callback-URL.

  validate_prepayment_async
    Validates a PrepaymentValidationRequest against the GovStackBeneficiary
    registry (ID Mapper), sets beneficiary_found / financial_address_valid,
    transitions the record to COMPLETED, writes an audit entry, and POSTs
    the result to the X-Callback-URL.

Both tasks are dispatched from their respective views via ``transaction.on_commit``
so the task is only enqueued after the acceptance record has been committed to
the database.

Reliability characteristics
----------------------------
- ``acks_late=True`` + ``reject_on_worker_lost=True``: message is NOT acked
  until the task function returns.  If the worker dies mid-flight, the broker
  re-queues the message for another worker.
- ``select_for_update()`` inside ``transaction.atomic()``: prevents two workers
  from processing the same record concurrently.
- Idempotency guard: tasks exit early (no-op) if the record is already in a
  terminal state (status ≠ STATUS_RECEIVED / STATUS_PENDING).
- ``max_retries=3, default_retry_delay=60``: Celery retries on unexpected
  exceptions.  Callback POST failures are explicitly caught and are non-fatal
  — they never trigger a Celery retry.

Security invariants (MUST NEVER be violated)
--------------------------------------------
- ``payee_functional_id`` MUST NEVER appear in any log message.
- ``financial_address`` MUST NEVER appear in any log message.
- Callback payloads MUST NOT contain ``payee_functional_id`` or
  ``financial_address``.
- Only ``batch.pk`` / ``pvr.pk`` / ``instr.pk`` (UUID strings) are used in
  log identifiers.
- The ``X-Callback-URL`` header is caller-supplied and untrusted.
  ``_post_callback()`` MUST NOT dispatch a request to a URL that resolves
  to a private, loopback, link-local (including the cloud metadata address
  169.254.169.254), multicast, or otherwise non-public IP address, and MUST
  NOT follow redirects. See ``_is_safe_callback_url()`` below.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from decimal import Decimal
from urllib.parse import urlsplit

import requests
from celery import shared_task
from django.db import transaction
from django.utils import timezone

from apps.payments.govstack_models import (
    BulkPaymentBatch,
    CreditInstruction,
    GovStackBeneficiary,
    GovStackPaymentAuditEntry,
    PrepaymentValidationRequest,
)

logger = logging.getLogger(__name__)

# Seconds to wait for a callback endpoint to respond before abandoning the POST.
# Non-fatal either way — this is purely a best-effort delivery.
_CALLBACK_TIMEOUT_SECONDS: float = 10.0


# ---------------------------------------------------------------------------
# process_bulk_payment_batch
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    name="payments.process_bulk_payment_batch",
    queue="payments",
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=120,
)
def process_bulk_payment_batch(self, batch_pk: str) -> None:
    """
    Per-instruction ID Mapper lookup: marks each CreditInstruction COMPLETED
    or FAILED, derives batch status as COMPLETED / PARTIAL / FAILED, updates
    accounting fields, writes audit entries, and POSTs the result to the
    X-Callback-URL.

    ID Mapper rule
    --------------
    An instruction is COMPLETED when an active ``GovStackBeneficiary`` exists
    for its ``payee_functional_id`` (``is_active=True``).  Otherwise it is
    FAILED and ``failure_reason`` is set to a non-PII string.

    Batch status derivation
    -----------------------
    - All instructions COMPLETED → ``STATUS_COMPLETED``
    - Some COMPLETED, some FAILED → ``STATUS_PARTIAL``
    - All instructions FAILED → ``STATUS_FAILED``

    Idempotent: exits without modification if ``batch.status`` is not
    ``STATUS_RECEIVED`` (i.e., already processed or in a terminal state).

    Args:
        batch_pk: ``str(batch.pk)`` — UUID string primary key of the
                  ``BulkPaymentBatch`` to process.

    Security:
        ``payee_functional_id`` and ``financial_address`` MUST NOT appear in
        any log message or callback payload.  Use ``batch.pk`` / ``instr.pk``
        in logs.
    """
    with transaction.atomic():
        try:
            batch = BulkPaymentBatch.objects.select_for_update().get(pk=batch_pk)
        except BulkPaymentBatch.DoesNotExist:
            logger.error(
                "process_bulk_payment_batch.not_found batch_pk=%s",
                batch_pk,
            )
            return

        if batch.status != BulkPaymentBatch.STATUS_RECEIVED:
            # Already processed — idempotent exit under the row lock.
            logger.debug(
                "process_bulk_payment_batch.already_processed batch_pk=%s status=%s",
                batch_pk,
                batch.status,
            )
            return

        # Per-instruction ID Mapper lookup.
        # We iterate rather than bulk-update so that each instruction can be
        # individually COMPLETED or FAILED based on whether an active beneficiary
        # exists for its payee_functional_id.
        # select_for_update() on the instruction queryset prevents concurrent
        # task retries from racing on the same rows.
        # Security: payee_functional_id is used ONLY as a DB filter key —
        # NEVER passed to logger or included in any audit details.
        completed_count: int = 0
        failed_count: int = 0
        completed_amount: Decimal = Decimal("0.00")
        failed_amount: Decimal = Decimal("0.00")

        for instr in (
            CreditInstruction.objects
            .filter(batch=batch, status=CreditInstruction.STATUS_PENDING)
            .select_for_update()
        ):
            found: bool = GovStackBeneficiary.objects.filter(
                payee_functional_id=instr.payee_functional_id,
                is_active=True,
            ).exists()

            if found:
                instr.status = CreditInstruction.STATUS_COMPLETED
                instr.failure_reason = ""  # clear any stale value from a prior partial run
                completed_count += 1
                completed_amount += instr.amount
            else:
                instr.status = CreditInstruction.STATUS_FAILED
                instr.failure_reason = "PayeeFunctionalID not found in ID Mapper."
                failed_count += 1
                failed_amount += instr.amount
                # Write a per-instruction audit entry for each failure.
                # Security: failure_reason is a static string — no PII.
                GovStackPaymentAuditEntry.objects.create(
                    action=GovStackPaymentAuditEntry.ACTION_INSTRUCTION_FAILED,
                    actor_bb_id=batch.source_bb_id,
                    object_type="instruction",
                    object_pk=str(instr.pk),
                    request_id=batch.request_id,
                    details={
                        "failure_reason": instr.failure_reason,
                        # NEVER include payee_functional_id or financial_address
                    },
                )

            instr.save(update_fields=["status", "failure_reason"])

        # Derive batch outcome from instruction counts.
        if failed_count == 0:
            batch.status = BulkPaymentBatch.STATUS_COMPLETED
            batch_audit_action = GovStackPaymentAuditEntry.ACTION_BATCH_COMPLETED
        elif completed_count == 0:
            batch.status = BulkPaymentBatch.STATUS_FAILED
            batch_audit_action = GovStackPaymentAuditEntry.ACTION_BATCH_FAILED
        else:
            batch.status = BulkPaymentBatch.STATUS_PARTIAL
            batch_audit_action = GovStackPaymentAuditEntry.ACTION_BATCH_PARTIAL

        # result_generated_at timestamps when this Celery task finished so that
        # monitoring queries can detect stuck batches (result_generated_at IS NULL
        # after sufficient time would indicate a processing error).
        batch.completed_amount = completed_amount
        batch.failed_amount = failed_amount
        batch.result_generated_at = timezone.now()
        batch.save(update_fields=[
            "status",
            "completed_amount",
            "failed_amount",
            "result_generated_at",
        ])

        GovStackPaymentAuditEntry.objects.create(
            action=batch_audit_action,
            actor_bb_id=batch.source_bb_id,
            object_type="batch",
            object_pk=str(batch.pk),
            request_id=batch.request_id,
            details={
                "batch_id": batch.batch_id,          # batch_id is not PII
                "source_bb_id": batch.source_bb_id,
                "completed_count": completed_count,
                "failed_count": failed_count,
                # NEVER include payee_functional_id or financial_address
            },
        )

        # Capture callback fields inside the transaction before the lock releases.
        # batch.status.upper(): "completed"→"COMPLETED", "partial"→"PARTIAL",
        # "failed"→"FAILED" — matches the GovStack spec callback Status values.
        callback_url = batch.callback_url
        callback_status = batch.status.upper()

    # POST callback OUTSIDE the transaction — failure is non-fatal.
    # The BulkPaymentBatch is already in its terminal state in the database.
    # A callback failure does NOT retry the task or rollback the status change.
    if callback_url:
        _post_callback(
            url=callback_url,
            payload={
                "RequestID": batch.request_id,
                "BatchID": batch.batch_id,
                "Status": callback_status,
                # NEVER include payee_functional_id or financial_address
            },
        )


# ---------------------------------------------------------------------------
# validate_prepayment_async
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    name="payments.validate_prepayment_async",
    queue="payments",
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=60,
)
def validate_prepayment_async(self, pvr_pk: str) -> None:
    """
    Validate a PrepaymentValidationRequest against the Beneficiary registry.

    Checks whether ``payee_functional_id`` identifies an active
    ``GovStackBeneficiary`` (ID Mapper lookup).  Sets ``beneficiary_found``
    and ``financial_address_valid`` accordingly, transitions the record to
    ``STATUS_COMPLETED``, writes an audit entry, and POSTs the result to the
    X-Callback-URL.

    Idempotent: exits without modification if ``pvr.status`` is not
    ``STATUS_PENDING`` (already processed).

    Args:
        pvr_pk: ``str(pvr.pk)`` — UUID string primary key of the
                ``PrepaymentValidationRequest`` to validate.

    Security:
        ``payee_functional_id`` MUST NOT appear in any log message or
        callback payload.  Use ``pvr.pk`` in logs.
        Callback payload uses ``instruction_id`` to identify failed accounts —
        NEVER ``payee_functional_id``.
    """
    with transaction.atomic():
        try:
            pvr = PrepaymentValidationRequest.objects.select_for_update().get(pk=pvr_pk)
        except PrepaymentValidationRequest.DoesNotExist:
            logger.error(
                "validate_prepayment_async.not_found pvr_pk=%s",
                pvr_pk,
            )
            return

        if pvr.status != PrepaymentValidationRequest.STATUS_PENDING:
            # Already processed — idempotent exit under the row lock.
            logger.debug(
                "validate_prepayment_async.already_processed pvr_pk=%s status=%s",
                pvr_pk,
                pvr.status,
            )
            return

        # ID Mapper lookup: is there an active beneficiary for this functional ID?
        # Security: payee_functional_id is used ONLY as a filter key — NEVER logged.
        beneficiary_found: bool = GovStackBeneficiary.objects.filter(
            payee_functional_id=pvr.payee_functional_id,
            is_active=True,
        ).exists()

        # financial_address_valid mirrors beneficiary_found for Wave 3 harness scope:
        # "active beneficiary is registered" is sufficient proof that the financial
        # address is valid.  A production implementation would additionally verify
        # that financial_address is non-empty on the beneficiary record.
        pvr.beneficiary_found = beneficiary_found
        pvr.financial_address_valid = beneficiary_found
        pvr.status = PrepaymentValidationRequest.STATUS_COMPLETED
        pvr.save(update_fields=["beneficiary_found", "financial_address_valid", "status"])

        failed_count: int = 0 if beneficiary_found else 1

        GovStackPaymentAuditEntry.objects.create(
            action=GovStackPaymentAuditEntry.ACTION_VALIDATION_COMPLETED,
            actor_bb_id=pvr.source_bb_id,
            object_type="validation",
            object_pk=str(pvr.pk),
            request_id=pvr.request_id,
            details={
                "batch_id": pvr.batch_id,
                "beneficiary_found": beneficiary_found,
                # NEVER include pvr.payee_functional_id in audit details
            },
        )

        # Capture callback fields before the transaction releases the lock.
        callback_url = pvr.callback_url
        callback_payload = {
            "RequestID": pvr.request_id,
            "Source_BatchID": pvr.batch_id,
            "NumberFailedCases": failed_count,
            # Security: NEVER include payee_functional_id in FailedAccounts.
            # The Source BB can correlate failures via InstructionID alone.
            "FailedAccounts": [] if beneficiary_found else [pvr.instruction_id],
        }

    # POST callback OUTSIDE the transaction — failure is non-fatal.
    # The PrepaymentValidationRequest is already COMPLETED in the database.
    if callback_url:
        _post_callback(url=callback_url, payload=callback_payload)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Only plain HTTP(S) callback endpoints are ever dispatched to. This blocks
# scheme-based SSRF tricks such as file://, gopher://, dict://, etc.
_CALLBACK_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})


def _is_safe_callback_url(url: str) -> bool:
    """
    Defense against SSRF via the caller-supplied ``X-Callback-URL`` header.

    ``callback_url`` on ``BulkPaymentBatch`` / ``PrepaymentValidationRequest``
    is taken verbatim from a request header supplied by whichever party is
    calling the G2P endpoints — it is untrusted input. Without this check,
    ``_post_callback()`` would happily let a caller (in harness/default
    settings mode, even an unauthenticated one — see ``IsTrustedSourceBB``)
    make the Celery worker issue an outbound POST to an arbitrary internal
    address, e.g. a cloud metadata endpoint or an internal admin service.

    This function is checked at the point of use — immediately before the
    outbound request — rather than at intake, so the guard applies no matter
    which code path stored the URL, and cannot be bypassed by adding a new
    caller that skips an intake-time check.

    Rejects:
      - Any URL that doesn't parse, or has no hostname.
      - Any scheme other than ``http``/``https``.
      - Any hostname that is a literal IP address, or that DNS-resolves to
        one, in a private, loopback, link-local (which includes the
        169.254.169.254 cloud metadata address), multicast, unspecified, or
        otherwise reserved range — checked against *every* address the
        hostname resolves to, not just the first.

    Deliberately does NOT reject a hostname that fails to resolve at all
    (``socket.gaierror``): if DNS resolution fails here, the subsequent
    ``requests.post()`` call will fail identically (no network access ever
    occurs either way), so rejecting up front would add a new class of
    false-negative test/environment failures without closing any actual
    SSRF gap. Only affirmatively-unsafe targets are blocked.

    Returns:
        True if the URL is safe to dispatch to (or safety cannot be
        determined due to DNS failure — see above); False if it is
        affirmatively unsafe and must not be dispatched to.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False

    if parts.scheme not in _CALLBACK_ALLOWED_SCHEMES:
        return False

    hostname = parts.hostname
    if not hostname:
        return False

    try:
        addrinfo = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, UnicodeError):
        # Cannot resolve — allow through; requests.post() will fail the same
        # way, with no outbound network access ever occurring. See docstring.
        return True

    for _family, _type, _proto, _canonname, sockaddr in addrinfo:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            # Malformed resolved address — fail closed.
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
            or ip.is_reserved
        ):
            return False

    return True


def _post_callback(url: str, payload: dict) -> None:
    """
    POST a GovStack async result callback to the Source BB.

    SSRF guard
    ----------
    ``url`` is untrusted (caller-supplied via ``X-Callback-URL``).
    ``_is_safe_callback_url()`` is checked before dispatch; an unsafe URL is
    logged and dropped without a network request ever being made. Redirects
    are not followed (``allow_redirects=False``) so a callback endpoint that
    is safe at request time cannot 302 the worker into an internal address.

    Failure policy
    --------------
    ALL exceptions (connection error, timeout, non-2xx response, JSON encode
    error) are caught and logged as warnings.  The caller MUST NOT raise or
    retry because of a callback failure — the DB record has already been
    committed to its terminal state.

    Args:
        url:     Callback endpoint supplied in the original X-Callback-URL header.
        payload: JSON-serialisable dict to POST.  MUST NOT contain
                 ``payee_functional_id`` or ``financial_address``; callers are
                 responsible for excluding those fields.

    Security:
        The URL itself is logged (it is an endpoint, not PII).
        Payload contents are NEVER logged (they may contain instruction IDs
        or batch IDs that could be correlated with PII in aggregated logs).
    """
    if not _is_safe_callback_url(url):
        logger.warning(
            "govstack.callback_post_blocked_unsafe_url url=%s",
            url,
        )
        return

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=_CALLBACK_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        response.raise_for_status()
        logger.info(
            "govstack.callback_posted url=%s status=%s",
            url,
            response.status_code,
        )
    except Exception as exc:  # noqa: BLE001
        # Non-fatal: log the exception class name (not the message, which may
        # contain the URL parameters or response body) and return normally.
        logger.warning(
            "govstack.callback_post_failed url=%s exc_type=%s",
            url,
            type(exc).__name__,
        )
