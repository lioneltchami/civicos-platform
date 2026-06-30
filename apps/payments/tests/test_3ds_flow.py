"""
3DS (Strong Customer Authentication) end-to-end tests.

Covers three security-relevant scenarios:
  1. create_donation_intent_api returns client_secret when the gateway status is
     requires_action (i.e. Stripe needs the frontend to show the 3DS challenge modal).
  2. payment_intent.succeeded webhook is processed correctly after a 3DS challenge
     completes — a Payment row is created and the PaymentIntent transitions to COMPLETED.
  3. The gateway's create_payment_intent does not raise when Stripe returns a
     requires_action status — it passes the status through in the result dict.

These tests do NOT hit Stripe's API. All gateway calls are mocked.
"""
import json
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.payments.gateways.stripe_gateway import StripeGateway
from apps.payments.models import (
    DonationCampaign,
    GATEWAY_STRIPE,
    Payment,
    PaymentIntent,
    TenantPaymentConfig,
    WebhookEvent,
)
from apps.payments.tasks import process_stripe_webhook
from apps.payments.views.donation import DONATION_SESSION_KEY

User = get_user_model()

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

INTENT_URL = reverse("donate:create_donation_intent")


def _make_user(email=None):
    email = email or f"user_3ds_{uuid.uuid4().hex[:6]}@example.com"
    return User.objects.create_user(email=email, password="TestPass3DS!")


def _make_campaign():
    return DonationCampaign.objects.create(
        slug=f"camp-3ds-{uuid.uuid4().hex[:6]}",
        name_en="3DS Test Campaign",
        start_date=date(2024, 1, 1),
        is_active=True,
        sort_order=0,
        advantage_amount=Decimal("0.00"),
    )


def _make_payment_intent(user, gateway_intent_id=None, status=None):
    return PaymentIntent.objects.create(
        payer=user,
        amount=Decimal("75.00"),
        currency="CAD",
        purpose=PaymentIntent.PURPOSE_DONATION,
        status=status or PaymentIntent.STATUS_PENDING,
        gateway=PaymentIntent.GATEWAY_STRIPE,
        gateway_intent_id=gateway_intent_id or f"pi_3ds_{uuid.uuid4().hex[:8]}",
    )


def _make_webhook_event(gateway_intent_id, event_type="payment_intent.succeeded"):
    gateway_event_id = f"evt_3ds_{uuid.uuid4().hex[:8]}"
    return WebhookEvent.objects.create(
        gateway=GATEWAY_STRIPE,
        gateway_event_id=gateway_event_id,
        event_type=event_type,
        payload={
            "id": gateway_event_id,
            "type": event_type,
            "data": {"object": {"id": gateway_intent_id}},
        },
        signature_verified=True,
        processed=False,
    )


def _succeeded_event_data(gateway_intent_id, gateway_charge_id="ch_3ds_test_001"):
    """Return a parse_webhook_event return value for payment_intent.succeeded."""
    return (
        "payment_intent.succeeded",
        {
            "gateway_intent_id": gateway_intent_id,
            "gateway_charge_id": gateway_charge_id,
            "amount_paid": Decimal("75.00"),
            "processor_fee": Decimal("0.00"),
            "net_amount": Decimal("75.00"),
            "payment_method_type": "card",
            "card_last_four": "3220",
            "card_brand": "visa",
            "paid_at": "1700000000",
        },
    )


# ---------------------------------------------------------------------------
# Test 1 — create_donation_intent_api returns client_secret when
#           gateway status is requires_action
# ---------------------------------------------------------------------------

