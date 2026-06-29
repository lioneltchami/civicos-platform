"""Tests for the notification inbox views.

URL layout (from apps/notifications/urls.py, mounted at /notifications/):
  GET  /notifications/                  → NotificationListView (inbox)
  POST /notifications/<uuid>/read/      → MarkNotificationReadView
  POST /notifications/mark-all-read/    → MarkAllReadView
  GET  /notifications/unread-count/     → UnreadCountView
"""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.notifications.models import Notification, NotificationChannel, NotificationStatus

User = get_user_model()
VALID_PASSWORD = "SecureTestPass123!"

INBOX_URL = "/notifications/"
MARK_ALL_URL = "/notifications/mark-all-read/"
UNREAD_COUNT_URL = "/notifications/unread-count/"


def make_user(email=None):
    return User.objects.create_user(
        email=email or f"u{uuid.uuid4().hex[:8]}@example.com",
        password=VALID_PASSWORD,
    )


def make_notification(recipient, read=False, **kwargs):
    n = Notification.objects.create(
        recipient=recipient,
        channel=kwargs.get("channel", NotificationChannel.EMAIL),
        subject=kwargs.get("subject", "Test notification"),
        body=kwargs.get("body", "Test body"),
        language=kwargs.get("language", "en"),
        status=kwargs.get("status", NotificationStatus.SENT),
    )
    if read:
        n.read_at = timezone.now()
        n.save(update_fields=["read_at"])
    return n


def mark_read_url(pk):
    return f"/notifications/{pk}/read/"


# ---------------------------------------------------------------------------
# NotificationListView
# ---------------------------------------------------------------------------

class NotificationListViewTest(TestCase):

    def setUp(self):
        self.citizen = make_user()
        self.client.force_login(self.citizen)

    def test_inbox_loads_200(self):
        response = self.client.get(INBOX_URL)
        self.assertEqual(response.status_code, 200)

    def test_inbox_requires_login(self):
        self.client.logout()
        response = self.client.get(INBOX_URL)
        self.assertRedirects(response, f"/accounts/login/?next={INBOX_URL}", fetch_redirect_response=False)

    def test_inbox_empty_for_new_user(self):
        response = self.client.get(INBOX_URL)
        self.assertEqual(len(response.context["notifications"]), 0)

    def test_inbox_shows_own_notifications(self):
        make_notification(self.citizen)
        make_notification(self.citizen)
        response = self.client.get(INBOX_URL)
        self.assertEqual(len(response.context["notifications"]), 2)

    def test_inbox_does_not_show_other_users_notifications(self):
        other = make_user()
        make_notification(other)
        response = self.client.get(INBOX_URL)
        self.assertEqual(len(response.context["notifications"]), 0)

    def test_unread_filter_returns_only_unread(self):
        make_notification(self.citizen, read=False)
        make_notification(self.citizen, read=True)
        response = self.client.get(f"{INBOX_URL}?filter=unread")
        self.assertEqual(len(response.context["notifications"]), 1)
        for n in response.context["notifications"]:
            self.assertIsNone(n.read_at)

    def test_read_filter_returns_only_read(self):
        make_notification(self.citizen, read=False)
        make_notification(self.citizen, read=True)
        response = self.client.get(f"{INBOX_URL}?filter=read")
        self.assertEqual(len(response.context["notifications"]), 1)
        for n in response.context["notifications"]:
            self.assertIsNotNone(n.read_at)

    def test_all_filter_returns_everything(self):
        make_notification(self.citizen, read=False)
        make_notification(self.citizen, read=True)
        response = self.client.get(f"{INBOX_URL}?filter=all")
        self.assertEqual(len(response.context["notifications"]), 2)

    def test_channel_filter_email(self):
        make_notification(self.citizen, channel=NotificationChannel.EMAIL)
        make_notification(self.citizen, channel=NotificationChannel.IN_APP)
        response = self.client.get(f"{INBOX_URL}?channel=email")
        self.assertEqual(len(response.context["notifications"]), 1)
        self.assertEqual(response.context["notifications"][0].channel, "email")

    def test_context_contains_unread_count(self):
        make_notification(self.citizen, read=False)
        make_notification(self.citizen, read=True)
        response = self.client.get(INBOX_URL)
        self.assertEqual(response.context["unread_count"], 1)

    def test_context_active_filter_default_is_all(self):
        response = self.client.get(INBOX_URL)
        self.assertEqual(response.context["active_filter"], "all")

    def test_context_active_filter_reflects_param(self):
        response = self.client.get(f"{INBOX_URL}?filter=unread")
        self.assertEqual(response.context["active_filter"], "unread")

    def test_uses_inbox_template(self):
        response = self.client.get(INBOX_URL)
        self.assertTemplateUsed(response, "notifications/inbox.html")


