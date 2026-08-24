"""
apps/documents/tests/test_views_http_contract.py
=================================================
HTTP protocol-contract tests for the Document Management HTML views.

These tests cover the Django HTML view layer (citizen and staff browser
views), NOT the DRF REST API.  For DRF API tests, see test_api.py.

Test classes
------------
CitizenUnauthenticatedTests — unauthenticated requests → 302 redirect to login
UploadInitHttpTests         — GET 200; POST invalid form 422; POST service
                              errors (403, 500).
UploadConfirmHttpTests      — POST success 302 to list; Http404 from service
                              → 404; ValidationError from service → 302 to
                              upload-init.
DownloadHttpTests           — ACTIVE doc → 302 to token-redeem; SCANNING doc
                              → 404; wrong-owner → 404; redirect Location
                              never contains raw storage_key.
TokenRedeemHttpTests        — small file → 200 + Content-Disposition (pk-based,
                              not original_filename); large file → 302 redirect;
                              invalid token → 404; storage_key absent from body.
StaffHttpContractTests      — authenticated no-perm → 403 for all staff views.
LegalHoldHttpTests          — apply → 302; missing action → 422;
                              missing/short reason → 422; service
                              PermissionDenied → 403.

Security invariants verified
-----------------------------
- storage_key never appears in Content-Disposition, Location headers, or
  response body.
- original_filename never appears in Content-Disposition.
- IDOR: non-owned document PKs return 404, not 403.
- Staff views: raise_exception=True → 403 (not redirect) for authenticated
  users lacking the required permission.

User model
----------
CivicOS uses auth_extension.User with email as the unique identifier.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.documents.models import Document, DocumentAccessToken, DocumentCategory

User = get_user_model()

# ---------------------------------------------------------------------------
# Module-level counter for unique emails/slugs
# ---------------------------------------------------------------------------
_CTR: int = 0


def _uid() -> str:
    global _CTR
    _CTR += 1
    return f"{_CTR:06d}"


def _email(prefix: str = "u") -> str:
    return f"{prefix}-{_uid()}@example.com"


def _slug() -> str:
    return f"api-cat-{_uid()}"


# ---------------------------------------------------------------------------
# CIVICOS settings applied via @override_settings to test classes that depend
# on specific CIVICOS values.  TokenRedeemHttpTests uses this directly because
# its small/large-file branching is governed by DOCUMENT_PROXY_MAX_BYTES.
# Other classes mock the service layer entirely and don't need it.
# ---------------------------------------------------------------------------
CIVICOS_SETTINGS = {
    "CLAMAV_HOST": "",
    "CLAMAV_REQUIRED": False,
    "ALLOWED_UPLOAD_MIME_TYPES": [
        "application/pdf",
        "image/jpeg",
        "image/png",
    ],
    "DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES": 10 * 1024 * 1024,
    "DOCUMENT_MAX_STAFF_UPLOAD_BYTES": 50 * 1024 * 1024,
    "DOCUMENT_PRESIGNED_POST_TTL_SECONDS": 900,
    "DOCUMENT_ACCESS_TOKEN_TTL_SECONDS": 300,
    "DOCUMENT_PROXY_MAX_BYTES": 1 * 1024 * 1024,  # 1 MB proxy threshold
    "DOCUMENT_ZIP_MAX_ENTRIES": 1000,
    "DOCUMENT_ZIP_MAX_RATIO": 100,
    "MAGIC_BYTES_REQUIRED": False,
}

# ---------------------------------------------------------------------------
# Shared fixture factories
# ---------------------------------------------------------------------------


def make_user(**kwargs) -> User:
    email = kwargs.pop("email", _email())
    password = kwargs.pop("password", "testpass123!")
    return User.objects.create_user(email=email, password=password, **kwargs)


def make_category(**kwargs) -> DocumentCategory:
    defaults = {
        "name_en": "API Test Category",
        "name_fr": "Catégorie test API",
        "slug": _slug(),
        "allowed_mime_types": ["application/pdf"],
        "max_size_bytes": 0,
        "min_retention_days": 730,
        "max_retention_days": 2555,
        "staff_only": False,
        "is_transitory": False,
    }
    defaults.update(kwargs)
    return DocumentCategory.objects.create(**defaults)


def make_document(user: User, category: DocumentCategory, **kwargs) -> Document:
    defaults = {
        "category": category,
        "uploaded_by": user,
        "original_filename": "test.pdf",
        "_storage_key": f"quarantine/documents/{uuid.uuid4()}.bin",
        "mime_type": "application/pdf",
        "size_bytes": 1024,
        "scan_status": Document.ScanStatus.ACTIVE,
    }
    defaults.update(kwargs)
    return Document.objects.create(**defaults)


def make_token(
    user: User,
    doc: Document,
    *,
    ttl_seconds: int = 300,
) -> DocumentAccessToken:
    """
    Create an unused, non-expired DocumentAccessToken for testing.

    Uses direct ORM creation to bypass issue_access_token() so individual
    download/redeem tests control the token independently.
    """
    return DocumentAccessToken.objects.create(
        document=doc,
        issued_to=user,
        expires_at=timezone.now() + timedelta(seconds=ttl_seconds),
    )


def _get_document_ct() -> ContentType:
    return ContentType.objects.get(app_label="documents", model="document")


def _grant_perm(user: User, codename: str) -> User:
    """Grant a permission and return a re-fetched user (clears perm cache)."""
    ct = _get_document_ct()
    perm, _ = Permission.objects.get_or_create(
        codename=codename,
        content_type=ct,
        defaults={"name": codename.replace("_", " ").capitalize()},
    )
    user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


# ===========================================================================
# 1. CitizenUnauthenticatedTests
# ===========================================================================


class CitizenUnauthenticatedTests(TestCase):
    """
    All citizen-facing endpoints must redirect unauthenticated requests to
    the login page (Django's LoginRequiredMixin default behaviour).

    Expected: HTTP 302 for every citizen URL when no session cookie is present.
    """

    def setUp(self) -> None:
        self.client = Client()
        self.category = make_category()
        self.user = make_user(email=_email("owner"))
        self.doc = make_document(self.user, self.category)

    def test_list_unauthenticated_returns_302(self) -> None:
        response = self.client.get(reverse("documents:list"))
        self.assertEqual(response.status_code, 302)

    def test_upload_init_unauthenticated_returns_302(self) -> None:
        response = self.client.get(reverse("documents:upload-init"))
        self.assertEqual(response.status_code, 302)

    def test_detail_unauthenticated_returns_302(self) -> None:
        response = self.client.get(reverse("documents:detail", args=[self.doc.pk]))
        self.assertEqual(response.status_code, 302)

    def test_download_unauthenticated_returns_302(self) -> None:
        response = self.client.get(reverse("documents:download", args=[self.doc.pk]))
        self.assertEqual(response.status_code, 302)

    def test_token_redeem_unauthenticated_returns_302(self) -> None:
        fake_token = "a" * 64
        response = self.client.get(reverse("documents:token-redeem", args=[fake_token]))
        self.assertEqual(response.status_code, 302)

    def test_confirm_unauthenticated_returns_302(self) -> None:
        response = self.client.post(reverse("documents:upload-confirm", args=[uuid.uuid4()]))
        self.assertEqual(response.status_code, 302)


# ===========================================================================
# 2. UploadInitHttpTests
# ===========================================================================


class UploadInitHttpTests(TestCase):
    """
    HTTP contract for DocumentUploadInitView.

    GET  → 200 HTML with the upload intent form.
    POST → varies by outcome: 422 for invalid form, 403 for permission denied,
           500 for unexpected service error.
    """

    def setUp(self) -> None:
        self.client = Client()
        self.user = make_user(email=_email("citizen"))
        self.category = make_category()
        self.client.force_login(self.user)
        self.url = reverse("documents:upload-init")

    def _valid_post_data(self) -> dict:
        """Minimal valid POST data that passes DocumentUploadIntentForm validation."""
        return {
            "category_slug": self.category.slug,
            "original_filename": "my-document.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 1024,
        }

    def test_get_returns_200_html(self) -> None:
        """GET /upload/ renders the upload intent form with HTTP 200."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.get("Content-Type", ""))

    def test_post_invalid_form_returns_422(self) -> None:
        """
        A POST with missing required fields fails form validation and the
        view re-renders with HTTP 422 (Unprocessable Entity).
        """
        response = self.client.post(self.url, data={})
        self.assertEqual(response.status_code, 422)

    def test_post_permission_denied_returns_403(self) -> None:
        """
        When validate_upload_request raises PermissionDenied (e.g. staff-only
        category), the view catches it and returns HTTP 403.
        """
        with patch("apps.documents.views.citizen.validate_upload_request") as mock_service:
            mock_service.side_effect = PermissionDenied("Staff-only category.")
            response = self.client.post(self.url, data=self._valid_post_data())

        self.assertEqual(response.status_code, 403)

    def test_post_unexpected_error_returns_500(self) -> None:
        """
        An unhandled exception from validate_upload_request returns HTTP 500.
        """
        with patch("apps.documents.views.citizen.validate_upload_request") as mock_service:
            mock_service.side_effect = RuntimeError("Storage backend unavailable.")
            response = self.client.post(self.url, data=self._valid_post_data())

        self.assertEqual(response.status_code, 500)


# ===========================================================================
# 3. UploadConfirmHttpTests
# ===========================================================================


class UploadConfirmHttpTests(TestCase):
    """
    HTTP contract for DocumentUploadConfirmView.

    The confirm step runs after the browser has uploaded directly to storage.
    It calls confirm_upload() which transitions the document from
    PENDING_UPLOAD to SCANNING and dispatches the AV scan task.
    """

    def setUp(self) -> None:
        self.client = Client()
        self.user = make_user(email=_email("citizen"))
        self.client.force_login(self.user)
        # Use a valid UUID as the document pk; the service is mocked so
        # the document doesn't need to exist in the DB for most tests.
        self.doc_pk = uuid.uuid4()
        self.url = reverse("documents:upload-confirm", args=[self.doc_pk])

    def test_post_success_returns_302_to_list(self) -> None:
        """
        A successful confirm redirects the citizen to their document list
        (documents:list).
        """
        with patch("apps.documents.views.citizen.confirm_upload") as mock:
            mock.return_value = None
            response = self.client.post(self.url)

        self.assertRedirects(
            response,
            reverse("documents:list"),
            fetch_redirect_response=False,
        )

    def test_post_http404_from_service_returns_404(self) -> None:
        """
        When confirm_upload raises Http404 (doc not found or IDOR guard
        triggered), the view propagates HTTP 404 to the browser.
        """
        with patch("apps.documents.views.citizen.confirm_upload") as mock:
            mock.side_effect = Http404
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, 404)

    def test_post_validation_error_returns_302_to_upload_init(self) -> None:
        """
        A ValidationError from confirm_upload redirects to upload-init so
        the citizen can restart the upload process.
        """
        with patch("apps.documents.views.citizen.confirm_upload") as mock:
            mock.side_effect = ValidationError("File integrity check failed.")
            response = self.client.post(self.url)

        self.assertRedirects(
            response,
            reverse("documents:upload-init"),
            fetch_redirect_response=False,
        )


