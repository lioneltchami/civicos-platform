"""
Package init for the ``config`` project package.

Bug fix (Payments BB certifiability audit, Finding 2 — real-server
async-dispatch gap): this file was previously EMPTY.

Standard Celery + Django integration (see Celery's own "First steps with
Django" docs) requires the Celery ``app`` instance defined in
``config/celery.py`` to be imported here, at ``config`` package import time —
NOT only by the ``celery -A config.celery worker`` CLI entrypoint. Without
this import, ``config/celery.py`` (and therefore
``app.config_from_object("django.conf:settings", namespace="CELERY")``, which
is what actually applies ``CELERY_TASK_ALWAYS_EAGER``, ``CELERY_BROKER_URL``,
``CELERY_TASK_ROUTES``, etc. from Django settings onto the Celery app) is
NEVER executed in any process that only imports Django itself — every
``manage.py`` command, the WSGI/ASGI server process, and any plain script
that does ``django.setup()`` and then imports a ``@shared_task``-decorated
task module.

In that situation, ``@shared_task`` (used by every task in this codebase,
e.g. ``apps.payments.govstack_tasks.process_bulk_payment_batch``) binds
lazily to Celery's own internal, UNCONFIGURED default app the first time
``.delay()``/``.apply_async()`` is called — not to the
``Celery("civicos")`` instance this project defines and configures. That
default app has ``task_always_eager=False`` (Celery's own hardcoded default,
regardless of ``settings.CELERY_TASK_ALWAYS_EAGER``) and Celery's own default
broker URL (``amqp://guest@localhost//``, NOT this project's configured
Redis broker) — so a real ``.delay()`` call attempts a real AMQP connection
to a broker that was never intended to be used, and fails with
``ConnectionRefusedError`` when nothing is listening there. In production
this would silently misroute every task; under
``config.settings.test`` (which correctly sets
``CELERY_TASK_ALWAYS_EAGER = True`` — that setting itself was never the
problem) it manifests as tasks failing to run eagerly at all whenever a test
exercises the real, unmocked ``.delay()`` path.

Confirmed via direct reproduction: calling
``process_bulk_payment_batch.delay(...)`` under
``DJANGO_SETTINGS_MODULE=config.settings.test`` (no custom throwaway
settings override, and no mocking of ``.delay()``) raised
``kombu.exceptions.OperationalError: [Errno 111] Connection refused`` via the
``pyamqp`` transport, and ``process_bulk_payment_batch.app.conf.task_always_eager``
printed ``False`` even though ``settings.CELERY_TASK_ALWAYS_EAGER`` is
``True`` — proof the task was bound to the wrong (unconfigured) Celery app
instance, not that eager mode itself was misconfigured.

The fix: import the properly-configured ``Celery`` app here, unconditionally,
so it is instantiated and Django-settings-configured as soon as ANY code
imports the ``config`` package — which happens for every Django entrypoint,
including ``manage.py test``. This is the well-established, textbook-correct
pattern; it does not change ``CELERY_TASK_ROUTES``, ``CELERY_TASK_ALWAYS_EAGER``,
or any other setting — it only ensures the app those settings are meant to
configure actually gets configured before any task is dispatched.
"""

from .celery import app as celery_app

__all__ = ("celery_app",)
