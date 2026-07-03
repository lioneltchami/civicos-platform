"""
Base settings for CivicOS.

All settings shared across development, production, and test environments.
Environment-specific overrides live in development.py, production.py, test.py.

Configuration is driven by environment variables following the 12-factor app
pattern. Use django-environ to read from a .env file in development.
"""

from datetime import timedelta
from pathlib import Path

import environ

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

# Repo root: civicos/
ROOT_DIR = Path(__file__).resolve().parent.parent.parent

# Django project root: civicos/ (same as repo root in this layout)
APPS_DIR = ROOT_DIR / "apps"

env = environ.Env()

# Read .env file if present (development). In production, vars come from the
# process environment — no .env file on the server.
environ.Env.read_env(ROOT_DIR / ".env")

# ---------------------------------------------------------------------------
# Django core
# ---------------------------------------------------------------------------

SECRET_KEY = env("DJANGO_SECRET_KEY")

DEBUG = env.bool("DJANGO_DEBUG", default=False)

SITE_ID = 1

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

# Public URL of the portal — used in email links (PIPEDA export ready notifications).
# Must be set in production. Example: "https://portal.example.gov.ca"
SITE_URL = env("SITE_URL", default="")

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    "django.contrib.humanize",
    "django.contrib.sites",
]

WAGTAIL_APPS = [
    "wagtail.contrib.forms",
    "wagtail.contrib.redirects",
    "wagtail.contrib.settings",
    "wagtail.contrib.search_promotions",
    "wagtail.contrib.typed_table_block",
    "wagtail.contrib.simple_translation",
    "wagtail.embeds",
    "wagtail.sites",
    "wagtail.users",
    "wagtail.snippets",
    "wagtail.documents",
    "wagtail.images",
    "wagtail.search",
    "wagtail.admin",
    "wagtail",
    "modelcluster",
    "taggit",
]

THIRD_PARTY_APPS = [
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "django_otp.plugins.otp_static",
    "two_factor",
    "django_celery_beat",
    "django_celery_results",
    "storages",
    "anymail",
    # REST API
    "rest_framework",
    "rest_framework.authtoken",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    # CSP — provides the {% load csp %} template tag library used in templates
    "csp",
]

LOCAL_APPS = [
    "apps.core",
    "apps.cms",
    "apps.forms",
    "apps.portal",
    "apps.workflows",
    "apps.backoffice",
    "apps.auth_extension",  # Extends Django auth; named to avoid collision
    "apps.notifications",
    "apps.consent",
    "apps.audit",
    "apps.api",
    "apps.payments",
    "apps.reports",
    "apps.volunteers",
    "apps.documents",
]

INSTALLED_APPS = DJANGO_APPS + WAGTAIL_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "csp.middleware.CSPMiddleware",          # Must be before WhiteNoise so static responses carry CSP headers
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",           # i18n language detection
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",                 # MFA — sets request.user.otp_device
    "apps.core.middleware.WagtailMFAMiddleware",          # H-E: enforces OTP for /cms/ admin
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "wagtail.contrib.redirects.middleware.RedirectMiddleware",
    "apps.core.middleware.RequestIDMiddleware",            # Injects X-Request-ID
    "apps.core.middleware.AuditMiddleware",               # Attaches actor to request
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [ROOT_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "wagtail.contrib.settings.context_processors.settings",
                "apps.core.context_processors.site_settings",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://civicos:civicos@localhost:5432/civicos",
    )
}
DATABASES["default"]["ATOMIC_REQUESTS"] = True  # Wrap every request in a transaction
DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True  # Discard stale persistent connections

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://localhost:6379/0"),
    }
}

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

AUTH_USER_MODEL = "auth_extension.User"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",  # Primary
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",  # Legacy fallback
]

# Session security
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 1800  # 30 minutes inactivity timeout
SESSION_SAVE_EVERY_REQUEST = True  # Reset timeout on every request
SESSION_ENGINE = "django.contrib.sessions.backends.db"

CSRF_COOKIE_SECURE = True
# IMPORTANT: CSRF_COOKIE_HTTPONLY=True prevents JS from reading the cookie.
# AJAX views must read the CSRF token from <meta name="csrf-token"> in base.html.
# Do NOT use getCookie('csrftoken') — it will silently return undefined here.
CSRF_COOKIE_HTTPONLY = True

# ---------------------------------------------------------------------------
# django-allauth
# ---------------------------------------------------------------------------

ACCOUNT_AUTHENTICATION_METHOD = "email"
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_SESSION_REMEMBER = False  # Session expires on browser close by default
ACCOUNT_RATE_LIMITS = {
    "login_failed": "5/5m",  # 5 attempts per 5 minutes
}

