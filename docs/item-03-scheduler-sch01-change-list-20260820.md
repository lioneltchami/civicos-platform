# Item 03 — SCH-01 Exact Implementation Change List

**Date:** 2026-08-20  
**Stage:** Fresh blind file-level code reviews  
**Scope:** SCH-01 authoritative durable schedule admission only.

> **Implementation gate.** This list is limited to authoritative current-generation admission, explicit outcome state, atomic recipient/outbox materialization, lifecycle fencing, compatibility-only legacy fields, no-pre-commit I/O, and the thirteen required real-database transaction tests. It does not authorize recovery, adapter, projection, fake, operation-harness, staging, official-suite, submission, Payments, or another Building Block work.

## Reconciled ordered change list

| Order | File | Required SCH-01 change |
|---:|---|---|
| 1 | `apps/appointments/models.py` | Add the smallest additive schedule-level authoritative admission marker to `GovStackAlertSchedule`: a nullable current-generation admission marker and bounded outcome that can distinguish **not admitted**, **admitted with recipients**, and **admitted with zero recipients**. Recommended names are `admitted_generation` and `admission_outcome`. Preserve `delivery_generation`, `dispatched`, `celery_task_id`, `SchedulerRecipientDelivery`, `SchedulerOutbox`, their identity/lease fields, and existing uniqueness constraints. Legacy fields must not become authoritative. |
| 2 | `apps/appointments/migrations/0021_sch01_authoritative_admission.py` | Add one forward-only migration depending on `0020_scheduler_outbox_publisher_claim`. Add only the marker/outcome fields and a narrowly required index/constraint if needed. Preserve existing legacy values but do not backfill an authoritative admission from `dispatched`, task ID, or legacy Celery state. No recovery, projection, telemetry, adapter, or unrelated schema may be introduced. |
| 3 | `apps/appointments/services/scheduler_runtime.py` | Add one authoritative service, named `admit_schedule_generation(...)` or a semantically equivalent repository-consistent name. It runs inside the caller’s atomic transaction with a locked schedule row; verifies `expected_generation == delivery_generation`; returns deterministic domain outcomes including `created`, `duplicate`, `stale_generation`, `zero_recipients`, and `blocked`/`ineligible`; atomically converges recipient and linked outbox rows; persists the schedule-level admission outcome; and performs no broker, Celery, HTTP, adapter, recipient transport, or external I/O. Existing `materialize` becomes an internal subordinate primitive and cannot remain a stale-generation bypass. |
| 4 | `apps/appointments/services/govstack_alert_schedule.py` | Consolidate create/modify/cancel/delete/re-arm behavior around one locked generation-fence transition. Admission-affecting changes must fence/invalidate non-terminal old work and increment `delivery_generation` once before new admission. Cancellation must make the current generation non-admittable. Deletion must apply the named durable cascade-or-retention policy before/with removal so stale task work cannot recreate rows. Re-arm must advance a new eligible generation and permit exactly one admission. Legacy revoke is best-effort cleanup only. |
| 5 | `apps/appointments/tasks.py` | Refactor `dispatch_alert_schedule` to load/lock the current schedule and delegate current-generation admission to the authoritative service. Remove any direct use of `dispatched` or `celery_task_id` as an admission gate and direct public materialization loop. Any retained compatibility wake-up is registered only with `transaction.on_commit()`; its failure cannot undo committed durable admission. Keep missing/deleted schedules safe no-ops and do not implement delivery, recovery, retry, or publishing work here. |
| 6 | `apps/appointments/govstack_views.py` | Make only the minimum schedule create/modify/delete call-site adjustment required to route lifecycle changes through the refactored service boundary and return existing-compatible API behavior. Do not alter route inventory, authorization design, response contract, operation harness, status, or another Building Block. |
| 7 | `apps/appointments/tests/test_scheduler_admission.py` | Add a new real-database `TransactionTestCase` module. It must contain a `SchedulerAdmissionTransactionTests` class with the thirteen exact mandatory methods below. Use existing schedule/slot/recipient/outbox fixture patterns but exercise real transactions and real admission/task code. A transport/broker/Celery spy is permitted only to prove that no pre-commit I/O occurred. |

