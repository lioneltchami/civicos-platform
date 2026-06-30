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


def save_receipt_pdf(receipt, pdf_bytes: bytes) -> None:
    """
    Save PDF bytes to the receipt's pdf_path CharField via Django file storage.

    Uses ContentFile to avoid writing to arbitrary filesystem paths.
    Only saves if pdf_path is currently empty (idempotency guard).
    Logs only serial_number — never pdf_path or donor name.
    """
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage

    if receipt.pdf_path:
        logger.info(
            "payments.receipt_pdf.already_saved serial=%s",
            receipt.serial_number,
        )
        return

    filename = f"receipts/{receipt.serial_number}.pdf"

    try:
        saved_path = default_storage.save(filename, ContentFile(pdf_bytes))
        # Use _base_manager to bypass append-only guard on pdf_path (mutable field)
        receipt.__class__._base_manager.filter(pk=receipt.pk).update(
            pdf_path=saved_path
        )
        receipt.pdf_path = saved_path
        logger.info(
            "payments.receipt_pdf.saved serial=%s",
            receipt.serial_number,
        )
    except Exception as exc:
        logger.error(
            "payments.receipt_pdf.save_error serial=%s error_type=%s",
            receipt.serial_number,
            type(exc).__name__,
        )
        raise
