"""
Core middleware for Govstack.

Middleware order in settings:
  RequestIDMiddleware  → assigns a unique ID to every request
  AuditMiddleware      → attaches current user info for audit logging
"""

import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse


class RequestIDMiddleware:
    """
    Injects a unique X-Request-ID header into every request and response.

    This ID is used throughout the request lifecycle to correlate log entries,
    including in the audit log. Accepts an incoming X-Request-ID from a reverse
    proxy (e.g., nginx, AWS ALB) if present; otherwise generates a new UUID.
    """

    HEADER = "HTTP_X_REQUEST_ID"
    RESPONSE_HEADER = "X-Request-ID"

    def __init__(self, get_response: Callable) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        raw_id = request.META.get(self.HEADER, "")
        try:
            request_id = str(uuid.UUID(raw_id))
        except (ValueError, AttributeError):
            request_id = str(uuid.uuid4())
        request.request_id = request_id  # type: ignore[attr-defined]

        response = self.get_response(request)
        response[self.RESPONSE_HEADER] = request_id
        return response


class AuditMiddleware:
    """
    Attaches audit context to the request for use by the audit app.

    Sets request.audit_context with actor information so that signal handlers
    and service functions can record who performed an action without needing
    to pass the request object through every layer.
    """

    def __init__(self, get_response: Callable) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.audit_context = {  # type: ignore[attr-defined]
            "actor_id": str(request.user.pk) if request.user.is_authenticated else None,
            "actor_ip": self._get_client_ip(request),
            "actor_user_agent": request.META.get("HTTP_USER_AGENT", "")[:512],
            "request_id": getattr(request, "request_id", None),
        }
        return self.get_response(request)

    def _get_client_ip(self, request: HttpRequest) -> str:
        """
        Extract the real client IP.

        SECURITY: X-Forwarded-For is only trusted when SECURE_PROXY_SSL_HEADER is
        configured in settings (i.e., we are provably behind a reverse proxy that
        strips/rewrites the header). Without that guard, any client can forge the
        header and spoof their audit-log IP.

        When behind a single trusted proxy, the header format is:
          X-Forwarded-For: <client>, <proxy1>
        We take the leftmost IP (the original client as seen by the outermost proxy).
        If multiple untrusted hops are present, consider deploying django-ipware for
        configurable trusted-proxy-count handling.
        """
        from django.conf import settings as django_settings

        behind_proxy = bool(getattr(django_settings, "SECURE_PROXY_SSL_HEADER", None))
        if behind_proxy:
            x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
            if x_forwarded_for:
                # Leftmost entry is the original client IP as appended by the outermost proxy
                return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "")
