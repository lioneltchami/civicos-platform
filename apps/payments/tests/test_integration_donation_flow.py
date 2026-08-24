"""
test_integration_donation_flow.py

End-to-end integration test for the one-time donation flow:

  PaymentIntent (PURPOSE_DONATION)
    → process_stripe_webhook (payment_intent.succeeded)
      → _handle_payment_intent_succeeded
        → _handle_one_time_donation
          → Donation row created (COMPLETED)
            → on_commit fires donation_completed signal
              → on_donation_completed receiver
                → OfficialDonationReceipt created
                  → on_commit fires generate_and_send_receipt.delay(receipt_pk)

Test strategy:
- Patch gateway.get_gateway to avoid Stripe HTTP calls
- Patch django.db.transaction.on_commit with side_effect=lambda fn: fn()
  so on_commit callbacks fire synchronously inside the test
- Patch OfficialDonationReceipt.save with _fake_save to bypass PostgreSQL
  serial-number sequence (payments_receipt_serial_seq)
- Patch generate_and_send_receipt.delay to prevent actual Celery dispatch
- CharitySettings must exist for the receipt receiver to fire
"""

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.payments.models import (
    DONATION_STATUS_COMPLETED,
    GATEWAY_STRIPE,
    CharitySettings,
    Donation,
    OfficialDonationReceipt,
    Payment,
    PaymentIntent,
)
from apps.payments.tasks import process_stripe_webhook
from apps.payments.tests.factories import make_fake_save

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_user(email=None):
    email = email or f"user_{uuid.uuid4().hex[:6]}@example.com"
    return User.objects.create_user(email=email, password="TestPass123!")


def _make_charity_settings(**kwargs):
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


def _make_donation_payment_intent(user, **kwargs):
    """Create a PaymentIntent with purpose=PURPOSE_DONATION."""
    defaults = {
        "payer": user,
        "amount": Decimal("100.00"),
        "currency": "CAD",
        "purpose": PaymentIntent.PURPOSE_DONATION,
        "status": PaymentIntent.STATUS_PENDING,
        "gateway": GATEWAY_STRIPE,
        "gateway_intent_id": f"pi_test_{uuid.uuid4().hex[:8]}",
        "metadata": {
            "is_recurring": "0",
            "source": "donation",
            "campaign_pk": "",
        },
    }
    defaults.update(kwargs)
    return PaymentIntent.objects.create(**defaults)


def _make_webhook_event(gateway_intent_id, event_type="payment_intent.succeeded"):
    from apps.payments.models import WebhookEvent

    gateway_event_id = f"evt_{uuid.uuid4().hex[:8]}"
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


def _succeeded_event_data(gateway_intent_id, gateway_charge_id="ch_test_integration"):
    return (
        "payment_intent.succeeded",
        {
            "gateway_intent_id": gateway_intent_id,
            "gateway_charge_id": gateway_charge_id,
            "amount_paid": Decimal("100.00"),
            "processor_fee": Decimal("2.90"),
            "net_amount": Decimal("97.10"),
            "payment_method_type": "card",
            "card_last_four": "4242",
            "card_brand": "visa",
            "paid_at": "1700000000",
        },
    )


_serial_counter = [0]
_fake_save = make_fake_save(_serial_counter, year=2026)


# ---------------------------------------------------------------------------
# Full flow integration test
# ---------------------------------------------------------------------------


