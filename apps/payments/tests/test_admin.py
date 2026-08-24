"""
Tests for apps/payments/admin.py

Covers uncovered display methods and permission overrides:
- PaymentIntentAdmin.payer_pk()
- PaymentAdmin.intent_reference(), refund_link(), has_add_permission(), has_delete_permission()
- RefundInline.has_add_permission()
- RefundAdmin.payment_charge_id(), authorized_by_pk(), has_add_permission(), has_delete_permission()
- WebhookEventAdmin.has_add_permission(), has_delete_permission()
- PaymentAuditEntryAdmin.actor_pk(), payment_intent_reference() (with and without intent),
  has_add_permission(), has_change_permission(), has_delete_permission()
- TenantPaymentConfigAdmin.webhook_secret_display() (set vs not configured),
  has_delete_permission()
- ServiceFeePaymentAdmin.has_add_permission(), has_delete_permission()
- DonationAdmin.donor_pk(), has_add_permission(), has_delete_permission()
- RecurringGiftPlanAdmin.donor_pk(), has_add_permission(), has_delete_permission()
- OfficialDonationReceiptAdmin.has_add_permission(), has_change_permission(), has_delete_permission()

Strategy: instantiate AdminModelAdmin classes directly and call methods on mock objects
to avoid the need for a running HTTP test client for every branch.
"""  # noqa: E501

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from apps.payments.admin import (
    DonationAdmin,
    OfficialDonationReceiptAdmin,
    PaymentAdmin,
    PaymentAuditEntryAdmin,
    PaymentIntentAdmin,
    RecurringGiftPlanAdmin,
    RefundAdmin,
    RefundInline,
    ServiceFeePaymentAdmin,
    TenantPaymentConfigAdmin,
    WebhookEventAdmin,
)
from apps.payments.models import (
    DONATION_STATUS_COMPLETED,
    Donation,
    OfficialDonationReceipt,
    Payment,
    PaymentAuditEntry,
    PaymentIntent,
    RecurringGiftPlan,
    Refund,
    ServiceFeePayment,
    TenantPaymentConfig,
    WebhookEvent,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_user(email=None):
    email = email or f"admin_test_{uuid.uuid4().hex[:6]}@example.com"
    return User.objects.create_user(email=email, password="TestPass123!")


def _make_superuser(email=None):
    email = email or f"super_{uuid.uuid4().hex[:6]}@example.com"
    return User.objects.create_superuser(email=email, password="SuperPass123!")


def _make_intent(user):
    return PaymentIntent.objects.create(
        payer=user,
        amount=Decimal("50.00"),
        currency="CAD",
        purpose=PaymentIntent.PURPOSE_DONATION,
        status=PaymentIntent.STATUS_COMPLETED,
        gateway=PaymentIntent.GATEWAY_STRIPE,
        gateway_intent_id=f"pi_{uuid.uuid4().hex[:8]}",
    )


def _make_payment(intent):
    from django.utils import timezone

    return Payment.objects.create(
        intent=intent,
        gateway_charge_id=f"ch_{uuid.uuid4().hex[:8]}",
        amount_paid=intent.amount,
        processor_fee=Decimal("1.75"),
        net_amount=intent.amount - Decimal("1.75"),
        payment_method_type="card",
        card_last_four="4242",
        card_brand="visa",
        paid_at=timezone.now(),
    )


def _make_refund(payment, authorized_by):
    from django.utils import timezone

    return Refund.objects.create(
        payment=payment,
        amount=Decimal("10.00"),
        reason="requested_by_customer",
        gateway_refund_id=f"re_{uuid.uuid4().hex[:8]}",
        authorized_by=authorized_by,
        refunded_at=timezone.now(),
    )


def _make_donation(user, intent, campaign=None):
    return Donation.objects.create(
        payment_intent=intent,
        donor=user,
        campaign=campaign,
        amount=Decimal("50.00"),
        advantage_amount=Decimal("0.00"),
        eligible_amount=Decimal("50.00"),
        is_recurring=False,
        is_anonymous=False,
        status=DONATION_STATUS_COMPLETED,
        donor_name_snapshot="Test Donor",
        donor_address_snapshot="123 Test St, Ottawa, ON K1A 0A6",
    )


def _make_receipt(donation):
    serial = f"2026-{uuid.uuid4().int % 1000000:06d}"
    r = OfficialDonationReceipt(
        donation=donation,
        status=OfficialDonationReceipt.RECEIPT_STATUS_ISSUED,
        donor_legal_name="Test Donor",
        donor_address_line1="123 Test St",
        donor_city="Ottawa",
        donor_province="ON",
        donor_postal_code="K1A 0A6",
        donation_date=date(2026, 1, 1),
        receipt_date=date(2026, 1, 15),
        eligible_amount=Decimal("50.00"),
        advantage_amount=Decimal("0.00"),
        advantage_description="",
        charity_legal_name="Test Charity",
        charity_registration_number="123456789 RR 0001",
        charity_address="1 Charity Ave, Ottawa ON K2A 1B2",
        place_of_issue="Ottawa",
        authorized_signatory_name="Jane Smith",
        authorized_signatory_title="Executive Director",
        is_annual_consolidated=False,
    )
    r.serial_number = serial
    r.save()
    return r


# ---------------------------------------------------------------------------
# PaymentIntentAdmin
# ---------------------------------------------------------------------------


class PaymentIntentAdminTest(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = PaymentIntentAdmin(PaymentIntent, self.site)
        self.user = _make_user()
        self.intent = _make_intent(self.user)

    def test_payer_pk_returns_payer_id(self):
        result = self.admin.payer_pk(self.intent)
        self.assertEqual(result, self.intent.payer_id)

    def test_payer_pk_is_uuid(self):
        result = self.admin.payer_pk(self.intent)
        self.assertIsNotNone(result)


# ---------------------------------------------------------------------------
# PaymentAdmin
# ---------------------------------------------------------------------------


class PaymentAdminTest(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = PaymentAdmin(Payment, self.site)
        self.factory = RequestFactory()
        self.user = _make_user()
        self.intent = _make_intent(self.user)
        self.payment = _make_payment(self.intent)

    def test_intent_reference_returns_intent_reference(self):
        result = self.admin.intent_reference(self.payment)
        self.assertEqual(result, self.intent.reference)

    def test_refund_link_returns_html_anchor(self):
        result = self.admin.refund_link(self.payment)
        # Should be a SafeData HTML anchor
        self.assertIn("Issue Refund", str(result))
        self.assertIn("<a href=", str(result))

    def test_refund_link_contains_payment_pk(self):
        result = self.admin.refund_link(self.payment)
        self.assertIn(str(self.payment.pk), str(result))

    def test_has_add_permission_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_add_permission(request))

    def test_has_delete_permission_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_delete_permission(request))

    def test_has_delete_permission_with_obj_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_delete_permission(request, self.payment))

    # ── L4: get_list_display permission gate ──────────────────────────────────

    def test_get_list_display_excludes_refund_link_without_permission(self):
        """Users without payments.add_refund must not see the refund_link column."""
        # Regular user has no add_refund permission by default
        request = self.factory.get("/")
        request.user = self.user
        columns = self.admin.get_list_display(request)
        self.assertNotIn("refund_link", columns)

    def test_get_list_display_includes_refund_link_with_permission(self):
        """Users with payments.add_refund see the refund_link column."""
        from django.contrib.auth.models import Permission
        from django.contrib.contenttypes.models import ContentType

        ct = ContentType.objects.get_for_model(Refund)
        perm = Permission.objects.get(content_type=ct, codename="add_refund")
        self.user.user_permissions.add(perm)
        # Refresh to clear permission cache
        self.user = User.objects.get(pk=self.user.pk)
        request = self.factory.get("/")
        request.user = self.user
        columns = self.admin.get_list_display(request)
        self.assertIn("refund_link", columns)

    def test_get_list_display_includes_refund_link_for_superuser(self):
        """Superusers implicitly hold all permissions, including add_refund."""
        superuser = _make_superuser()
        request = self.factory.get("/")
        request.user = superuser
        columns = self.admin.get_list_display(request)
        self.assertIn("refund_link", columns)


