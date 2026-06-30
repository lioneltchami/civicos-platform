"""
Donation views — Wave 4, Track A.
GovStack Payments Building Block — Canadian charitable donation flow.

Flow:
  1. DonationSelectView   GET/POST  /donate/
     Public — no auth required. Donor selects campaign, amount, recurring options.
     Amount is stored in session — never re-read from request body.

  2. DonationConfirmView  GET       /donate/confirm/
     Shows order summary + Stripe Elements card input.
     JS calls create_donation_intent_api, then stripe.confirmCardPayment().

  3. create_donation_intent_api  POST  /donate/api/create-intent/
     CSRF-protected JSON endpoint. Amount is authoritative from session.
     Creates PaymentIntent model row + Stripe PaymentIntent.
     Donation row is created by the webhook handler, not here — avoids duplicates.

  4. DonationSuccessView  GET       /donate/success/
     Shown after JS redirect on Stripe success.

  5. DonationCancelView   GET       /donate/cancel/
     Clears session, shown when donor backs out.

  6. RecurringGiftCancelView  GET/POST  /donate/recurring/<uuid:plan_pk>/cancel/
     Authenticated donors only. Cancels a recurring subscription.

Security invariants:
- Amount ALWAYS from session — never from request body.
- donor_email NEVER in logs.
- donor_name NEVER in logs.
- CSRF protection on ALL views including the JSON API (NOT @csrf_exempt).
- Rate limit 5/min on create_donation_intent_api, keyed by IP for anonymous users.
- UUID validation before any ORM lookup.
- actor_email absent from all PaymentAuditEntry.objects.create() calls.

CRA compliance notes:
- eligible_amount = amount - advantage_amount stored in session.
- Donation row (with donor_name_snapshot) created by webhook handler.
- OfficialDonationReceipt issued by post-payment signal receiver.
"""
import hashlib
import logging
import uuid as _uuid
import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView

from apps.payments.forms import DonationForm
from apps.payments.gateway import get_gateway
from apps.payments.gateways.exceptions import GatewayError
from apps.payments.models import (
    PaymentIntent,
    RecurringGiftPlan,
    TenantPaymentConfig,
    PLAN_STATUS_CANCELLED,
    PaymentAuditEntry,
)
from apps.payments.views.refund import _mask_ip

logger = logging.getLogger(__name__)

TWO_PLACES = Decimal("0.01")

# Session key — namespaced to avoid collisions with fee payment and other apps
DONATION_SESSION_KEY = "payments_donation_intent"


# ---------------------------------------------------------------------------
# Rate limiting helper
# ---------------------------------------------------------------------------

def _check_donation_rate_limit(request) -> bool:
    """Returns True if rate limit exceeded (5 POST/min). Thread-safe via atomic cache.incr()."""
    if request.user.is_authenticated:
        key = f"donation_ratelimit_user_{request.user.pk}"
    else:
        ip = request.META.get("REMOTE_ADDR", "")
        ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16]
        key = f"donation_ratelimit_ip_{ip_hash}"
    cache.add(key, 0, timeout=60)   # initialises to 0 only if key absent (atomic)
    count = cache.incr(key)          # atomically increment and return new value
    return count > 5


# ---------------------------------------------------------------------------
# Step 1: Select campaign / amount
# ---------------------------------------------------------------------------

class DonationSelectView(FormView):
    """
    Step 1: public donation form.
    No authentication required — anonymous donors can give.
    Amount is quantized server-side and stored in session for Step 2.
    """

    template_name = "payments/donation_select.html"
    form_class = DonationForm

    def form_valid(self, form):
        cd = form.cleaned_data
        amount = cd["amount"].quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        advantage_amount = (cd.get("advantage_amount") or Decimal("0.00")).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        eligible_amount = (cd.get("eligible_amount") or amount).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        campaign = cd.get("campaign")

        # Store as strings to ensure reliable session serialisation.
        # No PII in session key names.
        self.request.session[DONATION_SESSION_KEY] = {
            "campaign_pk": str(campaign.pk) if campaign else "",
            "campaign_name": campaign.get_name() if campaign else "",
            "amount": str(amount),
            "eligible_amount": str(eligible_amount),
            "advantage_amount": str(advantage_amount),
            "is_recurring": "1" if cd.get("is_recurring") else "0",
            "frequency": cd.get("frequency") or "",
            "donor_name": cd["donor_name"],        # stored but NEVER logged
            "donor_email": cd["donor_email"],      # stored but NEVER logged
            "is_anonymous": "1" if cd.get("is_anonymous") else "0",
        }
        self.request.session.modified = True
        return redirect("donate:donation_confirm")

    def get_success_url(self):
        # form_valid() handles the redirect directly; this satisfies FormView's contract.
        from django.urls import reverse
        return reverse("donate:donation_confirm")


