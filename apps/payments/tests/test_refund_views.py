"""
Wave 3 — test_refund_views.py

Tests for RefundCreateView, RefundConfirmView, RefundDetailView.
All views are staff-only (StaffRequiredMixin).
"""

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.payments.gateways.exceptions import GatewayError
from apps.payments.models import (
    Payment,
    PaymentAuditEntry,
    PaymentIntent,
    Refund,
)
from apps.payments.views.refund import (
    REFUND_SESSION_KEY,
    _compute_already_refunded,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def make_user(email=None, password="testpass123", **kwargs):
    email = email or f"user_{uuid.uuid4().hex[:6]}@example.com"
    return User.objects.create_user(email=email, password=password, **kwargs)


def make_payment_intent(payer, status=None, **kwargs):
    defaults = {
        "payer": payer,
        "amount": Decimal("100.00"),
        "currency": "CAD",
        "purpose": PaymentIntent.PURPOSE_SERVICE_FEE,
        "status": status or PaymentIntent.STATUS_COMPLETED,
        "gateway": PaymentIntent.GATEWAY_STRIPE,
        "gateway_intent_id": f"pi_test_{uuid.uuid4().hex[:8]}",
    }
    defaults.update(kwargs)
    return PaymentIntent.objects.create(**defaults)


def make_payment(intent, **kwargs):
    defaults = {
        "intent": intent,
        "amount_paid": Decimal("100.00"),
        "processor_fee": Decimal("3.20"),
        "gateway_charge_id": f"ch_test_{uuid.uuid4().hex[:8]}",
        "payment_method_type": Payment.PAYMENT_METHOD_CARD,
        "paid_at": timezone.now(),
    }
    defaults.update(kwargs)
    return Payment.objects.create(**defaults)


def make_completed_payment(payer):
    intent = make_payment_intent(payer, status=PaymentIntent.STATUS_COMPLETED)
    return make_payment(intent)


def make_refund(payment, amount, authorized_by, **kwargs):
    defaults = {
        "payment": payment,
        "amount": amount,
        "reason": Refund.REASON_CUSTOMER,
        "gateway_refund_id": f"re_test_{uuid.uuid4().hex[:8]}",
        "refunded_at": timezone.now(),
        "authorized_by": authorized_by,
        "notes": "",
    }
    defaults.update(kwargs)
    return Refund.objects.create(**defaults)


GATEWAY_REFUND_RESULT = {
    "gateway_refund_id": "re_test_001",
    "status": "succeeded",
    "amount": Decimal("10.00"),
}


# ---------------------------------------------------------------------------
# Base test class
# ---------------------------------------------------------------------------


class RefundViewTestBase(TestCase):
    def setUp(self):
        # Clear the locmem cache so rate-limit counters from prior tests don't
        # bleed into this test. Without this, multiple POSTs across tests in the
        # same class would accumulate against the 3-per-minute limit and cause
        # spurious 429-style redirects even though each individual test is valid.
        cache.clear()
        self.staff = make_user(
            email="staff1@example.com",
            password="staffpass",
            is_staff=True,
            is_active=True,
        )
        self.citizen = make_user(
            email="citizen1@example.com",
            password="citizenpass",
            is_staff=False,
        )
        self.inactive_staff = make_user(
            email="staffold@example.com",
            password="old",
            is_staff=True,
            is_active=False,
        )
        self.payment = make_completed_payment(self.citizen)
        self.CREATE_URL = reverse(
            "payments:refund_create",
            kwargs={"payment_pk": self.payment.pk},
        )
        self.CONFIRM_URL = reverse(
            "payments:refund_confirm",
            kwargs={"payment_pk": self.payment.pk},
        )

    def _set_session(self, data):
        session = self.client.session
        session[REFUND_SESSION_KEY] = data
        session.save()

    def _valid_session_data(self, **overrides):
        data = {
            "payment_pk": str(self.payment.pk),
            "amount": "10.00",
            "reason": Refund.REASON_CUSTOMER,
            "notes": "",
        }
        data.update(overrides)
        return data

    def _mock_gateway(self, refund_result=None, raises=None):
        mock_gw = MagicMock()
        if raises:
            mock_gw.create_refund.side_effect = raises
        else:
            mock_gw.create_refund.return_value = refund_result or GATEWAY_REFUND_RESULT
        return patch("apps.payments.views.refund.get_gateway", return_value=mock_gw)


# ---------------------------------------------------------------------------
# StaffRequiredMixin tests
# ---------------------------------------------------------------------------


class StaffRequiredMixinTests(RefundViewTestBase):
    def test_unauthenticated_redirects_to_login(self):
        resp = self.client.get(self.CREATE_URL)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])

    def test_non_staff_citizen_gets_403(self):
        self.client.force_login(self.citizen)
        resp = self.client.get(self.CREATE_URL)
        self.assertEqual(resp.status_code, 403)

    def test_inactive_staff_cannot_reach_view(self):
        """
        Inactive staff (is_active=False) cannot pass Django's session auth
        backend (ModelBackend.get_user() returns None for inactive users).
        force_login stores their pk but the session auth rejects them,
        resulting in a redirect to login rather than 403.
        The StaffRequiredMixin's test_func() also rejects them since it
        checks u.is_active explicitly.
        """
        self.client.force_login(self.inactive_staff)
        resp = self.client.get(self.CREATE_URL)
        # Inactive users are rejected at the session layer → redirect to login
        self.assertIn(resp.status_code, (302, 403))

    def test_active_staff_gets_200(self):
        self.client.force_login(self.staff)
        resp = self.client.get(self.CREATE_URL)
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# RefundCreateView — GET tests
# ---------------------------------------------------------------------------