# ===========================================================================
# 4. DownloadHttpTests
# ===========================================================================


class DownloadHttpTests(TestCase):
    """
    HTTP contract for DocumentDownloadView.

    The view issues a single-use access token and redirects to the
    token-redeem URL.  The storage key must never appear in Location.
    """

    def setUp(self) -> None:
        self.client = Client()
        self.user = make_user(email=_email("citizen"))
        self.other = make_user(email=_email("other"))
        self.category = make_category()
        self.client.force_login(self.user)

    def test_get_active_doc_returns_302_to_token_redeem(self) -> None:
        """
        GET /docs/<pk>/download/ on an ACTIVE document issues a token and
        redirects to /docs/dl/<token>/.
        """
        doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.ACTIVE)
        token = make_token(self.user, doc)

        with patch("apps.documents.views.citizen.issue_access_token") as mock_issue:
            mock_issue.return_value = token
            response = self.client.get(reverse("documents:download", args=[doc.pk]))

        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        self.assertIn(token.token, location)

    def test_get_scanning_doc_returns_404(self) -> None:
        """
        A document in SCANNING status is not yet available for download.
        Citizens receive HTTP 404 (not 403 — IDOR compliance).
        """
        scanning_doc = make_document(
            self.user, self.category, scan_status=Document.ScanStatus.SCANNING
        )
        response = self.client.get(reverse("documents:download", args=[scanning_doc.pk]))
        self.assertEqual(response.status_code, 404)

    def test_get_wrong_user_doc_returns_404(self) -> None:
        """
        A document owned by a different user returns HTTP 404 to prevent IDOR.
        The citizen must not learn whether the document exists.
        """
        other_doc = make_document(self.other, self.category, scan_status=Document.ScanStatus.ACTIVE)
        response = self.client.get(reverse("documents:download", args=[other_doc.pk]))
        self.assertEqual(response.status_code, 404)

    def test_302_location_does_not_contain_storage_key(self) -> None:
        """
        The redirect Location header must contain the opaque token path
        (/dl/<token>/) and must NOT contain the raw _storage_key value.
        """
        storage_key = f"quarantine/documents/{uuid.uuid4()}.bin"
        doc = make_document(
            self.user,
            self.category,
            _storage_key=storage_key,
            scan_status=Document.ScanStatus.ACTIVE,
        )
        token = make_token(self.user, doc)

        with patch("apps.documents.views.citizen.issue_access_token") as mock_issue:
            mock_issue.return_value = token
            response = self.client.get(reverse("documents:download", args=[doc.pk]))

        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        # Location must reference the token-redeem path.
        self.assertIn("/dl/", location)
        # Location must NEVER contain the raw storage_key.
        self.assertNotIn(storage_key, location)


