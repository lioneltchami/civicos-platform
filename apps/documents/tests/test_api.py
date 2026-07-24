"""
apps/documents/tests/test_api.py
==================================
DRF REST API integration tests for the Document Management BB (spec §18).

These tests cover the eight API endpoints implemented in apps/api/documents/:

  7.1  POST   /api/v1/documents/request-upload/              → 201
  7.2  POST   /api/v1/documents/{doc_id}/confirm-upload/     → 200
  7.3  GET    /api/v1/documents/{doc_id}/                    → 200
  7.4  GET    /api/v1/documents/{doc_id}/download/           → 200
  7.5  GET    /api/v1/documents/dl/{token}/                  → 200 or 302
  7.6  GET    /api/v1/documents/?attached_to=…&object_id=…  → 200
  7.7  POST   /api/v1/documents/{doc_id}/attach/             → 201
  7.8  DELETE /api/v1/documents/{doc_id}/                    → 204
  7.9  GET    /api/v1/documents/{doc_id}/versions/           → 200

Security invariants tested
──────────────────────────
  S1 — storage_key / _storage_key NEVER in any response body or content
  S2 — scan_engine_result hidden from citizens; visible only to view_quarantined staff
  S3 — uploaded_by_id is UUID pk string, never email or username
  S4 — IDOR: non-owned PKs return 404, not 403 (reveals no existence info)
  S6 — legal_hold=True blocks DELETE unconditionally (403)
  A5 — unauthenticated requests return 401 (no SessionAuth → no 302 redirect)
  A3 — staff attachment creates a DB record (audit trail)
  A4 — soft-delete sets deleted_at (permanent record)

Test organisation
─────────────────
Each view class has a corresponding test class. Within each class:
  - setUp() creates the minimum required fixtures.
  - Service mocks always target the view-module import path
    (apps.api.documents.views.*) so the view layer is fully isolated from S3.
  - Real DB-only services (issue_access_token, consume_access_token, soft_delete)
    are used without mocks where no storage access is required.

URL name reference (apps/api/documents/urls.py):
  document-request-upload   POST /api/v1/documents/request-upload/
  api-token-redeem          GET  /api/v1/documents/dl/<token>/
  document-attached-list    GET  /api/v1/documents/
  document-detail           GET  /api/v1/documents/<uuid:doc_id>/
  document-confirm-upload   POST /api/v1/documents/<uuid:doc_id>/confirm-upload/
  document-download         GET  /api/v1/documents/<uuid:doc_id>/download/
  document-attach           POST /api/v1/documents/<uuid:doc_id>/attach/
  document-versions         GET  /api/v1/documents/<uuid:doc_id>/versions/
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from io import BytesIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.documents.models import (
    Document,
    DocumentAccessToken,
    DocumentAttachment,
    DocumentCategory,
)

User = get_user_model()

# ─────────────────────────────────────────────────────────────────────────────
# Shared CIVICOS settings block.
# Applied via @override_settings to test classes whose code paths read CIVICOS
# keys (serializer size limit, proxy threshold, token TTL).
# ─────────────────────────────────────────────────────────────────────────────

_CIVICOS = {
    "CLAMAV_HOST": "",
    "CLAMAV_REQUIRED": False,
    "ALLOWED_UPLOAD_MIME_TYPES": ["application/pdf", "image/jpeg", "image/png"],
    "DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES": 10 * 1024 * 1024,   # 10 MiB
    "DOCUMENT_MAX_STAFF_UPLOAD_BYTES": 50 * 1024 * 1024,     # 50 MiB
    "DOCUMENT_PRESIGNED_POST_TTL_SECONDS": 900,
    "DOCUMENT_ACCESS_TOKEN_TTL_SECONDS": 300,
    "DOCUMENT_PROXY_MAX_BYTES": 1 * 1024 * 1024,             # 1 MiB
    "DOCUMENT_ZIP_MAX_ENTRIES": 1000,
    "DOCUMENT_ZIP_MAX_RATIO": 100,
    "MAGIC_BYTES_REQUIRED": False,
    "AUDIT_LOG_RETENTION_DAYS": 2555,
    "MAX_UPLOAD_SIZE": 10 * 1024 * 1024,
    "MAX_LOGIN_ATTEMPTS": 5,
    "GC_NOTIFY_API_KEY": "",
}

# ─────────────────────────────────────────────────────────────────────────────
# Module-level counter for unique slug / email generation
# ─────────────────────────────────────────────────────────────────────────────

_CTR: int = 0


def _uid() -> str:
    global _CTR
    _CTR += 1
    return str(_CTR)


# ─────────────────────────────────────────────────────────────────────────────
# Shared test helpers
# ─────────────────────────────────────────────────────────────────────────────


def _token_auth(user) -> str:
    """Return a DRF Token auth header value for *user*."""
    token, _ = Token.objects.get_or_create(user=user)
    return f"Token {token.key}"


def _make_user(**kwargs):
    n = _uid()
    return User.objects.create_user(
        email=f"api_test_{n}@example.com",
        password="testpass123",
        **kwargs,
    )


def _make_staff(**kwargs):
    """Create a user with ``is_staff=True``."""
    return _make_user(is_staff=True, **kwargs)


def _make_category(**kwargs):
    n = _uid()
    return DocumentCategory.objects.create(
        name_en=f"API Test Category {n}",
        name_fr=f"Catégorie de test API {n}",
        slug=f"api-cat-{n}",
        allowed_mime_types=["application/pdf"],
        min_retention_days=730,
        max_retention_days=2555,
        **kwargs,
    )


def _make_document(user, category, **kwargs):
    doc_uuid = uuid.uuid4()
    kwargs.setdefault("scan_status", Document.ScanStatus.ACTIVE)
    kwargs.setdefault("size_bytes", 8_192)
    return Document.objects.create(
        uploaded_by=user,
        category=category,
        original_filename="test-document.pdf",
        _storage_key=f"documents/active/{doc_uuid}/{uuid.uuid4().hex}.bin",
        mime_type="application/pdf",
        **kwargs,
    )


def _make_access_token(
    user,
    doc,
    *,
    ttl_seconds: int = 300,
    used: bool = False,
    expired: bool = False,
) -> DocumentAccessToken:
    """
    Create a DocumentAccessToken directly in the DB.

    Args:
        user:        The user the token is issued to.
        doc:         The document the token grants access to.
        ttl_seconds: Seconds until expiry (ignored when expired=True).
        used:        If True, set used_at to now (token already consumed).
        expired:     If True, set expires_at in the past.
    """
    if expired:
        expires_at = timezone.now() - timedelta(seconds=1)
    else:
        expires_at = timezone.now() + timedelta(seconds=ttl_seconds)

    # Two uuid4.hex calls yield exactly 64 hex characters.
    token_value = uuid.uuid4().hex + uuid.uuid4().hex

    token = DocumentAccessToken.objects.create(
        document=doc,
        issued_to=user,
        token=token_value,
        expires_at=expires_at,
    )

    if used:
        token.used_at = timezone.now()
        token.save(update_fields=["used_at"])

    return token


def _grant_perm(user, codename) -> "User":
    """Grant a Document model permission to *user* and return a refreshed instance."""
    ct = ContentType.objects.get_for_model(Document)
    perm, _ = Permission.objects.get_or_create(
        codename=codename,
        content_type=ct,
        defaults={"name": f"Can {codename}"},
    )
    user.user_permissions.add(perm)
    # Re-fetch to clear the permission cache on the user instance.
    return User.objects.get(pk=user.pk)


# ─────────────────────────────────────────────────────────────────────────────
# 7.1  POST /api/v1/documents/request-upload/
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=_CIVICOS)
class DocumentRequestUploadAPITests(TestCase):
    """
    Tests for DocumentRequestUploadView (spec §18 endpoint 7.1).

    The service (validate_upload_request) is always mocked — it touches S3.
    The serializer validation (category slug DB check, size limit) uses real DB.
    """

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.client = APIClient()
        self.url = reverse("api-v1:document-request-upload")
        # Baseline valid payload
        self.valid_payload = {
            "category_slug": self.cat.slug,
            "original_filename": "my-file.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 1024,
        }
        # Canonical mock return value — storage_key deliberately absent (S1 invariant)
        self._mock_result = {
            "doc_id": str(uuid.uuid4()),
            "upload_url": "https://s3.example.com/bucket/",
            "upload_fields": {
                "key": "documents/quarantine/abc/def.bin",
                "AWSAccessKeyId": "AKIAIOSFODNN7EXAMPLE",
            },
            "expires_at": "2099-01-01T00:00:00+00:00",
        }

    def test_201_success(self):
        """Valid payload → 201 with correct response structure."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        with patch(
            "apps.api.documents.views.validate_upload_request",
            return_value=self._mock_result,
        ):
            response = self.client.post(self.url, self.valid_payload, format="json")
        self.assertEqual(response.status_code, 201)

    def test_201_response_contains_required_fields(self):
        """201 body must contain doc_id, upload_url, upload_fields, expires_at."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        with patch(
            "apps.api.documents.views.validate_upload_request",
            return_value=self._mock_result,
        ):
            response = self.client.post(self.url, self.valid_payload, format="json")
        self.assertEqual(response.status_code, 201)
        for field in ("doc_id", "upload_url", "upload_fields", "expires_at"):
            self.assertIn(field, response.data, msg=f"Missing field: {field}")

    def test_s1_storage_key_absent_from_201_response(self):
        """S1: storage_key must NEVER appear in the 201 response body."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        with patch(
            "apps.api.documents.views.validate_upload_request",
            return_value=self._mock_result,
        ):
            response = self.client.post(self.url, self.valid_payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("storage_key", response.data)
        self.assertNotIn("_storage_key", response.data)
        # Also check raw bytes (catches accidental serialization of nested objects)
        body = response.content.decode()
        self.assertNotIn('"storage_key"', body)
        self.assertNotIn('"_storage_key"', body)

    def test_400_missing_required_fields(self):
        """Empty body → 400 with field-level errors."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_400_unknown_category_slug(self):
        """Unknown category slug → 400 (serializer DB check)."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        payload = dict(self.valid_payload, category_slug="does-not-exist-slug")
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 400)
        # civicos_exception_handler wraps validation errors under error.details
        self.assertIn("category_slug", response.data["error"]["details"])

    def test_400_size_bytes_too_large(self):
        """File size exceeding DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES → 400."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        # 11 MiB > 10 MiB configured limit
        payload = dict(self.valid_payload, size_bytes=11 * 1024 * 1024)
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("size_bytes", response.data["error"]["details"])

    def test_403_service_permission_denied(self):
        """Service raises Django PermissionDenied → 403 (converted at view boundary)."""
        from django.core.exceptions import PermissionDenied as DjangoPermissionDenied

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        with patch(
            "apps.api.documents.views.validate_upload_request",
            side_effect=DjangoPermissionDenied("upload not allowed"),
        ):
            response = self.client.post(self.url, self.valid_payload, format="json")
        self.assertEqual(response.status_code, 403)

    def test_401_unauthenticated(self):
        """A5: No credentials → 401 (no session auth in _AUTH → not 302)."""
        response = self.client.post(self.url, self.valid_payload, format="json")
        self.assertEqual(response.status_code, 401)


# ─────────────────────────────────────────────────────────────────────────────
# 7.2  POST /api/v1/documents/{doc_id}/confirm-upload/
# ─────────────────────────────────────────────────────────────────────────────


class DocumentConfirmUploadAPITests(TestCase):
    """
    Tests for DocumentConfirmUploadView (spec §18 endpoint 7.2).

    confirm_upload() is always mocked — it opens storage and triggers Celery.
    """

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        # A placeholder doc_id (views pass it to the mocked service)
        self.doc_id = uuid.uuid4()
        self.url = reverse("api-v1:document-confirm-upload", args=[self.doc_id])
        self.client = APIClient()

    def test_200_success_scan_status_scanning(self):
        """confirm_upload succeeds → 200 with doc_id and scan_status."""
        mock_doc = MagicMock()
        mock_doc.pk = self.doc_id
        mock_doc.scan_status = Document.ScanStatus.SCANNING

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        with patch(
            "apps.api.documents.views.confirm_upload",
            return_value=mock_doc,
        ):
            response = self.client.post(self.url, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["doc_id"], str(self.doc_id))
        self.assertEqual(response.data["scan_status"], Document.ScanStatus.SCANNING)

    def test_400_service_validation_error(self):
        """confirm_upload raises Django ValidationError → 400."""
        from django.core.exceptions import ValidationError as DjangoValidationError

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        with patch(
            "apps.api.documents.views.confirm_upload",
            side_effect=DjangoValidationError("zip bomb detected"),
        ):
            response = self.client.post(self.url, format="json")

        self.assertEqual(response.status_code, 400)

    def test_404_idor_wrong_owner(self):
        """confirm_upload raises Http404 for non-owned doc → 404."""
        from django.http import Http404

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        with patch(
            "apps.api.documents.views.confirm_upload",
            side_effect=Http404,
        ):
            response = self.client.post(self.url, format="json")

        self.assertEqual(response.status_code, 404)

    def test_404_nonexistent_doc(self):
        """confirm_upload raises Http404 for non-existent doc_id → 404."""
        from django.http import Http404

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        with patch(
            "apps.api.documents.views.confirm_upload",
            side_effect=Http404,
        ):
            response = self.client.post(self.url, format="json")

        self.assertEqual(response.status_code, 404)

    def test_401_unauthenticated(self):
        """A5: No credentials → 401."""
        response = self.client.post(self.url, format="json")
        self.assertEqual(response.status_code, 401)


# ─────────────────────────────────────────────────────────────────────────────
# 7.3  GET /api/v1/documents/{doc_id}/  (Detail)
# ─────────────────────────────────────────────────────────────────────────────


class DocumentDetailAPITests(TestCase):
    """
    Tests for DocumentDetailDeleteView.get() (spec §18 endpoint 7.3).

    No service mocks needed — the view only hits the DB.
    DocumentSerializer security gates (scan_engine_result, uploaded_by_id) are
    tested here because they live in the serializer, not the service layer.
    """

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)
        self.url = reverse("api-v1:document-detail", args=[self.doc.pk])
        self.client = APIClient()

    def test_200_returns_all_spec_fields(self):
        """GET on own document → 200 with all required spec fields."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        expected_fields = [
            "doc_id", "category_slug", "category_name",
            "original_filename", "mime_type", "size_bytes",
            "scan_status", "scan_status_display",
            "version_number", "is_latest_version",
            "security_classification", "security_classification_display",
            "uploaded_by_id", "is_on_legal_hold", "scan_engine_result",
            "description", "expires_at", "retain_until",
            "created_at", "updated_at",
        ]
        for field in expected_fields:
            self.assertIn(field, response.data, msg=f"Missing field: {field}")

    def test_s1_storage_key_absent_from_response(self):
        """S1: storage_key and _storage_key must NEVER appear in the 200 response."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        # Check the parsed response dict (catches accidental extra fields)
        self.assertNotIn("storage_key", response.data)
        self.assertNotIn("_storage_key", response.data)

        # Check the raw response bytes (catches serialisation of nested objects)
        body = response.content.decode()
        self.assertNotIn('"storage_key"', body)
        self.assertNotIn('"_storage_key"', body)

        # The raw storage key value itself must also not leak
        self.assertNotIn(self.doc._storage_key, body)

    def test_s3_uploaded_by_id_is_pk_not_email(self):
        """S3: uploaded_by_id must be the user's PK string, never email or username."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        uploaded_by_id = str(response.data["uploaded_by_id"])
        # Must not be an email address (primary PIPEDA concern)
        self.assertNotIn("@", uploaded_by_id, "uploaded_by_id must not contain an email")
        # Must be a non-empty string
        self.assertTrue(uploaded_by_id, "uploaded_by_id must be non-empty")
        # Must match the actual pk of the uploader
        self.assertEqual(uploaded_by_id, str(self.user.pk))

    def test_s2_scan_engine_result_hidden_from_citizen(self):
        """S2: QUARANTINED doc — scan_engine_result is null for citizen uploader."""
        quarantined_doc = _make_document(
            self.user,
            self.cat,
            scan_status=Document.ScanStatus.QUARANTINED,
            scan_engine_result="FOUND: Eicar-Test-Signature",
        )
        url = reverse("api-v1:document-detail", args=[quarantined_doc.pk])

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Citizen must never see the raw scan result
        self.assertIsNone(response.data["scan_engine_result"])

    def test_s2_scan_engine_result_shown_to_quarantine_staff(self):
        """S2: QUARANTINED doc — scan_engine_result visible to view_quarantined staff."""
        quarantined_doc = _make_document(
            self.user,
            self.cat,
            scan_status=Document.ScanStatus.QUARANTINED,
            scan_engine_result="FOUND: Eicar-Test-Signature",
        )
        url = reverse("api-v1:document-detail", args=[quarantined_doc.pk])

        # Staff with view_quarantined + view_all_documents (coordinator bypass)
        staff = _make_staff()
        staff = _grant_perm(staff, "view_quarantined")
        staff = _grant_perm(staff, "view_all_documents")

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(staff))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.data["scan_engine_result"])
        self.assertIn("Eicar-Test-Signature", response.data["scan_engine_result"])

    def test_s4_404_wrong_owner_idor(self):
        """S4: IDOR — a different user's document returns 404, not 403."""
        attacker = _make_user()
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(attacker))
        response = self.client.get(self.url)
        # Must be 404 — not 403 (403 reveals document existence)
        self.assertEqual(response.status_code, 404)

    def test_404_not_found(self):
        """Non-existent UUID → 404."""
        url = reverse("api-v1:document-detail", args=[uuid.uuid4()])
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_a5_401_unauthenticated(self):
        """A5: No credentials → 401."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)


# ─────────────────────────────────────────────────────────────────────────────
# 7.4  GET /api/v1/documents/{doc_id}/download/
# ─────────────────────────────────────────────────────────────────────────────


class DocumentDownloadInitAPITests(TestCase):
    """
    Tests for DocumentDownloadInitView (spec §18 endpoint 7.4).

    issue_access_token() is real (DB-only); no storage access is needed here.
    The redemption URL is built using the test client's base URL (http://testserver).
    """

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat, scan_status=Document.ScanStatus.ACTIVE)
        self.url = reverse("api-v1:document-download", args=[self.doc.pk])
        self.client = APIClient()

    def test_200_returns_token_url_and_expires_at(self):
        """GET on ACTIVE own doc → 200 with token_url and expires_at."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("token_url", response.data)
        self.assertIn("expires_at", response.data)

    def test_token_url_contains_dl_path(self):
        """Returned token_url must point to the /dl/ redemption endpoint."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        token_url = response.data["token_url"]
        self.assertIn("/api/v1/documents/dl/", token_url)

    def test_token_url_does_not_contain_storage_key(self):
        """S1: The token_url must not contain any part of the storage key."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        token_url = response.data["token_url"]
        self.assertNotIn(self.doc._storage_key, token_url)
        self.assertNotIn("storage_key", token_url)

    def test_404_scanning_document(self):
        """Scan gate: SCANNING document → 404 (scan not complete, unavailable)."""
        scanning_doc = _make_document(
            self.user, self.cat, scan_status=Document.ScanStatus.SCANNING
        )
        url = reverse("api-v1:document-download", args=[scanning_doc.pk])
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_404_wrong_owner(self):
        """S4: IDOR — requesting token for another user's document → 404."""
        other_user = _make_user()
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(other_user))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_404_deleted_document(self):
        """Soft-deleted document (deleted_at set) → 404."""
        self.doc.deleted_at = timezone.now()
        self.doc.scan_status = Document.ScanStatus.DELETED
        self.doc.save(update_fields=["deleted_at", "scan_status", "updated_at"])

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_a5_401_unauthenticated(self):
        """A5: No credentials → 401."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)


# ─────────────────────────────────────────────────────────────────────────────
# 7.5  GET /api/v1/documents/dl/{token}/
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=_CIVICOS)
class DocumentTokenRedeemAPITests(TestCase):
    """
    Tests for DocumentTokenRedeemView (spec §18 endpoint 7.5).

    consume_access_token() is real (DB-only).
    default_storage.open() is mocked for small-file tests (avoid S3 calls).
    generate_presigned_download_url() is mocked for large-file tests.

    DOCUMENT_PROXY_MAX_BYTES = 1 MiB (from _CIVICOS).
    Small doc: size_bytes=8192 (≤ 1 MiB) → FileResponse (200).
    Large doc: size_bytes=2*1024*1024 (> 1 MiB) → redirect (302).
    """

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()

        # Small document (≤ proxy threshold) — triggers FileResponse path
        self.small_doc = _make_document(
            self.user, self.cat,
            scan_status=Document.ScanStatus.ACTIVE,
            size_bytes=8_192,
        )
        # Large document (> proxy threshold) — triggers redirect path
        self.large_doc = _make_document(
            self.user, self.cat,
            scan_status=Document.ScanStatus.ACTIVE,
            size_bytes=2 * 1024 * 1024,
        )
        self.client = APIClient()

    def test_small_file_returns_200(self):
        """Small file (≤ 1 MiB): consume token → 200 file stream via Django proxy."""
        token = _make_access_token(self.user, self.small_doc)
        url = reverse("api-v1:api-token-redeem", args=[token.token])

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        with patch("apps.api.documents.views.default_storage") as mock_storage:
            mock_storage.open.return_value = BytesIO(b"PDF content here")
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

    def test_large_file_returns_302_redirect(self):
        """Large file (> 1 MiB): consume token → 302 redirect to presigned URL."""
        token = _make_access_token(self.user, self.large_doc)
        url = reverse("api-v1:api-token-redeem", args=[token.token])
        presigned_url = "https://s3.example.com/bucket/doc?X-Amz-Signature=fake"

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        with patch(
            "apps.api.documents.views.generate_presigned_download_url",
            return_value=presigned_url,
        ):
            response = self.client.get(url)

        self.assertEqual(response.status_code, 302)

    def test_large_file_302_location_no_storage_key(self):
        """S1: 302 Location header must not contain the raw storage key."""
        token = _make_access_token(self.user, self.large_doc)
        url = reverse("api-v1:api-token-redeem", args=[token.token])
        presigned_url = "https://s3.example.com/bucket/signed-url?param=value"

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        with patch(
            "apps.api.documents.views.generate_presigned_download_url",
            return_value=presigned_url,
        ):
            response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        location = response.get("Location", "")
        # Presigned URL must not contain the raw storage key
        self.assertNotIn(self.large_doc._storage_key, location)
        self.assertNotIn("storage_key", location)

    def test_s1_storage_key_absent_from_small_file_body(self):
        """S1: FileResponse streaming body must not contain the storage key string."""
        token = _make_access_token(self.user, self.small_doc)
        url = reverse("api-v1:api-token-redeem", args=[token.token])

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        with patch("apps.api.documents.views.default_storage") as mock_storage:
            mock_storage.open.return_value = BytesIO(b"clean PDF bytes")
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        # FileResponse is a streaming response — consume via streaming_content
        body = b"".join(response.streaming_content).decode(errors="replace")
        self.assertNotIn(self.small_doc._storage_key, body)

    def test_invalid_token_returns_404(self):
        """Token not in DB → 404 (consume_access_token raises Http404)."""
        fake_token = "f" * 64
        url = reverse("api-v1:api-token-redeem", args=[fake_token])

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_expired_token_returns_404(self):
        """Expired token (expires_at in past) → 404."""
        token = _make_access_token(self.user, self.small_doc, expired=True)
        url = reverse("api-v1:api-token-redeem", args=[token.token])

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_used_token_returns_404(self):
        """Already-consumed token (used_at is set) → 404."""
        token = _make_access_token(self.user, self.small_doc, used=True)
        url = reverse("api-v1:api-token-redeem", args=[token.token])

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_wrong_user_returns_404(self):
        """Token issued to user A — user B attempting to redeem → 404 (IDOR guard)."""
        other_user = _make_user()
        token = _make_access_token(self.user, self.small_doc)
        url = reverse("api-v1:api-token-redeem", args=[token.token])

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(other_user))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_a5_401_unauthenticated(self):
        """A5: No credentials → 401."""
        token = _make_access_token(self.user, self.small_doc)
        url = reverse("api-v1:api-token-redeem", args=[token.token])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 401)


# ─────────────────────────────────────────────────────────────────────────────
# 7.6  GET /api/v1/documents/?attached_to=…&object_id=…
# ─────────────────────────────────────────────────────────────────────────────


class DocumentAttachedListAPITests(TestCase):
    """
    Tests for DocumentAttachedListView (spec §18 endpoint 7.6).

    Requires is_staff=True (IsStaff permission) AND view_all_documents permission.
    Uses DocumentCategory as the "attached-to" target object (available in tests,
    has an integer pk that fits in object_id max_length=50).
    """

    def setUp(self):
        self.staff = _make_staff()
        self.staff = _grant_perm(self.staff, "view_all_documents")

        self.owner = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.owner, self.cat)

        # Create an attachment — document attached to the category itself
        target_ct = ContentType.objects.get_for_model(DocumentCategory)
        self.attachment = DocumentAttachment.objects.create(
            document=self.doc,
            content_type=target_ct,
            object_id=str(self.cat.pk),
            attachment_role="supporting_evidence",
            note="",
            attached_by=self.staff,
        )

        # attached_to query parameter: "app_label.model" (ContentType convention)
        self.attached_to_param = "documents.documentcategory"
        self.base_url = reverse("api-v1:document-attached-list")
        self.url = (
            f"{self.base_url}"
            f"?attached_to={self.attached_to_param}"
            f"&object_id={self.cat.pk}"
        )
        self.client = APIClient()

    def test_200_staff_gets_paginated_list(self):
        """Staff with view_all_documents → 200 with paginated results."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.staff))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("results", response.data)
        self.assertIn("count", response.data)
        self.assertGreaterEqual(response.data["count"], 1)

    def test_200_results_contain_attachment_fields(self):
        """Paginated results contain DocumentAttachmentSerializer fields."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.staff))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        first = response.data["results"][0]
        for field in ("attachment_id", "document", "attached_to_type", "attached_to_id",
                      "attachment_role", "note", "created_at"):
            self.assertIn(field, first, msg=f"Missing attachment field: {field}")

    def test_403_citizen_cannot_access(self):
        """Non-staff citizen → 403 (IsStaff permission fails)."""
        citizen = _make_user()
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(citizen))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_400_malformed_attached_to(self):
        """attached_to without a dot → 400 (can't split into app_label.model)."""
        url = f"{self.base_url}?attached_to=nodot&object_id=1"
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.staff))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)

    def test_400_unknown_content_type(self):
        """attached_to references unknown app.model → 400."""
        url = f"{self.base_url}?attached_to=fakeapp.fakemodel&object_id=1"
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.staff))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)

    def test_a5_401_unauthenticated(self):
        """A5: No credentials → 401."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)


# ─────────────────────────────────────────────────────────────────────────────
# 7.7  POST /api/v1/documents/{doc_id}/attach/
# ─────────────────────────────────────────────────────────────────────────────


class DocumentAttachAPITests(TestCase):
    """
    Tests for DocumentAttachView (spec §18 endpoint 7.7).

    Requires is_staff=True (IsStaff) AND upload_staff_document permission.
    Uses DocumentCategory as the target content type.
    """

    def setUp(self):
        self.staff = _make_staff()
        self.staff = _grant_perm(self.staff, "upload_staff_document")

        self.owner = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.owner, self.cat)

        self.url = reverse("api-v1:document-attach", args=[self.doc.pk])

        target_ct = ContentType.objects.get_for_model(DocumentCategory)
        self.valid_payload = {
            "content_type": f"{target_ct.app_label}.{target_ct.model}",
            "object_id": str(self.cat.pk),
            "attachment_role": "supporting_evidence",
            "note": "Optional staff note",
        }
        self.client = APIClient()

    def test_a3_201_creates_attachment(self):
        """A3: Successful attach → 201 with attachment_id; DB record exists."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.staff))
        response = self.client.post(self.url, self.valid_payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertIn("attachment_id", response.data)

        # Verify the DB record was created (audit trail)
        attachment_id = response.data["attachment_id"]
        self.assertTrue(
            DocumentAttachment.objects.filter(pk=attachment_id).exists(),
            "Attachment DB record must exist after 201 response",
        )

    def test_403_missing_upload_staff_document_perm(self):
        """Staff without upload_staff_document perm → 403."""
        unprivileged_staff = _make_staff()  # is_staff=True but no upload_staff_document
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(unprivileged_staff))
        response = self.client.post(self.url, self.valid_payload, format="json")
        self.assertEqual(response.status_code, 403)

    def test_403_citizen_is_blocked(self):
        """Citizen (is_staff=False) → 403 from IsStaff permission."""
        citizen = _make_user()
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(citizen))
        response = self.client.post(self.url, self.valid_payload, format="json")
        self.assertEqual(response.status_code, 403)

    def test_404_nonexistent_doc(self):
        """Non-existent doc_id → 404 (get_object_or_404 in view)."""
        url = reverse("api-v1:document-attach", args=[uuid.uuid4()])
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.staff))
        response = self.client.post(url, self.valid_payload, format="json")
        self.assertEqual(response.status_code, 404)

    def test_400_missing_content_type(self):
        """Missing content_type in body → 400 with validation error."""
        payload = dict(self.valid_payload)
        del payload["content_type"]
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.staff))
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 400)
        # civicos_exception_handler wraps validation errors under error.details
        self.assertIn("content_type", response.data["error"]["details"])

    def test_a5_401_unauthenticated(self):
        """A5: No credentials → 401."""
        response = self.client.post(self.url, self.valid_payload, format="json")
        self.assertEqual(response.status_code, 401)