ACCOUNT_ADAPTER = "apps.auth_extension.adapters.CivicOSAccountAdapter"
ACCOUNT_SIGNUP_FORM_CLASS = "apps.auth_extension.forms.CitizenSignupForm"
ACCOUNT_EMAIL_CONFIRMATION_EXPIRE_DAYS = 3
ACCOUNT_MAX_EMAIL_ADDRESSES = 1
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_LOGOUT_ON_PASSWORD_CHANGE = True
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "https"
SOCIALACCOUNT_ADAPTER = "apps.auth_extension.adapters.CivicOSSocialAccountAdapter"

LOGIN_URL = "two_factor:login"
LOGIN_REDIRECT_URL = "/"

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en"

LANGUAGES = [
    ("en", "English"),
    ("fr", "Français"),
]

LOCALE_PATHS = [ROOT_DIR / "locale"]

TIME_ZONE = "America/Toronto"

USE_I18N = True
USE_L10N = True
USE_TZ = True

WAGTAIL_I18N_ENABLED = True

WAGTAIL_CONTENT_LANGUAGES = LANGUAGES

# ---------------------------------------------------------------------------
# Static & media files
# ---------------------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = ROOT_DIR / "staticfiles"
STATICFILES_DIRS = [ROOT_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = ROOT_DIR / "media"

# Django 4.2+ uses STORAGES dict instead of STATICFILES_STORAGE / DEFAULT_FILE_STORAGE
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Production overrides STORAGES["default"] to S3 — see production.py

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@civicos.ca")
SERVER_EMAIL = env("SERVER_EMAIL", default="errors@civicos.ca")
EMAIL_SUBJECT_PREFIX = "[CivicOS] "

# Admins receive 500 error emails via AdminEmailHandler (requires LOGGING config)
# Format: comma-separated "Name:email@example.ca" pairs
ADMINS = [
    tuple(pair.split(":", 1))
    for pair in env.list("DJANGO_ADMINS", default=[])
]

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/1")
CELERY_RESULT_BACKEND = "django-db"
CELERY_CACHE_BACKEND = "django-cache"
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_TRACK_STARTED = True  # Expose STARTED state for monitoring / long-running tasks
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# Route tasks to named queues so payments and reports workers can scale independently.
# Consumers: celery -A config worker -Q payments
#            celery -A config worker -Q reports
# Default queue ("celery") handles everything else (notifications, portal, etc.)
CELERY_TASK_ROUTES = {
    # Payments BB — stripe webhooks, receipt generation, subscription management
    "apps.payments.tasks.*": {"queue": "payments"},
    # Analytics & Reporting BB — nightly snapshot computation
    "apps.reports.tasks.*": {"queue": "reports"},
    # Volunteer Management BB — shift reminders, expiry checks, impact snapshots
    "apps.volunteers.tasks.*": {"queue": "volunteers"},
    # Document Management BB — ClamAV scan, retention disposal, token purge
    "apps.documents.tasks.*": {"queue": "documents"},
    # Webhook-triggered tasks — fast, latency-sensitive.
    # Must reach workers within Stripe's 30-second retry window.
    "apps.payments.tasks.process_stripe_webhook": {"queue": "webhooks"},
    # Receipt / batch tasks — slow, latency-tolerant.
    # Isolated so a 10,000-donor annual run cannot delay webhook processing.
    "apps.payments.tasks_receipts.generate_annual_receipts": {"queue": "receipts"},
    "apps.payments.tasks_receipts.generate_and_send_receipt": {"queue": "receipts"},
    # kickoff_annual_receipts is a lightweight Beat trigger — runs on default queue;
    # it immediately delegates to generate_annual_receipts (receipts queue) via .delay().
}

# Task time limits — prevent runaway workers
CELERY_TASK_SOFT_TIME_LIMIT = 300   # 5 min — SoftTimeLimitExceeded is raised
CELERY_TASK_TIME_LIMIT = 360        # 6 min — worker SIGKILL after this

# ---------------------------------------------------------------------------
# Volunteer Management BB
# ---------------------------------------------------------------------------

# Provincial minimum hourly wages (CAD) — 2024 effective rates.
# Used to cross-check that honoraria don't inadvertently classify a volunteer
# as an employee under CRA guidelines.  Update annually from each province's
# Employment Standards Act schedule.
# Source: Government of Canada labour standards table, last updated 2024-10.
VOLUNTEER_MINIMUM_WAGES: dict[str, float] = {
    "AB": 15.00,   # Alberta
    "BC": 17.40,   # British Columbia
    "MB": 15.80,   # Manitoba
    "NB": 15.30,   # New Brunswick
    "NL": 15.60,   # Newfoundland & Labrador
    "NS": 15.70,   # Nova Scotia
    "NT": 16.05,   # Northwest Territories
    "NU": 16.00,   # Nunavut
    "ON": 17.20,   # Ontario
    "PE": 16.00,   # Prince Edward Island
    "QC": 15.75,   # Québec
    "SK": 14.00,   # Saskatchewan
    "YT": 17.59,   # Yukon
    "FED": 17.30,  # Federal (Canada Labour Code)
}

# CRA honorarium thresholds (CRA PC-025 / IT-334R2).
# alert_threshold  → coordinator is warned; T4A not yet required.
# t4a_threshold    → T4A slip must be issued; service layer sets t4a_required=True.
# hard_block       → service layer refuses to create the honorarium record.
VOLUNTEER_CRA_ALERT_THRESHOLD: float = 450.00
VOLUNTEER_CRA_T4A_THRESHOLD: float = 500.00
VOLUNTEER_CRA_HARD_BLOCK: float = 1_000.00

# Fernet keys for volunteer SIN encryption. MUST be set in production.py.
# Never use the Django SECRET_KEY for this purpose — key rotation would corrupt all SINs.
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Supports key rotation: list multiple keys; first is current, rest are decryption-only.
VOLUNTEER_SIN_FERNET_KEYS: list[str] = [
    k for k in env.list("VOLUNTEER_SIN_FERNET_KEYS", default=[]) if k
]

# ---------------------------------------------------------------------------
# Wagtail
# ---------------------------------------------------------------------------

WAGTAIL_SITE_NAME = env("WAGTAIL_SITE_NAME", default="CivicOS")
WAGTAILADMIN_BASE_URL = env("WAGTAILADMIN_BASE_URL", default="http://localhost:8000")  # Override to https:// in production env

WAGTAILIMAGES_IMAGE_MODEL = "cms.CustomImage"
WAGTAILDOCS_DOCUMENT_MODEL = "cms.CustomDocument"

WAGTAIL_ENABLE_WHATS_NEW_BANNER = False
WAGTAIL_SLIM_SIDEBAR = True

# Restrict document / image file types
# SVG excluded — requires django-svg-sanitize or equivalent before enabling
WAGTAILIMAGES_EXTENSIONS = ["gif", "jpg", "jpeg", "png", "webp"]
WAGTAILDOCS_EXTENSIONS = ["pdf", "docx", "xlsx", "csv", "txt"]

# Search
WAGTAILSEARCH_BACKENDS = {
    "default": {
        "BACKEND": "wagtail.search.backends.database",
    }
}

# ---------------------------------------------------------------------------
# Security headers (baseline — production.py tightens further)
# ---------------------------------------------------------------------------

X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# ── Content Security Policy ───────────────────────────────────────────────────
# Stripe requires js.stripe.com in script-src and frame-src.
# See: https://stripe.com/docs/security/guide#content-security-policy
CSP_DEFAULT_SRC = ("'self'",)
CSP_SCRIPT_SRC  = ("'self'", "https://js.stripe.com")
CSP_CONNECT_SRC = ("'self'", "https://api.stripe.com")
CSP_FRAME_SRC   = ("https://js.stripe.com",)
CSP_IMG_SRC     = ("'self'", "data:")
CSP_STYLE_SRC   = ("'self'",)  # H-G: unsafe-inline removed; nonces used for inline styles
CSP_FONT_SRC    = ("'self'",)
CSP_FRAME_ANCESTORS = ("'none'",)
# Wagtail CMS admin requires inline styles (JS-driven rich-text editor).
# Exempt /cms/ from the global CSP rather than re-adding unsafe-inline globally.
# Staff-only path; does not affect public payment or donation pages.
CSP_EXCLUDE_URL_PREFIXES = ("/cms/",)
# Nonces for inline <script> and <style> blocks in templates.
# django-csp injects {{ request.csp_nonce }} automatically.
CSP_INCLUDE_NONCE_IN = ["script-src", "style-src"]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "json": {
            "()": "apps.core.logging.JSONFormatter",
        },
    },
    "filters": {
        "require_debug_false": {"()": "django.utils.log.RequireDebugFalse"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "mail_admins": {
            "level": "ERROR",
            "filters": ["require_debug_false"],
            "class": "django.utils.log.AdminEmailHandler",
        },
    },
    "root": {"level": "INFO", "handlers": ["console"]},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "apps": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
        # Never log PII — these loggers are intentionally silent
        "apps.audit": {"handlers": [], "level": "CRITICAL", "propagate": False},
    },
}

