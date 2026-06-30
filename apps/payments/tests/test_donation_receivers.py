"""
Wave 4 — test_donation_receivers.py

Tests for apps/payments/receivers.py signal receivers.
"""
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.payments.models import (
    CharitySettings,
    Donation,
    DonationCampaign,
    OfficialDonationReceipt,
    Payment,
    PaymentIntent,
    DONATION_STATUS_COMPLETED,
)
from apps.payments.signals import donation_completed, receipt_issued

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def make_user(email=None, **kwargs):
    email = email or f"user_{uuid.uuid4().hex[:6]}@example.com"
    return User.objects.create_user(email=email, password="testpass123", **kwargs)


def make_campaign(**kwargs):
    defaults = {
        "slug": f"campaign-{uuid.uuid4().hex[:6]}",
        "name_en": "Test Campaign",
        "start_date": date(2024, 1, 1),
        "is_active": True,
        "sort_order": 0,
        "advantage_amount": Decimal("0.00"),
    }
    defaults.update(kwargs)
    return DonationCampaign.objects.create(**defaults)


def make_charity_settings(**kwargs):
    defaults = {
        "charity_legal_name": "Test Charity Inc.",
        "charity_registration_number": "123456789 RR 0001",
        "charity_address_line1": "100 Charity Ave",
        "charity_city": "Ottawa",
        "charity_province": "ON",
        "charity_postal_code": "K2A 1B2",
        "place_of_issue": "Ottawa",
        "authorized_signatory_name": "Jane Smith",
        "authorized_signatory_title": "Executive Director",
        "is_active": True,
    }
    defaults.update(kwargs)
    return CharitySettings.objects.create(**defaults)


def make_payment_intent(payer, **kwargs):
    defaults = {
        "payer": payer,
        "amount": Decimal("100.00"),
        "currency": "CAD",
        "purpose": PaymentIntent.PURPOSE_DONATION,
        "status": PaymentIntent.STATUS_COMPLETED,
        "gateway": PaymentIntent.GATEWAY_STRIPE,
        "gateway_intent_id": f"pi_{uuid.uuid4().hex[:8]}",
    }
    defaults.update(kwargs)
    return PaymentIntent.objects.create(**defaults)


def make_donation(donor, payment_intent, eligible_amount=Decimal("100.00"), **kwargs):
    amount = eligible_amount if "amount" not in kwargs else kwargs.pop("amount")
    defaults = {
        "payment_intent": payment_intent,
        "donor": donor,
        "amount": amount,
        "advantage_amount": Decimal("0.00"),
        "eligible_amount": eligible_amount,
        "is_recurring": False,
        "is_anonymous": False,
        "status": DONATION_STATUS_COMPLETED,
        "donor_name_snapshot": "Jane Citizen",
        "donor_address_snapshot": "123 Main St\nOttawa, ON  K1A 0A6",
    }
    defaults.update(kwargs)
    return Donation.objects.create(**defaults)


def make_payment(intent, **kwargs):
    from django.utils import timezone
    defaults = {
        "intent": intent,
        "gateway_charge_id": f"ch_{uuid.uuid4().hex[:8]}",
        "amount_paid": Decimal("100.00"),
        "processor_fee": Decimal("0.00"),
        "net_amount": Decimal("100.00"),
        "payment_method_type": Payment.PAYMENT_METHOD_CARD,
        "paid_at": timezone.now(),
    }
    defaults.update(kwargs)
    return Payment.objects.create(**defaults)


# ---------------------------------------------------------------------------
# on_donation_completed receiver tests
# ---------------------------------------------------------------------------

