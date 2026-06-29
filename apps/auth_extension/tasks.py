"""
Celery tasks for auth_extension.

flush_expired_jwt_tokens — scheduled daily via Celery Beat to purge
expired rows from rest_framework_simplejwt.token_blacklist tables.
Without this, OutstandingToken and BlacklistedToken grow without bound.
"""
from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@shared_task(name="auth_extension.flush_expired_jwt_tokens", bind=True, max_retries=2)
def flush_expired_jwt_tokens(self):
    """
    Delete expired JWT tokens from the blacklist tables.

    Equivalent to: python manage.py flushexpiredtokens
    Safe to run multiple times (idempotent DELETE WHERE expires_at < now).
    """
    try:
        from django.utils import timezone
        from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

        cutoff = timezone.now()
        deleted_count, _ = OutstandingToken.objects.filter(
            expires_at__lt=cutoff
        ).delete()
        logger.info(
            "flush_expired_jwt_tokens: deleted %d expired token(s)", deleted_count
        )
        return {"deleted": deleted_count}
    except Exception as exc:
        logger.exception("flush_expired_jwt_tokens failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * 60)  # retry in 1 hour
