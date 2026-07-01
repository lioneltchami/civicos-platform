"""
Analytics & Reporting BB — Forms.

DateRangeForm:     date-range picker for reconciliation and receipts exports.
FiscalYearEndForm: fiscal year end date picker for T3010 prep view.

MAX_RANGE_DAYS          = 92   — one quarter; reconciliation & receipts list (HTML)
MAX_RECEIPT_EXPORT_DAYS = 366  — one year + leap day; receipts CSV export
"""
from __future__ import annotations

from datetime import date, timedelta

from django import forms
from django.utils.translation import gettext_lazy as _

# Reconciliation HTML table and receipts list view: one quarter.
MAX_RANGE_DAYS = 92

# Receipts CSV export: full tax year (366 covers leap years).
MAX_RECEIPT_EXPORT_DAYS = 366


class DateRangeForm(forms.Form):
    """
    Simple start/end date range picker.

    Validation rules:
    - Both fields are required.
    - end must be >= start.
    - Range must not exceed MAX_RANGE_DAYS (92 days).
    """

    start = forms.DateField(
        label=_("From"),
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control",
                "aria-describedby": "start-help",
            }
        ),
        help_text=_("Start of the reconciliation window (inclusive)."),
    )
    end = forms.DateField(
        label=_("To"),
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control",
                "aria-describedby": "end-help",
            }
        ),
        help_text=_("End of the reconciliation window (inclusive)."),
    )

    def clean(self) -> dict:
        cleaned = super().clean()
        start: date | None = cleaned.get("start")
        end: date | None = cleaned.get("end")

        if start is None or end is None:
            # Individual field errors are already set by the field validators.
            return cleaned

        if end < start:
            raise forms.ValidationError(
                _("The end date must be on or after the start date."),
                code="end_before_start",
            )

        delta_days = (end - start).days + 1  # inclusive day count
        if delta_days > MAX_RANGE_DAYS:
            raise forms.ValidationError(
                _(
                    "The selected range spans %(days)d days. "
                    "Please select a range of %(max)d days or fewer."
                ),
                code="range_too_large",
                params={"days": delta_days, "max": MAX_RANGE_DAYS},
            )

        return cleaned

    @property
    def date_range(self) -> tuple[date, date] | None:
        """Return (start, end) tuple if the form is valid, else None."""
        if not self.is_valid():
            return None
        return self.cleaned_data["start"], self.cleaned_data["end"]


class ReceiptExportForm(forms.Form):
    """
    Date-range picker for the receipts CSV export.

    Same fields as DateRangeForm but allows up to MAX_RECEIPT_EXPORT_DAYS (366)
    so finance staff can export a full tax year in one request.
    """

    start = forms.DateField(
        label=_("From"),
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        help_text=_("Start of the receipts window (inclusive)."),
    )
    end = forms.DateField(
        label=_("To"),
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        help_text=_("End of the receipts window (inclusive)."),
    )

    def clean(self) -> dict:
        cleaned = super().clean()
        start: date | None = cleaned.get("start")
        end: date | None = cleaned.get("end")

        if start is None or end is None:
            return cleaned

        if end < start:
            raise forms.ValidationError(
                _("The end date must be on or after the start date."),
                code="end_before_start",
            )

        delta_days = (end - start).days + 1
        if delta_days > MAX_RECEIPT_EXPORT_DAYS:
            raise forms.ValidationError(
                _(
                    "The selected range spans %(days)d days. "
                    "Please select a range of %(max)d days or fewer (one full year)."
                ),
                code="range_too_large",
                params={"days": delta_days, "max": MAX_RECEIPT_EXPORT_DAYS},
            )

        return cleaned

    @property
    def date_range(self) -> tuple[date, date] | None:
        if not self.is_valid():
            return None
        return self.cleaned_data["start"], self.cleaned_data["end"]


class FiscalYearEndForm(forms.Form):
    """
    Fiscal year end date picker for the T3010 prep and T3010 export views.

    Accepts any past or current calendar date. The view defaults to December 31
    of the previous calendar year when no date is supplied.
    """

    fiscal_year_end = forms.DateField(
        label=_("Fiscal Year End"),
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        help_text=_(
            "Last day of the charity's fiscal year (e.g. December 31). "
            "The T3010 preparatory data will cover the 12 months ending on this date."
        ),
    )

    def clean_fiscal_year_end(self) -> date:
        d: date = self.cleaned_data["fiscal_year_end"]
        if d > date.today():
            raise forms.ValidationError(
                _("Fiscal year end cannot be in the future."),
                code="future_fiscal_year",
            )
        return d
