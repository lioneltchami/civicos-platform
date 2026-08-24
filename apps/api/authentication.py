"""
Custom authentication backends for the CivicOS API.

Security:
- Logs only user PKs (no PII such as email addresses).
- Verifies is_active on every request to catch deactivated accounts mid-session.
"""

import logging

from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed

# Re-export for convenience so callers can import from one place.
from rest_framework_simplejwt.authentication import JWTAuthentication  # noqa: F401

logger = logging.getLogger(__name__)


class CivicOSTokenAuthentication(TokenAuthentication):
    """
    DRF TokenAuthentication extended with:
    - Active-user guard: deactivated accounts are rejected even with a valid token.
    - Structured audit-friendly logging (PK only, no PII).
    """

    def authenticate(self, request):  # noqa: ANN001, ANN201
        """
        Authenticate the request.

        Returns (user, token) on success, None if no credentials provided,
        or raises AuthenticationFailed on bad/deactivated credentials.
        """
        result = super().authenticate(request)

        if result is None:
            # No token header present — let the next authenticator try.
            return None

        user, token = result

        if not user.is_active:
            logger.warning(
                "API token auth rejected: user_id=%s account inactive",
                user.pk,
            )
            raise AuthenticationFailed(
                "User account is disabled.",
                code="account_disabled",
            )

        logger.info("API token auth: user_id=%s", user.pk)
        return (user, token)
