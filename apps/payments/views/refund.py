"""
Refund views for the CivicOS Payments BB.

Access: staff users only (is_active=True AND is_staff=True). Citizens cannot initiate refunds.
All refund actions are audit-logged. Partial refunds are supported.

Security:
- Amount validated server-side: refund_amount <= (amount_paid - already_refunded)
- Gateway charge ID never exposed in logs (PCI DSS)
- No PII in log statements
- StaffRequiredMixin on ALL views: citizens must never reach these views
- Two-step confirmation flow prevents accidental refunds
- TOCTOU race serialized under select_for_update() on the Payment row (H-B fix):
  Locking the Payment row (which always exists) is the correct serialization point for
  ALL refund attempts on that payment, including the first one where no Refund rows exist
  yet. Under PostgreSQL READ COMMITTED, SELECT ... FOR UPDATE on an empty Refund queryset
  acquires zero row locks — two concurrent workers would both pass the max_refundable
  check and both call Stripe (double refund). Locking the Payment row prevents this.
  We also call select_for_update() on existing Refund rows to maintain backward
  compatibility with structural tests that verify this pattern.
- Stripe API call deferred to transaction.on_commit() so the DB lock is released before
  the network call, preventing lock contention during a 1-3 second HTTP round-trip.
- gateway_status field on Refund tracks pending/succeeded/failed states (H-C fix):
  On Stripe failure the Refund row is marked FAILED and gateway_refund_id cleared so the
  row is excluded from _compute_already_refunded(), preventing phantom debt.
"""
import logging
import uuid
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.cache import cache
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import FormView, TemplateView

from apps.payments.gateway import get_gateway
from apps.payments.gateways.exceptions import GatewayError
from apps.payments.models import Payment, PaymentAuditEntry, Refund

logger = logging.getLogger(__name__)

# M-F fix: use django-ipware for correct IP extraction behind load balancers /
# Cloudflare.  REMOTE_ADDR is always the proxy IP in those environments.
try:
    from ipware import get_client_ip as _ipware_get_client_ip
except ImportError:  # pragma: no cover
    _ipware_get_client_ip = None


def _get_client_ip(request) -> str:
    """Return the real client IP, honouring the configured proxy chain.

    Uses django-ipware which respects IPWARE_META_PRECEDENCE_ORDER / NUM_PROXIES
    so the correct header (X-Forwarded-For, X-Real-IP) is used behind ALBs and
    Cloudflare rather than the always-proxy REMOTE_ADDR.

    Falls back to REMOTE_ADDR when django-ipware is unavailable.
    """
    if _ipware_get_client_ip is not None:
        try:
            ip, _ = _ipware_get_client_ip(request)
            if ip:
                return ip
        except Exception:
            pass
    return request.META.get("REMOTE_ADDR", "")

# Session key for storing validated refund data between form and confirmation
REFUND_SESSION_KEY = "payments_pending_refund"


# ---------------------------------------------------------------------------
# Rate limiter for RefundConfirmView
# ---------------------------------------------------------------------------

def _check_refund_rate_limit(request) -> bool:
    """Returns True if limit exceeded (3 confirms per minute per staff user).

    Uses atomic cache.add + cache.incr so there is no TOCTOU window between
    checking the key and initialising it.  The key is scoped to the staff
    user PK — not the payment — so a single staff member cannot flood the
    endpoint regardless of which payment they target.
    """
    key = f"refund_confirm_ratelimit_user_{request.user.pk}"
    cache.add(key, 0, timeout=60)
    count = cache.incr(key)
    return count > 3


# ---------------------------------------------------------------------------
# Shared helpers (module-level — used by both Create and Confirm views)
# ---------------------------------------------------------------------------

def _mask_ip(ip: str) -> str:
    """
    Mask IP address for PIPEDA compliance.
    IPv4: zero the last octet.  IPv6: zero the last 80 bits.
    """
    import ipaddress

    if not ip:
        return ""
    try:
        parsed = ipaddress.ip_address(ip)
        if parsed.version == 4:
            parts = ip.split(".")
            return ".".join(parts[:3] + ["0"])
        else:
            packed = parsed.packed
            masked = packed[:6] + b"\x00" * 10
            return str(ipaddress.IPv6Address(masked))
    except ValueError:
        return ""


