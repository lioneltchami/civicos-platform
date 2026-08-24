from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("appointments", "0004_wave2_slot"),
    ]

    operations = [  # noqa: RUF012
        migrations.AddConstraint(
            model_name="availabilitytemplate",
            constraint=models.CheckConstraint(
                condition=models.Q(day_of_week__gte=1) & models.Q(day_of_week__lte=7),
                name="appt_avail_dow_1_to_7",
            ),
        ),
        migrations.AddConstraint(
            model_name="slot",
            constraint=models.CheckConstraint(
                condition=models.Q(capacity__gte=1),
                name="appt_slot_capacity_gte_1",
            ),
        ),
    ]
