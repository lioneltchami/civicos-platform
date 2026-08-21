from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("appointments", "0020_scheduler_outbox_publisher_claim")]

    operations = [
        migrations.AddField(
            model_name="govstackalertschedule",
            name="admitted_generation",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="The delivery generation durably admitted by the authoritative scheduler admission service.",
                null=True,
                verbose_name="Admitted generation",
            ),
        ),
        migrations.AddField(
            model_name="govstackalertschedule",
            name="admission_outcome",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "Not admitted"),
                    ("created", "Created"),
                    ("duplicate", "Duplicate"),
                    ("stale_generation", "Stale generation"),
                    ("zero_recipients", "Zero recipients"),
                ],
                default="",
                help_text="Durable outcome of the current authoritative scheduler admission.",
                max_length=32,
                verbose_name="Admission outcome",
            ),
        ),
    ]
