"""
Stripe payment gateway adapter for GovStack Payments BB.

Uses Stripe Connect Standard accounts (OQ-1 resolution).
Stripe is always the primary gateway (Phase 1). Moneris is Phase 2.

PCI DSS: Raw card numbers never touch GovStack servers. Stripe Elements
tokenizes card data in a Stripe-owned iframe. This adapter receives only
payment method tokens and PaymentIntent client_secrets.

Stripe pricing for Canadian non-profits: 2.2% + $0.30 (Stripe.org discount).
Apply via Stripe dashboard — no code change required.
"""
import datetime
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from apps.payments.gateways.exceptions import (
    GatewayAuthError,
    GatewayCardError,
    GatewayError,
    GatewayIdempotencyError,
    GatewayNetworkError,
    GatewayRateLimitError,
    GatewayWebhookError,
)
from apps.payments.gateway import PaymentGateway

logger = logging.getLogger(__name__)

# Stripe card brand mapping: Stripe brand string → our model's card_brand choice
_BRAND_MAP = {
    "visa": "visa",
    "mastercard": "mastercard",
    "amex": "amex",
    "interac": "interac",
    "discover": "discover",
    "jcb": "jcb",
    "diners": "diners",
    "unionpay": "unionpay",
}


def _to_cents(amount: Decimal) -> int:
    """Convert CAD Decimal to Stripe cents integer. Rounds half-up."""
    return int(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)


def _from_cents(cents: int) -> Decimal:
    """Convert Stripe cents integer to CAD Decimal."""
    return Decimal(cents) / Decimal(100)


