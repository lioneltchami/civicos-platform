# Item 03 — SCH-01.2a Exact Cancel-Only Change List

**Date:** 2026-08-21  
**Stage:** Two fresh blind file-level reviews  
**Scope:** Cancellation fence and one real-database test only.

## Ordered changes

| Order | File | Required change |
|---:|---|---|
| 1 | `apps/appointments/models.py` | Add `GovStackAlertSchedule.delivery_admittable = True` and nullable `SchedulerOutbox.cancelled_at`. Do not alter generation, SCH-01.1 markers, recipient-state definitions, or unrelated fields. |
| 2 | `apps/appointments/migrations/0022_sch01_2a_cancel_only.py` | Add one forward-only migration after `0021_sch01_1_core_admission` for exactly those two fields. Existing schedules remain admissible; no backfill or unrelated schema. |
| 3 | `apps/appointments/services/scheduler_runtime.py` | Add one locked cancellation operation. It locks the schedule before children; sets `delivery_admittable=False`; resets `admitted_generation` and `admission_outcome`; cancels every non-terminal recipient and clears its lease fields; stamps every associated unpublished outbox with `cancelled_at` and clears publisher claim fields. Published outbox rows and terminal recipients remain unchanged. |
| 4 | `apps/appointments/services/scheduler_runtime.py` | Add a narrow admission guard after the schedule lock and before recipient canonicalization or `_materialize_locked`. A non-admittable schedule returns `{outcome: "not_admittable", generation: current, created: 0, deliveries: []}` without mutating SCH-01.1 markers or creating recipient/outbox rows. Exclude cancelled unpublished outbox rows from claim, due, success, and failure paths. |
| 5 | `apps/appointments/services/govstack_alert_schedule.py` | Add only a public cancellation entry point if required, delegating directly to the runtime locked operation. Do not add modify, delete, re-arm, task, or orchestration behavior. |
| 6 | `apps/appointments/tests/test_scheduler_lifecycle.py` | Add exactly one `TransactionTestCase` method: `test_cancel_schedule_fences_non_terminal_work_and_blocks_admission`. Use real database rows and services; assert recipient cancellation/lease clearing, unpublished-outbox cancellation/publisher-claim clearing, terminal/published controls unchanged, reset markers, non-claimability/non-due state, and rowless `not_admittable` admission. |

## Focused validation

```bash
python manage.py makemigrations appointments --check --dry-run --settings=config.settings.test
python manage.py test apps.appointments.tests.test_scheduler_lifecycle.SchedulerLifecycleTransactionTests.test_cancel_schedule_fences_non_terminal_work_and_blocks_admission --settings=config.settings.test --verbosity=2
```

## Strict exclusions

No modify, delete, re-arm, task changes, recovery, adapters, projections, fakes, harnesses, staging, official-suite work, submission, Item 02, or later Scheduler work.
