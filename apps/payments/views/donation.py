"""
Donation views — Wave 4, Track A.
CivicOS Payments Building Block — Canadian charitable donation flow.

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
from django.db import transaction
from django.http import Http404, JsonResponse
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

try:
    from ipware import get_client_ip as _ipware_get_client_ip
except ImportError:  # pragma: no cover
    _ipware_get_client_ip = None

logger = logging.getLogger(__name__)

TWO_PLACES = Decimal("0.01")

# Session key — namespaced to avoid collisions with fee payment and other apps
DONATION_SESSION_KEY = "payments_donation_intent"


# ---------------------------------------------------------------------------
# IP extraction helper
# ---------------------------------------------------------------------------

def _get_client_ip(request) -> str:
    """Return the real client IP, trusting the configured proxy chain.

    Uses django-ipware which honours IPWARE_META_PRECEDENCE_ORDER and
    NUM_PROXIES so that the correct header (X-Forwarded-For, X-Real-IP)
    is used behind load-balancers, Cloudflare, or AWS ALB rather than
    the always-proxy REMOTE_ADDR.

    Falls back to REMOTE_ADDR when django-ipware is unavailable.

    See config/settings/base.py for IPWARE_META_PRECEDENCE_ORDER /
    NUM_PROXIES tuning required for the production proxy chain.
    """
    if _ipware_get_client_ip is not None:
        try:
            ip, _is_routable = _ipware_get_client_ip(request)
            if ip:
                return ip
        except Exception:
            pass
    return request.META.get("REMOTE_ADDR", "")


# ---------------------------------------------------------------------------
# Rate limiting helper
# ---------------------------------------------------------------------------

def _check_donation_rate_limit(request) -> bool:
    """Returns True if rate limit exceeded (5 POST/min). Thread-safe via atomic cache.incr()."""
    if request.user.is_authenticated:
        key = f"donation_ratelimit_user_{request.user.pk}"
    else:
        ip = _get_client_ip(request)
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

    # Auth check BEFORE rate limit — unauthenticated requests must not consume
    # the rate-limit quota.  An IP-based bot could otherwise exhaust the bucket
    # for all users behind the same proxy before any real (authenticated) donor
    # has a chance to use it.  (M-D fix)
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=401)

    # Rate limit — 5 per minute per IP (anonymous) or user pk (authenticated).
    # Now placed after the auth check so authenticated users always use the
    # user-keyed bucket, not the IP-keyed bucket.  (M-D fix)
    if _check_donation_rate_limit(request):
        return JsonResponse(
            {"error": "Too many requests. Please wait a moment."},
            status=429,
        )

    session_data = request.session.get(DONATION_SESSION_KEY)
    if not session_data:
        logger.warning(
            "payments.donation.create_intent.session_expired remote_addr=%s",
            _mask_ip(_get_client_ip(request)),
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
    request.session[DONATION_SESSION_KEY] = session_data
    request.session.modified = True

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

        # Layer 1 (primary IDOR defence): if the user is authenticated, restrict the
        # lookup to their own PaymentIntents. This prevents any authenticated user from
        # viewing another donor's receipt by guessing or obtaining a UUID.
        if request.user.is_authenticated:
            self.intent = get_object_or_404(PaymentIntent, pk=intent_pk, payer=request.user)
        else:
            # Layer 2 (belt-and-suspenders for anonymous/guest donors): verify the intent
            # PK matches what was stored in this session by create_donation_intent_api.
            # A probe or replay from a different browser/session cannot pass this check
            # even if the UUID is known, because the session key won't be present.
            session_data = request.session.get(DONATION_SESSION_KEY, {})
            session_intent_pk = session_data.get("donation_payment_intent_pk")
            if not session_intent_pk or str(intent_pk) != str(session_intent_pk):
                raise Http404  # Not your session → not your receipt
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

def _cancel_stripe_pi_safe(gateway, pi_id: str) -> None:
    """Cancel a Stripe PaymentIntent — fire-and-forget.

    Called via transaction.on_commit() from DonationCancelView and
    FeePaymentCancelView.  A failed cancel is NOT fatal — we log and move on.
    The PI is left in 'requires_payment_method' state on Stripe but the session
    has already been cleared, so the donor cannot be charged.
    """
    try:
        gateway.cancel_payment_intent(pi_id)
    except Exception as exc:
        logger.warning(
            "payments.cancel.stripe_pi_cancel_failed pi_id=%s exc_type=%s",
            pi_id,
            type(exc).__name__,
        )


class DonationCancelView(LoginRequiredMixin, TemplateView):
    """
    Step 5: donor cancelled — clear session, show no-charge message.

    M-M fix: if a live Stripe PaymentIntent was created for this session, cancel
    it via on_commit() (fire-and-forget) to avoid accumulating stale PIs on Stripe.

    M-C fix: requires login (LoginRequiredMixin) and filters the DB lookup by
    payer=request.user so an attacker who obtains another donor's session-stored
    intent_pk cannot trigger a Stripe cancel on a PI they do not own (IDOR defence).
    """

    template_name = "payments/donation_cancel.html"

    def get(self, request, *args, **kwargs):
        session_data = request.session.pop(DONATION_SESSION_KEY, {})

        # M-M fix: cancel the live Stripe PI if one was created for this session.
        # M-C fix: payer=request.user ensures only the owner's PI is cancelled.
        intent_pk = session_data.get("donation_payment_intent_pk")
        if intent_pk:
            try:
                import uuid as _uuid_mod
                _uuid_mod.UUID(str(intent_pk))  # validate before DB lookup
                intent = PaymentIntent.objects.get(
                    pk=intent_pk,
                    payer=request.user,  # M-C: IDOR defence — can only cancel own PI
                    status=PaymentIntent.STATUS_PENDING,
                )
                if intent.gateway_intent_id:
                    _gw = get_gateway()
                    _pi_id = intent.gateway_intent_id
                    # Use on_commit if inside a transaction, otherwise call directly.
                    # DonationCancelView makes no DB writes so there may be no active
                    # transaction — wrap in atomic() so on_commit fires reliably.
                    with transaction.atomic():
                        transaction.on_commit(
                            lambda gw=_gw, pi_id=_pi_id:
                                _cancel_stripe_pi_safe(gw, pi_id)
                        )
            except (PaymentIntent.DoesNotExist, ValueError):
                pass

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
        # Guard: only perform the scoped DB lookup for authenticated users.
        # setup() runs before LoginRequiredMixin.dispatch() checks authentication,
        # so passing AnonymousUser (SimpleLazyObject) as an FK filter value raises
        # TypeError before the login redirect can fire. Deferring the lookup until
        # we know the user is authenticated preserves the correct 302 behaviour.
        plan_pk = kwargs.get("plan_pk")
        # UUID validation already done by path converter <uuid:plan_pk>
        if getattr(request.user, "is_authenticated", False):
            self.plan = get_object_or_404(
                RecurringGiftPlan,
                pk=plan_pk,
                donor=request.user,
            )
        else:
            self.plan = None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["plan"] = self.plan
        return ctx

    def post(self, request, *args, **kwargs):
        """Execute recurring gift cancellation.

        M-E fix: gateway.cancel_subscription() is deferred to transaction.on_commit()
        so the DB lock is released before the Stripe HTTP call.  Holding the lock
        during a 1-3 second network call causes connection pool exhaustion under load
        and leaves the DB record in a half-cancelled state if Stripe times out.
        """
        from django.db import transaction as db_transaction

        plan = self.plan

        # If already cancelled, redirect gracefully
        if plan.status == PLAN_STATUS_CANCELLED:
            return redirect("donate:donation_select")

        _gateway_sub_id_holder: list = []

        with db_transaction.atomic():
            # Re-fetch with row lock inside the transaction
            plan = RecurringGiftPlan.objects.select_for_update().get(pk=plan.pk)

            if plan.status == PLAN_STATUS_CANCELLED:
                return redirect("donate:donation_select")

            # Update plan status BEFORE the gateway call — the on_commit callback
            # will attempt the Stripe cancel after the lock is released.
            plan.status = PLAN_STATUS_CANCELLED
            plan.cancelled_at = timezone.now()
            plan.cancellation_reason = "cancelled_by_donor"
            plan.save(update_fields=["status", "cancelled_at", "cancellation_reason", "updated_at"])

            # Mask IP per PIPEDA before storing in audit log.
            ip_raw = _get_client_ip(request)
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
                    "gateway_cancelled": "deferred",
                    "cancelled_by": "donor",
                },
            )

            # Defer Stripe cancel to after commit — releases DB lock first.
            # (M-E fix: same pattern as RefundConfirmView H8 fix)
            if plan.gateway_subscription_id:
                _sub_id = plan.gateway_subscription_id
                _plan_pk_str = str(plan.pk)

                def _do_stripe_cancel(
                    gateway_sub_id=_sub_id,
                    plan_pk_str=_plan_pk_str,
                ):
                    try:
                        gateway = get_gateway()
                        gateway.cancel_subscription(gateway_sub_id)
                    except Exception as exc:
                        logger.critical(
                            "payments.recurring_cancel.stripe_cancel_failed "
                            "plan_pk=%s sub_id_prefix=%s exc_type=%s RECONCILIATION_REQUIRED",
                            plan_pk_str,
                            gateway_sub_id[:8] if len(gateway_sub_id) > 8 else "***",
                            type(exc).__name__,
                        )

                db_transaction.on_commit(_do_stripe_cancel)

        logger.info(
            "payments.recurring_cancel.done plan_pk=%s",
            str(plan.pk),
        )

        return redirect("donate:donation_select")
