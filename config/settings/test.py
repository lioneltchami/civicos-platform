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

SECRET_KEY = "test-secret-key-not-for-production"

DEBUG = False

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
# DATABASES = {
#     "default": {
#         "ENGINE": "django.db.backends.sqlite3",
#         "NAME": ":memory:",
#     }
# }

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
# Logging — silence in tests (let pytest capture output instead)
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"]},
}
