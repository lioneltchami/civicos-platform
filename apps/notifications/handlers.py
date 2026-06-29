"""
Notification signal handlers.

Listens to govstack signals and queues notification tasks.

All handlers must catch exceptions from .delay() so that a Celery broker
outage or serialisation failure never propagates through the signal dispatch
chain and breaks the caller's request/response cycle.
"""

import logging

from django.dispatch import receiver

from apps.core.signals import (
    form_submission_received,
    service_request_status_changed,
)

logger = logging.getLogger(__name__)


@receiver(form_submission_received)
def notify_on_form_submission(sender, form_page, submission, request, **kwargs):
    """Send confirmation email to submitter when a form is submitted."""
    from .tasks import send_form_submission_confirmation
    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        try:
            send_form_submission_confirmation.delay(
                user_id=str(user.pk),
                form_title=str(form_page),
                submission_id=str(submission.pk),
            )
        except Exception:
            logger.exception(
                "Failed to queue form submission confirmation for user_id=%s submission_id=%s",
                user.pk,
                getattr(submission, "pk", "?"),
            )


@receiver(service_request_status_changed)
def notify_on_status_change(sender, instance, old_status, new_status, actor, **kwargs):
    """
    Notify citizen when their service request status changes.

    NOTE (architectural): apps/portal/services.py::update_request_status() calls
    _fire_notification() directly via transaction.on_commit(), bypassing this
    signal entirely. This handler is currently DEAD CODE — the signal
    service_request_status_changed is defined in apps/core/signals.py but
    portal/services.py never emits it. If this handler is ever activated by
    wiring the signal emit in portal/services.py, notifications will be sent
    TWICE (once via this handler, once via _fire_notification). Before activating
    this handler, remove the direct task call from _fire_notification.
    """
    from .tasks import send_status_update_notification
    try:
        send_status_update_notification.delay(
            citizen_id=str(instance.citizen_id),
            request_reference=instance.reference_number,
            new_status=new_status,
        )
    except Exception:
        logger.exception(
            "Failed to queue status update notification for request=%s status=%s",
            getattr(instance, "reference_number", "?"),
            new_status,
        )
