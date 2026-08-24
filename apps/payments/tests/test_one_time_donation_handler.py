"""
Tests for Fix 3: _handle_one_time_donation reads CRA fields from intent.metadata.

Covers:
- advantage_amount from metadata (not campaign default)
- eligible_amount from metadata
- is_anonymous from metadata
- donor_name_snapshot from metadata donor_legal_name (not user.get_full_name())
- Fallbacks for legacy intents lacking metadata fields
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.payments.models import (
    Donation,
    DonationCampaign,
    Payment,
    PaymentIntent,
)
from apps.payments.tasks import _handle_one_time_donation

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def make_user(**kwargs):
    email = kwargs.pop("email", f"donor_{uuid.uuid4().hex[:6]}@example.com")
    return User.objects.create_user(email=email, password="testpass123", **kwargs)


def make_campaign(**kwargs):
    defaults = {
        "slug": f"camp-{uuid.uuid4().hex[:6]}",
        "name_en": "Test Campaign",
        "start_date": date(2024, 1, 1),
        "is_active": True,
        "sort_order": 0,
        "advantage_amount": Decimal("5.00"),  # campaign default — should be OVERRIDDEN by metadata
    }
    defaults.update(kwargs)
    return DonationCampaign.objects.create(**defaults)


def make_intent(payer, metadata=None, **kwargs):
    defaults = {
        "payer": payer,
        "amount": Decimal("100.00"),
        "tax_amount": Decimal("0.00"),
        "currency": "CAD",
        "purpose": PaymentIntent.PURPOSE_DONATION,
        "status": PaymentIntent.STATUS_COMPLETED,
        "gateway": PaymentIntent.GATEWAY_STRIPE,
        "gateway_intent_id": f"pi_test_{uuid.uuid4().hex[:8]}",
        "metadata": metadata or {},
    }
    defaults.update(kwargs)
    return PaymentIntent.objects.create(**defaults)


def make_payment(intent, amount_paid=Decimal("100.00")):
    return Payment.objects.create(
        intent=intent,
        gateway_charge_id=f"ch_{uuid.uuid4().hex[:8]}",
        amount_paid=amount_paid,
        processor_fee=Decimal("0.00"),
        payment_method_type="card",
        paid_at=datetime(2024, 6, 1, tzinfo=UTC),
    )


def make_webhook_event():
    mock_event = MagicMock()
    mock_event.gateway_event_id = f"evt_{uuid.uuid4().hex[:8]}"
    return mock_event


# ---------------------------------------------------------------------------
# Fix 3 tests
# ---------------------------------------------------------------------------


class HandleOneTimeDonationMetadataTests(TestCase):
    """
    _handle_one_time_donation must read advantage_amount, eligible_amount,
    is_anonymous, and donor_legal_name from intent.metadata.
    """

    def setUp(self):
        self.donor = make_user()
        self.campaign = make_campaign()
        self.webhook_event = make_webhook_event()

    def _run_handler(self, metadata, amount_paid=Decimal("100.00")):
        intent = make_intent(self.donor, metadata=metadata, amount=amount_paid)
        payment = make_payment(intent, amount_paid=amount_paid)
        _handle_one_time_donation(intent, payment, self.webhook_event)
        return Donation.objects.get(payment_intent=intent)

    # Fix 3.1 — advantage_amount from metadata overrides campaign default
    def test_advantage_amount_from_metadata(self):
        """$25 metadata advantage_amount must win over $5 campaign default."""
        meta = {
            "campaign_pk": str(self.campaign.pk),
            "is_recurring": "0",
            "advantage_amount": "25.00",
            "eligible_amount": "75.00",
            "is_anonymous": "0",
            "donor_legal_name": "Jean Tremblay",
        }
        donation = self._run_handler(meta)
        self.assertEqual(
            donation.advantage_amount,
            Decimal("25.00"),
            "advantage_amount must come from metadata, not campaign default ($5)",
        )

    # Fix 3.2 — eligible_amount from metadata
    def test_eligible_amount_from_metadata(self):
        meta = {
            "campaign_pk": str(self.campaign.pk),
            "is_recurring": "0",
            "advantage_amount": "25.00",
            "eligible_amount": "75.00",
            "is_anonymous": "0",
            "donor_legal_name": "Jean Tremblay",
        }
        donation = self._run_handler(meta)
        self.assertEqual(
            donation.eligible_amount, Decimal("75.00"), "eligible_amount must come from metadata"
        )

    # Fix 3.3 — is_anonymous=True from metadata
    def test_is_anonymous_true_from_metadata(self):
        meta = {
            "campaign_pk": str(self.campaign.pk),
            "is_recurring": "0",
            "advantage_amount": "0.00",
            "eligible_amount": "100.00",
            "is_anonymous": "1",
            "donor_legal_name": "Anonymous Donor",
        }
        donation = self._run_handler(meta)
        self.assertTrue(
            donation.is_anonymous, "is_anonymous must be True when metadata is_anonymous='1'"
        )

    # Fix 3.4 — is_anonymous=False from metadata
    def test_is_anonymous_false_from_metadata(self):
        meta = {
            "campaign_pk": str(self.campaign.pk),
            "is_recurring": "0",
            "advantage_amount": "0.00",
            "eligible_amount": "100.00",
            "is_anonymous": "0",
            "donor_legal_name": "Jean Tremblay",
        }
        donation = self._run_handler(meta)
        self.assertFalse(
            donation.is_anonymous, "is_anonymous must be False when metadata is_anonymous='0'"
        )

    # Fix 3.5 — donor_name_snapshot from metadata donor_legal_name (not account name)
    def test_donor_name_snapshot_from_metadata(self):
        """donor_name_snapshot must use the form-submitted legal name, not the
        Django account name (user.get_full_name()), so CRA receipts show the
        name the donor entered on the donation form."""
        meta = {
            "campaign_pk": str(self.campaign.pk),
            "is_recurring": "0",
            "advantage_amount": "0.00",
            "eligible_amount": "100.00",
            "is_anonymous": "0",
            "donor_legal_name": "Jean Tremblay",
        }
        donation = self._run_handler(meta)
        self.assertEqual(
            donation.donor_name_snapshot,
            "Jean Tremblay",
            "donor_name_snapshot must be the legal name from metadata",
        )

    # Fix 3.6 — legacy fallback: empty metadata → uses campaign default + get_full_name()
    def test_legacy_fallback_advantage_from_campaign(self):
        """Intents created before Fix 2 lack metadata fields.
        advantage_amount should fall back to campaign.advantage_amount ($5)."""
        meta = {
            "campaign_pk": str(self.campaign.pk),
            "is_recurring": "0",
            # No advantage_amount, eligible_amount, is_anonymous, donor_legal_name
        }
        self.donor.first_name = "Legacy"
        self.donor.last_name = "Donor"
        self.donor.save()
        donation = self._run_handler(meta)
        # Campaign default is $5.00
        self.assertEqual(
            donation.advantage_amount,
            Decimal("5.00"),
            "Legacy intent: advantage_amount should fall back to campaign default",
        )

    # Fix 3.7 — legacy fallback: is_anonymous defaults to False
    def test_legacy_fallback_is_anonymous_defaults_false(self):
        meta = {
            "campaign_pk": str(self.campaign.pk),
            "is_recurring": "0",
        }
        donation = self._run_handler(meta)
        self.assertFalse(
            donation.is_anonymous,
            "Legacy intent without is_anonymous in metadata: defaults to False",
        )

    # Fix 3.8 — recurring intents (is_recurring="1") are skipped
    def test_recurring_intent_skipped(self):
        meta = {
            "campaign_pk": str(self.campaign.pk),
            "is_recurring": "1",
        }
        intent = make_intent(self.donor, metadata=meta)
        payment = make_payment(intent)
        _handle_one_time_donation(intent, payment, self.webhook_event)
        # No Donation row should be created for recurring intents
        self.assertFalse(
            Donation.objects.filter(payment_intent=intent).exists(),
            "Recurring intents should be skipped by _handle_one_time_donation",
        )

    # Fix 3.9 — idempotency: calling twice does not create a second Donation
    def test_idempotent_double_call(self):
        meta = {
            "campaign_pk": str(self.campaign.pk),
            "is_recurring": "0",
            "advantage_amount": "0.00",
            "eligible_amount": "100.00",
            "is_anonymous": "0",
            "donor_legal_name": "Jean Tremblay",
        }
        intent = make_intent(self.donor, metadata=meta)
        payment = make_payment(intent)
        _handle_one_time_donation(intent, payment, self.webhook_event)
        _handle_one_time_donation(intent, payment, self.webhook_event)
        count = Donation.objects.filter(payment_intent=intent).count()
        self.assertEqual(count, 1, "Calling handler twice must not create duplicate Donation rows")

    # Fix 3.10 — invalid advantage_amount in metadata → falls back to zero, not crash
    def test_invalid_advantage_amount_in_metadata_falls_back(self):
        meta = {
            "campaign_pk": str(self.campaign.pk),
            "is_recurring": "0",
            "advantage_amount": "not-a-number",
            "eligible_amount": "100.00",
            "is_anonymous": "0",
            "donor_legal_name": "Jean Tremblay",
        }
        donation = self._run_handler(meta)
        self.assertEqual(
            donation.advantage_amount,
            Decimal("0.00"),
            "Invalid advantage_amount in metadata must fall back to 0.00, not raise",
        )
