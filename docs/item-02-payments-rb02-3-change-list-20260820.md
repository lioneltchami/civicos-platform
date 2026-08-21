# Item 02 — RB-02.3 Exact Change List

**Date:** 2026-08-20  
**Stage:** Fresh blind Stage 2 review  
**Scope:** Live policy invocation, durable fenced decisions, one logical audit/outbox coupling, and exactly six new real-path tests.

> **Conclusion:** RB-02.3 reuses the closed lease and authoritative child-finality seams. It adds a single decision persistence seam and a focused finalization service; it does not redesign the evaluator, execute a refund, or reopen prior increments.

## Ordered file-level changes

| Order | File | Required RB-02.3 change |
|---:|---|---|
| 1 | `config/settings/base.py` | Add one explicit default-off configuration flag, `GOVSTACK_BULK_RETURN_FUNDS_ENABLED`, selecting whether an all-final rejected/partial decision is persisted with the explicit `return_funds` action. The flag must never execute a refund. Tests override the setting locally; no environment-specific settings rewrite is required. |
| 2 | `apps/payments/govstack_models.py` | Add `GovStackBatchDecision`, an additive append-only durable record with `batch` FK, owner token, lease generation, policy/input fingerprint, policy state, outcome action, deterministic settled/rejected/non-final/total counts, reason, and decision metadata. Add a unique `(batch, fingerprint)` constraint and a decision audit action constant. The first writer’s lease generation is retained for traceability; same logical inputs replay the decision across a later valid delivery generation. Do not add a second audit/outbox/refund model. |
| 3 | `apps/payments/migrations/0045_item02_rb023_govstack_batch_decision.py` | Add only the new decision table, uniqueness constraint, indexes, and fields. It must follow `0044_item02_rb022_credit_instruction_payment_attempt_binding` and preserve all existing rows/migrations. |
| 4 | `apps/payments/govstack_batch_decision.py` | Add a focused finalization service. It accepts materialized RB-02.2 child results plus the current `BatchLeaseHandle`; calls the existing `govstack_batch_policy.evaluate()` once; computes a stable fingerprint from batch identity, ordered child states/counts, policy threshold, and return-funds setting; maps the policy result to `empty`, `pause`, `retry`, `review`, `terminal`, or `return_funds`; asserts the current owner; and atomically creates/replays the durable decision. The same fingerprint replays regardless of a later valid delivery generation; a changed fingerprint in the same generation is rejected, and a stale handle writes nothing. The service must forbid terminal/return-funds action when any child is non-final. It must not modify the existing evaluator or call any provider/refund transfer. |
| 5 | `apps/payments/govstack_tasks.py` | Replace mapper/count-derived finalization in the live `process_bulk_payment_batch()` path with a call to the decision service after the closed RB-02.2 materializer has produced all child states. Apply terminal batch status/amount/timestamp only for an all-final `terminal`/`return_funds` decision; retain `processing` for `pause`, `retry`, or `review`; persist the explicit `empty` result without fabricating settlement. Fence the decision, projection, audit, callback, callback-result, and release by the current owner/generation. |
| 6 | `apps/payments/govstack_tasks.py` and `apps/payments/govstack_failure_services.py` | Couple side effects to the decision creation within the task transaction. On first decision insertion only, create one append-only `GovStackPaymentAuditEntry` using `object_type="batch_decision"`, `object_pk=str(decision.pk)`, current request correlation, and non-PII outcome/fingerprint/reason/count details. When the batch has an existing callback URL, create/replay the canonical callback attempt and call `PaymentLifecycleService.queue_callback()` with a decision-derived payload identity; preserve `CallbackDelivery`’s `(attempt, payload_hash)` uniqueness. A replay creates no new decision/audit/delivery, and all three rows commit or roll back together. |
| 7 | `apps/payments/tests/test_item02_rb02_batch_lease_live.py` | Extend the existing real-database `TransactionTestCase` class with exactly these six RB-02.3 methods: `test_threshold_pause_persists_one_idempotent_decision`, `test_retry_decision_is_durable_and_idempotent`, `test_review_decision_is_durable_and_idempotent`, `test_configured_return_funds_is_explicit_and_idempotent`, `test_empty_batch_has_one_durable_outcome`, and `test_duplicate_finalization_creates_one_decision_audit_and_callback`. Each uses real task, lease, policy, decision, audit, and callback rows; no primitive-only or mock-only test counts. |

## Frozen policy-action mapping

| Materialized child set / existing evaluator state | Persisted RB-02.3 action | Projection rule |
|---|---|---|
| No children | `empty` | Explicit durable empty outcome; no fabricated settlement. |
| `paused` | `pause` | Non-terminal. |
| `retryable` or `uncertain` | `retry` | Non-terminal. |
| `unresolved` or `review` | `review` | Non-terminal. |
| All children final; policy `completed` or `partial` | `terminal` | Terminal projection allowed. |
| All children final with rejection/partial result and return-funds setting enabled | `return_funds` | Terminal projection allowed; persist action only, never execute a refund. |

## Rejection criteria

Discard the increment if `evaluate()` is not invoked by the real task; decision uniqueness/fencing is not durable; the fingerprint is not deterministic; a changed same-generation fingerprint is silently accepted; a non-final child permits terminal projection; a replay creates a duplicate decision, audit, or callback delivery; any of the six exact methods is absent or failing; or any RB-02.1/RB-02.2, competing-worker, RB-03/RB-04, staging, official-suite, or Stage 4 work is changed.
