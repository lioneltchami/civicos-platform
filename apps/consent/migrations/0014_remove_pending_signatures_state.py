"""
Migration 0014 — Remove legacy 'pending_signatures' state from ConsentRecord.

The GovStack Consent BB spec defines exactly four states:
  unsigned → pending → signed → revoked

'pending_signatures' was a CivicOS legacy variant of 'pending'.  This
migration converts any lingering rows and adds a DB-level constraint.
"""
from django.db import migrations, models


def convert_pending_signatures(apps, schema_editor):
    """Convert any legacy 'pending_signatures' rows to 'pending'."""
    ConsentRecord = apps.get_model("consent", "ConsentRecord")
    ConsentRecord.objects.filter(state="pending_signatures").update(state="pending")


class Migration(migrations.Migration):

    dependencies = [
        ("consent", "0013_rotate_webhook_secrets"),
    ]

    operations = [
        migrations.RunPython(
            convert_pending_signatures,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="consentrecord",
            name="state",
            field=models.CharField(
                max_length=32,
                choices=[
                    ("unsigned", "Unsigned"),
                    ("pending", "Pending"),
                    ("signed", "Signed"),
                    ("revoked", "Revoked"),
                ],
                default="unsigned",
            ),
        ),
        migrations.AddConstraint(
            model_name="consentrecord",
            constraint=models.CheckConstraint(
                condition=models.Q(state__in=["unsigned", "pending", "signed", "revoked"]),
                name="consent_record_state_valid",
                violation_error_message="state must be one of: unsigned, pending, signed, revoked",
            ),
        ),
    ]
