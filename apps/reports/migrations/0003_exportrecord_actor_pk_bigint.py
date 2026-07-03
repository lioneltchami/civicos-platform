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

    dependencies = [
        ("reports", "0002_reports_cleanup"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="exportrecord",
            name="actor_pk",
        ),
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
    ]
