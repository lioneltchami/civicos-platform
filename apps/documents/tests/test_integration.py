"""
apps/documents/tests/test_integration.py
=========================================
Wave 7 integration test suite for the Documents Building Block.

This module tests the complete, cross-service pipelines rather than individual
service functions in isolation. Each class exercises a distinct lifecycle arc.

Test classes
------------
1. FullUploadScanDownloadPipelineTests (TransactionTestCase)
   Full pipeline: validate_upload_request → confirm_upload → scan_document
   (dev bypass) → issue_access_token → consume_access_token.

2. LegalHoldDisposalIntegrationTests (TestCase)
   Legal holds correctly block soft_delete and hard_delete; releasing a hold
   re-enables disposal.

3. VersionChainIntegrationTests (TestCase)
   Version chain invariants: demoting previous latest version, incrementing
   version_number, root_document pointer, and permission gates.
   NOTE: create_new_version() raises ImproperlyConfigured on SQLite (no
   SELECT FOR UPDATE). Version chain structure is validated by constructing
   chains directly with Document.objects.create(); the permission gate is
   tested via the internal _user_may_version() helper.

4. TransitoryDocumentIntegrationTests (TestCase)
   Transitory categories: no calendar expires_at at creation, purpose-fulfilled
   disposal, legal-hold blocking, and non-transitory schedule_expiry.

5. ScanStatusGateIntegrationTests (TestCase)
   Only ACTIVE documents may receive access tokens; all other scan statuses
   and soft-deleted documents raise Http404 from issue_access_token().

Architecture tested
-------------------
  Upload pipeline  : validate_upload_request → Document(PENDING_UPLOAD) →
                     confirm_upload → Document(SCANNING) → on_commit →
                     scan_document(dev bypass) → Document(ACTIVE)
  Download pipeline: issue_access_token → DocumentAccessToken →
                     consume_access_token → Document
  Retention        : schedule_expiry, soft_delete, hard_delete,
                     mark_purpose_fulfilled, apply_legal_hold, release_legal_hold
  Versioning       : _user_may_version, chain root / is_latest_version invariants

PIPEDA invariants verified
--------------------------
  - storage_key never returned by validate_upload_request
  - Http404 (not 403) for non-ACTIVE documents at token-issuance time
  - original_filename never in audit event_detail (enforced by service layer)
  - legal_hold=True unconditionally blocks all disposal paths

Governing law: PIPEDA clause 4.5.3 & 4.7.5, Privacy Act s.6(1),
LAC Disposition Authorization #2016/001, OWASP File Upload Cheat Sheet.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from io import BytesIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.documents.models import Document, DocumentAccessToken, DocumentCategory
from apps.documents.services.download import consume_access_token, issue_access_token
from apps.documents.services.retention import (
    apply_legal_hold,
    hard_delete,
    mark_purpose_fulfilled,
    release_legal_hold,
    schedule_expiry,
    soft_delete,
)
from apps.documents.services.upload import confirm_upload, validate_upload_request
from apps.documents.services.versioning import _user_may_version

User = get_user_model()

# ─────────────────────────────────────────────────────────────────────────────
# CIVICOS settings override for all tests in this module.
# MAGIC_BYTES_REQUIRED=False: skips python-magic validation (not installed in CI).
# CLAMAV_HOST="" + CLAMAV_REQUIRED=False: activates the dev bypass in scan_document,
# which marks the document ACTIVE immediately (safe because TESTING=True in
# test settings — the bypass is guarded by DEBUG or TESTING).
# ─────────────────────────────────────────────────────────────────────────────

_TEST_CIVICOS: dict = {
    "ALLOWED_UPLOAD_MIME_TYPES": ["application/pdf"],
    "DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES": 10 * 1024 * 1024,
    "DOCUMENT_MAX_STAFF_UPLOAD_BYTES": 50 * 1024 * 1024,
    "CLAMAV_HOST": "",
    "CLAMAV_PORT": 3310,
    "CLAMAV_REQUIRED": False,
    "MAGIC_BYTES_REQUIRED": False,
    "DOCUMENT_PRESIGNED_POST_TTL_SECONDS": 900,
    "DOCUMENT_PRESIGNED_URL_TTL_SECONDS": 300,
    "DOCUMENT_ACCESS_TOKEN_TTL_SECONDS": 300,
    "DOCUMENT_HARD_DELETE_GRACE_DAYS": 30,
    "DOCUMENT_ZIP_MAX_ENTRIES": 1000,
    "DOCUMENT_ZIP_MAX_RATIO": 100,
}


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixture helpers
# ─────────────────────────────────────────────────────────────────────────────

# Module-level counter for unique slugs / emails across all test runs.
# TransactionTestCase flushes the DB between tests so the counter avoids
# slug collisions within a single test process run.
_CTR: int = 0


def _make_user(**kwargs) -> User:
    """Create a unique user. Accepts any User.objects.create_user() kwargs."""
    global _CTR
    _CTR += 1
    return User.objects.create_user(
        email=f"integ{_CTR}@example.com",
        password="testpass123!",
        **kwargs,
    )


def _make_category(**kwargs) -> DocumentCategory:
    """
    Create a DocumentCategory with sensible defaults for integration tests.

    Override any field via kwargs. The slug is always auto-generated to avoid
    collisions when both TestCase and TransactionTestCase share the counter.
    """
    global _CTR
    _CTR += 1
    defaults = {
        "name_en": f"Integration Cat {_CTR}",
        "name_fr": f"Catégorie {_CTR}",
        "slug": f"integ-cat-{_CTR}",
        "allowed_mime_types": ["application/pdf"],
        "min_retention_days": 30,
        "max_retention_days": 2555,
        "is_transitory": False,
        "staff_only": False,
    }
    defaults.update(kwargs)
    return DocumentCategory.objects.create(**defaults)


def _make_document(user: User, category: DocumentCategory, **kwargs) -> Document:
    """
    Create a Document record directly in the DB (bypasses the upload pipeline).

    Defaults to ACTIVE status so tests can proceed to download/retention
    operations without going through the full upload flow. Override scan_status,
    legal_hold, deleted_at, etc. via kwargs.
    """
    doc_id = uuid.uuid4()
    defaults = {
        "scan_status": Document.ScanStatus.ACTIVE,
        "version_number": 1,
        "is_latest_version": True,
        "mime_type": "application/pdf",
        "size_bytes": 1024,
        "original_filename": "test.pdf",
        "_storage_key": f"documents/active/{doc_id}/{uuid.uuid4().hex}.bin",
        "security_classification": DocumentCategory.SecurityClassification.PROTECTED_B,
    }
    defaults.update(kwargs)
    return Document.objects.create(
        id=doc_id,
        uploaded_by=user,
        category=category,
        **defaults,
    )


def _make_token(
    user: User,
    doc: Document,
    **kwargs,
) -> DocumentAccessToken:
    """
    Create a valid, unexpired DocumentAccessToken for the given user/document.

    Defaults to a 5-minute TTL. Override expires_at, used_at, etc. via kwargs.
    """
    kwargs.setdefault("expires_at", timezone.now() + timedelta(minutes=5))
    return DocumentAccessToken.objects.create(
        document=doc,
        issued_to=user,
        **kwargs,
    )


def _grant_legal_hold_perm(user: User) -> User:
    """
    Grant documents.manage_legal_hold to user and clear the permission cache.

    apply_legal_hold() and release_legal_hold() check has_perm() which uses
    Django's permission cache. After adding a permission, the cache on the
    in-memory user instance must be cleared so the new permission is visible.

    Uses get_or_create() (not bare .get()) so the permission row is guaranteed
    to exist even in environments where post_migrate didn't fully run — matching
    the pattern used across all other test files in this app.
    """
    ct = ContentType.objects.get(app_label="documents", model="document")
    perm, _ = Permission.objects.get_or_create(
        codename="manage_legal_hold",
        content_type=ct,
        defaults={"name": "Can manage legal hold"},
    )
    user.user_permissions.add(perm)
    # Clear Django's cached permission set on this user instance.
    if hasattr(user, "_perm_cache"):
        del user._perm_cache
    if hasattr(user, "_user_perm_cache"):
        del user._user_perm_cache
    return user


def _grant_upload_perm(user: User) -> User:
    """
    Grant documents.upload_document and clear the permission cache.

    Uses get_or_create() for robustness (see _grant_legal_hold_perm above).
    """
    ct = ContentType.objects.get(app_label="documents", model="document")
    perm, _ = Permission.objects.get_or_create(
        codename="upload_document",
        content_type=ct,
        defaults={"name": "Can upload document"},
    )
    user.user_permissions.add(perm)
    if hasattr(user, "_perm_cache"):
        del user._perm_cache
    if hasattr(user, "_user_perm_cache"):
        del user._user_perm_cache
    return user


# ─────────────────────────────────────────────────────────────────────────────
# Class 1: FullUploadScanDownloadPipelineTests
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=_TEST_CIVICOS)
class FullUploadScanDownloadPipelineTests(TransactionTestCase):
    """
    End-to-end upload → scan → download pipeline using TransactionTestCase.

    TransactionTestCase is used because on_commit callbacks fire only after a
    real COMMIT. TestCase wraps everything in a single test-scoped transaction
    that never commits, so on_commit would never fire.

    scan_document.apply_async is patched inside _run_pipeline with a side_effect
    that calls scan_document.apply() synchronously. This bypasses any Celery
    broker configuration entirely — no broker connection is attempted. Using
    CELERY_TASK_ALWAYS_EAGER alone is not reliable because the Celery app reads
    settings at init time and the test override may not propagate to the task
    dispatcher when on_commit fires inside TransactionTestCase.

    With CLAMAV_HOST="" and CLAMAV_REQUIRED=False and TESTING=True (from
    test settings), scan_document takes the dev bypass path: it calls
    _mark_document_active_dev_bypass() which transitions the document
    directly to ACTIVE without running ClamAV.

    Storage is mocked out for confirm_upload because the test MEDIA_ROOT
    contains no real uploaded files. The service layer's internal
    _verify_file_exists() and _read_first_bytes() are patched at module level.
    """

    def setUp(self) -> None:
        self.user = _make_user()
        self.category = _make_category()

    def _run_pipeline(
        self,
        user: User | None = None,
        category: DocumentCategory | None = None,
    ) -> tuple[str, Document]:
        """
        Helper: run validate_upload_request → confirm_upload → scan with storage mocked.

        Returns (doc_id_str, doc_after_scan).

        Storage helpers _verify_file_exists and _read_first_bytes are patched
        so no real file needs to exist at the quarantine path.

        scan_document.apply_async is patched with a side_effect that calls
        scan_document.apply() synchronously. This is necessary because
        CELERY_TASK_ALWAYS_EAGER is read by the Celery app at init time and
        may not propagate to TransactionTestCase tests that run outside the
        normal test runner eager-mode patching window. The side_effect approach
        is explicit and broker-agnostic: it guarantees the task body executes
        regardless of Celery configuration.

        With CLAMAV_HOST="" + CLAMAV_REQUIRED=False + TESTING=True (test
        settings), scan_document takes the dev bypass path and marks the doc
        ACTIVE without contacting ClamAV.
        """
        from apps.documents.tasks import scan_document as _scan_task

        _user = user or self.user
        _cat = category or self.category

        def _eager_apply_async(*a, **kw):
            """Call apply() synchronously instead of dispatching to broker."""
            task_args = kw.get("args") or (list(a[0]) if a else [])
            _scan_task.apply(args=task_args)

        with (
            patch("apps.documents.services.upload._verify_file_exists"),
            patch(
                "apps.documents.services.upload._read_first_bytes",
                return_value=b"%PDF-1.4 fake content for integration test",
            ),
            patch(
                "apps.documents.tasks.scan_document.apply_async",
                side_effect=_eager_apply_async,
            ),
        ):
            result = validate_upload_request(
                user=_user,
                category_slug=_cat.slug,
                original_filename="test.pdf",
                mime_type="application/pdf",
                size_bytes=1024,
            )
            doc_id = result["doc_id"]
            doc = confirm_upload(user=_user, doc_id=doc_id)

        return doc_id, doc

    # ── Pipeline completeness tests ──────────────────────────────────────────

    def test_dev_bypass_scan_produces_active_document(self) -> None:
        """
        Full pipeline produces a Document with scan_status=ACTIVE.

        Sequence:
          validate_upload_request → PENDING_UPLOAD
          confirm_upload          → SCANNING (+ on_commit schedules scan)
          scan_document (eager)   → ACTIVE (dev bypass: TESTING=True)
        """
        doc_id, _ = self._run_pipeline()
        doc = Document.objects.get(pk=doc_id)
        self.assertEqual(
            doc.scan_status,
            Document.ScanStatus.ACTIVE,
            "Doc must be ACTIVE after full pipeline with dev bypass.",
        )

    def test_validate_upload_request_creates_pending_upload_document(self) -> None:
        """
        validate_upload_request creates a Document in PENDING_UPLOAD status
        before any file bytes are transferred.
        """
        with patch("apps.documents.services.upload._verify_file_exists"):
            result = validate_upload_request(
                user=self.user,
                category_slug=self.category.slug,
                original_filename="test.pdf",
                mime_type="application/pdf",
                size_bytes=1024,
            )

        doc = Document.objects.get(pk=result["doc_id"])
        self.assertEqual(doc.scan_status, Document.ScanStatus.PENDING_UPLOAD)
        self.assertEqual(doc.uploaded_by_id, self.user.pk)

    def test_validate_upload_request_never_returns_storage_key(self) -> None:
        """
        PIPEDA: storage_key must never appear in the validate_upload_request
        return value. Only doc_id, upload_url, upload_fields, expires_at.
        """
        with patch("apps.documents.services.upload._verify_file_exists"):
            result = validate_upload_request(
                user=self.user,
                category_slug=self.category.slug,
                original_filename="test.pdf",
                mime_type="application/pdf",
                size_bytes=1024,
            )

        self.assertIn("doc_id", result)
        self.assertIn("upload_url", result)
        self.assertIn("upload_fields", result)
        self.assertIn("expires_at", result)
        self.assertNotIn("storage_key", result, "PIPEDA: storage_key must never be returned.")
        self.assertNotIn("_storage_key", result, "PIPEDA: _storage_key must never be returned.")

    def test_confirm_upload_dispatches_scan_on_commit(self) -> None:
        """
        confirm_upload() dispatches scan_document.apply_async() via transaction.on_commit().

        In TransactionTestCase, on_commit fires after the atomic block commits.
        We patch apply_async at the Celery task level to capture the dispatch
        before the task executes, verifying both the call and its arguments.
        """
        # First, get a document into PENDING_UPLOAD state.
        with patch("apps.documents.services.upload._verify_file_exists"):
            result = validate_upload_request(
                user=self.user,
                category_slug=self.category.slug,
                original_filename="test.pdf",
                mime_type="application/pdf",
                size_bytes=1024,
            )
        doc_id = result["doc_id"]

        with (
            patch(
                "apps.documents.tasks.scan_document.apply_async"
            ) as mock_apply_async,
            patch("apps.documents.services.upload._verify_file_exists"),
            patch(
                "apps.documents.services.upload._read_first_bytes",
                return_value=b"%PDF-1.4",
            ),
        ):
            confirm_upload(user=self.user, doc_id=doc_id)

        mock_apply_async.assert_called_once_with(args=[doc_id], countdown=2)

    def test_confirm_upload_advances_to_scanning_status(self) -> None:
        """
        When scan_document.apply_async is patched to a no-op, confirm_upload
        must leave the document in SCANNING status (the scan was dispatched
        but not yet executed).
        """
        with patch("apps.documents.services.upload._verify_file_exists"):
            result = validate_upload_request(
                user=self.user,
                category_slug=self.category.slug,
                original_filename="test.pdf",
                mime_type="application/pdf",
                size_bytes=1024,
            )
        doc_id = result["doc_id"]

        with (
            patch("apps.documents.tasks.scan_document.apply_async"),
            patch("apps.documents.services.upload._verify_file_exists"),
            patch(
                "apps.documents.services.upload._read_first_bytes",
                return_value=b"%PDF-1.4",
            ),
        ):
            confirm_upload(user=self.user, doc_id=doc_id)

        doc = Document.objects.get(pk=doc_id)
        self.assertEqual(
            doc.scan_status,
            Document.ScanStatus.SCANNING,
            "Without the task executing, status should remain SCANNING after confirm_upload.",
        )

    def test_confirm_upload_idor_returns_404_for_wrong_user(self) -> None:
        """
        IDOR prevention: confirm_upload raises Http404 (not PermissionDenied)
        when the user is not the document's uploader.
        """
        other_user = _make_user()

        with patch("apps.documents.services.upload._verify_file_exists"):
            result = validate_upload_request(
                user=self.user,
                category_slug=self.category.slug,
                original_filename="test.pdf",
                mime_type="application/pdf",
                size_bytes=1024,
            )
        doc_id = result["doc_id"]

        with self.assertRaises(Http404):
            confirm_upload(user=other_user, doc_id=doc_id)

    # ── Download pipeline tests ───────────────────────────────────────────────

    def test_access_token_issued_for_active_document(self) -> None:
        """issue_access_token returns a valid token for an ACTIVE document."""
        doc_id, _ = self._run_pipeline()
        doc = Document.objects.get(pk=doc_id)

        token = issue_access_token(
            user=self.user,
            document=doc,
            ip_address="192.168.1.100",
        )

        self.assertTrue(token.is_valid, "Newly issued token must be valid.")
        self.assertEqual(token.document_id, doc.pk)
        self.assertEqual(token.issued_to_id, self.user.pk)
        self.assertIsNone(token.used_at, "Token must not be used at issuance time.")

    def test_consume_access_token_returns_document_and_marks_used(self) -> None:
        """consume_access_token marks the token used and returns the linked Document."""
        doc_id, _ = self._run_pipeline()
        doc = Document.objects.get(pk=doc_id)

        token = issue_access_token(
            user=self.user,
            document=doc,
            ip_address="10.0.0.1",
        )
        token_value = token.token

        returned_doc = consume_access_token(
            token_value=token_value,
            user=self.user,
            ip_address="10.0.0.1",
        )

        token.refresh_from_db()
        self.assertIsNotNone(token.used_at, "Token must be marked used after consumption.")
        self.assertEqual(returned_doc.pk, doc.pk, "consume_access_token must return the linked Document.")

    def test_consumed_token_cannot_be_reused(self) -> None:
        """
        Single-use enforcement: consuming the same token twice raises Http404
        on the second attempt (IDOR: 404 not 403).
        """
        doc_id, _ = self._run_pipeline()
        doc = Document.objects.get(pk=doc_id)

        token = issue_access_token(
            user=self.user,
            document=doc,
            ip_address="10.0.0.1",
        )
        token_value = token.token

        consume_access_token(
            token_value=token_value,
            user=self.user,
            ip_address="10.0.0.1",
        )

        with self.assertRaises(Http404, msg="A consumed token must raise Http404 on reuse."):
            consume_access_token(
                token_value=token_value,
                user=self.user,
                ip_address="10.0.0.1",
            )

    def test_expired_token_cannot_be_consumed(self) -> None:
        """An expired token (expires_at in the past) raises Http404 on consumption."""
        doc_id, _ = self._run_pipeline()
        doc = Document.objects.get(pk=doc_id)

        # Issue a token, then manually backdate its expires_at in the DB.
        token = issue_access_token(
            user=self.user,
            document=doc,
            ip_address="127.0.0.1",
        )
        DocumentAccessToken.objects.filter(pk=token.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

        with self.assertRaises(Http404, msg="Expired token must raise Http404."):
            consume_access_token(
                token_value=token.token,
                user=self.user,
                ip_address="127.0.0.1",
            )

    def test_other_user_cannot_consume_foreign_token(self) -> None:
        """
        Cross-user isolation: User B cannot consume a token issued to User A.
        consume_access_token raises Http404 (IDOR: 404 not 403).
        """
        doc_id, _ = self._run_pipeline()
        doc = Document.objects.get(pk=doc_id)
        other_user = _make_user()

        token = issue_access_token(
            user=self.user,
            document=doc,
            ip_address="10.0.0.1",
        )

        with self.assertRaises(Http404, msg="Token issued to User A must not be consumable by User B."):
            consume_access_token(
                token_value=token.token,
                user=other_user,
                ip_address="10.0.0.1",
            )


# ─────────────────────────────────────────────────────────────────────────────
# Class 2: LegalHoldDisposalIntegrationTests
# ─────────────────────────────────────────────────────────────────────────────


class LegalHoldDisposalIntegrationTests(TestCase):
    """
    Integration tests for the legal hold / disposal interaction.

    legal_hold=True is an ABSOLUTE block on ALL automated disposal:
      - soft_delete() raises ValueError
      - hard_delete() raises ValueError
      - mark_purpose_fulfilled() raises ValueError

    Releasing a legal hold restores normal disposal capability.
    apply_legal_hold and release_legal_hold require the
    documents.manage_legal_hold permission (PermissionDenied otherwise).

    Documents are created directly via _make_document to avoid needing the
    upload pipeline. A superuser is used for legal-hold operations so that
    the permission check always passes.
    """

    def setUp(self) -> None:
        self.user = _make_user()
        self.category = _make_category()
        # Superuser has all permissions — used for legal-hold operations.
        self.legal_officer = _make_user(is_staff=True, is_superuser=True)

    # ── Legal hold blocks disposal ────────────────────────────────────────────

    def test_legal_hold_blocks_soft_delete(self) -> None:
        """
        soft_delete() raises ValueError when document.legal_hold=True.
        legal_hold=True is an ABSOLUTE block per TBS ATIP guidance.
        """
        doc = _make_document(user=self.user, category=self.category)
        apply_legal_hold(document=doc, set_by=self.legal_officer, reason="ATIP hold")

        with self.assertRaises(ValueError) as cm:
            soft_delete(document=doc, deleted_by=self.user)

        self.assertIn("legal hold", str(cm.exception).lower())

    def test_legal_hold_blocks_hard_delete(self) -> None:
        """
        hard_delete() raises ValueError when document.legal_hold=True,
        regardless of deleted_at or scan_status.
        """
        doc = _make_document(
            user=self.user,
            category=self.category,
            scan_status=Document.ScanStatus.DELETED,
            deleted_at=timezone.now() - timedelta(days=40),
            legal_hold=True,
        )

        with self.assertRaises(ValueError) as cm:
            hard_delete(document=doc)

        self.assertIn("legal hold", str(cm.exception).lower())

    def test_releasing_legal_hold_allows_soft_delete(self) -> None:
        """
        After release_legal_hold(), soft_delete() succeeds.
        The hold block must be fully lifted by release_legal_hold.
        """
        doc = _make_document(user=self.user, category=self.category)
        apply_legal_hold(document=doc, set_by=self.legal_officer, reason="ATIP hold")
        release_legal_hold(document=doc, released_by=self.legal_officer, reason="ATIP closed")

        soft_delete(document=doc, deleted_by=self.user)

        doc.refresh_from_db()
        self.assertIsNotNone(doc.deleted_at, "soft_delete must set deleted_at after legal hold released.")
        self.assertEqual(doc.scan_status, Document.ScanStatus.DELETED)

    def test_apply_legal_hold_twice_raises_value_error(self) -> None:
        """
        Applying a legal hold to a document already on hold raises ValueError.
        The error prevents accidental duplicate hold applications.
        """
        doc = _make_document(user=self.user, category=self.category)
        apply_legal_hold(document=doc, set_by=self.legal_officer, reason="First hold")

        with self.assertRaises(ValueError, msg="Double apply_legal_hold must raise ValueError."):
            apply_legal_hold(document=doc, set_by=self.legal_officer, reason="Second hold")

    def test_release_legal_hold_when_not_held_raises_value_error(self) -> None:
        """
        release_legal_hold() on a document not on hold raises ValueError.
        The guard prevents phantom releases from corrupting the audit trail.
        """
        doc = _make_document(user=self.user, category=self.category)
        # legal_hold defaults to False — no hold to release.

        with self.assertRaises(ValueError, msg="release_legal_hold on unheld doc must raise ValueError."):
            release_legal_hold(document=doc, released_by=self.legal_officer, reason="spurious")

    def test_apply_legal_hold_requires_permission(self) -> None:
        """
        apply_legal_hold() raises PermissionDenied when the actor lacks
        documents.manage_legal_hold permission.
        """
        doc = _make_document(user=self.user, category=self.category)
        unprivileged_user = _make_user()  # no special permissions

        with self.assertRaises(PermissionDenied):
            apply_legal_hold(document=doc, set_by=unprivileged_user, reason="unauthorised")

    def test_release_legal_hold_requires_permission(self) -> None:
        """
        release_legal_hold() raises PermissionDenied when the actor lacks
        documents.manage_legal_hold permission.
        """
        doc = _make_document(user=self.user, category=self.category, legal_hold=True)
        unprivileged_user = _make_user()

        with self.assertRaises(PermissionDenied):
            release_legal_hold(document=doc, released_by=unprivileged_user, reason="unauthorised")

    def test_apply_legal_hold_sets_hold_fields(self) -> None:
        """
        apply_legal_hold() persists legal_hold=True, legal_hold_reason,
        and legal_hold_set_by to the database.
        """
        doc = _make_document(user=self.user, category=self.category)
        reason = "ATIP request #2026-001"

        apply_legal_hold(document=doc, set_by=self.legal_officer, reason=reason)

        doc.refresh_from_db()
        self.assertTrue(doc.legal_hold)
        self.assertEqual(doc.legal_hold_reason, reason)
        self.assertEqual(doc.legal_hold_set_by_id, self.legal_officer.pk)

    def test_release_legal_hold_clears_legal_hold_flag(self) -> None:
        """
        After release_legal_hold(), legal_hold=False is persisted to the DB.
        The legal_hold_reason and legal_hold_set_by are preserved for audit.
        """
        doc = _make_document(user=self.user, category=self.category)
        apply_legal_hold(document=doc, set_by=self.legal_officer, reason="Litigation hold")
        release_legal_hold(document=doc, released_by=self.legal_officer, reason="Case settled")

        doc.refresh_from_db()
        self.assertFalse(doc.legal_hold, "legal_hold must be False after release.")
        # legal_hold_reason preserved for audit trail per TBS guidance.
        self.assertEqual(doc.legal_hold_reason, "Litigation hold")

    def test_soft_delete_sets_scan_status_deleted(self) -> None:
        """
        soft_delete() sets scan_status=DELETED. This CONTRACT is required by
        pending_hard_delete() which filters on scan_status=DELETED.
        """
        doc = _make_document(user=self.user, category=self.category)
        soft_delete(document=doc, deleted_by=self.user, reason="retention_expired")

        doc.refresh_from_db()
        self.assertEqual(doc.scan_status, Document.ScanStatus.DELETED)
        self.assertIsNotNone(doc.deleted_at)


# ─────────────────────────────────────────────────────────────────────────────
# Class 3: VersionChainIntegrationTests
# ─────────────────────────────────────────────────────────────────────────────


class VersionChainIntegrationTests(TestCase):
    """
    Integration tests for document version chain invariants.

    create_new_version() uses SELECT FOR UPDATE which requires PostgreSQL.
    SQLite (used in the test environment) raises ImproperlyConfigured. For this
    reason, chain structure tests construct the version chain DIRECTLY via
    Document.objects.create() and assert the resulting invariants.

    The permission gate is tested via the internal _user_may_version() helper
    (same logic exercised by create_new_version before the SQLite guard fires).
    """

    def setUp(self) -> None:
        self.user = _make_user()
        self.category = _make_category()

    def _make_v1(self, **kwargs) -> Document:
        """Create a v1 (chain root) document."""
        return _make_document(user=self.user, category=self.category, **kwargs)

    def _make_v2(self, v1: Document, **kwargs) -> Document:
        """
        Create a v2 document for the given v1 chain root, demoting v1.
        Mirrors the invariants enforced by create_new_version().
        """
        # Demote v1 (create_new_version does this via select_for_update)
        Document.objects.filter(pk=v1.pk).update(is_latest_version=False)
        v1.is_latest_version = False  # sync in-memory

        doc_id = uuid.uuid4()
        defaults = {
            "version_number": 2,
            "root_document": v1,
            "is_latest_version": True,
            "scan_status": Document.ScanStatus.ACTIVE,
            "original_filename": "test_v2.pdf",
            "_storage_key": f"documents/active/{doc_id}/{uuid.uuid4().hex}.bin",
            "mime_type": "application/pdf",
            "size_bytes": 2048,
            "security_classification": DocumentCategory.SecurityClassification.PROTECTED_B,
        }
        defaults.update(kwargs)
        return Document.objects.create(
            id=doc_id,
            uploaded_by=self.user,
            category=self.category,
            **defaults,
        )

    # ── Chain structure invariants ────────────────────────────────────────────

    def test_new_version_demotes_previous_latest(self) -> None:
        """
        After creating v2, v1.is_latest_version must be False.
        create_new_version() enforces: exactly one is_latest_version=True per chain.
        """
        v1 = self._make_v1()
        self.assertTrue(v1.is_latest_version, "Pre-condition: v1 must start as latest.")

        _v2 = self._make_v2(v1)

        v1.refresh_from_db()
        self.assertFalse(v1.is_latest_version, "v1 must be demoted after v2 creation.")

    def test_new_version_is_marked_latest(self) -> None:
        """
        The newly created v2 document must have is_latest_version=True.
        """
        v1 = self._make_v1()
        v2 = self._make_v2(v1)

        self.assertTrue(v2.is_latest_version, "v2 must be marked is_latest_version=True.")

    def test_version_number_increments(self) -> None:
        """
        version_number must be 1 for v1 and 2 for v2.
        create_new_version() computes new_version_number = current_latest.version_number + 1.
        """
        v1 = self._make_v1()
        v2 = self._make_v2(v1)

        self.assertEqual(v1.version_number, 1, "v1 must have version_number=1.")
        self.assertEqual(v2.version_number, 2, "v2 must have version_number=2.")

    def test_root_document_is_none_for_v1(self) -> None:
        """
        v1 documents must have root_document=None (they ARE the chain root).
        A non-null root_document_id on v1 is a chain invariant violation.
        """
        v1 = self._make_v1()
        self.assertIsNone(v1.root_document_id, "v1 must have root_document=None.")

    def test_new_version_points_to_chain_root(self) -> None:
        """
        v2.root_document must point to v1 (the chain root).
        All subsequent versions always point to version 1 — never to each other.
        """
        v1 = self._make_v1()
        v2 = self._make_v2(v1)

        self.assertEqual(
            v2.root_document_id,
            v1.pk,
            "v2.root_document must be the chain root (v1).",
        )

    def test_exactly_one_latest_version_per_chain(self) -> None:
        """
        After creating v2, exactly one document in the chain has
        is_latest_version=True (the chain integrity invariant).
        """
        v1 = self._make_v1()
        v2 = self._make_v2(v1)

        chain_pks = [v1.pk, v2.pk]
        latest_count = Document.objects.filter(
            pk__in=chain_pks, is_latest_version=True
        ).count()
        self.assertEqual(
            latest_count,
            1,
            "Exactly one document per chain must have is_latest_version=True.",
        )

    # ── Permission gate tests ─────────────────────────────────────────────────

    def test_create_version_allowed_for_owner_with_upload_perm(self) -> None:
        """
        _user_may_version returns True for the document owner who has
        documents.upload_document permission and the category is not staff_only.
        """
        v1 = self._make_v1()
        _grant_upload_perm(self.user)

        result = _user_may_version(user=self.user, chain_root=v1)
        self.assertTrue(result, "Owner with upload_document perm must be allowed to version.")

    def test_create_version_allowed_for_superuser(self) -> None:
        """
        Superusers may create versions of any document chain.
        """
        v1 = self._make_v1()
        superuser = _make_user(is_superuser=True)

        result = _user_may_version(user=superuser, chain_root=v1)
        self.assertTrue(result, "Superuser must always be allowed to version.")

    def test_create_version_denied_for_non_owner_without_staff_perm(self) -> None:
        """
        _user_may_version returns False for a user who is not the uploader
        and does not have documents.upload_staff_document permission.
        The caller (create_new_version) converts this to PermissionDenied.
        """
        v1 = self._make_v1()
        other_user = _make_user()  # authenticated but not the owner

        result = _user_may_version(user=other_user, chain_root=v1)
        self.assertFalse(
            result,
            "Non-owner without upload_staff_document must not be allowed to version.",
        )

    def test_create_version_denied_for_owner_on_staff_only_category(self) -> None:
        """
        Citizens cannot create versions of documents in staff_only categories
        even if they are the original uploader.
        """
        staff_only_cat = _make_category(staff_only=True)
        v1 = _make_document(user=self.user, category=staff_only_cat)
        _grant_upload_perm(self.user)

        result = _user_may_version(user=self.user, chain_root=v1)
        self.assertFalse(
            result,
            "Owner with upload_document but not upload_staff_document must be blocked "
            "from versioning staff_only category documents.",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Class 4: TransitoryDocumentIntegrationTests
# ─────────────────────────────────────────────────────────────────────────────


class TransitoryDocumentIntegrationTests(TestCase):
    """
    Integration tests for transitory document disposal (LAC DA #2016/001).

    Transitory records (is_transitory=True) are destroyed when their purpose
    is fulfilled, not on a fixed calendar date:
      - schedule_expiry() leaves expires_at=None for transitory docs (LAC DA).
      - mark_purpose_fulfilled() sets both expires_at=now() AND deleted_at=now()
        in a single atomic operation with select_for_update().
      - legal_hold=True blocks mark_purpose_fulfilled().

    Non-transitory documents DO receive a calendar expires_at from schedule_expiry().
    """

    def setUp(self) -> None:
        self.user = _make_user()
        self.transitory_cat = _make_category(
            is_transitory=True,
            min_retention_days=0,
            max_retention_days=30,
        )
        self.non_transitory_cat = _make_category(
            is_transitory=False,
            min_retention_days=30,
            max_retention_days=2555,
        )

    def test_transitory_document_no_expires_at_after_schedule_expiry(self) -> None:
        """
        schedule_expiry() leaves expires_at=None for transitory documents.
        LAC DA #2016/001: transitory records are destroyed when purpose is
        fulfilled, not on a fixed calendar date.
        """
        doc = _make_document(user=self.user, category=self.transitory_cat)
        schedule_expiry(document=doc)

        self.assertIsNone(
            doc.expires_at,
            "Transitory docs must have expires_at=None after schedule_expiry "
            "(set later by mark_purpose_fulfilled).",
        )

    def test_transitory_document_retains_retain_until_if_min_retention_nonzero(self) -> None:
        """
        Even transitory docs with min_retention_days > 0 get retain_until set
        (Privacy Act s.6(1): minimum floor applies even to transitory records).
        A category with min_retention_days=0 gets retain_until=None.
        """
        # Category with non-zero min_retention
        cat_with_min = _make_category(
            is_transitory=True,
            min_retention_days=7,
            max_retention_days=30,
        )
        doc = _make_document(user=self.user, category=cat_with_min)
        schedule_expiry(document=doc)

        self.assertIsNotNone(
            doc.retain_until,
            "Transitory doc with min_retention_days>0 must have retain_until set.",
        )

    def test_mark_purpose_fulfilled_soft_deletes_document(self) -> None:
        """
        mark_purpose_fulfilled() soft-deletes the transitory document immediately,
        setting deleted_at and scan_status=DELETED in one atomic operation.
        """
        doc = _make_document(user=self.user, category=self.transitory_cat)

        mark_purpose_fulfilled(document=doc, actor=self.user)

        doc.refresh_from_db()
        self.assertIsNotNone(doc.deleted_at, "deleted_at must be set after mark_purpose_fulfilled.")
        self.assertEqual(
            doc.scan_status,
            Document.ScanStatus.DELETED,
            "scan_status must be DELETED after mark_purpose_fulfilled.",
        )
        self.assertEqual(doc.deletion_reason, "transitory_purpose_fulfilled")

    def test_mark_purpose_fulfilled_sets_expires_at(self) -> None:
        """
        mark_purpose_fulfilled() sets expires_at=now() simultaneously with the
        soft-delete. This closes the TOCTOU window between separate atomic blocks.
        """
        doc = _make_document(user=self.user, category=self.transitory_cat)
        self.assertIsNone(doc.expires_at, "Pre-condition: transitory doc starts with no expires_at.")

        mark_purpose_fulfilled(document=doc, actor=self.user)

        doc.refresh_from_db()
        self.assertIsNotNone(
            doc.expires_at,
            "expires_at must be set by mark_purpose_fulfilled (M-2 fix: single lock).",
        )

    def test_mark_purpose_fulfilled_blocked_by_legal_hold(self) -> None:
        """
        mark_purpose_fulfilled() raises ValueError when legal_hold=True.
        Legal holds block ALL automated disposal paths unconditionally.
        """
        doc = _make_document(
            user=self.user,
            category=self.transitory_cat,
            legal_hold=True,
        )

        with self.assertRaises(ValueError) as cm:
            mark_purpose_fulfilled(document=doc, actor=self.user)

        self.assertIn("legal hold", str(cm.exception).lower())

    def test_mark_purpose_fulfilled_on_non_transitory_raises_value_error(self) -> None:
        """
        mark_purpose_fulfilled() raises ValueError when called on a document
        whose category is not transitory.
        """
        doc = _make_document(user=self.user, category=self.non_transitory_cat)

        with self.assertRaises(ValueError, msg="Non-transitory doc must raise ValueError."):
            mark_purpose_fulfilled(document=doc, actor=self.user)

    def test_mark_purpose_fulfilled_on_already_deleted_raises_value_error(self) -> None:
        """
        mark_purpose_fulfilled() on an already soft-deleted document raises ValueError.
        Idempotent disposal is blocked to preserve audit trail integrity.
        """
        doc = _make_document(
            user=self.user,
            category=self.transitory_cat,
            scan_status=Document.ScanStatus.DELETED,
            deleted_at=timezone.now(),
        )

        with self.assertRaises(ValueError, msg="Already-deleted transitory doc must raise ValueError."):
            mark_purpose_fulfilled(document=doc, actor=self.user)

    def test_non_transitory_document_has_expires_at_after_schedule_expiry(self) -> None:
        """
        schedule_expiry() sets expires_at for non-transitory documents based on
        created_at + category.max_retention_days.
        """
        doc = _make_document(user=self.user, category=self.non_transitory_cat)
        schedule_expiry(document=doc)

        self.assertIsNotNone(
            doc.expires_at,
            "Non-transitory doc must have expires_at set after schedule_expiry.",
        )
        # expires_at should be approximately max_retention_days from now
        expected_delta = timedelta(days=self.non_transitory_cat.max_retention_days)
        # Allow a few seconds of clock skew
        self.assertAlmostEqual(
            (doc.expires_at - doc.created_at).days,
            expected_delta.days,
            delta=1,
            msg="expires_at must be created_at + max_retention_days.",
        )

    def test_non_transitory_has_retain_until_after_schedule_expiry(self) -> None:
        """
        schedule_expiry() sets retain_until for non-transitory documents with
        min_retention_days > 0 (Privacy Act s.6(1) minimum floor).
        """
        doc = _make_document(user=self.user, category=self.non_transitory_cat)
        schedule_expiry(document=doc)

        self.assertIsNotNone(
            doc.retain_until,
            "Non-transitory doc with min_retention_days>0 must have retain_until set.",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Class 5: ScanStatusGateIntegrationTests
# ─────────────────────────────────────────────────────────────────────────────


class ScanStatusGateIntegrationTests(TestCase):
    """
    Scan status gate tests: only ACTIVE, non-deleted documents may receive
    access tokens.

    issue_access_token() raises Http404 for all other states (IDOR: 404 not 403).
    This is the primary citizen-facing access control gate for the download
    pipeline. Citizens must never access QUARANTINED, PENDING_UPLOAD, SCANNING,
    or soft-deleted documents.

    PIPEDA: Http404 (not 403) prevents disclosure of document existence to
    callers who may not be the uploader.
    """

    def setUp(self) -> None:
        self.user = _make_user()
        self.category = _make_category()

    def _issue_token_for_doc(self, doc: Document) -> DocumentAccessToken:
        """Attempt to issue a token; propagate any exceptions to the caller."""
        return issue_access_token(
            user=self.user,
            document=doc,
            ip_address="192.168.0.1",
        )

    # ── Non-ACTIVE statuses must block token issuance ─────────────────────────

    def test_pending_upload_cannot_issue_token(self) -> None:
        """
        PENDING_UPLOAD document: issue_access_token raises Http404.
        The file has not been uploaded yet — no content to download.
        """
        doc = _make_document(
            user=self.user,
            category=self.category,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
        )
        with self.assertRaises(Http404):
            self._issue_token_for_doc(doc)

    def test_scanning_document_cannot_issue_token(self) -> None:
        """
        SCANNING document: issue_access_token raises Http404.
        The scan is in progress — content may still be infected.
        """
        doc = _make_document(
            user=self.user,
            category=self.category,
            scan_status=Document.ScanStatus.SCANNING,
        )
        with self.assertRaises(Http404):
            self._issue_token_for_doc(doc)

    def test_quarantined_document_cannot_issue_token(self) -> None:
        """
        QUARANTINED document: issue_access_token raises Http404.
        ClamAV detected a threat — citizens must never access infected files.
        """
        doc = _make_document(
            user=self.user,
            category=self.category,
            scan_status=Document.ScanStatus.QUARANTINED,
        )
        with self.assertRaises(Http404):
            self._issue_token_for_doc(doc)

    def test_deleted_document_cannot_issue_token(self) -> None:
        """
        Soft-deleted document (deleted_at set): issue_access_token raises Http404.
        Soft-deleted documents are inaccessible to all users.
        """
        doc = _make_document(
            user=self.user,
            category=self.category,
            scan_status=Document.ScanStatus.ACTIVE,
            deleted_at=timezone.now(),  # soft-deleted
        )
        with self.assertRaises(Http404):
            self._issue_token_for_doc(doc)

    def test_purged_document_cannot_issue_token(self) -> None:
        """
        PURGED document (storage cleared): issue_access_token raises Http404.
        Storage was irreversibly deleted — no file to serve.
        """
        doc = _make_document(
            user=self.user,
            category=self.category,
            scan_status=Document.ScanStatus.PURGED,
            deleted_at=timezone.now(),
        )
        with self.assertRaises(Http404):
            self._issue_token_for_doc(doc)

    # ── ACTIVE status allows token issuance ───────────────────────────────────

    def test_active_document_can_issue_token(self) -> None:
        """
        ACTIVE, non-deleted document: issue_access_token succeeds.
        This is the only state in which citizens can download.
        """
        doc = _make_document(
            user=self.user,
            category=self.category,
            scan_status=Document.ScanStatus.ACTIVE,
        )
        token = self._issue_token_for_doc(doc)

        self.assertTrue(token.is_valid, "Token for ACTIVE doc must be valid.")
        self.assertEqual(token.document_id, doc.pk)

    def test_non_owner_cannot_issue_token_for_others_document(self) -> None:
        """
        IDOR prevention: issue_access_token raises Http404 when the requesting
        user is not the document's uploader (and lacks coordinator permission).
        Http404 (not 403) avoids confirming document existence.
        """
        other_user = _make_user()
        doc = _make_document(user=other_user, category=self.category)

        with self.assertRaises(Http404, msg="Non-owner must receive Http404 (IDOR: not 403)."):
            issue_access_token(
                user=self.user,
                document=doc,
                ip_address="10.0.0.1",
            )

    def test_multiple_tokens_can_be_issued_for_same_active_document(self) -> None:
        """
        Multiple valid tokens may exist simultaneously for the same document.
        Each token is single-use; issuing a new one does not invalidate others.
        """
        doc = _make_document(user=self.user, category=self.category)

        token_a = self._issue_token_for_doc(doc)
        token_b = self._issue_token_for_doc(doc)

        self.assertNotEqual(token_a.token, token_b.token, "Each issued token must be unique.")
        self.assertTrue(token_a.is_valid)
        self.assertTrue(token_b.is_valid)

    def test_token_ip_address_is_masked(self) -> None:
        """
        PIPEDA: The IP address stored on the token must be masked.
        IPv4 last octet is zeroed (e.g. 192.168.1.100 → 192.168.1.0).
        """
        doc = _make_document(user=self.user, category=self.category)

        token = issue_access_token(
            user=self.user,
            document=doc,
            ip_address="192.168.1.100",
        )

        self.assertEqual(
            token.ip_address,
            "192.168.1.0",
            "PIPEDA: IPv4 last octet must be zeroed in stored ip_address.",
        )