# ---------------------------------------------------------------------------
# CivicOS-specific settings
# ---------------------------------------------------------------------------

CIVICOS = {
    # Minimum retention period in days for audit logs
    "AUDIT_LOG_RETENTION_DAYS": env.int("AUDIT_LOG_RETENTION_DAYS", default=2555),  # 7 years
    # Maximum file upload size in bytes (default 10 MB)
    "MAX_UPLOAD_SIZE": env.int("MAX_UPLOAD_SIZE", default=10 * 1024 * 1024),
    # Allowed MIME types for file uploads
    "ALLOWED_UPLOAD_MIME_TYPES": [
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/webp",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/csv",
    ],
    # Number of failed login attempts before account lockout
    "MAX_LOGIN_ATTEMPTS": env.int("MAX_LOGIN_ATTEMPTS", default=5),
    # GC Notify API key (used by notifications app)
    "GC_NOTIFY_API_KEY": env("GC_NOTIFY_API_KEY", default=""),

    # ── Document Management BB ────────────────────────────────────────────────

    # ClamAV daemon connection settings.
    # CLAMAV_HOST: empty string → ClamAV unavailable; uploads still accepted but skipped in dev.
    # CLAMAV_REQUIRED=True in production.py will raise ImproperlyConfigured if host unset.
    "CLAMAV_HOST": env("CLAMAV_HOST", default=""),
    "CLAMAV_PORT": env.int("CLAMAV_PORT", default=3310),
    # If True, startup fails when CLAMAV_HOST is not set (enforced in apps.documents.apps).
    # Set to True in production.py. False in dev (ClamAV is optional locally).
    "CLAMAV_REQUIRED": env.bool("CLAMAV_REQUIRED", default=False),

    # Maximum file upload sizes (bytes).
    # Citizens: 10 MB default. Staff uploads (backoffice): 50 MB.
    # Per-category overrides available via DocumentCategory.max_size_bytes.
    "DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES": env.int(
        "DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES", default=10 * 1024 * 1024
    ),
    "DOCUMENT_MAX_STAFF_UPLOAD_BYTES": env.int(
        "DOCUMENT_MAX_STAFF_UPLOAD_BYTES", default=50 * 1024 * 1024
    ),

    # Presigned POST URL TTL in seconds (how long the browser has to POST to S3).
    # Must be long enough for slow connections; short enough to limit replay attacks.
    "DOCUMENT_PRESIGNED_POST_TTL_SECONDS": env.int(
        "DOCUMENT_PRESIGNED_POST_TTL_SECONDS", default=900  # 15 minutes
    ),

    # Presigned download URL TTL in seconds (S3 GET URL returned to browser).
    # Short to limit sharing. Default 300 s = 5 minutes.
    "DOCUMENT_PRESIGNED_URL_TTL_SECONDS": env.int(
        "DOCUMENT_PRESIGNED_URL_TTL_SECONDS", default=300
    ),

    # DocumentAccessToken TTL in seconds (opaque token issued by Django download view).
    # Must be ≤ DOCUMENT_PRESIGNED_URL_TTL_SECONDS.
    "DOCUMENT_ACCESS_TOKEN_TTL_SECONDS": env.int(
        "DOCUMENT_ACCESS_TOKEN_TTL_SECONDS", default=300
    ),

    # Grace period (days) between soft-delete and hard-delete.
    # Hard deletion is irreversible. 30 days allows recovery from mistakes.
    # NIST SP 800-88 / OPC guidance: hard deletion must be irreversible.
    "DOCUMENT_HARD_DELETE_GRACE_DAYS": env.int(
        "DOCUMENT_HARD_DELETE_GRACE_DAYS", default=30
    ),

    # S3 prefix for document storage. Must NOT include a trailing slash.
    # Layout under this prefix: quarantine/{uuid}/{uuid}.bin
    #                           active/{uuid}/{uuid}.bin
    #                           deleted/{uuid}/{uuid}.bin
    "DOCUMENT_STORAGE_PREFIX": env("DOCUMENT_STORAGE_PREFIX", default="documents"),

    # ZIP bomb detection thresholds (CVE-2024-0450, Sep 2024).
    # Reject if a ZIP/DOCX/XLSX has more entries than this limit.
    "DOCUMENT_ZIP_MAX_ENTRIES": env.int("DOCUMENT_ZIP_MAX_ENTRIES", default=1000),
    # Reject if any ZIP entry's compression ratio exceeds this (uncompressed / compressed).
    "DOCUMENT_ZIP_MAX_RATIO": env.int("DOCUMENT_ZIP_MAX_RATIO", default=100),

    # Proxy threshold: files <= this size are proxied through Django (as file response).
    # Files > this size get a presigned URL redirect. Avoids memory pressure on workers.
    "DOCUMENT_PROXY_MAX_BYTES": env.int(
        "DOCUMENT_PROXY_MAX_BYTES", default=1 * 1024 * 1024  # 1 MB
    ),
}

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.api.authentication.CivicOSTokenAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.api.pagination.StandardPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_THROTTLE_CLASSES": [
        "apps.api.throttling.CitizenRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/hour",
        "citizen": "300/hour",
        "staff": "1000/hour",
        # Dedicated scope for the credential exchange endpoint — see TokenObtainThrottle.
        # 5/minute is generous for legitimate users and infeasible for brute-force.
        "token_obtain": "5/minute",
    },
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.api.exceptions.civicos_exception_handler",
    # NOTE: do NOT set UNAUTHENTICATED_USER: None — doing so causes DRF to raise
    # PermissionDenied (403) for unauthenticated requests instead of NotAuthenticated
    # (401), breaking RFC 7235 / government API contracts.  The default AnonymousUser
    # is correct and makes IsAuthenticated return 401 when no token is supplied.
}

