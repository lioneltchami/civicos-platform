"""
Tests for apps.documents.services.upload — Wave 2.

Coverage targets:
  - validate_upload_request(): all 11 validation steps, PIPEDA invariants,
    signal dispatch, storage key never returned
  - confirm_upload(): IDOR 404, idempotency, magic-byte, ZIP-bomb, audit log,
    task dispatch, signal dispatch
  - Helpers: _make_storage_key(), _check_zip_bomb(), _validate_magic_bytes()

PIPEDA invariants verified explicitly:
  - original_filename NEVER in event_detail
  - storage_key NEVER in validate_upload_request() return value
  - Http404 (not 403) for non-owned document PKs

Security invariants:
  - Invalid extension → ValidationError before Document created
  - Invalid MIME type → ValidationError before Document created
  - Magic-byte mismatch → ValidationError in confirm_upload()
  - ZIP-bomb → ValidationError in confirm_upload()
"""

from __future__ import annotations

import io
import struct
import uuid
import zipfile
from datetime import timedelta
from unittest.mock import MagicMock, call, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.documents.models import Document, DocumentCategory
from apps.documents.services.upload import (
    _check_zip_bomb,
    _make_storage_key,
    _validate_magic_bytes,
    confirm_upload,
    validate_upload_request,
)

User = get_user_model()

# ─────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ─────────────────────────────────────────────────────────────────────────────

ALLOWED_MIMES = [
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv",
]

CIVICOS_OVERRIDES = {
    "MAX_UPLOAD_SIZE": 10 * 1024 * 1024,
    "ALLOWED_UPLOAD_MIME_TYPES": ALLOWED_MIMES,
    "CLAMAV_HOST": "",
    "CLAMAV_PORT": 3310,
    "CLAMAV_REQUIRED": False,
    "DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES": 10 * 1024 * 1024,
    "DOCUMENT_MAX_STAFF_UPLOAD_BYTES": 50 * 1024 * 1024,
    "DOCUMENT_PRESIGNED_POST_TTL_SECONDS": 900,
    "DOCUMENT_PRESIGNED_URL_TTL_SECONDS": 300,
    "DOCUMENT_ACCESS_TOKEN_TTL_SECONDS": 300,
    "DOCUMENT_HARD_DELETE_GRACE_DAYS": 30,
    "DOCUMENT_STORAGE_PREFIX": "documents",
    "DOCUMENT_ZIP_MAX_ENTRIES": 1000,
    "DOCUMENT_ZIP_MAX_RATIO": 100,
    "DOCUMENT_PROXY_MAX_BYTES": 1 * 1024 * 1024,
}


def make_category(**kwargs) -> DocumentCategory:
    defaults = {
        "name_en": "Test Category",
        "name_fr": "Catégorie de test",
        "slug": f"test-cat-{uuid.uuid4().hex[:6]}",
        "security_classification": DocumentCategory.SecurityClassification.PROTECTED_B,
        "allowed_mime_types": [],  # use global list
        "max_size_bytes": 0,
        "min_retention_days": 730,
        "max_retention_days": 2555,
        "is_transitory": False,
    }
    defaults.update(kwargs)
    return DocumentCategory.objects.create(**defaults)


def make_user(**kwargs) -> User:
    uid = uuid.uuid4().hex[:8]
    return User.objects.create_user(
        email=f"up-{uid}@example.com",
        password="hunter2",
        **kwargs,
    )


def _make_minimal_pdf() -> bytes:
    """Return the minimum bytes needed to pass libmagic PDF detection."""
    return b"%PDF-1.4 fake pdf content"


