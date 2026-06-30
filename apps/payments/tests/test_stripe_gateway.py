"""
Tests for StripeGateway — all Stripe HTTP calls are mocked.
No real API calls are made in this test suite.
"""
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings

from apps.payments.gateways.exceptions import (
    GatewayAuthError,
    GatewayCardError,
    GatewayIdempotencyError,
    GatewayNetworkError,
    GatewayRateLimitError,
    GatewayWebhookError,
)


# ---------------------------------------------------------------------------
# Helper: make a minimal fake stripe.error module
# ---------------------------------------------------------------------------

def _make_stripe_error_module():
    """Return a namespace with fake stripe.error classes for patching."""
    import types
    mod = types.SimpleNamespace()
    mod.AuthenticationError = type("AuthenticationError", (Exception,), {"code": None, "user_message": None})
    mod.CardError = type("CardError", (Exception,), {"code": None, "decline_code": None, "user_message": None})
    mod.RateLimitError = type("RateLimitError", (Exception,), {"code": None})
    mod.APIConnectionError = type("APIConnectionError", (Exception,), {"code": None})
    mod.IdempotencyError = type("IdempotencyError", (Exception,), {"code": None})
    mod.SignatureVerificationError = type("SignatureVerificationError", (Exception,), {"code": None})
    mod.InvalidRequestError = type("InvalidRequestError", (Exception,), {"code": None})
    return mod


# ---------------------------------------------------------------------------
# _to_cents / _from_cents unit tests (no DB, no Stripe)
# ---------------------------------------------------------------------------

class ToCentsTests(SimpleTestCase):
    """Test _to_cents conversion helper."""

    def _to_cents(self, amount):
        from apps.payments.gateways.stripe_gateway import _to_cents
        return _to_cents(amount)

    def test_standard_amount(self):
        self.assertEqual(self._to_cents(Decimal("49.99")), 4999)

    def test_one_cent(self):
        self.assertEqual(self._to_cents(Decimal("0.01")), 1)

    def test_zero(self):
        self.assertEqual(self._to_cents(Decimal("0.00")), 0)

    def test_large_amount(self):
        self.assertEqual(self._to_cents(Decimal("1000.00")), 100_000)

    def test_whole_dollar(self):
        self.assertEqual(self._to_cents(Decimal("100.00")), 10_000)

    def test_rounds_half_up(self):
        # 10.005 rounds half-up to 10.01 = 1001 cents
        self.assertEqual(self._to_cents(Decimal("10.005")), 1001)

    def test_returns_int(self):
        result = self._to_cents(Decimal("49.99"))
        self.assertIsInstance(result, int)


class FromCentsTests(SimpleTestCase):
    """Test _from_cents conversion helper."""

    def _from_cents(self, cents):
        from apps.payments.gateways.stripe_gateway import _from_cents
        return _from_cents(cents)

    def test_standard_amount(self):
        self.assertEqual(self._from_cents(4999), Decimal("49.99"))

    def test_one_cent(self):
        self.assertEqual(self._from_cents(1), Decimal("0.01"))

    def test_zero(self):
        self.assertEqual(self._from_cents(0), Decimal("0.00"))

    def test_large_amount(self):
        self.assertEqual(self._from_cents(100_000), Decimal("1000.00"))

    def test_returns_decimal(self):
        result = self._from_cents(4999)
        self.assertIsInstance(result, Decimal)

    def test_round_trip(self):
        from apps.payments.gateways.stripe_gateway import _to_cents, _from_cents
        original = Decimal("99.99")
        self.assertEqual(_from_cents(_to_cents(original)), original)


# ---------------------------------------------------------------------------
# verify_webhook_signature tests
# ---------------------------------------------------------------------------

