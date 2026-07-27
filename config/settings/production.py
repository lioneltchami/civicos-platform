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

# S3 storage — location/prefix note (Documents BB SSRF/storage audit fix):
#
# There is intentionally NO "location" (key-prefix) option here. Static files
# are served by whitenoise from local disk (see "staticfiles" below) — they
# are never written to this S3 bucket — so there is no other content in the
# bucket that a "media/" prefix would need to separate from. This bucket is
# 100% dedicated to apps.documents storage_key-addressed objects.
#
# Previously this dict set "location": "media", which caused every
# django-storages call (default_storage.open/.delete/.url — used by the
# ClamAV scan task, the quarantine-cleanup delete, retention.hard_delete(),
# and the citizen HTML download view) to silently resolve to
# "media/<storage_key>", while every RAW boto3 call in
# apps/documents/services/upload.py and download.py (the presigned POST the
# browser actually uploads to, plus head_object/get_object/presigned GET)
# used the bare, un-prefixed storage_key. The two families of calls were
# never looking at the same S3 object:
#   - The ClamAV scan task 404'd on every real upload (default_storage.open()
#     looked for "media/documents/..." which never existed) and treated that
#     as a PERMANENT storage failure, immediately quarantining every
#     document regardless of content.
#   - The quarantine-cleanup delete and retention.hard_delete() 404'd
#     silently (S3 DELETE on a non-existent key returns success), so
#     infected/purged files were NEVER actually removed from the bucket —
#     the real (un-prefixed) object was left behind indefinitely, a genuine
#     PIPEDA/Privacy Act disposal-integrity gap, not just a functional bug.
#   - The citizen HTML download view (both the small-file proxy and the
#     large-file presigned-redirect code paths) would 404 for every
#     legitimate ACTIVE document in production.
#
# If a shared multi-purpose bucket ever requires a key prefix again, it MUST
# be applied identically to every raw boto3 call site (see the module
# docstrings in apps/documents/services/upload.py and download.py for the
# full list) — ideally via one shared helper, not scattered literals, so
# this class of bug cannot reoccur.
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        "OPTIONS": {
            # AWS_S3_ENDPOINT_URL (below) lets this same config point at an
            # S3-compatible on-prem/staging endpoint (e.g. MinIO) instead of
            # real AWS S3, without any code changes — used for manual
            # ClamAV+S3 verification where real AWS access is unavailable.
            "endpoint_url": env("AWS_S3_ENDPOINT_URL", default=None),
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

# ---------------------------------------------------------------------------
# GovStack Payments BB — BB whitelist enforcement (GAP-4)
# ---------------------------------------------------------------------------

# Controls whether IsTrustedSourceBB.has_permission() validates the
# X-Registering-Institution-ID header against the GovStackRegisteredBB
# database table (whitelist mode) or accepts any non-empty, ≤ 20-char value.
#
# Production deployments MUST set this to True (the default here).
# GovStack harness environments set GOVSTACK_REQUIRE_REGISTERED_BB=False via
# environment variable IF the harness sends a non-whitelisted institution ID.
# After running seed_govstack_vouchers (which seeds GovStackRegisteredBB
# with bb_id="GS-HARNESS"), the harness ID passes even when this is True.
# Never hardcode False in this file — use the env var for per-environment control.
#
# Handled by apps/payments/govstack_auth.IsTrustedSourceBB.has_permission().
GOVSTACK_REQUIRE_REGISTERED_BB = env.bool("GOVSTACK_REQUIRE_REGISTERED_BB", default=True)

# ---------------------------------------------------------------------------
# GovStack Payments BB — voucher endpoint JWT enforcement
# ---------------------------------------------------------------------------

# Controls whether Bearer JWT authentication is required on the voucher
# redemption and status-check endpoints:
#   POST /govstack/payments/vouchers/voucher_redemption
#   GET  /govstack/payments/vouchers/voucherstatuscheck/{serial}
#   PATCH /govstack/payments/vouchers/voucherstatuscheck/{serial}
#
# Production deployments MUST set this to True (the default here).
# GovStack harness environments set GOVSTACK_VOUCHER_REQUIRE_JWT=False via
# environment variable because the harness cannot supply a Bearer JWT.
# Never hardcode False in this file — use the env var for per-environment control.
#
# Handled by apps/payments/govstack_auth.HasVoucherJWT.has_permission().
GOVSTACK_VOUCHER_REQUIRE_JWT = env.bool("GOVSTACK_VOUCHER_REQUIRE_JWT", default=True)

# ---------------------------------------------------------------------------
# GovStack Payments BB — voucher Gov_Stack_BB registry enforcement (P2)
# ---------------------------------------------------------------------------

# Controls whether the ``Gov_Stack_BB`` field carried in the REQUEST BODY of the
# voucher endpoints is additionally validated against the GovStackRegisteredBB
# table (a real allowlist) on top of the always-on sentinel blocklist:
#   POST  /govstack/payments/vouchers/voucher_preactivation   → 460 on failure
#   PATCH /govstack/payments/vouchers/voucher_activation      → 460 on failure
#   POST  /govstack/payments/vouchers/voucher_redemption      → 460 on failure
#   PATCH /govstack/payments/vouchers/voucherstatuscheck/{s}  → 463 on failure
# (The GET status-check endpoint carries no Gov_Stack_BB field at all and is
#  therefore unaffected.)
#
# This is a PRODUCTION-HARDENING control, not a harness-conformance control.
# The live GovStack harness never exercises genuine "well-formed but
# unregistered BB" rejection: every negative Gov_Stack_BB scenario upstream
# uses one of two fixed sentinel strings ("not_exist", "invalid_bb"), both of
# which are handled unconditionally by the blocklist in
# apps/payments/govstack_services._is_known_invalid_gov_stack_bb().  So nothing
# below is harness-verified — do not describe it as such.
#
# Production deployments MUST set this to True (the default here).
# GovStack harness environments set GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB=False
# via environment variable, because the harness's own positive-scenario
# Gov_Stack_BB fixture values ("Gov_Stack_BB" on preactivation,
# "bb-digital-registries" elsewhere) cannot be stored in
# GovStackRegisteredBB.bb_id as that field is currently defined — see the
# comment block in apps/payments/management/commands/seed_govstack_vouchers.py.
# Never hardcode False in this file — use the env var for per-environment control.
#
# Handled by apps/payments/govstack_services._is_unregistered_gov_stack_bb().
GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB = env.bool(
    "GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB", default=True
)

# ---------------------------------------------------------------------------
# GovStack Scheduler BB — requestor token enforcement
# ---------------------------------------------------------------------------

# Controls whether GovStackSchedulerAuth validates request_token against the
# GovStackRegisteredBB whitelist (production mode) or accepts any non-empty
# requestor_id/request_token pair without a DB lookup (harness/dev mode).
#
# Production deployments MUST set this to True (the default here).
# GovStack harness environments set GOVSTACK_SCHEDULER_REQUIRE_TOKEN=False via
# environment variable because the harness cannot supply a whitelisted token
# before seed_govstack_vouchers has run. Mirrors GOVSTACK_REQUIRE_REGISTERED_BB
# above for the Payments BB.
# Never hardcode False in this file — use the env var for per-environment control.
#
# Handled by apps/appointments/govstack_auth.GovStackSchedulerAuth.authenticate().
GOVSTACK_SCHEDULER_REQUIRE_TOKEN = env.bool("GOVSTACK_SCHEDULER_REQUIRE_TOKEN", default=True)

# ---------------------------------------------------------------------------
# GovStack Payments BB — P2G Payer-FI whitelist enforcement (Issue B)
# ---------------------------------------------------------------------------

# Controls whether IsTrustedPayerFI.has_permission() validates the
# X-PayerFI-Id header (or accepted X-PayerFI-ID / PayerFI-Id spelling
# variants) against the GovStackRegisteredBB database table (whitelist mode,
# reusing the SAME table as GOVSTACK_REQUIRE_REGISTERED_BB — no separate
# model) or accepts any non-empty, <= 20-char value. Gates the 4 P2G bill
# payment endpoints:
#   GET  /govstack/payments/bills/{bill_id}
#   POST /govstack/payments/billTransferRequests
#   POST /govstack/payments/bills/{bill_id}/mark-paid
#   GET  /govstack/payments/transferRequests/{transfer_request_id}
#
# Kept as its own flag rather than reusing GOVSTACK_REQUIRE_REGISTERED_BB so
# Payer-FI enforcement can be rolled out independently of G2P/voucher
# enforcement.
#
# Production deployments MUST set this to True (the default here).
# GovStack harness environments set GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=False
# via environment variable IF the harness sends a non-whitelisted Payer-FI ID.
# There is currently zero P2G harness coverage (no bill/p2g/transferRequest
# reference anywhere in test/openAPI/features/), so this flag's harness-mode
# behaviour is not validated against a real Cucumber harness — it exists to
# close a real production security gap, not to satisfy harness conformance.
# Never hardcode False in this file — use the env var for per-environment control.
#
# Note: mark-bill-paid uses RequirePayerFI, not IsTrustedPayerFI — it ALWAYS
# requires the X-PayerFI-Id header regardless of this flag's value, because it
# mutates real bill state, has zero harness coverage to protect, and carries
# no idempotency key of its own. This flag still controls whether that
# endpoint's header is whitelist-checked once present.
#
# Handled by apps/payments/govstack_auth.IsTrustedPayerFI.has_permission()
# (and its fail-closed subclass, apps/payments/govstack_auth.RequirePayerFI).
GOVSTACK_REQUIRE_REGISTERED_PAYER_FI = env.bool(
    "GOVSTACK_REQUIRE_REGISTERED_PAYER_FI", default=True
)

# ---------------------------------------------------------------------------
# GovStack Payments BB — P2G tenant-scoping header validation
# ---------------------------------------------------------------------------

# Controls whether GovStackAPIView._validate_platform_tenant_id() requires
# the X-Platform-TenantId header (or the Platform-TenantId spelling variant
# used on 2 of the 15 live P2G YAMLs) to be present on the 4 P2G bill payment
# endpoints:
#   GET  /govstack/payments/bills/{bill_id}
#   POST /govstack/payments/billTransferRequests
#   POST /govstack/payments/bills/{bill_id}/mark-paid
#   GET  /govstack/payments/transferRequests/{transfer_request_id}
#
# This is a TENANT-SCOPING validation concern, distinct from
# GOVSTACK_REQUIRE_REGISTERED_PAYER_FI above (caller identity). It does NOT
# gate a whitelist lookup — there is no tenant registry table in this
# codebase, and the live spec's only constraint on this header is presence +
# maxLength: 20, not "is this a known tenant". A present-but-invalid
# (oversized) header is always rejected with HTTP 400 regardless of this
# flag's value; this flag only controls whether an ABSENT header is
# tolerated.
#
# Production deployments MUST set this to True (the default here).
# GovStack harness environments set GOVSTACK_REQUIRE_PLATFORM_TENANT_ID=False
# via environment variable, because there is currently zero P2G harness
# coverage (confirmed: no "tenantid" match anywhere in test/openAPI/) and the
# harness does not send this header. Never hardcode False in this file — use
# the env var for per-environment control.
#
# Handled by apps/payments/govstack_views.GovStackAPIView._validate_platform_tenant_id().
GOVSTACK_REQUIRE_PLATFORM_TENANT_ID = env.bool(
    "GOVSTACK_REQUIRE_PLATFORM_TENANT_ID", default=True
)

# ---------------------------------------------------------------------------
# GOVSTACK_REQUIRE_CONSENT_AUTH
# ---------------------------------------------------------------------------
# Gates the small, deliberately narrow set of read-only Consent BB endpoints
# that the upstream GovStackWorkingGroup/bb-consent reference test harness
# calls with ZERO auth headers on every request (confirmed by reading
# test/gherkin/features/environment.py, smoke.py, and data_agreement.py
# directly — there is no auth setup step anywhere in that harness):
#
#   GET /govstack/consent/service/policy/{policyId}/
#   GET /govstack/consent/config/data-agreement/{dataAgreementId}/
#
# Mirrors GOVSTACK_REQUIRE_REGISTERED_BB's mode-gating pattern for the
# Payments BB. When True (the default here), both endpoints require
# IsAuthenticated (+ IsConsentAdminUser for the data-agreement endpoint) same
# as every other Consent view — no PII exposure in production. When False,
# GET on just these two endpoints is public; every other Consent endpoint
# (create/update/delete, records, signatures, audit log, PIPEDA export, etc.)
# is completely unaffected and remains fully authenticated regardless of this
# flag's value.
#
# Production deployments MUST set this to True (the default here).
# GovStack harness environments set GOVSTACK_REQUIRE_CONSENT_AUTH=False via
# environment variable, because the harness cannot supply any auth header.
# Never hardcode False in this file — use the env var for per-environment control.
#
# Handled by apps/consent/govstack_views._PublicReadOrAuthenticated.has_permission()
# and its two subclasses, PublicPolicyReadPermission and
# PublicDataAgreementReadPermission.
GOVSTACK_REQUIRE_CONSENT_AUTH = env.bool("GOVSTACK_REQUIRE_CONSENT_AUTH", default=True)