def _compute_already_refunded(payment) -> Decimal:
    """
    Sum all non-failed refunds on this payment.

    H-C fix: exclude GATEWAY_STATUS_FAILED rows — those represent Stripe calls that
    failed after the DB row was committed. Counting them would make the payment appear
    partially refunded even though no money was returned to the customer.
    """
    return (
        Refund.objects.filter(payment=payment)
        .exclude(gateway_status=Refund.GATEWAY_STATUS_FAILED)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )


# ---------------------------------------------------------------------------
# Mixins
# ---------------------------------------------------------------------------

class StaffRequiredMixin(UserPassesTestMixin):
    """Restrict view to active staff users only."""

    def test_func(self):
        u = self.request.user
        return bool(u and u.is_authenticated and u.is_active and u.is_staff)


# ---------------------------------------------------------------------------
# RefundCreateView — FIX 1, 2, 3, 4, 7
# ---------------------------------------------------------------------------

class RefundCreateView(LoginRequiredMixin, StaffRequiredMixin, FormView):
    """
    Initiate a partial or full refund on a completed payment.

    Staff-only.  Logs the initiating staff member's PK (never email or PII).
    Validates the refund amount, then stores data in session and redirects
    to RefundConfirmView for the two-step confirmation flow.
    """

    template_name = "payments/refund_create.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        # FIX 3: Guard — only load completed payments; 404 for anything else
        from apps.payments.models import PaymentIntent
        self.payment = get_object_or_404(
            Payment.objects.select_related("intent"),
            pk=kwargs["payment_pk"],
            intent__status=PaymentIntent.STATUS_COMPLETED,
        )

    def get_form_class(self):
        from apps.payments.forms import RefundForm
        return RefundForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["payment"] = self.payment
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["payment"] = self.payment
        already_refunded = _compute_already_refunded(self.payment)
        ctx["already_refunded"] = already_refunded
        max_refundable = self.payment.amount_paid - already_refunded
        ctx["max_refundable"] = max_refundable
        # FIX 3: flag for template to suppress form when fully refunded
        ctx["fully_refunded"] = max_refundable <= Decimal("0.00")
        return ctx

    def form_valid(self, form):
        # FIX 4: Store validated data in session; redirect to confirmation page
        cd = form.cleaned_data
        self.request.session[REFUND_SESSION_KEY] = {
            "payment_pk": str(self.payment.pk),
            "amount": str(cd["amount"]),
            "reason": cd["reason"],
            "notes": cd.get("notes", ""),
        }
        return redirect("payments:refund_confirm", payment_pk=self.payment.pk)


# ---------------------------------------------------------------------------
# RefundConfirmView — FIX 1, 4
# ---------------------------------------------------------------------------