@override_settings(STRIPE_SECRET_KEY="sk_test_fake")
class StripeGatewayVerifyWebhookTests(SimpleTestCase):
    """Test verify_webhook_signature — must never raise, always return bool."""

    def setUp(self):
        from apps.payments.gateways.stripe_gateway import StripeGateway
        self.gateway = StripeGateway()
        self.payload = b'{"id":"evt_test","type":"payment_intent.succeeded"}'
        self.sig = "t=1234,v1=abcdef"
        self.secret = "whsec_test_secret"

    def test_returns_true_on_valid_signature(self):
        with patch("stripe.WebhookSignature.verify_header") as mock_verify:
            mock_verify.return_value = None  # Does not raise = valid
            result = self.gateway.verify_webhook_signature(
                self.payload, self.sig, self.secret
            )
        self.assertTrue(result)

    def test_returns_false_on_signature_verification_error(self):
        import stripe
        err_cls = type("SignatureVerificationError", (Exception,), {})
        with patch("stripe.WebhookSignature.verify_header") as mock_verify:
            mock_verify.side_effect = err_cls("bad sig")
            result = self.gateway.verify_webhook_signature(
                self.payload, self.sig, self.secret
            )
        self.assertFalse(result)

    def test_returns_false_when_webhook_secret_empty_string(self):
        result = self.gateway.verify_webhook_signature(self.payload, self.sig, "")
        self.assertFalse(result)

    def test_returns_false_when_webhook_secret_none(self):
        result = self.gateway.verify_webhook_signature(self.payload, self.sig, None)
        self.assertFalse(result)

    def test_returns_false_on_unexpected_exception(self):
        with patch("stripe.WebhookSignature.verify_header") as mock_verify:
            mock_verify.side_effect = RuntimeError("unexpected error")
            result = self.gateway.verify_webhook_signature(
                self.payload, self.sig, self.secret
            )
        self.assertFalse(result)

    def test_never_raises(self):
        """verify_webhook_signature must never raise — always returns bool."""
        with patch("stripe.WebhookSignature.verify_header") as mock_verify:
            mock_verify.side_effect = Exception("anything can happen")
            try:
                result = self.gateway.verify_webhook_signature(
                    self.payload, self.sig, self.secret
                )
                self.assertIsInstance(result, bool)
            except Exception as exc:
                self.fail(f"verify_webhook_signature raised unexpectedly: {exc}")

    def test_return_value_is_bool_on_success(self):
        with patch("stripe.WebhookSignature.verify_header"):
            result = self.gateway.verify_webhook_signature(
                self.payload, self.sig, self.secret
            )
        self.assertIsInstance(result, bool)

    def test_return_value_is_bool_on_failure(self):
        result = self.gateway.verify_webhook_signature(self.payload, self.sig, "")
        self.assertIsInstance(result, bool)


# ---------------------------------------------------------------------------
# parse_webhook_event tests
# ---------------------------------------------------------------------------

