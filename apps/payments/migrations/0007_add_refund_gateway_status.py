# Generated 2026-06-30 — H-C fix: add gateway_status to Refund

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0006_alter_payment_card_brand'),
    ]

    operations = [
        migrations.AddField(
            model_name='refund',
            name='gateway_status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('succeeded', 'Succeeded'),
                    ('failed', 'Failed'),
                ],
                db_index=True,
                default='pending',
                max_length=20,
                verbose_name='Gateway Status',
            ),
        ),
    ]
