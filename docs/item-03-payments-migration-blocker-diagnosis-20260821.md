# Item 03 — Payments PostgreSQL Migration Blocker Diagnosis

**Date:** 2026-08-21  
**Status:** Stage 1 diagnosis complete; no migration or model code changed.

## Diagnosis conclusion

The PostgreSQL migration prerequisite is blocked by a pre-existing **Payments migration-state drift**, not by Scheduler code. Both shared-context analyses identify the exact failure in `apps/payments/migrations/0033_alter_batchlease_created_at_alter_batchlease_id_and_more.py`.

> **Failing operation:** `AlterField(model_name='batchlease', name='id', field=models.BigAutoField(...))` at lines 18–22.

The immediately preceding migration, `0032_item02_platform_persistence.py`, created `BatchLease.id` as a `UUIDField` primary key. Migration `0033` then asks PostgreSQL to alter that established UUID primary key to a `BigAutoField`, producing SQL equivalent to:

> `ALTER COLUMN "id" TYPE bigint USING "id"::bigint`

PostgreSQL correctly rejects the operation because UUID values have no valid direct cast to signed 64-bit integers. The observed `ProgrammingError` occurs while applying a fresh project migration graph, before any SCH-02.1 publisher test executes.

## Evidence matrix

| Evidence source | Finding | Consequence |
|---|---|---|
| `0032_item02_platform_persistence.py` | Introduces `BatchLease.id` as an explicit UUID primary key. | UUID is the established persisted identity. |
| `0033_alter_batchlease_created_at_alter_batchlease_id_and_more.py` | Alters the same `id` to an implicit `BigAutoField`. | Django emits the invalid UUID-to-bigint DDL. |
| `apps/payments/govstack_models.py` | `BatchLease` omits an explicit `id`, allowing current model state to infer the project default auto primary key. | Current model state drifts from the UUID schema introduced by `0032`. |
| Fresh PostgreSQL test-database run | Fails at the generated cast before Scheduler tests begin. | Full migration-backed SCH-02.1 evidence is unavailable. |

## Intended identifier type

`BatchLease.id` should remain **UUID**. The migration history deliberately introduced it as UUID, and `BatchLease` is a durable ownership-token record. The diagnosis found no affirmative migration, model, or data evidence for a safe identifier redesign to bigint.

Forcing a UUID-to-bigint conversion would be unsafe: it could break foreign-key references, durable lease records, audit/log correlation, and ORM identity continuity. The failed PostgreSQL DDL is transactional, so the attempted type conversion did not partially succeed or rewrite valid UUID values.

## Stage 2 question

The minimal safe remediation must align the current model/migration state to the original UUID identity without data conversion. The next fresh reviews must decide whether the safest repair is to correct historical migration `0033` and explicitly declare `BatchLease.id` in the live model, or to use a narrowly scoped state-preserving alternative. Any approach must preserve the timestamp alterations in `0033`, leave unrelated Payments behavior unchanged, and allow a fresh PostgreSQL migration graph to complete.
