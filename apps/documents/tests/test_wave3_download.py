"""
Wave 3 download service test suite — apps.documents.services.download.

Coverage:
  - IssueAccessTokenTests:      ACTIVE gate, soft-delete gate, IDOR, permission hierarchy,
                                  token fields, TTL from settings, audit log
  - ConsumeAccessTokenTests:    valid path, cross-user IDOR, expiry, already-used, not-found,
                                  single-use enforcement, audit log, returned Document
  - MaskIpTests:                IPv4 octet masking, IPv6 /48 masking, None passthrough,
                                  invalid IP returns None
  - UserMayDownloadTests:       superuser, staff with view_document perm, citizen owner,
                                  citizen non-owner, anonymous
  - PurgeExpiredTokensTests:    deletes expired, deletes used, keeps valid, dry_run counts,
                                  run_purge_expired_tokens task wrapper

PIPEDA invariants verified in every relevant test:
  - original_filename NEVER in audit event_detail
  - storage_key NEVER in audit event_detail or signal kwargs
  - IDOR: Http404 (not 403 or 200) for non-owned resources

Settings:
  - CIVICOS_DOWNLOAD overrides DOCUMENT_ACCESS_TOKEN_TTL_SECONDS.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import ANY, MagicMock, patch

from django.contrib.auth import get_user_model
from django.http import Http404
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.documents.models import Document, DocumentAccessToken, DocumentCategory
from apps.documents.services.download import (
    _mask_ip,
    _user_may_download,
    consume_access_token,
    issue_access_token,
)
from apps.documents.services.retention import purge_expired_tokens
from apps.documents.services.upload import _make_storage_key
from apps.documents.tasks import run_purge_expired_tokens

User = get_user_model()

# ─────────────────────────────────────────────────────────────────────────────
# Shared CIVICOS fixtures
# ─────────────────────────────────────────────────────────────────────────────

CIVICOS_DOWNLOAD = {
    "CLAMAV_HOST": "",
    "CLAMAV_REQUIRED": False,
    "ALLOWED_UPLOAD_MIME_TYPES": ["application/pdf"],
    "DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES": 10 * 1024 * 1024,
    "DOCUMENT_MAX_STAFF_UPLOAD_BYTES": 50 * 1024 * 1024,
    "DOCUMENT_PRESIGNED_POST_TTL_SECONDS": 900,
    "DOCUMENT_ACCESS_TOKEN_TTL_SECONDS": 300,
    "DOCUMENT_ZIP_MAX_ENTRIES": 1000,
    "DOCUMENT_ZIP_MAX_RATIO": 100,
    "MAGIC_BYTES_REQUIRED": False,
}

# ─────────────────────────────────────────────────────────────────────────────
# Test helpers / factories
# ─────────────────────────────────────────────────────────────────────────────


def make_user(**kwargs) -> User:
    uid = uuid.uuid4().hex[:8]
    defaults = {
        "email": f"dl-{uid}@example.com",
        "password": "hunter2",
    }
    defaults.update(kwargs)
    return User.objects.create_user(**defaults)


def make_staff_user() -> User:
    uid = uuid.uuid4().hex[:8]
    u = User.objects.create_user(
        email=f"staff-{uid}@example.com",
        password="hunter2",
    )
    u.is_staff = True
    u.save(update_fields=["is_staff"])
    return u


def make_superuser() -> User:
    uid = uuid.uuid4().hex[:8]
    return User.objects.create_superuser(
        email=f"su-{uid}@example.com",
        password="hunter2",
    )


def make_category(**kwargs) -> DocumentCategory:
    defaults = {
        "name_en": "Download Test",
        "name_fr": "Test de téléchargement",
        "slug": f"dl-cat-{uuid.uuid4().hex[:6]}",
        "security_classification": DocumentCategory.SecurityClassification.PROTECTED_B,
        "allowed_mime_types": ["application/pdf"],
        "max_size_bytes": 0,
        "min_retention_days": 730,
        "max_retention_days": 2555,
        "is_transitory": False,
    }
    defaults.update(kwargs)
    return DocumentCategory.objects.create(**defaults)


def make_document(
    user: User,
    category: DocumentCategory,
    scan_status: str = Document.ScanStatus.ACTIVE,
    deleted_at=None,
) -> Document:
    doc = Document.objects.create(
        category=category,
        uploaded_by=user,
        original_filename="report.pdf",
        _storage_key=_make_storage_key(str(uuid.uuid4()), prefix="active"),
        mime_type="application/pdf",
        size_bytes=2048,
        scan_status=scan_status,
        security_classification=DocumentCategory.SecurityClassification.PROTECTED_B,
    )
    if deleted_at is not None:
        Document.objects.filter(pk=doc.pk).update(deleted_at=deleted_at)
        doc.refresh_from_db()
    return doc


def make_active_token(
    document: Document,
    user: User,
    *,
    ttl_seconds: int = 300,
    used: bool = False,
    expired: bool = False,
) -> DocumentAccessToken:
    """Create a DocumentAccessToken in the desired state."""
    if expired:
        expires_at = timezone.now() - timedelta(seconds=1)
    else:
        expires_at = timezone.now() + timedelta(seconds=ttl_seconds)

    token = DocumentAccessToken.objects.create(
        document=document,
        issued_to=user,
        expires_at=expires_at,
    )
    if used:
        DocumentAccessToken.objects.filter(pk=token.pk).update(
            used_at=timezone.now()
        )
        token.refresh_from_db()
    return token


# ─────────────────────────────────────────────────────────────────────────────
# 1. MaskIpTests — _mask_ip()
# ─────────────────────────────────────────────────────────────────────────────


class MaskIpTests(TestCase):
    """Unit tests for _mask_ip: privacy-compliant IP address masking."""

    # ── None passthrough ──────────────────────────────────────────────────────

    def test_none_returns_none(self):
        self.assertIsNone(_mask_ip(None))

    # ── IPv4 masking ──────────────────────────────────────────────────────────

    def test_ipv4_last_octet_zeroed(self):
        """Last octet of IPv4 must be zeroed: 192.168.1.100 → 192.168.1.0."""
        self.assertEqual(_mask_ip("192.168.1.100"), "192.168.1.0")

    def test_ipv4_last_octet_zeroed_already_zero(self):
        """Idempotent: already-zero last octet remains 0."""
        self.assertEqual(_mask_ip("10.0.0.0"), "10.0.0.0")

    def test_ipv4_loopback_masked(self):
        self.assertEqual(_mask_ip("127.0.0.1"), "127.0.0.0")

    def test_ipv4_public_address(self):
        self.assertEqual(_mask_ip("203.0.113.42"), "203.0.113.0")

    def test_ipv4_max_last_octet(self):
        """255 → 0."""
        self.assertEqual(_mask_ip("8.8.8.255"), "8.8.8.0")

    # ── IPv6 masking ──────────────────────────────────────────────────────────

    def test_ipv6_retains_48_prefix(self):
        """
        IPv6 /48 masking must zero the last 80 bits, retaining the /48 prefix.
        2001:db8:85a3::8a2e:370:7334 → 2001:db8:85a3::
        """
        result = _mask_ip("2001:db8:85a3::8a2e:370:7334")
        self.assertEqual(result, "2001:db8:85a3::")

    def test_ipv6_loopback_masked(self):
        """::1 masked to /48 → :: (all zeros)."""
        result = _mask_ip("::1")
        self.assertEqual(result, "::")

    def test_ipv6_link_local_masked(self):
        """fe80::1%eth0 without zone → fe80::."""
        result = _mask_ip("fe80::1")
        # fe80:: with /48 → first 48 bits of fe80::1 are fe80:0000:0000
        self.assertEqual(result, "fe80::")

    def test_ipv6_full_address_retains_prefix(self):
        """2001:db8:1234:abcd:ef01:2345:6789:abcd → 2001:db8:1234::"""
        result = _mask_ip("2001:db8:1234:abcd:ef01:2345:6789:abcd")
        self.assertEqual(result, "2001:db8:1234::")

    # ── Invalid input ─────────────────────────────────────────────────────────

    def test_invalid_ip_returns_none(self):
        """Unparseable IP returns None instead of raising."""
        self.assertIsNone(_mask_ip("not-an-ip"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_mask_ip(""))

    def test_garbage_returns_none(self):
        self.assertIsNone(_mask_ip("'; DROP TABLE users; --"))


# ─────────────────────────────────────────────────────────────────────────────
# 2. UserMayDownloadTests — _user_may_download()
# ─────────────────────────────────────────────────────────────────────────────


class UserMayDownloadTests(TestCase):
    """Unit tests for _user_may_download permission helper."""

    def setUp(self):
        self.category = make_category()
        self.owner = make_user()
        self.stranger = make_user()
        self.doc = make_document(self.owner, self.category)

    def test_superuser_may_download_any_document(self):
        su = make_superuser()
        self.assertTrue(_user_may_download(user=su, document=self.doc))

    def test_superuser_may_download_other_users_document(self):
        """Superuser access is not scoped to the uploader."""
        su = make_superuser()
        self.assertTrue(_user_may_download(user=su, document=self.doc))

    def test_staff_with_view_document_perm_may_download(self):
        """Staff with documents.coordinator_view_document may download any document."""
        from django.contrib.auth.models import Permission

        staff = make_user()
        perm = Permission.objects.get(
            content_type__app_label="documents",
            codename="coordinator_view_document",
        )
        staff.user_permissions.add(perm)
        # Refresh permission cache
        staff = User.objects.get(pk=staff.pk)
        self.assertTrue(_user_may_download(user=staff, document=self.doc))

    def test_citizen_owner_may_download_own_document(self):
        """Citizen who uploaded the document may download it."""
        self.assertTrue(_user_may_download(user=self.owner, document=self.doc))

    def test_citizen_stranger_may_not_download_other_document(self):
        """Citizen cannot download a document uploaded by another citizen (IDOR)."""
        self.assertFalse(_user_may_download(user=self.stranger, document=self.doc))

    def test_anonymous_user_may_not_download(self):
        """Unauthenticated user returns False."""
        from django.contrib.auth.models import AnonymousUser

        anon = AnonymousUser()
        self.assertFalse(_user_may_download(user=anon, document=self.doc))


# ─────────────────────────────────────────────────────────────────────────────
# 3. IssueAccessTokenTests — issue_access_token()
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=CIVICOS_DOWNLOAD)
class IssueAccessTokenTests(TestCase):
    """Tests for issue_access_token(): the token creation gate."""

    def setUp(self):
        self.category = make_category()
        self.user = make_user()
        self.doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.ACTIVE)

    # ── Happy path ────────────────────────────────────────────────────────────

    def test_returns_document_access_token(self):
        token = issue_access_token(user=self.user, document=self.doc)
        self.assertIsInstance(token, DocumentAccessToken)

    def test_token_linked_to_correct_document(self):
        token = issue_access_token(user=self.user, document=self.doc)
        self.assertEqual(token.document_id, self.doc.pk)

    def test_token_linked_to_correct_user(self):
        token = issue_access_token(user=self.user, document=self.doc)
        self.assertEqual(token.issued_to_id, self.user.pk)

    def test_token_is_initially_valid(self):
        """Freshly issued token must have used_at=None and expires_at in the future."""
        token = issue_access_token(user=self.user, document=self.doc)
        self.assertIsNone(token.used_at)
        self.assertGreater(token.expires_at, timezone.now())

    def test_token_ttl_from_civicos_setting(self):
        """TTL is read from CIVICOS['DOCUMENT_ACCESS_TOKEN_TTL_SECONDS']."""
        token = issue_access_token(user=self.user, document=self.doc)
        expected_expires = timezone.now() + timedelta(seconds=300)
        # Allow 5s clock slack
        delta = abs((token.expires_at - expected_expires).total_seconds())
        self.assertLess(delta, 5)

    def test_token_ttl_custom_setting(self):
        """Custom TTL (60s) is honoured."""
        civicos = {**CIVICOS_DOWNLOAD, "DOCUMENT_ACCESS_TOKEN_TTL_SECONDS": 60}
        with override_settings(CIVICOS=civicos):
            token = issue_access_token(user=self.user, document=self.doc)
        expected_expires = timezone.now() + timedelta(seconds=60)
        delta = abs((token.expires_at - expected_expires).total_seconds())
        self.assertLess(delta, 5)

    def test_token_is_64_hex_chars(self):
        """Token value is a 64-character hexadecimal string."""
        token = issue_access_token(user=self.user, document=self.doc)
        self.assertEqual(len(token.token), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in token.token))

    def test_ip_address_is_masked(self):
        """IPv4 address is masked (last octet zeroed) in the stored token."""
        token = issue_access_token(
            user=self.user, document=self.doc, ip_address="203.0.113.42"
        )
        self.assertEqual(token.ip_address, "203.0.113.0")

    def test_ip_address_none_stored_as_none(self):
        """No IP provided → stored as None."""
        token = issue_access_token(user=self.user, document=self.doc, ip_address=None)
        self.assertIsNone(token.ip_address)

    # ── ACTIVE-only gate ──────────────────────────────────────────────────────

    def test_pending_upload_raises_404(self):
        doc = make_document(
            self.user, self.category,
            scan_status=Document.ScanStatus.PENDING_UPLOAD,
        )
        with self.assertRaises(Http404):
            issue_access_token(user=self.user, document=doc)

    def test_scanning_raises_404(self):
        doc = make_document(
            self.user, self.category,
            scan_status=Document.ScanStatus.SCANNING,
        )
        with self.assertRaises(Http404):
            issue_access_token(user=self.user, document=doc)

    def test_quarantined_raises_404(self):
        doc = make_document(
            self.user, self.category,
            scan_status=Document.ScanStatus.QUARANTINED,
        )
        with self.assertRaises(Http404):
            issue_access_token(user=self.user, document=doc)

    def test_deleted_scan_status_raises_404(self):
        doc = make_document(
            self.user, self.category,
            scan_status=Document.ScanStatus.DELETED,
        )
        with self.assertRaises(Http404):
            issue_access_token(user=self.user, document=doc)

    # ── Soft-delete gate ──────────────────────────────────────────────────────

    def test_soft_deleted_active_doc_raises_404(self):
        """
        ACTIVE doc with deleted_at set must return 404.
        PIPEDA: soft-deleted documents are inaccessible regardless of scan status.
        """
        doc = make_document(
            self.user, self.category,
            scan_status=Document.ScanStatus.ACTIVE,
            deleted_at=timezone.now(),
        )
        with self.assertRaises(Http404):
            issue_access_token(user=self.user, document=doc)

    # ── IDOR / ownership gate ─────────────────────────────────────────────────

    def test_different_citizen_raises_404_not_403(self):
        """
        IDOR: a citizen requesting a token for another user's document must
        receive Http404, NOT Http403 (which would confirm document existence).
        """
        stranger = make_user()
        with self.assertRaises(Http404):
            issue_access_token(user=stranger, document=self.doc)

    def test_superuser_may_issue_token(self):
        """Superuser can issue a token for any document."""
        su = make_superuser()
        token = issue_access_token(user=su, document=self.doc)
        self.assertIsInstance(token, DocumentAccessToken)

    def test_staff_with_view_perm_may_issue_token(self):
        """Staff with documents.coordinator_view_document may issue tokens for any document."""
        from django.contrib.auth.models import Permission

        staff = make_user()
        perm = Permission.objects.get(
            content_type__app_label="documents",
            codename="coordinator_view_document",
        )
        staff.user_permissions.add(perm)
        staff = User.objects.get(pk=staff.pk)
        token = issue_access_token(user=staff, document=self.doc)
        self.assertIsInstance(token, DocumentAccessToken)

    # ── Audit log ─────────────────────────────────────────────────────────────

    def test_audit_event_written_on_token_issuance(self):
        """An audit log entry must be written when a token is issued."""
        from apps.audit.models import AuditLogEntry, AuditEventType

        before = timezone.now()
        issue_access_token(user=self.user, document=self.doc)
        entry = AuditLogEntry.objects.filter(
            actor_id=str(self.user.pk),
            resource_type="documents.Document",
            resource_id=str(self.doc.pk),
        ).order_by("-timestamp").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.event_type, AuditEventType.RECORD_VIEWED)

    def test_audit_event_detail_pipeda_no_original_filename(self):
        """PIPEDA: original_filename must NOT appear in audit event_detail."""
        from apps.audit.models import AuditLogEntry

        issue_access_token(user=self.user, document=self.doc)
        entry = AuditLogEntry.objects.filter(
            resource_id=str(self.doc.pk),
        ).order_by("-timestamp").first()
        self.assertIsNotNone(entry)
        detail_str = str(entry.event_detail)
        self.assertNotIn("report.pdf", detail_str)
        self.assertNotIn("original_filename", detail_str)

    def test_audit_event_detail_pipeda_no_storage_key(self):
        """PIPEDA: storage_key must NOT appear in audit event_detail."""
        from apps.audit.models import AuditLogEntry

        issue_access_token(user=self.user, document=self.doc)
        entry = AuditLogEntry.objects.filter(
            resource_id=str(self.doc.pk),
        ).order_by("-timestamp").first()
        self.assertIsNotNone(entry)
        detail_str = str(entry.event_detail)
        self.assertNotIn("storage_key", detail_str)
        self.assertNotIn("documents/active/", detail_str)
        self.assertNotIn("documents/quarantine/", detail_str)

    def test_audit_event_detail_contains_token_pk(self):
        """Audit event_detail must record the token PK for traceability."""
        from apps.audit.models import AuditLogEntry

        token = issue_access_token(user=self.user, document=self.doc)
        entry = AuditLogEntry.objects.filter(
            resource_id=str(self.doc.pk),
        ).order_by("-timestamp").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.event_detail.get("token_pk"), str(token.pk))

    def test_audit_event_detail_action_is_token_issued(self):
        """Audit event_detail must record action='token_issued'."""
        from apps.audit.models import AuditLogEntry

        issue_access_token(user=self.user, document=self.doc)
        entry = AuditLogEntry.objects.filter(
            resource_id=str(self.doc.pk),
        ).order_by("-timestamp").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.event_detail.get("action"), "token_issued")

    def test_audit_failure_does_not_prevent_token_issuance(self):
        """
        If the audit write raises, issue_access_token must still return
        a valid token (audit failure must never degrade citizen access).
        """
        with patch("apps.audit.services.record_event", side_effect=Exception("DB down")):
            token = issue_access_token(user=self.user, document=self.doc)
        self.assertIsInstance(token, DocumentAccessToken)
        self.assertTrue(token.is_valid)

    def test_legal_hold_document_is_downloadable(self):
        """Legal hold blocks disposal but must NOT block citizens from downloading."""
        doc = make_document(
            self.user,
            self.category,
            scan_status=Document.ScanStatus.ACTIVE,
        )
        doc.legal_hold = True
        doc.save(update_fields=["legal_hold"])
        token = issue_access_token(user=self.user, document=doc)
        self.assertIsNotNone(token)
        self.assertTrue(token.is_valid)


# ─────────────────────────────────────────────────────────────────────────────
# 4. ConsumeAccessTokenTests — consume_access_token()
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=CIVICOS_DOWNLOAD)
class ConsumeAccessTokenTests(TestCase):
    """Tests for consume_access_token(): token validation and single-use enforcement."""

    # NOTE: Concurrent select_for_update() enforcement cannot be tested under SQLite
    # because SQLite does not support true row-level locking. This invariant is
    # protected in production (PostgreSQL) by the select_for_update() in
    # consume_access_token(). See: https://docs.djangoproject.com/en/stable/ref/models/querysets/#select-for-update

    def setUp(self):
        self.category = make_category()
        self.user = make_user()
        self.doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.ACTIVE)

    # ── Happy path ────────────────────────────────────────────────────────────

    def test_valid_token_returns_document(self):
        """Redeeming a valid token must return the linked Document."""
        token = make_active_token(self.doc, self.user)
        returned_doc = consume_access_token(
            token_value=token.token, user=self.user
        )
        self.assertEqual(returned_doc.pk, self.doc.pk)

    def test_valid_token_marks_used_at(self):
        """After consumption, used_at must be set (single-use enforcement)."""
        token = make_active_token(self.doc, self.user)
        consume_access_token(token_value=token.token, user=self.user)
        token.refresh_from_db()
        self.assertIsNotNone(token.used_at)

    def test_valid_token_used_at_is_recent(self):
        """used_at must be stamped at consumption time (not at issuance)."""
        token = make_active_token(self.doc, self.user)
        before = timezone.now()
        consume_access_token(token_value=token.token, user=self.user)
        token.refresh_from_db()
        self.assertGreaterEqual(token.used_at, before)

    def test_valid_token_is_no_longer_valid_after_consumption(self):
        """After the first consumption, is_valid must be False (used_at is set)."""
        token = make_active_token(self.doc, self.user)
        consume_access_token(token_value=token.token, user=self.user)
        token.refresh_from_db()
        self.assertFalse(token.is_valid)

    # ── Single-use enforcement ────────────────────────────────────────────────

    def test_already_used_token_raises_404(self):
        """Presenting an already-consumed token must raise Http404."""
        token = make_active_token(self.doc, self.user, used=True)
        with self.assertRaises(Http404):
            consume_access_token(token_value=token.token, user=self.user)

    def test_second_call_with_same_token_raises_404(self):
        """Consuming the same token twice: second call must raise Http404."""
        token = make_active_token(self.doc, self.user)
        consume_access_token(token_value=token.token, user=self.user)
        with self.assertRaises(Http404):
            consume_access_token(token_value=token.token, user=self.user)

    # ── Expiry gate ───────────────────────────────────────────────────────────

    def test_expired_token_raises_404(self):
        """An expired token (expires_at in the past) must raise Http404."""
        token = make_active_token(self.doc, self.user, expired=True)
        with self.assertRaises(Http404):
            consume_access_token(token_value=token.token, user=self.user)

    # ── Not-found gate ────────────────────────────────────────────────────────

    def test_nonexistent_token_raises_404(self):
        """A token value that does not exist must raise Http404."""
        with self.assertRaises(Http404):
            consume_access_token(
                token_value="a" * 64,  # valid-length but non-existent token
                user=self.user,
            )

    # ── IDOR / cross-user isolation ───────────────────────────────────────────

    def test_different_user_raises_404_not_403(self):
        """
        IDOR: presenting another user's token must return Http404.
        Http403 would confirm the token exists, violating IDOR prevention.
        """
        token = make_active_token(self.doc, self.user)
        stranger = make_user()
        with self.assertRaises(Http404):
            consume_access_token(token_value=token.token, user=stranger)

    def test_different_user_does_not_consume_token(self):
        """
        After an IDOR-blocked attempt, the original token must still be valid
        (the rejected call must not burn the token).
        """
        token = make_active_token(self.doc, self.user)
        stranger = make_user()
        try:
            consume_access_token(token_value=token.token, user=stranger)
        except Http404:
            pass
        token.refresh_from_db()
        self.assertTrue(token.is_valid)

    # ── Audit log ─────────────────────────────────────────────────────────────

    def test_audit_event_written_on_token_redemption(self):
        """Consuming a valid token must write a data.viewed audit log entry."""
        from apps.audit.models import AuditLogEntry, AuditEventType

        token = make_active_token(self.doc, self.user)
        consume_access_token(token_value=token.token, user=self.user)
        entry = AuditLogEntry.objects.filter(
            actor_id=str(self.user.pk),
            resource_id=str(self.doc.pk),
        ).order_by("-timestamp").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.event_type, AuditEventType.RECORD_VIEWED)

    def test_audit_event_detail_action_is_token_redeemed(self):
        """Redemption audit entry must have action='token_redeemed'."""
        from apps.audit.models import AuditLogEntry

        token = make_active_token(self.doc, self.user)
        consume_access_token(token_value=token.token, user=self.user)
        entry = AuditLogEntry.objects.filter(
            resource_id=str(self.doc.pk),
        ).order_by("-timestamp").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.event_detail.get("action"), "token_redeemed")

    def test_audit_event_detail_pipeda_no_original_filename(self):
        """PIPEDA: original_filename must NOT appear in redemption audit detail."""
        from apps.audit.models import AuditLogEntry

        token = make_active_token(self.doc, self.user)
        consume_access_token(token_value=token.token, user=self.user)
        entry = AuditLogEntry.objects.filter(
            resource_id=str(self.doc.pk),
        ).order_by("-timestamp").first()
        self.assertIsNotNone(entry)
        detail_str = str(entry.event_detail)
        self.assertNotIn("report.pdf", detail_str)
        self.assertNotIn("original_filename", detail_str)

    def test_audit_event_detail_pipeda_no_storage_key(self):
        """PIPEDA: storage_key must NOT appear in redemption audit detail."""
        from apps.audit.models import AuditLogEntry

        token = make_active_token(self.doc, self.user)
        consume_access_token(token_value=token.token, user=self.user)
        entry = AuditLogEntry.objects.filter(
            resource_id=str(self.doc.pk),
        ).order_by("-timestamp").first()
        self.assertIsNotNone(entry)
        detail_str = str(entry.event_detail)
        self.assertNotIn("storage_key", detail_str)
        self.assertNotIn("documents/active/", detail_str)

    def test_audit_failure_does_not_prevent_download(self):
        """
        If the audit write raises, consume_access_token must still return
        the Document (audit failure must never degrade citizen access).
        """
        token = make_active_token(self.doc, self.user)
        with patch("apps.audit.services.record_event", side_effect=Exception("DB down")):
            doc = consume_access_token(token_value=token.token, user=self.user)
        self.assertEqual(doc.pk, self.doc.pk)

    # ── Returned document correctness ─────────────────────────────────────────

    def test_returned_document_has_correct_scan_status(self):
        """Returned Document must reflect the current scan_status from the DB."""
        token = make_active_token(self.doc, self.user)
        doc = consume_access_token(token_value=token.token, user=self.user)
        self.assertEqual(doc.scan_status, Document.ScanStatus.ACTIVE)


# ─────────────────────────────────────────────────────────────────────────────
# 5. TokenIsValidPropertyTests — DocumentAccessToken.is_valid
# ─────────────────────────────────────────────────────────────────────────────


class TokenIsValidPropertyTests(TestCase):
    """Unit tests for the is_valid property on DocumentAccessToken."""

    def setUp(self):
        self.category = make_category()
        self.user = make_user()
        self.doc = make_document(self.user, self.category)

    def test_unused_unexpired_token_is_valid(self):
        token = make_active_token(self.doc, self.user)
        self.assertTrue(token.is_valid)

    def test_used_token_is_not_valid(self):
        token = make_active_token(self.doc, self.user, used=True)
        self.assertFalse(token.is_valid)

    def test_expired_token_is_not_valid(self):
        token = make_active_token(self.doc, self.user, expired=True)
        self.assertFalse(token.is_valid)

    def test_used_and_expired_token_is_not_valid(self):
        token = make_active_token(self.doc, self.user, used=True, expired=True)
        self.assertFalse(token.is_valid)


# ─────────────────────────────────────────────────────────────────────────────
# 6. PurgeExpiredTokensTests — purge_expired_tokens()
# ─────────────────────────────────────────────────────────────────────────────


class PurgeExpiredTokensTests(TestCase):
    """
    Tests for purge_expired_tokens() in services/retention.py.

    purge_expired_tokens() deletes tokens where:
      - expires_at <= now() (expired), OR
      - used_at is not None (consumed, single-use exhausted)

    Valid tokens (expires_at in future AND used_at=None) must NOT be deleted.
    """

    def setUp(self):
        self.category = make_category()
        self.user = make_user()
        self.doc = make_document(self.user, self.category)

    # ── Deletion of purgeable tokens ──────────────────────────────────────────

    def test_purges_expired_tokens(self):
        """Tokens with expires_at in the past must be deleted."""
        token = make_active_token(self.doc, self.user, expired=True)
        count = purge_expired_tokens()
        self.assertEqual(count, 1)
        self.assertFalse(DocumentAccessToken.objects.filter(pk=token.pk).exists())

    def test_purges_used_tokens(self):
        """Tokens with used_at set must be deleted even if not expired."""
        token = make_active_token(self.doc, self.user, used=True)
        count = purge_expired_tokens()
        self.assertEqual(count, 1)
        self.assertFalse(DocumentAccessToken.objects.filter(pk=token.pk).exists())

    def test_purges_both_expired_and_used_tokens(self):
        """Both expired and used tokens purged in one call."""
        expired_token = make_active_token(self.doc, self.user, expired=True)
        used_token = make_active_token(self.doc, self.user, used=True)
        count = purge_expired_tokens()
        self.assertEqual(count, 2)
        self.assertFalse(DocumentAccessToken.objects.filter(pk=expired_token.pk).exists())
        self.assertFalse(DocumentAccessToken.objects.filter(pk=used_token.pk).exists())

    def test_purges_used_and_expired_token_once(self):
        """Token that is both used AND expired counts as 1 deletion."""
        make_active_token(self.doc, self.user, used=True, expired=True)
        count = purge_expired_tokens()
        self.assertEqual(count, 1)

    # ── Preservation of valid tokens ──────────────────────────────────────────

    def test_does_not_purge_valid_tokens(self):
        """Valid (unexpired, unused) tokens must NOT be deleted."""
        token = make_active_token(self.doc, self.user)
        count = purge_expired_tokens()
        self.assertEqual(count, 0)
        self.assertTrue(DocumentAccessToken.objects.filter(pk=token.pk).exists())

    def test_mixed_tokens_only_purges_purgeable(self):
        """Valid tokens survive; expired and used tokens are purged."""
        valid_token = make_active_token(self.doc, self.user)
        expired_token = make_active_token(self.doc, self.user, expired=True)
        used_token = make_active_token(self.doc, self.user, used=True)

        count = purge_expired_tokens()

        self.assertEqual(count, 2)
        self.assertTrue(DocumentAccessToken.objects.filter(pk=valid_token.pk).exists())
        self.assertFalse(DocumentAccessToken.objects.filter(pk=expired_token.pk).exists())
        self.assertFalse(DocumentAccessToken.objects.filter(pk=used_token.pk).exists())

    # ── Return value ──────────────────────────────────────────────────────────

    def test_returns_zero_when_nothing_to_purge(self):
        """Empty table: returns 0 without raising."""
        count = purge_expired_tokens()
        self.assertEqual(count, 0)

    def test_returns_correct_count(self):
        """Return value equals the number of deleted tokens."""
        make_active_token(self.doc, self.user, expired=True)
        make_active_token(self.doc, self.user, expired=True)
        make_active_token(self.doc, self.user, expired=True)
        count = purge_expired_tokens()
        self.assertEqual(count, 3)

    # ── dry_run mode ──────────────────────────────────────────────────────────

    def test_dry_run_returns_count_without_deleting(self):
        """dry_run=True returns the would-be count without performing deletion."""
        make_active_token(self.doc, self.user, expired=True)
        make_active_token(self.doc, self.user, used=True)

        count = purge_expired_tokens(dry_run=True)

        self.assertEqual(count, 2)
        # Tokens must still exist
        self.assertEqual(DocumentAccessToken.objects.count(), 2)

    def test_dry_run_false_actually_deletes(self):
        """dry_run=False (default) actually deletes the tokens."""
        make_active_token(self.doc, self.user, expired=True)
        purge_expired_tokens(dry_run=False)
        self.assertEqual(DocumentAccessToken.objects.count(), 0)


# ─────────────────────────────────────────────────────────────────────────────
# 7. RunPurgeExpiredTokensTaskTests — run_purge_expired_tokens Celery task
# ─────────────────────────────────────────────────────────────────────────────


class RunPurgeExpiredTokensTaskTests(TestCase):
    """
    Integration tests for the Celery task wrapper around purge_expired_tokens().

    Tests run in CELERY_TASK_ALWAYS_EAGER mode (set by test settings).
    """

    def setUp(self):
        self.category = make_category()
        self.user = make_user()
        self.doc = make_document(self.user, self.category)

    def test_task_deletes_expired_tokens(self):
        """run_purge_expired_tokens task must purge expired tokens."""
        make_active_token(self.doc, self.user, expired=True)
        run_purge_expired_tokens.run()
        self.assertEqual(DocumentAccessToken.objects.count(), 0)

    def test_task_deletes_used_tokens(self):
        """run_purge_expired_tokens task must purge used tokens."""
        make_active_token(self.doc, self.user, used=True)
        run_purge_expired_tokens.run()
        self.assertEqual(DocumentAccessToken.objects.count(), 0)

    def test_task_returns_count(self):
        """Task must return the number of deleted tokens."""
        make_active_token(self.doc, self.user, expired=True)
        make_active_token(self.doc, self.user, used=True)
        count = run_purge_expired_tokens.run()
        self.assertEqual(count, 2)

    def test_task_preserves_valid_tokens(self):
        """Valid tokens must not be deleted by the task."""
        valid_token = make_active_token(self.doc, self.user)
        run_purge_expired_tokens.run()
        self.assertTrue(DocumentAccessToken.objects.filter(pk=valid_token.pk).exists())

    def test_task_returns_zero_when_nothing_to_purge(self):
        """Task must return 0 when no tokens are purgeable."""
        count = run_purge_expired_tokens.run()
        self.assertEqual(count, 0)

    def test_task_decorator_acks_late(self):
        """acks_late must be True (at-least-once delivery guarantee)."""
        self.assertTrue(run_purge_expired_tokens.acks_late)

    def test_task_decorator_reject_on_worker_lost(self):
        """reject_on_worker_lost must be True (requeue on worker crash)."""
        self.assertTrue(run_purge_expired_tokens.reject_on_worker_lost)

    def test_task_decorator_max_retries_zero(self):
        """max_retries must be 0 (purge is a periodic task, not a retry candidate)."""
        self.assertEqual(run_purge_expired_tokens.max_retries, 0)


# ─────────────────────────────────────────────────────────────────────────────
# 8. GeneratePresignedDownloadUrlTests — generate_presigned_download_url()
# ─────────────────────────────────────────────────────────────────────────────


class GeneratePresignedDownloadUrlTests(TestCase):
    """Tests for generate_presigned_download_url."""

    def setUp(self):
        from apps.documents.services.download import generate_presigned_download_url
        self.generate_url = generate_presigned_download_url

    def test_returns_presigned_url_string(self):
        """Happy path: boto3 returns a URL string."""
        import sys
        from unittest.mock import MagicMock

        # boto3/botocore cannot be imported in the CI sandbox (pyopenssl conflict),
        # so we inject a fake boto3 module into sys.modules before the function runs.
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://s3.example.com/signed-url"
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            with self.settings(
                STORAGES={
                    "default": {
                        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
                        "OPTIONS": {"bucket_name": "test-bucket", "region_name": "ca-central-1"},
                    }
                }
            ):
                url = self.generate_url(storage_key="documents/active/abc/xyz.bin", ttl_seconds=300)

        self.assertIsInstance(url, str)
        self.assertIn("https://", url)

    def test_client_error_propagates(self):
        """ClientError from boto3 should propagate (not swallowed)."""
        import sys
        from unittest.mock import MagicMock

        # Use a real exception class without needing a real boto3 import.
        class _FakeClientError(Exception):
            pass

        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.side_effect = _FakeClientError("AccessDenied")
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        # Expose the exception class so the service's except clause can reference it
        mock_boto3.exceptions.ClientError = _FakeClientError

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            with self.settings(
                STORAGES={
                    "default": {
                        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
                        "OPTIONS": {"bucket_name": "test-bucket", "region_name": "ca-central-1"},
                    }
                }
            ):
                with self.assertRaises(_FakeClientError):
                    self.generate_url(storage_key="documents/active/abc/xyz.bin", ttl_seconds=300)


# ─────────────────────────────────────────────────────────────────────────────
# 9. FullDownloadFlowTests — end-to-end issue + consume integration
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CIVICOS=CIVICOS_DOWNLOAD)
class FullDownloadFlowTests(TestCase):
    """
    End-to-end integration tests: issue → consume → verify.

    These tests exercise the complete download flow without mocking any
    service-layer functions. They verify that the two service calls work
    together correctly.
    """

    def setUp(self):
        self.category = make_category()
        self.user = make_user()
        self.doc = make_document(self.user, self.category, scan_status=Document.ScanStatus.ACTIVE)

    def test_full_flow_citizen_downloads_own_document(self):
        """Citizen issues and redeems a token for their own ACTIVE document."""
        token = issue_access_token(user=self.user, document=self.doc)
        self.assertTrue(token.is_valid)

        returned_doc = consume_access_token(token_value=token.token, user=self.user)
        self.assertEqual(returned_doc.pk, self.doc.pk)

        token.refresh_from_db()
        self.assertFalse(token.is_valid)

    def test_full_flow_token_single_use_enforced(self):
        """After a successful download, the token cannot be reused."""
        token = issue_access_token(user=self.user, document=self.doc)
        consume_access_token(token_value=token.token, user=self.user)

        with self.assertRaises(Http404):
            consume_access_token(token_value=token.token, user=self.user)

    def test_full_flow_token_cross_user_blocked(self):
        """Token issued to user A cannot be consumed by user B."""
        token = issue_access_token(user=self.user, document=self.doc)
        stranger = make_user()
        with self.assertRaises(Http404):
            consume_access_token(token_value=token.token, user=stranger)

    def test_full_flow_superuser_can_issue_and_consume(self):
        """Superuser may issue and consume a token for any document."""
        su = make_superuser()
        token = issue_access_token(user=su, document=self.doc)
        returned_doc = consume_access_token(token_value=token.token, user=su)
        self.assertEqual(returned_doc.pk, self.doc.pk)

    def test_full_flow_two_tokens_for_same_document_are_independent(self):
        """Two separate tokens for the same document are independent."""
        token_a = issue_access_token(user=self.user, document=self.doc)
        token_b = issue_access_token(user=self.user, document=self.doc)

        self.assertNotEqual(token_a.token, token_b.token)

        # Consuming token_a must not affect token_b
        consume_access_token(token_value=token_a.token, user=self.user)
        # token_b is still valid
        returned_doc = consume_access_token(token_value=token_b.token, user=self.user)
        self.assertEqual(returned_doc.pk, self.doc.pk)

    def test_full_flow_purge_after_consumption(self):
        """Consumed token is purged by purge_expired_tokens."""
        token = issue_access_token(user=self.user, document=self.doc)
        consume_access_token(token_value=token.token, user=self.user)

        purge_count = purge_expired_tokens()
        self.assertEqual(purge_count, 1)
        self.assertFalse(DocumentAccessToken.objects.filter(pk=token.pk).exists())
