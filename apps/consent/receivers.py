"""
Signal receivers for the Consent & Privacy building block.

These receivers are registered in ConsentConfig.ready() by importing this module.
"""
from __future__ import annotations
import logging

from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.template.loader import render_to_string
from django.conf import settings

from apps.consent.signals import consent_withdrawn, export_requested

logger = logging.getLogger(__name__)

User = get_user_model()


@receiver(consent_withdrawn)
def send_withdrawal_confirmation_email(sender, consent_record, request=None, **kwargs):
    """
    Send a plain-text email to the citizen confirming their consent withdrawal.
    PIPEDA accountability principle — citizens must receive evidence of their rights being exercised.
    """
    citizen = consent_record.citizen
    category = consent_record.category
    subject = f"Consent Withdrawn: {category.name_en} — GovStack"
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
        logger.info(
            "send_withdrawal_confirmation_email: sent to citizen %s for category %s",
            citizen.pk,
            category.slug,
        )
    except Exception as e:
        logger.error(
            "send_withdrawal_confirmation_email: failed for citizen %s: %s",
            citizen.pk,
            e,
        )


@receiver(export_requested)
def send_export_request_received_email(sender, export_request, request=None, **kwargs):
    """
    Send acknowledgement email when a PIPEDA data export request is submitted.
    Informs the citizen of the 30-day processing SLA.
    """
    citizen = export_request.citizen
    subject = "Your Data Export Request — GovStack"
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
        logger.info(
            "send_export_request_received_email: sent to citizen %s for export %s",
            citizen.pk,
            export_request.pk,
        )
    except Exception as e:
        logger.warning(
            "send_export_request_received_email: failed for citizen %s: %s",
            citizen.pk,
            e,
        )


@receiver(post_save, sender=User)
def bootstrap_required_consents(sender, instance, created, **kwargs):
    """
    Automatically create ConsentRecord entries for required consent categories
    when a new citizen account is created. Required categories (is_required=True)
    cannot be withdrawn but still need explicit records in the database so that
    ConsentService.has_consent() returns True for newly registered citizens.

    This runs inside the post_save signal — the user row is already committed.
    """
    if not created:
        return
    # Avoid circular imports — import inside the function
    from apps.consent.models import ConsentCategory, ConsentRecord
    from django.utils import timezone

    required_categories = ConsentCategory.objects.filter(
        is_required=True, is_active=True
    )
    created_count = 0
    for category in required_categories:
        _, created = ConsentRecord.objects.get_or_create(
            citizen=instance,
            category=category,
            defaults={
                "status": ConsentRecord.STATUS_GRANTED,
                "granted_at": timezone.now(),
                "source": "admin",  # system-granted on registration
                "consent_version": getattr(settings, "CONSENT_CURRENT_VERSION", "1.0"),
            },
        )
        if created:
            created_count += 1
    logger.info(
        "bootstrap_required_consents: created %d required consent records for citizen %s",
        created_count,
        instance.pk,
    )
