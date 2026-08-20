import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0038_payment_execution_intent")]

    operations = [
        migrations.CreateModel(
            name="PrepaymentExecution",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("execution_key", models.CharField(max_length=160, unique=True)),
                ("admitted_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "validation_request",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="execution",
                        to="payments.prepaymentvalidationrequest",
                    ),
                ),
            ],
            options={
                "verbose_name": "Prepayment Execution",
                "verbose_name_plural": "Prepayment Executions",
            },
        ),
    ]