class OnDonationCompletedTests(TestCase):

    def setUp(self):
        self.user = make_user()
        self.campaign = make_campaign()
        self.charity = make_charity_settings()
        self.intent = make_payment_intent(self.user)
        self.donation = make_donation(self.user, self.intent)
        self.payment = make_payment(self.intent)

    def _fire_signal(self, donation=None, payment=None):
        from apps.payments.receivers import on_donation_completed
        from apps.payments.tasks_receipts import generate_and_send_receipt
        from apps.payments.models import OfficialDonationReceipt
        d = donation or self.donation
        p = payment or self.payment

        _counter = [0]

        def _fake_receipt_save(receipt_instance, *args, **kwargs):
            if not receipt_instance.serial_number:
                _counter[0] += 1
                receipt_instance.serial_number = f"2026-{str(_counter[0]).zfill(6)}"
            from django.db.models import Model
            Model.save(receipt_instance, *args, **kwargs)

        # generate_and_send_receipt is a Celery task — patch .delay directly
        # Also patch OfficialDonationReceipt.save to bypass PostgreSQL nextval()
        with patch.object(OfficialDonationReceipt, "save", _fake_receipt_save):
            with patch.object(generate_and_send_receipt, "delay", return_value=None) as mock_delay:
                on_donation_completed(sender=Donation, donation=d, payment=p)
        return mock_delay

    # 1. Creates OfficialDonationReceipt for eligible donation
    def test_creates_receipt_for_eligible_donation(self):
        self._fire_signal()
        self.assertEqual(OfficialDonationReceipt.objects.count(), 1)

    # 2. Does NOT create receipt when eligible_amount = 0
    def test_no_receipt_when_eligible_amount_zero(self):
        # Create a donation with eligible_amount = 0
        intent = make_payment_intent(self.user)
        donation = make_donation(
            self.user, intent,
            eligible_amount=Decimal("0.00"),
            amount=Decimal("100.00"),
            advantage_amount=Decimal("100.00"),
        )
        self._fire_signal(donation=donation)
        self.assertEqual(OfficialDonationReceipt.objects.count(), 0)

    # 3. Does NOT create receipt when CharitySettings not configured
    def test_no_receipt_without_charity_settings(self):
        CharitySettings.objects.all().delete()
        self._fire_signal()
        self.assertEqual(OfficialDonationReceipt.objects.count(), 0)

    # 4. Idempotent — second call for same donation returns early
    def test_idempotent_second_call_does_not_create_duplicate(self):
        self._fire_signal()
        self.assertEqual(OfficialDonationReceipt.objects.count(), 1)
        self._fire_signal()
        self.assertEqual(OfficialDonationReceipt.objects.count(), 1)

    # 5. Queues generate_and_send_receipt Celery task
    def test_queues_celery_task(self):
        from apps.payments.receivers import on_donation_completed
        from apps.payments.tasks_receipts import generate_and_send_receipt
        from apps.payments.models import OfficialDonationReceipt as ODR

        _counter = [0]

        def _fake_receipt_save(receipt_instance, *args, **kwargs):
            if not receipt_instance.serial_number:
                _counter[0] += 1
                receipt_instance.serial_number = f"2026-{str(_counter[0]).zfill(6)}"
            from django.db.models import Model
            Model.save(receipt_instance, *args, **kwargs)

        delay_calls = []

        def _fake_delay(pk):
            delay_calls.append(pk)

        # captureOnCommitCallbacks forces on_commit hooks to execute synchronously
        # in TestCase (which normally wraps tests in a never-committed transaction).
        with patch.object(ODR, "save", _fake_receipt_save):
            with patch.object(generate_and_send_receipt, "delay", side_effect=_fake_delay):
                with self.captureOnCommitCallbacks(execute=True):
                    on_donation_completed(
                        sender=Donation,
                        donation=self.donation,
                        payment=self.payment,
                    )
        # Task delay should be called once (via on_commit)
        self.assertEqual(len(delay_calls), 1)

    # 6. Does NOT raise on any exception — exceptions are swallowed
    def test_does_not_raise_on_exception(self):
        from apps.payments.receivers import on_donation_completed
        with patch("apps.payments.models.OfficialDonationReceipt.save",
                   side_effect=Exception("DB error")):
            # Should not raise — receiver swallows exceptions
            try:
                on_donation_completed(
                    sender=Donation,
                    donation=self.donation,
                    payment=self.payment,
                )
            except Exception:
                self.fail("on_donation_completed should not raise exceptions")

    # 7. Receipt created with status = RECEIPT_STATUS_ISSUED
    def test_receipt_status_is_issued(self):
        self._fire_signal()
        receipt = OfficialDonationReceipt.objects.get()
        self.assertEqual(receipt.status, OfficialDonationReceipt.RECEIPT_STATUS_ISSUED)

    # 8. Receipt donation FK points to the passed donation
    def test_receipt_donation_fk_matches_donation(self):
        self._fire_signal()
        receipt = OfficialDonationReceipt.objects.get()
        self.assertEqual(receipt.donation, self.donation)

    # 9. Receipt has correct charity data from CharitySettings
    def test_receipt_has_correct_charity_data(self):
        self._fire_signal()
        receipt = OfficialDonationReceipt.objects.get()
        self.assertEqual(receipt.charity_legal_name, self.charity.charity_legal_name)
        self.assertEqual(receipt.charity_registration_number,
                         self.charity.charity_registration_number)

    # 10. Receipt has correct donor snapshot data
    def test_receipt_has_correct_donor_snapshot(self):
        self._fire_signal()
        receipt = OfficialDonationReceipt.objects.get()
        self.assertEqual(receipt.donor_legal_name, self.donation.donor_name_snapshot)

    # 11. Receipt eligible_amount matches donation.eligible_amount
    def test_receipt_eligible_amount_matches_donation(self):
        self._fire_signal()
        receipt = OfficialDonationReceipt.objects.get()
        self.assertEqual(receipt.eligible_amount, self.donation.eligible_amount)


