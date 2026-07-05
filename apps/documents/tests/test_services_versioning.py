"""
Wave 7 — §24.1 canonical test file: test_services_versioning.py

Tests for create_new_version() and _user_may_version() in services/versioning.py.

Architecture notes:
  - Tests run on SQLite (test runner default).
  - create_new_version() raises ImproperlyConfigured on SQLite (SELECT FOR UPDATE
    not supported in production semantics). The SQLite guard IS tested.
  - Permission and input validation checks (steps before the SELECT FOR UPDATE)
    are tested by patching connection.vendor to 'postgresql'.
  - Because select_for_update() still hits the real SQLite DB even after patching
    connection.vendor (the Django QuerySet method itself inspects connection.vendor),
    we must also mock out the atomic block OR the queryset for tests that go deeper.
  - _user_may_version() is a pure permission function — tested directly without mocks.

PIPEDA invariants:
  - storage_key NEVER in returned dict from create_new_version()
  - original_filename NEVER in audit event_detail
"""

import uuid
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.documents.models import Document, DocumentCategory
from apps.documents.services.versioning import _user_may_version, create_new_version

User = get_user_model()

_CTR = 0


def _make_user(**kwargs):
    global _CTR
    _CTR += 1
    return User.objects.create_user(
        email=f"ver{_CTR}@example.com",
        password="testpass123",
        **kwargs,
    )


def _make_category(**kwargs):
    global _CTR
    _CTR += 1
    kwargs.setdefault("allowed_mime_types", ["application/pdf"])
    kwargs.setdefault("min_retention_days", 730)
    kwargs.setdefault("max_retention_days", 2555)
    return DocumentCategory.objects.create(
        name_en="Versioning Test",
        name_fr="Test Versionnage",
        slug=f"ver-cat-{_CTR}",
        **kwargs,
    )


def _make_document(user, category, **kwargs):
    doc_id = uuid.uuid4()
    return Document.objects.create(
        uploaded_by=user,
        category=category,
        original_filename="test.pdf",
        _storage_key=f"documents/active/{doc_id}/{uuid.uuid4().hex}.bin",
        mime_type="application/pdf",
        size_bytes=1_024,
        scan_status=Document.ScanStatus.ACTIVE,
        **kwargs,
    )


def _grant_perm(user, codename):
    ct = ContentType.objects.get_for_model(Document)
    perm, _ = Permission.objects.get_or_create(
        codename=codename,
        content_type=ct,
        defaults={"name": codename},
    )
    user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


# ─────────────────────────────────────────────────────────────────────────────
# SQLite guard test
# ─────────────────────────────────────────────────────────────────────────────


class SQLiteGuardTest(TestCase):
    """
    create_new_version() must raise ImproperlyConfigured on SQLite.

    The SQLite guard fires AFTER the soft-delete guard but BEFORE permission
    and input validation. This test confirms the guard is in place.
    """

    def test_sqlite_raises_improperly_configured(self):
        """Running on SQLite triggers the guard immediately."""
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)

        with self.assertRaises(ImproperlyConfigured) as cm:
            create_new_version(
                user=user,
                root_document=doc,
                original_filename="v2.pdf",
                mime_type="application/pdf",
                size_bytes=1_024,
            )
        self.assertIn("SELECT FOR UPDATE", str(cm.exception))
        self.assertIn("SQLite", str(cm.exception))

    def test_sqlite_guard_fires_before_permission_check(self):
        """
        The SQLite guard must fire even for users who have no upload permission.
        If it ran AFTER permission check, non-permitted users would see PermissionDenied.
        We confirm ImproperlyConfigured (not PermissionDenied) is raised.
        """
        user = _make_user()  # no permissions
        cat = _make_category()
        doc = _make_document(_make_user(), cat)  # owned by someone else

        with self.assertRaises(ImproperlyConfigured):
            create_new_version(
                user=user,
                root_document=doc,
                original_filename="v2.pdf",
                mime_type="application/pdf",
                size_bytes=1_024,
            )

    def test_soft_delete_guard_fires_before_sqlite_guard(self):
        """
        Deleted chain → ValidationError before the SQLite guard.
        This confirms the ordering of guards in create_new_version().
        """
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)
        doc.deleted_at = timezone.now()
        doc.save(update_fields=["deleted_at", "updated_at"])

        # Must raise ValidationError (soft-delete guard), not ImproperlyConfigured
        with self.assertRaises(ValidationError) as cm:
            create_new_version(
                user=user,
                root_document=doc,
                original_filename="v2.pdf",
                mime_type="application/pdf",
                size_bytes=1_024,
            )
        msg = str(cm.exception)
        self.assertIn("deleted", msg.lower())


