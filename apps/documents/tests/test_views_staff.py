"""
apps/documents/tests/test_views_staff.py
==========================================
Edge-case test suite for staff-facing Document Management BB views.

Complements test_wave5_views.py, which covers the basic happy-path and 403
scenarios.  This file tests every filter dimension, context key, pagination
boundary, and error-surface path that the wave-5 suite intentionally leaves
to a separate focused file.

What IS covered here
---------------------
- StaffListFilterTests       — scan_status, legal_hold, category, date_from,
                                date_to, search filters; total_count and
                                categories context keys; empty-result case.
- StaffDetailViewTests       — can_view_quarantine_details flag (True/False);
                                storage_key deferred from queryset;
                                soft-deleted documents visible to staff.
- LegalHoldViewEdgeCaseTests — GET pre-fill ACTION_RELEASE when hold is active;
                                release-success redirect; service errors:
                                PermissionDenied → 403, ValidationError → 422,
                                unexpected exception → 500; invalid action → 422.
- QuarantineListViewTests    — non-quarantined statuses excluded; 25/page
                                pagination boundary; no-perm → 403.
- AuditLogViewTests          — nonexistent document → 404; newest-first
                                ordering; 50/page pagination; empty entry list;
                                no-perm → 403.

What test_wave5_views.py already covers (NOT duplicated)
---------------------------------------------------------
- Basic 403 for missing view_all_documents / view_quarantined / manage_legal_hold
- Staff list showing all users' docs
- Basic staff detail rendering (status 200)
- Legal hold GET form render (status 200)
- Legal hold POST apply success + redirect
- Basic quarantine list queryset
- Quarantine list: scan_engine_result absent from HTML
- Audit log 200 with entries rendered

User model
----------
CivicOS uses auth_extension.User with email as the unique identifier.
All creation uses ``User.objects.create_user(email=..., password=...)``.
Authentication uses ``self.client.force_login(user)`` to bypass MFA.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.documents.models import Document, DocumentCategory
from apps.documents.forms import LegalHoldForm

User = get_user_model()

# ---------------------------------------------------------------------------
# Module-level counter — unique emails and slugs without hitting the DB
# ---------------------------------------------------------------------------
_CTR: int = 0


def _uid() -> str:
    global _CTR
    _CTR += 1
    return f"{_CTR:06d}"


def _email(prefix: str = "user") -> str:
    return f"{prefix}-{_uid()}@example.com"


def _slug(prefix: str = "cat") -> str:
    return f"{prefix}-{_uid()}"


# ---------------------------------------------------------------------------
# Shared fixture factories
# ---------------------------------------------------------------------------


def make_user(**kwargs) -> User:
    """Create a CivicOS user with a unique email."""
    email = kwargs.pop("email", _email())
    password = kwargs.pop("password", "testpass123!")
    return User.objects.create_user(email=email, password=password, **kwargs)


def make_category(**kwargs) -> DocumentCategory:
    """Create a DocumentCategory with sane defaults."""
    defaults = dict(
        name_en="Test Category",
        name_fr="Catégorie test",
        slug=_slug(),
        allowed_mime_types=["application/pdf"],
        max_size_bytes=0,
        min_retention_days=730,
        max_retention_days=2555,
        staff_only=False,
        is_transitory=False,
    )
    defaults.update(kwargs)
    return DocumentCategory.objects.create(**defaults)


def make_document(user: User, category: DocumentCategory, **kwargs) -> Document:
    """Create a Document with minimal required fields."""
    defaults = dict(
        category=category,
        uploaded_by=user,
        original_filename="test.pdf",
        _storage_key=f"quarantine/documents/{uuid.uuid4()}.bin",
        mime_type="application/pdf",
        size_bytes=1024,
        scan_status=Document.ScanStatus.ACTIVE,
    )
    defaults.update(kwargs)
    return Document.objects.create(**defaults)


def _get_document_ct() -> ContentType:
    return ContentType.objects.get(app_label="documents", model="document")


def _grant_perm(user: User, codename: str) -> User:
    """
    Grant a model-level permission and return a fresh DB instance.

    get_or_create is used so permissions not yet in Meta.permissions are
    created on the fly against the documents | document ContentType.
    Returning a fresh instance clears Django's per-instance permission cache.
    """
    ct = _get_document_ct()
    perm, _ = Permission.objects.get_or_create(
        codename=codename,
        content_type=ct,
        defaults={"name": codename.replace("_", " ").capitalize()},
    )
    user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


# ===========================================================================
# 1. StaffListFilterTests
# ===========================================================================


class StaffListFilterTests(TestCase):
    """
    Verifies every filter dimension of DocumentAdminListView.

    Filter form fields: scan_status, legal_hold, category, search,
    date_from, date_to.  Context keys tested: documents, total_count,
    categories, filter_form.
    """

    def setUp(self) -> None:
        self.client = Client()
        self.user = make_user(email=_email("citizen"))
        self.staff = make_user(email=_email("staff"), is_staff=True)
        self.category = make_category(slug=_slug("main-cat"))
        self.staff = _grant_perm(self.staff, "view_all_documents")
        self.client.force_login(self.staff)
        self.url = reverse("documents:staff-list")

    # ------------------------------------------------------------------
    # 1.1 scan_status filter
    # ------------------------------------------------------------------

    def test_filter_by_scan_status_quarantined(self) -> None:
        """scan_status=QUARANTINED shows only quarantined docs."""
        active_doc = make_document(
            self.user, self.category, scan_status=Document.ScanStatus.ACTIVE
        )
        quarantined_doc = make_document(
            self.user, self.category, scan_status=Document.ScanStatus.QUARANTINED
        )

        response = self.client.get(
            self.url, {"scan_status": Document.ScanStatus.QUARANTINED.value}
        )
        self.assertEqual(response.status_code, 200)
        pks = [str(d.pk) for d in response.context["documents"]]
        self.assertIn(str(quarantined_doc.pk), pks)
        self.assertNotIn(str(active_doc.pk), pks)

    # ------------------------------------------------------------------
    # 1.2 legal_hold filter
    # ------------------------------------------------------------------

    def test_filter_by_legal_hold_yes(self) -> None:
        """legal_hold=yes returns only documents with legal_hold=True."""
        held_doc = make_document(self.user, self.category, legal_hold=True)
        free_doc = make_document(self.user, self.category, legal_hold=False)

        response = self.client.get(self.url, {"legal_hold": "yes"})
        self.assertEqual(response.status_code, 200)
        pks = [str(d.pk) for d in response.context["documents"]]
        self.assertIn(str(held_doc.pk), pks)
        self.assertNotIn(str(free_doc.pk), pks)

    def test_filter_by_legal_hold_no(self) -> None:
        """legal_hold=no returns only documents without a legal hold."""
        held_doc = make_document(self.user, self.category, legal_hold=True)
        free_doc = make_document(self.user, self.category, legal_hold=False)

        response = self.client.get(self.url, {"legal_hold": "no"})
        self.assertEqual(response.status_code, 200)
        pks = [str(d.pk) for d in response.context["documents"]]
        self.assertIn(str(free_doc.pk), pks)
        self.assertNotIn(str(held_doc.pk), pks)

    # ------------------------------------------------------------------
    # 1.3 category filter
    # ------------------------------------------------------------------

    def test_filter_by_category_slug(self) -> None:
        """category filter returns only docs belonging to that category."""
        other_cat = make_category(slug=_slug("other"))
        doc_in_cat = make_document(self.user, self.category)
        doc_other = make_document(self.user, other_cat)

        response = self.client.get(self.url, {"category": self.category.slug})
        self.assertEqual(response.status_code, 200)
        pks = [str(d.pk) for d in response.context["documents"]]
        self.assertIn(str(doc_in_cat.pk), pks)
        self.assertNotIn(str(doc_other.pk), pks)

    # ------------------------------------------------------------------
    # 1.4 date_from / date_to filters
    # ------------------------------------------------------------------

    def test_filter_by_date_from(self) -> None:
        """date_from excludes documents created before that date."""
        # Create a document and back-date it 10 days.
        old_doc = make_document(self.user, self.category)
        Document.objects.filter(pk=old_doc.pk).update(
            created_at=timezone.now() - timedelta(days=10)
        )

        # Create a document with today's timestamp (default created_at).
        recent_doc = make_document(self.user, self.category)

        # Filter from yesterday — should include recent_doc, exclude old_doc.
        yesterday = (timezone.now() - timedelta(days=1)).date()
        response = self.client.get(self.url, {"date_from": yesterday.isoformat()})
        self.assertEqual(response.status_code, 200)
        pks = [str(d.pk) for d in response.context["documents"]]
        self.assertIn(str(recent_doc.pk), pks)
        self.assertNotIn(str(old_doc.pk), pks)

    def test_filter_by_date_to(self) -> None:
        """date_to excludes documents created after that date."""
        # Create a document and back-date it 10 days.
        old_doc = make_document(self.user, self.category)
        Document.objects.filter(pk=old_doc.pk).update(
            created_at=timezone.now() - timedelta(days=10)
        )

        # Create a document with today's timestamp.
        recent_doc = make_document(self.user, self.category)

        # Filter up to 5 days ago — should include old_doc, exclude recent_doc.
        five_days_ago = (timezone.now() - timedelta(days=5)).date()
        response = self.client.get(self.url, {"date_to": five_days_ago.isoformat()})
        self.assertEqual(response.status_code, 200)
        pks = [str(d.pk) for d in response.context["documents"]]
        self.assertIn(str(old_doc.pk), pks)
        self.assertNotIn(str(recent_doc.pk), pks)

    # ------------------------------------------------------------------
    # 1.5 search filter
    # ------------------------------------------------------------------

    def test_search_by_category_slug_fragment(self) -> None:
        """search matches a partial category slug via icontains."""
        cat_alpha = make_category(slug="alpha-cat-001", name_en="Alpha Category")
        cat_beta = make_category(slug="beta-cat-002", name_en="Beta Category")
        doc_alpha = make_document(self.user, cat_alpha)
        doc_beta = make_document(self.user, cat_beta)

        # "alpha" appears in cat_alpha.slug but not cat_beta.slug
        response = self.client.get(self.url, {"search": "alpha"})
        self.assertEqual(response.status_code, 200)
        pks = [str(d.pk) for d in response.context["documents"]]
        self.assertIn(str(doc_alpha.pk), pks)
        self.assertNotIn(str(doc_beta.pk), pks)

    # ------------------------------------------------------------------
    # 1.6 Context key assertions
    # ------------------------------------------------------------------

    def test_total_count_in_context(self) -> None:
        """total_count equals the number of documents matching the current filter."""
        make_document(self.user, self.category)
        make_document(self.user, self.category)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("total_count", response.context)
        # No filter applied — total_count should equal all documents in DB.
        expected = Document.objects.all().count()
        self.assertEqual(response.context["total_count"], expected)

    def test_categories_in_context(self) -> None:
        """All DocumentCategory objects are present in the context dropdown."""
        extra_cat = make_category(slug=_slug("extra"))

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("categories", response.context)
        slugs = [c.slug for c in response.context["categories"]]
        self.assertIn(self.category.slug, slugs)
        self.assertIn(extra_cat.slug, slugs)

    def test_filter_with_no_match_returns_empty_list(self) -> None:
        """A filter that matches no documents returns an empty queryset."""
        # Create only ACTIVE documents — no QUARANTINED ones.
        make_document(self.user, self.category, scan_status=Document.ScanStatus.ACTIVE)

        response = self.client.get(
            self.url, {"scan_status": Document.ScanStatus.QUARANTINED.value}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["documents"]), [])
        self.assertEqual(response.context["total_count"], 0)


# ===========================================================================
# 2. StaffDetailViewTests
# ===========================================================================


class StaffDetailViewTests(TestCase):
    """
    Verifies StaffDocumentDetailView context keys beyond basic rendering.

    Focuses on:
    - can_view_quarantine_details flag (permission-gated)
    - storage_key deferred at ORM level
    - soft-deleted documents visible to staff
    """

    def setUp(self) -> None:
        self.client = Client()
        self.owner = make_user(email=_email("citizen"))
        self.staff = make_user(email=_email("staff"), is_staff=True)
        self.category = make_category()
        self.doc = make_document(
            self.owner,
            self.category,
            scan_status=Document.ScanStatus.QUARANTINED,
            scan_engine_result="Eicar-Test-Signature",
        )
        self.staff = _grant_perm(self.staff, "view_all_documents")
        self.client.force_login(self.staff)

    def _url(self, pk=None) -> str:
        return reverse("documents:staff-detail", args=[pk or self.doc.pk])

    # ------------------------------------------------------------------
    # 2.1 can_view_quarantine_details context key
    # ------------------------------------------------------------------

    def test_can_view_quarantine_details_false_without_perm(self) -> None:
        """Staff without view_quarantined sees can_view_quarantine_details=False."""
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_view_quarantine_details"])

    def test_can_view_quarantine_details_true_with_perm(self) -> None:
        """Staff with view_quarantined sees can_view_quarantine_details=True."""
        self.staff = _grant_perm(self.staff, "view_quarantined")
        # Re-login to ensure the fresh user instance (cleared perm cache) is used.
        self.client.force_login(self.staff)

        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_view_quarantine_details"])

    # ------------------------------------------------------------------
    # 2.2 storage_key deferred from queryset
    # ------------------------------------------------------------------

    def test_storage_key_absent_from_queryset(self) -> None:
        """
        The view calls .defer('_storage_key'), so _storage_key must not be
        loaded into the document's __dict__ at template render time.
        """
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        doc_in_ctx = response.context["document"]
        # A deferred field is absent from __dict__ until explicitly accessed.
        self.assertNotIn("_storage_key", doc_in_ctx.__dict__)

    # ------------------------------------------------------------------
    # 2.3 Soft-deleted documents visible to staff
    # ------------------------------------------------------------------

    def test_soft_deleted_document_accessible_to_staff(self) -> None:
        """
        StaffDocumentDetailView has no deleted_at filter — staff can review
        soft-deleted documents.  Citizens receive 404 for the same doc.
        """
        soft_deleted = make_document(
            self.owner, self.category, deleted_at=timezone.now()
        )
        response = self.client.get(self._url(pk=soft_deleted.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            str(response.context["document"].pk), str(soft_deleted.pk)
        )


# ===========================================================================
# 3. LegalHoldViewEdgeCaseTests
# ===========================================================================


class LegalHoldViewEdgeCaseTests(TestCase):
    """
    Edge cases for DocumentLegalHoldView not covered in test_wave5_views.py.

    Covers:
    - GET with legal_hold=True → ACTION_RELEASE pre-filled
    - POST release success → redirect
    - PermissionDenied from service → 403
    - ValidationError from service → 422
    - Unexpected exception → 500
    - Invalid action value (fails ChoiceField) → 422
    """

    def setUp(self) -> None:
        self.client = Client()
        self.owner = make_user(email=_email("citizen"))
        self.staff = make_user(email=_email("staff"), is_staff=True)
        self.category = make_category()
        self.doc = make_document(self.owner, self.category)
        self.staff = _grant_perm(self.staff, "manage_legal_hold")
        self.client.force_login(self.staff)

    def _url(self) -> str:
        return reverse("documents:legal-hold", args=[self.doc.pk])

    def _valid_post(self, action: str = "apply", reason: str | None = None) -> dict:
        """Return valid POST data for the LegalHoldForm."""
        return {
            "action": action,
            "reason": reason or "Legal hold required for litigation proceedings.",
            "confirm_action": True,
        }

    # ------------------------------------------------------------------
    # 3.1 GET: ACTION_RELEASE pre-filled when hold is already active
    # ------------------------------------------------------------------

    def test_get_shows_release_action_when_hold_active(self) -> None:
        """
        When the document already has legal_hold=True, the GET form initial
        action should be ACTION_RELEASE so the staff member can release it.
        """
        self.doc.legal_hold = True
        self.doc.save(update_fields=["legal_hold", "updated_at"])

        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.initial.get("action"), LegalHoldForm.ACTION_RELEASE)

    # ------------------------------------------------------------------
    # 3.2 POST release success → redirect to staff-detail
    # ------------------------------------------------------------------

    def test_post_release_success_redirects_to_staff_detail(self) -> None:
        """A successful POST release action redirects to staff-detail."""
        self.doc.legal_hold = True
        self.doc.save(update_fields=["legal_hold", "updated_at"])

        with patch("apps.documents.views.staff.release_legal_hold") as mock_release:
            mock_release.return_value = self.doc
            response = self.client.post(
                self._url(), data=self._valid_post(action="release")
            )

        mock_release.assert_called_once()
        self.assertRedirects(
            response,
            reverse("documents:staff-detail", args=[self.doc.pk]),
            fetch_redirect_response=False,
        )

    # ------------------------------------------------------------------
    # 3.3 Service raises PermissionDenied → 403
    # ------------------------------------------------------------------

    def test_post_permission_denied_from_service_returns_403(self) -> None:
        """
        When the service layer raises PermissionDenied (e.g. a secondary
        permission check fails), the view returns HTTP 403.
        """
        with patch("apps.documents.views.staff.apply_legal_hold") as mock_apply:
            mock_apply.side_effect = PermissionDenied(
                "Secondary permission check failed."
            )
            response = self.client.post(
                self._url(), data=self._valid_post(action="apply")
            )

        self.assertEqual(response.status_code, 403)

    # ------------------------------------------------------------------
    # 3.4 Service raises ValidationError → 422
    # ------------------------------------------------------------------

    def test_post_validation_error_from_service_returns_422(self) -> None:
        """
        When the service raises ValidationError (e.g. hold already active),
        the view re-renders the form with HTTP 422.
        """
        with patch("apps.documents.views.staff.apply_legal_hold") as mock_apply:
            mock_apply.side_effect = ValidationError(
                "Document is already on legal hold."
            )
            response = self.client.post(
                self._url(), data=self._valid_post(action="apply")
            )

        self.assertEqual(response.status_code, 422)

    # ------------------------------------------------------------------
    # 3.5 Unexpected exception → 500
    # ------------------------------------------------------------------

    def test_post_unexpected_error_returns_500(self) -> None:
        """
        An unhandled exception from the service layer bubbles up to the
        generic handler, which returns HTTP 500 with an error message.
        """
        with patch("apps.documents.views.staff.apply_legal_hold") as mock_apply:
            mock_apply.side_effect = RuntimeError("Unexpected database error")
            response = self.client.post(
                self._url(), data=self._valid_post(action="apply")
            )

        self.assertEqual(response.status_code, 500)

    # ------------------------------------------------------------------
    # 3.6 Invalid action value → form invalid → 422
    # ------------------------------------------------------------------

    def test_post_invalid_action_returns_422(self) -> None:
        """
        An action value not in LegalHoldForm.ACTION_CHOICES fails ChoiceField
        validation, making the form invalid → view returns 422.
        """
        response = self.client.post(
            self._url(),
            data={
                "action": "delete",  # not a valid choice
                "reason": "Attempting invalid action.",
                "confirm_action": True,
            },
        )
        self.assertEqual(response.status_code, 422)


# ===========================================================================
# 4. QuarantineListViewTests
# ===========================================================================


class QuarantineListViewTests(TestCase):
    """
    Edge cases for DocumentQuarantineListView.

    Covers:
    - Only QUARANTINED documents appear (ACTIVE/SCANNING excluded)
    - 25-per-page pagination boundary
    - Authenticated user lacking view_quarantined → 403
    """

    def setUp(self) -> None:
        self.client = Client()
        self.owner = make_user(email=_email("citizen"))
        self.staff = make_user(email=_email("staff"), is_staff=True)
        self.category = make_category()
        self.staff = _grant_perm(self.staff, "view_quarantined")
        self.client.force_login(self.staff)
        self.url = reverse("documents:quarantine-list")

    # ------------------------------------------------------------------
    # 4.1 Only QUARANTINED docs shown
    # ------------------------------------------------------------------

    def test_only_quarantined_documents_shown(self) -> None:
        """ACTIVE and SCANNING documents must not appear in the quarantine list."""
        active_doc = make_document(
            self.owner, self.category, scan_status=Document.ScanStatus.ACTIVE
        )
        scanning_doc = make_document(
            self.owner, self.category, scan_status=Document.ScanStatus.SCANNING
        )
        quarantined_doc = make_document(
            self.owner, self.category, scan_status=Document.ScanStatus.QUARANTINED
        )

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        pks = [str(d.pk) for d in response.context["documents"]]
        self.assertIn(str(quarantined_doc.pk), pks)
        self.assertNotIn(str(active_doc.pk), pks)
        self.assertNotIn(str(scanning_doc.pk), pks)

    # ------------------------------------------------------------------
    # 4.2 Pagination: 25 per page
    # ------------------------------------------------------------------

    def test_pagination_25_per_page(self) -> None:
        """With 26 quarantined documents, page 1 shows exactly 25."""
        for _ in range(26):
            make_document(
                self.owner,
                self.category,
                scan_status=Document.ScanStatus.QUARANTINED,
            )

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        # ListView puts the page's object list into the context_object_name key.
        docs_on_page = list(response.context["documents"])
        self.assertEqual(len(docs_on_page), 25)
        self.assertTrue(response.context.get("is_paginated", False))

    # ------------------------------------------------------------------
    # 4.3 Missing permission → 403
    # ------------------------------------------------------------------

    def test_authenticated_without_perm_returns_403(self) -> None:
        """
        An authenticated user who lacks view_quarantined receives HTTP 403
        (raise_exception=True on the view — no redirect to login).
        """
        no_perm_user = make_user(email=_email("noperm"))
        self.client.force_login(no_perm_user)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)


# ===========================================================================
# 5. AuditLogViewTests
# ===========================================================================


class AuditLogViewTests(TestCase):
    """
    Edge cases for DocumentAuditLogView.

    Covers:
    - Nonexistent document PK → 404
    - Entries ordered newest-first (descending timestamp)
    - 50-per-page pagination boundary
    - No entries → empty list (not an error)
    - Missing view_all_documents permission → 403
    """

    def setUp(self) -> None:
        self.client = Client()
        self.owner = make_user(email=_email("citizen"))
        self.staff = make_user(email=_email("staff"), is_staff=True)
        self.category = make_category()
        self.doc = make_document(self.owner, self.category)
        self.staff = _grant_perm(self.staff, "view_all_documents")
        self.client.force_login(self.staff)

    def _url(self, pk=None) -> str:
        return reverse("documents:audit-log", args=[pk or self.doc.pk])

    # ------------------------------------------------------------------
    # 5.1 Nonexistent document → 404
    # ------------------------------------------------------------------

    def test_nonexistent_document_returns_404(self) -> None:
        """
        A request for the audit log of a document PK that does not exist
        in the database must return HTTP 404.
        """
        nonexistent_pk = uuid.uuid4()
        response = self.client.get(self._url(pk=nonexistent_pk))
        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------
    # 5.2 Newest-first ordering
    # ------------------------------------------------------------------

    def test_entries_ordered_newest_first(self) -> None:
        """
        Audit entries for a document are returned newest-first
        (order_by('-timestamp')).  The most recent entry must appear first
        in the audit_entries context list.
        """
        from apps.audit.models import AuditEventType, AuditLogEntry

        # Create first entry (older).
        entry_old = AuditLogEntry.objects.create(
            event_type=AuditEventType.RECORD_VIEWED,
            actor_id=str(self.staff.pk),
            resource_type="documents.Document",
            resource_id=str(self.doc.pk),
            event_detail={"sequence": "old"},
        )
        # Back-date the older entry using queryset.update() which bypasses
        # AuditLogEntry.save()'s immutability guard (intentional in tests).
        AuditLogEntry.objects.filter(pk=entry_old.pk).update(
            timestamp=timezone.now() - timedelta(hours=2)
        )

        # Create second entry (newer — auto_now_add gives current time).
        entry_new = AuditLogEntry.objects.create(
            event_type=AuditEventType.RECORD_VIEWED,
            actor_id=str(self.staff.pk),
            resource_type="documents.Document",
            resource_id=str(self.doc.pk),
            event_detail={"sequence": "new"},
        )

        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        entries = list(response.context["audit_entries"])
        self.assertEqual(len(entries), 2)
        # Newest first: entry_new should precede entry_old.
        self.assertEqual(entries[0].pk, entry_new.pk)
        self.assertEqual(entries[1].pk, entry_old.pk)

    # ------------------------------------------------------------------
    # 5.3 Pagination: 50 per page
    # ------------------------------------------------------------------

    def test_pagination_50_per_page(self) -> None:
        """With 51 audit entries, page 1 shows exactly 50."""
        from apps.audit.models import AuditEventType, AuditLogEntry

        for i in range(51):
            AuditLogEntry.objects.create(
                event_type=AuditEventType.RECORD_VIEWED,
                actor_id=str(self.staff.pk),
                resource_type="documents.Document",
                resource_id=str(self.doc.pk),
                event_detail={"index": i},
            )

        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        # audit_entries = page_obj.object_list (first page capped at 50).
        audit_entries = list(response.context["audit_entries"])
        self.assertEqual(len(audit_entries), 50)
        self.assertTrue(response.context.get("is_paginated", False))

    # ------------------------------------------------------------------
    # 5.4 No entries → empty list (not an error)
    # ------------------------------------------------------------------

    def test_no_entries_shows_empty_list(self) -> None:
        """
        When no AuditLogEntry rows exist for the document, the view must
        return HTTP 200 with an empty audit_entries list — not an error.
        """
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["audit_entries"]), [])

    # ------------------------------------------------------------------
    # 5.5 Missing permission → 403
    # ------------------------------------------------------------------

    def test_authenticated_without_perm_returns_403(self) -> None:
        """
        An authenticated user lacking view_all_documents receives HTTP 403
        (raise_exception=True — never redirect to login for authenticated users).
        """
        no_perm_user = make_user(email=_email("noperm"))
        self.client.force_login(no_perm_user)

        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 403)