class DonationIntentRequiresActionTests(TestCase):
    """
    Verify that create_donation_intent_api propagates client_secret to the
    JSON response even when the Stripe gateway returns status=requires_action.

    This is the 3DS path: Stripe has created the PaymentIntent but needs the
    browser to complete the SCA challenge before confirming the payment. The
    frontend needs client_secret to call stripe.confirmCardPayment() and show
    the challenge modal.
    """

    def setUp(self):
        cache.clear()
        self.user = _make_user()
        self.campaign = _make_campaign()
        config = TenantPaymentConfig.get_solo()
        config.stripe_publishable_key = "pk_test_3ds_fixture"
        config.save()

    def tearDown(self):
        cache.clear()

    def _set_session(self, overrides=None):
        data = {
            "campaign_pk": str(self.campaign.pk),
            "campaign_name": "3DS Test Campaign",
            "amount": "75.00",
            "eligible_amount": "75.00",
            "advantage_amount": "0.00",
            "is_recurring": "0",
            "frequency": "",
            "donor_name": "Jane 3DS Donor",
            "donor_email": "jane3ds@example.ca",
            "is_anonymous": "0",
        }
        if overrides:
            data.update(overrides)
        session = self.client.session
        session[DONATION_SESSION_KEY] = data
        session.save()

    def _post_intent(self):
        return self.client.post(INTENT_URL, content_type="application/json")

    def test_requires_action_returns_client_secret(self):
        """
        When the gateway returns status=requires_action, the API must still
        return HTTP 200 with client_secret in the response body.
        """
        self.client.force_login(self.user)
        self._set_session()

        gateway_result = {
            "gateway_intent_id": "pi_3ds_requires_action_001",
            "client_secret": "pi_3ds_requires_action_001_secret_abc",
            "status": "requires_action",
        }
        mock_gw = MagicMock()
        mock_gw.create_payment_intent.return_value = gateway_result

        with patch("apps.payments.views.donation.get_gateway", return_value=mock_gw):
            resp = self._post_intent()

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("client_secret", data)
        self.assertEqual(data["client_secret"], "pi_3ds_requires_action_001_secret_abc")

    def test_requires_action_returns_payment_intent_pk(self):
        """
        The response must also include payment_intent_pk so the frontend can
        poll for completion or pass the PK to the success redirect.
        """
        self.client.force_login(self.user)
        self._set_session()

        gateway_result = {
            "gateway_intent_id": "pi_3ds_requires_action_002",
            "client_secret": "pi_3ds_requires_action_002_secret_xyz",
            "status": "requires_action",
        }
        mock_gw = MagicMock()
        mock_gw.create_payment_intent.return_value = gateway_result

        with patch("apps.payments.views.donation.get_gateway", return_value=mock_gw):
            resp = self._post_intent()

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("payment_intent_pk", data)
        # Verify the PK corresponds to a real PaymentIntent row
        pi_pk = data["payment_intent_pk"]
        intent = PaymentIntent.objects.get(pk=pi_pk)
        self.assertEqual(intent.gateway_intent_id, "pi_3ds_requires_action_002")

    def test_requires_action_creates_pending_payment_intent_row(self):
        """
        A PaymentIntent DB row must be created with STATUS_PENDING so the
        webhook handler can find it when Stripe fires payment_intent.succeeded
        after 3DS completes.
        """
        self.client.force_login(self.user)
        self._set_session()

        gateway_result = {
            "gateway_intent_id": "pi_3ds_ra_003",
            "client_secret": "pi_3ds_ra_003_secret",
            "status": "requires_action",
        }
        mock_gw = MagicMock()
        mock_gw.create_payment_intent.return_value = gateway_result

        with patch("apps.payments.views.donation.get_gateway", return_value=mock_gw):
            self._post_intent()

        intent = PaymentIntent.objects.get(gateway_intent_id="pi_3ds_ra_003")
        self.assertEqual(intent.status, PaymentIntent.STATUS_PENDING)
        self.assertEqual(intent.payer, self.user)

    def test_requires_action_stores_intent_pk_in_session(self):
        """
        The session must be updated with donation_payment_intent_pk after a
        requires_action response so the idempotency guard and success page work.
        """
        self.client.force_login(self.user)
        self._set_session()

        gateway_result = {
            "gateway_intent_id": "pi_3ds_ra_004",
            "client_secret": "pi_3ds_ra_004_secret",
            "status": "requires_action",
        }
        mock_gw = MagicMock()
        mock_gw.create_payment_intent.return_value = gateway_result

        with patch("apps.payments.views.donation.get_gateway", return_value=mock_gw):
            self._post_intent()

        session_data = self.client.session.get(DONATION_SESSION_KEY, {})
        self.assertIn("donation_payment_intent_pk", session_data)


# ---------------------------------------------------------------------------
# Test 2 — payment_intent.succeeded webhook processed after 3DS
# ---------------------------------------------------------------------------

