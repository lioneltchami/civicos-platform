from django.db import migrations, models
import django.db.models.deletion
import uuid

class Migration(migrations.Migration):
    dependencies = [("payments", "0031_alter_providerobservation_created_at_and_more")]
    operations = [
        migrations.CreateModel(
            name="IdempotencyLedger",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("tenant_id", models.CharField(max_length=100)), ("method", models.CharField(max_length=10)),
                ("path", models.CharField(max_length=255)), ("key", models.CharField(max_length=255)),
                ("fingerprint", models.CharField(max_length=64)), ("state", models.CharField(choices=[("in_progress", "In progress"), ("complete", "Complete")], default="in_progress", max_length=20)),
                ("status_code", models.PositiveSmallIntegerField(blank=True, null=True)), ("body", models.JSONField(default=dict)), ("headers", models.JSONField(default=dict)), ("completed_at", models.DateTimeField(blank=True, null=True)),
            ], options={"indexes": [models.Index(fields=["tenant_id", "created_at"], name="gs_idem_tenant_created_idx")], "constraints": [models.UniqueConstraint(fields=["tenant_id", "method", "path", "key"], name="gs_idem_tenant_method_path_key")]},
        ),
        migrations.CreateModel(
            name="BatchLease",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)), ("owner_token", models.CharField(max_length=128)), ("generation", models.PositiveIntegerField(default=1)), ("expires_at", models.DateTimeField(db_index=True)),
                ("batch", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="runtime_lease", to="payments.bulkpaymentbatch")),
            ], options={"indexes": [models.Index(fields=["expires_at", "generation"], name="gs_batch_lease_due_idx")]},
        ),
    ]
