"""
Celery tasks for the GovStack Payments BB.

Idempotency contract:
- process_stripe_webhook checks WebhookEvent.processed before processing.
  If processed is already True, it exits immediately.
  This makes it safe to call multiple times (Stripe retries, manual reruns).

Logging contract:
- Log gateway_event_id and event_type — never raw payload content.
- Log error TYPE only — error messages from Stripe may contain PII.

Retry policy:
- 3 retries with exponential backoff (60s, 120s, 240s).
- Do NOT retry on 400-class gateway errors (card declined etc.) — retrying
  a declined card won't help.

Error handling:
- On unhandled exception: mark WebhookEvent.processed = False, store error.
- Re-raise so Celery marks the task as FAILURE (visible in Flower/monitoring).

WebhookEvent field reference (actual model):
- payload         JSONField   — raw Stripe event dict
- processed       BooleanField — True once successfully handled
- processed_at    DateTimeField (nullable) — timestamp of successful processing
- error           TextField   — last processing error (blank if none)
- signature_verified BooleanField — must be True before processing
- gateway         CharField   — "stripe" | "moneris"
"""
import datetime
import logging
import types
from decimal import Decimal

from celery import shared_task
from django.db import transaction
from django.db import transaction as db_transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_stripe_webhook(self, webhook_event_pk: str) -> None:
    """
    Process a single Stripe webhook event.

    Args:
        webhook_event_pk: UUID string of the WebhookEvent row.

    Idempotency: Exits immediately if WebhookEvent.processed is True.
    All state changes are wrapped in a transaction — partial updates never persist.
    """
    from apps.payments.models import WebhookEvent
    from apps.payments.gateway import get_gateway
    from apps.payments.gateways.exceptions import GatewayError

    # ── Load the event ────────────────────────────────────────────────────────
    try:
        webhook_event = WebhookEvent.objects.get(pk=webhook_event_pk)
    except WebhookEvent.DoesNotExist:
        logger.error(
            "payments.task.webhook_event_not_found pk=%s",
            webhook_event_pk,
        )
        return

    # ── Idempotency gate ──────────────────────────────────────────────────────
    if webhook_event.processed:
        logger.info(
            "payments.task.skipping_already_processed "
            "gateway_event_id=%s",
            webhook_event.gateway_event_id,
        )
        return

    # ── Safety gate: signature must have been verified by the view ────────────
    if not webhook_event.signature_verified:
        logger.error(
            "payments.task.signature_not_verified "
            "gateway_event_id=%s — refusing to process",
            webhook_event.gateway_event_id,
        )
        webhook_event.error = "SignatureNotVerified: refusing to process unverified event"
        webhook_event.save(update_fields=["error"])
        return

    logger.info(
        "payments.task.processing "
        "gateway_event_id=%s event_type=%s",
        webhook_event.gateway_event_id,
        webhook_event.event_type,
    )

    # ── Parse event data via gateway ──────────────────────────────────────────
    try:
        gateway = get_gateway()
        event_type, event_data = gateway.parse_webhook_event(webhook_event.payload)
    except GatewayError as exc:
        logger.error(
            "payments.task.parse_error type=%s gateway_event_id=%s",
            type(exc).__name__,
            webhook_event.gateway_event_id,
        )
        webhook_event.error = f"{type(exc).__name__}: {exc.gateway_code}"
        webhook_event.save(update_fields=["error"])
        return

    # ── Dispatch to handler ───────────────────────────────────────────────────
    handler = _HANDLERS.get(event_type)
    if handler is None:
        logger.info(
            "payments.task.unhandled_event_type event_type=%s gateway_event_id=%s",
            event_type,
            webhook_event.gateway_event_id,
        )
        # Mark as processed so we don't reprocess on Stripe retries
        webhook_event.processed = True
        webhook_event.processed_at = timezone.now()
        webhook_event.error = ""
        webhook_event.save(update_fields=["processed", "processed_at", "error"])
        return

    try:
        with transaction.atomic():
            handler(event_data, webhook_event)
            webhook_event.processed = True
            webhook_event.processed_at = timezone.now()
            webhook_event.error = ""
            webhook_event.save(update_fields=["processed", "processed_at", "error"])
    except Exception as exc:
        logger.error(
            "payments.task.handler_error "
            "type=%s event_type=%s gateway_event_id=%s",
            type(exc).__name__,
            event_type,
            webhook_event.gateway_event_id,
        )
        # Store error type for ops visibility; don't include exc.args (may have PII)
        webhook_event.error = type(exc).__name__
        webhook_event.save(update_fields=["error"])

        # Retry on transient errors
        from apps.payments.gateways.exceptions import GatewayNetworkError, GatewayRateLimitError
        if isinstance(exc, (GatewayNetworkError, GatewayRateLimitError)):
            raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
        raise


