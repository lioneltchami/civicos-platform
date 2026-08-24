import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("volunteers", "0008_add_honorarium_t4a_document"),
    ]

    operations = [  # noqa: RUF012
        migrations.AddField(
            model_name="screeningrecord",
            name="vsc_confirmation_doc",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="screening_vsc_confirmations",
                to="documents.document",
                help_text=(
                    "PIPEDA: stores reference to chain-of-custody confirmation document only. "
                    "Must never reference or store the criminal check result."
                ),
            ),
        ),
    ]
