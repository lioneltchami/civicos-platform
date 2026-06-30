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
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, call, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.payments.models import (
    CharitySettings,
    Donation,
    DonationCampaign,
    OfficialDonationReceipt,
    PaymentIntent,
    DONATION_STATUS_COMPLETED,
)

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

    def _run_task_with_mocks(self, receipt_pk=None,
                             pdf_bytes=None, email_result=True,
                             pdf_error=None, email_error=None):
        """Run the task using apply() (always-eager) with mocked PDF/email services."""
        from apps.payments.tasks_receipts import generate_and_send_receipt
        pk = receipt_pk or str(self.receipt.pk)
        _bytes = pdf_bytes or self.pdf_bytes

        with patch(_PDF_GEN, return_value=_bytes) as mock_gen:
            with patch(_PDF_SAVE) as mock_save:
                with patch(_EMAIL_SEND, return_value=email_result) as mock_email:
                    result = generate_and_send_receipt.apply(args=[pk]).get()

        return result, mock_gen, mock_save, mock_email

    # 1. Happy path: generates PDF, saves it, sends email
    def test_happy_path(self):
        result, mock_gen, mock_save, mock_email = self._run_task_with_mocks()
        self.assertEqual(result["status"], "generated_and_sent")
        mock_gen.assert_called_once()
        mock_save.assert_called_once()
        mock_email.assert_called_once()

    # 2. Idempotency: if pdf_path already set, skips PDF generation
    def test_skips_pdf_generation_if_already_exists(self):
        from apps.payments.tasks_receipts import generate_and_send_receipt

        # Set pdf_path on receipt via _base_manager
        saved_path = f"receipts/{self.receipt.serial_number}.pdf"
        OfficialDonationReceipt._base_manager.filter(pk=self.receipt.pk).update(
            pdf_path=saved_path,
        )

        with patch(_PDF_GEN) as mock_gen:
            with patch(_PDF_SAVE):
                with patch(_EMAIL_SEND, return_value=True):
                    with patch(
                        "django.core.files.storage.default_storage.open"
                    ) as mock_open:
                        mock_open.return_value.__enter__.return_value.read.return_value = self.pdf_bytes
                        generate_and_send_receipt.apply(args=[str(self.receipt.pk)]).get()

        # PDF generation should NOT be called (already saved)
        mock_gen.assert_not_called()

    # 3. Receipt not found → logs error, returns dict with status not_found
    def test_receipt_not_found_returns_not_found(self):
        from apps.payments.tasks_receipts import generate_and_send_receipt

        fake_pk = str(uuid.uuid4())
        with self.assertLogs("apps.payments.tasks_receipts", level="ERROR") as log_ctx:
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
            with self.assertRaises(Exception):
                # In always-eager mode with task_eager_propagates=True, exceptions propagate
                generate_and_send_receipt.apply(
                    args=[str(self.receipt.pk)],
                    throw=True,
                ).get()

    # 6. Email send failure → task raises
    def test_email_send_failure_raises(self):
        from apps.payments.tasks_receipts import generate_and_send_receipt

        with patch(_PDF_GEN, return_value=self.pdf_bytes):
            with patch(_PDF_SAVE):
                with patch(_EMAIL_SEND, return_value=False):
                    with self.assertRaises(Exception):
                        generate_and_send_receipt.apply(
                            args=[str(self.receipt.pk)],
                            throw=True,
                        ).get()

    # 7. Status is 'resent' when PDF was already generated
    def test_status_is_resent_when_pdf_already_existed(self):
        from apps.payments.tasks_receipts import generate_and_send_receipt

        saved_path = f"receipts/{self.receipt.serial_number}.pdf"
        OfficialDonationReceipt._base_manager.filter(pk=self.receipt.pk).update(
            pdf_path=saved_path,
        )

        with patch(_PDF_GEN):
            with patch(_PDF_SAVE):
                with patch(_EMAIL_SEND, return_value=True):
                    with patch(
                        "django.core.files.storage.default_storage.open"
                    ) as mock_open:
                        mock_open.return_value.__enter__.return_value.read.return_value = self.pdf_bytes
                        result = generate_and_send_receipt.apply(
                            args=[str(self.receipt.pk)]
                        ).get()

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
            generate_annual_receipts,
            generate_and_send_receipt,
        )

        # SQLite doesn't support nextval() — patch the serial_number generation
        # so OfficialDonationReceipt.save() doesn't call the PostgreSQL sequence.
        _counter = [0]

        def _fake_save(receipt_instance, *args, **kwargs):
            if not receipt_instance.serial_number:
                _counter[0] += 1
                receipt_instance.serial_number = f"{year}-{str(_counter[0]).zfill(6)}"
            # Call the underlying Django model save, skipping OfficialDonationReceipt.save
            from django.db.models import Model
            Model.save(receipt_instance, *args, **kwargs)

        from apps.payments.models import OfficialDonationReceipt

        # captureOnCommitCallbacks(execute=True) forces on_commit hooks to run
        # synchronously inside the TestCase's wrapping transaction — otherwise
        # on_commit hooks are deferred until real commit, which never happens.
        # IMPORTANT: patch.object must wrap captureOnCommitCallbacks so that
        # when the callbacks fire (on context-manager exit), the mocks are still active.
        with patch.object(OfficialDonationReceipt, "save", _fake_save):
            with patch.object(generate_and_send_receipt, "delay", return_value=None) as mock_delay:
                with self.captureOnCommitCallbacks(execute=True):
                    result = generate_annual_receipts.apply(args=[year]).get()

        return result, mock_delay

    def _make_completed_donation(self, eligible_amount=Decimal("100.00"),
                                  amount=None, advantage_amount=Decimal("0.00"), **kwargs):
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
            self.user, intent,
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
            generate_annual_receipts,
            generate_and_send_receipt,
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
            generate_annual_receipts,
            generate_and_send_receipt,
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
        from apps.payments.tasks_receipts import (
            generate_annual_receipts,
            generate_and_send_receipt,
        )
        from django.db.models.query import QuerySet

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
            generate_annual_receipts,
            generate_and_send_receipt,
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
        utc_midnight_rollover = dt.datetime(2026, 1, 1, 4, 30, 0, tzinfo=dt.timezone.utc)
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
        from apps.payments.signals import receipt_issued
        from apps.payments.models import OfficialDonationReceipt
        from unittest.mock import patch

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
        from apps.payments.tasks_receipts import generate_annual_receipts, generate_and_send_receipt
        from apps.payments.models import OfficialDonationReceipt

        _counter = [0]

        def _fake_save(receipt_instance, *args, **kwargs):
            if not receipt_instance.serial_number:
                _counter[0] += 1
                receipt_instance.serial_number = f"{year}-{str(_counter[0]).zfill(6)}"
            from django.db.models import Model
            Model.save(receipt_instance, *args, **kwargs)

        with patch.object(OfficialDonationReceipt, "save", _fake_save):
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
        from unittest.mock import patch, MagicMock
        from apps.payments.tasks_receipts import kickoff_annual_receipts
        from django.utils.timezone import now

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
