"""
Notification service layer.

Abstracts over GC Notify / anymail backends. Always call these functions,
never Django's send_mail directly, so that:
- Language is selected per recipient
- Notifications are recorded in the Notification model
- Delivery failures are handled consistently
"""

import logging
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.template import TemplateDoesNotExist
from django.template.exceptions import TemplateSyntaxError
from django.template.loader import render_to_string
from django.utils import translation

logger = logging.getLogger(__name__)
User = get_user_model()


def send_email_notification(
    *,
    recipient,
    subject_key: str,
    context: dict[str, Any],
    from_email: str | None = None,
) -> bool:
    """
    Send an email notification to a single recipient.

    Selects the recipient's preferred language for the template.
    Records the notification in the Notification model.
    Returns True on success.
    Raises on SMTP failure (so Celery can autoretry).
    Returns False only if template rendering fails (non-retryable configuration error).
    """
    from .models import Notification, NotificationChannel, NotificationStatus

    language = getattr(recipient, "preferred_language", settings.LANGUAGE_CODE)
    from_email = from_email or settings.DEFAULT_FROM_EMAIL

    try:
        with translation.override(language):
            subject = render_to_string(
                f"notifications/email/{subject_key}_subject.txt",
                context,
            ).strip()
            body_html = render_to_string(
                f"notifications/email/{subject_key}_body.html",
                {**context, "recipient": recipient},
            )
            body_text = render_to_string(
                f"notifications/email/{subject_key}_body.txt",
                {**context, "recipient": recipient},
            )
    except (TemplateDoesNotExist, TemplateSyntaxError) as exc:
        logger.error(
            "Notification template error for subject_key=%r language=%r: %s — "
            "create/fix templates/notifications/email/%s_*.txt/html",
            subject_key,
            language,
            exc,
            subject_key,
        )
        return False

    # Record notification before sending (so we have a record even if send fails)
    notification = Notification.objects.create(
        recipient=recipient,
        channel=NotificationChannel.EMAIL,
        subject=subject,
        body=body_text,
        language=language,
        status=NotificationStatus.PENDING,
    )

    try:
        send_mail(
            subject=subject,
            message=body_text,
            from_email=from_email,
            recipient_list=[recipient.email],
            html_message=body_html,
            fail_silently=False,
        )
        from django.utils import timezone
        notification.status = NotificationStatus.SENT
        notification.sent_at = timezone.now()
        notification.save(update_fields=["status", "sent_at"])
        return True

    except Exception:
        # Log recipient.pk only — never log email addresses (PII).
        logger.exception(
            "Failed to send email notification to recipient_id=%s",
            recipient.pk,
        )
        notification.status = NotificationStatus.FAILED
        notification.save(update_fields=["status"])
        raise  # Re-raise so Celery autoretry_for=(Exception,) can retry the task.
