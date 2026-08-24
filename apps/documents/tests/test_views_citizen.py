"""
apps/documents/tests/test_views_citizen.py
==========================================
Wave 7 test suite — citizen-facing view EDGE CASES.

This file covers scenarios NOT already addressed by ``test_wave5_views.py``.

What test_wave5_views.py already covers (NOT duplicated here):
- Basic auth redirect for all citizen views (CitizenAuthTests)
- Simple IDOR 404 for non-owned docs
- Basic list (own docs visible, other-user/soft-deleted/old-version excluded)
- Basic upload-init form render + service dispatch + PermissionDenied/ValidationError surfaces
- Basic confirm: success redirect, ValidationError redirect, Http404 pass-through, IDOR 404
- Basic download: active doc → token redirect; non-active/other-user/nonexistent → 404
- Basic token redeem: small-file FileResponse, invalid/expired/other-user token → 404,
  storage_key not in response headers, large-file presigned-URL redirect

New edge cases covered here per test class:
  DocumentListViewEdgeCaseTests     — pagination (page 1 / page 2), total_count in context,
                                      non-ACTIVE statuses excluded, _storage_key deferred
  DocumentDetailViewEdgeCaseTests   — PENDING_UPLOAD visible but not downloadable,
                                      PURGED document → 404, _storage_key deferred,
                                      legal-hold ACTIVE doc still downloadable,
                                      'storage_key' key never in template context dict
  DocumentUploadInitViewEdgeCaseTests — RuntimeError → 500, GET response has no existing-doc
                                        paths, presign context structure, description passthrough
  DocumentUploadConfirmViewEdgeCaseTests — RuntimeError → redirect to upload-init,
                                           success message on commit, error message on
                                           ValidationError, already-scanning idempotency,
                                           str(pk) passed to service
  DocumentDownloadViewEdgeCaseTests — PENDING_UPLOAD → 404, QUARANTINED → 404,
                                      soft-deleted ACTIVE doc → 404
  DocumentTokenRedeemViewEdgeCaseTests — Content-Disposition is document-{pk}.bin (not filename),
                                         storage_key absent from response body for small files,
                                         raw storage_key absent from Location for large-file redirect,
                                         doc.mime_type used as Content-Type, auth guard,
                                         presigned URL is the opaque storage URL not the raw key

Security invariants verified throughout:
  1. ``_storage_key`` is NEVER present in template context __dict__ (deferred at ORM level)
  2. ``original_filename`` is NOT used in Content-Disposition (only document-{pk}.bin)
  3. Citizens get 404 (not 403) for non-owned or unavailable PKs
  4. Only ACTIVE docs are downloadable
"""  # noqa: E501

from __future__ import annotations

import uuid
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.documents.models import Document, DocumentCategory

User = get_user_model()

# ---------------------------------------------------------------------------
# Module-level counter — unique emails and slugs across all test classes
# ---------------------------------------------------------------------------

_CTR = 0


def _next() -> int:
    """Return a monotonically increasing integer, unique per test run."""
    global _CTR
    _CTR += 1
    return _CTR


def _email(prefix: str = "user") -> str:
    return f"{prefix}-w7-{_next()}@example.com"


def _slug() -> str:
    return f"cat-w7-{_next()}"


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def make_category(**kwargs) -> DocumentCategory:
    defaults = {
        "name_en": "Wave 7 Category",
        "name_fr": "Catégorie vague 7",
        "slug": _slug(),
        "allowed_mime_types": ["application/pdf"],
        "max_size_bytes": 0,
        "min_retention_days": 730,
        "max_retention_days": 2555,
        "staff_only": False,
    }
    defaults.update(kwargs)
    return DocumentCategory.objects.create(**defaults)


def make_user(**kwargs):
    email = kwargs.pop("email", _email())
    password = kwargs.pop("password", "testpass123!")
    return User.objects.create_user(email=email, password=password, **kwargs)


def make_document(user, category: DocumentCategory, **kwargs) -> Document:
    defaults = {
        "category": category,
        "uploaded_by": user,
        "original_filename": "test-document.pdf",
        "_storage_key": f"quarantine/documents/w7/{uuid.uuid4().hex}.bin",
        "mime_type": "application/pdf",
        "size_bytes": 1024,
        "scan_status": Document.ScanStatus.ACTIVE,
        "is_latest_version": True,
    }
    defaults.update(kwargs)
    return Document.objects.create(**defaults)


# ---------------------------------------------------------------------------
# 1. DocumentListViewEdgeCaseTests
# ---------------------------------------------------------------------------


