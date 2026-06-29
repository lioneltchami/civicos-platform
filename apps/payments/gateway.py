"""
Abstract payment gateway interface for the GovStack Payments BB.

All concrete gateway implementations (StripeGateway, MonerisGateway) must
implement this interface. Application code imports only this interface and
calls get_gateway() — it never imports concrete gateway classes directly.

Usage:
    from apps.payments.gateway import get_gateway
    gw = get_gateway()
    result = gw.create_payment_intent(amount=Decimal("50.00"), ...)
"""
import abc
import functools
from decimal import Decimal
from typing import Optional


class PaymentGateway(abc.ABC):
    """
    Abstract interface for payment gateway adapters.

    All monetary amounts are Decimal in CAD.
    All methods raise GatewayError subclasses (never gateway-specific exceptions).
    """

    @abc.abstractmethod
    def create_payment_intent(
        self,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
        metadata: dict,
        description: str = "",
        connect_account_id: Optional[str] = None,
    ) -> dict:
        """
        Create a payment intent on the gateway.

        Args:
            amount: Amount in CAD (e.g. Decimal("49.99"))
            currency: Always "cad"
            idempotency_key: UUID string — gateway uses this to deduplicate retries
            metadata: Dict of key-value pairs attached to the intent (max 50 keys,
                      500 chars per value on Stripe). Never include PII here.
            description: Human-readable description (appears on Stripe dashboard)
            connect_account_id: Stripe Connect account ID if use_connect=True, else None

        Returns:
            {
                "gateway_intent_id": str,   # e.g. "pi_xxx"
                "client_secret": str,       # for Stripe.js / Elements front-end
                "status": str,              # "requires_payment_method"
            }

        Raises:
            GatewayAuthError: Invalid API key
            GatewayNetworkError: Timeout or connectivity issue
            GatewayIdempotencyError: Idempotency key reused with different params
        """

    @abc.abstractmethod
    def retrieve_payment_intent(self, gateway_intent_id: str) -> dict:
        """
        Retrieve current state of a payment intent from the gateway.

        Args:
            gateway_intent_id: Gateway-assigned ID (e.g. "pi_xxx")

        Returns:
            {
                "gateway_intent_id": str,
                "status": str,
                "amount": Decimal,
                "currency": str,
                "gateway_charge_id": Optional[str],   # set when completed
                "failure_reason": Optional[str],
            }

        Raises:
            GatewayError: Intent not found or network error
        """

    @abc.abstractmethod
    def create_refund(
        self,
        gateway_charge_id: str,
        amount: Decimal,
        reason: str,
        idempotency_key: str,
    ) -> dict:
        """
        Issue a refund against a completed charge.

        Args:
            gateway_charge_id: The charge/payment ID to refund
            amount: Amount to refund in CAD — must be <= original charge amount
            reason: One of "duplicate", "fraudulent", "requested_by_customer"
            idempotency_key: UUID string for deduplication

        Returns:
            {
                "gateway_refund_id": str,
                "status": str,           # "succeeded" | "pending" | "failed"
                "amount": Decimal,
            }

        Raises:
            GatewayError: Charge not found, amount exceeds original, etc.
        """

    @abc.abstractmethod
    def cancel_payment_intent(self, gateway_intent_id: str) -> bool:
        """
        Cancel a payment intent that has not yet been confirmed.

        Args:
            gateway_intent_id: Gateway-assigned ID

        Returns:
            True if successfully cancelled, False if already in a terminal state

        Raises:
            GatewayError: Network error or intent not found
        """

    @abc.abstractmethod
    def create_subscription(
        self,
        customer_id: str,
        price_id: str,
        payment_method_id: str,
        idempotency_key: str,
        metadata: dict,
        connect_account_id: Optional[str] = None,
    ) -> dict:
        """
        Create a recurring subscription (recurring gift plan).

        Args:
            customer_id: Gateway customer ID
            price_id: Gateway price/plan ID
            payment_method_id: Tokenized payment method (never a raw card number)
            idempotency_key: UUID string
            metadata: Dict attached to the subscription
            connect_account_id: Connect account ID if use_connect=True

        Returns:
            {
                "gateway_subscription_id": str,
                "status": str,           # "active" | "incomplete" | etc.
                "current_period_end": str,  # ISO datetime
            }

        Raises:
            GatewayCardError: Payment method declined
            GatewayError: Other gateway error
        """

    @abc.abstractmethod
    def cancel_subscription(self, gateway_subscription_id: str) -> bool:
        """
        Cancel a subscription immediately.

        Args:
            gateway_subscription_id: Gateway subscription ID

        Returns:
            True if cancelled, False if already cancelled

        Raises:
            GatewayError: Subscription not found or network error
        """

    @abc.abstractmethod
    def verify_webhook_signature(
        self,
        payload_bytes: bytes,
        signature_header: str,
        webhook_secret: str,
    ) -> bool:
        """
        Verify that a webhook payload was sent by the gateway (not forged).

        MUST be called before ANY processing of the webhook payload.
        MUST return False (not raise) if signature is invalid.

        Args:
            payload_bytes: Raw request body bytes (before any JSON parsing)
            signature_header: Value of the gateway's signature header
                              (e.g. "Stripe-Signature" header value)
            webhook_secret: The webhook endpoint signing secret

        Returns:
            True if signature is valid, False if invalid or if any error occurs

        Raises:
            Never — all errors result in False return value
        """

    @abc.abstractmethod
    def parse_webhook_event(self, payload: dict) -> tuple:
        """
        Extract event type and structured data from a verified webhook payload.

        Only call this AFTER verify_webhook_signature returns True.

        Args:
            payload: Parsed JSON dict of the webhook body

        Returns:
            (event_type: str, event_data: dict)

            event_type examples:
                "payment_intent.succeeded"
                "payment_intent.payment_failed"
                "charge.refunded"
                "customer.subscription.deleted"
                "customer.subscription.updated"

            event_data: Normalized dict relevant to the event type, e.g.:
                For payment_intent.succeeded:
                {
                    "gateway_intent_id": str,
                    "gateway_charge_id": str,
                    "amount_paid": Decimal,
                    "processor_fee": Decimal,
                    "net_amount": Decimal,
                    "payment_method_type": str,
                    "card_last_four": Optional[str],
                    "card_brand": Optional[str],
                    "paid_at": str,   # ISO datetime string
                }
                For payment_intent.payment_failed:
                {
                    "gateway_intent_id": str,
                    "failure_reason": str,
                }

        Raises:
            GatewayWebhookError: Payload is structurally invalid
        """


@functools.lru_cache(maxsize=1)
def get_gateway() -> PaymentGateway:
    """
    Return the configured payment gateway instance.

    Reads PAYMENT_GATEWAY setting (default: "stripe").
    The result is cached per-process via lru_cache — instantiation happens
    once on first call. Note: the cached instance uses the API key from
    settings at the time of the first call. API key rotation requires a
    process restart (or cache_clear() in tests).

    Usage:
        from apps.payments.gateway import get_gateway
        gw = get_gateway()
    """
    from django.conf import settings
    gateway_name = getattr(settings, "PAYMENT_GATEWAY", "stripe")

    if gateway_name == "stripe":
        from apps.payments.gateways.stripe_gateway import StripeGateway
        return StripeGateway()
    else:
        raise ValueError(f"Unknown payment gateway: {gateway_name!r}")
