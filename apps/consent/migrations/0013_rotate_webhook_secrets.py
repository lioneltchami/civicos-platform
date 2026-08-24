"""
Migration 0013 — Rotate ConsentWebhook secrets after EncryptedCharField migration.

Migration 0012 changed ConsentWebhook.secret_key from CharField (plaintext text)
to BinaryField (Fernet-encrypted bytes).  In PostgreSQL, AlterField casts old
varchar values to bytea — those raw bytes are NOT valid Fernet ciphertext, so
EncryptedCharField.from_db_value() returns "" for every existing row.

This migration generates a new cryptographically-random 256-bit secret for each
ConsentWebhook and stores it properly encrypted.

Production action required: notify each webhook subscriber that their shared
HMAC secret has been rotated and they must retrieve the new value from the API.
"""

import secrets as _secrets

from django.db import migrations, models


def rotate_webhook_secrets(apps, schema_editor) -> None:  # noqa: ANN001
    """Re-encrypt all ConsentWebhook secrets using Fernet."""
    from apps.core.fields import _get_fernet

    fernet = _get_fernet()
    ConsentWebhook = apps.get_model("consent", "ConsentWebhook")

    webhooks = list(ConsentWebhook.objects.all())
    if not webhooks:
        return

    for webhook in webhooks:
        new_secret = _secrets.token_hex(32)  # 256 bits of entropy
        encrypted = fernet.encrypt(new_secret.encode("utf-8"))
        # Use QuerySet.update() to bypass historical model field descriptors
        # and write raw encrypted bytes directly to the BinaryField column.
        ConsentWebhook.objects.filter(pk=webhook.pk).update(secret_key=encrypted)


def noop(apps, schema_editor) -> None:  # noqa: ANN001
    """Cannot restore original plaintext secrets — webhook owners must re-register."""
    pass


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("consent", "0012_codex_audit_fixes"),
    ]

    operations = [  # noqa: RUF012
        # Add index on ConsentWebhook.is_disabled for efficient webhook dispatch queries
        migrations.AlterField(
            model_name="consentwebhook",
            name="is_disabled",
            field=models.BooleanField(
                default=False,
                db_index=True,
                help_text="If True, this webhook will not receive events.",
            ),
        ),
        migrations.RunPython(rotate_webhook_secrets, reverse_code=noop),
    ]
