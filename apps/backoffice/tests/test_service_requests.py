"""Tests for backoffice service request management views."""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.portal.models import ServiceRequest, ServiceRequestStatus

User = get_user_model()

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

VALID_PASSWORD = "SecureTestPass123!"


def _make_staff(email: str) -> User:
    """Create an active staff user."""
    return User.objects.create_user(
        email=email,
        password=VALID_PASSWORD,
        is_staff=True,
        is_active=True,
    )


def _make_citizen(email: str) -> User:
    """Create an active non-staff citizen user."""
    return User.objects.create_user(
        email=email,
        password=VALID_PASSWORD,
        is_staff=False,
        is_active=True,
    )


def _make_sr(
    citizen: User,
    ref: str,
    status: str = ServiceRequestStatus.SUBMITTED,
) -> ServiceRequest:
    """
    Create a ServiceRequest bypassing the service layer — for test fixture setup only.
    Uses create() with an explicit reference_number so tests are deterministic.
    """
    sr = ServiceRequest(
        citizen=citizen,
        service_name="Test Service",
        status=status,
        submission_data={"name": "Test Citizen", "id_number": "A12345"},
        internal_notes="",
    )
    # Override the auto-generated reference_number with the supplied value
    sr.reference_number = ref
    sr.save()
    return sr


# ---------------------------------------------------------------------------
# List view tests
# ---------------------------------------------------------------------------


