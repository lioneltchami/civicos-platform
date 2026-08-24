"""
Signal handlers for authentication events.

Bridges Django auth signals → CivicOS core signals (for audit/notifications),
and adds allauth-specific signal handling for signup and password reset.

All handlers write to the audit log via AuditLogEntry directly (no .log()
classmethod exists on that model). PII (email, IP) is stored only in the
audit log record — never written to the application logger.
"""

from __future__ import annotations

import logging

from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver

from apps.core.signals import (
    user_logged_out as gs_logged_out,
)
from apps.core.signals import (
    user_login_failed as gs_login_failed,
)
from apps.core.signals import (
    user_login_succeeded as gs_login_succeeded,
)
from apps.forms.utils import _mask_ip

try:
    from allauth.account.signals import (
        password_reset as allauth_password_reset,
    )
    from allauth.account.signals import (
        user_signed_up,
    )

    _allauth_available = True
except ImportError:
    _allauth_available = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_client_ip(request) -> str | None:  # noqa: ANN001
    """
    Extract the real client IP.
    Only trusts X-Forwarded-For when a known reverse proxy is configured
    (SECURE_PROXY_SSL_HEADER set), preventing IP spoofing in audit records.
    """
    from django.conf import settings

    if getattr(settings, "SECURE_PROXY_SSL_HEADER", None):
        x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded:
            return x_forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


def _write_audit(event_type: str, outcome: str, user, request) -> None:  # noqa: ANN001
    """
    Create an immutable AuditLogEntry for the given auth event.

    Wrapped in try/except so audit log failure never breaks the auth flow.
    - event_type: must be a valid AuditEventType value (e.g. "auth.login.success")
    - outcome: "success" or "failure" (matches AuditLogEntry.outcome choices)
    """
    try:
        from apps.audit.models import AuditLogEntry

        actor_ip = _mask_ip(_get_client_ip(request) or "")
        actor_ua = request.META.get("HTTP_USER_AGENT", "")[:512]

        session_key = ""
        try:
            session_key = request.session.session_key or ""
        except Exception:  # noqa: S110
            pass

        request_id = getattr(request, "request_id", "") or ""

        entry = AuditLogEntry(
            event_type=event_type,
            outcome=outcome,
            actor_id=str(user.pk) if user and user.pk else None,
            actor_email=getattr(user, "email", ""),
            actor_ip=actor_ip,
            actor_user_agent=actor_ua,
            resource_type="auth_extension.User",
            resource_id=str(user.pk) if user and user.pk else "",
            request_id=request_id,
            session_id=session_key,
        )
        entry.save()
    except Exception:
        # Audit log failure must never break the auth flow
        logger.exception("Failed to write audit log for event_type=%s", event_type)


# ---------------------------------------------------------------------------
# Django auth signal handlers
# ---------------------------------------------------------------------------


@receiver(user_logged_in)
def on_login_success(sender, request, user, **kwargs) -> None:  # noqa: ANN001, ANN003
    # Update last login IP (use update() to avoid triggering full model save hooks)
    ip = _get_client_ip(request)
    if ip:
        type(user).objects.filter(pk=user.pk).update(last_login_ip=_mask_ip(ip))
    # Fire CivicOS core signal for audit/notifications listeners
    # (audit/handlers.py:audit_login_success writes the entry via the signal)
    gs_login_succeeded.send(sender=sender, request=request, user=user)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs) -> None:  # noqa: ANN001, ANN003
    # Fire CivicOS core signal; audit/handlers.py:audit_logout writes the entry
    gs_logged_out.send(sender=sender, request=request, user=user)


@receiver(user_login_failed)
def on_login_failure(sender, credentials, request, **kwargs) -> None:  # noqa: ANN001, ANN003
    gs_login_failed.send(sender=sender, request=request, credentials=credentials)
    # No user object available on login failure — log with minimal context
    try:
        from apps.audit.models import AuditLogEntry

        actor_ip = _mask_ip(_get_client_ip(request) or "")
        actor_ua = request.META.get("HTTP_USER_AGENT", "")[:512]
        request_id = getattr(request, "request_id", "") or ""
        entry = AuditLogEntry(
            event_type="auth.login.failed",
            outcome="failure",
            actor_ip=actor_ip,
            actor_user_agent=actor_ua,
            resource_type="auth_extension.User",
            request_id=request_id,
        )
        entry.save()
    except Exception:
        logger.exception("Failed to write audit log for event_type=auth.login.failed")


# ---------------------------------------------------------------------------
# allauth signal handlers (conditional on allauth being installed)
# ---------------------------------------------------------------------------

if _allauth_available:

    @receiver(user_signed_up)
    def on_user_signed_up(sender, request, user, **kwargs) -> None:  # noqa: ANN001, ANN003
        _write_audit("system.user.created", "success", user, request)

    @receiver(allauth_password_reset)
    def on_password_reset(sender, request, user, **kwargs) -> None:  # noqa: ANN001, ANN003
        _write_audit("auth.password.changed", "success", user, request)
