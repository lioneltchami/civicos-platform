# Item 03 — SCH-01.2 Exact Change List

**Date:** 2026-08-21  
**Stage:** Two fresh blind file-level reviews  
**Scope:** Generation-fenced modify, cancel, delete, and re-arm only.

> SCH-01.2 retains the SCH-01.1 locked admission authority. It adds only the lifecycle eligibility and fencing checks required to make delivery transitions durable and stale-safe.

## Ordered implementation changes

| Order | File | Required change |
|---:|---|---|
| 1 | `apps/appointments/models.py` | Add `delivery_admittable` to `GovStackAlertSchedule`, defaulting to `True`, as the minimal durable cancellation/ineligibility marker. Add nullable `cancelled_at` to `SchedulerOutbox` to remove cancelled unpublished outbox rows from publishability. Preserve `delivery_generation`, SCH-01.1 markers, and existing cascade foreign keys. |
| 2 | `apps/appointments/migrations/0022_sch01_2_lifecycle_fencing.py` | Add a forward-only migration after `0021_sch01_1_core_admission` for only `delivery_admittable` and `SchedulerOutbox.cancelled_at`; existing schedules default to admissible. Do not change task, recovery, adapter, projection, fake, or unrelated schema. |
| 3 | `apps/appointments/services/scheduler_runtime.py` | Add the lifecycle eligibility guard to `admit_schedule_generation(...)` after locking the schedule and before child-row creation. An ineligible schedule returns an explicit rowless `not_admittable` result and does not mutate SCH-01.1 markers. Preserve stale-generation rowlessness. Add locked helper(s) to cancel/fence non-terminal recipient rows, clear recipient lease state, and stamp every unpublished associated outbox row with `cancelled_at` while clearing publisher claim state. Update runtime outbox claim/publish eligibility to exclude `cancelled_at IS NOT NULL`. Add locked cancel, hard-delete, and re-arm lifecycle operations; re-arm advances generation once, clears ineligibility, resets admission markers, and fences prior non-terminal work. |
| 4 | `apps/appointments/services/govstack_alert_schedule.py` | Under the existing schedule lock, treat `event_id`, `target_category`, `message_id`, and `alert_datetime` as delivery-affecting changes. Such modify calls fence old non-terminal work, advance `delivery_generation` once, clear admission markers, and leave the schedule admissible. Metadata-only/no-op changes do none of these. Add or expose an explicit re-arm transition rather than relying on incidental modify behavior. Route delete through the locked hard-delete lifecycle helper so hard-delete cascade is preceded by durable fencing; best-effort broker revoke remains cleanup only. |
| 5 | `apps/appointments/tests/test_scheduler_lifecycle.py` | Add a real-database `TransactionTestCase` class containing exactly the four required lifecycle methods below. Use the real lifecycle and admission services; assert recipient status, outbox cancellation, generation, marker, cascade, stale rowlessness, and exact-once admission. |

## Required lifecycle semantics

| Transition | Required behavior |
|---|---|
| Modify | Delivery-affecting changes (`event_id`, `target_category`, `message_id`, `alert_datetime`) fence old non-terminal recipient/outbox work, advance generation exactly once, clear markers, and make only the new generation eligible. |
| Cancel | Mark schedule non-admittable; cancel non-terminal recipient work; clear recipient lease tokens; make unpublished outbox rows permanently non-publishable; reset admission markers. |
| Delete | Lock and fence before hard delete. Existing database `CASCADE` removes recipients/outboxes. A later invocation cannot recreate rows without the locked parent schedule. |
| Re-arm | Lock, fence old work, advance generation exactly once, set admissible, clear markers, then permit one new-generation admission; a repeat is duplicate/no-op. |

## Required `TransactionTestCase` methods

1. `test_modify_delivery_content_invalidates_old_generation_before_new_admission`
2. `test_cancel_schedule_fences_non_terminal_work_and_blocks_admission`
3. `test_delete_schedule_cannot_leave_recreatable_admission_work`
4. `test_rearm_advances_generation_and_admits_exactly_once`

## Required validation

```bash
python manage.py makemigrations appointments --check --dry-run --settings=config.settings.test
python manage.py migrate --settings=config.settings.test
python manage.py test apps.appointments.tests.test_scheduler_lifecycle.SchedulerLifecycleTransactionTests --settings=config.settings.test --verbosity=2
```

All four methods must pass. In addition, the SCH-01.1 admission suite should remain green as a focused regression check.

## Explicit exclusions

Do not change `apps/appointments/tasks.py`. Do not introduce recovery, lease reclaim, adapters, projections, fakes, 37-operation harness work, staging, official-suite execution, submission, Item 02, or later Scheduler increments.
