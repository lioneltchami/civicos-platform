"""
Celery application configuration for Govstack.

Workers are started with:
  celery -A config.celery worker -l info
  celery -A config.celery beat -l info
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

app = Celery("govstack")

# Read celery config from Django settings (CELERY_* keys)
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks in tasks.py files across all installed apps
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Diagnostic task — prints worker request info."""
    print(f"Request: {self.request!r}")
