# Item 02 — RB-02 Complete Implementation Checklist

**Date:** 2026-08-20  
**Scope lock:** RB-02 only: the live `process_bulk_payment_batch` path. No prepayment, RB-03, RB-04, staging, official suite, credential, submission, or public HTTP contract change is permitted.

## Ordered implementation checklist

| Order | Files | Required complete change |
|---|---|---|
| 1 | New `apps/payments/govstack_batch_lease.py` | Add the sole database-backed lease lifecycle: atomic fresh acquire, busy result, heartbeat, expiry takeover, `assert_current_owner`, and release. Every operation must compare batch, owner token, and generation; takeover advances exactly one generation. Generation must remain durable across release/replay. |
| 2 | `govstack_models.py`, one additive migration | Add durable instruction-to-attempt binding and a unique/idempotent batch-decision record. The decision holds batch key, owner generation, policy-input fingerprint, child counts, threshold/reason, state, and timestamps. Keep historical rows readable; do not rewrite migration history. |
| 3 | `govstack_failure_services.py`, `govstack_reconciliation.py` | Add one authoritative child-outcome materializer. It must require the exactly bound attempt plus verified provider observation/reconciliation evidence. Mapper eligibility can only establish eligibility. Missing, malformed, unverified, conflicting, retryable, uncertain, review, and unresolved evidence produces non-final/review. |
| 4 | `govstack_batch_policy.py` | Keep `evaluate()` as the only decision authority. It must return deterministic final, pause, retry, review, and explicitly configured return-funds outcomes with stable data needed for persistence. |
| 5 | `govstack_tasks.py` | Replace task-local lease handling. Acquire/heartbeat/assert/release via the service. Fence every child, attempt/result, audit, decision, callback enqueue, terminal projection, and release write. Invoke the child materializer and `evaluate()`. Persist one decision; never terminalize while a child is non-final. |
| 6 | Existing batch audit/callback path | Atomically tie one non-PII callback outbox record and one logical audit decision to the fenced durable decision. Replay and stale workers must create neither a second decision/audit nor a second logical callback. |
| 7 | New `tests/test_item02_rb02_batch_lease_live.py`, affected task tests | Use `TransactionTestCase`, real database, controllable clock, and a barrier-controlled task/provider seam while invoking `process_bulk_payment_batch` itself. Adapt only mapper-as-settlement assumptions in directly affected legacy tests. |

## Mandatory live test methods

The new test module must include and pass the following scenarios:

| Test method | Required proof |
|---|---|
| `test_fresh_acquire_and_heartbeat_is_fenced` | One owner only; valid heartbeat changes expiry; wrong token/generation cannot change lease. |
| `test_expiry_takeover_advances_generation_once` | Expiry yields exactly one new owner at exactly one higher generation; old owner cannot heartbeat or release. |
| `test_stale_owner_cannot_write_any_side_effect` | After takeover, no old-owner child, attempt/result, audit, decision, callback, terminal, or release write exists. |
| `test_authoritative_mixed_child_finality_is_nonterminal` | Only exactly bound verified settled/rejected evidence is final; all specified non-final evidence stays non-final/review. |
| `test_threshold_pause_persists_one_idempotent_decision` | One durable pause, stable reason/counts/fingerprint, no terminal batch projection. |
| `test_retry_decision_is_durable_and_idempotent` | One retry decision and no duplicate execution side effect on replay. |
| `test_review_decision_is_durable_and_idempotent` | One non-terminal review decision on replay. |
| `test_configured_return_funds_is_explicit_and_idempotent` | Return funds only when explicitly configured; unresolved funds never infer it. |
| `test_empty_batch_has_one_durable_outcome` | One explicit empty decision, no fabricated settlement, no duplicate replay effect. |
| `test_already_terminal_children_are_aggregated_not_reprocessed` | Existing terminal children are aggregated but never rebound/reprocessed. |
| `test_duplicate_finalization_creates_one_decision_audit_and_callback` | Exactly one durable decision, logical audit, and callback outbox record. |
| `test_two_live_workers_cross_expiry_and_stale_finalization` | Worker A stalls, expires, B takes over/completes, A resumes with zero stale writes; B has sole decision/generation. |

## Mandatory rejection conditions

Reject and discard the implementation if it is helper-only, uses task-local/process-local concurrency, deletes or resets durable lease generation, permits a stale owner to write any side effect, treats mapper success as settlement, omits durable attempt binding or provider-finality materialization, omits live `evaluate()` use, terminalizes with non-final children, lacks durable unique decisions, infers return funds for unresolved money, omits any named live scenario, relies only on in-memory/eager behavior, alters excluded scope, or breaks existing public contracts.
