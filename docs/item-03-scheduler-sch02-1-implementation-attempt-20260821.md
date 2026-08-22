# Item 03 — SCH-02.1 Implementation Attempt: All-or-Discard Rejection

**Date:** 2026-08-21  
**Status:** Rejected; SCH-02.1 remains open.  
**Stage 4:** Not started.

## Decision

The SCH-02.1 candidate implementation is discarded under the agreed **all-or-discard** rule. The candidate added a bounded publisher-state design, finite retry/backoff and exhaustion behavior, replay guards, a proposed additive migration, focused publisher tests, and a narrow PostgreSQL test profile. However, the acceptance gate required all focused publisher `TransactionTestCase` methods to run against a fresh, migration-created PostgreSQL database. That gate could not complete.

No implementation code, migration, settings profile, or candidate test remains in the repository after this record. Only the completed SCH-02.1 planning records and this rejection record are retained.

## Blocking evidence

The local Compose `db` PostgreSQL service was started successfully and reported healthy. The focused Django test command then created `test_civicos_sch02` and began applying the full project migration graph. It failed before the SCH-02.1 tests ran because an existing unrelated Payments migration attempted an invalid PostgreSQL conversion:

> `django.db.utils.ProgrammingError: cannot cast type uuid to bigint`  
> `... ALTER COLUMN "id" TYPE bigint USING "id"::bigint`

The failure arose while applying the pre-existing Payments migration sequence to the fresh PostgreSQL test database. The local `civicos` database had no initialized `django_migrations` table, so it could not be cloned or used as a pre-migrated baseline.

| Required SCH-02.1 gate | Result |
|---|---|
| PostgreSQL service availability | Available and healthy |
| Fresh Django PostgreSQL test database creation | Began successfully |
| Full migration graph to SCH-02.1 test schema | **Failed** on unrelated existing Payments UUID-to-bigint migration |
| Ten focused publisher `TransactionTestCase` methods | Not executed; no passing evidence |
| Migration-backfill proof | Not executed; no passing evidence |
| Stage 4 independent verification | Not started |

## Scope preservation

The failure is an infrastructure/migration prerequisite, not evidence that the proposed publisher state machine is correct. Fixing or bypassing the Payments migration is outside SCH-02.1 and outside the user's allowed Scheduler-only scope. No workaround that suppresses migrations, falls back to SQLite, edits unrelated Payments migrations, or claims equivalent PostgreSQL coverage is acceptable for this increment.

The candidate also did not alter recipient delivery semantics, SCH-01 authority or lifecycle behavior, publisher crash-window proof, acknowledgement/history, projections, adapters, fakes, the 37-operation harness, staging, the official suite, or submission.

## Remaining prerequisite

A future SCH-02.1 attempt requires a reproducible PostgreSQL test database whose full project migration graph completes successfully, including the existing Payments history. After that prerequisite exists, a fresh full all-or-discard SCH-02.1 implementation attempt must add and pass every planned publisher test against that database before independent verification may start.
