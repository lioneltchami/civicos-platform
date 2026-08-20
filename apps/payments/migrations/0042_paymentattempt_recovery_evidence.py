from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0041_paymentcommandoutbox_acknowledgement")]

    operations = [
        migrations.AddField(
            model_name="paymentattempt",
            name="recovery_evidence",
            field=models.JSONField(default=dict),
        ),
    ]
