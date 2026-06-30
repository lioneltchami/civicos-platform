"""
Fee payment views — Wave 3, Round 3a.

Flow:
  1. FeePaymentSelectView   GET/POST  /payments/fees/
     Citizen picks fee_code + province + quantity.
     Amount is derived server-side in the form — never from client input.

  2. FeePaymentConfirmView  GET       /payments/fees/confirm/
     Shows order summary + Stripe Elements card input.
     JS calls create_payment_intent_api, then stripe.confirmCardPayment().

  3. create_payment_intent_api  POST  /payments/fees/api/create-intent/
     CSRF-protected JSON endpoint.  Amount is authoritative from session.
     Creates PaymentIntent model row + Stripe PaymentIntent.
     Returns {client_secret, payment_intent_pk}.

  4. FeePaymentSuccessView  GET       /payments/fees/success/
     Shown after JS redirect on Stripe success.

  5. FeePaymentCancelView   GET       /payments/fees/cancel/
     Clears session, shown when citizen backs out.

Security invariants:
- LoginRequiredMixin on all views: PaymentIntent.payer (FK) requires an
  authenticated user.  Anonymous fee payment is not supported.
- Amount ALWAYS from session in create_payment_intent_api, NEVER from
  request body.
- CSRF protection on all views including the JSON API (NOT @csrf_exempt).
- No PII in logs: log fee_code, province, intent_pk — never payer_email.
- stripe_publishable_key is rendered in template; stripe_secret_key and
  webhook_secret are NEVER passed to template context.

CSP note: Stripe.js is loaded from https://js.stripe.com. The deployer must
add "https://js.stripe.com" to CSP_SCRIPT_SRC and "https://api.stripe.com"
to CSP_CONNECT_SRC in config/settings/base.py (or production.py).
"""
import hashlib
import logging
import uuid as _uuid
import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView

from apps.payments.forms import FeePaymentForm
from apps.payments.gateway import get_gateway
from apps.payments.gateways.exceptions import GatewayError
from apps.payments.models import PaymentIntent, TenantPaymentConfig
from apps.payments.views.refund import _mask_ip

TWO_PLACES = Decimal("0.01")


def _check_rate_limit(user_pk: str) -> bool:
    """Returns True if rate limit is exceeded."""
    key = f"payments:create_intent:rl:{user_pk}"
    cache.add(key, 0, timeout=60)   # initialises to 0 only if key absent (atomic)
    count = cache.incr(key)          # atomically increment and return new value
    return count > 5

logger = logging.getLogger(__name__)

# Session key — namespaced to avoid collisions with other apps
SESSION_KEY = "payments_fee_intent"


class FeePaymentSelectView(LoginRequiredMixin, FormView):
    """Step 1: select fee code, province, quantity."""

    template_name = "payments/fee_payment_select.html"
    form_class = FeePaymentForm

    def form_valid(self, form):
        cd = form.cleaned_data
        fee = cd["fee"]
        # Store everything the confirm step needs.  No PII beyond payer_reference.
        # Quantize monetary values to exactly 2 decimal places to avoid
        # floating-point drift and session serialisation surprises.
        self.request.session[SESSION_KEY] = {
            "fee_pk": str(fee.pk),
            "fee_code": cd["fee_code"],
            "fee_description": fee.get_description(),
            "province": cd["province"],
            "quantity": cd["quantity"],
            "payer_reference": cd.get("payer_reference", ""),
            "subtotal": str(cd["subtotal"].quantize(TWO_PLACES, rounding=ROUND_HALF_UP)),
            "tax_amount": str(cd["tax_amount"].quantize(TWO_PLACES, rounding=ROUND_HALF_UP)),
            "total": str(cd["total"].quantize(TWO_PLACES, rounding=ROUND_HALF_UP)),
            "is_taxable": fee.is_taxable,
        }
        return redirect("payments:fee_payment_confirm")

    def get_success_url(self):
        # form_valid() handles the redirect directly.
        # This fallback is required by FormView's contract.
        from django.urls import reverse
        return reverse("payments:fee_payment_confirm")


