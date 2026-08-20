import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0035_paymentattempt_runtime_claim_fields")]

    operations = [
        migrations.CreateModel(
            name="PaymentCommand",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Created at")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Updated at")),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("tenant_id", models.CharField(db_index=True, max_length=100)),
                ("caller_bb_id", models.CharField(max_length=20)),
                ("operation", models.CharField(max_length=64)),
                ("request_identity", models.CharField(max_length=255)),
                ("fingerprint", models.CharField(max_length=64)),
                ("payload", models.JSONField(default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[("reserved", "Reserved"), ("dispatched", "Dispatched")],
                        default="reserved",
                        max_length=24,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("tenant_id", "operation", "request_identity"),
                        name="payment_command_identity_uniq",
                    )
                ],
                "indexes": [
                    models.Index(
                        fields=("tenant_id", "operation", "created_at"),
                        name="payment_command_scope_idx",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="PaymentCommandOutbox",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Created at")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Updated at")),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("topic", models.CharField(max_length=120)),
                ("payload", models.JSONField(default=dict)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                (
                    "command",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="outbox",
                        to="payments.paymentcommand",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=("published_at", "created_at"),
                        name="payment_outbox_due_idx",
                    )
                ]
            },
        ),
    ]
