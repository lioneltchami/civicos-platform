"""
Forms for staff notification management.
"""

from django import forms
from django.utils.translation import gettext_lazy as _


class StaffNotificationSendForm(forms.Form):
    """
    Form for staff to send a one-off email notification to a citizen.

    Validation of the recipient (must be a registered, non-staff citizen)
    is performed in the view, not here, so that we can attach a model-aware
    error message.
    """

    recipient_email = forms.EmailField(
        label=_("Recipient email"),
        help_text=_("Must be a registered citizen email address."),
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "placeholder": "citizen@example.com",
                "autocomplete": "off",
            }
        ),
    )
    subject = forms.CharField(
        label=_("Subject"),
        max_length=255,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": _("Email subject"),
            }
        ),
    )
    body = forms.CharField(
        label=_("Message body"),
        max_length=5000,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 8,
                "placeholder": _("Write your message here…"),
            }
        ),
    )
