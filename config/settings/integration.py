"""Local/CI integration settings using declared service dependencies.

This module intentionally inherits test safety defaults while replacing only the
service-backed components needed to exercise the release topology.
"""
from .test import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", default="civicos_test"),
        "USER": env("POSTGRES_USER", default="civicos"),
        "PASSWORD": env("POSTGRES_PASSWORD", default="ci_test_password"),
        "HOST": env("POSTGRES_HOST", default="127.0.0.1"),
        "PORT": env("POSTGRES_PORT", default="5432"),
        "ATOMIC_REQUESTS": True,
    }
}

REDIS_URL = env("REDIS_URL", default="redis://127.0.0.1:6379/15")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_EAGER_PROPAGATES = False
