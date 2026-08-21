# Item 02 — RB-02 Canonical Live Persistence Seams

**Date:** 2026-08-20  
**Scope:** Frozen mapping only. No RB-02 implementation is included.

> **Mapping conclusion: complete.** The seams below are the current live production paths reached by `process_bulk_payment_batch`; they are not test helpers or parallel replacement records.

## 1. Canonical provider observation and reconciliation binding

| Concern | Canonical live seam | Durable fields and uniqueness | Current role in batch path |
|---|---|---|---|
| Attempt identity | `PaymentLifecycleService.get_or_create_attempt()` in `apps/payments/govstack_failure_services.py`; live bulk call in `_record_bulk_instruction_lifecycle()` in `apps/payments/govstack_tasks.py` | `PaymentAttempt` has unique `(tenant_id, operation, request_id)` through `gs_attempt_scope_request_uniq`; bulk identity is `operation="g2p_bulk_instruction"`, `request_id="{batch.request_id}:{instruction.instruction_id}"`. | The attempt is created/replayed durably but is not currently referenced by `CreditInstruction`. |
| Provider evidence | `PaymentLifecycleService.record_provider_result()` in `govstack_failure_services.py`, called by `ProviderRuntime` in `apps/payments/provider_runtime.py` | `ProviderObservation.attempt` is the canonical FK. Fields: `tenant_id`, `observation_kind`, `observation_id`, `provider_transaction_id`, `event_id`, `amount`, `currency`, `outcome`, `verified`, `verification_method`, `binding_hash`, `accepted_finality`, `metadata`. Unique `(observation_kind, observation_id)` plus one accepted-finality observation per attempt. | Durable evidence is bound to an attempt, never directly to a batch/instruction. The live batch task currently does not read it. |
| Reconciliation | `PaymentLifecycleService.reconcile()` called from `record_provider_result()` | `PaymentReconciliation.attempt`, `provider_status`, `internal_status`, `source_bb_status`, `status`, `external_transaction_id`, `resolution_note`, `resolved_at`. | Durable history; no uniqueness constraint. The pure `govstack_reconciliation.py` helper is in-memory computation only, not a persistence seam. |

A verified, exact, accepted-finality `ProviderObservation` with outcome `settled` or `rejected` is the canonical finality authority. Missing, unverified, conflicting, malformed, retryable, uncertain, review, and unresolved evidence is non-final or review-only.

## 2. Canonical batch and instruction audit seams

| Record | Live call site | Durable identity and behavior |
|---|---|---|
| Instruction audit | `process_bulk_payment_batch()` creates `GovStackPaymentAuditEntry` after `_record_bulk_instruction_lifecycle()` for both local-validation branches in `apps/payments/govstack_tasks.py`. | `action`, `actor_bb_id`, `object_type="instruction"`, `object_pk=str(instruction.pk)`, `request_id=batch.request_id`, redacted `details`, timestamp. Append-only: updates/deletes are blocked. |
| Attempt lifecycle audit | `PaymentLifecycleService.audit()` in `govstack_failure_services.py`, called by attempt creation/outcome paths. | Same `GovStackPaymentAuditEntry` append-only model. |
| Batch audit | `process_bulk_payment_batch()` creates `GovStackPaymentAuditEntry` after it saves the batch aggregate status. | `object_type="batch"`, `object_pk=str(batch.pk)`, `request_id=batch.request_id`, legacy completed/failed/partial action and count details. |

The current audit seam is database-backed and append-only. It has no current batch-decision uniqueness key and is not yet fenced to a lease generation.

## 3. Canonical callback outbox and delivery seams

| Concern | Canonical live seam | Durable uniqueness and idempotency |
|---|---|---|
| Callback attempt | `get_or_create_attempt()` in `process_bulk_payment_batch()` after the batch transaction | `PaymentAttempt` identity: tenant `""`, operation `g2p_bulk_callback`, request ID `callback:{batch.request_id}:{batch.batch_id}`; the normal attempt uniqueness constraint prevents duplicate callback attempts for identical payload identity. |
| Callback outbox | `PaymentLifecycleService.queue_callback()` called from `process_bulk_payment_batch()` | `CallbackDelivery` fields: `attempt`, `callback_url`, `payload`, `payload_hash`, `status`, `delivery_count`, `next_attempt_at`, `last_http_status`, `last_error`. Database key: `(attempt, payload_hash)` via `gs_callback_attempt_payload_uniq`. |
| Transport result | `_post_callback()` followed by `PaymentLifecycleService.record_callback_result()` | Network POST is task-local; delivery count, HTTP/error, retry/backoff/dead-letter state and delivery audit are durable. |

The callback payload is presently non-PII: `{RequestID, BatchID, Status}`. The outbox ledger is durable and replayable, but callback creation is not yet coupled to a fenced batch decision.

## 4. Exact current live side-effect order

| Order | Current function and effect | Durable or task-local |
|---:|---|---|
| 1 | `process_bulk_payment_batch`: begin transaction, `select_for_update()` batch, reject non-`RECEIVED` status. | Database lock/state. |
| 2 | Task-local acquire/renew/takeover handling for `BatchLease`. | Durable row, but lifecycle is task-local logic. |
| 3 | One early lease owner/generation/expiry recheck. | Durable read only. |
| 4 | Lock pending `CreditInstruction`; perform active `GovStackBeneficiary` existence lookup. | Lock durable; boolean lookup is task-local until later write. |
| 5 | `_record_bulk_instruction_lifecycle()` creates/replays attempt and enqueues provider runtime or applies review/invalid-account lifecycle outcome. | Attempt/outcome/audit durable; enqueue is asynchronous task dispatch. |
| 6 | Create instruction `GovStackPaymentAuditEntry`; persist instruction local status/failure reason. | Durable. |
| 7 | Derive mapper-count batch status and persist batch status/amounts/result time. | Durable but not provider-finality-aware. |
| 8 | Create batch audit. | Durable append-only. |
| 9 | Exit transaction; create/replay callback attempt and `CallbackDelivery`. | Durable callback ledger. |
| 10 | Network POST callback; record durable result/retry/dead-letter state. | POST task-local; result durable. |

## Frozen RB-02 implementation targets

The implementation must change these existing live records and call sites—not introduce parallel decision/audit/callback substitutes:

1. bind `CreditInstruction` to the existing `PaymentAttempt` identity;
2. read `ProviderObservation.attempt` and `PaymentReconciliation.attempt` through the existing lifecycle services;
3. fence the existing `GovStackPaymentAuditEntry` and `CallbackDelivery` creation paths using the batch lease context;
4. replace the task-local lease block and mapper-count finalization in `process_bulk_payment_batch`;
5. preserve the existing callback attempt and `(attempt, payload_hash)` outbox uniqueness while coupling queueing to the new fenced durable decision.
