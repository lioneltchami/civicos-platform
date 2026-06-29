"""
Tests for the back-office dashboard view.

Covers access control (unauthenticated, non-staff citizen, staff) and
basic content rendering with real database objects.

All test users are created with is_staff=False (citizen) or is_staff=True
(staff) and are disposed of after each test by Django's TestCase rollback.
"""

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType

from apps.portal.models import ServiceRequest, ServiceRequestStatus
from apps.workflows.models import WorkItem, WorkItemStatus, WorkItemPriority

User = get_user_model()

DASHBOARD_URL = "/backoffice/"


def _dashboard_url():
    """Return the dashboard URL via reverse, falling back to the hardcoded path."""
    try:
        return reverse("backoffice:dashboard")
    except Exception:
        return DASHBOARD_URL


class DashboardAccessTests(TestCase):
    """Access control: unauthenticated, citizen, and staff scenarios."""

    def setUp(self):
        self.url = _dashboard_url()

        # Citizen (non-staff) user
        self.citizen = User.objects.create_user(
            email="citizen@example.com",
            password="SecurePass123!",
            is_staff=False,
        )

        # Staff user
        self.staff = User.objects.create_user(
            email="staff@example.com",
            password="SecurePass123!",
            is_staff=True,
        )

    # ------------------------------------------------------------------
    # Unauthenticated
    # ------------------------------------------------------------------

    def test_unauthenticated_redirects_to_login(self):
        """
        An anonymous GET to /backoffice/ must redirect to the login page.
        The exact target URL includes the ``next`` parameter pointing back
        to the dashboard.
        """
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    # ------------------------------------------------------------------
    # Authenticated — not staff
    # ------------------------------------------------------------------

    def test_citizen_gets_403(self):
        """
        An authenticated citizen (is_staff=False) must receive HTTP 403.
        They should never see back-office content.
        """
        logged_in = self.client.login(
            email="citizen@example.com", password="SecurePass123!"
        )
        self.assertTrue(logged_in, "Citizen login failed — check auth backend config.")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    # ------------------------------------------------------------------
    # Authenticated — staff
    # ------------------------------------------------------------------

    def test_staff_gets_200(self):
        """
        A staff user (is_staff=True) must receive HTTP 200 and see the
        word 'Dashboard' somewhere in the rendered HTML.
        """
        logged_in = self.client.login(
            email="staff@example.com", password="SecurePass123!"
        )
        self.assertTrue(logged_in, "Staff login failed — check auth backend config.")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dashboard")

    # ------------------------------------------------------------------
    # Content rendering: service requests
    # ------------------------------------------------------------------

    def test_dashboard_shows_sr_count(self):
        """
        When two ServiceRequests exist, the dashboard must render a count
        of at least 2 in the service request stats section.
        """
        ServiceRequest.objects.create(
            citizen=self.citizen,
            service_name="Passport Application",
            reference_number="GS-TEST-001",
            status=ServiceRequestStatus.SUBMITTED,
            submission_data={},
        )
        ServiceRequest.objects.create(
            citizen=self.citizen,
            service_name="Birth Certificate",
            reference_number="GS-TEST-002",
            status=ServiceRequestStatus.IN_REVIEW,
            submission_data={},
        )

        self.client.login(email="staff@example.com", password="SecurePass123!")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        # sr_total must be at least 2 (could be more if other tests ran first,
        # but TestCase wraps each test in a transaction so counts are isolated)
        self.assertGreaterEqual(response.context["sr_total"], 2)

    # ------------------------------------------------------------------
    # Content rendering: work items
    # ------------------------------------------------------------------

    def test_dashboard_shows_work_item_stats(self):
        """
        When at least one open WorkItem exists the wi_open context variable
        must be ≥ 1, and the dashboard must render without error.
        """
        ct = ContentType.objects.get_for_model(User)
        WorkItem.objects.create(
            title="Review passport application GS-TEST-003",
            status=WorkItemStatus.PENDING,
            priority=WorkItemPriority.NORMAL,
            assigned_to=None,
            content_type=ct,
            object_id="1",
        )

        self.client.login(email="staff@example.com", password="SecurePass123!")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.context["wi_open"], 1)

    # ------------------------------------------------------------------
    # Context variables present
    # ------------------------------------------------------------------

    def test_dashboard_context_contains_all_stat_keys(self):
        """
        The dashboard context must expose all expected stat keys so that
        the template can render without raising a KeyError or silent failure.
        """
        self.client.login(email="staff@example.com", password="SecurePass123!")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        expected_keys = [
            "sr_total",
            "sr_submitted",
            "sr_in_review",
            "sr_awaiting",
            "sr_approved_today",
            "wi_open",
            "wi_overdue",
            "wi_unassigned",
            "wi_mine",
            "citizen_count",
            "recent_audit",
        ]
        for key in expected_keys:
            self.assertIn(
                key,
                response.context,
                msg=f"Context key '{key}' missing from dashboard response.",
            )
