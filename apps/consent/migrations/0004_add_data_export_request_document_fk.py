import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("consent", "0003_protect_citizen_fk"),
        ("documents", "0007_seed_document_categories"),
    ]

    operations = [  # noqa: RUF012
        migrations.AddField(
            model_name="dataexportrequest",
            name="document",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="data_export",
                to="documents.document",
            ),
        ),
    ]
