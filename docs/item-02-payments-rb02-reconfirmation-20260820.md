# Item 02 — RB-02 Plan Reconfirmation

**Date:** 2026-08-20  
**Scope:** RB-02 only: the live bulk batch worker.

Two independent shared-context analyses re-read the committed RB-02 plan and code review, then traced the current `process_bulk_payment_batch` implementation. Both concluded that the plan remains accurate and that **all seven hard acceptance points remain open**.

| Acceptance point | Current conclusion |
|---|---|
| Fenced lease lifecycle | `BatchLease` is still a persistence primitive; the task has no reusable atomic acquire, heartbeat, expiry takeover, or generation-fenced release service. |
| Fencing every side effect | Ownership is checked only early in the task. Child, attempt/result, audit, decision, callback, terminal, and release effects are not all fenced. |
| Instruction-to-attempt binding | `CreditInstruction` has no durable attempt reference; mapper eligibility remains separate from financial finality. |
| Authoritative child finality | Verified, exactly bound provider observations and explicit review are not the live completion authority. |
| Live policy invocation | `govstack_batch_policy.evaluate()` remains unused by the task. |
| Durable idempotent decisions | No unique batch-decision record persists pause, retry, review, return-funds, empty-batch, or duplicate-finalization decisions. |
| Competing-worker proof | No `TransactionTestCase` invokes two real bulk-task executions across expiry takeover and stale finalization. |

No new scope appeared. The existing plan’s file constraints remain mandatory: a new lease service, additive model/migration changes for instruction binding and durable batch decisions, live task integration, and `apps/payments/tests/test_item02_rb02_batch_lease_live.py`. Prepayment, RB-03, RB-04, staging, official-suite, submission, and public contract changes remain outside this scope.
