"""
Add GATEWAY_MANUAL ("manual") to PaymentIntent.gateway choices and
PURPOSE_HONORARIUM ("honorarium") to PaymentIntent.purpose choices.

Backward-compatible: existing rows are unchanged.
Used for offline/manual honorarium payments that bypass the Stripe gateway.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("payments", "0010_paymentintent_report_permissions"),
    ]

    operations = [  # noqa: RUF012
        migrations.AlterField(
            model_name="paymentintent",
            name="gateway",
            field=models.CharField(
                choices=[
                    ("stripe", "Stripe"),
                    ("moneris", "Moneris"),
                    ("manual", "Manual / Offline"),
                ],
                default="stripe",
                max_length=20,
                verbose_name="Payment Gateway",
            ),
        ),
        migrations.AlterField(
            model_name="paymentintent",
            name="purpose",
            field=models.CharField(
                choices=[
                    ("service_fee", "Service Fee"),
                    ("donation", "Donation"),
                    ("fine", "Fine / Penalty"),
                    ("honorarium", "Volunteer Honorarium"),
                ],
                max_length=30,
                verbose_name="Purpose",
            ),
        ),
    ]