class DocumentListViewEdgeCaseTests(TestCase):
    """
    Edge cases for DocumentListView not covered by test_wave5_views.py.

    Wave 5 covers: basic 200, own docs visible, other-user excluded,
    soft-deleted excluded, old versions excluded.

    Wave 7 adds: pagination boundary, total_count context key,
    non-ACTIVE status exclusion, storage_key deferred.
    """

    def setUp(self) -> None:
        self.client = Client()
        self.user = make_user(email=_email("citizen"))
        self.category = make_category()
        self.client.force_login(self.user)
        self.url = reverse("documents:list")

    def _make_owned_active(self) -> Document:
        return make_document(self.user, self.category)

    # -- Pagination -----------------------------------------------------------

    def test_pagination_shows_20_docs_per_page(self) -> None:
        """Page 1 must contain exactly 20 documents when 21 active docs exist."""
        for _ in range(21):
            self._make_owned_active()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        page_items = list(response.context["documents"])
        self.assertEqual(len(page_items), 20)

    def test_pagination_page_2_has_remainder(self) -> None:
        """Page 2 must contain the 1 remaining document after 21 are created."""
        for _ in range(21):
            self._make_owned_active()
        response = self.client.get(self.url + "?page=2")
        self.assertEqual(response.status_code, 200)
        page_items = list(response.context["documents"])
        self.assertEqual(len(page_items), 1)

    # -- Context: total_count -------------------------------------------------

    def test_total_count_in_context_matches_active_doc_count(self) -> None:
        """context['total_count'] must equal the number of active latest-version docs."""
        expected = 5
        for _ in range(expected):
            self._make_owned_active()
        # Create a soft-deleted doc — must NOT be in total_count
        make_document(self.user, self.category, deleted_at=timezone.now())
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_count"], expected)

    def test_total_count_zero_when_no_active_docs(self) -> None:
        """total_count is 0 when the user has no active latest-version documents."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_count"], 0)

    # -- Non-ACTIVE status exclusion ------------------------------------------

    def test_quarantined_doc_excluded_from_list(self) -> None:
        """QUARANTINED docs are excluded because .active() filters scan_status=ACTIVE."""
        quarantined = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.QUARANTINED,
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        pks = [str(d.pk) for d in response.context["documents"]]
        self.assertNotIn(str(quarantined.pk), pks)

    def test_pending_upload_doc_excluded_from_list(self) -> None:
        """PENDING_UPLOAD docs are excluded by .active() filter."""
        pending = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        pks = [str(d.pk) for d in response.context["documents"]]
        self.assertNotIn(str(pending.pk), pks)

    # -- Security: _storage_key deferred --------------------------------------

    def test_storage_key_deferred_in_list_queryset(self) -> None:
        """
        _storage_key must NOT be loaded into document.__dict__ when the citizen
        list view returns.  This verifies that ``.defer("_storage_key")`` is
        applied at the ORM level and the field is never in template context.
        """
        self._make_owned_active()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        for doc in response.context["documents"]:
            self.assertNotIn(
                "_storage_key",
                doc.__dict__,
                msg=f"_storage_key was loaded for doc pk={doc.pk} — deferred() not applied",
            )


# ---------------------------------------------------------------------------
# 2. DocumentDetailViewEdgeCaseTests
# ---------------------------------------------------------------------------


class DocumentDetailViewEdgeCaseTests(TestCase):
    """
    Edge cases for DocumentDetailView not covered by test_wave5_views.py.

    Wave 5 covers: own doc → 200, IDOR → 404, nonexistent → 404,
    storage_key not in response body, XSS escaping, can_download True/False
    for ACTIVE/SCANNING/QUARANTINED, soft-deleted → 404.

    Wave 7 adds: PENDING_UPLOAD visible but can_download=False, PURGED → 404,
    _storage_key deferred, legal-hold ACTIVE downloadable, 'storage_key' key
    absent from the template context dictionary.
    """

    def setUp(self) -> None:
        self.client = Client()
        self.user = make_user(email=_email("citizen"))
        self.category = make_category()
        self.client.force_login(self.user)

    def _url(self, pk) -> str:
        return reverse("documents:detail", args=[pk])

    # -- Scan status visibility -----------------------------------------------

    def test_pending_upload_document_is_visible_but_not_downloadable(self) -> None:
        """
        PENDING_UPLOAD doc is owned and not soft-deleted so the detail view
        returns 200.  However can_download must be False — the file hasn't
        been uploaded yet, let alone scanned.
        """
        doc = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
        )
        response = self.client.get(self._url(doc.pk))
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.context["can_download"], False)

    def test_purged_document_returns_404(self) -> None:
        """
        PURGED documents have deleted_at set (they pass through soft-delete
        before hard-delete).  The detail view filter requires deleted_at__isnull=True
        so PURGED docs return 404.
        """
        doc = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.PURGED,
            deleted_at=timezone.now(),
        )
        response = self.client.get(self._url(doc.pk))
        self.assertEqual(response.status_code, 404)

    def test_legal_hold_active_document_is_downloadable(self) -> None:
        """
        legal_hold=True does not prevent a citizen from downloading their
        ACTIVE document.  can_download depends solely on scan_status.
        """
        doc = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.ACTIVE,
            legal_hold=True,
        )
        response = self.client.get(self._url(doc.pk))
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.context["can_download"], True)

    # -- Security: _storage_key deferred and absent from context --------------

    def test_storage_key_deferred_in_detail_queryset(self) -> None:
        """
        _storage_key must NOT be present in the document object's __dict__
        after the detail view renders — confirming ORM-level .defer() is applied.
        """
        doc = make_document(self.user, self.category)
        response = self.client.get(self._url(doc.pk))
        self.assertEqual(response.status_code, 200)
        context_doc = response.context["document"]
        self.assertNotIn(
            "_storage_key",
            context_doc.__dict__,
            msg="_storage_key was loaded into context document — .defer() not applied",
        )

    def test_storage_key_key_absent_from_template_context(self) -> None:
        """
        Neither 'storage_key' nor '_storage_key' must appear as top-level keys
        in the template context.  The view passes only {'document', 'can_download'}.
        """
        doc = make_document(self.user, self.category)
        response = self.client.get(self._url(doc.pk))
        self.assertEqual(response.status_code, 200)
        # The view only passes 'document' and 'can_download' to the template.
        self.assertNotIn("storage_key", response.context)
        self.assertNotIn("_storage_key", response.context)


# ---------------------------------------------------------------------------
# 3. DocumentUploadInitViewEdgeCaseTests
# ---------------------------------------------------------------------------


class DocumentUploadInitViewEdgeCaseTests(TestCase):
    """
    Edge cases for DocumentUploadInitView not covered by test_wave5_views.py.

    Wave 5 covers: GET 200, POST calls service, ValidationError → 422,
    PermissionDenied → 403, no storage_key in response body.

    Wave 7 adds: RuntimeError → 500, GET has no existing-doc paths in HTML,
    presign context has confirm_url and description (no storage_key key),
    description is threaded through to template context.
    """

    def setUp(self) -> None:
        self.client = Client()
        self.user = make_user(email=_email("citizen"))
        self.category = make_category(slug=f"upload-w7-{_next()}")
        self.client.force_login(self.user)
        self.url = reverse("documents:upload-init")

    def _post_data(self) -> dict:
        return {
            "category_slug": self.category.slug,
            "original_filename": "report.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 2048,
            "description": "Annual performance report",
        }

    def _mock_result(self) -> dict:
        doc_id = str(uuid.uuid4())
        return {
            "doc_id": doc_id,
            "upload_url": "https://s3.example.com/upload",
            "upload_fields": {
                "key": f"quarantine/{doc_id}.bin",
                "AWSAccessKeyId": "AKIA_TEST",
            },
            "expires_at": "2026-07-21T12:00:00Z",
        }

    # -- Error surfaces -------------------------------------------------------

    def test_post_unexpected_exception_returns_500(self) -> None:
        """Unhandled exception in validate_upload_request → 500 response."""
        with patch(
            "apps.documents.views.citizen.validate_upload_request",
            side_effect=RuntimeError("Unexpected failure in storage backend"),
        ):
            response = self.client.post(self.url, data=self._post_data())
        self.assertEqual(response.status_code, 500)

    # -- GET response safety --------------------------------------------------

    def test_get_does_not_contain_quarantine_path_in_html(self) -> None:
        """
        The GET form render must not embed any existing quarantine paths in the
        HTML — the form only collects metadata, it has no access to storage keys.
        """
        # Create a doc for this user so there IS a storage key in the DB
        make_document(
            self.user,
            self.category,
            _storage_key="quarantine/documents/w7/get-form-leak-test.bin",
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn("quarantine/documents/w7/get-form-leak-test.bin", content)
        # "quarantine/" path prefix must not appear anywhere on the form page
        self.assertNotIn("quarantine/documents/", content)

    # -- Presign context structure --------------------------------------------

    def test_post_presign_context_contains_confirm_url(self) -> None:
        """
        After a successful presign, the response context must contain confirm_url
        pointing to the upload-confirm endpoint for that doc_id.
        """
        result = self._mock_result()
        with patch(
            "apps.documents.views.citizen.validate_upload_request",
            return_value=result,
        ):
            response = self.client.post(self.url, data=self._post_data())
        self.assertEqual(response.status_code, 200)
        expected_confirm = reverse("documents:upload-confirm", args=[result["doc_id"]])
        self.assertEqual(response.context["confirm_url"], expected_confirm)

    def test_post_presign_context_does_not_contain_storage_key_key(self) -> None:
        """
        The presign template context must never contain a 'storage_key' key.
        The service returns upload_url, upload_fields, doc_id, expires_at —
        none of which are the stored DB storage_key.
        """
        result = self._mock_result()
        with patch(
            "apps.documents.views.citizen.validate_upload_request",
            return_value=result,
        ):
            response = self.client.post(self.url, data=self._post_data())
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("storage_key", response.context)
        self.assertNotIn("_storage_key", response.context)

    def test_post_presign_context_threads_description_through(self) -> None:
        """
        The description from the upload form must appear in the presign template
        context so the confirm view can persist it.
        """
        result = self._mock_result()
        with patch(
            "apps.documents.views.citizen.validate_upload_request",
            return_value=result,
        ):
            response = self.client.post(self.url, data=self._post_data())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["description"], "Annual performance report")


# ---------------------------------------------------------------------------
# 4. DocumentUploadConfirmViewEdgeCaseTests
# ---------------------------------------------------------------------------


class DocumentUploadConfirmViewEdgeCaseTests(TestCase):
    """
    Edge cases for DocumentUploadConfirmView not covered by test_wave5_views.py.

    Wave 5 covers: success → redirect to list, ValidationError → redirect to
    upload-init, Http404 pass-through, IDOR Http404.

    Wave 7 adds: RuntimeError → redirect to upload-init (not 500), success
    message on commit, error message on ValidationError, idempotent call when
    doc is already SCANNING, service called with str(pk).
    """

    def setUp(self) -> None:
        self.client = Client()
        self.user = make_user(email=_email("citizen"))
        self.category = make_category()
        self.doc = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
        )
        self.client.force_login(self.user)

    def _url(self, pk=None) -> str:
        return reverse("documents:upload-confirm", args=[pk or self.doc.pk])

    # -- Error surfaces -------------------------------------------------------

    def test_confirm_unexpected_exception_redirects_to_upload_init(self) -> None:
        """
        An unhandled exception in confirm_upload must redirect to upload-init
        (not raise a 500) so the citizen can retry their upload.
        """
        with patch(
            "apps.documents.views.citizen.confirm_upload",
            side_effect=RuntimeError("Storage backend unavailable"),
        ):
            response = self.client.post(self._url())
        self.assertRedirects(
            response,
            reverse("documents:upload-init"),
            fetch_redirect_response=False,
        )

    # -- Messages -------------------------------------------------------------

    def test_confirm_success_adds_success_message(self) -> None:
        """
        On successful confirm, the view adds a success-level Django message
        before redirecting to the document list.
        """
        with patch(
            "apps.documents.views.citizen.confirm_upload",
            return_value=self.doc,
        ):
            response = self.client.post(self._url())
        # Follow redirect so session-stored messages are processed
        msgs = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any(m.level_tag == "success" for m in msgs),
            msg="No success message was added after upload confirmation",
        )

    def test_confirm_validation_error_adds_error_message(self) -> None:
        """
        On ValidationError from confirm_upload, an error-level Django message
        must be added before redirecting to upload-init.
        """
        with patch(
            "apps.documents.views.citizen.confirm_upload",
            side_effect=ValidationError("File not found in quarantine."),
        ):
            response = self.client.post(self._url())
        msgs = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any(m.level_tag == "error" for m in msgs),
            msg="No error message was added after ValidationError in upload confirm",
        )

    # -- Idempotency ----------------------------------------------------------

    def test_confirm_already_scanning_doc_still_redirects_to_list(self) -> None:
        """
        If confirm_upload returns a SCANNING document (idempotent call), the
        view must still redirect to the document list, not raise an error.
        The view does not inspect the returned document's scan_status.
        """
        scanning_doc = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.SCANNING,
        )
        with patch(
            "apps.documents.views.citizen.confirm_upload",
            return_value=scanning_doc,
        ):
            response = self.client.post(self._url(pk=scanning_doc.pk))
        self.assertRedirects(
            response,
            reverse("documents:list"),
            fetch_redirect_response=False,
        )

    # -- Service call contract ------------------------------------------------

    def test_confirm_passes_str_of_pk_to_service(self) -> None:
        """
        confirm_upload must be called with doc_id as a string (str(pk)), not a
        UUID object, because the service performs string-based lookups.
        """
        with patch(
            "apps.documents.views.citizen.confirm_upload",
            return_value=self.doc,
        ) as mock_confirm:
            self.client.post(self._url())
        # The view always calls confirm_upload with keyword args (user=, doc_id=).
        doc_id_arg = mock_confirm.call_args.kwargs["doc_id"]
        self.assertIsInstance(doc_id_arg, str)
        self.assertEqual(doc_id_arg, str(self.doc.pk))


# ---------------------------------------------------------------------------
# 5. DocumentDownloadViewEdgeCaseTests
# ---------------------------------------------------------------------------


class DocumentDownloadViewEdgeCaseTests(TestCase):
    """
    Edge cases for DocumentDownloadView not covered by test_wave5_views.py.

    Wave 5 covers: ACTIVE doc → redirect to token-redeem; SCANNING doc → 404;
    other-user doc → 404; nonexistent pk → 404.

    Wave 7 adds: PENDING_UPLOAD → 404, QUARANTINED → 404, soft-deleted ACTIVE
    doc → 404 (deleted_at gate).
    """

    def setUp(self) -> None:
        self.client = Client()
        self.user = make_user(email=_email("citizen"))
        self.category = make_category()
        self.client.force_login(self.user)

    def _url(self, pk) -> str:
        return reverse("documents:download", args=[pk])

    # -- Scan-status gate -----------------------------------------------------

    def test_pending_upload_doc_returns_404(self) -> None:
        """
        PENDING_UPLOAD docs are not downloadable — the file has not yet arrived
        in storage, let alone been scanned.
        """
        doc = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
        )
        response = self.client.get(self._url(doc.pk))
        self.assertEqual(response.status_code, 404)

    def test_quarantined_doc_returns_404(self) -> None:
        """
        QUARANTINED docs (infected files) must return 404.  Citizens must never
        be able to obtain a download token for an infected file.
        """
        doc = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.QUARANTINED,
        )
        response = self.client.get(self._url(doc.pk))
        self.assertEqual(response.status_code, 404)

    # -- Soft-delete gate -----------------------------------------------------

    def test_soft_deleted_active_doc_returns_404(self) -> None:
        """
        A doc with scan_status=ACTIVE but deleted_at set must return 404.
        The download view filter requires both ACTIVE status AND deleted_at__isnull=True.
        """
        doc = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.ACTIVE,
            deleted_at=timezone.now(),
        )
        response = self.client.get(self._url(doc.pk))
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# 6. DocumentTokenRedeemViewEdgeCaseTests
# ---------------------------------------------------------------------------


class DocumentTokenRedeemViewEdgeCaseTests(TestCase):
    """
    Edge cases for DocumentTokenRedeemView not covered by test_wave5_views.py.

    Wave 5 covers: valid token → small file (FileResponse), invalid/expired/
    wrong-user token → 404, storage_key not in response HEADERS, large file
    → redirect to presigned URL.

    Wave 7 adds:
    - Content-Disposition uses exact 'document-{pk}.bin' format (not original_filename)
    - storage_key absent from response BODY for small files (different from headers)
    - raw storage_key absent from Location header for large-file redirect
    - doc.mime_type used as Content-Type header for small files
    - unexpected exception in token consume → 404 (not 500)
    - large-file redirect: default_storage.url() called with storage_key (not raw key in response)
    """

    def setUp(self) -> None:
        self.client = Client()
        self.user = make_user(email=_email("citizen"))
        self.category = make_category()
        # Use a distinctive original_filename to verify it does NOT appear in headers
        self.doc = make_document(
            self.user,
            self.category,
            original_filename="my-private-file-with-pii-name.pdf",
            mime_type="application/pdf",
            size_bytes=512,
            _storage_key="quarantine/documents/w7/token-redeem-edge.bin",
        )
        self.client.force_login(self.user)

    def _url(self, token: str) -> str:
        return reverse("documents:token-redeem", args=[token])

    def _small_file_context(self, doc=None):
        """Return patch context managers for a small-file redeem."""
        target_doc = doc or self.doc
        return (
            patch(
                "apps.documents.views.citizen.consume_access_token",
                return_value=target_doc,
            ),
            patch(
                "apps.documents.views.citizen.default_storage.open",
                return_value=BytesIO(b"%PDF-1.4 fake content"),
            ),
        )

    # -- Content-Disposition format -------------------------------------------

    def test_content_disposition_uses_document_pk_bin_format(self) -> None:
        """
        Content-Disposition must be exactly 'attachment; filename="document-{pk}.bin"'.
        The original_filename (which may contain PII) must NOT appear.
        """
        consume_mock, open_mock = self._small_file_context()
        with consume_mock, open_mock:
            response = self.client.get(self._url("a" * 64))
        self.assertEqual(response.status_code, 200)
        content_disp = response.get("Content-Disposition", "")
        expected_filename = f"document-{self.doc.pk}.bin"
        self.assertIn(expected_filename, content_disp)
        self.assertIn("attachment", content_disp)
        # The original_filename must never appear in Content-Disposition
        self.assertNotIn("my-private-file-with-pii-name.pdf", content_disp)

    # -- storage_key not in response body -------------------------------------

    def test_storage_key_not_in_small_file_response_body(self) -> None:
        """
        The raw storage_key must not appear anywhere in the response body
        when a small file is proxied.  The body is the raw file bytes, not
        any metadata, so this checks that the view doesn't inject path info.
        """
        storage_key = self.doc._storage_key  # "quarantine/documents/w7/token-redeem-edge.bin"
        consume_mock, open_mock = self._small_file_context()
        with consume_mock, open_mock:
            response = self.client.get(self._url("b" * 64))
        self.assertEqual(response.status_code, 200)
        body = (
            b"".join(response.streaming_content)
            if hasattr(response, "streaming_content")
            else response.content
        )
        self.assertNotIn(storage_key.encode(), body)
        self.assertNotIn(b"quarantine/", body)

    # -- MIME type correct header ---------------------------------------------

    def test_small_file_content_type_matches_doc_mime_type(self) -> None:
        """
        The Content-Type response header must match doc.mime_type, not a
        generic application/octet-stream.
        """
        consume_mock, open_mock = self._small_file_context()
        with consume_mock, open_mock:
            response = self.client.get(self._url("c" * 64))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    # -- Large file: raw storage_key not in Location --------------------------

    def test_large_file_raw_storage_key_not_in_location_header(self) -> None:
        """
        For large files, the Location header must be the presigned URL returned
        by default_storage.url(), NOT the raw storage_key string.

        The raw storage_key ("quarantine/documents/...") must not appear in the
        Location header — only the opaque presigned URL does.
        """
        from django.conf import settings as django_settings

        proxy_threshold = django_settings.CIVICOS.get("DOCUMENT_PROXY_MAX_BYTES", 1 * 1024 * 1024)

        raw_key = f"quarantine/documents/w7/large-file-{uuid.uuid4().hex}.bin"
        large_doc = make_document(
            self.user,
            self.category,
            size_bytes=proxy_threshold + 1,
            _storage_key=raw_key,
        )
        presigned_url = (
            "https://s3.ca-central-1.amazonaws.com/govstack-docs"
            f"/documents%2Fw7%2F{uuid.uuid4().hex}.bin"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc123"
        )
        with (
            patch(
                "apps.documents.views.citizen.consume_access_token",
                return_value=large_doc,
            ),
            patch("apps.documents.views.citizen.default_storage") as mock_storage,
        ):
            mock_storage.url.return_value = presigned_url
            response = self.client.get(self._url("d" * 64))
        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        # Location is the presigned URL, not the raw storage key
        self.assertEqual(location, presigned_url)
        # The raw storage key must never appear directly in the Location header
        self.assertNotIn(raw_key, location)
        self.assertNotIn("quarantine/documents/w7/", location)

    # -- Large file: storage URL is obtained via .url() ----------------------

    def test_large_file_uses_default_storage_url_method(self) -> None:
        """
        For large files, default_storage.url(doc.storage_key) must be called.
        This confirms the view goes through the storage backend's URL generation
        (presigned URL), not a raw key exposure.
        """
        from django.conf import settings as django_settings

        proxy_threshold = django_settings.CIVICOS.get("DOCUMENT_PROXY_MAX_BYTES", 1 * 1024 * 1024)

        large_doc = make_document(
            self.user,
            self.category,
            size_bytes=proxy_threshold + 1,
        )
        presigned = "https://s3.example.com/presigned?sig=xyz"
        with (
            patch(
                "apps.documents.views.citizen.consume_access_token",
                return_value=large_doc,
            ),
            patch("apps.documents.views.citizen.default_storage") as mock_storage,
        ):
            mock_storage.url.return_value = presigned
            response = self.client.get(self._url("e" * 64))
        self.assertEqual(response.status_code, 302)
        # Verify .url() was called with the doc's storage_key (not skipped)
        mock_storage.url.assert_called_once_with(large_doc.storage_key)

    # -- Unexpected exception in consume_access_token -------------------------

    def test_unexpected_exception_in_consume_returns_404(self) -> None:
        """
        An unhandled exception from consume_access_token must result in 404,
        not a 500 server error.  Citizens must not see internal error details.
        """
        with patch(
            "apps.documents.views.citizen.consume_access_token",
            side_effect=OSError("Storage connection timeout"),
        ):
            response = self.client.get(self._url("f" * 64))
        self.assertEqual(response.status_code, 404)

    # -- Small file: storage_key not in any header ----------------------------

    def test_small_file_storage_key_not_in_any_response_header(self) -> None:
        """
        Regression guard: the raw storage_key must not appear in any response
        header for proxied small files.  Verify the full header set.
        """
        storage_key = self.doc._storage_key
        consume_mock, open_mock = self._small_file_context()
        with consume_mock, open_mock:
            response = self.client.get(self._url("g" * 64))
        self.assertEqual(response.status_code, 200)
        all_header_values = " ".join(str(v) for v in response.headers.values())
        self.assertNotIn(storage_key, all_header_values)
        self.assertNotIn("quarantine/", all_header_values)

    # -- Auth guard -----------------------------------------------------------

    def test_token_redeem_requires_login(self) -> None:
        """
        Unauthenticated request to the token-redeem URL must redirect to login.

        (Moved here from DocumentDownloadViewEdgeCaseTests where it was
        mis-classified — this tests the token-redeem URL, not the download URL.)
        """
        anon_client = Client()
        response = anon_client.get(reverse("documents:token-redeem", args=["a" * 64]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])


# ---------------------------------------------------------------------------
# 7. DocumentTokenRedeemPostRedemptionGateTests  (H-2 / L-2)
# ---------------------------------------------------------------------------


class DocumentTokenRedeemPostRedemptionGateTests(TestCase):
    """
    H-2: the HTML redeem view must re-check scan_status / deleted_at AFTER the
    token is consumed, not only when the token was issued.

    A document can be QUARANTINED by a delayed or re-run virus scan, or
    soft-deleted by the citizen or a retention job, at any point inside the
    token's 5-minute TTL. Before this fix the HTML path kept serving the bytes
    for the remainder of that window (the DRF path already had the guard).

    L-2: the large-file redirect must use a presigned URL whose TTL matches
    CIVICOS["DOCUMENT_PRESIGNED_URL_TTL_SECONDS"] (300s) — not
    default_storage.url(), which honours AWS_QUERYSTRING_EXPIRE (3600s in
    production) and would outlive the single-use token by 12×.

    These tests use REAL DocumentAccessToken rows and the real
    consume_access_token() service so that the guard is exercised end to end
    rather than against a mocked document.
    """  # noqa: RUF002

    def setUp(self) -> None:
        from apps.documents.models import DocumentAccessToken

        self.client = Client()
        self.user = make_user(email=_email("citizen"))
        self.category = make_category()
        self.doc = make_document(self.user, self.category, size_bytes=512)
        self.token = DocumentAccessToken.objects.create(
            document=self.doc,
            issued_to=self.user,
            expires_at=timezone.now() + timezone.timedelta(seconds=300),
        )
        self.client.force_login(self.user)

    def _url(self, token: str | None = None) -> str:
        return reverse("documents:token-redeem", args=[token or self.token.token])

    # -- H-2: quarantined during the token's TTL window -----------------------

    def test_document_quarantined_after_token_issue_is_not_served(self) -> None:
        """
        Valid, unexpired, unused token + document quarantined after issuance
        → 404 (previously: the file was served).
        """
        Document.objects.filter(pk=self.doc.pk).update(
            scan_status=Document.ScanStatus.QUARANTINED,
        )

        with patch("apps.documents.views.citizen.default_storage") as mock_storage:
            mock_storage.open.return_value = BytesIO(b"malware bytes")
            response = self.client.get(self._url())

        self.assertEqual(response.status_code, 404)
        # The quarantined bytes must never have been opened.
        mock_storage.open.assert_not_called()

    def test_document_soft_deleted_after_token_issue_is_not_served(self) -> None:
        """Soft-deleted (deleted_at set) inside the TTL window → 404."""
        Document.objects.filter(pk=self.doc.pk).update(
            deleted_at=timezone.now(),
            scan_status=Document.ScanStatus.DELETED,
        )

        with patch("apps.documents.views.citizen.default_storage") as mock_storage:
            mock_storage.open.return_value = BytesIO(b"deleted bytes")
            response = self.client.get(self._url())

        self.assertEqual(response.status_code, 404)
        mock_storage.open.assert_not_called()

    def test_deleted_at_set_but_status_still_active_is_not_served(self) -> None:
        """
        Defence in depth: deleted_at alone (without the DELETED status) must
        also block serving — the guard is an OR, not an AND.
        """
        Document.objects.filter(pk=self.doc.pk).update(deleted_at=timezone.now())

        with patch("apps.documents.views.citizen.default_storage") as mock_storage:
            mock_storage.open.return_value = BytesIO(b"deleted bytes")
            response = self.client.get(self._url())

        self.assertEqual(response.status_code, 404)
        mock_storage.open.assert_not_called()

    def test_still_active_document_is_served(self) -> None:
        """Control: an untouched ACTIVE document still downloads normally."""
        with patch("apps.documents.views.citizen.default_storage") as mock_storage:
            mock_storage.open.return_value = BytesIO(b"%PDF-1.4 clean")
            response = self.client.get(self._url())

        self.assertEqual(response.status_code, 200)
        mock_storage.open.assert_called_once()

    # -- L-2: presigned redirect TTL parity with the DRF path -----------------

    def _large_doc_and_token(self):
        from django.conf import settings as django_settings

        from apps.documents.models import DocumentAccessToken

        proxy_threshold = django_settings.CIVICOS.get("DOCUMENT_PROXY_MAX_BYTES", 1 * 1024 * 1024)
        large_doc = make_document(self.user, self.category, size_bytes=proxy_threshold + 1)
        token = DocumentAccessToken.objects.create(
            document=large_doc,
            issued_to=self.user,
            expires_at=timezone.now() + timezone.timedelta(seconds=300),
        )
        return large_doc, token

    def _s3_settings(self):
        return self.settings(
            STORAGES={
                "default": {
                    "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
                    "OPTIONS": {
                        "bucket_name": "test-bucket",
                        "region_name": "ca-central-1",
                    },
                }
            }
        )

    def test_large_file_redirect_uses_short_presigned_ttl_on_s3(self) -> None:
        """
        L-2: on an S3 backend the redirect URL is generated by
        generate_presigned_download_url() with the 300s
        DOCUMENT_PRESIGNED_URL_TTL_SECONDS TTL — matching the DRF path — rather
        than default_storage.url()'s AWS_QUERYSTRING_EXPIRE (3600s) default.
        """
        large_doc, token = self._large_doc_and_token()
        signed = "https://s3.example.com/bucket/key?X-Amz-Expires=300&X-Amz-Signature=abc"

        # NOTE: default_storage is deliberately NOT patched here. Under the S3
        # STORAGES override the backend class cannot even be instantiated in
        # the test environment, so any fall-through to default_storage.url()
        # would raise InvalidStorageError and fail this test — which is exactly
        # the assertion we want ("the S3 path never touches default_storage").
        with (
            self._s3_settings(),
            patch(
                "apps.documents.views.citizen.generate_presigned_download_url",
                return_value=signed,
            ) as mock_presign,
        ):
            response = self.client.get(self._url(token.token))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], signed)
        mock_presign.assert_called_once_with(
            storage_key=large_doc.storage_key,
            ttl_seconds=300,
        )

    def test_presigned_ttl_matches_configured_setting(self) -> None:
        """The TTL is read from CIVICOS, not hardcoded at the call site."""
        from django.conf import settings as django_settings

        _large_doc, token = self._large_doc_and_token()
        civicos = {**django_settings.CIVICOS, "DOCUMENT_PRESIGNED_URL_TTL_SECONDS": 120}

        with (
            self._s3_settings(),
            self.settings(CIVICOS=civicos),
            patch(
                "apps.documents.views.citizen.generate_presigned_download_url",
                return_value="https://s3.example.com/signed",
            ) as mock_presign,
        ):
            response = self.client.get(self._url(token.token))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(mock_presign.call_args.kwargs["ttl_seconds"], 120)

    def test_non_s3_backend_still_uses_default_storage_url(self) -> None:
        """
        Dev/test FileSystemStorage has no presigned-URL concept — the view must
        keep using default_storage.url() there (which returns a /media/ path
        with no query-string expiry at all).
        """
        large_doc, token = self._large_doc_and_token()

        with (
            patch("apps.documents.views.citizen.default_storage") as mock_storage,
            patch("apps.documents.views.citizen.generate_presigned_download_url") as mock_presign,
        ):
            mock_storage.url.return_value = "/media/some/path.bin"
            response = self.client.get(self._url(token.token))

        self.assertEqual(response.status_code, 302)
        mock_storage.url.assert_called_once_with(large_doc.storage_key)
        mock_presign.assert_not_called()
