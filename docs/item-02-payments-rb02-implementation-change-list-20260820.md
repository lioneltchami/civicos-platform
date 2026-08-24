# Item 02 — RB-02 Final Implementation Change List

**Date:** 2026-08-20  
**Stage:** Fresh blind Stage 2 review  
**Status:** Complete change list; no implementation is included in this document.

> **Conclusion:** Two fresh blind reviews found the frozen seam map and existing checklist sufficient. The following ordered changes are required together; omitting any item fails the all-or-discard rule.

## Ordered file-level changes

| Order | File | Required complete change |
|---:|---|---|
| 1 | `apps/payments/govstack_batch_lease.py` (new) | Add the sole reusable database-backed batch lease service: atomic acquisition, busy-owner result, owner-token/generation-fenced heartbeat, expiry takeover, `assert_current_owner`, and release. Preserve monotonically advancing generation; never reset/delete it. Every operation must compare batch identity, token, and generation and expose a fencing context. |
| 2 | `apps/payments/govstack_models.py` | Add an additive nullable `CreditInstruction` → existing `PaymentAttempt` binding. Add the uniquely keyed durable batch-decision model with batch identity, decision identity/fingerprint, lease generation, decision state/reason/counts, timestamps, and idempotency constraints. Preserve existing attempt, execution-intent, observation, reconciliation, audit, and callback identities. |
| 3 | `apps/payments/migrations/0044_item02_rb02_batch_decision_and_attempt_binding.py` | Add only the instruction attempt binding, durable decision table, constraints, and supporting indexes. Depend on `0043_executionintent_submit_admission`; preserve historical rows and do not alter migration history or excluded command-route records. |
| 4 | `apps/payments/govstack_failure_services.py` | Add the authoritative bound-child finality materialization/query path. Read only the instruction-bound attempt and its durable `ProviderObservation` / `PaymentReconciliation` evidence. Finality requires exact verified accepted `settled`/`rejected` evidence; all unbound, malformed, uncertain, retryable, review, unresolved, or conflicting evidence is non-final/review. |
| 5 | `apps/payments/govstack_reconciliation.py` | Change only shared pure result-shaping/aggregation support if needed. It must remain computation only and must never replace the durable attempt-bound evidence seam or treat mapper eligibility, enqueue, or admission as settlement. |
| 6 | `apps/payments/govstack_batch_policy.py` | Keep `evaluate()` as the sole live decision authority. Its deterministic output must support authoritative finality, threshold pause, retry, review, explicit configured return-funds, and empty-batch outcomes. Non-final children must block terminalization; unresolved funds must never infer return-funds. |
| 7 | `apps/payments/govstack_tasks.py` | Replace task-local lease handling and mapper-count finalization in `process_bulk_payment_batch()` with the lease service, durable instruction-attempt binding, authoritative finality materialization, and live policy evaluation. Fence every child mutation, finality read/commit, audit, decision, callback, batch projection, heartbeat, and release. Existing terminal children must aggregate without reprocessing; empty batches require one durable outcome. |
| 8 | `apps/payments/govstack_tasks.py` and `apps/payments/govstack_failure_services.py` | Couple exactly one append-only logical batch-decision audit to the unique fenced decision using the existing `GovStackPaymentAuditEntry`; do not use an audit row as the decision record or infer a wider key from `request_id`. |
| 9 | `apps/payments/govstack_tasks.py` and `apps/payments/govstack_failure_services.py` | Couple exactly one callback attempt/outbox record to the fenced decision using the existing callback-attempt identity and `CallbackDelivery` `(attempt, payload_hash)` uniqueness. Preserve payload, transport, retry, and dead-letter contracts; do not add a new outbox. |
| 10 | `apps/payments/provider_runtime.py` and `apps/payments/provider_runtime_tasks.py` only if necessary | Preserve the existing post-commit runtime chain, execution-intent admission, submit/poll handling, and claim-fenced `finalize_claimed_result()` behavior. Do not create a replacement execution/finalization path. |
| 11 | `apps/payments/tests/test_item02_rb02_batch_lease_live.py` (new) | Add a real-database `TransactionTestCase` module that invokes `process_bulk_payment_batch()` through controllable time/barrier seams. It must contain all twelve mandatory method names and prove durable effects, not eager/in-memory behavior. |
| 12 | Directly affected existing payment tests only | Update legacy assertions that encode mapper eligibility as settlement or mapper-count finality, while retaining or strengthening provider-runtime, public task, audit, callback, and recovery coverage. No staging, official suite, command-route, RB-03, or RB-04 changes are allowed. |

## Mandatory live test inventory

The new live module must define and pass all of the following methods:

1. `test_fresh_acquire_and_heartbeat_is_fenced`
2. `test_expiry_takeover_advances_generation_once`
3. `test_stale_owner_cannot_write_any_side_effect`
4. `test_authoritative_mixed_child_finality_is_nonterminal`
5. `test_threshold_pause_persists_one_idempotent_decision`
6. `test_retry_decision_is_durable_and_idempotent`
7. `test_review_decision_is_durable_and_idempotent`
8. `test_configured_return_funds_is_explicit_and_idempotent`
9. `test_empty_batch_has_one_durable_outcome`
10. `test_already_terminal_children_are_aggregated_not_reprocessed`
11. `test_duplicate_finalization_creates_one_decision_audit_and_callback`
12. `test_two_live_workers_cross_expiry_and_stale_finalization`

## Rejection conditions

The implementation must be discarded if it introduces task-local concurrency as the control, parallel persistence records, mapper-derived settlement, stale-owner side effects, non-final terminalization, non-idempotent decision/audit/callback effects, inferred return-funds, omitted named tests, broken external contracts, or any excluded-scope change.
