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


def _clean_header(value: str) -> str:
    """Remove newline characters to prevent MIME header injection.

    Admin-supplied strings such as charity_legal_name must not contain CR or LF
    before being embedded in email headers (Subject, From, Reply-To).  Django's
    EmailMessage raises BadHeaderError on newlines, but proactive sanitisation
    is the correct defence: we replace rather than reject so that a stray
    newline in a charity name does not silently drop the receipt email.
    """
    return value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").strip()


def send_receipt_email(receipt, pdf_bytes: bytes) -> bool:
    """
    Send the official donation receipt PDF to the donor.

    Returns True on success.
    Returns False only when there is no donor email address on file (not an
    error condition that should be retried — the receipt was intentionally
    created without an email).

    Raises on any send/transport failure so Celery can retry the task.
    Logs only serial_number and error type — never donor email or name.
    """
    donor_email = _get_donor_email(receipt)
    if not donor_email:
        logger.warning(
            "payments.receipt_email.no_email serial=%s",
            receipt.serial_number,
        )
        return False

    from_email = getattr(settings, "RECEIPT_FROM_EMAIL", settings.DEFAULT_FROM_EMAIL)
    # M-F fix: sanitise charity_legal_name before embedding in the Subject header
    # to prevent MIME header injection via CR/LF sequences.
    subject = (
        f"Official Donation Receipt — {receipt.serial_number} — "
        f"{_clean_header(receipt.charity_legal_name)}"
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

    try:
        email.send(fail_silently=False)
    except Exception as exc:
        logger.error(
            "payments.receipt_email.send_failed serial=%s error_type=%s",
            receipt.serial_number,
            type(exc).__name__,
            # NO exc message — may contain SMTP auth details or donor address
        )
        raise  # Re-raise so Celery task can retry

    logger.info(
        "payments.receipt_email.sent serial=%s",
        receipt.serial_number,
    )
    return True


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
    except Exception as exc:
        logger.warning(
            "payments.receipt_email.donor_email_lookup_failed "
            "receipt_pk=%s exc_type=%s",
            receipt.pk,
            type(exc).__name__,
            # NOTE: do NOT log exc message or str(exc) — may contain PII
        )
        return ""
