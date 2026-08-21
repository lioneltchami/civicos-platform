# Item 02 — Payments RB-02 Code Review: Live Batch Finality

**Date:** 2026-08-20  
**Scope:** RB-02 only. This review covers the live `process_bulk_payment_batch` worker, its batch lease, durable child outcome binding, policy decisions, audit/callback finalization, and live task tests.

## Verdict

> **Not ready to implement without the conditions below.** The present live task is an ID-Mapper eligibility workflow, not a lease-fenced provider-finality batch worker. It may write `COMPLETED`, `PARTIAL`, or `FAILED` before an authoritative provider outcome exists.

## Required file-level changes

| Priority | File(s) | Required change |
|---|---|---|
| P0 | New `apps/payments/govstack_batch_lease.py`; `govstack_models.py`; new migration | Introduce atomic lease acquire, heartbeat, expiry takeover, release, and `assert_current_owner` operations. Each operation must use owner token and generation compare-and-set conditions. Preserve one lease per batch and add safe indexes/metadata without rewriting prior migrations. |
| P0 | `govstack_tasks.py` | Replace task-local lease handling with the service. Guard every child mutation, attempt/result mutation, audit write, policy-decision persistence, callback enqueue, terminal projection, and release. A stale owner must make no further write. |
| P0 | `govstack_models.py`; new migration | Persist one durable instruction-to-attempt binding and one durable batch decision with unique decision/idempotency key, generation, policy input fingerprint, child status counts, threshold reason, and decision state. Historical rows must remain readable. |
| P0 | `govstack_failure_services.py`; `govstack_reconciliation.py`; `govstack_tasks.py` | Materialize each child state from the exactly bound attempt and verified provider observation/reconciliation data. Mapper success is only eligibility. Missing, unverified, conflicting, malformed, retryable, uncertain, review, and unresolved evidence remain non-final or explicit review. |
| P0 | `govstack_batch_policy.py`; `govstack_tasks.py` | Invoke `evaluate()` in the live task using stable authoritative child outcomes. Terminal batch projection is forbidden while any child is non-final. Persist deterministic pause, retry, review, and configured return-funds decisions; all commands must be idempotent. |
| P1 | `govstack_tasks.py`; callback/audit code | Tie callback outbox creation to the fenced durable batch decision. Replay or stale owner must not create another audit or callback. Preserve the existing non-PII payload discipline. |
| P1 | New `tests/test_item02_rb02_batch_lease_live.py`; existing task tests | Replace mapper-derived success assumptions only where necessary and add real task-invocation coverage. Helper-only policy tests are supplementary. |

## Mandatory test suite

The new live suite must use `TransactionTestCase`, real database state, an injected controllable clock, and a barrier-controlled provider/task seam. It must invoke `process_bulk_payment_batch` itself.

| Scenario | Required proof |
|---|---|
| Fresh acquire and heartbeat | One owner only; wrong token/generation heartbeat cannot change expiry. |
| Expiry takeover | A new owner gets exactly one higher generation after expiry; prior owner cannot heartbeat or release. |
| Stale worker fencing | After takeover, the former owner cannot mutate child state, create audits, persist decisions, enqueue callbacks, finalize the batch, or release the new lease. |
| Authoritative mixed child finality | Only exactly bound verified settled/rejected observations are final. Retryable, uncertain, review, unresolved, missing, malformed, conflicting, and unverified evidence remain non-final. |
| Threshold pause, retry, review, return funds | Each policy outcome is durable, idempotent, and non-terminal when required. Return funds is explicit/configured and never inferred for unresolved funds. |
| Empty and already-terminal children | Empty input has one explicit durable outcome. Existing terminal children are aggregated but never reprocessed or rebound. |
| Duplicate finalization and callback | Exactly one current-owner decision, audit, and logical callback outbox record; replay returns the existing decision. |
| Two competing live task invocations | Worker A stalls during provider/child work, expires, worker B takes over and completes, then A resumes. Database assertions prove B’s sole decision/generation and zero stale writes. |

## Constraints

No prepayment, request/response contract, staging, official-suite, credential, or submission change is permitted. The public harness contract must be preserved. The current review rejects any implementation that merely wraps the task in a helper, uses mapper lookup as settlement, or relies on in-memory/process-local concurrency evidence.
