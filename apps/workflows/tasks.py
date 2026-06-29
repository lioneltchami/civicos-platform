"""
Celery tasks for the workflows building block.

check_sla_breaches: runs every 15 minutes, marks overdue WorkItems.
"""

import logging

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

logger = logging.getLogger(__name__)


@shared_task(name="workflows.check_sla_breaches", bind=True, max_retries=3)
def check_sla_breaches_task(self):
    """
    Periodic task: mark overdue WorkItems as SLA-breached.

    Should be run every 15 minutes via Celery beat.
    Configure in CELERY_BEAT_SCHEDULE:
        "check-sla-breaches": {
            "task": "workflows.check_sla_breaches",
            "schedule": crontab(minute="*/15"),
        }
    """
    try:
        from apps.workflows.services import check_sla_breaches
        count = check_sla_breaches()
        if count:
            logger.info("SLA breach check: %d item(s) newly marked as breached.", count)
        return count
    except SoftTimeLimitExceeded:
        logger.warning(
            "check_sla_breaches_task: soft time limit exceeded — "
            "will retry at next schedule"
        )
        return  # Don't retry — next cron firing will pick up where this left off
    except Exception as exc:
        logger.exception("check_sla_breaches_task failed: %s", exc)
        raise self.retry(exc=exc, countdown=60)