class ServiceRequestListTests(TestCase):
    """Tests for ServiceRequestListView (GET /backoffice/service-requests/)."""

    def setUp(self):
        self.staff = _make_staff("staff@gov.example")
        self.citizen = _make_citizen("citizen@example.com")

        self.sr1 = _make_sr(self.citizen, "GS-TEST-001", ServiceRequestStatus.SUBMITTED)
        self.sr2 = _make_sr(self.citizen, "GS-TEST-002", ServiceRequestStatus.IN_REVIEW)
        self.sr3 = _make_sr(self.citizen, "GS-TEST-003", ServiceRequestStatus.APPROVED)

        self.list_url = reverse("backoffice:sr-list")

    def test_unauthenticated_redirects(self):
        """Unauthenticated users are redirected to login (302)."""
        response = self.client.get(self.list_url)
        self.assertRedirects(response, "/account/login/?next=/backoffice/service-requests/")

    def test_list_requires_staff(self):
        """Authenticated non-staff citizens receive HTTP 403."""
        self.client.force_login(self.citizen)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 403)

    def test_staff_sees_all_requests(self):
        """Staff can access the list and see all three service requests."""
        self.client.force_login(self.staff)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        # All three reference numbers appear in the response
        self.assertContains(response, "GS-TEST-001")
        self.assertContains(response, "GS-TEST-002")
        self.assertContains(response, "GS-TEST-003")

    def test_status_filter_narrows_results(self):
        """Filtering by ?status=submitted returns only submitted requests."""
        self.client.force_login(self.staff)
        response = self.client.get(self.list_url, {"status": "submitted"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "GS-TEST-001")
        self.assertNotContains(response, "GS-TEST-002")
        self.assertNotContains(response, "GS-TEST-003")

    def test_search_by_reference_number(self):
        """Searching by ?q=GS-TEST-001 finds only the matching request."""
        self.client.force_login(self.staff)
        response = self.client.get(self.list_url, {"q": "GS-TEST-001"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "GS-TEST-001")
        self.assertNotContains(response, "GS-TEST-002")

    def test_search_by_citizen_email(self):
        """Searching by citizen email returns their requests."""
        self.client.force_login(self.staff)
        response = self.client.get(self.list_url, {"q": "citizen@example"})
        self.assertEqual(response.status_code, 200)
        # All three belong to this citizen
        self.assertContains(response, "GS-TEST-001")

    def test_invalid_status_filter_ignored(self):
        """An invalid status value is ignored and all requests are shown."""
        self.client.force_login(self.staff)
        response = self.client.get(self.list_url, {"status": "INVALID_VALUE"})
        self.assertEqual(response.status_code, 200)
        # All three should appear
        self.assertContains(response, "GS-TEST-001")

    def test_pagination_present(self):
        """Creating 30 requests triggers pagination in context."""
        self.client.force_login(self.staff)
        # Create enough to exceed paginate_by=25 (3 already exist)
        for i in range(4, 31):
            _make_sr(self.citizen, f"GS-PAGE-{i:03d}")

        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        # Paginator should be present in context
        self.assertTrue(response.context["is_paginated"])
        self.assertIsNotNone(response.context["paginator"])
        # First page has 25 results
        self.assertEqual(len(response.context["service_requests"]), 25)

    def test_context_contains_status_choices(self):
        """The list context includes status_choices for the filter dropdown."""
        self.client.force_login(self.staff)
        response = self.client.get(self.list_url)
        self.assertIn("status_choices", response.context)
        self.assertEqual(response.context["status_choices"], ServiceRequestStatus.choices)

    def test_empty_state_shown_when_no_results(self):
        """When filters produce no results, an empty state message is shown."""
        self.client.force_login(self.staff)
        response = self.client.get(self.list_url, {"q": "NONEXISTENT-REF-9999"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No service requests match")


# ---------------------------------------------------------------------------
# Detail view tests
# ---------------------------------------------------------------------------


class ServiceRequestDetailTests(TestCase):
    """Tests for ServiceRequestDetailView (GET /backoffice/service-requests/<ref>/)."""

    def setUp(self):
        self.staff = _make_staff("staff@gov.example")
        self.citizen = _make_citizen("citizen@example.com")
        self.sr = _make_sr(self.citizen, "GS-DETAIL-001")
        self.detail_url = reverse("backoffice:sr-detail", args=["GS-DETAIL-001"])

    def test_detail_requires_staff(self):
        """Non-staff citizen receives HTTP 403 on the detail view."""
        self.client.force_login(self.citizen)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_redirects(self):
        """Unauthenticated user is redirected to login."""
        response = self.client.get(self.detail_url)
        self.assertRedirects(response, f"/account/login/?next={self.detail_url}")

    def test_staff_can_view_detail(self):
        """Staff can access the detail page and see the reference number."""
        self.client.force_login(self.staff)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "GS-DETAIL-001")

    def test_submission_data_rendered(self):
        """Submission data keys and values appear in the detail page."""
        self.client.force_login(self.staff)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        # The key "name" should appear (title-cased as "Name") in the rendered page
        self.assertContains(response, "Name")
        # The value "Test Citizen" should also appear
        self.assertContains(response, "Test Citizen")

    def test_nonexistent_reference_returns_404(self):
        """A reference number that does not exist returns HTTP 404."""
        self.client.force_login(self.staff)
        url = reverse("backoffice:sr-detail", args=["GS-NONEXISTENT-9999"])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_context_contains_forms(self):
        """Detail context includes both action forms."""
        self.client.force_login(self.staff)
        response = self.client.get(self.detail_url)
        self.assertIn("status_form", response.context)
        self.assertIn("notes_form", response.context)

    def test_context_contains_citizen_request_count(self):
        """Detail context includes the citizen's total request count."""
        # Create a second SR for the same citizen
        _make_sr(self.citizen, "GS-DETAIL-002")
        self.client.force_login(self.staff)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.context["citizen_requests_count"], 2)

    def test_terminal_status_hides_status_form(self):
        """When request is approved (terminal), status update form is not shown."""
        self.sr.status = ServiceRequestStatus.APPROVED
        self.sr.save(update_fields=["status"])
        self.client.force_login(self.staff)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "terminal state")


# ---------------------------------------------------------------------------
# Status update view tests
# ---------------------------------------------------------------------------


class ServiceRequestStatusTests(TestCase):
    """Tests for ServiceRequestStatusView (POST /backoffice/service-requests/<ref>/status/)."""

    def setUp(self):
        self.staff = _make_staff("staff@gov.example")
        self.citizen = _make_citizen("citizen@example.com")
        self.sr = _make_sr(self.citizen, "GS-STATUS-001", ServiceRequestStatus.SUBMITTED)
        self.status_url = reverse("backoffice:sr-status", args=["GS-STATUS-001"])

    def test_status_update_succeeds(self):
        """Staff POST with valid new_status transitions the request and redirects."""
        self.client.force_login(self.staff)
        response = self.client.post(
            self.status_url,
            {"new_status": ServiceRequestStatus.IN_REVIEW, "public_note": "Now in review."},
        )
        # POST-redirect-GET: expect redirect to detail
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(
            response,
            reverse("backoffice:sr-detail", args=["GS-STATUS-001"]),
        )
        # Confirm DB state changed
        self.sr.refresh_from_db()
        self.assertEqual(self.sr.status, ServiceRequestStatus.IN_REVIEW)

    def test_invalid_status_shows_error(self):
        """Posting an invalid status value redirects back with an error message."""
        self.client.force_login(self.staff)
        response = self.client.post(
            self.status_url,
            {"new_status": "not_a_real_status"},
        )
        # Still redirects (POST-redirect-GET always)
        self.assertEqual(response.status_code, 302)
        # Status must NOT have changed
        self.sr.refresh_from_db()
        self.assertEqual(self.sr.status, ServiceRequestStatus.SUBMITTED)

    def test_citizen_cannot_update_status(self):
        """Non-staff citizen attempting a status update receives HTTP 403."""
        self.client.force_login(self.citizen)
        response = self.client.post(
            self.status_url,
            {"new_status": ServiceRequestStatus.IN_REVIEW},
        )
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_cannot_update_status(self):
        """Unauthenticated POST to the status endpoint redirects to login."""
        response = self.client.post(
            self.status_url,
            {"new_status": ServiceRequestStatus.IN_REVIEW},
        )
        self.assertRedirects(response, f"/account/login/?next={self.status_url}")

    def test_csrf_required(self):
        """POST without CSRF token returns HTTP 403 when CSRF enforcement is on."""
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        response = csrf_client.post(
            self.status_url,
            {"new_status": ServiceRequestStatus.IN_REVIEW},
        )
        self.assertEqual(response.status_code, 403)

    def test_get_method_not_allowed(self):
        """GET request to the status endpoint returns HTTP 405 Method Not Allowed."""
        self.client.force_login(self.staff)
        response = self.client.get(self.status_url)
        self.assertEqual(response.status_code, 405)

    def test_nonexistent_reference_returns_404(self):
        """POSTing to a non-existent reference number returns 404."""
        self.client.force_login(self.staff)
        url = reverse("backoffice:sr-status", args=["GS-GHOST-0000"])
        response = self.client.post(url, {"new_status": ServiceRequestStatus.IN_REVIEW})
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# Internal notes view tests
# ---------------------------------------------------------------------------


class ServiceRequestNotesTests(TestCase):
    """Tests for ServiceRequestNotesView (POST /backoffice/service-requests/<ref>/notes/)."""

    def setUp(self):
        self.staff = _make_staff("staff@gov.example")
        self.citizen = _make_citizen("citizen@example.com")
        self.sr = _make_sr(self.citizen, "GS-NOTES-001")
        self.notes_url = reverse("backoffice:sr-notes", args=["GS-NOTES-001"])

    def test_unauthenticated_redirects(self):
        """Unauthenticated POST to the notes endpoint redirects to login."""
        response = self.client.post(self.notes_url, {"internal_notes": "test"})
        self.assertRedirects(response, f"/account/login/?next={self.notes_url}")

    def test_notes_update_succeeds(self):
        """Staff can update internal notes and are redirected to detail."""
        self.client.force_login(self.staff)
        response = self.client.post(
            self.notes_url,
            {"internal_notes": "Staff note: requires additional verification."},
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(
            response,
            reverse("backoffice:sr-detail", args=["GS-NOTES-001"]),
        )
        self.sr.refresh_from_db()
        self.assertEqual(
            self.sr.internal_notes,
            "Staff note: requires additional verification.",
        )

    def test_notes_can_be_cleared(self):
        """Posting an empty internal_notes value clears existing notes."""
        self.sr.internal_notes = "Old note"
        self.sr.save(update_fields=["internal_notes"])

        self.client.force_login(self.staff)
        self.client.post(self.notes_url, {"internal_notes": ""})

        self.sr.refresh_from_db()
        self.assertEqual(self.sr.internal_notes, "")

    def test_citizen_cannot_update_notes(self):
        """Non-staff citizen receives HTTP 403 when trying to update notes."""
        self.client.force_login(self.citizen)
        response = self.client.post(
            self.notes_url,
            {"internal_notes": "Citizen trying to write staff notes"},
        )
        self.assertEqual(response.status_code, 403)
        # Notes must not have changed
        self.sr.refresh_from_db()
        self.assertEqual(self.sr.internal_notes, "")

    def test_csrf_required(self):
        """POST without CSRF token returns HTTP 403."""
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        response = csrf_client.post(
            self.notes_url,
            {"internal_notes": "Attempting without CSRF"},
        )
        self.assertEqual(response.status_code, 403)

    def test_get_method_not_allowed(self):
        """GET request to the notes endpoint returns HTTP 405."""
        self.client.force_login(self.staff)
        response = self.client.get(self.notes_url)
        self.assertEqual(response.status_code, 405)

    def test_notes_do_not_appear_in_citizen_portal(self):
        """
        Verifies the notes are stored in internal_notes only, not in submission_data
        or any other citizen-visible field.

        This is a structural test: internal_notes is the only field updated,
        and the portal views (not tested here) must never expose it.
        """
        self.client.force_login(self.staff)
        self.client.post(
            self.notes_url,
            {"internal_notes": "SENSITIVE: do not show to citizen"},
        )
        self.sr.refresh_from_db()
        # Note is stored in internal_notes
        self.assertIn("SENSITIVE", self.sr.internal_notes)
        # Note is NOT in submission_data (citizen-visible JSON field)
        self.assertNotIn("SENSITIVE", str(self.sr.submission_data))

    def test_nonexistent_reference_returns_404(self):
        """POSTing to a non-existent reference number returns 404."""
        self.client.force_login(self.staff)
        url = reverse("backoffice:sr-notes", args=["GS-GHOST-9999"])
        response = self.client.post(url, {"internal_notes": "Hello"})
        self.assertEqual(response.status_code, 404)