class WebhookAfter3DSTests(TestCase):
    """
    Verify that the process_stripe_webhook task correctly handles
    payment_intent.succeeded events that arrive after a 3DS challenge.

    In the 3DS flow the PaymentIntent starts as STATUS_PENDING (requires_action
    from Stripe's perspective). The webhook fires once the challenge completes
    and Stripe confirms the charge.
    """

    def _run_webhook(self, pi, charge_id="ch_after_3ds_001"):
        """Helper: create a webhook event and process it synchronously."""
        event = _make_webhook_event(pi.gateway_intent_id)
        mock_parse_return = _succeeded_event_data(
            gateway_intent_id=pi.gateway_intent_id,
            gateway_charge_id=charge_id,
        )
        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))
        return event

    def test_webhook_creates_payment_row_after_3ds(self):
        """
        A Payment row must be created when payment_intent.succeeded fires
        after the donor completes the 3DS challenge.
        """
        user = _make_user()
        pi = _make_payment_intent(user, gateway_intent_id="pi_3ds_w_001")
        self._run_webhook(pi)
        self.assertEqual(Payment.objects.count(), 1)
        payment = Payment.objects.get()
        self.assertEqual(payment.intent, pi)

    def test_webhook_transitions_intent_to_completed_after_3ds(self):
        """
        The PaymentIntent status must transition from PENDING to COMPLETED
        when payment_intent.succeeded is processed.
        """
        user = _make_user()
        pi = _make_payment_intent(user, gateway_intent_id="pi_3ds_w_002",
                                  status=PaymentIntent.STATUS_PENDING)
        self.assertEqual(pi.status, PaymentIntent.STATUS_PENDING)

        self._run_webhook(pi)

        pi.refresh_from_db()
        self.assertEqual(pi.status, PaymentIntent.STATUS_COMPLETED)

    def test_webhook_records_correct_amount_paid_after_3ds(self):
        """
        The Payment row amount_paid must match the amount returned by the
        webhook event data (75.00 CAD for our fixture).
        """
        user = _make_user()
        pi = _make_payment_intent(user, gateway_intent_id="pi_3ds_w_003")
        self._run_webhook(pi)
        payment = Payment.objects.get()
        self.assertEqual(payment.amount_paid, Decimal("75.00"))

    def test_webhook_marks_event_processed_after_3ds(self):
        """
        The WebhookEvent.processed flag must be set True after the task
        successfully processes the event.
        """
        user = _make_user()
        pi = _make_payment_intent(user, gateway_intent_id="pi_3ds_w_004")
        event = self._run_webhook(pi)
        event.refresh_from_db()
        self.assertTrue(event.processed)

    def test_webhook_is_idempotent_after_3ds(self):
        """
        Processing the same payment_intent.succeeded event twice must not
        create a duplicate Payment row (idempotency guard).
        """
        user = _make_user()
        pi = _make_payment_intent(user, gateway_intent_id="pi_3ds_w_005")
        event = _make_webhook_event(pi.gateway_intent_id)
        mock_parse_return = _succeeded_event_data(gateway_intent_id=pi.gateway_intent_id)

        with patch("apps.payments.gateway.get_gateway") as mock_get_gw, \
             patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            process_stripe_webhook(str(event.pk))  # first call
            process_stripe_webhook(str(event.pk))  # second call — must be no-op

        self.assertEqual(Payment.objects.count(), 1)

    def test_webhook_stores_gateway_charge_id_after_3ds(self):
        """
        The Payment row must store the gateway_charge_id from the webhook so
        refunds can reference the correct Stripe charge object.
        """
        user = _make_user()
        pi = _make_payment_intent(user, gateway_intent_id="pi_3ds_w_006")
        self._run_webhook(pi, charge_id="ch_after_3ds_specific_006")
        payment = Payment.objects.get()
        self.assertEqual(payment.gateway_charge_id, "ch_after_3ds_specific_006")


# ---------------------------------------------------------------------------
# Test 3 — gateway handles requires_action status without raising
# ---------------------------------------------------------------------------

