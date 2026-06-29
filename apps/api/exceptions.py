"""
Custom exception handler for the Govstack API.

Wraps all DRF errors in a consistent JSON envelope so API consumers
always get the same shape regardless of what went wrong:

    {
        "error": {
            "code":   "snake_case_error_code",
            "detail": "Human-readable message or dict of field errors",
            "status": 403
        }
    }

Register in settings.py:
    REST_FRAMEWORK = {
        "EXCEPTION_HANDLER": "apps.api.exceptions.govstack_exception_handler",
        ...
    }
"""

import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)

# Maps HTTP status codes to machine-readable error codes.
_STATUS_CODE_MAP: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: "validation_error",
    status.HTTP_401_UNAUTHORIZED: "unauthorized",
    status.HTTP_403_FORBIDDEN: "permission_denied",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
    status.HTTP_429_TOO_MANY_REQUESTS: "too_many_requests",
}


def govstack_exception_handler(exc, context):
    """
    DRF exception handler that normalises all error responses.

    Falls back to the DRF default handler first so that authentication,
    throttling, and permission classes all work as expected. Then the
    response is reshaped into the Govstack envelope format.

    Args:
        exc: The exception instance.
        context: DRF context dict (view, request, args, kwargs).

    Returns:
        Response with the envelope format, or None (letting Django's
        500 handler take over) for unhandled exceptions.
    """
    # Let DRF build the initial response (handles auth, throttle, etc.)
    response = exception_handler(exc, context)

    if response is None:
        # Unhandled exception — log it and let Django's 500 machinery run.
        logger.exception(
            "Unhandled exception in API view %s",
            context.get("view", "unknown"),
        )
        return None

    http_status = response.status_code

    # Determine error code from the status map; fall back to "server_error"
    # for anything 5xx or unmapped.
    if http_status >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        code = "server_error"
    else:
        code = _STATUS_CODE_MAP.get(http_status, "api_error")

    # For validation errors (400), preserve the full field-error dict so
    # clients can display per-field feedback. For all other errors, collapse
    # the detail to a single string.
    raw_detail = response.data
    if http_status == status.HTTP_400_BAD_REQUEST and isinstance(raw_detail, dict):
        # Keep the nested field → [messages] structure intact.
        detail = raw_detail
    else:
        # DRF sometimes wraps the message in a list or ErrorDetail; flatten it.
        if isinstance(raw_detail, dict) and "detail" in raw_detail:
            detail = str(raw_detail["detail"])
        elif isinstance(raw_detail, list) and len(raw_detail) == 1:
            detail = str(raw_detail[0])
        else:
            detail = str(raw_detail)

    response.data = {
        "error": {
            "code": code,
            "detail": detail,
            "status": http_status,
        }
    }

    return response
