# Item 02 — RB-02 Canonical Live Persistence Seams

**Date:** 2026-08-20  
**Status:** Amended after initial independent verification; pending fresh re-verification  
**Scope:** Frozen persistence mapping only for the live `process_bulk_payment_batch()` worker and the provider-runtime/lifecycle chain it directly invokes. No RB-02 implementation is included.

> **Candidate mapping conclusion:** The live path includes both direct `process_bulk_payment_batch()` writes and the asynchronous provider-execution admission and finalization path. The seams below are current production records and call sites, not test helpers or parallel replacement records. Two fresh reviews must confirm the amended mapping before it may be treated as frozen and safe for RB-02 implementation.

## Scope boundary: separate live command-route records

`PaymentCommand`, `PaymentCommandOutbox`, and `PaymentCommandService.admit()` are live persistence records/call sites for mounted command-route admission in `apps/payments/govstack_views.py` and `apps/payments/payment_command_boundary.py`. They are intentionally **out of scope** for this RB-02 worker mapping: `apps/payments/govstack_tasks.py` has no `PaymentCommand`, `PaymentCommandService`, or `PaymentCommandOutbox` reference. The live bulk worker creates/replays `PaymentAttempt` directly in `_record_bulk_instruction_lifecycle()` and hands it to `enqueue_attempt()`.

This exclusion does not treat the command-route records as tests or in-memory helpers. It establishes only that they are a separate mounted-route admission/outbox path, not a persistence seam the current bulk worker calls or that a lease-fenced bulk-finalization change may substitute for its own direct attempt/provider-runtime chain.

## 1. Canonical production chain

| Sequence | Production function / model | Durable purpose | Current RB-02 relevance |
|---:|---|---|---|
| 1 | `process_bulk_payment_batch()` in `apps/payments/govstack_tasks.py` | Locks and locally validates the batch/instructions, creates/replays an instruction attempt, writes local audit/status, and currently derives mapper-count batch completion. | It does not bind `CreditInstruction` to its attempt or aggregate attempt-bound provider finality. |
| 2 | `_record_bulk_instruction_lifecycle()` in `govstack_tasks.py` | Calls `PaymentLifecycleService.get_or_create_attempt()` with `operation="g2p_bulk_instruction"` and `request_id="{batch.request_id}:{instruction.instruction_id}"`. | Attempt creation/enqueue is not provider finality. |
| 3 | `enqueue_attempt()` in `apps/payments/provider_runtime.py` | Registers orchestration through `transaction.on_commit()`. | Asynchronous dispatch is task-local/process work, not a provider-evidence write. |
| 4 | `ProviderRuntime.submit_or_poll()` in `provider_runtime.py` | Locks/creates `PaymentExecutionIntent`, records submit admission where applicable, claims the attempt, performs submit/poll, and calls fenced finalization. | This is the canonical durable provider-execution admission seam. |
| 5 | `ProviderRuntime.finalize_claimed_result()` in `provider_runtime.py` | Re-locks the attempt, rejects stale token/generation, calls `PaymentLifecycleService.record_provider_result()`, records recovery evidence, and clears the active claim. | This is the canonical fenced result-persistence boundary. |
| 6 | `record_provider_result()` / `record_observation()` / `reconcile()` in `govstack_failure_services.py` | Persists observation evidence, applies only exact verified finality, and appends reconciliation history. | The live batch task currently does not consume this evidence before finalization. |

## 2. Canonical provider observation, reconciliation, and execution-admission binding

