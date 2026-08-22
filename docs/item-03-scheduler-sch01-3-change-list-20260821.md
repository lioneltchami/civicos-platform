# Item 03 — SCH-01.3 Exact Change List: Authoritative Task Integration

**Date:** 2026-08-21  
**Status:** Reconciled from two fresh blind reviews  
**Scope:** SCH-01.3 only

## Ordered implementation boundary

| Order | File | Required SCH-01.3 change |
|---:|---|---|
| 1 | `apps/appointments/tasks.py` | Refactor live `dispatch_alert_schedule` so it obtains the current schedule generation and invokes `scheduler_runtime.admit_schedule_generation(...)` as the only admission authority. Do not branch on `dispatched` or `celery_task_id` to authorize, suppress, or identify admission. Keep pre-commit work database-only; register the optional wake-up only with `transaction.on_commit(...)`. Isolate wake-up failure so it cannot undo a committed durable admission. |
| 2 | `apps/appointments/services/scheduler_runtime.py` | Preserve the locked, generation-aware, database-only `admit_schedule_generation(...)` contract. Change it only if a narrow seam is required to expose an outcome or post-commit-safe wake-up handoff; do not add broker, Celery, HTTP, adapter, or transport I/O. |
| 3 | `apps/appointments/tests/test_scheduler_admission.py` | Add real-database `TransactionTestCase` coverage for the required legacy-state, no-pre-commit-I/O, rollback-with-no-transport, and post-commit-wake-up-failure proofs. |
| 4 | Existing Scheduler test modules | Run `test_scheduler_runtime.py`, `test_scheduler_lifecycle.py`, and `test_govstack_alert_schedule.py` as regression coverage. Amend them only if an existing expectation incorrectly treats a legacy field or pre-commit publication as authority. |

## Mandatory acceptance tests

1. `test_legacy_dispatched_and_celery_state_never_authorize_admission` must prove combinations of `dispatched` and `celery_task_id` neither suppress a valid current-generation admission nor authorize a stale/non-admittable schedule.
2. `test_no_transport_io_before_admission_commit` must spy on broker/Celery, HTTP, adapter, and transport seams. They must remain untouched while the authoritative admission transaction is open; only the registered post-commit wake-up may run after commit.
3. `test_rolled_back_admission_does_not_publish_or_schedule_transport` must inject an admission/materialization failure and prove no recipient rows, outbox rows, admission marker, legacy success state, broker, Celery, HTTP, adapter, or transport effect survives.
4. A focused post-commit wake-up-failure test must prove that recipient/outbox rows and authoritative admission markers remain durable after the wake-up raises.
5. Full SCH-01.1/SCH-01.2 regression must run, including current-generation, duplicate, stale, zero-recipient, rollback, Cancel, Modify, Delete, and Re-arm coverage.

## Transaction ordering

> **Before commit:** locked authoritative admission and durable database materialization only.  
> **At commit:** admission becomes durable.  
> **After commit:** optional wake-up/enqueue only. A wake-up failure is observable operationally but cannot erase or reclassify the committed admission.

## Explicit exclusions

No model or migration change is authorized. Recovery, adapters, projections, fakes, topology/harness work, later Scheduler work, staging, official-suite work, testing-site activity, certification, and submission remain out of scope.