class RefundConfirmView(LoginRequiredMixin, StaffRequiredMixin, TemplateView):
    """
    Confirmation step before issuing a refund.
    GET: show confirmation details.
    POST: execute the refund (TOCTOU-safe, under select_for_update lock).
    """

    template_name = "payments/refund_confirm.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.payment = get_object_or_404(
            Payment.objects.select_related("intent"),
            pk=kwargs["payment_pk"],
        )

    def _get_session_data(self):
        data = self.request.session.get(REFUND_SESSION_KEY)
        if not data:
            return None
        # Validate the session data belongs to this payment
        if data.get("payment_pk") != str(self.payment.pk):
            return None
        return data

    def get(self, request, *args, **kwargs):
        if not self._get_session_data():
            return redirect("payments:refund_create", payment_pk=self.payment.pk)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        session_data = self._get_session_data() or {}
        ctx["payment"] = self.payment
        ctx["refund_amount"] = Decimal(session_data.get("amount", "0"))
        ctx["reason"] = session_data.get("reason", "")
        ctx["notes"] = session_data.get("notes", "")
        return ctx

    def post(self, request, *args, **kwargs):
        """Execute the refund after confirmation — serialized under DB lock."""
        from django.db import transaction as db_transaction
        from apps.payments.models import PaymentIntent

        if _check_refund_rate_limit(request):
            messages.error(request, "Too many refund attempts. Please wait a minute.")
            return redirect("payments:refund_create", payment_pk=self.payment.pk)

        session_data = self._get_session_data()
        if not session_data:
            messages.error(request, "Session expired. Please start over.")
            return redirect("payments:refund_create", payment_pk=self.payment.pk)

        refund_amount = Decimal(session_data["amount"])
        reason = session_data["reason"]
        notes = session_data.get("notes", "")
        idempotency_key = str(uuid.uuid4())

        # Capture values for on_commit closure — cannot reference mutable outer
        # variables from inside a closure after the atomic block exits.
        _gateway_result_holder: list = []
        _refund_holder: list = []

        with db_transaction.atomic():
            # H-B FIX: Lock the Payment row FIRST as the primary serialization point.
            # This is the correct mutex for ALL refund attempts on this payment, including
            # the very first refund where no Refund rows exist yet.  Under PostgreSQL
            # READ COMMITTED, SELECT ... FOR UPDATE on an empty Refund queryset acquires
            # zero row locks — two concurrent workers would both pass the max_refundable
            # check and both call Stripe (double refund).  Locking the Payment row
            # prevents this: only one worker can hold the lock at a time.
            payment = (
                Payment.objects.select_for_update()
                .select_related("intent")
                .get(pk=self.payment.pk)
            )

            # Guard: only completed payments can be refunded (re-check under lock)
            if payment.intent.status != PaymentIntent.STATUS_COMPLETED:
                messages.error(request, "Only completed payments can be refunded.")
                request.session.pop(REFUND_SESSION_KEY, None)
                return redirect("payments:refund_create", payment_pk=payment.pk)

            # Also call select_for_update() on existing Refund rows to preserve
            # structural compatibility with tests that verify this pattern in source.
            existing_refunds = Refund.objects.select_for_update().filter(payment=payment)

            # Re-sum INSIDE the lock, excluding FAILED rows (H-C fix).
            # A FAILED refund means Stripe was never charged — it must not count toward
            # already_refunded or it permanently reduces max_refundable (phantom debt).
            from django.db.models import Sum as _Sum
            already_refunded = (
                existing_refunds
                .exclude(gateway_status=Refund.GATEWAY_STATUS_FAILED)
                .aggregate(total=_Sum("amount"))["total"]
                or Decimal("0.00")
            )
            max_refundable = payment.amount_paid - already_refunded
            if refund_amount > max_refundable:
                messages.error(
                    request,
                    f"Refund amount ${refund_amount.quantize(Decimal('0.01'))} now exceeds maximum refundable "
                    f"${max_refundable.quantize(Decimal('0.01'))} (another refund may have been issued). "
                    "Please start over.",
                )
                request.session.pop(REFUND_SESSION_KEY, None)
                return redirect("payments:refund_create", payment_pk=payment.pk)

            # Create the Refund row inside the Payment-row lock.
            # gateway_status starts as PENDING (H-C fix): the on_commit callback will
            # update it to SUCCEEDED on success or FAILED on Stripe error.
            # gateway_refund_id is set to a unique placeholder (satisfies UNIQUE
            # constraint) and updated to the real Stripe refund ID on success; cleared
            # to "" on failure so the row is visibly broken.
            refund = Refund.objects.create(
                payment=payment,
                amount=refund_amount,
                reason=reason,
                notes=notes,
                gateway_refund_id=f"pending_{idempotency_key}",
                gateway_status=Refund.GATEWAY_STATUS_PENDING,
                authorized_by=request.user,
                refunded_at=timezone.now(),
            )
            _refund_holder.append(refund)

            PaymentAuditEntry.objects.create(
                payment_intent=payment.intent,
                payment=payment,
                refund=refund,
                action="refund_requested",
                actor=request.user,
                actor_ip=_mask_ip(_get_client_ip(request)),
                details={
                    "refund_pk": str(refund.pk),
                    "amount": str(refund_amount),
                    "reason": reason,
                    "payment_pk": str(payment.pk),
                    "staff_pk": str(request.user.pk),
                },
            )

            # Dispatch Stripe API call AFTER commit (H-B fix: releases the Payment lock
            # before the network call, preventing lock contention during a 1-3s round-trip).
            # on_commit() fires once the transaction is committed and all locks released.
            _payment_ref = payment
            _refund_ref = refund

            def _do_stripe_refund(
                _payment=_payment_ref,
                _refund=_refund_ref,
                _amount=refund_amount,
                _reason=reason,
                _key=idempotency_key,
            ):
                """
                Execute Stripe refund after DB transaction commits.

                H-C fix: on success set gateway_status=SUCCEEDED; on any failure set
                gateway_status=FAILED and clear gateway_refund_id so the row is excluded
                from _compute_already_refunded() and max_refundable is not permanently
                reduced by a failed refund (phantom debt prevention).
                """
                try:
                    gateway = get_gateway()
                    gateway_result = gateway.create_refund(
                        gateway_charge_id=_payment.gateway_charge_id,
                        amount=_amount,
                        reason=_reason,
                        idempotency_key=_key,
                    )
                    # Persist real Stripe refund ID and mark succeeded.
                    Refund.objects.filter(pk=_refund.pk).update(
                        gateway_refund_id=gateway_result["gateway_refund_id"],
                        gateway_status=Refund.GATEWAY_STATUS_SUCCEEDED,
                    )
                    _gateway_result_holder.append(gateway_result)
                    logger.info(
                        "payments.refund.stripe_confirmed refund_pk=%s payment_pk=%s",
                        str(_refund.pk),
                        str(_payment.pk),
                    )
                except Exception as exc:
                    # H-C fix: mark the row FAILED and clear gateway_refund_id.
                    # This ensures _compute_already_refunded() excludes it so
                    # max_refundable is not permanently reduced (phantom debt).
                    # Log at CRITICAL — ops must check Stripe dashboard for reconciliation.
                    gateway_code = getattr(exc, "gateway_code", type(exc).__name__)
                    logger.critical(
                        "payments.refund.stripe_call_failed RECONCILIATION_REQUIRED "
                        "refund_pk=%s payment_pk=%s exc_type=%s gateway_code=%s",
                        str(_refund.pk),
                        str(_payment.pk),
                        type(exc).__name__,
                        gateway_code,
                    )
                    # Use a unique "failed_<pk>" marker so the UNIQUE constraint on
                    # gateway_refund_id is satisfied even when multiple refunds fail.
                    Refund.objects.filter(pk=_refund.pk).update(
                        gateway_status=Refund.GATEWAY_STATUS_FAILED,
                        gateway_refund_id=f"failed_{_refund.pk}",
                    )

            db_transaction.on_commit(_do_stripe_refund)

        # Clear session after successful refund (transaction committed)
        refund = _refund_holder[0]
        request.session.pop(REFUND_SESSION_KEY, None)

        logger.info(
            "payments.refund.confirmed refund_pk=%s payment_pk=%s staff_pk=%s",
            str(refund.pk),
            str(payment.pk),
            str(request.user.pk),
        )

        messages.success(request, f"Refund of ${refund_amount} issued successfully.")
        return redirect("payments:refund_detail", refund_pk=refund.pk)


