# Item 03 — SCH-01.2c Exact Delete-and-Re-arm Change List

**Date:** 2026-08-21  
**Stage:** Two fresh blind file-level reviews  
**Scope:** Delete and Re-arm with exactly two real-database tests.

## Ordered changes

| Order | File | Required change |
|---:|---|---|
| 1 | `apps/appointments/services/scheduler_runtime.py` | Add a locked Delete primitive that acquires the parent schedule, fences non-terminal recipients and unpublished outbox rows, clears claims, then calls existing hard delete/cascade. Parent deletion—not broker revoke—is the durable prevention of stale recreation. Add a distinct locked Re-arm primitive. When `delivery_admittable=False`, it fences old non-terminal work, advances generation once, sets admittable `True`, resets admission markers, and persists atomically. When already admittable, it is a no-op/duplicate. |
| 2 | `apps/appointments/services/govstack_alert_schedule.py` | Route `alert_schedule_delete` through the locked Delete primitive. Expose only an `alert_schedule_rearm` service boundary delegating to the distinct runtime primitive. Preserve broker revoke as best-effort cleanup only. |
| 3 | `apps/appointments/tests/test_scheduler_lifecycle.py` | Add exactly `test_delete_schedule_cannot_leave_recreatable_admission_work` and `test_rearm_advances_generation_and_admits_exactly_once` as `TransactionTestCase` methods. |

## Required validation

```bash
python manage.py test apps.appointments.tests.test_scheduler_lifecycle.SchedulerLifecycleTransactionTests.test_delete_schedule_cannot_leave_recreatable_admission_work --settings=config.settings.test --verbosity=2
python manage.py test apps.appointments.tests.test_scheduler_lifecycle.SchedulerLifecycleTransactionTests.test_rearm_advances_generation_and_admits_exactly_once --settings=config.settings.test --verbosity=2
```

The Delete test must prove parent absence/cascade prevents later recreation independent of broker-revoke outcome. The Re-arm test must prove one generation advance, old-work fencing, restored admittability, reset markers, one successful new-generation admission, duplicate repeated admission, and no-op repeated Re-arm.

## Strict exclusions

No model or migration change. No task integration, recovery, adapters, projections, fakes, harnesses, staging, official-suite work, submission, Item 02, or later work.
