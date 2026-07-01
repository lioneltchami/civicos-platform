# Wave 1 cleanup — applied after 0001_initial:
#
#   1. Remove rpt_snap_type_period_idx: the AlterUniqueTogether in 0001
#      already creates an implicit B-tree index on (report_type, period_year,
#      period_month). An explicit duplicate index wastes write overhead on every
#      snapshot upsert and is confusing in EXPLAIN plans.
#
#   2. AlterField on period_month to record the MinValueValidator(1) /
#      MaxValueValidator(12) constraints in migration state. Django validators
#      are enforced at the ORM level; no DB CHECK constraint is added, but the
#      migration keeps model and migration state in sync so squash / makemigrations
#      does not regenerate a spurious AlterField later.
import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0001_initial"),
    ]

    operations = [
        # ── 1. Drop redundant snapshot index ─────────────────────────────────
        migrations.RemoveIndex(
            model_name="reportsnapshot",
            name="rpt_snap_type_period_idx",
        ),
        # ── 2. Record period_month validators in migration state ──────────────
        migrations.AlterField(
            model_name="reportsnapshot",
            name="period_month",
            field=models.PositiveSmallIntegerField(
                help_text="Calendar month 1–12.",
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(12),
                ],
                verbose_name="Period month",
            ),
        ),
    ]
