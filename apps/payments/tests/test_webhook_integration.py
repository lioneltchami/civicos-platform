"""
test_webhook_integration.py — L5 fix

End-to-end TransactionTestCase for the stripe_webhook view → Celery task path.

Why TransactionTestCase?
  Django's TestCase wraps every test in a transaction and uses savepoints for
  setUp/tearDown.  This means transaction.on_commit() callbacks registered inside
  the test never fire — they are queued on the outer (rolled-back) transaction.
  TransactionTestCase truncates tables instead, so each COMMIT is real and
  on_commit() fires for real.

What is tested here:
  1. View receives a POST → creates WebhookEvent + dispatches process_stripe_webhook
  2. Task runs (via CELERY_TASK_ALWAYS_EAGER) → creates Payment + marks event processed
  3. on_commit() callbacks inside the task fire (captureOnCommitCallbacks(execute=True))
  4. Duplicate delivery → idempotent (view returns 200 "duplicate", no second Payment)

What is mocked:
  - apps.payments.views.webhook.get_gateway  → verify_webhook_signature = True
  - apps.payments.gateway.get_gateway       → parse_webhook_event returns fixture data
  All DB writes are real; no Stripe HTTP calls are made.
"""

import json
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TransactionTestCase
from django.urls import reverse

from apps.payments.models import (
    GATEWAY_STRIPE,
    Payment,
    PaymentIntent,
    TenantPaymentConfig,
    WebhookEvent,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_user(email=None):
    from django.contrib.auth import get_user_model

    User = get_user_model()  # noqa: N806
    email = email or f"user_{uuid.uuid4().hex[:6]}@example.com"
    return User.objects.create_user(email=email, password="TestPass123!")


def _make_payment_intent(user=None, gateway_intent_id=None, status=None):
    if user is None:
        user = _make_user()
    return PaymentIntent.objects.create(
        payer=user,
        amount=Decimal("100.00"),
        currency="CAD",
        purpose=PaymentIntent.PURPOSE_SERVICE_FEE,
        status=status or PaymentIntent.STATUS_PENDING,
        gateway=GATEWAY_STRIPE,
        gateway_intent_id=gateway_intent_id or f"pi_integ_{uuid.uuid4().hex[:8]}",
    )


def _make_succeeded_parse_return(gateway_intent_id, gateway_charge_id):
    """Return a tuple matching gateway.parse_webhook_event() for payment_intent.succeeded."""
    return (
        "payment_intent.succeeded",
        {
            "gateway_intent_id": gateway_intent_id,
            "gateway_charge_id": gateway_charge_id,
            "amount_paid": Decimal("100.00"),
            "processor_fee": Decimal("3.20"),
            "net_amount": Decimal("96.80"),
            "payment_method_type": "card",
            "card_last_four": "4242",
            "card_brand": "visa",
            "paid_at": "1700000000",
        },
    )


def _build_webhook_payload(event_id, event_type, gateway_intent_id):
    """Build a minimal Stripe-like webhook payload."""
    return {
        "id": event_id,
        "type": event_type,
        "data": {
            "object": {
                "id": gateway_intent_id,
                "status": "succeeded",
                "amount": 10000,
                "currency": "cad",
            }
        },
    }


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class WebhookTaskIntegrationTest(TransactionTestCase):
    """
    End-to-end: stripe_webhook view → process_stripe_webhook task.

    Uses TransactionTestCase so transaction.on_commit() callbacks inside the
    task fire for real (no savepoint wrapping — every COMMIT is a real commit).

    Task dispatch strategy: we replace process_stripe_webhook.delay with a
    side-effect that calls the task synchronously (process_stripe_webhook(pk)).
    This exercises the full view → task code path without requiring a live
    broker, and without relying on CELERY_TASK_ALWAYS_EAGER (which requires
    Celery to re-read Django settings at runtime — unreliable in tests).
    """

    def setUp(self):
        # Seed TenantPaymentConfig so the empty-secret guard in the view doesn't
        # reject our requests before reaching gateway.verify_webhook_signature.
        config = TenantPaymentConfig.get_solo()
        config.webhook_endpoint_secret = "whsec_integration_test_secret"
        config.save()

        self.webhook_url = reverse("payments:stripe_webhook")
        self.user = _make_user()

    def _post_webhook(self, payload, mock_parse_return=None):
        """
        POST a webhook payload to the view with:
          - view's get_gateway mocked (verify_webhook_signature → True)
          - process_stripe_webhook.delay replaced by a synchronous call
          - task's get_gateway mocked (parse_webhook_event → mock_parse_return)

        Returns the HTTP response.
        """
        from apps.payments.tasks import process_stripe_webhook

        body = json.dumps(payload).encode()

        def _run_task_synchronously(webhook_event_pk):
            """Side-effect: run the task synchronously instead of via broker."""
            process_stripe_webhook(webhook_event_pk)

        with (
            patch("apps.payments.views.webhook.get_gateway") as mock_view_gw,
            patch("apps.payments.gateway.get_gateway") as mock_task_gw,
            patch(
                "apps.payments.tasks.process_stripe_webhook.delay",
                side_effect=_run_task_synchronously,
            ),
        ):
            # View gateway: only verify_webhook_signature is called
            mock_view_instance = MagicMock()
            mock_view_instance.verify_webhook_signature.return_value = True
            mock_view_gw.return_value = mock_view_instance

            # Task gateway: parse_webhook_event returns fixture data
            mock_task_instance = MagicMock()
            if mock_parse_return is not None:
                mock_task_instance.parse_webhook_event.return_value = mock_parse_return
            mock_task_gw.return_value = mock_task_instance

            # In TransactionTestCase each COMMIT is real, so transaction.on_commit()
            # callbacks registered by the task fire automatically — no
            # captureOnCommitCallbacks() wrapper is needed (that helper only
            # exists on TestCase to simulate commit in a wrapped transaction).
            response = self.client.post(
                self.webhook_url,
                data=body,
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1234,v1=integration_test",
            )

        return response

    # ── Full view → task path ─────────────────────────────────────────────────

    def test_payment_intent_succeeded_returns_200(self):
        """View must return 200 when signature is valid and event is new."""
        event_id = f"evt_integ_{uuid.uuid4().hex[:8]}"
        pi = _make_payment_intent(user=self.user)
        payload = _build_webhook_payload(event_id, "payment_intent.succeeded", pi.gateway_intent_id)
        parse_return = _make_succeeded_parse_return(
            pi.gateway_intent_id, f"ch_{uuid.uuid4().hex[:8]}"
        )

        response = self._post_webhook(payload, mock_parse_return=parse_return)
        self.assertEqual(response.status_code, 200)

    def test_payment_intent_succeeded_creates_webhook_event(self):
        """View must persist a WebhookEvent row for the inbound event."""
        event_id = f"evt_integ_{uuid.uuid4().hex[:8]}"
        pi = _make_payment_intent(user=self.user)
        payload = _build_webhook_payload(event_id, "payment_intent.succeeded", pi.gateway_intent_id)
        charge_id = f"ch_{uuid.uuid4().hex[:8]}"
        parse_return = _make_succeeded_parse_return(pi.gateway_intent_id, charge_id)

        self._post_webhook(payload, mock_parse_return=parse_return)

        self.assertTrue(
            WebhookEvent.objects.filter(gateway_event_id=event_id).exists(),
            "WebhookEvent must be created by the view",
        )

    def test_payment_intent_succeeded_marks_event_processed(self):
        """Task must mark WebhookEvent.processed = True after handling."""
        event_id = f"evt_integ_{uuid.uuid4().hex[:8]}"
        pi = _make_payment_intent(user=self.user)
        payload = _build_webhook_payload(event_id, "payment_intent.succeeded", pi.gateway_intent_id)
        charge_id = f"ch_{uuid.uuid4().hex[:8]}"
        parse_return = _make_succeeded_parse_return(pi.gateway_intent_id, charge_id)

        self._post_webhook(payload, mock_parse_return=parse_return)

        event = WebhookEvent.objects.get(gateway_event_id=event_id)
        self.assertTrue(event.processed, "Task must set WebhookEvent.processed = True")
        self.assertIsNotNone(event.processed_at)

    def test_payment_intent_succeeded_creates_payment(self):
        """Task must create a Payment row linked to the PaymentIntent."""
        event_id = f"evt_integ_{uuid.uuid4().hex[:8]}"
        charge_id = f"ch_integ_{uuid.uuid4().hex[:8]}"
        pi = _make_payment_intent(user=self.user)
        payload = _build_webhook_payload(event_id, "payment_intent.succeeded", pi.gateway_intent_id)
        parse_return = _make_succeeded_parse_return(pi.gateway_intent_id, charge_id)

        self._post_webhook(payload, mock_parse_return=parse_return)

        self.assertTrue(
            Payment.objects.filter(gateway_charge_id=charge_id).exists(),
            "Task must create a Payment row for the charge",
        )
        payment = Payment.objects.get(gateway_charge_id=charge_id)
        self.assertEqual(payment.intent, pi)
        self.assertEqual(payment.amount_paid, Decimal("100.00"))

    def test_payment_intent_succeeded_advances_intent_to_completed(self):
        """Task must transition PaymentIntent to STATUS_COMPLETED."""
        event_id = f"evt_integ_{uuid.uuid4().hex[:8]}"
        pi = _make_payment_intent(user=self.user, status=PaymentIntent.STATUS_PENDING)
        charge_id = f"ch_{uuid.uuid4().hex[:8]}"
        payload = _build_webhook_payload(event_id, "payment_intent.succeeded", pi.gateway_intent_id)
        parse_return = _make_succeeded_parse_return(pi.gateway_intent_id, charge_id)

        self._post_webhook(payload, mock_parse_return=parse_return)

        pi.refresh_from_db()
        self.assertEqual(pi.status, PaymentIntent.STATUS_COMPLETED)

    # ── Idempotency (duplicate delivery) ─────────────────────────────────────

    def test_duplicate_delivery_second_response_is_200(self):
        """
        Stripe sends the same event twice (at-least-once delivery).
        Both POSTs must return 200 — the second as 'duplicate'.
        """
        event_id = f"evt_dup_{uuid.uuid4().hex[:8]}"
        charge_id = f"ch_{uuid.uuid4().hex[:8]}"
        pi = _make_payment_intent(user=self.user)
        payload = _build_webhook_payload(event_id, "payment_intent.succeeded", pi.gateway_intent_id)
        parse_return = _make_succeeded_parse_return(pi.gateway_intent_id, charge_id)

        r1 = self._post_webhook(payload, mock_parse_return=parse_return)
        r2 = self._post_webhook(payload, mock_parse_return=parse_return)

        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200, "Duplicate delivery must return 200, not 500")

    def test_duplicate_delivery_creates_only_one_payment(self):
        """
        Task must be idempotent: two deliveries of the same event must produce
        exactly one Payment row (not two).
        """
        event_id = f"evt_dup_{uuid.uuid4().hex[:8]}"
        charge_id = f"ch_{uuid.uuid4().hex[:8]}"
        pi = _make_payment_intent(user=self.user)
        payload = _build_webhook_payload(event_id, "payment_intent.succeeded", pi.gateway_intent_id)
        parse_return = _make_succeeded_parse_return(pi.gateway_intent_id, charge_id)

        self._post_webhook(payload, mock_parse_return=parse_return)
        self._post_webhook(payload, mock_parse_return=parse_return)

        count = Payment.objects.filter(gateway_charge_id=charge_id).count()
        self.assertEqual(count, 1, "Duplicate delivery must not create a second Payment row")

    def test_duplicate_delivery_second_response_is_duplicate_status(self):
        """Second delivery must return JSON body with status='duplicate'."""
        event_id = f"evt_dup_{uuid.uuid4().hex[:8]}"
        charge_id = f"ch_{uuid.uuid4().hex[:8]}"
        pi = _make_payment_intent(user=self.user)
        payload = _build_webhook_payload(event_id, "payment_intent.succeeded", pi.gateway_intent_id)
        parse_return = _make_succeeded_parse_return(pi.gateway_intent_id, charge_id)

        self._post_webhook(payload, mock_parse_return=parse_return)
        r2 = self._post_webhook(payload, mock_parse_return=parse_return)

        data = json.loads(r2.content)
        self.assertEqual(data.get("status"), "duplicate")

    def test_first_delivery_response_is_queued_status(self):
        """First delivery must return JSON body with status='queued'."""
        event_id = f"evt_first_{uuid.uuid4().hex[:8]}"
        charge_id = f"ch_{uuid.uuid4().hex[:8]}"
        pi = _make_payment_intent(user=self.user)
        payload = _build_webhook_payload(event_id, "payment_intent.succeeded", pi.gateway_intent_id)
        parse_return = _make_succeeded_parse_return(pi.gateway_intent_id, charge_id)

        r1 = self._post_webhook(payload, mock_parse_return=parse_return)

        data = json.loads(r1.content)
        self.assertEqual(data.get("status"), "queued")

    # ── Signature verification gate ───────────────────────────────────────────

    def test_invalid_signature_returns_400_and_no_event_created(self):
        """
        If signature verification fails, view must return 400 and must not
        persist a WebhookEvent or dispatch the task.
        """
        event_id = f"evt_badsig_{uuid.uuid4().hex[:8]}"
        pi = _make_payment_intent(user=self.user)
        payload = _build_webhook_payload(event_id, "payment_intent.succeeded", pi.gateway_intent_id)
        body = json.dumps(payload).encode()

        with (
            patch("apps.payments.views.webhook.get_gateway") as mock_view_gw,
            patch("apps.payments.tasks.process_stripe_webhook.delay") as mock_delay,
        ):
            mock_view_instance = MagicMock()
            mock_view_instance.verify_webhook_signature.return_value = False
            mock_view_gw.return_value = mock_view_instance

            response = self.client.post(
                self.webhook_url,
                data=body,
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1234,v1=bad_signature",
            )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            WebhookEvent.objects.filter(gateway_event_id=event_id).exists(),
            "No WebhookEvent must be created when signature is invalid",
        )
        mock_delay.assert_not_called()
