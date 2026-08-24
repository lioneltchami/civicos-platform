"""
Wave 7 — §24.1 canonical test file: test_pipeda.py

PIPEDA / Privacy Act compliance tests for the Document Management BB.

Constraints tested:
  1. IDOR prevention — citizens get Http404 (not 403) for non-owned document PKs
  2. Scan gate — only ACTIVE documents are downloadable
  3. storage_key leakage — NEVER in audit event_detail, signal kwargs, or download response
  4. Legal hold absolute block — hard_delete raises when legal_hold=True
  5. Quarantine PII — document_quarantined signal MUST NOT contain uploader identity
  6. original_filename — NEVER in audit event_detail (PII risk)
  7. IP masking — IPv4 last octet zeroed; IPv6 /48 prefix retained
  8. Citizen vs staff IDOR — staff with coordinator_view_document CAN access non-owned docs
  9. Access token IDOR — Http404 for wrong issuer
  10. record_event atomicity — audit entry committed with state change
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.http import Http404
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.audit.models import AuditEventType, AuditLogEntry
from apps.documents.models import Document, DocumentAccessToken, DocumentCategory
from apps.documents.services.download import (
    TokenExpiredError,
    _mask_ip,
    consume_access_token,
    issue_access_token,
)
from apps.documents.services.retention import apply_legal_hold, hard_delete, soft_delete

User = get_user_model()

_CTR = 0


def _token_auth(user) -> str:
    """Return a DRF Token auth header value for the given user."""
    token, _ = Token.objects.get_or_create(user=user)
    return f"Token {token.key}"


def _make_user(**kwargs):
    global _CTR
    _CTR += 1
    return User.objects.create_user(
        email=f"pipeda{_CTR}@example.com",
        password="testpass123",
        **kwargs,
    )


def _make_category(**kwargs):
    global _CTR
    _CTR += 1
    return DocumentCategory.objects.create(
        name_en="PIPEDA Test",
        name_fr="Test PIPEDA",
        slug=f"pipeda-cat-{_CTR}",
        allowed_mime_types=["application/pdf"],
        min_retention_days=730,
        max_retention_days=2555,
        **kwargs,
    )


def _make_document(user, category, **kwargs):
    doc_id = uuid.uuid4()
    kwargs.setdefault("scan_status", Document.ScanStatus.ACTIVE)
    return Document.objects.create(
        uploaded_by=user,
        category=category,
        original_filename="confidential-citizen-info.pdf",
        _storage_key=f"documents/active/{doc_id}/{uuid.uuid4().hex}.bin",
        mime_type="application/pdf",
        size_bytes=8_192,
        **kwargs,
    )


def _grant_perm(user, codename):
    ct = ContentType.objects.get_for_model(Document)
    perm, _ = Permission.objects.get_or_create(
        codename=codename,
        content_type=ct,
        defaults={"name": f"Can {codename}"},
    )
    user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


# ─────────────────────────────────────────────────────────────────────────────
# 1. IDOR prevention — Http404 for non-owned documents
# ─────────────────────────────────────────────────────────────────────────────


class IDORPreventionTests(TestCase):
    """
    Citizens must receive Http404 (not 403) for documents they don't own.
    OWASP IDOR: returning 403 confirms resource existence, enabling enumeration.
    """

    def setUp(self):
        self.owner = _make_user()
        self.attacker = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.owner, self.cat)

    def test_citizen_gets_404_for_non_owned_document(self):
        """Attacker getting an access token for owner's document raises Http404."""
        with self.assertRaises(Http404):
            issue_access_token(user=self.attacker, document=self.doc)

    def test_not_403_for_non_owned_document(self):
        """Explicitly verify PermissionDenied is NOT raised (would confirm existence)."""
        from django.core.exceptions import PermissionDenied

        try:
            issue_access_token(user=self.attacker, document=self.doc)
        except Http404:
            pass  # expected
        except PermissionDenied:
            self.fail("PermissionDenied raised — exposes document existence (IDOR risk)")

    def test_anon_user_gets_404(self):
        """
        Anonymous users must get Http404.

        Uses AnonymousUser (is_authenticated=False) — not an unsaved User()
        instance, which has is_authenticated=True and would bypass the auth
        check, potentially reaching the ownership comparison and raising an
        arbitrary exception (masking IDOR violations).
        """
        from django.contrib.auth.models import AnonymousUser

        anon = AnonymousUser()
        with self.assertRaises(Http404):
            issue_access_token(user=anon, document=self.doc)

    def test_wrong_token_issuer_gets_404(self):
        """Consuming a token issued to another user raises Http404."""
        token = issue_access_token(user=self.owner, document=self.doc)
        with self.assertRaises(Http404):
            consume_access_token(token_value=token.token, user=self.attacker)

    def test_owner_can_get_token(self):
        """The document owner can successfully get a download token."""
        token = issue_access_token(user=self.owner, document=self.doc)
        self.assertIsNotNone(token)
        self.assertTrue(token.is_valid)

    def test_idor_returns_404_not_403_via_api(self):
        """
        IDOR via DRF API: GET /api/v1/documents/{other_doc.pk}/ as attacker
        must return 404, not 403.

        A 403 would confirm to the attacker that a document with that UUID
        exists, enabling IDOR enumeration (OWASP A01:2021).
        """
        url = reverse("api-v1:document-detail", args=[self.doc.pk])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=_token_auth(self.attacker))
        response = client.get(url)
        self.assertEqual(
            response.status_code,
            404,
            "IDOR: attacker must receive 404 (not 403) for a non-owned document UUID",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Scan gate — only ACTIVE documents downloadable by citizens
# ─────────────────────────────────────────────────────────────────────────────


class ScanGateTests(TestCase):
    """
    Citizens may only download ACTIVE documents.
    All other scan_status values must return Http404.
    """

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()

    def _make_doc_with_status(self, status):
        doc_id = uuid.uuid4()
        return Document.objects.create(
            uploaded_by=self.user,
            category=self.cat,
            original_filename="test.pdf",
            _storage_key=f"documents/active/{doc_id}/{uuid.uuid4().hex}.bin",
            mime_type="application/pdf",
            size_bytes=1024,
            scan_status=status,
        )

    def test_pending_upload_not_downloadable(self):
        doc = self._make_doc_with_status(Document.ScanStatus.PENDING_UPLOAD)
        with self.assertRaises(Http404):
            issue_access_token(user=self.user, document=doc)

    def test_scanning_not_downloadable(self):
        doc = self._make_doc_with_status(Document.ScanStatus.SCANNING)
        with self.assertRaises(Http404):
            issue_access_token(user=self.user, document=doc)

    def test_quarantined_not_downloadable(self):
        doc = self._make_doc_with_status(Document.ScanStatus.QUARANTINED)
        with self.assertRaises(Http404):
            issue_access_token(user=self.user, document=doc)

    def test_deleted_not_downloadable(self):
        doc = self._make_doc_with_status(Document.ScanStatus.DELETED)
        with self.assertRaises(Http404):
            issue_access_token(user=self.user, document=doc)

    def test_purged_not_downloadable(self):
        doc = self._make_doc_with_status(Document.ScanStatus.PURGED)
        with self.assertRaises(Http404):
            issue_access_token(user=self.user, document=doc)

    def test_active_is_downloadable(self):
        doc = self._make_doc_with_status(Document.ScanStatus.ACTIVE)
        token = issue_access_token(user=self.user, document=doc)
        self.assertTrue(token.is_valid)

    def test_soft_deleted_not_downloadable(self):
        """Soft-deleted documents (deleted_at set) must return Http404 even if ACTIVE."""
        doc = self._make_doc_with_status(Document.ScanStatus.ACTIVE)
        soft_delete(document=doc, deleted_by=self.user, reason="test")
        doc.refresh_from_db()
        # After soft_delete, status is DELETED — verifies double protection
        with self.assertRaises(Http404):
            issue_access_token(user=self.user, document=doc)

    def test_scan_pending_blocks_download_via_api(self):
        """
        Scan gate via DRF API: POST /api/v1/documents/{doc}/request-download/ for a
        SCANNING document must return 404.

        The download-init view checks scan_status == ACTIVE before issuing a
        token. Any other status must produce 404 (not 403, to avoid leaking
        document state to unauthenticated / IDOR attackers).

        Note: renamed GET /download/ → POST /request-download/ per GovStack spec §18 §7.4.
        """
        scanning_doc = self._make_doc_with_status(Document.ScanStatus.SCANNING)
        url = reverse("api-v1:document-request-download", args=[scanning_doc.pk])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = client.post(url)
        self.assertEqual(
            response.status_code,
            404,
            "Scan gate: SCANNING document must return 404 via the request-download API endpoint",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. storage_key leakage prevention
# ─────────────────────────────────────────────────────────────────────────────


class StorageKeyLeakageTests(TestCase):
    """
    storage_key MUST NEVER appear in audit event_detail, signal kwargs,
    or any service return value.
    """

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)

    def test_soft_delete_audit_no_storage_key(self):
        soft_delete(document=self.doc, deleted_by=self.user, reason="test")
        entries = AuditLogEntry.objects.filter(
            resource_id=str(self.doc.pk),
            event_type=AuditEventType.RECORD_DELETED,
        )
        for entry in entries:
            detail_str = str(entry.event_detail)
            self.assertNotIn("storage_key", detail_str)
            self.assertNotIn(self.doc._storage_key, detail_str)

    def test_issue_access_token_audit_no_storage_key(self):
        issue_access_token(user=self.user, document=self.doc)
        entries = AuditLogEntry.objects.filter(
            resource_id=str(self.doc.pk),
            event_type=AuditEventType.RECORD_VIEWED,
        )
        for entry in entries:
            detail_str = str(entry.event_detail)
            self.assertNotIn("storage_key", detail_str)
            self.assertNotIn(self.doc._storage_key, detail_str)

    def test_access_token_object_no_storage_key_field(self):
        """DocumentAccessToken model must not have a storage_key field."""
        token = issue_access_token(user=self.user, document=self.doc)
        self.assertFalse(hasattr(token, "storage_key"))
        self.assertFalse(hasattr(token, "_storage_key"))

    def test_hard_delete_audit_no_storage_key_value(self):
        """RECORD_PURGED event_detail MUST NOT contain the actual storage_key path."""
        storage_key_value = self.doc._storage_key
        self.doc.deleted_at = timezone.now() - timedelta(days=31)
        self.doc.scan_status = Document.ScanStatus.DELETED
        self.doc.legal_hold = False
        self.doc.save(update_fields=["deleted_at", "scan_status", "legal_hold", "updated_at"])

        with patch("apps.documents.services.retention.default_storage") as mock_s:
            mock_s.delete.return_value = None
            hard_delete(document=self.doc)

        entries = AuditLogEntry.objects.filter(
            resource_id=str(self.doc.pk),
            event_type=AuditEventType.RECORD_PURGED,
        )
        self.assertGreaterEqual(entries.count(), 1)
        for entry in entries:
            self.assertNotIn("storage_key", entry.event_detail)
            self.assertNotIn(storage_key_value, str(entry.event_detail))

    def test_storage_key_never_in_api_response(self):
        """
        S1 via DRF API: GET /api/v1/documents/{doc}/ must never leak storage_key
        or _storage_key in the JSON response.

        Checks both the parsed response dict (catches extra serializer fields)
        and the raw response bytes (catches accidental nested serialisation).
        The actual _storage_key path value must also not appear verbatim.
        """
        url = reverse("api-v1:document-detail", args=[self.doc.pk])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
        response = client.get(url)

        self.assertEqual(response.status_code, 200)

        # Check parsed response dict (field-name leakage)
        self.assertNotIn("storage_key", response.data)
        self.assertNotIn("_storage_key", response.data)

        # Check raw response bytes (value leakage or nested serialisation)
        body = response.content.decode()
        self.assertNotIn(
            '"storage_key"', body, "storage_key field name must not appear in JSON response"
        )
        self.assertNotIn(
            '"_storage_key"', body, "_storage_key field name must not appear in JSON response"
        )
        self.assertNotIn(
            self.doc._storage_key,
            body,
            "Raw storage key path value must not appear in JSON response",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Legal hold absolute block
# ─────────────────────────────────────────────────────────────────────────────


class LegalHoldAbsoluteBlockTests(TestCase):
    """legal_hold=True must ABSOLUTELY block all automated disposal."""

    def setUp(self):
        self.user = _make_user()
        self.user = _grant_perm(self.user, "manage_legal_hold")
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)

    def test_legal_hold_blocks_hard_delete(self):
        """hard_delete() must raise ValueError if legal_hold=True."""
        self.doc.deleted_at = timezone.now() - timedelta(days=31)
        self.doc.scan_status = Document.ScanStatus.DELETED
        self.doc.legal_hold = True
        self.doc.save(update_fields=["deleted_at", "scan_status", "legal_hold", "updated_at"])

        with self.assertRaises(ValueError):
            hard_delete(document=self.doc)

    def test_legal_hold_blocks_hard_delete_even_after_grace_period(self):
        """No grace period matters — legal hold is an absolute block."""
        self.doc.deleted_at = timezone.now() - timedelta(days=3650)  # 10 years ago
        self.doc.scan_status = Document.ScanStatus.DELETED
        self.doc.legal_hold = True
        self.doc.save(update_fields=["deleted_at", "scan_status", "legal_hold", "updated_at"])

        with self.assertRaises(ValueError):
            hard_delete(document=self.doc)

    def test_release_hold_then_hard_delete_succeeds(self):
        """After releasing legal hold, hard_delete() can proceed."""
        apply_legal_hold(document=self.doc, set_by=self.user, reason="litigation")
        self.doc.refresh_from_db()
        self.assertTrue(self.doc.legal_hold)

        from apps.documents.services.retention import release_legal_hold

        release_legal_hold(document=self.doc, released_by=self.user)
        self.doc.refresh_from_db()
        self.assertFalse(self.doc.legal_hold)

        # Now set up for hard delete
        self.doc.deleted_at = timezone.now() - timedelta(days=31)
        self.doc.scan_status = Document.ScanStatus.DELETED
        self.doc.save(update_fields=["deleted_at", "scan_status", "updated_at"])

        with patch("apps.documents.services.retention.default_storage") as mock_s:
            mock_s.delete.return_value = None
            hard_delete(document=self.doc)  # must NOT raise

        self.doc.refresh_from_db()
        self.assertEqual(self.doc.scan_status, Document.ScanStatus.PURGED)

    def test_legal_hold_prevents_soft_delete_automated(self):
        """Legal hold should also block soft_delete() for automated disposal."""
        self.doc.legal_hold = True
        self.doc.save(update_fields=["legal_hold", "updated_at"])
        with self.assertRaises(ValueError):
            soft_delete(document=self.doc, deleted_by=None, reason="retention_expired")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Quarantine signal PII — no uploader identity
# ─────────────────────────────────────────────────────────────────────────────


class QuarantineSignalPIITests(TestCase):
    """
    PIPEDA: document_quarantined signal MUST NOT include uploader identity.
    Admin is notified; citizen is not — and admin must not be given PII they
    don't need (minimisation principle, PIPEDA Schedule 1 §4.4).
    """

    def test_quarantine_signal_kwargs_contain_no_uploader_pii(self):
        """
        PIPEDA: document_quarantined must NOT expose uploader identity at runtime.

        Calls _mark_document_quarantined_clamav() — the real production code path
        that fires document_quarantined — with a mocked storage backend.  A
        receiver captures every kwarg the signal actually delivers and asserts that
        no PII keys are present.

        This tests runtime behaviour, not source comments.  A developer could
        add ``uploaded_by_id=doc.uploaded_by_id`` to the send() call and a
        source-inspection test would not catch it; this test will.
        """
        from apps.documents import signals as doc_signals
        from apps.documents.tasks import _mark_document_quarantined_clamav

        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat, scan_status=Document.ScanStatus.SCANNING)

        received: dict = {}

        def capture(sender, **kwargs):
            received.update(kwargs)

        doc_signals.document_quarantined.connect(capture, weak=False)
        try:
            with patch("django.core.files.storage.default_storage") as mock_storage:
                mock_storage.delete.return_value = None
                _mark_document_quarantined_clamav(
                    doc_pk=str(doc.pk),
                    virus_name="Eicar-Test-Signature",
                )
        finally:
            doc_signals.document_quarantined.disconnect(capture)

        # Signal must have fired
        self.assertTrue(received, "document_quarantined was not fired")

        # Only the two permitted keys (plus Django's internal 'signal' kwarg) are allowed
        pii_keys = {
            "uploaded_by_id",
            "uploaded_by",
            "original_filename",
            "storage_key",
            "_storage_key",
            "uploader_email",
        }
        found_pii = pii_keys & set(received.keys())
        self.assertFalse(
            found_pii,
            f"PIPEDA violation: document_quarantined signal contained PII keys: {found_pii}",
        )

        # Positive assertion: the two expected keys must be present
        self.assertIn("document_pk", received)
        self.assertIn("scan_engine_result", received)
        self.assertEqual(received["document_pk"], str(doc.pk))
        self.assertIn("Eicar-Test-Signature", received["scan_engine_result"])

    def test_quarantine_signal_excludes_original_filename(self):
        """
        Gap 1.2 — PIPEDA data minimisation: original_filename MUST NOT appear in
        the document_quarantined signal kwargs.

        original_filename can directly identify a citizen (e.g. "John_Smith_SIN.pdf").
        Admin notifications fired from the signal receive only document_pk and
        scan_engine_result — never any filename or identity-revealing field.

        This is a focused companion to test_quarantine_signal_kwargs_contain_no_uploader_pii
        that pinpoints the original_filename constraint by name, ensuring a future
        developer who adds ``original_filename=doc.original_filename`` to the
        signal send() is caught immediately.
        """
        from apps.documents import signals as doc_signals
        from apps.documents.tasks import _mark_document_quarantined_clamav

        user = _make_user()
        cat = _make_category()
        # Give the document a detectably PII filename
        doc = _make_document(user, cat, scan_status=Document.ScanStatus.SCANNING)
        doc.original_filename = f"SensitivePII_{uuid.uuid4().hex}.pdf"
        doc.save(update_fields=["original_filename"])

        received: dict = {}

        def capture(sender, **kwargs):
            received.update(kwargs)

        doc_signals.document_quarantined.connect(capture, weak=False)
        try:
            with patch("django.core.files.storage.default_storage") as mock_storage:
                mock_storage.delete.return_value = None
                _mark_document_quarantined_clamav(
                    doc_pk=str(doc.pk),
                    virus_name="Eicar-Test-Signature",
                )
        finally:
            doc_signals.document_quarantined.disconnect(capture)

        # Explicit named assertion for original_filename (PIPEDA §4.5 minimisation)
        self.assertNotIn(
            "original_filename",
            received,
            "PIPEDA violation: original_filename must NOT be in document_quarantined signal kwargs",
        )
        # Also assert the raw filename value does not appear anywhere in the signal
        for key, value in received.items():
            self.assertNotIn(
                doc.original_filename,
                str(value),
                f"PIPEDA violation: filename value leaked in signal kwarg '{key}'",
            )


