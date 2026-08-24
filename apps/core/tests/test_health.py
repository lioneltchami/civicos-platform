"""
Tests for M12 — Stripe connectivity check in the health endpoint.

GET /health/ and GET /health/ready/ both run the readiness view, which now
includes a "stripe" field in the checks dict alongside "database" and "cache".

Rules:
  - stripe == "ok"           → Stripe API is reachable
  - stripe == "degraded"     → Stripe API is unreachable (network/API error)
  - stripe == "unconfigured" → STRIPE_SECRET_KEY is not set
  - A "degraded" stripe result alone must NOT change the HTTP status code to 503;
    DB/cache availability drives the status code.
  - An AuthenticationError (bad key) counts as "ok" — API is reachable.

We also smoke-test the _check_stripe helper directly for precise error-path coverage.
"""

from unittest.mock import patch

import stripe
from django.test import SimpleTestCase, TestCase, override_settings

from apps.core.urls.health import _check_stripe

# ---------------------------------------------------------------------------
# Unit tests for _check_stripe helper
# ---------------------------------------------------------------------------


class CheckStripeHelperTest(SimpleTestCase):
    """_check_stripe() returns True/False based on Stripe SDK responses."""

    def test_returns_true_on_success(self):
        with patch("stripe.Customer.list", return_value={"data": []}):
            self.assertTrue(_check_stripe("sk_test_fake"))

    def test_returns_true_on_authentication_error(self):
        """AuthenticationError means bad key but API is reachable → True."""
        with patch("stripe.Customer.list", side_effect=stripe.error.AuthenticationError("bad key")):
            self.assertTrue(_check_stripe("sk_test_invalid"))

    def test_returns_false_on_api_connection_error(self):
        with patch("stripe.Customer.list", side_effect=stripe.error.APIConnectionError("timeout")):
            self.assertFalse(_check_stripe("sk_test_fake"))

    def test_returns_false_on_api_error(self):
        with patch("stripe.Customer.list", side_effect=stripe.error.APIError("server error")):
            self.assertFalse(_check_stripe("sk_test_fake"))

    def test_returns_false_on_unexpected_exception(self):
        with patch("stripe.Customer.list", side_effect=RuntimeError("unexpected")):
            self.assertFalse(_check_stripe("sk_test_fake"))


# ---------------------------------------------------------------------------
# Integration tests for the readiness view
# ---------------------------------------------------------------------------


@override_settings(STRIPE_SECRET_KEY="sk_test_fake")
class HealthCheckStripeOkTest(TestCase):
    """Stripe check reports 'ok' when the API is reachable."""

    def test_health_check_includes_stripe_key(self):
        with patch("apps.core.urls.health._check_stripe", return_value=True):
            response = self.client.get("/health/")
        data = response.json()
        self.assertIn("stripe", data.get("checks", {}))

    def test_health_check_stripe_ok(self):
        with patch("apps.core.urls.health._check_stripe", return_value=True):
            response = self.client.get("/health/")
        data = response.json()
        self.assertEqual(data["checks"]["stripe"], "ok")

    def test_health_check_status_200_when_stripe_ok(self):
        with patch("apps.core.urls.health._check_stripe", return_value=True):
            response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)

    def test_health_ready_also_includes_stripe(self):
        """The /health/ready/ alias runs the same readiness view."""
        with patch("apps.core.urls.health._check_stripe", return_value=True):
            response = self.client.get("/health/ready/")
        data = response.json()
        self.assertIn("stripe", data.get("checks", {}))


@override_settings(STRIPE_SECRET_KEY="sk_test_fake")
class HealthCheckStripeDegradedTest(TestCase):
    """Stripe check reports 'degraded' on connectivity failure — HTTP 200 preserved."""

    def test_health_check_stripe_degraded_on_api_connection_error(self):
        with patch("stripe.Customer.list", side_effect=stripe.error.APIConnectionError("timeout")):
            response = self.client.get("/health/")
        data = response.json()
        self.assertEqual(data["checks"]["stripe"], "degraded")

    def test_health_check_status_200_when_only_stripe_degraded(self):
        """DB + cache are healthy → 200 even when Stripe is degraded."""
        with patch("stripe.Customer.list", side_effect=stripe.error.APIConnectionError("timeout")):
            response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)

    def test_health_check_overall_status_ok_when_only_stripe_degraded(self):
        """top-level 'status' is 'ok' when only Stripe is degraded (DB + cache fine)."""
        with patch("stripe.Customer.list", side_effect=stripe.error.APIConnectionError("timeout")):
            response = self.client.get("/health/")
        data = response.json()
        self.assertEqual(data["status"], "ok")

    def test_health_check_stripe_degraded_on_api_error(self):
        with patch("stripe.Customer.list", side_effect=stripe.error.APIError("server error")):
            response = self.client.get("/health/")
        data = response.json()
        self.assertEqual(data["checks"]["stripe"], "degraded")


@override_settings(STRIPE_SECRET_KEY="")
class HealthCheckStripeUnconfiguredTest(TestCase):
    """Stripe check reports 'unconfigured' when no API key is set."""

    def test_health_check_stripe_unconfigured_when_no_key(self):
        response = self.client.get("/health/")
        data = response.json()
        self.assertEqual(data["checks"]["stripe"], "unconfigured")

    def test_health_check_status_200_when_stripe_unconfigured(self):
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)


class HealthCheckLivenessTest(TestCase):
    """Liveness probe is unaffected — no Stripe check."""

    def test_liveness_returns_200(self):
        response = self.client.get("/health/live/")
        self.assertEqual(response.status_code, 200)

    def test_liveness_has_no_checks_key(self):
        response = self.client.get("/health/live/")
        data = response.json()
        self.assertNotIn("checks", data)
        self.assertEqual(data["status"], "ok")


@override_settings(STRIPE_SECRET_KEY="sk_test_fake")
class HealthCheckDatabaseFailureTest(TestCase):
    """DB failure still returns 503 regardless of Stripe status."""

    def test_503_when_db_fails_even_if_stripe_ok(self):
        with patch("django.db.connection.ensure_connection", side_effect=Exception("db gone")):
            with patch("apps.core.urls.health._check_stripe", return_value=True):
                response = self.client.get("/health/")
        self.assertEqual(response.status_code, 503)
        data = response.json()
        self.assertEqual(data["checks"]["stripe"], "ok")
        self.assertIn("error", data["checks"]["database"])
