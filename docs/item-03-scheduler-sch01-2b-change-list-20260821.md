# Item 03 — SCH-01.2b Exact Modify-Only Change List

**Date:** 2026-08-21  
**Stage:** Two fresh blind file-level reviews  
**Scope:** Delivery-affecting Modify and one real-database test only.

## Ordered changes

| Order | File | Required change |
|---:|---|---|
| 1 | `apps/appointments/services/scheduler_runtime.py` | Add one locked Modify-specific child-fencing primitive. It cancels old non-terminal recipients, clears recipient leases, and fences old unpublished outbox rows with cancellation metadata and cleared publisher claims. It must not set `delivery_admittable=False`, must not change terminal/published controls, and must preserve SCH-01.1 admission authority. |
| 2 | `apps/appointments/services/govstack_alert_schedule.py` | For genuine changes to `event_id`, `target_category`, `message_id`, or `alert_datetime`, invoke the Modify fencing primitive once, advance `delivery_generation` exactly once, reset `admitted_generation` and `admission_outcome`, and retain `delivery_admittable=True`. No-op and metadata-only calls must make no generation, marker, or child-work change. |
| 3 | `apps/appointments/tests/test_scheduler_lifecycle.py` | Add exactly `test_modify_delivery_content_invalidates_old_generation_before_new_admission` as a real-database `TransactionTestCase`. It must prove old non-terminal recipient/outbox fencing, one generation advance, marker reset, continued admittability, stale old-generation rowlessness, and one successful new-generation admission. |

## Required validation

```bash
python manage.py test apps.appointments.tests.test_scheduler_lifecycle.SchedulerLifecycleTransactionTests.test_modify_delivery_content_invalidates_old_generation_before_new_admission --settings=config.settings.test --verbosity=2
```

The test must use real lifecycle and admission services. It must show that the existing locked `admit_schedule_generation(...)` check—not a new parallel rule—rejects old expected generation after the single Modify generation advance.

## Strict exclusions

No model or migration change. No Delete, Re-arm, task change, recovery, adapters, projections, fakes, harnesses, staging, official-suite work, submission, Item 02, or later Scheduler work.
