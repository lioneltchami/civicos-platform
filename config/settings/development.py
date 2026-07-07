"""
Development settings for CivicOS.

Extends base.py with developer-friendly defaults:
- DEBUG on
- Console email backend
- django-debug-toolbar enabled
- Relaxed security settings (HTTP, no HSTS)
- No S3 — local file storage
"""

import sys

from .base import *  # noqa: F401, F403
from .base import INSTALLED_APPS, MIDDLEWARE, env

DEBUG = True

SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="django-insecure-dev-key-change-before-production-do-not-use",
)

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "[::1]"]

SITE_URL = env("SITE_URL", default="http://localhost:8000")

# ---------------------------------------------------------------------------
# Dev apps & middleware
# ---------------------------------------------------------------------------

# Detect both `manage.py test` and `pytest` invocations.
# sys.argv[0] is "pytest" (or a path ending in /pytest) when invoked via pytest.
_TESTING = "test" in sys.argv or (
    bool(sys.argv) and ("pytest" in sys.argv[0] or sys.argv[0].endswith("/py.test"))
)

INSTALLED_APPS += [
    "django_extensions",
] + ([] if _TESTING else ["debug_toolbar"])

MIDDLEWARE = (
    [] if _TESTING else ["debug_toolbar.middleware.DebugToolbarMiddleware"]
) + MIDDLEWARE

INTERNAL_IPS = ["127.0.0.1", "::1"]

if not _TESTING:
    DEBUG_TOOLBAR_CONFIG = {
        "SHOW_COLLAPSED": True,
        "SHOW_TOOLBAR_CALLBACK": lambda request: DEBUG,
    }

# ---------------------------------------------------------------------------
# Email — print to console
# ---------------------------------------------------------------------------

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# ---------------------------------------------------------------------------
# Media — local filesystem
# ---------------------------------------------------------------------------

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        # Use simple static files storage in dev — no manifest hashing needed
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# ---------------------------------------------------------------------------
# Security — relaxed for local development
# ---------------------------------------------------------------------------

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False

# Allow inline scripts in development for debug toolbar
CSP_SCRIPT_SRC = ("'self'", "'unsafe-inline'")
CSP_STYLE_SRC = ("'self'", "'unsafe-inline'")

# ---------------------------------------------------------------------------
# Cache — local memory (no Redis required to run dev server)
# ---------------------------------------------------------------------------

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# ---------------------------------------------------------------------------
# Celery — run tasks synchronously in development (no worker needed)
# ---------------------------------------------------------------------------

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# ---------------------------------------------------------------------------
# JWT — generate a dev RSA key pair on-the-fly if not provided in .env
# This means developers don't need to manually generate keys locally.
# In production, JWT_PRIVATE_KEY / JWT_PUBLIC_KEY must be set explicitly.
# ---------------------------------------------------------------------------

if not SIMPLE_JWT.get("SIGNING_KEY"):  # type: ignore[name-defined]  # noqa: F405
    try:
        from cryptography.hazmat.primitives import serialization as _s
        from cryptography.hazmat.primitives.asymmetric import rsa as _rsa

        _dev_key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
        SIMPLE_JWT["SIGNING_KEY"] = _dev_key.private_bytes(  # type: ignore[name-defined]  # noqa: F405
            encoding=_s.Encoding.PEM,
            format=_s.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=_s.NoEncryption(),
        ).decode("utf-8")
        SIMPLE_JWT["VERIFYING_KEY"] = _dev_key.public_key().public_bytes(  # type: ignore[name-defined]  # noqa: F405
            encoding=_s.Encoding.PEM,
            format=_s.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
    except ImportError:
        pass  # cryptography not installed — JWT will fail; developer must install deps

# ---------------------------------------------------------------------------
# Logging — verbose output in dev
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {"level": "DEBUG", "handlers": ["console"]},
    "loggers": {
        "django.db.backends": {
            "handlers": ["console"],
            "level": "DEBUG",  # Log all SQL queries in development
            "propagate": False,
        },
    },
}
