"""
Security / privacy tests for rate-limiting and IP handling in payment views.

Covers three issues (PIPEDA compliance):
  Item 10 — raw REMOTE_ADDR must not appear in any log output.
  Item 11 — rate-limit initialisation must use atomic cache.add+incr (not TOCTOU set).
  Item 12 — anonymous donation rate-limit cache key must not contain raw IP.
"""
import hashlib
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import call, patch, MagicMock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, RequestFactory
from django.urls import reverse

from apps.payments.views.donation import _check_donation_rate_limit
from apps.payments.views.fee_payment import _check_rate_limit

User = get_user_model()

TEST_IP = "203.0.113.42"   # RFC 5737 documentation address — clearly fake
MASKED_IP = "203.0.113.0"  # expected masked form (last octet zeroed)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_anon_request(ip=TEST_IP):
    """Return an unauthenticated RequestFactory request with REMOTE_ADDR set."""
    factory = RequestFactory()
    request = factory.post("/fake/")
    request.META["REMOTE_ADDR"] = ip
    # Mark as anonymous
    from django.contrib.auth.models import AnonymousUser
    request.user = AnonymousUser()
    return request


def _make_auth_request(user, ip=TEST_IP):
    """Return an authenticated RequestFactory request with REMOTE_ADDR set."""
    factory = RequestFactory()
    request = factory.post("/fake/")
    request.META["REMOTE_ADDR"] = ip
    request.user = user
    return request


# ---------------------------------------------------------------------------
# Item 10 — raw IP must not appear in logs (fee_payment)
# ---------------------------------------------------------------------------

class FeePaymentIPMaskingInLogsTest(TestCase):
    """Verify that a session-expired warning in create_payment_intent_api
    logs the masked IP, not the raw address."""

    def setUp(self):
        self.user = User.objects.create_user(
            email=f"fee_{uuid.uuid4().hex[:6]}@example.com",
            password="testpass123",
        )
        # Minimal TenantPaymentConfig (uses get_solo — create if absent)
        from apps.payments.models import TenantPaymentConfig
        TenantPaymentConfig.objects.get_or_create(
            defaults={"stripe_publishable_key": "pk_test_dummy"}
        )
        cache.clear()

    def test_session_expired_log_does_not_contain_raw_ip(self):
        """logger.warning in create_payment_intent_api must use _mask_ip, not raw IP."""
        self.client.force_login(self.user)
        # Deliberately provide no session data so the session-expired branch fires.
        # REMOTE_ADDR is set via SERVER_NAME / extra kwargs on the test client.
        with self.assertLogs("apps.payments", level="WARNING") as log_ctx:
            response = self.client.post(
                reverse("payments:create_payment_intent"),
                content_type="application/json",
                data="{}",
                REMOTE_ADDR=TEST_IP,
            )
        self.assertEqual(response.status_code, 403)
        # The raw IP must not appear in any log message.
        for record in log_ctx.output:
            self.assertNotIn(
                TEST_IP,
                record,
                msg=f"Raw IP {TEST_IP!r} found in log: {record!r}",
            )
        # The masked IP *should* appear instead.
        self.assertTrue(
            any(MASKED_IP in record for record in log_ctx.output),
            msg=f"Expected masked IP {MASKED_IP!r} in logs but found: {log_ctx.output}",
        )


# ---------------------------------------------------------------------------
# Item 10 — raw IP must not appear in logs (donation)
# ---------------------------------------------------------------------------