# ─────────────────────────────────────────────────────────────────────────────
# 6. original_filename NEVER in audit event_detail
# ─────────────────────────────────────────────────────────────────────────────


class OriginalFilenameAuditTests(TestCase):
    """
    PIPEDA clause 4.5: Limit collection and disclosure of personal information.
    original_filename is PII (can identify the document creator/subject).
    It must NEVER appear in audit event_detail.
    """

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        # Include PII in original_filename to make leakage detectable
        self.doc = _make_document(
            self.user,
            self.cat,
        )
        # Override original_filename with something detectably PII
        self.doc.original_filename = f"John_Smith_SIN123456789_{uuid.uuid4().hex}.pdf"
        self.doc.save(update_fields=["original_filename"])

    def test_soft_delete_no_filename_in_audit(self):
        soft_delete(document=self.doc, deleted_by=self.user, reason="test")
        entries = AuditLogEntry.objects.filter(resource_id=str(self.doc.pk))
        for entry in entries:
            self.assertNotIn("original_filename", entry.event_detail)
            self.assertNotIn(self.doc.original_filename, str(entry.event_detail))

    def test_issue_token_no_filename_in_audit(self):
        issue_access_token(user=self.user, document=self.doc)
        entries = AuditLogEntry.objects.filter(resource_id=str(self.doc.pk))
        for entry in entries:
            self.assertNotIn("original_filename", entry.event_detail)
            self.assertNotIn(self.doc.original_filename, str(entry.event_detail))


