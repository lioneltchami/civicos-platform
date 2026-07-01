"""
Custom allauth forms for CivicOS citizen authentication.
"""
from __future__ import annotations
from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


LANGUAGE_CHOICES = [
    ("en", _("English")),
    ("fr", _("Français")),
]


class CitizenSignupForm(forms.Form):
    """
    Extra fields collected during citizen sign-up.
    allauth calls signup(request, user) after creating the user.
    """
    preferred_language = forms.ChoiceField(
        choices=LANGUAGE_CHOICES,
        initial="en",
        widget=forms.RadioSelect,
        label=_("Preferred language / Langue préférée"),
    )
    terms_accepted = forms.BooleanField(
        required=True,
        label=_(
            "I agree to the Terms of Service and Privacy Policy / "
            "J'accepte les conditions d'utilisation et la politique de confidentialité"
        ),
        error_messages={
            "required": _(
                "You must accept the terms of service to create an account. / "
                "Vous devez accepter les conditions d'utilisation pour créer un compte."
            )
        },
    )

    def signup(self, request, user) -> None:
        """Called by allauth after the user record is created."""
        user.preferred_language = self.cleaned_data.get("preferred_language", "en")
        user.terms_accepted_at = timezone.now()
        user.save(update_fields=["preferred_language", "terms_accepted_at"])


class ProfileUpdateForm(forms.ModelForm):
    """Form for citizens to update their profile information."""

    class Meta:
        model = get_user_model()
        fields = ["first_name", "last_name", "phone_number", "preferred_language"]
        widgets = {
            "preferred_language": forms.RadioSelect(choices=LANGUAGE_CHOICES),
        }
        labels = {
            "first_name": _("First name / Prénom"),
            "last_name": _("Last name / Nom de famille"),
            "phone_number": _("Phone number / Numéro de téléphone"),
            "preferred_language": _("Preferred language / Langue préférée"),
        }
        help_texts = {
            "phone_number": _("Optional. Include country code, e.g. +1 613 555 0100"),
        }
