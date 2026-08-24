# Generated manually — GAP-4: GovStack BB whitelist table.
#
# Adds one new model:
#   - GovStackRegisteredBB: whitelist of Building Blocks authorised to call
#     this BB's G2P endpoints.  Used by IsTrustedSourceBB.has_permission()
#     when GOVSTACK_REQUIRE_REGISTERED_BB=True.
#
# This migration does NOT modify any existing model or column.
# It is safe to run against live databases — it only adds a new table.

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("payments", "0020_govstack_p2g_models"),
    ]

    operations = [  # noqa: RUF012
        migrations.CreateModel(
            name="GovStackRegisteredBB",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name="Created at",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                        verbose_name="Updated at",
                    ),
                ),
                (
                    "bb_id",
                    models.CharField(
                        help_text=(
                            "Must match the X-Registering-Institution-ID header value "
                            "sent by the BB. 1–20 alphanumeric or hyphen characters. "  # noqa: RUF001
                            "Case-sensitive."
                        ),
                        max_length=20,
                        unique=True,
                        validators=[
                            django.core.validators.RegexValidator(
                                message="BB ID must be 1–20 alphanumeric or hyphen characters.",  # noqa: RUF001
                                regex="^[a-zA-Z0-9\\-]{1,20}$",
                            )
                        ],
                        verbose_name="BB Identifier",
                    ),
                ),
                (
                    "description",
                    models.TextField(
                        blank=True,
                        help_text="Human-readable description of this Building Block (optional).",
                        verbose_name="Description",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        db_index=True,
                        default=True,
                        help_text=(
                            "Inactive BBs are rejected by IsTrustedSourceBB even if their "
                            "bb_id is present in the table. Use this to suspend access "
                            "without deleting records."
                        ),
                        verbose_name="Active",
                    ),
                ),
            ],
            options={
                "verbose_name": "GovStack Registered BB",
                "verbose_name_plural": "GovStack Registered BBs",
                "ordering": ["bb_id"],
            },
        ),
    ]
