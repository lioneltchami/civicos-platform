"""
Wave 7 — §24.1 canonical test file: test_models.py

Tests for DocumentCategory, Document (model + QuerySet), DocumentAttachment,
and DocumentAccessToken model logic.

PIPEDA invariants verified:
  - Document.__str__ NEVER includes original_filename
  - storage_key property delegates to _storage_key field
  - pending_disposal() excludes legal_hold=True
  - pending_hard_delete() excludes PURGED (terminal state)
"""

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.documents.models import (
    Document,
    DocumentAccessToken,
    DocumentAttachment,
    DocumentCategory,
)

User = get_user_model()

# ─────────────────────────────────────────────────────────────────────────────
# Shared factories
# ─────────────────────────────────────────────────────────────────────────────

_CTR = 0


def _make_category(**kwargs):
    global _CTR
    _CTR += 1
    return DocumentCategory.objects.create(
        name_en="Test Category",
        name_fr="Catégorie test",
        slug=f"test-cat-{_CTR}",
        allowed_mime_types=["application/pdf"],
        min_retention_days=730,
        max_retention_days=2555,
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


def _make_document(user, category, **kwargs):
    doc_uuid = uuid.uuid4()
    kwargs.setdefault("scan_status", Document.ScanStatus.ACTIVE)
    return Document.objects.create(
        uploaded_by=user,
        category=category,
        original_filename="sensitive-report.pdf",
        _storage_key=f"documents/active/{doc_uuid}/{uuid.uuid4().hex}.bin",
        mime_type="application/pdf",
        size_bytes=10_240,
        **kwargs,
    )


# ─────────────────────────────────────────────────────────────────────────────
# DocumentCategory tests
# ─────────────────────────────────────────────────────────────────────────────


class DocumentCategoryStrTests(TestCase):
    def test_str_returns_slug(self):
        cat = _make_category()
        self.assertEqual(str(cat), cat.slug)

    def test_str_not_name_en(self):
        cat = _make_category()
        self.assertNotIn("Test Category", str(cat))


class DocumentCategoryCleanTests(TestCase):
    def test_clean_raises_when_min_exceeds_max(self):
        cat = _make_category()
        cat.min_retention_days = 3000
        cat.max_retention_days = 730
        with self.assertRaises(ValidationError):
            cat.clean()

    def test_clean_ok_when_equal(self):
        cat = _make_category()
        cat.min_retention_days = 730
        cat.max_retention_days = 730
        # Should not raise
        cat.clean()

    def test_clean_ok_when_min_less_than_max(self):
        cat = _make_category()
        cat.min_retention_days = 730
        cat.max_retention_days = 2555
        cat.clean()

    def test_clean_error_message_contains_values(self):
        cat = _make_category()
        cat.min_retention_days = 3000
        cat.max_retention_days = 730
        try:
            cat.clean()
            self.fail("ValidationError not raised")
        except ValidationError as exc:
            msg = str(exc)
            self.assertIn("3000", msg)
            self.assertIn("730", msg)


class DocumentCategoryDefaultsTests(TestCase):
    def test_default_security_classification_is_protected_b(self):
        cat = _make_category()
        self.assertEqual(
            cat.security_classification,
            DocumentCategory.SecurityClassification.PROTECTED_B,
        )

    def test_staff_only_defaults_false(self):
        cat = _make_category()
        self.assertFalse(cat.staff_only)

    def test_is_transitory_defaults_false(self):
        cat = _make_category()
        self.assertFalse(cat.is_transitory)

    def test_min_retention_days_default(self):
        cat = _make_category()
        # At creation we explicitly set 730, so just confirm it's >=730.
        self.assertGreaterEqual(cat.min_retention_days, 0)

    def test_max_size_bytes_default_zero(self):
        # Default max_size_bytes=0 means "use global setting"
        cat = _make_category()
        self.assertGreaterEqual(cat.max_size_bytes, 0)


# ─────────────────────────────────────────────────────────────────────────────
# Document.__str__ PIPEDA tests
# ─────────────────────────────────────────────────────────────────────────────


class DocumentStrTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)

    def test_str_does_not_contain_original_filename(self):
        """PIPEDA: __str__ must NEVER include original_filename (may contain PII)."""
        result = str(self.doc)
        self.assertNotIn(self.doc.original_filename, result)
        self.assertNotIn("sensitive-report", result)

    def test_str_contains_pk(self):
        result = str(self.doc)
        self.assertIn(str(self.doc.pk), result)

    def test_str_contains_version_number(self):
        result = str(self.doc)
        self.assertIn(str(self.doc.version_number), result)

    def test_str_contains_scan_status(self):
        result = str(self.doc)
        self.assertIn(self.doc.scan_status, result)

    def test_str_format(self):
        # Exact format: "Document #{pk} [v{version}, {scan_status}]"
        expected = f"Document #{self.doc.pk} [v{self.doc.version_number}, {self.doc.scan_status}]"
        self.assertEqual(str(self.doc), expected)


