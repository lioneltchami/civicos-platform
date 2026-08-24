"""
Tests for Consent & Privacy Django views (non-API).

Covers:
- ConsentDashboardView — authentication guard, category display
- ConsentUpdateView — grant/withdraw actions, required category guard
- ConsentWithdrawConfirmView — GET/POST flows, required category guard
- ExportRequestView — form validation, task queuing
- ExportStatusView — export history display
- ExportDownloadView — IDOR guard, status guard, expiry guard, delivered status
"""

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.consent.models import (
    ConsentAuditEntry,
    ConsentCategory,
    ConsentRecord,
    DataExportRequest,
)

User = get_user_model()
VALID_PASSWORD = "SecureTest123!"

_PROCESS_TASK = "apps.consent.tasks.process_data_export"
_EXPORT_STORAGE = "django.core.files.storage.default_storage"


def _make_citizen(email=None):
    return User.objects.create_user(
        email=email or f"citizen-{uuid.uuid4().hex[:8]}@example.gov",
        password=VALID_PASSWORD,
    )


def _make_category(slug=None, is_required=False, is_active=True):
    slug = slug or f"cat-{uuid.uuid4().hex[:6]}"
    return ConsentCategory.objects.create(
        slug=slug,
        name_en=f"Category {slug}",
        name_fr=f"Catégorie {slug}",
        purpose_en="Test purpose",
        purpose_fr="Objet de test",
        lawful_basis="consent",
        is_required=is_required,
        is_active=is_active,
    )