# ── Event handlers ─────────────────────────────────────────────────────────────

def _handle_payment_intent_succeeded(event_data: dict, webhook_event) -> None:
    """
    Create a Payment record and advance PaymentIntent to COMPLETED.

    Called inside an atomic transaction by process_stripe_webhook.
    If a Payment row already exists for this charge, do nothing (idempotent).

    Note on model fields (actual Payment model):
    - Payment.intent  is the FK to PaymentIntent (not payment_intent)
    - Payment.paid_at is required (DateTimeField)
    - Payment.net_amount is computed in Payment.save() as amount_paid - processor_fee

    State machine: PENDING → PROCESSING → COMPLETED (two-step transition required).
    Signal emission deferred to on_commit() so it fires only after DB commits.
    """
    from apps.payments.models import Payment, PaymentIntent, PaymentAuditEntry

    gateway_intent_id = event_data.get("gateway_intent_id", "")
    gateway_charge_id = event_data.get("gateway_charge_id", "")

    if not gateway_intent_id:
        logger.error(
            "payments.handler.payment_intent_succeeded.missing_intent_id "
            "gateway_event_id=%s",
            webhook_event.gateway_event_id,
        )
        return

    # Idempotency: skip if Payment already exists for this charge (inside atomic)
    if gateway_charge_id and Payment.objects.filter(gateway_charge_id=gateway_charge_id).exists():
        logger.info(
            "payments.handler.payment_intent_succeeded.already_processed "
            "gateway_charge_id=%s",
            gateway_charge_id,
        )
        return

    try:
        intent = PaymentIntent.objects.select_for_update().get(
            gateway_intent_id=gateway_intent_id
        )
    except PaymentIntent.DoesNotExist:
        logger.error(
            "payments.handler.payment_intent_succeeded.intent_not_found "
            "gateway_intent_id=%s",
            gateway_intent_id,
        )
        return

    # Validate card_last_four (PCI DSS — never more than 4 digits)
    card_last_four = event_data.get("card_last_four") or None
    if card_last_four and len(card_last_four) > 4:
        logger.error(
            "payments.handler.card_last_four_too_long gateway_event_id=%s",
            webhook_event.gateway_event_id,
        )
        card_last_four = card_last_four[-4:]  # Take only last 4

    amount_paid = event_data.get("amount_paid", Decimal("0.00"))
    processor_fee = event_data.get("processor_fee", Decimal("0.00"))

    # FIX 5: Parse paid_at from Stripe charge.created (Unix timestamp integer as string)
    paid_at_raw = event_data.get("paid_at", "")
    try:
        paid_at = datetime.datetime.fromtimestamp(int(paid_at_raw), tz=datetime.timezone.utc)
    except (ValueError, TypeError):
        logger.warning(
            "payments.handler.paid_at_parse_failed "
            "gateway_event_id=%s paid_at_raw_type=%s",
            webhook_event.gateway_event_id,
            type(paid_at_raw).__name__,
        )
        paid_at = timezone.now()

    # FIX 1: Two-step transition PENDING → PROCESSING → COMPLETED
    # ALLOWED_TRANSITIONS: PENDING→[PROCESSING, CANCELLED, FAILED], PROCESSING→[COMPLETED, FAILED]
    # Direct PENDING → COMPLETED is not allowed.
    if intent.status == PaymentIntent.STATUS_PENDING:
        intent.transition(PaymentIntent.STATUS_PROCESSING)
    intent.transition(PaymentIntent.STATUS_COMPLETED)

    payment = Payment.objects.create(
        intent=intent,
        amount_paid=amount_paid,
        processor_fee=processor_fee,
        # net_amount computed in Payment.save() as amount_paid - processor_fee
        gateway_charge_id=gateway_charge_id,
        payment_method_type=event_data.get("payment_method_type", "card"),
        card_last_four=card_last_four,
        card_brand=event_data.get("card_brand") or None,
        paid_at=paid_at,
    )

    # Append audit entry — PaymentAuditEntry fields: action, actor (FK nullable),
    # payment_intent (FK nullable), payment (FK nullable), actor_ip, details
    PaymentAuditEntry.objects.create(
        payment_intent=intent,
        payment=payment,
        action="payment_completed",
        actor=None,      # system event
        actor_ip="",
        details={
            "gateway_event_id": webhook_event.gateway_event_id,
            "payment_pk": str(payment.pk),
        },
    )

    logger.info(
        "payments.handler.payment_intent_succeeded.done "
        "gateway_intent_id=%s payment_pk=%s",
        gateway_intent_id,
        str(payment.pk),
    )

    # FIX 4: Emit domain signal AFTER commit via on_commit() to avoid
    # signal receivers seeing uncommitted DB state or firing on rollback.
    intent_pk = str(intent.pk)
    payment_pk = str(payment.pk)

    def _send_payment_completed_signal(intent_pk=intent_pk, payment_pk=payment_pk):
        from apps.payments.models import PaymentIntent as _PaymentIntent, Payment as _Payment
        from apps.payments.signals import payment_completed
        try:
            _intent = _PaymentIntent.objects.get(pk=intent_pk)
            _payment = _Payment.objects.get(pk=payment_pk)
            payment_completed.send(
                sender=_Payment,
                payment_intent=_intent,
                payment=_payment,
            )
        except Exception:
            pass  # Never let signal errors crash post-commit hooks

    db_transaction.on_commit(_send_payment_completed_signal)