# ---------------------------------------------------------------------------
# MarkNotificationReadView
# ---------------------------------------------------------------------------

class MarkNotificationReadTest(TestCase):

    def setUp(self):
        self.citizen = make_user()
        self.client.force_login(self.citizen)

    def test_marks_notification_as_read(self):
        n = make_notification(self.citizen, read=False)
        self.client.post(mark_read_url(n.pk))
        n.refresh_from_db()
        self.assertIsNotNone(n.read_at)

    def test_redirects_to_inbox_by_default(self):
        n = make_notification(self.citizen, read=False)
        response = self.client.post(mark_read_url(n.pk))
        self.assertRedirects(response, INBOX_URL, fetch_redirect_response=False)

    def test_redirects_to_safe_next_param(self):
        n = make_notification(self.citizen, read=False)
        response = self.client.post(mark_read_url(n.pk), {"next": "/some/internal/page/"})
        self.assertRedirects(response, "/some/internal/page/", fetch_redirect_response=False)

    def test_open_redirect_guard_falls_back_to_inbox(self):
        """next= with an absolute URL must NOT be followed."""
        n = make_notification(self.citizen, read=False)
        response = self.client.post(mark_read_url(n.pk), {"next": "https://evil.com/phish"})
        self.assertRedirects(response, INBOX_URL, fetch_redirect_response=False)

    def test_open_redirect_guard_protocol_relative(self):
        """next= starting with // must NOT be followed."""
        n = make_notification(self.citizen, read=False)
        response = self.client.post(mark_read_url(n.pk), {"next": "//evil.com/"})
        self.assertRedirects(response, INBOX_URL, fetch_redirect_response=False)

    def test_cannot_mark_other_users_notification(self):
        other = make_user()
        n = make_notification(other, read=False)
        response = self.client.post(mark_read_url(n.pk))
        self.assertEqual(response.status_code, 404)

    def test_other_users_read_at_unchanged(self):
        other = make_user()
        n = make_notification(other, read=False)
        self.client.post(mark_read_url(n.pk))
        n.refresh_from_db()
        self.assertIsNone(n.read_at)

    def test_requires_post_method(self):
        n = make_notification(self.citizen, read=False)
        response = self.client.get(mark_read_url(n.pk))
        self.assertEqual(response.status_code, 405)

    def test_requires_login(self):
        self.client.logout()
        n = make_notification(self.citizen, read=False)
        response = self.client.post(mark_read_url(n.pk))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_marking_already_read_notification_is_idempotent(self):
        n = make_notification(self.citizen, read=True)
        original_read_at = n.read_at
        self.client.post(mark_read_url(n.pk))
        n.refresh_from_db()
        # read_at should not be updated a second time
        self.assertEqual(n.read_at, original_read_at)

    def test_ajax_request_returns_json(self):
        n = make_notification(self.citizen, read=False)
        response = self.client.post(
            mark_read_url(n.pk),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("read_at", data)

    def test_unknown_notification_pk_returns_404(self):
        response = self.client.post(mark_read_url(uuid.uuid4()))
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# MarkAllReadView
# ---------------------------------------------------------------------------

class MarkAllReadTest(TestCase):

    def setUp(self):
        self.citizen = make_user()
        self.client.force_login(self.citizen)

    def test_marks_all_unread_as_read(self):
        make_notification(self.citizen, read=False)
        make_notification(self.citizen, read=False)
        self.client.post(MARK_ALL_URL)
        unread_count = Notification.objects.filter(
            recipient=self.citizen, read_at__isnull=True
        ).count()
        self.assertEqual(unread_count, 0)

    def test_does_not_affect_other_users(self):
        other = make_user()
        n = make_notification(other, read=False)
        self.client.post(MARK_ALL_URL)
        n.refresh_from_db()
        self.assertIsNone(n.read_at)

    def test_already_read_notifications_unaffected(self):
        """Already-read notifications should retain their original read_at timestamp."""
        n = make_notification(self.citizen, read=True)
        original_read_at = n.read_at
        self.client.post(MARK_ALL_URL)
        n.refresh_from_db()
        self.assertEqual(n.read_at, original_read_at)

    def test_requires_post_method(self):
        response = self.client.get(MARK_ALL_URL)
        self.assertEqual(response.status_code, 405)

    def test_requires_login(self):
        self.client.logout()
        response = self.client.post(MARK_ALL_URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_redirects_to_inbox(self):
        response = self.client.post(MARK_ALL_URL)
        self.assertRedirects(response, INBOX_URL, fetch_redirect_response=False)

    def test_ajax_returns_json_with_marked_count(self):
        make_notification(self.citizen, read=False)
        make_notification(self.citizen, read=False)
        response = self.client.post(MARK_ALL_URL, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["marked_count"], 2)

    def test_no_unread_notifications_is_safe(self):
        """Calling mark-all-read with no unread notifications should not error."""
        response = self.client.post(MARK_ALL_URL)
        self.assertIn(response.status_code, (200, 302))


# ---------------------------------------------------------------------------
# UnreadCountView
# ---------------------------------------------------------------------------

class UnreadCountViewTest(TestCase):

    def setUp(self):
        self.citizen = make_user()
        self.client.force_login(self.citizen)

    def test_returns_200_json(self):
        response = self.client.get(UNREAD_COUNT_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")

    def test_correct_unread_count(self):
        make_notification(self.citizen, read=False)
        make_notification(self.citizen, read=False)
        make_notification(self.citizen, read=True)
        response = self.client.get(UNREAD_COUNT_URL)
        data = response.json()
        self.assertEqual(data["unread_count"], 2)

    def test_count_zero_when_all_read(self):
        make_notification(self.citizen, read=True)
        response = self.client.get(UNREAD_COUNT_URL)
        self.assertEqual(response.json()["unread_count"], 0)

    def test_count_zero_when_no_notifications(self):
        response = self.client.get(UNREAD_COUNT_URL)
        self.assertEqual(response.json()["unread_count"], 0)

    def test_count_excludes_other_users_notifications(self):
        other = make_user()
        make_notification(other, read=False)
        response = self.client.get(UNREAD_COUNT_URL)
        self.assertEqual(response.json()["unread_count"], 0)

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(UNREAD_COUNT_URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_response_contains_unread_count_key(self):
        response = self.client.get(UNREAD_COUNT_URL)
        self.assertIn("unread_count", response.json())


# ---------------------------------------------------------------------------
# Cross-cutting security / isolation tests
# ---------------------------------------------------------------------------

class NotificationIsolationTest(TestCase):
    """Ensure one citizen cannot ever touch another citizen's notifications."""

    def setUp(self):
        self.alice = make_user("alice@example.com")
        self.bob = make_user("bob@example.com")

    def test_alice_cannot_read_bobs_notification_count(self):
        make_notification(self.bob, read=False)
        self.client.force_login(self.alice)
        response = self.client.get(UNREAD_COUNT_URL)
        self.assertEqual(response.json()["unread_count"], 0)

    def test_alice_inbox_empty_when_only_bob_has_notifications(self):
        make_notification(self.bob)
        self.client.force_login(self.alice)
        response = self.client.get(INBOX_URL)
        self.assertEqual(len(response.context["notifications"]), 0)

    def test_alice_mark_all_read_does_not_affect_bob(self):
        n = make_notification(self.bob, read=False)
        self.client.force_login(self.alice)
        self.client.post(MARK_ALL_URL)
        n.refresh_from_db()
        self.assertIsNone(n.read_at)
