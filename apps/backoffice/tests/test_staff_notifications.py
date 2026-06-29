"""Tests for backoffice staff notification views."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.notifications.models import Notification, NotificationChannel, NotificationStatus

User = get_user_model()

VALID_PASSWORD = "SecurePass123!"


class StaffNotificationListTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email="staff@example.com",
            password=VALID_PASSWORD,
            is_staff=True,
        )
        self.citizen = User.objects.create_user(
            email="citizen@example.com",
            password=VALID_PASSWORD,
            is_staff=False,
        )
        self.list_url = "/backoffice/notifications/"
        self.send_url = "/backoffice/notifications/send/"

    def test_unauthenticated_redirects(self):
        """Unauthenticated GET to list view redirects to login."""
        response = self.client.get(self.list_url)
        self.assertRedirects(
            response,
            "/account/login/?next=/backoffice/notifications/",
        )

    def test_citizen_gets_403(self):
        """Authenticated non-staff citizen receives HTTP 403."""
        self.client.force_login(self.citizen)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 403)

    def test_staff_can_view_list(self):
        """Staff user can access the notifications list (200)."""
        self.client.force_login(self.staff)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)

    def test_filter_by_channel(self):
        """Filtering by ?channel=email shows notifications with that channel."""
        notif = Notification.objects.create(
            recipient=self.citizen,
            channel=NotificationChannel.EMAIL,
            subject="Test email subject",
            body="Test body",
            status=NotificationStatus.SENT,
        )
        self.client.force_login(self.staff)
        response = self.client.get(self.list_url, {"channel": "email"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, notif.subject)

    def test_filter_by_status(self):
        """Filtering by ?status=sent returns 200."""
        Notification.objects.create(
            recipient=self.citizen,
            channel=NotificationChannel.EMAIL,
            subject="Sent notification",
            body="Body text",
            status=NotificationStatus.SENT,
        )
        self.client.force_login(self.staff)
        response = self.client.get(self.list_url, {"status": "sent"})
        self.assertEqual(response.status_code, 200)


class StaffNotificationSendTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email="staff@example.com",
            password=VALID_PASSWORD,
            is_staff=True,
        )
        self.citizen = User.objects.create_user(
            email="citizen@example.com",
            password=VALID_PASSWORD,
            is_staff=False,
        )
        self.list_url = "/backoffice/notifications/"
        self.send_url = "/backoffice/notifications/send/"

    def test_unauthenticated_redirects(self):
        """Unauthenticated GET to send view redirects to login."""
        response = self.client.get(self.send_url)
        self.assertRedirects(
            response,
            "/account/login/?next=/backoffice/notifications/send/",
        )

    def test_citizen_gets_403(self):
        """Authenticated non-staff citizen receives HTTP 403."""
        self.client.force_login(self.citizen)
        response = self.client.get(self.send_url)
        self.assertEqual(response.status_code, 403)

    def test_get_renders_form(self):
        """Staff GET renders the send form with recipient_email field."""
        self.client.force_login(self.staff)
        response = self.client.get(self.send_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "recipient_email")

    def test_post_unknown_recipient_shows_error(self):
        """POSTing with an unknown email address re-renders the form with an error."""
        self.client.force_login(self.staff)
        response = self.client.post(
            self.send_url,
            {
                "recipient_email": "nobody@example.com",
                "subject": "Hello",
                "body": "Test message body",
            },
        )
        # Should re-render the form (200), not redirect
        self.assertEqual(response.status_code, 200)

    def test_post_staff_email_shows_error(self):
        """POSTing with the staff user's own email (is_staff=True) shows error."""
        self.client.force_login(self.staff)
        response = self.client.post(
            self.send_url,
            {
                "recipient_email": self.staff.email,
                "subject": "Hello",
                "body": "Test message body",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No citizen with that email address")

    def test_post_valid_sends_and_redirects(self):
        """Valid POST sends notification and redirects to list view."""
        with patch("apps.notifications.services.send_mail") as mock_send:
            mock_send.return_value = 1
            self.client.force_login(self.staff)
            response = self.client.post(
                self.send_url,
                {
                    "recipient_email": self.citizen.email,
                    "subject": "Important message",
                    "body": "This is the message body.",
                },
            )

        self.assertRedirects(response, self.list_url)
        # Verify a Notification record was created with status SENT
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.citizen,
                subject="Important message",
                status=NotificationStatus.SENT,
            ).exists()
        )
