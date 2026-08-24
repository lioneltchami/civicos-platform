"""
Wave 7 — §24.1 canonical test file: test_admin.py

Tests the Document admin interface for PIPEDA compliance and security invariants.

Invariants tested:
  - _storage_key NEVER in list_display, fieldsets, search_fields, or readonly_fields
  - original_filename NOT in list_display (only in readonly_fields for detail views)
  - has_add_permission() returns False for DocumentAdmin
  - has_change_permission() returns False for DocumentAdmin
  - has_delete_permission() returns False for DocumentAdmin and DocumentCategoryAdmin
  - scan_engine_result NOT in list_display
  - uploaded_by_id (int FK PK) used — NOT uploaded_by (which exposes .email via __str__)
  - DocumentAdmin is read-only: admin can observe but not mutate
"""

import uuid
from unittest.mock import MagicMock

from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from apps.documents.admin import (
    DocumentAccessTokenAdmin,
    DocumentAdmin,
    DocumentAttachmentAdmin,
    DocumentCategoryAdmin,
)
from apps.documents.models import Document, DocumentCategory

User = get_user_model()

_CTR = 0


def _make_superuser(**kwargs):
    global _CTR
    _CTR += 1
    return User.objects.create_superuser(
        email=f"admin{_CTR}@example.com",
        password="testpass123",
        **kwargs,
    )


def _make_user(**kwargs):
    global _CTR
    _CTR += 1
    return User.objects.create_user(
        email=f"user{_CTR}@example.com",
        password="testpass123",
        **kwargs,
    )


def _make_category(**kwargs):
    global _CTR
    _CTR += 1
    return DocumentCategory.objects.create(
        name_en="Admin Test",
        name_fr="Test Admin",
        slug=f"admin-cat-{_CTR}",
        allowed_mime_types=["application/pdf"],
        min_retention_days=730,
        max_retention_days=2555,
        **kwargs,
    )


def _make_document(user, category, **kwargs):
    doc_id = uuid.uuid4()
    return Document.objects.create(
        uploaded_by=user,
        category=category,
        original_filename="private-document.pdf",
        _storage_key=f"documents/active/{doc_id}/{uuid.uuid4().hex}.bin",
        mime_type="application/pdf",
        size_bytes=4_096,
        scan_status=Document.ScanStatus.ACTIVE,
        **kwargs,
    )


def _make_request(user):
    rf = RequestFactory()
    request = rf.get("/admin/documents/document/")
    request.user = user
    return request


# ─────────────────────────────────────────────────────────────────────────────
# DocumentAdmin — storage_key exclusion
# ─────────────────────────────────────────────────────────────────────────────


class DocumentAdminStorageKeyTests(TestCase):
    """_storage_key must NEVER appear in any admin configuration."""

    def setUp(self):
        self.site = AdminSite()
        self.admin = DocumentAdmin(Document, self.site)

    def test_storage_key_not_in_list_display(self):
        for field in self.admin.list_display:
            self.assertNotIn("storage_key", str(field))
            self.assertNotIn("_storage_key", str(field))

    def test_storage_key_not_in_readonly_fields(self):
        for field in self.admin.readonly_fields:
            self.assertNotIn("storage_key", str(field))
            self.assertNotIn("_storage_key", str(field))

    def test_storage_key_not_in_search_fields(self):
        for field in self.admin.search_fields:
            self.assertNotIn("storage_key", str(field))
            self.assertNotIn("_storage_key", str(field))

    def test_storage_key_not_in_any_fieldset(self):
        for _title, options in self.admin.fieldsets:
            for field in options.get("fields", ()):
                self.assertNotIn("storage_key", str(field))
                self.assertNotIn("_storage_key", str(field))

    def test_storage_key_not_in_list_filter(self):
        for f in self.admin.list_filter:
            self.assertNotIn("storage_key", str(f))


