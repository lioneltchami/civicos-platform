# Item 03 — Scheduler Dispatch PostgreSQL Lock Fix Plan

**Status: Approved for a narrow implementation attempt.** Two fresh blind reviews agreed that a scoped lock on the base `GovStackAlertSchedule` table is the smallest safe correction. The project environment confirms the required support: Django `5.2.3`, PostgreSQL backend, and `connection.features.has_select_for_update_of=True`.

> **Frozen change.** Replace the unscoped `select_for_update()` in `dispatch_alert_schedule()` with `select_for_update(of=("self",))`; retain every existing eager relation, the single transaction, recipient derivation, authoritative admission call, and conditional `transaction.on_commit()` wake-up unchanged.

## Exact ordered change list

| Order | File | Change | Purpose |
|---:|---|---|---|
| 1 | `apps/appointments/tasks.py` | Change only `GovStackAlertSchedule.objects.select_for_update()` to `GovStackAlertSchedule.objects.select_for_update(of=("self",))` in `dispatch_alert_schedule()` | Lock only the schedule table while retaining the existing eager snapshot joins, preventing PostgreSQL from attempting to lock nullable `slot__resource` outer-join rows. |
| 2 | `apps/appointments/tests/test_scheduler_runtime.py` | In the existing live-dispatch durable-materialization test, explicitly set `self.slot.resource = None` and save that relation before invoking the task; retain all existing assertions. | Force the nullable joined relation that previously caused the PostgreSQL failure while still proving one durable delivery/outbox record and one post-commit publisher wake-up. |
| 3 | No other source files | Do not modify any other code. | Preserve all SCH-01 authority, lifecycle, task, and publisher boundaries. |

## Required proof

The implementation may be retained only if the following PostgreSQL commands pass in the containerized project environment. The regression test must execute against PostgreSQL; a SQLite pass or a skipped PostgreSQL test is not evidence.

```sh
# Focused nullable-resource live dispatch regression
docker compose run --rm --no-deps \
  -e DJANGO_SETTINGS_MODULE=config.settings.integration \
  -e POSTGRES_DB=<disposable-db> -e POSTGRES_USER=civicos \
  -e POSTGRES_PASSWORD=civicos -e POSTGRES_HOST=db \
  web python manage.py test \
  apps.appointments.tests.test_scheduler_runtime.SchedulerRuntimeTests.test_live_dispatch_materializes_durable_work_without_http \
  --verbosity 2

# SCH-01 admission/lifecycle regression
docker compose run --rm --no-deps \
  -e DJANGO_SETTINGS_MODULE=config.settings.integration \
  -e POSTGRES_DB=<disposable-db> -e POSTGRES_USER=civicos \
  -e POSTGRES_PASSWORD=civicos -e POSTGRES_HOST=db \
  web python manage.py test apps.appointments.tests.test_scheduler_lifecycle --verbosity 2

# Combined bounded Scheduler regression
docker compose run --rm --no-deps \
  -e DJANGO_SETTINGS_MODULE=config.settings.integration \
  -e POSTGRES_DB=<disposable-db> -e POSTGRES_USER=civicos \
  -e POSTGRES_PASSWORD=civicos -e POSTGRES_HOST=db \
  web python manage.py test \
  apps.appointments.tests.test_scheduler_runtime \
  apps.appointments.tests.test_scheduler_lifecycle --verbosity 2
```

The focused test must show that `dispatch_alert_schedule.run()` completes when the slot has no resource, creates the same durable admission rows, and invokes `publish_scheduler_outbox.delay()` exactly once only after commit. The lifecycle suite must retain Cancel, Modify, Delete, and Re-arm behavior without changing their implementation.

## Explicit exclusions

This cycle does not implement SCH-02.1 publisher recovery, recipient recovery, crash-window proof, state projections, acknowledgement/history, transports, adapters, or the 37-operation harness. It does not alter `scheduler_runtime.admit_schedule_generation()`, generation/idempotency policy, recipient-selection rules, model fields, Celery task settings, lifecycle fencing, outbound safety helpers, or any post-commit sequencing. It must not split the current fetch into a two-query refactor, move the lock boundary, lock related tables, or call the publisher inside the transaction.

If the scoped-lock change fails the stated PostgreSQL proof, all source and test changes must be discarded and a rejection record committed. Only a passing implementation may advance to independent verification.
