"""
Notification signal handlers.

Listens to civicos signals and queues notification tasks.

All handlers must catch exceptions from .delay() so that a Celery broker
outage or serialisation failure never propagates through the signal dispatch
chain and breaks the caller's request/response cycle.
"""

import logging

from django.db import transaction
from django.dispatch import receiver

from apps.core.signals import form_submission_received

logger = logging.getLogger(__name__)


@receiver(form_submission_received)
def notify_on_form_submission(sender, form_page, submission, request, **kwargs):
    """Send confirmation email to submitter when a form is submitted."""
    from .tasks import send_form_submission_confirmation
    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        _user_id = str(user.pk)
        _form_title = str(form_page)
        _submission_id = str(submission.pk)

        def _enqueue():
            try:
                send_form_submission_confirmation.delay(
                    user_id=_user_id,
                    form_title=_form_title,
                    submission_id=_submission_id,
                )
            except Exception:
                logger.exception(
                    "Failed to queue form submission confirmation for user_id=%s submission_id=%s",
                    _user_id,
                    _submission_id,
                )

        transaction.on_commit(_enqueue)


