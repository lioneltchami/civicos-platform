"""PostgreSQL-only evidence profile for SCH-02.1 publisher recovery."""
from .test import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", default="civicos_sch02"),
        "USER": env("POSTGRES_USER", default="civicos"),
        "PASSWORD": env("POSTGRES_PASSWORD", default="civicos"),
        "HOST": env("POSTGRES_HOST", default="db"),
        "PORT": env("POSTGRES_PORT", default="5432"),
        "ATOMIC_REQUESTS": True,
        "TEST": {"NAME": env("POSTGRES_TEST_DB", default="civicos_sch02_test")},
    }
}

# Migrations remain enabled. No SQLite fallback is configured in this profile.
MIGRATION_MODULES = {}
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
