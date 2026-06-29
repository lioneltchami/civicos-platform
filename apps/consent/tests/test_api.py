"""
Tests for the Consent & Privacy REST API.

Covers all consent API endpoints, authentication guards, IDOR prevention,
and isolation between citizens.

URL prefix: /api/v1/consent/
"""
import uuid

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APITestCase

from apps.consent.models import (
    ConsentCategory,
    ConsentRecord,
    DataExportRequest,
)
from apps.consent.services import ConsentService

User = get_user_model()
VALID_PASSWORD = "SecureTest123!"


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


def _bearer(client, user):
    """Obtain a JWT access token and configure the APIClient with it."""
    resp = client.post(
        "/api/v1/auth/token/",
        {"email": user.email, "password": VALID_PASSWORD},
        format="json",
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Token fetch failed for {user.email}: "
            f"status={resp.status_code} data={resp.data}"
        )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")


CATEGORIES_URL = "/api/v1/consent/categories/"
RECORDS_URL = "/api/v1/consent/records/"
EXPORTS_URL = "/api/v1/consent/export-requests/"


def _record_url(slug):
    return f"/api/v1/consent/records/{slug}/"


def _export_detail_url(pk):
    return f"/api/v1/consent/export-requests/{pk}/"


class ConsentCategoryListAPITests(APITestCase):
    """Tests for GET /api/v1/consent/categories/."""

    def setUp(self):
        self.citizen = _make_citizen()

    def test_returns_active_categories_authenticated(self):
        _make_category(slug="api-cat-one")
        _bearer(self.client, self.citizen)
        resp = self.client.get(CATEGORIES_URL)
        self.assertEqual(resp.status_code, 200)
        slugs = [c["slug"] for c in resp.data]
        self.assertIn("api-cat-one", slugs)

    def test_excludes_inactive_categories(self):
        _make_category(slug="api-active")
        _make_category(slug="api-inactive", is_active=False)
        _bearer(self.client, self.citizen)
        resp = self.client.get(CATEGORIES_URL)
        slugs = [c["slug"] for c in resp.data]
        self.assertIn("api-active", slugs)
        self.assertNotIn("api-inactive", slugs)

    def test_unauthenticated_returns_401(self):
        resp = self.client.get(CATEGORIES_URL)
        self.assertEqual(resp.status_code, 401)

    def test_response_fields_present(self):
        _make_category(slug="api-fields-test")
        _bearer(self.client, self.citizen)
        resp = self.client.get(CATEGORIES_URL)
        self.assertEqual(resp.status_code, 200)
        item = next(c for c in resp.data if c["slug"] == "api-fields-test")
        for field in ["id", "slug", "name_en", "name_fr", "lawful_basis", "is_required"]:
            self.assertIn(field, item)


class ConsentRecordListAPITests(APITestCase):
    """Tests for GET /api/v1/consent/records/."""

    def setUp(self):
        self.citizen = _make_citizen()
        self.other_citizen = _make_citizen()
        self.category = _make_category(slug="record-list-cat")

    def test_returns_own_records(self):
        _bearer(self.client, self.citizen)
        ConsentService.grant(self.citizen, "record-list-cat")
        resp = self.client.get(RECORDS_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)

    def test_idor_cannot_see_other_citizens_records(self):
        ConsentService.grant(self.other_citizen, "record-list-cat")
        _bearer(self.client, self.citizen)
        resp = self.client.get(RECORDS_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 0)

    def test_unauthenticated_returns_401(self):
        resp = self.client.get(RECORDS_URL)
        self.assertEqual(resp.status_code, 401)

    def test_empty_list_for_new_citizen(self):
        _bearer(self.client, self.citizen)
        resp = self.client.get(RECORDS_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, [])

    def test_record_fields_present(self):
        _bearer(self.client, self.citizen)
        ConsentService.grant(self.citizen, "record-list-cat")
        resp = self.client.get(RECORDS_URL)
        item = resp.data[0]
        for field in ["id", "category", "category_slug", "status"]:
            self.assertIn(field, item)


