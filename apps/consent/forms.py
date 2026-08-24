"""
Forms for the Consent & Privacy building block.

ConsentUpdateForm — lets a citizen grant or withdraw a single consent category.
ExportRequestForm — lets a citizen submit a PIPEDA data export request.
"""

from django import forms
from django.utils.translation import gettext_lazy as _


class ConsentUpdateForm(forms.Form):
    """Single-category consent toggle submitted from the consent dashboard."""

    ACTION_GRANT = "grant"
    ACTION_WITHDRAW = "withdraw"
    ACTION_CHOICES = [  # noqa: RUF012
        (ACTION_GRANT, _("Grant Consent")),
        (ACTION_WITHDRAW, _("Withdraw Consent")),
    ]

    action = forms.ChoiceField(choices=ACTION_CHOICES, widget=forms.HiddenInput)
    category_slug = forms.CharField(widget=forms.HiddenInput, max_length=100)

    def clean_action(self):  # noqa: ANN201
        action = self.cleaned_data["action"]
        if action not in {self.ACTION_GRANT, self.ACTION_WITHDRAW}:
            raise forms.ValidationError(_("Invalid action."))
        return action

    def clean_category_slug(self):  # noqa: ANN201
        slug = self.cleaned_data.get("category_slug", "").strip()
        from apps.consent.models import ConsentCategory

        if not ConsentCategory.objects.filter(slug=slug, is_active=True).exists():
            raise forms.ValidationError(_("Invalid consent category."))
        return slug


class ExportRequestForm(forms.Form):
    """Confirmation form before submitting a PIPEDA data export request."""

    confirm = forms.BooleanField(
        required=True,
        label=_(
            "I understand that my data export will be prepared within 30 days "
            "and the download link will expire after 7 days."
        ),
        error_messages={"required": _("You must confirm to request your data export.")},
    )
