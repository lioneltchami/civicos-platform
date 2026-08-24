"""
Wave 9 model tests — test_model_encryption.py

Tests for three model-layer changes:
- Item 13: OfficialDonationReceipt.email_sent (dedup guard)
- Item 14: PaymentIntent.reference (collision-safe 16-char hex)
- Item 17: TenantPaymentConfig.webhook_endpoint_secret (Fernet-encrypted at rest)
"""

import uuid
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase

from apps.payments.models import (
    DONATION_STATUS_COMPLETED,
    Donation,
    DonationCampaign,
    OfficialDonationReceipt,
    PaymentIntent,
    TenantPaymentConfig,
    _generate_payment_reference,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixture helpers (mirrors test_receipt_services.py conventions)
# ---------------------------------------------------------------------------


def _make_user(email=None, **kwargs):
    email = email or f"user_{uuid.uuid4().hex[:6]}@example.com"
    return User.objects.create_user(email=email, password="testpass123", **kwargs)


def _make_campaign(**kwargs):
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


def _make_payment_intent(payer, **kwargs):
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


def _make_donation(donor, payment_intent, **kwargs):
    defaults = {
        "payment_intent": payment_intent,
        "donor": donor,
        "amount": Decimal("100.00"),
        "advantage_amount": Decimal("0.00"),
        "eligible_amount": Decimal("100.00"),
        "is_recurring": False,
        "is_anonymous": False,
        "status": DONATION_STATUS_COMPLETED,
        "donor_name_snapshot": "Jane Citizen",
        "donor_address_snapshot": "123 Main St\nOttawa, ON  K1A 0A6",
    }
    defaults.update(kwargs)
    return Donation.objects.create(**defaults)


def _make_receipt(donation, **kwargs):
    """Create an OfficialDonationReceipt bypassing the DB serial sequence."""
    serial = f"2024-{str(uuid.uuid4().int % 1_000_000).zfill(6)}"
    defaults = {
        "donation": donation,
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


# ---------------------------------------------------------------------------
# Item 17 — Webhook secret encryption
# ---------------------------------------------------------------------------


class WebhookSecretEncryptionTest(TestCase):
    """TenantPaymentConfig.webhook_endpoint_secret must be Fernet-encrypted at rest."""

    def test_secret_not_stored_plaintext(self):
        """Raw DB value must NOT equal the plaintext webhook secret."""
        config = TenantPaymentConfig.get_solo()
        config.webhook_endpoint_secret = "whsec_test_secret_123"
        config.save()

        with connection.cursor() as cursor:
            # Use LIMIT 1 — SQLite stores UUID pk as text; avoid relying on exact cast
            cursor.execute(
                "SELECT webhook_endpoint_secret FROM payments_tenantpaymentconfig LIMIT 1"
            )
            row = cursor.fetchone()

        self.assertIsNotNone(row, "TenantPaymentConfig row not found in DB")
        raw = row[0]
        if isinstance(raw, memoryview):
            raw = bytes(raw)
        self.assertNotEqual(
            raw,
            b"whsec_test_secret_123",
            "Secret must not be stored as plaintext in the DB",
        )
        self.assertNotEqual(
            raw,
            "whsec_test_secret_123",
            "Secret (as str) must not equal plaintext",
        )

    def test_secret_decrypts_transparently(self):
        """ORM must decrypt the stored value back to the original plaintext."""
        config = TenantPaymentConfig.get_solo()
        config.webhook_endpoint_secret = "whsec_test_secret_123"
        config.save()

        # Re-fetch from DB to exercise from_db_value path
        fresh = TenantPaymentConfig.objects.get(pk=config.pk)
        self.assertEqual(
            fresh.webhook_endpoint_secret,
            "whsec_test_secret_123",
            "ORM must decrypt the stored ciphertext transparently",
        )

    def test_empty_secret_roundtrips(self):
        """An empty string should round-trip without errors."""
        config = TenantPaymentConfig.get_solo()
        config.webhook_endpoint_secret = ""
        config.save()
        fresh = TenantPaymentConfig.objects.get(pk=config.pk)
        self.assertEqual(fresh.webhook_endpoint_secret, "")

    def test_raw_db_value_looks_like_ciphertext(self):
        """Stored bytes should be substantially longer than the plaintext (ciphertext overhead)."""
        config = TenantPaymentConfig.get_solo()
        config.webhook_endpoint_secret = "whsec_any_secret"
        config.save()

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT webhook_endpoint_secret FROM payments_tenantpaymentconfig LIMIT 1"
            )
            row = cursor.fetchone()

        self.assertIsNotNone(row, "TenantPaymentConfig row not found in DB")
        raw = row[0]
        if isinstance(raw, memoryview):
            raw = bytes(raw)
        # Fernet ciphertext is always significantly longer than plaintext
        # (includes IV, HMAC, padding overhead).
        self.assertGreater(
            len(raw),
            len("whsec_any_secret"),
            "Stored ciphertext should be longer than plaintext",
        )


# ---------------------------------------------------------------------------
# Item 14 — Payment reference collision safety
# ---------------------------------------------------------------------------


class PaymentReferenceTest(TestCase):
    """PaymentIntent.reference must be a collision-safe 16-char hex string."""

    def test_reference_format(self):
        """Generated reference must be exactly 16 lowercase hex chars."""
        ref = _generate_payment_reference()
        self.assertEqual(len(ref), 16, f"Expected 16 chars, got {len(ref)}: {ref!r}")
        self.assertTrue(
            all(c in "0123456789abcdef" for c in ref),
            f"Reference contains non-hex characters: {ref!r}",
        )

    def test_reference_uniqueness_at_scale(self):
        """500 generated references must all be distinct (2^64 space makes collision negligible)."""
        refs = {_generate_payment_reference() for _ in range(500)}
        self.assertEqual(len(refs), 500, "Duplicate references generated — RNG broken?")

    def test_reference_assigned_on_create(self):
        """A newly created PaymentIntent must have a non-empty 16-char hex reference."""
        user = _make_user()
        intent = _make_payment_intent(user)
        self.assertTrue(intent.reference, "reference must be set after create()")
        self.assertEqual(len(intent.reference), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in intent.reference))

    def test_reference_is_unique_in_db(self):
        """Two PaymentIntents created back-to-back must have different references."""
        user = _make_user()
        intent1 = _make_payment_intent(user)
        intent2 = _make_payment_intent(user)
        self.assertNotEqual(intent1.reference, intent2.reference)


