"""
Tests for M11 — Sentry PII filtering hooks in config/settings/production.py.

The _before_breadcrumb hook must:
  - Drop breadcrumbs from high-risk third-party categories (stripe, urllib3, requests)
  - Replace known PII field values in the breadcrumb data dict with "[Filtered]"
  - Replace IPv4 addresses in the breadcrumb message with "[ip]"

The _before_send hook must:
  - Strip Authorization, Cookie, and X-Stripe-Signature headers from event requests

These hooks are imported directly from the production settings module so they can
be tested in isolation without initialising the full Sentry SDK or requiring a DSN.
"""

from django.test import SimpleTestCase

# Import the hooks from apps.core.sentry — they are pure stdlib functions with
# no sentry_sdk dependency, making them safe to test under config.settings.test.
from apps.core.sentry import before_breadcrumb as _before_breadcrumb
from apps.core.sentry import before_send as _before_send


class BeforeBreadcrumbPIIFieldsTest(SimpleTestCase):
    """_before_breadcrumb scrubs known PII field names from crumb['data']."""

    def test_strips_email_field(self):
        crumb = {"message": "user logged in", "data": {"email": "donor@example.com"}, "category": "auth"}
        result = _before_breadcrumb(crumb, {})
        self.assertEqual(result["data"]["email"], "[Filtered]")

    def test_strips_name_field(self):
        crumb = {"message": "form submitted", "data": {"name": "Jane Doe"}, "category": "app"}
        result = _before_breadcrumb(crumb, {})
        self.assertEqual(result["data"]["name"], "[Filtered]")

    def test_strips_ip_address_field(self):
        crumb = {"message": "request", "data": {"ip_address": "203.0.113.5"}, "category": "app"}
        result = _before_breadcrumb(crumb, {})
        self.assertEqual(result["data"]["ip_address"], "[Filtered]")

    def test_strips_token_field(self):
        crumb = {"message": "auth", "data": {"token": "sk_live_secret"}, "category": "app"}
        result = _before_breadcrumb(crumb, {})
        self.assertEqual(result["data"]["token"], "[Filtered]")

    def test_strips_webhook_endpoint_secret_field(self):
        crumb = {"message": "webhook", "data": {"webhook_endpoint_secret": "whsec_abc"}, "category": "app"}
        result = _before_breadcrumb(crumb, {})
        self.assertEqual(result["data"]["webhook_endpoint_secret"], "[Filtered]")

    def test_preserves_non_pii_fields(self):
        # Note: "amount" was moved to PII_FIELDS by M-J fix (financial data is PII
        # when combined with donor identity under PIPEDA). Use non-PII keys instead.
        crumb = {"message": "payment processed", "data": {"payment_id": "pi_abc123", "status": "succeeded"}, "category": "app"}
        result = _before_breadcrumb(crumb, {})
        self.assertEqual(result["data"]["payment_id"], "pi_abc123")
        self.assertEqual(result["data"]["status"], "succeeded")

    def test_handles_missing_data_key(self):
        crumb = {"message": "something happened", "category": "app"}
        result = _before_breadcrumb(crumb, {})
        self.assertIsNotNone(result)
        self.assertEqual(result["message"], "something happened")

    def test_handles_none_data_value(self):
        crumb = {"message": "something", "data": None, "category": "app"}
        result = _before_breadcrumb(crumb, {})
        self.assertIsNotNone(result)

    def test_field_matching_is_case_insensitive(self):
        """Field names like 'Email' or 'EMAIL' are still scrubbed."""
        crumb = {"message": "x", "data": {"Email": "test@example.com"}, "category": "app"}
        result = _before_breadcrumb(crumb, {})
        self.assertEqual(result["data"]["Email"], "[Filtered]")


class BeforeBreadcrumbCategoryDropTest(SimpleTestCase):
    """_before_breadcrumb drops entire breadcrumbs from risky third-party categories."""

    def test_drops_stripe_category(self):
        crumb = {"message": "stripe call", "category": "stripe", "data": {}}
        result = _before_breadcrumb(crumb, {})
        self.assertIsNone(result)

    def test_drops_urllib3_category(self):
        crumb = {"message": "http request", "category": "urllib3", "data": {}}
        result = _before_breadcrumb(crumb, {})
        self.assertIsNone(result)

    def test_drops_requests_category(self):
        crumb = {"message": "GET /v1/customers", "category": "requests", "data": {}}
        result = _before_breadcrumb(crumb, {})
        self.assertIsNone(result)

    def test_passes_through_app_category(self):
        crumb = {"message": "payment initiated", "category": "app", "data": {}}
        result = _before_breadcrumb(crumb, {})
        self.assertIsNotNone(result)

    def test_passes_through_auth_category(self):
        crumb = {"message": "login attempt", "category": "auth", "data": {}}
        result = _before_breadcrumb(crumb, {})
        self.assertIsNotNone(result)


