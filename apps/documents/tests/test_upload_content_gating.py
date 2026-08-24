"""
Regression tests for the Documents BB upload-content validation layers.

Covers two defects found by the 2026-07-26 adversarial audit
(MASTER_BB_CERTIFIABILITY_REPORT.md → "Documents Management BB"):

CRITICAL-1 — ``_check_pdf_encryption()`` tested ``Pdf.encryption`` (an
             ``EncryptionInfo`` object that is truthy for EVERY PDF) instead of
             ``Pdf.is_encrypted`` (a real bool), so *every* clean PDF upload was
             rejected. Every pre-existing happy-path PDF test patched
             ``_check_pdf_encryption`` out, which is why the 1066-test suite
             never saw it.

HIGH-2     — Layers 5b (encrypted PDF) and 6 (ZIP bomb, CVE-2024-0450) were
             gated on the CLIENT-supplied filename extension. Renaming an
             encrypted PDF to ``evidence.csv`` or a ZIP-bomb OOXML file to
             ``photo.jpg`` skipped both checks while still passing the
             magic-byte allowlist. Gating now uses the server-DETECTED MIME type.

Design rules for this module (deliberately different from the rest of the suite):
  - ``_check_pdf_encryption`` / ``_check_zip_bomb`` / ``_validate_magic_bytes``
    are NEVER patched here. Real bytes are written to MEDIA_ROOT and read back
    through the real storage helpers, so the assertions describe what the
    pipeline actually does end to end.
  - PDF fixtures are generated with pikepdf itself (clean, owner-password-only,
    and real user-password variants), never hand-rolled byte blobs.
  - PIPEDA: no filename, email, or storage key is asserted on or logged.
"""

from __future__ import annotations

import io
import os
import unittest
import uuid
import zipfile
from unittest.mock import patch

from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from apps.documents.models import Document, DocumentCategory
from apps.documents.services import upload as upload_service
from apps.documents.services.upload import (
    _check_pdf_encryption,
    _make_storage_key,
    confirm_upload,
)

User = get_user_model()

pikepdf = upload_service._pikepdf

_HAVE_PIKEPDF = pikepdf is not None
_HAVE_MAGIC = upload_service.magic is not None

# Mirrors CIVICOS["ALLOWED_UPLOAD_MIME_TYPES"] in config/settings/base.py.
# Kept explicit (not imported) so a silent narrowing of the production list
# cannot silently weaken these tests.
_ALLOWED_MIMES = [
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv",
]

_CIVICOS = {
    "ALLOWED_UPLOAD_MIME_TYPES": _ALLOWED_MIMES,
    "MAGIC_BYTES_REQUIRED": True,
    "DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES": 10 * 1024 * 1024,
    "DOCUMENT_MAX_STAFF_UPLOAD_BYTES": 50 * 1024 * 1024,
    "DOCUMENT_ZIP_MAX_ENTRIES": 1000,
    "DOCUMENT_ZIP_MAX_RATIO": 100,
    "CLAMAV_HOST": "",
}

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


# ─────────────────────────────────────────────────────────────────────────────
# Real-file fixtures
# ─────────────────────────────────────────────────────────────────────────────


def make_clean_pdf_bytes() -> bytes:
    """A genuinely valid, completely unencrypted single-page PDF."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def make_owner_password_pdf_bytes() -> bytes:
    """
    An encrypted PDF with an EMPTY user password.

    ``pikepdf.open()`` succeeds on this file (no password prompt) but the content
    is still ciphertext, so ClamAV cannot scan it — it must be rejected.
    """
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()
    buf = io.BytesIO()
    pdf.save(buf, encryption=pikepdf.Encryption(owner="ownerpw", user="", R=6))
    return buf.getvalue()


def make_user_password_pdf_bytes() -> bytes:
    """A PDF that genuinely requires a password to open (pikepdf.PasswordError)."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()
    buf = io.BytesIO()
    pdf.save(buf, encryption=pikepdf.Encryption(owner="ownerpw", user="userpw", R=6))
    return buf.getvalue()