# ─────────────────────────────────────────────────────────────────────────────
# 7.8  DELETE /api/v1/documents/{doc_id}/
# ─────────────────────────────────────────────────────────────────────────────


class DocumentDeleteAPITests(TestCase):
    """
    Tests for DocumentDetailDeleteView.delete() (spec §18 endpoint 7.8).

    soft_delete() is real (DB-only — no S3 access in soft delete).
    The user must hold documents.delete_document permission.
    """

    def setUp(self):
        self.user = _make_user()
        self.user = _grant_perm(self.user, "delete_document")

        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)
        self.url = reverse("api-v1:document-detail", args=[self.doc.pk])
        self.valid_reason = "Retention policy expired — document no longer needed."
        self.client = APIClient()

    def test_a4_204_soft_delete_success(self):
        """A4: DELETE with valid reason → 204; deleted_at is set in DB."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.delete(
            self.url, {"reason": self.valid_reason}, format="json"
        )
        self.assertEqual(response.status_code, 204)

        # A4 invariant: verify permanent audit trail (deleted_at set)
        self.doc.refresh_from_db()
        self.assertIsNotNone(self.doc.deleted_at)
        self.assertEqual(self.doc.scan_status, Document.ScanStatus.DELETED)

    def test_s6_403_legal_hold_blocks_delete(self):
        """S6: Document on legal hold → 403 (unconditional block)."""
        self.doc.legal_hold = True
        self.doc.save(update_fields=["legal_hold", "updated_at"])

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.delete(
            self.url, {"reason": self.valid_reason}, format="json"
        )
        self.assertEqual(response.status_code, 403)

        # Verify the document was NOT deleted
        self.doc.refresh_from_db()
        self.assertIsNone(self.doc.deleted_at)

    def test_403_missing_delete_permission(self):
        """User without delete_document permission → 403 (checked before IDOR)."""
        unprivileged = _make_user()
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(unprivileged))
        response = self.client.delete(
            self.url, {"reason": self.valid_reason}, format="json"
        )
        self.assertEqual(response.status_code, 403)

    def test_400_short_reason(self):
        """Deletion reason shorter than 10 chars → 400."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.delete(
            self.url, {"reason": "short"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        # civicos_exception_handler wraps validation errors under error.details
        self.assertIn("reason", response.data["error"]["details"])

    def test_400_missing_reason(self):
        """Missing reason field entirely → 400."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.delete(self.url, {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("reason", response.data["error"]["details"])

    def test_s4_404_wrong_owner_idor(self):
        """S4: User with delete_document perm trying to delete another user's doc → 404."""
        other_doc_owner = _make_user()
        other_doc = _make_document(other_doc_owner, self.cat)
        url = reverse("api-v1:document-detail", args=[other_doc.pk])

        # self.user has delete_document perm but does NOT own other_doc
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.delete(
            url, {"reason": self.valid_reason}, format="json"
        )
        # Must be 404 — not 403 (403 reveals document existence)
        self.assertEqual(response.status_code, 404)

    def test_a5_401_unauthenticated(self):
        """A5: No credentials → 401."""
        response = self.client.delete(
            self.url, {"reason": self.valid_reason}, format="json"
        )
        self.assertEqual(response.status_code, 401)


# ─────────────────────────────────────────────────────────────────────────────
# 7.9  GET /api/v1/documents/{doc_id}/versions/
# ─────────────────────────────────────────────────────────────────────────────


class DocumentVersionsAPITests(TestCase):
    """
    Tests for DocumentVersionsView (spec §18 endpoint 7.9).

    A three-document version chain is created in setUp:
      v1 — root_document=None (IS the root), is_latest_version=False
      v2 — root_document=v1, is_latest_version=False
      v3 — root_document=v1, is_latest_version=True  (latest)

    Traversal from any chain member must return all 3 documents in ascending
    version_number order (1, 2, 3).
    """

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()

        # Version 1 (the root)
        self.v1 = _make_document(
            self.user, self.cat,
            version_number=1,
            is_latest_version=False,
        )
        # Version 2
        self.v2 = _make_document(
            self.user, self.cat,
            version_number=2,
            root_document=self.v1,
            is_latest_version=False,
        )
        # Version 3 (latest)
        self.v3 = _make_document(
            self.user, self.cat,
            version_number=3,
            root_document=self.v1,
            is_latest_version=True,
        )
        self.client = APIClient()

    def test_200_three_version_chain(self):
        """GET /versions/ on v1 → all 3 versions returned."""
        url = reverse("api-v1:document-versions", args=[self.v1.pk])
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 3)

    def test_200_versions_from_any_chain_member(self):
        """GET /versions/ on v3 (not root) → all 3 versions returned."""
        url = reverse("api-v1:document-versions", args=[self.v3.pk])
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 3)

    def test_versions_ordered_ascending(self):
        """Versions returned in ascending version_number order (1, 2, 3)."""
        url = reverse("api-v1:document-versions", args=[self.v1.pk])
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        version_numbers = [item["version_number"] for item in response.data]
        self.assertEqual(version_numbers, [1, 2, 3])

    def test_200_single_version_document(self):
        """Solo document (no versions) → list with 1 item."""
        solo = _make_document(
            self.user, self.cat,
            version_number=1,
            is_latest_version=True,
        )
        url = reverse("api-v1:document-versions", args=[solo.pk])
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_s4_404_wrong_owner_idor(self):
        """S4: IDOR — traversing another user's version chain → 404."""
        other_user = _make_user()
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(other_user))
        url = reverse("api-v1:document-versions", args=[self.v1.pk])
        response = self.client.get(url)
        # Must be 404 — not 403 (403 reveals document existence)
        self.assertEqual(response.status_code, 404)

    def test_a5_401_unauthenticated(self):
        """A5: No credentials → 401."""
        url = reverse("api-v1:document-versions", args=[self.v1.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 401)
