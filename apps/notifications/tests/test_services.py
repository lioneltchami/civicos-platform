"""Tests for the notifications service layer."""
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.notifications.models import Notification, NotificationStatus

User = get_user_model()
VALID_PASSWORD = "SecureTestPass123!"


def make_user(email=None, preferred_language="en"):
    user = User.objects.create_user(
        email=email or f"u{uuid.uuid4().hex[:8]}@example.com",
        password=VALID_PASSWORD,
        preferred_language=preferred_language,
    )
    return user


# ---------------------------------------------------------------------------
# Helpers — patch paths mirror what services.py actually imports
# ---------------------------------------------------------------------------
SEND_MAIL = "apps.notifications.services.send_mail"
RENDER = "apps.notifications.services.render_to_string"


class SendEmailNotificationSuccessTest(TestCase):
    """Happy-path tests for send_email_notification."""

    def setUp(self):
        self.render_patcher = patch(RENDER, return_value="rendered content")
        self.send_patcher = patch(SEND_MAIL, return_value=1)
        self.mock_render = self.render_patcher.start()
        self.mock_send = self.send_patcher.start()

    def tearDown(self):
        self.render_patcher.stop()
        self.send_patcher.stop()

    def _call(self, user, **kwargs):
        from apps.notifications.services import send_email_notification
        return send_email_notification(
            recipient=user,
            subject_key=kwargs.get("subject_key", "status_update"),
            context=kwargs.get("context", {"reference": "GS-2026-001", "new_status": "in_review"}),
        )

    def test_returns_true_on_success(self):
        user = make_user()
        result = self._call(user)
        self.assertTrue(result)

    def test_creates_notification_record(self):
        user = make_user()
        self._call(user)
        self.assertEqual(Notification.objects.filter(recipient=user).count(), 1)

    def test_notification_status_is_sent(self):
        user = make_user()
        self._call(user)
        n = Notification.objects.get(recipient=user)
        self.assertEqual(n.status, NotificationStatus.SENT)

    def test_sent_at_is_populated(self):
        user = make_user()
        self._call(user)
        n = Notification.objects.get(recipient=user)
        self.assertIsNotNone(n.sent_at)

    def test_channel_is_email(self):
        user = make_user()
        self._call(user)
        n = Notification.objects.get(recipient=user)
        self.assertEqual(n.channel, "email")

    def test_language_stored_from_user_preference(self):
        user = make_user(preferred_language="fr")
        self._call(user)
        n = Notification.objects.get(recipient=user)
        self.assertEqual(n.language, "fr")

    def test_language_defaults_to_en_when_no_preference(self):
        """User with default preferred_language='en' stores 'en' on the record."""
        user = make_user(preferred_language="en")
        self._call(user)
        n = Notification.objects.get(recipient=user)
        self.assertEqual(n.language, "en")

    def test_send_mail_called_with_recipient_email(self):
        user = make_user(email="citizen@example.com")
        self._call(user)
        args, kwargs = self.mock_send.call_args
        self.assertIn("citizen@example.com", kwargs.get("recipient_list", args[3] if len(args) > 3 else []))

    def test_template_render_called_for_subject_key(self):
        user = make_user()
        self._call(user, subject_key="form_submission_confirmation")
        # render_to_string called at least once; check that the subject_key appears in one call
        called_templates = [call.args[0] for call in self.mock_render.call_args_list]
        self.assertTrue(
            any("form_submission_confirmation" in t for t in called_templates),
            f"Expected subject_key in template names, got: {called_templates}",
        )

    def test_custom_from_email_passed_to_send_mail(self):
        from apps.notifications.services import send_email_notification
        user = make_user()
        send_email_notification(
            recipient=user,
            subject_key="status_update",
            context={"reference": "X", "new_status": "submitted"},
            from_email="noreply@gov.example.com",
        )
        _, kwargs = self.mock_send.call_args
        self.assertEqual(kwargs.get("from_email"), "noreply@gov.example.com")


