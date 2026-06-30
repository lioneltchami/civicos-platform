"""
Test settings for Govstack.

Optimized for speed and isolation:
- In-memory SQLite (fast, no real DB needed for unit tests)
- Synchronous Celery
- Dummy email backend
- No migrations (use --no-migrations flag with pytest-django)
- Simplified password hashing
"""

from .base import *  # noqa: F401, F403

# Generate a fresh RSA key pair for test use only (never used in production)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa as _rsa

_test_private_key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
_JWT_PRIVATE_KEY = _test_private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption(),
).decode("utf-8")
_JWT_PUBLIC_KEY = _test_private_key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode("utf-8")

SECRET_KEY = "test-secret-key-not-for-production"

DEBUG = False

# TESTING flag: suppresses FERNET_KEYS startup guard and other production-only
# checks (mirrors the convention used in apps/payments/models._get_fernet).
TESTING = True

ALLOWED_HOSTS = ["*"]

# ---------------------------------------------------------------------------
# Fast password hashing in tests
# ---------------------------------------------------------------------------

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# ---------------------------------------------------------------------------
# Database — use PostgreSQL but with a test-specific URL
# ---------------------------------------------------------------------------

# pytest-django creates and destroys the test DB automatically.
# To use SQLite for speed: uncomment the block below.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# ---------------------------------------------------------------------------
# Media — temp directory per test run
# ---------------------------------------------------------------------------

import tempfile  # noqa: E402

MEDIA_ROOT = tempfile.mkdtemp()

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# ---------------------------------------------------------------------------
# Celery — synchronous in tests
# ---------------------------------------------------------------------------

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# ---------------------------------------------------------------------------
# Cache — local memory
# ---------------------------------------------------------------------------

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# ---------------------------------------------------------------------------
# Security — relax for test client
# ---------------------------------------------------------------------------

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# ---------------------------------------------------------------------------
# REST Framework — disable throttling so rate limits don't interfere with tests
# ---------------------------------------------------------------------------

SIMPLE_JWT["ALGORITHM"] = "RS256"
SIMPLE_JWT["SIGNING_KEY"] = _JWT_PRIVATE_KEY
SIMPLE_JWT["VERIFYING_KEY"] = _JWT_PUBLIC_KEY

REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # inherit all base settings  # noqa: F405
    "DEFAULT_THROTTLE_CLASSES": [],
    # Keep named rates so view-level throttle classes (CitizenRateThrottle,
    # StaffRateThrottle) can instantiate without ImproperlyConfigured.
    # The rates are high enough that tests will never be throttled in practice.
    "DEFAULT_THROTTLE_RATES": {
        "citizen": "10000/minute",
        "staff": "10000/minute",
        "anon": "10000/minute",
        "token_obtain": "10000/minute",
    },
}

# ---------------------------------------------------------------------------
# Logging — suppress noise in tests but keep app loggers alive so
# assertLogs() works reliably without depending on re-enabling disabled loggers.
# Fix 26: disable_existing_loggers=False keeps all loggers active at import time.
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,   # Fix 26: keep app loggers active
    "handlers": {
        "null": {"class": "logging.NullHandler"},
    },
    "root": {
        "handlers": ["null"],
        "level": "WARNING",  # Suppress INFO/DEBUG noise in test output
    },
    "loggers": {
        # Explicitly configure apps.payments at DEBUG so assertLogs() reliably
        # captures output regardless of logger name drift. PIPEDA log assertions
        # depend on this (test_donation_receivers, test_receipt_tasks).
        "apps.payments": {
            "handlers": ["null"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}
