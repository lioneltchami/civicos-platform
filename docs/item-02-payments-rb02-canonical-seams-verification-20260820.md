# Item 02 — RB-02 Canonical Live Persistence Seams Verification

**Date:** 2026-08-20  
**Stage:** Independent verification of the frozen seam mapping  
**Scope:** Mapping verification only. No lease, policy, model, task, test, staging, official-suite, or submission implementation was changed.

> **Verdict: Incomplete — not yet safe as the sole foundation for RB-02 implementation.**
>
> One fresh reviewer found the direct `process_bulk_payment_batch()` mapping accurate and safe. The other fresh reviewer independently found a material omitted canonical live persistence seam: `PaymentExecutionIntent` and its submit-admission/finalize-claim path in the provider runtime. Under the strict all-or-discard safety rule, that unresolved completeness finding controls. RB-02 implementation must not start from the present mapping.

## Verification method

Two reviewers with no access to the Stage 1 reasoning received the current source archive and the candidate mapping. Each traced production sources only, with particular attention to the live task, lifecycle service, provider runtime, data models, audit creation, callback delivery, and database constraints. Neither reviewer found evidence that the mapped audit or callback paths were test helpers or parallel replacements.

| Reviewer focus | Independent conclusion | Effect on verdict |
|---|---|---|
| Live task and provider-evidence trace | The mapped `ProviderObservation`/`PaymentReconciliation` attempt FKs, audit rows, callback delivery key, and direct task ordering are correct. | Supports the accuracy of the mapped direct batch-task records. |
| Audit, outbox, and end-to-end runtime trace | The mapping omits `PaymentExecutionIntent`, the durable submit/poll admission seam that precedes fenced provider I/O and `record_provider_result()`. It also identifies precision omissions about callback-key wording, audit request-ID width, and blank-tenant runtime behavior. | Prevents a finding that the mapping is complete and safe. |

## Findings confirmed as accurate

| Mapping area | Confirmed canonical records and path | Verification result |
|---|---|---|
| Provider evidence | `ProviderObservation.attempt` and `PaymentReconciliation.attempt` bind durable evidence/history to `PaymentAttempt`. `PaymentLifecycleService.record_provider_result()` persists exact verified observations and finality before lifecycle outcome/reconciliation effects. | Accurate. The pure `govstack_reconciliation.py` helper is in-memory comparison logic, not the persistence seam. |
| Instruction identity gap | `_record_bulk_instruction_lifecycle()` creates/replays a `PaymentAttempt` using the bulk instruction request identity, while `CreditInstruction` currently has no durable attempt FK. | Accurate and correctly presented as an RB-02 implementation gap. |
| Audit | `GovStackPaymentAuditEntry` is the live, append-only instruction/batch/lifecycle audit record. The task writes instruction audits and then batch audits on its production path. | Accurate. These are durable records, not task-local audit substitutes. |
| Callback | The live task creates/replays a callback `PaymentAttempt`, calls `PaymentLifecycleService.queue_callback()`, writes `CallbackDelivery`, performs network delivery, then records the durable result. The delivery uniqueness key is `(attempt, payload_hash)` named `gs_callback_attempt_payload_uniq`. | Accurate. The HTTP request is task-local I/O; outbox state and delivery result are durable. |
| Direct bulk task ordering | The current task locks the batch, performs its task-local lease handling and early recheck, locks instructions and validates beneficiaries, records local lifecycle/audit/status effects, saves mapper-count batch status, writes batch audit, then queues callback delivery after commit. | Accurate for direct task effects. The current completion is correctly identified as not provider-finality-aware. |

## Blocking omission: provider-execution admission and finalization seam

The candidate mapping identifies the durable provider-evidence result seam but not the preceding durable execution-admission seam through which the live provider runtime reaches it. This omission is material because the requested frozen mapping must identify the canonical production persistence path, uniqueness behavior, and durability status—not only the rows written directly by the batch task.

