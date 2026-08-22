# Item 03 — SCH-01.3 Scope Confirmation: Task Integration and Commit-Ordered I/O

**Date:** 2026-08-21  
**Status:** Confirmed by two independent reviews  
**Scope:** SCH-01.3 only

> SCH-01.3 integrates the live `dispatch_alert_schedule` task with the already-authoritative, locked `admit_schedule_generation(...)` path. It does not redefine lifecycle, recovery, transport, adapters, projections, fakes, harnesses, staging, official-suite work, or submission.

## Confirmed authority boundary

| Concern | Required rule |
|---|---|
| Admission authority | Only the locked current `delivery_generation`, `delivery_admittable`, and durable admission markers determine admission. |
| Legacy fields | `dispatched` and `celery_task_id` remain compatibility-only. They must neither authorize nor suppress admission. |
| Before commit | The task may perform only database-only authoritative admission and durable recipient/outbox materialization. No broker, Celery publish, HTTP, adapter, transport, or external I/O may occur. |
| After commit | A wake-up/enqueue may occur only through a post-commit callback. A wake-up failure must not erase the committed durable admission. |
| Rollback | A failed admission transaction must leave no durable recipient/outbox/marker state and no publish, broker, scheduling, HTTP, adapter, or transport side effect. |

## Files and test boundary

| File | SCH-01.3 role |
|---|---|
| `apps/appointments/tasks.py` | Route live dispatch through authoritative admission and defer wake-up until after commit. |
| `apps/appointments/services/scheduler_runtime.py` | Preserve the authoritative transaction contract; alter only if necessary for a post-commit-safe wake-up seam. |
| `apps/appointments/tests/test_scheduler_admission.py` | Add or extend real-database task/transaction proofs. |
| Existing Scheduler admission, lifecycle, runtime, and alert-schedule test modules | Run as regression evidence for SCH-01.1/SCH-01.2. |

## Required proofs

1. `test_legacy_dispatched_and_celery_state_never_authorize_admission` proves that compatibility fields cannot suppress or authorize the live task admission.
2. `test_no_transport_io_before_admission_commit` proves no broker, Celery, HTTP, adapter, or transport I/O before commit.
3. `test_rolled_back_admission_does_not_publish_or_schedule_transport` proves rollback leaves neither durable admission nor transport side effects.
4. A focused post-commit wake-up failure proof confirms durable admission survives a failed wake-up.
5. The complete SCH-01.1 and SCH-01.2 regression surface must pass.

## Explicit exclusions

No model or migration change is justified. Recovery, adapters, projections, fakes, topology/harness work, later Scheduler work, staging, the official suite, certification, testing-site work, and submission remain out of scope.