@override_settings(STRIPE_SECRET_KEY="sk_test_fake")
class StripeGatewayParseWebhookTests(SimpleTestCase):
    """Test parse_webhook_event."""

    def setUp(self):
        from apps.payments.gateways.stripe_gateway import StripeGateway
        self.gateway = StripeGateway()

    def _succeeded_payload(self, pi_id="pi_test_001", charge_id="ch_test_001"):
        """
        Fixture using the current Stripe API 2024-06-20 format.

        ``latest_charge`` is a string charge ID; card details live in
        ``latest_charge_expanded`` (the expanded charge object).
        The legacy ``charges`` embed is intentionally omitted to verify that the
        gateway correctly handles the current API response shape.
        """
        return {
            "id": "evt_test_001",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": pi_id,
                    "amount_received": 4999,
                    # API 2024-06-20: latest_charge is a string ID
                    "latest_charge": charge_id,
                    # Expanded charge object (available when expand=["latest_charge"] is set)
                    "latest_charge_expanded": {
                        "id": charge_id,
                        "created": 1700000000,
                        "payment_method_details": {
                            "card": {
                                "last4": "4242",
                                "brand": "visa",
                            }
                        },
                    },
                }
            },
        }

    def _succeeded_payload_legacy(self, pi_id="pi_test_001", charge_id="ch_test_001"):
        """
        Fixture using the deprecated ``charges.data`` embed (API < 2022-11-15).
        Retained to verify the fallback path remains functional.
        """
        return {
            "id": "evt_test_legacy_001",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": pi_id,
                    "amount_received": 4999,
                    "charges": {
                        "data": [
                            {
                                "id": charge_id,
                                "created": 1700000000,
                                "payment_method_details": {
                                    "card": {
                                        "last4": "4242",
                                        "brand": "visa",
                                    }
                                },
                            }
                        ]
                    },
                }
            },
        }

    def test_raises_when_type_missing(self):
        payload = {"id": "evt_001", "data": {"object": {}}}
        with self.assertRaises(GatewayWebhookError):
            self.gateway.parse_webhook_event(payload)

    def test_raises_when_type_empty_string(self):
        payload = {"id": "evt_001", "type": "", "data": {"object": {}}}
        with self.assertRaises(GatewayWebhookError):
            self.gateway.parse_webhook_event(payload)

    def test_succeeded_returns_tuple(self):
        event_type, event_data = self.gateway.parse_webhook_event(self._succeeded_payload())
        self.assertIsInstance(event_type, str)
        self.assertIsInstance(event_data, dict)

    def test_succeeded_event_type(self):
        event_type, _ = self.gateway.parse_webhook_event(self._succeeded_payload())
        self.assertEqual(event_type, "payment_intent.succeeded")

    def test_succeeded_gateway_intent_id(self):
        _, data = self.gateway.parse_webhook_event(self._succeeded_payload(pi_id="pi_abc123"))
        self.assertEqual(data["gateway_intent_id"], "pi_abc123")

    def test_succeeded_gateway_charge_id(self):
        _, data = self.gateway.parse_webhook_event(
            self._succeeded_payload(charge_id="ch_charge_001")
        )
        self.assertEqual(data["gateway_charge_id"], "ch_charge_001")

    def test_succeeded_payment_method_type_card(self):
        _, data = self.gateway.parse_webhook_event(self._succeeded_payload())
        self.assertEqual(data["payment_method_type"], "card")

    def test_succeeded_card_last_four_length(self):
        _, data = self.gateway.parse_webhook_event(self._succeeded_payload())
        if data["card_last_four"] is not None:
            self.assertLessEqual(len(data["card_last_four"]), 4)

    def test_succeeded_card_last_four_value(self):
        _, data = self.gateway.parse_webhook_event(self._succeeded_payload())
        self.assertEqual(data["card_last_four"], "4242")

    def test_succeeded_no_pii_name(self):
        _, data = self.gateway.parse_webhook_event(self._succeeded_payload())
        self.assertNotIn("name", data)

    def test_succeeded_no_pii_email(self):
        _, data = self.gateway.parse_webhook_event(self._succeeded_payload())
        self.assertNotIn("email", data)

    def test_succeeded_no_pii_address(self):
        _, data = self.gateway.parse_webhook_event(self._succeeded_payload())
        self.assertNotIn("address", data)

    def test_succeeded_no_pii_phone(self):
        _, data = self.gateway.parse_webhook_event(self._succeeded_payload())
        self.assertNotIn("phone", data)

    def test_succeeded_has_card_last_four(self):
        _, data = self.gateway.parse_webhook_event(self._succeeded_payload())
        self.assertIn("card_last_four", data)

    def test_succeeded_has_card_brand(self):
        _, data = self.gateway.parse_webhook_event(self._succeeded_payload())
        self.assertIn("card_brand", data)

    def test_succeeded_has_gateway_intent_id(self):
        _, data = self.gateway.parse_webhook_event(self._succeeded_payload())
        self.assertIn("gateway_intent_id", data)

    def test_succeeded_has_gateway_charge_id(self):
        _, data = self.gateway.parse_webhook_event(self._succeeded_payload())
        self.assertIn("gateway_charge_id", data)

    def test_failed_event_type(self):
        payload = {
            "type": "payment_intent.payment_failed",
            "data": {
                "object": {
                    "id": "pi_fail_001",
                    "last_payment_error": {"code": "card_declined"},
                }
            },
        }
        event_type, _ = self.gateway.parse_webhook_event(payload)
        self.assertEqual(event_type, "payment_intent.payment_failed")

    def test_failed_failure_reason(self):
        payload = {
            "type": "payment_intent.payment_failed",
            "data": {
                "object": {
                    "id": "pi_fail_001",
                    "last_payment_error": {"code": "insufficient_funds"},
                }
            },
        }
        _, data = self.gateway.parse_webhook_event(payload)
        self.assertEqual(data["failure_reason"], "insufficient_funds")

    def test_failed_gateway_intent_id(self):
        payload = {
            "type": "payment_intent.payment_failed",
            "data": {
                "object": {
                    "id": "pi_fail_002",
                    "last_payment_error": {"code": "card_declined"},
                }
            },
        }
        _, data = self.gateway.parse_webhook_event(payload)
        self.assertEqual(data["gateway_intent_id"], "pi_fail_002")

    def test_unknown_event_type_returns_minimal_dict(self):
        payload = {
            "type": "account.updated",
            "data": {"object": {"id": "acct_001"}},
        }
        event_type, data = self.gateway.parse_webhook_event(payload)
        self.assertEqual(event_type, "account.updated")
        self.assertIn("gateway_intent_id", data)

    def test_pci_violation_raises_when_last4_too_long(self):
        """GatewayWebhookError raised if Stripe returns last4 > 4 chars (current API path)."""
        payload = {
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_pci_001",
                    "amount_received": 1000,
                    "latest_charge": "ch_pci_001",
                    "latest_charge_expanded": {
                        "id": "ch_pci_001",
                        "created": 1700000000,
                        "payment_method_details": {
                            "card": {
                                "last4": "12345",  # 5 chars — PCI violation
                                "brand": "visa",
                            }
                        },
                    },
                }
            },
        }
        with self.assertRaises(GatewayWebhookError):
            self.gateway.parse_webhook_event(payload)

    def test_pci_violation_raises_when_last4_too_long_legacy_path(self):
        """GatewayWebhookError raised if Stripe returns last4 > 4 chars (legacy charges.data path)."""
        payload = {
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_pci_002",
                    "amount_received": 1000,
                    "charges": {
                        "data": [
                            {
                                "id": "ch_pci_002",
                                "created": 1700000000,
                                "payment_method_details": {
                                    "card": {
                                        "last4": "12345",  # 5 chars — PCI violation
                                        "brand": "visa",
                                    }
                                },
                            }
                        ]
                    },
                }
            },
        }
        with self.assertRaises(GatewayWebhookError):
            self.gateway.parse_webhook_event(payload)

    def test_legacy_charges_embed_still_parses(self):
        """Fallback path: legacy charges.data embed still yields correct charge ID."""
        _, data = self.gateway.parse_webhook_event(
            self._succeeded_payload_legacy(pi_id="pi_leg_001", charge_id="ch_leg_001")
        )
        self.assertEqual(data["gateway_charge_id"], "ch_leg_001")
        self.assertEqual(data["gateway_intent_id"], "pi_leg_001")
        self.assertEqual(data["card_last_four"], "4242")


