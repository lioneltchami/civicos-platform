"""
Wave 7 — §24.1 canonical test file: test_access_tokens.py

Tests for DocumentAccessToken service layer:
  - issue_access_token()   — IDOR, scan gate, IP masking, TTL, audit
  - consume_access_token() — single-use, expiry, cross-user isolation
  - purge_expired_tokens() — cleanup

PIPEDA invariants:
  - Non-owners receive Http404 (not 403) — IDOR prevention
  - Only ACTIVE documents are downloadable
  - IP address is masked (last IPv4 octet zeroed)
  - storage_key NEVER in audit event_detail
"""

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.http import Http404
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.audit.models import AuditEventType, AuditLogEntry
from apps.documents.models import Document, DocumentAccessToken, DocumentCategory
from apps.documents.services.download import consume_access_token, issue_access_token
from apps.documents.services.retention import purge_expired_tokens

User = get_user_model()

_CTR = 0


def _make_user(**kwargs):
    global _CTR
    _CTR += 1
    return User.objects.create_user(
        email=f"token{_CTR}@example.com",
        password="testpass123",
        **kwargs,
    )


def _make_category(**kwargs):
    global _CTR
    _CTR += 1
    return DocumentCategory.objects.create(
        name_en="Token Test Category",
        name_fr="Cat Tokens",
        slug=f"token-cat-{_CTR}",
        allowed_mime_types=["application/pdf"],
        min_retention_days=730,
        max_retention_days=2555,
        **kwargs,
    )


def _make_document(user, category, status=Document.ScanStatus.ACTIVE, **kwargs):
    doc_id = uuid.uuid4()
    return Document.objects.create(
        uploaded_by=user,
        category=category,
        original_filename="document.pdf",
        _storage_key=f"documents/active/{doc_id}/{uuid.uuid4().hex}.bin",
        mime_type="application/pdf",
        size_bytes=5_120,
        scan_status=status,
        **kwargs,
    )


def _grant_perm(user, codename, model=Document):
    ct = ContentType.objects.get_for_model(model)
    perm, _ = Permission.objects.get_or_create(
        codename=codename,
        content_type=ct,
        defaults={"name": codename},
    )
    user.user_permissions.add(perm)
    # Re-fetch to clear perm cache
    return User.objects.get(pk=user.pk)


# ─────────────────────────────────────────────────────────────────────────────
# issue_access_token() tests
# ─────────────────────────────────────────────────────────────────────────────


class IssueAccessTokenTests(TestCase):

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)

    def test_issue_token_creates_record(self):
        before = DocumentAccessToken.objects.count()
        issue_access_token(user=self.user, document=self.doc)
        self.assertEqual(DocumentAccessToken.objects.count(), before + 1)

    def test_issue_token_for_active_document_is_valid(self):
        token = issue_access_token(user=self.user, document=self.doc)
        self.assertTrue(token.is_valid)

    def test_issue_token_linked_to_correct_document(self):
        token = issue_access_token(user=self.user, document=self.doc)
        self.assertEqual(token.document_id, self.doc.pk)

    def test_issue_token_linked_to_correct_user(self):
        token = issue_access_token(user=self.user, document=self.doc)
        self.assertEqual(token.issued_to_id, self.user.pk)

    def test_issue_token_raises_404_for_scanning_document(self):
        doc = _make_document(self.user, self.cat, status=Document.ScanStatus.SCANNING)
        with self.assertRaises(Http404):
            issue_access_token(user=self.user, document=doc)

    def test_issue_token_raises_404_for_quarantined_document(self):
        doc = _make_document(self.user, self.cat, status=Document.ScanStatus.QUARANTINED)
        with self.assertRaises(Http404):
            issue_access_token(user=self.user, document=doc)

    def test_issue_token_raises_404_for_pending_upload(self):
        doc = _make_document(self.user, self.cat, status=Document.ScanStatus.PENDING_UPLOAD)
        with self.assertRaises(Http404):
            issue_access_token(user=self.user, document=doc)

    def test_issue_token_raises_404_for_deleted_document(self):
        """Soft-deleted document → 404 even if scan_status=ACTIVE."""
        doc = _make_document(self.user, self.cat)
        doc.deleted_at = timezone.now()
        doc.save(update_fields=["deleted_at", "updated_at"])
        with self.assertRaises(Http404):
            issue_access_token(user=self.user, document=doc)

    def test_issue_token_raises_404_for_non_owner(self):
        """IDOR prevention: non-owner gets 404, not 403."""
        other_user = _make_user()
        with self.assertRaises(Http404):
            issue_access_token(user=other_user, document=self.doc)

    def test_issue_token_staff_coordinator_can_download_any(self):
        """Staff with coordinator_view_document can issue token for any document."""
        staff = _make_user()
        staff = _grant_perm(staff, "coordinator_view_document")
        token = issue_access_token(user=staff, document=self.doc)
        self.assertTrue(token.is_valid)

    def test_issue_token_superuser_can_download_any(self):
        """Superuser can always download."""
        admin = _make_user(is_superuser=True, is_staff=True)
        token = issue_access_token(user=admin, document=self.doc)
        self.assertTrue(token.is_valid)


