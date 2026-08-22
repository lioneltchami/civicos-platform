# Item 03 — SCH-01.1 Retry Exact Change List

**Date:** 2026-08-21  
**Stage:** Fresh call-graph reconciliation reviews  
**Scope:** Core admission plus elimination of the callable materialization bypass only.

> The current task calls `scheduler_runtime.materialize(...)` directly. Because task-code changes are forbidden, `materialize(...)` must remain only as a compatibility wrapper that delegates exclusively to the locked authority. It must no longer create durable rows itself.

## Complete materialization call graph

```text
Unchanged dispatch_alert_schedule task
  → scheduler_runtime.materialize(...) compatibility wrapper
    → admit_schedule_generation(...) locked authority
      → _materialize_locked(...) private row-convergence helper
        → SchedulerRecipientDelivery + SchedulerOutbox
```

No other callable edge may create recipient or outbox rows.

## Ordered changes

| Order | File | Required change |
|---:|---|---|
| 1 | `apps/appointments/models.py` | Add only nullable `admitted_generation` and bounded `admission_outcome` on `GovStackAlertSchedule`. `delivery_generation` remains the sole generation authority. |
| 2 | `apps/appointments/migrations/0021_sch01_1_core_admission.py` | Add one forward-only migration after `0020_scheduler_outbox_publisher_claim` for exactly those two fields. No backfill, indexes, constraints, lifecycle state, recovery state, adapter state, or data migration. |
| 3 | `apps/appointments/services/scheduler_runtime.py` | Move the existing raw recipient/outbox persistence body to a private `_materialize_locked(...)` helper. It accepts only the schedule already locked and generation already validated by the authority. It has no independent `atomic()` block, cannot lock or derive authority, and is not an admission API. |
| 4 | `apps/appointments/services/scheduler_runtime.py` | Add public `admit_schedule_generation(...)` as the sole authority. In one transaction it locks the schedule with `select_for_update()`, validates `expected_generation == delivery_generation` before materialization, canonicalizes recipients, invokes the private helper, writes the schedule marker/outcome, and returns deterministic `created`, `duplicate`, `stale_generation`, or `zero_recipients` results. Exceptions propagate and roll back all admission writes. No I/O, broker, HTTP, publisher, lifecycle, recovery, adapter, or projection behavior is added. |
| 5 | `apps/appointments/services/scheduler_runtime.py` | Retain public `materialize(...)` only as a legacy-shape compatibility wrapper for the existing unchanged task. It must make **no** direct database row-creation call and open no admission transaction of its own. It must translate the existing single-recipient arguments into `admit_schedule_generation(...)`, use `generation` only as expected generation, and return the existing `(delivery, created)` shape after the authority succeeds. A stale outcome must not yield a delivery row. |
| 6 | `apps/appointments/tests/test_scheduler_runtime.py` | Replace direct raw-materializer fixture setup with the authority or compatibility wrapper, so no test itself demonstrates a bypass. Do not modify task code or add broader lifecycle coverage. |
| 7 | `apps/appointments/tests/test_scheduler_admission.py` | Add exactly six real-database `TransactionTestCase` methods for current generation, duplicate, concurrent, stale generation, rollback, and zero-recipient admission. All must call the locked authority and assert marker, outcome, recipient, and outbox state. |

## Required tests

1. `test_current_generation_materializes_one_recipient_and_one_outbox_per_recipient`
2. `test_duplicate_admission_converges_without_duplicate_rows`
3. `test_concurrent_current_generation_admission_is_single_winner`
4. `test_stale_generation_cannot_materialize_current_work`
5. `test_materialization_rollback_leaves_no_recipient_or_outbox_or_dispatched_claim`
6. `test_zero_recipient_admission_is_deterministic_and_rowless`

## Mandatory closure checks

```bash
grep -RIn --exclude-dir='__pycache__' 'def materialize' apps/appointments
grep -RIn --exclude-dir='__pycache__' 'SchedulerRecipientDelivery.objects.get_or_create\|SchedulerOutbox.objects.create' apps/appointments
python manage.py makemigrations appointments --check --dry-run --settings=config.settings.test
python manage.py test apps.appointments.tests.test_scheduler_admission.SchedulerAdmissionTransactionTests --settings=config.settings.test --verbosity=2
```

The first search may return only the compatibility wrapper. Raw recipient/outbox creation must appear only in the private helper. The six tests must pass against a real database. The wrapper is compliant only if its only row-creation path is through `admit_schedule_generation(...)`.

## Explicit exclusions

`apps/appointments/tasks.py` remains unchanged. Modify/cancel/delete/re-arm, task integration, recovery, adapters, projections, fakes, the 37-operation harness, staging, official suite, submission, Item 02, other Building Blocks, transport-I/O tests, and legacy-field behavior tests remain excluded.
