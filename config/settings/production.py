"""
Production settings for CivicOS.

Extends base.py with hardened security, S3 storage, and Sentry error tracking.
All sensitive values must be provided via environment variables — no defaults.
"""

import warnings

import sentry_sdk
from django.core.exceptions import ImproperlyConfigured
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.redis import RedisIntegration

from .base import *  # noqa: F401, F403
from .base import env

# Hard override — DEBUG must NEVER be True in production
DEBUG = False

# ---------------------------------------------------------------------------
# Security — strict in production
# ---------------------------------------------------------------------------

SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS")

# ALLOWED_HOSTS is read from DJANGO_ALLOWED_HOSTS env var (set in base.py).
# Default is [] which causes Django to reject all requests — set the env var in deployment.
# Example: DJANGO_ALLOWED_HOSTS=civicos.ca,www.civicos.ca
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "DJANGO_ALLOWED_HOSTS env var must be set in production. "
        "Example: DJANGO_ALLOWED_HOSTS=yourdomain.ca,www.yourdomain.ca"
    )

# Wagtail email links (password reset, notification emails) must use HTTPS in production.
# WAGTAILADMIN_BASE_URL must be set as an env var (e.g., https://cms.civicos.ca).
# The base.py default of http://localhost:8000 is deliberately not overridden here
# so a missing env var fails loudly rather than silently emitting http:// links.
WAGTAILADMIN_BASE_URL = env("WAGTAILADMIN_BASE_URL")  # No default — required in production

# ---------------------------------------------------------------------------
# Media / file storage — AWS S3
# ---------------------------------------------------------------------------

AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY")
AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME")
AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", default="ca-central-1")
AWS_S3_CUSTOM_DOMAIN = env("AWS_S3_CUSTOM_DOMAIN", default=None)
AWS_S3_FILE_OVERWRITE = False
AWS_DEFAULT_ACL = "private"  # Files are private; served via pre-signed URLs
AWS_S3_OBJECT_PARAMETERS = {
    "CacheControl": "max-age=86400",  # 1 day client cache
}
AWS_S3_SIGNATURE_VERSION = "s3v4"
AWS_QUERYSTRING_AUTH = True
AWS_QUERYSTRING_EXPIRE = 3600  # Pre-signed URL validity: 1 hour

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        "OPTIONS": {
            "location": "media",
        },
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# ---------------------------------------------------------------------------
# Email — anymail (configurable backend)
# ---------------------------------------------------------------------------

ANYMAIL = {
    "SENDGRID_API_KEY": env("SENDGRID_API_KEY", default=""),
    "MAILGUN_API_KEY": env("MAILGUN_API_KEY", default=""),
    "MAILGUN_SENDER_DOMAIN": env("MAILGUN_SENDER_DOMAIN", default=""),
}

EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="anymail.backends.sendgrid.EmailBackend",
)

# ---------------------------------------------------------------------------
# Logging — JSON format for log aggregation (Datadog, CloudWatch, etc.)
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "apps.core.logging.JSONFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {"level": "INFO", "handlers": ["console"]},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "apps": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # Audit logger intentionally silent — prevents PII from leaking into Sentry
        # via LoggingIntegration. Audit data lives exclusively in AuditLogEntry records.
        "apps.audit": {"handlers": [], "level": "CRITICAL", "propagate": False},
    },
}

# ---------------------------------------------------------------------------
# Sentry — error tracking & performance monitoring
# ---------------------------------------------------------------------------

# PII-filtering hooks are defined in apps/core/sentry.py so they can be
# imported and unit-tested without pulling in the sentry_sdk package.
from apps.core.sentry import before_breadcrumb as _before_breadcrumb  # noqa: E402
from apps.core.sentry import before_send as _before_send  # noqa: E402

SENTRY_DSN = env("SENTRY_DSN", default="")

if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[
            DjangoIntegration(transaction_style="url"),
            CeleryIntegration(),
            RedisIntegration(),
            LoggingIntegration(level=None, event_level=None),
        ],
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.1),
        profiles_sample_rate=env.float("SENTRY_PROFILES_SAMPLE_RATE", default=0.1),
        environment=env("SENTRY_ENVIRONMENT", default="production"),
        send_default_pii=False,  # NEVER send PII to Sentry
        before_breadcrumb=_before_breadcrumb,
        before_send=_before_send,
    )
else:
    warnings.warn(
        "SENTRY_DSN is not configured — unhandled exceptions will not be reported.",
        RuntimeWarning,
        stacklevel=2,
    )

# ---------------------------------------------------------------------------
# JWT RS256 asymmetric signing — required in production
# ---------------------------------------------------------------------------

SIMPLE_JWT["SIGNING_KEY"] = env("JWT_PRIVATE_KEY")   # no default — must be set
SIMPLE_JWT["VERIFYING_KEY"] = env("JWT_PUBLIC_KEY")   # no default — must be set

# ---------------------------------------------------------------------------
# Cache — Redis in production
# ---------------------------------------------------------------------------

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL"),
        "OPTIONS": {
            "socket_connect_timeout": 5,
            "socket_timeout": 5,
        },
    }
}

# ---------------------------------------------------------------------------
# Wagtail search — upgrade to Elasticsearch for large deployments
# ---------------------------------------------------------------------------

# Uncomment and configure when search volume warrants it:
# WAGTAILSEARCH_BACKENDS = {
#     "default": {
#         "BACKEND": "wagtail.search.backends.elasticsearch8",
#         "URLS": env.list("ELASTICSEARCH_URLS", default=["http://localhost:9200"]),
#         "INDEX": "civicos",
#         "TIMEOUT": 5,
#     }
# }
