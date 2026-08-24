# Item 03 — SCH-01.2a Cancel-Only Scope Confirmation

**Date:** 2026-08-21  
**Stage:** Two independent scope confirmations  
**Verdict:** **Confirmed — cancel only.**

> SCH-01.2a is restricted to durable cancellation fencing. Modify, delete, re-arm, task integration, recovery, adapters, projections, fakes, harness work, staging, official-suite validation, submission, Item 02, and later work are excluded.

## Required cancellation-only behavior

| Area | Required result |
|---|---|
| Durable schedule state | Add `GovStackAlertSchedule.delivery_admittable`, default `True`. Cancellation sets it to `False`; no generation advance is included in this increment. |
| Locked cancellation | Under the schedule lock, cancel/fence all non-terminal recipient rows, clear recipient lease fields, reset `admitted_generation` and `admission_outcome`, and make associated unpublished outbox rows non-publishable. |
| Durable outbox fence | Add nullable `SchedulerOutbox.cancelled_at`. Cancellation stamps it and clears publisher claim state; claim/due/publish paths must exclude cancelled unpublished rows. |
| Admission guard | `admit_schedule_generation(...)` must reject `delivery_admittable=False` after locking the schedule and before child-row creation, returning rowless `not_admittable` without changing SCH-01.1 markers. |
| Acceptance test | Add exactly `test_cancel_schedule_fences_non_terminal_work_and_blocks_admission` as a real-database `TransactionTestCase`. |

## Permitted file set

| File | Permitted narrow change |
|---|---|
| `apps/appointments/models.py` | `delivery_admittable` and `SchedulerOutbox.cancelled_at` only. |
| `apps/appointments/migrations/0022_sch01_2a_cancel_only.py` | One forward-only migration after `0021_sch01_1_core_admission`. |
| `apps/appointments/services/scheduler_runtime.py` | Locked cancellation fence, rowless admission eligibility guard, and cancelled-outbox publish exclusion. |
| `apps/appointments/services/govstack_alert_schedule.py` | Public cancellation entry point delegating only to the locked cancellation operation, if needed. |
| `apps/appointments/tests/test_scheduler_lifecycle.py` | The one exact required cancellation test only. |

No code was written during this stage.