# ---------------------------------------------------------------------------
# Item 13 — email_sent dedup guard
# ---------------------------------------------------------------------------


class EmailSentFlagTest(TestCase):
    """OfficialDonationReceipt.email_sent must default to False."""

    def setUp(self):
        self.user = _make_user()
        self.campaign = _make_campaign()
        self.intent = _make_payment_intent(self.user)
        self.donation = _make_donation(self.user, self.intent, campaign=self.campaign)

    def test_email_sent_defaults_false(self):
        """A freshly created receipt must have email_sent=False."""
        receipt = _make_receipt(self.donation)
        self.assertFalse(
            receipt.email_sent,
            "email_sent must default to False on a new receipt",
        )

    def test_email_sent_persists_true(self):
        """email_sent can be set to True and saved without triggering the immutable-field guard."""
        receipt = _make_receipt(self.donation)
        # Use _base_manager to bypass the append-only QuerySet guard
        OfficialDonationReceipt._base_manager.filter(pk=receipt.pk).update(email_sent=True)
        fresh = OfficialDonationReceipt._default_manager.get(pk=receipt.pk)
        self.assertTrue(fresh.email_sent)

    def test_email_sent_is_boolean_field(self):
        """Confirm the field type is BooleanField (not NullBooleanField)."""
        field = OfficialDonationReceipt._meta.get_field("email_sent")
        self.assertIsInstance(
            field, __import__("django.db.models", fromlist=["BooleanField"]).BooleanField
        )
        self.assertFalse(field.null, "email_sent must not be nullable")


# ---------------------------------------------------------------------------
# H3 — _get_fernet() lru_cache behaviour
# ---------------------------------------------------------------------------


class FernetCacheTest(TestCase):
    """_get_fernet() must be cached at module level (lru_cache)."""

    def setUp(self):
        from apps.payments.models import _get_fernet

        # Always start each test with a clean cache so tests are independent.
        _get_fernet.cache_clear()

    def tearDown(self):
        from apps.payments.models import _get_fernet

        # Restore a clean cache so later tests don't see a stale MultiFernet.
        _get_fernet.cache_clear()

    def test_get_fernet_is_cached(self):
        """_get_fernet() must return the same MultiFernet object on repeated calls."""
        from apps.payments.models import _get_fernet

        f1 = _get_fernet()
        f2 = _get_fernet()
        self.assertIs(f1, f2, "_get_fernet() must return the cached instance, not reconstruct")

    def test_cache_clear_rebuilds_fernet(self):
        """cache_clear() forces reconstruction on the next call."""
        from apps.payments.models import _get_fernet

        f1 = _get_fernet()
        _get_fernet.cache_clear()
        f2 = _get_fernet()
        self.assertIsNot(f1, f2, "After cache_clear(), _get_fernet() must return a new object")