# ---------------------------------------------------------------------------
# SimpleJWT — short-lived access tokens, daily refresh
# ---------------------------------------------------------------------------

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "RS256",
    "SIGNING_KEY": env("JWT_PRIVATE_KEY", default=None),
    "VERIFYING_KEY": env("JWT_PUBLIC_KEY", default=None),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "AUTH_HEADER_NAME": "HTTP_AUTHORIZATION",
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "TOKEN_OBTAIN_SERIALIZER": "rest_framework_simplejwt.serializers.TokenObtainPairSerializer",
    # Explicitly check is_active on token refresh — do NOT rely on simplejwt version
    # defaults.  Without this, a deactivated user can continue refreshing tokens
    # until their refresh token expires (up to 1 day).
    "USER_AUTHENTICATION_RULE": "rest_framework_simplejwt.authentication.default_user_authentication_rule",
}

# ---------------------------------------------------------------------------
# drf-spectacular — OpenAPI schema generation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Consent & Privacy (PIPEDA)
# ---------------------------------------------------------------------------

# Increment this string whenever the consent text shown to citizens is updated.
# The value is stored on ConsentRecord.consent_version so we have an audit trail
# of exactly which version of the text each citizen agreed to.
CONSENT_CURRENT_VERSION = "1.0"

# ---------------------------------------------------------------------------
# drf-spectacular — OpenAPI schema generation
# ---------------------------------------------------------------------------