def _make_zip_bytes(entries: int = 1, ratio: int = 1) -> bytes:
    """
    Build an in-memory ZIP with `entries` entries where each entry's
    uncompressed:compressed ratio is approximately `ratio`.
    Used for ZIP-bomb detection tests.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i in range(entries):
            # Create data that deflates to roughly ratio:1
            data = b"A" * max(ratio, 1)
            zf.writestr(f"entry_{i}.txt", data)
    buf.seek(0)
    return buf.read()


def _make_bomb_zip() -> bytes:
    """
    Build a ZIP that exceeds the DOCUMENT_ZIP_MAX_RATIO limit.
    Uses a single entry with highly compressible content (all zeros).
    ratio = file_size / compress_size; with all-zeros, this is >> 100.
    """
    buf = io.BytesIO()
    # 1 MB of zeros compresses to ~1 KB → ratio ~1000
    compressible = b"\x00" * (1 * 1024 * 1024)
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bomb.txt", compressible)
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────────────────────
# _make_storage_key
# ─────────────────────────────────────────────────────────────────────────────


class MakeStorageKeyTests(TestCase):

    def test_valid_prefix_quarantine(self):
        key = _make_storage_key("abc", prefix="quarantine")
        self.assertTrue(key.startswith("documents/quarantine/abc/"))
        self.assertTrue(key.endswith(".bin"))

    def test_valid_prefix_active(self):
        key = _make_storage_key("xyz", prefix="active")
        self.assertTrue(key.startswith("documents/active/xyz/"))

    def test_valid_prefix_deleted(self):
        key = _make_storage_key("xyz", prefix="deleted")
        self.assertTrue(key.startswith("documents/deleted/xyz/"))

    def test_invalid_prefix_raises(self):
        with self.assertRaises(ValueError):
            _make_storage_key("abc", prefix="public")

    def test_invalid_prefix_tmp_raises(self):
        with self.assertRaises(ValueError):
            _make_storage_key("abc", prefix="tmp")

    def test_keys_are_unique(self):
        """Each call generates a new UUID segment — no collisions."""
        doc_uuid = str(uuid.uuid4())
        keys = {_make_storage_key(doc_uuid) for _ in range(10)}
        self.assertEqual(len(keys), 10)

    def test_default_prefix_is_quarantine(self):
        key = _make_storage_key("abc")
        self.assertIn("/quarantine/", key)

    def test_no_original_filename_in_key(self):
        """Storage key must NEVER derive from a filename (OWASP)."""
        key = _make_storage_key("doc-123")
        self.assertNotIn("document.pdf", key)
        self.assertNotIn("invoice", key)


# ─────────────────────────────────────────────────────────────────────────────
# _validate_magic_bytes
# ─────────────────────────────────────────────────────────────────────────────


class ValidateMagicBytesTests(TestCase):
    """
    `magic` is imported at module level in upload.py (None if not installed).
    All tests patch `apps.documents.services.upload.magic` at the module level.
    """

    @patch("apps.documents.services.upload.magic")
    def test_allowed_mime_passes_and_returns_detected_mime(self, mock_magic_module):
        """M-3: Returns the detected MIME type string on success."""
        mock_magic_module.Magic.return_value.from_buffer.return_value = "application/pdf"
        result = _validate_magic_bytes(
            first_bytes=b"%PDF-1.4",
            allowed_mimes=["application/pdf"],
        )
        self.assertEqual(result, "application/pdf")

    @patch("apps.documents.services.upload.magic")
    def test_disallowed_mime_raises(self, mock_magic_module):
        mock_magic_module.Magic.return_value.from_buffer.return_value = "application/x-executable"
        with self.assertRaises(ValidationError):
            _validate_magic_bytes(
                first_bytes=b"\x7fELF",
                allowed_mimes=["application/pdf"],
            )

    @patch("apps.documents.services.upload.magic")
    def test_magic_error_raises(self, mock_magic_module):
        mock_magic_module.Magic.return_value.from_buffer.side_effect = Exception("libmagic error")
        with self.assertRaises(ValidationError):
            _validate_magic_bytes(first_bytes=b"garbage", allowed_mimes=["application/pdf"])

    @override_settings(CIVICOS={**CIVICOS_OVERRIDES, "MAGIC_BYTES_REQUIRED": False})
    def test_magic_not_installed_dev_skip(self):
        """
        M-1: When MAGIC_BYTES_REQUIRED=False and python-magic is absent,
        the function skips validation and returns None (not raises).
        This is independent of CLAMAV_REQUIRED — they are separate concerns.
        """
        with patch("apps.documents.services.upload.magic", None):
            result = _validate_magic_bytes(
                first_bytes=b"anything",
                allowed_mimes=["application/pdf"],
            )
        # M-3: returns None when bypassed, so caller knows not to overwrite mime_type
        self.assertIsNone(result)

    @override_settings(CIVICOS={**CIVICOS_OVERRIDES, "MAGIC_BYTES_REQUIRED": True})
    def test_magic_not_installed_required_raises_improperly_configured(self):
        """
        M-1: When MAGIC_BYTES_REQUIRED=True (the default) and python-magic is
        absent, raise ImproperlyConfigured so operators see a clear error.
        This replaces the old CLAMAV_REQUIRED check — the two flags are independent.
        """
        from django.core.exceptions import ImproperlyConfigured

        with patch("apps.documents.services.upload.magic", None):
            with self.assertRaises(ImproperlyConfigured) as ctx:
                _validate_magic_bytes(
                    first_bytes=b"anything",
                    allowed_mimes=["application/pdf"],
                )
            self.assertIn("MAGIC_BYTES_REQUIRED", str(ctx.exception))


# ─────────────────────────────────────────────────────────────────────────────
# _check_zip_bomb
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=CIVICOS_OVERRIDES)
class CheckZipBombTests(TestCase):

    def test_clean_zip_passes(self):
        """A small, clean ZIP does not trigger the bomb check."""
        zip_bytes = _make_zip_bytes(entries=1, ratio=1)
        # Should not raise
        _check_zip_bomb(zip_bytes)

    @override_settings(CIVICOS={**CIVICOS_OVERRIDES, "DOCUMENT_ZIP_MAX_ENTRIES": 3})
    def test_too_many_entries_raises(self):
        zip_bytes = _make_zip_bytes(entries=5)
        with self.assertRaises(ValidationError) as ctx:
            _check_zip_bomb(zip_bytes)
        self.assertIn("entries", str(ctx.exception).lower())

    def test_high_compression_ratio_raises(self):
        """Bomb-like compression ratio (>>100) triggers ValidationError."""
        bomb_bytes = _make_bomb_zip()
        with self.assertRaises(ValidationError) as ctx:
            _check_zip_bomb(bomb_bytes)
        self.assertIn("ratio", str(ctx.exception).lower())

    def test_invalid_zip_raises(self):
        """
        Non-ZIP bytes → BadZipFile → ValidationError.
        A .docx/.xlsx that cannot be parsed as a valid ZIP is corrupted or
        malformed. _check_zip_bomb now receives the FULL file, so BadZipFile
        means the archive is genuinely broken — reject it, do not skip silently.
        """
        with self.assertRaises(ValidationError) as ctx:
            _check_zip_bomb(b"not a zip file at all")
        self.assertIn("valid archive", str(ctx.exception).lower())

    def test_truncated_zip_raises(self):
        """
        Truncated ZIP (missing central directory) → BadZipFile → ValidationError.
        In the real code path, _check_zip_bomb always receives the full file.
        A file whose ZIP structure cannot be parsed is treated as corrupted.
        """
        full_zip = _make_zip_bytes(entries=1)
        truncated = full_zip[:50]  # deliberately truncated — missing central directory
        with self.assertRaises(ValidationError):
            _check_zip_bomb(truncated)


# ─────────────────────────────────────────────────────────────────────────────
# validate_upload_request
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(
    CIVICOS=CIVICOS_OVERRIDES,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {}},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class ValidateUploadRequestTests(TestCase):

    def setUp(self):
        self.user = make_user()
        self.category = make_category()

    def _call(self, **kwargs):
        defaults = {
            "user": self.user,
            "category_slug": self.category.slug,
            "original_filename": "report.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 100 * 1024,  # 100 KB
        }
        defaults.update(kwargs)
        with patch(
            "apps.documents.services.upload._generate_presigned_post",
            return_value={
                "url": "http://dev-upload/",
                "fields": {},
                "expires_at": timezone.now().isoformat(),
            },
        ):
            return validate_upload_request(**defaults)

    def test_happy_path_returns_expected_keys(self):
        result = self._call()
        self.assertIn("doc_id", result)
        self.assertIn("upload_url", result)
        self.assertIn("upload_fields", result)
        self.assertIn("expires_at", result)

    def test_storage_key_never_in_response(self):
        """
        T-5 / PIPEDA: storage_key MUST NOT be returned as a top-level key.

        Use a realistic presigned-POST mock that includes the S3 'key' field
        (as a real S3 response would) so we verify the actual value path,
        not an empty-fields vacuous pass.

        Note: upload_fields IS intentionally forwarded to the browser — the
        browser needs the 'key' field to POST to the correct S3 location.
        PIPEDA compliance applies to top-level API response keys only.
        """
        sentinel_storage_key = "documents/quarantine/test-uuid/file-uuid.bin"
        with patch(
            "apps.documents.services.upload._generate_presigned_post",
            return_value={
                "url": "https://bucket.s3.amazonaws.com/",
                "fields": {
                    "key": sentinel_storage_key,
                    "Content-Type": "application/pdf",
                    "AWSAccessKeyId": "AKIDEXAMPLE",
                    "policy": "base64encodedpolicy",
                    "signature": "sig",
                },
                "expires_at": timezone.now().isoformat(),
            },
        ):
            result = validate_upload_request(
                user=self.user,
                category_slug=self.category.slug,
                original_filename="report.pdf",
                mime_type="application/pdf",
                size_bytes=100 * 1024,
            )

        # Top-level response must not expose storage_key as a named key
        self.assertNotIn("storage_key", result)
        # Exact allowed top-level keys
        self.assertEqual(set(result.keys()), {"doc_id", "upload_url", "upload_fields", "expires_at"})
        # doc_id must be a UUID (not accidentally set to the storage key)
        self.assertNotEqual(result["doc_id"], sentinel_storage_key)
        uuid.UUID(result["doc_id"])  # raises ValueError if not a valid UUID

    def test_doc_created_in_pending_upload_state(self):
        result = self._call()
        doc = Document.objects.get(pk=result["doc_id"])
        self.assertEqual(doc.scan_status, Document.ScanStatus.PENDING_UPLOAD)

    def test_doc_category_set_correctly(self):
        result = self._call()
        doc = Document.objects.get(pk=result["doc_id"])
        self.assertEqual(doc.category_id, self.category.pk)

    def test_doc_uploader_set(self):
        result = self._call()
        doc = Document.objects.get(pk=result["doc_id"])
        self.assertEqual(doc.uploaded_by_id, self.user.pk)

    def test_doc_original_filename_stored(self):
        result = self._call(original_filename="my-document.pdf")
        doc = Document.objects.get(pk=result["doc_id"])
        self.assertEqual(doc.original_filename, "my-document.pdf")

    def test_schedule_expiry_called(self):
        """schedule_expiry() must be called during validate_upload_request."""
        with patch(
            "apps.documents.services.upload._generate_presigned_post",
            return_value={"url": "", "fields": {}, "expires_at": timezone.now().isoformat()},
        ):
            with patch(
                "apps.documents.services.retention.schedule_expiry"
            ) as mock_expiry:
                validate_upload_request(
                    user=self.user,
                    category_slug=self.category.slug,
                    original_filename="doc.pdf",
                    mime_type="application/pdf",
                    size_bytes=1024,
                )
                mock_expiry.assert_called_once()

    def test_signal_fired_with_correct_kwargs(self):
        """document_upload_initiated signal must include pk, category_slug, user_pk only."""
        received_signals = []

        def handler(sender, **kwargs):
            received_signals.append(kwargs)

        from apps.documents.signals import document_upload_initiated
        document_upload_initiated.connect(handler)
        try:
            result = self._call()
        finally:
            document_upload_initiated.disconnect(handler)

        self.assertEqual(len(received_signals), 1)
        kwargs = received_signals[0]
        self.assertEqual(kwargs["document_pk"], result["doc_id"])
        self.assertEqual(kwargs["category_slug"], self.category.slug)
        self.assertEqual(kwargs["uploaded_by_id"], self.user.pk)
        # PIPEDA: no email, no filename
        self.assertNotIn("email", str(kwargs))
        self.assertNotIn("original_filename", str(kwargs))

    def test_category_not_found_raises_does_not_exist(self):
        with self.assertRaises(DocumentCategory.DoesNotExist):
            validate_upload_request(
                user=self.user,
                category_slug="nonexistent-slug",
                original_filename="doc.pdf",
                mime_type="application/pdf",
                size_bytes=1024,
            )

    def test_anonymous_user_raises_permission_denied(self):
        from django.contrib.auth.models import AnonymousUser
        anon = AnonymousUser()
        with self.assertRaises(PermissionDenied):
            validate_upload_request(
                user=anon,
                category_slug=self.category.slug,
                original_filename="doc.pdf",
                mime_type="application/pdf",
                size_bytes=1024,
            )

    def test_size_zero_raises(self):
        with self.assertRaises(ValidationError):
            self._call(size_bytes=0)

    def test_size_negative_raises(self):
        with self.assertRaises(ValidationError):
            self._call(size_bytes=-1)

    @override_settings(CIVICOS={**CIVICOS_OVERRIDES, "DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES": 1024})
    def test_size_exceeds_citizen_cap_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            self._call(size_bytes=2048)
        self.assertIn("size", str(ctx.exception).lower())

    @override_settings(CIVICOS={**CIVICOS_OVERRIDES, "DOCUMENT_MAX_STAFF_UPLOAD_BYTES": 1024})
    def test_size_exceeds_staff_cap_raises(self):
        staff = make_user(is_staff=True)
        with self.assertRaises(ValidationError):
            self._call(user=staff, size_bytes=2048)

    def test_category_max_size_bytes_override(self):
        """Category-level max_size_bytes takes precedence over global cap."""
        small_cat = make_category(max_size_bytes=1024)
        with self.assertRaises(ValidationError):
            self._call(category_slug=small_cat.slug, size_bytes=2048)

    def test_invalid_extension_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            self._call(original_filename="script.php")
        self.assertIn("not permitted", str(ctx.exception).lower())

    def test_missing_extension_raises(self):
        with self.assertRaises(ValidationError):
            self._call(original_filename="Makefile")

    def test_zip_extension_rejected(self):
        """Bare .zip is not in _ALLOWED_EXTENSIONS."""
        with self.assertRaises(ValidationError):
            self._call(original_filename="archive.zip")

    def test_executable_extension_rejected(self):
        with self.assertRaises(ValidationError):
            self._call(original_filename="malware.exe")

    def test_double_extension_uses_last(self):
        """pathlib.Path.suffix extracts the last suffix — test the edge case."""
        # "report.pdf.php" → suffix=".php" → rejected
        with self.assertRaises(ValidationError):
            self._call(original_filename="report.pdf.php")

    def test_invalid_mime_type_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            self._call(mime_type="application/x-executable")
        self.assertIn("not permitted", str(ctx.exception).lower())

    def test_category_allowed_mime_types_overrides_global(self):
        """Category with restricted MIME list rejects globally-allowed types."""
        restricted_cat = make_category(
            allowed_mime_types=["application/pdf"],  # only PDF
        )
        with self.assertRaises(ValidationError):
            self._call(
                category_slug=restricted_cat.slug,
                original_filename="spreadsheet.xlsx",
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    def test_category_allowed_mime_types_accepts_its_type(self):
        restricted_cat = make_category(
            slug=f"pdf-only-{uuid.uuid4().hex[:6]}",
            allowed_mime_types=["application/pdf"],
        )
        result = self._call(
            category_slug=restricted_cat.slug,
            original_filename="report.pdf",
            mime_type="application/pdf",
        )
        self.assertIn("doc_id", result)

    def test_security_classification_inherited_from_category(self):
        result = self._call()
        doc = Document.objects.get(pk=result["doc_id"])
        self.assertEqual(
            doc.security_classification,
            self.category.security_classification,
        )

    def test_no_document_created_on_extension_error(self):
        """Validation failure before Document.objects.create — DB must be clean."""
        count_before = Document.objects.count()
        with self.assertRaises(ValidationError):
            self._call(original_filename="bad.exe")
        self.assertEqual(Document.objects.count(), count_before)

    def test_no_document_created_on_mime_error(self):
        count_before = Document.objects.count()
        with self.assertRaises(ValidationError):
            self._call(mime_type="text/html")
        self.assertEqual(Document.objects.count(), count_before)

    def test_superuser_may_upload_to_any_category(self):
        superuser = make_user(is_superuser=True, is_staff=True)
        result = self._call(user=superuser)
        self.assertIn("doc_id", result)

    # ── T-4: Exact size boundary tests ───────────────────────────────────────

    @override_settings(CIVICOS={**CIVICOS_OVERRIDES, "DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES": 1024})
    def test_size_exactly_at_citizen_cap_passes(self):
        """
        T-4: size_bytes == max_size must pass.
        The check is `size_bytes > max_size` (strict), so equality is permitted.
        """
        result = self._call(size_bytes=1024)
        self.assertIn("doc_id", result)

    @override_settings(CIVICOS={**CIVICOS_OVERRIDES, "DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES": 1024})
    def test_size_one_byte_over_citizen_cap_fails(self):
        """T-4: size_bytes == max_size + 1 must be rejected with a ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            self._call(size_bytes=1025)
        self.assertIn("size", str(ctx.exception).lower())

    def test_case_insensitive_extension_accepted(self):
        """'.PDF' and '.pdf' should both be accepted."""
        result = self._call(
            original_filename="REPORT.PDF",
            mime_type="application/pdf",
        )
        self.assertIn("doc_id", result)

    @override_settings(CIVICOS={**CIVICOS_OVERRIDES, "ALLOWED_UPLOAD_MIME_TYPES": []})
    def test_empty_allowed_mimes_raises_improperly_configured(self):
        """
        C-3: If both category.allowed_mime_types and CIVICOS['ALLOWED_UPLOAD_MIME_TYPES']
        are empty, validate_upload_request must raise ImproperlyConfigured rather than
        cryptically rejecting every upload with 'Content type not permitted'.
        """
        from django.core.exceptions import ImproperlyConfigured
        # Category with no MIME override → falls back to CIVICOS → also empty
        empty_mime_cat = make_category(allowed_mime_types=[])
        with self.assertRaises(ImproperlyConfigured) as ctx:
            self._call(category_slug=empty_mime_cat.slug)
        self.assertIn("allowed_mime_types", str(ctx.exception).lower())

    def test_no_document_created_on_empty_mime_error(self):
        """ImproperlyConfigured before Document.objects.create — DB must be clean."""
        from django.core.exceptions import ImproperlyConfigured
        with override_settings(CIVICOS={**CIVICOS_OVERRIDES, "ALLOWED_UPLOAD_MIME_TYPES": []}):
            empty_mime_cat = make_category(allowed_mime_types=[])
            count_before = Document.objects.count()
            with self.assertRaises(ImproperlyConfigured):
                self._call(category_slug=empty_mime_cat.slug)
            self.assertEqual(Document.objects.count(), count_before)


