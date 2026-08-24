# Generated migration — Wave 1 review fixes for Volunteer Management BB.
# Covers:
#   C-NEW-2: ScreeningRecord — partial unique on opportunity-specific checks (opportunity IS NOT NULL)
#   M-2 (validators): Honorarium.amount — add MinValueValidator(0.01)
#   M-2 (validators): HoursLog.hours — add MinValueValidator(0.01) and MaxValueValidator(24)
from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("volunteers", "0002_wave1_fixes"),
    ]

    operations = [  # noqa: RUF012
        # C-NEW-2: ScreeningRecord — partial unique on opportunity-specific checks.
        # Prevents a coordinator from creating a second VSC record for the same
        # volunteer+opportunity and overriding a prior verified_clear=False result.
        migrations.AddConstraint(
            model_name="screeningrecord",
            constraint=models.UniqueConstraint(
                fields=["volunteer", "check_type", "opportunity"],
                condition=models.Q(opportunity__isnull=False),
                name="vol_screen_unique_vol_type_with_opp",
            ),
        ),
        # Honorarium.amount — add MinValueValidator(0.01) to migration state.
        # Ensures constraint tests work on SQLite (which doesn't enforce DB-level
        # CHECK constraints at the application layer).
        migrations.AlterField(
            model_name="honorarium",
            name="amount",
            field=models.DecimalField(
                decimal_places=2,
                max_digits=8,
                validators=[django.core.validators.MinValueValidator(Decimal("0.01"))],
                verbose_name="Amount (CAD)",
            ),
        ),
        # HoursLog.hours — add MinValueValidator(0.01) and MaxValueValidator(24).
        migrations.AlterField(
            model_name="hourslog",
            name="hours",
            field=models.DecimalField(
                decimal_places=2,
                max_digits=6,
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0.01")),
                    django.core.validators.MaxValueValidator(Decimal("24")),
                ],
                verbose_name="Hours",
                help_text="Hours volunteered (0.01 – 24.00).",  # noqa: RUF001
            ),
        ),
    ]