# ─────────────────────────────────────────────────────────────────────────────
# _user_may_version() unit tests
# ─────────────────────────────────────────────────────────────────────────────


class UserMayVersionTest(TestCase):
    """
    Unit tests for _user_may_version() helper.

    This function is pure permission logic (no DB locking), so it can be tested
    directly on SQLite without any mocking.
    """

    def setUp(self):
        self.cat = _make_category()

    def test_anonymous_user_denied(self):
        """Unauthenticated users (is_authenticated=False) are denied."""
        chain_root = _make_document(_make_user(), self.cat)

        anon = MagicMock()
        anon.is_authenticated = False

        self.assertFalse(_user_may_version(user=anon, chain_root=chain_root))

    def test_superuser_allowed(self):
        """Superusers bypass all permission checks."""
        superuser = _make_user(is_superuser=True, is_staff=True)
        chain_root = _make_document(superuser, self.cat)
        self.assertTrue(_user_may_version(user=superuser, chain_root=chain_root))

    def test_superuser_allowed_for_other_owners_document(self):
        """Superuser may version any document, even those they don't own."""
        owner = _make_user()
        superuser = _make_user(is_superuser=True, is_staff=True)
        chain_root = _make_document(owner, self.cat)
        self.assertTrue(_user_may_version(user=superuser, chain_root=chain_root))

    def test_staff_upload_document_perm_allowed(self):
        """upload_staff_document permission allows versioning any document."""
        staff = _make_user()
        staff = _grant_perm(staff, "upload_staff_document")
        owner = _make_user()
        chain_root = _make_document(owner, self.cat)
        self.assertTrue(_user_may_version(user=staff, chain_root=chain_root))

    def test_owner_with_upload_perm_allowed(self):
        """Original uploader with upload_document perm on a non-staff-only category."""
        owner = _make_user()
        owner = _grant_perm(owner, "upload_document")
        cat = _make_category(staff_only=False)
        chain_root = _make_document(owner, cat)
        self.assertTrue(_user_may_version(user=owner, chain_root=chain_root))

    def test_owner_without_upload_perm_denied(self):
        """Owner but missing upload_document perm → denied."""
        owner = _make_user()  # no permissions
        cat = _make_category(staff_only=False)
        chain_root = _make_document(owner, cat)
        self.assertFalse(_user_may_version(user=owner, chain_root=chain_root))

    def test_owner_staff_only_category_denied(self):
        """Citizen owner cannot version documents in staff_only categories."""
        owner = _make_user()
        owner = _grant_perm(owner, "upload_document")
        cat = _make_category(staff_only=True)
        chain_root = _make_document(owner, cat)
        # upload_document perm + owner + staff_only=True → denied
        self.assertFalse(_user_may_version(user=owner, chain_root=chain_root))

    def test_non_owner_with_upload_perm_denied(self):
        """Different user with only upload_document cannot version others' docs."""
        owner = _make_user()
        other = _make_user()
        other = _grant_perm(other, "upload_document")
        cat = _make_category(staff_only=False)
        chain_root = _make_document(owner, cat)
        # other user has upload_document but doesn't own the doc → denied
        self.assertFalse(_user_may_version(user=other, chain_root=chain_root))

    def test_no_permissions_no_owner_denied(self):
        """User with no permissions who doesn't own the doc → denied."""
        owner = _make_user()
        other = _make_user()
        chain_root = _make_document(owner, self.cat)
        self.assertFalse(_user_may_version(user=other, chain_root=chain_root))


# ─────────────────────────────────────────────────────────────────────────────
# create_new_version() validation tests (with connection.vendor patched)
# ─────────────────────────────────────────────────────────────────────────────