def _handle_payment_intent_failed(event_data: dict, webhook_event) -> None:
    """
    Advance PaymentIntent to FAILED and log audit entry.

    State machine: PENDING → FAILED is allowed directly per ALLOWED_TRANSITIONS.
    PROCESSING → FAILED is also allowed. Both paths are handled below.
    Signal emission deferred to on_commit() so it fires only after DB commits.
    """
    from apps.payments.models import PaymentIntent, PaymentAuditEntry

    gateway_intent_id = event_data.get("gateway_intent_id", "")
    failure_reason = event_data.get("failure_reason", "unknown")

    if not gateway_intent_id:
        logger.error(
            "payments.handler.payment_intent_failed.missing_intent_id "
            "gateway_event_id=%s",
            webhook_event.gateway_event_id,
        )
        return

    try:
        intent = PaymentIntent.objects.select_for_update().get(
            gateway_intent_id=gateway_intent_id
        )
    except PaymentIntent.DoesNotExist:
        logger.error(
            "payments.handler.payment_intent_failed.intent_not_found "
            "gateway_intent_id=%s",
            gateway_intent_id,
        )
        return

    # ALLOWED_TRANSITIONS: PENDING→[PROCESSING, CANCELLED, FAILED], PROCESSING→[COMPLETED, FAILED]
    # Both PENDING→FAILED and PROCESSING→FAILED are valid direct transitions.
    # No two-step advance needed for the failure path.
    intent.transition(PaymentIntent.STATUS_FAILED)

    PaymentAuditEntry.objects.create(
        payment_intent=intent,
        action="payment_failed",
        actor=None,
        actor_ip="",
        details={
            "gateway_event_id": webhook_event.gateway_event_id,
            "failure_reason": failure_reason,
        },
    )

    # FIX 4: Defer signal to post-commit to avoid firing on rollback.
    intent_pk = str(intent.pk)

    def _send_payment_failed_signal(intent_pk=intent_pk, failure_reason=failure_reason):
        from apps.payments.models import PaymentIntent as _PaymentIntent
        from apps.payments.signals import payment_failed
        try:
            _intent = _PaymentIntent.objects.get(pk=intent_pk)
            payment_failed.send(
                sender=_PaymentIntent,
                payment_intent=_intent,
                failure_reason=failure_reason,
            )
        except Exception:
            pass  # Never let signal errors crash post-commit hooks

    db_transaction.on_commit(_send_payment_failed_signal)


