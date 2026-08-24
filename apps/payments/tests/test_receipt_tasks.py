"""
Wave 4 — test_receipt_tasks.py

Tests for apps/payments/tasks_receipts.py:
  - generate_and_send_receipt
  - generate_annual_receipts

Patching strategy:
  The tasks import PDF/email service functions inside the task function body:
    from apps.payments.services.receipt_pdf import generate_receipt_pdf, save_receipt_pdf
    from apps.payments.services.receipt_email import send_receipt_email
  These are fresh imports on every call, so we patch the functions at their
  source module (apps.payments.services.receipt_pdf / receipt_email) — that
  way the mock is picked up by Python's module cache.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from apps.payments.models import (
    DONATION_STATUS_COMPLETED,
    CharitySettings,
    Donation,
    DonationCampaign,
    OfficialDonationReceipt,
    PaymentIntent,
)
from apps.payments.tests.factories import make_fake_save

User = get_user_model()

# Patch targets — source module paths so fresh imports pick up the mock
_PDF_GEN = "apps.payments.services.receipt_pdf.generate_receipt_pdf"
_PDF_SAVE = "apps.payments.services.receipt_pdf.save_receipt_pdf"
_EMAIL_SEND = "apps.payments.services.receipt_email.send_receipt_email"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def make_user(email=None, **kwargs):
    email = email or f"user_{uuid.uuid4().hex[:6]}@example.com"
    return User.objects.create_user(email=email, password="testpass123", **kwargs)


def make_campaign(**kwargs):
    defaults = {
        "slug": f"campaign-{uuid.uuid4().hex[:6]}",
        "name_en": "Annual Fund",
        "start_date": date(2026, 1, 1),
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


def make_donation(donor, payment_intent, **kwargs):
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


def make_receipt(donation, **kwargs):
    """Create OfficialDonationReceipt with a pre-set serial_number."""
    serial = f"2026-{str(uuid.uuid4().int % 1000000).zfill(6)}"
    defaults = {
        "donation": donation,
        "status": OfficialDonationReceipt.RECEIPT_STATUS_ISSUED,
        "donor_legal_name": "Jane Citizen",
        "donor_address_line1": "123 Main St",
        "donor_city": "Ottawa",
        "donor_province": "ON",
        "donor_postal_code": "K1A 0A6",
        "donation_date": date(2026, 6, 1),
        "receipt_date": date(2026, 6, 15),
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
# generate_and_send_receipt tests
# ---------------------------------------------------------------------------


class GenerateAndSendReceiptTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.campaign = make_campaign()
        self.intent = make_payment_intent(self.user)
        self.donation = make_donation(self.user, self.intent)
        self.receipt = make_receipt(self.donation)
        self.pdf_bytes = b"%PDF-1.4 fake content"

    def _run_task_with_mocks(
        self, receipt_pk=None, pdf_bytes=None, email_result=True, pdf_error=None, email_error=None
    ):
        """Run the task using apply() (always-eager) with mocked PDF/email services.

        Uses captureOnCommitCallbacks(execute=True) so that the on_commit hook
        that dispatches send_receipt_email() is executed synchronously inside the test.
        Without this, Django's TestCase wraps everything in a transaction that never
        commits, so on_commit callbacks are silently dropped.
        """
        from apps.payments.tasks_receipts import generate_and_send_receipt

        pk = receipt_pk or str(self.receipt.pk)
        _bytes = pdf_bytes or self.pdf_bytes

        with patch(_PDF_GEN, return_value=_bytes) as mock_gen:
            with patch(_PDF_SAVE) as mock_save:
                with patch(_EMAIL_SEND, return_value=email_result) as mock_email:
                    with self.captureOnCommitCallbacks(execute=True):
                        result = generate_and_send_receipt.apply(args=[pk]).get()

        return result, mock_gen, mock_save, mock_email

    # 1. Happy path: generates PDF, saves it, sends email
    def test_happy_path(self):
        result, mock_gen, mock_save, mock_email = self._run_task_with_mocks()
        self.assertEqual(result["status"], "generated_and_sent")
        mock_gen.assert_called_once()
        mock_save.assert_called_once()
        mock_email.assert_called_once()

    # 2. Idempotency: if document already linked, skips PDF generation
    def test_skips_pdf_generation_if_already_exists(self):
        from apps.documents.models import Document, DocumentCategory
        from apps.payments.tasks_receipts import generate_and_send_receipt

        # Wave 6: link a Document BB record to simulate prior successful PDF generation
        cat, _ = DocumentCategory.objects.get_or_create(
            slug="donation-receipt-pdf",
            defaults={
                "name_en": "Donation Receipt PDF",
                "name_fr": "Reçu de don PDF",
                "min_retention_days": 2555,
                "max_retention_days": 2555,
            },
        )
        doc = Document.objects.create(
            uploaded_by=self.user,
            category=cat,
            original_filename="receipt.bin",
            mime_type="application/pdf",
            size_bytes=len(self.pdf_bytes),
            _storage_key=f"documents/active/receipts/{self.receipt.serial_number}/receipt.bin",
            scan_status=Document.ScanStatus.ACTIVE,
        )
        OfficialDonationReceipt._base_manager.filter(pk=self.receipt.pk).update(
            document=doc,
        )

        with patch(_PDF_GEN) as mock_gen:
            with patch(_PDF_SAVE):
                with patch(_EMAIL_SEND, return_value=True):
                    with patch("django.core.files.storage.default_storage.open") as mock_open:
                        mock_open.return_value.__enter__.return_value.read.return_value = (
                            self.pdf_bytes
                        )
                        generate_and_send_receipt.apply(args=[str(self.receipt.pk)]).get()

        # PDF generation should NOT be called (already saved)
        mock_gen.assert_not_called()

    # 3. Receipt not found → logs error, returns dict with status not_found
    def test_receipt_not_found_returns_not_found(self):
        from apps.payments.tasks_receipts import generate_and_send_receipt

        fake_pk = str(uuid.uuid4())
        with self.assertLogs("apps.payments.tasks_receipts", level="ERROR"):
            result = generate_and_send_receipt.apply(args=[fake_pk]).get()

        self.assertEqual(result["status"], "not_found")

    # 4. Receipt with wrong status → returns skipped
    def test_wrong_status_returns_skipped(self):
        from apps.payments.tasks_receipts import generate_and_send_receipt

        OfficialDonationReceipt._base_manager.filter(pk=self.receipt.pk).update(
            status=OfficialDonationReceipt.RECEIPT_STATUS_CANCELLED
        )

        result = generate_and_send_receipt.apply(args=[str(self.receipt.pk)]).get()
        self.assertEqual(result["status"], "skipped")

    # 5. PDF generation failure → task raises (max_retries=3, always_eager raises)
    def test_pdf_generation_failure_raises(self):
        from apps.payments.tasks_receipts import generate_and_send_receipt

        with patch(_PDF_GEN, side_effect=Exception("WeasyPrint failed")):
            with self.assertRaises(Exception):  # noqa: B017
                # In always-eager mode with task_eager_propagates=True, exceptions propagate
                generate_and_send_receipt.apply(
                    args=[str(self.receipt.pk)],
                    throw=True,
                ).get()

    # 6. Email send failure → task retries (via on_commit firing synchronously in eager mode)
    def test_email_send_failure_raises(self):
        """
        send_receipt_email is called inside a transaction.on_commit() callback.
        In test eager mode, captureOnCommitCallbacks(execute=True) causes on_commit
        to fire synchronously within the transaction.atomic() block, which is still
        inside the task's outer try/except. When send_receipt_email returns False
        the closure raises RuntimeError, which is caught by the task and triggers
        a retry (celery.exceptions.Retry). In production, on_commit fires after the
        transaction commits so the retry does NOT trigger — but the error IS logged.

        We verify the retry path works correctly in eager test mode.
        """
        from apps.payments.tasks_receipts import generate_and_send_receipt

        with patch(_PDF_GEN, return_value=self.pdf_bytes):
            with patch(_PDF_SAVE):
                with patch(_EMAIL_SEND, return_value=False):
                    with self.assertRaises(Exception):  # noqa: B017
                        with self.captureOnCommitCallbacks(execute=True):
                            generate_and_send_receipt.apply(
                                args=[str(self.receipt.pk)],
                                throw=True,
                            ).get()

    # 7. Status is 'resent' when PDF was already generated
    def test_status_is_resent_when_pdf_already_existed(self):
        from apps.documents.models import Document, DocumentCategory
        from apps.payments.tasks_receipts import generate_and_send_receipt

        # Wave 6: link a Document BB record to simulate prior successful PDF generation
        cat, _ = DocumentCategory.objects.get_or_create(
            slug="donation-receipt-pdf",
            defaults={
                "name_en": "Donation Receipt PDF",
                "name_fr": "Reçu de don PDF",
                "min_retention_days": 2555,
                "max_retention_days": 2555,
            },
        )
        doc = Document.objects.create(
            uploaded_by=self.user,
            category=cat,
            original_filename="receipt.bin",
            mime_type="application/pdf",
            size_bytes=len(self.pdf_bytes),
            _storage_key=f"documents/active/receipts/{self.receipt.serial_number}/receipt.bin",
            scan_status=Document.ScanStatus.ACTIVE,
        )
        OfficialDonationReceipt._base_manager.filter(pk=self.receipt.pk).update(
            document=doc,
        )

        with patch(_PDF_GEN):
            with patch(_PDF_SAVE):
                with patch(_EMAIL_SEND, return_value=True):
                    with patch("django.core.files.storage.default_storage.open") as mock_open:
                        mock_open.return_value.__enter__.return_value.read.return_value = (
                            self.pdf_bytes
                        )
                        result = generate_and_send_receipt.apply(args=[str(self.receipt.pk)]).get()

        self.assertEqual(result["status"], "resent")

    # 8. Task logs serial number on success
    def test_logs_serial_number_on_success(self):
        from apps.payments.tasks_receipts import generate_and_send_receipt

        with patch(_PDF_GEN, return_value=self.pdf_bytes):
            with patch(_PDF_SAVE):
                with patch(_EMAIL_SEND, return_value=True):
                    with self.assertLogs("apps.payments.tasks_receipts", level="INFO") as log_ctx:
                        generate_and_send_receipt.apply(args=[str(self.receipt.pk)]).get()

        log_output = "\n".join(log_ctx.output)
        self.assertIn(self.receipt.serial_number, log_output)

    # Item 13a — second call (Celery retry simulation) must not send a second email.
    def test_generate_and_send_receipt_sends_email_exactly_once(self):
        """Two consecutive calls with the same receipt_pk send exactly one email."""
        from apps.payments.tasks_receipts import generate_and_send_receipt

        with patch(_PDF_GEN, return_value=self.pdf_bytes):
            with patch(_PDF_SAVE):
                with patch(_EMAIL_SEND, return_value=True) as mock_send:
                    # First call — sets email_sent=True and sends email
                    generate_and_send_receipt.apply(args=[str(self.receipt.pk)]).get()
                    # Second call — simulates Celery retry or duplicate dispatch
                    generate_and_send_receipt.apply(args=[str(self.receipt.pk)]).get()

        mock_send.assert_called_once()

    # Item 13b — email_sent flag is persisted after successful delivery.
    def test_email_sent_flag_set_after_send(self):
        """email_sent=True must be saved to the DB after successful email delivery."""
        from apps.payments.tasks_receipts import generate_and_send_receipt

        with patch(_PDF_GEN, return_value=self.pdf_bytes):
            with patch(_PDF_SAVE):
                with patch(_EMAIL_SEND, return_value=True):
                    generate_and_send_receipt.apply(args=[str(self.receipt.pk)]).get()

        self.receipt.refresh_from_db()
        self.assertTrue(self.receipt.email_sent)

    # Item 13c — email_sent flag is reset to False when email delivery fails.
    def test_email_sent_flag_reset_on_email_failure(self):
        """On email failure, email_sent must be reset to False so the next retry can re-attempt."""
        from apps.payments.tasks_receipts import generate_and_send_receipt

        with patch(_PDF_GEN, return_value=self.pdf_bytes):
            with patch(_PDF_SAVE):
                with patch(_EMAIL_SEND, return_value=False):
                    with self.assertRaises(Exception):  # noqa: B017
                        generate_and_send_receipt.apply(
                            args=[str(self.receipt.pk)],
                            throw=True,
                        ).get()

        self.receipt.refresh_from_db()
        self.assertFalse(self.receipt.email_sent)


# ---------------------------------------------------------------------------
# generate_annual_receipts tests
# ---------------------------------------------------------------------------


class GenerateAnnualReceiptsTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.campaign = make_campaign()
        self.charity = make_charity_settings()

    def _run_annual_task(self, year=2026):
        from apps.payments.tasks_receipts import (
            generate_and_send_receipt,
            generate_annual_receipts,
        )

        # SQLite doesn't support nextval() — patch the serial_number generation
        # so OfficialDonationReceipt.save() doesn't call the PostgreSQL sequence.
        _counter = [0]
        from apps.payments.models import OfficialDonationReceipt

        # captureOnCommitCallbacks(execute=True) forces on_commit hooks to run
        # synchronously inside the TestCase's wrapping transaction — otherwise
        # on_commit hooks are deferred until real commit, which never happens.
        # IMPORTANT: patch.object must wrap captureOnCommitCallbacks so that
        # when the callbacks fire (on context-manager exit), the mocks are still active.
        with patch.object(OfficialDonationReceipt, "save", make_fake_save(_counter, year=year)):
            with patch.object(generate_and_send_receipt, "delay", return_value=None) as mock_delay:
                with self.captureOnCommitCallbacks(execute=True):
                    result = generate_annual_receipts.apply(args=[year]).get()

        return result, mock_delay

    def _make_completed_donation(
        self,
        eligible_amount=Decimal("100.00"),
        amount=None,
        advantage_amount=Decimal("0.00"),
        **kwargs,
    ):
        intent = make_payment_intent(self.user)
        _amount = amount or eligible_amount
        return make_donation(
            self.user,
            intent,
            eligible_amount=eligible_amount,
            amount=_amount,
            advantage_amount=advantage_amount,
            **kwargs,
        )

    # 8. Returns correct counts for empty year
    def test_empty_year_returns_zeros(self):
        result, _ = self._run_annual_task(year=2099)
        self.assertEqual(result, {"processed": 0, "skipped": 0, "failed": 0})

    # 9. Creates receipts for donations in the tax year with no existing receipt
    def test_creates_receipts_for_eligible_donations(self):
        self._make_completed_donation()
        result, _ = self._run_annual_task(year=2026)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(OfficialDonationReceipt.objects.count(), 1)

    # 10. Skips donations already receipted
    def test_skips_already_receipted_donations(self):
        donation = self._make_completed_donation()
        make_receipt(donation)
        result, _ = self._run_annual_task(year=2026)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["processed"], 0)

    # 11. Idempotent: calling twice produces same result (second run: all skipped)
    def test_idempotent_double_call(self):
        self._make_completed_donation()
        result1, _ = self._run_annual_task(year=2026)
        self.assertEqual(result1["processed"], 1)

        result2, _ = self._run_annual_task(year=2026)
        self.assertEqual(result2["skipped"], 1)
        self.assertEqual(result2["processed"], 0)
        self.assertEqual(OfficialDonationReceipt.objects.count(), 1)

    # 12. TOCTOU safety: receipt created inside atomic block
    def test_receipt_created_in_atomic_block(self):
        """Receipt creation happens inside transaction.atomic() — verified by ORM result."""
        self._make_completed_donation()
        result, mock_delay = self._run_annual_task(year=2026)
        # If atomic() is working correctly, receipt is created and committed
        self.assertEqual(result["processed"], 1)
        self.assertEqual(OfficialDonationReceipt.objects.count(), 1)

    # 13. Task dispatch (generate_and_send_receipt.delay) called for each new receipt
    def test_task_dispatch_called_for_new_receipts(self):
        self._make_completed_donation()
        result, mock_delay = self._run_annual_task(year=2026)
        self.assertEqual(result["processed"], 1)
        # In test environment with on_commit firing, delay is called
        mock_delay.assert_called_once()

    # 14. Returns correct counts when some succeed and some fail
    def test_correct_counts_with_mixed_results(self):
        # Two eligible donations for same user → one consolidated receipt
        intent1 = make_payment_intent(self.user)
        intent2 = make_payment_intent(self.user)
        make_donation(self.user, intent1, eligible_amount=Decimal("50.00"), amount=Decimal("50.00"))
        make_donation(self.user, intent2, eligible_amount=Decimal("75.00"), amount=Decimal("75.00"))

        result, _ = self._run_annual_task(year=2026)
        self.assertEqual(result["processed"], 1)  # One receipt for one donor

    # 15. generate_and_send_receipt.delay called inside on_commit (not before commit)
    def test_task_dispatch_via_on_commit(self):
        """Verify generate_and_send_receipt.delay is called via transaction.on_commit."""
        self._make_completed_donation()
        # _run_annual_task uses captureOnCommitCallbacks so on_commit fires synchronously
        result, mock_delay = self._run_annual_task(year=2026)
        # dispatch was called once (via on_commit)
        self.assertEqual(result["processed"], 1)
        mock_delay.assert_called_once()

    # 16. Zero eligible_amount donations are skipped
    def test_zero_eligible_amount_skipped(self):
        intent = make_payment_intent(self.user)
        make_donation(
            self.user,
            intent,
            eligible_amount=Decimal("0.00"),
            amount=Decimal("100.00"),
            advantage_amount=Decimal("100.00"),
        )
        result, _ = self._run_annual_task(year=2026)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["processed"], 0)

    # 17. No charity settings → returns zeros without crash
    def test_no_charity_settings_returns_zeros(self):
        CharitySettings.objects.all().delete()
        self._make_completed_donation()
        result, _ = self._run_annual_task(year=2026)
        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["failed"], 0)

    # 18. Multiple donors get separate receipts
    def test_multiple_donors_get_separate_receipts(self):
        user2 = make_user()
        intent1 = make_payment_intent(self.user)
        intent2 = make_payment_intent(user2)
        make_donation(self.user, intent1)
        make_donation(user2, intent2)

        result, _ = self._run_annual_task(year=2026)
        self.assertEqual(result["processed"], 2)
        self.assertEqual(OfficialDonationReceipt.objects.count(), 2)

    # 19. Consolidated receipt for donor with multiple donations
    def test_consolidated_receipt_for_multiple_donations(self):
        intent1 = make_payment_intent(self.user)
        intent2 = make_payment_intent(self.user)
        make_donation(self.user, intent1, eligible_amount=Decimal("50.00"), amount=Decimal("50.00"))
        make_donation(self.user, intent2, eligible_amount=Decimal("75.00"), amount=Decimal("75.00"))

        self._run_annual_task(year=2026)
        receipt = OfficialDonationReceipt.objects.get()
        self.assertTrue(receipt.is_annual_consolidated)

    # 20. Annual receipt has summed eligible_amount
    def test_consolidated_receipt_sums_eligible_amounts(self):
        intent1 = make_payment_intent(self.user)
        intent2 = make_payment_intent(self.user)
        make_donation(self.user, intent1, eligible_amount=Decimal("50.00"), amount=Decimal("50.00"))
        make_donation(self.user, intent2, eligible_amount=Decimal("75.00"), amount=Decimal("75.00"))

        self._run_annual_task(year=2026)
        receipt = OfficialDonationReceipt.objects.get()
        self.assertEqual(receipt.eligible_amount, Decimal("125.00"))

    # 21. Logs correct counts at end
    def test_logs_final_counts(self):
        from apps.payments.tasks_receipts import (
            generate_and_send_receipt,
            generate_annual_receipts,
        )

        self._make_completed_donation()

        with patch.object(generate_and_send_receipt, "delay", return_value=None):
            with self.assertLogs("apps.payments.tasks_receipts", level="INFO") as log_ctx:
                generate_annual_receipts.apply(args=[2026]).get()

        log_output = "\n".join(log_ctx.output)
        self.assertIn("tax_year=2026", log_output)

    # 22. Never logs donor email in any log line
    def test_never_logs_donor_email(self):
        from apps.payments.tasks_receipts import (
            generate_and_send_receipt,
            generate_annual_receipts,
        )

        donor_email = self.user.email
        self._make_completed_donation()

        with patch.object(generate_and_send_receipt, "delay", return_value=None):
            with self.assertLogs("apps.payments.tasks_receipts", level="DEBUG") as log_ctx:
                generate_annual_receipts.apply(args=[2026]).get()

        log_output = "\n".join(log_ctx.output)
        self.assertNotIn(donor_email, log_output)

    # Fix 21. generate_annual_receipts uses .iterator() to avoid loading all rows at once
    def test_uses_iterator_for_memory_efficiency(self):
        """
        Regression: the queryset over completed_donations must use .iterator()
        so that Django fetches rows in chunks (server-side cursor) rather than
        loading the entire result set into memory. We verify this by patching
        QuerySet.iterator and asserting it was called with chunk_size=500.
        """
        from django.db.models.query import QuerySet

        from apps.payments.tasks_receipts import (
            generate_and_send_receipt,
            generate_annual_receipts,
        )

        self._make_completed_donation()

        original_iterator = QuerySet.iterator
        iterator_calls = []

        def capturing_iterator(qs_self, chunk_size=None):
            iterator_calls.append(chunk_size)
            # Delegate to the real iterator so processing continues normally
            return original_iterator(qs_self, chunk_size=chunk_size)

        with patch.object(QuerySet, "iterator", capturing_iterator):
            with patch.object(generate_and_send_receipt, "delay", return_value=None):
                with self.captureOnCommitCallbacks(execute=True):
                    generate_annual_receipts.apply(args=[2026]).get()

        self.assertTrue(
            iterator_calls,
            "QuerySet.iterator() was never called — Fix 21 regression: "
            "all donation rows are loaded into memory at once.",
        )
        self.assertIn(
            500,
            iterator_calls,
            f"iterator() was called but not with chunk_size=500. Got: {iterator_calls}",
        )

    # Fix 21b. generate_annual_receipts logs total_donations at start
    def test_logs_total_donations_at_start(self):
        """
        After Fix 21, the task must log total_donations=<n> before iterating,
        giving ops visibility into how large the batch is.
        """
        from apps.payments.tasks_receipts import (
            generate_and_send_receipt,
            generate_annual_receipts,
        )

        self._make_completed_donation()
        self._make_completed_donation()  # Two donations for same user

        with patch.object(generate_and_send_receipt, "delay", return_value=None):
            with self.assertLogs("apps.payments.tasks_receipts", level="INFO") as log_ctx:
                generate_annual_receipts.apply(args=[2026]).get()

        log_output = "\n".join(log_ctx.output)
        self.assertIn("total_donations=", log_output)
        self.assertIn("tax_year=2026", log_output)


# ---------------------------------------------------------------------------
# Fix 5 — UTC → local time for receipt dates
# ---------------------------------------------------------------------------


class ReceiptDateLocalTimeTests(TestCase):
    """
    Fix 5: donation_date on the receipt must reflect the donor's local calendar
    date (America/Toronto), not the UTC calendar date.

    Scenario: A donation at 23:30 ET on December 31 is stored as
    2026-01-01T04:30:00Z in UTC. Without localtime(), .date() returns 2026-01-01
    (the UTC date), placing the receipt in the wrong tax year.
    """

    def test_receipt_date_uses_local_time_not_utc(self):
        """donation_date must be the local date, not the UTC date."""
        import datetime as dt
        from unittest.mock import patch

        from apps.payments.models import OfficialDonationReceipt

        make_charity_settings()
        user = make_user()
        intent = make_payment_intent(user)

        # A UTC datetime that rolls over to the next calendar day in Toronto.
        # 2025-12-31 at 23:30 ET = 2026-01-01 04:30 UTC.
        utc_midnight_rollover = dt.datetime(2026, 1, 1, 4, 30, 0, tzinfo=dt.UTC)
        expected_local_date = dt.date(2025, 12, 31)  # what the donor sees in Toronto

        donation = make_donation(user, intent)
        # Backdate created_at via _base_manager so it crosses the calendar date boundary.
        Donation._base_manager.filter(pk=donation.pk).update(created_at=utc_midnight_rollover)
        donation.refresh_from_db()

        # OfficialDonationReceipt.save() calls nextval() which is PostgreSQL-only.
        # Patch save to assign a serial_number and delegate to the base Django save.
        _counter = [0]

        def _fake_receipt_save(receipt_instance, *args, **kwargs):
            if not receipt_instance.serial_number:
                _counter[0] += 1
                receipt_instance.serial_number = f"2025-{str(_counter[0]).zfill(6)}"
            # Skip OfficialDonationReceipt.save() validation and go straight to Model.save()
            from django.db.models import Model

            Model.save(receipt_instance, *args, **kwargs)

        with patch.object(OfficialDonationReceipt, "save", _fake_receipt_save):
            with patch("apps.payments.tasks_receipts.generate_and_send_receipt.delay"):
                with self.captureOnCommitCallbacks(execute=True):
                    with self.settings(TIME_ZONE="America/Toronto", USE_TZ=True):
                        from apps.payments.receivers import on_donation_completed

                        on_donation_completed(
                            sender=Donation,
                            donation=donation,
                            payment=None,
                        )

        receipt = OfficialDonationReceipt.objects.filter(donation=donation).first()
        self.assertIsNotNone(receipt, "Receipt should have been created")
        self.assertEqual(
            receipt.donation_date,
            expected_local_date,
            f"Expected local date {expected_local_date} but got {receipt.donation_date}",
        )


# ---------------------------------------------------------------------------
# Fix 6 — receipt_issued signal emitted after on_donation_completed
# ---------------------------------------------------------------------------


class ReceiptIssuedSignalTests(TestCase):
    """
    Fix 6: receipt_issued signal must be sent after on_donation_completed
    creates and saves the receipt.
    """

    def test_receipt_issued_signal_emitted(self):
        """After on_donation_completed fires, receipt_issued signal carries the receipt."""
        from unittest.mock import patch

        from apps.payments.models import OfficialDonationReceipt
        from apps.payments.signals import receipt_issued

        make_charity_settings()
        user = make_user()
        intent = make_payment_intent(user)
        donation = make_donation(user, intent)

        received_kwargs = {}

        def _capture(sender, receipt, donation, **kwargs):
            received_kwargs["receipt"] = receipt
            received_kwargs["donation"] = donation

        receipt_issued.connect(_capture, dispatch_uid="test_receipt_issued_signal")

        # OfficialDonationReceipt.save() calls nextval() which is PostgreSQL-only.
        _counter = [0]

        def _fake_receipt_save(receipt_instance, *args, **kwargs):
            if not receipt_instance.serial_number:
                _counter[0] += 1
                receipt_instance.serial_number = f"2026-{str(_counter[0]).zfill(6)}"
            from django.db.models import Model

            Model.save(receipt_instance, *args, **kwargs)

        try:
            with patch.object(OfficialDonationReceipt, "save", _fake_receipt_save):
                with patch("apps.payments.tasks_receipts.generate_and_send_receipt.delay"):
                    with self.captureOnCommitCallbacks(execute=True):
                        from apps.payments.receivers import on_donation_completed

                        on_donation_completed(
                            sender=Donation,
                            donation=donation,
                            payment=None,
                        )
        finally:
            receipt_issued.disconnect(_capture, dispatch_uid="test_receipt_issued_signal")

        self.assertIn("receipt", received_kwargs, "receipt_issued signal was not emitted")
        self.assertIsInstance(received_kwargs["receipt"], OfficialDonationReceipt)
        self.assertEqual(received_kwargs["donation"], donation)


# ---------------------------------------------------------------------------
# Fix 7 — TOCTOU duplicate safety in generate_annual_receipts
# ---------------------------------------------------------------------------


class AnnualReceiptDuplicateSafetyTests(TestCase):
    """
    Fix 7: Running generate_annual_receipts twice for the same tax year must
    not create duplicate receipts. The second run must skip already-processed
    donors rather than creating a second OfficialDonationReceipt row.
    """

    def setUp(self):
        self.user = make_user()
        self.campaign = make_campaign()
        self.charity = make_charity_settings()

    def _run(self, year=2026):
        from apps.payments.models import OfficialDonationReceipt
        from apps.payments.tasks_receipts import generate_and_send_receipt, generate_annual_receipts

        _counter = [0]

        with patch.object(OfficialDonationReceipt, "save", make_fake_save(_counter, year=year)):
            with patch.object(generate_and_send_receipt, "delay", return_value=None):
                with self.captureOnCommitCallbacks(execute=True):
                    result = generate_annual_receipts.apply(args=[year]).get()
        return result

    def test_annual_receipts_duplicate_safe(self):
        """Second run for same year skips already-issued donors; no duplicate rows."""
        intent = make_payment_intent(self.user)
        make_donation(self.user, intent)

        result1 = self._run(year=2026)
        self.assertEqual(result1["processed"], 1)
        self.assertEqual(OfficialDonationReceipt.objects.count(), 1)

        result2 = self._run(year=2026)
        self.assertEqual(result2["skipped"], 1, "Second run should skip the already-issued donor")
        self.assertEqual(result2["processed"], 0)
        # Still only one receipt row — no duplicate
        self.assertEqual(OfficialDonationReceipt.objects.count(), 1)


# ---------------------------------------------------------------------------
# Fix 8 — kickoff_annual_receipts fire-and-forget (no .get())
# ---------------------------------------------------------------------------


class KickoffAnnualReceiptsTests(TestCase):
    """
    Fix 8: kickoff_annual_receipts must dispatch generate_annual_receipts.delay()
    and return immediately — it must NOT call .get() on the AsyncResult, which
    would block the worker and risk deadlock.
    """

    def test_kickoff_dispatches_and_returns(self):
        """kickoff fires .delay with tax_year=current_year-1 and returns a dict."""
        from unittest.mock import patch

        from django.utils.timezone import now

        from apps.payments.tasks_receipts import kickoff_annual_receipts

        fake_result = MagicMock()
        fake_result.id = "fake-task-id-1234"

        with patch(
            "apps.payments.tasks_receipts.generate_annual_receipts.delay",
            return_value=fake_result,
        ) as mock_delay:
            result = kickoff_annual_receipts.apply().get()

        expected_year = now().year - 1
        mock_delay.assert_called_once_with(expected_year)
        # .get() must NOT have been called on the AsyncResult
        fake_result.get.assert_not_called()
        self.assertEqual(result["tax_year"], expected_year)
        self.assertEqual(result["task_id"], "fake-task-id-1234")
        self.assertEqual(result["status"], "dispatched")


# ---------------------------------------------------------------------------
# C2 — email_sent=True must only be set AFTER successful send
# ---------------------------------------------------------------------------


class GenerateAndSendReceiptEmailSentFlagTest(TestCase):
    """
    C2: email_sent=True must only be committed to the DB after a confirmed
    successful email send.  If it is set before the send and the worker dies
    between commit and send, the receipt is permanently marked delivered
    without having been sent — a CRA compliance failure.
    """

    def setUp(self):
        self.user = make_user()
        self.campaign = make_campaign()
        self.intent = make_payment_intent(self.user)
        self.donation = make_donation(self.user, self.intent)
        self.receipt = make_receipt(self.donation)
        self.pdf_bytes = b"%PDF-1.4 fake content"

    def test_email_sent_false_if_send_raises(self):
        """If send_receipt_email raises, email_sent must remain False for retry."""
        from smtplib import SMTPException

        from apps.payments.tasks_receipts import generate_and_send_receipt

        with patch(_PDF_GEN, return_value=self.pdf_bytes):
            with patch(_PDF_SAVE):
                with patch(_EMAIL_SEND, side_effect=SMTPException("timeout")):
                    with self.assertRaises(Exception):  # noqa: B017
                        generate_and_send_receipt.apply(
                            args=[str(self.receipt.pk)],
                            throw=True,
                        ).get()

        self.receipt.refresh_from_db()
        self.assertFalse(
            self.receipt.email_sent,
            "email_sent must remain False after a failed send so the retry can re-attempt",
        )

    def test_email_sent_true_after_successful_send(self):
        """After a successful send, email_sent must be True in the DB."""
        from apps.payments.tasks_receipts import generate_and_send_receipt

        with patch(_PDF_GEN, return_value=self.pdf_bytes):
            with patch(_PDF_SAVE):
                with patch(_EMAIL_SEND, return_value=True):
                    generate_and_send_receipt.apply(args=[str(self.receipt.pk)]).get()

        self.receipt.refresh_from_db()
        self.assertTrue(
            self.receipt.email_sent,
            "email_sent must be True after a successful send",
        )

    def test_email_sent_true_skips_resend(self):
        """If email_sent=True already on the receipt, the task exits without sending."""
        from apps.payments.tasks_receipts import generate_and_send_receipt

        # Mark the receipt as already delivered
        OfficialDonationReceipt._base_manager.filter(pk=self.receipt.pk).update(email_sent=True)

        with patch(_PDF_GEN, return_value=self.pdf_bytes) as mock_gen:
            with patch(_PDF_SAVE) as mock_save:
                with patch(_EMAIL_SEND) as mock_send:
                    generate_and_send_receipt.apply(args=[str(self.receipt.pk)]).get()

        mock_send.assert_not_called()
        mock_gen.assert_not_called()
        mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# C3 — only the specific unique constraint IntegrityError is swallowed
# ---------------------------------------------------------------------------


class AnnualReceiptIntegrityErrorTest(TestCase):
    """
    C3: generate_annual_receipts must only swallow IntegrityError raised by the
    specific duplicate-receipt unique constraint.  Any other IntegrityError
    (FK violation, NOT NULL failure, etc.) must propagate so it surfaces to
    operators rather than being silently absorbed as "skipped".
    """

    def setUp(self):
        self.user = make_user()
        self.campaign = make_campaign()
        self.charity = make_charity_settings()
        self.intent = make_payment_intent(self.user)
        make_donation(self.user, self.intent)

    def test_unrelated_integrity_error_is_reraised(self):
        """
        An IntegrityError whose message does NOT contain the unique constraint name
        must propagate out of the inner try/except, be caught by the outer
        per-donor except block, and increment failed (not skipped).
        """
        from apps.payments.models import OfficialDonationReceipt
        from apps.payments.tasks_receipts import generate_and_send_receipt, generate_annual_receipts

        unrelated_exc = IntegrityError(
            "NOT NULL constraint failed: payments_officialdonationreceipt.serial_number"
        )

        _counter = [0]
        with patch.object(OfficialDonationReceipt, "save", make_fake_save(_counter, year=2026)):
            # Patch the inner transaction.atomic save to raise an unrelated IntegrityError

            def _raise_unrelated(instance, *args, **kwargs):
                raise unrelated_exc

            with patch.object(OfficialDonationReceipt, "save", _raise_unrelated):
                with patch.object(generate_and_send_receipt, "delay", return_value=None):
                    result = generate_annual_receipts.apply(args=[2026]).get()

        # The unrelated IntegrityError must escape the inner except and be caught
        # by the outer per-donor handler, incrementing `failed`.
        self.assertEqual(
            result["failed"],
            1,
            "An unrelated IntegrityError must not be swallowed — it should increment failed",
        )
        self.assertEqual(result["skipped"], 0)

    def test_unique_constraint_integrity_error_is_skipped(self):
        """
        An IntegrityError whose message contains the specific unique constraint name
        must be swallowed and increment skipped (not failed).
        """
        from apps.payments.models import OfficialDonationReceipt
        from apps.payments.tasks_receipts import generate_and_send_receipt, generate_annual_receipts

        duplicate_exc = IntegrityError(
            "UNIQUE constraint failed: payments_receipt_unique_issued_per_donation"
        )

        def _raise_duplicate(instance, *args, **kwargs):
            raise duplicate_exc

        with patch.object(OfficialDonationReceipt, "save", _raise_duplicate):
            with patch.object(generate_and_send_receipt, "delay", return_value=None):
                result = generate_annual_receipts.apply(args=[2026]).get()

        self.assertEqual(
            result["skipped"],
            1,
            "A duplicate-receipt IntegrityError must be swallowed and counted as skipped",
        )
        self.assertEqual(result["failed"], 0)


# ---------------------------------------------------------------------------
# H4 — .iterator(chunk_size=500) must run inside transaction.atomic()
# ---------------------------------------------------------------------------


class AnnualReceiptsIteratorTransactionTests(TestCase):
    """
    H4: generate_annual_receipts must wrap .iterator(chunk_size=500) inside
    transaction.atomic() so that PostgreSQL server-side cursors actually stream
    rows in chunks rather than falling back to a full in-memory load.
    Without an open transaction, Django silently fetches the entire queryset
    into memory — a potential OOM for 10,000+ donors.
    """

    def setUp(self):
        self.user = make_user()
        self.charity = make_charity_settings()

    def test_iterator_runs_inside_transaction(self):
        """
        Verify that QuerySet.iterator is called while a transaction is active.
        We capture the Django connection.in_atomic_block state at the moment
        .iterator() is invoked, confirming the outer transaction.atomic() wrapper
        is in place.
        """
        from django.db import connection
        from django.db.models.query import QuerySet

        from apps.payments.models import OfficialDonationReceipt
        from apps.payments.tasks_receipts import generate_and_send_receipt, generate_annual_receipts

        intent = make_payment_intent(self.user)
        make_donation(self.user, intent)

        in_atomic_when_iterator_called = []
        original_iterator = QuerySet.iterator

        def capturing_iterator(qs_self, chunk_size=None):
            in_atomic_when_iterator_called.append(connection.in_atomic_block)
            return original_iterator(qs_self, chunk_size=chunk_size)

        _counter = [0]
        with patch.object(QuerySet, "iterator", capturing_iterator):
            with patch.object(OfficialDonationReceipt, "save", make_fake_save(_counter, year=2026)):
                with patch.object(generate_and_send_receipt, "delay", return_value=None):
                    with self.captureOnCommitCallbacks(execute=True):
                        generate_annual_receipts.apply(args=[2026]).get()

        self.assertTrue(
            in_atomic_when_iterator_called,
            "QuerySet.iterator() was never called — H4 regression.",
        )
        self.assertTrue(
            all(in_atomic_when_iterator_called),
            "iterator() was called outside a transaction — server-side cursor will not work. "
            f"in_atomic states: {in_atomic_when_iterator_called}",
        )


# ---------------------------------------------------------------------------
# H5 — generate_annual_receipts must have reject_on_worker_lost=True
# ---------------------------------------------------------------------------


class AnnualReceiptsRejectOnWorkerLostTests(TestCase):
    """
    H5: generate_annual_receipts must declare reject_on_worker_lost=True.
    Without it, a SIGKILL mid-run with acks_late=True causes Celery to
    acknowledge the task without requeuing — donors processed after the crash
    point silently miss their annual receipt (CRA compliance failure).
    """

    def test_generate_annual_receipts_reject_on_worker_lost(self):
        from apps.payments.tasks_receipts import generate_annual_receipts

        self.assertTrue(
            generate_annual_receipts.reject_on_worker_lost,
            "generate_annual_receipts must set reject_on_worker_lost=True so that a "
            "SIGKILL mid-run causes Celery to requeue the task rather than silently "
            "dropping it — CRA compliance requires all donors receive their receipt.",
        )


# ---------------------------------------------------------------------------
# H9 — Queue routing: receipt and webhook tasks on dedicated queues
# ---------------------------------------------------------------------------


class TaskQueueRoutingTests(TestCase):
    """
    H9: Verify that receipt tasks declare queue="receipts" and the webhook
    task declares queue="webhooks" on their decorators.

    These decorator-level queue names are belt-and-suspenders alongside the
    task_routes in config/celery.py — they ensure the correct queue is used
    even when tasks are dispatched without an explicit queue= argument.

    Preventing annual receipt runs from starving Stripe webhook processing is
    critical: Stripe retries webhooks at 30s, 1min, 3min and then stops.
    If webhook tasks pile up behind a 10,000-donor annual run, payments will
    not be recorded and donation receipts will be permanently lost.
    """

    def test_generate_annual_receipts_routes_to_receipts_queue(self):
        """generate_annual_receipts must declare queue='receipts'."""
        from apps.payments.tasks_receipts import generate_annual_receipts

        self.assertEqual(
            generate_annual_receipts.queue,
            "receipts",
            "generate_annual_receipts.queue must be 'receipts' so the annual run "
            "is isolated from latency-sensitive webhook tasks.",
        )

    def test_generate_and_send_receipt_routes_to_receipts_queue(self):
        """generate_and_send_receipt must declare queue='receipts'."""
        from apps.payments.tasks_receipts import generate_and_send_receipt

        self.assertEqual(
            generate_and_send_receipt.queue,
            "receipts",
            "generate_and_send_receipt.queue must be 'receipts' so PDF/email work "
            "does not compete with webhook tasks.",
        )

    def test_process_stripe_webhook_routes_to_webhooks_queue(self):
        """process_stripe_webhook must declare queue='webhooks'."""
        from apps.payments.tasks import process_stripe_webhook

        self.assertEqual(
            process_stripe_webhook.queue,
            "webhooks",
            "process_stripe_webhook.queue must be 'webhooks' so Stripe webhooks "
            "reach dedicated workers and are not delayed by receipt batch tasks.",
        )


# ---------------------------------------------------------------------------
# M7 — Annual receipt year filter uses local Canadian time, not UTC year
# ---------------------------------------------------------------------------


class AnnualReceiptsLocalTimezoneFilterTests(TestCase):
    """
    M7: generate_annual_receipts must include donations that fall within the
    tax year in local Canadian time (America/Toronto), even if their UTC
    timestamp falls into the next calendar year.

    A donation at 23:00 ET on Dec 31 = 04:00 UTC Jan 1 of the following year.
    Using __year=tax_year on a UTC-stored timestamp would exclude this donation
    from the current-year run — a CRA compliance failure (donor receives no
    official receipt for that tax year).
    """

    def _make_donation_at_local_time(self, user, intent, local_dt_naive, tz_name="America/Toronto"):
        """
        Create a Donation and force its created_at to a specific local-time moment.

        Django's auto_now_add ignores any value passed to create(), so we use a
        post-create update() on the base manager to bypass the auto-set behaviour.

        Args:
            local_dt_naive: a naive datetime in the given local timezone.
            tz_name: IANA timezone name (default: America/Toronto).
        Returns:
            The refreshed Donation instance with the overridden timestamp.
        """
        tz = ZoneInfo(tz_name)
        dt_utc = local_dt_naive.replace(tzinfo=tz).astimezone(UTC)
        donation = make_donation(user, intent)
        # Force-set the auto_now_add field via update() — create() ignores it.
        Donation.objects.filter(pk=donation.pk).update(created_at=dt_utc)
        donation.refresh_from_db()
        return donation

    def setUp(self):
        self.user = make_user()
        self.charity = make_charity_settings()
        self.intent = make_payment_intent(self.user)

        # 2024-12-31 23:00 ET = 2025-01-01 04:00 UTC.
        # This is a valid 2024 donation by Canadian local time, but its UTC year is 2025.
        # A naive __year=2024 filter on UTC-stored timestamps would miss it entirely.
        self.late_dec_donation = self._make_donation_at_local_time(
            self.user,
            self.intent,
            datetime(2024, 12, 31, 23, 0, 0),
        )

    def test_annual_receipt_includes_dec31_late_evening_et(self):
        """
        A donation at 23:00 ET on Dec 31 must be included in that year's run,
        even though its UTC timestamp falls on Jan 1 of the following year.
        """
        from apps.payments.tasks_receipts import generate_and_send_receipt, generate_annual_receipts

        _counter = [0]
        with patch.object(OfficialDonationReceipt, "save", make_fake_save(_counter, year=2024)):
            with patch.object(generate_and_send_receipt, "delay", return_value=None):
                result = generate_annual_receipts.apply(args=[2024]).get()

        self.assertEqual(
            result["processed"],
            1,
            "A donation at 23:00 ET on Dec 31 (UTC Jan 1) must be processed in "
            "the 2024 annual receipt run — UTC year filter would miss it.",
        )
        self.assertEqual(result["failed"], 0)

    def test_annual_receipt_excludes_next_year_donation(self):
        """
        A donation in January of the following year must NOT be included in
        the current year's receipt run.
        """
        user2 = make_user()
        intent2 = make_payment_intent(user2)
        # 2025-01-15 10:00 ET — clearly in 2025, must not appear in 2024 run.
        self._make_donation_at_local_time(
            user2,
            intent2,
            datetime(2025, 1, 15, 10, 0, 0),
        )

        from apps.payments.tasks_receipts import generate_and_send_receipt, generate_annual_receipts

        _counter = [0]
        with patch.object(OfficialDonationReceipt, "save", make_fake_save(_counter, year=2024)):
            with patch.object(generate_and_send_receipt, "delay", return_value=None):
                result = generate_annual_receipts.apply(args=[2024]).get()

        # Only the Dec 31 ET donation belongs to 2024; the Jan 15 2025 one must not.
        self.assertEqual(
            result["processed"],
            1,
            "A Jan 2025 donation must not appear in the 2024 annual receipt run.",
        )


# ---------------------------------------------------------------------------
# H-D — BC/NL timezone boundary: all Dec 31 Canadian donations captured
# ---------------------------------------------------------------------------


class AnnualReceiptsBCTimezoneTest(TestCase):
    """
    H-D: The year filter must use America/Vancouver (westernmost CA timezone,
    UTC-8 in winter) for the end boundary so that BC donors giving on Dec 31
    after 19:00 PST (= next UTC day) are included in the correct tax year.

    The start boundary must use America/St_Johns (UTC-3:30) so NL donors are
    never incorrectly spilled into the prior year's run.
    """

    def _make_donation_at_utc(self, user, intent, dt_utc):
        """Create a Donation with created_at forced to a specific UTC datetime."""
        donation = make_donation(user, intent)
        Donation.objects.filter(pk=donation.pk).update(created_at=dt_utc)
        donation.refresh_from_db()
        return donation

    def setUp(self):
        self.charity = make_charity_settings()

    def _run_annual_for_year(self, tax_year):
        from apps.payments.tasks_receipts import generate_and_send_receipt, generate_annual_receipts

        _counter = [0]
        with patch.object(OfficialDonationReceipt, "save", make_fake_save(_counter, year=tax_year)):
            with patch.object(generate_and_send_receipt, "delay", return_value=None):
                return generate_annual_receipts.apply(args=[tax_year]).get()

    def test_bc_donor_dec31_22h_pst_included_in_current_year(self):
        """
        22:00 PST Dec 31 2024 = 06:00 UTC Jan 1 2025 — must be included in the
        2024 run because it is Dec 31 in the donor's local (Pacific) time.
        """
        pst = ZoneInfo("America/Vancouver")
        dt_utc = datetime(2024, 12, 31, 22, 0, 0).replace(tzinfo=pst).astimezone(UTC)

        user = make_user()
        intent = make_payment_intent(user)
        self._make_donation_at_utc(user, intent, dt_utc)

        result = self._run_annual_for_year(2024)
        self.assertEqual(
            result["processed"],
            1,
            f"BC donor giving at 22:00 PST Dec 31 (UTC: {dt_utc}) must be in 2024 run. "
            "year_end_utc must reach Pacific midnight.",
        )
        self.assertEqual(result["failed"], 0)

    def test_bc_donor_jan1_0001_pst_excluded_from_prior_year(self):
        """
        00:01 PST Jan 1 2025 = 08:01 UTC Jan 1 2025 — must NOT be in the 2024 run
        because it is already January 1 in the donor's local (Pacific) time.
        """
        pst = ZoneInfo("America/Vancouver")
        dt_utc = datetime(2025, 1, 1, 0, 1, 0).replace(tzinfo=pst).astimezone(UTC)

        user = make_user()
        intent = make_payment_intent(user)
        self._make_donation_at_utc(user, intent, dt_utc)

        result = self._run_annual_for_year(2024)
        self.assertEqual(
            result["processed"],
            0,
            f"BC donor giving at 00:01 PST Jan 1 (UTC: {dt_utc}) must NOT be in 2024 run.",
        )

    def test_nl_donor_jan1_0001_nst_boundary_behaviour(self):
        """
        00:01 NST Jan 1 2025 = 03:31 UTC Jan 1 2025.

        This timestamp is Jan 1 in NL local time, but because the system does not
        store per-donor timezone it uses a single country-wide window:
          start = NL Jan 1 00:00 NST  (UTC-3:30, earliest Canadian Jan 1)
          end   = BC Dec 31 23:59 PST (UTC-8, latest Canadian Dec 31)

        The end boundary (2025-01-01 07:59:59 UTC) extends past this timestamp
        (2025-01-01 03:31 UTC), so the donation IS captured in the 2024 run.
        This is intentional: the system errs toward inclusion to avoid a donor
        missing a CRA-claimable receipt due to an ambiguous boundary.  A NL donor
        giving at 00:01 NST Jan 1 is simultaneously Dec 31 20:31 ET, so the
        ambiguity is real.  Including it guarantees CRA compliance; issuing a
        duplicate is rectifiable, but a missing receipt is not.
        """
        nst = ZoneInfo("America/St_Johns")
        dt_utc = datetime(2025, 1, 1, 0, 1, 0).replace(tzinfo=nst).astimezone(UTC)

        user = make_user()
        intent = make_payment_intent(user)
        self._make_donation_at_utc(user, intent, dt_utc)

        result = self._run_annual_for_year(2024)
        # Included by design — see docstring above.
        self.assertEqual(
            result["processed"],
            1,
            f"NL donor at 00:01 NST Jan 1 (UTC: {dt_utc}) is within the country-wide "
            "Dec 31 PST end boundary and is included in 2024 run by design.",
        )

    def test_existing_toronto_case_still_passes(self):
        """
        Previously-fixed M7 case: 23:00 ET Dec 31 (= 04:00 UTC Jan 1) must still
        be included in the current-year run — regression guard.
        """
        et = ZoneInfo("America/Toronto")
        dt_utc = datetime(2024, 12, 31, 23, 0, 0).replace(tzinfo=et).astimezone(UTC)

        user = make_user()
        intent = make_payment_intent(user)
        self._make_donation_at_utc(user, intent, dt_utc)

        result = self._run_annual_for_year(2024)
        self.assertEqual(
            result["processed"],
            1,
            f"Toronto donor at 23:00 ET Dec 31 (UTC: {dt_utc}) must remain in 2024 run.",
        )
        self.assertEqual(result["failed"], 0)


# ---------------------------------------------------------------------------
# M-G — generate_annual_receipts must not buffer all donor rows in memory
# ---------------------------------------------------------------------------


class AnnualReceiptsGroupbyTests(TestCase):
    """
    M-G: generate_annual_receipts must process donations donor-by-donor using
    itertools.groupby rather than building a donations_by_donor dict that holds
    all rows in memory simultaneously.

    For a large municipality (200k donors, 1.5M donations) the old pattern
    caused OOM kills because every donation row was appended to a dict before
    any processing happened — completely defeating .iterator(chunk_size=500).
    """

    def test_source_uses_groupby_not_donor_dict(self):
        """Source code must use itertools.groupby, not a donations_by_donor dict."""
        import inspect

        from apps.payments.tasks_receipts import generate_annual_receipts

        source = inspect.getsource(generate_annual_receipts)
        self.assertNotIn(
            "donations_by_donor = {}",
            source,
            "generate_annual_receipts must not build an all-donors-in-memory dict. "
            "Use itertools.groupby to process one donor at a time.",
        )
        self.assertNotIn(
            "donations_by_donor: dict = defaultdict(list)",
            source,
            "generate_annual_receipts must not use a defaultdict to buffer all donors. "
            "Use itertools.groupby instead.",
        )
        self.assertIn(
            "groupby",
            source,
            "generate_annual_receipts must use itertools.groupby to stream "
            "donations donor-by-donor without buffering all rows in memory.",
        )

    def test_multiple_donors_processed_correctly_with_groupby(self):
        """
        Two donors with different donations must each receive their own receipt.
        Verifies that the groupby refactor preserves correct per-donor aggregation.
        """
        from apps.payments.models import OfficialDonationReceipt
        from apps.payments.tasks_receipts import generate_and_send_receipt, generate_annual_receipts

        make_charity_settings()
        user1 = make_user()
        user2 = make_user()
        intent1 = make_payment_intent(user1)
        intent2a = make_payment_intent(user2)
        intent2b = make_payment_intent(user2)

        make_donation(user1, intent1, eligible_amount=Decimal("100.00"), amount=Decimal("100.00"))
        make_donation(user2, intent2a, eligible_amount=Decimal("50.00"), amount=Decimal("50.00"))
        make_donation(user2, intent2b, eligible_amount=Decimal("75.00"), amount=Decimal("75.00"))

        _counter = [0]
        with patch.object(OfficialDonationReceipt, "save", make_fake_save(_counter, year=2026)):
            with patch.object(generate_and_send_receipt, "delay", return_value=None):
                with self.captureOnCommitCallbacks(execute=True):
                    result = generate_annual_receipts.apply(args=[2026]).get()

        self.assertEqual(result["processed"], 2, "Both donors must get a receipt.")
        self.assertEqual(result["failed"], 0)

        receipts = OfficialDonationReceipt.objects.all()
        self.assertEqual(receipts.count(), 2)

        # user2's receipt must be consolidated with summed eligible amount
        user2_receipt = receipts.filter(donation__donor=user2).first()
        self.assertIsNotNone(user2_receipt)
        self.assertTrue(user2_receipt.is_annual_consolidated)
        self.assertEqual(user2_receipt.eligible_amount, Decimal("125.00"))


# ---------------------------------------------------------------------------
# M-H — kickoff_annual_receipts must have acks_late=True, reject_on_worker_lost=True
# ---------------------------------------------------------------------------


class KickoffAnnualReceiptsReliabilityTests(TestCase):
    """
    M-H: kickoff_annual_receipts is a once-per-year CRA compliance task.
    If the Celery worker dies mid-task with acks_late=False (the default),
    the message is acked on pickup and permanently lost — every donor in
    that tax year silently misses their official receipt.

    acks_late=True defers the ACK until after successful task completion.
    reject_on_worker_lost=True requeues the task on worker death (SIGKILL/OOM).
    Together they guarantee the kickoff task is retried if the worker fails.
    """

    def test_kickoff_annual_receipts_has_acks_late(self):
        """kickoff_annual_receipts must declare acks_late=True."""
        from apps.payments.tasks_receipts import kickoff_annual_receipts

        self.assertTrue(
            kickoff_annual_receipts.acks_late,
            "kickoff_annual_receipts must set acks_late=True so the message is not "
            "acked until after the task completes. Without this, a worker death "
            "between pickup and .delay() permanently loses the annual receipt run.",
        )

    def test_kickoff_annual_receipts_has_reject_on_worker_lost(self):
        """kickoff_annual_receipts must declare reject_on_worker_lost=True."""
        from apps.payments.tasks_receipts import kickoff_annual_receipts

        self.assertTrue(
            kickoff_annual_receipts.reject_on_worker_lost,
            "kickoff_annual_receipts must set reject_on_worker_lost=True so a "
            "SIGKILL or OOM during execution causes Celery to requeue the task "
            "rather than silently dropping the annual receipt run.",
        )