class ConsentRecordUpdateAPITests(APITestCase):
    """Tests for PATCH /api/v1/consent/records/<slug>/."""

    def setUp(self):
        self.citizen = _make_citizen()
        self.category = _make_category(slug="patchable")
        self.required_category = _make_category(slug="required-for-api", is_required=True)

    def test_grant_action_updates_status(self):
        _bearer(self.client, self.citizen)
        resp = self.client.patch(
            _record_url("patchable"),
            {"action": "grant"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], ConsentRecord.STATUS_GRANTED)

    def test_withdraw_action_updates_status(self):
        _bearer(self.client, self.citizen)
        ConsentService.grant(self.citizen, "patchable")
        resp = self.client.patch(
            _record_url("patchable"),
            {"action": "withdraw"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], ConsentRecord.STATUS_WITHDRAWN)

    def test_withdraw_required_category_returns_400(self):
        _bearer(self.client, self.citizen)
        resp = self.client.patch(
            _record_url("required-for-api"),
            {"action": "withdraw"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_action_returns_400(self):
        _bearer(self.client, self.citizen)
        resp = self.client.patch(
            _record_url("patchable"),
            {"action": "delete"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_unknown_category_returns_400(self):
        _bearer(self.client, self.citizen)
        resp = self.client.patch(
            _record_url("totally-unknown-slug"),
            {"action": "grant"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_unauthenticated_returns_401(self):
        resp = self.client.patch(
            _record_url("patchable"),
            {"action": "grant"},
            format="json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_missing_action_returns_400(self):
        _bearer(self.client, self.citizen)
        resp = self.client.patch(
            _record_url("patchable"),
            {},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)


class DataExportRequestAPITests(APITestCase):
    """Tests for GET/POST /api/v1/consent/export-requests/ and detail."""

    def setUp(self):
        self.citizen = _make_citizen()
        self.other_citizen = _make_citizen()

    def test_post_creates_export_request(self):
        _bearer(self.client, self.citizen)
        with self.captureOnCommitCallbacks(execute=False):
            resp = self.client.post(EXPORTS_URL, {}, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertIn("id", resp.data)
        self.assertEqual(resp.data["status"], DataExportRequest.STATUS_PENDING)

    def test_post_second_request_while_pending_returns_400(self):
        _bearer(self.client, self.citizen)
        with self.captureOnCommitCallbacks(execute=False):
            self.client.post(EXPORTS_URL, {}, format="json")
        resp = self.client.post(EXPORTS_URL, {}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_post_unauthenticated_returns_401(self):
        resp = self.client.post(EXPORTS_URL, {}, format="json")
        self.assertEqual(resp.status_code, 401)

    def test_get_returns_own_exports(self):
        _bearer(self.client, self.citizen)
        DataExportRequest.objects.create(citizen=self.citizen)
        DataExportRequest.objects.create(citizen=self.other_citizen)
        resp = self.client.get(EXPORTS_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)
        # Must only return this citizen's exports
        self.assertEqual(str(resp.data[0]["id"]), str(
            DataExportRequest.objects.filter(citizen=self.citizen).first().pk
        ))

    def test_get_unauthenticated_returns_401(self):
        resp = self.client.get(EXPORTS_URL)
        self.assertEqual(resp.status_code, 401)

    def test_get_detail_own_request(self):
        _bearer(self.client, self.citizen)
        export = DataExportRequest.objects.create(citizen=self.citizen)
        resp = self.client.get(_export_detail_url(export.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(str(resp.data["id"]), str(export.pk))

    def test_get_detail_other_citizens_request_returns_404(self):
        _bearer(self.client, self.citizen)
        other_export = DataExportRequest.objects.create(citizen=self.other_citizen)
        resp = self.client.get(_export_detail_url(other_export.pk))
        self.assertEqual(resp.status_code, 404)

    def test_export_response_fields(self):
        _bearer(self.client, self.citizen)
        with self.captureOnCommitCallbacks(execute=False):
            resp = self.client.post(EXPORTS_URL, {}, format="json")
        self.assertEqual(resp.status_code, 201)
        for field in ["id", "status", "format", "requested_at", "download_token"]:
            self.assertIn(field, resp.data)


class CitizenIsolationAPITests(APITestCase):
    """Cross-citizen isolation: citizen A cannot access citizen B's data."""

    def setUp(self):
        self.citizen_a = _make_citizen()
        self.citizen_b = _make_citizen()
        self.category = _make_category(slug="isolation-cat")

    def test_citizen_a_cannot_see_citizen_b_consent_records(self):
        ConsentService.grant(self.citizen_b, "isolation-cat")
        _bearer(self.client, self.citizen_a)
        resp = self.client.get(RECORDS_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 0, "Citizen A must not see Citizen B's records")

    def test_citizen_a_cannot_see_citizen_b_exports(self):
        DataExportRequest.objects.create(citizen=self.citizen_b)
        _bearer(self.client, self.citizen_a)
        resp = self.client.get(EXPORTS_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 0, "Citizen A must not see Citizen B's exports")

    def test_citizen_a_cannot_access_citizen_b_export_detail(self):
        export_b = DataExportRequest.objects.create(citizen=self.citizen_b)
        _bearer(self.client, self.citizen_a)
        resp = self.client.get(_export_detail_url(export_b.pk))
        self.assertEqual(resp.status_code, 404)