class StripeGateway(PaymentGateway):
    """
    Stripe Connect Standard gateway implementation.

    Thread-safe — no mutable instance state. Safe to use as a singleton.
    All Stripe API errors are caught and re-raised as GatewayError subclasses.
    """

    # FIX 6: Pin the Stripe API version as a class constant so it is defined
    # in one place and not scattered across method bodies.
    _STRIPE_API_VERSION = "2024-06-20"

    def _get_api_key(self) -> str:
        """
        Return the Stripe secret key.
        Prefers TenantPaymentConfig DB value; falls back to settings.STRIPE_SECRET_KEY.
        Raises ImproperlyConfigured if neither is set.
        """
        from django.conf import settings
        from django.core.exceptions import ImproperlyConfigured

        try:
            from apps.payments.models import TenantPaymentConfig  # noqa: F401 — import verifies model exists
            # TenantPaymentConfig stores publishable key, not secret key.
            # Secret key always comes from environment for security.
        except Exception:
            pass

        key = getattr(settings, "STRIPE_SECRET_KEY", "")
        if not key:
            raise ImproperlyConfigured(
                "STRIPE_SECRET_KEY is not configured. "
                "Set it as an environment variable."
            )
        return key

    def _get_connect_account(self) -> Optional[str]:
        """Return the Stripe Connect account ID if use_connect=True, else None."""
        try:
            from apps.payments.models import TenantPaymentConfig
            config = TenantPaymentConfig.get_solo()
            if config.use_connect and config.stripe_connect_account_id:
                return config.stripe_connect_account_id
        except Exception:
            pass
        return None

    def _api_key(self) -> str:
        """
        Return the Stripe API key for this gateway instance.
        Never mutated globally — callers pass it as a per-call keyword argument
        to avoid thread-safety hazards under multi-worker gunicorn deployments.
        """
        return self._get_api_key()

    def _stripe(self):
        """
        Return the stripe module without mutating any global state.

        FIX 27: Previously this method set stripe.api_key globally, which is
        not thread-safe under multi-worker gunicorn: two concurrent requests
        for different organisations would race on that single global value.
        The fix passes api_key= as a per-call keyword argument on every
        Stripe API call instead (supported by stripe-python SDK v2+).
        """
        try:
            import stripe as _stripe_module
            return _stripe_module
        except ImportError:
            raise ImportError(
                "The 'stripe' package is required for StripeGateway. "
                "Install it: pip install stripe"
            )

    def _handle_stripe_error(self, exc):
        """
        Convert stripe.error.* exceptions to GatewayError subclasses.
        Logs error TYPE only — never the full message, which may contain PII.
        """
        import stripe

        logger.error(
            "payments.gateway.stripe_error type=%s gateway_code=%s",
            type(exc).__name__,
            getattr(exc, "code", "unknown"),
        )

        if isinstance(exc, stripe.error.AuthenticationError):
            raise GatewayAuthError(str(exc), gateway_code="authentication_error") from exc
        if isinstance(exc, stripe.error.CardError):
            raise GatewayCardError(
                exc.user_message or str(exc),
                gateway_code=exc.code,
                decline_code=exc.decline_code,
            ) from exc
        if isinstance(exc, stripe.error.RateLimitError):
            raise GatewayRateLimitError(str(exc), gateway_code="rate_limit") from exc
        if isinstance(exc, stripe.error.APIConnectionError):
            raise GatewayNetworkError(str(exc), gateway_code="network_error") from exc
        if isinstance(exc, stripe.error.IdempotencyError):
            raise GatewayIdempotencyError(str(exc), gateway_code="idempotency_error") from exc
        if isinstance(exc, stripe.error.SignatureVerificationError):
            raise GatewayWebhookError(str(exc), gateway_code="signature_invalid") from exc
        # Generic Stripe error
        raise GatewayError(str(exc), gateway_code=getattr(exc, "code", None)) from exc

    # ------------------------------------------------------------------ #
    # Interface implementation                                             #
    # ------------------------------------------------------------------ #

    def create_payment_intent(
        self,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
        metadata: dict,
        description: str = "",
        connect_account_id: Optional[str] = None,
    ) -> dict:
        stripe = self._stripe()
        kwargs = {
            "amount": _to_cents(amount),
            "currency": currency.lower(),
            "metadata": metadata,
            "description": description or "",
            # Stripe Elements / manual confirmation
            "confirmation_method": "manual",
            "confirm": False,
        }
        if connect_account_id:
            kwargs["stripe_account"] = connect_account_id

        try:
            intent = stripe.PaymentIntent.create(
                **kwargs,
                idempotency_key=str(idempotency_key),
                api_key=self._api_key(),
            )
        except Exception as exc:
            self._handle_stripe_error(exc)

        return {
            "gateway_intent_id": intent.id,
            "client_secret": intent.client_secret,
            "status": intent.status,
        }

    def retrieve_payment_intent(self, gateway_intent_id: str) -> dict:
        stripe = self._stripe()
        try:
            intent = stripe.PaymentIntent.retrieve(
                gateway_intent_id,
                api_key=self._api_key(),
            )
        except Exception as exc:
            self._handle_stripe_error(exc)

        # Extract charge info if present
        gateway_charge_id = None
        if intent.latest_charge:
            gateway_charge_id = (
                intent.latest_charge
                if isinstance(intent.latest_charge, str)
                else intent.latest_charge.id
            )

        return {
            "gateway_intent_id": intent.id,
            "status": intent.status,
            "amount": _from_cents(intent.amount),
            "currency": intent.currency,
            "gateway_charge_id": gateway_charge_id,
            "failure_reason": (
                intent.last_payment_error.code
                if intent.last_payment_error
                else None
            ),
        }

    def create_refund(
        self,
        gateway_charge_id: str,
        amount: Decimal,
        reason: str,
        idempotency_key: str,
    ) -> dict:
        """
        Issue a refund on a previously captured charge.

        ``reason`` must be one of the Refund.REASON_CHOICES values:
        - "duplicate"              → Stripe "duplicate"
        - "fraudulent"             → Stripe "fraudulent"
        - "requested_by_customer"  → Stripe "requested_by_customer"
        - "service_not_rendered"   → Stripe "requested_by_customer"
          (Stripe does not have a "service_not_rendered" reason; we map it to
          "requested_by_customer" at the gateway level. The original reason is
          stored verbatim in our Refund row for internal audit purposes.)

        Returns a dict with keys: gateway_refund_id, status, amount.
        Raises GatewayError subclasses on Stripe API failures.
        """
        stripe = self._stripe()
        # Map our reason choices to Stripe's accepted values
        stripe_reasons = {
            "duplicate": "duplicate",
            "fraudulent": "fraudulent",
            "requested_by_customer": "requested_by_customer",
            "service_not_rendered": "requested_by_customer",  # Stripe doesn't have this choice
        }
        try:
            refund = stripe.Refund.create(
                charge=gateway_charge_id,
                amount=_to_cents(amount),
                reason=stripe_reasons.get(reason, "requested_by_customer"),
                idempotency_key=str(idempotency_key),
                api_key=self._api_key(),
            )
        except Exception as exc:
            self._handle_stripe_error(exc)

        return {
            "gateway_refund_id": refund.id,
            "status": refund.status,
            "amount": _from_cents(refund.amount),
        }

    def cancel_payment_intent(self, gateway_intent_id: str) -> bool:
        stripe = self._stripe()
        try:
            intent = stripe.PaymentIntent.cancel(
                gateway_intent_id,
                api_key=self._api_key(),
            )
            return intent.status == "canceled"
        except Exception as exc:
            # If already cancelled, return False gracefully.
            # Use exc.code (stable, versioned) — NOT string matching on exc.args[0]
            # which Stripe can change between API versions.
            # "payment_intent_unexpected_state" is the documented code Stripe raises
            # when a PaymentIntent is already in a terminal state (canceled/succeeded).
            import stripe as _stripe
            if isinstance(exc, _stripe.error.InvalidRequestError):
                if exc.code == "payment_intent_unexpected_state":
                    return False
            self._handle_stripe_error(exc)

    def create_subscription(
        self,
        customer_id: str,
        price_id: str,
        payment_method_id: str,
        idempotency_key: str,
        metadata: dict,
        connect_account_id: Optional[str] = None,
    ) -> dict:
        stripe = self._stripe()
        kwargs = {
            "customer": customer_id,
            "items": [{"price": price_id}],
            "default_payment_method": payment_method_id,
            "metadata": metadata,
            "payment_behavior": "default_incomplete",
            "expand": ["latest_invoice.payment_intent"],
        }
        if connect_account_id:
            kwargs["stripe_account"] = connect_account_id

        try:
            sub = stripe.Subscription.create(
                **kwargs,
                idempotency_key=str(idempotency_key),
                api_key=self._api_key(),
            )
        except Exception as exc:
            self._handle_stripe_error(exc)

        result = {
            "gateway_subscription_id": sub.id,
            "status": sub.status,
            "current_period_end": (
                datetime.datetime.fromtimestamp(
                    sub.current_period_end, tz=datetime.timezone.utc
                ).isoformat()
                if sub.current_period_end
                else None
            ),
        }

        if sub.status == "incomplete":
            # 3DS/SCA authentication required — the first invoice's payment intent
            # needs a client_secret so the frontend can show the challenge modal.
            # Without this, the donor silently never gets charged and Stripe marks
            # the subscription as incomplete_expired after 23 hours.
            try:
                pi = sub.latest_invoice.payment_intent
                if pi and pi.client_secret:
                    result["client_secret"] = pi.client_secret
                    result["payment_intent_id"] = pi.id
            except AttributeError:
                # latest_invoice or payment_intent not expanded — log and continue.
                # The caller will need to fetch via Subscription.retrieve(expand=...).
                logger.warning(
                    "payments.stripe.subscription_incomplete_no_pi subscription_id=%s",
                    sub.id,
                )

        return result

    def cancel_subscription(self, gateway_subscription_id: str) -> bool:
        stripe = self._stripe()
        try:
            sub = stripe.Subscription.cancel(
                gateway_subscription_id,
                api_key=self._api_key(),
            )
            return sub.status == "canceled"
        except Exception as exc:
            # Use exc.code (stable, versioned) — NOT string matching on str(exc)
            # which Stripe can change between API versions.
            # "resource_missing" is the documented code for "No such subscription".
            import stripe as _stripe
            if isinstance(exc, _stripe.error.InvalidRequestError):
                if exc.code == "resource_missing":
                    return False  # subscription already cancelled or never existed
            self._handle_stripe_error(exc)

    def verify_webhook_signature(
        self,
        payload_bytes: bytes,
        signature_header: str,
        webhook_secret: str,
    ) -> bool:
        """
        Verify Stripe webhook signature using HMAC-SHA256.
        Returns False on ANY error — never raises.

        FIX 6: Import stripe directly here rather than calling self._stripe()
        which sets the global api_key unnecessarily in the high-frequency
        webhook verification path. WebhookSignature.verify_header does not
        require an API key — it is a pure HMAC-SHA256 check.

        M3: Log in key=value format (no colon-sentence style).
        """
        if not webhook_secret:
            # Secret not configured — reject all webhooks
            logger.error(
                "payments.gateway.webhook_secret_missing "
                "rejecting_webhook=True"
            )
            return False
        try:
            import stripe as _stripe_module
            _stripe_module.WebhookSignature.verify_header(
                payload_bytes,
                signature_header,
                webhook_secret,
                tolerance=300,  # 5-minute tolerance
            )
            return True
        except Exception:
            # Broad except intentional — any error means the signature is invalid.
            # Do NOT log the exception — it may contain header contents with PII.
            return False

    def parse_webhook_event(self, payload: dict) -> tuple:
        """
        Extract (event_type, event_data) from a verified Stripe webhook payload.
        Normalises Stripe's structure into our internal format.
        Never logs PII from the payload.
        """
        event_type = payload.get("type", "")
        data_object = payload.get("data", {}).get("object", {})

        if not event_type:
            raise GatewayWebhookError("Webhook payload missing 'type' field.")

        event_data = self._normalise_event_data(event_type, data_object)
        return event_type, event_data

    def _normalise_event_data(self, event_type: str, obj: dict) -> dict:
        """
        Normalise a Stripe event object into our internal event_data format.
        Strips PII fields (billing_details.name, billing_details.email, etc.)
        before returning — this data must never be logged or stored in audit entries.
        """
        if event_type == "payment_intent.succeeded":
            return self._parse_payment_intent_succeeded(obj)
        elif event_type == "payment_intent.payment_failed":
            return self._parse_payment_intent_failed(obj)
        elif event_type == "charge.refunded":
            return self._parse_charge_refunded(obj)
        elif event_type in (
            "customer.subscription.deleted",
            "customer.subscription.updated",
        ):
            return self._parse_subscription_event(obj)
        elif event_type.startswith("invoice."):
            return self._parse_invoice_event(obj)
        else:
            # Unknown event type — return minimal normalised data
            return {"gateway_intent_id": obj.get("id", "")}

    def _parse_payment_intent_succeeded(self, obj: dict) -> dict:
        """
        Parse payment_intent.succeeded event. Strips billing PII.

        Stripe API 2024-06-20: the ``charges`` embed on PaymentIntent objects is
        deprecated and ``charges.data`` may be empty. The canonical approach is to
        read ``latest_charge`` which is a string charge ID on the PaymentIntent.

        Strategy:
        1. Prefer ``latest_charge`` (string ID) — current API (2022-11-15+).
           Card details are sourced from ``latest_charge_expanded`` if the event
           payload includes an expanded charge object; otherwise card fields are
           left empty (the Celery task can fetch them via Charge.retrieve()).
        2. Fall back to legacy ``charges.data[0]`` embed (API < 2022-11-15) so
           older test fixtures and manual replay events still parse correctly.
        """
        # ── Preferred path: latest_charge string ID (API 2022-11-15 / 2024-06-20) ─
        latest_charge_id = obj.get("latest_charge")

        if isinstance(latest_charge_id, str) and latest_charge_id:
            gateway_charge_id = latest_charge_id
            # Card details: only available if the charge is expanded in the event.
            # In webhook events, latest_charge is a bare string ID — not expanded.
            # We accept empty card fields here; the reconciliation task fills them in.
            latest_charge_obj = obj.get("latest_charge_expanded") or {}
            if isinstance(latest_charge_obj, dict) and latest_charge_obj:
                pm_details = (latest_charge_obj.get("payment_method_details") or {})
                card_info = pm_details.get("card") or {}
            else:
                card_info = {}

            card_last_four = card_info.get("last4") if card_info else None
            # Security: card_last_four must be at most 4 chars (never a full PAN).
            # Use explicit raise — assert is stripped under python -O.
            if card_last_four is not None and len(card_last_four) > 4:
                raise GatewayWebhookError(
                    f"card_last_four exceeds 4 chars: len={len(card_last_four)}",
                    gateway_code="pci_violation",
                )
            card_brand = _BRAND_MAP.get(card_info.get("brand", ""), "other") if card_info else None
            paid_at = str(latest_charge_obj.get("created", "")) if latest_charge_obj else ""

        else:
            # ── Fallback: legacy charges.data embed (API < 2022-11-15) ────────────
            charges_embed = obj.get("charges", {})
            if isinstance(charges_embed, dict):
                charges_data = charges_embed.get("data", [])
            else:
                charges_data = []

            charge = charges_data[0] if charges_data else {}
            gateway_charge_id = charge.get("id", "")

            pm_details = charge.get("payment_method_details") or {}
            card_info = pm_details.get("card") or {}

            card_last_four = card_info.get("last4") if card_info else None
            if card_last_four is not None and len(card_last_four) > 4:
                raise GatewayWebhookError(
                    f"card_last_four exceeds 4 chars: len={len(card_last_four)}",
                    gateway_code="pci_violation",
                )
            card_brand = _BRAND_MAP.get(card_info.get("brand", ""), "other") if card_info else None
            paid_at = str(charge.get("created", ""))

        # Processor fee is in balance_transaction — not available synchronously.
        # The Celery task will fetch it via BalanceTransaction.retrieve() if needed.
        # For now store 0.00 — reconciliation task corrects it.
        amount_cents = obj.get("amount_received", obj.get("amount", 0))

        return {
            "gateway_intent_id": obj.get("id", ""),
            "gateway_charge_id": gateway_charge_id,
            "amount_paid": _from_cents(amount_cents),
            "processor_fee": Decimal("0.00"),  # Fetched async via BalanceTransaction
            "net_amount": _from_cents(amount_cents),  # Updated after fee fetch
            "payment_method_type": "card" if card_info else "bank_transfer",
            "card_last_four": card_last_four,
            "card_brand": card_brand,
            "paid_at": paid_at,
        }

    def _parse_payment_intent_failed(self, obj: dict) -> dict:
        """Parse payment_intent.payment_failed event."""
        last_error = obj.get("last_payment_error") or {}
        return {
            "gateway_intent_id": obj.get("id", ""),
            "failure_reason": last_error.get("code", "unknown"),
            # NOTE: last_error.message may contain PII — DO NOT log or store it
        }

    def _parse_charge_refunded(self, obj: dict) -> dict:
        """Parse charge.refunded event."""
        refunds = obj.get("refunds", {}).get("data", [])
        # Stripe returns refunds.data in reverse chronological order (newest first).
        # refunds[0] is the most recent refund — the one that triggered this event.
        # The oldest refund (index -1) would be WRONG for partial-refund scenarios.
        latest_refund = refunds[0] if refunds else {}
        return {
            "gateway_charge_id": obj.get("id", ""),
            "gateway_refund_id": latest_refund.get("id", ""),
            "refund_amount": _from_cents(latest_refund.get("amount", 0)),
            "refund_status": latest_refund.get("status", ""),
        }

    def _parse_subscription_event(self, obj: dict) -> dict:
        """Parse customer.subscription.* events."""
        raw_period_end = obj.get("current_period_end")
        current_period_end = (
            datetime.datetime.fromtimestamp(
                raw_period_end, tz=datetime.timezone.utc
            ).isoformat()
            if raw_period_end
            else None
        )
        return {
            "gateway_subscription_id": obj.get("id", ""),
            "status": obj.get("status", ""),
            "current_period_end": current_period_end,
            "cancel_at_period_end": obj.get("cancel_at_period_end", False),
        }

    def _parse_invoice_event(self, obj: dict) -> dict:
        """
        Parse invoice.payment_succeeded/failed events.

        Extracts subscription and charge IDs plus the amount paid.
        NOTE: obj.get("subscription") may be a string ID or None (for one-off invoices).
        We only process subscription invoices where subscription is a non-empty string.
        """
        subscription_id = obj.get("subscription", "")
        charge_id = obj.get("charge", "")
        amount_paid = _from_cents(obj.get("amount_paid", 0))
        return {
            "gateway_subscription_id": subscription_id if isinstance(subscription_id, str) else "",
            "gateway_charge_id": charge_id if isinstance(charge_id, str) else "",
            "amount_paid": amount_paid,
            "status": obj.get("status", ""),
        }
