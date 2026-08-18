"""Medium-priority runtime contracts for service-backed CI verification."""

from decimal import Decimal
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TransactionTestCase
from django.urls import reverse

from apps.payments.gateways.exceptions import GatewayWebhookError
from apps.payments.gateways.stripe_gateway import StripeGateway
from celery import shared_task
from celery.contrib.testing.worker import start_worker
from celery.result import AsyncResult


@shared_task(name="tests.medium_priority_contracts.add_one")
def add_one(value):
    return value + 1


SERVICE_BACKED_INTEGRATION = (
    settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"
    and settings.CACHES["default"]["BACKEND"].endswith("RedisCache")
    and not settings.CELERY_TASK_ALWAYS_EAGER
)


@skipUnless(
    SERVICE_BACKED_INTEGRATION,
    "requires config.settings.integration with PostgreSQL, Redis, and non-eager Celery",
)
class IntegrationSettingsTopologyTests(SimpleTestCase):
    def test_integration_profile_is_non_eager_and_service_backed(self):
        self.assertFalse(settings.CELERY_TASK_ALWAYS_EAGER)
        self.assertFalse(settings.CELERY_TASK_EAGER_PROPAGATES)
        self.assertEqual(settings.DATABASES["default"]["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(
            settings.DATABASES["default"]["NAME"].removeprefix("test_"),
            "civicos_test",
        )
        self.assertTrue(settings.CACHES["default"]["BACKEND"].endswith("RedisCache"))
        self.assertTrue(settings.CELERY_BROKER_URL.startswith("redis://"))


@skipUnless(
    SERVICE_BACKED_INTEGRATION,
    "requires config.settings.integration with PostgreSQL, Redis, and non-eager Celery",
)
class ServiceBackedRuntimeContractTests(TransactionTestCase):
    reset_sequences = True

    def tearDown(self):
        cache.delete("medium-priority-contract:test-key")
        super().tearDown()

    def test_postgresql_orm_write_read_and_redis_round_trip(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            email="medium-contract@example.invalid",
            password="not-used",
        )
        self.assertEqual(
            user_model.objects.get(pk=user.pk).email, "medium-contract@example.invalid"
        )

        cache.set("medium-priority-contract:test-key", "redis-value", timeout=30)
        self.assertEqual(cache.get("medium-priority-contract:test-key"), "redis-value")

    def test_non_eager_task_is_consumed_by_bounded_worker(self):
        self.assertFalse(settings.CELERY_TASK_ALWAYS_EAGER)
        from config.celery import app

        with start_worker(app, tasks=[add_one], perform_ping_check=False, shutdown_timeout=10):
            result = add_one.apply_async(args=(41,))
            self.assertIsInstance(result, AsyncResult)
            self.assertEqual(result.get(timeout=10), 42)


class StripeWebhookBehaviorTests(SimpleTestCase):
    def setUp(self):
        self.gateway = StripeGateway()

    @patch("stripe.WebhookSignature.verify_header")
    def test_valid_signature_is_accepted(self, verify_header):
        self.assertTrue(self.gateway.verify_webhook_signature(b"{}", "t=1,v1=valid", "whsec_test"))
        verify_header.assert_called_once_with(b"{}", "t=1,v1=valid", "whsec_test", tolerance=300)

    @patch(
        "stripe.WebhookSignature.verify_header",
        side_effect=stripe.error.SignatureVerificationError("bad", "sig"),
    )
    def test_invalid_signature_is_rejected(self, verify_header):
        self.assertFalse(self.gateway.verify_webhook_signature(b"{}", "t=1,v1=bad", "whsec_test"))

    def test_missing_secret_and_malformed_payload_are_rejected(self):
        self.assertFalse(self.gateway.verify_webhook_signature(b"{}", "", ""))
        with self.assertRaises(GatewayWebhookError):
            self.gateway.parse_webhook_event({"data": {"object": {"id": "pi_123"}}})

    def test_representative_event_is_normalized_without_billing_pii(self):
        event_type, data = self.gateway.parse_webhook_event(
            {
                "type": "payment_intent.succeeded",
                "data": {
                    "object": {
                        "id": "pi_123",
                        "amount_received": 1250,
                        "latest_charge": "ch_123",
                        "billing_details": {
                            "name": "Private Person",
                            "email": "private@example.invalid",
                        },
                    }
                },
            }
        )
        self.assertEqual(event_type, "payment_intent.succeeded")
        self.assertEqual(data["gateway_intent_id"], "pi_123")
        self.assertEqual(data["gateway_charge_id"], "ch_123")
        self.assertEqual(data["amount_paid"], Decimal("12.5"))
        self.assertNotIn("billing_details", data)
        self.assertNotIn("email", data)


class ConsentHTTPContractTests(SimpleTestCase):
    def test_documented_consent_api_routes_require_authentication_as_json(self):
        for route_name, expected_path in (
            ("api-v1:consent:api-category-list", "/api/v1/consent/categories/"),
            ("api-v1:consent:api-record-list", "/api/v1/consent/records/"),
        ):
            path = reverse(route_name)
            self.assertEqual(path, expected_path)

            response = self.client.get(path, HTTP_ACCEPT="application/json")
            self.assertEqual(response.status_code, 401)
            self.assertTrue(response.headers["Content-Type"].startswith("application/json"))
            self.assertEqual(
                response.wsgi_request.resolver_match.url_name, route_name.rsplit(":", 1)[1]
            )
            self.assertEqual(response.json()["error"]["code"], "unauthorized")

    def test_consent_openapi_route_is_registered(self):
        self.assertEqual(reverse("api-v1:consent-schema"), "/api/v1/consent/schema/")


class TestFrontendApiBoundary(SimpleTestCase):
    def test_frontend_declares_api_base_and_build_scripts(self):
        package = Path("civicos-site/package.json").read_text()
        app = Path("civicos-site/src/App.jsx").read_text()
        assert '"build"' in package
        assert '"lint"' in package
        assert '"contract-smoke"' in package
        assert "<Routes>" in app
        assert '<Route path="*"' in app