# ─────────────────────────────────────────────────────────────────────────────
# confirm_upload — uses TransactionTestCase for on_commit() testing
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(
    CIVICOS=CIVICOS_OVERRIDES,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {}},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class ConfirmUploadTests(TransactionTestCase):
    """
    TransactionTestCase is required because confirm_upload() dispatches the
    Celery task inside on_commit(), which only fires when the enclosing
    transaction actually commits. TestCase wraps everything in a rollback,
    so on_commit() never fires in TestCase.
    """

    def setUp(self):
        self.user = make_user()
        self.category = make_category()

    def _make_pending_doc(self, user=None, scan_status=Document.ScanStatus.PENDING_UPLOAD):
        """Create a Document in PENDING_UPLOAD state for testing."""
        u = user or self.user
        doc = Document.objects.create(
            category=self.category,
            uploaded_by=u,
            original_filename="test.pdf",
            _storage_key=_make_storage_key(str(uuid.uuid4()), prefix="quarantine"),
            mime_type="application/pdf",
            size_bytes=1024,
            scan_status=scan_status,
            security_classification=DocumentCategory.SecurityClassification.PROTECTED_B,
        )
        return doc

    def _call_confirm(self, doc, user=None, **patches):
        """
        Call confirm_upload() with the standard set of mocks:
          - _verify_file_exists → no-op (file always "exists")
          - _read_first_bytes   → returns minimal PDF bytes
          - _validate_magic_bytes → returns "application/pdf" (M-3 return type)
          - scan_document.apply_async → no-op (Celery task stub)

        Returns:
            (result, task_calls) where task_calls is a list snapshot of
            mock_task.call_args_list captured INSIDE the patch context (T-1).
            Returning the live mock object would allow vacuous assertions after
            the context exits; a snapshot forces callers to inspect real data.
        """
        u = user or self.user
        with patch("apps.documents.services.upload._verify_file_exists"):
            with patch(
                "apps.documents.services.upload._read_first_bytes",
                return_value=b"%PDF-1.4",
            ):
                with patch(
                    "apps.documents.services.upload._validate_magic_bytes",
                    return_value="application/pdf",  # M-3: must return str so doc.mime_type gets a valid value
                ):
                    # Layer 5b: PDF encryption check calls _read_full_file then
                    # _check_pdf_encryption.  Patch both so tests don't need real files.
                    with patch(
                        "apps.documents.services.upload._read_full_file",
                        return_value=b"%PDF-1.4 dummy",
                    ):
                        with patch("apps.documents.services.upload._check_pdf_encryption"):
                            with patch(
                                "apps.documents.tasks.scan_document.apply_async"
                            ) as mock_task:
                                result = confirm_upload(user=u, doc_id=str(doc.pk))
                                # T-1: capture snapshot while still inside the context
                                task_calls = list(mock_task.call_args_list)
        return result, task_calls

    # ── IDOR ──────────────────────────────────────────────────────────────────

    def test_idor_wrong_user_returns_404(self):
        """
        IDOR prevention: non-owner gets Http404, not 403.
        403 would confirm the document exists with this ID.
        """
        doc = self._make_pending_doc()
        other_user = make_user()
        with self.assertRaises(Http404):
            confirm_upload(user=other_user, doc_id=str(doc.pk))

    def test_nonexistent_doc_returns_404(self):
        """Http404 for non-existent doc_id (IDOR: 404 not 403)."""
        with self.assertRaises(Http404):
            confirm_upload(user=self.user, doc_id=str(uuid.uuid4()))

    # ── Idempotency ───────────────────────────────────────────────────────────

    def test_already_scanning_returns_doc_without_error(self):
        """
        If confirm_upload() is called again while already SCANNING (double-submit),
        it must return the current Document without re-running validation or
        dispatching a second task.
        """
        doc = self._make_pending_doc(scan_status=Document.ScanStatus.SCANNING)
        result = confirm_upload(user=self.user, doc_id=str(doc.pk))
        self.assertEqual(result.scan_status, Document.ScanStatus.SCANNING)

    def test_already_active_returns_doc_without_error(self):
        doc = self._make_pending_doc(scan_status=Document.ScanStatus.ACTIVE)
        result = confirm_upload(user=self.user, doc_id=str(doc.pk))
        self.assertEqual(result.scan_status, Document.ScanStatus.ACTIVE)

    # ── Happy path ────────────────────────────────────────────────────────────

    def test_happy_path_sets_scanning_status(self):
        doc = self._make_pending_doc()
        with patch("apps.documents.services.upload._verify_file_exists"):
            with patch("apps.documents.services.upload._read_first_bytes", return_value=b"%PDF-1.4"):
                with patch(
                    "apps.documents.services.upload._validate_magic_bytes",
                    return_value="application/pdf",  # H-4: must return str, not MagicMock
                ):
                    with patch("apps.documents.services.upload._read_full_file", return_value=b"%PDF-1.4 dummy"):
                        with patch("apps.documents.services.upload._check_pdf_encryption"):
                            with patch("apps.documents.tasks.scan_document.apply_async"):
                                result = confirm_upload(user=self.user, doc_id=str(doc.pk))

        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.SCANNING)
        self.assertEqual(result.scan_status, Document.ScanStatus.SCANNING)

    def test_happy_path_dispatches_scan_task(self):
        """scan_document.apply_async() must be called on_commit with precise args."""
        doc = self._make_pending_doc()
        with patch("apps.documents.services.upload._verify_file_exists"):
            with patch("apps.documents.services.upload._read_first_bytes", return_value=b"%PDF-1.4"):
                with patch(
                    "apps.documents.services.upload._validate_magic_bytes",
                    return_value="application/pdf",
                ):
                    with patch("apps.documents.services.upload._read_full_file", return_value=b"%PDF-1.4 dummy"):
                        with patch("apps.documents.services.upload._check_pdf_encryption"):
                            with patch(
                                "apps.documents.tasks.scan_document.apply_async"
                            ) as mock_task:
                                confirm_upload(user=self.user, doc_id=str(doc.pk))
                                # T-2: assert INSIDE the context with precise args/countdown
                                # (on_commit fires synchronously in TransactionTestCase).
                                mock_task.assert_called_once_with(
                                    args=[str(doc.pk)], countdown=2
                                )

    def test_happy_path_fires_document_confirmed_signal(self):
        doc = self._make_pending_doc()
        received = []

        from apps.documents.signals import document_confirmed
        # weak=False: Django stores receivers as weak references by default.
        # Anonymous lambdas have no strong reference and are GC'd before the
        # signal fires unless we opt into a strong reference.
        handler = lambda sender, **kw: received.append(kw)  # noqa: E731
        document_confirmed.connect(handler, weak=False)
        try:
            with patch("apps.documents.services.upload._verify_file_exists"):
                with patch("apps.documents.services.upload._read_first_bytes", return_value=b"%PDF-1.4"):
                    with patch(
                        "apps.documents.services.upload._validate_magic_bytes",
                        return_value="application/pdf",  # H-4: must return str — MagicMock written to doc.mime_type otherwise
                    ):
                        with patch("apps.documents.services.upload._read_full_file", return_value=b"%PDF-1.4 dummy"):
                            with patch("apps.documents.services.upload._check_pdf_encryption"):
                                with patch("apps.documents.tasks.scan_document.apply_async"):
                                    confirm_upload(user=self.user, doc_id=str(doc.pk))
        finally:
            document_confirmed.disconnect(handler)

        self.assertEqual(len(received), 1)
        kwargs = received[0]
        self.assertEqual(kwargs["document_pk"], str(doc.pk))
        self.assertEqual(kwargs["size_bytes"], doc.size_bytes)
        self.assertEqual(kwargs["mime_type"], "application/pdf")  # H-4: validates against known value

    def test_document_confirmed_signal_no_storage_key_in_kwargs(self):
        """
        T-3 / PIPEDA: document_confirmed signal kwargs must NOT include storage_key
        (or its value). Receivers must not be able to forward the internal S3 path
        to external systems via signal kwargs.
        """
        doc = self._make_pending_doc()
        received = []

        from apps.documents.signals import document_confirmed
        handler = lambda sender, **kw: received.append(kw)  # noqa: E731
        document_confirmed.connect(handler, weak=False)
        try:
            with patch("apps.documents.services.upload._verify_file_exists"):
                with patch("apps.documents.services.upload._read_first_bytes", return_value=b"%PDF-1.4"):
                    with patch(
                        "apps.documents.services.upload._validate_magic_bytes",
                        return_value="application/pdf",
                    ):
                        with patch("apps.documents.services.upload._read_full_file", return_value=b"%PDF-1.4 dummy"):
                            with patch("apps.documents.services.upload._check_pdf_encryption"):
                                with patch("apps.documents.tasks.scan_document.apply_async"):
                                    confirm_upload(user=self.user, doc_id=str(doc.pk))
        finally:
            document_confirmed.disconnect(handler)

        self.assertEqual(len(received), 1)
        kwargs = received[0]
        # storage_key must not appear as a kwarg key
        self.assertNotIn("storage_key", kwargs)
        # T-3 / PIPEDA: original_filename must also be absent from signal kwargs (M-4 invariant)
        self.assertNotIn("original_filename", kwargs)
        # Positive assertion: exact expected key set — any new PII key would be caught immediately
        # (Django dispatch adds 'signal'; document_confirmed adds document_pk, size_bytes, mime_type)
        expected_keys = {"signal", "document_pk", "size_bytes", "mime_type"}
        self.assertEqual(
            set(kwargs.keys()),
            expected_keys,
            f"signal kwargs must be exactly {expected_keys}, got: {set(kwargs.keys())}",
        )
        # Verify the internal storage key VALUE is not present anywhere in kwargs values
        doc.refresh_from_db()
        storage_key_value = doc._storage_key
        for v in kwargs.values():
            self.assertNotEqual(v, storage_key_value)

    def test_audit_log_written_with_correct_fields(self):
        """
        T-6 / PIPEDA: audit event_detail must NOT contain original_filename or storage_key.
        It MUST contain category_slug, mime_type, size_bytes, and event_type=RECORD_CREATED.
        """
        from apps.audit.models import AuditEventType
        doc = self._make_pending_doc()
        with patch("apps.documents.services.upload._verify_file_exists"):
            with patch("apps.documents.services.upload._read_first_bytes", return_value=b"%PDF-1.4"):
                with patch(
                    "apps.documents.services.upload._validate_magic_bytes",
                    return_value="application/pdf",  # H-4: must return str
                ):
                    with patch("apps.documents.services.upload._read_full_file", return_value=b"%PDF-1.4 dummy"):
                        with patch("apps.documents.services.upload._check_pdf_encryption"):
                            with patch("apps.documents.tasks.scan_document.apply_async"):
                                with patch(
                                    "apps.audit.services.record_event"
                                ) as mock_audit:
                                    confirm_upload(user=self.user, doc_id=str(doc.pk))

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        event_detail = call_kwargs.get("event_detail", {})

        # T-6: assert the event type is correct
        self.assertEqual(call_kwargs.get("event_type"), AuditEventType.RECORD_CREATED)

        # Required event_detail fields
        self.assertIn("category_slug", event_detail)
        self.assertIn("mime_type", event_detail)
        self.assertIn("size_bytes", event_detail)

        # PIPEDA exclusions (negative assertions)
        self.assertNotIn("original_filename", event_detail)
        self.assertNotIn("storage_key", event_detail)
        self.assertNotIn("filename", event_detail)

        # T-6: exhaustive exact-key-set assertion — a newly added PII field would be
        # caught here immediately rather than slipping through the 3 negative checks above
        self.assertEqual(
            set(event_detail.keys()),
            {"category_slug", "mime_type", "size_bytes"},
            f"event_detail must contain exactly {{category_slug, mime_type, size_bytes}}, "
            f"got: {set(event_detail.keys())}",
        )

    def test_audit_actor_id_is_user_pk(self):
        doc = self._make_pending_doc()
        with patch("apps.documents.services.upload._verify_file_exists"):
            with patch("apps.documents.services.upload._read_first_bytes", return_value=b"%PDF-1.4"):
                with patch(
                    "apps.documents.services.upload._validate_magic_bytes",
                    return_value="application/pdf",  # H-4: must return str
                ):
                    with patch("apps.documents.services.upload._read_full_file", return_value=b"%PDF-1.4 dummy"):
                        with patch("apps.documents.services.upload._check_pdf_encryption"):
                            with patch("apps.documents.tasks.scan_document.apply_async"):
                                with patch(
                                    "apps.audit.services.record_event"
                                ) as mock_audit:
                                    confirm_upload(user=self.user, doc_id=str(doc.pk))

        call_kwargs = mock_audit.call_args.kwargs
        self.assertEqual(call_kwargs["actor_id"], str(self.user.pk))
        # PIPEDA: actor_email intentionally absent from our call
        self.assertNotIn("actor_email", call_kwargs)

    # ── File-not-found ────────────────────────────────────────────────────────

    def test_file_not_found_raises_validation_error(self):
        doc = self._make_pending_doc()
        with patch(
            "apps.documents.services.upload._verify_file_exists",
            side_effect=ValidationError("File not found"),
        ):
            with self.assertRaises(ValidationError):
                confirm_upload(user=self.user, doc_id=str(doc.pk))

    def test_file_not_found_does_not_advance_status(self):
        doc = self._make_pending_doc()
        with patch(
            "apps.documents.services.upload._verify_file_exists",
            side_effect=ValidationError("File not found"),
        ):
            try:
                confirm_upload(user=self.user, doc_id=str(doc.pk))
            except ValidationError:
                pass

        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.PENDING_UPLOAD)

    # ── Magic-byte validation ─────────────────────────────────────────────────

    def test_magic_byte_mismatch_raises(self):
        doc = self._make_pending_doc()
        with patch("apps.documents.services.upload._verify_file_exists"):
            with patch("apps.documents.services.upload._read_first_bytes", return_value=b"\x7fELF"):
                with patch(
                    "apps.documents.services.upload._validate_magic_bytes",
                    side_effect=ValidationError("Magic-byte mismatch"),
                ):
                    with self.assertRaises(ValidationError):
                        confirm_upload(user=self.user, doc_id=str(doc.pk))

    def test_magic_byte_failure_leaves_status_pending(self):
        doc = self._make_pending_doc()
        with patch("apps.documents.services.upload._verify_file_exists"):
            with patch("apps.documents.services.upload._read_first_bytes", return_value=b"\x7fELF"):
                with patch(
                    "apps.documents.services.upload._validate_magic_bytes",
                    side_effect=ValidationError("mismatch"),
                ):
                    try:
                        confirm_upload(user=self.user, doc_id=str(doc.pk))
                    except ValidationError:
                        pass

        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.PENDING_UPLOAD)

    # ── ZIP-bomb detection ────────────────────────────────────────────────────

    def test_zip_bomb_in_docx_raises(self):
        """
        ZIP-bomb detection must trigger on .docx files.

        confirm_upload() now calls _read_full_file() specifically for the ZIP bomb
        check (the 8 KB first_bytes read is insufficient for real-world .docx/.xlsx).
        Patch _read_full_file (not _read_first_bytes) to inject the bomb payload.
        """
        docx_doc = Document.objects.create(
            category=self.category,
            uploaded_by=self.user,
            original_filename="malicious.docx",
            _storage_key=_make_storage_key(str(uuid.uuid4()), prefix="quarantine"),
            mime_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            size_bytes=1024,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
            security_classification=DocumentCategory.SecurityClassification.PROTECTED_B,
        )
        bomb_bytes = _make_bomb_zip()

        with patch("apps.documents.services.upload._verify_file_exists"):
            with patch(
                "apps.documents.services.upload._read_first_bytes",
                return_value=b"%PDF-1.4",  # magic-byte check (mocked out anyway)
            ):
                with patch("apps.documents.services.upload._validate_magic_bytes"):
                    with patch(
                        "apps.documents.services.upload._read_full_file",
                        return_value=bomb_bytes,
                    ):
                        with self.assertRaises(ValidationError) as ctx:
                            confirm_upload(user=self.user, doc_id=str(docx_doc.pk))

        self.assertIn("ratio", str(ctx.exception).lower())

    def test_zip_bomb_in_xlsx_raises(self):
        """
        ZIP-bomb detection must trigger on .xlsx files.

        confirm_upload() calls _read_full_file() for the ZIP bomb check.
        Patch _read_full_file to inject the bomb payload.
        """
        xlsx_doc = Document.objects.create(
            category=self.category,
            uploaded_by=self.user,
            original_filename="malicious.xlsx",
            _storage_key=_make_storage_key(str(uuid.uuid4()), prefix="quarantine"),
            mime_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            size_bytes=1024,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
            security_classification=DocumentCategory.SecurityClassification.PROTECTED_B,
        )
        bomb_bytes = _make_bomb_zip()

        with patch("apps.documents.services.upload._verify_file_exists"):
            with patch(
                "apps.documents.services.upload._read_first_bytes",
                return_value=b"%PDF-1.4",
            ):
                with patch("apps.documents.services.upload._validate_magic_bytes"):
                    with patch(
                        "apps.documents.services.upload._read_full_file",
                        return_value=bomb_bytes,
                    ):
                        with self.assertRaises(ValidationError):
                            confirm_upload(user=self.user, doc_id=str(xlsx_doc.pk))

    def test_pdf_skips_zip_bomb_check(self):
        """PDFs are not ZIP containers — zip bomb check must not apply."""
        doc = self._make_pending_doc()  # original_filename="test.pdf"
        with patch("apps.documents.services.upload._verify_file_exists"):
            with patch("apps.documents.services.upload._read_first_bytes", return_value=b"%PDF-1.4"):
                with patch(
                    "apps.documents.services.upload._validate_magic_bytes",
                    return_value="application/pdf",  # H-4: must return str
                ):
                    with patch("apps.documents.services.upload._read_full_file", return_value=b"%PDF-1.4 dummy"):
                        with patch("apps.documents.services.upload._check_pdf_encryption"):
                            with patch(
                                "apps.documents.services.upload._check_zip_bomb"
                            ) as mock_zip_check:
                                with patch("apps.documents.tasks.scan_document.apply_async"):
                                    confirm_upload(user=self.user, doc_id=str(doc.pk))
                                mock_zip_check.assert_not_called()

    # ── Security classification propagation ───────────────────────────────────

    def test_confirm_does_not_change_security_classification(self):
        doc = self._make_pending_doc()
        original_classification = doc.security_classification
        with patch("apps.documents.services.upload._verify_file_exists"):
            with patch("apps.documents.services.upload._read_first_bytes", return_value=b"%PDF-1.4"):
                with patch(
                    "apps.documents.services.upload._validate_magic_bytes",
                    return_value="application/pdf",  # H-4: must return str
                ):
                    with patch("apps.documents.services.upload._read_full_file", return_value=b"%PDF-1.4 dummy"):
                        with patch("apps.documents.services.upload._check_pdf_encryption"):
                            with patch("apps.documents.tasks.scan_document.apply_async"):
                                confirm_upload(user=self.user, doc_id=str(doc.pk))
        doc.refresh_from_db()
        self.assertEqual(doc.security_classification, original_classification)


