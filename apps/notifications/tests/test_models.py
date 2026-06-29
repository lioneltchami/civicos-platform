"""Tests for the Notification model."""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.notifications.models import Notification, NotificationChannel, NotificationStatus

User = get_user_model()
VALID_PASSWORD = "SecureTestPass123!"


def make_user(email=None):
    return User.objects.create_user(
        email=email or f"u{uuid.uuid4().hex[:8]}@example.com",
        password=VALID_PASSWORD,
    )


def make_notification(recipient=None, **kwargs):
    if recipient is None:
        recipient = make_user()
    return Notification.objects.create(
        recipient=recipient,
        channel=kwargs.get("channel", NotificationChannel.EMAIL),
        subject=kwargs.get("subject", "Test notification"),
        body=kwargs.get("body", "Test body"),
        language=kwargs.get("language", "en"),
        status=kwargs.get("status", NotificationStatus.PENDING),
    )


class NotificationModelTest(TestCase):

    def test_str_includes_channel_and_email(self):
        user = make_user("citizen@example.com")
        n = make_notification(recipient=user, subject="Your request update")
        self.assertIn("email", str(n))
        self.assertIn("citizen@example.com", str(n))

    def test_str_includes_subject(self):
        n = make_notification(subject="Your request update")
        self.assertIn("Your request update", str(n))

    def test_str_no_subject_shows_fallback(self):
        """Blank subject should show a readable fallback, not an empty string."""
        n = make_notification(subject="")
        self.assertIn("(no subject)", str(n))

    def test_default_status_is_pending(self):
        n = make_notification()
        self.assertEqual(n.status, NotificationStatus.PENDING)

    def test_read_at_is_null_by_default(self):
        n = make_notification()
        self.assertIsNone(n.read_at)

    def test_sent_at_is_null_by_default(self):
        n = make_notification()
        self.assertIsNone(n.sent_at)

    def test_pk_is_uuid(self):
        n = make_notification()
        self.assertIsInstance(n.pk, uuid.UUID)

    def test_notifications_ordered_newest_first(self):
        user = make_user()
        n1 = make_notification(recipient=user)
        n2 = make_notification(recipient=user)
        notifications = list(Notification.objects.filter(recipient=user))
        # Meta ordering = ["-created_at"], so newest (n2) comes first
        self.assertEqual(notifications[0].pk, n2.pk)

    def test_channel_choices_are_valid(self):
        for channel in (NotificationChannel.EMAIL, NotificationChannel.SMS, NotificationChannel.IN_APP):
            n = make_notification(channel=channel)
            self.assertEqual(n.channel, channel)

    def test_status_choices_cover_lifecycle(self):
        statuses = [
            NotificationStatus.PENDING,
            NotificationStatus.SENT,
            NotificationStatus.DELIVERED,
            NotificationStatus.FAILED,
            NotificationStatus.BOUNCED,
        ]
        user = make_user()
        for status in statuses:
            n = make_notification(recipient=user, status=status)
            self.assertEqual(n.status, status)

    def test_language_stored_correctly(self):
        n = make_notification(language="fr")
        self.assertEqual(n.language, "fr")

    def test_cascade_delete_with_user(self):
        user = make_user()
        make_notification(recipient=user)
        pk = user.pk
        user.delete()
        self.assertFalse(Notification.objects.filter(recipient_id=pk).exists())

    def test_external_id_blank_by_default(self):
        n = make_notification()
        self.assertEqual(n.external_id, "")

    def test_external_id_can_be_stored(self):
        n = make_notification()
        n.external_id = "gc-notify-abc123"
        n.save(update_fields=["external_id"])
        n.refresh_from_db()
        self.assertEqual(n.external_id, "gc-notify-abc123")

    def test_recipient_related_name(self):
        user = make_user()
        make_notification(recipient=user)
        make_notification(recipient=user)
        self.assertEqual(user.notifications.count(), 2)

    def test_multiple_users_isolated(self):
        u1, u2 = make_user(), make_user()
        make_notification(recipient=u1)
        make_notification(recipient=u2)
        self.assertEqual(Notification.objects.filter(recipient=u1).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=u2).count(), 1)