class DonationIPMaskingInLogsTest(TestCase):
    """Verify that a session-expired warning in create_donation_intent_api
    logs the masked IP, not the raw address."""

    def setUp(self):
        self.user = User.objects.create_user(
            email=f"don_{uuid.uuid4().hex[:6]}@example.com",
            password="testpass123",
        )
        from apps.payments.models import TenantPaymentConfig
        TenantPaymentConfig.objects.get_or_create(
            defaults={"stripe_publishable_key": "pk_test_dummy"}
        )
        cache.clear()

    def test_session_expired_log_does_not_contain_raw_ip(self):
        """logger.warning in create_donation_intent_api must use _mask_ip, not raw IP."""
        self.client.force_login(self.user)
        # No session data → session_expired branch fires.
        with self.assertLogs("apps.payments", level="WARNING") as log_ctx:
            response = self.client.post(
                reverse("donate:create_donation_intent"),
                content_type="application/json",
                data="{}",
                REMOTE_ADDR=TEST_IP,
            )
        self.assertEqual(response.status_code, 403)
        for record in log_ctx.output:
            self.assertNotIn(
                TEST_IP,
                record,
                msg=f"Raw IP {TEST_IP!r} found in log: {record!r}",
            )
        self.assertTrue(
            any(MASKED_IP in record for record in log_ctx.output),
            msg=f"Expected masked IP {MASKED_IP!r} in logs but found: {log_ctx.output}",
        )


# ---------------------------------------------------------------------------
# Item 12 — donation rate-limit cache key must NOT contain raw IP
# ---------------------------------------------------------------------------

class DonationRateLimitCacheKeyTest(TestCase):
    """Verify the anonymous rate-limit cache key is keyed on a SHA-256 hash
    of the IP address, never the raw IP string itself."""

    def setUp(self):
        cache.clear()

    def test_anonymous_cache_key_does_not_contain_raw_ip(self):
        """cache.add and cache.incr must be called with a key that does not
        contain the raw IP address."""
        request = _make_anon_request(ip=TEST_IP)

        with patch("apps.payments.views.donation.cache") as mock_cache:
            mock_cache.add.return_value = True
            mock_cache.incr.return_value = 1

            _check_donation_rate_limit(request)

            # Collect all key arguments passed to cache.add and cache.incr
            keys_used = []
            for c in mock_cache.add.call_args_list:
                keys_used.append(c.args[0] if c.args else c.kwargs.get("key", ""))
            for c in mock_cache.incr.call_args_list:
                keys_used.append(c.args[0] if c.args else c.kwargs.get("key", ""))

            for key in keys_used:
                self.assertNotIn(
                    TEST_IP,
                    key,
                    msg=f"Raw IP {TEST_IP!r} found in cache key: {key!r}",
                )

    def test_anonymous_cache_key_contains_expected_hash(self):
        """The cache key should embed a SHA-256 prefix of the IP, not the IP itself."""
        request = _make_anon_request(ip=TEST_IP)
        expected_hash = hashlib.sha256(TEST_IP.encode()).hexdigest()[:16]

        with patch("apps.payments.views.donation.cache") as mock_cache:
            mock_cache.add.return_value = True
            mock_cache.incr.return_value = 1

            _check_donation_rate_limit(request)

            # At least one add call should use the hashed key
            add_keys = [
                (c.args[0] if c.args else c.kwargs.get("key", ""))
                for c in mock_cache.add.call_args_list
            ]
            self.assertTrue(
                any(expected_hash in k for k in add_keys),
                msg=f"Expected hash {expected_hash!r} not found in add keys: {add_keys}",
            )

    def test_authenticated_cache_key_uses_user_pk(self):
        """Authenticated rate-limit key should be keyed by user PK, not IP."""
        user = User.objects.create_user(
            email=f"auth_{uuid.uuid4().hex[:6]}@example.com",
            password="testpass123",
        )
        request = _make_auth_request(user, ip=TEST_IP)

        with patch("apps.payments.views.donation.cache") as mock_cache:
            mock_cache.add.return_value = True
            mock_cache.incr.return_value = 1

            _check_donation_rate_limit(request)

            add_keys = [
                (c.args[0] if c.args else c.kwargs.get("key", ""))
                for c in mock_cache.add.call_args_list
            ]
            self.assertTrue(
                any(str(user.pk) in k for k in add_keys),
                msg=f"User PK not found in cache keys: {add_keys}",
            )
            for key in add_keys:
                self.assertNotIn(
                    TEST_IP,
                    key,
                    msg=f"Raw IP found in auth cache key: {key!r}",
                )


# ---------------------------------------------------------------------------
# Item 11 — atomic init: cache.add+incr pattern (no TOCTOU set)
# ---------------------------------------------------------------------------

