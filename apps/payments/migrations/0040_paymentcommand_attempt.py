import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0039_prepayment_execution")]

    operations = [
        migrations.AddField(
            model_name="paymentcommand",
            name="attempt",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="payment_command",
                to="payments.paymentattempt",
            ),
        ),
    ]
