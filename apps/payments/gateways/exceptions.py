"""
Gateway-specific exceptions for the Payments BB.
All gateway adapters raise these — callers never catch stripe.error.* directly.
"""


class GatewayError(Exception):
    """Base class for all gateway errors."""

    def __init__(self, message, gateway_code=None, decline_code=None) -> None:  # noqa: ANN001
        super().__init__(message)
        self.gateway_code = gateway_code  # e.g. "card_declined"
        self.decline_code = decline_code  # e.g. "insufficient_funds"


class GatewayAuthError(GatewayError):
    """Invalid API key or authentication failure."""


class GatewayCardError(GatewayError):
    """Card was declined. decline_code gives the specific reason."""


class GatewayNetworkError(GatewayError):
    """Temporary network/timeout error — safe to retry."""


class GatewayRateLimitError(GatewayError):
    """Rate limit hit — back off and retry."""


class GatewayWebhookError(GatewayError):
    """Webhook signature verification failed or payload is malformed."""


class GatewayIdempotencyError(GatewayError):
    """Idempotency key reused with different parameters."""
