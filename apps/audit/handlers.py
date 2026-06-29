"""
Signal handlers that write to the audit log.

Connected in AuditConfig.ready(). All govstack signals from apps.core.signals
are subscribed here and converted into AuditLogEntry records.
"""

from django.dispatch import receiver

from apps.core.signals import (
    user_login_failed,
    user_login_succeeded,
    user_logged_out,
    form_submission_received,
    service_request_status_changed,
    workflow_item_approved,
    workflow_item_assigned,
    workflow_item_rejected,
    pii_record_accessed,
    pii_record_exported,
)

from .models import AuditEventType
from .services import record_event_from_request


@receiver(user_login_succeeded)
def audit_login_success(sender, request, user, **kwargs):
    record_event_from_request(
        request,
        event_type=AuditEventType.LOGIN_SUCCESS,
        resource_type="auth_extension.User",
        resource_id=user.pk,
    )


@receiver(user_login_failed)
def audit_login_failure(sender, request, credentials, **kwargs):
    record_event_from_request(
        request,
        event_type=AuditEventType.LOGIN_FAILED,
        outcome="failure",
        event_detail={"email": credentials.get("email", "")},
    )


@receiver(user_logged_out)
def audit_logout(sender, request, user, **kwargs):
    record_event_from_request(
        request,
        event_type=AuditEventType.LOGOUT,
        resource_type="auth_extension.User",
        resource_id=user.pk if user else "",
    )


@receiver(form_submission_received)
def audit_form_submission(sender, form_page, submission, request, **kwargs):
    record_event_from_request(
        request,
        event_type=AuditEventType.SUBMISSION_RECEIVED,
        resource_type="forms.FormSubmission",
        resource_id=submission.pk,
        event_detail={"form_page_id": form_page.pk, "form_title": str(form_page)},
    )


@receiver(service_request_status_changed)
def audit_status_change(sender, instance, old_status, new_status, actor, **kwargs):
    from .services import record_event
    record_event(
        event_type=AuditEventType.STATUS_CHANGED,
        actor_id=str(actor.pk) if actor else None,
        actor_email=actor.email if actor else "",
        resource_type=f"{instance._meta.app_label}.{instance.__class__.__name__}",
        resource_id=str(instance.pk),
        before_state={"status": old_status},
        after_state={"status": new_status},
    )


@receiver(pii_record_accessed)
def audit_pii_access(sender, resource_type, resource_id, actor, **kwargs):
    from .services import record_event
    record_event(
        event_type=AuditEventType.RECORD_VIEWED,
        actor_id=str(actor.pk) if actor else None,
        actor_email=actor.email if actor else "",
        resource_type=resource_type,
        resource_id=str(resource_id),
    )


@receiver(pii_record_exported)
def audit_pii_export(sender, resource_type, count, actor, request, **kwargs):
    record_event_from_request(
        request,
        event_type=AuditEventType.RECORD_EXPORTED,
        resource_type=resource_type,
        event_detail={"record_count": count},
    )
