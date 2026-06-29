"""
Notification signal handlers.

Listens to govstack signals and queues notification tasks.
"""

from django.dispatch import receiver

from apps.core.signals import (
    form_submission_received,
    service_request_status_changed,
)


@receiver(form_submission_received)
def notify_on_form_submission(sender, form_page, submission, request, **kwargs):
    """Send confirmation email to submitter when a form is submitted."""
    from .tasks import send_form_submission_confirmation
    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        send_form_submission_confirmation.delay(
            user_id=str(user.pk),
            form_title=str(form_page),
            submission_id=str(submission.pk),
        )


@receiver(service_request_status_changed)
def notify_on_status_change(sender, instance, old_status, new_status, actor, **kwargs):
    """Notify citizen when their service request status changes."""
    from .tasks import send_status_update_notification
    send_status_update_notification.delay(
        citizen_id=str(instance.citizen_id),
        request_reference=instance.reference_number,
        new_status=new_status,
    )