class FeePaymentConfirmView(LoginRequiredMixin, TemplateView):
    """Step 2: show order summary + Stripe Elements card form."""

    template_name = "payments/fee_payment_confirm.html"

    def _get_session_data(self):
        return self.request.session.get(SESSION_KEY)

    def get(self, request, *args, **kwargs):
        if not self._get_session_data():
            return redirect("payments:fee_payment_select")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sd = self._get_session_data() or {}
        config = TenantPaymentConfig.get_solo()
        ctx.update({
            "fee_code": sd.get("fee_code", ""),
            "fee_description": sd.get("fee_description", ""),
            "province": sd.get("province", ""),
            "quantity": sd.get("quantity", 1),
            "subtotal": Decimal(sd.get("subtotal", "0.00")),
            "tax_amount": Decimal(sd.get("tax_amount", "0.00")),
            "total": Decimal(sd.get("total", "0.00")),
            # Publishable key is safe for front-end.
            # stripe_secret_key and webhook_secret are NEVER passed here.
            "stripe_publishable_key": config.stripe_publishable_key or "",
        })
        return ctx


def create_payment_intent_api(request):
    """
    POST /payments/fees/api/create-intent/

    CSRF-protected JSON endpoint called by Stripe.js after card entry.
    Amount is authoritative from session — the request body is ignored.

    Returns: {"client_secret": "...", "payment_intent_pk": "..."}
    Errors:  {"error": "<message>"} with appropriate HTTP status.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)

    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)

    # Rate-limit: 5 POST per minute per user to prevent double-click and abuse.
    if _check_rate_limit(str(request.user.pk)):
        return JsonResponse(
            {"error": "Too many requests. Please wait a moment."},
            status=429,
        )

    session_data = request.session.get(SESSION_KEY)
    if not session_data:
        logger.warning(
            "payments.create_intent.session_expired remote_addr=%s",
            _mask_ip(request.META.get("REMOTE_ADDR", "")),
        )
        return JsonResponse(
            {"error": "Session expired. Please start over."},
            status=403,
        )

    try:
        total = Decimal(session_data["total"])
    except Exception:
        return JsonResponse({"error": "Invalid session data."}, status=400)

    if total <= Decimal("0.00"):
        return JsonResponse({"error": "Amount must be greater than zero."}, status=400)

    # Idempotency guard — prevents double-click creating two Stripe intents.
    existing_pk = session_data.get("payment_intent_pk")
    if existing_pk:
        try:
            existing_intent = PaymentIntent.objects.get(
                pk=existing_pk,
                payer=request.user,
                status=PaymentIntent.STATUS_PENDING,
            )
            # Re-use the existing pending intent — do not create another.
            logger.info(
                "payments.create_intent.reusing_existing intent_pk=%s",
                str(existing_intent.pk),
            )
            # Signal the client to wait and retry.
            return JsonResponse(
                {"error": "A payment is already in progress. Please wait a moment and try again."},
                status=409,
            )
        except PaymentIntent.DoesNotExist:
            pass  # Stale or completed — proceed to create a fresh one

    idempotency_key = str(uuid.uuid4())

    # Metadata: no PII — reference and fee_code only.
    metadata = {
        "fee_code": session_data.get("fee_code", ""),
        "province": session_data.get("province", ""),
        "payer_reference": session_data.get("payer_reference", ""),
        "source": "fee_payment",
    }

    try:
        gateway = get_gateway()
        config = TenantPaymentConfig.get_solo()
        connect_account_id = (
            config.stripe_connect_account_id if config.use_connect else None
        )
        gateway_result = gateway.create_payment_intent(
            amount=total,
            currency="cad",
            idempotency_key=idempotency_key,
            metadata=metadata,
            description=(
                f"Government fee: {session_data.get('fee_code', '')} "
                f"— {session_data.get('province', '')}"
            ),
            connect_account_id=connect_account_id,
        )
    except GatewayError as exc:
        logger.error(
            "payments.create_intent.gateway_error type=%s gateway_code=%s",
            type(exc).__name__,
            exc.gateway_code,
        )
        return JsonResponse(
            {"error": str(_("Payment processing is temporarily unavailable. Please try again."))},
            status=502,
        )

    # Create the PaymentIntent model row.
    # payer is required — the login check above guarantees request.user is set.
    # Wrapped in try/except: if the DB write fails after the Stripe intent is
    # created, we cancel the Stripe intent to avoid an orphaned charge.
    try:
        intent = PaymentIntent.objects.create(
            payer=request.user,
            amount=total,
            tax_amount=Decimal(session_data.get("tax_amount", "0.00")),
            currency="CAD",
            purpose=PaymentIntent.PURPOSE_SERVICE_FEE,
            status=PaymentIntent.STATUS_PENDING,
            gateway=PaymentIntent.GATEWAY_STRIPE,
            gateway_intent_id=gateway_result["gateway_intent_id"],
            idempotency_key=uuid.UUID(idempotency_key),
            metadata=metadata,
        )
    except Exception as db_exc:
        logger.error(
            "payments.create_intent.db_error type=%s — cancelling Stripe intent pi_id=%s",
            type(db_exc).__name__,
            gateway_result["gateway_intent_id"],
        )
        try:
            gateway.cancel_payment_intent(gateway_result["gateway_intent_id"])
        except GatewayError:
            logger.error(
                "payments.create_intent.orphan_stripe_pi pi_id=%s",
                gateway_result["gateway_intent_id"],
            )
        return JsonResponse(
            {"error": "A database error occurred. Please try again."},
            status=500,
        )

    # Store intent PK in session for success page lookup.
    session_data["payment_intent_pk"] = str(intent.pk)
    request.session[SESSION_KEY] = session_data
    request.session.modified = True

    logger.info(
        "payments.create_intent.created intent_pk=%s fee_code=%s province=%s",
        str(intent.pk),
        session_data.get("fee_code", ""),
        session_data.get("province", ""),
    )

    return JsonResponse({
        "client_secret": gateway_result["client_secret"],
        "payment_intent_pk": str(intent.pk),
    })


class FeePaymentSuccessView(LoginRequiredMixin, TemplateView):
    """Step 4: payment accepted — show reference number."""

    template_name = "payments/fee_payment_success.html"

    def get(self, request, *args, **kwargs):
        intent_pk_raw = request.GET.get("payment_intent_pk") or (
            request.session.get(SESSION_KEY) or {}
        ).get("payment_intent_pk")
        if not intent_pk_raw:
            return redirect("payments:fee_payment_select")
        # Validate UUID format before DB round-trip to prevent injection via
        # crafted query strings.
        try:
            intent_pk = _uuid.UUID(str(intent_pk_raw))
        except ValueError:
            return redirect("payments:fee_payment_select")
        # Verify the intent belongs to the current user (IDOR protection).
        self.intent = get_object_or_404(
            PaymentIntent,
            pk=intent_pk,
            payer=request.user,
        )
        # Clear session so Back+Refresh doesn't re-show stale data.
        request.session.pop(SESSION_KEY, None)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        intent = getattr(self, "intent", None)
        ctx["payment_intent"] = intent
        ctx["reference"] = intent.reference if intent else ""
        return ctx


class FeePaymentCancelView(LoginRequiredMixin, TemplateView):
    """Step 5: citizen cancelled — clear session, show confirmation."""

    template_name = "payments/fee_payment_cancel.html"

    def get(self, request, *args, **kwargs):
        request.session.pop(SESSION_KEY, None)
        return super().get(request, *args, **kwargs)
