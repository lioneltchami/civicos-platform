"""
Back-office forms for service request management.

ServiceRequestStatusForm  — advance status + optional public note for the citizen.
ServiceRequestNotesForm   — update internal staff notes (never citizen-visible).
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.portal.models import ServiceRequestStatus


class ServiceRequestStatusForm(forms.Form):
    """
    Form for advancing a service request to a new status.

    The public_note field is shown to the citizen in their portal, so staff
    should use plain, non-technical language.
    """

    new_status = forms.ChoiceField(
        choices=ServiceRequestStatus.choices,
        label=_("New status"),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    public_note = forms.CharField(
        required=False,
        max_length=500,
        label=_("Public note (visible to citizen)"),
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "class": "form-control",
            }
        ),
        help_text=_("Explain the status change to the citizen."),
    )

    def clean_new_status(self):
        value = self.cleaned_data.get("new_status", "")
        if value not in ServiceRequestStatus.values:
            raise forms.ValidationError(_("Select a valid status."))
        return value


class ServiceRequestNotesForm(forms.Form):
    """
    Form for updating internal staff notes on a service request.

    These notes are never shown to the citizen — they are for staff
    coordination and case management only.
    """

    internal_notes = forms.CharField(
        required=False,
        max_length=5000,
        label=_("Internal notes (staff only)"),
        widget=forms.Textarea(
            attrs={
                "rows": 6,
                "class": "form-control",
            }
        ),
        help_text=_("These notes are never shown to the citizen."),
    )
