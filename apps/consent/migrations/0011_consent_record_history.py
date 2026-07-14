"""
Migration 0011 — ConsentRecord history (F4 + F17 fixes)

Changes:
  1. Remove unique_together = [("citizen", "category")] from ConsentRecord.
     Multiple rows per citizen/category are now allowed to preserve full
     consent history.
  2. Add is_current BooleanField (default=True) to ConsentRecord.
     All existing rows default to True (safe: there was at most 1 row per
     citizen/category before this migration).
  3. Add "pgp" to ConsentSignature.verification_type choices.
"""
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("consent", "0010_govstack_gap2_fixes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Remove unique_together constraint
        migrations.AlterUniqueTogether(
            name="consentrecord",
            unique_together=set(),
        ),

        # 2. Add is_current BooleanField
        migrations.AddField(
            model_name="consentrecord",
            name="is_current",
            field=models.BooleanField(
                default=True,
                db_index=True,
                help_text=(
                    "True for the most-recent ConsentRecord for this citizen/category. "
                    "Older historical rows have is_current=False."
                ),
            ),
        ),

        # 3. Update ConsentSignature.verification_type to include 'pgp'
        migrations.AlterField(
            model_name="consentsignature",
            name="verification_type",
            field=models.CharField(
                max_length=20,
                choices=[
                    ("string", "String (non-cryptographic)"),
                    ("rs256", "RS256 (RSA + SHA-256)"),
                    ("ed25519", "Ed25519"),
                    ("ps256", "PS256 (RSA-PSS + SHA-256)"),
                    ("pgp", "PGP"),
                ],
                default="string",
                help_text="Cryptographic algorithm used to sign/verify the payload.",
            ),
        ),
    ]
