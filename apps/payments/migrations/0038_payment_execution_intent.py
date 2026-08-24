import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0037_provider_factory_registry")]  # noqa: RUF012

    operations = [  # noqa: RUF012
        migrations.CreateModel(
            name="PaymentExecutionIntent",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, db_index=True, verbose_name="Created at"
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Updated at")),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("scope", models.CharField(db_index=True, max_length=100)),
                ("operation", models.CharField(max_length=30)),
                ("request_identity", models.CharField(max_length=100)),
                ("payload_fingerprint", models.CharField(max_length=64)),
                (
                    "provider_correlation",
                    models.CharField(blank=True, db_index=True, max_length=160),
                ),
                (
                    "state",
                    models.CharField(
                        choices=[("reserved", "Reserved"), ("correlated", "Correlated")],
                        db_index=True,
                        default="reserved",
                        max_length=20,
                    ),
                ),
                (
                    "attempt",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="execution_intent",
                        to="payments.paymentattempt",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("scope", "operation", "request_identity"),
                        name="gs_exec_intent_identity_uniq",
                    )
                ],
                "indexes": [
                    models.Index(
                        fields=("scope", "operation", "created_at"),
                        name="gs_exec_intent_scope_idx",
                    )
                ],
            },
        ),
    ]
