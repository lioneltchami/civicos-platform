# Generated migration — adds webhook payload replay log fields to ConsentWebhook.
#
# Three nullable fields are added:
#   last_payload          — JSONField: full body of the most recent delivery
#   last_delivery_at      — DateTimeField: timestamp of most recent delivery
#   last_delivery_status  — CharField(10): "success" | "failed" | null
#
# These fields are written by consent.dispatch_consent_webhook (Celery task)
# on each delivery attempt and exposed by ConfigWebhookPayloadView.
from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("consent", "0015_nullable_download_token"),
    ]

    operations = [  # noqa: RUF012
        migrations.AddField(
            model_name="consentwebhook",
            name="last_payload",
            field=models.JSONField(
                blank=True,
                default=None,
                help_text=(
                    "The full body (event + timestamp + payload) of the most recently "
                    "delivered webhook POST.  Null until the first successful delivery."
                ),
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="consentwebhook",
            name="last_delivery_at",
            field=models.DateTimeField(
                blank=True,
                default=None,
                help_text="Timestamp of the most recent successful webhook delivery.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="consentwebhook",
            name="last_delivery_status",
            field=models.CharField(
                blank=True,
                choices=[("success", "Success"), ("failed", "Failed")],
                default=None,
                help_text="Result of the most recent delivery attempt.",
                max_length=10,
                null=True,
            ),
        ),
    ]
