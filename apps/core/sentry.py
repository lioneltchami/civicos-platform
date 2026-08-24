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
# M-J fix: extended with donor-specific PII fields that appear in event["extra"]
# via Django's Sentry SDK integration (which includes request.POST there), and
# financial fields (amount, eligible_amount, advantage_amount) that together with
# a donor name constitute identifiable donation information under PIPEDA.
PII_FIELDS = frozenset(
    {
        "email",
        "name",
        "legal_name",
        "donor_name",
        "donor_email",
        "donor_legal_name",
        "full_name",
        "first_name",
        "last_name",
        "address",
        "postal_code",
        "phone",
        "ip",
        "ip_address",
        "sin",
        "card_number",
        "cvv",
        "secret",
        "token",
        "password",
        "api_key",
        "authorization",
        "webhook_endpoint_secret",
        "amount",
        "eligible_amount",
        "advantage_amount",
    }
)

# Match bare IPv4 addresses (e.g. 192.168.1.1) in strings.
_IP_PATTERN = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")

# Match IPv6 addresses (full, compressed, link-local, loopback, IPv4-mapped).
# Covers: full (2001:db8:...), compressed (::1, fe80::1), IPv4-mapped (::ffff:x.x.x.x).
# Pattern: a sequence of hex groups and colons with at least two colons OR 7 colons.
_IPV6_PATTERN = re.compile(
    r"(?:(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}"  # full 8-group
    r"|(?:[0-9a-fA-F]{1,4}:){1,7}:"  # trailing ::
    r"|:(?::[0-9a-fA-F]{1,4}){1,7}"  # leading ::
    r"|(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}"  # one :: in middle
    r"|(?:[0-9a-fA-F]{1,4}:){1,5}(?::[0-9a-fA-F]{1,4}){1,2}"
    r"|(?:[0-9a-fA-F]{1,4}:){1,4}(?::[0-9a-fA-F]{1,4}){1,3}"
    r"|(?:[0-9a-fA-F]{1,4}:){1,3}(?::[0-9a-fA-F]{1,4}){1,4}"
    r"|(?:[0-9a-fA-F]{1,4}:){1,2}(?::[0-9a-fA-F]{1,4}){1,5}"
    r"|[0-9a-fA-F]{1,4}:(?::[0-9a-fA-F]{1,4}){1,6}"
    r"|::(?:ffff(?::0{1,4})?:)?(?:25[0-5]|(?:2[0-4]|1?\d)?\d)"
    r"(?:\.(?:25[0-5]|(?:2[0-4]|1?\d)?\d)){3}"  # IPv4-mapped ::ffff:x.x.x.x
    r"|::)"  # bare ::
)

# Log categories from third-party libraries that may contain secrets or request
# bodies — drop these breadcrumbs entirely rather than attempt to scrub them.
# M-I fix: added "httpx" and "httpcore" — Stripe SDK >= 15 switched its HTTP
# transport from requests/urllib3 to httpx, and httpcore is httpx's underlying
# transport layer. Without these entries, Stripe API calls to
# api.stripe.com/v1/payment_intents appear in Sentry with full request/response
# bodies including charge IDs and customer references (PIPEDA violation).
_DROP_CATEGORIES = frozenset({"stripe", "urllib3", "requests", "httpx", "httpcore"})

# HTTP headers that must never be forwarded to Sentry.
_SENSITIVE_HEADERS = frozenset({"Authorization", "Cookie", "X-Stripe-Signature"})


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def _mask_ip(text: str) -> str:
    """
    Mask all IPv4 and IPv6 addresses in *text*, replacing each with '[masked]'.

    M-G fix: the original code only masked IPv4 addresses. IPv6 addresses
    (including loopback ::1, link-local fe80::1, full 2001:db8:... addresses,
    and IPv4-mapped ::ffff:x.x.x.x addresses) were silently forwarded to Sentry
    in breadcrumb messages, REMOTE_ADDR, and X-Forwarded-For — a PIPEDA violation.

    IPv6 is matched first so that IPv4-mapped addresses (::ffff:192.0.2.1) are
    caught by the IPv6 pattern; the leftover literal IPv4 part (if any) is then
    caught by the IPv4 pattern in the same pass.
    """
    # IPv6 first (catches IPv4-mapped forms like ::ffff:192.0.2.1)
    text = _IPV6_PATTERN.sub("[masked]", text)
    # IPv4 second (catches bare IPv4 and any literal IPv4 remaining after IPv6 sub)
    text = _IP_PATTERN.sub("[masked]", text)
    return text


