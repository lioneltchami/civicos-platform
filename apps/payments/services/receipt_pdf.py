"""
CRA-compliant donation receipt PDF generation.

Security invariants:
- pdf_path NEVER written to any log line.
- donor_name NEVER written to any log line.
- Only serial_number and error TYPE are logged.
- PDF bytes are returned in-memory and stored via ORM — never arbitrary FS path.

CRA mandatory fields (Income Tax Act s.118.1 / IT-110R3 / T4033):
 1. "Official receipt for income tax purposes" (bilingual)
 2. Charity legal name
 3. Charity address
 4. CRA registration number (123456789 RR 0001)
 5. Receipt serial number (unique)
 6. Place of issue
 7. Date receipt was issued
 8. Date donation was made
 9. Donor legal name
10. Donor full address
11. Amount of donation (CAD)
12. Eligible amount of the gift
13. Advantage amount received
14. Description of advantage (if advantage_amount > 0)
15. Signature of authorized official + title
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.template.loader import render_to_string
from django.utils import timezone

logger = logging.getLogger("apps.payments.receipt_pdf")


def generate_receipt_pdf(receipt) -> bytes:
    """
    Render an OfficialDonationReceipt to PDF bytes using WeasyPrint.

    Returns raw PDF bytes. Caller is responsible for storage via save_receipt_pdf().
    Logs only serial_number and error type — never donor name or pdf_path.
    """
    from weasyprint import HTML  # Local import — heavy library, avoid module-level cost

    try:
        context = _build_receipt_context(receipt)
        html_string = render_to_string("payments/receipt_pdf.html", context)
        pdf_bytes = HTML(string=html_string, base_url=None).write_pdf()
        logger.info(
            "payments.receipt_pdf.generated serial=%s",
            receipt.serial_number,
        )
        return pdf_bytes
    except Exception as exc:
        logger.error(
            "payments.receipt_pdf.error serial=%s error_type=%s",
            receipt.serial_number,
            type(exc).__name__,
        )
        raise


def _build_receipt_context(receipt) -> dict:
    """
    Build template context containing all 14 CRA mandatory fields.
    Uses real field names from OfficialDonationReceipt model introspection.
    """
    has_advantage = (
        receipt.advantage_amount is not None
        and receipt.advantage_amount > Decimal("0.00")
    )

    # Total donation amount = eligible_amount + advantage_amount
    total_donation_amount = receipt.eligible_amount + receipt.advantage_amount

    return {
        # CRA field 1 — bilingual title (handled in template)
        # CRA field 2 — charity legal name
        "charity_legal_name": receipt.charity_legal_name,
        # CRA field 3 — charity address
        "charity_address": receipt.charity_address,
        # CRA field 4 — CRA registration number
        "charity_registration_number": receipt.charity_registration_number,
        # CRA field 5 — receipt serial number
        "serial_number": receipt.serial_number,
        # CRA field 6 — place of issue
        "place_of_issue": receipt.place_of_issue,
        # CRA field 7 — date receipt was issued
        "receipt_date": receipt.receipt_date,
        # CRA field 8 — date donation was made
        "donation_date": receipt.donation_date,
        # CRA field 9 — donor legal name (snapshot)
        "donor_legal_name": receipt.donor_legal_name,
        # CRA field 10 — donor full address (assembled from snapshot fields)
        "donor_address_line1": receipt.donor_address_line1,
        "donor_city": receipt.donor_city,
        "donor_province": receipt.donor_province,
        "donor_postal_code": receipt.donor_postal_code,
        # CRA field 11 — total donation amount (eligible + advantage)
        "total_donation_amount": total_donation_amount,
        # CRA field 12 — eligible amount of the gift
        "eligible_amount": receipt.eligible_amount,
        # CRA field 13 — advantage amount received
        "advantage_amount": receipt.advantage_amount,
        "has_advantage": has_advantage,
        # CRA field 14 — description of advantage
        "advantage_description": receipt.advantage_description,
        # CRA field 15 — authorized signatory
        "authorized_signatory_name": receipt.authorized_signatory_name,
        "authorized_signatory_title": receipt.authorized_signatory_title,
        # Receipt metadata
        "receipt": receipt,
        "is_annual_consolidated": receipt.is_annual_consolidated,
    }


def save_receipt_pdf(receipt, pdf_bytes: bytes) -> str:
    """
    Save PDF bytes to the Documents BB and link them to the receipt.

    Creates a Document record (category=donation-receipt-pdf, scan_status=ACTIVE)
    and sets receipt.document via _base_manager to bypass the append-only guard.

    Idempotent: if receipt.document_id is already set, skips creation and returns
    the existing document's storage key.

    PIPEDA: only logs serial_number, never the storage key, storage path, or donor PII.
    Returns the storage key string (internal — never expose to callers beyond this service).
    """
    import uuid as _uuid
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage
    from apps.documents.models import Document, DocumentCategory
    from apps.documents.services.retention import schedule_expiry

    serial = receipt.serial_number

    if receipt.document_id:
        # Idempotency: document already linked — nothing to do.
        logger.info(
            "payments.receipt_pdf.already_saved serial=%s",
            serial,
        )
        return receipt.document._storage_key  # type: ignore[union-attr]

    # Get or create the CRA receipt document category (seeded by Wave 6 migration).
    category = DocumentCategory.objects.filter(slug="donation-receipt-pdf").first()
    if category is None:
        raise ValueError(
            "DocumentCategory 'donation-receipt-pdf' is not seeded. "
            "Run apps/documents/migrations/0007_seed_document_categories.py first."
        )

    # Build a deterministic storage key from serial number so retries are idempotent.
    # Using serial number only ensures that if the file write succeeds but the DB
    # update fails, a retry will find and reuse the existing file rather than
    # creating an orphan at a new random path.
    storage_key = f"documents/active/receipts/{serial}/receipt.bin"

    # Storage-level idempotency: if a prior attempt wrote the file but the DB update
    # failed, reuse the file rather than creating a duplicate.
    saved_key = None
    if default_storage.exists(storage_key):
        try:
            with default_storage.open(storage_key, "rb") as _fh:
                _header = _fh.read(4)
            if _header == b"%PDF":
                logger.info(
                    "payments.save_receipt_pdf.reusing_existing_file serial=%s",
                    serial,
                )
                saved_key = storage_key
            else:
                logger.warning(
                    "payments.receipt_pdf.corrupt_existing_file_regenerating serial=%s",
                    serial,
                )
                default_storage.delete(storage_key)
        except (OSError, IOError):
            logger.warning(
                "payments.receipt_pdf.unreadable_file_regenerating serial=%s",
                serial,
            )

    if saved_key is None:
        try:
            default_storage.save(storage_key, ContentFile(pdf_bytes))
            saved_key = storage_key
        except Exception as exc:
            logger.error(
                "payments.receipt_pdf.save_error serial=%s error_type=%s",
                serial,
                type(exc).__name__,
            )
            raise
        logger.info(
            "payments.save_receipt_pdf.saved serial=%s",
            serial,
        )

    # Create the Document BB record.
    donor = getattr(receipt, "donation", None)
    uploaded_by = getattr(donor, "donor", None) if donor else None
    if uploaded_by is None:
        raise ValueError(
            f"save_receipt_pdf: could not resolve donation.donor for receipt serial={serial}. "
            "Ensure receipt is fetched with select_related('donation__donor')."
        )

    doc = Document.objects.create(
        category=category,
        uploaded_by=uploaded_by,
        # original_filename: uses serial number only — NEVER donor name or PII.
        original_filename=f"receipt-{serial}.pdf",
        _storage_key=saved_key,
        mime_type="application/pdf",
        size_bytes=len(pdf_bytes),
        scan_status=Document.ScanStatus.ACTIVE,
        version_number=1,
        is_latest_version=True,
        security_classification="protected_b",
    )
    schedule_expiry(document=doc)

    # Update the DB record outside the storage write — safe to retry independently.
    # Use _base_manager to bypass append-only guard (document is a mutable field).
    receipt.__class__._base_manager.filter(pk=receipt.pk).update(
        document=doc,
        updated_at=timezone.now(),
    )
    receipt.document = doc
    return saved_key
