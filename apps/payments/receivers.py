"""
Payment BB signal receivers.

Registered in PaymentsConfig.ready() via:
    import apps.payments.receivers  # noqa: F401

Wave 4 Track B: donation receipt creation on donation_completed signal.

Security invariants:
- donor_email NEVER logged.
- donor_name NEVER logged.
- Only serial_number, donation_pk, and error TYPE are logged.
- Exceptions are caught and logged — signal receivers must not crash callers.
"""
from __future__ import annotations

import logging

from django.db import transaction

logger = logging.getLogger("apps.payments.receivers")


def on_donation_completed(sender, donation, payment, **kwargs):
    """
    Fire when a donation payment succeeds (donation_completed signal).

    Creates an OfficialDonationReceipt and queues PDF generation + email delivery.

    Conditions for issuing a receipt:
    1. donation.eligible_amount > 0  (CRA requires non-zero eligible gift)
    2. An active CharitySettings row exists (needed for CRA fields)

    On any exception: logs error TYPE + donation_pk, does NOT re-raise.
    Signal receivers must never crash callers.
    """
    from decimal import Decimal

    from django.utils import timezone

    from apps.payments.models import CharitySettings, OfficialDonationReceipt
    from apps.payments.tasks_receipts import generate_and_send_receipt

    try:
        # Guard 1: must have an eligible gift amount
        if donation.eligible_amount <= Decimal("0.00"):
            logger.info(
                "payments.receiver.receipt_skipped_ineligible donation_pk=%s",
                str(donation.pk),
            )
            return

        # Guard 2: must have active charity CRA configuration
        try:
            charity = CharitySettings.objects.get(is_active=True)
        except CharitySettings.DoesNotExist:
            logger.warning(
                "payments.receiver.no_active_charity donation_pk=%s",
                str(donation.pk),
            )
            return

        # Idempotency: never issue a second receipt for the same donation
        if OfficialDonationReceipt.objects.filter(
            donation=donation,
        ).exists():
            logger.info(
                "payments.receiver.receipt_already_exists donation_pk=%s",
                str(donation.pk),
            )
            return

        # Build charity address snapshot
        charity_address = (
            f"{charity.charity_address_line1}, "
            f"{charity.charity_city}, "
            f"{charity.charity_province} "
            f"{charity.charity_postal_code}"
        )

        # Parse individual donor address fields from snapshot
        from apps.payments.tasks_receipts import _parse_donor_address
        donor_address_parts = _parse_donor_address(donation.donor_address_snapshot)

        with transaction.atomic():
            receipt = OfficialDonationReceipt(
                donation=donation,
                status=OfficialDonationReceipt.RECEIPT_STATUS_ISSUED,
                # CRA donor snapshot fields
                donor_legal_name=donation.donor_name_snapshot,
                donor_address_line1=donor_address_parts.get(
                    "line1", donation.donor_address_snapshot[:255]
                ),
                donor_city=donor_address_parts.get("city", ""),
                donor_province=donor_address_parts.get("province", ""),
                donor_postal_code=donor_address_parts.get("postal_code", ""),
                # CRA date fields
                donation_date=donation.created_at.date(),
                receipt_date=timezone.now().date(),
                # CRA amount fields
                eligible_amount=donation.eligible_amount,
                advantage_amount=donation.advantage_amount,
                advantage_description=donation.advantage_description,
                # CRA charity snapshot fields
                charity_legal_name=charity.charity_legal_name,
                charity_registration_number=charity.charity_registration_number,
                charity_address=charity_address,
                place_of_issue=charity.place_of_issue,
                authorized_signatory_name=charity.authorized_signatory_name,
                authorized_signatory_title=charity.authorized_signatory_title,
                is_annual_consolidated=False,
            )
            receipt.save()
            receipt_pk_str = str(receipt.pk)
            transaction.on_commit(
                lambda: generate_and_send_receipt.delay(receipt_pk_str)
            )

        logger.info(
            "payments.receiver.receipt_created serial=%s donation_pk=%s",
            receipt.serial_number,
            str(donation.pk),
        )

    except Exception as exc:
        # Never crash the caller — log type and donation pk only
        logger.error(
            "payments.receiver.receipt_error donation_pk=%s error_type=%s",
            str(donation.pk),
            type(exc).__name__,
        )


def on_receipt_issued(sender, receipt, donation, **kwargs):
    """
    Hook fired when an OfficialDonationReceipt is issued (receipt_issued signal).

    Currently logs the serial number for audit trail.
    Reserved for future integrations (e.g. CRA e-filing, analytics).
    """
    logger.info(
        "payments.receiver.receipt_issued serial=%s",
        receipt.serial_number,
    )