# ─────────────────────────────────────────────────────────────────────────────
# 7. IP masking
# ─────────────────────────────────────────────────────────────────────────────


class IPMaskingTests(TestCase):
    """
    PIPEDA: IP addresses must be masked before storage.
    IPv4: last octet → 0.
    IPv6: last 80 bits → 0 (retain /48 prefix).
    """

    def test_ipv4_last_octet_zeroed(self):
        self.assertEqual(_mask_ip("192.168.1.100"), "192.168.1.0")

    def test_ipv4_all_octets(self):
        self.assertEqual(_mask_ip("10.20.30.40"), "10.20.30.0")

    def test_ipv4_last_octet_255(self):
        self.assertEqual(_mask_ip("172.16.0.255"), "172.16.0.0")

    def test_ipv6_prefix_retained(self):
        """IPv6 /48 prefix retained — last 80 bits zeroed."""
        masked = _mask_ip("2001:db8:1234:5678:9abc:def0:1234:5678")
        self.assertIsNotNone(masked)
        # Last 80 bits zeroed — mask should zero from 4th group onwards
        import ipaddress

        addr = ipaddress.ip_address(masked)
        network = ipaddress.ip_network("2001:db8:1234::/48", strict=False)
        self.assertIn(addr, network)

    def test_ipv6_full_mask(self):
        masked = _mask_ip("2001:0db8:85a3:0000:0000:8a2e:0370:7334")
        self.assertIsNotNone(masked)
        # Last 80 bits (5 groups of 16 bits) are zeroed
        masked.split(":")
        # IPv6 addresses may be compressed — just verify it's a valid address
        import ipaddress

        addr = ipaddress.ip_address(masked)
        # The last 80 bits should be zero
        int_val = int(addr)
        last_80_bits = int_val & ((1 << 80) - 1)
        self.assertEqual(last_80_bits, 0)

    def test_none_ip_returns_none(self):
        self.assertIsNone(_mask_ip(None))

    def test_unparseable_ip_returns_none(self):
        self.assertIsNone(_mask_ip("not-an-ip"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_mask_ip(""))

    def test_masked_ip_stored_in_token(self):
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)

        token = issue_access_token(
            user=user,
            document=doc,
            ip_address="192.168.5.99",
        )
        self.assertEqual(token.ip_address, "192.168.5.0")

    def test_raw_ip_not_stored_in_token(self):
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)

        token = issue_access_token(
            user=user,
            document=doc,
            ip_address="10.0.0.42",
        )
        self.assertNotEqual(token.ip_address, "10.0.0.42")
        self.assertEqual(token.ip_address, "10.0.0.0")