SPECTACULAR_SETTINGS = {
    "TITLE": "CivicOS API",
    "DESCRIPTION": (
        "Government service delivery platform REST API. "
        "All endpoints require Bearer token authentication unless stated otherwise."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.IsAdminUser"],
    "COMPONENT_SPLIT_REQUEST": True,
    "SORT_OPERATIONS": False,
}

# ── IP / Proxy trust (django-ipware) ─────────────────────────────────────────
# django-ipware is used in apps/payments/views/donation.py to extract the real
# client IP behind load-balancers, reverse proxies (nginx, AWS ALB, Cloudflare).
#
# For a single trusted proxy (e.g. nginx in front of Gunicorn) set:
#   IPWARE_META_PRECEDENCE_ORDER = ("HTTP_X_FORWARDED_FOR",)
#
# For a fixed number of proxy hops (e.g. Cloudflare -> ALB -> Gunicorn):
#   NUM_PROXIES = 2   # number of trusted proxies between client and app
#
# Neither is set here because the correct value depends on the deployment
# topology. Override in production.py / .env once the proxy chain is known.
# Leaving both unset causes ipware to fall back to safe defaults (rightmost
# non-private IP from X-Forwarded-For), which is correct for most setups.

# ── Payments BB ──────────────────────────────────────────────────────────────
PAYMENT_GATEWAY = env("PAYMENT_GATEWAY", default="stripe")
STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY", default="")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", default="")
# Stripe publishable key is safe for front-end — kept in settings for template context
STRIPE_PUBLISHABLE_KEY = env("STRIPE_PUBLISHABLE_KEY", default="")

# ── Donation Receipt Email ────────────────────────────────────────────────────
RECEIPT_FROM_EMAIL = env("RECEIPT_FROM_EMAIL", default="receipts@example.ca")