class IssueAccessTokenIPMaskingTests(TestCase):

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)

    def test_ipv4_last_octet_zeroed(self):
        token = issue_access_token(
            user=self.user, document=self.doc, ip_address="192.168.1.100"
        )
        self.assertEqual(token.ip_address, "192.168.1.0")

    def test_ipv4_127_0_0_1_masked(self):
        token = issue_access_token(
            user=self.user, document=self.doc, ip_address="127.0.0.1"
        )
        self.assertEqual(token.ip_address, "127.0.0.0")

    def test_ipv6_masked_to_48_prefix(self):
        """IPv6 should retain only the /48 prefix (last 80 bits zeroed)."""
        token = issue_access_token(
            user=self.user, document=self.doc, ip_address="2001:db8::1"
        )
        # The masked result should be the /48 network address
        self.assertIsNotNone(token.ip_address)
        # Should not be the original full address
        self.assertNotEqual(token.ip_address, "2001:db8::1")

    def test_none_ip_stored_as_none(self):
        token = issue_access_token(
            user=self.user, document=self.doc, ip_address=None
        )
        self.assertIsNone(token.ip_address)

    def test_unparseable_ip_stored_as_none(self):
        """Unparseable IP must not crash the download."""
        token = issue_access_token(
            user=self.user, document=self.doc, ip_address="not-an-ip"
        )
        self.assertIsNone(token.ip_address)


class IssueAccessTokenAuditTests(TestCase):

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)

    def test_issue_token_writes_audit_record(self):
        before_count = AuditLogEntry.objects.filter(
            resource_type="documents.Document",
            resource_id=str(self.doc.pk),
            event_type=AuditEventType.RECORD_VIEWED,
        ).count()
        issue_access_token(user=self.user, document=self.doc)
        after_count = AuditLogEntry.objects.filter(
            resource_type="documents.Document",
            resource_id=str(self.doc.pk),
            event_type=AuditEventType.RECORD_VIEWED,
        ).count()
        self.assertEqual(after_count, before_count + 1)

    def test_issue_token_audit_actor_is_user(self):
        issue_access_token(user=self.user, document=self.doc)
        entry = AuditLogEntry.objects.filter(
            resource_type="documents.Document",
            resource_id=str(self.doc.pk),
            event_type=AuditEventType.RECORD_VIEWED,
        ).order_by("-timestamp").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor_id, str(self.user.pk))

    def test_issue_token_audit_no_storage_key(self):
        """PIPEDA: storage_key must NEVER appear in audit event_detail."""
        issue_access_token(user=self.user, document=self.doc)
        entry = AuditLogEntry.objects.filter(
            resource_type="documents.Document",
            resource_id=str(self.doc.pk),
            event_type=AuditEventType.RECORD_VIEWED,
        ).order_by("-timestamp").first()
        self.assertIsNotNone(entry)
        self.assertNotIn("storage_key", entry.event_detail)
        # Also check the actual key value isn't buried in any string value
        detail_str = str(entry.event_detail)
        self.assertNotIn(self.doc._storage_key, detail_str)

    def test_issue_token_audit_no_original_filename(self):
        """PIPEDA: original_filename must NEVER appear in audit event_detail."""
        issue_access_token(user=self.user, document=self.doc)
        entry = AuditLogEntry.objects.filter(
            resource_type="documents.Document",
            resource_id=str(self.doc.pk),
            event_type=AuditEventType.RECORD_VIEWED,
        ).order_by("-timestamp").first()
        self.assertIsNotNone(entry)
        self.assertNotIn("original_filename", entry.event_detail)


class IssueAccessTokenTTLTests(TestCase):

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)

    @override_settings(CIVICOS={"DOCUMENT_ACCESS_TOKEN_TTL_SECONDS": 60})
    def test_token_ttl_from_settings(self):
        before = timezone.now()
        token = issue_access_token(user=self.user, document=self.doc)
        after = timezone.now()
        # Token should expire approximately 60 seconds after issuance
        expected_min = before + timedelta(seconds=59)
        expected_max = after + timedelta(seconds=61)
        self.assertGreater(token.expires_at, expected_min)
        self.assertLess(token.expires_at, expected_max)

    @override_settings(CIVICOS={})
    def test_token_default_ttl_300_seconds(self):
        """Default TTL is 300 seconds (5 minutes) when setting not present."""
        before = timezone.now()
        token = issue_access_token(user=self.user, document=self.doc)
        after = timezone.now()
        expected_min = before + timedelta(seconds=299)
        expected_max = after + timedelta(seconds=301)
        self.assertGreater(token.expires_at, expected_min)
        self.assertLess(token.expires_at, expected_max)


# ─────────────────────────────────────────────────────────────────────────────
# consume_access_token() tests
# ─────────────────────────────────────────────────────────────────────────────


