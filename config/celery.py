"""
Celery application configuration for CivicOS.

Workers are started with:
  celery -A config.celery worker -l info
  celery -A config.celery beat -l info

Queue design (H9 fix):
  default   — general tasks (fallback)
  webhooks  — latency-sensitive Stripe webhook handlers; must not be starved
              by long-running batch tasks.  Run with higher concurrency.
  receipts  — slow batch tasks (annual receipt generation, PDF/email delivery).
              Isolated so a 10,000-donor run cannot delay webhook processing.

Start dedicated workers:
  # Webhook worker — handles default queue too so orphaned tasks don't pile up
  celery -A config.celery worker -Q webhooks,default -c 4 --loglevel=info

  # Receipt worker — isolated, lower concurrency (CPU/memory bound)
  celery -A config.celery worker -Q receipts -c 2 --loglevel=info
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

app = Celery("civicos")

# Read celery config from Django settings (CELERY_* keys)
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks in tasks.py files across all installed apps
app.autodiscover_tasks()

# ---------------------------------------------------------------------------
# H9: Queue routing — prevents annual receipt run from starving webhook tasks
# ---------------------------------------------------------------------------
# Tasks not listed here fall through to task_default_queue ("default").
# The queue= argument on the decorator (belt-and-suspenders) takes precedence
# when tasks are called via .delay() / .apply_async() without an explicit queue.
# ---------------------------------------------------------------------------

app.conf.task_default_queue = "default"

app.conf.task_routes = {
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

