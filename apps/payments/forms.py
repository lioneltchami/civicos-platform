"""
Forms for the Payments BB.

Design decisions:
- FeePaymentForm is intentionally minimal: province + fee_code + quantity.
  Amount is derived server-side from FeeSchedule — never trust client-submitted amounts.
- RefundForm is staff-only: amount validated against amount_paid minus already-refunded.
- No card fields here — Stripe Elements renders those in a Stripe-hosted iframe.
- All fields have explicit labels for WCAG 2.1 AA compliance.
- Tax is only applied when FeeSchedule.is_taxable is True.
"""
import logging
from decimal import Decimal

from django import forms
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _

from apps.payments.models import FeeSchedule, Refund, TaxRate

logger = logging.getLogger(__name__)

PROVINCE_CHOICES = [
    ("", _("— Select province/territory —")),
    ("AB", _("Alberta")),
    ("BC", _("British Columbia")),
    ("MB", _("Manitoba")),
    ("NB", _("New Brunswick")),
    ("NL", _("Newfoundland and Labrador")),
    ("NS", _("Nova Scotia")),
    ("NT", _("Northwest Territories")),
    ("NU", _("Nunavut")),
    ("ON", _("Ontario")),
    ("PE", _("Prince Edward Island")),
    ("QC", _("Québec")),
    ("SK", _("Saskatchewan")),
    ("YT", _("Yukon")),
]


class FeePaymentForm(forms.Form):
    """
    Step 1 of the fee payment flow: select fee and province.
    Amount is computed server-side — never from this form.
    """

    province = forms.ChoiceField(
        label=_("Province / Territory"),
        choices=PROVINCE_CHOICES,
        widget=forms.Select(attrs={"autocomplete": "address-level1"}),
    )
    fee_code = forms.CharField(
        label=_("Fee Code"),
        max_length=50,
        widget=forms.TextInput(attrs={
            "placeholder": _("e.g. PERMIT-BUILDING"),
            "autocomplete": "off",
        }),
    )
    quantity = forms.IntegerField(
        label=_("Quantity"),
        min_value=1,
        max_value=999,
        initial=1,
        widget=forms.NumberInput(attrs={"min": "1", "max": "999"}),
    )
    # Payer contact — used for receipt / follow-up. Not written to audit log.
    payer_reference = forms.CharField(
        label=_("Reference / File Number"),
        max_length=100,
        required=False,
        help_text=_("Optional: your permit number, file number, or account reference."),
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )

    def clean_province(self):
        value = self.cleaned_data.get("province", "").strip().upper()
        if not value:
            raise forms.ValidationError(_("Please select a province or territory."))
        return value

    def clean_fee_code(self):
        return self.cleaned_data.get("fee_code", "").strip().upper()

    def clean(self):
        cleaned = super().clean()
        province = cleaned.get("province")
        fee_code = cleaned.get("fee_code")
        quantity = cleaned.get("quantity", 1)

        if province and fee_code:
            # Server-side amount derivation using the model's get_current() helper.
            # Never trust a client-submitted amount.
            fee = FeeSchedule.get_current(fee_code=fee_code, province=province)
            if fee is None:
                raise forms.ValidationError(
                    _("No active fee found for code %(code)s in %(province)s."),
                    params={"code": fee_code, "province": province},
                )
            cleaned["fee"] = fee
            cleaned["subtotal"] = fee.amount * Decimal(str(quantity))

            # Tax applies only when the fee schedule row marks it taxable.
            if fee.is_taxable:
                tax = TaxRate.get_for_province(province)
            else:
                tax = None

            cleaned["tax_rate"] = tax
            if tax:
                cleaned["tax_amount"] = (
                    cleaned["subtotal"] * tax.combined_rate
                ).quantize(Decimal("0.01"))
                cleaned["total"] = cleaned["subtotal"] + cleaned["tax_amount"]
            else:
                cleaned["tax_amount"] = Decimal("0.00")
                cleaned["total"] = cleaned["subtotal"]

        return cleaned


class RefundForm(forms.Form):
    """
    Staff-initiated refund form.

    Amount is validated against payment.amount_paid minus the sum of all
    existing Refund rows on this payment.  Requires ``payment`` kwarg in
    __init__.

    The Refund model has no status field, so every existing refund row counts
    toward the already-refunded total.
    """

    amount = forms.DecimalField(
        label=_("Refund Amount (CAD)"),
        min_value=Decimal("0.01"),
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "step": "0.01",
            "min": "0.01",
        }),
    )
    reason = forms.ChoiceField(
        label=_("Reason"),
        choices=Refund.REASON_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    notes = forms.CharField(
        label=_("Internal Notes"),
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        help_text=_("Optional. For internal records only — not shown to the payer."),
    )

    def __init__(self, *args, payment=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.payment = payment

        if payment is not None:
            already = (
                Refund.objects.filter(payment=payment).aggregate(
                    total=Sum("amount")
                )["total"]
                or Decimal("0.00")
            )
            max_refundable = payment.amount_paid - already
            self.fields["amount"].max_value = max_refundable
            self.fields["amount"].widget.attrs["max"] = str(max_refundable)
            self.fields["amount"].help_text = _(
                f"Maximum refundable: ${max_refundable}. "
                f"Original amount paid: ${payment.amount_paid}."
            )

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is not None and amount <= Decimal("0.00"):
            raise forms.ValidationError(_("Refund amount must be greater than zero."))
        return amount
