from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0036_payment_command_boundary")]

    operations = [
        migrations.AddField(
            model_name="providerregistration",
            name="factory_key",
            field=models.CharField(db_index=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="providerregistration",
            name="schema_version",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="providerregistration",
            name="active_from",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="providerregistration",
            name="active_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="providerregistration",
            name="audit_metadata",
            field=models.JSONField(default=dict),
        ),
    ]
