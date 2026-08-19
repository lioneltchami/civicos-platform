from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [("payments", "0033_alter_batchlease_created_at_alter_batchlease_id_and_more")]

    operations = [
        migrations.CreateModel(
            name="ProviderRegistration",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Created at")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Updated at")),
                ("tenant_id", models.CharField(db_index=True, max_length=100)),
                ("operation", models.CharField(max_length=30)),
                ("provider_name", models.CharField(max_length=80)),
                ("configuration_version", models.CharField(max_length=80)),
                ("configuration", models.JSONField(default=dict)),
                ("active", models.BooleanField(db_index=True, default=True)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["tenant_id", "operation", "active"],
                        name="gs_provider_reg_lookup_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=["tenant_id", "operation"],
                        name="gs_provider_registration_scope_uniq",
                    )
                ],
            },
        ),
        migrations.AddField(
            model_name="paymentattempt",
            name="provider_registration",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="attempts",
                to="payments.providerregistration",
            ),
        ),
        migrations.AddField(
            model_name="paymentattempt",
            name="claim_token",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="paymentattempt",
            name="claim_expires_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="paymentattempt",
            name="claim_generation",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="paymentattempt",
            name="tenant_id",
            field=models.CharField(db_index=True, max_length=100),
        ),
    ]