class BeforeBreadcrumbIPScrubbingTest(SimpleTestCase):
    """_before_breadcrumb replaces IPv4 addresses in the message with [ip]."""

    def test_scrubs_ip_in_message(self):
        crumb = {"message": "Request from 192.168.1.1", "data": {}, "category": "app"}
        result = _before_breadcrumb(crumb, {})
        self.assertNotIn("192.168.1.1", result["message"])
        self.assertIn("[ip]", result["message"])

    def test_scrubs_multiple_ips_in_message(self):
        crumb = {"message": "10.0.0.1 forwarded to 172.16.0.5", "data": {}, "category": "app"}
        result = _before_breadcrumb(crumb, {})
        self.assertNotIn("10.0.0.1", result["message"])
        self.assertNotIn("172.16.0.5", result["message"])

    def test_preserves_message_text_around_ip(self):
        crumb = {"message": "Request from 203.0.113.42 rejected", "data": {}, "category": "app"}
        result = _before_breadcrumb(crumb, {})
        self.assertIn("Request from", result["message"])
        self.assertIn("rejected", result["message"])

    def test_handles_empty_message(self):
        crumb = {"message": "", "data": {}, "category": "app"}
        result = _before_breadcrumb(crumb, {})
        self.assertEqual(result["message"], "")

    def test_handles_none_message(self):
        crumb = {"message": None, "data": {}, "category": "app"}
        result = _before_breadcrumb(crumb, {})
        self.assertIsNotNone(result)


class BeforeSendHeaderStrippingTest(SimpleTestCase):
    """_before_send strips sensitive HTTP headers from Sentry error events."""

    def _make_event(self, headers):
        return {"request": {"headers": dict(headers), "url": "/pay/"}}

    def test_strips_authorization_header(self):
        event = self._make_event({"Authorization": "Bearer sk_live_abc", "Content-Type": "application/json"})
        result = _before_send(event, {})
        self.assertNotIn("Authorization", result["request"]["headers"])

    def test_strips_cookie_header(self):
        event = self._make_event({"Cookie": "sessionid=abc123", "Accept": "application/json"})
        result = _before_send(event, {})
        self.assertNotIn("Cookie", result["request"]["headers"])

    def test_strips_stripe_signature_header(self):
        event = self._make_event({"X-Stripe-Signature": "t=1234,v1=abc"})
        result = _before_send(event, {})
        self.assertNotIn("X-Stripe-Signature", result["request"]["headers"])

    def test_preserves_safe_headers(self):
        event = self._make_event({
            "Content-Type": "application/json",
            "X-Request-Id": "req_abc",
            "Authorization": "Bearer token",
        })
        result = _before_send(event, {})
        self.assertEqual(result["request"]["headers"]["Content-Type"], "application/json")
        self.assertEqual(result["request"]["headers"]["X-Request-Id"], "req_abc")

    def test_no_request_key_does_not_raise(self):
        event = {"exception": {"values": []}}
        result = _before_send(event, {})
        self.assertIsNotNone(result)

    def test_returns_event(self):
        event = self._make_event({"Content-Type": "application/json"})
        result = _before_send(event, {})
        self.assertIs(result, event)


# ---------------------------------------------------------------------------
# M-I — httpx and httpcore breadcrumbs must be dropped
# ---------------------------------------------------------------------------

class BeforeBreadcrumbHttpxDropTest(SimpleTestCase):
    """
    M-I: Stripe SDK >= 15 switched its HTTP transport from requests/urllib3 to
    httpx. If httpx breadcrumbs are not dropped, Stripe API calls to
    api.stripe.com/v1/payment_intents appear in Sentry with full request/response
    bodies including charge IDs and customer references — a PIPEDA violation.

    httpcore is httpx's underlying transport layer and may emit its own
    breadcrumbs with raw HTTP data.
    """

    def test_httpx_breadcrumbs_dropped(self):
        """httpx category breadcrumbs must be filtered entirely (Stripe SDK >= 15 uses httpx)."""
        crumb = {
            "category": "httpx",
            "message": "GET https://api.stripe.com/v1/customers/cus_xxx",
            "data": {"status_code": 200},
        }
        result = _before_breadcrumb(crumb, {})
        self.assertIsNone(
            result,
            "httpx breadcrumbs must be dropped — Stripe SDK >= 15 uses httpx as "
            "its HTTP transport and request/response bodies may contain customer IDs.",
        )

    def test_httpcore_breadcrumbs_dropped(self):
        """httpcore category breadcrumbs must be filtered entirely (transport layer under httpx)."""
        crumb = {
            "category": "httpcore",
            "message": "HTTP/1.1 200 OK",
            "data": {},
        }
        result = _before_breadcrumb(crumb, {})
        self.assertIsNone(
            result,
            "httpcore breadcrumbs must be dropped — it is the transport layer "
            "under httpx and may emit raw HTTP data including Stripe response bodies.",
        )

    def test_urllib3_still_dropped(self):
        """Regression: urllib3 breadcrumbs must still be dropped after M-I change."""
        crumb = {"category": "urllib3", "message": "http request", "data": {}}
        result = _before_breadcrumb(crumb, {})
        self.assertIsNone(result)

    def test_requests_still_dropped(self):
        """Regression: requests breadcrumbs must still be dropped after M-I change."""
        crumb = {"category": "requests", "message": "GET /v1/customers", "data": {}}
        result = _before_breadcrumb(crumb, {})
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# M-J — before_send must scrub event["extra"]
# ---------------------------------------------------------------------------

