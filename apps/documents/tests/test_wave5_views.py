"""
apps/documents/tests/test_wave5_views.py
=========================================
Wave 5 view test suite for the Document Management Building Block.

Coverage
--------
- CitizenAuthTests            — unauthenticated access is redirected to login
- CitizenDocumentListTests    — queryset scoping, soft-delete exclusion, versioning
- CitizenDocumentDetailTests  — IDOR 404, storage-key leakage, XSS escaping
- CitizenUploadInitTests      — form render, service dispatch, error surfaces
- CitizenUploadConfirmTests   — service dispatch, redirect, IDOR
- CitizenDownloadTests        — ACTIVE gate, IDOR, token redirect
- CitizenTokenRedeemTests     — token consume, file serve, storage_key never in headers
- StaffAuthTests              — 403 (raise_exception=True) for missing permissions
- StaffDocumentListTests      — all-user visibility, filtering
- StaffDocumentDetailTests    — no storage_key, scan-engine-result gating
- LegalHoldViewTests          — GET form, apply/release services, validation
- QuarantineListTests         — quarantine-only filter, no scan_engine_result
- AuditLogViewTests           — entries rendered
- PIPEDAInvariantTests        — cross-cutting: storage_key, IDOR, PII,
                                scan_engine_result

User model notes
----------------
CivicOS uses a custom user model (``auth_extension.User``) that replaces
``username`` with ``email`` as the unique identifier.  All user creation in
this file uses ``email=...`` rather than ``username=...``.  ``force_login``
is used instead of ``login(username=, password=)`` because the login view
requires MFA which is out of scope here.

Permissions note
----------------
``view_all_documents`` and ``view_quarantined`` are not declared in
``Document.Meta.permissions`` at time of writing.  ``_grant_perm()`` creates
them on the fly in the ``auth_permission`` table using the existing
``documents | document`` ContentType so the Permission objects exist for tests
without requiring a migration.
"""
from __future__ import annotations

import uuid
from io import BytesIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.http import Http404
from django.test import Client, TestCase
from django.urls import reverse

from apps.documents.models import Document, DocumentAccessToken, DocumentCategory

User = get_user_model()

# ---------------------------------------------------------------------------
# Counter used to generate unique emails per test class
# ---------------------------------------------------------------------------
_EMAIL_CTR = 0


def _email(prefix: str = "user") -> str:
    """Return a unique email address for each call."""
    global _EMAIL_CTR
    _EMAIL_CTR += 1
    return f"{prefix}-{_EMAIL_CTR}@example.com"


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def make_category(**kwargs) -> DocumentCategory:
    defaults = dict(
        name_en="Test Category",
        name_fr="Catégorie test",
        slug=f"test-cat-{uuid.uuid4().hex[:8]}",
        allowed_mime_types=["application/pdf"],
        max_size_bytes=0,
        min_retention_days=730,
        max_retention_days=2555,
        staff_only=False,
    )
    defaults.update(kwargs)
    return DocumentCategory.objects.create(**defaults)


def make_user(**kwargs):
    """
    Create a CivicOS user.  Email is required; no username field exists.
    """
    email = kwargs.pop("email", _email())
    password = kwargs.pop("password", "testpass123!")
    return User.objects.create_user(email=email, password=password, **kwargs)


def make_document(user, category: DocumentCategory, **kwargs) -> Document:
    defaults = dict(
        category=category,
        uploaded_by=user,
        original_filename="test.pdf",
        _storage_key=f"quarantine/documents/{uuid.uuid4()}.bin",
        mime_type="application/pdf",
        size_bytes=1024,
        scan_status=Document.ScanStatus.ACTIVE,
    )
    defaults.update(kwargs)
    return Document.objects.create(**defaults)


def _get_document_ct() -> ContentType:
    return ContentType.objects.get(app_label="documents", model="document")


def _grant_perm(user, perm_codename: str):
    """
    Grant a permission to a user and return a fresh DB instance
    (clears Django's per-instance permission cache).

    If the permission does not yet exist in Document.Meta.permissions (e.g.
    ``view_all_documents``, ``view_quarantined``), it is created on the fly
    against the ``documents | document`` ContentType.
    """
    ct = _get_document_ct()
    perm, _ = Permission.objects.get_or_create(
        codename=perm_codename,
        content_type=ct,
        defaults={"name": perm_codename.replace("_", " ").capitalize()},
    )
    user.user_permissions.add(perm)
    # Refresh so the permission cache is cleared.
    return User.objects.get(pk=user.pk)


# ---------------------------------------------------------------------------
# 1. CitizenAuthTests
# ---------------------------------------------------------------------------


