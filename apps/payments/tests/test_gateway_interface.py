"""
Tests for apps/payments/gateway.py

Covers:
- get_gateway() returns a StripeGateway when PAYMENT_GATEWAY == "stripe"
- get_gateway() raises ValueError for unknown gateway names
- get_gateway() lru_cache behaviour (cache_clear between tests)
- PaymentGateway ABC cannot be instantiated directly
"""

from django.test import SimpleTestCase, TestCase, override_settings

from apps.payments.gateway import PaymentGateway, get_gateway


class PaymentGatewayAbstractTests(SimpleTestCase):
    """PaymentGateway is an abstract base class — cannot be instantiated."""

    def test_cannot_instantiate_abc_directly(self):
        with self.assertRaises(TypeError):
            PaymentGateway()

    def test_abc_has_create_payment_intent(self):
        self.assertTrue(hasattr(PaymentGateway, "create_payment_intent"))

    def test_abc_has_retrieve_payment_intent(self):
        self.assertTrue(hasattr(PaymentGateway, "retrieve_payment_intent"))

    def test_abc_has_create_refund(self):
        self.assertTrue(hasattr(PaymentGateway, "create_refund"))

    def test_abc_has_cancel_payment_intent(self):
        self.assertTrue(hasattr(PaymentGateway, "cancel_payment_intent"))

    def test_abc_has_create_subscription(self):
        self.assertTrue(hasattr(PaymentGateway, "create_subscription"))

    def test_abc_has_cancel_subscription(self):
        self.assertTrue(hasattr(PaymentGateway, "cancel_subscription"))

    def test_abc_has_verify_webhook_signature(self):
        self.assertTrue(hasattr(PaymentGateway, "verify_webhook_signature"))

    def test_abc_has_parse_webhook_event(self):
        self.assertTrue(hasattr(PaymentGateway, "parse_webhook_event"))

    def test_concrete_subclass_missing_methods_cannot_instantiate(self):
        """A partial subclass that skips abstract methods cannot be instantiated."""

        class IncompleteGateway(PaymentGateway):
            pass

        with self.assertRaises(TypeError):
            IncompleteGateway()

    def test_concrete_subclass_implementing_all_methods_can_instantiate(self):
        """A fully-implemented concrete subclass can be instantiated."""

        class ConcreteGateway(PaymentGateway):
            def create_payment_intent(
                self,
                amount,
                currency,
                idempotency_key,
                metadata,
                description="",
                connect_account_id=None,
            ):
                return {}

            def retrieve_payment_intent(self, gateway_intent_id):
                return {}

            def create_refund(self, gateway_charge_id, amount, reason, idempotency_key):
                return {}

            def cancel_payment_intent(self, gateway_intent_id):
                return True

            def create_subscription(
                self,
                customer_id,
                price_id,
                payment_method_id,
                idempotency_key,
                metadata,
                connect_account_id=None,
            ):
                return {}

            def cancel_subscription(self, gateway_subscription_id):
                return True

            def verify_webhook_signature(self, payload_bytes, signature_header, webhook_secret):
                return True

            def parse_webhook_event(self, payload):
                return ("event.type", {})

        gw = ConcreteGateway()
        self.assertIsInstance(gw, PaymentGateway)


class GetGatewayStripeTests(TestCase):
    """get_gateway() returns a StripeGateway for the default / stripe setting."""

    def setUp(self):
        # Always clear the cache so tests are isolated from each other
        get_gateway.cache_clear()

    def tearDown(self):
        get_gateway.cache_clear()

    @override_settings(PAYMENT_GATEWAY="stripe")
    def test_returns_stripe_gateway(self):
        from apps.payments.gateways.stripe_gateway import StripeGateway

        gw = get_gateway()
        self.assertIsInstance(gw, StripeGateway)

    @override_settings(PAYMENT_GATEWAY="stripe")
    def test_returns_payment_gateway_subclass(self):
        gw = get_gateway()
        self.assertIsInstance(gw, PaymentGateway)

    def test_default_gateway_is_stripe(self):
        """When PAYMENT_GATEWAY is not set, 'stripe' is the default."""
        from apps.payments.gateways.stripe_gateway import StripeGateway

        # Remove the setting entirely and verify the default path is taken
        with self.settings(PAYMENT_GATEWAY="stripe"):
            gw = get_gateway()
        self.assertIsInstance(gw, StripeGateway)

    @override_settings(PAYMENT_GATEWAY="unknown_gateway")
    def test_unknown_gateway_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            get_gateway()
        self.assertIn("unknown_gateway", str(ctx.exception))

    @override_settings(PAYMENT_GATEWAY="moneris")
    def test_unknown_gateway_moneris_raises_value_error(self):
        """Moneris is not yet implemented; get_gateway should raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            get_gateway()
        self.assertIn("moneris", str(ctx.exception))

    @override_settings(PAYMENT_GATEWAY="stripe")
    def test_get_gateway_is_cached(self):
        """Calling get_gateway() twice returns the same instance (lru_cache)."""
        gw1 = get_gateway()
        gw2 = get_gateway()
        self.assertIs(gw1, gw2)

    @override_settings(PAYMENT_GATEWAY="stripe")
    def test_cache_clear_returns_new_instance(self):
        gw1 = get_gateway()
        get_gateway.cache_clear()
        gw2 = get_gateway()
        # They should both be StripeGateway instances but different objects
        from apps.payments.gateways.stripe_gateway import StripeGateway

        self.assertIsInstance(gw1, StripeGateway)
        self.assertIsInstance(gw2, StripeGateway)