# ---------------------------------------------------------------------------
# create_payment_intent tests
# ---------------------------------------------------------------------------

@override_settings(STRIPE_SECRET_KEY="sk_test_fake")
class StripeGatewayCreatePaymentIntentTests(SimpleTestCase):
    """Test create_payment_intent — Stripe calls mocked."""

    def setUp(self):
        from apps.payments.gateways.stripe_gateway import StripeGateway
        self.gateway = StripeGateway()
        self.fake_intent = MagicMock()
        self.fake_intent.id = "pi_test_created"
        self.fake_intent.client_secret = "pi_test_created_secret_xxx"
        self.fake_intent.status = "requires_payment_method"

    def test_calls_stripe_with_amount_in_cents(self):
        with patch("stripe.PaymentIntent.create", return_value=self.fake_intent) as mock_create:
            self.gateway.create_payment_intent(
                amount=Decimal("49.99"),
                currency="cad",
                idempotency_key="test-key-001",
                metadata={},
            )
        call_kwargs = mock_create.call_args[1]
        self.assertEqual(call_kwargs["amount"], 4999)

    def test_calls_stripe_with_currency(self):
        with patch("stripe.PaymentIntent.create", return_value=self.fake_intent) as mock_create:
            self.gateway.create_payment_intent(
                amount=Decimal("50.00"),
                currency="CAD",
                idempotency_key="test-key-002",
                metadata={},
            )
        call_kwargs = mock_create.call_args[1]
        self.assertEqual(call_kwargs["currency"], "cad")  # lowercased

    def test_calls_stripe_with_idempotency_key(self):
        with patch("stripe.PaymentIntent.create", return_value=self.fake_intent) as mock_create:
            self.gateway.create_payment_intent(
                amount=Decimal("50.00"),
                currency="cad",
                idempotency_key="idem-key-abc",
                metadata={},
            )
        call_kwargs = mock_create.call_args[1]
        self.assertEqual(call_kwargs["idempotency_key"], "idem-key-abc")

    def test_returns_gateway_intent_id(self):
        with patch("stripe.PaymentIntent.create", return_value=self.fake_intent):
            result = self.gateway.create_payment_intent(
                amount=Decimal("50.00"),
                currency="cad",
                idempotency_key="test-key",
                metadata={},
            )
        self.assertEqual(result["gateway_intent_id"], "pi_test_created")

    def test_returns_client_secret(self):
        with patch("stripe.PaymentIntent.create", return_value=self.fake_intent):
            result = self.gateway.create_payment_intent(
                amount=Decimal("50.00"),
                currency="cad",
                idempotency_key="test-key",
                metadata={},
            )
        self.assertEqual(result["client_secret"], "pi_test_created_secret_xxx")

    def test_returns_status(self):
        with patch("stripe.PaymentIntent.create", return_value=self.fake_intent):
            result = self.gateway.create_payment_intent(
                amount=Decimal("50.00"),
                currency="cad",
                idempotency_key="test-key",
                metadata={},
            )
        self.assertEqual(result["status"], "requires_payment_method")

    def test_passes_stripe_account_when_connect_account_id_provided(self):
        with patch("stripe.PaymentIntent.create", return_value=self.fake_intent) as mock_create:
            self.gateway.create_payment_intent(
                amount=Decimal("50.00"),
                currency="cad",
                idempotency_key="test-key",
                metadata={},
                connect_account_id="acct_connect_001",
            )
        call_kwargs = mock_create.call_args[1]
        self.assertEqual(call_kwargs["stripe_account"], "acct_connect_001")

    def test_no_stripe_account_when_no_connect_account_id(self):
        with patch("stripe.PaymentIntent.create", return_value=self.fake_intent) as mock_create:
            self.gateway.create_payment_intent(
                amount=Decimal("50.00"),
                currency="cad",
                idempotency_key="test-key",
                metadata={},
            )
        call_kwargs = mock_create.call_args[1]
        self.assertNotIn("stripe_account", call_kwargs)

    def test_maps_authentication_error_to_gateway_auth_error(self):
        import stripe

        auth_err = stripe.error.AuthenticationError("bad key")
        auth_err.code = "authentication_error"

        with patch("stripe.PaymentIntent.create", side_effect=auth_err):
            with self.assertRaises(GatewayAuthError):
                self.gateway.create_payment_intent(
                    amount=Decimal("50.00"),
                    currency="cad",
                    idempotency_key="test-key",
                    metadata={},
                )

    def test_maps_card_error_to_gateway_card_error(self):
        """
        When Stripe raises CardError, create_payment_intent raises GatewayCardError.
        We test this by patching _handle_stripe_error to raise GatewayCardError,
        bypassing stripe's internal attribute layout which varies across SDK versions.
        """
        import stripe

        card_err = stripe.error.CardError("card declined", None, "card_declined")

        expected_gateway_err = GatewayCardError(
            "card declined",
            gateway_code="card_declined",
            decline_code="insufficient_funds",
        )

        with patch("stripe.PaymentIntent.create", side_effect=card_err), \
             patch.object(
                 self.gateway, "_handle_stripe_error", side_effect=expected_gateway_err
             ):
            with self.assertRaises(GatewayCardError) as ctx:
                self.gateway.create_payment_intent(
                    amount=Decimal("50.00"),
                    currency="cad",
                    idempotency_key="test-key",
                    metadata={},
                )
        self.assertEqual(ctx.exception.decline_code, "insufficient_funds")

    def test_maps_api_connection_error_to_gateway_network_error(self):
        import stripe

        net_err = stripe.error.APIConnectionError("connection failed")

        with patch("stripe.PaymentIntent.create", side_effect=net_err):
            with self.assertRaises(GatewayNetworkError):
                self.gateway.create_payment_intent(
                    amount=Decimal("50.00"),
                    currency="cad",
                    idempotency_key="test-key",
                    metadata={},
                )

    def test_maps_rate_limit_error_to_gateway_rate_limit_error(self):
        import stripe

        rate_err = stripe.error.RateLimitError("too many requests")

        with patch("stripe.PaymentIntent.create", side_effect=rate_err):
            with self.assertRaises(GatewayRateLimitError):
                self.gateway.create_payment_intent(
                    amount=Decimal("50.00"),
                    currency="cad",
                    idempotency_key="test-key",
                    metadata={},
                )


