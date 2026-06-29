"""
Rate-throttle classes for the Govstack API.

Scopes are configured in settings.py under REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]:

    "DEFAULT_THROTTLE_RATES": {
        "citizen": "300/hour",
        "staff": "1000/hour",
        "anon": "60/hour",
        "token_obtain": "5/minute",
    }
"""

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


class TokenObtainThrottle(AnonRateThrottle):
    """
    Aggressive throttle for the credential exchange endpoint (POST /auth/token/).

    5 attempts per minute per IP is sufficient for legitimate use and makes
    brute-force attacks against government accounts economically infeasible.
    Scope key: "token_obtain" (configure rate in settings).
    """

    scope = "token_obtain"
