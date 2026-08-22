"""Dedicated durable Scheduler delivery tasks.

The task claim and outcome writes are transactionally fenced in
``scheduler_runtime``. Transport happens only after the claim transaction ends.
"""
from __future__ import annotations

from celery import shared_task
from django.utils import timezone
import requests

from apps.appointments.models import (
    GovStackSubscriberProfile,
    Resource,
    SchedulerOutbox,
    SchedulerRecipientDelivery,
    StaffProfile,
)
from apps.appointments.services import scheduler_runtime as runtime


def _resolve_push_destination(row: SchedulerRecipientDelivery) -> str:
    """Resolve a current push URL from an opaque stored recipient reference."""
    try:
        if row.recipient_kind == "subscriber":
            profile = GovStackSubscriberProfile.objects.get(pk=row.recipient_ref)
            return profile.alert_url if profile.alert_preference == "push" else ""
        if row.recipient_kind == "staff":
            staff = StaffProfile.objects.get(pk=row.recipient_ref)
            return staff.gs_alert_url if staff.gs_alert_preference == "push" else ""
        if row.recipient_kind == "resource":
            resource = Resource.objects.get(pk=row.recipient_ref)
            return resource.alert_url if resource.alert_preference == "push" else ""
    except (GovStackSubscriberProfile.DoesNotExist, StaffProfile.DoesNotExist, Resource.DoesNotExist):
        return ""
    return ""


@shared_task(
    name="appointments.deliver_scheduler_recipient",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=60,
    time_limit=75,
)
def deliver_scheduler_recipient(self, idempotency_key: str) -> dict:
    """Claim, transport, and persist one recipient outcome without DB-held I/O."""
    token = runtime.claim(idempotency_key=idempotency_key)
    if not token:
        return {"claimed": False}

    row = SchedulerRecipientDelivery.objects.select_related("schedule__message").get(idempotency_key=idempotency_key)
    destination_url = _resolve_push_destination(row)
    body = {
        "alert_schedule_id": str(row.schedule_id),
        "category": row.schedule.message.category,
        "message_body": row.schedule.message.message_body,
        "alert_datetime": row.schedule.alert_datetime.isoformat(),
        "correlation_id": row.correlation_id,
        "idempotency_key": row.idempotency_key,
    }

    # Reuse the established URL validation at the last responsible moment.
    from apps.appointments.tasks import _ALERT_DISPATCH_TIMEOUT_SECONDS, _is_safe_outbound_url

    if not destination_url or not _is_safe_outbound_url(destination_url):
        runtime.fail(idempotency_key=idempotency_key, lease_token=token, error_class="unsafe_destination")
        return {"claimed": True, "delivered": False, "error_class": "unsafe_destination"}

    try:
        response = requests.post(
            destination_url,
            json=body,
            timeout=_ALERT_DISPATCH_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            raise requests.exceptions.HTTPError("redirect_response")
        response.raise_for_status()
    except requests.exceptions.Timeout:
        runtime.fail(idempotency_key=idempotency_key, lease_token=token, error_class="timeout")
        return {"claimed": True, "delivered": False, "error_class": "timeout"}
    except requests.exceptions.HTTPError:
        runtime.fail(idempotency_key=idempotency_key, lease_token=token, error_class="http_error")
        return {"claimed": True, "delivered": False, "error_class": "http_error"}
    except requests.exceptions.RequestException:
        runtime.fail(idempotency_key=idempotency_key, lease_token=token, error_class="transport_error")
        return {"claimed": True, "delivered": False, "error_class": "transport_error"}
    except Exception:
        runtime.fail(idempotency_key=idempotency_key, lease_token=token, error_class="transport_exception")
        return {"claimed": True, "delivered": False, "error_class": "transport_exception"}

    runtime.succeed(idempotency_key=idempotency_key, lease_token=token)
    return {"claimed": True, "delivered": True}


@shared_task(
    name="appointments.publish_scheduler_outbox",
    acks_late=True,
    reject_on_worker_lost=True,
)
def publish_scheduler_outbox() -> dict:
    """Claim, publish outside the transaction, then token-fence the outcome."""
    published = 0
    while True:
        claim = runtime.claim_outbox(owner="scheduler-publisher")
        if claim is None:
            break
        outbox_id, idempotency_key, token, generation = claim
        try:
            deliver_scheduler_recipient.delay(idempotency_key)
        except TimeoutError as exc:
            runtime.mark_outbox_unknown_handoff(
                outbox_id=outbox_id, token=token, generation=generation,
                error_class=type(exc).__name__,
            )
            continue
        except Exception as exc:
            runtime.mark_outbox_failed(
                outbox_id=outbox_id, token=token, generation=generation,
                error_class=type(exc).__name__,
            )
            continue
        if runtime.mark_outbox_published(outbox_id=outbox_id, token=token, generation=generation):
            published += 1
    return {"published": published}


@shared_task(
    name="appointments.reap_scheduler_leases",
    acks_late=True,
    reject_on_worker_lost=True,
)
def reap_scheduler_leases() -> dict:
    return {"reaped": runtime.reap_expired()}
