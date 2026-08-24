# Hand-written migration: UUID → BigIntegerField for actor_pk.
#
# Django's AlterField emits `ALTER COLUMN … TYPE bigint USING actor_pk::bigint`
# which Postgres rejects because UUID cannot be cast to bigint.
#
# Instead we:
#   1. Remove the old UUID column entirely.
#   2. Add a new BigIntegerField column with the same name.
#
# This is safe on a fresh test database (no existing rows).  For a live
# database with existing data the column would need to be populated
# separately — but ExportRecord rows are ephemeral audit entries and this
# field stored a UUID value that was incorrect from the start.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("reports", "0002_reports_cleanup"),
    ]

    operations = [  # noqa: RUF012
        # ── 1. Drop index that references actor_pk before removing the column ──
        # Required for SQLite compatibility: SQLite cannot drop a column while
        # an index still references it (OperationalError: error in index … after
        # drop column: no such column: actor_pk).
        migrations.RemoveIndex(
            model_name="exportrecord",
            name="rpt_exp_actor_ts_idx",
        ),
        # ── 2. Drop the old UUID column ───────────────────────────────────────
        migrations.RemoveField(
            model_name="exportrecord",
            name="actor_pk",
        ),
        # ── 3. Add the new BigIntegerField column ─────────────────────────────
        migrations.AddField(
            model_name="exportrecord",
            name="actor_pk",
            field=models.BigIntegerField(
                help_text=(
                    "Integer primary key of the staff user who triggered this export. "
                    "Stored as BigIntegerField to match User.pk (BigAutoField). "
                    "Never stores email, name, or any other PII."
                ),
                verbose_name="Actor PK",
            ),
        ),
        # ── 4. Recreate the index on the new column ───────────────────────────
        migrations.AddIndex(
            model_name="exportrecord",
            index=models.Index(
                fields=["actor_pk", "created_at"],
                name="rpt_exp_actor_ts_idx",
            ),
        ),
    ]
