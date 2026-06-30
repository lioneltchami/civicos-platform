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
    ]
