from django.db import migrations


class Migration(migrations.Migration):
    """
    Phase 3 migration — runs AFTER the management command backfill that uploads
    existing PDF files into documents.Document and sets OfficialDonationReceipt.document.
    Safe to drop the legacy pdf_path CharField.
    """

    dependencies = [  # noqa: RUF012
        ("payments", "0013_add_donation_receipt_document_fk"),
    ]

    operations = [  # noqa: RUF012
        migrations.RemoveField(
            model_name="officialdonationreceipt",
            name="pdf_path",
        ),
    ]