def before_breadcrumb(crumb, hint):  # noqa: ANN001, ANN201
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

    # 3. Scrub IPv4 addresses from the breadcrumb message.
    # Uses [ip] sentinel (not [masked]) to preserve backward compatibility with
    # existing log-monitoring rules that key off the [ip] token.
    message = crumb.get("message") or ""
    message = _IPV6_PATTERN.sub("[ip]", message)
    crumb["message"] = _IP_PATTERN.sub("[ip]", message)

    return crumb


def _scrub_dict(d, keys=None):  # noqa: ANN001, ANN202
    """Recursively scrub PII keys from a dict or list, replacing values with '[Filtered]'.

    Used by before_send to sanitise event["extra"] — Django's Sentry SDK places
    request.POST data there when send_default_pii=False, bypassing the header-scrub
    path and exposing donor names, amounts, and email addresses (PIPEDA violation).

    M-H fix: now recurses into list values so that list[dict] structures (e.g.
    event["extra"]["donors"] = [{"name": "...", "amount": "..."}]) are fully scrubbed
    rather than silently passed through.

    Args:
        d: The data structure to scrub (dict or list; other types returned unchanged).
        keys: Optional frozenset of lowercase PII field names. Defaults to PII_FIELDS.
              Pass an explicit set in tests to avoid coupling tests to the global constant.
    """
    if keys is None:
        keys = PII_FIELDS
    if isinstance(d, dict):
        return {
            k: "[Filtered]" if k.lower() in keys else _scrub_dict(v, keys) for k, v in d.items()
        }
    if isinstance(d, list):
        return [_scrub_dict(item, keys) for item in d]
    return d


def before_send(event, hint):  # noqa: ANN001, ANN201
    """Strip sensitive HTTP headers and PII from Sentry error events before transmission.

    Registered as ``before_send`` in ``sentry_sdk.init()``.

    Actions:
    1. Remove Authorization, Cookie, and X-Stripe-Signature headers so that
       session tokens and webhook secrets are never sent to Sentry's servers.
    2. M-G fix: mask IPv4 and IPv6 addresses in REMOTE_ADDR and HTTP_X_FORWARDED_FOR
       within event["request"]["env"] so that real client IPs are never forwarded to
       Sentry's US servers (PIPEDA compliance).
    3. M-J fix: scrub event["extra"] — Django's Sentry SDK (with send_default_pii=False
       and DEBUG=False) includes request.POST in event["extra"] rather than
       event["request"]["data"], so PII in POST bodies (donor names, amounts, email
       addresses) would otherwise bypass the header-scrub and reach Sentry's US servers
       in violation of PIPEDA and the municipal data-processing agreement.
    """
    request = event.get("request", {})

    # 1. Strip sensitive HTTP headers
    headers = request.get("headers", {})
    for header in _SENSITIVE_HEADERS:
        headers.pop(header, None)

    # 2. M-G: mask IP addresses in request.env fields
    env = request.get("env", {})
    for ip_field in ("REMOTE_ADDR", "HTTP_X_FORWARDED_FOR"):
        if env.get(ip_field):
            env[ip_field] = _mask_ip(env[ip_field])

    # 3. M-J: scrub PII from event["extra"] (contains request.POST under Django SDK)
    if "extra" in event:
        event["extra"] = _scrub_dict(event["extra"])

    # 4. Scrub event["request"]["data"] — the parsed JSON/form body forwarded by
    #    the Sentry SDK for API requests. This contains donor names, email addresses,
    #    and donation amounts sent in POST/PUT bodies (PIPEDA violation if forwarded).
    if "data" in request and isinstance(request["data"], dict):
        request["data"] = _scrub_dict(request["data"])
    elif "data" in request and isinstance(request["data"], str):
        # Form-encoded body or raw JSON string — replace entirely to be safe.
        request["data"] = "[Filtered]"

    return event
