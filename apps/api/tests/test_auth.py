"""
Test suite for JWT authentication endpoints.

Covers token obtain, refresh, verify, and access-control enforcement
on a representative protected endpoint (portal requests list).

Security invariants:
- Invalid credentials → 401
- Inactive user (deactivated mid-session) → 401
- No auth header → 401 (not 403)
- Valid JWT Bearer → 200
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()

VALID_PASSWORD = "SecureTestPass123!"
TOKEN_URL = "/api/v1/auth/token/"
REFRESH_URL = "/api/v1/auth/token/refresh/"
VERIFY_URL = "/api/v1/auth/token/verify/"
PROTECTED_URL = "/api/v1/portal/requests/"


def _get_tokens(client, email, password=VALID_PASSWORD):
    """Helper: obtain a JWT token pair for a given user and return the response data."""
    resp = client.post(TOKEN_URL, {"email": email, "password": password}, format="json")
    return resp


class TokenObtainTests(TestCase):
    """Tests for POST /api/v1/auth/token/ — token issuance."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="citizen@example.gov",
            password=VALID_PASSWORD,
            is_active=True,
        )

    def test_valid_credentials_return_200_with_tokens(self):
        # A correct email + password pair must yield HTTP 200 with access and refresh keys.
        resp = _get_tokens(self.client, self.user.email)

        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)
        self.assertIsInstance(resp.data["access"], str)
        self.assertIsInstance(resp.data["refresh"], str)

    def test_wrong_password_returns_401(self):
        # A bad password must never yield a token — must be 401.
        resp = _get_tokens(self.client, self.user.email, password="wrong-password!")

        self.assertEqual(resp.status_code, 401)
        self.assertNotIn("access", resp.data)

    def test_wrong_email_returns_401(self):
        # A non-existent email must not leak whether the account exists; returns 401.
        resp = _get_tokens(self.client, "nobody@example.gov")

        self.assertEqual(resp.status_code, 401)

    def test_inactive_user_returns_401(self):
        # A deactivated account must be rejected at token-obtain time.
        inactive = User.objects.create_user(
            email="inactive@example.gov",
            password=VALID_PASSWORD,
            is_active=False,
        )
        resp = _get_tokens(self.client, inactive.email)

        self.assertEqual(resp.status_code, 401)

    def test_missing_email_field_returns_400(self):
        # Omitting required fields should yield a validation error, not a 500.
        resp = self.client.post(TOKEN_URL, {"password": VALID_PASSWORD}, format="json")

        self.assertEqual(resp.status_code, 400)

    def test_missing_password_field_returns_400(self):
        # Omitting password should fail gracefully.
        resp = self.client.post(TOKEN_URL, {"email": self.user.email}, format="json")

        self.assertEqual(resp.status_code, 400)

    def test_empty_body_returns_400(self):
        # Sending an empty request should not 500.
        resp = self.client.post(TOKEN_URL, {}, format="json")

        self.assertEqual(resp.status_code, 400)


class TokenRefreshTests(TestCase):
    """Tests for POST /api/v1/auth/token/refresh/ — access-token rotation."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="citizen@example.gov",
            password=VALID_PASSWORD,
        )
        tokens = _get_tokens(self.client, self.user.email)
        self.access = tokens.data["access"]
        self.refresh = tokens.data["refresh"]

    def test_valid_refresh_token_returns_200_with_new_access(self):
        # A live refresh token must yield a fresh access token.
        resp = self.client.post(REFRESH_URL, {"refresh": self.refresh}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)

    def test_invalid_refresh_token_returns_401(self):
        # A garbage token string must be rejected with 401.
        resp = self.client.post(REFRESH_URL, {"refresh": "not.a.real.token"}, format="json")

        self.assertEqual(resp.status_code, 401)

    def test_missing_refresh_field_returns_400(self):
        # Omitting the refresh field must fail gracefully.
        resp = self.client.post(REFRESH_URL, {}, format="json")

        self.assertEqual(resp.status_code, 400)

    def test_deactivated_user_cannot_refresh_token(self):
        # A refresh token obtained while active must not work after the account is deactivated.
        # This validates that deactivating a user effectively revokes their session.
        user = User.objects.create_user(
            email="todeactivate@example.gov",
            password=VALID_PASSWORD,
            is_active=True,
        )
        tokens = _get_tokens(self.client, user.email)
        self.assertEqual(tokens.status_code, 200)
        refresh = tokens.data["refresh"]

        # Deactivate the user mid-session
        user.is_active = False
        user.save(update_fields=["is_active"])

        # The refresh token must now be rejected
        resp = self.client.post(REFRESH_URL, {"refresh": refresh}, format="json")
        self.assertEqual(
            resp.status_code,
            401,
            "Deactivated user must not be able to refresh tokens — session must be revoked.",
        )


class TokenVerifyTests(TestCase):
    """Tests for POST /api/v1/auth/token/verify/ — token validation without rotation."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="citizen@example.gov",
            password=VALID_PASSWORD,
        )
        tokens = _get_tokens(self.client, self.user.email)
        self.access = tokens.data["access"]

    def test_valid_access_token_returns_200(self):
        # A live access token must pass verification.
        resp = self.client.post(VERIFY_URL, {"token": self.access}, format="json")

        self.assertEqual(resp.status_code, 200)

    def test_invalid_token_returns_401(self):
        # A forged or expired token string must be rejected.
        resp = self.client.post(VERIFY_URL, {"token": "garbage.token.value"}, format="json")

        self.assertEqual(resp.status_code, 401)

    def test_missing_token_field_returns_400(self):
        # Omitting the token field must fail with validation error.
        resp = self.client.post(VERIFY_URL, {}, format="json")

        self.assertEqual(resp.status_code, 400)


class BearerAuthOnProtectedEndpointTests(TestCase):
    """
    Tests for access-control enforcement on a representative protected endpoint.

    Verifies that unauthenticated requests receive 401 (not 403) and that
    valid JWT Bearer tokens grant access.
    """

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="citizen@example.gov",
            password=VALID_PASSWORD,
        )

    def test_no_auth_header_returns_401(self):
        # An unauthenticated request to a protected endpoint must yield 401, not 403.
        # RFC 7235 §3.1: 401 is for missing/invalid credentials; 403 for insufficient privileges.
        resp = self.client.get(PROTECTED_URL)

        self.assertEqual(resp.status_code, 401)

    def test_malformed_bearer_header_returns_401(self):
        # A syntactically incorrect Authorization header must be rejected as 401.
        resp = self.client.get(
            PROTECTED_URL,
            HTTP_AUTHORIZATION="Bearer this.is.not.valid",
        )

        self.assertEqual(resp.status_code, 401)

    def test_valid_bearer_token_returns_200(self):
        # A properly-formed Bearer token must grant access and return the list.
        tokens = _get_tokens(self.client, self.user.email)
        access = tokens.data["access"]

        resp = self.client.get(
            PROTECTED_URL,
            HTTP_AUTHORIZATION=f"Bearer {access}",
        )

        self.assertEqual(resp.status_code, 200)

    def test_basic_auth_without_bearer_returns_401(self):
        # Using HTTP Basic credentials (username:password) must not bypass JWT auth.
        resp = self.client.get(
            PROTECTED_URL,
            HTTP_AUTHORIZATION="Basic Y2l0aXplbkBleGFtcGxlLmdvdjpwYXNzd29yZA==",
        )

        self.assertEqual(resp.status_code, 401)
