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
from decimal import Decimal, ROUND_DOWN

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
            # C-D: exclude GATEWAY_STATUS_FAILED rows — failed Stripe calls are
            # not real money movements and must not reduce the max refundable cap.
            # This mirrors _compute_already_refunded() in views/refund.py.
            already = (
                Refund.objects.filter(payment=payment)
                .exclude(gateway_status=Refund.GATEWAY_STATUS_FAILED)
                .aggregate(total=Sum("amount"))["total"]
                or Decimal("0.00")
            )
            max_refundable = payment.amount_paid - already
            self.fields["amount"].max_value = max_refundable
            self.fields["amount"].widget.attrs["max"] = str(max_refundable)
            max_display = max_refundable.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            paid_display = payment.amount_paid.quantize(Decimal("0.01"))
            self.fields["amount"].help_text = _(
                f"Maximum refundable: ${max_display}. "
                f"Original amount paid: ${paid_display}."
            )

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is not None and amount <= Decimal("0.00"):
            raise forms.ValidationError(_("Refund amount must be greater than zero."))
        return amount


class DonationForm(forms.Form):
    """
    Donor selects campaign, amount, and optionally sets up recurring giving.
    Eligible amount = amount - advantage_amount (CRA rule).

    Security: amount is derived server-side and authoritative from session —
    this form only collects donor intent for session storage, never for
    direct charging.
    """

    from apps.payments.models import DonationCampaign as _DonationCampaign  # noqa: F811

    campaign = forms.ModelChoiceField(
        label=_("Campaign"),
        queryset=None,  # Set in __init__ to avoid import at class-definition time
        empty_label=_("— Select a campaign —"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    amount = forms.DecimalField(
        label=_("Donation Amount (CAD)"),
        min_value=Decimal("1.00"),
        max_value=Decimal("999999.99"),  # $1M cap — prevents accidental 7-figure charges
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "step": "0.01",
            "min": "1.00",
            "max": "999999.99",
            "placeholder": "0.00",
        }),
    )
    is_recurring = forms.BooleanField(
        label=_("Make this a recurring gift"),
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input", "id": "id_is_recurring"}),
    )
    frequency = forms.ChoiceField(
        label=_("Frequency"),
        required=False,
        choices=[("", _("— Select frequency —"))] + [
            ("monthly", _("Monthly")),
            ("quarterly", _("Quarterly")),
            ("annually", _("Annually")),
        ],
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    donor_name = forms.CharField(
        label=_("Full Name"),
        max_length=200,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "autocomplete": "name",
            "placeholder": _("Your legal name"),
        }),
    )
    donor_email = forms.EmailField(
        label=_("Email Address"),
        widget=forms.EmailInput(attrs={
            "class": "form-control",
            "autocomplete": "email",
            "placeholder": _("you@example.ca"),
        }),
    )
    is_anonymous = forms.BooleanField(
        label=_("Make my donation anonymous"),
        required=False,
        help_text=_("Your name will not appear in public donor lists."),
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    advantage_amount = forms.DecimalField(
        label=_("Advantage Amount (CAD)"),
        required=False,
        min_value=Decimal("0.00"),
        max_digits=10,
        decimal_places=2,
        initial=Decimal("0.00"),
        help_text=_(
            "Fair market value of any benefit you received in exchange for this donation "
            "(CRA requirement). Enter 0.00 if none."
        ),
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "step": "0.01",
            "min": "0.00",
        }),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.payments.models import DonationCampaign
        self.fields["campaign"].queryset = DonationCampaign.objects.filter(is_active=True).order_by(
            "sort_order", "name_en"
        )

    def clean(self):
        cleaned = super().clean()
        amount = cleaned.get("amount")
        advantage_amount = cleaned.get("advantage_amount") or Decimal("0.00")
        is_recurring = cleaned.get("is_recurring", False)
        frequency = cleaned.get("frequency", "")

        # CRA rule: advantage_amount must be less than donation amount
        if amount is not None and advantage_amount >= amount:
            raise forms.ValidationError(
                _(
                    "The advantage amount ($%(adv)s) must be less than the donation amount "
                    "($%(amt)s). The eligible tax credit amount must be positive."
                ),
                params={"adv": advantage_amount, "amt": amount},
            )

        # Compute eligible amount (CRA receipt rule)
        if amount is not None:
            cleaned["eligible_amount"] = max(
                Decimal("0.00"),
                amount - advantage_amount,
            )

        # Frequency is required when recurring is selected
        if is_recurring and not frequency:
            self.add_error(
                "frequency",
                _("Please select a frequency for your recurring gift."),
            )

        cleaned["advantage_amount"] = advantage_amount
        return cleaned
