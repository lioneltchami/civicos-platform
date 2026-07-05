import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0012_alter_webhookevent_gateway"),
        ("documents", "0007_seed_document_categories"),
    ]

    operations = [
        migrations.AddField(
            model_name="officialdonationreceipt",
            name="document",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="donation_receipt",
                to="documents.document",
            ),
        ),
    ]
