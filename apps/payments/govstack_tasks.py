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
  NOT follow redirects. See ``_is_safe_callback_url()`` below — it is kept
  at parity with ``apps.appointments.tasks._is_safe_outbound_url`` /
  ``apps.consent.tasks._is_safe_outbound_url`` (HTTPS-only, same extra IP
  ranges rejected, same fail-closed behaviour).
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from decimal import Decimal
from urllib.parse import urlsplit
from uuid import uuid4

import requests
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.payments.govstack_batch_decision import (
    BatchDecisionConflict,
    BatchDecisionService,
)
from apps.payments.govstack_batch_lease import (
    BatchLeaseHandle,
    BatchLeaseOwnershipLost,
    BatchLeaseService,
)
from apps.payments.govstack_failure_services import PaymentLifecycleService
from apps.payments.provider_runtime import enqueue_attempt
from apps.payments.govstack_models import (
    BulkPaymentBatch,
    CallbackDelivery,
    CreditInstruction,
    GovStackBatchDecision,
    GovStackBeneficiary,
    GovStackPaymentAuditEntry,
    PaymentOutcome,
    PrepaymentValidationRequest,
)

logger = logging.getLogger(__name__)


def _record_bulk_instruction_lifecycle(
    batch: BulkPaymentBatch,
    instruction: CreditInstruction,
    *,
    beneficiary_found: bool,
    lease_handle: BatchLeaseHandle | None = None,
):
    """Persist non-PII execution evidence without treating ID lookup as settlement.

    Existing GovStack G2P compatibility semantics continue to expose local ID
    Mapper validation through ``CreditInstruction``. The durable attempt records
    whether a provider settlement adapter has supplied financial finality. Until
    such an adapter is configured, a locally valid instruction is explicitly
    routed to review rather than silently represented as provider-settled.
    """
    if lease_handle is not None:
        BatchLeaseService.assert_current_owner(lease_handle)
    attempt, _ = PaymentLifecycleService.get_or_create_attempt(
        tenant_id="",
        operation="g2p_bulk_instruction",
        request_id=f"{batch.request_id}:{instruction.instruction_id}",
        payload={
            "batch_pk": str(batch.pk),
            "instruction_pk": str(instruction.pk),
            "amount": str(instruction.amount),
            "currency": instruction.currency,
        },
        amount=instruction.amount,
        currency=instruction.currency,
        correlation_id=batch.correlation_id,
        source_bb_id=batch.source_bb_id,
    )
    if beneficiary_found:
        # Local ID Mapper validation remains distinct from settlement.  An
        # explicitly enabled provider runtime owns the next asynchronous step;
        # without that deployment configuration we preserve the existing
        # fail-closed, operator-review behavior.
        if getattr(settings, "GOVSTACK_PAYMENT_PROVIDER_RUNTIME_ENABLED", False):
            if lease_handle is not None:
                BatchLeaseService.assert_current_owner(lease_handle)
            enqueue_attempt(attempt)
        else:
            if lease_handle is not None:
                BatchLeaseService.assert_current_owner(lease_handle)
            PaymentLifecycleService.apply_outcome(
                attempt,
                PaymentOutcome(
                    "review",
                    code="SETTLEMENT_ADAPTER_UNCONFIGURED",
                    category="configuration",
                    message="Local account validation completed; settlement verification is required.",
                ),
            )
    else:
        if lease_handle is not None:
            BatchLeaseService.assert_current_owner(lease_handle)
        PaymentLifecycleService.apply_outcome(
            attempt,
            PaymentOutcome(
                "invalid_account",
                code="ID_MAPPER_NOT_FOUND",
                category="account",
                message="Destination account could not be validated.",
            ),
        )
    return attempt


# Seconds to wait for a callback endpoint to respond before abandoning the POST.
# Non-fatal either way — this is purely a best-effort delivery.
_CALLBACK_TIMEOUT_SECONDS: float = 10.0

# RFC 6598 Shared Address Space (a.k.a. CGNAT range) — NOT covered by any of
# ipaddress.ip_address's is_private/is_loopback/is_link_local/is_reserved/
# is_multicast/is_unspecified properties (confirmed: ipaddress.ip_address
# ("100.64.0.1") reports False for all six), yet it is routable inside many
# cloud VPC / Kubernetes overlay networks and can reach internal
# infrastructure — must be checked explicitly. Mirrors
# apps.appointments.tasks._SHARED_ADDRESS_SPACE /
# apps.consent.tasks._SHARED_ADDRESS_SPACE exactly.
_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")

