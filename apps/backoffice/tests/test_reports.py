"""Tests for backoffice reports view."""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.notifications.models import Notification, NotificationChannel, NotificationStatus
from apps.portal.models import ServiceRequest, ServiceRequestStatus

User = get_user_model()

REPORTS_URL = reverse("backoffice:reports")


class ReportsViewTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            email="staff@example.com",
            password="testpass123",
            is_staff=True,
        )
        self.citizen_user = User.objects.create_user(
            email="citizen@example.com",
            password="testpass123",
            is_staff=False,
        )
        self.client = Client()

    # ------------------------------------------------------------------
    # Access control
    # ------------------------------------------------------------------

    def test_requires_staff_unauthenticated(self):
        """Unauthenticated requests are redirected to the login page."""
        response = self.client.get(REPORTS_URL)
        self.assertRedirects(response, "/account/login/?next=/backoffice/reports/")

    def test_requires_staff_citizen_gets_403(self):
        """Authenticated non-staff users receive HTTP 403."""
        self.client.login(email="citizen@example.com", password="testpass123")
        response = self.client.get(REPORTS_URL)
        self.assertEqual(response.status_code, 403)

    def test_requires_staff(self):
        """Staff users can access the reports page."""
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(REPORTS_URL)
        self.assertEqual(response.status_code, 200)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def test_staff_gets_200(self):
        """Reports page renders without error even with no data."""
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(REPORTS_URL)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "backoffice/reports/overview.html")

    # ------------------------------------------------------------------
    # Context — service requests
    # ------------------------------------------------------------------

    def test_sr_by_status_in_context(self):
        """sr_by_status aggregation appears in context after creating ServiceRequests."""
        ServiceRequest.objects.create(
            citizen=self.citizen_user,
            service_name="Test Service",
            status=ServiceRequestStatus.SUBMITTED,
        )
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(REPORTS_URL)
        self.assertIn("sr_by_status", response.context)
        statuses = [row["status"] for row in response.context["sr_by_status"]]
        self.assertIn(ServiceRequestStatus.SUBMITTED, statuses)

    def test_sr_by_service_in_context(self):
        """sr_by_service top-10 list appears in context."""
        ServiceRequest.objects.create(
            citizen=self.citizen_user,
            service_name="My Service",
            status=ServiceRequestStatus.SUBMITTED,
        )
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(REPORTS_URL)
        self.assertIn("sr_by_service", response.context)

    def test_sr_total_in_context(self):
        """sr_total is present and matches created service requests."""
        ServiceRequest.objects.create(
            citizen=self.citizen_user,
            service_name="Test",
            status=ServiceRequestStatus.SUBMITTED,
        )
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(REPORTS_URL)
        self.assertIn("sr_total", response.context)
        self.assertGreaterEqual(response.context["sr_total"], 1)

    # ------------------------------------------------------------------
    # Context — notifications
    # ------------------------------------------------------------------

    def test_notif_stats_in_context(self):
        """notif_by_channel and notif_by_status appear in context."""
        Notification.objects.create(
            recipient=self.citizen_user,
            channel=NotificationChannel.EMAIL,
            subject="Hello",
            body="Test body",
            status=NotificationStatus.SENT,
        )
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(REPORTS_URL)
        self.assertIn("notif_by_channel", response.context)
        self.assertIn("notif_by_status", response.context)

    def test_notif_by_channel_counts_correctly(self):
        """notif_by_channel reflects the correct channel distribution."""
        Notification.objects.create(
            recipient=self.citizen_user,
            channel=NotificationChannel.EMAIL,
            subject="E",
            body="Body",
            status=NotificationStatus.SENT,
        )
        Notification.objects.create(
            recipient=self.citizen_user,
            channel=NotificationChannel.SMS,
            subject="S",
            body="Body",
            status=NotificationStatus.SENT,
        )
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(REPORTS_URL)
        by_channel = {
            row["channel"]: row["count"]
            for row in response.context["notif_by_channel"]
        }
        self.assertEqual(by_channel.get(NotificationChannel.EMAIL, 0), 1)
        self.assertEqual(by_channel.get(NotificationChannel.SMS, 0), 1)

    # ------------------------------------------------------------------
    # Context — metadata
    # ------------------------------------------------------------------

    def test_generated_at_in_context(self):
        """generated_at timestamp is present in context."""
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(REPORTS_URL)
        self.assertIn("generated_at", response.context)
        self.assertIsNotNone(response.context["generated_at"])

    def test_sla_breaches_in_context(self):
        """sla_breaches list is present (may be empty if no breached work items)."""
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(REPORTS_URL)
        self.assertIn("sla_breaches", response.context)

    def test_avg_completion_in_context(self):
        """avg_completion key is present (may be None if no completed work items)."""
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(REPORTS_URL)
        self.assertIn("avg_completion", response.context)