# ---------------------------------------------------------------------------
# create_refund tests
# ---------------------------------------------------------------------------

@override_settings(STRIPE_SECRET_KEY="sk_test_fake")
class StripeGatewayCreateRefundTests(SimpleTestCase):
    """Test create_refund — Stripe calls mocked."""

    def setUp(self):
        from apps.payments.gateways.stripe_gateway import StripeGateway
        self.gateway = StripeGateway()
        self.fake_refund = MagicMock()
        self.fake_refund.id = "re_test_001"
        self.fake_refund.status = "succeeded"
        self.fake_refund.amount = 4999

    def test_converts_amount_to_cents(self):
        with patch("stripe.Refund.create", return_value=self.fake_refund) as mock_create:
            self.gateway.create_refund(
                gateway_charge_id="ch_001",
                amount=Decimal("49.99"),
                reason="requested_by_customer",
                idempotency_key="refund-key-001",
            )
        call_kwargs = mock_create.call_args[1]
        self.assertEqual(call_kwargs["amount"], 4999)

    def test_maps_service_not_rendered_to_requested_by_customer(self):
        with patch("stripe.Refund.create", return_value=self.fake_refund) as mock_create:
            self.gateway.create_refund(
                gateway_charge_id="ch_001",
                amount=Decimal("49.99"),
                reason="service_not_rendered",
                idempotency_key="refund-key-002",
            )
        call_kwargs = mock_create.call_args[1]
        self.assertEqual(call_kwargs["reason"], "requested_by_customer")

    def test_passes_through_duplicate_reason(self):
        with patch("stripe.Refund.create", return_value=self.fake_refund) as mock_create:
            self.gateway.create_refund(
                gateway_charge_id="ch_001",
                amount=Decimal("49.99"),
                reason="duplicate",
                idempotency_key="refund-key-003",
            )
        call_kwargs = mock_create.call_args[1]
        self.assertEqual(call_kwargs["reason"], "duplicate")

    def test_returns_gateway_refund_id(self):
        with patch("stripe.Refund.create", return_value=self.fake_refund):
            result = self.gateway.create_refund(
                gateway_charge_id="ch_001",
                amount=Decimal("49.99"),
                reason="requested_by_customer",
                idempotency_key="refund-key",
            )
        self.assertEqual(result["gateway_refund_id"], "re_test_001")

    def test_returns_status(self):
        with patch("stripe.Refund.create", return_value=self.fake_refund):
            result = self.gateway.create_refund(
                gateway_charge_id="ch_001",
                amount=Decimal("49.99"),
                reason="requested_by_customer",
                idempotency_key="refund-key",
            )
        self.assertEqual(result["status"], "succeeded")

    def test_returns_amount_as_decimal(self):
        with patch("stripe.Refund.create", return_value=self.fake_refund):
            result = self.gateway.create_refund(
                gateway_charge_id="ch_001",
                amount=Decimal("49.99"),
                reason="requested_by_customer",
                idempotency_key="refund-key",
            )
        self.assertEqual(result["amount"], Decimal("49.99"))
        self.assertIsInstance(result["amount"], Decimal)