class GatewayRequiresActionTests(TestCase):
    """
    Verify that StripeGateway.create_payment_intent does not raise an exception
    when Stripe returns a PaymentIntent with status=requires_action.

    The gateway must treat requires_action as a normal (non-error) outcome and
    return a dict that includes the client_secret. Raising here would prevent
    donors from completing 3DS challenges.
    """

    def _make_stripe_pi_mock(self, status="requires_action"):
        """Return a MagicMock that looks like a Stripe PaymentIntent object."""
        pi_mock = MagicMock()
        pi_mock.id = f"pi_mock_{uuid.uuid4().hex[:8]}"
        pi_mock.client_secret = f"{pi_mock.id}_secret_{uuid.uuid4().hex[:8]}"
        pi_mock.status = status
        return pi_mock

    def _gateway(self):
        """Return a StripeGateway with _api_key patched to avoid settings read."""
        gw = StripeGateway.__new__(StripeGateway)
        return gw

    def test_create_payment_intent_does_not_raise_on_requires_action(self):
        """
        StripeGateway.create_payment_intent must not raise when Stripe returns
        status=requires_action. The result dict must include client_secret.
        """
        pi_mock = self._make_stripe_pi_mock(status="requires_action")
        gw = self._gateway()

        mock_stripe_module = MagicMock()
        mock_stripe_module.PaymentIntent.create.return_value = pi_mock

        with patch.object(gw, "_stripe", return_value=mock_stripe_module), \
             patch.object(gw, "_api_key", return_value="sk_test_dummy"):
            result = gw.create_payment_intent(
                amount=Decimal("50.00"),
                currency="cad",
                idempotency_key=str(uuid.uuid4()),
                metadata={"source": "donation"},
                description="3DS test donation",
            )

        self.assertIn("client_secret", result)
        self.assertEqual(result["client_secret"], pi_mock.client_secret)

    def test_create_payment_intent_includes_status_on_requires_action(self):
        """
        The result dict must include the status field so callers can distinguish
        requires_action from succeeded without additional Stripe API calls.
        """
        pi_mock = self._make_stripe_pi_mock(status="requires_action")
        gw = self._gateway()

        mock_stripe_module = MagicMock()
        mock_stripe_module.PaymentIntent.create.return_value = pi_mock

        with patch.object(gw, "_stripe", return_value=mock_stripe_module), \
             patch.object(gw, "_api_key", return_value="sk_test_dummy"):
            result = gw.create_payment_intent(
                amount=Decimal("50.00"),
                currency="cad",
                idempotency_key=str(uuid.uuid4()),
                metadata={"source": "donation"},
            )

        self.assertEqual(result.get("status"), "requires_action")

    def test_create_payment_intent_includes_gateway_intent_id_on_requires_action(self):
        """
        The result dict must include gateway_intent_id even in requires_action
        state so the DB row and session can reference the Stripe object.
        """
        pi_mock = self._make_stripe_pi_mock(status="requires_action")
        gw = self._gateway()

        mock_stripe_module = MagicMock()
        mock_stripe_module.PaymentIntent.create.return_value = pi_mock

        with patch.object(gw, "_stripe", return_value=mock_stripe_module), \
             patch.object(gw, "_api_key", return_value="sk_test_dummy"):
            result = gw.create_payment_intent(
                amount=Decimal("50.00"),
                currency="cad",
                idempotency_key=str(uuid.uuid4()),
                metadata={"source": "donation"},
            )

        self.assertIn("gateway_intent_id", result)
        self.assertEqual(result["gateway_intent_id"], pi_mock.id)

    def test_create_payment_intent_does_not_raise_on_succeeded(self):
        """
        Sanity check: the gateway also must not raise on status=succeeded
        (the normal, non-3DS path).
        """
        pi_mock = self._make_stripe_pi_mock(status="succeeded")
        gw = self._gateway()

        mock_stripe_module = MagicMock()
        mock_stripe_module.PaymentIntent.create.return_value = pi_mock

        with patch.object(gw, "_stripe", return_value=mock_stripe_module), \
             patch.object(gw, "_api_key", return_value="sk_test_dummy"):
            result = gw.create_payment_intent(
                amount=Decimal("50.00"),
                currency="cad",
                idempotency_key=str(uuid.uuid4()),
                metadata={"source": "donation"},
            )

        self.assertEqual(result.get("status"), "succeeded")
        self.assertIn("client_secret", result)
