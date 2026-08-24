from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0034_item02_core_runtime")]  # noqa: RUF012
    operations = [  # noqa: RUF012
        migrations.AddField(
            model_name="paymentattempt",
            name="claim_heartbeat_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="paymentattempt",
            name="submission_intent",
            field=models.JSONField(default=dict),
        ),
    ]
