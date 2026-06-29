"""
Refund views for the GovStack Payments BB.

Access: staff users only (is_staff=True). Citizens cannot initiate refunds.
All refund actions are audit-logged. Partial refunds are supported.

Security:
- Amount validated server-side: refund_amount <= (amount_paid - already_refunded)
- Gateway charge ID never exposed in logs (PCI DSS)
- No PII in log statements
- StaffRequiredMixin on ALL views: citizens must never reach these views
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


class StaffRequiredMixin(UserPassesTestMixin):
    """Restrict view to staff users (is_staff=True)."""

    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_staff


def _already_refunded(payment):
    """
    Sum of all refunds on this payment.

    Because Refund has no status field (it is created only after the gateway
    confirms success), every Refund row counts toward the refunded total.
    """
    result = (
        Refund.objects.filter(payment=payment).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    return result


def _mask_ip(request):
    """
    Return a masked IP for PIPEDA compliance.
    IPv4: zero the last octet.  IPv6: zero the last 80 bits.
    """
    import ipaddress

    ip = request.META.get("REMOTE_ADDR", "")
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


class RefundCreateView(LoginRequiredMixin, StaffRequiredMixin, FormView):
    """
    Initiate a partial or full refund on a completed payment.

    Staff-only.  Logs the initiating staff member's PK (never email or PII).
    Refund is created only after the gateway confirms success.  The Refund row
    and audit entry are written atomically.
    """

    template_name = "payments/refund_create.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.payment = get_object_or_404(Payment, pk=kwargs["payment_pk"])

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
        already = _already_refunded(self.payment)
        ctx["already_refunded"] = already
        ctx["max_refundable"] = self.payment.amount_paid - already
        return ctx

    def form_valid(self, form):
        refund_amount = form.cleaned_data["amount"]
        reason = form.cleaned_data["reason"]
        notes = form.cleaned_data.get("notes", "")

        # Final server-side validation (client max= attribute is UX only)
        already = _already_refunded(self.payment)
        max_refundable = self.payment.amount_paid - already
        if refund_amount > max_refundable:
            form.add_error(
                "amount",
                f"Refund amount exceeds maximum refundable (${max_refundable}).",
            )
            return self.form_invalid(form)

        idempotency_key = str(uuid.uuid4())

        try:
            gateway = get_gateway()
            gateway_result = gateway.create_refund(
                gateway_charge_id=self.payment.gateway_charge_id,
                amount=refund_amount,
                reason=reason,
                idempotency_key=idempotency_key,
            )
        except GatewayError as exc:
            # Log gateway_code (not gateway_charge_id — PCI DSS)
            logger.error(
                "payments.refund.gateway_error type=%s gateway_code=%s payment_pk=%s",
                type(exc).__name__,
                exc.gateway_code,
                str(self.payment.pk),
            )
            messages.error(
                self.request,
                "Refund could not be processed. Please try again or contact support.",
            )
            return self.form_invalid(form)

        # Create Refund row and audit entry atomically
        with transaction.atomic():
            refund = Refund.objects.create(
                payment=self.payment,
                amount=refund_amount,
                reason=reason,
                notes=notes,
                gateway_refund_id=gateway_result["gateway_refund_id"],
                # authorized_by is the required FK on the Refund model (not initiated_by)
                authorized_by=self.request.user,
                # refunded_at is required by the model; set to now
                refunded_at=timezone.now(),
            )

            PaymentAuditEntry.objects.create(
                # payment_intent FK (nullable) — wire through payment.intent
                payment_intent=self.payment.intent,
                # payment FK (nullable) — link to the Payment record directly
                payment=self.payment,
                # refund FK (nullable) — link to the new Refund row
                refund=refund,
                # "refund_requested" is the valid ACTION_CHOICES value for this stage
                action="refund_requested",
                actor=self.request.user,
                actor_ip=_mask_ip(self.request),
                # PaymentAuditEntry has no actor_email field — omitted (PIPEDA)
                details={
                    "refund_pk": str(refund.pk),
                    "amount": str(refund_amount),
                    "reason": reason,
                    "payment_pk": str(self.payment.pk),
                    "staff_pk": str(self.request.user.pk),
                },
            )

        logger.info(
            "payments.refund.initiated refund_pk=%s payment_pk=%s staff_pk=%s",
            str(refund.pk),
            str(self.payment.pk),
            str(self.request.user.pk),
        )

        messages.success(
            self.request,
            f"Refund of ${refund_amount} initiated successfully.",
        )
        return redirect("payments:refund_detail", refund_pk=refund.pk)


class RefundDetailView(LoginRequiredMixin, StaffRequiredMixin, TemplateView):
    """Show refund details. Staff-only."""

    template_name = "payments/refund_detail.html"

    def get(self, request, *args, **kwargs):
        self.refund = get_object_or_404(Refund, pk=kwargs["refund_pk"])
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["refund"] = getattr(self, "refund", None)
        return ctx
