# Item 03 — SCH-01.1 Exact Change List

**Date:** 2026-08-20  
**Stage:** Two fresh blind file-level reviews  
**Scope:** Core authoritative durable admission only.

> **Implementation boundary.** SCH-01.1 authorizes exactly four files: the schedule model, one forward migration, the Scheduler runtime service, and one new real-database transaction-test module. It does not authorize task integration or lifecycle behavior.

## Ordered file-level changes

| Order | File | Required SCH-01.1 change |
|---:|---|---|
| 1 | `apps/appointments/models.py` | Add only two schedule-level fields on `GovStackAlertSchedule`: nullable `admitted_generation` and a bounded `admission_outcome` value. The marker identifies a durably admitted current generation; the outcome represents no admission, `created`, `duplicate`, `stale_generation`, or `zero_recipients`. Preserve `delivery_generation` as the generation authority. Do not make `dispatched`, `celery_task_id`, publisher state, recipient-delivery state, or outbox state a competing admission authority. |
| 2 | `apps/appointments/migrations/0021_sch01_1_core_admission.py` | Add one forward-only migration with dependency `('appointments', '0020_scheduler_outbox_publisher_claim')`. Add only the two marker/outcome fields. Do not backfill an admission claim from legacy fields and do not add an index, constraint, lifecycle field, recovery state, adapter state, or data migration. |
| 3 | `apps/appointments/services/scheduler_runtime.py` | Add `admit_schedule_generation(...)` as the single core admission authority. In one `transaction.atomic()` block, obtain the schedule with `select_for_update()`, compare `expected_generation` to locked `delivery_generation` before any work, return `stale_generation` with no artifacts on mismatch, canonicalize the supplied logical-recipient set, and converge the existing deterministic recipient/outbox identities. It must atomically create exactly one `SchedulerRecipientDelivery` plus linked `SchedulerOutbox` per logical recipient, persist marker/outcome in the same transaction, and return `created`, `duplicate`, or `zero_recipients` deterministically. Existing `materialize()` may become an internal subordinate primitive but cannot remain a generation-bypass API. No external I/O is permitted. |
| 4 | `apps/appointments/tests/test_scheduler_admission.py` | Add `SchedulerAdmissionTransactionTests(TransactionTestCase)` with exactly the six methods below. Use real database transactions; use an actual concurrent transaction/connection strategy rather than sequential mocks; and inject a failure inside materialization for rollback evidence. |

## Required service contract

| Situation | Required outcome and durable effect |
|---|---|
| Current generation with recipients | `created`; one recipient delivery and one linked outbox per distinct logical recipient; marker equals current generation. |
| Repeated current-generation request | `duplicate`; no new recipient or outbox row; no generation increment. |
| Concurrent current-generation requests | One durable admission wins/creates; the other converges without uniqueness failure or duplicate durable work. |
| Stale expected generation | `stale_generation`; no marker, recipient, or outbox artifact for the stale request. |
| Materialization exception | Exception propagates; transaction rollback leaves no recipient, outbox, admitted-generation marker, outcome, or false `dispatched` claim. |
| No logical recipients | `zero_recipients`; current-generation marker/outcome is durable, recipient/outbox counts remain zero, and repeat converges. |

## Mandatory real-database transaction tests

The new class must contain all and only these named SCH-01.1 acceptance methods:

1. `test_current_generation_materializes_one_recipient_and_one_outbox_per_recipient`
2. `test_duplicate_admission_converges_without_duplicate_rows`
3. `test_concurrent_current_generation_admission_is_single_winner`
4. `test_stale_generation_cannot_materialize_current_work`
5. `test_materialization_rollback_leaves_no_recipient_or_outbox_or_dispatched_claim`
6. `test_zero_recipient_admission_is_deterministic_and_rowless`

The concurrency method must use independent committed database connections/transactions and assert exactly one durable recipient/outbox set. The rollback method must fail after admission starts and verify all admission state is absent after the transaction ends.

## Validation commands

```bash
python manage.py makemigrations appointments --check --dry-run --settings=config.settings.test
python manage.py migrate --settings=config.settings.test
python manage.py showmigrations appointments --settings=config.settings.test
python manage.py test apps.appointments.tests.test_scheduler_admission.SchedulerAdmissionTransactionTests --settings=config.settings.test --verbosity=2
```

The migration and all six real-database tests must pass before a commit is permitted.

## Explicitly excluded

Do not change `tasks.py`, `scheduler_tasks.py`, views, lifecycle services, publisher/recovery code, settings, adapters, projections, fakes, the 37-operation harness, staging, official-suite assets, submission materials, Item 02, or another Building Block. Modify/cancel/delete/re-arm fencing, legacy-field behavior testing, transport-I/O testing, and the seven broader SCH-01 tests are deferred beyond SCH-01.1.