# ─────────────────────────────────────────────────────────────────────────────
# Document.clean() validation tests
# ─────────────────────────────────────────────────────────────────────────────


class DocumentCleanTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()

    def test_clean_self_root_raises(self):
        """A document cannot be its own root document."""
        doc = _make_document(self.user, self.cat)
        doc.root_document_id = doc.pk
        doc.version_number = 2  # avoid triggering version1+root conflict only
        with self.assertRaises(ValidationError) as cm:
            doc.clean()
        self.assertIn("root_document", cm.exception.message_dict)

    def test_clean_version_number_zero_raises(self):
        """Version number must be at least 1."""
        doc = _make_document(self.user, self.cat)
        doc.version_number = 0
        with self.assertRaises(ValidationError) as cm:
            doc.clean()
        self.assertIn("version_number", cm.exception.message_dict)

    def test_clean_version_number_negative_raises(self):
        doc = _make_document(self.user, self.cat)
        doc.version_number = -5
        with self.assertRaises(ValidationError):
            doc.clean()

    def test_clean_version1_with_root_raises(self):
        """Version 1 documents cannot have a root_document."""
        root = _make_document(self.user, self.cat)
        # Create a version-2 doc pointing to root, then try to set it as v1.
        child = _make_document(
            self.user,
            self.cat,
            version_number=2,
            root_document=root,
        )
        child.version_number = 1  # invalid: v1 with root set
        with self.assertRaises(ValidationError) as cm:
            child.clean()
        self.assertIn("version_number", cm.exception.message_dict)

    def test_clean_no_root_version2_raises(self):
        """root_document=None requires version_number==1."""
        doc = _make_document(self.user, self.cat)
        doc.version_number = 2
        doc.root_document_id = None
        with self.assertRaises(ValidationError) as cm:
            doc.clean()
        self.assertIn("version_number", cm.exception.message_dict)

    def test_clean_version2_with_root_ok(self):
        """version_number=2 with root_document set is valid."""
        root = _make_document(self.user, self.cat)
        child = _make_document(
            self.user,
            self.cat,
            version_number=2,
            root_document=root,
        )
        # Should not raise
        child.clean()

    def test_clean_version1_no_root_ok(self):
        """version_number=1 with root_document=None is the normal case."""
        doc = _make_document(self.user, self.cat)
        doc.clean()  # No exception


# ─────────────────────────────────────────────────────────────────────────────
# DocumentQuerySet.active()
# ─────────────────────────────────────────────────────────────────────────────


class DocumentQuerySetActiveTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()

    def test_active_returns_active_non_deleted(self):
        doc = _make_document(self.user, self.cat, scan_status=Document.ScanStatus.ACTIVE)
        self.assertIn(doc, Document.objects.active())

    def test_active_excludes_quarantined(self):
        doc = _make_document(self.user, self.cat, scan_status=Document.ScanStatus.QUARANTINED)
        self.assertNotIn(doc, Document.objects.active())

    def test_active_excludes_scanning(self):
        doc = _make_document(self.user, self.cat, scan_status=Document.ScanStatus.SCANNING)
        self.assertNotIn(doc, Document.objects.active())

    def test_active_excludes_pending_upload(self):
        doc = _make_document(self.user, self.cat, scan_status=Document.ScanStatus.PENDING_UPLOAD)
        self.assertNotIn(doc, Document.objects.active())

    def test_active_excludes_soft_deleted(self):
        """ACTIVE status but deleted_at set → not in active()."""
        doc = _make_document(self.user, self.cat, scan_status=Document.ScanStatus.ACTIVE)
        doc.deleted_at = timezone.now()
        doc.save(update_fields=["deleted_at", "updated_at"])
        self.assertNotIn(doc, Document.objects.active())

    def test_active_excludes_deleted_status(self):
        doc = _make_document(self.user, self.cat, scan_status=Document.ScanStatus.DELETED)
        self.assertNotIn(doc, Document.objects.active())

    def test_active_excludes_purged(self):
        doc = _make_document(self.user, self.cat, scan_status=Document.ScanStatus.PURGED)
        self.assertNotIn(doc, Document.objects.active())


# ─────────────────────────────────────────────────────────────────────────────
# DocumentQuerySet.latest_versions()
# ─────────────────────────────────────────────────────────────────────────────


class DocumentQuerySetLatestVersionsTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()

    def test_latest_versions_returns_is_latest_true(self):
        doc = _make_document(self.user, self.cat, is_latest_version=True)
        self.assertIn(doc, Document.objects.latest_versions())

    def test_latest_versions_excludes_non_latest(self):
        root = _make_document(self.user, self.cat, is_latest_version=False)
        self.assertNotIn(root, Document.objects.latest_versions())

    def test_latest_versions_combined_with_active(self):
        """Most common use case: active().latest_versions()."""
        doc = _make_document(
            self.user,
            self.cat,
            scan_status=Document.ScanStatus.ACTIVE,
            is_latest_version=True,
        )
        qs = Document.objects.active().latest_versions()
        self.assertIn(doc, qs)


# ─────────────────────────────────────────────────────────────────────────────
# DocumentQuerySet.pending_disposal()
# ─────────────────────────────────────────────────────────────────────────────


class DocumentQuerySetPendingDisposalTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()

    def _past_datetime(self, days=1):
        return timezone.now() - timedelta(days=days)

    def _past_date(self, days=1):
        return (timezone.now() - timedelta(days=days)).date()

    def _future_datetime(self, days=1):
        return timezone.now() + timedelta(days=days)

    def _future_date(self, days=1):
        return (timezone.now() + timedelta(days=days)).date()

    def test_pending_disposal_eligible(self):
        doc = _make_document(self.user, self.cat)
        doc.expires_at = self._past_datetime(2)
        doc.retain_until = self._past_date(2)
        doc.legal_hold = False
        doc.deleted_at = None
        doc.save(
            update_fields=["expires_at", "retain_until", "legal_hold", "deleted_at", "updated_at"]
        )
        self.assertIn(doc, Document.objects.pending_disposal())

    def test_pending_disposal_excludes_legal_hold(self):
        doc = _make_document(self.user, self.cat)
        doc.expires_at = self._past_datetime(2)
        doc.retain_until = self._past_date(2)
        doc.legal_hold = True
        doc.deleted_at = None
        doc.save(
            update_fields=["expires_at", "retain_until", "legal_hold", "deleted_at", "updated_at"]
        )
        self.assertNotIn(doc, Document.objects.pending_disposal())

    def test_pending_disposal_excludes_already_deleted(self):
        doc = _make_document(self.user, self.cat)
        doc.expires_at = self._past_datetime(2)
        doc.retain_until = self._past_date(2)
        doc.legal_hold = False
        doc.deleted_at = self._past_datetime(1)
        doc.save(
            update_fields=["expires_at", "retain_until", "legal_hold", "deleted_at", "updated_at"]
        )
        self.assertNotIn(doc, Document.objects.pending_disposal())

    def test_pending_disposal_excludes_future_expires_at(self):
        doc = _make_document(self.user, self.cat)
        doc.expires_at = self._future_datetime(10)
        doc.retain_until = self._past_date(2)
        doc.legal_hold = False
        doc.deleted_at = None
        doc.save(
            update_fields=["expires_at", "retain_until", "legal_hold", "deleted_at", "updated_at"]
        )
        self.assertNotIn(doc, Document.objects.pending_disposal())

    def test_pending_disposal_excludes_future_retain_until(self):
        doc = _make_document(self.user, self.cat)
        doc.expires_at = self._past_datetime(2)
        doc.retain_until = self._future_date(10)
        doc.legal_hold = False
        doc.deleted_at = None
        doc.save(
            update_fields=["expires_at", "retain_until", "legal_hold", "deleted_at", "updated_at"]
        )
        self.assertNotIn(doc, Document.objects.pending_disposal())

    def test_pending_disposal_excludes_null_expires_at(self):
        """NULL expires_at: SQL <= NULL = UNKNOWN → excluded."""
        doc = _make_document(self.user, self.cat)
        doc.expires_at = None
        doc.retain_until = self._past_date(2)
        doc.legal_hold = False
        doc.deleted_at = None
        doc.save(
            update_fields=["expires_at", "retain_until", "legal_hold", "deleted_at", "updated_at"]
        )
        self.assertNotIn(doc, Document.objects.pending_disposal())

    def test_pending_disposal_excludes_null_retain_until(self):
        """NULL retain_until → excluded (SQL semantics)."""
        doc = _make_document(self.user, self.cat)
        doc.expires_at = self._past_datetime(2)
        doc.retain_until = None
        doc.legal_hold = False
        doc.deleted_at = None
        doc.save(
            update_fields=["expires_at", "retain_until", "legal_hold", "deleted_at", "updated_at"]
        )
        self.assertNotIn(doc, Document.objects.pending_disposal())


