"""Tests for profile management views."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

try:
    from allauth.account.models import EmailAddress

    HAS_ALLAUTH = True
except ImportError:
    HAS_ALLAUTH = False

User = get_user_model()
VALID_PASSWORD = "SecureTestPass123!"

# auth_extension views under i18n_patterns (prefix_default_language=False)
PROFILE_URL = "/account/profile/"
DASHBOARD_URL = "/account/dashboard/"
LANGUAGE_URL = "/account/language/"


def create_verified_user(email="user@example.com", password=VALID_PASSWORD):
    user = User.objects.create_user(email=email, password=password)
    if HAS_ALLAUTH:
        EmailAddress.objects.create(user=user, email=email, primary=True, verified=True)
    return user


def force_otp_login(client, user):
    """
    Authenticate the test client as *user* AND mark them as OTP-verified.

    L7 added OTPRequiredMixin to profile/dashboard views. Plain force_login()
    authenticates the session but leaves is_verified()=False, causing 403.
    """
    from django_otp import DEVICE_ID_SESSION_KEY
    from django_otp.plugins.otp_static.models import StaticDevice

    client.force_login(user)
    device, _ = StaticDevice.objects.get_or_create(user=user, defaults={"name": "test-device"})
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()


@override_settings(ACCOUNT_EMAIL_VERIFICATION="none")
class ProfileUpdateTest(TestCase):
    def setUp(self):
        self.user = create_verified_user()
        force_otp_login(self.client, self.user)

    def test_profile_page_loads(self):
        response = self.client.get(PROFILE_URL)
        self.assertEqual(response.status_code, 200)

    def test_profile_update_saves_first_name(self):
        self.client.post(
            PROFILE_URL,
            {
                "first_name": "Lionel",
                "last_name": "Smith",
                "preferred_language": "en",
                "phone_number": "",
            },
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Lionel")

    def test_profile_update_saves_last_name(self):
        self.client.post(
            PROFILE_URL,
            {
                "first_name": "Lionel",
                "last_name": "Smith",
                "preferred_language": "en",
                "phone_number": "",
            },
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.last_name, "Smith")

    def test_profile_update_saves_preferred_language_fr(self):
        self.client.post(
            PROFILE_URL,
            {
                "first_name": "",
                "last_name": "",
                "preferred_language": "fr",
                "phone_number": "",
            },
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.preferred_language, "fr")

    def test_profile_update_redirects_to_dashboard(self):
        response = self.client.post(
            PROFILE_URL,
            {
                "first_name": "Test",
                "last_name": "User",
                "preferred_language": "en",
                "phone_number": "",
            },
        )
        self.assertRedirects(response, DASHBOARD_URL)

    def test_dashboard_shows_request_count(self):
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)
        self.assertIn("request_count", response.context)

    def test_change_language_requires_post(self):
        response = self.client.get(LANGUAGE_URL)
        self.assertEqual(response.status_code, 405)

    def test_change_language_updates_user(self):
        self.client.post(LANGUAGE_URL, {"language": "fr", "next": "/"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.preferred_language, "fr")

    def test_change_language_ignores_invalid_value(self):
        original_lang = self.user.preferred_language
        self.client.post(LANGUAGE_URL, {"language": "es", "next": "/"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.preferred_language, original_lang)

    def test_profile_page_contains_form_fields(self):
        response = self.client.get(PROFILE_URL)
        self.assertContains(response, "first_name")
        self.assertContains(response, "preferred_language")

    def test_dashboard_shows_unread_count(self):
        response = self.client.get(DASHBOARD_URL)
        self.assertIn("unread_count", response.context)
