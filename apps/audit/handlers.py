"""
Signal handlers that write to the audit log.

Connected in AuditConfig.ready(). All civicos signals from apps.core.signals
are subscribed here and converted into AuditLogEntry records.
"""

from django.dispatch import receiver

from apps.core.signals import (
    form_submission_received,
    pii_record_accessed,
    pii_record_exported,
    service_request_status_changed,
    user_logged_out,
    user_login_failed,
    user_login_succeeded,
)

from .models import AuditEventType
from .services import record_event_from_request


@receiver(user_login_succeeded)
def audit_login_success(sender, request, user, **kwargs) -> None:  # noqa: ANN001, ANN003
    # actor_email suppressed — storing real email in audit log is PII.
    # Match the pattern used in audit_login_failure.
    record_event_from_request(
        request,
        event_type=AuditEventType.LOGIN_SUCCESS,
        actor_email="",
        resource_type="auth_extension.User",
        resource_id=user.pk,
    )


@receiver(user_login_failed)
def audit_login_failure(sender, request, credentials, **kwargs) -> None:  # noqa: ANN001, ANN003
    # Do NOT store the attempted email in event_detail — it is PII.
    # The actor_email field is intentionally left empty for failed logins
    # because the credential may belong to a non-existent account.
    record_event_from_request(
        request,
        event_type=AuditEventType.LOGIN_FAILED,
        outcome="failure",
    )


@receiver(user_logged_out)
def audit_logout(sender, request, user, **kwargs) -> None:  # noqa: ANN001, ANN003
    record_event_from_request(
        request,
        event_type=AuditEventType.LOGOUT,
        resource_type="auth_extension.User",
        resource_id=user.pk if user else "",
    )


@receiver(form_submission_received)
def audit_form_submission(sender, form_page, submission, request, **kwargs) -> None:  # noqa: ANN001, ANN003
    record_event_from_request(
        request,
        event_type=AuditEventType.SUBMISSION_RECEIVED,
        resource_type="forms.FormSubmission",
        resource_id=submission.pk,
        event_detail={"form_page_id": form_page.pk, "form_title": str(form_page)},
    )


@receiver(service_request_status_changed)
def audit_status_change(sender, instance, old_status, new_status, actor, **kwargs) -> None:  # noqa: ANN001, ANN003
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
def audit_pii_access(sender, resource_type, resource_id, actor, **kwargs) -> None:  # noqa: ANN001, ANN003
    from .services import record_event

    record_event(
        event_type=AuditEventType.RECORD_VIEWED,
        actor_id=str(actor.pk) if actor else None,
        actor_email=actor.email if actor else "",
        resource_type=resource_type,
        resource_id=str(resource_id),
    )


@receiver(pii_record_exported)
def audit_pii_export(sender, resource_type, count, actor, request, **kwargs) -> None:  # noqa: ANN001, ANN003
    record_event_from_request(
        request,
        event_type=AuditEventType.RECORD_EXPORTED,
        resource_type=resource_type,
        event_detail={"record_count": count},
    )
