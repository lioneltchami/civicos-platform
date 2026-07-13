"""
Migration 0012 — Codex audit security and correctness fixes.

1. Composite index (citizen, category, is_current) on ConsentRecord — M-01.
2. Composite index (citizen_id, timestamp) on ConsentAuditEntry — M-01.
3. Fix ConsentAuditEntry CheckConstraint: check= → condition= — M-02 (Django 6.0).
4. ConsentWebhook.secret_key: CharField → BinaryField (EncryptedCharField storage) — C-02.

Production note for (4): existing plaintext secret_key values will not auto-migrate.
After running this migration in production, re-save each ConsentWebhook via:
    python manage.py shell -c "
    from apps.consent.models import ConsentWebhook
    for wh in ConsentWebhook.objects.all():
        wh.save()  # triggers EncryptedCharField.get_prep_value() to encrypt
    "
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("consent", "0011_consent_record_history"),
    ]

    operations = [
        # 1. Composite index on ConsentRecord
        migrations.AddIndex(
            model_name="consentrecord",
            index=models.Index(
                fields=["citizen", "category", "is_current"],
                name="cr_citizen_cat_curr_idx",
            ),
        ),
        # 2. Composite index on ConsentAuditEntry
        migrations.AddIndex(
            model_name="consentauditentry",
            index=models.Index(
                fields=["citizen", "timestamp"],
                name="consent_audit_citizen_ts_idx",
            ),
        ),
        # 3. Drop and re-add CheckConstraint with condition= (Django 6.0 compat)
        migrations.RemoveConstraint(
            model_name="consentauditentry",
            name="consent_audit_valid_action",
        ),
        migrations.AddConstraint(
            model_name="consentauditentry",
            constraint=models.CheckConstraint(
                condition=models.Q(action__in=[
                    "granted", "withdrawn",
                    "export_requested", "export_ready",
                    "export_delivered", "export_expired", "export_failed",
                    "export_downloaded", "export_marked_delivered",
                    "rtbf_requested", "rtbf_completed",
                ]),
                name="consent_audit_valid_action",
            ),
        ),
        # 4. ConsentWebhook.secret_key: CharField → BinaryField (EncryptedCharField)
        migrations.AlterField(
            model_name="consentwebhook",
            name="secret_key",
            field=models.BinaryField(
                editable=True,
                help_text=(
                    "HMAC secret key used to sign payloads (SHA-256). "
                    "Stored Fernet-encrypted at rest; returned in API responses "
                    "as required by GovStack spec."
                ),
            ),
        ),
    ]
