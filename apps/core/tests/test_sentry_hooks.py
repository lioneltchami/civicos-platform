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
        crumb = {"message": "payment processed", "data": {"payment_id": "pi_abc123", "amount": 5000}, "category": "app"}
        result = _before_breadcrumb(crumb, {})
        self.assertEqual(result["data"]["payment_id"], "pi_abc123")
        self.assertEqual(result["data"]["amount"], 5000)

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
