"""Tests for GatewayError exception hierarchy."""

from django.test import SimpleTestCase

from apps.payments.gateways.exceptions import (
    GatewayAuthError,
    GatewayCardError,
    GatewayError,
    GatewayIdempotencyError,
    GatewayNetworkError,
    GatewayRateLimitError,
    GatewayWebhookError,
)


class GatewayErrorBaseTests(SimpleTestCase):
    """Test GatewayError base class."""

    def test_base_error_message(self):
        exc = GatewayError("test message")
        self.assertEqual(str(exc), "test message")

    def test_base_error_is_exception(self):
        exc = GatewayError("msg")
        self.assertIsInstance(exc, Exception)

    def test_base_error_gateway_code_kwarg(self):
        exc = GatewayError("msg", gateway_code="card_declined")
        self.assertEqual(exc.gateway_code, "card_declined")

    def test_base_error_decline_code_kwarg(self):
        exc = GatewayError("msg", decline_code="insufficient_funds")
        self.assertEqual(exc.decline_code, "insufficient_funds")

    def test_defaults_are_none(self):
        exc = GatewayError("msg")
        self.assertIsNone(exc.gateway_code)
        self.assertIsNone(exc.decline_code)

    def test_both_codes_set(self):
        exc = GatewayError("msg", gateway_code="card_declined", decline_code="do_not_honor")
        self.assertEqual(exc.gateway_code, "card_declined")
        self.assertEqual(exc.decline_code, "do_not_honor")

    def test_message_stored_in_args(self):
        exc = GatewayError("test message")
        self.assertEqual(exc.args[0], "test message")

    def test_can_be_raised_and_caught(self):
        with self.assertRaises(GatewayError):
            raise GatewayError("boom")


class GatewayAuthErrorTests(SimpleTestCase):
    """Test GatewayAuthError subclass."""

    def test_is_gateway_error(self):
        exc = GatewayAuthError("bad key")
        self.assertIsInstance(exc, GatewayError)

    def test_is_exception(self):
        exc = GatewayAuthError("bad key")
        self.assertIsInstance(exc, Exception)

    def test_message(self):
        exc = GatewayAuthError("Invalid API key")
        self.assertEqual(str(exc), "Invalid API key")

    def test_gateway_code(self):
        exc = GatewayAuthError("msg", gateway_code="authentication_error")
        self.assertEqual(exc.gateway_code, "authentication_error")

    def test_defaults(self):
        exc = GatewayAuthError("msg")
        self.assertIsNone(exc.gateway_code)
        self.assertIsNone(exc.decline_code)


class GatewayCardErrorTests(SimpleTestCase):
    """Test GatewayCardError subclass."""

    def test_is_gateway_error(self):
        exc = GatewayCardError("card declined")
        self.assertIsInstance(exc, GatewayError)

    def test_message(self):
        exc = GatewayCardError("Your card was declined.")
        self.assertEqual(str(exc), "Your card was declined.")

    def test_decline_code(self):
        exc = GatewayCardError(
            "declined",
            gateway_code="card_declined",
            decline_code="insufficient_funds",
        )
        self.assertEqual(exc.decline_code, "insufficient_funds")

    def test_gateway_code(self):
        exc = GatewayCardError("declined", gateway_code="card_declined")
        self.assertEqual(exc.gateway_code, "card_declined")

    def test_defaults(self):
        exc = GatewayCardError("msg")
        self.assertIsNone(exc.decline_code)

    def test_can_be_caught_as_gateway_error(self):
        with self.assertRaises(GatewayError):
            raise GatewayCardError("declined", decline_code="insufficient_funds")


class GatewayNetworkErrorTests(SimpleTestCase):
    """Test GatewayNetworkError subclass."""

    def test_is_gateway_error(self):
        exc = GatewayNetworkError("timeout")
        self.assertIsInstance(exc, GatewayError)

    def test_message(self):
        exc = GatewayNetworkError("connection refused")
        self.assertEqual(str(exc), "connection refused")

    def test_gateway_code(self):
        exc = GatewayNetworkError("msg", gateway_code="network_error")
        self.assertEqual(exc.gateway_code, "network_error")

    def test_defaults(self):
        exc = GatewayNetworkError("msg")
        self.assertIsNone(exc.gateway_code)
        self.assertIsNone(exc.decline_code)


