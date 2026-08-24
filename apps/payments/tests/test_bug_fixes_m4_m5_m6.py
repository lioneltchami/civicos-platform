"""
Bug-fix regression tests — M4, M5, M6.

M4 — donation.py proxy IP trust
    Verifies that _get_client_ip() is used for rate-limiting and audit
    logging instead of reading REMOTE_ADDR directly, so a load-balancer IP
    does not collapse all donors into one rate-limit bucket.

M5 — receipt_pdf.py corrupt file reuse
    Verifies that save_receipt_pdf() deletes and regenerates a corrupt
    (non-PDF-header) file rather than silently returning the broken path.

M6 — models.py advantage_amount <= eligible_amount DB constraint
    Verifies that the new CheckConstraint prevents rows where
    advantage_amount > eligible_amount from being committed to the DB.
"""

import hashlib
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.db import IntegrityError
from django.test import RequestFactory, TestCase

from apps.payments.models import (
    DONATION_STATUS_COMPLETED,
    Donation,
    DonationCampaign,
    OfficialDonationReceipt,
    PaymentIntent,
)
from apps.payments.services.receipt_pdf import save_receipt_pdf
from apps.payments.views.donation import _check_donation_rate_limit, _get_client_ip

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixture helpers (minimal — no factory_boy dependency)
# ---------------------------------------------------------------------------


def _make_user(email=None):
    email = email or f"user_{uuid.uuid4().hex[:6]}@example.com"
    return User.objects.create_user(email=email, password="testpass123")


def _make_campaign():
    return DonationCampaign.objects.create(
        slug=f"camp-{uuid.uuid4().hex[:6]}",
        name_en="Test Campaign",
        start_date=date(2024, 1, 1),
        is_active=True,
        sort_order=0,
        advantage_amount=Decimal("0.00"),
    )


def _make_intent(payer):
    return PaymentIntent.objects.create(
        payer=payer,
        amount=Decimal("100.00"),
        currency="CAD",
        purpose=PaymentIntent.PURPOSE_DONATION,
        status=PaymentIntent.STATUS_COMPLETED,
        gateway=PaymentIntent.GATEWAY_STRIPE,
        gateway_intent_id=f"pi_{uuid.uuid4().hex[:8]}",
    )


def _make_donation(
    donor, intent, *, eligible_amount=Decimal("100.00"), advantage_amount=Decimal("0.00")
):
    return Donation.objects.create(
        payment_intent=intent,
        donor=donor,
        amount=Decimal("100.00"),
        advantage_amount=advantage_amount,
        eligible_amount=eligible_amount,
        is_recurring=False,
        is_anonymous=False,
        status=DONATION_STATUS_COMPLETED,
        donor_name_snapshot="Test Donor",
        donor_address_snapshot="123 Main St\nOttawa, ON  K1A 0A6",
    )


def _make_receipt(donation, *, eligible_amount=Decimal("100.00"), advantage_amount=Decimal("0.00")):
    serial = f"2024-{str(uuid.uuid4().int % 1_000_000).zfill(6)}"
    receipt = OfficialDonationReceipt(
        donation=donation,
        status=OfficialDonationReceipt.RECEIPT_STATUS_ISSUED,
        serial_number=serial,
        donor_legal_name="Test Donor",
        donor_address_line1="123 Main St",
        donor_city="Ottawa",
        donor_province="ON",
        donor_postal_code="K1A 0A6",
        donation_date=date(2024, 6, 1),
        receipt_date=date(2024, 6, 15),
        eligible_amount=eligible_amount,
        advantage_amount=advantage_amount,
        advantage_description="",
        charity_legal_name="Test Charity Inc.",
        charity_registration_number="123456789 RR 0001",
        charity_address="100 Charity Ave, Ottawa, ON K2A 1B2",
        place_of_issue="Ottawa",
        authorized_signatory_name="Jane Smith",
        authorized_signatory_title="Executive Director",
        is_annual_consolidated=False,
    )
    receipt.save()
    return receipt