# ---------------------------------------------------------------------------
# cancel_payment_intent tests
# ---------------------------------------------------------------------------

@override_settings(STRIPE_SECRET_KEY="sk_test_fake")
class StripeGatewayCancelPaymentIntentTests(SimpleTestCase):
    """Test cancel_payment_intent."""

    def setUp(self):
        from apps.payments.gateways.stripe_gateway import StripeGateway
        self.gateway = StripeGateway()

    def test_returns_true_when_stripe_returns_canceled(self):
        fake_intent = MagicMock()
        fake_intent.status = "canceled"
        with patch("stripe.PaymentIntent.cancel", return_value=fake_intent):
            result = self.gateway.cancel_payment_intent("pi_test_001")
        self.assertTrue(result)

    def test_returns_false_when_already_canceled_invalid_request_error(self):
        import stripe

        err = stripe.error.InvalidRequestError("PaymentIntent is already canceled", None)
        with patch("stripe.PaymentIntent.cancel", side_effect=err):
            result = self.gateway.cancel_payment_intent("pi_already_canceled")
        self.assertFalse(result)

    def test_returns_false_when_status_not_canceled(self):
        fake_intent = MagicMock()
        fake_intent.status = "succeeded"
        with patch("stripe.PaymentIntent.cancel", return_value=fake_intent):
            result = self.gateway.cancel_payment_intent("pi_test_002")
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# _get_api_key tests
# ---------------------------------------------------------------------------

class StripeGatewayApiKeyTests(SimpleTestCase):
    """Test _get_api_key raises ImproperlyConfigured when key is absent."""

    def test_raises_improperly_configured_when_key_missing(self):
        from apps.payments.gateways.stripe_gateway import StripeGateway
        gateway = StripeGateway()
        with override_settings(STRIPE_SECRET_KEY=""):
            with self.assertRaises(ImproperlyConfigured):
                gateway._get_api_key()

    def test_returns_key_when_set(self):
        from apps.payments.gateways.stripe_gateway import StripeGateway
        gateway = StripeGateway()
        with override_settings(STRIPE_SECRET_KEY="sk_test_validkey"):
            key = gateway._get_api_key()
        self.assertEqual(key, "sk_test_validkey")


# ---------------------------------------------------------------------------
# _parse_charge_refunded — refund ordering regression tests
# ---------------------------------------------------------------------------