class GatewayRateLimitErrorTests(SimpleTestCase):
    """Test GatewayRateLimitError subclass."""

    def test_is_gateway_error(self):
        exc = GatewayRateLimitError("too many requests")
        self.assertIsInstance(exc, GatewayError)

    def test_message(self):
        exc = GatewayRateLimitError("Rate limit exceeded")
        self.assertEqual(str(exc), "Rate limit exceeded")

    def test_gateway_code(self):
        exc = GatewayRateLimitError("msg", gateway_code="rate_limit")
        self.assertEqual(exc.gateway_code, "rate_limit")

    def test_defaults(self):
        exc = GatewayRateLimitError("msg")
        self.assertIsNone(exc.gateway_code)
        self.assertIsNone(exc.decline_code)


class GatewayWebhookErrorTests(SimpleTestCase):
    """Test GatewayWebhookError subclass."""

    def test_is_gateway_error(self):
        exc = GatewayWebhookError("invalid signature")
        self.assertIsInstance(exc, GatewayError)

    def test_message(self):
        exc = GatewayWebhookError("Webhook payload missing 'type' field.")
        self.assertEqual(str(exc), "Webhook payload missing 'type' field.")

    def test_gateway_code(self):
        exc = GatewayWebhookError("msg", gateway_code="signature_invalid")
        self.assertEqual(exc.gateway_code, "signature_invalid")

    def test_defaults(self):
        exc = GatewayWebhookError("msg")
        self.assertIsNone(exc.gateway_code)
        self.assertIsNone(exc.decline_code)


class GatewayIdempotencyErrorTests(SimpleTestCase):
    """Test GatewayIdempotencyError subclass."""

    def test_is_gateway_error(self):
        exc = GatewayIdempotencyError("key reused")
        self.assertIsInstance(exc, GatewayError)

    def test_message(self):
        exc = GatewayIdempotencyError("Idempotency key reused with different params")
        self.assertEqual(str(exc), "Idempotency key reused with different params")

    def test_gateway_code(self):
        exc = GatewayIdempotencyError("msg", gateway_code="idempotency_error")
        self.assertEqual(exc.gateway_code, "idempotency_error")

    def test_defaults(self):
        exc = GatewayIdempotencyError("msg")
        self.assertIsNone(exc.gateway_code)


class AllSubclassesInheritanceTests(SimpleTestCase):
    """Verify all subclasses inherit from GatewayError."""

    ALL_SUBCLASSES = [  # noqa: RUF012
        GatewayAuthError,
        GatewayCardError,
        GatewayNetworkError,
        GatewayRateLimitError,
        GatewayWebhookError,
        GatewayIdempotencyError,
    ]

    def test_all_subclasses_are_gateway_error(self):
        for cls in self.ALL_SUBCLASSES:
            with self.subTest(cls=cls.__name__):
                exc = cls("msg")
                self.assertIsInstance(exc, GatewayError)

    def test_all_subclasses_are_exceptions(self):
        for cls in self.ALL_SUBCLASSES:
            with self.subTest(cls=cls.__name__):
                exc = cls("msg")
                self.assertIsInstance(exc, Exception)

    def test_all_subclasses_have_gateway_code(self):
        for cls in self.ALL_SUBCLASSES:
            with self.subTest(cls=cls.__name__):
                exc = cls("msg", gateway_code="test_code")
                self.assertEqual(exc.gateway_code, "test_code")

    def test_all_subclasses_have_decline_code(self):
        for cls in self.ALL_SUBCLASSES:
            with self.subTest(cls=cls.__name__):
                exc = cls("msg", decline_code="test_decline")
                self.assertEqual(exc.decline_code, "test_decline")

    def test_all_subclasses_can_be_raised_and_caught_as_gateway_error(self):
        for cls in self.ALL_SUBCLASSES:
            with self.subTest(cls=cls.__name__):
                with self.assertRaises(GatewayError):
                    raise cls("msg")