# ---------------------------------------------------------------------------
# on_receipt_issued receiver tests
# ---------------------------------------------------------------------------

class OnReceiptIssuedTests(TestCase):

    def setUp(self):
        self.user = make_user()
        self.intent = make_payment_intent(self.user)
        self.donation = make_donation(self.user, self.intent)

    def _make_receipt(self, **kwargs):
        serial = f"2024-{str(uuid.uuid4().int % 1000000).zfill(6)}"
        defaults = {
            "donation": self.donation,
            "status": OfficialDonationReceipt.RECEIPT_STATUS_ISSUED,
            "donor_legal_name": "Jane Citizen",
            "donor_address_line1": "123 Main St",
            "donor_city": "Ottawa",
            "donor_province": "ON",
            "donor_postal_code": "K1A 0A6",
            "donation_date": date(2024, 6, 1),
            "receipt_date": date(2024, 6, 15),
            "eligible_amount": Decimal("100.00"),
            "advantage_amount": Decimal("0.00"),
            "advantage_description": "",
            "charity_legal_name": "Test Charity Inc.",
            "charity_registration_number": "123456789 RR 0001",
            "charity_address": "100 Charity Ave, Ottawa, ON K2A 1B2",
            "place_of_issue": "Ottawa",
            "authorized_signatory_name": "Jane Smith",
            "authorized_signatory_title": "Executive Director",
            "is_annual_consolidated": False,
        }
        defaults.update(kwargs)
        receipt = OfficialDonationReceipt(**defaults)
        receipt.serial_number = serial
        receipt.save()
        return receipt

    # 12. on_receipt_issued logs serial number
    def test_on_receipt_issued_logs_serial_number(self):
        from apps.payments.receivers import on_receipt_issued
        receipt = self._make_receipt()
        with self.assertLogs("apps.payments.receivers", level="INFO") as log_ctx:
            on_receipt_issued(
                sender=OfficialDonationReceipt,
                receipt=receipt,
                donation=self.donation,
            )
        log_output = "\n".join(log_ctx.output)
        self.assertIn(receipt.serial_number, log_output)

    # 13. on_receipt_issued does NOT log donor name
    def test_on_receipt_issued_does_not_log_donor_name(self):
        from apps.payments.receivers import on_receipt_issued
        receipt = self._make_receipt()
        donor_name = receipt.donor_legal_name
        with self.assertLogs("apps.payments.receivers", level="INFO") as log_ctx:
            on_receipt_issued(
                sender=OfficialDonationReceipt,
                receipt=receipt,
                donation=self.donation,
            )
        log_output = "\n".join(log_ctx.output)
        self.assertNotIn(donor_name, log_output)

    # 14. dispatch_uid prevents duplicate registration
    def test_dispatch_uid_prevents_duplicate_registration(self):
        """Signal fires exactly once per emission even if ready() is called multiple times."""
        from apps.payments.apps import PaymentsConfig
        from apps.payments.tasks_receipts import generate_and_send_receipt

        # Re-calling ready() should not double-register (dispatch_uid prevents it)
        PaymentsConfig("payments", __import__("apps.payments")).ready()
        PaymentsConfig("payments", __import__("apps.payments")).ready()

        _counter = [0]

        def _fake_receipt_save(receipt_instance, *args, **kwargs):
            if not receipt_instance.serial_number:
                _counter[0] += 1
                receipt_instance.serial_number = f"2026-{str(_counter[0]).zfill(6)}"
            from django.db.models import Model
            Model.save(receipt_instance, *args, **kwargs)

        charity = make_charity_settings()
        with patch.object(OfficialDonationReceipt, "save", _fake_receipt_save):
            with patch.object(generate_and_send_receipt, "delay", return_value=None):
                donation_completed.send(
                    sender=Donation,
                    donation=self.donation,
                    payment=make_payment(self.intent),
                )

        # Should have created exactly one receipt, not two
        self.assertEqual(OfficialDonationReceipt.objects.count(), 1)

    # 15. Error logged with donation_pk only — not donor_name
    def test_error_logged_without_donor_name(self):
        from apps.payments.receivers import on_donation_completed
        charity = make_charity_settings()
        donor_name = self.donation.donor_name_snapshot

        with patch(
            "apps.payments.models.OfficialDonationReceipt.save",
            side_effect=Exception("DB error"),
        ):
            with self.assertLogs("apps.payments.receivers", level="ERROR") as log_ctx:
                on_donation_completed(
                    sender=Donation,
                    donation=self.donation,
                    payment=make_payment(self.intent),
                )

        log_output = "\n".join(log_ctx.output)
        self.assertNotIn(donor_name, log_output)
        # donation_pk should be in error log
        self.assertIn(str(self.donation.pk), log_output)