# ---------------------------------------------------------------------------
# M4 — proxy IP trust in _get_client_ip and _check_donation_rate_limit
# ---------------------------------------------------------------------------


class GetClientIpTest(TestCase):
    """_get_client_ip() must prefer ipware over raw REMOTE_ADDR."""

    def _make_request(self, remote_addr="10.0.0.1"):
        factory = RequestFactory()
        req = factory.post("/fake/")
        req.META["REMOTE_ADDR"] = remote_addr
        req.user = AnonymousUser()
        return req

    def test_returns_ipware_ip_when_available(self):
        """When ipware returns a valid IP it must be preferred over REMOTE_ADDR."""
        req = self._make_request(remote_addr="10.0.0.1")
        req.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.42"

        with patch(
            "apps.payments.views.donation._ipware_get_client_ip",
            return_value=("203.0.113.42", True),
        ) as mock_ipware:
            ip = _get_client_ip(req)

        mock_ipware.assert_called_once_with(req)
        self.assertEqual(ip, "203.0.113.42")

    def test_falls_back_to_remote_addr_when_ipware_returns_none(self):
        """If ipware returns (None, False), fall back to REMOTE_ADDR."""
        req = self._make_request(remote_addr="10.0.0.1")

        with patch(
            "apps.payments.views.donation._ipware_get_client_ip",
            return_value=(None, False),
        ):
            ip = _get_client_ip(req)

        self.assertEqual(ip, "10.0.0.1")

    def test_falls_back_to_remote_addr_when_ipware_is_none(self):
        """If ipware is not installed (_ipware_get_client_ip=None), use REMOTE_ADDR."""
        req = self._make_request(remote_addr="192.168.1.50")

        with patch("apps.payments.views.donation._ipware_get_client_ip", None):
            ip = _get_client_ip(req)

        self.assertEqual(ip, "192.168.1.50")

    def test_falls_back_to_remote_addr_on_ipware_exception(self):
        """If ipware raises unexpectedly, fall back gracefully to REMOTE_ADDR."""
        req = self._make_request(remote_addr="10.10.10.10")

        with patch(
            "apps.payments.views.donation._ipware_get_client_ip",
            side_effect=RuntimeError("ipware internal error"),
        ):
            ip = _get_client_ip(req)

        self.assertEqual(ip, "10.10.10.10")


class RateLimitUsesRealClientIpTest(TestCase):
    """_check_donation_rate_limit must key on the real client IP, not REMOTE_ADDR."""

    def setUp(self):
        cache.clear()

    def test_rate_limit_uses_real_client_ip_not_proxy(self):
        """REMOTE_ADDR is proxy IP; real IP must come from ipware (X-Forwarded-For)."""
        factory = RequestFactory()
        req = factory.post("/fake/")
        req.META["REMOTE_ADDR"] = "10.0.0.1"  # load-balancer IP
        req.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.42"  # real client IP
        req.user = AnonymousUser()

        real_ip = "203.0.113.42"
        expected_hash = hashlib.sha256(real_ip.encode()).hexdigest()[:16]

        with patch(
            "apps.payments.views.donation._ipware_get_client_ip",
            return_value=(real_ip, True),
        ) as mock_ipware:
            with patch("apps.payments.views.donation.cache") as mock_cache:
                mock_cache.add.return_value = True
                mock_cache.incr.return_value = 1
                _check_donation_rate_limit(req)

        mock_ipware.assert_called_once()

        # The cache key must embed the real IP hash, not the proxy REMOTE_ADDR
        add_keys = [
            (c.args[0] if c.args else c.kwargs.get("key", ""))
            for c in mock_cache.add.call_args_list
        ]
        proxy_hash = hashlib.sha256(b"10.0.0.1").hexdigest()[:16]

        for key in add_keys:
            self.assertNotIn("10.0.0.1", key, "Proxy REMOTE_ADDR must not appear in cache key")
            self.assertNotIn(proxy_hash, key, "Proxy IP hash must not be used as rate-limit key")

        self.assertTrue(
            any(expected_hash in k for k in add_keys),
            f"Real IP hash {expected_hash!r} not found in cache keys: {add_keys}",
        )

    def test_all_donors_behind_proxy_get_separate_rate_limit_buckets(self):
        """Two donors sharing the same REMOTE_ADDR (proxy) but different real IPs
        must land in separate rate-limit buckets."""
        factory = RequestFactory()

        def _req(real_ip):
            req = factory.post("/fake/")
            req.META["REMOTE_ADDR"] = "10.0.0.1"  # same proxy for both
            req.user = AnonymousUser()
            return req, real_ip

        req1, ip1 = _req("203.0.113.10")
        req2, ip2 = _req("203.0.113.20")

        keys_seen = []

        def _capture_add(key, value, timeout=None):
            keys_seen.append(key)
            return True

        with patch("apps.payments.views.donation.cache") as mock_cache:
            mock_cache.add.side_effect = _capture_add
            mock_cache.incr.return_value = 1

            with patch(
                "apps.payments.views.donation._ipware_get_client_ip",
                return_value=(ip1, True),
            ):
                _check_donation_rate_limit(req1)

            with patch(
                "apps.payments.views.donation._ipware_get_client_ip",
                return_value=(ip2, True),
            ):
                _check_donation_rate_limit(req2)

        self.assertEqual(len(keys_seen), 2, "Expected one cache key per donor")
        self.assertNotEqual(
            keys_seen[0], keys_seen[1], "Two donors must not share a rate-limit bucket"
        )


