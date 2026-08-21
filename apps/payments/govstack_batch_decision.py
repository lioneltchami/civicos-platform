"""Durable, fenced policy decisions for the live bulk-payment worker.

This module persists an outcome only.  It never submits a provider operation,
executes a refund, sends a callback, or replaces the RB-02.2 child-finality
materializer.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable

from django.db import IntegrityError, transaction

from apps.payments.govstack_batch_lease import BatchLeaseHandle, BatchLeaseService
from apps.payments.govstack_batch_policy import BatchDecision, evaluate
from apps.payments.govstack_models import BulkPaymentBatch, GovStackBatchDecision


class BatchDecisionConflict(ValueError):
    """A live lease generation attempted to persist changed policy inputs."""


@dataclass(frozen=True)
class BatchDecisionResult:
    decision: GovStackBatchDecision
    created: bool
    policy: BatchDecision


class BatchDecisionService:
    """Create or replay exactly one logical decision for deterministic inputs."""

    @staticmethod
    def _canonical_items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "id": str(item["id"]),
                "status": str(item["status"]),
                "amount": str(item.get("amount", "0.00")),
            }
            for item in sorted(items, key=lambda value: str(value["id"]))
        ]

    @classmethod
    def fingerprint(
        cls,
        *,
        batch: BulkPaymentBatch,
        items: Iterable[dict[str, Any]],
        failure_threshold: float,
        return_funds_enabled: bool,
    ) -> str:
        payload = {
            "batch_pk": str(batch.pk),
            "items": cls._canonical_items(items),
            "failure_threshold": str(failure_threshold),
            "return_funds_enabled": bool(return_funds_enabled),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _reason(policy: BatchDecision, *, total_count: int) -> str:
        if total_count == 0:
            return "empty_batch"
        return f"policy_{policy.state}"

    @staticmethod
    def _action(
        *,
        policy: BatchDecision,
        total_count: int,
        rejected_count: int,
        non_final_count: int,
        return_funds_enabled: bool,
    ) -> str:
        if total_count == 0:
            return GovStackBatchDecision.ACTION_EMPTY
        if non_final_count:
            if policy.state == "paused":
                return GovStackBatchDecision.ACTION_PAUSE
            if policy.state in {"retryable", "uncertain"}:
                return GovStackBatchDecision.ACTION_RETRY
            return GovStackBatchDecision.ACTION_REVIEW
        if policy.state not in {"completed", "partial"}:
            # Fail closed if the evaluator ever returns an unknown all-final
            # state rather than projecting a terminal batch result.
            return GovStackBatchDecision.ACTION_REVIEW
        if rejected_count and return_funds_enabled:
            return GovStackBatchDecision.ACTION_RETURN_FUNDS
        return GovStackBatchDecision.ACTION_TERMINAL

    @classmethod
    def decide(
        cls,
        *,
        batch: BulkPaymentBatch,
        lease_handle: BatchLeaseHandle,
        items: list[dict[str, Any]],
        failure_threshold: float,
        return_funds_enabled: bool,
    ) -> BatchDecisionResult:
        """Fence, create, or replay one durable policy decision.

        The fingerprint omits lease generation deliberately: the same logical
        task redelivery can replay its decision under a later legitimate lease.
        A changed input set under the same generation is an explicit conflict.
        """
        canonical_items = cls._canonical_items(items)
        policy = evaluate(canonical_items, failure_threshold=failure_threshold)
        total_count = len(canonical_items)
        settled_count = sum(item["status"] == "settled" for item in canonical_items)
        rejected_count = sum(item["status"] == "rejected" for item in canonical_items)
        non_final_count = total_count - settled_count - rejected_count
        fingerprint = cls.fingerprint(
            batch=batch,
            items=canonical_items,
            failure_threshold=failure_threshold,
            return_funds_enabled=return_funds_enabled,
        )
        action = cls._action(
            policy=policy,
            total_count=total_count,
            rejected_count=rejected_count,
            non_final_count=non_final_count,
            return_funds_enabled=return_funds_enabled,
        )
        if non_final_count and action in {
            GovStackBatchDecision.ACTION_TERMINAL,
            GovStackBatchDecision.ACTION_RETURN_FUNDS,
        }:
            raise BatchDecisionConflict("non-final children cannot produce a terminal decision")

        with transaction.atomic():
            BatchLeaseService.assert_current_owner(lease_handle)
            same_generation = (
                GovStackBatchDecision.objects.select_for_update()
                .filter(batch=batch, lease_generation=lease_handle.generation)
                .exclude(fingerprint=fingerprint)
                .first()
            )
            if same_generation is not None:
                raise BatchDecisionConflict(
                    "batch lease generation already has a different decision fingerprint"
                )
            existing = (
                GovStackBatchDecision.objects.select_for_update()
                .filter(batch=batch, fingerprint=fingerprint)
                .first()
            )
            if existing is not None:
                return BatchDecisionResult(existing, False, policy)
            try:
                with transaction.atomic():
                    decision = GovStackBatchDecision.objects.create(
                        batch=batch,
                        fingerprint=fingerprint,
                        lease_owner_token=lease_handle.owner_token,
                        lease_generation=lease_handle.generation,
                        policy_state=policy.state,
                        outcome_action=action,
                        total_count=total_count,
                        settled_count=settled_count,
                        rejected_count=rejected_count,
                        non_final_count=non_final_count,
                        reason=cls._reason(policy, total_count=total_count),
                        details={
                            "retry_ids": list(policy.retry_ids),
                            "settled_ids": list(policy.settled_ids),
                            "kick_back": policy.kick_back,
                        },
                    )
            except IntegrityError:
                decision = GovStackBatchDecision.objects.select_for_update().get(
                    batch=batch,
                    fingerprint=fingerprint,
                )
                return BatchDecisionResult(decision, False, policy)
            return BatchDecisionResult(decision, True, policy)