class CitizenAuthTests(TestCase):
    """Unauthenticated requests to citizen views must redirect to login."""

    def setUp(self) -> None:
        self.client = Client()
        self.category = make_category()
        self.owner = make_user(email=_email("owner"))
        self.doc = make_document(self.owner, self.category)

    def test_list_requires_login(self) -> None:
        response = self.client.get(reverse("documents:list"))
        self.assertEqual(response.status_code, 302)

    def test_detail_requires_login(self) -> None:
        response = self.client.get(
            reverse("documents:detail", args=[self.doc.pk])
        )
        self.assertEqual(response.status_code, 302)

    def test_upload_init_requires_login(self) -> None:
        response = self.client.get(reverse("documents:upload-init"))
        self.assertEqual(response.status_code, 302)

    def test_download_requires_login(self) -> None:
        response = self.client.get(
            reverse("documents:download", args=[self.doc.pk])
        )
        self.assertEqual(response.status_code, 302)


# ---------------------------------------------------------------------------
# 2. CitizenDocumentListTests
# ---------------------------------------------------------------------------


class CitizenDocumentListTests(TestCase):
    """DocumentListView scopes results to the authenticated citizen."""

    def setUp(self) -> None:
        self.client = Client()
        self.user = make_user(email=_email("citizen"))
        self.other = make_user(email=_email("other"))
        self.category = make_category()
        self.client.force_login(self.user)
        self.url = reverse("documents:list")

    def test_list_200_empty(self) -> None:
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_list_shows_own_documents(self) -> None:
        doc = make_document(self.user, self.category)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        pks = [str(d.pk) for d in response.context["documents"]]
        self.assertIn(str(doc.pk), pks)

    def test_list_excludes_other_users_docs(self) -> None:
        other_doc = make_document(self.other, self.category)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        pks = [str(d.pk) for d in response.context["documents"]]
        self.assertNotIn(str(other_doc.pk), pks)

    def test_list_excludes_soft_deleted(self) -> None:
        from django.utils import timezone
        deleted = make_document(
            self.user, self.category, deleted_at=timezone.now()
        )
        response = self.client.get(self.url)
        pks = [str(d.pk) for d in response.context["documents"]]
        self.assertNotIn(str(deleted.pk), pks)

    def test_list_shows_latest_versions_only(self) -> None:
        # v1 — not latest
        v1 = make_document(
            self.user, self.category,
            version_number=1,
            is_latest_version=False,
        )
        # v2 — latest, same document chain
        v2 = make_document(
            self.user, self.category,
            version_number=2,
            is_latest_version=True,
            root_document=v1,
        )
        response = self.client.get(self.url)
        pks = [str(d.pk) for d in response.context["documents"]]
        self.assertIn(str(v2.pk), pks)
        self.assertNotIn(str(v1.pk), pks)


# ---------------------------------------------------------------------------
# 3. CitizenDocumentDetailTests
# ---------------------------------------------------------------------------