`apps/appointments/scheduler_tasks.py` is **not** an authorized SCH-01 production change surface. The existing publisher/recovery wrapper remains a later-increment concern. If its pre-existing call site needs a strictly necessary import-compatible post-commit adjustment, that must be documented at implementation time and cannot add publisher recovery behavior.

## Required service and lifecycle behavior

| Condition | Required behavior |
|---|---|
| First current-generation admission | Lock schedule; create one deterministic recipient row per logical recipient plus one linked outbox row per recipient atomically; persist `created`/admitted outcome for that generation. |
| Duplicate or concurrent same-generation call | Return `duplicate`/already-admitted; no extra row, no generation increment, no unhandled uniqueness error. |
| Stale expected generation | Return `stale_generation`; create no recipient, outbox, outcome marker, compatibility claim, or transport side effect. |
| Zero recipients | Persist an explicit current-generation `zero_recipients` outcome, with zero recipient and outbox rows; repeat is idempotent. |
| Fan-out exception | Roll back every recipient/outbox/marker/compatibility-state mutation. |
| Modify, cancel, delete, re-arm | Use one locked transition that fences stale work. Deletion correctness cannot rely on legacy broker revoke. |
| Legacy disagreement | `dispatched=True` without durable current-generation state admits once; `dispatched=False` with durable current-generation state converges duplicate; task ID never grants authority. |
| I/O boundary | No broker, Celery publish, HTTP, adapter, or transport side effect occurs before commit. Post-commit wake-up failure leaves durable admission intact. |

## Mandatory test methods

The new `SchedulerAdmissionTransactionTests` class must define exactly these named methods and all must pass:

1. `test_current_generation_materializes_one_recipient_and_one_outbox_per_recipient`
2. `test_duplicate_admission_converges_without_duplicate_rows`
3. `test_concurrent_current_generation_admission_is_single_winner`
4. `test_stale_generation_cannot_materialize_current_work`
5. `test_materialization_rollback_leaves_no_recipient_or_outbox_or_dispatched_claim`
6. `test_zero_recipient_admission_is_deterministic_and_rowless`
7. `test_modify_delivery_content_invalidates_old_generation_before_new_admission`
8. `test_cancel_schedule_fences_non_terminal_work_and_blocks_admission`
9. `test_delete_schedule_cannot_leave_recreatable_admission_work`
10. `test_rearm_advances_generation_and_admits_exactly_once`
11. `test_legacy_dispatched_and_celery_state_never_authorize_admission`
12. `test_no_transport_io_before_admission_commit`
13. `test_rolled_back_admission_does_not_publish_or_schedule_transport`

The concurrent test must use real transaction behavior, not a mocked sequential call. The rollback tests must inject failure inside the materialization transaction. The pre-commit test must inspect transaction ordering, not only whether a mocked publisher was eventually called.

## Required validation commands

Run the forward migration and test against the project test settings without a no-migrations shortcut:

```bash
python manage.py makemigrations appointments --check --dry-run --settings=config.settings.test
python manage.py migrate --settings=config.settings.test
python manage.py test apps.appointments.tests.test_scheduler_admission.SchedulerAdmissionTransactionTests --settings=config.settings.test --verbosity=2
python manage.py test apps.appointments.tests.test_scheduler_runtime apps.appointments.tests.test_govstack_alert_schedule --settings=config.settings.test --verbosity=2
python manage.py showmigrations appointments --settings=config.settings.test
```

The final acceptance run must include the new thirteen-method transaction module and existing Scheduler runtime/alert-schedule regression modules. No staging, official suite, external endpoint, deployment, or submission command is part of SCH-01.

## Explicit rejection

Do not modify `config/settings/test.py`, project configuration, Scheduler URL registration, publisher/recovery/reaper code, Payments/Consent code, adapters, projections, status/history/metrics, fakes, local 37-operation topology, staging/deployment artifacts, official-suite assets, or submission materials. Any implementation that needs these changes is incomplete for SCH-01 and must be discarded under the all-or-discard rule.