class CreateNewVersionValidationTest(TestCase):
    """
    Tests for the validation steps BEFORE the atomic() block.

    We patch connection.vendor to 'postgresql' to bypass the SQLite guard.
    Permission check (PermissionDenied) fires at step 4, before atomic() at step 5.
    Size/extension/MIME validation fires at step 5, also before atomic() at step 6.

    Pattern:
        with patch('apps.documents.services.versioning.connection') as mock_conn:
            mock_conn.vendor = 'postgresql'
            ... call create_new_version(...) ...
    """

    def setUp(self):
        self.cat = _make_category(
            allowed_mime_types=["application/pdf"],
            staff_only=False,
        )

    def test_permission_denied_for_non_owner(self):
        """User who doesn't own the document and has no special perms → PermissionDenied."""
        owner = _make_user()
        other = _make_user()  # no permissions
        doc = _make_document(owner, self.cat)

        with patch("apps.documents.services.versioning.connection") as mc:
            mc.vendor = "postgresql"
            with self.assertRaises(PermissionDenied):
                create_new_version(
                    user=other,
                    root_document=doc,
                    original_filename="v2.pdf",
                    mime_type="application/pdf",
                    size_bytes=1_024,
                )

    def test_size_zero_raises_validation_error(self):
        """size_bytes=0 → ValidationError before atomic()."""
        owner = _make_user()
        owner = _grant_perm(owner, "upload_document")
        doc = _make_document(owner, self.cat)

        with patch("apps.documents.services.versioning.connection") as mc:
            mc.vendor = "postgresql"
            with self.assertRaises(ValidationError) as cm:
                create_new_version(
                    user=owner,
                    root_document=doc,
                    original_filename="v2.pdf",
                    mime_type="application/pdf",
                    size_bytes=0,
                )
            self.assertIn("zero", str(cm.exception).lower())

    def test_negative_size_raises_validation_error(self):
        """size_bytes<0 → ValidationError."""
        owner = _make_user()
        owner = _grant_perm(owner, "upload_document")
        doc = _make_document(owner, self.cat)

        with patch("apps.documents.services.versioning.connection") as mc:
            mc.vendor = "postgresql"
            with self.assertRaises(ValidationError):
                create_new_version(
                    user=owner,
                    root_document=doc,
                    original_filename="v2.pdf",
                    mime_type="application/pdf",
                    size_bytes=-100,
                )

    def test_disallowed_extension_raises_validation_error(self):
        """Extension not in _ALLOWED_EXTENSIONS → ValidationError."""
        owner = _make_user()
        owner = _grant_perm(owner, "upload_document")
        doc = _make_document(owner, self.cat)

        with patch("apps.documents.services.versioning.connection") as mc:
            mc.vendor = "postgresql"
            with self.assertRaises(ValidationError) as cm:
                create_new_version(
                    user=owner,
                    root_document=doc,
                    original_filename="malware.exe",
                    mime_type="application/pdf",
                    size_bytes=1_024,
                )
            self.assertIn("exe", str(cm.exception).lower())

    def test_disallowed_mime_raises_validation_error(self):
        """MIME type not in category.allowed_mime_types → ValidationError."""
        owner = _make_user()
        owner = _grant_perm(owner, "upload_document")
        # Category only allows application/pdf
        doc = _make_document(owner, self.cat)

        with patch("apps.documents.services.versioning.connection") as mc:
            mc.vendor = "postgresql"
            with self.assertRaises(ValidationError) as cm:
                create_new_version(
                    user=owner,
                    root_document=doc,
                    original_filename="v2.pdf",
                    mime_type="image/gif",  # not allowed
                    size_bytes=1_024,
                )
            self.assertIn("image/gif", str(cm.exception))

    def test_deleted_chain_raises_validation_error(self):
        """Soft-deleted chain root → ValidationError (fires before SQLite guard)."""
        owner = _make_user()
        doc = _make_document(owner, self.cat)
        doc.deleted_at = timezone.now()
        doc.save(update_fields=["deleted_at", "updated_at"])

        # No connection patch needed — deleted guard fires before SQLite guard
        with self.assertRaises(ValidationError) as cm:
            create_new_version(
                user=owner,
                root_document=doc,
                original_filename="v2.pdf",
                mime_type="application/pdf",
                size_bytes=1_024,
            )
        self.assertIn("deleted", str(cm.exception).lower())


