"""PostgreSQL-only evidence profile for SCH-02.1 publisher recovery."""

from .test import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", default="civicos_sch02"),  # noqa: F405
        "USER": env("POSTGRES_USER", default="civicos"),  # noqa: F405
        "PASSWORD": env("POSTGRES_PASSWORD", default="civicos"),  # noqa: F405
        "HOST": env("POSTGRES_HOST", default="db"),  # noqa: F405
        "PORT": env("POSTGRES_PORT", default="5432"),  # noqa: F405
        "ATOMIC_REQUESTS": True,
        "TEST": {"NAME": env("POSTGRES_TEST_DB", default="civicos_sch02_test")},  # noqa: F405
    }
}

# Migrations remain enabled. No SQLite fallback is configured in this profile.
MIGRATION_MODULES = {}
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