# ===========================================================================
# 5. TokenRedeemHttpTests
# ===========================================================================


@override_settings(CIVICOS=CIVICOS_SETTINGS)
class TokenRedeemHttpTests(TestCase):
    """
    HTTP contract for DocumentTokenRedeemView.

    Small files (size_bytes ≤ DOCUMENT_PROXY_MAX_BYTES = 1 MB) are proxied
    as FileResponse; large files redirect to a storage-generated URL.

    @override_settings(CIVICOS=CIVICOS_SETTINGS) ensures DOCUMENT_PROXY_MAX_BYTES
    is exactly 1 MB for every test method, regardless of the project test settings.
    This pins the small/large-file branching threshold to a known value so the
    doc size_bytes used in each test (256 B, 512 B, 1 KB, 2 MB) reliably exercises
    the intended code path.

    PIPEDA invariants:
    - Content-Disposition uses document PK, never original_filename.
    - The raw _storage_key value must not appear in response body or headers.
    """

    def setUp(self) -> None:
        self.client = Client()
        self.user = make_user(email=_email("citizen"))
        self.category = make_category()
        self.client.force_login(self.user)

    def _redeem_url(self, token_str: str) -> str:
        return reverse("documents:token-redeem", args=[token_str])

    # ------------------------------------------------------------------
    # 5.1 Small file → 200 with Content-Disposition
    # ------------------------------------------------------------------

    def test_small_file_returns_200_with_content_disposition(self) -> None:
        """
        For a small file (1 KB < 1 MB threshold), the view proxies the content
        and returns HTTP 200 with an attachment Content-Disposition header.
        """
        doc = make_document(
            self.user,
            self.category,
            size_bytes=1024,  # well below the 1 MB proxy threshold
            scan_status=Document.ScanStatus.ACTIVE,
        )
        token = make_token(self.user, doc)

        mock_file = BytesIO(b"PDF content for testing")
        with patch("apps.documents.views.citizen.default_storage") as mock_storage:
            mock_storage.open.return_value = mock_file
            response = self.client.get(self._redeem_url(token.token))

        self.assertEqual(response.status_code, 200)
        content_disp = response.get("Content-Disposition", "")
        self.assertIn("attachment", content_disp)
        # Filename must include the document PK (pk-derived, not original_filename).
        self.assertIn(str(doc.pk), content_disp)

    # ------------------------------------------------------------------
    # 5.2 Content-Disposition uses PK — never original_filename
    # ------------------------------------------------------------------

    def test_content_disposition_uses_pk_not_original_filename(self) -> None:
        """
        PIPEDA: Content-Disposition must use a pk-derived filename such as
        'document-<pk>.bin' and must NEVER expose original_filename, which
        may contain PII (e.g. a citizen's name in the file name).
        """
        doc = make_document(
            self.user,
            self.category,
            original_filename="sensitive-person-name-2024.pdf",
            size_bytes=512,
            scan_status=Document.ScanStatus.ACTIVE,
        )
        token = make_token(self.user, doc)

        mock_file = BytesIO(b"safe content")
        with patch("apps.documents.views.citizen.default_storage") as mock_storage:
            mock_storage.open.return_value = mock_file
            response = self.client.get(self._redeem_url(token.token))

        content_disp = response.get("Content-Disposition", "")
        # The actual filename must never contain the original_filename.
        self.assertNotIn("sensitive-person-name", content_disp)
        # The PK must appear instead.
        self.assertIn(str(doc.pk), content_disp)

    # ------------------------------------------------------------------
    # 5.3 Large file → 302 redirect to storage URL
    # ------------------------------------------------------------------

    def test_large_file_returns_302_redirect(self) -> None:
        """
        A file larger than DOCUMENT_PROXY_MAX_BYTES (1 MB) is served via
        HTTP 302 redirect to a storage-generated (e.g. presigned S3) URL.
        The raw storage_key must not appear in the Location header.
        """
        storage_key = f"quarantine/documents/{uuid.uuid4()}.bin"
        doc = make_document(
            self.user,
            self.category,
            _storage_key=storage_key,
            size_bytes=2 * 1024 * 1024,  # 2 MB > 1 MB threshold
            scan_status=Document.ScanStatus.ACTIVE,
        )
        token = make_token(self.user, doc)

        presigned_url = "https://s3.ca-central-1.amazonaws.com/bucket/object?sig=abc123"
        with patch("apps.documents.views.citizen.default_storage") as mock_storage:
            mock_storage.url.return_value = presigned_url
            response = self.client.get(self._redeem_url(token.token))

        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        # The redirect must point to the presigned URL.
        self.assertIn("s3.ca-central-1.amazonaws.com", location)
        # The raw storage key must not appear in the redirect Location.
        self.assertNotIn(storage_key, location)

    # ------------------------------------------------------------------
    # 5.4 Invalid / unknown token → 404
    # ------------------------------------------------------------------

    def test_invalid_token_returns_404(self) -> None:
        """
        A token value that does not exist in the database returns HTTP 404.
        IDOR: never return 403 or 200 — the existence of the token is not
        revealed.
        """
        fake_token = "f" * 64  # valid hex format, but does not exist in DB
        response = self.client.get(self._redeem_url(fake_token))
        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------
    # 5.5 storage_key absent from response body
    # ------------------------------------------------------------------

    def test_storage_key_not_in_response_content(self) -> None:
        """
        PIPEDA / OWASP: for a proxied (small) file response, the raw
        _storage_key value must not appear anywhere in the response content
        (which should contain only the proxied file bytes).
        """
        storage_key = f"quarantine/documents/{uuid.uuid4()}.bin"
        doc = make_document(
            self.user,
            self.category,
            _storage_key=storage_key,
            size_bytes=256,
            scan_status=Document.ScanStatus.ACTIVE,
        )
        token = make_token(self.user, doc)

        file_bytes = b"safe-file-content-bytes"
        with patch("apps.documents.views.citizen.default_storage") as mock_storage:
            mock_storage.open.return_value = BytesIO(file_bytes)
            response = self.client.get(self._redeem_url(token.token))

        self.assertEqual(response.status_code, 200)
        # Response body must be the file bytes, not the storage key.
        # FileResponse uses streaming_content, not content.
        streamed = b"".join(response.streaming_content)
        self.assertNotIn(storage_key.encode(), streamed)