def _docx_zip(*, bomb: bool = False) -> bytes:
    """
    Build a real OOXML (.docx) package.

    libmagic identifies the result as the wordprocessingml MIME type from the
    first 8 KB (the ``[Content_Types].xml`` entry is written first), which is
    what makes the "rename it to photo.jpg" bypass possible in the first place.

    Args:
        bomb: if True, add a highly compressible 2 MB member so the
              uncompressed/compressed ratio blows past DOCUMENT_ZIP_MAX_RATIO.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'  # noqa: E501
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>hello</w:t></w:r></w:p>'
            "</w:body></w:document>",
        )
        if bomb:
            zf.writestr("word/media/payload.bin", b"\x00" * (2 * 1024 * 1024))
    return buf.getvalue()


def make_docx_bytes() -> bytes:
    """A small, legitimate .docx package."""
    return _docx_zip(bomb=False)


def make_docx_bomb_bytes() -> bytes:
    """A .docx-shaped ZIP bomb (CVE-2024-0450 class)."""
    return _docx_zip(bomb=True)


def make_csv_bytes() -> bytes:
    return b"name,age,city\nalice,30,ottawa\nbob,41,toronto\n"


# ─────────────────────────────────────────────────────────────────────────────
# 1. _check_pdf_encryption — real bytes, real pikepdf, nothing patched
# ─────────────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAVE_PIKEPDF, "pikepdf is not installed in this environment")
class CheckPdfEncryptionRealBytesTests(TestCase):
    """
    Direct tests of ``_check_pdf_encryption()`` against real PDF byte strings.

    The function itself is never mocked — these are the tests the audit found
    missing (every prior PDF test patched the function out entirely).
    """

    def test_clean_unencrypted_pdf_is_accepted(self):
        """CRITICAL-1: a normal, unencrypted PDF must NOT be rejected."""
        # Must not raise.
        _check_pdf_encryption(make_clean_pdf_bytes())

    def test_pikepdf_encryption_attribute_is_truthy_even_when_clean(self):
        """
        Documents WHY ``is_encrypted`` is the only correct test.

        ``Pdf.encryption`` returns an ``EncryptionInfo`` object with no
        ``__bool__``/``__len__``, so it is truthy for a clean PDF too. If this
        assertion ever fails upstream, the old ``if _pdf.encryption:`` form
        would start "working" by accident — but it must still never be used.
        """
        with pikepdf.open(io.BytesIO(make_clean_pdf_bytes())) as pdf:
            self.assertFalse(pdf.is_encrypted)
            self.assertTrue(
                bool(pdf.encryption),
                "pikepdf.Pdf.encryption is truthy for every PDF — never branch on it.",
            )

    def test_encrypted_pdf_without_user_password_is_rejected(self):
        """
        Owner-password-only encryption: opens without a password, but the
        content is ciphertext ClamAV cannot scan → must be rejected.
        """
        with self.assertRaises(ValidationError) as ctx:
            _check_pdf_encryption(make_owner_password_pdf_bytes())
        self.assertIn("password", str(ctx.exception).lower())

    def test_password_protected_pdf_is_rejected_not_500(self):
        """
        A PDF that truly requires a password raises ``pikepdf.PasswordError``
        inside ``pikepdf.open()``. That must be caught and converted to the same
        ValidationError, never propagate as an unhandled 500.
        """
        with self.assertRaises(ValidationError) as ctx:
            _check_pdf_encryption(make_user_password_pdf_bytes())
        self.assertIn("password", str(ctx.exception).lower())

    def test_corrupted_pdf_is_rejected_as_unreadable(self):
        """Garbage that claims to be a PDF is rejected, never silently accepted."""
        with self.assertRaises(ValidationError):
            _check_pdf_encryption(b"%PDF-1.7\nthis is not actually a pdf body")

    def test_fallback_heuristic_used_when_pikepdf_absent(self):
        """
        With pikepdf unavailable the raw ``/Encrypt`` heuristic still rejects an
        encrypted PDF, and a clean PDF still passes (no blanket rejection).
        """
        encrypted = make_owner_password_pdf_bytes()
        clean = make_clean_pdf_bytes()
        with patch.object(upload_service, "_pikepdf", None):
            with self.assertRaises(ValidationError):
                _check_pdf_encryption(encrypted)
            _check_pdf_encryption(clean)  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# 2. confirm_upload — detected-MIME gating (Layers 5b / 6)
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=_CIVICOS)
@unittest.skipUnless(_HAVE_PIKEPDF, "pikepdf is not installed in this environment")
@unittest.skipUnless(_HAVE_MAGIC, "python-magic / libmagic is not installed")
class ConfirmUploadDetectedMimeGatingTests(TestCase):
    """
    End-to-end ``confirm_upload()`` tests with REAL file bytes on disk.

    Nothing in the validation pipeline is patched: the real storage helpers read
    the real file, real libmagic sniffs it, and the real Layer 5b / Layer 6
    checks run. Only the Celery dispatch is stubbed (no broker in tests).

    The declared extension and declared Content-Type are set to whatever an
    attacker would choose; the assertions prove they no longer decide which
    security layers run.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email=f"gating-{uuid.uuid4().hex[:8]}@example.com",
            password="hunter2",
        )
        self.category = DocumentCategory.objects.create(
            name_en="Gating Test",
            name_fr="Test de filtrage",
            slug=f"gating-{uuid.uuid4().hex[:6]}",
            security_classification=DocumentCategory.SecurityClassification.PROTECTED_B,
            allowed_mime_types=[],  # falls back to CIVICOS list (production default)
            max_size_bytes=0,
            min_retention_days=730,
            max_retention_days=2555,
            is_transitory=False,
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _make_doc_with_real_file(
        self,
        *,
        content: bytes,
        original_filename: str,
        declared_mime: str,
    ) -> Document:
        """Create a PENDING_UPLOAD Document whose storage key holds real bytes."""
        doc_uuid = uuid.uuid4()
        storage_key = _make_storage_key(str(doc_uuid), prefix="quarantine")
        full_path = os.path.join(django_settings.MEDIA_ROOT, storage_key)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as fh:
            fh.write(content)
        return Document.objects.create(
            id=doc_uuid,
            category=self.category,
            uploaded_by=self.user,
            original_filename=original_filename,
            _storage_key=storage_key,
            mime_type=declared_mime,
            size_bytes=len(content),
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
            security_classification=self.category.security_classification,
        )

    def _confirm(self, doc: Document) -> Document:
        """Run the real confirm_upload(); only Celery dispatch is stubbed."""
        with patch("apps.documents.tasks.scan_document.apply_async"):
            return confirm_upload(user=self.user, doc_id=str(doc.pk))

    # ── Happy paths (must NOT regress) ────────────────────────────────────────

    def test_clean_pdf_uploaded_as_pdf_is_accepted(self):
        """
        CRITICAL-1 end-to-end: a genuinely clean PDF must reach SCANNING with
        the real (unpatched) Layer 5b check in the path.
        """
        doc = self._make_doc_with_real_file(
            content=make_clean_pdf_bytes(),
            original_filename="statement.pdf",
            declared_mime="application/pdf",
        )
        result = self._confirm(doc)
        self.assertEqual(result.scan_status, Document.ScanStatus.SCANNING)
        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.SCANNING)
        # Layer 5 overwrites the client-declared value with libmagic's verdict.
        self.assertEqual(doc.mime_type, "application/pdf")

    def test_legitimate_docx_uploaded_as_docx_is_accepted(self):
        """A small, valid .docx must still pass Layer 6."""
        doc = self._make_doc_with_real_file(
            content=make_docx_bytes(),
            original_filename="letter.docx",
            declared_mime=_DOCX_MIME,
        )
        result = self._confirm(doc)
        self.assertEqual(result.scan_status, Document.ScanStatus.SCANNING)

    def test_plain_csv_is_accepted_and_runs_no_container_checks(self):
        """
        A real CSV is neither a PDF nor an archive — neither Layer 5b nor
        Layer 6 may fire (no over-gating from the new detected-MIME rules).
        """
        doc = self._make_doc_with_real_file(
            content=make_csv_bytes(),
            original_filename="rows.csv",
            declared_mime="text/csv",
        )
        with (
            patch("apps.documents.tasks.scan_document.apply_async"),
            patch.object(upload_service, "_check_pdf_encryption") as mock_pdf,
            patch.object(upload_service, "_check_zip_bomb") as mock_zip,
        ):
            result = confirm_upload(user=self.user, doc_id=str(doc.pk))

        self.assertEqual(result.scan_status, Document.ScanStatus.SCANNING)
        mock_pdf.assert_not_called()
        mock_zip.assert_not_called()

    # ── HIGH-2: extension/Content-Type lies must no longer bypass the layers ──

    def test_encrypted_pdf_disguised_as_csv_is_rejected(self):
        """
        HIGH-2: encrypted PDF bytes uploaded as ``evidence.csv`` with a declared
        Content-Type of ``text/csv``.

        Before the fix the extension gate ("is it .pdf?") skipped Layer 5b and
        the file was accepted for scanning even though ClamAV cannot read it.
        """
        doc = self._make_doc_with_real_file(
            content=make_owner_password_pdf_bytes(),
            original_filename="evidence.csv",
            declared_mime="text/csv",
        )
        with self.assertRaises(ValidationError) as ctx:
            self._confirm(doc)
        self.assertIn("password", str(ctx.exception).lower())

        doc.refresh_from_db()
        self.assertEqual(
            doc.scan_status,
            Document.ScanStatus.PENDING_UPLOAD,
            "A rejected upload must never advance to SCANNING.",
        )

    def test_password_protected_pdf_disguised_as_csv_is_rejected(self):
        """Same bypass, using a PDF that truly requires a password to open."""
        doc = self._make_doc_with_real_file(
            content=make_user_password_pdf_bytes(),
            original_filename="evidence.csv",
            declared_mime="text/csv",
        )
        with self.assertRaises(ValidationError):
            self._confirm(doc)

    def test_zip_bomb_disguised_as_jpeg_is_rejected(self):
        """
        HIGH-2: a .docx-shaped ZIP bomb uploaded as ``photo.jpg`` with a declared
        Content-Type of ``image/jpeg``.

        Before the fix the extension gate ("is it .docx/.xlsx?") skipped Layer 6
        entirely and the bomb was accepted.
        """
        doc = self._make_doc_with_real_file(
            content=make_docx_bomb_bytes(),
            original_filename="photo.jpg",
            declared_mime="image/jpeg",
        )
        with self.assertRaises(ValidationError) as ctx:
            self._confirm(doc)
        self.assertIn("ratio", str(ctx.exception).lower())

        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.PENDING_UPLOAD)

    def test_zip_bomb_declared_as_docx_is_still_rejected(self):
        """The honest-filename case must keep working (no regression)."""
        doc = self._make_doc_with_real_file(
            content=make_docx_bomb_bytes(),
            original_filename="report.docx",
            declared_mime=_DOCX_MIME,
        )
        with self.assertRaises(ValidationError):
            self._confirm(doc)

    def test_gating_uses_detected_mime_not_extension(self):
        """
        White-box confirmation of the gate itself: PDF content named ``.csv``
        invokes Layer 5b and NOT Layer 6, driven purely by the detected MIME.
        """
        doc = self._make_doc_with_real_file(
            content=make_clean_pdf_bytes(),
            original_filename="notes.csv",
            declared_mime="text/csv",
        )
        with (
            patch("apps.documents.tasks.scan_document.apply_async"),
            patch.object(upload_service, "_check_pdf_encryption") as mock_pdf,
            patch.object(upload_service, "_check_zip_bomb") as mock_zip,
        ):
            confirm_upload(user=self.user, doc_id=str(doc.pk))

        mock_pdf.assert_called_once()
        mock_zip.assert_not_called()

    def test_extension_fallback_when_magic_bytes_bypassed(self):
        """
        When magic-byte validation is deliberately disabled (dev only), the
        gate falls back to the declared extension rather than skipping Layers
        5b/6 altogether — degraded, but never silently off.
        """
        doc = self._make_doc_with_real_file(
            content=make_owner_password_pdf_bytes(),
            original_filename="statement.pdf",
            declared_mime="application/pdf",
        )
        # _validate_magic_bytes returns None only in the bypassed configuration.
        with patch.object(upload_service, "_validate_magic_bytes", return_value=None):
            with self.assertRaises(ValidationError):
                self._confirm(doc)
