"""
Celery tasks for sending notifications.

Tasks are idempotent — safe to retry on failure.
"""

import logging

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)
User = get_user_model()


@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    name="notifications.send_form_submission_confirmation",
)
def send_form_submission_confirmation(*, user_id: str, form_title: str, submission_id: str):
    """Send a submission confirmation email to the form submitter."""
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("Cannot send confirmation — user %s not found", user_id)
        return

    from .services import send_email_notification
    send_email_notification(
        recipient=user,
        subject_key="form_submission_confirmation",
        context={"form_title": form_title, "submission_id": submission_id},
    )


@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    name="notifications.send_status_update_notification",
)
def send_status_update_notification(*, citizen_id: str, request_reference: str, new_status: str):
    """Notify a citizen that their service request status has changed."""
    try:
        user = User.objects.get(pk=citizen_id)
    except User.DoesNotExist:
        logger.warning("Cannot send status update — user %s not found", citizen_id)
        return

    from .services import send_email_notification
    site_url = getattr(settings, "SITE_URL", "http://localhost:8000")
    portal_link = f"{site_url}/portal/"
    send_email_notification(
        recipient=user,
        subject_key="status_update",
        context={
            "reference": request_reference,
            "new_status": new_status,
            "portal_link": portal_link,
        },
    )