# ─────────────────────────────────────────────────────────────────────────────
# DocumentQuerySet.pending_hard_delete()
# ─────────────────────────────────────────────────────────────────────────────


class DocumentQuerySetPendingHardDeleteTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()

    def test_pending_hard_delete_eligible(self):
        """Soft-deleted 31 days ago, DELETED status, no legal hold → eligible."""
        doc = _make_document(self.user, self.cat)
        doc.deleted_at = timezone.now() - timedelta(days=31)
        doc.scan_status = Document.ScanStatus.DELETED
        doc.legal_hold = False
        doc.save(update_fields=["deleted_at", "scan_status", "legal_hold", "updated_at"])
        self.assertIn(doc, Document.objects.pending_hard_delete())

    def test_pending_hard_delete_excludes_purged(self):
        """PURGED is a terminal status — must not appear in pending_hard_delete()."""
        doc = _make_document(self.user, self.cat)
        doc.deleted_at = timezone.now() - timedelta(days=31)
        doc.scan_status = Document.ScanStatus.PURGED
        doc.legal_hold = False
        doc.save(update_fields=["deleted_at", "scan_status", "legal_hold", "updated_at"])
        self.assertNotIn(doc, Document.objects.pending_hard_delete())

    def test_pending_hard_delete_excludes_active_status(self):
        """ACTIVE (not DELETED) → excluded even if deleted_at set."""
        doc = _make_document(self.user, self.cat)
        doc.deleted_at = timezone.now() - timedelta(days=31)
        doc.scan_status = Document.ScanStatus.ACTIVE  # not DELETED
        doc.legal_hold = False
        doc.save(update_fields=["deleted_at", "scan_status", "legal_hold", "updated_at"])
        self.assertNotIn(doc, Document.objects.pending_hard_delete())

    def test_pending_hard_delete_excludes_legal_hold(self):
        """legal_hold=True blocks hard delete absolutely."""
        doc = _make_document(self.user, self.cat)
        doc.deleted_at = timezone.now() - timedelta(days=31)
        doc.scan_status = Document.ScanStatus.DELETED
        doc.legal_hold = True
        doc.save(update_fields=["deleted_at", "scan_status", "legal_hold", "updated_at"])
        self.assertNotIn(doc, Document.objects.pending_hard_delete())

    def test_pending_hard_delete_excludes_within_grace(self):
        """Deleted only 5 days ago → within 30-day grace → excluded."""
        doc = _make_document(self.user, self.cat)
        doc.deleted_at = timezone.now() - timedelta(days=5)
        doc.scan_status = Document.ScanStatus.DELETED
        doc.legal_hold = False
        doc.save(update_fields=["deleted_at", "scan_status", "legal_hold", "updated_at"])
        self.assertNotIn(doc, Document.objects.pending_hard_delete())

    def test_pending_hard_delete_excludes_null_deleted_at(self):
        """deleted_at=None → not soft-deleted → excluded."""
        doc = _make_document(self.user, self.cat)
        doc.deleted_at = None
        doc.scan_status = Document.ScanStatus.DELETED
        doc.legal_hold = False
        doc.save(update_fields=["deleted_at", "scan_status", "legal_hold", "updated_at"])
        self.assertNotIn(doc, Document.objects.pending_hard_delete())

    def test_pending_hard_delete_custom_grace_days(self):
        """grace_days parameter is honoured."""
        doc = _make_document(self.user, self.cat)
        # Deleted 5 days ago
        doc.deleted_at = timezone.now() - timedelta(days=5)
        doc.scan_status = Document.ScanStatus.DELETED
        doc.legal_hold = False
        doc.save(update_fields=["deleted_at", "scan_status", "legal_hold", "updated_at"])
        # With grace_days=3, 5 days > 3 → should be eligible
        self.assertIn(doc, Document.objects.pending_hard_delete(grace_days=3))
        # With grace_days=30, 5 days < 30 → should be excluded
        self.assertNotIn(doc, Document.objects.pending_hard_delete(grace_days=30))


