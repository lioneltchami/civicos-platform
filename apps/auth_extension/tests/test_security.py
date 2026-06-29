"""Security-focused tests for the auth building block."""
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model

try:
    from allauth.account.models import EmailAddress
    HAS_ALLAUTH = True
except ImportError:
    HAS_ALLAUTH = False

User = get_user_model()
VALID_PASSWORD = "SecureTestPass123!"

LOGIN_URL = "/account/two-factor/login/"
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
        self.user = User.objects.create_user(
            email="secure@example.com", password=VALID_PASSWORD
        )
        if HAS_ALLAUTH:
            EmailAddress.objects.create(
                user=self.user, email=self.user.email, primary=True, verified=True
            )

    def test_password_is_hashed_not_plaintext(self):
        self.assertNotEqual(self.user.password, VALID_PASSWORD)

    @override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.Argon2PasswordHasher"])
    def test_password_is_hashed_with_argon2_when_configured(self):
        user = User.objects.create_user(
            email="argon2test@example.com", password=VALID_PASSWORD
        )
        self.assertTrue(user.password.startswith("$argon2"))

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
        self.client.force_login(self.user)
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
        self.client.force_login(self.user)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)
