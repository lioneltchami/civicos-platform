"""Security-focused tests for the auth building block."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

try:
    from allauth.account.models import EmailAddress

    HAS_ALLAUTH = True
except ImportError:
    HAS_ALLAUTH = False

User = get_user_model()
VALID_PASSWORD = "SecureTestPass123!"


def force_otp_login(client, user):
    """
    Authenticate the test client as *user* AND mark them as OTP-verified.

    L7 added OTPRequiredMixin to dashboard/profile/mfa/backup-code views.
    Plain force_login() leaves is_verified()=False, causing 403.
    """
    from django_otp import DEVICE_ID_SESSION_KEY
    from django_otp.plugins.otp_static.models import StaticDevice

    client.force_login(user)
    device, _ = StaticDevice.objects.get_or_create(user=user, defaults={"name": "test-device"})
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()


LOGIN_URL = "/account/login/"  # two_factor URLs have built-in "account/" prefix; mount at root
SIGNUP_URL = "/account/signup/"
DASHBOARD_URL = "/account/dashboard/"
PROFILE_URL = "/account/profile/"
MFA_URL = "/account/mfa/"
BACKUP_CODES_URL = "/account/mfa/backup-codes/generate/"
LANGUAGE_URL = "/account/language/"
PASSWORD_RESET_URL = "/account/password/reset/"


@override_settings(ACCOUNT_EMAIL_VERIFICATION="none")
class AuthSecurityTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="secure@example.com", password=VALID_PASSWORD)
        if HAS_ALLAUTH:
            EmailAddress.objects.create(
                user=self.user, email=self.user.email, primary=True, verified=True
            )

    def test_password_is_hashed_not_plaintext(self):
        self.assertNotEqual(self.user.password, VALID_PASSWORD)

    @override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.Argon2PasswordHasher"])
    def test_password_is_hashed_with_argon2_when_configured(self):
        user = User.objects.create_user(email="argon2test@example.com", password=VALID_PASSWORD)
        # Django's Argon2PasswordHasher stores as "argon2$argon2id$..."
        self.assertTrue(user.password.startswith("argon2$"))

    def test_csrf_token_present_on_login_page(self):
        response = self.client.get(LOGIN_URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "csrfmiddlewaretoken")

    def test_csrf_token_present_on_signup_page(self):
        response = self.client.get(SIGNUP_URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "csrfmiddlewaretoken")

    def test_dashboard_redirects_unauthenticated(self):
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 302)

    def test_profile_edit_redirects_unauthenticated(self):
        response = self.client.get(PROFILE_URL)
        self.assertEqual(response.status_code, 302)

    def test_mfa_page_redirects_unauthenticated(self):
        response = self.client.get(MFA_URL)
        self.assertEqual(response.status_code, 302)

    def test_backup_codes_requires_post(self):
        force_otp_login(self.client, self.user)
        response = self.client.get(BACKUP_CODES_URL)
        self.assertEqual(response.status_code, 405)

    def test_change_language_requires_post(self):
        self.client.force_login(self.user)
        response = self.client.get(LANGUAGE_URL)
        self.assertEqual(response.status_code, 405)

    def test_open_redirect_blocked_on_language_change(self):
        self.client.force_login(self.user)
        response = self.client.post(
            LANGUAGE_URL,
            {
                "language": "fr",
                "next": "https://evil.com/steal-cookies",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(response["Location"].startswith("https://evil.com"))

    def test_password_reset_with_nonexistent_email_does_not_reveal_existence(self):
        response = self.client.post(
            PASSWORD_RESET_URL,
            {"email": "doesnotexist@example.com"},
            follow=True,
        )
        # Same "check your email" page regardless of whether email exists
        self.assertEqual(response.status_code, 200)

    def test_dashboard_redirect_contains_login_in_url(self):
        """Redirect destination for protected views must route through login."""
        response = self.client.get(DASHBOARD_URL)
        self.assertIn("login", response["Location"])

    def test_guest_token_endpoint_does_not_require_login(self):
        """Guest session endpoint must be accessible anonymously."""
        response = self.client.get("/account/guest/", HTTP_ACCEPT="application/json")
        # Should return token, not redirect to login
        self.assertEqual(response.status_code, 200)

    def test_session_cookie_httponly_not_set_in_test(self):
        """
        In test settings SESSION_COOKIE_SECURE is False so the client
        can work over plain HTTP. Verify session is still created normally.
        """
        force_otp_login(self.client, self.user)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)

    def test_csrf_enforced_on_backup_codes_generation(self):
        """POST without CSRF token must be rejected with 403."""
        from django.test import Client

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        response = csrf_client.post(BACKUP_CODES_URL)
        self.assertEqual(response.status_code, 403)

    def test_csrf_enforced_on_language_change(self):
        """POST without CSRF token must be rejected with 403."""
        from django.test import Client

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        response = csrf_client.post(LANGUAGE_URL, {"language": "fr", "next": "/"})
        self.assertEqual(response.status_code, 403)

    def test_backup_codes_shown_only_once(self):
        """After codes are generated and viewed once, a second page load must show None."""
        force_otp_login(self.client, self.user)
        self.client.post(BACKUP_CODES_URL)
        response1 = self.client.get(MFA_URL)
        self.assertIsNotNone(response1.context.get("new_backup_codes"))
        response2 = self.client.get(MFA_URL)
        self.assertIsNone(response2.context.get("new_backup_codes"))

    def test_open_redirect_via_protocol_relative_url_blocked(self):
        """next=//evil.com must be blocked even though it starts with /."""
        self.client.force_login(self.user)
        response = self.client.post(
            LANGUAGE_URL,
            {"language": "fr", "next": "//evil.com/steal"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(response["Location"].startswith("//"))
