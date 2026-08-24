# Generated migration for Wave 4: Document Management BB
#
# Operations:
#   1. AlterModelOptions: add manage_legal_hold to Document.Meta.permissions
#   2. RunPython: register all 5 Celery Beat PeriodicTask entries via create_beat_schedule()
#
# The beat schedule registration is idempotent (uses get_or_create) — safe to
# re-run at any time. If django_celery_beat tables don't yet exist (e.g. in a
# test with a bare schema), the RunPython is wrapped in a try/except so the
# migration can still complete.
#
# Beat schedule (all times UTC):
#   01:00 — run_purge_expired_tokens
#   02:00 — run_disposal_schedule
#   03:00 — run_hard_delete_schedule
#   04:00 — cleanup_stale_pending_uploads
#   08:00 — notify_expiring_documents

import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def _register_beat_schedule(apps, schema_editor) -> None:  # noqa: ANN001
    """
    Migration wrapper for create_beat_schedule().

    Guarded by a try/except so this migration doesn't fail if
    django_celery_beat hasn't been set up yet (e.g. test environments
    without celery beat installed, or a bare DB before djcelerybeat
    migrations have run).

    In production this will always succeed because:
      - django_celery_beat is in INSTALLED_APPS
      - Beat migrations run before app migrations in the CI/CD pipeline
        (django_celery_beat is a dependency of apps.documents)
    """
    try:
        from apps.documents.tasks import create_beat_schedule

        create_beat_schedule()
    except Exception as exc:
        # Log but don't re-raise: the permission change (AlterModelOptions)
        # must still be applied even if beat registration fails.
        # Operators can re-run create_beat_schedule() manually via the shell.
        logger.warning(
            "0004_document_beat_schedule: beat schedule registration skipped — %s: %s",
            type(exc).__name__,
            exc,
        )


def _noop(apps, schema_editor) -> None:  # noqa: ANN001
    """No-op reverse for beat schedule registration."""
    pass


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("documents", "0003_document_permissions"),
        # django_celery_beat must be migrated before we can write PeriodicTask rows.
        # This is a soft dependency — listed so the migration runner applies celery
        # beat tables first, but the try/except in _register_beat_schedule provides
        # a safety net if this dependency ordering is ever violated.
        ("django_celery_beat", "0018_improve_crontab_helptext"),
    ]

    operations = [  # noqa: RUF012
        # 1. Add manage_legal_hold to Document permissions.
        migrations.AlterModelOptions(
            name="document",
            options={
                "ordering": ["-created_at"],
                "permissions": [
                    ("upload_document", "Can upload documents to public categories"),
                    (
                        "upload_staff_document",
                        "Can upload documents to staff-only categories",
                    ),
                    (
                        "manage_legal_hold",
                        "Can apply and release legal holds on documents",
                    ),
                ],
                "verbose_name": "Document",
                "verbose_name_plural": "Documents",
            },
        ),
        # 2. Register Celery Beat PeriodicTask entries.
        migrations.RunPython(
            _register_beat_schedule,
            reverse_code=_noop,
        ),
    ]