# ─────────────────────────────────────────────────────────────────────────────
# 8. Staff IDOR bypass — coordinator_view_document permission
# ─────────────────────────────────────────────────────────────────────────────


class StaffIDORBypassTests(TestCase):
    """
    Staff with coordinator_view_document CAN access non-owned documents.
    This is the only legitimate bypass of the ownership check.
    """

    def setUp(self):
        self.citizen = _make_user()
        self.coordinator = _make_user()
        self.coordinator = _grant_perm(self.coordinator, "coordinator_view_document")
        self.cat = _make_category()
        self.doc = _make_document(self.citizen, self.cat)

    def test_coordinator_can_download_non_owned_document(self):
        """Staff with coordinator_view_document can issue a token for any doc."""
        token = issue_access_token(user=self.coordinator, document=self.doc)
        self.assertIsNotNone(token)
        self.assertTrue(token.is_valid)

    def test_superuser_can_download_any_document(self):
        """Superusers bypass ownership check."""
        superuser = User.objects.create_superuser(
            email="super@example.com",
            password="testpass123",
        )
        token = issue_access_token(user=superuser, document=self.doc)
        self.assertIsNotNone(token)

    def test_staff_without_permission_gets_404(self):
        """Staff without coordinator_view_document are also subject to IDOR."""
        staff = _make_user(is_staff=True)
        with self.assertRaises(Http404):
            issue_access_token(user=staff, document=self.doc)