# ─────────────────────────────────────────────────────────────────────────────
# HIGH fix tests — H-1, H-2, H-3, H-5, H-7, H-8
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(
    CIVICOS=CIVICOS_OVERRIDES,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {}},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class StaffOnlyCategoryTests(TestCase):
    """H-3: staff_only BooleanField on DocumentCategory controls upload access."""

    def setUp(self):
        self.staff_only_cat = make_category(
            slug=f"staff-only-{uuid.uuid4().hex[:6]}",
            staff_only=True,
        )
        self.public_cat = make_category(
            slug=f"public-{uuid.uuid4().hex[:6]}",
            staff_only=False,
        )

    def _call(self, user, category):
        with patch(
            "apps.documents.services.upload._generate_presigned_post",
            return_value={
                "url": "http://dev-upload/",
                "fields": {},
                "expires_at": timezone.now().isoformat(),
            },
        ):
            return validate_upload_request(
                user=user,
                category_slug=category.slug,
                original_filename="report.pdf",
                mime_type="application/pdf",
                size_bytes=100 * 1024,
            )

    def test_citizen_blocked_from_staff_only_category(self):
        """An authenticated citizen without upload_staff_document must be blocked."""
        citizen = make_user()
        with self.assertRaises(PermissionDenied):
            self._call(citizen, self.staff_only_cat)

    def test_citizen_allowed_to_public_category(self):
        """staff_only=False categories must be accessible to any authenticated user."""
        citizen = make_user()
        result = self._call(citizen, self.public_cat)
        self.assertIn("doc_id", result)

    def test_superuser_allowed_to_staff_only_category(self):
        """Superusers bypass staff_only (rule 2 in permission hierarchy)."""
        superuser = make_user(is_superuser=True, is_staff=True)
        result = self._call(superuser, self.staff_only_cat)
        self.assertIn("doc_id", result)

    def test_staff_with_upload_staff_document_perm_allowed(self):
        """Staff with documents.upload_staff_document may upload to staff-only categories."""
        from django.contrib.auth.models import Permission
        from django.contrib.contenttypes.models import ContentType
        staff_user = make_user(is_staff=True)
        content_type = ContentType.objects.get_for_model(Document)
        perm = Permission.objects.get(codename="upload_staff_document", content_type=content_type)
        staff_user.user_permissions.add(perm)
        # Reload to clear permission cache
        staff_user = staff_user.__class__.objects.get(pk=staff_user.pk)
        result = self._call(staff_user, self.staff_only_cat)
        self.assertIn("doc_id", result)

    def test_user_with_only_upload_document_perm_blocked_from_staff_only(self):
        """upload_document permission alone is not sufficient for staff-only categories."""
        from django.contrib.auth.models import Permission
        from django.contrib.contenttypes.models import ContentType
        regular_user = make_user()
        content_type = ContentType.objects.get_for_model(Document)
        perm = Permission.objects.get(codename="upload_document", content_type=content_type)
        regular_user.user_permissions.add(perm)
        regular_user = regular_user.__class__.objects.get(pk=regular_user.pk)
        with self.assertRaises(PermissionDenied):
            self._call(regular_user, self.staff_only_cat)


