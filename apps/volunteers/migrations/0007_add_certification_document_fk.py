import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("volunteers", "0006_volunteer_note_body_max_length"),
        ("documents", "0007_seed_document_categories"),
    ]

    operations = [
        migrations.AddField(
            model_name="certification",
            name="document_v2",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="certification_documents",
                to="documents.document",
            ),
        ),
    ]
