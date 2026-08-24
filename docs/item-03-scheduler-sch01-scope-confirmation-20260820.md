# Item 03 — SCH-01 Scope Confirmation

**Date:** 2026-08-20  
**Stage:** Two independent scope confirmations

> **Verdict: Confirmed.** SCH-01 is strictly limited to authoritative, current-generation, durable schedule admission. It must not expand into recovery, adapters, status projection, fakes, the 37-operation harness, staging, official-suite work, submission, Payments, or another Building Block.

## Confirmed in-scope boundary

| SCH-01 concern | Required boundary |
|---|---|
| Authoritative admission | One service is the sole production authority. It locks the schedule, verifies the current generation, and emits a stable documented domain outcome. |
| Explicit outcome | The durable contract distinguishes at least `created`, `duplicate`, `stale_generation`, `zero_recipients`, and ineligible/blocked outcomes. A zero-recipient result is not inferred from legacy state. |
| Atomic materialization | Current-generation recipient and outbox rows are created/converged together under the schedule lock. Fan-out failure leaves no orphan recipient/outbox row or false compatibility success state. |
| Legacy compatibility | `dispatched` and `celery_task_id` may remain migration-compatible metadata, but neither may authorize or suppress admission. |
| Lifecycle fencing | Modify, cancel, delete, and re-arm use one locked generation-fence transition. Stale work cannot rematerialize after the transition. Deletion must enforce a named cascade-or-retention policy and cannot depend on broker revoke for correctness. |
| Transaction boundary | No broker, Celery publish, HTTP, adapter, recipient transport, or external I/O occurs before the admission transaction commits. A post-commit wake-up failure cannot undo committed durable admission. |
| Required evidence | The acceptance boundary is the thirteen exact methods in `SchedulerAdmissionTransactionTests`, implemented as real database `TransactionTestCase` tests. Existing partial `TestCase` coverage does not satisfy SCH-01. |

## Acceptance clarifications

The existing plan remains correct with two clarifications. First, implementation must assert stable **domain outcomes**, not merely row counts: stale, blocked, duplicate, created, and zero-recipient branches must be deterministic. Second, deletion must apply and test the selected durable cascade-or-retention contract so no work can later be recreated for the deleted schedule.

## Explicit exclusions

| Excluded from SCH-01 | Reason |
|---|---|
| Publisher/recipient lease recovery, reclaim, retries, backoff, dead-letter, replay, and crash-window behavior | SCH-02 |
| Adapter redesign and normalized cross-BB outcomes | Later Scheduler increments |
| Lifecycle status projection, metrics, history, and persisted authorization redesign | SCH-03 |
| Payments/Consent fake topology and external endpoints | Later local-only increment |
| Complete request-level 37-operation harness | Later Scheduler increment |
| Staging, official suite, credentials, deployment, certification, or submission | Explicitly prohibited |
| Item 02 or any other Building Block | Explicitly prohibited |

## Outcome

SCH-01 may proceed to a fresh exact file-level change-list stage. No implementation code has been changed by this scope-confirmation stage.