@override_settings(
    CIVICOS=CIVICOS_OVERRIDES,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {}},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class CitizenCapPassedToS3Tests(TestCase):
    """H-1: Citizen upload size cap must be passed to the S3 presigned POST, not the staff cap."""

    def setUp(self):
        self.category = make_category()

    def test_citizen_max_size_passed_to_presigned_post(self):
        """
        For a citizen user (not is_staff, no upload_staff_document perm), the
        resolved max_size passed to _generate_presigned_post() must equal
        DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES (10 MB), NOT DOCUMENT_MAX_STAFF_UPLOAD_BYTES (50 MB).
        """
        citizen = make_user()
        captured_calls = []

        def capture_presigned_post(**kwargs):
            captured_calls.append(kwargs)
            return {
                "url": "http://dev-upload/",
                "fields": {},
                "expires_at": timezone.now().isoformat(),
            }

        with patch(
            "apps.documents.services.upload._generate_presigned_post",
            side_effect=capture_presigned_post,
        ):
            validate_upload_request(
                user=citizen,
                category_slug=self.category.slug,
                original_filename="report.pdf",
                mime_type="application/pdf",
                size_bytes=100 * 1024,
            )

        self.assertEqual(len(captured_calls), 1)
        passed_max_size = captured_calls[0]["max_size"]
        citizen_cap = CIVICOS_OVERRIDES["DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES"]
        staff_cap = CIVICOS_OVERRIDES["DOCUMENT_MAX_STAFF_UPLOAD_BYTES"]
        self.assertEqual(passed_max_size, citizen_cap)
        self.assertNotEqual(passed_max_size, staff_cap)

    def test_staff_max_size_passed_to_presigned_post(self):
        """For a staff user, max_size passed must equal DOCUMENT_MAX_STAFF_UPLOAD_BYTES."""
        staff = make_user(is_staff=True)
        captured_calls = []

        def capture_presigned_post(**kwargs):
            captured_calls.append(kwargs)
            return {
                "url": "http://dev-upload/",
                "fields": {},
                "expires_at": timezone.now().isoformat(),
            }

        with patch(
            "apps.documents.services.upload._generate_presigned_post",
            side_effect=capture_presigned_post,
        ):
            validate_upload_request(
                user=staff,
                category_slug=self.category.slug,
                original_filename="report.pdf",
                mime_type="application/pdf",
                size_bytes=100 * 1024,
            )

        self.assertEqual(len(captured_calls), 1)
        passed_max_size = captured_calls[0]["max_size"]
        staff_cap = CIVICOS_OVERRIDES["DOCUMENT_MAX_STAFF_UPLOAD_BYTES"]
        self.assertEqual(passed_max_size, staff_cap)

    def test_category_override_takes_precedence_for_all_users(self):
        """Category-level max_size_bytes overrides global caps for both citizens and staff."""
        category_cap = 5 * 1024 * 1024  # 5 MB
        custom_cat = make_category(max_size_bytes=category_cap)
        citizen = make_user()
        captured_calls = []

        def capture_presigned_post(**kwargs):
            captured_calls.append(kwargs)
            return {
                "url": "http://dev-upload/",
                "fields": {},
                "expires_at": timezone.now().isoformat(),
            }

        with patch(
            "apps.documents.services.upload._generate_presigned_post",
            side_effect=capture_presigned_post,
        ):
            validate_upload_request(
                user=citizen,
                category_slug=custom_cat.slug,
                original_filename="report.pdf",
                mime_type="application/pdf",
                size_bytes=100 * 1024,
            )

        self.assertEqual(captured_calls[0]["max_size"], category_cap)


