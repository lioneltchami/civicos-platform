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

# two_factor patterns include their own "account/" prefix, so mount at root:
# reverse('two_factor:login') → /account/login/
LOGIN_URL = "/account/login/"

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


def force_otp_login(client, user):
    """
    Authenticate the test client as *user* AND mark them as OTP-verified.

    L7 added OTPRequiredMixin to dashboard/profile/mfa views. plain force_login()
    authenticates the session but leaves is_verified()=False, causing 403.

    This helper creates a dummy StaticDevice (no real TOTP needed in tests),
    then stores its persistent_id in the session key that OTPMiddleware reads.
    OTPMiddleware._verify_user() will find the device and set otp_device, making
    user.is_verified() return True.
    """
    from django_otp import DEVICE_ID_SESSION_KEY
    from django_otp.plugins.otp_static.models import StaticDevice

    client.force_login(user)
    device, _ = StaticDevice.objects.get_or_create(
        user=user, defaults={"name": "test-device"}
    )
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()


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
        self.client.post(
            LOGIN_URL,
            {
                "auth-username": self.user.email,
                "auth-password": VALID_PASSWORD,
                "login_view-current_step": "auth",
            },
            follow=True,
        )
        # Verify the user was actually authenticated into the session
        self.assertIn("_auth_user_id", self.client.session)

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
        force_otp_login(self.client, self.user)
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)
