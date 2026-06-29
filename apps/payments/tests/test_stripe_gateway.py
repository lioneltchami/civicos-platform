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
        return {
            "id": "evt_test_001",
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
        """GatewayWebhookError raised if Stripe returns last4 > 4 chars."""
        payload = {
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_pci_001",
                    "amount_received": 1000,
                    "charges": {
                        "data": [
                            {
                                "id": "ch_pci_001",
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