# ---------------------------------------------------------------------------
# M5 — corrupt file reuse in save_receipt_pdf
# ---------------------------------------------------------------------------


class CorruptExistingPdfRegenerationTest(TestCase):
    """save_receipt_pdf must delete and regenerate files with a corrupt header."""

    def setUp(self):
        from apps.documents.models import DocumentCategory

        self.user = _make_user()
        self.intent = _make_intent(self.user)
        self.donation = _make_donation(self.user, self.intent)
        self.receipt = _make_receipt(self.donation)
        self.pdf_bytes = b"%PDF-1.4 good content"
        # Wave 6: save_receipt_pdf() requires this category to exist.
        DocumentCategory.objects.get_or_create(
            slug="donation-receipt-pdf",
            defaults={
                "name_en": "Donation Receipt PDF",
                "name_fr": "Reçu de don PDF",
                "min_retention_days": 2555,
                "max_retention_days": 2555,
            },
        )

    def test_corrupt_existing_pdf_is_deleted_and_regenerated(self):
        """A 0-byte or non-PDF file at the expected path must be deleted and regenerated."""
        # Wave 6: new storage key format
        expected_key = f"documents/active/receipts/{self.receipt.serial_number}/receipt.bin"

        mock_fh = MagicMock()
        mock_fh.__enter__ = MagicMock(return_value=mock_fh)
        mock_fh.__exit__ = MagicMock(return_value=False)
        mock_fh.read.return_value = b"\x00\x00\x00\x00"  # not a PDF

        with (
            patch("django.core.files.storage.default_storage") as mock_storage,
            patch("apps.documents.services.retention.schedule_expiry"),
        ):
            mock_storage.exists.side_effect = [True, False]  # exists→True (corrupt), then unused
            mock_storage.open.return_value = mock_fh
            mock_storage.save.return_value = expected_key

            save_receipt_pdf(self.receipt, self.pdf_bytes)

        # Must have deleted the corrupt file
        mock_storage.delete.assert_called_once_with(expected_key)
        # Must have written a fresh file
        mock_storage.save.assert_called_once()

    def test_zero_byte_file_triggers_warning_log(self):
        """A zero-byte file (empty read) must log a warning before regenerating."""
        # Wave 6: new storage key format
        expected_key = f"documents/active/receipts/{self.receipt.serial_number}/receipt.bin"

        mock_fh = MagicMock()
        mock_fh.__enter__ = MagicMock(return_value=mock_fh)
        mock_fh.__exit__ = MagicMock(return_value=False)
        mock_fh.read.return_value = b""  # empty file

        with (
            patch("django.core.files.storage.default_storage") as mock_storage,
            patch("apps.documents.services.retention.schedule_expiry"),
        ):
            mock_storage.exists.side_effect = [True, False]
            mock_storage.open.return_value = mock_fh
            mock_storage.save.return_value = expected_key

            with self.assertLogs("apps.payments.receipt_pdf", level="WARNING") as log_ctx:
                save_receipt_pdf(self.receipt, self.pdf_bytes)

        self.assertTrue(
            any("corrupt" in line or "regenerating" in line for line in log_ctx.output),
            f"Expected corrupt/regenerating warning in logs: {log_ctx.output}",
        )

    def test_valid_pdf_header_is_reused_without_delete(self):
        """A file with a valid %PDF header must be reused — delete must NOT be called."""
        # Wave 6: new storage key format
        expected_key = f"documents/active/receipts/{self.receipt.serial_number}/receipt.bin"

        mock_fh = MagicMock()
        mock_fh.__enter__ = MagicMock(return_value=mock_fh)
        mock_fh.__exit__ = MagicMock(return_value=False)
        mock_fh.read.return_value = b"%PDF"  # valid header

        with (
            patch("django.core.files.storage.default_storage") as mock_storage,
            patch("apps.documents.services.retention.schedule_expiry"),
        ):
            mock_storage.exists.return_value = True
            mock_storage.open.return_value = mock_fh

            result = save_receipt_pdf(self.receipt, self.pdf_bytes)

        mock_storage.delete.assert_not_called()
        mock_storage.save.assert_not_called()
        self.assertEqual(result, expected_key)

    def test_unreadable_file_logs_warning_and_regenerates(self):
        """An OSError on open must log a warning and fall through to regenerate."""
        # Wave 6: new storage key format
        expected_key = f"documents/active/receipts/{self.receipt.serial_number}/receipt.bin"

        with (
            patch("django.core.files.storage.default_storage") as mock_storage,
            patch("apps.documents.services.retention.schedule_expiry"),
        ):
            mock_storage.exists.side_effect = [True, False]
            mock_storage.open.side_effect = OSError("permission denied")
            mock_storage.save.return_value = expected_key

            with self.assertLogs("apps.payments.receipt_pdf", level="WARNING") as log_ctx:
                save_receipt_pdf(self.receipt, self.pdf_bytes)

        # Save must be called (regeneration happened)
        mock_storage.save.assert_called_once()
        self.assertTrue(
            any("unreadable" in line or "regenerating" in line for line in log_ctx.output),
            f"Expected unreadable/regenerating warning: {log_ctx.output}",
        )

    def test_corrupt_file_log_does_not_include_pii(self):
        """The warning log for a corrupt file must only include the serial, never donor PII."""
        # Wave 6: new storage key format
        expected_key = f"documents/active/receipts/{self.receipt.serial_number}/receipt.bin"
        donor_name = self.receipt.donor_legal_name

        mock_fh = MagicMock()
        mock_fh.__enter__ = MagicMock(return_value=mock_fh)
        mock_fh.__exit__ = MagicMock(return_value=False)
        mock_fh.read.return_value = b"\xff\xfe\x00\x00"  # not PDF

        with (
            patch("django.core.files.storage.default_storage") as mock_storage,
            patch("apps.documents.services.retention.schedule_expiry"),
        ):
            mock_storage.exists.side_effect = [True, False]
            mock_storage.open.return_value = mock_fh
            mock_storage.save.return_value = expected_key

            with self.assertLogs("apps.payments.receipt_pdf", level="WARNING") as log_ctx:
                save_receipt_pdf(self.receipt, self.pdf_bytes)

        for record in log_ctx.output:
            self.assertNotIn(donor_name, record, f"Donor name found in log: {record!r}")


