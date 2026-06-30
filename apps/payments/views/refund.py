"""
Refund views for the GovStack Payments BB.

Access: staff users only (is_active=True AND is_staff=True). Citizens cannot initiate refunds.
All refund actions are audit-logged. Partial refunds are supported.

Security:
- Amount validated server-side: refund_amount <= (amount_paid - already_refunded)
- Gateway charge ID never exposed in logs (PCI DSS)
- No PII in log statements
- StaffRequiredMixin on ALL views: citizens must never reach these views
- Two-step confirmation flow prevents accidental refunds
- TOCTOU race serialized under select_for_update() lock on Payment row
"""
import logging
import uuid
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import FormView, TemplateView

from apps.payments.gateway import get_gateway
from apps.payments.gateways.exceptions import GatewayError
from apps.payments.models import Payment, PaymentAuditEntry, Refund

logger = logging.getLogger(__name__)

# Session key for storing validated refund data between form and confirmation
REFUND_SESSION_KEY = "payments_pending_refund"


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
    """Sum all refunds on this payment."""
    return (
        Refund.objects.filter(payment=payment)
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

        session_data = self._get_session_data()
        if not session_data:
            messages.error(request, "Session expired. Please start over.")
            return redirect("payments:refund_create", payment_pk=self.payment.pk)

        refund_amount = Decimal(session_data["amount"])
        reason = session_data["reason"]
        notes = session_data.get("notes", "")
        idempotency_key = str(uuid.uuid4())

        with db_transaction.atomic():
            # FIX 1: Lock the Payment row — serializes concurrent refund attempts
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

            # FIX 1: Re-compute under lock — prevents TOCTOU race condition
            already_refunded = _compute_already_refunded(payment)
            max_refundable = payment.amount_paid - already_refunded
            if refund_amount > max_refundable:
                messages.error(
                    request,
                    f"Refund amount ${refund_amount} now exceeds maximum refundable "
                    f"${max_refundable} (another refund may have been issued). "
                    "Please start over.",
                )
                request.session.pop(REFUND_SESSION_KEY, None)
                return redirect("payments:refund_create", payment_pk=payment.pk)

            # Gateway call inside the atomic block.
            # If the DB write below fails, log the gateway_refund_id for manual reconciliation.
            try:
                gateway = get_gateway()
                gateway_result = gateway.create_refund(
                    gateway_charge_id=payment.gateway_charge_id,
                    amount=refund_amount,
                    reason=reason,
                    idempotency_key=idempotency_key,
                )
            except GatewayError as exc:
                logger.error(
                    "payments.refund.gateway_error type=%s gateway_code=%s payment_pk=%s",
                    type(exc).__name__,
                    exc.gateway_code,
                    str(payment.pk),
                )
                messages.error(request, "Refund could not be processed. Please try again.")
                return redirect("payments:refund_confirm", payment_pk=payment.pk)

            # DB write — if this fails, log the gateway_refund_id for reconciliation
            try:
                refund = Refund.objects.create(
                    payment=payment,
                    amount=refund_amount,
                    reason=reason,
                    notes=notes,
                    gateway_refund_id=gateway_result["gateway_refund_id"],
                    authorized_by=request.user,
                    refunded_at=timezone.now(),
                )
            except Exception as db_exc:
                # CRITICAL: Stripe refund issued but DB write failed.
                # Log the gateway_refund_id for manual reconciliation.
                logger.critical(
                    "payments.refund.db_write_failed RECONCILIATION_REQUIRED "
                    "gateway_refund_id=%s payment_pk=%s type=%s",
                    gateway_result["gateway_refund_id"],
                    str(payment.pk),
                    type(db_exc).__name__,
                )
                raise  # Re-raise so the transaction rolls back and the 500 page shows

            PaymentAuditEntry.objects.create(
                payment_intent=payment.intent,
                payment=payment,
                refund=refund,
                action="refund_requested",
                actor=request.user,
                actor_ip=_mask_ip(request.META.get("REMOTE_ADDR", "")),
                details={
                    "refund_pk": str(refund.pk),
                    "amount": str(refund_amount),
                    "reason": reason,
                    "gateway_refund_id": gateway_result["gateway_refund_id"],
                    "payment_pk": str(payment.pk),
                    "staff_pk": str(request.user.pk),
                },
            )

        # Clear session after successful refund
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