class FeeRateLimitAtomicInitTest(TestCase):
    """Verify _check_rate_limit uses the atomic cache.add + cache.incr pattern
    and does NOT fall back to cache.set(key, 1, ...) on first call."""

    def setUp(self):
        cache.clear()

    def test_cache_add_called_before_incr(self):
        """cache.add must be called before cache.incr on every invocation."""
        call_order = []

        def mock_add(key, value, timeout=None):
            call_order.append(("add", key, value))
            return True  # simulate key was absent

        def mock_incr(key):
            call_order.append(("incr", key))
            return 1

        with patch("apps.payments.views.fee_payment.cache") as mock_cache:
            mock_cache.add.side_effect = mock_add
            mock_cache.incr.side_effect = mock_incr

            result = _check_rate_limit("user-pk-123")

        self.assertFalse(result, "First call should be within rate limit (returns False = not exceeded)")
        self.assertEqual(len(call_order), 2)
        self.assertEqual(call_order[0][0], "add", "cache.add must be called first")
        self.assertEqual(call_order[1][0], "incr", "cache.incr must be called second")

    def test_cache_set_with_value_1_never_called(self):
        """The old broken TOCTOU pattern (cache.set(key, 1, timeout)) must not appear."""
        with patch("apps.payments.views.fee_payment.cache") as mock_cache:
            mock_cache.add.return_value = True
            mock_cache.incr.return_value = 1

            _check_rate_limit("user-pk-456")

            # cache.set must NOT be called at all (old except-branch is gone)
            mock_cache.set.assert_not_called()

    def test_rate_limit_allows_up_to_5(self):
        """Calls 1-5 should return False (not exceeded); call 6 should return True (exceeded)."""
        user_pk = f"rl-test-{uuid.uuid4().hex[:8]}"
        for i in range(1, 6):
            result = _check_rate_limit(user_pk)
            self.assertFalse(result, f"Call {i} should be within limit (returns False)")
        result = _check_rate_limit(user_pk)
        self.assertTrue(result, "Call 6 should exceed the rate limit (returns True)")


class DonationRateLimitAtomicInitTest(TestCase):
    """Verify _check_donation_rate_limit uses atomic cache.add + cache.incr
    and does NOT fall back to cache.set(key, 1, ...) on first call."""

    def setUp(self):
        cache.clear()

    def test_cache_add_called_before_incr_anon(self):
        """cache.add must be called before cache.incr for anonymous requests."""
        call_order = []

        def mock_add(key, value, timeout=None):
            call_order.append(("add", key, value))
            return True

        def mock_incr(key):
            call_order.append(("incr", key))
            return 1

        request = _make_anon_request()

        with patch("apps.payments.views.donation.cache") as mock_cache:
            mock_cache.add.side_effect = mock_add
            mock_cache.incr.side_effect = mock_incr

            result = _check_donation_rate_limit(request)

        self.assertFalse(result, "count=1 should be within limit (returns False = not exceeded)")
        self.assertEqual(call_order[0][0], "add")
        self.assertEqual(call_order[1][0], "incr")

    def test_cache_set_never_called_donation(self):
        """cache.set must not be called — old TOCTOU branch must be gone."""
        request = _make_anon_request()

        with patch("apps.payments.views.donation.cache") as mock_cache:
            mock_cache.add.return_value = True
            mock_cache.incr.return_value = 1

            _check_donation_rate_limit(request)

            mock_cache.set.assert_not_called()

    def test_donation_rate_limit_allows_up_to_5(self):
        """Calls 1-5 should NOT be rate-limited; call 6 should be rate-limited."""
        user = User.objects.create_user(
            email=f"rl_don_{uuid.uuid4().hex[:6]}@example.com",
            password="testpass123",
        )
        request = _make_auth_request(user)
        for i in range(1, 6):
            result = _check_donation_rate_limit(request)
            self.assertFalse(result, f"Call {i} should be within limit (returns False)")
        result = _check_donation_rate_limit(request)
        self.assertTrue(result, "Call 6 should be rate-limited (returns True)")
