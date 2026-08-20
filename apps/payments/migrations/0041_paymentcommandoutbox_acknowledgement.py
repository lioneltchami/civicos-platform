from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0040_paymentcommand_attempt")]

    operations = [
        migrations.AddField(
            model_name="paymentcommandoutbox",
            name="acknowledged_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="paymentcommandoutbox",
            name="acknowledgement_token",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="paymentcommandoutbox",
            name="acknowledgement_result",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.RemoveIndex(
            model_name="paymentcommandoutbox",
            name="payment_outbox_due_idx",
        ),
        migrations.AddIndex(
            model_name="paymentcommandoutbox",
            index=models.Index(
                fields=["acknowledged_at", "created_at"],
                name="payment_outbox_due_idx",
            ),
        ),
    ]
