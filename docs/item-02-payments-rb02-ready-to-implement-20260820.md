# Item 02 — RB-02 Ready-to-Implement Review

**Date:** 2026-08-20  
**Scope:** RB-02 only: the live bulk batch worker.

## Verdict

> **Blocked.** The seven-point checklist remains correct and complete, but a complete all-or-discard implementation cannot safely begin until the canonical live persistence seams are mapped precisely. The current review does not identify the exact existing provider-observation/reconciliation, audit, and callback-outbox insertion points/keys needed to guarantee that migrations and stale-write fencing target the live production rows rather than parallel replacement records.

This is a pre-implementation safety block, not a reduction in the required seven controls or named live tests.

## Exact ordered change list once the canonical seams are mapped

1. Add `apps/payments/govstack_batch_lease.py` with atomic acquire, busy, heartbeat, expiry takeover, owner assertion, and release; every operation compares batch, owner token, and generation and preserves monotonic generation across replay.
2. Extend `apps/payments/govstack_models.py` with durable instruction-to-attempt binding and a unique batch-decision record; create one new additive migration with safe historical-row compatibility and indexes.
3. Map child finality in `apps/payments/govstack_failure_services.py` and `apps/payments/govstack_reconciliation.py` from exactly bound attempts plus verified provider evidence, with explicit review/non-final results for every non-authoritative condition.
4. Retain `apps/payments/govstack_batch_policy.py:evaluate()` as the sole decision authority, with deterministic pause, retry, review, and explicitly configured return-funds outcomes.
5. Replace task-local logic in `apps/payments/govstack_tasks.py:process_bulk_payment_batch` with the fenced lease and authoritative finality sequence. Assert the same owner token/generation before every child, attempt/result, audit, decision, callback enqueue, terminal projection, heartbeat, and release effect.
6. Couple one logical audit and one non-PII callback outbox record to the unique fenced durable decision; replay and stale workers must produce no duplicate effect.
7. Add `apps/payments/tests/test_item02_rb02_batch_lease_live.py` using `TransactionTestCase`, a real database, controllable clock, and barrier-controlled provider/task seam. It must invoke `process_bulk_payment_batch` itself and include every named method in the committed implementation checklist.

## Blocking prerequisite mapping

Before Stage 3 may start, the implementation must freeze the exact model and call-site contracts for: the live `ProviderObservation`/reconciliation lookup and binding fields; the existing batch/instruction audit model and its insertion functions; the callback outbox/delivery model and its unique logical key; the direct `process_bulk_payment_batch` side-effect order; and the migration head/field metadata for the additive RB-02 migration. Without this mapping, an implementation could satisfy helper tests while writing to a parallel decision/audit/callback record, which violates the all-or-discard rule.

## Hard rejection conditions

The implementation remains rejected if it omits a fenced live side effect, uses mapper eligibility as settlement, creates a terminal batch with a non-final child, fails to persist idempotent policy decisions, omits any named live `TransactionTestCase`, relies on in-memory/eager evidence, changes excluded scope, or lacks the canonical live-seam mapping above.