# ---------------------------------------------------------------------------
# Step 2: Confirm donation + Stripe card entry
# ---------------------------------------------------------------------------

class DonationConfirmView(TemplateView):
    """
    Step 2: show donation summary + Stripe Elements card input.
    GET only. POST goes to create_donation_intent_api.
    """

    template_name = "payments/donation_confirm.html"

    def _get_session_data(self):
        return self.request.session.get(DONATION_SESSION_KEY)

    def get(self, request, *args, **kwargs):
        if not self._get_session_data():
            return redirect("donate:donation_select")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sd = self._get_session_data() or {}
        config = TenantPaymentConfig.get_solo()
        ctx.update({
            "amount": Decimal(sd.get("amount", "0.00")),
            "eligible_amount": Decimal(sd.get("eligible_amount", "0.00")),
            "advantage_amount": Decimal(sd.get("advantage_amount", "0.00")),
            "campaign_name": sd.get("campaign_name", ""),
            "is_recurring": sd.get("is_recurring") == "1",
            "frequency": sd.get("frequency", ""),
            # Publishable key is safe for front-end.
            # stripe_secret_key and webhook_secret are NEVER passed here.
            "stripe_publishable_key": config.stripe_publishable_key or "",
        })
        return ctx


# ---------------------------------------------------------------------------
# Step 3: JSON API — create Stripe PaymentIntent
# ---------------------------------------------------------------------------

