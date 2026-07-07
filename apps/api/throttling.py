"""
Rate-throttle classes for the CivicOS API.

Scopes are configured in settings.py under REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]:

    "DEFAULT_THROTTLE_RATES": {
        "citizen": "300/hour",
        "staff": "1000/hour",
        "anon": "60/hour",
        "token_obtain": "5/minute",
    }
"""

from django.conf import settings
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class CitizenRateThrottle(UserRateThrottle):
    """
    Throttle for authenticated citizen-facing endpoints.
    Scope key: "citizen" (configure rate in settings).
    """

    scope = "citizen"


class StaffRateThrottle(UserRateThrottle):
    """
    Throttle for staff-facing endpoints.
    Staff have a higher allowance than citizens.
    Scope key: "staff" (configure rate in settings).
    """

    scope = "staff"


class _TestBypassMixin:
    """
    Disable IP-keyed throttling during the test suite.

    When settings.TESTING is True, get_cache_key() returns None, which
    causes DRF to skip the throttle check entirely.  This is necessary
    because TokenObtainThrottle / TokenRefreshThrottle are applied at the
    URL level (not via DEFAULT_THROTTLE_CLASSES) and therefore survive the
    ``DEFAULT_THROTTLE_CLASSES: []`` override in test.py.
    """

    def get_cache_key(self, request, view):
        if getattr(settings, "TESTING", False):
            return None
        return super().get_cache_key(request, view)


class TokenObtainThrottle(_TestBypassMixin, AnonRateThrottle):
    """
    Aggressive throttle for the credential exchange endpoint (POST /auth/token/).

    5 attempts per minute per IP is sufficient for legitimate use and makes
    brute-force attacks against government accounts economically infeasible.
    Scope key: "token_obtain" (configure rate in settings).
    """

    scope = "token_obtain"


class TokenRefreshThrottle(_TestBypassMixin, AnonRateThrottle):
    """
    Throttle for the token-refresh endpoint (POST /auth/token/refresh/).

    A stolen refresh token can be exchanged many times before expiry; applying
    the same 5/minute IP-level limit as token-obtain caps that attack surface.
    Scope key: "token_obtain" (reuses the same rate — no separate scope needed).
    """

    scope = "token_obtain"