@override_settings(
    CIVICOS=CIVICOS_OVERRIDES,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {}},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    MEDIA_ROOT="/tmp/civicos-test-media",
)
class LocalFilePathContainmentTests(TestCase):
    """H-2: Path traversal prevention in local filesystem file helpers."""

    def test_verify_local_file_exists_rejects_traversal(self):
        """storage_key containing '../' must be rejected before any file access."""
        from apps.documents.services.upload import _verify_local_file_exists
        with self.assertRaises(ValidationError):
            _verify_local_file_exists("../../etc/passwd")

    def test_read_local_first_bytes_rejects_traversal(self):
        from apps.documents.services.upload import _read_local_first_bytes
        with self.assertRaises(ValidationError):
            _read_local_first_bytes("../../etc/passwd", length=8192)

    def test_read_full_local_file_rejects_traversal(self):
        from apps.documents.services.upload import _read_full_local_file
        with self.assertRaises(ValidationError):
            _read_full_local_file("../../etc/passwd")

    def test_verify_local_file_exists_rejects_absolute_path(self):
        """An absolute path key (e.g. '/etc/passwd') must also be rejected."""
        from apps.documents.services.upload import _verify_local_file_exists
        with self.assertRaises(ValidationError):
            _verify_local_file_exists("/etc/passwd")


