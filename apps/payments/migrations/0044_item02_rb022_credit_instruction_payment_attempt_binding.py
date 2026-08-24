import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0043_executionintent_submit_admission")]  # noqa: RUF012

    operations = [  # noqa: RUF012
        migrations.AddField(
            model_name="creditinstruction",
            name="payment_attempt",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="credit_instruction",
                to="payments.paymentattempt",
            ),
        ),
    ]
