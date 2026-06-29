"""
Stripe webhook endpoint for the GovStack Payments BB.

Security design:
- CSRF exempt: Stripe cannot send CSRF tokens. This is intentional and safe
  because we verify the Stripe-Signature header (HMAC-SHA256) before processing
  ANY payload content. Reject first, process second.
- Raw body: Django's request.body gives us the raw bytes before any decoding.
  We MUST verify the signature against raw bytes — JSON-parsing first would
  break the HMAC.
- 200 on success only: Return 400 for signature failures. Return 200 even for
  events we don't handle — Stripe treats non-200 as delivery failure and retries.
- Idempotency: The Celery task handles duplicate delivery (Stripe may send the
  same event multiple times). The view stores the WebhookEvent row; the task
  checks it before processing.

Logging:
- Log event_id and event_type (not payload content — may contain PII).
- Log the error TYPE only on signature failure, not the raw error message.
"""
import copy
import json
import logging

from django.conf import settings
from django.db import IntegrityError
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.payments.gateway import get_gateway
from apps.payments.models import GATEWAY_STRIPE, WebhookEvent

logger = logging.getLogger(__name__)


def _strip_pii_from_payload(payload: dict) -> dict:
    """
    Strip PII fields from a Stripe webhook payload before storing in DB.

    Stripe embeds billing_details (name, email, address) in charge objects.
    Under PIPEDA Article 4.5, we must not retain PII beyond what is necessary.
    We store the raw payload for debugging but strip cardholder PII fields.
    """
    payload = copy.deepcopy(payload)
    # Strip billing_details from charge objects in payment_intent events
    try:
        charges = payload["data"]["object"]["charges"]["data"]
        for charge in charges:
            if "billing_details" in charge:
                charge["billing_details"] = {"REDACTED": "PII stripped at ingestion"}
            if "payment_method_details" in charge:
                pm = charge["payment_method_details"]
                # Keep card brand/last4 but strip billing_details within
                for pm_type in pm.values():
                    if isinstance(pm_type, dict) and "billing_details" in pm_type:
                        pm_type["billing_details"] = {"REDACTED": "PII stripped at ingestion"}
    except (KeyError, TypeError):
        pass
    # Strip customer name/email from customer objects
    try:
        obj = payload["data"]["object"]
        for field in ("name", "email", "phone", "address"):
            if field in obj:
                obj[field] = "REDACTED"
    except (KeyError, TypeError):
        pass
    return payload


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """
    Receive and queue Stripe webhook events.

    Flow:
    1. Read raw body bytes
    2. Verify Stripe-Signature header (reject with 400 if invalid)
    3. Parse JSON
    4. Store WebhookEvent row (signature_verified=True, processed=False)
    5. Dispatch process_stripe_webhook.delay(webhook_event_pk)
    6. Return 200 immediately

    The view is intentionally thin — all business logic is in the Celery task.
    This keeps the HTTP response time < 100ms and lets us retry failed processing
    without receiving the webhook again.
    """
    payload_bytes = request.body

    # ── 1. Verify signature ───────────────────────────────────────────────────
    signature_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")

    gateway = get_gateway()
    if not gateway.verify_webhook_signature(payload_bytes, signature_header, webhook_secret):
        logger.warning(
            "payments.webhook.signature_invalid remote_addr=%s",
            request.META.get("REMOTE_ADDR", ""),
        )
        return HttpResponse("Invalid signature", status=400)

    # ── 2. Parse JSON ─────────────────────────────────────────────────────────
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.error(
            "payments.webhook.json_parse_error type=%s",
            type(exc).__name__,
        )
        return HttpResponse("Invalid JSON", status=400)

    gateway_event_id = payload.get("id", "")
    event_type = payload.get("type", "")

    if not gateway_event_id or not event_type:
        logger.error("payments.webhook.missing_id_or_type")
        return HttpResponse("Missing event id or type", status=400)

    logger.info(
        "payments.webhook.received gateway_event_id=%s event_type=%s",
        gateway_event_id,
        event_type,
    )

    # ── 3. Store WebhookEvent (idempotent — get_or_create on gateway_event_id) ─
    # FIX 7: Strip PII from payload before storing (PIPEDA Article 4.5).
    clean_payload = _strip_pii_from_payload(payload)

    # FIX 3: Wrap in IntegrityError handler to guard against concurrent insert
    # race — two workers receiving the same event simultaneously both attempt
    # get_or_create; the loser hits a unique constraint violation.
    try:
        webhook_event, created = WebhookEvent.objects.get_or_create(
            gateway_event_id=gateway_event_id,
            defaults={
                "gateway": GATEWAY_STRIPE,
                "event_type": event_type,
                "payload": clean_payload,
                "signature_verified": True,
                "processed": False,
            },
        )
    except IntegrityError:
        # Lost the race — another worker already inserted this event.
        logger.info(
            "payments.webhook.race_duplicate gateway_event_id=%s",
            gateway_event_id,
        )
        return JsonResponse({"status": "duplicate"})

    if not created:
        # Duplicate delivery — Stripe sends events at least once.
        # The task handles this case; we still return 200 so Stripe stops retrying.
        logger.info(
            "payments.webhook.duplicate gateway_event_id=%s processed=%s",
            gateway_event_id,
            webhook_event.processed,
        )
        return JsonResponse({"status": "duplicate"})

    # ── 4. Dispatch to Celery ─────────────────────────────────────────────────
    from apps.payments.tasks import process_stripe_webhook
    process_stripe_webhook.delay(str(webhook_event.pk))

    return JsonResponse({"status": "queued"})