class ConsentDashboardViewTests(TestCase):
    """Tests for GET /consent/ — the consent dashboard."""

    def setUp(self):
        self.client = Client()
        self.citizen = _make_citizen()

    def test_requires_login_anon_gets_redirect(self):
        resp = self.client.get(reverse("consent:dashboard"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp["Location"].lower() or "account")

    def test_authenticated_citizen_gets_200(self):
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        resp = self.client.get(reverse("consent:dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_shows_active_categories(self):
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        _make_category(slug="visible-cat")
        resp = self.client.get(reverse("consent:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("consent_items", resp.context)
        slugs = [item["category"].slug for item in resp.context["consent_items"]]
        self.assertIn("visible-cat", slugs)

    def test_does_not_show_inactive_categories(self):
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        _make_category(slug="hidden-cat", is_active=False)
        resp = self.client.get(reverse("consent:dashboard"))
        self.assertEqual(resp.status_code, 200)
        slugs = [item["category"].slug for item in resp.context["consent_items"]]
        self.assertNotIn("hidden-cat", slugs)


class ConsentUpdateViewTests(TestCase):
    """Tests for POST /consent/update/ — grant/withdraw actions."""

    def setUp(self):
        self.client = Client()
        self.citizen = _make_citizen()
        self.category = _make_category(slug="analytics")
        self.required_category = _make_category(slug="required-ops", is_required=True)

    def test_grant_action_creates_consent_record(self):
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        resp = self.client.post(
            reverse("consent:update"),
            {"action": "grant", "category_slug": "analytics"},
        )
        self.assertEqual(resp.status_code, 302)
        record = ConsentRecord.objects.get(citizen=self.citizen, category=self.category)
        self.assertEqual(record.status, ConsentRecord.STATUS_GRANTED)

    def test_withdraw_action_withdraws_consent(self):
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        # First grant
        self.client.post(
            reverse("consent:update"),
            {"action": "grant", "category_slug": "analytics"},
        )
        # Then withdraw
        resp = self.client.post(
            reverse("consent:update"),
            {"action": "withdraw", "category_slug": "analytics"},
        )
        self.assertEqual(resp.status_code, 302)
        record = ConsentRecord.objects.get(citizen=self.citizen, category=self.category)
        self.assertEqual(record.status, ConsentRecord.STATUS_WITHDRAWN)

    def test_withdraw_on_required_category_does_not_withdraw(self):
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        resp = self.client.post(
            reverse("consent:update"),
            {"action": "withdraw", "category_slug": "required-ops"},
        )
        # Should redirect to dashboard (with error message) — not crash
        self.assertEqual(resp.status_code, 302)
        # Ensure no withdrawn record was created
        self.assertFalse(
            ConsentRecord.objects.filter(
                citizen=self.citizen,
                category=self.required_category,
                status=ConsentRecord.STATUS_WITHDRAWN,
            ).exists()
        )

    def test_unauthenticated_post_redirects(self):
        resp = self.client.post(
            reverse("consent:update"),
            {"action": "grant", "category_slug": "analytics"},
        )
        self.assertEqual(resp.status_code, 302)
        # Should redirect to login
        self.assertIn("login", resp["Location"].lower() + resp["Location"])

    def test_invalid_action_shows_error(self):
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        resp = self.client.post(
            reverse("consent:update"),
            {"action": "INVALID", "category_slug": "analytics"},
        )
        # Redirects to dashboard with error
        self.assertEqual(resp.status_code, 302)


class ConsentWithdrawConfirmViewTests(TestCase):
    """Tests for GET/POST /consent/withdraw/<slug>/confirm/."""

    def setUp(self):
        self.client = Client()
        self.citizen = _make_citizen()
        self.category = _make_category(slug="newsletter")
        self.required = _make_category(slug="required-service", is_required=True)

    def test_get_shows_confirmation_page(self):
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        # Grant first so we have a record
        from apps.consent.services import ConsentService

        ConsentService.grant(self.citizen, "newsletter")
        resp = self.client.get(
            reverse("consent:withdraw-confirm", kwargs={"category_slug": "newsletter"})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("category", resp.context)

    def test_get_required_category_returns_400(self):
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        resp = self.client.get(
            reverse("consent:withdraw-confirm", kwargs={"category_slug": "required-service"})
        )
        self.assertEqual(resp.status_code, 400)

    def test_get_unknown_category_returns_404(self):
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        resp = self.client.get(
            reverse("consent:withdraw-confirm", kwargs={"category_slug": "no-such-category"})
        )
        self.assertEqual(resp.status_code, 404)

    def test_post_performs_withdrawal(self):
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        from apps.consent.services import ConsentService

        ConsentService.grant(self.citizen, "newsletter")
        resp = self.client.post(
            reverse("consent:withdraw-confirm", kwargs={"category_slug": "newsletter"})
        )
        self.assertEqual(resp.status_code, 302)
        record = ConsentRecord.objects.get(citizen=self.citizen, category=self.category)
        self.assertEqual(record.status, ConsentRecord.STATUS_WITHDRAWN)

    def test_post_required_category_returns_400(self):
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        resp = self.client.post(
            reverse("consent:withdraw-confirm", kwargs={"category_slug": "required-service"})
        )
        self.assertEqual(resp.status_code, 400)

    def test_requires_login(self):
        resp = self.client.get(
            reverse("consent:withdraw-confirm", kwargs={"category_slug": "newsletter"})
        )
        self.assertEqual(resp.status_code, 302)


class ExportRequestViewTests(TestCase):
    """Tests for GET/POST /consent/export/request/."""

    def setUp(self):
        self.client = Client()
        self.citizen = _make_citizen()

    def test_get_shows_form(self):
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        resp = self.client.get(reverse("consent:export-request"))
        self.assertEqual(resp.status_code, 200)

    def test_post_without_confirm_shows_validation_error(self):
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        resp = self.client.post(reverse("consent:export-request"), {})
        self.assertEqual(resp.status_code, 200)
        self.assertFormError(
            resp.context["form"], "confirm", "You must confirm to request your data export."
        )

    def test_post_with_confirm_creates_export_request(self):
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        with self.captureOnCommitCallbacks(execute=False):
            resp = self.client.post(
                reverse("consent:export-request"),
                {"confirm": "on"},
            )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(DataExportRequest.objects.filter(citizen=self.citizen).exists())

    def test_post_second_request_while_pending_shows_error(self):
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        with self.captureOnCommitCallbacks(execute=False):
            self.client.post(reverse("consent:export-request"), {"confirm": "on"})
        # Try again — should show error not crash
        resp = self.client.post(
            reverse("consent:export-request"),
            {"confirm": "on"},
        )
        self.assertEqual(resp.status_code, 200)  # form_invalid re-renders

    def test_requires_login(self):
        resp = self.client.get(reverse("consent:export-request"))
        self.assertEqual(resp.status_code, 302)


class ExportStatusViewTests(TestCase):
    """Tests for GET /consent/export/status/."""

    def setUp(self):
        self.client = Client()
        self.citizen = _make_citizen()

    def test_shows_export_history(self):
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        DataExportRequest.objects.create(
            citizen=self.citizen, status=DataExportRequest.STATUS_READY
        )
        resp = self.client.get(reverse("consent:export-status"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("exports", resp.context)
        self.assertEqual(resp.context["exports"].count(), 1)

    def test_requires_login(self):
        resp = self.client.get(reverse("consent:export-status"))
        self.assertEqual(resp.status_code, 302)

    def test_shows_empty_for_new_citizen(self):
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        resp = self.client.get(reverse("consent:export-status"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["exports"].count(), 0)


class ExportDownloadViewTests(TestCase):
    """Tests for GET /consent/export/download/<token>/."""

    def setUp(self):
        self.client = Client()
        self.citizen = _make_citizen()
        self.other_citizen = _make_citizen()

    def _make_ready_export(self, citizen=None):
        """
        Create a STATUS_READY DataExportRequest with a linked Document BB record
        and a real file in storage.  Wave 6 removed the storage_path field; the
        download view now reads from req.document._storage_key.
        """
        import json

        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        from apps.documents.models import Document, DocumentCategory

        citizen = citizen or self.citizen

        cat, _ = DocumentCategory.objects.get_or_create(
            slug="pipeda-data-export",
            defaults={
                "name_en": "PIPEDA Export",
                "name_fr": "Export PIPEDA",
                "is_transitory": True,
                "min_retention_days": 0,
                "max_retention_days": 30,
            },
        )
        export = DataExportRequest.objects.create(
            citizen=citizen,
            status=DataExportRequest.STATUS_READY,
            expires_at=timezone.now() + timedelta(days=7),
        )
        storage_key = f"documents/active/exports/{export.pk}/export.bin"
        content = json.dumps({"export_id": str(export.pk)}).encode()
        if default_storage.exists(storage_key):
            default_storage.delete(storage_key)
        default_storage.save(storage_key, ContentFile(content))

        doc = Document.objects.create(
            uploaded_by=citizen,
            category=cat,
            original_filename="export.json",
            mime_type="application/json",
            size_bytes=len(content),
            _storage_key=storage_key,
            scan_status=Document.ScanStatus.ACTIVE,
        )
        export.document = doc
        export.save(update_fields=["document"])
        return export

    def test_wrong_citizen_gets_404(self):
        """Wave 6 IDOR fix: non-owner receives 404 (not 403) to prevent existence leak."""
        export = self._make_ready_export(citizen=self.citizen)
        self.client.login(email=self.other_citizen.email, password=VALID_PASSWORD)
        resp = self.client.get(
            reverse("consent:export-download", kwargs={"token": export.download_token})
        )
        self.assertEqual(resp.status_code, 404)

    def test_status_not_ready_returns_404(self):
        export = DataExportRequest.objects.create(
            citizen=self.citizen,
            status=DataExportRequest.STATUS_PENDING,
        )
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        resp = self.client.get(
            reverse("consent:export-download", kwargs={"token": export.download_token})
        )
        self.assertEqual(resp.status_code, 404)

    def test_expired_export_returns_404(self):
        # Wave 6: storage_path removed; just create with an expired expires_at.
        export = DataExportRequest.objects.create(
            citizen=self.citizen,
            status=DataExportRequest.STATUS_READY,
            expires_at=timezone.now() - timedelta(hours=1),
        )
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        resp = self.client.get(
            reverse("consent:export-download", kwargs={"token": export.download_token})
        )
        self.assertEqual(resp.status_code, 404)

    def test_successful_download_updates_status_to_delivered(self):
        export = self._make_ready_export()
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        resp = self.client.get(
            reverse("consent:export-download", kwargs={"token": export.download_token})
        )
        self.assertEqual(resp.status_code, 200)
        export.refresh_from_db()
        self.assertEqual(export.status, DataExportRequest.STATUS_DELIVERED)

    def test_nonexistent_token_returns_404(self):
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        fake_token = uuid.uuid4()
        resp = self.client.get(reverse("consent:export-download", kwargs={"token": fake_token}))
        self.assertEqual(resp.status_code, 404)

    def test_requires_login(self):
        export = self._make_ready_export()
        resp = self.client.get(
            reverse("consent:export-download", kwargs={"token": export.download_token})
        )
        self.assertEqual(resp.status_code, 302)

    def test_successful_download_creates_audit_entry(self):
        export = self._make_ready_export()
        self.client.login(email=self.citizen.email, password=VALID_PASSWORD)
        self.client.get(reverse("consent:export-download", kwargs={"token": export.download_token}))
        entry = ConsentAuditEntry.objects.filter(
            citizen=self.citizen, action="export_downloaded"
        ).first()
        self.assertIsNotNone(entry)
