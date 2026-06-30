"""
Structural tests for high-severity configuration bugs H-E and H-F.

H-E — Wagtail /cms/ admin must be protected by OTP/MFA enforcement.
H-F — Django cache and Celery broker must use separate Redis databases.
"""

import inspect

from django.test import SimpleTestCase, RequestFactory, override_settings
from django.conf import settings as django_settings


class WagtailMFAMiddlewareStructureTest(SimpleTestCase):
    """H-E: Wagtail /cms/ must be protected by OTP middleware."""

    def test_wagtail_mfa_middleware_in_middleware_list(self):
        """WagtailMFAMiddleware must appear in MIDDLEWARE."""
        middleware_dotpaths = " ".join(django_settings.MIDDLEWARE)
        self.assertIn(
            "WagtailMFAMiddleware",
            middleware_dotpaths,
            "WagtailMFAMiddleware is not listed in MIDDLEWARE — /cms/ admin is "
            "unprotected by MFA.",
        )

    def test_wagtail_mfa_middleware_follows_otp_middleware(self):
        """WagtailMFAMiddleware must be ordered after OTPMiddleware."""
        middleware = list(django_settings.MIDDLEWARE)
        otp_index = next(
            (i for i, m in enumerate(middleware) if "OTPMiddleware" in m), None
        )
        mfa_index = next(
            (i for i, m in enumerate(middleware) if "WagtailMFAMiddleware" in m), None
        )
        self.assertIsNotNone(otp_index, "django_otp.middleware.OTPMiddleware missing from MIDDLEWARE")
        self.assertIsNotNone(mfa_index, "WagtailMFAMiddleware missing from MIDDLEWARE")
        self.assertGreater(
            mfa_index,
            otp_index,
            "WagtailMFAMiddleware must come AFTER OTPMiddleware so the OTP "
            "verified flag is already set when the CMS check runs.",
        )

    def test_wagtail_mfa_middleware_uses_user_is_verified(self):
        """WagtailMFAMiddleware source must call user_is_verified (not a stub)."""
        from apps.core.middleware import WagtailMFAMiddleware

        source = inspect.getsource(WagtailMFAMiddleware)
        self.assertIn(
            "user_is_verified",
            source,
            "WagtailMFAMiddleware must call django_otp.user_is_verified to check "
            "whether the second factor has been completed.",
        )

    def test_wagtail_mfa_middleware_redirects_unverified_user(self):
        """WagtailMFAMiddleware must redirect authenticated but unverified users."""
        from unittest.mock import MagicMock, patch
        from apps.core.middleware import WagtailMFAMiddleware

        factory = RequestFactory()
        request = factory.get("/cms/pages/")

        # Simulate an authenticated but OTP-unverified staff user.
        user = MagicMock()
        user.is_authenticated = True
        request.user = user

        sentinel = object()

        def get_response(req):
            return sentinel

        middleware = WagtailMFAMiddleware(get_response)

        with patch("apps.core.middleware.user_is_verified", return_value=False):
            response = middleware(request)

        # Should NOT pass through to the sentinel; must redirect instead.
        self.assertIsNot(
            response,
            sentinel,
            "WagtailMFAMiddleware must intercept /cms/ requests from unverified users.",
        )
        # Django redirect responses have a Location header.
        self.assertIn(response.status_code, (301, 302),
                      "Expected a redirect response for unverified CMS access.")

    def test_wagtail_mfa_middleware_passes_verified_user(self):
        """WagtailMFAMiddleware must let OTP-verified users through."""
        from unittest.mock import MagicMock, patch
        from apps.core.middleware import WagtailMFAMiddleware

        factory = RequestFactory()
        request = factory.get("/cms/pages/")

        user = MagicMock()
        user.is_authenticated = True
        request.user = user

        sentinel = object()

        def get_response(req):
            return sentinel

        middleware = WagtailMFAMiddleware(get_response)

        with patch("apps.core.middleware.user_is_verified", return_value=True):
            response = middleware(request)

        self.assertIs(
            response,
            sentinel,
            "WagtailMFAMiddleware must pass verified users through without redirecting.",
        )

    def test_wagtail_mfa_middleware_exempts_login_page(self):
        """WagtailMFAMiddleware must not redirect the /cms/login/ path (infinite loop guard)."""
        from unittest.mock import MagicMock, patch
        from apps.core.middleware import WagtailMFAMiddleware

        factory = RequestFactory()
        request = factory.get("/cms/login/")

        user = MagicMock()
        user.is_authenticated = True
        request.user = user

        sentinel = object()

        def get_response(req):
            return sentinel

        middleware = WagtailMFAMiddleware(get_response)

        with patch("apps.core.middleware.user_is_verified", return_value=False):
            response = middleware(request)

        self.assertIs(
            response,
            sentinel,
            "/cms/login/ must be exempt from MFA enforcement to avoid an "
            "infinite redirect loop.",
        )

    def test_wagtail_cms_protected_by_mfa_middleware(self):
        """H-E: Wagtail /cms/ must be protected by OTP middleware or hook (canonical check)."""
        middleware_list = " ".join(django_settings.MIDDLEWARE)

        has_middleware = (
            "WagtailMFA" in middleware_list
            or "wagtail_mfa" in middleware_list.lower()
        )

        if not has_middleware:
            # Fallback: accept a wagtail_hooks approach.
            try:
                from apps.core import wagtail_hooks
                source = inspect.getsource(wagtail_hooks)
                self.assertIn(
                    "user_is_verified",
                    source,
                    "Wagtail admin must enforce OTP via hook or middleware",
                )
            except ImportError:
                self.fail(
                    "No WagtailMFA middleware and no wagtail_hooks with OTP enforcement"
                )