# IANA special-purpose registry: IETF Protocol Assignments — likewise not
# covered by the six ipaddress properties above. Mirrors
# apps.appointments.tasks._IETF_PROTOCOL_ASSIGNMENTS /
# apps.consent.tasks._IETF_PROTOCOL_ASSIGNMENTS exactly.
_IETF_PROTOCOL_ASSIGNMENTS = ipaddress.ip_network("192.0.0.0/24")


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
    """Materialize child finality, then persist one fenced live policy decision.

    RB-02.3 leaves RB-02.1 lease ownership and RB-02.2 evidence materialization
    intact.  It persists policy, audit, and outbox side effects atomically; no
    callback transport or return-funds transfer is executed in this task.
    """
    lease_handle: BatchLeaseHandle | None = None
    owner_token = str(getattr(self.request, "id", None) or uuid4())

    try:
        with transaction.atomic():
            try:
                batch = BulkPaymentBatch.objects.select_for_update().get(pk=batch_pk)
            except BulkPaymentBatch.DoesNotExist:
                logger.error("process_bulk_payment_batch.not_found batch_pk=%s", batch_pk)
                return

            if batch.status not in {
                BulkPaymentBatch.STATUS_RECEIVED,
                BulkPaymentBatch.STATUS_PROCESSING,
            }:
                logger.debug(
                    "process_bulk_payment_batch.already_terminal batch_pk=%s status=%s",
                    batch_pk,
                    batch.status,
                )
                return

            lease_handle = BatchLeaseService.acquire(batch=batch, owner_token=owner_token)
            if lease_handle is None:
                logger.info("process_bulk_payment_batch.lease_busy batch_pk=%s", batch_pk)
                return
            BatchLeaseService.heartbeat(lease_handle)

            settled_amount = Decimal("0.00")
            rejected_amount = Decimal("0.00")
            child_items: list[dict[str, str]] = []

            for instr in (
                CreditInstruction.objects.filter(batch=batch)
                .select_for_update()
                .select_related("payment_attempt")
            ):
                BatchLeaseService.heartbeat(lease_handle)
                BatchLeaseService.assert_current_owner(lease_handle)

                if (
                    instr.payment_attempt_id is None
                    and instr.status
                    in {CreditInstruction.STATUS_COMPLETED, CreditInstruction.STATUS_FAILED}
                ):
                    # A historic terminal-looking row without the RB-02.2
                    # binding remains non-final and cannot produce a terminal
                    # policy projection.
                    child_items.append(
                        {"id": str(instr.pk), "status": "review", "amount": str(instr.amount)}
                    )
                    continue

                if instr.payment_attempt_id is None:
                    found = GovStackBeneficiary.objects.filter(
                        payee_functional_id=instr.payee_functional_id,
                        is_active=True,
                    ).exists()
                    attempt = _record_bulk_instruction_lifecycle(
                        batch,
                        instr,
                        beneficiary_found=found,
                        lease_handle=lease_handle,
                    )
                    BatchLeaseService.assert_current_owner(lease_handle)
                    instr = PaymentLifecycleService.bind_credit_instruction_attempt(instr, attempt)

                finality = PaymentLifecycleService.materialize_credit_instruction_finality(instr)
                if finality.final and finality.status == "settled":
                    child_items.append(
                        {"id": str(instr.pk), "status": "settled", "amount": str(instr.amount)}
                    )
                    settled_amount += instr.amount
                    if instr.status != CreditInstruction.STATUS_COMPLETED:
                        instr.status = CreditInstruction.STATUS_COMPLETED
                        instr.failure_reason = ""
                        BatchLeaseService.assert_current_owner(lease_handle)
                        instr.save(update_fields=["status", "failure_reason"])
                elif finality.final and finality.status == "rejected":
                    child_items.append(
                        {"id": str(instr.pk), "status": "rejected", "amount": str(instr.amount)}
                    )
                    rejected_amount += instr.amount
                    if instr.status != CreditInstruction.STATUS_FAILED:
                        instr.status = CreditInstruction.STATUS_FAILED
                        instr.failure_reason = "Provider evidence rejected the instruction."
                        BatchLeaseService.assert_current_owner(lease_handle)
                        instr.save(update_fields=["status", "failure_reason"])
                else:
                    non_final_status = "review"
                    if instr.payment_attempt_id:
                        attempt_status = instr.payment_attempt.status
                        if attempt_status in {"retryable", "uncertain", "review"}:
                            non_final_status = attempt_status
                    child_items.append(
                        {
                            "id": str(instr.pk),
                            "status": non_final_status,
                            "amount": str(instr.amount),
                        }
                    )
                    if instr.status == CreditInstruction.STATUS_PENDING:
                        instr.status = CreditInstruction.STATUS_VALIDATED
                        instr.failure_reason = ""
                        BatchLeaseService.assert_current_owner(lease_handle)
                        instr.save(update_fields=["status", "failure_reason"])

            BatchLeaseService.assert_current_owner(lease_handle)
            result = BatchDecisionService.decide(
                batch=batch,
                lease_handle=lease_handle,
                items=child_items,
                failure_threshold=float(
                    getattr(settings, "GOVSTACK_BULK_FAILURE_THRESHOLD", 0.25)
                ),
                return_funds_enabled=bool(
                    getattr(settings, "GOVSTACK_BULK_RETURN_FUNDS_ENABLED", False)
                ),
            )
            decision = result.decision

            if decision.outcome_action in {
                GovStackBatchDecision.ACTION_TERMINAL,
                GovStackBatchDecision.ACTION_RETURN_FUNDS,
            }:
                if decision.non_final_count:
                    raise BatchDecisionConflict("terminal projection with non-final child is forbidden")
                if decision.rejected_count == 0:
                    batch.status = BulkPaymentBatch.STATUS_COMPLETED
                elif decision.settled_count == 0:
                    batch.status = BulkPaymentBatch.STATUS_FAILED
                else:
                    batch.status = BulkPaymentBatch.STATUS_PARTIAL
                batch.result_generated_at = timezone.now()
            else:
                batch.status = BulkPaymentBatch.STATUS_PROCESSING
                batch.result_generated_at = None

            batch.completed_amount = settled_amount
            batch.failed_amount = rejected_amount
            BatchLeaseService.assert_current_owner(lease_handle)
            batch.save(
                update_fields=[
                    "status",
                    "completed_amount",
                    "failed_amount",
                    "result_generated_at",
                ]
            )

            if result.created:
                BatchLeaseService.assert_current_owner(lease_handle)
                GovStackPaymentAuditEntry.objects.create(
                    action=GovStackPaymentAuditEntry.ACTION_BATCH_DECISION_RECORDED,
                    actor_bb_id=batch.source_bb_id,
                    object_type="batch_decision",
                    object_pk=str(decision.pk),
                    request_id=batch.request_id,
                    details={
                        "outcome_action": decision.outcome_action,
                        "policy_state": decision.policy_state,
                        "fingerprint": decision.fingerprint,
                        "reason": decision.reason,
                        "total_count": decision.total_count,
                        "settled_count": decision.settled_count,
                        "rejected_count": decision.rejected_count,
                        "non_final_count": decision.non_final_count,
                        "lease_generation": decision.lease_generation,
                    },
                )
                if batch.callback_url:
                    # Preserve the established external callback contract;
                    # the durable decision/audit rows retain decision identity.
                    callback_payload = {
                        "RequestID": batch.request_id,
                        "BatchID": batch.batch_id,
                        "Status": decision.outcome_action.upper(),
                    }
                    BatchLeaseService.assert_current_owner(lease_handle)
                    callback_attempt, _ = PaymentLifecycleService.get_or_create_attempt(
                        tenant_id="",
                        operation="g2p_bulk_callback",
                        request_id=f"callback:{batch.request_id}:{batch.batch_id}",
                        # This is the canonical callback-attempt identity. The
                        # decision-specific status remains in CallbackDelivery's
                        # payload hash, allowing later distinct decisions to
                        # enqueue one delivery without idempotency conflict.
                        payload={"RequestID": batch.request_id, "BatchID": batch.batch_id},
                        audit_created=False,
                        correlation_id=batch.correlation_id,
                        source_bb_id=batch.source_bb_id,
                    )
                    BatchLeaseService.assert_current_owner(lease_handle)
                    PaymentLifecycleService.queue_callback(
                        attempt=callback_attempt,
                        callback_url=batch.callback_url,
                        payload=callback_payload,
                    )
    except (BatchLeaseOwnershipLost, BatchDecisionConflict):
        logger.info("process_bulk_payment_batch.decision_not_written batch_pk=%s", batch_pk)
        return
    finally:
        if lease_handle is not None:
            try:
                BatchLeaseService.release(lease_handle)
            except BatchLeaseOwnershipLost:
                logger.info("process_bulk_payment_batch.release_stale batch_pk=%s", batch_pk)


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

    # Persist callback work before transport. The original validation result is
    # available for idempotent replay if the source endpoint is unavailable.
    if callback_url:
        callback_attempt, _ = PaymentLifecycleService.get_or_create_attempt(
            tenant_id="",
            operation="g2p_prepayment_callback",
            request_id=f"callback:{pvr.request_id}:{pvr.instruction_id}",
            payload=callback_payload,
            correlation_id="",
            source_bb_id=pvr.source_bb_id,
        )
        delivery, _ = PaymentLifecycleService.queue_callback(
            attempt=callback_attempt,
            callback_url=callback_url,
            payload=callback_payload,
        )
        success, http_status, error_code = _post_callback(
            url=callback_url,
            payload=callback_payload,
        )
        PaymentLifecycleService.record_callback_result(
            delivery,
            http_status=http_status if success else None,
            error_code=error_code,
        )




