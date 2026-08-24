"""Migration 0015 — Allow download_token to be null (single-use enforcement)."""

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("consent", "0014_remove_pending_signatures_state"),
    ]

    operations = [  # noqa: RUF012
        migrations.AlterField(
            model_name="dataexportrequest",
            name="download_token",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                null=True,
                blank=True,
                unique=True,
                help_text="Single-use download token. Nulled after first successful delivery.",
            ),
        ),
    ]