def _handle_charge_refunded(event_data: dict, webhook_event) -> None:
    """
    Log that a charge has been refunded.

    Note: The Refund model has no status field — it records a completed refund
    event. We cannot update Refund status. Instead we log the event for audit.
    Future: if Refund gains a status field, update it here.
    """
    gateway_refund_id = event_data.get("gateway_refund_id", "")
    gateway_charge_id = event_data.get("gateway_charge_id", "")

    if not gateway_refund_id:
        logger.info(
            "payments.handler.charge_refunded.no_refund_id "
            "gateway_event_id=%s",
            webhook_event.gateway_event_id,
        )
        return

    logger.info(
        "payments.handler.charge_refunded gateway_event_id=%s",
        webhook_event.gateway_event_id,
    )


def _handle_subscription_deleted(event_data: dict, webhook_event) -> None:
    """Mark RecurringGiftPlan as cancelled when Stripe subscription is deleted."""
    from apps.payments.models import RecurringGiftPlan, PaymentAuditEntry, PLAN_STATUS_CANCELLED

    gateway_subscription_id = event_data.get("gateway_subscription_id", "")

    if not gateway_subscription_id:
        logger.error(
            "payments.handler.subscription_deleted.missing_id "
            "gateway_event_id=%s",
            webhook_event.gateway_event_id,
        )
        return

    try:
        plan = RecurringGiftPlan.objects.get(
            gateway_subscription_id=gateway_subscription_id
        )
    except RecurringGiftPlan.DoesNotExist:
        logger.warning(
            "payments.handler.subscription_deleted.not_found "
            "gateway_subscription_id=%s",
            gateway_subscription_id,
        )
        return

    if plan.status == PLAN_STATUS_CANCELLED:
        return  # Already cancelled — idempotent

    plan.status = PLAN_STATUS_CANCELLED
    plan.cancelled_at = timezone.now()
    plan.cancellation_reason = "subscription_deleted_by_gateway"
    plan.save(update_fields=["status", "cancelled_at", "cancellation_reason", "updated_at"])

    PaymentAuditEntry.objects.create(
        action="recurring_plan_cancelled",
        actor=None,
        actor_ip="",
        details={
            "gateway_event_id": webhook_event.gateway_event_id,
            "gateway_subscription_id": gateway_subscription_id,
        },
    )


def _handle_subscription_updated(event_data: dict, webhook_event) -> None:
    """Update RecurringGiftPlan status when Stripe subscription status changes."""
    from apps.payments.models import (
        RecurringGiftPlan,
        PLAN_STATUS_ACTIVE,
        PLAN_STATUS_PAUSED,
        PLAN_STATUS_CANCELLED,
    )

    gateway_subscription_id = event_data.get("gateway_subscription_id", "")
    new_status = event_data.get("status", "")

    if not gateway_subscription_id:
        return

    # Map Stripe subscription status → our PLAN_STATUS_* constants.
    # Note: RecurringGiftPlan only has active/paused/cancelled.
    # Stripe's past_due, incomplete, unpaid → no direct mapping; we keep current status.
    status_map = {
        "active": PLAN_STATUS_ACTIVE,
        "paused": PLAN_STATUS_PAUSED,
        "canceled": PLAN_STATUS_CANCELLED,
        "cancelled": PLAN_STATUS_CANCELLED,
    }

    our_status = status_map.get(new_status)
    if not our_status:
        logger.info(
            "payments.handler.subscription_updated.unmapped_status "
            "stripe_status=%s gateway_subscription_id=%s",
            new_status,
            gateway_subscription_id,
        )
        return

    update_fields = {"status": our_status}
    if our_status == PLAN_STATUS_CANCELLED:
        update_fields["cancelled_at"] = timezone.now()
        update_fields["cancellation_reason"] = f"stripe_status_{new_status}"

    updated = RecurringGiftPlan.objects.filter(
        gateway_subscription_id=gateway_subscription_id
    ).exclude(
        status=our_status
    ).update(**update_fields)

    if updated:
        logger.info(
            "payments.handler.subscription_updated.done "
            "gateway_subscription_id=%s new_status=%s",
            gateway_subscription_id,
            our_status,
        )