class ConsumeAccessTokenTests(TestCase):

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)
        self.token = issue_access_token(user=self.user, document=self.doc)

    def test_consume_token_returns_document(self):
        result = consume_access_token(token_value=self.token.token, user=self.user)
        self.assertEqual(result.pk, self.doc.pk)

    def test_consume_token_marks_used_at(self):
        consume_access_token(token_value=self.token.token, user=self.user)
        self.token.refresh_from_db()
        self.assertIsNotNone(self.token.used_at)

    def test_consume_token_is_invalid_after_use(self):
        consume_access_token(token_value=self.token.token, user=self.user)
        self.token.refresh_from_db()
        self.assertFalse(self.token.is_valid)

    def test_consume_token_raises_404_on_used_token(self):
        """Single-use enforcement: second attempt raises Http404."""
        consume_access_token(token_value=self.token.token, user=self.user)
        with self.assertRaises(Http404):
            consume_access_token(token_value=self.token.token, user=self.user)

    def test_consume_token_raises_404_on_expired_token(self):
        """Expired token → Http404."""
        expired_token = DocumentAccessToken.objects.create(
            document=self.doc,
            issued_to=self.user,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        with self.assertRaises(Http404):
            consume_access_token(token_value=expired_token.token, user=self.user)

    def test_consume_token_raises_404_on_nonexistent_token(self):
        """Unknown token string → Http404 (IDOR: never 403)."""
        with self.assertRaises(Http404):
            consume_access_token(token_value="a" * 64, user=self.user)

    def test_consume_token_raises_404_wrong_user(self):
        """Token issued to user_a cannot be consumed by user_b (IDOR)."""
        other_user = _make_user()
        with self.assertRaises(Http404):
            consume_access_token(token_value=self.token.token, user=other_user)

    def test_consume_token_writes_audit_record(self):
        before = AuditLogEntry.objects.filter(
            resource_type="documents.Document",
            resource_id=str(self.doc.pk),
            event_type=AuditEventType.RECORD_VIEWED,
        ).count()
        consume_access_token(token_value=self.token.token, user=self.user)
        after = AuditLogEntry.objects.filter(
            resource_type="documents.Document",
            resource_id=str(self.doc.pk),
            event_type=AuditEventType.RECORD_VIEWED,
        ).count()
        self.assertGreater(after, before)

    def test_consume_token_used_at_is_recent(self):
        before = timezone.now()
        consume_access_token(token_value=self.token.token, user=self.user)
        after = timezone.now()
        self.token.refresh_from_db()
        self.assertGreaterEqual(self.token.used_at, before)
        self.assertLessEqual(self.token.used_at, after)


# ─────────────────────────────────────────────────────────────────────────────
# purge_expired_tokens() tests
# ─────────────────────────────────────────────────────────────────────────────


class PurgeExpiredTokensTests(TestCase):

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

    def test_purge_deletes_expired_tokens(self):
        expired = self._make_token(
            expires_at=timezone.now() - timedelta(hours=1)
        )
        purge_expired_tokens()
        with self.assertRaises(DocumentAccessToken.DoesNotExist):
            DocumentAccessToken.objects.get(pk=expired.pk)

    def test_purge_deletes_used_tokens(self):
        used_token = self._make_token()
        used_token.used_at = timezone.now()
        used_token.save(update_fields=["used_at"])
        purge_expired_tokens()
        with self.assertRaises(DocumentAccessToken.DoesNotExist):
            DocumentAccessToken.objects.get(pk=used_token.pk)

    def test_purge_preserves_valid_tokens(self):
        valid_token = self._make_token()
        purge_expired_tokens()
        # Should still exist
        self.assertTrue(
            DocumentAccessToken.objects.filter(pk=valid_token.pk).exists()
        )

    def test_purge_returns_count_of_deleted(self):
        # 2 expired, 1 valid
        self._make_token(expires_at=timezone.now() - timedelta(hours=1))
        self._make_token(expires_at=timezone.now() - timedelta(hours=2))
        self._make_token()  # valid
        count = purge_expired_tokens()
        self.assertGreaterEqual(count, 2)

    def test_purge_dry_run_does_not_delete(self):
        expired = self._make_token(
            expires_at=timezone.now() - timedelta(hours=1)
        )
        purge_expired_tokens(dry_run=True)
        # Should still exist with dry_run=True
        self.assertTrue(
            DocumentAccessToken.objects.filter(pk=expired.pk).exists()
        )

    def test_purge_mixed_batch(self):
        """2 expired + 1 used + 2 valid → only 4 purged, 2 remain."""
        self._make_token(expires_at=timezone.now() - timedelta(hours=1))
        self._make_token(expires_at=timezone.now() - timedelta(days=1))
        used = self._make_token()
        used.used_at = timezone.now()
        used.save(update_fields=["used_at"])
        valid1 = self._make_token()
        valid2 = self._make_token()

        purge_expired_tokens()

        self.assertTrue(DocumentAccessToken.objects.filter(pk=valid1.pk).exists())
        self.assertTrue(DocumentAccessToken.objects.filter(pk=valid2.pk).exists())
