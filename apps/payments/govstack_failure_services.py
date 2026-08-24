"""Provider-neutral, durable Payments failure-remediation services for Item 02.

This module is deliberately free of real provider credentials and network I/O. A
caller supplies a provider adapter outcome or a provider-status result; the
service persists the resulting lifecycle, callback-outbox, reconciliation, and
audit state using non-PII metadata only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.payments.govstack_models import (
    CallbackDelivery,
    CreditInstruction,
    GovStackPaymentAuditEntry,
    IdempotencyConflict,
    PaymentAttempt,
    PaymentOutcome,
    PaymentReconciliation,
    ProviderObservation,
)
from apps.payments.govstack_provider import ProviderOutcome, ProviderResult, normalize_result


@dataclass(frozen=True)
class AuthoritativeChildFinality:
    """Read-only finality projection for one durably bound credit instruction."""

    status: str
    final: bool
    reason: str
    attempt_id: str | None = None


class PaymentLifecycleService:
    """State-safe persistence helpers for provider-neutral payment execution."""

    MAX_CALLBACK_ATTEMPTS = 5
    UNCERTAIN_POLL_DELAY = timedelta(minutes=5)

    _OUTCOME_TO_STATUS = {  # noqa: RUF012
        "settled": PaymentAttempt.STATUS_SETTLED,
        "rejected": PaymentAttempt.STATUS_REJECTED,
        "invalid_account": PaymentAttempt.STATUS_REJECTED,
        "insufficient_funds": PaymentAttempt.STATUS_REJECTED,
        "retryable": PaymentAttempt.STATUS_RETRYABLE,
        "uncertain": PaymentAttempt.STATUS_UNCERTAIN,
        "review": PaymentAttempt.STATUS_REVIEW,
        "dead_letter": PaymentAttempt.STATUS_DEAD_LETTER,
    }

    @staticmethod
    def fingerprint(payload: Mapping[str, Any]) -> str:
        """Return a canonical request fingerprint without persisting payload PII."""
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def payload_hash(payload: Mapping[str, Any]) -> str:
        return PaymentLifecycleService.fingerprint(payload)

    @classmethod
    def get_or_create_attempt(
        cls,
        *,
        tenant_id: str,
        operation: str,
        request_id: str,
        payload: Mapping[str, Any],
        audit_created: bool = True,
        **defaults: Any,
    ) -> tuple[PaymentAttempt, bool]:
        """Create one scoped attempt or replay the canonical existing attempt.

        Reusing the same scope/key with a changed payload is deterministic and
        raises ``IdempotencyConflict``. The nested savepoint keeps the outer
        transaction usable after a concurrent uniqueness collision.
        """
        fingerprint = cls.fingerprint(payload)
        lookup = {
            "tenant_id": tenant_id[:100],
            "operation": operation[:30],
            "request_id": request_id[:100],
        }
        with transaction.atomic():
            existing = PaymentAttempt.objects.select_for_update().filter(**lookup).first()
            if existing is not None:
                if existing.payload_fingerprint != fingerprint:
                    raise IdempotencyConflict("idempotency key was reused with a different payload")
                return existing, False
            try:
                with transaction.atomic():
                    attempt = PaymentAttempt.objects.create(
                        **lookup,
                        payload_fingerprint=fingerprint,
                        **defaults,
                    )
            except IntegrityError:
                attempt = PaymentAttempt.objects.select_for_update().get(**lookup)
                if attempt.payload_fingerprint != fingerprint:
                    raise IdempotencyConflict("idempotency key was reused with a different payload")  # noqa: B904
                return attempt, False
            if audit_created:
                cls.audit(
                    attempt,
                    GovStackPaymentAuditEntry.ACTION_PAYMENT_ATTEMPT_CREATED,
                    {"operation": attempt.operation},
                )
            return attempt, True

    @staticmethod
    def audit(
        attempt: PaymentAttempt,
        action: str,
        details: Mapping[str, Any] | None = None,
    ) -> GovStackPaymentAuditEntry:
        """Append a lifecycle event using only non-PII, correlation-safe details."""
        return GovStackPaymentAuditEntry.objects.create(
            action=action,
            actor_bb_id=attempt.source_bb_id[:50],
            object_type="payment_attempt",
            object_pk=str(attempt.pk),
            request_id=attempt.request_id[:20],
            details=dict(details or {}),
        )

    @classmethod
    def apply_outcome(
        cls,
        attempt: PaymentAttempt,
        outcome: PaymentOutcome,
        *,
        now: Any = None,
    ) -> PaymentAttempt:
        """Apply one provider-neutral outcome with guarded, auditable transition."""
        now = now or timezone.now()
        target = cls._OUTCOME_TO_STATUS.get(outcome.status, PaymentAttempt.STATUS_REVIEW)
        with transaction.atomic():
            attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt.pk)
            if attempt.is_terminal:
                return attempt
            attempt.transition_to(
                target,
                failure_code=outcome.code[:50],
                failure_category=outcome.category[:30],
                retryable=bool(outcome.retryable),
                provider_attempt_id=outcome.provider_attempt_id[:100],
                external_transaction_id=outcome.external_transaction_id[:100],
                last_error=outcome.message[:255],
            )
            attempt.attempt_count += 1
            attempt.next_retry_at = None
            if target == PaymentAttempt.STATUS_RETRYABLE:
                attempt.next_retry_at = now + timedelta(
                    minutes=min(60, 2 ** min(attempt.attempt_count, 6))
                )
                cls.audit(
                    attempt,
                    GovStackPaymentAuditEntry.ACTION_PAYMENT_RETRY_SCHEDULED,
                    {"attempt_count": attempt.attempt_count, "failure_code": attempt.failure_code},
                )
            elif target == PaymentAttempt.STATUS_UNCERTAIN:
                # An uncertain provider result is polled/reconciled; it is never
                # blindly resubmitted as a normal retry.
                attempt.next_retry_at = now + cls.UNCERTAIN_POLL_DELAY
                cls.audit(
                    attempt,
                    GovStackPaymentAuditEntry.ACTION_PAYMENT_UNCERTAIN,
                    {"failure_code": attempt.failure_code},
                )
            elif target in {PaymentAttempt.STATUS_REVIEW, PaymentAttempt.STATUS_DEAD_LETTER}:
                cls.audit(
                    attempt,
                    GovStackPaymentAuditEntry.ACTION_PAYMENT_REVIEW_REQUIRED,
                    {"status": target, "failure_code": attempt.failure_code},
                )
            attempt.save()
            cls.audit(
                attempt,
                GovStackPaymentAuditEntry.ACTION_PAYMENT_OUTCOME_RECORDED,
                {
                    "status": target,
                    "failure_code": attempt.failure_code,
                    "retryable": attempt.retryable,
                    "attempt_count": attempt.attempt_count,
                },
            )
            return attempt

    @classmethod
    def queue_callback(
        cls,
        *,
        attempt: PaymentAttempt,
        callback_url: str,
        payload: Mapping[str, Any],
        now: Any = None,
    ) -> tuple[CallbackDelivery, bool]:
        """Persist one deduplicated callback event before transport is attempted."""
        now = now or timezone.now()
        digest = cls.payload_hash(payload)
        with transaction.atomic():
            delivery, created = CallbackDelivery.objects.get_or_create(
                attempt=attempt,
                payload_hash=digest,
                defaults={
                    "callback_url": callback_url[:500],
                    "payload": dict(payload),
                    "status": CallbackDelivery.STATUS_PENDING,
                    "next_attempt_at": now,
                },
            )
            if created:
                cls.audit(
                    attempt,
                    GovStackPaymentAuditEntry.ACTION_CALLBACK_QUEUED,
                    {"delivery_id": str(delivery.pk)},
                )
            return delivery, created

    @classmethod
    def record_callback_result(
        cls,
        delivery: CallbackDelivery,
        *,
        http_status: int | None = None,
        error_code: str = "",
        now: Any = None,
    ) -> CallbackDelivery:
        """Persist callback result, applying bounded backoff and dead-lettering."""
        now = now or timezone.now()
        with transaction.atomic():
            delivery = (
                CallbackDelivery.objects.select_for_update()
                .select_related("attempt")
                .get(pk=delivery.pk)
            )
            if delivery.status in {CallbackDelivery.STATUS_DELIVERED, CallbackDelivery.STATUS_DEAD}:
                return delivery
            delivery.delivery_count += 1
            delivery.last_http_status = http_status
            delivery.last_error = error_code[:255]
            success = http_status is not None and 200 <= http_status < 300
            if success:
                delivery.status = CallbackDelivery.STATUS_DELIVERED
                delivery.next_attempt_at = None
                action = GovStackPaymentAuditEntry.ACTION_CALLBACK_DELIVERED
            elif delivery.delivery_count >= cls.MAX_CALLBACK_ATTEMPTS:
                delivery.status = CallbackDelivery.STATUS_DEAD
                delivery.next_attempt_at = None
                action = GovStackPaymentAuditEntry.ACTION_CALLBACK_DEAD_LETTERED
            else:
                delivery.status = CallbackDelivery.STATUS_RETRY
                delivery.next_attempt_at = now + timedelta(
                    minutes=min(60, 2 ** min(delivery.delivery_count, 6))
                )
                action = GovStackPaymentAuditEntry.ACTION_PAYMENT_RETRY_SCHEDULED
            delivery.save()
            cls.audit(
                delivery.attempt,
                action,
                {
                    "delivery_id": str(delivery.pk),
                    "http_status": http_status,
                    "delivery_count": delivery.delivery_count,
                    "error_code": error_code[:50],
                },
            )
            return delivery

    @classmethod
    def reconcile(
        cls,
        attempt: PaymentAttempt,
        provider_status: str,
        source_bb_status: str = "",
    ) -> PaymentReconciliation:
        """Record one internal/provider/source status comparison for review."""
        internal = attempt.status
        provider_status = provider_status[:30]
        source_bb_status = source_bb_status[:30]
        matched = (provider_status == "settled" and internal == PaymentAttempt.STATUS_SETTLED) or (
            provider_status == "rejected" and internal == PaymentAttempt.STATUS_REJECTED
        )
        status = (
            PaymentReconciliation.STATUS_MATCHED
            if matched
            else (
                PaymentReconciliation.STATUS_UNKNOWN
                if provider_status in {"", "unknown"}
                else PaymentReconciliation.STATUS_MISMATCH
            )
        )
        reconciliation = PaymentReconciliation.objects.create(
            attempt=attempt,
            provider_status=provider_status,
            source_bb_status=source_bb_status,
            internal_status=internal,
            status=status,
            external_transaction_id=attempt.external_transaction_id[:100],
        )
        cls.audit(
            attempt,
            GovStackPaymentAuditEntry.ACTION_RECONCILIATION_RECORDED,
            {"status": status, "provider_status": provider_status},
        )
        return reconciliation

    @staticmethod
    def binding_hash(tenant_id: str, attempt: PaymentAttempt, amount: Any, currency: str) -> str:
        return hashlib.sha256(
            f"{tenant_id}|{attempt.pk}|{amount}|{currency.upper()}".encode()
        ).hexdigest()

    @classmethod
    def bind_credit_instruction_attempt(
        cls,
        instruction: CreditInstruction,
        attempt: PaymentAttempt,
    ) -> CreditInstruction:
        """Durably bind one instruction to its canonical existing bulk attempt.

        The binding is additive and immutable in practice: a replay can reuse the
        same attempt but a different attempt for the same instruction is rejected.
        This prevents batch aggregation from selecting a parallel lifecycle row.
        """
        with transaction.atomic():
            locked_instruction = CreditInstruction.objects.select_for_update().get(
                pk=instruction.pk
            )
            locked_attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt.pk)
            expected_request_id = (
                f"{locked_instruction.batch.request_id}:{locked_instruction.instruction_id}"
            )
            if (
                locked_attempt.operation != "g2p_bulk_instruction"
                or locked_attempt.request_id != expected_request_id
                or locked_attempt.amount != locked_instruction.amount
                or locked_attempt.currency.upper() != locked_instruction.currency.upper()
            ):
                raise ValueError("payment attempt does not match the credit instruction identity")
            if locked_instruction.payment_attempt_id is None:
                locked_instruction.payment_attempt = locked_attempt
                locked_instruction.save(update_fields=["payment_attempt", "updated_at"])
            elif locked_instruction.payment_attempt_id != locked_attempt.pk:
                raise IdempotencyConflict("credit instruction is already bound to another attempt")
            return locked_instruction

    @classmethod
    def materialize_credit_instruction_finality(
        cls,
        instruction: CreditInstruction,
    ) -> AuthoritativeChildFinality:
        """Project child finality from bound, verified, reconciled provider evidence.

        Local mapper eligibility, attempt status alone, and unverified or
        conflicting evidence are deliberately insufficient.  The method does not
        create policy decisions or mutate provider/audit/callback records.
        """
        with transaction.atomic():
            locked_instruction = (
                CreditInstruction.objects.select_for_update()
                .select_related("batch", "payment_attempt")
                .get(pk=instruction.pk)
            )
            attempt = locked_instruction.payment_attempt
            if attempt is None:
                return AuthoritativeChildFinality("review", False, "instruction_unbound")
            attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt.pk)
            expected_request_id = (
                f"{locked_instruction.batch.request_id}:{locked_instruction.instruction_id}"
            )
            if (
                attempt.operation != "g2p_bulk_instruction"
                or attempt.request_id != expected_request_id
                or attempt.amount != locked_instruction.amount
                or attempt.currency.upper() != locked_instruction.currency.upper()
            ):
                return AuthoritativeChildFinality(
                    "review", False, "bound_attempt_malformed", str(attempt.pk)
                )

            observations = list(
                ProviderObservation.objects.select_for_update()
                .filter(attempt=attempt)
                .order_by("created_at", "pk")
            )
            accepted = [
                observation
                for observation in observations
                if observation.accepted_finality
                and observation.verified
                and observation.outcome
                in {ProviderObservation.OUTCOME_SETTLED, ProviderObservation.OUTCOME_REJECTED}
            ]
            if len(accepted) != 1:
                return AuthoritativeChildFinality(
                    "review", False, "missing_or_conflicting_observation", str(attempt.pk)
                )
            observation = accepted[0]
            expected_hash = cls.binding_hash(
                attempt.tenant_id,
                attempt,
                locked_instruction.amount,
                locked_instruction.currency,
            )
            if (
                observation.tenant_id != attempt.tenant_id
                or observation.amount != locked_instruction.amount
                or observation.currency.upper() != locked_instruction.currency.upper()
                or observation.binding_hash != expected_hash
                or any(item.pk != observation.pk for item in observations)
            ):
                return AuthoritativeChildFinality(
                    "review", False, "observation_unverified_or_conflicting", str(attempt.pk)
                )
            if attempt.status != observation.outcome:
                return AuthoritativeChildFinality(
                    "review", False, "attempt_status_not_authoritative", str(attempt.pk)
                )

            reconciliations = list(
                PaymentReconciliation.objects.select_for_update()
                .filter(attempt=attempt)
                .order_by("created_at", "pk")
            )
            if not reconciliations or any(
                reconciliation.status != PaymentReconciliation.STATUS_MATCHED
                or reconciliation.provider_status != observation.outcome
                or reconciliation.internal_status != observation.outcome
                for reconciliation in reconciliations
            ):
                return AuthoritativeChildFinality(
                    "review", False, "reconciliation_unresolved_or_conflicting", str(attempt.pk)
                )
            return AuthoritativeChildFinality(
                observation.outcome, True, "verified_reconciled_finality", str(attempt.pk)
            )

    @classmethod
    def record_observation(
        cls,
        attempt: PaymentAttempt,
        *,
        tenant_id: str,
        observation_kind: str,
        observation_id: str,
        outcome: str,
        amount: Any,
        currency: str,
        provider_transaction_id: str = "",
        event_id: str = "",
        verified: bool = False,
        verification_method: str = "",
        binding_hash: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> ProviderObservation:
        """Persist evidence; only verified exact bindings can establish finality."""
        exact = (
            tenant_id == attempt.tenant_id
            and amount == attempt.amount
            and currency.upper() == attempt.currency.upper()
            and binding_hash == cls.binding_hash(tenant_id, attempt, amount, currency)
            and verified
            and outcome
            in {ProviderObservation.OUTCOME_SETTLED, ProviderObservation.OUTCOME_REJECTED}
        )
        with transaction.atomic():
            locked = PaymentAttempt.objects.select_for_update().get(pk=attempt.pk)
            obs = ProviderObservation.objects.create(
                attempt=locked,
                tenant_id=tenant_id[:100],
                observation_kind=observation_kind[:20],
                observation_id=observation_id[:160],
                provider_transaction_id=provider_transaction_id[:100],
                event_id=event_id[:160],
                amount=amount,
                currency=currency[:3],
                outcome=outcome[:20],
                verified=verified,
                verification_method=verification_method[:80],
                binding_hash=binding_hash[:64],
                accepted_finality=exact,
                metadata=dict(metadata or {}),
            )
            if exact and not locked.is_terminal:
                cls.apply_outcome(
                    locked,
                    PaymentOutcome(
                        outcome,
                        provider_attempt_id=provider_transaction_id,
                        external_transaction_id=provider_transaction_id,
                    ),
                )
            else:
                cls.audit(
                    locked,
                    GovStackPaymentAuditEntry.ACTION_PAYMENT_REVIEW_REQUIRED,
                    {"observation_id": str(obs.pk), "exact_binding": exact},
                )
            return obs

    @classmethod
    def record_provider_result(
        cls,
        attempt: PaymentAttempt,
        result: ProviderResult,
        *,
        source: str = ProviderObservation.KIND_PROVIDER,
    ) -> ProviderObservation:
        """Persist one adapter result under an exact-binding, fail-closed policy.

        A returned status is not itself proof of finality.  Only a result that
        identifies a stable observation/event and is explicitly marked verified
        by the configured adapter can settle or reject a payment attempt.
        """
        normalized = normalize_result(result)
        final_outcome = {
            ProviderOutcome.SETTLED: ProviderObservation.OUTCOME_SETTLED,
            ProviderOutcome.REJECTED: ProviderObservation.OUTCOME_REJECTED,
            ProviderOutcome.INVALID_ACCOUNT: ProviderObservation.OUTCOME_REJECTED,
            ProviderOutcome.INSUFFICIENT_FUNDS: ProviderObservation.OUTCOME_REJECTED,
        }.get(normalized.outcome, ProviderObservation.OUTCOME_UNCERTAIN)
        observation_id = (
            normalized.observation_id
            or normalized.event_id
            or normalized.external_transaction_id
            or normalized.provider_attempt_id
        )
        # There is no durable, independently addressable observation without an
        # identifier; leave such responses explicitly non-final even if an
        # adapter accidentally marks them verified.
        verified = bool(normalized.verified and observation_id)
        binding_hash = cls.binding_hash(
            attempt.tenant_id,
            attempt,
            attempt.amount,
            attempt.currency,
        )
        observation = cls.record_observation(
            attempt,
            tenant_id=attempt.tenant_id,
            observation_kind=source,
            observation_id=observation_id or f"unidentified:{attempt.pk}",
            outcome=final_outcome,
            amount=attempt.amount,
            currency=attempt.currency,
            provider_transaction_id=(
                normalized.external_transaction_id or normalized.provider_attempt_id
            ),
            event_id=normalized.event_id,
            verified=verified,
            verification_method=normalized.verification_method,
            binding_hash=binding_hash if verified else "",
            metadata={
                "provider_code": normalized.code,
                "outcome": str(normalized.outcome),
                "source": source,
            },
        )
        attempt.refresh_from_db()
        if not observation.accepted_finality and not attempt.is_terminal:
            if normalized.outcome in {
                ProviderOutcome.TIMEOUT,
                ProviderOutcome.NETWORK,
                ProviderOutcome.UNCERTAIN,
            }:
                non_final = PaymentOutcome(
                    "uncertain",
                    code=normalized.code or "PROVIDER_OUTCOME_UNCERTAIN",
                    category="provider",
                    message=normalized.message or "Provider outcome requires a status check.",
                )
            elif normalized.retryable:
                non_final = PaymentOutcome(
                    "retryable",
                    code=normalized.code or "PROVIDER_RETRYABLE_FAILURE",
                    category="provider",
                    retryable=True,
                    message=normalized.message
                    or "Provider retry is eligible after reconciliation.",
                )
            else:
                non_final = PaymentOutcome(
                    "review",
                    code=normalized.code or "UNVERIFIED_PROVIDER_OBSERVATION",
                    category="reconciliation",
                    message=normalized.message
                    or "Provider observation is not verified for finality.",
                )
            cls.apply_outcome(attempt, non_final)
            attempt.refresh_from_db()
        cls.reconcile(
            attempt,
            provider_status=final_outcome if verified else "unknown",
        )
        return observation

    @staticmethod
    def due_retries(now: Any = None):  # noqa: ANN205
        now = now or timezone.now()
        return PaymentAttempt.objects.filter(
            status=PaymentAttempt.STATUS_RETRYABLE,
            next_retry_at__lte=now,
        ).order_by("next_retry_at")

    @staticmethod
    def due_uncertain(now: Any = None):  # noqa: ANN205
        now = now or timezone.now()
        return PaymentAttempt.objects.filter(
            status=PaymentAttempt.STATUS_UNCERTAIN,
            next_retry_at__lte=now,
        ).order_by("next_retry_at")

    @staticmethod
    def due_callbacks(now: Any = None):  # noqa: ANN205
        now = now or timezone.now()
        return CallbackDelivery.objects.filter(
            status__in=[CallbackDelivery.STATUS_PENDING, CallbackDelivery.STATUS_RETRY],
            next_attempt_at__lte=now,
        ).order_by("next_attempt_at")