# ---------------------------------------------------------------------------
# H6 — Idempotency race-condition fix (get_or_create replaces TOCTOU pattern)
# ---------------------------------------------------------------------------

class IdempotencyRaceConditionTests(TestCase):
    """
    Duplicate signal delivery must not raise an unhandled exception.

    Before the fix, a second concurrent delivery could pass the .exists() check,
    then collide on INSERT and raise IntegrityError outside the atomic block.
    The fix uses get_or_create() so the second delivery gets created=False and
    returns cleanly, never reaching the INSERT path.
    """

    def setUp(self):
        self.user = make_user()
        self.campaign = make_campaign()
        self.charity = make_charity_settings()
        self.intent = make_payment_intent(self.user)
        self.donation = make_donation(self.user, self.intent)
        self.payment = make_payment(self.intent)

    def _fire(self, donation=None):
        """Fire on_donation_completed with receipt-serial patching."""
        from apps.payments.receivers import on_donation_completed
        from apps.payments.tasks_receipts import generate_and_send_receipt

        _counter = [0]

        def _fake_save(receipt_instance, *args, **kwargs):
            if not receipt_instance.serial_number:
                _counter[0] += 1
                receipt_instance.serial_number = f"2026-{str(_counter[0]).zfill(6)}"
            from django.db.models import Model
            Model.save(receipt_instance, *args, **kwargs)

        d = donation or self.donation
        with patch.object(OfficialDonationReceipt, "save", _fake_save):
            with patch.object(generate_and_send_receipt, "delay", return_value=None):
                on_donation_completed(sender=Donation, donation=d, payment=self.payment)

    def test_duplicate_signal_does_not_raise(self):
        """
        Simulates duplicate signal delivery: first call creates the receipt,
        second call must return silently without raising any exception.
        """
        self._fire()
        self.assertEqual(OfficialDonationReceipt.objects.count(), 1)

        # Second delivery — must not raise, must not create a duplicate
        try:
            self._fire()
        except Exception as exc:
            self.fail(
                f"Second signal delivery raised {type(exc).__name__}: {exc}. "
                "get_or_create must handle the duplicate case silently."
            )
        self.assertEqual(
            OfficialDonationReceipt.objects.count(),
            1,
            "A second signal delivery must not create a duplicate receipt.",
        )

    def test_simulated_concurrent_delivery_via_pre_existing_receipt(self):
        """
        Simulates the race where a receipt already exists at the point of the
        get_or_create call (as if a concurrent worker just committed it).
        The receiver must log receipt_already_issued and return without error.
        """
        from datetime import date
        from apps.payments.receivers import on_donation_completed
        from apps.payments.tasks_receipts import generate_and_send_receipt

        # Pre-create a receipt as if the concurrent worker already committed
        existing = OfficialDonationReceipt(
            donation=self.donation,
            status=OfficialDonationReceipt.RECEIPT_STATUS_ISSUED,
            donor_legal_name="Jane Citizen",
            donor_address_line1="123 Main St",
            donor_city="Ottawa",
            donor_province="ON",
            donor_postal_code="K1A 0A6",
            donation_date=date(2026, 1, 1),
            receipt_date=date(2026, 1, 1),
            eligible_amount=self.donation.eligible_amount,
            advantage_amount=self.donation.advantage_amount,
            advantage_description="",
            charity_legal_name=self.charity.charity_legal_name,
            charity_registration_number=self.charity.charity_registration_number,
            charity_address="100 Charity Ave, Ottawa, ON K2A 1B2",
            place_of_issue=self.charity.place_of_issue,
            authorized_signatory_name=self.charity.authorized_signatory_name,
            authorized_signatory_title=self.charity.authorized_signatory_title,
            is_annual_consolidated=False,
        )
        existing.serial_number = "2026-000001"
        existing.save()

        # Now fire the receiver — it must detect the existing receipt and return silently
        with patch.object(generate_and_send_receipt, "delay", return_value=None):
            with self.assertLogs("apps.payments.receivers", level="INFO") as log_ctx:
                on_donation_completed(
                    sender=Donation,
                    donation=self.donation,
                    payment=self.payment,
                )

        log_output = "\n".join(log_ctx.output)
        self.assertIn("receipt_already_issued", log_output)
        self.assertEqual(OfficialDonationReceipt.objects.count(), 1)