# ─────────────────────────────────────────────────────────────────────────────
# DocumentAdmin — original_filename PII constraints
# ─────────────────────────────────────────────────────────────────────────────


class DocumentAdminOriginalFilenameTests(TestCase):
    """original_filename must NOT appear in list_display (bulk PII exposure)."""

    def setUp(self):
        self.site = AdminSite()
        self.admin = DocumentAdmin(Document, self.site)

    def test_original_filename_not_in_list_display(self):
        """Never show original_filename in the list view — PIPEDA bulk exposure risk."""
        self.assertNotIn("original_filename", self.admin.list_display)

    def test_original_filename_not_in_search_fields(self):
        """
        Searching by original_filename exposes PII in search result snippets.
        It must NOT be a search field.
        """
        self.assertNotIn("original_filename", self.admin.search_fields)

    def test_original_filename_in_readonly_fields_for_detail(self):
        """
        original_filename IS allowed in readonly_fields for detail views
        (restricted to staff with coordinator_view_document permission).
        """
        # Not asserting it IS there — the spec allows but doesn't mandate it.
        # What matters is it's not EDITABLE. Verify no editable fieldset includes it.
        editable_fieldset_fields = set()
        for _title, options in self.admin.fieldsets:
            for field in options.get("fields", ()):
                if field not in self.admin.readonly_fields:
                    editable_fieldset_fields.add(field)
        # original_filename must not be editable
        self.assertNotIn("original_filename", editable_fieldset_fields)


# ─────────────────────────────────────────────────────────────────────────────
# DocumentAdmin — permission overrides (immutability)
# ─────────────────────────────────────────────────────────────────────────────


class DocumentAdminPermissionTests(TestCase):
    """DocumentAdmin must be read-only: no add, change, or delete."""

    def setUp(self):
        self.site = AdminSite()
        self.admin = DocumentAdmin(Document, self.site)
        self.superuser = _make_superuser()
        self.request = _make_request(self.superuser)

    def test_has_add_permission_false(self):
        self.assertFalse(self.admin.has_add_permission(self.request))

    def test_has_change_permission_false_without_obj(self):
        self.assertFalse(self.admin.has_change_permission(self.request))

    def test_has_change_permission_false_with_obj(self):
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)
        self.assertFalse(self.admin.has_change_permission(self.request, obj=doc))

    def test_has_delete_permission_false_without_obj(self):
        self.assertFalse(self.admin.has_delete_permission(self.request))

    def test_has_delete_permission_false_with_obj(self):
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)
        self.assertFalse(self.admin.has_delete_permission(self.request, obj=doc))

    def test_read_only_even_for_superuser(self):
        """Even superuser cannot add/change/delete via admin."""
        self.assertTrue(self.superuser.is_superuser)
        self.assertFalse(self.admin.has_add_permission(self.request))
        self.assertFalse(self.admin.has_change_permission(self.request))
        self.assertFalse(self.admin.has_delete_permission(self.request))


# ─────────────────────────────────────────────────────────────────────────────
# DocumentAdmin — list_display PIPEDA compliance
# ─────────────────────────────────────────────────────────────────────────────


class DocumentAdminListDisplayTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = DocumentAdmin(Document, self.site)

    def test_list_display_uses_uploaded_by_id_not_email(self):
        """
        PIPEDA: list_display must use uploaded_by_id (int PK) not uploaded_by
        (which calls User.__str__ and exposes email).
        """
        self.assertIn("uploaded_by_id", self.admin.list_display)
        self.assertNotIn("uploaded_by", self.admin.list_display)

    def test_scan_engine_result_not_in_list_display(self):
        """scan_engine_result is staff-internal, never in the list view."""
        self.assertNotIn("scan_engine_result", self.admin.list_display)

    def test_list_display_has_scan_status_badge(self):
        self.assertIn("scan_status_badge", self.admin.list_display)


# ─────────────────────────────────────────────────────────────────────────────
# DocumentAdmin — fieldset content
# ─────────────────────────────────────────────────────────────────────────────


class DocumentAdminFieldsetTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = DocumentAdmin(Document, self.site)

    def _all_fieldset_fields(self):
        fields = set()
        for _title, options in self.admin.fieldsets:
            for f in options.get("fields", ()):
                fields.add(f)
        return fields

    def test_no_storage_key_in_any_fieldset(self):
        for field in self._all_fieldset_fields():
            self.assertNotIn("storage_key", str(field))

    def test_scan_engine_result_in_fieldset_for_detail(self):
        """scan_engine_result IS shown in detail view fieldset for forensics."""
        self.assertIn("scan_engine_result", self._all_fieldset_fields())

    def test_legal_hold_related_fields_in_fieldset(self):
        fields = self._all_fieldset_fields()
        self.assertIn("legal_hold", fields)
        self.assertIn("legal_hold_reason", fields)


# ─────────────────────────────────────────────────────────────────────────────
# DocumentCategoryAdmin — delete permission
# ─────────────────────────────────────────────────────────────────────────────


class DocumentCategoryAdminPermissionTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = DocumentCategoryAdmin(DocumentCategory, self.site)
        self.superuser = _make_superuser()
        self.request = _make_request(self.superuser)

    def test_has_delete_permission_false(self):
        """Category records are disposition authority records — never deletable via admin."""
        self.assertFalse(self.admin.has_delete_permission(self.request))

    def test_has_delete_permission_false_with_obj(self):
        cat = _make_category()
        self.assertFalse(self.admin.has_delete_permission(self.request, obj=cat))


# ─────────────────────────────────────────────────────────────────────────────
# DocumentAccessTokenAdmin — immutability
# ─────────────────────────────────────────────────────────────────────────────


class DocumentAccessTokenAdminPermissionTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = DocumentAccessTokenAdmin(
            admin.site._registry.get(Document.__class__, MagicMock()),
            self.site,
        )
        # Re-create with correct model
        from apps.documents.models import DocumentAccessToken

        self.admin = DocumentAccessTokenAdmin(DocumentAccessToken, self.site)
        self.superuser = _make_superuser()
        self.request = _make_request(self.superuser)

    def test_has_add_permission_false(self):
        self.assertFalse(self.admin.has_add_permission(self.request))

    def test_has_change_permission_false(self):
        self.assertFalse(self.admin.has_change_permission(self.request))

    def test_has_delete_permission_false(self):
        self.assertFalse(self.admin.has_delete_permission(self.request))


# ─────────────────────────────────────────────────────────────────────────────
# DocumentAttachmentAdmin — immutability
# ─────────────────────────────────────────────────────────────────────────────


class DocumentAttachmentAdminPermissionTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        from apps.documents.models import DocumentAttachment

        self.admin = DocumentAttachmentAdmin(DocumentAttachment, self.site)
        self.superuser = _make_superuser()
        self.request = _make_request(self.superuser)

    def test_has_add_permission_false(self):
        self.assertFalse(self.admin.has_add_permission(self.request))

    def test_has_change_permission_false(self):
        self.assertFalse(self.admin.has_change_permission(self.request))

    def test_has_delete_permission_false(self):
        self.assertFalse(self.admin.has_delete_permission(self.request))


# ─────────────────────────────────────────────────────────────────────────────
# Admin registration tests
# ─────────────────────────────────────────────────────────────────────────────


class AdminRegistrationTests(TestCase):
    """All models must be registered with the correct admin classes."""

    def test_document_registered(self):
        self.assertIn(Document, admin.site._registry)

    def test_document_category_registered(self):
        self.assertIn(DocumentCategory, admin.site._registry)

    def test_document_admin_class(self):
        self.assertIsInstance(admin.site._registry[Document], DocumentAdmin)

    def test_document_category_admin_class(self):
        self.assertIsInstance(admin.site._registry[DocumentCategory], DocumentCategoryAdmin)