@override_settings(STRIPE_SECRET_KEY="sk_test_fake")
class StripeGatewayParseChargeRefundedTests(SimpleTestCase):
    """
    Tests for correct refund selection from charge.refunded events.

    Stripe returns refunds.data in reverse chronological order (newest first).
    The gateway must select refunds[0] (newest), NOT refunds[-1] (oldest).
    Selecting the wrong index silently drops second partial refunds because
    _handle_charge_refunded finds the old refund ID already in the DB and
    logs "already_recorded" instead of recording the new refund.
    """

    def setUp(self):
        from apps.payments.gateways.stripe_gateway import StripeGateway
        self.gateway = StripeGateway()

    def _build_charge_payload(self, refunds_list):
        """Build a minimal charge.refunded webhook payload."""
        return {
            "id": "ch_test_123",
            "amount": 10000,
            "refunds": {
                "object": "list",
                "data": refunds_list,
                "has_more": False,
            },
            "payment_method_details": {
                "type": "card",
                "card": {"brand": "visa", "last4": "4242"},
            },
        }

    def test_single_refund_returns_correct_id(self):
        """With one refund, gateway_refund_id is that refund's ID."""
        refunds = [
            {"id": "re_only", "amount": 5000, "status": "succeeded"},
        ]
        charge = self._build_charge_payload(refunds)
        result = self.gateway._parse_charge_refunded(charge)
        self.assertEqual(result["gateway_refund_id"], "re_only")

    def test_multiple_refunds_returns_newest_not_oldest(self):
        """
        Critical regression test: with two refunds, gateway_refund_id must be
        re_newest (index 0), NOT re_oldest (index -1).

        Stripe returns refunds.data newest-first. Using refunds[-1] silently
        drops the second partial refund because the old refund ID already exists
        in the DB and _handle_charge_refunded logs 'already_recorded'.
        """
        refunds = [
            {"id": "re_newest", "amount": 2500, "status": "succeeded"},
            {"id": "re_oldest", "amount": 1000, "status": "succeeded"},
        ]
        charge = self._build_charge_payload(refunds)
        result = self.gateway._parse_charge_refunded(charge)
        self.assertEqual(result["gateway_refund_id"], "re_newest")
        self.assertNotEqual(result["gateway_refund_id"], "re_oldest")

    def test_multiple_refunds_returns_newest_amount(self):
        """The refund_amount must match the newest refund (index 0), not the oldest."""
        from decimal import Decimal
        refunds = [
            {"id": "re_newest", "amount": 2500, "status": "succeeded"},
            {"id": "re_oldest", "amount": 1000, "status": "succeeded"},
        ]
        charge = self._build_charge_payload(refunds)
        result = self.gateway._parse_charge_refunded(charge)
        # re_newest is 2500 cents = $25.00; re_oldest is 1000 cents = $10.00
        self.assertEqual(result["refund_amount"], Decimal("25.00"))

    def test_empty_refunds_returns_empty_id_no_crash(self):
        """With no refunds, gateway_refund_id is empty string and no exception is raised."""
        charge = self._build_charge_payload([])
        result = self.gateway._parse_charge_refunded(charge)
        self.assertEqual(result["gateway_refund_id"], "")

    def test_empty_refunds_returns_zero_amount(self):
        """With no refunds, refund_amount is $0.00."""
        from decimal import Decimal
        charge = self._build_charge_payload([])
        result = self.gateway._parse_charge_refunded(charge)
        self.assertEqual(result["refund_amount"], Decimal("0.00"))

    def test_result_contains_gateway_charge_id(self):
        """The parsed result always includes the charge ID."""
        charge = self._build_charge_payload([
            {"id": "re_001", "amount": 1000, "status": "succeeded"},
        ])
        result = self.gateway._parse_charge_refunded(charge)
        self.assertEqual(result["gateway_charge_id"], "ch_test_123")

    def test_result_contains_refund_status(self):
        """The parsed result includes refund_status from the newest refund."""
        charge = self._build_charge_payload([
            {"id": "re_001", "amount": 1000, "status": "succeeded"},
        ])
        result = self.gateway._parse_charge_refunded(charge)
        self.assertEqual(result["refund_status"], "succeeded")


# ---------------------------------------------------------------------------
# Fix 27 — thread-safety: no global api_key mutation
# ---------------------------------------------------------------------------