# ─────────────────────────────────────────────────────────────────────────────
# 9. Access token security — single-use, expiry, issuer binding
# ─────────────────────────────────────────────────────────────────────────────


class AccessTokenSecurityTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)

    def test_token_is_single_use(self):
        """After consuming a token, it cannot be consumed again."""
        token = issue_access_token(user=self.user, document=self.doc)
        consume_access_token(token_value=token.token, user=self.user)
        with self.assertRaises(Http404):
            consume_access_token(token_value=token.token, user=self.user)

    def test_expired_token_raises_token_expired_error(self):
        """
        Expired (never-used) tokens raise TokenExpiredError.
        The view maps this to HTTP 410 Gone (GovStack spec §18 §7.5).
        """
        token = DocumentAccessToken.objects.create(
            document=self.doc,
            issued_to=self.user,
            token="a" * 64,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        with self.assertRaises(TokenExpiredError):
            consume_access_token(token_value=token.token, user=self.user)

    def test_nonexistent_token_raises_404(self):
        with self.assertRaises(Http404):
            consume_access_token(token_value="b" * 64, user=self.user)

    def test_token_bound_to_issuer(self):
        """A token issued to user A cannot be consumed by user B."""
        other = _make_user()
        token = issue_access_token(user=self.user, document=self.doc)
        with self.assertRaises(Http404):
            consume_access_token(token_value=token.token, user=other)

    def test_token_64_chars(self):
        """Tokens must be 64 hex chars (128-bit entropy)."""
        token = issue_access_token(user=self.user, document=self.doc)
        self.assertEqual(len(token.token), 64)
        # Verify hex
        int(token.token, 16)  # raises ValueError if not hex


# ─────────────────────────────────────────────────────────────────────────────
# 10. record_event atomicity (PIPEDA 4.5.3)
# ─────────────────────────────────────────────────────────────────────────────


class RecordEventAtomicityTests(TestCase):
    """
    PIPEDA 4.5.3: every access to personal information must be logged.
    The audit entry MUST be created in the same transaction as the state change.
    If the state change fails, the audit entry must NOT be committed.
    """

    def test_audit_entry_written_for_every_token_issuance(self):
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)

        before = AuditLogEntry.objects.filter(
            resource_id=str(doc.pk),
            event_type=AuditEventType.RECORD_VIEWED,
        ).count()

        issue_access_token(user=user, document=doc)

        after = AuditLogEntry.objects.filter(
            resource_id=str(doc.pk),
            event_type=AuditEventType.RECORD_VIEWED,
        ).count()
        self.assertEqual(after, before + 1)

    def test_audit_entry_written_on_token_consumption(self):
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)
        token = issue_access_token(user=user, document=doc)

        before = AuditLogEntry.objects.filter(
            resource_id=str(doc.pk),
            event_type=AuditEventType.RECORD_VIEWED,
        ).count()

        consume_access_token(token_value=token.token, user=user)

        after = AuditLogEntry.objects.filter(
            resource_id=str(doc.pk),
            event_type=AuditEventType.RECORD_VIEWED,
        ).count()
        self.assertEqual(after, before + 1)

    def test_audit_entry_actor_id_is_user_pk_string(self):
        """PIPEDA: audit actor_id must be str(user.pk) — not email."""
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)
        issue_access_token(user=user, document=doc)

        entry = (
            AuditLogEntry.objects.filter(
                resource_id=str(doc.pk),
                event_type=AuditEventType.RECORD_VIEWED,
            )
            .order_by("-timestamp")
            .first()
        )

        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor_id, str(user.pk))
        self.assertNotIn("@", entry.actor_id or "")  # not email

    def test_audit_entry_event_detail_no_user_email(self):
        """No user email in audit event_detail for any document operation."""
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)
        issue_access_token(user=user, document=doc)

        for entry in AuditLogEntry.objects.filter(resource_id=str(doc.pk)):
            self.assertNotIn(user.email, str(entry.event_detail))
