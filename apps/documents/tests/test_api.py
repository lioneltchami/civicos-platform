"""
apps/documents/tests/test_api.py
==================================
DRF REST API integration tests for the Document Management BB (spec §18).

These tests cover all API endpoints implemented in apps/api/documents/:

  7.1   POST   /api/v1/documents/request-upload/                    → 201
  7.2   POST   /api/v1/documents/{doc_id}/confirm-upload/           → 200
  7.3   GET    /api/v1/documents/{doc_id}/                          → 200
  7.4   POST   /api/v1/documents/{doc_id}/request-download/         → 200
  7.5   GET    /api/v1/documents/dl/{token}/                        → 200 or 302
  7.6   GET    /api/v1/documents/                                   → 200 (general list)
  7.6b  GET    /api/v1/documents/attachments/?attached_to=…&object_id=… → 200
  7.7   POST   /api/v1/documents/{doc_id}/attach/                   → 201
  7.8   DELETE /api/v1/documents/{doc_id}/                          → 204
  7.9   GET    /api/v1/documents/{doc_id}/versions/                 → 200
  7.10  GET    /api/v1/documents/quarantined/                       → 200

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
  document-request-upload    POST /api/v1/documents/request-upload/
  api-token-redeem           GET  /api/v1/documents/dl/<token>/
  document-quarantined-list  GET  /api/v1/documents/quarantined/
  document-attached-list     GET  /api/v1/documents/attachments/
  document-list              GET  /api/v1/documents/
  document-detail            GET  /api/v1/documents/<uuid:doc_id>/
  document-confirm-upload    POST /api/v1/documents/<uuid:doc_id>/confirm-upload/
  document-request-download  POST /api/v1/documents/<uuid:doc_id>/request-download/
  document-attach            POST /api/v1/documents/<uuid:doc_id>/attach/
  document-versions          GET  /api/v1/documents/<uuid:doc_id>/versions/
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
        ) as mock_confirm:
            response = self.client.post(self.url, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["doc_id"], str(self.doc_id))
        self.assertEqual(response.data["scan_status"], Document.ScanStatus.SCANNING)
        # Regression guard: confirm_upload must receive a str, not a uuid.UUID.
        # Django's <uuid:doc_id> converter yields a uuid.UUID object; the service
        # calls uuid.UUID(doc_id) internally which raises AttributeError on a
        # UUID object, caught as Http404. The view must convert via str(doc_id).
        _call_kwargs = mock_confirm.call_args.kwargs
        self.assertIsInstance(
            _call_kwargs["doc_id"],
            str,
            "confirm_upload must be called with doc_id as str, not uuid.UUID",
        )

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

    Endpoint: POST /api/v1/documents/{doc_id}/request-download/
    Response:  { "download_url": "<token redemption URL>", "expires_at": "<ISO 8601>" }

    issue_access_token() is real (DB-only); no storage access is needed here.
    The redemption URL is built using the test client's base URL (http://testserver).

    Note: changed from GET /download/ → POST /request-download/ per GovStack spec §18
    to reflect that issuing a token is a state-changing operation (creates a
    DocumentAccessToken record + audit entry).  GET must be idempotent; this is not.
    """

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat, scan_status=Document.ScanStatus.ACTIVE)
        self.url = reverse("api-v1:document-request-download", args=[self.doc.pk])
        self.client = APIClient()

    def test_200_returns_download_url_and_expires_at(self):
        """POST on ACTIVE own doc → 200 with download_url and expires_at."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("download_url", response.data)
        self.assertIn("expires_at", response.data)

    def test_download_url_contains_dl_path(self):
        """Returned download_url must point to the /dl/ redemption endpoint."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        download_url = response.data["download_url"]
        self.assertIn("/api/v1/documents/dl/", download_url)

    def test_s1_download_url_does_not_contain_storage_key(self):
        """S1: The download_url must not contain any part of the storage key."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        download_url = response.data["download_url"]
        self.assertNotIn(self.doc._storage_key, download_url)
        self.assertNotIn("storage_key", download_url)

    def test_no_token_url_field_in_response(self):
        """Spec alignment: old 'token_url' field must NOT be present (renamed to download_url)."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("token_url", response.data)

    def test_404_scanning_document(self):
        """Scan gate: SCANNING document → 404 (scan not complete, unavailable)."""
        scanning_doc = _make_document(
            self.user, self.cat, scan_status=Document.ScanStatus.SCANNING
        )
        url = reverse("api-v1:document-request-download", args=[scanning_doc.pk])
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)

    def test_404_wrong_owner(self):
        """S4: IDOR — requesting token for another user's document → 404."""
        other_user = _make_user()
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(other_user))
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 404)

    def test_404_deleted_document(self):
        """Soft-deleted document (deleted_at set) → 404."""
        self.doc.deleted_at = timezone.now()
        self.doc.scan_status = Document.ScanStatus.DELETED
        self.doc.save(update_fields=["deleted_at", "scan_status", "updated_at"])

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 404)

    def test_405_get_not_allowed(self):
        """GET on request-download/ → 405 Method Not Allowed (is POST only)."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_a5_401_unauthenticated(self):
        """A5: No credentials → 401."""
        response = self.client.post(self.url)
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

    def test_expired_token_returns_410(self):
        """Expired token (expires_at in past, not yet used) → 410 Gone (spec §18 §7.5).

        Distinguished from 404 (invalid/not-found) so clients know to request a
        new token rather than assume the document doesn't exist.
        """
        token = _make_access_token(self.user, self.small_doc, expired=True)
        url = reverse("api-v1:api-token-redeem", args=[token.token])

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 410)
        # Response body must carry a meaningful error code
        self.assertEqual(response.data["error"]["code"], "TOKEN_EXPIRED")

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
        """A3: Successful attach → 201 with attachment_id AND doc_id; DB record exists."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.staff))
        response = self.client.post(self.url, self.valid_payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertIn("attachment_id", response.data)
        # spec §18 §7.7: response must also include doc_id so callers know which
        # document was linked without a round-trip GET.
        self.assertIn("doc_id", response.data)
        self.assertEqual(str(response.data["doc_id"]), str(self.doc.pk))

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


# ─────────────────────────────────────────────────────────────────────────────
# 7.6  GET /api/v1/documents/  (general document list)
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=_CIVICOS)
class DocumentListAPITests(TestCase):
    """
    Tests for DocumentListView (spec §18 endpoint 7.6 — general list).

    GET /api/v1/documents/
      Citizens: returns own non-deleted documents.
      Staff with view_all_documents: returns all non-deleted documents.

    Moved from "" to a proper general list; the old attachment-list is now
    at GET /api/v1/documents/attachments/.
    """

    def setUp(self):
        self.user = _make_user()
        self.other_user = _make_user()
        self.cat = _make_category()
        self.doc1 = _make_document(self.user, self.cat)
        self.doc2 = _make_document(self.user, self.cat)
        self.other_doc = _make_document(self.other_user, self.cat)
        self.url = reverse("api-v1:document-list")
        self.client = APIClient()

    def test_200_citizen_sees_own_documents(self):
        """Citizen GET / → 200 list containing only own documents."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.data.get("results", response.data)
        doc_ids = [str(d["doc_id"]) for d in data]
        self.assertIn(str(self.doc1.pk), doc_ids)
        self.assertIn(str(self.doc2.pk), doc_ids)
        # Must NOT see the other user's document
        self.assertNotIn(str(self.other_doc.pk), doc_ids)

    def test_200_citizen_count(self):
        """Citizen sees exactly their 2 documents, not the 3rd (other owner)."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.data.get("results", response.data)
        self.assertEqual(len(data), 2)

    def test_200_coordinator_sees_all_documents(self):
        """Staff with view_all_documents → sees documents from all users."""
        coordinator = _make_staff()
        coordinator = _grant_perm(coordinator, "view_all_documents")
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(coordinator))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.data.get("results", response.data)
        doc_ids = [str(d["doc_id"]) for d in data]
        self.assertIn(str(self.doc1.pk), doc_ids)
        self.assertIn(str(self.other_doc.pk), doc_ids)

    def test_200_excludes_deleted_documents(self):
        """Soft-deleted documents must not appear in the list."""
        self.doc1.deleted_at = timezone.now()
        self.doc1.scan_status = Document.ScanStatus.DELETED
        self.doc1.save(update_fields=["deleted_at", "scan_status", "updated_at"])

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.data.get("results", response.data)
        doc_ids = [str(d["doc_id"]) for d in data]
        self.assertNotIn(str(self.doc1.pk), doc_ids)
        self.assertIn(str(self.doc2.pk), doc_ids)  # doc2 still there

    def test_200_scan_status_filter(self):
        """?scan_status= filters by scan status."""
        scanning_doc = _make_document(
            self.user, self.cat, scan_status=Document.ScanStatus.SCANNING
        )
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(self.url, {"scan_status": "scanning"})
        self.assertEqual(response.status_code, 200)
        data = response.data.get("results", response.data)
        doc_ids = [str(d["doc_id"]) for d in data]
        self.assertIn(str(scanning_doc.pk), doc_ids)
        # doc1 and doc2 are ACTIVE — should NOT appear in scanning filter
        self.assertNotIn(str(self.doc1.pk), doc_ids)

    def test_200_category_filter(self):
        """?category= filters by category slug."""
        other_cat = _make_category()
        other_cat_doc = _make_document(self.user, other_cat)
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(self.url, {"category": other_cat.slug})
        self.assertEqual(response.status_code, 200)
        data = response.data.get("results", response.data)
        doc_ids = [str(d["doc_id"]) for d in data]
        self.assertIn(str(other_cat_doc.pk), doc_ids)
        self.assertNotIn(str(self.doc1.pk), doc_ids)

    def test_s1_storage_key_absent_from_list(self):
        """S1: storage_key / _storage_key must NEVER appear in list response."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertNotIn('"storage_key"', body)
        self.assertNotIn('"_storage_key"', body)

    def test_a5_401_unauthenticated(self):
        """A5: No credentials → 401."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)

    def test_200_paginated_response_structure(self):
        """Gap 6.1: General list response is paginated — count/results/next/previous present."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        # Standard DRF pagination keys must all be present
        self.assertIn("count", response.data)
        self.assertIn("results", response.data)
        self.assertIn("next", response.data)
        self.assertIn("previous", response.data)
        self.assertGreaterEqual(response.data["count"], 1)

    def test_200_staff_without_view_all_sees_only_own_docs(self):
        """Gap 6.2: Staff user WITHOUT view_all_documents sees only own documents.

        is_staff=True does not automatically grant cross-user visibility — that
        requires the explicit view_all_documents permission.  This is a scoping
        regression guard: a new staff hire must not see other users' documents.
        """
        plain_staff = _make_staff()  # is_staff=True but NO view_all_documents perm
        own_doc = _make_document(plain_staff, self.cat)

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(plain_staff))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.data.get("results", response.data)
        doc_ids = [str(d["doc_id"]) for d in data]

        # Must see their own document
        self.assertIn(str(own_doc.pk), doc_ids)
        # Must NOT see other users' documents (doc1, doc2, other_doc are from other users)
        self.assertNotIn(str(self.doc1.pk), doc_ids)
        self.assertNotIn(str(self.other_doc.pk), doc_ids)

    def test_403_quarantined_filter_not_accessible_via_general_list(self):
        """H-1 regression: ?scan_status=quarantined via general list → 403 PermissionDenied.

        The quarantined list is a privileged endpoint at /quarantined/.
        Passing scan_status=quarantined to the general list must be rejected
        unconditionally, even by staff with view_all_documents.
        """
        coordinator = _make_staff()
        coordinator = _grant_perm(coordinator, "view_all_documents")
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(coordinator))
        response = self.client.get(self.url, {"scan_status": "quarantined"})
        # Must be 403 — not 200 (H-1 security fix)
        self.assertEqual(response.status_code, 403)


# ─────────────────────────────────────────────────────────────────────────────
# 7.10  GET /api/v1/documents/quarantined/
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=_CIVICOS)
class DocumentQuarantinedListAPITests(TestCase):
    """
    Tests for DocumentQuarantinedListView (spec §18 endpoint 7.10).

    GET /api/v1/documents/quarantined/
    Requires is_staff=True (IsStaff) AND documents.view_quarantined permission.
    Returns all documents with scan_status=QUARANTINED.
    """

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()

        # Quarantined document (should appear in list)
        self.q_doc = _make_document(
            self.user, self.cat,
            scan_status=Document.ScanStatus.QUARANTINED,
            scan_engine_result="Eicar-Test-Signature",
        )
        # Active document (should NOT appear in list)
        self.active_doc = _make_document(self.user, self.cat, scan_status=Document.ScanStatus.ACTIVE)

        # Staff with correct permission
        self.inspector = _make_staff()
        self.inspector = _grant_perm(self.inspector, "view_quarantined")

        self.url = reverse("api-v1:document-quarantined-list")
        self.client = APIClient()

    def test_200_returns_quarantined_documents(self):
        """Staff with view_quarantined → 200 list containing quarantined doc."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.inspector))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.data.get("results", response.data)
        doc_ids = [str(d["doc_id"]) for d in data]
        self.assertIn(str(self.q_doc.pk), doc_ids)

    def test_200_excludes_active_documents(self):
        """Only QUARANTINED documents appear; active docs are excluded."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.inspector))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.data.get("results", response.data)
        doc_ids = [str(d["doc_id"]) for d in data]
        self.assertNotIn(str(self.active_doc.pk), doc_ids)

    def test_200_scan_engine_result_visible(self):
        """scan_engine_result is exposed to view_quarantined staff (not null)."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.inspector))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.data.get("results", response.data)
        q_entry = next(d for d in data if str(d["doc_id"]) == str(self.q_doc.pk))
        self.assertIsNotNone(q_entry["scan_engine_result"])
        self.assertEqual(q_entry["scan_engine_result"], "Eicar-Test-Signature")

    def test_403_staff_without_view_quarantined_perm(self):
        """Staff without view_quarantined perm → 403."""
        unprivileged_staff = _make_staff()  # is_staff=True but no view_quarantined
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(unprivileged_staff))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_403_citizen_is_blocked(self):
        """Citizen (is_staff=False) → 403 from IsStaff permission."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_403_citizen_with_view_quarantined_perm_still_blocked(self):
        """Gap 1.3: Non-staff citizen with view_quarantined perm → 403 (IsStaff blocks first).

        view_quarantined is a necessary but not sufficient condition — the user
        must ALSO be is_staff=True.  A citizen who somehow receives the DB perm
        must still be denied access.
        """
        citizen_with_perm = _make_user()  # is_staff=False
        citizen_with_perm = _grant_perm(citizen_with_perm, "view_quarantined")
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(citizen_with_perm))
        response = self.client.get(self.url)
        # IsStaff permission class denies before view_quarantined is ever checked
        self.assertEqual(response.status_code, 403)

    def test_200_paginated_response_structure(self):
        """Gap 1.1: Response is paginated — contains count, results, next, previous."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.inspector))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        # Standard DRF pagination keys must all be present
        self.assertIn("count", response.data)
        self.assertIn("results", response.data)
        self.assertIn("next", response.data)
        self.assertIn("previous", response.data)
        self.assertGreaterEqual(response.data["count"], 1)

    def test_200_excludes_deleted_quarantined_documents(self):
        """L-1 regression: soft-deleted quarantined docs must NOT appear in list.

        A document with scan_status=QUARANTINED AND deleted_at set should be
        invisible — the defensive deleted_at__isnull=True filter is required.
        """
        # Soft-delete the quarantined doc
        self.q_doc.deleted_at = timezone.now()
        self.q_doc.save(update_fields=["deleted_at", "updated_at"])

        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.inspector))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.data.get("results", response.data)
        doc_ids = [str(d["doc_id"]) for d in data]
        self.assertNotIn(
            str(self.q_doc.pk), doc_ids,
            "Deleted quarantined document must not appear in quarantined list",
        )

    def test_s1_storage_key_absent_from_quarantined_list(self):
        """S1: storage_key / _storage_key must NEVER appear in quarantined list."""
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.inspector))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertNotIn('"storage_key"', body)
        self.assertNotIn('"_storage_key"', body)

    def test_a5_401_unauthenticated(self):
        """A5: No credentials → 401."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)


# ─────────────────────────────────────────────────────────────────────────────
# 7.5 expired-token 410 — standalone test class
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=_CIVICOS)
class DocumentTokenRedeemExpiredAPITests(TestCase):
    """
    Targeted tests for the expired-token → HTTP 410 Gone behaviour.

    These complement DocumentTokenRedeemAPITests which now uses test_expired_token_returns_410.
    This class pins the exact error shape and differentiates expired from already-used.
    """

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat, scan_status=Document.ScanStatus.ACTIVE)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))

    def test_expired_token_is_410_not_404(self):
        """Expired token → 410, not 404 (distinct per spec §18 §7.5)."""
        token = _make_access_token(self.user, self.doc, expired=True)
        url = reverse("api-v1:api-token-redeem", args=[token.token])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 410)
        self.assertNotEqual(response.status_code, 404)

    def test_expired_token_response_has_error_code(self):
        """410 response body contains {error: {code: TOKEN_EXPIRED, message: ...}}."""
        token = _make_access_token(self.user, self.doc, expired=True)
        url = reverse("api-v1:api-token-redeem", args=[token.token])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 410)
        self.assertIn("error", response.data)
        self.assertEqual(response.data["error"]["code"], "TOKEN_EXPIRED")
        self.assertIn("message", response.data["error"])

    def test_already_used_token_is_404_not_410(self):
        """Already-used token (used_at set) → 404, not 410 (IDOR distinction)."""
        token = _make_access_token(self.user, self.doc, used=True)
        url = reverse("api-v1:api-token-redeem", args=[token.token])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_expired_and_used_token_is_404(self):
        """Token that is both expired AND used → 404 (used_at check wins for IDOR)."""
        token = _make_access_token(self.user, self.doc, expired=True, used=True)
        url = reverse("api-v1:api-token-redeem", args=[token.token])
        response = self.client.get(url)
        # used_at is checked first; 404 is correct (IDOR-safe, not TokenExpiredError)
        self.assertEqual(response.status_code, 404)


# ─────────────────────────────────────────────────────────────────────────────
# Encrypted / password-protected PDF rejection tests (Layer 5b)
# ─────────────────────────────────────────────────────────────────────────────

_SERVICE = "apps.documents.services.upload"


@override_settings(CIVICOS=_CIVICOS)
class EncryptedPdfRejectionTests(TestCase):
    """
    Tests for _check_pdf_encryption() in apps/documents/services/upload.py (Layer 5b).

    confirm_upload() must reject password-protected PDFs because ClamAV cannot
    scan ciphertext.  The storage layer (_verify_file_exists, _read_first_bytes,
    _read_full_file) is patched at the service module level so no filesystem or
    S3 access is needed.
    """

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))

    def _make_pending_doc(self):
        """Create a Document in PENDING_UPLOAD state ready for confirm_upload()."""
        doc_uuid = uuid.uuid4()
        return Document.objects.create(
            id=doc_uuid,
            uploaded_by=self.user,
            category=self.cat,
            original_filename="test.pdf",
            _storage_key=f"documents/quarantine/{doc_uuid}/{uuid.uuid4().hex}.bin",
            mime_type="application/pdf",
            size_bytes=1024,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
        )

    def test_encrypted_pdf_rejected_with_400(self):
        """
        pikepdf raises PasswordError → _check_pdf_encryption raises ValidationError
        → confirm_upload → view catches and returns HTTP 400.
        """
        from django.core.exceptions import ValidationError as DjangoValidationError

        doc = self._make_pending_doc()
        url = reverse("api-v1:document-confirm-upload", args=[doc.pk])

        with patch(f"{_SERVICE}._verify_file_exists"), \
             patch(f"{_SERVICE}._read_first_bytes", return_value=b"%PDF-1.4 dummy"), \
             patch(f"{_SERVICE}._check_pdf_encryption",
                   side_effect=DjangoValidationError(
                       "Password-protected PDFs are not accepted."
                   )):
            response = self.client.post(url)

        self.assertEqual(response.status_code, 400)

    def test_clean_pdf_accepted(self):
        """
        _check_pdf_encryption does NOT raise → confirm_upload advances to SCANNING.

        Patches all storage-layer helpers (_verify_file_exists, _read_first_bytes,
        _read_full_file, _check_pdf_encryption) and the Celery task so no filesystem
        or S3 access is needed.

        Note: _read_full_file MUST be patched because Layer 5b calls it before
        _check_pdf_encryption; without the patch the local-file fallback raises
        ValidationError (FileNotFoundError → 400) before the encryption check runs.
        """
        doc = self._make_pending_doc()
        url = reverse("api-v1:document-confirm-upload", args=[doc.pk])

        with patch(f"{_SERVICE}._verify_file_exists"), \
             patch(f"{_SERVICE}._read_first_bytes", return_value=b"%PDF-1.4 dummy"), \
             patch(f"{_SERVICE}._read_full_file", return_value=b"%PDF-1.4 dummy"), \
             patch(f"{_SERVICE}._check_pdf_encryption"), \
             patch("apps.documents.tasks.scan_document.apply_async"), \
             patch("apps.audit.services.record_event"):
            response = self.client.post(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["scan_status"], "scanning")

    def test_encrypted_pdf_fallback_raw_byte_check(self):
        """
        Without pikepdf (_pikepdf=None), the raw /Encrypt byte heuristic in
        _check_pdf_encryption() catches encrypted PDFs.

        Patches _pikepdf at module level to None and _read_full_file to return
        bytes containing /Encrypt so the heuristic fires.
        """
        doc = self._make_pending_doc()
        url = reverse("api-v1:document-confirm-upload", args=[doc.pk])

        encrypted_pdf_bytes = b"%PDF-1.6\n/Encrypt <</Filter /Standard>>"

        with patch(f"{_SERVICE}._verify_file_exists"), \
             patch(f"{_SERVICE}._read_first_bytes", return_value=encrypted_pdf_bytes), \
             patch(f"{_SERVICE}._read_full_file", return_value=encrypted_pdf_bytes), \
             patch(f"{_SERVICE}._pikepdf", None):
            response = self.client.post(url)

        self.assertEqual(response.status_code, 400)

    def test_non_pdf_skips_encryption_check(self):
        """Gap 7.1: Non-PDF uploads skip _check_pdf_encryption entirely.

        Only files with a .pdf extension are passed through _check_pdf_encryption.
        A JPEG confirm_upload must reach SCANNING state without _check_pdf_encryption
        ever being invoked.

        Verifies that the guard in confirm_upload — which gates the encryption
        check on Path(original_filename).suffix == '.pdf' — is in place.

        Both _validate_magic_bytes and _check_pdf_encryption are patched because:
          - _validate_magic_bytes would reject the JPEG bytes against the PDF-only
            category MIME list if python-magic is installed in the test environment.
          - _check_pdf_encryption is the actual subject of this test (must not be called).
        """
        # Create a category that explicitly allows image/jpeg
        jpeg_cat = DocumentCategory.objects.create(
            name_en="JPEG Test Category",
            name_fr="Catégorie test JPEG",
            slug=f"jpeg-cat-{_uid()}",
            allowed_mime_types=["image/jpeg"],
            min_retention_days=730,
            max_retention_days=2555,
        )
        # Create a pending JPEG document (not a PDF)
        doc_uuid = uuid.uuid4()
        jpeg_doc = Document.objects.create(
            id=doc_uuid,
            uploaded_by=self.user,
            category=jpeg_cat,
            original_filename="photo.jpg",
            _storage_key=f"documents/quarantine/{doc_uuid}/{uuid.uuid4().hex}.bin",
            mime_type="image/jpeg",
            size_bytes=2048,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
        )
        url = reverse("api-v1:document-confirm-upload", args=[jpeg_doc.pk])

        # _check_pdf_encryption must NOT be called for a JPEG.
        # We use a side_effect sentinel so if it IS called, the test will fail.
        _sentinel = Exception("_check_pdf_encryption must NOT be called for non-PDF")

        with patch(f"{_SERVICE}._verify_file_exists"), \
             patch(f"{_SERVICE}._read_first_bytes", return_value=b"\xff\xd8\xff jpeg"), \
             patch(f"{_SERVICE}._read_full_file", return_value=b"\xff\xd8\xff jpeg"), \
             patch(f"{_SERVICE}._validate_magic_bytes", return_value="image/jpeg"), \
             patch(f"{_SERVICE}._check_pdf_encryption", side_effect=_sentinel) as mock_enc, \
             patch("apps.documents.tasks.scan_document.apply_async"), \
             patch("apps.audit.services.record_event"):
            response = self.client.post(url)

        # Confirm_upload should succeed without calling the encryption check
        mock_enc.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["scan_status"], "scanning")
