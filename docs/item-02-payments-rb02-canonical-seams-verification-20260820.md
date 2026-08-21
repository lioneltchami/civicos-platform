# Item 02 — RB-02 Canonical Live Persistence Seams Verification

**Date:** 2026-08-20  
**Stage:** Independent verification of the frozen mapping  
**Scope:** The live `process_bulk_payment_batch()` worker and its directly invoked asynchronous provider-runtime/lifecycle chain. No RB-02 implementation was changed in this cycle.

> **Final verdict: Complete and safe.** Two fresh independent closure reviews confirmed that the frozen mapping identifies the real current persistence seams needed as the foundation for a later RB-02 implementation. The reviews found no material factual error or omitted canonical persistence seam within the defined worker scope.

## Verification boundary

The verification deliberately distinguishes the bulk worker path from other live payment routes. `PaymentCommand`, `PaymentCommandOutbox`, and `PaymentCommandService.admit()` are real mounted command-route records, but `process_bulk_payment_batch()` does not call them. They are therefore correctly excluded from this **RB-02 bulk-worker** map, rather than being misclassified as test helpers or in-memory records.

| Review boundary | Included | Excluded as a separate route surface |
|---|---|---|
| RB-02 worker path | `process_bulk_payment_batch()`; `_record_bulk_instruction_lifecycle()`; `BatchLease`; `PaymentAttempt`; post-commit orchestration; recovery/runtime; provider registration resolution; `PaymentExecutionIntent`; attempt-bound provider evidence/reconciliation; audit; callback outbox/delivery. | Mounted command-route admission/outbox records that the bulk worker does not invoke. |

## Independent closure findings

| Canonical seam | Verified live model / call path | Result |
|---|---|---|
| Worker lease | `BatchLease` is a durable one-to-one batch row with `owner_token`, `generation`, and `expires_at`; the task acquires it or takes it over only after expiry, then performs an early recheck. | **Confirmed.** The map correctly records that no current-owner renewal/heartbeat exists and that existing task-local handling is not the future fenced lease service. |
| Instruction attempt identity | `_record_bulk_instruction_lifecycle()` calls `PaymentLifecycleService.get_or_create_attempt()` with bulk `operation="g2p_bulk_instruction"` and `request_id="{batch.request_id}:{instruction.instruction_id}"`. | **Confirmed.** `PaymentAttempt` has durable unique `(tenant_id, operation, request_id)` identity; `CreditInstruction` has no existing attempt binding. |
| Asynchronous bridge | `enqueue_attempt()` registers `orchestrate_attempt_task` through `transaction.on_commit()`; the task reaches `orchestrate_attempt()` and `RecoverPaymentAttemptCommand.execute()` before runtime submit/poll processing. | **Confirmed.** This is dispatch/recovery orchestration, not evidence or settlement. |
| Provider configuration | `ProviderRuntime.resolve()` and `resolve_provider()` read active tenant/operation-scoped `ProviderRegistration`; `PaymentAttempt.provider_registration` is the protected durable relationship when populated. | **Confirmed.** Registration is a durable configuration read, never provider financial evidence; blank-tenant bulk attempts can stop before normal provider resolution. |
| Execution admission and claims | `ProviderRuntime.submit_or_poll()` creates/locks one-to-one `PaymentExecutionIntent`, records submit admission, selects poll versus resubmit, and writes fenced attempt-claim state. `finalize_claimed_result()` rejects stale token/generation before result persistence. | **Confirmed.** `PaymentExecutionIntent` is admission/correlation protection, not finality. |
| Finality and reconciliation | `record_provider_result()` writes attempt-bound `ProviderObservation`; only exact, verified, accepted-finality `settled`/`rejected` evidence is terminal. `reconcile()` writes attempt-bound `PaymentReconciliation`. | **Confirmed.** Observation uniqueness is `(observation_kind, observation_id)` plus one accepted-finality observation per attempt; reconciliation is durable history without a uniqueness constraint. |
| Audit | `GovStackPaymentAuditEntry` is written for instruction and batch events by the live task and for lifecycle/provider/reconciliation/callback events by the lifecycle service. | **Confirmed.** Records are append-only, have no current batch-decision uniqueness key, and are not currently lease-generation fenced. |
| Callback outbox | The task creates/replays a callback `PaymentAttempt`, queues `CallbackDelivery`, posts the callback, and persists the result. | **Confirmed.** Attempt identity is `(tenant_id, operation, request_id)` with `g2p_bulk_callback`; delivery uniqueness is separately `(attempt, payload_hash)` via `gs_callback_attempt_payload_uniq`. Network I/O is task-local; outbox/result/retry/dead-letter state is durable. |
| Current ordering | Batch lock/status gate; lease acquisition or expired takeover; early ownership recheck; instruction lock/lookup; attempt lifecycle/on-commit dispatch or local outcome; instruction audit/status; mapper-count batch status/audit; commit; callback outbox; transport; durable callback result. | **Confirmed.** The map documents the actual current order, including its lack of provider-finality-aware batch completion. |

## Mapping corrections completed during verification

The initial mapping review identified gaps and precision risks. They were corrected before the closure reviews: the provider execution-admission seam (`PaymentExecutionIntent`), execution/finalization distinction, separate command-route scope boundary, `BatchLease`, provider-registration configuration read, attempt claim/recovery fields, post-commit orchestration/recovery bridge, and the absence of current lease renewal/heartbeat. The preliminary record is retained as `item-02-payments-rb02-canonical-seams-initial-verification-20260820.md` for traceability.

## Implementation gate

The mapping is now frozen and may be used as the foundation for a **full RB-02 implementation attempt only**. This conclusion does **not** claim RB-02 is closed. The seven acceptance points, mandatory live `TransactionTestCase` methods, all-or-discard rule, and prohibition on RB-03, RB-04, staging, official-suite, or Stage 4 work remain in effect until a complete implementation is independently verified.
