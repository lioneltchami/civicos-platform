from django.db import migrations


class Migration(migrations.Migration):
    """
    Phase 3 migration — runs AFTER the management command backfill that populates
    Certification.document_v2 for all existing records.  Safe to drop the legacy
    FileField because document_v2 (FK to documents.Document) is now the canonical
    reference.
    """

    dependencies = [  # noqa: RUF012
        ("volunteers", "0009_add_screening_vsc_confirmation_doc"),
    ]

    operations = [  # noqa: RUF012
        migrations.RemoveField(
            model_name="certification",
            name="document",
        ),
    ]
