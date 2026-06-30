"""
Wave 9 model fixes — single authoritative migration.

Changes:
- OfficialDonationReceipt.email_sent: BooleanField(default=False) dedup guard
- PaymentIntent.reference: collision-safe 16-char hex via secrets.token_hex(8)
- TenantPaymentConfig.webhook_endpoint_secret: EncryptedCharField (Fernet at rest)

NOTE: Existing plaintext webhook_endpoint_secret values will fail decryption after
deploy. Re-enter all webhook_endpoint_secret values in Django admin after migrating.
"""
import apps.payments.models
from django.db import migrations, models

# C1 FIX: varchar→bytea cannot be cast automatically on PostgreSQL.
# SeparateDatabaseAndState runs custom SQL with USING ''::bytea to safely
# convert the column type. Existing plaintext values are cleared — re-enter
# all webhook_endpoint_secret values in Django admin after running migrate.
#
# The database operation uses RunPython (not RunSQL) so it can guard against
# SQLite in CI/test environments — SQLite does not support ALTER COLUMN TYPE.


def _alter_webhook_secret_to_bytea(apps, schema_editor):
    """
    Alter webhook_endpoint_secret from varchar to bytea on PostgreSQL only.
    Clears existing values because they are plaintext and cannot be
    Fernet-decrypted without the original key.
    SQLite (used in CI/tests) does not support ALTER COLUMN TYPE — skip it.
    """
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
        ALTER TABLE payments_tenantpaymentconfig
            ALTER COLUMN webhook_endpoint_secret
            TYPE bytea USING ''::bytea;
        """
    )


def _revert_webhook_secret_to_varchar(apps, schema_editor):
    """Reverse: bytea → varchar(255) on PostgreSQL."""
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
        ALTER TABLE payments_tenantpaymentconfig
            ALTER COLUMN webhook_endpoint_secret
            TYPE varchar(255) USING '';
        """
    )


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0003_add_receipt_unique_issued_per_donation'),
    ]

    operations = [
        migrations.AddField(
            model_name='officialdonationreceipt',
            name='email_sent',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Set to True once the CRA receipt email has been delivered. '
                    'Guards against duplicate delivery on Celery retry races.'
                ),
            ),
        ),
        migrations.AlterField(
            model_name='paymentintent',
            name='reference',
            field=models.CharField(
                db_index=True,
                default=apps.payments.models._generate_payment_reference,
                editable=False,
                help_text='Unique payment reference (16-char hex). Collision-safe: 2^64 address space.',
                max_length=20,
                unique=True,
            ),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name='tenantpaymentconfig',
                    name='webhook_endpoint_secret',
                    field=apps.payments.models.EncryptedCharField(
                        blank=True,
                        editable=True,
                        help_text=(
                            'Stripe webhook signing secret (whsec_...). '
                            'Stored encrypted at rest (Fernet/AES-128-CBC). '
                            'Changing this requires rotating the Stripe webhook signing secret and redeploying.'
                        ),
                        max_length=255,
                        verbose_name='Webhook Endpoint Secret',
                    ),
                ),
            ],
            database_operations=[
                migrations.RunPython(
                    _alter_webhook_secret_to_bytea,
                    reverse_code=_revert_webhook_secret_to_varchar,
                ),
            ],
        ),
    ]