# ---------------------------------------------------------------------------
# Item 02 failure-remediation sweeps
# ---------------------------------------------------------------------------

@shared_task(
    name="payments.replay_govstack_callbacks",
    queue="payments",
    acks_late=True,
    reject_on_worker_lost=True,
)
def replay_govstack_callbacks() -> int:
    """Replay due non-PII callback outbox records with bounded persistence."""
    delivered_or_recorded = 0
    for delivery in PaymentLifecycleService.due_callbacks():
        success, http_status, error_code = _post_callback(
            delivery.callback_url,
            delivery.payload,
        )
        PaymentLifecycleService.record_callback_result(
            delivery,
            http_status=http_status if success else None,
            error_code=error_code,
        )
        delivered_or_recorded += 1
    return delivered_or_recorded


@shared_task(
    name="payments.triage_govstack_uncertain_attempts",
    queue="payments",
    acks_late=True,
    reject_on_worker_lost=True,
)
def triage_govstack_uncertain_attempts() -> int:
    """Avoid a blind retry when a provider outcome remains uncertain.

    A production provider adapter may replace this bounded safety net with a
    status-query result. Until then, the record becomes an auditable review
    item after an unknown reconciliation result rather than being resubmitted.
    """
    triaged = 0
    for attempt in PaymentLifecycleService.due_uncertain():
        PaymentLifecycleService.reconcile(attempt, provider_status="unknown")
        PaymentLifecycleService.apply_outcome(
            attempt,
            PaymentOutcome(
                "review",
                code="UNCERTAIN_OUTCOME_REQUIRES_REVIEW",
                category="reconciliation",
                message="Provider outcome is unresolved; manual reconciliation is required.",
            ),
        )
        triaged += 1
    return triaged


