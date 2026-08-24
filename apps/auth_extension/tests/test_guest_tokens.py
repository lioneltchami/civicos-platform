"""Tests for anonymous/guest session token support."""

from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase

from apps.auth_extension.tokens import (
    GUEST_SESSION_KEY,
    GuestTokenManager,
    generate_guest_token,
    get_or_create_guest_token,
)

# URL for the guest session view
GUEST_URL = "/account/guest/"


class GuestTokenTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _request_with_session(self):
        request = self.factory.get("/")
        request.session = SessionStore()
        return request

    def test_guest_token_is_created_in_session(self):
        request = self._request_with_session()
        token = get_or_create_guest_token(request)
        self.assertIsNotNone(token)
        self.assertEqual(request.session[GUEST_SESSION_KEY], token)

    def test_same_token_returned_on_second_call(self):
        request = self._request_with_session()
        token1 = get_or_create_guest_token(request)
        token2 = get_or_create_guest_token(request)
        self.assertEqual(token1, token2)

    def test_token_is_long_enough(self):
        token = generate_guest_token()
        # URL-safe base64 of 32 bytes = ~43 chars
        self.assertGreaterEqual(len(token), 32)

    def test_manager_get_returns_none_when_no_token(self):
        request = self._request_with_session()
        self.assertIsNone(GuestTokenManager.get(request))

    def test_manager_get_returns_token_when_present(self):
        request = self._request_with_session()
        GuestTokenManager.create(request)
        self.assertIsNotNone(GuestTokenManager.get(request))

    def test_manager_clear_removes_token(self):
        request = self._request_with_session()
        GuestTokenManager.create(request)
        GuestTokenManager.clear(request)
        self.assertNotIn(GUEST_SESSION_KEY, request.session)

    def test_manager_create_stores_token_in_session(self):
        request = self._request_with_session()
        token = GuestTokenManager.create(request)
        self.assertEqual(request.session[GUEST_SESSION_KEY], token)

    def test_generate_token_produces_unique_values(self):
        token_a = generate_guest_token()
        token_b = generate_guest_token()
        self.assertNotEqual(token_a, token_b)

    def test_guest_session_view_creates_token(self):
        """GET /account/guest/ should create a token in session for anonymous users."""
        response = self.client.get(GUEST_URL)
        self.assertIn(response.status_code, [200, 302])
        self.assertIn(GUEST_SESSION_KEY, self.client.session)

    def test_guest_session_view_authenticated_user_clears_token(self):
        """Authenticated users visiting /account/guest/ should have guest token cleared."""
        from django.contrib.auth import get_user_model

        User = get_user_model()  # noqa: N806
        user = User.objects.create_user(email="auth@example.com", password="TestPass123!")
        self.client.force_login(user)
        # Pre-seed a token
        session = self.client.session
        session[GUEST_SESSION_KEY] = "old-token"
        session.save()
        self.client.get(GUEST_URL)
        self.assertNotIn(GUEST_SESSION_KEY, self.client.session)

    def test_guest_session_view_json_returns_token(self):
        response = self.client.get(GUEST_URL, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("guest_token", data)
        # Token should also be stored in session
        self.assertIn(GUEST_SESSION_KEY, self.client.session)
