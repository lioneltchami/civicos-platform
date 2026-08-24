# Item 03 — Payments PostgreSQL Migration Blocker: Minimal Safe Fix Plan

**Date:** 2026-08-21  
**Status:** Stage 2 plan complete; no migration/model code changed.

## Recommended repair

Both fresh reviews recommend **editing the erroneous historical migration `0033` and explicitly declaring the existing UUID primary key in the live `BatchLease` model**.

| File | Minimal change | Reason |
|---|---|---|
| `apps/payments/migrations/0033_alter_batchlease_created_at_alter_batchlease_id_and_more.py` | Remove only the `AlterField` operation that changes `BatchLease.id` to `models.BigAutoField`. Retain the `BatchLease.created_at`, `BatchLease.updated_at`, `IdempotencyLedger.created_at`, and `IdempotencyLedger.updated_at` operations unchanged. | The preceding `0032` already created the primary key as UUID; removing this one erroneous type-alteration prevents invalid PostgreSQL UUID-to-bigint DDL. |
| `apps/payments/govstack_models.py` | Add `id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)` explicitly to `BatchLease`. | Prevent the global default auto-primary-key setting from recreating model-state drift and align ORM state with `0032` and corrected `0033`. |

No new migration is recommended. A follow-up migration cannot unblock a fresh database because the graph fails before reaching it. Guarded SQL is inferior because it would conceal migration-state drift. UUID-to-bigint conversion, surrogate replacement, fake migration records, migration suppression, and SQLite fallback are all rejected because they weaken identity integrity or do not prove the fresh PostgreSQL graph.

## Integrity and compatibility contract

The repair preserves the canonical durable `BatchLease` UUID identity. It does not convert data, rewrite identifiers, alter foreign keys, modify leasing behavior, or change timestamp intent. On PostgreSQL, the failed DDL transaction aborts before a valid type conversion occurs, so the correct pre-`0033` schema remains UUID.

Editing historical migration content creates an operational compatibility risk because Django records migration names, not content checksums. Before release, any non-fresh deployed database must be inventoried: inspect its `django_migrations` state and the actual PostgreSQL column type. This repair is only the canonical fresh-install fix and is schema-neutral for an already migrated database that still has the UUID column. A database with an anomalous persisted bigint column requires a separate, backup-protected data-migration design; this narrow fix must not attempt to infer or convert those identities.

## Fresh PostgreSQL acceptance procedure

The implementation stage must use a disposable, empty PostgreSQL database and run the complete project migration graph with normal migrations enabled. Acceptance requires all of the following:

| Gate | Required evidence |
|---|---|
| Full fresh migration graph | `python manage.py migrate --noinput` exits zero using a PostgreSQL-backed settings profile. |
| Migration recorder | `payments.0032`, corrected `payments.0033`, and every later Payments migration are recorded as applied. |
| Physical schema | `payments_batchlease.id` has PostgreSQL type `uuid`, is `NOT NULL`, and is the primary-key column. |
| Migration-state consistency | `python manage.py makemigrations --check` reports no model changes. |
| Lease integrity | A `BatchLease` UUID survives create/reload and the existing lease/fencing behaviors retain their expected semantics. |
| Scheduler prerequisite | The fresh graph proceeds through Appointments/Scheduler migrations so SCH-02.1 can be retried against PostgreSQL. |

## Rollback and exclusions

Rollback is source-level for an un-deployed or disposable database: revert the model declaration and the single `0033` operation edit. It must **not** convert UUID rows to bigint or delete/rewrite durable lease identities. Any production-like database rollback must be separately approved after backup and catalog inspection.

This plan does not change Payments runtime logic, Item 02 acceptance controls, Scheduler code, recipient behavior, recovery semantics, adapters, fakes, harnesses, staging, official-suite work, deployment, or submission.
