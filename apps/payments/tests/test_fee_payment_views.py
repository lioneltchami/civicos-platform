"""
Wave 3 — test_fee_payment_views.py

Tests for all 5 fee payment views:
  FeePaymentSelectView, FeePaymentConfirmView, create_payment_intent_api,
  FeePaymentSuccessView, FeePaymentCancelView.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.payments.gateways.exceptions import GatewayError
from apps.payments.models import (
    FeeSchedule,
    PaymentIntent,
    TaxRate,
    TenantPaymentConfig,
)
from apps.payments.views.fee_payment import SESSION_KEY

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def make_fee_schedule(**kwargs):
    defaults = {
        "fee_code": "PERMIT-VIEW",
        "service_type": "permit",
        "province": "ON",
        "amount": Decimal("50.00"),
        "is_taxable": True,
        "description_en": "Test Permit Fee",
        "description_fr": "Frais de test",
        "effective_date": date(2020, 1, 1),
        "is_active": True,
    }
    defaults.update(kwargs)
    return FeeSchedule.objects.create(**defaults)


def make_tax_rate(**kwargs):
    defaults = {
        "province": "ON",
        "federal_rate": Decimal("0.05000"),
        "provincial_rate": Decimal("0.08000"),
        "combined_rate": Decimal("0.13000"),
        "tax_name_en": "HST",
        "tax_name_fr": "TVH",
        "effective_date": date(2020, 1, 1),
    }
    defaults.update(kwargs)
    return TaxRate.objects.create(**defaults)


def make_user(email=None, password="testpass123", **kwargs):
    email = email or f"user_{uuid.uuid4().hex[:6]}@example.com"
    return User.objects.create_user(email=email, password=password, **kwargs)


def make_payment_intent(payer, status=None, **kwargs):
    defaults = {
        "payer": payer,
        "amount": Decimal("56.50"),
        "currency": "CAD",
        "purpose": PaymentIntent.PURPOSE_SERVICE_FEE,
        "status": status or PaymentIntent.STATUS_PENDING,
        "gateway": PaymentIntent.GATEWAY_STRIPE,
        "gateway_intent_id": f"pi_test_{uuid.uuid4().hex[:8]}",
    }
    defaults.update(kwargs)
    return PaymentIntent.objects.create(**defaults)


def _session_data(**overrides):
    data = {
        "fee_pk": str(uuid.uuid4()),
        "fee_code": "PERMIT-VIEW",
        "fee_description": "Test Permit Fee",
        "province": "ON",
        "quantity": 1,
        "payer_reference": "",
        "subtotal": "50.00",
        "tax_amount": "6.50",
        "total": "56.50",
        "is_taxable": True,
    }
    data.update(overrides)
    return data


GATEWAY_RESULT = {
    "gateway_intent_id": "pi_fake_001",
    "client_secret": "pi_fake_001_secret_xyz",
}


# ---------------------------------------------------------------------------
# Base test class
# ---------------------------------------------------------------------------


class FeePaymentViewTestBase(TestCase):
    SELECT_URL = None
    CONFIRM_URL = None
    SUCCESS_URL = None
    CANCEL_URL = None
    INTENT_URL = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.SELECT_URL = reverse("payments:fee_payment_select")
        cls.CONFIRM_URL = reverse("payments:fee_payment_confirm")
        cls.SUCCESS_URL = reverse("payments:fee_payment_success")
        cls.CANCEL_URL = reverse("payments:fee_payment_cancel")
        cls.INTENT_URL = reverse("payments:create_payment_intent")

    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.client.force_login(self.user)
        self.fee = make_fee_schedule()
        self.tax = make_tax_rate()

    def tearDown(self):
        cache.clear()

    def _set_session(self, data):
        session = self.client.session
        session[SESSION_KEY] = data
        session.save()

    def _mock_gateway(self, result=None, raises=None):
        mock_gw = MagicMock()
        if raises:
            mock_gw.create_payment_intent.side_effect = raises
        else:
            mock_gw.create_payment_intent.return_value = result or GATEWAY_RESULT
        mock_gw.cancel_payment_intent.return_value = {}
        return patch(
            "apps.payments.views.fee_payment.get_gateway",
            return_value=mock_gw,
        )


# ---------------------------------------------------------------------------
# FeePaymentSelectView
# ---------------------------------------------------------------------------


class FeePaymentSelectViewTests(FeePaymentViewTestBase):
    def test_get_returns_200(self):
        resp = self.client.get(self.SELECT_URL)
        self.assertEqual(resp.status_code, 200)

    def test_get_has_form_in_context(self):
        resp = self.client.get(self.SELECT_URL)
        self.assertIn("form", resp.context)

    def test_unauthenticated_get_redirects_to_login(self):
        self.client.logout()
        resp = self.client.get(self.SELECT_URL)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])

    def test_valid_post_stores_session(self):
        resp = self.client.post(
            self.SELECT_URL,
            data={
                "province": "ON",
                "fee_code": "PERMIT-VIEW",
                "quantity": 1,
                "payer_reference": "",
            },
        )
        # Should redirect to confirm
        self.assertEqual(resp.status_code, 302)
        session = self.client.session
        self.assertIn(SESSION_KEY, session)

    def test_valid_post_session_has_correct_keys(self):
        self.client.post(
            self.SELECT_URL,
            data={
                "province": "ON",
                "fee_code": "PERMIT-VIEW",
                "quantity": 1,
                "payer_reference": "",
            },
        )
        session = self.client.session[SESSION_KEY]
        for key in ("fee_pk", "fee_code", "province", "subtotal", "tax_amount", "total"):
            self.assertIn(key, session, f"Session missing key: {key}")

    def test_valid_post_redirects_to_confirm(self):
        resp = self.client.post(
            self.SELECT_URL,
            data={
                "province": "ON",
                "fee_code": "PERMIT-VIEW",
                "quantity": 1,
                "payer_reference": "",
            },
        )
        self.assertRedirects(resp, self.CONFIRM_URL, fetch_redirect_response=False)

    def test_invalid_post_returns_200_with_errors(self):
        resp = self.client.post(
            self.SELECT_URL,
            data={
                "province": "ON",
                "fee_code": "NO-SUCH-FEE",
                "quantity": 1,
                "payer_reference": "",
            },
        )
        self.assertEqual(resp.status_code, 200)
        form = resp.context["form"]
        self.assertTrue(form.errors or form.non_field_errors())

    def test_session_amounts_are_strings(self):
        """Session values must be strings (JSON-serialisable), not Decimals."""
        self.client.post(
            self.SELECT_URL,
            data={
                "province": "ON",
                "fee_code": "PERMIT-VIEW",
                "quantity": 1,
                "payer_reference": "",
            },
        )
        session = self.client.session[SESSION_KEY]
        for key in ("subtotal", "tax_amount", "total"):
            self.assertIsInstance(session[key], str, f"{key} must be a string")

    def test_session_amounts_quantized_to_2dp(self):
        """Session monetary strings must have exactly 2 decimal places."""
        self.client.post(
            self.SELECT_URL,
            data={
                "province": "ON",
                "fee_code": "PERMIT-VIEW",
                "quantity": 1,
                "payer_reference": "",
            },
        )
        session = self.client.session[SESSION_KEY]
        for key in ("subtotal", "tax_amount", "total"):
            val = session[key]
            decimal_part = val.split(".")[-1] if "." in val else ""
            self.assertEqual(len(decimal_part), 2, f"{key}={val!r} not 2dp")


# ---------------------------------------------------------------------------
# FeePaymentConfirmView
# ---------------------------------------------------------------------------


class FeePaymentConfirmViewTests(FeePaymentViewTestBase):
    def test_get_with_valid_session_returns_200(self):
        self._set_session(_session_data())
        resp = self.client.get(self.CONFIRM_URL)
        self.assertEqual(resp.status_code, 200)

    def test_get_without_session_redirects_to_select(self):
        resp = self.client.get(self.CONFIRM_URL)
        self.assertRedirects(resp, self.SELECT_URL, fetch_redirect_response=False)

    def test_get_context_has_total(self):
        self._set_session(_session_data())
        resp = self.client.get(self.CONFIRM_URL)
        self.assertIn("total", resp.context)
        self.assertEqual(resp.context["total"], Decimal("56.50"))

    def test_get_context_has_stripe_publishable_key(self):
        config = TenantPaymentConfig.get_solo()
        config.stripe_publishable_key = "pk_test_abc123"
        config.save()
        self._set_session(_session_data())
        resp = self.client.get(self.CONFIRM_URL)
        self.assertIn("stripe_publishable_key", resp.context)
        self.assertEqual(resp.context["stripe_publishable_key"], "pk_test_abc123")

    def test_stripe_publishable_key_comes_from_config(self):
        config = TenantPaymentConfig.get_solo()
        config.stripe_publishable_key = "pk_test_from_config"
        config.save()
        self._set_session(_session_data())
        resp = self.client.get(self.CONFIRM_URL)
        self.assertEqual(resp.context["stripe_publishable_key"], "pk_test_from_config")


# ---------------------------------------------------------------------------
# create_payment_intent_api
# ---------------------------------------------------------------------------


class CreatePaymentIntentApiTests(FeePaymentViewTestBase):
    def _post_intent(self, **extra):
        return self.client.post(
            self.INTENT_URL,
            content_type="application/json",
            **extra,
        )

    def test_get_returns_405(self):
        resp = self.client.get(self.INTENT_URL)
        self.assertEqual(resp.status_code, 405)

    def test_unauthenticated_post_returns_401(self):
        """
        create_payment_intent_api is a JSON endpoint that explicitly returns
        401 for unauthenticated requests rather than redirecting to login.
        """
        self.client.logout()
        resp = self._post_intent()
        self.assertEqual(resp.status_code, 401)
        data = resp.json()
        self.assertIn("error", data)

    def test_valid_post_creates_payment_intent_row(self):
        self._set_session(_session_data())
        with self._mock_gateway():
            resp = self._post_intent()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(PaymentIntent.objects.count(), 1)

    def test_valid_post_returns_client_secret_and_pk(self):
        self._set_session(_session_data())
        with self._mock_gateway():
            resp = self._post_intent()
        data = resp.json()
        self.assertIn("client_secret", data)
        self.assertIn("payment_intent_pk", data)

    def test_valid_post_payment_intent_payer_is_user(self):
        self._set_session(_session_data())
        with self._mock_gateway():
            self._post_intent()
        intent = PaymentIntent.objects.get()
        self.assertEqual(intent.payer, self.user)

    def test_valid_post_amount_from_session_not_client(self):
        """Amount in PaymentIntent must match session total, not any client value."""
        self._set_session(_session_data(total="56.50"))
        with self._mock_gateway():
            self._post_intent()
        intent = PaymentIntent.objects.get()
        self.assertEqual(intent.amount, Decimal("56.50"))

    def test_valid_post_stores_intent_pk_in_session(self):
        self._set_session(_session_data())
        with self._mock_gateway():
            self._post_intent()
        session = self.client.session[SESSION_KEY]
        self.assertIn("payment_intent_pk", session)

    def test_no_session_returns_403(self):
        resp = self._post_intent()
        self.assertEqual(resp.status_code, 403)
        data = resp.json()
        self.assertIn("error", data)

    def test_zero_total_returns_400(self):
        self._set_session(_session_data(total="0.00"))
        resp = self._post_intent()
        self.assertEqual(resp.status_code, 400)

    def test_double_click_returns_409(self):
        """Second POST with payment_intent_pk already in session returns 409."""
        # Set up session with an existing pending intent
        intent = make_payment_intent(self.user, status=PaymentIntent.STATUS_PENDING)
        sd = _session_data()
        sd["payment_intent_pk"] = str(intent.pk)
        self._set_session(sd)
        with self._mock_gateway():
            resp = self._post_intent()
        self.assertEqual(resp.status_code, 409)

    def test_rate_limit_sixth_request_returns_429(self):
        """5 requests succeed; 6th returns 429."""
        for i in range(5):
            self._set_session(_session_data())
            gw_result = {
                "gateway_intent_id": f"pi_rl_{uuid.uuid4().hex[:6]}",
                "client_secret": f"secret_{i}",
            }
            with self._mock_gateway(result=gw_result):
                resp = self._post_intent()
            # Clear intent_pk from session so idempotency guard doesn't fire
            sd = self.client.session[SESSION_KEY]
            sd.pop("payment_intent_pk", None)
            self._set_session(sd)

        # 6th request
        self._set_session(_session_data())
        with self._mock_gateway():
            resp = self._post_intent()
        self.assertEqual(resp.status_code, 429)

    def test_gateway_error_returns_502(self):
        self._set_session(_session_data())
        with self._mock_gateway(raises=GatewayError("gateway down", gateway_code="unavailable")):
            resp = self._post_intent()
        self.assertEqual(resp.status_code, 502)
        data = resp.json()
        self.assertIn("error", data)

    def test_gateway_error_no_payment_intent_created(self):
        self._set_session(_session_data())
        with self._mock_gateway(raises=GatewayError("gateway down", gateway_code="unavailable")):
            self._post_intent()
        self.assertEqual(PaymentIntent.objects.count(), 0)

    def test_db_error_after_gateway_returns_500_and_cancels_stripe(self):
        """DB failure after Stripe intent created: cancel Stripe intent, return 500."""
        self._set_session(_session_data())
        mock_gw = MagicMock()
        mock_gw.create_payment_intent.return_value = GATEWAY_RESULT
        mock_gw.cancel_payment_intent.return_value = {}

        with patch("apps.payments.views.fee_payment.get_gateway", return_value=mock_gw):
            with patch(
                "apps.payments.models.PaymentIntent.objects.create",
                side_effect=Exception("DB is down"),
            ):
                resp = self._post_intent()

        self.assertEqual(resp.status_code, 500)
        mock_gw.cancel_payment_intent.assert_called_once()


# ---------------------------------------------------------------------------
# FeePaymentSuccessView
# ---------------------------------------------------------------------------


class FeePaymentSuccessViewTests(FeePaymentViewTestBase):
    def test_get_with_query_param_returns_200(self):
        intent = make_payment_intent(self.user, status=PaymentIntent.STATUS_COMPLETED)
        resp = self.client.get(self.SUCCESS_URL, {"payment_intent_pk": str(intent.pk)})
        self.assertEqual(resp.status_code, 200)

    def test_get_with_session_pk_returns_200(self):
        intent = make_payment_intent(self.user, status=PaymentIntent.STATUS_COMPLETED)
        self._set_session(_session_data(payment_intent_pk=str(intent.pk)))
        resp = self.client.get(self.SUCCESS_URL)
        self.assertEqual(resp.status_code, 200)

    def test_get_without_pk_redirects_to_select(self):
        resp = self.client.get(self.SUCCESS_URL)
        self.assertRedirects(resp, self.SELECT_URL, fetch_redirect_response=False)

    def test_get_with_garbage_pk_redirects_to_select(self):
        resp = self.client.get(self.SUCCESS_URL, {"payment_intent_pk": "not-a-uuid"})
        self.assertRedirects(resp, self.SELECT_URL, fetch_redirect_response=False)

    def test_get_with_other_users_intent_returns_404(self):
        other_user = make_user()
        intent = make_payment_intent(other_user, status=PaymentIntent.STATUS_COMPLETED)
        resp = self.client.get(self.SUCCESS_URL, {"payment_intent_pk": str(intent.pk)})
        self.assertEqual(resp.status_code, 404)

    def test_get_clears_session(self):
        intent = make_payment_intent(self.user, status=PaymentIntent.STATUS_COMPLETED)
        self._set_session(_session_data(payment_intent_pk=str(intent.pk)))
        self.client.get(self.SUCCESS_URL)
        session = self.client.session
        self.assertNotIn(SESSION_KEY, session)


# ---------------------------------------------------------------------------
# FeePaymentCancelView
# ---------------------------------------------------------------------------


class FeePaymentCancelViewTests(FeePaymentViewTestBase):
    def test_get_returns_200(self):
        resp = self.client.get(self.CANCEL_URL)
        self.assertEqual(resp.status_code, 200)

    def test_get_clears_session(self):
        self._set_session(_session_data())
        self.client.get(self.CANCEL_URL)
        session = self.client.session
        self.assertNotIn(SESSION_KEY, session)

    def test_get_without_session_does_not_crash(self):
        """Cancel with no session key set is still a valid 200."""
        resp = self.client.get(self.CANCEL_URL)
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# M-F — IP masking via ipware in fee_payment views
# ---------------------------------------------------------------------------


class MFIPMaskingFeePaymentTests(FeePaymentViewTestBase):
    """M-F: session-expired log in fee_payment uses _mask_ip(_get_client_ip()), not REMOTE_ADDR."""

    def _post_intent(self, **extra):
        return self.client.post(
            self.INTENT_URL,
            content_type="application/json",
            **extra,
        )

    def test_session_expired_log_uses_get_client_ip_not_remote_addr(self):
        """session_expired warning must call _get_client_ip, not request.META['REMOTE_ADDR']."""
        # No session set — will trigger session_expired branch (403 response)
        with patch(
            "apps.payments.views.fee_payment._get_client_ip", return_value="203.0.113.5"
        ) as mock_ip:
            resp = self._post_intent()
        self.assertEqual(resp.status_code, 403)
        mock_ip.assert_called()

    def test_session_expired_log_does_not_use_raw_remote_addr(self):
        """REMOTE_ADDR must not appear directly in the masked IP log — ipware must be used."""
        # Patch request.META to have a proxy IP as REMOTE_ADDR
        # and ipware to return the real client IP
        with patch(
            "apps.payments.views.fee_payment._get_client_ip", return_value="1.2.3.4"
        ) as mock_ip:
            resp = self._post_intent()
        self.assertEqual(resp.status_code, 403)
        # _get_client_ip was called, not raw META access
        mock_ip.assert_called_once()


# ---------------------------------------------------------------------------
# M-M — FeePaymentCancelView cancels Stripe PI
# ---------------------------------------------------------------------------


class MMFeePaymentCancelStripeTests(FeePaymentViewTestBase):
    """M-M: FeePaymentCancelView must cancel the live Stripe PI via on_commit."""

    def test_cancel_view_cancels_stripe_pi_on_commit(self):
        """When a pending PaymentIntent exists in session, cancel it via on_commit."""
        intent = make_payment_intent(
            self.user,
            status=PaymentIntent.STATUS_PENDING,
            gateway_intent_id="pi_fee_live_001",
        )
        sd = _session_data()
        sd["payment_intent_pk"] = str(intent.pk)
        self._set_session(sd)

        mock_gw = MagicMock()
        mock_gw.cancel_payment_intent.return_value = {}

        with patch("apps.payments.views.fee_payment.get_gateway", return_value=mock_gw):
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.client.get(self.CANCEL_URL)

        self.assertEqual(resp.status_code, 200)
        mock_gw.cancel_payment_intent.assert_called_once_with("pi_fee_live_001")

    def test_cancel_view_no_stripe_call_when_no_session_pi(self):
        """No Stripe PI cancel when session has no payment_intent_pk."""
        mock_gw = MagicMock()
        with patch("apps.payments.views.fee_payment.get_gateway", return_value=mock_gw):
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.client.get(self.CANCEL_URL)
        self.assertEqual(resp.status_code, 200)
        mock_gw.cancel_payment_intent.assert_not_called()

    def test_cancel_view_session_cleared_after_get(self):
        """Session must be cleared regardless of whether a PI exists."""
        intent = make_payment_intent(self.user, status=PaymentIntent.STATUS_PENDING)
        sd = _session_data()
        sd["payment_intent_pk"] = str(intent.pk)
        self._set_session(sd)

        mock_gw = MagicMock()
        with patch("apps.payments.views.fee_payment.get_gateway", return_value=mock_gw):
            with self.captureOnCommitCallbacks(execute=False):
                self.client.get(self.CANCEL_URL)

        session = self.client.session
        self.assertNotIn(SESSION_KEY, session)

    def test_cancel_view_stripe_cancel_failure_does_not_crash(self):
        """A Stripe cancel failure must not propagate — view must still return 200."""
        intent = make_payment_intent(
            self.user,
            status=PaymentIntent.STATUS_PENDING,
            gateway_intent_id="pi_fee_fail_test",
        )
        sd = _session_data()
        sd["payment_intent_pk"] = str(intent.pk)
        self._set_session(sd)

        mock_gw = MagicMock()
        mock_gw.cancel_payment_intent.side_effect = Exception("Stripe is down")

        with patch("apps.payments.views.fee_payment.get_gateway", return_value=mock_gw):
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.client.get(self.CANCEL_URL)

        self.assertEqual(resp.status_code, 200)

    def test_cancel_view_only_cancels_pending_intents(self):
        """Completed or failed intents must NOT be cancelled — only STATUS_PENDING."""
        intent = make_payment_intent(
            self.user,
            status=PaymentIntent.STATUS_COMPLETED,
            gateway_intent_id="pi_fee_completed",
        )
        sd = _session_data()
        sd["payment_intent_pk"] = str(intent.pk)
        self._set_session(sd)

        mock_gw = MagicMock()
        with patch("apps.payments.views.fee_payment.get_gateway", return_value=mock_gw):
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.client.get(self.CANCEL_URL)

        self.assertEqual(resp.status_code, 200)
        mock_gw.cancel_payment_intent.assert_not_called()