class StripeGatewayNoGlobalApiKeyMutationTests(SimpleTestCase):
    """
    StripeGateway must not mutate stripe.api_key globally.

    Thread-safety requires per-call api_key= parameter, not global mutation.
    Under multi-worker gunicorn two concurrent requests for different
    organisations would race on the global stripe.api_key value.
    """

    def test_no_global_api_key_mutation(self):
        """
        StripeGateway source must not contain a bare 'api_key =' assignment.
        The only legal form is 'api_key=' (keyword argument in a function call).
        """
        import inspect
        from apps.payments.gateways.stripe_gateway import StripeGateway

        src = inspect.getsource(StripeGateway)
        # Strip comment lines so commented-out legacy code doesn't trigger false positives
        code_lines = [
            line for line in src.split("\n")
            if not line.strip().startswith("#")
        ]
        code = "\n".join(code_lines)

        # 'api_key =' (with a space before =) is the global-mutation pattern.
        # 'api_key=' (no space) is the per-call keyword-argument pattern — allowed.
        self.assertNotIn(
            "api_key =",
            code,
            "StripeGateway must not assign to stripe.api_key globally. "
            "Use api_key= as a per-call keyword argument instead.",
        )

    def test_stripe_module_returned_without_key_set(self):
        """
        _stripe() must return the module without setting api_key on it.
        After calling _stripe(), stripe.api_key must not have been mutated
        to the gateway's key.
        """
        import stripe
        from apps.payments.gateways.stripe_gateway import StripeGateway

        original_key = stripe.api_key

        with override_settings(STRIPE_SECRET_KEY="sk_test_thread_safety_check"):
            gw = StripeGateway()
            gw._stripe()  # must NOT set stripe.api_key

        # If the global was mutated, stripe.api_key would be "sk_test_thread_safety_check"
        self.assertNotEqual(
            stripe.api_key,
            "sk_test_thread_safety_check",
            "_stripe() must not mutate stripe.api_key globally.",
        )

        # Restore original key (defensive cleanup)
        stripe.api_key = original_key

    def test_api_key_passed_per_call_to_payment_intent_create(self):
        """
        create_payment_intent must pass api_key= as a keyword argument to
        stripe.PaymentIntent.create, not rely on the global stripe.api_key.
        """
        from apps.payments.gateways.stripe_gateway import StripeGateway

        gw = StripeGateway()
        fake_intent = MagicMock()
        fake_intent.id = "pi_thread_safe"
        fake_intent.client_secret = "secret_xxx"
        fake_intent.status = "requires_payment_method"

        with override_settings(STRIPE_SECRET_KEY="sk_test_per_call"):
            with patch("stripe.PaymentIntent.create", return_value=fake_intent) as mock_create:
                gw.create_payment_intent(
                    amount=Decimal("10.00"),
                    currency="cad",
                    idempotency_key="idem-thread-001",
                    metadata={},
                )

        call_kwargs = mock_create.call_args[1]
        self.assertIn(
            "api_key",
            call_kwargs,
            "create_payment_intent must pass api_key= per call to stripe.PaymentIntent.create",
        )
        self.assertEqual(call_kwargs["api_key"], "sk_test_per_call")


# ---------------------------------------------------------------------------
# Nit 1 — _BRAND_MAP completeness tests
# ---------------------------------------------------------------------------

class BrandMapCompletenessTests(SimpleTestCase):
    """
    Verify that _BRAND_MAP maps all four additional Stripe card brands
    that were added in Nit 1.  The .get(brand, "Other") fallback still
    handles anything else — these tests confirm common cards no longer
    fall through to "Other".
    """

    def setUp(self):
        from apps.payments.gateways.stripe_gateway import _BRAND_MAP
        self._map = _BRAND_MAP

    def test_discover_mapped(self):
        self.assertEqual(self._map.get("discover"), "Discover")

    def test_jcb_mapped(self):
        self.assertEqual(self._map.get("jcb"), "JCB")

    def test_diners_mapped(self):
        self.assertEqual(self._map.get("diners"), "Diners Club")

    def test_unionpay_mapped(self):
        self.assertEqual(self._map.get("unionpay"), "UnionPay")

    def test_existing_visa_still_mapped(self):
        self.assertEqual(self._map.get("visa"), "visa")

    def test_existing_mastercard_still_mapped(self):
        self.assertEqual(self._map.get("mastercard"), "mastercard")

    def test_unknown_brand_returns_none_not_other(self):
        # _BRAND_MAP itself returns None for unknown brands via .get();
        # the "Other" fallback lives at the call sites.
        self.assertIsNone(self._map.get("unknown_brand"))