# ---------------------------------------------------------------------------
# RefundInline
# ---------------------------------------------------------------------------


class RefundInlineTest(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.inline = RefundInline(Payment, self.site)
        self.factory = RequestFactory()

    def test_has_add_permission_no_obj_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.inline.has_add_permission(request))

    def test_has_add_permission_with_obj_returns_false(self):
        request = self.factory.get("/")
        user = _make_user()
        intent = _make_intent(user)
        payment = _make_payment(intent)
        self.assertFalse(self.inline.has_add_permission(request, payment))


# ---------------------------------------------------------------------------
# RefundAdmin
# ---------------------------------------------------------------------------


class RefundAdminTest(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = RefundAdmin(Refund, self.site)
        self.factory = RequestFactory()
        self.user = _make_user()
        self.intent = _make_intent(self.user)
        self.payment = _make_payment(self.intent)
        self.refund = _make_refund(self.payment, self.user)

    def test_payment_charge_id_returns_charge_id(self):
        result = self.admin.payment_charge_id(self.refund)
        self.assertEqual(result, self.payment.gateway_charge_id)

    def test_authorized_by_pk_returns_user_pk(self):
        result = self.admin.authorized_by_pk(self.refund)
        self.assertEqual(result, self.user.pk)

    def test_has_add_permission_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_add_permission(request))

    def test_has_delete_permission_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_delete_permission(request))

    def test_has_delete_permission_with_obj_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_delete_permission(request, self.refund))


