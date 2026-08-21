from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("payments", "0043_executionintent_submit_admission")]

    operations = [
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
