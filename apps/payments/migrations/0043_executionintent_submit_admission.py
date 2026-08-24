from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0042_paymentattempt_recovery_evidence")]  # noqa: RUF012

    operations = [  # noqa: RUF012
        migrations.AddField(
            model_name="paymentexecutionintent",
            name="submit_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="paymentexecutionintent",
            name="submit_admission_generation",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