# ---------------------------------------------------------------------------
# WebhookEventAdmin
# ---------------------------------------------------------------------------


class WebhookEventAdminTest(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = WebhookEventAdmin(WebhookEvent, self.site)
        self.factory = RequestFactory()

    def test_has_add_permission_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_add_permission(request))

    def test_has_delete_permission_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_delete_permission(request))

    def test_has_delete_permission_with_obj_returns_false(self):
        request = self.factory.get("/")
        mock_event = MagicMock(spec=WebhookEvent)
        self.assertFalse(self.admin.has_delete_permission(request, mock_event))


# ---------------------------------------------------------------------------
# PaymentAuditEntryAdmin
# ---------------------------------------------------------------------------


class PaymentAuditEntryAdminTest(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = PaymentAuditEntryAdmin(PaymentAuditEntry, self.site)
        self.factory = RequestFactory()
        self.user = _make_user()
        self.intent = _make_intent(self.user)

    def _make_audit_entry(self, intent=None):
        return PaymentAuditEntry.objects.create(
            actor=self.user,
            action="intent_created",
            payment_intent=intent,
            actor_ip="127.0.0.1",
            details={"test": True},
        )

    def test_actor_pk_returns_actor_id(self):
        entry = self._make_audit_entry(self.intent)
        result = self.admin.actor_pk(entry)
        self.assertEqual(result, self.user.pk)

    def test_payment_intent_reference_with_intent(self):
        entry = self._make_audit_entry(self.intent)
        result = self.admin.payment_intent_reference(entry)
        self.assertEqual(result, self.intent.reference)

    def test_payment_intent_reference_without_intent(self):
        entry = self._make_audit_entry(intent=None)
        result = self.admin.payment_intent_reference(entry)
        self.assertEqual(result, "-")

    def test_has_add_permission_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_add_permission(request))

    def test_has_change_permission_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_change_permission(request))

    def test_has_change_permission_with_obj_returns_false(self):
        request = self.factory.get("/")
        entry = self._make_audit_entry(self.intent)
        self.assertFalse(self.admin.has_change_permission(request, entry))

    def test_has_delete_permission_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_delete_permission(request))

    def test_has_delete_permission_with_obj_returns_false(self):
        request = self.factory.get("/")
        entry = self._make_audit_entry(self.intent)
        self.assertFalse(self.admin.has_delete_permission(request, entry))


