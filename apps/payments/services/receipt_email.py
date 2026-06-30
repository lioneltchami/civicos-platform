"""
Donation receipt email delivery.

Security invariants:
- donor_email NEVER written to any log line.
- donor_name NEVER in subject line or logs.
- PDF attachment filename uses serial_number only — never donor name.
- Only serial_number and error TYPE are logged.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string

logger = logging.getLogger("apps.payments.receipt_email")


def send_receipt_email(receipt, pdf_bytes: bytes) -> bool:
    """
    Send the official donation receipt PDF to the donor.

    Returns True on success, False on failure.
    Logs only serial_number and error type — never donor email or name.
    """
    try:
        donor_email = _get_donor_email(receipt)
        if not donor_email:
            logger.warning(
                "payments.receipt_email.no_email serial=%s",
                receipt.serial_number,
            )
            return False

        from_email = getattr(settings, "RECEIPT_FROM_EMAIL", settings.DEFAULT_FROM_EMAIL)
        subject = (
            f"Official Donation Receipt — {receipt.serial_number} — "
            f"{receipt.charity_legal_name}"
        )
        attachment_filename = f"receipt-{receipt.serial_number}.pdf"

        body_context = {
            "receipt": receipt,
            "serial_number": receipt.serial_number,
            "charity_legal_name": receipt.charity_legal_name,
            "eligible_amount": receipt.eligible_amount,
            "donation_date": receipt.donation_date,
            "receipt_date": receipt.receipt_date,
            "is_annual_consolidated": receipt.is_annual_consolidated,
        }
        body_html = render_to_string("payments/receipt_email.html", body_context)

        email = EmailMessage(
            subject=subject,
            body=body_html,
            from_email=from_email,
            to=[donor_email],
        )
        email.content_subtype = "html"
        email.attach(
            attachment_filename,
            pdf_bytes,
            "application/pdf",
        )
        email.send(fail_silently=False)

        logger.info(
            "payments.receipt_email.sent serial=%s",
            receipt.serial_number,
        )
        return True

    except Exception as exc:
        logger.error(
            "payments.receipt_email.error serial=%s error_type=%s",
            receipt.serial_number,
            type(exc).__name__,
        )
        return False


def _get_donor_email(receipt) -> str:
    """
    Extract the donor email address from the receipt's related donation.

    Checks the donor User's email field.
    Never logs the email address.
    """
    try:
        donation = receipt.donation
        # donation.donor is a FK to AUTH_USER_MODEL which has an email field
        donor = donation.donor
        return donor.email or ""
    except Exception:
        return ""
