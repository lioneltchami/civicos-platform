"""
Analytics & Reporting BB — Forms.

DateRangeForm: date-range picker for the payment reconciliation view.
Enforces a 92-day maximum window (roughly one quarter) to prevent
unbounded queries on large datasets.
"""
from __future__ import annotations

from datetime import date, timedelta

from django import forms
from django.utils.translation import gettext_lazy as _

# Maximum reconciliation window (one quarter ≈ 92 days).
MAX_RANGE_DAYS = 92


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