class RedisDatabaseSeparationTest(SimpleTestCase):
    """H-F: Cache and Celery broker must use separate Redis databases."""

    def test_redis_cache_and_broker_use_different_dbs(self):
        """H-F: Cache and Celery broker must not share the same Redis DB."""
        broker_url = django_settings.CELERY_BROKER_URL

        # Check the backend FIRST — LocMemCache has no LOCATION key, so we
        # must skip before attempting to read it.
        cache_backend = django_settings.CACHES["default"]["BACKEND"]
        if "redis" not in cache_backend.lower():
            self.skipTest(
                "Non-Redis cache backend in use (likely test settings) — "
                "Redis DB separation check not applicable."
            )

        # Only reachable when using a Redis cache backend.
        cache_location = django_settings.CACHES["default"]["LOCATION"]
        self.assertNotEqual(
            cache_location,
            broker_url,
            f"Cache ({cache_location!r}) and Celery broker ({broker_url!r}) "
            "must use separate Redis databases to prevent cache pollution and "
            "queue loss on cache.clear().",
        )

    def test_celery_broker_url_configured(self):
        """CELERY_BROKER_URL must be set and non-empty."""
        broker_url = getattr(django_settings, "CELERY_BROKER_URL", None)
        self.assertIsNotNone(broker_url, "CELERY_BROKER_URL must be set in settings.")
        self.assertTrue(bool(broker_url), "CELERY_BROKER_URL must not be empty.")

    def test_celery_broker_default_uses_db1(self):
        """Default CELERY_BROKER_URL must target Redis DB 1 (not DB 0 which is the cache)."""
        from django.test import override_settings
        import django.conf

        # Only meaningful when the broker is a Redis URL.
        broker_url = django_settings.CELERY_BROKER_URL
        if not broker_url.startswith("redis://") and not broker_url.startswith("rediss://"):
            self.skipTest("CELERY_BROKER_URL is not a Redis URL — skipping DB index check.")

        # The path component of the Redis URL encodes the database index.
        import urllib.parse
        parsed = urllib.parse.urlparse(broker_url)
        db_path = parsed.path  # e.g. "/1"
        self.assertNotEqual(
            db_path,
            "/0",
            f"CELERY_BROKER_URL ({broker_url!r}) uses Redis DB 0, which "
            "collides with the Django cache. Set the broker to DB 1 or higher.",
        )