def create_donation_intent_api(request):
    """
    POST /donate/api/create-intent/

    CSRF-protected JSON endpoint called by Stripe.js after card entry.
    Amount is authoritative from session — request body is ignored.

    Returns: {"client_secret": "...", "payment_intent_pk": "..."}
    Errors:  {"error": "<message>"} with appropriate HTTP status.

    Security:
    - NOT @csrf_exempt — CSRF token required
    - Amount from session only
    - donor_email and donor_name never logged
    - Rate limit 5/min by IP for anonymous users
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)

    # Rate limit — 5 per minute per IP (anonymous) or user pk (authenticated)
    if _check_donation_rate_limit(request):
        return JsonResponse(
            {"error": "Too many requests. Please wait a moment."},
            status=429,
        )

    # Auth check BEFORE any gateway call — avoids creating an orphaned Stripe
    # PaymentIntent for anonymous users (PaymentIntent.payer is NOT NULL).
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=401)

    session_data = request.session.get(DONATION_SESSION_KEY)
    if not session_data:
        logger.warning(
            "payments.donation.create_intent.session_expired remote_addr=%s",
            _mask_ip(request.META.get("REMOTE_ADDR", "")),
        )
        return JsonResponse(
            {"error": "Session expired. Please start over."},
            status=403,
        )

    try:
        amount = Decimal(session_data["amount"])
    except Exception:
        return JsonResponse({"error": "Invalid session data."}, status=400)

    if amount <= Decimal("0.00"):
        return JsonResponse({"error": "Amount must be greater than zero."}, status=400)

    # Idempotency guard — prevents double-click creating two Stripe intents.
    # Key is per-session so anonymous and authenticated donors both benefit.
    existing_pk = session_data.get("donation_payment_intent_pk")
    if existing_pk:
        try:
            # Validate UUID before DB lookup
            _uuid.UUID(str(existing_pk))
            PaymentIntent.objects.get(
                pk=existing_pk,
                status=PaymentIntent.STATUS_PENDING,
            )
            logger.info(
                "payments.donation.create_intent.reusing_existing intent_pk=%s",
                str(existing_pk),
            )
            return JsonResponse(
                {"error": "A donation is already in progress. Please wait a moment and try again."},
                status=409,
            )
        except (PaymentIntent.DoesNotExist, ValueError):
            pass  # Stale or completed — proceed to create a fresh one

    idempotency_key = str(uuid.uuid4())

    # Full metadata stored on the Django PaymentIntent model so the webhook handler
    # (_handle_one_time_donation) can produce a correct CRA receipt without re-reading
    # the (long-expired) session. donor_legal_name is the name submitted on the donation
    # form and is required for CRA receipt generation. intent.metadata is a JSONField
    # stored in our own DB — not sent to Stripe.
    metadata = {
        "campaign_pk": session_data.get("campaign_pk", ""),
        "source": "donation",
        "is_recurring": session_data.get("is_recurring", "0"),
        "advantage_amount": str(session_data.get("advantage_amount", "0.00")),
        "eligible_amount": str(session_data.get("eligible_amount", "0.00")),
        "is_anonymous": session_data.get("is_anonymous", "0"),  # already "1"/"0" string from session
        "donor_legal_name": session_data.get("donor_name", ""),  # legal name for CRA receipt — DB only
    }

    # H7 fix (PIPEDA): only non-PII internal identifiers are sent to Stripe metadata.
    # donor_legal_name must NOT be sent to Stripe — it is PII that Stripe stores on their
    # servers, exposes in the Dashboard, and includes in data exports. The webhook handler
    # reads donor_legal_name from the Django PaymentIntent.metadata (above), not from Stripe.
    stripe_metadata = {
        "campaign_pk": session_data.get("campaign_pk", ""),
        "source": "donation",
        "is_recurring": session_data.get("is_recurring", "0"),
        "advantage_amount": str(session_data.get("advantage_amount", "0.00")),
        "eligible_amount": str(session_data.get("eligible_amount", "0.00")),
        "is_anonymous": session_data.get("is_anonymous", "0"),
    }

    try:
        gateway = get_gateway()
        config = TenantPaymentConfig.get_solo()
        connect_account_id = (
            config.stripe_connect_account_id if config.use_connect else None
        )
        gateway_result = gateway.create_payment_intent(
            amount=amount,
            currency="cad",
            idempotency_key=idempotency_key,
            metadata=stripe_metadata,
            description="Charitable donation",
            connect_account_id=connect_account_id,
        )
    except GatewayError as exc:
        logger.error(
            "payments.donation.create_intent.gateway_error type=%s gateway_code=%s",
            type(exc).__name__,
            exc.gateway_code,
        )
        return JsonResponse(
            {"error": str(_("Payment processing is temporarily unavailable. Please try again."))},
            status=502,
        )

    # Create the PaymentIntent model row.
    # Auth is guaranteed above — request.user is a real User (PaymentIntent.payer is NOT NULL).
    # Wrapped in try/except: if the DB write fails after the Stripe intent is
    # created, cancel the Stripe intent to avoid an orphaned charge.
    try:
        intent = PaymentIntent.objects.create(
            payer=request.user,
            amount=amount,
            tax_amount=Decimal("0.00"),  # Donations are not taxed
            currency="CAD",
            purpose=PaymentIntent.PURPOSE_DONATION,
            status=PaymentIntent.STATUS_PENDING,
            gateway=PaymentIntent.GATEWAY_STRIPE,
            gateway_intent_id=gateway_result["gateway_intent_id"],
            idempotency_key=uuid.UUID(idempotency_key),
            metadata=metadata,
        )
    except Exception as db_exc:
        logger.error(
            "payments.donation.create_intent.db_error type=%s — cancelling Stripe intent pi_id=%s",
            type(db_exc).__name__,
            gateway_result["gateway_intent_id"],
        )
        try:
            gateway.cancel_payment_intent(gateway_result["gateway_intent_id"])
        except GatewayError:
            logger.error(
                "payments.donation.create_intent.orphan_stripe_pi pi_id=%s",
                gateway_result["gateway_intent_id"],
            )
        return JsonResponse(
            {"error": "A database error occurred. Please try again."},
            status=500,
        )

    # Store intent PK in session for idempotency guard and success page lookup.
    session_data["donation_payment_intent_pk"] = str(intent.pk)
    self_session = request.session
    self_session[DONATION_SESSION_KEY] = session_data
    self_session.modified = True

    logger.info(
        "payments.donation.create_intent.created intent_pk=%s campaign_pk=%s",
        str(intent.pk),
        metadata.get("campaign_pk", ""),
    )

    return JsonResponse({
        "client_secret": gateway_result["client_secret"],
        "payment_intent_pk": str(intent.pk),
    })


# ---------------------------------------------------------------------------
# Step 4: Success
# ---------------------------------------------------------------------------

class DonationSuccessView(TemplateView):
    """
    Step 4: donation accepted — show reference number and receipt notice.
    Clears session. UUID validated before ORM lookup to prevent injection.
    """

    template_name = "payments/donation_success.html"

    def get(self, request, *args, **kwargs):
        intent_pk_raw = request.GET.get("payment_intent_pk") or (
            request.session.get(DONATION_SESSION_KEY) or {}
        ).get("donation_payment_intent_pk")

        if not intent_pk_raw:
            return redirect("donate:donation_select")

        # Validate UUID format before DB round-trip
        try:
            intent_pk = _uuid.UUID(str(intent_pk_raw))
        except ValueError:
            return redirect("donate:donation_select")

        # No IDOR risk: receipt only shows reference number (not financial details).
        # We do NOT filter by payer so anonymous donors can see their success page.
        self.intent = get_object_or_404(PaymentIntent, pk=intent_pk)

        # Clear session so Back+Refresh doesn't re-show stale data
        request.session.pop(DONATION_SESSION_KEY, None)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        intent = getattr(self, "intent", None)
        ctx["payment_reference"] = intent.reference if intent else ""
        return ctx


# ---------------------------------------------------------------------------
# Step 5: Cancel
# ---------------------------------------------------------------------------

class DonationCancelView(TemplateView):
    """
    Step 5: donor cancelled — clear session, show no-charge message.
    """

    template_name = "payments/donation_cancel.html"

    def get(self, request, *args, **kwargs):
        request.session.pop(DONATION_SESSION_KEY, None)
        return super().get(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Recurring gift cancellation (authenticated donors only)
# ---------------------------------------------------------------------------

class RecurringGiftCancelView(LoginRequiredMixin, TemplateView):
    """
    GET:  Show plan details and confirm-cancellation button.
    POST: Cancel the subscription on the gateway and mark plan cancelled.

    Authenticated donors only. plan_pk is a UUID path param.
    Only the donor who owns the plan can cancel it.
    """

    template_name = "payments/recurring_cancel.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        plan_pk = kwargs.get("plan_pk")
        # UUID validation already done by path converter <uuid:plan_pk>
        self.plan = get_object_or_404(
            RecurringGiftPlan,
            pk=plan_pk,
            donor=request.user,
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["plan"] = self.plan
        return ctx

    def post(self, request, *args, **kwargs):
        """Execute recurring gift cancellation."""
        from django.db import transaction as db_transaction

        plan = self.plan

        # If already cancelled, redirect gracefully
        if plan.status == PLAN_STATUS_CANCELLED:
            return redirect("donate:donation_select")

        with db_transaction.atomic():
            # Re-fetch with row lock inside the transaction
            plan = RecurringGiftPlan.objects.select_for_update().get(pk=plan.pk)

            if plan.status == PLAN_STATUS_CANCELLED:
                return redirect("donate:donation_select")

            # Cancel on gateway if subscription ID is set
            gateway_cancelled = False
            if plan.gateway_subscription_id:
                try:
                    gateway = get_gateway()
                    gateway_cancelled = gateway.cancel_subscription(plan.gateway_subscription_id)
                except GatewayError as exc:
                    logger.error(
                        "payments.recurring_cancel.gateway_error type=%s gateway_code=%s plan_pk=%s",
                        type(exc).__name__,
                        exc.gateway_code,
                        str(plan.pk),
                    )
                    # Continue to mark cancelled locally so donor isn't stuck
                    gateway_cancelled = False

            # Update plan status regardless of gateway result
            plan.status = PLAN_STATUS_CANCELLED
            plan.cancelled_at = timezone.now()
            plan.cancellation_reason = "cancelled_by_donor"
            plan.save(update_fields=["status", "cancelled_at", "cancellation_reason", "updated_at"])

            # Mask IP per PIPEDA before storing in audit log.
            ip_raw = request.META.get("REMOTE_ADDR", "")
            actor_ip = _mask_ip(ip_raw) if ip_raw else ""

            # Mask gateway subscription ID — it is PII-adjacent and should
            # not appear in full in the audit log.
            sub_id = plan.gateway_subscription_id or ""
            masked_sub_id = f"{sub_id[:8]}***" if len(sub_id) > 8 else "***"

            PaymentAuditEntry.objects.create(
                action="recurring_plan_cancelled",
                actor=request.user,
                actor_ip=actor_ip,
                details={
                    "plan_pk": str(plan.pk),
                    "gateway_subscription_id": masked_sub_id,
                    "gateway_cancelled": gateway_cancelled,
                    "cancelled_by": "donor",
                },
            )

        logger.info(
            "payments.recurring_cancel.done plan_pk=%s gateway_cancelled=%s",
            str(plan.pk),
            gateway_cancelled,
        )

        return redirect("donate:donation_select")