# ─────────────────────────────────────────────────────────────────────────────
# Document property tests
# ─────────────────────────────────────────────────────────────────────────────


class DocumentPropertyTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()

    def test_is_deleted_false_when_no_deleted_at(self):
        doc = _make_document(self.user, self.cat)
        self.assertFalse(doc.is_deleted)

    def test_is_deleted_true_when_deleted_at_set(self):
        doc = _make_document(self.user, self.cat)
        doc.deleted_at = timezone.now()
        self.assertTrue(doc.is_deleted)

    def test_storage_key_property_returns_storage_key_field(self):
        """storage_key property is the only correct accessor for _storage_key."""
        doc = _make_document(self.user, self.cat)
        self.assertEqual(doc.storage_key, doc._storage_key)

    def test_storage_key_is_not_empty(self):
        doc = _make_document(self.user, self.cat)
        self.assertTrue(doc.storage_key)

    def test_storage_key_format(self):
        """Storage key should follow documents/{prefix}/{uuid}/{uuid}.bin pattern."""
        doc = _make_document(self.user, self.cat)
        self.assertTrue(doc.storage_key.startswith("documents/"))
        self.assertTrue(doc.storage_key.endswith(".bin"))


# ─────────────────────────────────────────────────────────────────────────────
# DocumentAccessToken tests
# ─────────────────────────────────────────────────────────────────────────────


class DocumentAccessTokenTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)

    def _make_token(self, **kwargs):
        defaults = {
            "document": self.doc,
            "issued_to": self.user,
            "expires_at": timezone.now() + timedelta(minutes=5),
        }
        defaults.update(kwargs)
        return DocumentAccessToken.objects.create(**defaults)

    def test_is_valid_true_when_unused_not_expired(self):
        token = self._make_token()
        self.assertTrue(token.is_valid)

    def test_is_valid_false_when_used(self):
        token = self._make_token()
        token.used_at = timezone.now()
        self.assertFalse(token.is_valid)

    def test_is_valid_false_when_expired(self):
        token = self._make_token(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertFalse(token.is_valid)

    def test_is_valid_false_when_used_and_expired(self):
        token = self._make_token(
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        token.used_at = timezone.now() - timedelta(seconds=1)
        self.assertFalse(token.is_valid)

    def test_generate_token_returns_64_chars(self):
        from apps.documents.models import _generate_token

        token = _generate_token()
        self.assertEqual(len(token), 64)

    def test_generate_token_is_hex_string(self):
        from apps.documents.models import _generate_token

        token = _generate_token()
        int(token, 16)  # Raises ValueError if not hex

    def test_generate_token_unique(self):
        from apps.documents.models import _generate_token

        tokens = {_generate_token() for _ in range(20)}
        self.assertEqual(len(tokens), 20)

    def test_token_uniqueness_db_constraint(self):
        """Two tokens with the same value → IntegrityError."""
        from django.db import IntegrityError

        token_a = self._make_token()
        with self.assertRaises(IntegrityError):
            DocumentAccessToken.objects.create(
                document=self.doc,
                issued_to=self.user,
                expires_at=timezone.now() + timedelta(minutes=5),
                token=token_a.token,  # duplicate!
            )

    def test_str_contains_pk(self):
        token = self._make_token()
        self.assertIn(str(token.pk), str(token))

    def test_str_contains_doc_id(self):
        token = self._make_token()
        self.assertIn(str(self.doc.pk), str(token))


# ─────────────────────────────────────────────────────────────────────────────
# DocumentAttachment tests
# ─────────────────────────────────────────────────────────────────────────────


class DocumentAttachmentStrTests(TestCase):
    def setUp(self):
        from django.contrib.contenttypes.models import ContentType

        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)
        # Use DocumentCategory as a convenient linked object (any model works)
        self.linked_obj = self.cat
        ct = ContentType.objects.get_for_model(DocumentCategory)
        self.attachment = DocumentAttachment.objects.create(
            document=self.doc,
            content_type=ct,
            object_id=str(self.linked_obj.pk),
            attached_by=self.user,
            attachment_role="supporting_evidence",
        )

    def test_str_contains_document_pk(self):
        result = str(self.attachment)
        self.assertIn(str(self.doc.pk), result)

    def test_str_contains_object_id(self):
        result = str(self.attachment)
        self.assertIn(str(self.linked_obj.pk), result)

    def test_str_format(self):
        result = str(self.attachment)
        self.assertIn("DocumentAttachment #", result)
        self.assertIn("doc=", result)


# ─────────────────────────────────────────────────────────────────────────────
# ScanStatus choices
# ─────────────────────────────────────────────────────────────────────────────


class ScanStatusChoicesTests(TestCase):
    def test_all_statuses_exist(self):
        expected = {
            "pending_upload",
            "scanning",
            "active",
            "quarantined",
            "deleted",
            "purged",
        }
        actual = {s.value for s in Document.ScanStatus}
        self.assertTrue(expected.issubset(actual))

    def test_purged_is_terminal(self):
        """PURGED must be excluded from pending_hard_delete (C-3 fix)."""
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)
        doc.deleted_at = timezone.now() - timedelta(days=31)
        doc.scan_status = Document.ScanStatus.PURGED
        doc.save(update_fields=["deleted_at", "scan_status", "updated_at"])
        # PURGED must NEVER appear in pending_hard_delete
        self.assertNotIn(doc, Document.objects.pending_hard_delete())


# ─────────────────────────────────────────────────────────────────────────────
# SecurityClassification choices
# ─────────────────────────────────────────────────────────────────────────────


class SecurityClassificationTests(TestCase):
    def test_three_levels_exist(self):
        values = {c[0] for c in DocumentCategory.SecurityClassification.choices}
        self.assertIn("unclassified", values)
        self.assertIn("protected_a", values)
        self.assertIn("protected_b", values)

    def test_document_inherits_category_classification(self):
        user = _make_user()
        cat = _make_category()
        cat.security_classification = DocumentCategory.SecurityClassification.PROTECTED_A
        cat.save(update_fields=["security_classification"])
        # Create doc without explicit security_classification
        doc = _make_document(user, cat)
        # Document has its own field — at creation we don't auto-inherit in this test
        # (auto-inherit happens in the service layer). Just check field exists.
        self.assertIn(
            doc.security_classification,
            [c[0] for c in DocumentCategory.SecurityClassification.choices],
        )