class SendEmailNotificationFailureTest(TestCase):
    """Tests for error handling in send_email_notification."""

    @patch(RENDER, return_value="rendered content")
    @patch(SEND_MAIL, side_effect=Exception("SMTP timeout"))
    def test_raises_on_smtp_error(self, mock_send, mock_render):
        """Service raises on SMTP failure so Celery autoretry_for=(Exception,) fires."""
        from apps.notifications.services import send_email_notification
        user = make_user()
        with self.assertRaises(Exception):
            send_email_notification(
                recipient=user,
                subject_key="status_update",
                context={"reference": "GS-2026-001", "new_status": "in_review"},
            )

    @patch(RENDER, return_value="rendered content")
    @patch(SEND_MAIL, side_effect=Exception("SMTP timeout"))
    def test_notification_marked_failed_on_smtp_error(self, mock_send, mock_render):
        """Notification record is persisted as FAILED before the exception propagates."""
        from apps.notifications.services import send_email_notification
        user = make_user()
        with self.assertRaises(Exception):
            send_email_notification(
                recipient=user,
                subject_key="status_update",
                context={"reference": "GS-2026-001", "new_status": "in_review"},
            )
        n = Notification.objects.get(recipient=user)
        self.assertEqual(n.status, NotificationStatus.FAILED)

    @patch(RENDER, return_value="rendered content")
    @patch(SEND_MAIL, side_effect=Exception("SMTP timeout"))
    def test_notification_record_exists_even_after_smtp_error(self, mock_send, mock_render):
        """Record is created before sending, so it survives a send failure."""
        from apps.notifications.services import send_email_notification
        user = make_user()
        with self.assertRaises(Exception):
            send_email_notification(
                recipient=user,
                subject_key="status_update",
                context={"reference": "GS-2026-001", "new_status": "in_review"},
            )
        self.assertEqual(Notification.objects.filter(recipient=user).count(), 1)

    @patch(RENDER, side_effect=Exception("TemplateDoesNotExist"))
    def test_returns_false_when_template_missing(self, mock_render):
        from apps.notifications.services import send_email_notification
        user = make_user()
        result = send_email_notification(
            recipient=user,
            subject_key="nonexistent_key",
            context={},
        )
        self.assertFalse(result)

    @patch(RENDER, side_effect=Exception("TemplateDoesNotExist"))
    def test_no_notification_record_when_template_missing(self, mock_render):
        """If render fails, we should NOT create a notification record."""
        from apps.notifications.services import send_email_notification
        user = make_user()
        send_email_notification(
            recipient=user,
            subject_key="nonexistent_key",
            context={},
        )
        self.assertEqual(Notification.objects.filter(recipient=user).count(), 0)

    @patch(RENDER, side_effect=Exception("TemplateDoesNotExist"))
    def test_send_mail_not_called_when_template_missing(self, mock_render):
        from apps.notifications.services import send_email_notification
        with patch(SEND_MAIL) as mock_send:
            user = make_user()
            send_email_notification(
                recipient=user,
                subject_key="nonexistent_key",
                context={},
            )
            mock_send.assert_not_called()


class SendEmailNotificationLanguageTest(TestCase):
    """Tests that the service honours the recipient's preferred language."""

    @patch(RENDER, return_value="contenu rendu")
    @patch(SEND_MAIL, return_value=1)
    def test_french_user_gets_french_notification(self, mock_send, mock_render):
        from apps.notifications.services import send_email_notification
        user = make_user(preferred_language="fr")
        send_email_notification(
            recipient=user,
            subject_key="status_update",
            context={"reference": "GS-2026-001", "new_status": "soumis"},
        )
        n = Notification.objects.get(recipient=user)
        self.assertEqual(n.language, "fr")

    @patch(RENDER, return_value="rendered")
    @patch(SEND_MAIL, return_value=1)
    def test_english_user_gets_english_notification(self, mock_send, mock_render):
        from apps.notifications.services import send_email_notification
        user = make_user(preferred_language="en")
        send_email_notification(
            recipient=user,
            subject_key="status_update",
            context={"reference": "GS-2026-001", "new_status": "in_review"},
        )
        n = Notification.objects.get(recipient=user)
        self.assertEqual(n.language, "en")