def _handle_invoice_payment_succeeded(event_data: dict, webhook_event) -> None:
    """
    Create a Donation + Payment when a Stripe Subscription renews.

    Fires on invoice.payment_succeeded for subscription invoices.
    Called inside an atomic transaction by process_stripe_webhook.
    Idempotent: skips if a Payment already exists for this charge_id.

    Note: donor_name_snapshot and donor_address_snapshot are CRA requirements.
    We populate them from the donor's User fields. If a RecurringGiftPlan is
    found, the donor is the plan.donor FK; address snapshot defaults to "" if
    not available (CRA allows blank for recurring donors where address is on file).

    Signal emission deferred to on_commit() so it fires only after DB commits.
    """
    from apps.payments.models import (
        Payment,
        PaymentIntent,
        Donation,
        RecurringGiftPlan,
        PaymentAuditEntry,
        DONATION_STATUS_COMPLETED,
    )
    from apps.payments.signals import donation_completed

    gateway_subscription_id = event_data.get("gateway_subscription_id", "")
    gateway_charge_id = event_data.get("gateway_charge_id", "")
    amount_paid = event_data.get("amount_paid", Decimal("0.00"))

    if not gateway_subscription_id:
        logger.error(
            "payments.handler.invoice_succeeded.missing_sub_id "
            "gateway_event_id=%s",
            webhook_event.gateway_event_id,
        )
        return

    logger.info(
        "payments.handler.invoice_succeeded sub_id=%s",
        gateway_subscription_id,
    )

    # Idempotency: skip if Payment already exists for this charge
    if gateway_charge_id and Payment.objects.filter(gateway_charge_id=gateway_charge_id).exists():
        logger.info(
            "payments.handler.invoice_succeeded.already_processed "
            "gateway_charge_id=%s",
            gateway_charge_id,
        )
        return

    # Find the RecurringGiftPlan for this subscription — row-locked for consistency
    try:
        plan = RecurringGiftPlan.objects.select_for_update().get(
            gateway_subscription_id=gateway_subscription_id
        )
    except RecurringGiftPlan.DoesNotExist:
        logger.warning(
            "payments.handler.invoice_succeeded.plan_not_found "
            "gateway_subscription_id=%s",
            gateway_subscription_id,
        )
        return

    donor = plan.donor

    # Get address snapshot from the linked donor user — address may not be on profile
    # Use getattr with nested fallback to avoid AttributeError
    donor_user = getattr(plan, "donor", None)
    address_parts = []
    if donor_user:
        for attr in ("address", "billing_address", "profile_address"):
            addr = getattr(donor_user, attr, None)
            if addr:
                address_parts = [addr]
                break
    donor_address_snapshot = "\n".join(address_parts) if address_parts else "Address on file — subscription renewal"

    # Create a new PaymentIntent for this renewal
    import uuid as _uuid_module
    new_idempotency_key = _uuid_module.uuid4()
    intent = PaymentIntent.objects.create(
        payer=donor,
        amount=amount_paid,
        tax_amount=Decimal("0.00"),
        currency="CAD",
        purpose=PaymentIntent.PURPOSE_DONATION,
        status=PaymentIntent.STATUS_COMPLETED,
        gateway=PaymentIntent.GATEWAY_STRIPE,
        gateway_intent_id="",  # Invoice renewals don't have a standalone PI id
        idempotency_key=new_idempotency_key,
        metadata={
            "gateway_subscription_id": gateway_subscription_id,
            "source": "recurring_renewal",
            "gateway_event_id": webhook_event.gateway_event_id,
        },
    )

    # Create the Payment record
    payment = Payment.objects.create(
        intent=intent,
        gateway_charge_id=gateway_charge_id or f"inv_{webhook_event.gateway_event_id}",
        amount_paid=amount_paid,
        processor_fee=Decimal("0.00"),
        # net_amount computed in Payment.save() as amount_paid - processor_fee
        payment_method_type=Payment.PAYMENT_METHOD_CARD,
        card_last_four=None,
        card_brand=None,
        paid_at=timezone.now(),
    )

    # donor_name_snapshot: CRA requirement — use display_name or full_name if available
    donor_name_snapshot = getattr(donor, "get_full_name", lambda: "")() or str(donor)

    # Create the Donation record
    donation = Donation.objects.create(
        payment_intent=intent,
        donor=donor,
        campaign=plan.campaign,
        amount=amount_paid,
        advantage_amount=Decimal("0.00"),
        # eligible_amount computed in Donation.save()
        eligible_amount=amount_paid,  # Will be recomputed in save()
        is_recurring=True,
        recurring_plan=plan,
        is_anonymous=False,
        status=DONATION_STATUS_COMPLETED,
        donor_name_snapshot=donor_name_snapshot or "Recurring Donor",
        donor_address_snapshot=donor_address_snapshot,
    )

    PaymentAuditEntry.objects.create(
        action="donation_created",
        actor=None,          # system / webhook event
        actor_ip="",
        payment_intent=intent,
        payment=payment,
        details={
            "gateway_event_id": webhook_event.gateway_event_id,
            "gateway_subscription_id": gateway_subscription_id,
            "donation_pk": str(donation.pk),
            "payment_pk": str(payment.pk),
        },
    )

    logger.info(
        "payments.handler.invoice_succeeded.done "
        "sub_id=%s donation_pk=%s payment_pk=%s",
        gateway_subscription_id,
        str(donation.pk),
        str(payment.pk),
    )

    # Defer signal to post-commit so it fires only after DB commits
    donation_pk = str(donation.pk)
    payment_pk = str(payment.pk)

    def _send_donation_completed(donation_pk=donation_pk, payment_pk=payment_pk):
        from apps.payments.models import Donation as _Donation, Payment as _Payment
        from apps.payments.signals import donation_completed as _signal
        try:
            _donation = _Donation.objects.get(pk=donation_pk)
            _payment = _Payment.objects.get(pk=payment_pk)
            _signal.send(sender=_Donation, donation=_donation, payment=_payment)
        except Exception:
            pass  # Never let signal errors crash post-commit hooks

    db_transaction.on_commit(_send_donation_completed)