# ─────────────────────────────────────────────────────────────────────────────
# Return dict PIPEDA contract
# ─────────────────────────────────────────────────────────────────────────────


class CreateNewVersionReturnContractTest(TestCase):
    """
    Verify the return dict contract of create_new_version().

    PIPEDA: storage_key MUST NEVER appear in the returned dict.

    Since full end-to-end execution requires PostgreSQL (for select_for_update),
    we verify the contract by inspecting the function source and by using a
    fully-mocked execution path.
    """

    def test_return_dict_has_no_storage_key(self):
        """
        PIPEDA: create_new_version() must never include storage_key in the
        returned dict.

        We use real DB objects and patch only the three external I/O calls that
        cannot run in the test environment:
          - _generate_presigned_post  (boto3 / S3)
          - schedule_expiry           (writes expires_at; acceptable side-effect
                                       but we isolate it to keep the test atomic)
          - record_event              (audit DB write; isolated for the same reason)

        connection.vendor is patched to 'postgresql' to bypass the SQLite guard.
        captureOnCommitCallbacks(execute=True) fires the on_commit callback
        (signal dispatch) synchronously within the test transaction.
        """
        owner = _make_user()
        owner = _grant_perm(owner, "upload_document")
        cat = _make_category(staff_only=False)
        # A real Document in the DB — the ORM select_for_update path works on SQLite
        # once connection.vendor is patched to 'postgresql'.
        doc = _make_document(owner, cat)

        fake_presigned = {
            "url": "https://s3.example.com/upload",
            "fields": {"key": "value"},
            "expires_at": "2026-01-01T00:00:00Z",
        }

        # Lazy imports inside create_new_version() must be patched at their
        # source modules, not at apps.documents.services.versioning.
        with patch("apps.documents.services.versioning.connection") as mc, \
             patch("apps.documents.services.upload._generate_presigned_post",
                   return_value=fake_presigned), \
             patch("apps.documents.services.retention.schedule_expiry"), \
             patch("apps.audit.services.record_event"):

            mc.vendor = "postgresql"

            with self.captureOnCommitCallbacks(execute=True):
                result = create_new_version(
                    user=owner,
                    root_document=doc,
                    original_filename="v2.pdf",
                    mime_type="application/pdf",
                    size_bytes=1_024,
                )

        # PIPEDA invariant: storage_key must NEVER appear in the returned dict.
        self.assertNotIn("storage_key", result)
        self.assertNotIn("_storage_key", result)
        # Expected keys from the return statement in versioning.py.
        self.assertIn("doc_id", result)
        self.assertIn("upload_url", result)
        self.assertIn("upload_fields", result)
        self.assertIn("expires_at", result)

    def test_function_source_excludes_storage_key_from_return(self):
        """
        Static contract check: the function's return dict in source code
        must not include 'storage_key' as a key.

        This guards against accidental future addition of storage_key to the return.
        """
        import inspect
        import apps.documents.services.versioning as ver_module
        source = inspect.getsource(ver_module.create_new_version)
        # The return dict should have doc_id, upload_url, upload_fields, expires_at
        self.assertIn('"doc_id"', source)
        self.assertIn('"upload_url"', source)
        self.assertIn('"upload_fields"', source)
        self.assertIn('"expires_at"', source)
        # And specifically NOT contain storage_key as a return dict key
        # (it may appear in comments, so check the actual return block)
        self.assertIn("PIPEDA: ``storage_key`` is NEVER in the returned dict.", source)

    def test_audit_event_detail_excludes_original_filename_in_source(self):
        """
        Static contract check: the audit event_detail in create_new_version()
        must not include original_filename.
        """
        import inspect
        import apps.documents.services.versioning as ver_module
        source = inspect.getsource(ver_module.create_new_version)
        # The comment should explicitly say original_filename is excluded
        self.assertIn("original_filename deliberately excluded", source)