| Concern | Canonical live seam | Durable fields and uniqueness | Current role in batch path |
|---|---|---|---|
| Batch-worker lease | `BatchLease` in `apps/payments/govstack_models.py`, acquired/renewed/taken over and rechecked directly by `process_bulk_payment_batch()` in `apps/payments/govstack_tasks.py`. | One-to-one `batch`; fields `owner_token`, `generation`, `expires_at`. The one-to-one batch identity makes the row the current durable lease/concurrency record. | The current worker uses it directly, but its control flow is task-local and does not yet provide the required reusable owner-token/generation fencing service. |
| Attempt identity | `PaymentLifecycleService.get_or_create_attempt()` in `apps/payments/govstack_failure_services.py`; live bulk call in `_record_bulk_instruction_lifecycle()` in `apps/payments/govstack_tasks.py` | `PaymentAttempt` has unique `(tenant_id, operation, request_id)` through `gs_attempt_scope_request_uniq`; bulk identity is `operation="g2p_bulk_instruction"`, `request_id="{batch.request_id}:{instruction.instruction_id}"`. Durable attempt claim-state writes include `claim_token`, `claim_generation`, `claim_expires_at`, `claim_heartbeat_at`, `submission_intent`, and `recovery_evidence`. | The attempt is created/replayed durably but is not currently referenced by `CreditInstruction`. |
| Provider registration resolution | `ProviderRuntime.resolve()` calls `resolve_provider()` in `apps/payments/provider_registry.py`, which reads active `ProviderRegistration` records for the attempt's tenant/operation scope. | `ProviderRegistration` fields include `tenant_id`, `operation`, `provider_name`, `factory_key`, `configuration_version`, `configuration`, and active status; `PaymentAttempt.provider_registration` is the durable protected FK to the selected registration when bound. | Durable adapter-configuration/read seam required before normal provider I/O. It is configuration, not provider financial evidence, and blank-tenant bulk attempts can return without resolving a provider. |
| Provider execution admission | `PaymentExecutionIntent` in `apps/payments/govstack_models.py`, created/locked by `ProviderRuntime.submit_or_poll()`; the associated attempt result is finalized through `ProviderRuntime.finalize_claimed_result()` in `apps/payments/provider_runtime.py`. | One-to-one `attempt`; fields `scope`, `operation`, `request_identity`, `payload_fingerprint`, `provider_correlation`, `submit_started_at`, `submit_admission_generation`; unique `(scope, operation, request_identity)`. | Durable submit/poll admission and provider correlation. A committed submit admission is fail-closed evidence that external submission may have occurred, so takeover must poll rather than resubmit. It is neither finality evidence nor an instruction binding. |
| Provider evidence | `PaymentLifecycleService.record_provider_result()` in `govstack_failure_services.py`, called by `ProviderRuntime` in `apps/payments/provider_runtime.py` | `ProviderObservation.attempt` is the canonical FK. Fields: `tenant_id`, `observation_kind`, `observation_id`, `provider_transaction_id`, `event_id`, `amount`, `currency`, `outcome`, `verified`, `verification_method`, `binding_hash`, `accepted_finality`, `metadata`. Unique `(observation_kind, observation_id)` plus one accepted-finality observation per attempt. | Durable evidence is bound to an attempt, never directly to a batch/instruction. The live batch task currently does not read it. |
| Reconciliation | `PaymentLifecycleService.reconcile()` called from `record_provider_result()` | `PaymentReconciliation.attempt`, `provider_status`, `internal_status`, `source_bb_status`, `status`, `external_transaction_id`, `resolution_note`, `resolved_at`. | Durable history; no uniqueness constraint. The pure `govstack_reconciliation.py` helper is in-memory computation only, not a persistence seam. |

A verified, exact, accepted-finality `ProviderObservation` with outcome `settled` or `rejected` is the canonical finality authority. Missing, unverified, conflicting, malformed, retryable, uncertain, review, and unresolved evidence is non-final or review-only. The pure `apps/payments/govstack_reconciliation.py` helper is in-memory comparison logic, not a persistence seam. Provider runtime dispatch may not produce provider evidence for every bulk attempt; enqueue and local lifecycle state must not be treated as financial finality.

## 3. Canonical batch and instruction audit seams

| Record | Live call site | Durable identity and behavior |
|---|---|---|
| Instruction audit | `process_bulk_payment_batch()` creates `GovStackPaymentAuditEntry` after `_record_bulk_instruction_lifecycle()` for both local-validation branches in `apps/payments/govstack_tasks.py`. | `action`, `actor_bb_id`, `object_type="instruction"`, `object_pk=str(instruction.pk)`, `request_id=batch.request_id`, redacted `details`, timestamp. Append-only: updates/deletes are blocked. |
| Attempt lifecycle audit | `PaymentLifecycleService.audit()` in `govstack_failure_services.py`, called by attempt creation/outcome paths. | Same `GovStackPaymentAuditEntry` append-only model. |
| Batch audit | `process_bulk_payment_batch()` creates `GovStackPaymentAuditEntry` after it saves the batch aggregate status. | `object_type="batch"`, `object_pk=str(batch.pk)`, `request_id=batch.request_id`, legacy completed/failed/partial action and count details. |