class BeforeSendExtraScrubbingTest(SimpleTestCase):
    """
    M-J: Django's Sentry SDK (with send_default_pii=False, DEBUG=False) places
    request.POST in event["extra"] rather than event["request"]["data"].
    The existing header-scrub logic misses this, exposing donor names, amounts,
    and email addresses in POST bodies to Sentry's US servers — a PIPEDA violation
    under the municipal data-processing agreement.
    """

    def test_before_send_scrubs_donor_name_from_extra(self):
        """before_send must replace donor_name in event['extra'] with '[Filtered]'."""
        event = {
            "request": {},
            "extra": {"donor_name": "Alice Smith", "non_pii_key": "keep_this"},
        }
        result = _before_send(event, {})
        self.assertEqual(result["extra"]["donor_name"], "[Filtered]")
        self.assertEqual(result["extra"]["non_pii_key"], "keep_this")

    def test_before_send_scrubs_amount_from_extra(self):
        """before_send must replace amount in event['extra'] with '[Filtered]'."""
        event = {
            "request": {},
            "extra": {"amount": "100.00", "tax_year": 2024},
        }
        result = _before_send(event, {})
        self.assertEqual(result["extra"]["amount"], "[Filtered]")
        self.assertEqual(result["extra"]["tax_year"], 2024)

    def test_before_send_scrubs_multiple_pii_fields_from_extra(self):
        """before_send must scrub all PII keys from event['extra'] in one pass."""
        event = {
            "request": {},
            "extra": {
                "donor_name": "Alice Smith",
                "amount": "100.00",
                "non_pii_key": "keep_this",
            },
        }
        result = _before_send(event, {})
        self.assertEqual(result["extra"]["donor_name"], "[Filtered]")
        self.assertEqual(result["extra"]["amount"], "[Filtered]")
        self.assertEqual(result["extra"]["non_pii_key"], "keep_this")

    def test_before_send_scrubs_email_from_extra(self):
        """before_send must scrub email addresses from event['extra']."""
        event = {
            "request": {},
            "extra": {"email": "donor@example.com", "record_id": 42},
        }
        result = _before_send(event, {})
        self.assertEqual(result["extra"]["email"], "[Filtered]")
        self.assertEqual(result["extra"]["record_id"], 42)

    def test_before_send_no_extra_key_does_not_raise(self):
        """before_send must handle events without an 'extra' key gracefully."""
        event = {"request": {"headers": {}}}
        result = _before_send(event, {})
        self.assertIsNotNone(result)
        self.assertNotIn("extra", result)

    def test_before_send_scrubs_nested_pii_in_extra(self):
        """before_send must recursively scrub nested dicts in event['extra']."""
        event = {
            "request": {},
            "extra": {
                "form_data": {"donor_name": "Alice Smith", "amount": "50.00"},
                "safe_key": "safe_value",
            },
        }
        result = _before_send(event, {})
        self.assertEqual(result["extra"]["form_data"]["donor_name"], "[Filtered]")
        self.assertEqual(result["extra"]["form_data"]["amount"], "[Filtered]")
        self.assertEqual(result["extra"]["safe_key"], "safe_value")

    def test_before_send_still_strips_headers_after_mj_change(self):
        """Regression: header scrubbing must still work after the M-J extra-scrub addition."""
        event = {
            "request": {
                "headers": {
                    "Authorization": "Bearer sk_live_abc",
                    "Content-Type": "application/json",
                }
            },
            "extra": {"donor_name": "Bob"},
        }
        result = _before_send(event, {})
        self.assertNotIn("Authorization", result["request"]["headers"])
        self.assertEqual(result["extra"]["donor_name"], "[Filtered]")