# ---------------------------------------------------------------------------
# M6 — advantage_amount <= eligible_amount DB constraint
# ---------------------------------------------------------------------------


class ReceiptAmountConstraintTest(TestCase):
    """DB-level CheckConstraint must reject advantage_amount > eligible_amount."""

    def setUp(self):
        self.user = _make_user()
        self.intent = _make_intent(self.user)
        self.donation = _make_donation(self.user, self.intent)

    def _make_base_receipt_kwargs(self, *, eligible_amount, advantage_amount):
        serial = f"2024-{str(uuid.uuid4().int % 1_000_000).zfill(6)}"
        return {
            "donation": self.donation,
            "status": OfficialDonationReceipt.RECEIPT_STATUS_ISSUED,
            "serial_number": serial,
            "donor_legal_name": "Test Donor",
            "donor_address_line1": "123 Main St",
            "donor_city": "Ottawa",
            "donor_province": "ON",
            "donor_postal_code": "K1A 0A6",
            "donation_date": date(2024, 6, 1),
            "receipt_date": date(2024, 6, 15),
            "eligible_amount": eligible_amount,
            "advantage_amount": advantage_amount,
            "advantage_description": "",
            "charity_legal_name": "Test Charity Inc.",
            "charity_registration_number": "123456789 RR 0001",
            "charity_address": "100 Charity Ave, Ottawa, ON K2A 1B2",
            "place_of_issue": "Ottawa",
            "authorized_signatory_name": "Jane Smith",
            "authorized_signatory_title": "Executive Director",
            "is_annual_consolidated": False,
        }

    def test_advantage_exceeds_eligible_raises_integrity_error(self):
        """advantage_amount > eligible_amount must be rejected by the DB constraint."""
        kwargs = self._make_base_receipt_kwargs(
            eligible_amount=Decimal("50.00"),
            advantage_amount=Decimal("100.00"),  # invalid: 100 > 50
        )
        with self.assertRaises(IntegrityError):
            OfficialDonationReceipt.objects.create(**kwargs)

    def test_advantage_equal_to_eligible_is_allowed(self):
        """advantage_amount == eligible_amount is a valid edge case (full advantage)."""
        kwargs = self._make_base_receipt_kwargs(
            eligible_amount=Decimal("50.00"),
            advantage_amount=Decimal("50.00"),
        )
        # Should not raise
        receipt = OfficialDonationReceipt.objects.create(**kwargs)
        self.assertEqual(receipt.advantage_amount, Decimal("50.00"))

    def test_advantage_zero_eligible_nonzero_is_allowed(self):
        """Standard donation with no advantage must be allowed."""
        kwargs = self._make_base_receipt_kwargs(
            eligible_amount=Decimal("100.00"),
            advantage_amount=Decimal("0.00"),
        )
        receipt = OfficialDonationReceipt.objects.create(**kwargs)
        self.assertEqual(receipt.eligible_amount, Decimal("100.00"))

    def test_advantage_negative_raises_integrity_error(self):
        """advantage_amount < 0 must be rejected by the existing non-neg constraint."""
        kwargs = self._make_base_receipt_kwargs(
            eligible_amount=Decimal("100.00"),
            advantage_amount=Decimal("-1.00"),
        )
        with self.assertRaises(IntegrityError):
            OfficialDonationReceipt.objects.create(**kwargs)

    def test_eligible_negative_raises_integrity_error(self):
        """eligible_amount < 0 must be rejected by the existing non-neg constraint."""
        kwargs = self._make_base_receipt_kwargs(
            eligible_amount=Decimal("-1.00"),
            advantage_amount=Decimal("0.00"),
        )
        with self.assertRaises(IntegrityError):
            OfficialDonationReceipt.objects.create(**kwargs)

    def test_constraint_name_is_registered_in_meta(self):
        """The new constraint must be declared in OfficialDonationReceipt.Meta.constraints."""
        constraint_names = {c.name for c in OfficialDonationReceipt._meta.constraints}
        self.assertIn(
            "payments_receipt_advantage_lte_eligible",
            constraint_names,
            f"Constraint not found in Meta. Registered: {constraint_names}",
        )
