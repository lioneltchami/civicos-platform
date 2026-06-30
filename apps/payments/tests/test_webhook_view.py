"""
Tests for the stripe_webhook Django view.
Uses TestCase (DB required for WebhookEvent model).
Mocks gateway.verify_webhook_signature and Celery task dispatch.
"""
import json
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse

from apps.payments.models import WebhookEvent, GATEWAY_STRIPE


class StripeWebhookViewTests(TestCase):
    """Tests for stripe_webhook view."""

    def setUp(self):
        from apps.payments.models import TenantPaymentConfig

        self.url = reverse("payments:stripe_webhook")
        self.valid_payload = {
            "id": "evt_test_001",
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": "pi_test_001"}},
        }
        # M8 fix: the view now reads the webhook secret from TenantPaymentConfig.
        # Seed the singleton so existing tests that mock verify_webhook_signature
        # still reach the gateway call (the empty-secret guard must not fire).
        config = TenantPaymentConfig.get_solo()
        config.webhook_endpoint_secret = "whsec_test_fixture_secret"
        config.save()

    def _post(self, payload=None, sig="t=1234,v1=valid", verify_result=True):
        """Helper: POST to webhook endpoint with mocked gateway and task."""
        body = json.dumps(payload if payload is not None else self.valid_payload).encode()
        with patch("apps.payments.views.webhook.get_gateway") as mock_get_gw, \
             patch("apps.payments.tasks.process_stripe_webhook.delay") as mock_delay:
            mock_gw = MagicMock()
            mock_gw.verify_webhook_signature.return_value = verify_result
            mock_get_gw.return_value = mock_gw
            response = self.client.post(
                self.url,
                data=body,
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE=sig,
            )
            return response, mock_delay

    # ── Method enforcement ────────────────────────────────────────────────────

    def test_get_returns_405(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_put_returns_405(self):
        response = self.client.put(self.url, data=b"{}", content_type="application/json")
        self.assertEqual(response.status_code, 405)

    # ── Signature verification ────────────────────────────────────────────────

    def test_invalid_signature_returns_400(self):
        response, mock_delay = self._post(verify_result=False)
        self.assertEqual(response.status_code, 400)

    def test_invalid_signature_does_not_dispatch_task(self):
        _, mock_delay = self._post(verify_result=False)
        mock_delay.assert_not_called()

    def test_valid_signature_returns_200(self):
        response, _ = self._post(verify_result=True)
        self.assertIn(response.status_code, [200])

    def test_valid_signature_no_webhook_event_created_on_bad_sig(self):
        self._post(verify_result=False)
        self.assertEqual(WebhookEvent.objects.count(), 0)

    # ── Payload parsing ───────────────────────────────────────────────────────

    def test_malformed_json_returns_400(self):
        body = b"not valid json {"
        with patch("apps.payments.views.webhook.get_gateway") as mock_get_gw:
            mock_gw = MagicMock()
            mock_gw.verify_webhook_signature.return_value = True
            mock_get_gw.return_value = mock_gw
            response = self.client.post(
                self.url,
                data=body,
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1234,v1=valid",
            )
        self.assertEqual(response.status_code, 400)

    def test_missing_id_field_returns_400(self):
        payload = {"type": "payment_intent.succeeded", "data": {"object": {}}}
        response, _ = self._post(payload=payload)
        self.assertEqual(response.status_code, 400)

    def test_missing_type_field_returns_400(self):
        payload = {"id": "evt_missing_type", "data": {"object": {}}}
        response, _ = self._post(payload=payload)
        self.assertEqual(response.status_code, 400)

    def test_empty_id_returns_400(self):
        payload = {"id": "", "type": "payment_intent.succeeded", "data": {"object": {}}}
        response, _ = self._post(payload=payload)
        self.assertEqual(response.status_code, 400)

    def test_empty_type_returns_400(self):
        payload = {"id": "evt_001", "type": "", "data": {"object": {}}}
        response, _ = self._post(payload=payload)
        self.assertEqual(response.status_code, 400)

    # ── Idempotency / duplicate handling ─────────────────────────────────────

    def test_first_delivery_creates_webhook_event(self):
        self._post()
        self.assertEqual(WebhookEvent.objects.count(), 1)

    def test_first_delivery_returns_queued(self):
        response, _ = self._post()
        data = json.loads(response.content)
        self.assertEqual(data["status"], "queued")

    def test_first_delivery_dispatches_task(self):
        _, mock_delay = self._post()
        mock_delay.assert_called_once()

    def test_first_delivery_task_called_with_webhook_event_pk(self):
        _, mock_delay = self._post()
        event = WebhookEvent.objects.get(gateway_event_id="evt_test_001")
        mock_delay.assert_called_once_with(str(event.pk))

    def test_second_delivery_returns_duplicate(self):
        self._post()  # First delivery
        response, _ = self._post()  # Second delivery (same payload, same id)
        data = json.loads(response.content)
        self.assertEqual(data["status"], "duplicate")

    def test_second_delivery_no_new_row(self):
        self._post()
        self._post()
        self.assertEqual(WebhookEvent.objects.count(), 1)

    def test_second_delivery_task_not_dispatched_again(self):
        self._post()  # First delivery
        _, mock_delay = self._post()  # Second delivery
        mock_delay.assert_not_called()

    def test_concurrent_delivery_race_returns_duplicate(self):
        """Simulate IntegrityError on get_or_create (concurrent insert race)."""
        from django.db import IntegrityError

        with patch("apps.payments.views.webhook.get_gateway") as mock_get_gw, \
             patch("apps.payments.tasks.process_stripe_webhook.delay") as mock_delay, \
             patch("apps.payments.views.webhook.WebhookEvent.objects.get_or_create",
                   side_effect=IntegrityError("duplicate key")):
            mock_gw = MagicMock()
            mock_gw.verify_webhook_signature.return_value = True
            mock_get_gw.return_value = mock_gw
            body = json.dumps(self.valid_payload).encode()
            response = self.client.post(
                self.url,
                data=body,
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1234,v1=valid",
            )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["status"], "duplicate")
        mock_delay.assert_not_called()

    # ── Webhook event stored correctly ────────────────────────────────────────

    def test_webhook_event_gateway_event_id(self):
        self._post()
        event = WebhookEvent.objects.get(gateway_event_id="evt_test_001")
        self.assertEqual(event.gateway_event_id, "evt_test_001")

    def test_webhook_event_event_type(self):
        self._post()
        event = WebhookEvent.objects.get(gateway_event_id="evt_test_001")
        self.assertEqual(event.event_type, "payment_intent.succeeded")

    def test_webhook_event_signature_verified_true(self):
        self._post()
        event = WebhookEvent.objects.get(gateway_event_id="evt_test_001")
        self.assertTrue(event.signature_verified)

    def test_webhook_event_processed_false_initially(self):
        self._post()
        event = WebhookEvent.objects.get(gateway_event_id="evt_test_001")
        self.assertFalse(event.processed)

    def test_webhook_event_gateway_is_stripe(self):
        self._post()
        event = WebhookEvent.objects.get(gateway_event_id="evt_test_001")
        self.assertEqual(event.gateway, GATEWAY_STRIPE)

    # ── PII stripping ────────────────────────────────────────────────────────

    def test_billing_details_stripped_from_stored_payload(self):
        """Payload with billing_details — stored payload must not contain name/email."""
        payload = {
            "id": "evt_pii_001",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_pii_001",
                    "charges": {
                        "data": [
                            {
                                "id": "ch_pii_001",
                                "billing_details": {
                                    "name": "John Citizen",
                                    "email": "john@example.com",
                                },
                                "payment_method_details": {"card": {"last4": "4242"}},
                            }
                        ]
                    },
                }
            },
        }
        self._post(payload=payload)
        event = WebhookEvent.objects.get(gateway_event_id="evt_pii_001")
        stored_str = json.dumps(event.payload)
        self.assertNotIn("John Citizen", stored_str)
        self.assertNotIn("john@example.com", stored_str)

    def test_top_level_email_field_redacted(self):
        """Top-level email in data.object is stored as REDACTED."""
        payload = {
            "id": "evt_email_001",
            "type": "customer.updated",
            "data": {
                "object": {
                    "id": "cus_001",
                    "email": "customer@example.com",
                    "name": "Customer Name",
                }
            },
        }
        self._post(payload=payload)
        event = WebhookEvent.objects.get(gateway_event_id="evt_email_001")
        obj = event.payload["data"]["object"]
        self.assertEqual(obj["email"], "REDACTED")
        self.assertNotIn("customer@example.com", json.dumps(event.payload))

    def test_top_level_name_field_redacted(self):
        """Top-level name in data.object is stored as REDACTED."""
        payload = {
            "id": "evt_name_001",
            "type": "customer.updated",
            "data": {
                "object": {
                    "id": "cus_002",
                    "name": "Real Name Here",
                    "email": "test@example.com",
                }
            },
        }
        self._post(payload=payload)
        event = WebhookEvent.objects.get(gateway_event_id="evt_name_001")
        obj = event.payload["data"]["object"]
        self.assertEqual(obj["name"], "REDACTED")

    # ── Celery dispatch ───────────────────────────────────────────────────────

    def test_task_not_called_when_signature_invalid(self):
        _, mock_delay = self._post(verify_result=False)
        mock_delay.assert_not_called()

    def test_task_not_called_for_duplicate_events(self):
        self._post()  # First delivery creates the event
        _, mock_delay = self._post()  # Duplicate
        mock_delay.assert_not_called()

    def test_different_event_ids_create_separate_rows(self):
        payload1 = {**self.valid_payload, "id": "evt_separate_001"}
        payload2 = {**self.valid_payload, "id": "evt_separate_002"}
        self._post(payload=payload1)
        self._post(payload=payload2)
        self.assertEqual(WebhookEvent.objects.count(), 2)

    # ── M8 — Webhook secret sourced from TenantPaymentConfig, not settings ────

    def test_webhook_secret_read_from_tenant_config_not_settings(self):
        """
        M8: The webhook view must pass TenantPaymentConfig.webhook_endpoint_secret
        to gateway.verify_webhook_signature, NOT a value from Django settings.

        This ensures the per-tenant secret (stored encrypted in the DB) is used
        for HMAC verification.  A global settings.STRIPE_WEBHOOK_SECRET would
        fail for all tenants except one and would not auto-rotate when Stripe
        rotates the signing secret.
        """
        from apps.payments.models import TenantPaymentConfig

        config = TenantPaymentConfig.get_solo()
        config.webhook_endpoint_secret = "whsec_per_tenant_secret_xyz"
        config.save()

        body = json.dumps(self.valid_payload).encode()
        captured_secret = []

        def capturing_verify(payload_bytes, sig_header, secret):
            captured_secret.append(secret)
            return True  # simulate valid signature

        with patch("apps.payments.views.webhook.get_gateway") as mock_get_gw, \
             patch("apps.payments.tasks.process_stripe_webhook.delay"):
            mock_gw = mock_get_gw.return_value
            mock_gw.verify_webhook_signature.side_effect = capturing_verify
            self.client.post(
                self.url,
                data=body,
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1234,v1=valid",
            )

        self.assertEqual(len(captured_secret), 1, "verify_webhook_signature should be called once")
        self.assertEqual(
            captured_secret[0],
            "whsec_per_tenant_secret_xyz",
            "The secret passed to verify_webhook_signature must come from "
            "TenantPaymentConfig, not settings.STRIPE_WEBHOOK_SECRET.",
        )

    def test_webhook_returns_400_when_tenant_config_secret_empty(self):
        """
        M8: If TenantPaymentConfig.webhook_endpoint_secret is blank, the view
        must return 400 and not process the webhook.
        """
        from apps.payments.models import TenantPaymentConfig

        config = TenantPaymentConfig.get_solo()
        config.webhook_endpoint_secret = ""
        config.save()

        body = json.dumps(self.valid_payload).encode()
        with patch("apps.payments.tasks.process_stripe_webhook.delay") as mock_delay:
            response = self.client.post(
                self.url,
                data=body,
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1234,v1=valid",
            )
        self.assertEqual(response.status_code, 400)
        mock_delay.assert_not_called()
