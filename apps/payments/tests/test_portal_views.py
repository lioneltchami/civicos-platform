"""
Wave 5 — test_portal_views.py

Comprehensive test suite for apps/payments/views/portal.py.

Covers:
  - DonorPortalDashboardView   (12 tests)
  - DonationHistoryView         (15 tests)
  - ReceiptDownloadView         (20 tests)
  - ReceiptListView             (12 tests)
  - RecurringGiftListView       (10 tests)
  - RecurringGiftDetailView     (12 tests)
  - _get_year_filter helper      (8 tests)

Security invariants verified:
  - LoginRequired on every view (unauthenticated → 302)
  - IDOR: donors cannot access each other's records
  - pdf_path never appears in any HTTP response header or body
  - FileResponse filename uses serial_number only
  - Cancelled/superseded receipts return 404 from download
  - Log lines contain serial= but never pdf_path or donor PII
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.payments.models import (
    DONATION_STATUS_COMPLETED,
    FREQUENCY_MONTHLY,
    PLAN_STATUS_ACTIVE,
    PLAN_STATUS_CANCELLED,
    PLAN_STATUS_PAUSED,
    Donation,
    DonationCampaign,
    OfficialDonationReceipt,
    PaymentIntent,
    RecurringGiftPlan,
    CharitySettings,
)
from apps.payments.tests.factories import make_fixed_serial_fake_save
from apps.payments.views.portal import _get_year_filter

User = get_user_model()

# ---------------------------------------------------------------------------
# Module-level URL helpers (resolved once)
# ---------------------------------------------------------------------------

DASHBOARD_URL = reverse("donor_portal:dashboard")
HISTORY_URL = reverse("donor_portal:donation_history")
RECEIPT_LIST_URL = reverse("donor_portal:receipt_list")
RECURRING_LIST_URL = reverse("donor_portal:recurring_list")

# ---------------------------------------------------------------------------
# Fixture factories
# ---------------------------------------------------------------------------

_receipt_counter = [0]


def _next_serial():
    _receipt_counter[0] += 1
    return f"TEST-{str(_receipt_counter[0]).zfill(6)}"


def make_user(email=None, **kwargs):
    email = email or f"user_{uuid.uuid4().hex[:8]}@example.com"
    return User.objects.create_user(email=email, password="testpass123", **kwargs)


def make_campaign(name="Test Campaign", **kwargs):
    defaults = {
        "slug": f"campaign-{uuid.uuid4().hex[:6]}",
        "name_en": name,
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
        "charity_postal_code": "K1A 0A6",
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


def make_donation(donor, payment_intent=None, amount=Decimal("100.00"), **kwargs):
    if payment_intent is None:
        payment_intent = make_payment_intent(donor)
    defaults = {
        "payment_intent": payment_intent,
        "donor": donor,
        "amount": amount,
        "advantage_amount": Decimal("0.00"),
        "eligible_amount": amount,
        "is_recurring": False,
        "is_anonymous": False,
        "status": DONATION_STATUS_COMPLETED,
        "donor_name_snapshot": "Jane Citizen",
        "donor_address_snapshot": "123 Main St\nOttawa, ON  K1A 0A6",
    }
    defaults.update(kwargs)
    return Donation.objects.create(**defaults)


def make_recurring_plan(donor, campaign=None, status=PLAN_STATUS_ACTIVE, **kwargs):
    defaults = {
        "donor": donor,
        "campaign": campaign,
        "amount": Decimal("25.00"),
        "frequency": FREQUENCY_MONTHLY,
        "next_charge_date": date(2026, 7, 1),
        "gateway_subscription_id": f"sub_{uuid.uuid4().hex[:8]}",
        "status": status,
    }
    defaults.update(kwargs)
    return RecurringGiftPlan.objects.create(**defaults)


def make_receipt(donation, status="issued"):
    """
    Create an OfficialDonationReceipt, bypassing the PostgreSQL nextval() call
    in OfficialDonationReceipt.save() which doesn't exist in SQLite test DB.

    Wave 6: pdf_path removed; receipts start without a Document linked (pending state).
    Call BasePortalTestCase._link_document_to_receipt() to simulate a generated PDF.
    """
    serial = _next_serial()

    with patch.object(OfficialDonationReceipt, "save", make_fixed_serial_fake_save(serial)):
        receipt = OfficialDonationReceipt(
            donation=donation,
            status=status,
            donor_legal_name="Jane Citizen",
            donor_address_line1="123 Main St",
            donor_city="Ottawa",
            donor_province="ON",
            donor_postal_code="K1A 0A6",
            donation_date=date(2026, 1, 15),
            receipt_date=date(2026, 1, 16),
            eligible_amount=donation.eligible_amount,
            advantage_amount=Decimal("0.00"),
            charity_legal_name="Test Charity Inc.",
            charity_registration_number="123456789 RR 0001",
            charity_address="100 Charity Ave\nOttawa, ON  K1A 0A6",
            place_of_issue="Ottawa",
            authorized_signatory_name="Jane Smith",
            authorized_signatory_title="Executive Director",
            is_annual_consolidated=False,
        )
        receipt.save()
    return receipt


# ---------------------------------------------------------------------------
# Base test case
# ---------------------------------------------------------------------------

class BasePortalTestCase(TestCase):

    def setUp(self):
        # Two donors — used for IDOR tests
        self.donor = make_user(email="donor@example.com")
        self.other_donor = make_user(email="other@example.com")

        # Shared campaign and charity
        self.campaign = make_campaign(name="Help Fund")
        self.charity = make_charity_settings()

        # Recurring plans — one per donor
        self.plan = make_recurring_plan(self.donor, campaign=self.campaign)
        self.other_plan = make_recurring_plan(self.other_donor, campaign=self.campaign)

        # One donation per donor
        self.donation = make_donation(self.donor, amount=Decimal("50.00"))
        self.other_donation = make_donation(self.other_donor, amount=Decimal("75.00"))

        # One receipt per donor (issued, with Document BB linked to simulate generated PDF)
        self.receipt = make_receipt(self.donation, status="issued")
        self.other_receipt = make_receipt(self.other_donation, status="issued")
        self._link_document_to_receipt(self.receipt)
        self._link_document_to_receipt(self.other_receipt)

    def _link_document_to_receipt(self, receipt):
        """
        Wave 6: Simulate a generated PDF by creating a Document BB record and
        linking it to the receipt via _base_manager (bypasses append-only guard).
        Mirrors the logic of save_receipt_pdf() for test setup.
        """
        from apps.documents.models import Document, DocumentCategory

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
            uploaded_by=receipt.donation.donor,
            category=cat,
            original_filename=f"receipt-{receipt.serial_number}.pdf",
            mime_type="application/pdf",
            size_bytes=100,
            _storage_key=f"documents/active/receipts/{receipt.serial_number}/receipt.bin",
            scan_status=Document.ScanStatus.ACTIVE,
        )
        OfficialDonationReceipt._base_manager.filter(pk=receipt.pk).update(document=doc)
        receipt.document = doc
        receipt.document_id = doc.pk

    def _login_donor(self):
        self.client.force_login(self.donor)

    def _receipt_download_url(self, receipt):
        return reverse("donor_portal:receipt_download", kwargs={"receipt_pk": receipt.pk})

    def _recurring_detail_url(self, plan):
        return reverse("donor_portal:recurring_detail", kwargs={"pk": plan.pk})


# ===========================================================================
# 1. DonorPortalDashboardView — 12 tests
# ===========================================================================

class DashboardViewTests(BasePortalTestCase):

    # Test 1
    def test_unauthenticated_get_redirects_to_login(self):
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    # Test 2
    def test_authenticated_get_returns_200(self):
        self._login_donor()
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)

    # Test 3
    def test_total_donated_scoped_to_donor(self):
        self._login_donor()
        # donor has $50, other_donor has $75
        response = self.client.get(DASHBOARD_URL)
        ctx = response.context
        self.assertEqual(ctx["total_donated"], Decimal("50.00"))

    # Test 4
    def test_donation_count_scoped_to_donor(self):
        self._login_donor()
        # Add a second donation for donor
        make_donation(self.donor, amount=Decimal("30.00"))
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.context["donation_count"], 2)

    # Test 5
    def test_pending_receipt_count_for_donor_only(self):
        # Use fresh donations so the unique-issued-per-donation constraint is not violated
        # (setUp already creates one issued receipt per donor)
        pending_donation = make_donation(self.donor, amount=Decimal("25.00"))
        other_pending_donation = make_donation(self.other_donor, amount=Decimal("25.00"))
        # Wave 6: receipts without a linked Document are "pending" (document__isnull=True)
        pending = make_receipt(pending_donation, status="issued")
        # Also create pending for other_donor — should not count
        make_receipt(other_pending_donation, status="issued")
        self._login_donor()
        response = self.client.get(DASHBOARD_URL)
        # donor has 1 pending receipt (the new one without a Document;
        # self.receipt from setUp has a Document linked so it's not pending)
        self.assertEqual(response.context["pending_receipt_count"], 1)

    # Test 6
    def test_active_plans_scoped_to_donor(self):
        self._login_donor()
        response = self.client.get(DASHBOARD_URL)
        plans = list(response.context["active_plans"])
        pks = [p.pk for p in plans]
        self.assertIn(self.plan.pk, pks)
        self.assertNotIn(self.other_plan.pk, pks)

    # Test 7
    def test_recent_donations_scoped_to_donor(self):
        self._login_donor()
        response = self.client.get(DASHBOARD_URL)
        donations = list(response.context["recent_donations"])
        donor_pks = [d.pk for d in donations]
        self.assertIn(self.donation.pk, donor_pks)
        self.assertNotIn(self.other_donation.pk, donor_pks)

    # Test 8
    def test_recent_donations_limited_to_5(self):
        # Create 9 more donations for donor (total 10)
        for _ in range(9):
            make_donation(self.donor)
        self._login_donor()
        response = self.client.get(DASHBOARD_URL)
        recent = list(response.context["recent_donations"])
        self.assertEqual(len(recent), 5)

    # Test 9
    def test_empty_state_shows_when_no_donations_and_no_plans(self):
        # Create a fresh donor with no donations/plans
        fresh = make_user(email="fresh@example.com")
        self.client.force_login(fresh)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["donation_count"], 0)
        content = response.content.decode()
        self.assertIn("You haven't made any donations yet.", content)

    # Test 10
    def test_pending_receipt_count_zero_when_all_have_document_linked(self):
        # self.receipt has a Document BB linked (setUp calls _link_document_to_receipt)
        self._login_donor()
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.context["pending_receipt_count"], 0)

    # Test 11
    def test_dashboard_with_no_active_plans_shows_empty_active_plans(self):
        # Cancel the plan
        self.plan.status = PLAN_STATUS_CANCELLED
        self.plan.save()
        self._login_donor()
        response = self.client.get(DASHBOARD_URL)
        active_plans = list(response.context["active_plans"])
        self.assertEqual(len(active_plans), 0)

    # Test 12
    def test_total_donated_is_zero_when_no_donations(self):
        fresh = make_user(email="nodonate@example.com")
        self.client.force_login(fresh)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.context["total_donated"], Decimal("0.00"))


# ===========================================================================
# 2. DonationHistoryView — 15 tests
# ===========================================================================

class DonationHistoryViewTests(BasePortalTestCase):

    # Test 13
    def test_unauthenticated_redirects(self):
        response = self.client.get(HISTORY_URL)
        self.assertEqual(response.status_code, 302)

    # Test 14
    def test_authenticated_get_returns_200_with_donations_in_context(self):
        self._login_donor()
        response = self.client.get(HISTORY_URL)
        self.assertEqual(response.status_code, 200)
        self.assertIn("donations", response.context)

    # Test 15
    def test_only_donor_donations_in_context(self):
        self._login_donor()
        response = self.client.get(HISTORY_URL)
        pks = [d.pk for d in response.context["donations"]]
        self.assertIn(self.donation.pk, pks)
        self.assertNotIn(self.other_donation.pk, pks)

    # Test 16
    def test_paginated_30_donations_first_page_has_25(self):
        # Add 29 more donations for donor (total 30)
        for _ in range(29):
            make_donation(self.donor)
        self._login_donor()
        response = self.client.get(HISTORY_URL)
        self.assertEqual(len(response.context["donations"]), 25)

    # Test 17
    def test_page_2_returns_remaining_items(self):
        for _ in range(29):
            make_donation(self.donor)
        self._login_donor()
        response = self.client.get(HISTORY_URL + "?page=2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["donations"]), 5)

    # Test 18
    def test_page_abc_returns_404(self):
        self._login_donor()
        response = self.client.get(HISTORY_URL + "?page=abc")
        self.assertEqual(response.status_code, 404)

    # Test 19
    def test_page_0_returns_404(self):
        self._login_donor()
        response = self.client.get(HISTORY_URL + "?page=0")
        self.assertEqual(response.status_code, 404)

    # Test 20
    def test_page_9999_returns_404(self):
        self._login_donor()
        response = self.client.get(HISTORY_URL + "?page=9999")
        self.assertEqual(response.status_code, 404)

    # Test 21
    def test_year_filter_2026_filters_correctly(self):
        # The donation created in setUp was created at current time (2026)
        self._login_donor()
        response = self.client.get(HISTORY_URL + "?year=2026")
        self.assertEqual(response.status_code, 200)
        # The donation should appear (created_at is in 2026)
        pks = [d.pk for d in response.context["donations"]]
        self.assertIn(self.donation.pk, pks)

    # Test 22
    def test_year_filter_0000_ignored_returns_all(self):
        self._login_donor()
        response = self.client.get(HISTORY_URL + "?year=0000")
        self.assertEqual(response.status_code, 200)
        pks = [d.pk for d in response.context["donations"]]
        self.assertIn(self.donation.pk, pks)

    # Test 23
    def test_year_filter_9999_ignored(self):
        self._login_donor()
        response = self.client.get(HISTORY_URL + "?year=9999")
        self.assertEqual(response.status_code, 200)
        pks = [d.pk for d in response.context["donations"]]
        self.assertIn(self.donation.pk, pks)

    # Test 24
    def test_year_filter_abc_ignored(self):
        self._login_donor()
        response = self.client.get(HISTORY_URL + "?year=abc")
        self.assertEqual(response.status_code, 200)
        pks = [d.pk for d in response.context["donations"]]
        self.assertIn(self.donation.pk, pks)

    # Test 25
    def test_year_filter_1999_ignored(self):
        self._login_donor()
        response = self.client.get(HISTORY_URL + "?year=1999")
        self.assertEqual(response.status_code, 200)
        pks = [d.pk for d in response.context["donations"]]
        self.assertIn(self.donation.pk, pks)

    # Test 26
    def test_available_years_in_context(self):
        self._login_donor()
        response = self.client.get(HISTORY_URL)
        self.assertIn("available_years", response.context)
        # The donation was created this year
        self.assertIn(timezone.now().year, response.context["available_years"])

    # Test 27
    def test_empty_donations_renders_without_error(self):
        fresh = make_user(email="empty2@example.com")
        self.client.force_login(fresh)
        response = self.client.get(HISTORY_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["donations"]), 0)


# ===========================================================================
# 3. ReceiptDownloadView — 20 tests
# ===========================================================================

class ReceiptDownloadViewTests(BasePortalTestCase):

    # Test 28
    def test_unauthenticated_redirects(self):
        url = self._receipt_download_url(self.receipt)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    # Test 29
    @patch("apps.payments.views.portal.default_storage")
    def test_authenticated_donor_downloads_own_receipt_200(self, mock_storage):
        mock_storage.open.return_value = BytesIO(b"%PDF-1.4 fake")
        self._login_donor()
        url = self._receipt_download_url(self.receipt)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    # Test 30
    @patch("apps.payments.views.portal.default_storage")
    def test_response_has_correct_content_type_and_attachment(self, mock_storage):
        mock_storage.open.return_value = BytesIO(b"%PDF-1.4 fake content")
        self._login_donor()
        url = self._receipt_download_url(self.receipt)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get("Content-Type"), "application/pdf")
        cd = response.get("Content-Disposition", "")
        self.assertIn("attachment", cd)
        self.assertIn(self.receipt.serial_number, cd)

    # Test 31 — IDOR
    @patch("apps.payments.views.portal.default_storage")
    def test_idor_donor_cannot_download_other_donors_receipt(self, mock_storage):
        mock_storage.open.return_value = BytesIO(b"%PDF-1.4 fake")
        self._login_donor()
        # Try to access other_donor's receipt
        url = self._receipt_download_url(self.other_receipt)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    # Test 32
    def test_nonexistent_receipt_uuid_returns_404(self):
        self._login_donor()
        url = reverse("donor_portal:receipt_download", kwargs={"receipt_pk": uuid.uuid4()})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    # Test 33
    def test_receipt_without_document_returns_202(self):
        # Use a fresh donation so the unique-issued-per-donation constraint is not violated
        fresh_donation = make_donation(self.donor, amount=Decimal("25.00"))
        # Wave 6: receipt with no Document linked → view returns 202 (PDF not yet generated)
        pending_receipt = make_receipt(fresh_donation, status="issued")
        self._login_donor()
        url = self._receipt_download_url(pending_receipt)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 202)

    # Test 34 — CRA Fix 1: cancelled receipt returns 404
    @patch("apps.payments.views.portal.default_storage")
    def test_cancelled_receipt_returns_404(self, mock_storage):
        mock_storage.open.return_value = BytesIO(b"%PDF-1.4 fake")
        cancelled = make_receipt(
            self.donation,
            status=OfficialDonationReceipt.RECEIPT_STATUS_CANCELLED,
        )
        self._login_donor()
        url = self._receipt_download_url(cancelled)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    # Test 35 — CRA Fix 1: superseded receipt returns 404
    @patch("apps.payments.views.portal.default_storage")
    def test_superseded_receipt_returns_404(self, mock_storage):
        mock_storage.open.return_value = BytesIO(b"%PDF-1.4 fake")
        superseded = make_receipt(
            self.donation,
            status=OfficialDonationReceipt.RECEIPT_STATUS_SUPERSEDED,
        )
        self._login_donor()
        url = self._receipt_download_url(superseded)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    # Test 36 — Wave 6: storage_key NOT in Content-Disposition (security invariant)
    @patch("apps.payments.views.portal.default_storage")
    def test_storage_key_not_in_content_disposition_header(self, mock_storage):
        mock_storage.open.return_value = BytesIO(b"%PDF-1.4 fake")
        self._login_donor()
        url = self._receipt_download_url(self.receipt)
        response = self.client.get(url)
        cd = response.get("Content-Disposition", "")
        self.assertNotIn(self.receipt.document._storage_key, cd)
        self.assertNotIn("documents/active/", cd)

    # Test 37 — Wave 6: storage_key NOT in Content-Type
    @patch("apps.payments.views.portal.default_storage")
    def test_storage_key_not_in_content_type_header(self, mock_storage):
        mock_storage.open.return_value = BytesIO(b"%PDF-1.4 fake")
        self._login_donor()
        url = self._receipt_download_url(self.receipt)
        response = self.client.get(url)
        ct = response.get("Content-Type", "")
        self.assertNotIn(self.receipt.document._storage_key, ct)

    # Test 38 — Missing file from storage → 404, not 500
    @patch("apps.payments.views.portal.default_storage")
    def test_missing_file_returns_404_not_500(self, mock_storage):
        mock_storage.open.side_effect = OSError("File not found")
        self._login_donor()
        url = self._receipt_download_url(self.receipt)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    # Test 39 — File handle closed on exception
    @patch("apps.payments.views.portal.default_storage")
    def test_file_handle_closed_on_exception(self, mock_storage):
        mock_fileobj = MagicMock()
        mock_fileobj.read = MagicMock(return_value=b"%PDF-1.4 fake")
        mock_storage.open.return_value = mock_fileobj
        # Simulate FileResponse raising an exception after open
        with patch("apps.payments.views.portal.FileResponse", side_effect=RuntimeError("boom")):
            self._login_donor()
            url = self._receipt_download_url(self.receipt)
            try:
                self.client.get(url)
            except RuntimeError:
                pass
        mock_fileobj.close.assert_called_once()

    # Test 40 — Content-Disposition uses serial_number, not pdf_path
    @patch("apps.payments.views.portal.default_storage")
    def test_content_disposition_uses_serial_number_not_pdf_path(self, mock_storage):
        mock_storage.open.return_value = BytesIO(b"%PDF-1.4 fake")
        self._login_donor()
        url = self._receipt_download_url(self.receipt)
        response = self.client.get(url)
        cd = response.get("Content-Disposition", "")
        self.assertIn(f"receipt-{self.receipt.serial_number}.pdf", cd)

    # Test 41 — Wave 6: Log contains serial= but NOT the storage key (PIPEDA)
    @patch("apps.payments.views.portal.default_storage")
    def test_log_contains_serial_not_storage_key(self, mock_storage):
        mock_storage.open.return_value = BytesIO(b"%PDF-1.4 fake")
        self._login_donor()
        url = self._receipt_download_url(self.receipt)
        with self.assertLogs("apps.payments.views.portal", level="INFO") as cm:
            self.client.get(url)
        log_output = "\n".join(cm.output)
        self.assertIn(self.receipt.serial_number, log_output)
        self.assertNotIn(self.receipt.document._storage_key, log_output)

    # Test 42 — Log does NOT contain donor email or name
    @patch("apps.payments.views.portal.default_storage")
    def test_log_does_not_contain_donor_pii(self, mock_storage):
        mock_storage.open.return_value = BytesIO(b"%PDF-1.4 fake")
        self._login_donor()
        url = self._receipt_download_url(self.receipt)
        with self.assertLogs("apps.payments.views.portal", level="INFO") as cm:
            self.client.get(url)
        log_output = "\n".join(cm.output)
        self.assertNotIn(self.donor.email, log_output)
        self.assertNotIn("Jane Citizen", log_output)

    # Test 43 — Malformed UUID → 404 (Django URL resolver rejects non-UUID)
    def test_malformed_uuid_in_url_returns_404(self):
        self._login_donor()
        response = self.client.get("/donate/portal/receipts/not-a-uuid/download/")
        self.assertEqual(response.status_code, 404)

    # Test 44 — Correct status but wrong donor → 404 (IDOR prevention)
    @patch("apps.payments.views.portal.default_storage")
    def test_correct_status_wrong_donor_returns_404(self, mock_storage):
        # other_receipt is ISSUED and has a Document linked, but donor is not the owner
        self._login_donor()
        url = self._receipt_download_url(self.other_receipt)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    # Test 45 — Response bytes are served (not empty)
    @patch("apps.payments.views.portal.default_storage")
    def test_response_bytes_served(self, mock_storage):
        pdf_bytes = b"%PDF-1.4 fake content here"
        mock_storage.open.return_value = BytesIO(pdf_bytes)
        self._login_donor()
        url = self._receipt_download_url(self.receipt)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = b"".join(response.streaming_content)
        self.assertEqual(content, pdf_bytes)

    # Test 46 — as_attachment=True in response
    @patch("apps.payments.views.portal.default_storage")
    def test_as_attachment_true_in_response(self, mock_storage):
        mock_storage.open.return_value = BytesIO(b"%PDF-1.4 fake")
        self._login_donor()
        url = self._receipt_download_url(self.receipt)
        response = self.client.get(url)
        cd = response.get("Content-Disposition", "")
        self.assertIn("attachment", cd)

    # Test 47 — Wave 6: Missing file error log has serial= but NOT storage key (PIPEDA)
    @patch("apps.payments.views.portal.default_storage")
    def test_missing_file_error_log_contains_serial_not_storage_key(self, mock_storage):
        mock_storage.open.side_effect = OSError("no such file")
        self._login_donor()
        url = self._receipt_download_url(self.receipt)
        with self.assertLogs("apps.payments.views.portal", level="ERROR") as cm:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 404)
        log_output = "\n".join(cm.output)
        self.assertIn(self.receipt.serial_number, log_output)
        self.assertNotIn(self.receipt.document._storage_key, log_output)


# ===========================================================================
# 4. ReceiptListView — 12 tests
# ===========================================================================

class ReceiptListViewTests(BasePortalTestCase):

    # Test 48
    def test_unauthenticated_redirects(self):
        response = self.client.get(RECEIPT_LIST_URL)
        self.assertEqual(response.status_code, 302)

    # Test 49
    def test_authenticated_get_returns_200(self):
        self._login_donor()
        response = self.client.get(RECEIPT_LIST_URL)
        self.assertEqual(response.status_code, 200)

    # Test 50 — Only issued receipts; cancelled excluded (Fix 8)
    def test_only_issued_receipts_shown(self):
        # Create a cancelled receipt for donor
        cancelled = make_receipt(
            self.donation,
            status=OfficialDonationReceipt.RECEIPT_STATUS_CANCELLED,
        )
        self._login_donor()
        response = self.client.get(RECEIPT_LIST_URL)
        receipt_pks = [r.pk for r in response.context["receipts"]]
        self.assertIn(self.receipt.pk, receipt_pks)
        self.assertNotIn(cancelled.pk, receipt_pks)

    # Test 51 — cancelled_receipt_count in context
    def test_cancelled_receipt_count_in_context(self):
        make_receipt(
            self.donation,
            status=OfficialDonationReceipt.RECEIPT_STATUS_CANCELLED,
        )
        make_receipt(
            self.donation,
            status=OfficialDonationReceipt.RECEIPT_STATUS_CANCELLED,
        )
        self._login_donor()
        response = self.client.get(RECEIPT_LIST_URL)
        self.assertEqual(response.context["cancelled_receipt_count"], 2)

    # Test 52 — Warning banner rendered when cancelled_receipt_count > 0
    def test_warning_banner_shown_when_cancelled_receipts(self):
        make_receipt(
            self.donation,
            status=OfficialDonationReceipt.RECEIPT_STATUS_CANCELLED,
        )
        self._login_donor()
        response = self.client.get(RECEIPT_LIST_URL)
        content = response.content.decode()
        self.assertIn("receipt(s) have been cancelled", content)

    # Test 53 — Pagination: 30 receipts → first page has 25
    def test_paginated_30_receipts_first_page_has_25(self):
        # Create 29 more issued receipts for donor on separate donations
        # (unique-issued-per-donation constraint prevents two issued receipts per donation)
        for _ in range(29):
            fresh = make_donation(self.donor, amount=Decimal("10.00"))
            make_receipt(fresh, status="issued")
        self._login_donor()
        response = self.client.get(RECEIPT_LIST_URL)
        self.assertEqual(len(response.context["receipts"]), 25)

    # Test 54 — Year filter
    def test_year_filter_filters_receipts_by_year(self):
        self._login_donor()
        response = self.client.get(RECEIPT_LIST_URL + "?year=2026")
        self.assertEqual(response.status_code, 200)
        # receipt_date is 2026-01-16, so should appear
        pks = [r.pk for r in response.context["receipts"]]
        self.assertIn(self.receipt.pk, pks)

    # Test 55 — Year filter 9999 ignored
    def test_year_filter_9999_ignored(self):
        self._login_donor()
        response = self.client.get(RECEIPT_LIST_URL + "?year=9999")
        self.assertEqual(response.status_code, 200)
        pks = [r.pk for r in response.context["receipts"]]
        self.assertIn(self.receipt.pk, pks)

    # Test 56 — Other donor's receipts not in list
    def test_other_donor_receipts_not_in_list(self):
        self._login_donor()
        response = self.client.get(RECEIPT_LIST_URL)
        pks = [r.pk for r in response.context["receipts"]]
        self.assertNotIn(self.other_receipt.pk, pks)

    # Test 57 — Empty list renders without error
    def test_empty_receipt_list_renders_without_error(self):
        fresh = make_user(email="noreceipt@example.com")
        self.client.force_login(fresh)
        response = self.client.get(RECEIPT_LIST_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["receipts"]), 0)

    # Test 58 — Download links use donor_portal:receipt_download URL (UUID-based, no storage key)
    def test_download_links_use_receipt_download_url(self):
        # Use a fresh donation to avoid violating the unique-issued-per-donation constraint
        fresh_donation = make_donation(self.donor, amount=Decimal("25.00"))
        # Wave 6: create receipt and link a Document BB record to simulate a generated PDF
        receipt_with_pdf = make_receipt(fresh_donation, status="issued")
        self._link_document_to_receipt(receipt_with_pdf)
        self._login_donor()
        response = self.client.get(RECEIPT_LIST_URL)
        content = response.content.decode()
        expected_url = reverse(
            "donor_portal:receipt_download",
            kwargs={"receipt_pk": receipt_with_pdf.pk},
        )
        self.assertIn(expected_url, content)
        # Verify the download URL uses only the receipt UUID, not the storage key path
        self.assertNotIn("documents/active/", expected_url)

    # Test 59 — Wave 6: storage key (documents/active/...) never appears in HTML body
    def test_storage_key_not_in_html_response_body(self):
        self._login_donor()
        response = self.client.get(RECEIPT_LIST_URL)
        content = response.content.decode("utf-8", errors="replace")
        # Ensure the internal storage path format is not rendered to the browser
        self.assertNotIn("documents/active/receipts/", content)
        self.assertNotIn(self.receipt.document._storage_key, content)


# ===========================================================================
# 5. RecurringGiftListView — 10 tests
# ===========================================================================

class RecurringGiftListViewTests(BasePortalTestCase):

    # Test 60
    def test_unauthenticated_redirects(self):
        response = self.client.get(RECURRING_LIST_URL)
        self.assertEqual(response.status_code, 302)

    # Test 61
    def test_authenticated_get_returns_200_with_plans_in_context(self):
        self._login_donor()
        response = self.client.get(RECURRING_LIST_URL)
        self.assertEqual(response.status_code, 200)
        self.assertIn("plans", response.context)

    # Test 62
    def test_only_donor_plans_in_context(self):
        self._login_donor()
        response = self.client.get(RECURRING_LIST_URL)
        pks = [p.pk for p in response.context["plans"]]
        self.assertIn(self.plan.pk, pks)
        self.assertNotIn(self.other_plan.pk, pks)

    # Test 63
    def test_status_counts_in_context(self):
        self._login_donor()
        response = self.client.get(RECURRING_LIST_URL)
        ctx = response.context
        self.assertIn("active_count", ctx)
        self.assertIn("paused_count", ctx)
        self.assertIn("cancelled_count", ctx)

    # Test 64
    def test_status_counts_correct_for_fresh_donor(self):
        self._login_donor()
        response = self.client.get(RECURRING_LIST_URL)
        ctx = response.context
        self.assertEqual(ctx["active_count"], 1)
        self.assertEqual(ctx["paused_count"], 0)
        self.assertEqual(ctx["cancelled_count"], 0)

    # Test 65
    def test_cancel_link_rendered_for_active_plan(self):
        self._login_donor()
        response = self.client.get(RECURRING_LIST_URL)
        content = response.content.decode()
        # Cancel link should appear for active plan
        cancel_url = reverse("donate:recurring_cancel", kwargs={"plan_pk": self.plan.pk})
        self.assertIn(cancel_url, content)

    # Test 66
    def test_cancel_link_not_rendered_for_cancelled_plan(self):
        # Cancel the plan
        self.plan.status = PLAN_STATUS_CANCELLED
        self.plan.save()
        self._login_donor()
        response = self.client.get(RECURRING_LIST_URL)
        content = response.content.decode()
        cancel_url = reverse("donate:recurring_cancel", kwargs={"plan_pk": self.plan.pk})
        self.assertNotIn(cancel_url, content)

    # Test 67
    def test_other_donor_plan_not_in_context(self):
        self._login_donor()
        response = self.client.get(RECURRING_LIST_URL)
        pks = [p.pk for p in response.context["plans"]]
        self.assertNotIn(self.other_plan.pk, pks)

    # Test 68
    def test_empty_plan_list_renders_without_error(self):
        fresh = make_user(email="noplan@example.com")
        self.client.force_login(fresh)
        response = self.client.get(RECURRING_LIST_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["plans"]), 0)

    # Test 69
    def test_plan_campaign_name_appears_in_response(self):
        self._login_donor()
        response = self.client.get(RECURRING_LIST_URL)
        content = response.content.decode()
        self.assertIn(self.campaign.name_en, content)


# ===========================================================================
# 6. RecurringGiftDetailView — 12 tests
# ===========================================================================

class RecurringGiftDetailViewTests(BasePortalTestCase):

    # Test 70
    def test_unauthenticated_redirects(self):
        url = self._recurring_detail_url(self.plan)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    # Test 71
    def test_authenticated_get_own_plan_returns_200(self):
        self._login_donor()
        url = self._recurring_detail_url(self.plan)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    # Test 72 — IDOR
    def test_idor_cannot_view_other_donors_plan(self):
        self._login_donor()
        url = self._recurring_detail_url(self.other_plan)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    # Test 73
    def test_nonexistent_plan_uuid_returns_404(self):
        self._login_donor()
        url = reverse("donor_portal:recurring_detail", kwargs={"pk": uuid.uuid4()})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    # Test 74
    def test_plan_in_context_is_correct(self):
        self._login_donor()
        url = self._recurring_detail_url(self.plan)
        response = self.client.get(url)
        self.assertEqual(response.context["plan"].pk, self.plan.pk)

    # Test 75
    def test_can_cancel_true_for_active_plan(self):
        self._login_donor()
        url = self._recurring_detail_url(self.plan)
        response = self.client.get(url)
        self.assertTrue(response.context["can_cancel"])

    # Test 76
    def test_can_cancel_false_for_cancelled_plan(self):
        self.plan.status = PLAN_STATUS_CANCELLED
        self.plan.save()
        self._login_donor()
        url = self._recurring_detail_url(self.plan)
        response = self.client.get(url)
        self.assertFalse(response.context["can_cancel"])

    # Test 77
    def test_cancel_url_resolves_correctly(self):
        self._login_donor()
        url = self._recurring_detail_url(self.plan)
        response = self.client.get(url)
        expected = reverse("donate:recurring_cancel", kwargs={"plan_pk": self.plan.pk})
        self.assertEqual(response.context["cancel_url"], expected)

    # Test 78 — plan_donations in context, limited to 25
    def test_plan_donations_in_context(self):
        # Link donations to the plan
        donation = make_donation(
            self.donor,
            amount=Decimal("25.00"),
            recurring_plan=self.plan,
            is_recurring=True,
        )
        self._login_donor()
        url = self._recurring_detail_url(self.plan)
        response = self.client.get(url)
        self.assertIn("plan_donations", response.context)
        pks = [d.pk for d in response.context["plan_donations"]]
        self.assertIn(donation.pk, pks)

    # Test 79 — billing_history_truncated False when ≤ 25
    def test_billing_history_truncated_false_when_25_or_fewer(self):
        for _ in range(5):
            make_donation(
                self.donor,
                amount=Decimal("25.00"),
                recurring_plan=self.plan,
                is_recurring=True,
            )
        self._login_donor()
        url = self._recurring_detail_url(self.plan)
        response = self.client.get(url)
        self.assertFalse(response.context["billing_history_truncated"])

    # Test 80 — billing_history_truncated True when > 25
    def test_billing_history_truncated_true_when_26_or_more(self):
        for _ in range(26):
            make_donation(
                self.donor,
                amount=Decimal("25.00"),
                recurring_plan=self.plan,
                is_recurring=True,
            )
        self._login_donor()
        url = self._recurring_detail_url(self.plan)
        response = self.client.get(url)
        self.assertTrue(response.context["billing_history_truncated"])

    # Test 81 — Plan receipts in context
    def test_plan_receipts_in_context(self):
        # Create a donation linked to the plan, then a receipt for it
        plan_donation = make_donation(
            self.donor,
            amount=Decimal("25.00"),
            recurring_plan=self.plan,
            is_recurring=True,
        )
        plan_receipt = make_receipt(plan_donation, status="issued")
        self._login_donor()
        url = self._recurring_detail_url(self.plan)
        response = self.client.get(url)
        self.assertIn("receipts", response.context)
        receipt_pks = [r.pk for r in response.context["receipts"]]
        self.assertIn(plan_receipt.pk, receipt_pks)


# ===========================================================================
# 7. _get_year_filter helper — 8 tests
# ===========================================================================

class GetYearFilterTests(TestCase):

    # Test 82
    def test_valid_year_2024_returns_2024(self):
        self.assertEqual(_get_year_filter("2024"), 2024)

    # Test 83
    def test_valid_year_current_year_returns_current_year(self):
        current = timezone.now().year
        self.assertEqual(_get_year_filter(str(current)), current)

    # Test 84
    def test_0000_returns_none(self):
        self.assertIsNone(_get_year_filter("0000"))

    # Test 85
    def test_9999_returns_none(self):
        self.assertIsNone(_get_year_filter("9999"))

    # Test 86
    def test_1999_returns_none(self):
        self.assertIsNone(_get_year_filter("1999"))

    # Test 87
    def test_alpha_string_returns_none(self):
        self.assertIsNone(_get_year_filter("abc"))

    # Test 88
    def test_empty_string_returns_none(self):
        self.assertIsNone(_get_year_filter(""))

    # Test 89
    def test_none_returns_none(self):
        self.assertIsNone(_get_year_filter(None))

    # Test 90 — L6 fix: _get_year_filter must use localtime, not UTC year
    def test_year_filter_uses_localtime_not_utc(self):
        """
        L6: _get_year_filter must use localtime(timezone.now()).year.
        A BC donor at 21:00 PST on Dec 31 (= 05:00 UTC Jan 1) would have
        their gift excluded from current-year results if the raw UTC year
        is used instead.
        """
        import inspect
        from apps.payments.views import portal
        source = inspect.getsource(portal._get_year_filter)
        # Must call localtime()
        self.assertIn(
            "localtime",
            source,
            "_get_year_filter must call localtime() to use the server's configured "
            "local timezone, not raw UTC year.",
        )
        # The current_year assignment must wrap now() in localtime
        self.assertIn(
            "localtime(timezone.now()).year",
            source,
            "_get_year_filter must assign current_year = localtime(timezone.now()).year",
        )
