import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0026_govstackregisteredbb_allowed_platform_tenant_ids_and_more")]  # noqa: RUF012
    operations = [  # noqa: RUF012
        migrations.CreateModel(
            name="PaymentAttempt",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("tenant_id", models.CharField(blank=True, db_index=True, max_length=100)),
                ("request_id", models.CharField(db_index=True, max_length=100)),
                ("operation", models.CharField(default="g2p", max_length=30)),
                ("correlation_id", models.CharField(blank=True, db_index=True, max_length=100)),
                ("source_bb_id", models.CharField(blank=True, max_length=50)),
                (
                    "provider_attempt_id",
                    models.CharField(blank=True, db_index=True, max_length=100),
                ),
                (
                    "external_transaction_id",
                    models.CharField(blank=True, db_index=True, max_length=100),
                ),
                (
                    "amount",
                    models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
                ),
                ("currency", models.CharField(blank=True, max_length=3)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("retryable", "Retryable"),
                            ("uncertain", "Uncertain"),
                            ("settled", "Settled"),
                            ("rejected", "Rejected"),
                            ("review", "Review"),
                            ("dead_letter", "Dead Letter"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("failure_code", models.CharField(blank=True, max_length=50)),
                ("failure_category", models.CharField(blank=True, max_length=30)),
                ("retryable", models.BooleanField(default=False)),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("next_retry_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, max_length=255)),
                ("payload_fingerprint", models.CharField(max_length=64)),
                ("version", models.PositiveIntegerField(default=1)),
            ],
            options={
                "indexes": [
                    models.Index(fields=["status", "next_retry_at"], name="gs_attempt_due_idx")
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=["tenant_id", "operation", "request_id"],
                        name="gs_attempt_scope_request_uniq",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="CallbackDelivery",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("callback_url", models.URLField(max_length=500)),
                ("payload", models.JSONField(default=dict)),
                ("payload_hash", models.CharField(max_length=64)),
                ("status", models.CharField(default="pending", db_index=True, max_length=20)),
                ("delivery_count", models.PositiveIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("last_http_status", models.PositiveIntegerField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, max_length=255)),
                (
                    "attempt",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="callbacks",
                        to="payments.paymentattempt",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=["attempt", "payload_hash"], name="gs_callback_attempt_payload_uniq"
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="PaymentReconciliation",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("provider_status", models.CharField(blank=True, max_length=30)),
                ("internal_status", models.CharField(max_length=30)),
                ("source_bb_status", models.CharField(blank=True, max_length=30)),
                ("status", models.CharField(db_index=True, default="unknown", max_length=20)),
                ("external_transaction_id", models.CharField(blank=True, max_length=100)),
                ("resolution_note", models.CharField(blank=True, max_length=255)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                (
                    "attempt",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reconciliations",
                        to="payments.paymentattempt",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["status", "created_at"], name="gs_recon_status_created_idx"
                    )
                ]
            },
        ),
    ]
