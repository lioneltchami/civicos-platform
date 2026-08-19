from django.db import migrations, models
import django.db.models.deletion
import uuid

class Migration(migrations.Migration):
    dependencies = [("payments", "0029_alter_callbackdelivery_created_at_and_more")]
    operations = [
        migrations.CreateModel(
            name="ProviderObservation",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("tenant_id", models.CharField(db_index=True, max_length=100)),
                ("observation_kind", models.CharField(max_length=20)),
                ("observation_id", models.CharField(max_length=160)),
                ("provider_transaction_id", models.CharField(blank=True, max_length=100)),
                ("event_id", models.CharField(blank=True, max_length=160)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("currency", models.CharField(max_length=3)),
                ("outcome", models.CharField(max_length=20)),
                ("verified", models.BooleanField(default=False)),
                ("verification_method", models.CharField(blank=True, max_length=80)),
                ("binding_hash", models.CharField(max_length=64)),
                ("accepted_finality", models.BooleanField(db_index=True, default=False)),
                ("metadata", models.JSONField(default=dict)),
                ("attempt", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="observations", to="payments.paymentattempt")),
            ],
            options={"indexes": [models.Index(fields=["tenant_id", "attempt", "created_at"], name="gs_obs_tenant_attempt_idx")], "constraints": [models.UniqueConstraint(fields=["observation_kind", "observation_id"], name="gs_observation_kind_id_uniq"), models.UniqueConstraint(condition=models.Q(("accepted_finality", True)), fields=["attempt", "accepted_finality"], name="gs_one_accepted_finality")]},
        )
    ]
