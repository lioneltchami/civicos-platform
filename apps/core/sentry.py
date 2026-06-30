"""
Sentry SDK hook functions for PII filtering.

These hooks are registered with sentry_sdk.init() in config/settings/production.py.
They are defined here (rather than inline in the settings file) so they can be
imported and tested independently without pulling in the sentry_sdk package.

PIPEDA compliance rationale: Sentry is a US-based third party not listed in the
municipal data-processing agreement. Breadcrumbs are auto-captured log messages
that may include PII from third-party libraries (Stripe, Celery, urllib3) that
we do not control. These hooks scrub PII at the SDK boundary before any data
leaves the process.
"""

import re

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Field names that should never appear in Sentry breadcrumb data or event extras.
# Covers PIPEDA-sensitive identifiers that third-party libraries may log.
PII_FIELDS = frozenset({
    "email", "name", "legal_name", "donor_name", "address",
    "postal_code", "phone", "ip", "ip_address", "sin",
    "card_number", "cvv", "secret", "token", "password",
    "webhook_endpoint_secret",
})

# Match bare IPv4 addresses (e.g. 192.168.1.1) in breadcrumb messages.
_IP_PATTERN = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")

# Log categories from third-party libraries that may contain secrets or request
# bodies — drop these breadcrumbs entirely rather than attempt to scrub them.
_DROP_CATEGORIES = frozenset({"stripe", "urllib3", "requests"})

# HTTP headers that must never be forwarded to Sentry.
_SENSITIVE_HEADERS = frozenset({"Authorization", "Cookie", "X-Stripe-Signature"})


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

def before_breadcrumb(crumb, hint):
    """Strip PII from Sentry breadcrumbs before they leave the process.

    Registered as ``before_breadcrumb`` in ``sentry_sdk.init()``.

    Three actions:
    1. Drop breadcrumbs from high-risk third-party categories entirely.
    2. Replace values of known PII field names in crumb["data"] with "[Filtered]".
    3. Replace IPv4 addresses in crumb["message"] with "[ip]".
    """
    # 1. Drop entire breadcrumb for noisy/high-risk categories
    if crumb.get("category") in _DROP_CATEGORIES:
        return None

    # 2. Scrub known PII field names from the breadcrumb data dict
    data = crumb.get("data") or {}
    for key in list(data.keys()):
        if key.lower() in PII_FIELDS:
            data[key] = "[Filtered]"

    # 3. Scrub IPv4 addresses from the breadcrumb message
    message = crumb.get("message") or ""
    crumb["message"] = _IP_PATTERN.sub("[ip]", message)

    return crumb


def before_send(event, hint):
    """Strip sensitive HTTP headers from Sentry error events before transmission.

    Registered as ``before_send`` in ``sentry_sdk.init()``.

    Removes Authorization, Cookie, and X-Stripe-Signature headers so that
    session tokens and webhook secrets are never sent to Sentry's servers.
    """
    request = event.get("request", {})
    headers = request.get("headers", {})
    for header in _SENSITIVE_HEADERS:
        headers.pop(header, None)
    return event