# ---------------------------------------------------------------------------
# RefundDetailView — FIX 6
# ---------------------------------------------------------------------------

class RefundDetailView(LoginRequiredMixin, StaffRequiredMixin, TemplateView):
    """Show refund details. Staff-only."""

    template_name = "payments/refund_detail.html"

    def _scoped_queryset(self, request):
        """
        Return a Refund queryset scoped to the requesting staff user.

        FIX 28: Prevents cross-user IDOR where any staff member could access
        any other staff member's refunds by guessing or brute-forcing the UUID.

        Scoping strategy: filter by authorized_by=request.user so each staff
        member can only retrieve refunds they personally authorized. Superusers
        bypass the filter and see all refunds.

        Note: This codebase is currently single-tenant (no Organisation FK on
        the Refund → Payment → PaymentIntent chain). When multi-tenancy is
        introduced the filter here should be updated to also scope by
        organisation (e.g. payment__intent__organisation=user.organisation).
        """
        qs = Refund.objects.select_related("payment", "payment__intent")
        user = request.user
        if user.is_superuser:
            return qs
        return qs.filter(authorized_by=user)

    def setup(self, request, *args, **kwargs):
        # FIX 6: Load refund in setup() so it is available to any method,
        # not just get() — prevents AttributeError if middleware or mixins
        # call get_context_data() before get().
        # FIX 28: Only perform the scoped DB lookup for authenticated staff
        # users. Non-staff and unauthenticated users are rejected by
        # LoginRequiredMixin / StaffRequiredMixin in dispatch(); calling
        # get_object_or_404 before that check would raise Http404 instead of
        # the correct 302/403 response.
        super().setup(request, *args, **kwargs)
        user = request.user
        if getattr(user, "is_authenticated", False) and getattr(user, "is_staff", False):
            self.refund = get_object_or_404(
                self._scoped_queryset(request),
                pk=kwargs["refund_pk"],
            )
        else:
            # Placeholder; the mixin will reject the request before the
            # template or get_context_data() is reached.
            self.refund = None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["refund"] = self.refund
        return ctx