def _handle_invoice_payment_failed(event_data: dict, webhook_event) -> None:
    """
    Mark RecurringGiftPlan as past_due when a subscription renewal fails.

    Fires on invoice.payment_failed for subscription invoices.
    RecurringGiftPlan.PLAN_STATUS_* constants only have active/paused/cancelled —
    no STATUS_PAST_DUE exists. We use PLAN_STATUS_PAUSED as the closest match
    and record the reason in cancellation_reason for audit purposes.
    """
    from apps.payments.models import RecurringGiftPlan, PLAN_STATUS_PAUSED

    gateway_subscription_id = event_data.get("gateway_subscription_id", "")

    if not gateway_subscription_id:
        logger.error(
            "payments.handler.invoice_failed.missing_sub_id "
            "gateway_event_id=%s",
            webhook_event.gateway_event_id,
        )
        return

    logger.info(
        "payments.handler.invoice_failed sub_id=%s",
        gateway_subscription_id,
    )

    updated = RecurringGiftPlan.objects.filter(
        gateway_subscription_id=gateway_subscription_id
    ).exclude(
        status=PLAN_STATUS_PAUSED,
    ).update(
        status=PLAN_STATUS_PAUSED,
        cancellation_reason="invoice_payment_failed",
    )

    if updated:
        logger.info(
            "payments.handler.invoice_failed.paused "
            "gateway_subscription_id=%s",
            gateway_subscription_id,
        )


# ── Handler dispatch table ─────────────────────────────────────────────────────
# FIX 8: Freeze the dispatch table to prevent runtime mutation bugs.
# types.MappingProxyType is a read-only view — assignment raises TypeError.

_HANDLERS = types.MappingProxyType({
    "payment_intent.succeeded": _handle_payment_intent_succeeded,
    "payment_intent.payment_failed": _handle_payment_intent_failed,
    "charge.refunded": _handle_charge_refunded,
    "customer.subscription.deleted": _handle_subscription_deleted,
    "customer.subscription.updated": _handle_subscription_updated,
    "invoice.payment_succeeded": _handle_invoice_payment_succeeded,
    "invoice.payment_failed": _handle_invoice_payment_failed,
})
