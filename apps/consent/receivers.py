"""
Signal receivers for the Consent & Privacy building block.

These receivers are registered in ConsentConfig.ready() by importing this module.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.template.loader import render_to_string

from apps.consent.signals import consent_withdrawn, export_requested

logger = logging.getLogger(__name__)

User = get_user_model()


@receiver(consent_withdrawn)
def send_withdrawal_confirmation_email(sender, consent_record, request=None, **kwargs) -> None:  # noqa: ANN001, ANN003
    """
    Send a plain-text email to the citizen confirming their consent withdrawal.
    PIPEDA accountability principle — citizens must receive evidence of their rights being exercised.
    """  # noqa: E501
    citizen = consent_record.citizen
    category = consent_record.category
    subject = f"Consent Withdrawn: {category.name_en} — CivicOS"
    try:
        body = render_to_string(
            "email/consent/withdrawal_confirmation.txt",
            {"citizen": citizen, "category": category},
        )
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[citizen.email],
        )
        logger.info("send_withdrawal_confirmation_email: sent confirmation email")
    except Exception as e:
        logger.error("send_withdrawal_confirmation_email: failed: %s", type(e).__name__)


@receiver(export_requested)
def send_export_request_received_email(sender, export_request, request=None, **kwargs) -> None:  # noqa: ANN001, ANN003
    """
    Send acknowledgement email when a PIPEDA data export request is submitted.
    Informs the citizen of the 30-day processing SLA.
    """
    citizen = export_request.citizen
    subject = "Your Data Export Request — CivicOS"
    try:
        body = render_to_string(
            "email/consent/export_request_received.txt",
            {"citizen": citizen, "export_request": export_request},
        )
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[citizen.email],
            fail_silently=True,
        )
        logger.info("send_export_request_received_email: acknowledgement email sent")
    except Exception as e:
        logger.warning("send_export_request_received_email: failed: %s", type(e).__name__)


@receiver(post_save, sender=User)
def bootstrap_required_consents(sender, instance, created, **kwargs) -> None:  # noqa: ANN001, ANN003
    """
    Automatically grant required consent categories for new citizen accounts.

    Required categories (is_required=True) cannot be withdrawn but still need
    full ConsentRecord, ConsentRevision, ConsentAuditEntry, and ConsentSignature
    rows so the GovStack tamper-proof audit chain is complete for every record.

    C-04 fix: calls ConsentService.grant() through the full service layer instead
    of creating records directly with get_or_create(), ensuring the state machine
    (unsigned → signed), ConsentRevision chain, ConsentAuditEntry, and
    ConsentSignature are all created correctly.

    M-05 fix: ConsentService.grant() uses is_current=True in all its lookups,
    eliminating the MultipleObjectsReturned risk from the old bare get_or_create().

    grant() is idempotent — calling it twice for the same citizen/category
    returns the existing granted record without creating duplicates.
    """
    if not created:
        return

    from apps.consent.models import ConsentCategory
    from apps.consent.services import ConsentService

    required_categories = ConsentCategory.objects.filter(is_required=True, is_active=True)
    granted_count = 0
    for category in required_categories:
        try:
            ConsentService.grant(
                citizen=instance,
                category_slug=category.slug,
                request=None,
            )
            granted_count += 1
        except Exception as exc:
            logger.error(
                "bootstrap_required_consents: grant failed for category %s: %s",
                category.slug,
                type(exc).__name__,
            )

    if granted_count:
        logger.info(
            "bootstrap_required_consents: granted %d required categories for new citizen",
            granted_count,
        )