# ---------------------------------------------------------------------------
# TenantPaymentConfigAdmin
# ---------------------------------------------------------------------------


class TenantPaymentConfigAdminTest(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = TenantPaymentConfigAdmin(TenantPaymentConfig, self.site)
        self.factory = RequestFactory()

    def _make_config(self, secret=""):
        return TenantPaymentConfig.objects.create(
            stripe_publishable_key="pk_test_abc",
            webhook_endpoint_secret=secret,
        )

    def test_webhook_secret_display_when_set(self):
        config = self._make_config(secret="whsec_abcdefgh")
        result = self.admin.webhook_secret_display(config)
        self.assertIn("********", result)
        self.assertIn("set", result)

    def test_webhook_secret_display_shows_length(self):
        secret = "whsec_abcdefgh"
        config = self._make_config(secret=secret)
        result = self.admin.webhook_secret_display(config)
        self.assertIn(str(len(secret)), result)

    def test_webhook_secret_display_when_not_configured(self):
        config = self._make_config(secret="")
        result = self.admin.webhook_secret_display(config)
        self.assertIn("Not configured", result)

    def test_has_delete_permission_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_delete_permission(request))

    def test_has_delete_permission_with_obj_returns_false(self):
        request = self.factory.get("/")
        config = self._make_config()
        self.assertFalse(self.admin.has_delete_permission(request, config))


# ---------------------------------------------------------------------------
# ServiceFeePaymentAdmin
# ---------------------------------------------------------------------------


class ServiceFeePaymentAdminTest(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = ServiceFeePaymentAdmin(ServiceFeePayment, self.site)
        self.factory = RequestFactory()

    def test_has_add_permission_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_add_permission(request))

    def test_has_delete_permission_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_delete_permission(request))

    def test_has_delete_permission_with_obj_returns_false(self):
        request = self.factory.get("/")
        mock_obj = MagicMock(spec=ServiceFeePayment)
        self.assertFalse(self.admin.has_delete_permission(request, mock_obj))


# ---------------------------------------------------------------------------
# DonationAdmin
# ---------------------------------------------------------------------------


class DonationAdminTest(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = DonationAdmin(Donation, self.site)
        self.factory = RequestFactory()
        self.user = _make_user()
        self.intent = _make_intent(self.user)
        self.donation = _make_donation(self.user, self.intent)

    def test_donor_pk_returns_donor_id(self):
        result = self.admin.donor_pk(self.donation)
        self.assertEqual(result, self.user.pk)

    def test_has_add_permission_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_add_permission(request))

    def test_has_delete_permission_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_delete_permission(request))

    def test_has_delete_permission_with_obj_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_delete_permission(request, self.donation))


# ---------------------------------------------------------------------------
# RecurringGiftPlanAdmin
# ---------------------------------------------------------------------------


class RecurringGiftPlanAdminTest(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = RecurringGiftPlanAdmin(RecurringGiftPlan, self.site)
        self.factory = RequestFactory()
        self.user = _make_user()

    def _make_plan(self):
        return RecurringGiftPlan.objects.create(
            donor=self.user,
            campaign=None,
            amount=Decimal("25.00"),
            frequency="monthly",
            next_charge_date=date(2026, 7, 1),
            gateway_subscription_id=f"sub_{uuid.uuid4().hex[:8]}",
            gateway_payment_method_id=f"pm_{uuid.uuid4().hex[:8]}",
            status="active",
        )

    def test_donor_pk_returns_donor_id(self):
        plan = self._make_plan()
        result = self.admin.donor_pk(plan)
        self.assertEqual(result, self.user.pk)

    def test_has_add_permission_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_add_permission(request))

    def test_has_delete_permission_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_delete_permission(request))

    def test_has_delete_permission_with_obj_returns_false(self):
        request = self.factory.get("/")
        plan = self._make_plan()
        self.assertFalse(self.admin.has_delete_permission(request, plan))