The current audit seam is database-backed and append-only. It has no current batch-decision uniqueness key and is not yet fenced to a lease generation. `request_id` is stored through the audit model's field-width/truncation behavior, so future decision identity must not infer a wider durable uniqueness key from audit correlation alone.

## 4. Canonical callback outbox and delivery seams

| Concern | Canonical live seam | Durable uniqueness and idempotency |
|---|---|---|
| Callback attempt | `get_or_create_attempt()` in `process_bulk_payment_batch()` after the batch transaction | `PaymentAttempt` identity: tenant `""`, operation `g2p_bulk_callback`, request ID `callback:{batch.request_id}:{batch.batch_id}`. Its `(tenant_id, operation, request_id)` key deduplicates callback attempt identity; changed payload for that identity is an idempotency conflict. |
| Callback outbox | `PaymentLifecycleService.queue_callback()` called from `process_bulk_payment_batch()` | `CallbackDelivery` fields: `attempt`, `callback_url`, `payload`, `payload_hash`, `status`, `delivery_count`, `next_attempt_at`, `last_http_status`, `last_error`. Database key: `(attempt, payload_hash)` via `gs_callback_attempt_payload_uniq`. |
| Transport result | `_post_callback()` followed by `PaymentLifecycleService.record_callback_result()` | Network POST is task-local; delivery count, HTTP/error, retry/backoff/dead-letter state and delivery audit are durable. |

The callback payload is presently non-PII: `{RequestID, BatchID, Status}`. The outbox ledger is durable and replayable, but callback creation is not yet coupled to a fenced batch decision.

## 5. Exact current live side-effect order

| Order | Current function and effect | Durable or task-local |
|---:|---|---|
| 1 | `process_bulk_payment_batch`: begin transaction, `select_for_update()` batch, reject non-`RECEIVED` status. | Database lock/state. |
| 2 | Task-local acquire/renew/takeover handling for `BatchLease`. | Durable row, but lifecycle is task-local logic. |
| 3 | One early lease owner/generation/expiry recheck. | Durable read only. |
| 4 | Lock pending `CreditInstruction`; perform active `GovStackBeneficiary` existence lookup. | Lock durable; boolean lookup is task-local until later write. |
| 5 | `_record_bulk_instruction_lifecycle()` creates/replays attempt and records local review/invalid-account lifecycle outcome or registers provider orchestration after commit. The normal runtime resolves active `ProviderRegistration` before provider I/O, then flows through `PaymentExecutionIntent` admission. | Attempt/outcome/audit and configuration records are durable; provider dispatch is asynchronous. Provider resolution/admission must not be treated as financial evidence. |
| 6 | Create instruction `GovStackPaymentAuditEntry`; persist instruction local status/failure reason. | Durable. |
| 7 | Derive mapper-count batch status and persist batch status/amounts/result time. | Durable but not provider-finality-aware. |
| 8 | Create batch audit. | Durable append-only. |
| 9 | Exit transaction; create/replay callback attempt and `CallbackDelivery`. | Durable callback ledger. |
| 10 | Network POST callback; record durable result/retry/dead-letter state. | POST task-local; result durable. |

## 6. Frozen RB-02 implementation targets

The implementation must change these existing live records and call sites—not introduce parallel decision/audit/callback/execution-admission substitutes:

1. bind `CreditInstruction` to the existing `PaymentAttempt` identity;
2. read `ProviderObservation.attempt` and `PaymentReconciliation.attempt` through the existing lifecycle services;
3. preserve and correctly use `PaymentExecutionIntent` submit-admission and claim-finalization semantics before treating attempt-bound provider evidence as available;
4. fence the existing `GovStackPaymentAuditEntry` and `CallbackDelivery` creation paths using the batch lease context;
5. replace the task-local lease block and mapper-count finalization in `process_bulk_payment_batch`;
6. preserve the distinct callback-attempt `(tenant_id, operation, request_id)` and delivery `(attempt, payload_hash)` idempotency keys while coupling queueing to the new fenced durable decision.