class CitizenDocumentDetailTests(TestCase):
    """DocumentDetailView: ownership enforcement, no storage_key leakage, XSS."""

    def setUp(self) -> None:
        self.client = Client()
        self.user = make_user(email=_email("citizen"))
        self.other = make_user(email=_email("other"))
        self.category = make_category()
        self.doc = make_document(self.user, self.category)
        self.client.force_login(self.user)

    def test_detail_own_doc_200(self) -> None:
        response = self.client.get(
            reverse("documents:detail", args=[self.doc.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_detail_other_user_doc_404(self) -> None:
        """IDOR: other user's document PK must return 404, not 403."""
        other_doc = make_document(self.other, self.category)
        response = self.client.get(
            reverse("documents:detail", args=[other_doc.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_detail_404_for_nonexistent(self) -> None:
        response = self.client.get(
            reverse("documents:detail", args=[uuid.uuid4()])
        )
        self.assertEqual(response.status_code, 404)

    def test_detail_does_not_expose_storage_key(self) -> None:
        """The raw storage_key value must not appear in the response body."""
        storage_key = self.doc._storage_key  # e.g. "quarantine/documents/<uuid>.bin"
        response = self.client.get(
            reverse("documents:detail", args=[self.doc.pk])
        )
        content = response.content.decode()
        self.assertNotIn(storage_key, content)
        self.assertNotIn("quarantine/", content)

    def test_detail_shows_filename_escaped(self) -> None:
        """original_filename containing HTML special chars must be auto-escaped."""
        xss_doc = make_document(
            self.user, self.category,
            original_filename="<script>alert(1)</script>.pdf",
        )
        response = self.client.get(
            reverse("documents:detail", args=[xss_doc.pk])
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # The raw unescaped XSS payload must not appear in the rendered output.
        # The base template contains legitimate <script> tags, so we check for
        # the specific payload string "alert(1)" surrounded by angle brackets,
        # which only the raw (unescaped) filename would produce.
        self.assertNotIn("<script>alert(1)</script>", content)
        # Django auto-escaping converts < to &lt; — verify the safe form appears
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", content)

    # -- H-10: can_download context key tests ----------------------------------

    def test_detail_active_doc_can_download_true(self):
        """can_download=True when scan_status=ACTIVE."""
        self.doc.scan_status = Document.ScanStatus.ACTIVE
        self.doc.save(update_fields=["scan_status"])
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("documents:detail", kwargs={"pk": self.doc.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.context["can_download"], True)

    def test_detail_scanning_doc_can_download_false(self):
        """can_download=False when scan_status=SCANNING."""
        self.doc.scan_status = Document.ScanStatus.SCANNING
        self.doc.save(update_fields=["scan_status"])
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("documents:detail", kwargs={"pk": self.doc.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.context["can_download"], False)

    def test_detail_quarantined_doc_can_download_false(self):
        """can_download=False when scan_status=QUARANTINED."""
        self.doc.scan_status = Document.ScanStatus.QUARANTINED
        self.doc.save(update_fields=["scan_status"])
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("documents:detail", kwargs={"pk": self.doc.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.context["can_download"], False)


# ---------------------------------------------------------------------------
# 4. CitizenUploadInitTests
# ---------------------------------------------------------------------------


class CitizenUploadInitTests(TestCase):
    """DocumentUploadInitView: form render and service dispatch."""

    def setUp(self) -> None:
        self.client = Client()
        self.user = make_user(email=_email("citizen"))
        self.category = make_category(slug="upload-cat")
        self.client.force_login(self.user)
        self.url = reverse("documents:upload-init")

    def _valid_post_data(self) -> dict:
        return {
            "category_slug": "upload-cat",
            "original_filename": "report.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 1024,
            "description": "My annual report",
        }

    def test_get_upload_form_200(self) -> None:
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_post_calls_validate_upload_request(self) -> None:
        doc_id = str(uuid.uuid4())
        mock_result = {
            "doc_id": doc_id,
            "upload_url": "https://s3.example.com/upload",
            "upload_fields": {
                "key": f"quarantine/{doc_id}.bin",
                "AWSAccessKeyId": "AKIATEST",
            },
            "expires_at": "2026-07-05T00:00:00Z",
        }
        with patch(
            "apps.documents.views.citizen.validate_upload_request",
            return_value=mock_result,
        ) as mock_validate:
            response = self.client.post(self.url, data=self._valid_post_data())
        mock_validate.assert_called_once()
        # Successful presign → render presign template (200)
        self.assertEqual(response.status_code, 200)

    def test_post_validation_error_shows_form(self) -> None:
        from django.core.exceptions import ValidationError
        with patch(
            "apps.documents.views.citizen.validate_upload_request",
            side_effect=ValidationError("File type not allowed."),
        ):
            response = self.client.post(self.url, data=self._valid_post_data())
        self.assertEqual(response.status_code, 422)
        content = response.content.decode()
        self.assertIn("File type not allowed.", content)

    def test_post_permission_denied_shows_error(self) -> None:
        from django.core.exceptions import PermissionDenied
        with patch(
            "apps.documents.views.citizen.validate_upload_request",
            side_effect=PermissionDenied("No access."),
        ):
            response = self.client.post(self.url, data=self._valid_post_data())
        self.assertEqual(response.status_code, 403)

    def test_post_no_storage_key_in_response(self) -> None:
        """
        The presign page must not contain the raw _storage_key of any Document
        row.  The S3 form field ``key`` (upload destination path) is permitted
        because it is an S3 object path chosen by the service, not the stored
        ``_storage_key`` of an existing Document.
        """
        doc_id = str(uuid.uuid4())
        existing_doc = make_document(self.user, self.category)
        raw_storage_key = existing_doc._storage_key  # "quarantine/documents/<uuid>.bin"

        mock_result = {
            "doc_id": doc_id,
            "upload_url": "https://s3.example.com/upload",
            "upload_fields": {
                "key": f"quarantine/{doc_id}.bin",
                "AWSAccessKeyId": "AKIATEST",
            },
            "expires_at": "2026-07-05T00:00:00Z",
        }
        with patch(
            "apps.documents.views.citizen.validate_upload_request",
            return_value=mock_result,
        ):
            response = self.client.post(self.url, data=self._valid_post_data())
        self.assertEqual(response.status_code, 200)
        # The _storage_key of the existing doc must not appear in the response
        self.assertNotIn(raw_storage_key, response.content.decode())


# ---------------------------------------------------------------------------
# 5. CitizenUploadConfirmTests
# ---------------------------------------------------------------------------


class CitizenUploadConfirmTests(TestCase):
    """DocumentUploadConfirmView: service dispatch, redirect, IDOR protection."""

    def setUp(self) -> None:
        self.client = Client()
        self.user = make_user(email=_email("citizen"))
        self.other = make_user(email=_email("other"))
        self.category = make_category()
        self.doc = make_document(
            self.user, self.category,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
        )
        self.client.force_login(self.user)

    def _url(self, pk=None) -> str:
        return reverse("documents:upload-confirm", args=[pk or self.doc.pk])

    def test_post_calls_confirm_upload(self) -> None:
        with patch(
            "apps.documents.views.citizen.confirm_upload",
            return_value=self.doc,
        ) as mock_confirm:
            self.client.post(self._url())
        mock_confirm.assert_called_once_with(
            user=self.user, doc_id=str(self.doc.pk)
        )

    def test_post_redirects_to_list(self) -> None:
        with patch(
            "apps.documents.views.citizen.confirm_upload",
            return_value=self.doc,
        ):
            response = self.client.post(self._url())
        self.assertRedirects(
            response, reverse("documents:list"), fetch_redirect_response=False
        )

    def test_post_validation_error_shows_error(self) -> None:
        from django.core.exceptions import ValidationError
        with patch(
            "apps.documents.views.citizen.confirm_upload",
            side_effect=ValidationError("File not found in quarantine."),
        ):
            response = self.client.post(self._url())
        # View redirects to upload-init with an error message on ValidationError
        self.assertRedirects(
            response,
            reverse("documents:upload-init"),
            fetch_redirect_response=False,
        )

    def test_post_http404_from_service(self) -> None:
        with patch(
            "apps.documents.views.citizen.confirm_upload",
            side_effect=Http404,
        ):
            response = self.client.post(self._url())
        self.assertEqual(response.status_code, 404)

    def test_post_wrong_user_404(self) -> None:
        """IDOR: confirming another user's document must return 404."""
        other_doc = make_document(
            self.other, self.category,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
        )
        with patch(
            "apps.documents.views.citizen.confirm_upload",
            side_effect=Http404,
        ):
            response = self.client.post(self._url(pk=other_doc.pk))
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# 6. CitizenDownloadTests
# ---------------------------------------------------------------------------


class CitizenDownloadTests(TestCase):
    """DocumentDownloadView: ACTIVE gate, IDOR, token-redirect."""

    def setUp(self) -> None:
        self.client = Client()
        self.user = make_user(email=_email("citizen"))
        self.other = make_user(email=_email("other"))
        self.category = make_category()
        self.doc = make_document(self.user, self.category)
        self.client.force_login(self.user)

    def _url(self, pk=None) -> str:
        return reverse("documents:download", args=[pk or self.doc.pk])

    def test_download_active_doc_redirects_to_token(self) -> None:
        """ACTIVE own doc → 302 redirect to token-redeem URL."""
        token_hex = "a" * 64
        mock_token = MagicMock()
        mock_token.token = token_hex
        with patch(
            "apps.documents.views.citizen.issue_access_token",
            return_value=mock_token,
        ):
            response = self.client.get(self._url())
        self.assertEqual(response.status_code, 302)
        expected = reverse("documents:token-redeem", args=[token_hex])
        self.assertEqual(response["Location"], expected)

    def test_download_non_active_doc_404(self) -> None:
        """A SCANNING doc is not downloadable → 404."""
        scanning_doc = make_document(
            self.user, self.category,
            scan_status=Document.ScanStatus.SCANNING,
        )
        response = self.client.get(self._url(pk=scanning_doc.pk))
        self.assertEqual(response.status_code, 404)

    def test_download_other_user_doc_404(self) -> None:
        """IDOR: another user's doc → 404."""
        other_doc = make_document(self.other, self.category)
        response = self.client.get(self._url(pk=other_doc.pk))
        self.assertEqual(response.status_code, 404)

    def test_download_nonexistent_doc_404(self) -> None:
        response = self.client.get(self._url(pk=uuid.uuid4()))
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# 7. CitizenTokenRedeemTests
# ---------------------------------------------------------------------------


class CitizenTokenRedeemTests(TestCase):
    """DocumentTokenRedeemView: consume token, serve file, no storage_key leak."""

    def setUp(self) -> None:
        self.client = Client()
        self.user = make_user(email=_email("citizen"))
        self.category = make_category()
        self.doc = make_document(self.user, self.category, size_bytes=512)
        self.client.force_login(self.user)

    def _url(self, token: str) -> str:
        return reverse("documents:token-redeem", args=[token])

    def test_redeem_valid_token_serves_file(self) -> None:
        """Valid token + small file (≤ proxy threshold) → proxied FileResponse."""
        token_hex = "b" * 64
        fake_file = BytesIO(b"PDF content here")
        with (
            patch(
                "apps.documents.views.citizen.consume_access_token",
                return_value=self.doc,
            ),
            patch(
                "apps.documents.views.citizen.default_storage.open",
                return_value=fake_file,
            ),
        ):
            response = self.client.get(self._url(token_hex))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        content_disp = response.get("Content-Disposition", "")
        # Content-Disposition uses doc.pk — NOT original_filename
        self.assertIn(str(self.doc.pk), content_disp)
        self.assertNotIn("test.pdf", content_disp)

    def test_redeem_invalid_token_404(self) -> None:
        """Non-existent token → 404."""
        with patch(
            "apps.documents.views.citizen.consume_access_token",
            side_effect=Http404,
        ):
            response = self.client.get(self._url("0" * 64))
        self.assertEqual(response.status_code, 404)

    def test_redeem_expired_token_404(self) -> None:
        """Expired token (service raises Http404) → 404."""
        with patch(
            "apps.documents.views.citizen.consume_access_token",
            side_effect=Http404,
        ):
            response = self.client.get(self._url("c" * 64))
        self.assertEqual(response.status_code, 404)

    def test_redeem_other_user_token_404(self) -> None:
        """Token issued to a different user → service raises Http404 → 404."""
        with patch(
            "apps.documents.views.citizen.consume_access_token",
            side_effect=Http404,
        ):
            response = self.client.get(self._url("d" * 64))
        self.assertEqual(response.status_code, 404)

    def test_redeem_storage_key_not_in_response_headers(self) -> None:
        """storage_key must not appear in any response header."""
        storage_key = self.doc._storage_key  # raw private field for assertion
        fake_file = BytesIO(b"data")
        with (
            patch(
                "apps.documents.views.citizen.consume_access_token",
                return_value=self.doc,
            ),
            patch(
                "apps.documents.views.citizen.default_storage.open",
                return_value=fake_file,
            ),
        ):
            response = self.client.get(self._url("e" * 64))
        self.assertEqual(response.status_code, 200)
        header_values = " ".join(str(v) for v in response.headers.values())
        self.assertNotIn(storage_key, header_values)
        self.assertNotIn("quarantine/", header_values)


# ---------------------------------------------------------------------------
# 8. StaffAuthTests
# ---------------------------------------------------------------------------


class StaffAuthTests(TestCase):
    """Staff views return 403 (raise_exception=True) for missing permissions."""

    def setUp(self) -> None:
        self.client = Client()
        self.staff = make_user(email=_email("staff"), is_staff=True)
        self.category = make_category()
        self.doc = make_document(self.staff, self.category)
        self.client.force_login(self.staff)

    def test_staff_list_requires_permission(self) -> None:
        response = self.client.get(reverse("documents:staff-list"))
        self.assertEqual(response.status_code, 403)

    def test_staff_detail_requires_permission(self) -> None:
        response = self.client.get(
            reverse("documents:staff-detail", args=[self.doc.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_legal_hold_requires_permission(self) -> None:
        response = self.client.get(
            reverse("documents:legal-hold", args=[self.doc.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_quarantine_requires_permission(self) -> None:
        response = self.client.get(reverse("documents:quarantine-list"))
        self.assertEqual(response.status_code, 403)

    def test_audit_log_requires_permission(self) -> None:
        response = self.client.get(
            reverse("documents:audit-log", args=[self.doc.pk])
        )
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# 8b. AnonymousStaffViewRedirectTests
# ---------------------------------------------------------------------------


class AnonymousStaffViewRedirectTests(TestCase):
    """Anonymous requests to staff views receive 403, not 200 or 404.

    All staff views use ``raise_exception = True``. Under Django's ``AccessMixin``,
    ``handle_no_permission()`` raises ``PermissionDenied`` (→ HTTP 403) whenever
    ``raise_exception`` is True — regardless of whether the user is authenticated.
    ``LoginRequiredMixin`` fires first in the MRO (its ``dispatch`` calls
    ``handle_no_permission()`` for unauthenticated requests), but since
    ``raise_exception=True`` is set on the view, the shared ``handle_no_permission``
    raises instead of redirecting.

    These tests verify that anonymous users are denied access (403) — NOT served
    document data (200) or silently ignored (404).  They lock down the invariant
    that the MRO is wired correctly (LoginRequiredMixin before PermissionRequired)
    and that ``raise_exception=True`` is present on every staff view.
    """

    def setUp(self) -> None:
        self.client = Client()
        self.category = make_category()
        self.owner = make_user(email=_email("owner"))
        self.doc = make_document(self.owner, self.category)

    def _assert_denied(self, url: str) -> None:
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_access_staff_list(self) -> None:
        self._assert_denied(reverse("documents:staff-list"))

    def test_anonymous_cannot_access_staff_detail(self) -> None:
        self._assert_denied(
            reverse("documents:staff-detail", kwargs={"pk": self.doc.pk})
        )

    def test_anonymous_cannot_access_legal_hold(self) -> None:
        self._assert_denied(
            reverse("documents:legal-hold", kwargs={"pk": self.doc.pk})
        )

    def test_anonymous_cannot_access_quarantine_list(self) -> None:
        self._assert_denied(reverse("documents:quarantine-list"))

    def test_anonymous_cannot_access_audit_log(self) -> None:
        self._assert_denied(
            reverse("documents:audit-log", kwargs={"pk": self.doc.pk})
        )


# ---------------------------------------------------------------------------
# 9. StaffDocumentListTests
# ---------------------------------------------------------------------------


class StaffDocumentListTests(TestCase):
    """DocumentAdminListView: all-user scope, filtering."""

    def setUp(self) -> None:
        self.client = Client()
        self.user1 = make_user(email=_email("citizen1"))
        self.user2 = make_user(email=_email("citizen2"))
        self.staff = make_user(email=_email("staff"), is_staff=True)
        self.category = make_category()
        # Grant staff view permission
        self.staff = _grant_perm(self.staff, "view_all_documents")
        self.client.force_login(self.staff)
        self.url = reverse("documents:staff-list")

    def test_staff_list_200(self) -> None:
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_staff_list_shows_all_users_docs(self) -> None:
        doc1 = make_document(self.user1, self.category)
        doc2 = make_document(self.user2, self.category)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        pks = [str(d.pk) for d in response.context["documents"]]
        self.assertIn(str(doc1.pk), pks)
        self.assertIn(str(doc2.pk), pks)

    def test_staff_list_filter_by_scan_status(self) -> None:
        active_doc = make_document(
            self.user1, self.category,
            scan_status=Document.ScanStatus.ACTIVE,
        )
        quarantined_doc = make_document(
            self.user1, self.category,
            scan_status=Document.ScanStatus.QUARANTINED,
        )
        response = self.client.get(
            self.url, {"scan_status": Document.ScanStatus.QUARANTINED}
        )
        self.assertEqual(response.status_code, 200)
        pks = [str(d.pk) for d in response.context["documents"]]
        self.assertIn(str(quarantined_doc.pk), pks)
        self.assertNotIn(str(active_doc.pk), pks)

    def test_staff_list_filter_by_legal_hold(self) -> None:
        held = make_document(self.user1, self.category, legal_hold=True)
        not_held = make_document(self.user2, self.category, legal_hold=False)
        response = self.client.get(self.url, {"legal_hold": "yes"})
        self.assertEqual(response.status_code, 200)
        pks = [str(d.pk) for d in response.context["documents"]]
        self.assertIn(str(held.pk), pks)
        self.assertNotIn(str(not_held.pk), pks)

    def test_staff_list_no_storage_key_in_context(self) -> None:
        """Queryset documents must have _storage_key deferred (not in __dict__)."""
        make_document(self.user1, self.category)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        for doc in response.context["documents"]:
            # A deferred field has not been loaded into __dict__
            self.assertNotIn("_storage_key", doc.__dict__)


# ---------------------------------------------------------------------------
# 10. StaffDocumentDetailTests
# ---------------------------------------------------------------------------


class StaffDocumentDetailTests(TestCase):
    """StaffDocumentDetailView: full metadata, no storage_key, scan-result gating."""

    def setUp(self) -> None:
        self.client = Client()
        self.owner = make_user(email=_email("citizen"))
        self.staff = make_user(email=_email("staff"), is_staff=True)
        self.category = make_category()
        self.doc = make_document(
            self.owner, self.category,
            scan_status=Document.ScanStatus.QUARANTINED,
            scan_engine_result="Eicar-Test-Signature",
        )
        self.staff = _grant_perm(self.staff, "view_all_documents")
        self.client.force_login(self.staff)

    def test_staff_detail_200(self) -> None:
        response = self.client.get(
            reverse("documents:staff-detail", args=[self.doc.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_staff_detail_no_storage_key(self) -> None:
        """storage_key must not appear in the response body."""
        storage_key = self.doc._storage_key
        response = self.client.get(
            reverse("documents:staff-detail", args=[self.doc.pk])
        )
        content = response.content.decode()
        self.assertNotIn(storage_key, content)
        # The "quarantine/documents/" prefix (with UUID segment) must not appear
        self.assertNotIn("quarantine/documents/", content)

    def test_staff_detail_shows_scan_engine_result_only_with_perm(self) -> None:
        """
        The template guards the scan-engine result block with
        ``{% if can_view_quarantine_details %}``.  The view currently passes
        only ``{"document": doc}``, so the context variable is falsy and the
        block is hidden.

        This test verifies:
        1. Without ``view_quarantined``: response is 200 and the raw scan result
           string is NOT in the response body (block hidden by template guard).
        2. With ``view_quarantined``: once the view passes
           ``can_view_quarantine_details=True``, the result would be shown.
           Until that view update lands, we assert the response is still 200.
        """
        # Step 1 — without view_quarantined
        response = self.client.get(
            reverse("documents:staff-detail", args=[self.doc.pk])
        )
        self.assertEqual(response.status_code, 200)
        # Template guard is falsy → scan result hidden
        self.assertNotIn("Eicar-Test-Signature", response.content.decode())

        # Step 2 — with view_quarantined: the template renders the quarantine
        # details card containing the raw scan_engine_result string.
        self.staff = _grant_perm(self.staff, "view_quarantined")
        self.client.force_login(self.staff)
        response2 = self.client.get(
            reverse("documents:staff-detail", args=[self.doc.pk])
        )
        self.assertEqual(response2.status_code, 200)
        # Template renders scan_engine_result inside the quarantine card when
        # can_view_quarantine_details is True. Verify the actual result text appears.
        self.assertContains(response2, "Eicar-Test-Signature")
        # Verify the quarantine card header is present (not just the raw result).
        self.assertContains(response2, "Quarantine Details")


# ---------------------------------------------------------------------------
# 11. LegalHoldViewTests
# ---------------------------------------------------------------------------


class LegalHoldViewTests(TestCase):
    """DocumentLegalHoldView: GET form, apply/release dispatch, error handling."""

    def setUp(self) -> None:
        self.client = Client()
        self.owner = make_user(email=_email("citizen"))
        self.staff = make_user(email=_email("staff"), is_staff=True)
        self.category = make_category()
        self.doc = make_document(self.owner, self.category)
        self.staff = _grant_perm(self.staff, "manage_legal_hold")
        self.client.force_login(self.staff)

    def _url(self) -> str:
        return reverse("documents:legal-hold", args=[self.doc.pk])

    def test_get_legal_hold_form_200(self) -> None:
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)

    def test_apply_hold(self) -> None:
        with patch(
            "apps.documents.views.staff.apply_legal_hold"
        ) as mock_apply:
            response = self.client.post(
                self._url(),
                data={
                    "action": "apply",
                    "reason": "Litigation hold required.",
                    "confirm_action": True,  # LegalHoldForm.confirm_action required
                },
            )
        mock_apply.assert_called_once()
        call_kwargs = mock_apply.call_args.kwargs
        self.assertEqual(call_kwargs["document"], self.doc)
        self.assertRedirects(
            response,
            reverse("documents:staff-detail", args=[self.doc.pk]),
            fetch_redirect_response=False,
        )

    def test_release_hold(self) -> None:
        self.doc.legal_hold = True
        self.doc.save()
        with patch(
            "apps.documents.views.staff.release_legal_hold"
        ) as mock_release:
            response = self.client.post(
                self._url(),
                data={
                    "action": "release",
                    "reason": "Litigation concluded.",
                    "confirm_action": True,
                },
            )
        mock_release.assert_called_once()
        self.assertRedirects(
            response,
            reverse("documents:staff-detail", args=[self.doc.pk]),
            fetch_redirect_response=False,
        )

    def test_apply_hold_missing_reason(self) -> None:
        """POST action=apply with empty reason → LegalHoldForm invalid → 422."""
        response = self.client.post(
            self._url(),
            data={"action": "apply", "reason": "", "confirm_action": True},
        )
        self.assertEqual(response.status_code, 422)

    def test_apply_hold_missing_confirm(self) -> None:
        """POST without confirm_action checkbox → form invalid → 422 (server-side guard)."""
        response = self.client.post(
            self._url(),
            data={"action": "apply", "reason": "Litigation hold required."},
            # confirm_action omitted intentionally
        )
        self.assertEqual(response.status_code, 422)

    def test_permission_denied_from_service(self) -> None:
        """Service raises PermissionDenied → 403 rendered without crashing."""
        from django.core.exceptions import PermissionDenied
        with patch(
            "apps.documents.views.staff.apply_legal_hold",
            side_effect=PermissionDenied("Double-check failed."),
        ):
            response = self.client.post(
                self._url(),
                data={
                    "action": "apply",
                    "reason": "Litigation hold required.",
                    "confirm_action": True,
                },
            )
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# 12. QuarantineListTests
# ---------------------------------------------------------------------------


class QuarantineListTests(TestCase):
    """DocumentQuarantineListView: only QUARANTINED docs; no scan_engine_result."""

    def setUp(self) -> None:
        self.client = Client()
        self.owner = make_user(email=_email("citizen"))
        self.staff = make_user(email=_email("staff"), is_staff=True)
        self.category = make_category()
        self.staff = _grant_perm(self.staff, "view_quarantined")
        self.client.force_login(self.staff)
        self.url = reverse("documents:quarantine-list")

    def test_quarantine_list_200(self) -> None:
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_quarantine_list_shows_only_quarantined(self) -> None:
        active_doc = make_document(
            self.owner, self.category,
            scan_status=Document.ScanStatus.ACTIVE,
        )
        quarantined_doc = make_document(
            self.owner, self.category,
            scan_status=Document.ScanStatus.QUARANTINED,
            scan_engine_result="Win.Test.EICAR_HDB-1",
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        pks = [str(d.pk) for d in response.context["documents"]]
        self.assertIn(str(quarantined_doc.pk), pks)
        self.assertNotIn(str(active_doc.pk), pks)

    def test_quarantine_list_no_scan_engine_result(self) -> None:
        """scan_engine_result must not be rendered in the quarantine list HTML."""
        make_document(
            self.owner, self.category,
            scan_status=Document.ScanStatus.QUARANTINED,
            scan_engine_result="Win.Malware.Detected-1234",
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn("Win.Malware.Detected-1234", content)


# ---------------------------------------------------------------------------
# 13. AuditLogViewTests
# ---------------------------------------------------------------------------


class AuditLogViewTests(TestCase):
    """DocumentAuditLogView: renders successfully; shows AuditLogEntry rows."""

    def setUp(self) -> None:
        self.client = Client()
        self.owner = make_user(email=_email("citizen"))
        self.staff = make_user(email=_email("staff"), is_staff=True)
        self.category = make_category()
        self.doc = make_document(self.owner, self.category)
        self.staff = _grant_perm(self.staff, "view_all_documents")
        self.client.force_login(self.staff)

    def _url(self) -> str:
        return reverse("documents:audit-log", args=[self.doc.pk])

    def test_audit_log_200(self) -> None:
        """Audit log view returns 200 even when no audit entries exist."""
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)

    def test_audit_log_shows_entries(self) -> None:
        """AuditLogEntry rows for this document appear in the response context."""
        from apps.audit.models import AuditEventType, AuditLogEntry

        AuditLogEntry.objects.create(
            event_type=AuditEventType.RECORD_VIEWED,
            actor_id=str(self.staff.pk),
            resource_type="documents.Document",
            resource_id=str(self.doc.pk),
            event_detail={"document_pk": str(self.doc.pk)},
        )
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        audit_entries = list(response.context.get("audit_entries", []))
        self.assertEqual(len(audit_entries), 1)
        self.assertEqual(audit_entries[0].resource_id, str(self.doc.pk))


# ---------------------------------------------------------------------------
# 14. PIPEDAInvariantTests
# ---------------------------------------------------------------------------


class PIPEDAInvariantTests(TestCase):
    """
    Cross-cutting PIPEDA invariant checks.

    Each test covers a different invariant that must hold across all views:
    - storage_key (quarantine/ prefix) never in any citizen response body
    - IDOR returns 404 not 403 for citizens
    - User email never in staff list response
    - scan_engine_result never exposed to citizens
    """

    def setUp(self) -> None:
        self.client = Client()
        self.citizen = make_user(email="citizen_pipeda@example.com")
        self.other = make_user(email="other_pipeda@example.com")
        self.staff_user = make_user(
            email="staff_pipeda@example.com", is_staff=True
        )
        self.category = make_category()
        # Document with a known storage key owned by citizen
        self.own_doc = make_document(
            self.citizen, self.category,
            _storage_key="quarantine/documents/pipeda-invariant-test.bin",
            scan_status=Document.ScanStatus.ACTIVE,
        )
        # Document owned by other — used for IDOR checks
        self.other_doc = make_document(self.other, self.category)
        # Grant staff the needed permission
        self.staff_user = _grant_perm(self.staff_user, "view_all_documents")

    def test_storage_key_never_in_any_citizen_response(self) -> None:
        """
        The raw storage_key value must not appear in list or detail responses.
        """
        self.client.force_login(self.citizen)
        for url in [
            reverse("documents:list"),
            reverse("documents:detail", args=[self.own_doc.pk]),
        ]:
            response = self.client.get(url)
            content = response.content.decode()
            self.assertNotIn(
                "quarantine/documents/pipeda-invariant-test.bin",
                content,
                msg=f"storage_key leaked in {url}",
            )

    def test_idor_404_not_403_for_citizen(self) -> None:
        """Citizen viewing another citizen's doc must get 404, not 403 or 200."""
        self.client.force_login(self.citizen)
        response = self.client.get(
            reverse("documents:detail", args=[self.other_doc.pk])
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotEqual(response.status_code, 403)

    def test_uploaded_by_email_never_in_staff_list(self) -> None:
        """Staff list must not contain any citizen's email address."""
        make_document(self.citizen, self.category)
        make_document(self.other, self.category)
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("documents:staff-list"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn(self.citizen.email, content)
        self.assertNotIn(self.other.email, content)

    def test_scan_engine_result_never_in_citizen_response(self) -> None:
        """
        A quarantined doc owned by the citizen must not expose scan_engine_result.

        DocumentDetailView filters for ACTIVE docs only, so a QUARANTINED doc
        returns 404.  Either outcome is acceptable — what must NOT happen is a
        200 response that contains the scan result.
        """
        quarantined_doc = make_document(
            self.citizen, self.category,
            scan_status=Document.ScanStatus.QUARANTINED,
            scan_engine_result="Trojan.Dropper.XYZ-2026",
        )
        self.client.force_login(self.citizen)
        response = self.client.get(
            reverse("documents:detail", args=[quarantined_doc.pk])
        )
        if response.status_code == 200:
            self.assertNotIn(
                "Trojan.Dropper.XYZ-2026",
                response.content.decode(),
            )
        else:
            self.assertEqual(response.status_code, 404)
