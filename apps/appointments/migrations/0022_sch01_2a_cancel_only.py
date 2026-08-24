from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("appointments", "0021_sch01_1_core_admission")]  # noqa: RUF012

    operations = [  # noqa: RUF012
        migrations.AddField(
            model_name="govstackalertschedule",
            name="delivery_admittable",
            field=models.BooleanField(
                default=True,
                help_text="False after durable cancellation; blocks new recipient/outbox admission.",
                verbose_name="Delivery admittable",
            ),
        ),
        migrations.AddField(
            model_name="scheduleroutbox",
            name="cancelled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