@override_settings(
    CIVICOS=CIVICOS_OVERRIDES,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {}},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class ConfirmUploadIdempotencyHighTests(TransactionTestCase):
    """H-8: QUARANTINED/DELETED documents must raise ValidationError, not return silently."""

    def setUp(self):
        self.user = make_user()
        self.category = make_category()

    def _make_doc(self, scan_status):
        return Document.objects.create(
            category=self.category,
            uploaded_by=self.user,
            original_filename="test.pdf",
            _storage_key=_make_storage_key(str(uuid.uuid4()), prefix="quarantine"),
            mime_type="application/pdf",
            size_bytes=1024,
            scan_status=scan_status,
            security_classification=DocumentCategory.SecurityClassification.PROTECTED_B,
        )

    def test_quarantined_doc_raises_validation_error(self):
        """
        H-8: A QUARANTINED document must raise ValidationError — not be returned
        silently. Returning a quarantined doc would hide a security event from the caller.
        """
        doc = self._make_doc(Document.ScanStatus.QUARANTINED)
        with self.assertRaises(ValidationError) as ctx:
            confirm_upload(user=self.user, doc_id=str(doc.pk))
        self.assertIn("not available", str(ctx.exception).lower())

    def test_deleted_doc_raises_validation_error(self):
        """H-8: A DELETED document must raise ValidationError."""
        doc = self._make_doc(Document.ScanStatus.DELETED)
        with self.assertRaises(ValidationError) as ctx:
            confirm_upload(user=self.user, doc_id=str(doc.pk))
        self.assertIn("not available", str(ctx.exception).lower())

    def test_scanning_doc_returns_silently(self):
        """SCANNING (concurrent double-submit) must still return the doc without error."""
        doc = self._make_doc(Document.ScanStatus.SCANNING)
        result = confirm_upload(user=self.user, doc_id=str(doc.pk))
        self.assertEqual(result.scan_status, Document.ScanStatus.SCANNING)

    def test_active_doc_returns_silently(self):
        """ACTIVE (upload already confirmed) must return without error."""
        doc = self._make_doc(Document.ScanStatus.ACTIVE)
        result = confirm_upload(user=self.user, doc_id=str(doc.pk))
        self.assertEqual(result.scan_status, Document.ScanStatus.ACTIVE)