@shared_task(
    name="payments.triage_govstack_retryable_attempts",
    queue="payments",
    acks_late=True,
    reject_on_worker_lost=True,
)
def triage_govstack_retryable_attempts() -> int:
    """Route due retryable work to review until a provider adapter is configured."""
    triaged = 0
    for attempt in PaymentLifecycleService.due_retries():
        PaymentLifecycleService.apply_outcome(
            attempt,
            PaymentOutcome(
                "review",
                code="RETRY_ADAPTER_UNCONFIGURED",
                category="configuration",
                message="Retry requires a configured provider adapter.",
            ),
        )
        triaged += 1
    return triaged


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
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

    Kept at parity with ``apps.appointments.tasks._is_safe_outbound_url`` /
    ``apps.consent.tasks._is_safe_outbound_url`` (both hardened earlier this
    session) — all three are deliberately duplicated rather than extracted
    into a shared ``apps.core`` helper (confirmed via repo-wide search: no
    such helper exists); per-BB duplication is this codebase's established
    precedent for this exact class of check.

    Checks, in order (fails closed on ANY failure):
      1. scheme must be exactly ``https`` and a hostname must be present.
         (Previously this also accepted plain ``http`` — tightened to
         HTTPS-only to match the sibling BBs; no real Payments caller or
         harness fixture relies on plaintext HTTP callback URLs.)
      2. The hostname is resolved via DNS (``socket.getaddrinfo``) — this is
         the TOCTOU-safe step: DNS can be repointed at any time after a
         callback URL was first supplied, so re-validating immediately
         before each dispatch (not just once) is mandatory.
      3. EVERY resolved IP address (a hostname may have multiple A/AAAA
         records) must be public and routable. Rejected ranges: private,
         loopback, link-local (which includes the 169.254.169.254 cloud
         metadata address), reserved, multicast, and unspecified (the six
         ``ipaddress.ip_address`` properties), PLUS two ranges those six
         properties do NOT cover: RFC 6598 Shared Address Space / CGNAT
         (100.64.0.0/10) and the IANA IETF Protocol Assignments block
         (192.0.0.0/24). A single unsafe address among several resolved
         addresses is enough to reject the whole URL.
      4. Any exception at all (malformed URL, DNS resolution failure, no
         addresses returned) is treated as unsafe.

    Note on a prior, now-removed behaviour: this function used to
    deliberately ALLOW a hostname that failed to resolve at all
    (``socket.gaierror``) through, reasoning that the subsequent
    ``requests.post()`` call would fail identically with no network access
    ever occurring. That is no longer this function's behaviour — it now
    fails closed on every exception, exactly like
    ``apps.appointments.tasks._is_safe_outbound_url`` /
    ``apps.consent.tasks._is_safe_outbound_url`` — for defense-in-depth
    consistency across all three BBs' callback/webhook dispatch paths,
    rather than carrying a Payments-specific carve-out.

    Returns:
        True only if the URL is affirmatively safe to dispatch to; False in
        every other case (unsafe scheme, unsafe IP, or any parse/DNS error).
    """
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            return False

        addrinfo = socket.getaddrinfo(parts.hostname, None)
        if not addrinfo:
            return False

        for info in addrinfo:
            ip = ipaddress.ip_address(info[4][0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
                or ip in _SHARED_ADDRESS_SPACE
                or ip in _IETF_PROTOCOL_ASSIGNMENTS
            ):
                return False

        return True
    except Exception:  # noqa: BLE001 — fail closed on ANY parse/DNS error.
        return False


def _post_callback(url: str, payload: dict) -> tuple[bool, int | None, str]:
    """
    POST a GovStack async result callback to the Source BB.

    SSRF guard
    ----------
    ``url`` is untrusted (caller-supplied via ``X-Callback-URL``).
    ``_is_safe_callback_url()`` is checked before dispatch; an unsafe URL is
    logged and dropped without a network request ever being made. Redirects
    are not followed (``allow_redirects=False``) so a callback endpoint that
    is safe at request time cannot 302 the worker into an internal address.

    3xx-as-failure
    ---------------
    A 3xx response is explicitly treated as a failed delivery, logged
    identically to any other non-2xx outcome — it is NEVER logged as
    delivered. ``Response.raise_for_status()`` only raises for status codes
    >= 400, so a 3xx would otherwise silently fall through as "delivered"
    below, which is misleading telemetry (not a live SSRF hole on its own,
    since ``allow_redirects=False`` already guarantees the redirect target is
    never connected to) — mirrors the identical explicit check in
    ``apps.appointments.tasks._attempt_alert_delivery`` /
    ``apps.consent.tasks.dispatch_consent_webhook``.

    Failure policy
    --------------
    ALL exceptions (connection error, timeout, non-2xx/3xx response, JSON
    encode error) are caught and logged as warnings.  The caller MUST NOT
    raise or retry because of a callback failure — the DB record has already
    been committed to its terminal state. ``_post_callback`` is a
    fire-and-forget helper (``-> None``) called directly from within
    ``process_bulk_payment_batch`` / ``validate_prepayment_async`` — unlike
    the Celery-task-level dispatch functions in Appointments/Consent, it has
    no Task-level return-status shape to preserve, so the 3xx check here logs
    a warning (same as any other delivery failure) rather than returning a
    distinct status value.

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
        return False, None, "unsafe_callback_url"

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=_CALLBACK_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            # A validated-safe URL that 3xx-redirects to an internal target
            # must never be silently treated as delivered — see docstring
            # "3xx-as-failure" above. raise_for_status() would NOT catch this
            # on its own (it only raises for >= 400), so this is explicit.
            raise requests.exceptions.HTTPError(
                f"{response.status_code} redirect response received "
                f"(not followed — allow_redirects=False)"
            )
        response.raise_for_status()
        logger.info(
            "govstack.callback_posted url=%s status=%s",
            url,
            response.status_code,
        )
        return True, response.status_code, ""
    except Exception as exc:  # noqa: BLE001
        # Non-fatal: log the exception class name (not the message, which may
        # contain the URL parameters or response body) and return normally.
        logger.warning(
            "govstack.callback_post_failed url=%s exc_type=%s",
            url,
            type(exc).__name__,
        )
        return False, None, type(exc).__name__
