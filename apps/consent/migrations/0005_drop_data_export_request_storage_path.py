from django.db import migrations


class Migration(migrations.Migration):
    """
    Phase 3 migration — runs AFTER the management command backfill that migrates
    existing export archives into documents.Document and sets DataExportRequest.document.
    Safe to drop the legacy storage_path CharField.
    """

    dependencies = [
        ("consent", "0004_add_data_export_request_document_fk"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="dataexportrequest",
            name="storage_path",
        ),
    ]