| Omitted seam | Production location | Durable role | Why it is required in the frozen map |
|---|---|---|---|
| `PaymentExecutionIntent` | `apps/payments/govstack_models.py`; used by `ProviderRuntime.submit_or_poll()` in `apps/payments/provider_runtime.py` | Durable execution identity and submit-admission record associated one-to-one with `PaymentAttempt`; unique `(scope, operation, request_identity)`. Its state includes provider correlation and committed submit-admission/claim information before provider I/O. | It is the actual idempotency and recovery/admission boundary between `enqueue_attempt()` from the bulk task and `record_provider_result()`/`ProviderObservation` persistence. Without it, an implementation could incorrectly reason about duplicate prevention, recovery ordering, or ownership of provider submission. |
| Fenced provider finalization | `ProviderRuntime.finalize_claimed_result()` in `provider_runtime.py`, which calls `PaymentLifecycleService.record_provider_result()` | Fenced persistence boundary after a claimed provider operation. | It identifies the live call chain that creates the durable observation/reconciliation evidence the batch must later aggregate. |
| Provider dispatch timing | Provider runtime enqueue is registered after commit; runtime handling for blank-tenant attempts can take an early non-provider-I/O terminal/uncertain path. | The task dispatch is asynchronous and not itself an evidence write. | The mapping must distinguish “attempt created/enqueued” from “provider observation/reconciliation exists.” The current bulk task cannot assume provider evidence will necessarily appear. |

## Non-blocking precision corrections required in the revised mapping

| Topic | Required correction |
|---|---|
| Callback idempotency language | State separately that `PaymentAttempt` deduplicates callback attempts by `(tenant_id, operation, request_id)`, while `CallbackDelivery` deduplicates delivery rows by `(attempt, payload_hash)`. The attempt key does not itself mean “identical payload identity.” |
| Audit request ID | Record the actual `GovStackPaymentAuditEntry.request_id` field width and lifecycle-path truncation behavior. This does not replace the logical batch request correlation, but it matters when designing a future unique batch-decision identity. |
| Execution vs. evidence | State explicitly that `PaymentExecutionIntent` is neither provider evidence nor a direct instruction FK; it is the durable provider submit/poll admission seam. Provider finality remains authoritative only through the attempt-bound verified observation/reconciliation path. |

## Required next action and implementation gate

The canonical mapping document must be amended to include the full live chain:

`process_bulk_payment_batch` → `_record_bulk_instruction_lifecycle` → `PaymentAttempt` → post-commit provider enqueue → `PaymentExecutionIntent` admission/claim → provider I/O → `finalize_claimed_result` → `record_provider_result` → `ProviderObservation` / `PaymentReconciliation`.

It must also incorporate the three precision corrections above. Two fresh independent reviewers must then re-verify the amended mapping. Until both the scope and accuracy are confirmed, the mapping is **not frozen**, RB-02 remains **blocked**, and no Stage 3 implementation may begin.

## Evidence locations

| Evidence | Locations |
|---|---|
| Live bulk task, instruction audit, batch audit, callback creation/transport/result | `apps/payments/govstack_tasks.py`, especially `process_bulk_payment_batch()` and `_record_bulk_instruction_lifecycle()` |
| Attempt creation, observation, reconciliation, audit, callback outbox/result | `apps/payments/govstack_failure_services.py`, especially `get_or_create_attempt()`, `record_provider_result()`, `record_observation()`, `reconcile()`, `queue_callback()`, and `record_callback_result()` |
| Provider submit/poll, execution admission, claim finalization, and result persistence call | `apps/payments/provider_runtime.py`, especially `submit_or_poll()` and `finalize_claimed_result()` |
| Durable schema and database constraints | `apps/payments/govstack_models.py`, especially `PaymentAttempt`, `PaymentExecutionIntent`, `CallbackDelivery`, `PaymentReconciliation`, and `ProviderObservation` |