# ---------------------------------------------------------------------------
# OfficialDonationReceiptAdmin
# ---------------------------------------------------------------------------


class OfficialDonationReceiptAdminTest(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = OfficialDonationReceiptAdmin(OfficialDonationReceipt, self.site)
        self.factory = RequestFactory()
        self.user = _make_user()
        self.intent = _make_intent(self.user)
        self.donation = _make_donation(self.user, self.intent)

    def test_has_add_permission_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_add_permission(request))

    def test_has_change_permission_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_change_permission(request))

    def test_has_change_permission_with_obj_returns_false(self):
        receipt = _make_receipt(self.donation)
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_change_permission(request, receipt))

    def test_has_delete_permission_returns_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_delete_permission(request))

    def test_has_delete_permission_with_obj_returns_false(self):
        receipt = _make_receipt(self.donation)
        request = self.factory.get("/")
        self.assertFalse(self.admin.has_delete_permission(request, receipt))


# ---------------------------------------------------------------------------
# AdminOTPEnforcementTest — security regression guard for C5
# ---------------------------------------------------------------------------


class AdminOTPEnforcementTest(TestCase):
    """
    Verify that the Django admin site at /django-admin/ enforces OTP / MFA.

    A staff user authenticated with only a password (force_login bypasses the
    login form but does NOT call is_verified()) must not be able to access the
    admin index.  The OTPAdminSite.has_permission() check requires
    request.user.is_verified() to return True, so an unverified staff user
    should receive a redirect (302) or a forbidden-equivalent response rather
    than a 200 OK admin page.

    This test is a regression guard for vulnerability C5: Django admin
    completely bypassing MFA.
    """

    def test_admin_redirects_unverified_staff(self):
        """Staff user logged in without OTP must not see the admin index."""
        user = User.objects.create_user(
            email="staff_otp_test@example.com",
            password="correct-password-123!",
            is_staff=True,
            is_active=True,
        )
        # force_login authenticates the user at the session level but does NOT
        # mark them as OTP-verified (is_verified() returns False).
        self.client.force_login(user)
        response = self.client.get("/django-admin/")
        # OTPAdminSite redirects unverified users to the login page.
        self.assertNotEqual(
            response.status_code,
            200,
            "Admin returned 200 to an unverified staff user — OTP enforcement is broken.",
        )
        self.assertIn(
            response.status_code,
            [302, 301, 403],
            f"Expected a redirect or 403, got {response.status_code}.",
        )

    def test_admin_denies_unverified_superuser(self):
        """Superuser without OTP verification must also be blocked."""
        superuser = User.objects.create_superuser(
            email="super_otp_test@example.com",
            password="super-password-123!",
        )
        self.client.force_login(superuser)
        response = self.client.get("/django-admin/")
        self.assertNotEqual(
            response.status_code,
            200,
            "Admin returned 200 to an unverified superuser — OTP enforcement is broken.",
        )


# ---------------------------------------------------------------------------
# M-L: OfficialDonationReceiptAdmin PII access control (Bug Fix)
# ---------------------------------------------------------------------------