# ===========================================================================
# 6. StaffHttpContractTests
# ===========================================================================


class StaffHttpContractTests(TestCase):
    """
    Verifies that every staff view returns HTTP 403 — not a redirect —
    when the authenticated user lacks the required permission.

    All staff views set raise_exception=True on PermissionRequiredMixin.
    """

    def setUp(self) -> None:
        self.client = Client()
        self.owner = make_user(email=_email("citizen"))
        self.no_perm_user = make_user(email=_email("noperm"))
        self.category = make_category()
        self.doc = make_document(self.owner, self.category)
        self.client.force_login(self.no_perm_user)

    def test_staff_list_authenticated_no_perm_returns_403(self) -> None:
        """GET /docs/staff/ without view_all_documents → 403."""
        response = self.client.get(reverse("documents:staff-list"))
        self.assertEqual(response.status_code, 403)

    def test_staff_detail_authenticated_no_perm_returns_403(self) -> None:
        """GET /docs/staff/<pk>/ without view_all_documents → 403."""
        response = self.client.get(reverse("documents:staff-detail", args=[self.doc.pk]))
        self.assertEqual(response.status_code, 403)

    def test_legal_hold_view_no_perm_returns_403(self) -> None:
        """GET /docs/staff/<pk>/legal-hold/ without manage_legal_hold → 403."""
        response = self.client.get(reverse("documents:legal-hold", args=[self.doc.pk]))
        self.assertEqual(response.status_code, 403)

    def test_quarantine_list_no_perm_returns_403(self) -> None:
        """GET /docs/staff/quarantine/ without view_quarantined → 403."""
        response = self.client.get(reverse("documents:quarantine-list"))
        self.assertEqual(response.status_code, 403)

    def test_audit_log_no_perm_returns_403(self) -> None:
        """GET /docs/staff/<pk>/audit/ without view_all_documents → 403."""
        response = self.client.get(reverse("documents:audit-log", args=[self.doc.pk]))
        self.assertEqual(response.status_code, 403)


