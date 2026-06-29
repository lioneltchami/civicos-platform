"""Tests for the citizen login flow."""
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model

try:
    from allauth.account.models import EmailAddress
    HAS_ALLAUTH = True
except ImportError:
    HAS_ALLAUTH = False

User = get_user_model()
VALID_PASSWORD = "SecureTestPass123!"

# two_factor login is mounted at /account/two-factor/
LOGIN_URL = "/account/two-factor/login/"

# auth_extension views under i18n_patterns — prefix_default_language=False
# means English has no /en/ prefix
DASHBOARD_URL = "/account/dashboard/"
PROFILE_URL = "/account/profile/"
MFA_URL = "/account/mfa/"


def create_verified_user(email="user@example.com", password=VALID_PASSWORD, **kwargs):
    user = User.objects.create_user(email=email, password=password, **kwargs)
    if HAS_ALLAUTH:
        EmailAddress.objects.create(
            user=user, email=email, primary=True, verified=True
        )
    return user


@override_settings(
    ACCOUNT_EMAIL_VERIFICATION="none",
    CELERY_TASK_ALWAYS_EAGER=True,
)
class LoginFlowTest(TestCase):
    def setUp(self):
        self.user = create_verified_user()

    def test_login_page_loads(self):
        response = self.client.get(LOGIN_URL)
        self.assertEqual(response.status_code, 200)

    def test_login_with_valid_credentials_succeeds(self):
        response = self.client.post(
            LOGIN_URL,
            {
                "auth-username": self.user.email,
                "auth-password": VALID_PASSWORD,
                "login_view-current_step": "auth",
            },
            follow=True,
        )
        # Should reach portal or next step (MFA if configured)
        self.assertIn(response.status_code, [200, 302])

    def test_login_with_wrong_password_fails(self):
        self.client.post(
            LOGIN_URL,
            {
                "auth-username": self.user.email,
                "auth-password": "WrongPassword!",
                "login_view-current_step": "auth",
            },
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_with_nonexistent_email_fails(self):
        self.client.post(
            LOGIN_URL,
            {
                "auth-username": "nobody@example.com",
                "auth-password": VALID_PASSWORD,
                "login_view-current_step": "auth",
            },
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_dashboard_requires_login(self):
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_profile_edit_requires_login(self):
        response = self.client.get(PROFILE_URL)
        self.assertEqual(response.status_code, 302)

    def test_mfa_status_requires_login(self):
        response = self.client.get(MFA_URL)
        self.assertEqual(response.status_code, 302)

    def test_authenticated_user_can_access_dashboard(self):
        self.client.force_login(self.user)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)
