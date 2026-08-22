# Item 03 — Scheduler SCH-02.1 Retry Rejection Record

**Status: Rejected and discarded.** This record closes the second SCH-02.1 implementation attempt without an implementation commit. The retry met the new migration prerequisite and the narrow publisher test gate, but it did not meet the increment's all-or-discard evidence rule because the bounded PostgreSQL Scheduler regression set contains a failing live dispatch path. Accordingly, every candidate source change, the candidate migration `0023`, the PostgreSQL-only test profile, the candidate tests, and the temporary implementation archive were removed before this record was written. SCH-01 remains unmodified.

> **All-or-discard decision.** A passing targeted test suite is not sufficient when an agreed bounded regression exposes a Scheduler failure. No Stage 4 verification and no SCH-02.2 work may begin from this rejected attempt.

## Candidate scope and required boundary

The discarded candidate was limited to SCH-02.1: a durable publisher-state vocabulary, finite retry budget and deterministic backoff, token-and-generation-fenced finalization, `LOCAL_FAILURE` versus `UNKNOWN_HANDOFF`, terminal exhaustion, guarded replay, and a safe additive outbox migration. It did not change recipient recovery, crash-window proof, acknowledgements or history, projections, transports, adapters, the operation harness, Payments behavior, or any SCH-01 implementation.

| Control | Result before discard | Evidence |
|---|---|---|
| Fresh PostgreSQL full migration graph, including candidate `appointments.0023` | Passed | [Fresh migration log][1] |
| Model/migration consistency check | Passed; `No changes detected` | [Migration-drift log][2] |
| Exact ten required PostgreSQL `TransactionTestCase` methods | Passed; 10 tests in 36.266 seconds | [Publisher-suite log][3] |
| Bounded SCH-01 lifecycle regressions | Four lifecycle tests passed | [Regression log][4] |
| Existing live durable-dispatch regression | Failed on PostgreSQL | [Regression log][4] |
| Restored committed baseline confirmation of the same failure | Failed identically on PostgreSQL | [Baseline confirmation log][5] |

## Commands and environment identity

The database engine was the Compose `db` service, `postgres:16-alpine`, with `POSTGRES_DB=civicos`, `POSTGRES_USER=civicos`, and `POSTGRES_PASSWORD=civicos`. Validation used disposable databases named `civicos_sch02`, `civicos_sch02_test`, and, after discard, `civicos_sch02_baseline`. The containerized Django runner used the repository image, PostgreSQL host `db`, an ephemeral non-production `DJANGO_SECRET_KEY`, and `DJANGO_SETTINGS_MODULE=config.settings.sch02_postgres` for the candidate migration and ten-method suite. The restored-baseline confirmation used `config.settings.integration`.

```sh
# Candidate fresh migration and model-drift gate
docker compose run --rm --no-deps \
  -e DJANGO_SETTINGS_MODULE=config.settings.sch02_postgres \
  -e POSTGRES_DB=civicos_sch02 -e POSTGRES_USER=civicos \
  -e POSTGRES_PASSWORD=civicos -e POSTGRES_HOST=db \
  web python manage.py migrate --noinput

docker compose run --rm --no-deps \
  -e DJANGO_SETTINGS_MODULE=config.settings.sch02_postgres \
  -e POSTGRES_DB=civicos_sch02 -e POSTGRES_USER=civicos \
  -e POSTGRES_PASSWORD=civicos -e POSTGRES_HOST=db \
  web python manage.py makemigrations --check --dry-run

# Exact candidate publisher suite
docker compose run --rm --no-deps \
  -e DJANGO_SETTINGS_MODULE=config.settings.sch02_postgres \
  -e POSTGRES_DB=civicos_sch02 -e POSTGRES_TEST_DB=civicos_sch02_test \
  -e POSTGRES_USER=civicos -e POSTGRES_PASSWORD=civicos -e POSTGRES_HOST=db \
  web python manage.py test \
  apps.appointments.tests.test_scheduler_runtime.SchedulerPublisherRecoveryPostgresTests \
  --verbosity 2
```

## Passing narrow publisher evidence

