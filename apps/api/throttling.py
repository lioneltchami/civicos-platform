"""
Rate-throttle classes for the Govstack API.

Scopes are configured in settings.py under REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]:

    "DEFAULT_THROTTLE_RATES": {
        "citizen": "60/minute",
        "staff": "300/minute",
        "anon": "20/minute",
    }
"""

from rest_framework.throttling import AnonRateThrottle, UserRateThrottle  # noqa: F401


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