# ===========================================================================
# 7. LegalHoldHttpTests
# ===========================================================================


class LegalHoldHttpTests(TestCase):
    """
    HTTP contract for DocumentLegalHoldView POST submissions.

    Focuses on form-level validation responses and the service-layer
    PermissionDenied surface that is independent of Django's permission
    gate (the view requires manage_legal_hold via PermissionRequiredMixin,
    and the service independently re-checks the same permission).
    """

    def setUp(self) -> None:
        self.client = Client()
        self.owner = make_user(email=_email("citizen"))
        self.staff = make_user(email=_email("staff"), is_staff=True)
        self.category = make_category()
        self.doc = make_document(self.owner, self.category)
        self.staff = _grant_perm(self.staff, "manage_legal_hold")
        self.client.force_login(self.staff)
        self.url = reverse("documents:legal-hold", args=[self.doc.pk])

    # ------------------------------------------------------------------
    # 7.1 Successful apply → 302
    # ------------------------------------------------------------------

    def test_post_apply_returns_302(self) -> None:
        """
        A valid POST to apply a legal hold succeeds and redirects to the
        staff-detail view (HTTP 302 redirect).
        """
        with patch("apps.documents.views.staff.apply_legal_hold") as mock_apply:
            mock_apply.return_value = self.doc
            response = self.client.post(
                self.url,
                data={
                    "action": "apply",
                    "reason": "ATIP litigation hold per TBS guidance.",
                    "confirm_action": True,
                },
            )

        self.assertEqual(response.status_code, 302)

    # ------------------------------------------------------------------
    # 7.2 Missing action field → 422
    # ------------------------------------------------------------------

    def test_post_missing_action_returns_422(self) -> None:
        """
        Omitting the action field fails LegalHoldForm ChoiceField validation
        and the view returns HTTP 422 (Unprocessable Entity).
        """
        response = self.client.post(
            self.url,
            data={
                # "action" intentionally omitted
                "reason": "Valid reason text with enough characters.",
                "confirm_action": True,
            },
        )
        self.assertEqual(response.status_code, 422)

    # ------------------------------------------------------------------
    # 7.3 Missing / too-short reason → 422
    # ------------------------------------------------------------------

    def test_post_missing_reason_returns_422(self) -> None:
        """
        LegalHoldForm.clean_reason() requires at least 10 characters.
        An empty reason fails validation and returns HTTP 422.
        """
        response = self.client.post(
            self.url,
            data={
                "action": "apply",
                "reason": "",  # fails the min-10-chars check
                "confirm_action": True,
            },
        )
        self.assertEqual(response.status_code, 422)

    # ------------------------------------------------------------------
    # 7.4 Service raises PermissionDenied → 403
    # ------------------------------------------------------------------

    def test_post_permission_denied_returns_403(self) -> None:
        """
        When the service layer raises PermissionDenied — e.g. the service
        independently verifies manage_legal_hold and finds it missing after
        an in-flight permission change — the view returns HTTP 403 with an
        error message rather than crashing.
        """
        with patch("apps.documents.views.staff.apply_legal_hold") as mock_apply:
            mock_apply.side_effect = PermissionDenied(
                "Permission revoked between view check and service call."
            )
            response = self.client.post(
                self.url,
                data={
                    "action": "apply",
                    "reason": "Litigation hold required by OPC directive.",
                    "confirm_action": True,
                },
            )

        self.assertEqual(response.status_code, 403)