The candidate's ten-method suite ran only against PostgreSQL and completed successfully. Its raw output names every required method: `test_claim_outbox_consumes_attempt_and_sets_publisher_lease`, `test_local_enqueue_failure_is_classified_backed_off_and_reclaimable`, `test_unknown_handoff_is_distinct_and_preserves_correlation`, `test_retry_policy_is_bounded_and_not_an_unbounded_hot_loop`, `test_attempt_budget_transitions_to_terminal_publisher_failure`, `test_guarded_replay_reopens_terminal_row_without_losing_correlation`, `test_replay_rejects_published_and_cancelled_rows`, `test_published_and_failure_require_current_publisher_token_and_generation`, `test_existing_outbox_rows_migrate_to_safe_publisher_states`, and `test_real_competing_publishers_have_one_current_owner`. The log reports all ten as `ok` and ends with `Ran 10 tests in 36.266s` and `OK`.[3]

This evidence is retained only to explain the rejection; it does not authorize keeping the candidate implementation. In particular, the tenth test proved PostgreSQL-backed competing claims and stale finalization rejection, while the migration gate showed that the independently repaired Payments historical migration no longer blocked the complete graph.[1] [3]

## Blocking regression and baseline confirmation

The agreed bounded regression command ran `apps.appointments.tests.test_scheduler_lifecycle` and `SchedulerRuntimeTests` under PostgreSQL. The lifecycle cases for Cancel, delivery-affecting Modify, Delete, and Re-arm passed. The pre-existing live-dispatch test `test_live_dispatch_materializes_durable_work_without_http` then errored before durable materialization.

The PostgreSQL error is:

> `django.db.utils.NotSupportedError: FOR UPDATE cannot be applied to the nullable side of an outer join`

The stack trace enters `apps/appointments/tasks.py` at `dispatch_alert_schedule`, where the schedule query calls `select_for_update()` together with a nullable-side join before `.get(pk=alert_schedule_pk)`. The candidate SCH-02.1 code did not modify that path. To distinguish a candidate regression from an existing PostgreSQL incompatibility, the entire SCH-02.1 candidate was discarded and the single failing test was rerun on the restored committed baseline with `config.settings.integration`. It failed with the same PostgreSQL exception and the same location.[4] [5]

The failure is therefore documented as a **pre-existing PostgreSQL Scheduler dispatch regression**, not as an acceptance claim about SCH-02.1. It is nevertheless blocking for this retry because the agreed all-or-discard rule requires the bounded Scheduler regression gate to succeed. Repairing the dispatch query would alter SCH-01-adjacent live task integration and is outside the frozen SCH-02.1 publisher-recovery scope; it was deliberately not attempted here.

## Discard verification

After the failed regression, the following candidate paths were restored or removed: `apps/appointments/models.py`, `apps/appointments/services/scheduler_runtime.py`, `apps/appointments/scheduler_tasks.py`, `apps/appointments/tests/test_scheduler_runtime.py`, `config/settings/base.py`, `apps/appointments/migrations/0023_sch02_1_bounded_publisher_recovery.py`, `config/settings/sch02_postgres.py`, and `.tmp-item03-sch02-1-retry-implementation.tar.gz`. The repository was clean before adding this documentation and raw evidence only.

No Stage 4 independent verification has been started. SCH-02.1 remains **open**. A future cycle must first decide, in a separately scoped and reviewed increment, how to resolve or formally gate the confirmed PostgreSQL `select_for_update()` / nullable-join failure without reopening completed SCH-01 behavior. Only then may a new clean SCH-02.1 implementation attempt begin.

## References

[1]: evidence/item-03-scheduler-sch02-1-retry-fresh-migration-20260822.log "Fresh PostgreSQL migration graph raw output"
[2]: evidence/item-03-scheduler-sch02-1-retry-makemigrations-check-20260822.log "Model migration-drift check raw output"
[3]: evidence/item-03-scheduler-sch02-1-retry-publisher-suite-20260822.log "Exact ten-method PostgreSQL publisher suite raw output"
[4]: evidence/item-03-scheduler-sch02-1-retry-regression-failure-20260822.log "Candidate bounded Scheduler PostgreSQL regression raw output"
[5]: evidence/item-03-scheduler-sch02-1-retry-baseline-confirmation-20260822.log "Restored baseline PostgreSQL failure confirmation raw output"