class DonationFlowIntegrationTests(TestCase):
    """
    Exercises the complete donation chain from webhook event to
    receipt creation and Celery task dispatch.
    """

    def setUp(self):
        _serial_counter[0] = 0
        self.user = _make_user()
        self.charity = _make_charity_settings()
        self.pi = _make_donation_payment_intent(self.user)
        self.event = _make_webhook_event(self.pi.gateway_intent_id)
        self.mock_parse_return = _succeeded_event_data(self.pi.gateway_intent_id)

    def _run_webhook(self, extra_patches=None):
        """
        Execute process_stripe_webhook with all required patches active.
        Returns the mock for generate_and_send_receipt.delay.
        """
        from apps.payments.tasks_receipts import generate_and_send_receipt

        with (
            patch("apps.payments.gateway.get_gateway") as mock_get_gw,
            patch.object(OfficialDonationReceipt, "save", _fake_save),
            patch.object(generate_and_send_receipt, "delay", return_value=None) as mock_delay,
        ):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = self.mock_parse_return
            mock_get_gw.return_value = mock_gw
            with self.captureOnCommitCallbacks(execute=True):
                process_stripe_webhook(str(self.event.pk))
        return mock_delay

    # ------------------------------------------------------------------
    # Payment row assertions
    # ------------------------------------------------------------------

    def test_payment_row_created(self):
        """Webhook handler must create exactly one Payment record."""
        self._run_webhook()
        self.assertEqual(Payment.objects.count(), 1)

    def test_payment_linked_to_intent(self):
        self._run_webhook()
        payment = Payment.objects.get()
        self.assertEqual(payment.intent, self.pi)

    def test_payment_amount_paid(self):
        self._run_webhook()
        payment = Payment.objects.get()
        self.assertEqual(payment.amount_paid, Decimal("100.00"))

    # ------------------------------------------------------------------
    # Donation row assertions
    # ------------------------------------------------------------------

    def test_donation_row_created(self):
        """_handle_one_time_donation must create a Donation record."""
        self._run_webhook()
        self.assertEqual(Donation.objects.count(), 1)

    def test_donation_linked_to_intent(self):
        self._run_webhook()
        donation = Donation.objects.get()
        self.assertEqual(donation.payment_intent, self.pi)

    def test_donation_status_is_completed(self):
        self._run_webhook()
        donation = Donation.objects.get()
        self.assertEqual(donation.status, DONATION_STATUS_COMPLETED)

    def test_donation_not_recurring(self):
        self._run_webhook()
        donation = Donation.objects.get()
        self.assertFalse(donation.is_recurring)

    def test_donation_eligible_amount_set(self):
        self._run_webhook()
        donation = Donation.objects.get()
        self.assertGreater(donation.eligible_amount, Decimal("0.00"))

    # ------------------------------------------------------------------
    # OfficialDonationReceipt assertions
    # ------------------------------------------------------------------

    def test_receipt_created(self):
        """donation_completed signal → on_donation_completed → OfficialDonationReceipt."""
        self._run_webhook()
        self.assertEqual(OfficialDonationReceipt.objects.count(), 1)

    def test_receipt_linked_to_donation(self):
        self._run_webhook()
        receipt = OfficialDonationReceipt.objects.get()
        donation = Donation.objects.get()
        self.assertEqual(receipt.donation, donation)

    def test_receipt_status_is_issued(self):
        self._run_webhook()
        receipt = OfficialDonationReceipt.objects.get()
        self.assertEqual(receipt.status, OfficialDonationReceipt.RECEIPT_STATUS_ISSUED)

    def test_receipt_has_serial_number(self):
        self._run_webhook()
        receipt = OfficialDonationReceipt.objects.get()
        self.assertTrue(receipt.serial_number)

    def test_receipt_eligible_amount_matches_donation(self):
        self._run_webhook()
        receipt = OfficialDonationReceipt.objects.get()
        donation = Donation.objects.get()
        self.assertEqual(receipt.eligible_amount, donation.eligible_amount)

    def test_receipt_charity_fields_populated(self):
        self._run_webhook()
        receipt = OfficialDonationReceipt.objects.get()
        self.assertEqual(receipt.charity_legal_name, self.charity.charity_legal_name)
        self.assertEqual(
            receipt.charity_registration_number,
            self.charity.charity_registration_number,
        )

    def test_receipt_not_annual_consolidated(self):
        """One-time donation receipts are not annual consolidated."""
        self._run_webhook()
        receipt = OfficialDonationReceipt.objects.get()
        self.assertFalse(receipt.is_annual_consolidated)

    def test_receipt_has_pdf_false_initially(self):
        """PDF is generated asynchronously — has_pdf is False right after creation."""
        self._run_webhook()
        receipt = OfficialDonationReceipt.objects.get()
        self.assertFalse(receipt.has_pdf)

    # ------------------------------------------------------------------
    # Celery task dispatch assertions
    # ------------------------------------------------------------------

    def test_generate_and_send_receipt_delay_called(self):
        """generate_and_send_receipt.delay must be called once with the receipt PK."""
        mock_delay = self._run_webhook()
        mock_delay.assert_called_once()

    def test_generate_and_send_receipt_called_with_receipt_pk(self):
        """The argument to .delay() must be the string PK of the created receipt."""
        mock_delay = self._run_webhook()
        receipt = OfficialDonationReceipt.objects.get()
        called_with_pk = mock_delay.call_args[0][0]
        self.assertEqual(called_with_pk, str(receipt.pk))

    # ------------------------------------------------------------------
    # WebhookEvent state assertions
    # ------------------------------------------------------------------

    def test_webhook_event_marked_processed(self):
        self._run_webhook()
        self.event.refresh_from_db()
        self.assertTrue(self.event.processed)

    def test_webhook_event_processed_at_set(self):
        self._run_webhook()
        self.event.refresh_from_db()
        self.assertIsNotNone(self.event.processed_at)

    # ------------------------------------------------------------------
    # No-receipt scenarios
    # ------------------------------------------------------------------

    def test_no_receipt_when_no_charity_settings(self):
        """Without CharitySettings the receiver skips receipt creation."""
        CharitySettings.objects.all().delete()
        self._run_webhook()
        self.assertEqual(OfficialDonationReceipt.objects.count(), 0)

    def test_no_donation_for_service_fee_intent(self):
        """
        PURPOSE_SERVICE_FEE intents must NOT trigger _handle_one_time_donation.
        No Donation row should be created.
        """
        from apps.payments.tasks_receipts import generate_and_send_receipt

        fee_pi = PaymentIntent.objects.create(
            payer=self.user,
            amount=Decimal("50.00"),
            currency="CAD",
            purpose=PaymentIntent.PURPOSE_SERVICE_FEE,
            status=PaymentIntent.STATUS_PENDING,
            gateway=GATEWAY_STRIPE,
            gateway_intent_id=f"pi_fee_{uuid.uuid4().hex[:8]}",
        )
        fee_event = _make_webhook_event(fee_pi.gateway_intent_id)
        mock_parse_return = _succeeded_event_data(fee_pi.gateway_intent_id)

        with (
            patch("apps.payments.gateway.get_gateway") as mock_get_gw,
            patch.object(OfficialDonationReceipt, "save", _fake_save),
            patch.object(generate_and_send_receipt, "delay", return_value=None),
        ):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = mock_parse_return
            mock_get_gw.return_value = mock_gw
            with self.captureOnCommitCallbacks(execute=True):
                process_stripe_webhook(str(fee_event.pk))

        self.assertEqual(Donation.objects.count(), 0)
        self.assertEqual(OfficialDonationReceipt.objects.count(), 0)

    def test_idempotent_second_webhook_does_not_duplicate(self):
        """
        Running the same webhook event twice must not create a second
        Donation or OfficialDonationReceipt.
        """
        from apps.payments.tasks_receipts import generate_and_send_receipt

        with (
            patch("apps.payments.gateway.get_gateway") as mock_get_gw,
            patch.object(OfficialDonationReceipt, "save", _fake_save),
            patch.object(generate_and_send_receipt, "delay", return_value=None),
        ):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = self.mock_parse_return
            mock_get_gw.return_value = mock_gw
            with self.captureOnCommitCallbacks(execute=True):
                # First run
                process_stripe_webhook(str(self.event.pk))
            with self.captureOnCommitCallbacks(execute=True):
                # Second run — event is now marked processed, should exit early
                process_stripe_webhook(str(self.event.pk))

        self.assertEqual(Donation.objects.count(), 1)
        self.assertEqual(OfficialDonationReceipt.objects.count(), 1)

    def test_no_receipt_when_eligible_amount_is_zero(self):
        """
        End-to-end: when advantage_amount == amount (eligible_amount = 0),
        no OfficialDonationReceipt should be created even after the webhook fires.

        CRA: receipts are only issued when eligible_amount > 0.
        """
        from apps.payments.tasks_receipts import generate_and_send_receipt

        # Override metadata so eligible_amount is 0 (full advantage = full donation)
        self.pi.metadata = {
            "is_recurring": "0",
            "source": "donation",
            "campaign_pk": "",
            "advantage_amount": "100.00",
            "eligible_amount": "0.00",
            "is_anonymous": "0",
            "donor_legal_name": "Jean Tremblay",
        }
        self.pi.save(update_fields=["metadata"])

        with (
            patch("apps.payments.gateway.get_gateway") as mock_get_gw,
            patch.object(OfficialDonationReceipt, "save", _fake_save),
            patch.object(generate_and_send_receipt, "delay", return_value=None) as mock_delay,
        ):
            mock_gw = MagicMock()
            mock_gw.parse_webhook_event.return_value = self.mock_parse_return
            mock_get_gw.return_value = mock_gw
            with self.captureOnCommitCallbacks(execute=True):
                process_stripe_webhook(str(self.event.pk))

        # Donation IS created even when eligible_amount = 0
        self.assertEqual(Donation.objects.count(), 1)
        # But no receipt — CRA rule: eligible_amount must be > 0
        self.assertEqual(
            OfficialDonationReceipt.objects.count(),
            0,
            "No receipt should be issued when eligible_amount is 0 (full advantage)",
        )
        # Celery task must NOT have been dispatched
        mock_delay.assert_not_called()