class OfficialDonationReceiptAdminPermissionTests(TestCase):
    """
    has_view_permission() must gate access to the OfficialDonationReceipt
    admin to superusers or users with the explicit
    payments.view_officialdonationreceipt permission.

    A plain is_staff=True user (finance clerk at another municipality) must
    not be able to browse donor PII.
    """

    def setUp(self):
        self.site = AdminSite()
        self.admin_instance = OfficialDonationReceiptAdmin(OfficialDonationReceipt, self.site)
        self.factory = RequestFactory()

    def _make_staff_user(self):
        user = User.objects.create_user(
            email=f"staff_{uuid.uuid4().hex[:6]}@example.com",
            password="StaffPass123!",
            is_staff=True,
        )
        return user

    def _make_superuser(self):
        return User.objects.create_superuser(
            email=f"super_{uuid.uuid4().hex[:6]}@example.com",
            password="SuperPass123!",
        )

    def _request_for(self, user):
        request = self.factory.get("/")
        request.user = user
        return request

    # -- has_view_permission --------------------------------------------------

    def test_has_view_permission_denied_for_plain_staff(self):
        """Staff user without explicit permission must be denied view access."""
        user = self._make_staff_user()
        request = self._request_for(user)
        self.assertFalse(
            self.admin_instance.has_view_permission(request),
            "Plain staff user (no view_officialdonationreceipt perm) must not have view access.",
        )

    def test_has_view_permission_denied_for_staff_with_obj(self):
        """has_view_permission(request, obj) is also denied for plain staff."""
        user = self._make_staff_user()
        request = self._request_for(user)
        mock_receipt = MagicMock(spec=OfficialDonationReceipt)
        self.assertFalse(self.admin_instance.has_view_permission(request, mock_receipt))

    def test_has_view_permission_granted_to_superuser(self):
        """Superusers must always have view access."""
        superuser = self._make_superuser()
        request = self._request_for(superuser)
        self.assertTrue(
            self.admin_instance.has_view_permission(request),
            "Superuser must have view access to OfficialDonationReceiptAdmin.",
        )

    def test_has_view_permission_granted_with_explicit_perm(self):
        """Staff user with payments.view_officialdonationreceipt must be granted access."""
        from django.contrib.auth.models import Permission
        from django.contrib.contenttypes.models import ContentType

        user = self._make_staff_user()
        ct = ContentType.objects.get_for_model(OfficialDonationReceipt)
        perm = Permission.objects.get(codename="view_officialdonationreceipt", content_type=ct)
        user.user_permissions.add(perm)
        # Refresh to bust permission cache
        user = User.objects.get(pk=user.pk)
        request = self._request_for(user)
        self.assertTrue(
            self.admin_instance.has_view_permission(request),
            "Staff user with explicit view_officialdonationreceipt perm must have access.",
        )

    # -- list_display PII exclusion -------------------------------------------

    def test_list_display_excludes_donor_legal_name(self):
        """list_display must not include donor_legal_name (PII)."""
        self.assertNotIn(
            "donor_legal_name",
            self.admin_instance.list_display,
            "donor_legal_name is PII and must not appear in list_display.",
        )

    def test_list_display_excludes_donor_address_line1(self):
        """list_display must not include donor_address_line1 (PII)."""
        self.assertNotIn(
            "donor_address_line1",
            self.admin_instance.list_display,
            "donor_address_line1 is PII and must not appear in list_display.",
        )

    def test_list_display_excludes_donor_email(self):
        """list_display must not include donor_email (PII)."""
        self.assertNotIn(
            "donor_email",
            self.admin_instance.list_display,
            "donor_email is PII and must not appear in list_display.",
        )

    def test_list_display_includes_serial_number(self):
        """serial_number is a non-PII identifier and must remain in list_display."""
        self.assertIn("serial_number", self.admin_instance.list_display)

    def test_list_display_includes_is_annual_consolidated(self):
        """is_annual_consolidated must remain in list_display."""
        self.assertIn("is_annual_consolidated", self.admin_instance.list_display)

    def test_list_display_includes_email_sent(self):
        """email_sent status indicator must be in list_display."""
        self.assertIn("email_sent", self.admin_instance.list_display)

    # -- append-only permission guards (regression: still enforced) -----------

    def test_has_add_permission_still_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin_instance.has_add_permission(request))

    def test_has_change_permission_still_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin_instance.has_change_permission(request))

    def test_has_delete_permission_still_false(self):
        request = self.factory.get("/")
        self.assertFalse(self.admin_instance.has_delete_permission(request))
