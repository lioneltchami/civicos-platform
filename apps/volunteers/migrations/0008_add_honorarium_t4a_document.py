import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("volunteers", "0007_add_certification_document_fk"),
    ]

    operations = [
        migrations.AddField(
            model_name="honorarium",
            name="t4a_document",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="honorarium_t4a",
                to="documents.document",
            ),
        ),
    ]