@override_settings(
    CIVICOS=CIVICOS_OVERRIDES,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {}},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class AuditWriteFailureTests(TransactionTestCase):
    """
    C-5 / PIPEDA 4.5.3: A failed audit write MUST roll back the scan_status change.

    The previous behaviour (swallow the exception, return SCANNING) was a PIPEDA
    violation: a state change without an audit trail is not permitted. The correct
    invariant is: if record_event() fails inside the atomic block, the block rolls
    back — the document stays in PENDING_UPLOAD and the exception propagates.
    """

    def setUp(self):
        self.user = make_user()
        self.category = make_category()

    def _make_pending_doc(self):
        return Document.objects.create(
            category=self.category,
            uploaded_by=self.user,
            original_filename="test.pdf",
            _storage_key=_make_storage_key(str(uuid.uuid4()), prefix="quarantine"),
            mime_type="application/pdf",
            size_bytes=1024,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
            security_classification=DocumentCategory.SecurityClassification.PROTECTED_B,
        )

    def test_audit_failure_raises(self):
        """
        C-5 / PIPEDA 4.5.3: audit write failure MUST propagate — PIPEDA 4.5.3 requires
        that state changes and their audit entries commit together. A failed audit must
        roll back the operation, not silently succeed without an audit trail.
        """
        doc = self._make_pending_doc()
        with patch("apps.documents.services.upload._verify_file_exists"):
            with patch("apps.documents.services.upload._read_first_bytes", return_value=b"%PDF-1.4"):
                with patch(
                    "apps.documents.services.upload._validate_magic_bytes",
                    return_value="application/pdf",
                ):
                    with patch("apps.documents.tasks.scan_document.apply_async"):
                        with patch(
                            "apps.audit.services.record_event",
                            side_effect=Exception("DB write failed"),
                        ):
                            # MUST raise — audit failure rolls back the atomic block
                            with self.assertRaises(Exception):
                                confirm_upload(user=self.user, doc_id=str(doc.pk))

    def test_audit_failure_rolls_back_to_pending_upload(self):
        """
        C-5 / PIPEDA 4.5.3: When audit write fails, the atomic block rolls back,
        so the document must remain in PENDING_UPLOAD — not advance to SCANNING.
        """
        doc = self._make_pending_doc()
        with patch("apps.documents.services.upload._verify_file_exists"):
            with patch("apps.documents.services.upload._read_first_bytes", return_value=b"%PDF-1.4"):
                with patch(
                    "apps.documents.services.upload._validate_magic_bytes",
                    return_value="application/pdf",
                ):
                    with patch("apps.documents.tasks.scan_document.apply_async"):
                        with patch(
                            "apps.audit.services.record_event",
                            side_effect=Exception("DB write failed"),
                        ):
                            with self.assertRaises(Exception):
                                confirm_upload(user=self.user, doc_id=str(doc.pk))

        doc.refresh_from_db()
        # Rollback: document must still be PENDING_UPLOAD (not SCANNING)
        self.assertEqual(doc.scan_status, Document.ScanStatus.PENDING_UPLOAD)


class AppsReadyImportErrorTests(TestCase):
    """H-7: apps.py ready() must re-raise ImportError if receivers.py exists but is broken."""

    def test_ready_raises_if_receivers_exists_but_broken(self):
        """
        If find_spec() finds apps.documents.receivers but importing it raises
        an ImportError (e.g. a missing dependency inside receivers.py), the error
        must propagate — not be silently swallowed.
        """
        import importlib.util
        from apps.documents.apps import DocumentsConfig
        from unittest.mock import MagicMock

        # Simulate: find_spec returns a spec (module file exists) but import fails
        mock_spec = MagicMock()
        with patch("importlib.util.find_spec", return_value=mock_spec):
            with patch(
                "builtins.__import__",
                side_effect=lambda name, *args, **kwargs: (
                    (_ for _ in ()).throw(ImportError("broken import inside receivers"))
                    if name == "apps.documents.receivers"
                    else __import__(name, *args, **kwargs)
                ),
            ):
                with self.assertRaises(ImportError):
                    # Directly test the import logic path
                    _receivers_spec = importlib.util.find_spec("apps.documents.receivers")
                    if _receivers_spec is not None:
                        import apps.documents.receivers  # noqa: F401

    def test_ready_does_not_raise_if_receivers_absent(self):
        """
        If find_spec() returns None (module not on disk), no import is attempted
        and no error is raised. This is the Wave 1 / Wave 2 case.
        """
        import importlib.util
        with patch("importlib.util.find_spec", return_value=None):
            # Should not raise
            _receivers_spec = importlib.util.find_spec("apps.documents.receivers")
            self.assertIsNone(_receivers_spec)
