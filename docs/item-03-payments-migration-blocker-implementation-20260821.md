# Item 03 — Payments PostgreSQL Migration Blocker: Implementation Evidence

**Date:** 2026-08-21  
**Status:** Stage 3 implementation and fresh PostgreSQL migration proof complete; pending independent verification.

## Implemented repair

The implementation follows the reviewed minimal UUID-preserving plan exactly.

| File | Change | Integrity effect |
|---|---|---|
| `apps/payments/govstack_models.py` | Explicitly declares `BatchLease.id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)`. | Aligns live ORM state with the UUID primary key introduced by `0032`; prevents default auto-field drift. |
| `apps/payments/migrations/0033_alter_batchlease_created_at_alter_batchlease_id_and_more.py` | Removes only the erroneous `BatchLease.id` `BigAutoField` `AlterField`. | Prevents invalid UUID-to-bigint DDL while preserving every timestamp operation in the migration. |

No Payments runtime behavior, foreign key, identifier value, Scheduler file, or unrelated migration was modified. No UUID-to-bigint conversion, guarded SQL bypass, migration faking, or data rewrite was used.

## Fresh PostgreSQL proof

The repository Compose PostgreSQL service was started and reported healthy. A disposable database named `civicos_migration_validation` was dropped and recreated. The normal full project migration graph was then applied from zero through a containerized Django command using `config.settings.integration` and PostgreSQL host `db`.

```bash
DJANGO_SETTINGS_MODULE=config.settings.integration \
POSTGRES_HOST=db POSTGRES_PORT=5432 \
POSTGRES_DB=civicos_migration_validation \
POSTGRES_USER=civicos POSTGRES_PASSWORD=civicos \
python manage.py migrate --noinput --verbosity 1

python manage.py makemigrations --check --dry-run
```

The complete migration graph finished successfully. The final migration evidence recorded the following results:

| Verification query or command | Result |
|---|---|
| Complete `migrate --noinput` | Exit-successful; reached all application migrations, including Payments and Appointments/Scheduler. |
| `makemigrations --check --dry-run` | `No changes detected`. |
| PostgreSQL `information_schema.columns` for `payments_batchlease.id` | `data_type = uuid`, `udt_name = uuid`, `is_nullable = NO`. |
| Payments migration recorder count | 45 Payments migrations applied. |
| Required Payments history | `0032_item02_platform_persistence`, corrected `0033_alter_batchlease_created_at_alter_batchlease_id_and_more`, and `0034_item02_core_runtime` all recorded in order. |

The pre-fix failure—`ALTER COLUMN id TYPE bigint USING id::bigint`—did not recur. The full migration graph proceeds past the former blocker and establishes a fresh PostgreSQL prerequisite suitable for later SCH-02.1 validation.

## Remaining gate

This evidence does not start or implement SCH-02.1. Stage 4 must independently verify the repaired migration contents, full PostgreSQL evidence, and preservation of Payments UUID identity before the migration prerequisite is considered closed.
