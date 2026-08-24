from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("appointments", "0019_scheduler_runtime_core")]  # noqa: RUF012

    operations = [  # noqa: RUF012
        migrations.AddField(
            model_name="scheduleroutbox",
            name="publisher_generation",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="scheduleroutbox",
            name="publisher_token",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="scheduleroutbox",
            name="publisher_owner",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="scheduleroutbox",
            name="publisher_lease_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="scheduleroutbox",
            index=models.Index(
                fields=["published_at", "publisher_lease_expires_at"],
                name="appt_sched_pub_lease_idx",
            ),
        ),
    ]