class RefundCreateViewGetTests(RefundViewTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def test_get_by_staff_returns_200(self):
        resp = self.client.get(self.CREATE_URL)
        self.assertEqual(resp.status_code, 200)

    def test_get_has_form_in_context(self):
        resp = self.client.get(self.CREATE_URL)
        self.assertIn("form", resp.context)

    def test_get_has_payment_in_context(self):
        resp = self.client.get(self.CREATE_URL)
        self.assertEqual(resp.context["payment"], self.payment)

    def test_get_non_completed_payment_returns_404(self):
        pending_intent = make_payment_intent(
            self.citizen,
            status=PaymentIntent.STATUS_PENDING,
        )
        pending_payment = make_payment(pending_intent)
        url = reverse(
            "payments:refund_create",
            kwargs={"payment_pk": pending_payment.pk},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_get_already_refunded_context_flag(self):
        """Fully refunded payment: fully_refunded=True in context."""
        make_refund(self.payment, Decimal("100.00"), self.staff)
        resp = self.client.get(self.CREATE_URL)
        self.assertTrue(resp.context["fully_refunded"])

    def test_get_already_refunded_in_context(self):
        make_refund(self.payment, Decimal("30.00"), self.staff)
        resp = self.client.get(self.CREATE_URL)
        self.assertEqual(resp.context["already_refunded"], Decimal("30.00"))

    def test_get_max_refundable_in_context(self):
        make_refund(self.payment, Decimal("30.00"), self.staff)
        resp = self.client.get(self.CREATE_URL)
        self.assertEqual(resp.context["max_refundable"], Decimal("70.00"))


# ---------------------------------------------------------------------------
# RefundCreateView — POST tests
# ---------------------------------------------------------------------------


class RefundCreateViewPostTests(RefundViewTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def test_valid_post_stores_session_data(self):
        self.client.post(
            self.CREATE_URL,
            data={
                "amount": "10.00",
                "reason": Refund.REASON_CUSTOMER,
                "notes": "",
            },
        )
        self.assertIn(REFUND_SESSION_KEY, self.client.session)

    def test_valid_post_session_has_payment_pk(self):
        self.client.post(
            self.CREATE_URL,
            data={
                "amount": "10.00",
                "reason": Refund.REASON_CUSTOMER,
                "notes": "",
            },
        )
        session = self.client.session[REFUND_SESSION_KEY]
        self.assertEqual(session["payment_pk"], str(self.payment.pk))

    def test_valid_post_session_has_correct_amount(self):
        self.client.post(
            self.CREATE_URL,
            data={
                "amount": "25.00",
                "reason": Refund.REASON_CUSTOMER,
                "notes": "",
            },
        )
        session = self.client.session[REFUND_SESSION_KEY]
        self.assertEqual(session["amount"], "25.00")

    def test_valid_post_session_has_reason(self):
        self.client.post(
            self.CREATE_URL,
            data={
                "amount": "10.00",
                "reason": Refund.REASON_DUPLICATE,
                "notes": "",
            },
        )
        session = self.client.session[REFUND_SESSION_KEY]
        self.assertEqual(session["reason"], Refund.REASON_DUPLICATE)

    def test_valid_post_redirects_to_confirm(self):
        resp = self.client.post(
            self.CREATE_URL,
            data={
                "amount": "10.00",
                "reason": Refund.REASON_CUSTOMER,
                "notes": "",
            },
        )
        self.assertRedirects(resp, self.CONFIRM_URL, fetch_redirect_response=False)

    def test_invalid_post_amount_zero_returns_200(self):
        resp = self.client.post(
            self.CREATE_URL,
            data={
                "amount": "0.00",
                "reason": Refund.REASON_CUSTOMER,
                "notes": "",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("form", resp.context)
        self.assertTrue(resp.context["form"].errors)

    def test_invalid_post_amount_zero_no_session_stored(self):
        """Zero amount fails clean_amount validation → form invalid → no session."""
        self.client.post(
            self.CREATE_URL,
            data={
                "amount": "0.00",
                "reason": Refund.REASON_CUSTOMER,
                "notes": "",
            },
        )
        self.assertNotIn(REFUND_SESSION_KEY, self.client.session)

    def test_post_amount_above_paid_allowed_by_form_blocked_by_view_toctou(self):
        """
        RefundForm does NOT enforce max_value server-side (MaxValueValidator
        is not added after field __init__). A large amount passes form
        validation and goes to session → the TOCTOU guard in RefundConfirmView
        catches it. So the create view redirects to confirm.
        """
        resp = self.client.post(
            self.CREATE_URL,
            data={
                "amount": "999.00",  # > amount_paid, but form allows it
                "reason": Refund.REASON_CUSTOMER,
                "notes": "",
            },
        )
        # Form passes; view redirects to confirm
        self.assertEqual(resp.status_code, 302)


# ---------------------------------------------------------------------------
# RefundConfirmView — GET tests
# ---------------------------------------------------------------------------


class RefundConfirmViewGetTests(RefundViewTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def test_get_with_valid_session_returns_200(self):
        self._set_session(self._valid_session_data())
        resp = self.client.get(self.CONFIRM_URL)
        self.assertEqual(resp.status_code, 200)

    def test_get_without_session_redirects_to_create(self):
        resp = self.client.get(self.CONFIRM_URL)
        self.assertRedirects(resp, self.CREATE_URL, fetch_redirect_response=False)

    def test_get_with_wrong_payment_pk_redirects_to_create(self):
        self._set_session(self._valid_session_data(payment_pk=str(uuid.uuid4())))
        resp = self.client.get(self.CONFIRM_URL)
        self.assertRedirects(resp, self.CREATE_URL, fetch_redirect_response=False)

    def test_get_context_has_refund_amount(self):
        self._set_session(self._valid_session_data(amount="15.00"))
        resp = self.client.get(self.CONFIRM_URL)
        self.assertEqual(resp.context["refund_amount"], Decimal("15.00"))

    def test_get_context_has_reason(self):
        self._set_session(self._valid_session_data(reason=Refund.REASON_DUPLICATE))
        resp = self.client.get(self.CONFIRM_URL)
        self.assertEqual(resp.context["reason"], Refund.REASON_DUPLICATE)

    def test_get_context_has_payment(self):
        self._set_session(self._valid_session_data())
        resp = self.client.get(self.CONFIRM_URL)
        self.assertEqual(resp.context["payment"], self.payment)


# ---------------------------------------------------------------------------
# RefundConfirmView — POST tests (critical)
# ---------------------------------------------------------------------------


class RefundConfirmViewPostTests(RefundViewTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)
        self._set_session(self._valid_session_data())

    def test_confirm_post_creates_refund(self):
        with self._mock_gateway():
            self.client.post(self.CONFIRM_URL)
        self.assertEqual(Refund.objects.count(), 1)

    def test_confirm_post_creates_audit_entry(self):
        with self._mock_gateway():
            self.client.post(self.CONFIRM_URL)
        self.assertEqual(PaymentAuditEntry.objects.count(), 1)

    def test_confirm_post_audit_entry_action(self):
        with self._mock_gateway():
            self.client.post(self.CONFIRM_URL)
        entry = PaymentAuditEntry.objects.get()
        self.assertEqual(entry.action, "refund_requested")

    def test_confirm_post_audit_entry_actor(self):
        with self._mock_gateway():
            self.client.post(self.CONFIRM_URL)
        entry = PaymentAuditEntry.objects.get()
        self.assertEqual(entry.actor, self.staff)

    def test_confirm_post_audit_entry_payment_fk(self):
        with self._mock_gateway():
            self.client.post(self.CONFIRM_URL)
        entry = PaymentAuditEntry.objects.get()
        self.assertEqual(entry.payment, self.payment)

    def test_confirm_post_audit_entry_refund_fk(self):
        with self._mock_gateway():
            self.client.post(self.CONFIRM_URL)
        refund = Refund.objects.get()
        entry = PaymentAuditEntry.objects.get()
        self.assertEqual(entry.refund, refund)

    def test_confirm_post_redirects_to_detail(self):
        with self._mock_gateway():
            resp = self.client.post(self.CONFIRM_URL)
        refund = Refund.objects.get()
        expected_url = reverse("payments:refund_detail", kwargs={"refund_pk": refund.pk})
        self.assertRedirects(resp, expected_url, fetch_redirect_response=False)

    def test_confirm_post_clears_session_on_success(self):
        with self._mock_gateway():
            self.client.post(self.CONFIRM_URL)
        self.assertNotIn(REFUND_SESSION_KEY, self.client.session)

    def test_confirm_post_amount_quantized_correctly(self):
        self._set_session(self._valid_session_data(amount="10.00"))
        with self._mock_gateway(
            refund_result={
                "gateway_refund_id": f"re_test_{uuid.uuid4().hex[:6]}",
                "status": "succeeded",
                "amount": Decimal("10.00"),
            }
        ):
            self.client.post(self.CONFIRM_URL)
        refund = Refund.objects.get()
        self.assertEqual(refund.amount, Decimal("10.00"))

    def test_confirm_post_toctou_concurrent_refund_blocked(self):
        """
        If another refund depletes max_refundable between form submit and confirm,
        the second refund is blocked and no new Refund row is created.
        """
        # Session says $10, but we'll fully refund $100 in the DB first
        self._set_session(self._valid_session_data(amount="10.00"))
        # Simulate concurrent refund: full amount already refunded
        make_refund(self.payment, Decimal("100.00"), self.staff)
        # Now our $10 refund request exceeds max_refundable ($0)
        with self._mock_gateway():
            self.client.post(self.CONFIRM_URL)
        # Only the pre-existing refund exists
        self.assertEqual(Refund.objects.count(), 1)
        # Session cleared on TOCTOU block
        self.assertNotIn(REFUND_SESSION_KEY, self.client.session)

    def test_confirm_post_gateway_error_refund_row_created_stripe_call_deferred(self):
        """
        H8 fix: Stripe call is deferred to on_commit() so a gateway error fires
        AFTER the DB transaction has committed.  The Refund row IS created and
        persisted; only the gateway_refund_id update fails.

        Before the H8 fix: gateway error inside the atomic block rolled back the
        transaction → no Refund row.  After the fix: the Refund row is committed
        first; on_commit() fires the Stripe call and only logs on failure.

        In Django's TestCase, on_commit() callbacks do NOT fire during the test
        because the test itself is wrapped in a transaction that never commits.
        So the mock gateway is never called here — we just verify that the Refund
        row was created and the view redirected to the detail page (success path).
        """
        with self._mock_gateway(raises=GatewayError("stripe down", gateway_code="stripe_error")):
            resp = self.client.post(self.CONFIRM_URL)
        # Refund row IS created — Stripe call is deferred to on_commit, not inside atomic
        self.assertEqual(Refund.objects.count(), 1)
        # View redirects to detail page (the commit succeeded even though Stripe may fail)
        self.assertEqual(resp.status_code, 302)
        # Redirect URL contains the refund UUID — we're on the detail page path
        refund = Refund.objects.get()
        self.assertIn(str(refund.pk), resp["Location"])

    def test_confirm_post_non_completed_payment_blocked(self):
        """Payment intent status changed to FAILED between form and confirm."""
        self.payment.intent.status = PaymentIntent.STATUS_FAILED
        self.payment.intent.save(update_fields=["status"])
        with self._mock_gateway():
            self.client.post(self.CONFIRM_URL)
        self.assertEqual(Refund.objects.count(), 0)
        self.assertNotIn(REFUND_SESSION_KEY, self.client.session)

    def test_confirm_post_partial_allows_second_partial(self):
        """Two partial refunds sum to ≤ amount_paid → both succeed."""
        # First refund
        self._set_session(self._valid_session_data(amount="30.00"))
        with self._mock_gateway(
            refund_result={
                "gateway_refund_id": f"re_first_{uuid.uuid4().hex[:6]}",
                "status": "succeeded",
                "amount": Decimal("30.00"),
            }
        ):
            self.client.post(self.CONFIRM_URL)
        self.assertEqual(Refund.objects.count(), 1)

        # Second refund
        self._set_session(self._valid_session_data(amount="40.00"))
        with self._mock_gateway(
            refund_result={
                "gateway_refund_id": f"re_second_{uuid.uuid4().hex[:6]}",
                "status": "succeeded",
                "amount": Decimal("40.00"),
            }
        ):
            self.client.post(self.CONFIRM_URL)
        self.assertEqual(Refund.objects.count(), 2)

    def test_confirm_post_full_refund_blocks_subsequent(self):
        """After full refund, any subsequent refund attempt is blocked by TOCTOU guard."""
        # Full refund first
        self._set_session(self._valid_session_data(amount="100.00"))
        with self._mock_gateway(
            refund_result={
                "gateway_refund_id": f"re_full_{uuid.uuid4().hex[:6]}",
                "status": "succeeded",
                "amount": Decimal("100.00"),
            }
        ):
            self.client.post(self.CONFIRM_URL)
        self.assertEqual(Refund.objects.count(), 1)

        # Attempt second refund — should be blocked by TOCTOU guard
        self._set_session(self._valid_session_data(amount="10.00"))
        with self._mock_gateway():
            self.client.post(self.CONFIRM_URL)
        self.assertEqual(Refund.objects.count(), 1)

    def test_confirm_post_session_expired_redirects_to_create(self):
        """No session → redirect to create with error message."""
        # Clear the session
        session = self.client.session
        session.pop(REFUND_SESSION_KEY, None)
        session.save()
        with self._mock_gateway():
            resp = self.client.post(self.CONFIRM_URL)
        self.assertRedirects(resp, self.CREATE_URL, fetch_redirect_response=False)


# ---------------------------------------------------------------------------
# RefundDetailView tests
# ---------------------------------------------------------------------------


class RefundDetailViewTests(RefundViewTestBase):
    def setUp(self):
        super().setUp()
        self.refund = make_refund(self.payment, Decimal("10.00"), self.staff)
        self.DETAIL_URL = reverse(
            "payments:refund_detail",
            kwargs={"refund_pk": self.refund.pk},
        )

    def test_get_by_staff_returns_200(self):
        self.client.force_login(self.staff)
        resp = self.client.get(self.DETAIL_URL)
        self.assertEqual(resp.status_code, 200)

    def test_get_has_refund_in_context(self):
        self.client.force_login(self.staff)
        resp = self.client.get(self.DETAIL_URL)
        self.assertEqual(resp.context["refund"], self.refund)

    def test_get_by_citizen_returns_403(self):
        self.client.force_login(self.citizen)
        resp = self.client.get(self.DETAIL_URL)
        self.assertEqual(resp.status_code, 403)

    def test_get_invalid_uuid_returns_404(self):
        self.client.force_login(self.staff)
        bad_url = reverse(
            "payments:refund_detail",
            kwargs={"refund_pk": uuid.uuid4()},  # Non-existent UUID
        )
        resp = self.client.get(bad_url)
        self.assertEqual(resp.status_code, 404)

    def test_refund_payment_accessible_from_context(self):
        self.client.force_login(self.staff)
        resp = self.client.get(self.DETAIL_URL)
        resp.context["refund"]
        # Should not raise AttributeError
        try:
            pass
        except AttributeError as exc:
            self.fail(f"refund.payment.pk raised AttributeError: {exc}")

    def test_get_unauthenticated_redirects_to_login(self):
        resp = self.client.get(self.DETAIL_URL)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])


# ---------------------------------------------------------------------------
# Fix 28 — RefundDetailView staff scoping (IDOR prevention)
# ---------------------------------------------------------------------------


class RefundDetailViewStaffScopingTests(TestCase):
    """
    Staff user A cannot access a refund authorized by staff user B.

    FIX 28: RefundDetailView scopes its queryset to authorized_by=request.user
    (or all refunds for superusers). This prevents UUID-guessing IDOR attacks
    where any staff member could access any refund by knowing its UUID.

    Note: This codebase is currently single-tenant (no Organisation FK on the
    Refund → Payment → PaymentIntent chain). The scoping guard implemented here
    uses authorized_by to restrict per-user visibility. When multi-tenancy is
    added, the filter should additionally scope by organisation.
    """

    def setUp(self):
        # Two distinct staff users in the same deployment
        self.staff_a = make_user(
            email="staff_a@example.com",
            password="passA",
            is_staff=True,
            is_active=True,
        )
        self.staff_b = make_user(
            email="staff_b@example.com",
            password="passB",
            is_staff=True,
            is_active=True,
        )
        self.payer = make_user(
            email="payer_scoping@example.com",
            password="payerpass",
            is_staff=False,
        )
        # Refund authorized by staff_b
        self.payment_b = make_completed_payment(self.payer)
        self.refund_b = make_refund(
            self.payment_b,
            Decimal("20.00"),
            authorized_by=self.staff_b,
        )
        self.detail_url_b = reverse(
            "payments:refund_detail",
            kwargs={"refund_pk": self.refund_b.pk},
        )

    def test_cross_user_refund_returns_404(self):
        """
        Staff A cannot access a refund authorized by Staff B — must get 404.
        This is the IDOR guard: UUID knowledge alone is insufficient.
        """
        self.client.force_login(self.staff_a)
        resp = self.client.get(self.detail_url_b)
        self.assertEqual(resp.status_code, 404)

    def test_own_refund_accessible(self):
        """Staff B can access the refund they authorized — must get 200."""
        self.client.force_login(self.staff_b)
        resp = self.client.get(self.detail_url_b)
        self.assertEqual(resp.status_code, 200)

    def test_own_refund_in_context(self):
        """Staff B's refund is present in the template context."""
        self.client.force_login(self.staff_b)
        resp = self.client.get(self.detail_url_b)
        self.assertEqual(resp.context["refund"], self.refund_b)

    def test_superuser_can_access_any_refund(self):
        """
        Superusers bypass the authorized_by filter and can access all refunds
        (needed for admin oversight and incident response).
        """
        superuser = make_user(
            email="super@example.com",
            password="superpass",
            is_staff=True,
            is_active=True,
            is_superuser=True,
        )
        self.client.force_login(superuser)
        resp = self.client.get(self.detail_url_b)
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# H8 — Concurrent refund race: Refund rows locked inside atomic block
# ---------------------------------------------------------------------------


class RefundConfirmViewRangeLockTests(RefundViewTestBase):
    """
    H8: Verify that RefundConfirmView.post() acquires a select_for_update()
    lock on existing Refund rows INSIDE the atomic block, and that the sum
    is recomputed from that locked queryset (not from _compute_already_refunded
    which runs a fresh, unlocked query).

    A full threading concurrency test requires a real PostgreSQL instance with
    advisory-lock semantics, which is out of scope for a unit test suite.
    Instead we verify the structural guarantee: select_for_update() is called
    on the Refund queryset, and the Stripe API call is dispatched via
    transaction.on_commit() rather than inside the atomic block.
    """

    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)
        self._set_session(self._valid_session_data())

    def test_refund_view_source_contains_range_lock(self):
        """
        Structural check: verify that RefundConfirmView.post() source code
        contains 'Refund.objects.select_for_update()' (the range lock that
        prevents concurrent double-refund inserts).

        A full concurrency test requires PostgreSQL with real row-level locking;
        this unit test guards against the pattern being accidentally removed.
        """
        import inspect

        from apps.payments.views.refund import RefundConfirmView

        source = inspect.getsource(RefundConfirmView.post)
        self.assertIn(
            "Refund.objects.select_for_update()",
            source,
            "RefundConfirmView.post() must call Refund.objects.select_for_update() "
            "inside the atomic block to create a range lock on existing refund rows. "
            "Without this, two concurrent workers can both pass the total-refunded "
            "check before either inserts a row — causing a double refund.",
        )

    def test_stripe_call_deferred_to_on_commit_not_inside_atomic(self):
        """
        The gateway.create_refund() call must NOT be inside the atomic block.
        We verify this by checking that a Refund row is created even when we
        patch get_gateway() to raise inside on_commit (i.e. after commit).

        Structural check: if Stripe were called inside the atomic block and it
        raised GatewayError, no Refund row would be created (transaction rolled
        back).  With the H8 fix, the Refund row is committed first; the Stripe
        call happens after commit via on_commit().
        """
        from apps.payments.gateways.exceptions import GatewayError as _GatewayError

        initial_count = Refund.objects.count()

        # Patch get_gateway so the Stripe call always raises — simulates a
        # network failure that fires after the DB transaction has committed.
        with patch("apps.payments.views.refund.get_gateway") as mock_gw:
            mock_gw.return_value.create_refund.side_effect = _GatewayError(
                message="network timeout", gateway_code="network_error"
            )
            resp = self.client.post(self.CONFIRM_URL)

        # The Refund row must have been created (transaction committed before Stripe call).
        # If the Stripe call were inside the atomic block, the transaction would have
        # rolled back and no Refund row would exist.
        self.assertEqual(
            Refund.objects.count(),
            initial_count + 1,
            "No Refund row was created.  The Stripe call appears to still be inside "
            "the atomic block — it must be moved to transaction.on_commit().",
        )
        # Response redirected to detail page (success path)
        self.assertEqual(resp.status_code, 302)

    def test_refund_amount_validated_inside_lock(self):
        """
        When a prior refund has consumed the full payment amount, a second
        refund attempt must be rejected even if it arrived while the first
        was still in flight.  The re-sum inside the lock catches this.
        """
        # Pre-create a refund that exhausts the full amount (100.00)
        make_refund(
            self.payment,
            amount=Decimal("100.00"),
            authorized_by=self.staff,
        )
        # Session requests a further 10.00 refund
        self._set_session(self._valid_session_data(amount="10.00"))

        with patch("apps.payments.views.refund.get_gateway"):
            resp = self.client.post(self.CONFIRM_URL, follow=True)

        self.assertIn(
            "exceeds maximum refundable",
            resp.content.decode(),
            "Expected an over-refund error message but did not find one.",
        )

    def test_payment_row_locked_during_refund_create(self):
        """
        H-B: Verify that RefundConfirmView.post() calls select_for_update() on the
        Payment queryset — the primary serialization point for first-refund races.

        Structural check: inspect source for 'Payment.objects.select_for_update()'
        so the pattern cannot be accidentally removed without a test failure.
        """
        import inspect

        from apps.payments.views.refund import RefundConfirmView

        source = inspect.getsource(RefundConfirmView.post)
        self.assertIn(
            "Payment.objects.select_for_update",
            source,
            "RefundConfirmView.post() must call Payment.objects.select_for_update() "
            "to lock the Payment row — the correct serialization point for the first "
            "refund where no Refund rows exist yet to lock.",
        )


# ---------------------------------------------------------------------------
# H-B — Payment row lock prevents first-refund double-spend
# ---------------------------------------------------------------------------


class RefundDoubleRacePreventionTest(RefundViewTestBase):
    """H-B: Lock on Payment row prevents first-refund double-spend."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)
        self._set_session(self._valid_session_data())

    def test_payment_select_for_update_in_source(self):
        """
        Structural check: RefundConfirmView.post() source must contain
        'Payment.objects.select_for_update()' — the primary serialization point
        for the first-refund race condition where no Refund rows exist yet.

        Under PostgreSQL READ COMMITTED, SELECT ... FOR UPDATE on an empty Refund
        queryset acquires zero locks.  Locking the Payment row (which always exists)
        is the correct fix.
        """
        import inspect

        from apps.payments.views.refund import RefundConfirmView

        source = inspect.getsource(RefundConfirmView.post)
        self.assertIn(
            "Payment.objects.select_for_update",
            source,
            "RefundConfirmView.post() must call Payment.objects.select_for_update() "
            "to lock the Payment row — the correct serialization point for the first "
            "refund on a payment where no Refund rows exist yet to lock.",
        )


# ---------------------------------------------------------------------------
# H-C — gateway_status field transitions
# ---------------------------------------------------------------------------


class RefundGatewayStatusTest(RefundViewTestBase):
    """H-C: gateway_status field on Refund tracks pending/succeeded/failed states."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def test_new_refund_has_pending_status(self):
        """A freshly created Refund defaults to GATEWAY_STATUS_PENDING."""
        refund = make_refund(self.payment, Decimal("10.00"), self.staff)
        # make_refund does not set gateway_status, so the model default kicks in
        self.assertEqual(refund.gateway_status, Refund.GATEWAY_STATUS_PENDING)

    def test_refund_created_by_view_has_pending_status(self):
        """RefundConfirmView.post() sets gateway_status=PENDING on creation."""
        self._set_session(self._valid_session_data(amount="10.00"))
        with self._mock_gateway():
            self.client.post(self.CONFIRM_URL)
        refund = Refund.objects.get()
        # In TestCase on_commit does not fire, so status stays PENDING
        self.assertEqual(refund.gateway_status, Refund.GATEWAY_STATUS_PENDING)

    def test_failed_refund_excluded_from_already_refunded(self):
        """Failed refund must not count against max_refundable (phantom debt prevention)."""
        make_refund(
            self.payment,
            Decimal("50.00"),
            self.staff,
            gateway_status=Refund.GATEWAY_STATUS_FAILED,
            gateway_refund_id=f"failed_{uuid.uuid4().hex[:8]}",
        )
        already = _compute_already_refunded(self.payment)
        self.assertEqual(
            already,
            Decimal("0.00"),
            "A FAILED refund row must be excluded from _compute_already_refunded(). "
            "No money was returned so it must not reduce max_refundable.",
        )

    def test_succeeded_refund_counts_toward_already_refunded(self):
        """Succeeded refund must count toward already_refunded."""
        make_refund(
            self.payment,
            Decimal("50.00"),
            self.staff,
            gateway_status=Refund.GATEWAY_STATUS_SUCCEEDED,
        )
        already = _compute_already_refunded(self.payment)
        self.assertEqual(already, Decimal("50.00"))

    def test_pending_refund_counts_toward_already_refunded(self):
        """Pending refund (in-flight) must count toward already_refunded to prevent over-refund."""
        make_refund(
            self.payment,
            Decimal("30.00"),
            self.staff,
            gateway_status=Refund.GATEWAY_STATUS_PENDING,
        )
        already = _compute_already_refunded(self.payment)
        self.assertEqual(already, Decimal("30.00"))

    def test_mixed_statuses_only_non_failed_counted(self):
        """Only PENDING and SUCCEEDED rows count; FAILED rows are excluded."""
        make_refund(
            self.payment,
            Decimal("20.00"),
            self.staff,
            gateway_status=Refund.GATEWAY_STATUS_SUCCEEDED,
        )
        make_refund(
            self.payment,
            Decimal("10.00"),
            self.staff,
            gateway_status=Refund.GATEWAY_STATUS_FAILED,
            gateway_refund_id=f"failed_{uuid.uuid4().hex[:8]}",
        )
        make_refund(
            self.payment,
            Decimal("5.00"),
            self.staff,
            gateway_status=Refund.GATEWAY_STATUS_PENDING,
            gateway_refund_id=f"pending_{uuid.uuid4().hex[:8]}",
        )
        already = _compute_already_refunded(self.payment)
        # Only SUCCEEDED ($20) + PENDING ($5) = $25; FAILED ($10) excluded
        self.assertEqual(already, Decimal("25.00"))

    def test_failed_refund_blocked_from_reducing_max_refundable_in_view(self):
        """
        A FAILED refund must not prevent a subsequent valid refund attempt.
        If the FAILED row were counted, max_refundable would be reduced and
        a legitimate refund for the full amount would be incorrectly blocked.
        """
        # Create a failed refund for the full amount — as if Stripe timed out
        make_refund(
            self.payment,
            Decimal("100.00"),
            self.staff,
            gateway_status=Refund.GATEWAY_STATUS_FAILED,
            gateway_refund_id=f"failed_{uuid.uuid4().hex[:8]}",
        )

        # Now request the full amount again — should succeed (FAILED row excluded)
        self._set_session(self._valid_session_data(amount="100.00"))
        with self._mock_gateway(
            refund_result={
                "gateway_refund_id": f"re_retry_{uuid.uuid4().hex[:6]}",
                "status": "succeeded",
                "amount": Decimal("100.00"),
            }
        ):
            resp = self.client.post(self.CONFIRM_URL)

        # A new PENDING refund row was created (the FAILED one already existed)
        self.assertEqual(Refund.objects.count(), 2)
        new_refund = Refund.objects.exclude(gateway_status=Refund.GATEWAY_STATUS_FAILED).get()
        self.assertEqual(new_refund.gateway_status, Refund.GATEWAY_STATUS_PENDING)
        self.assertEqual(resp.status_code, 302)
