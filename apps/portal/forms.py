"""
Forms for the citizen portal.
"""
from __future__ import annotations
from django import forms
from django.utils.translation import gettext_lazy as _
from .models import ServiceRequest, ServiceRequestStatus


class ServiceRequestSubmitForm(forms.Form):
    """
    Generic service request submission form.
    Used when a citizen submits a request not linked to a specific CMS form.
    """
    service_name = forms.CharField(
        max_length=255,
        label=_("Service requested / Service demandé"),
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    description = forms.CharField(
        label=_("Description / Description"),
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=_("Describe your request in as much detail as possible. / "
                    "Décrivez votre demande avec le plus de détails possible."),
    )
    contact_email = forms.EmailField(
        label=_("Contact email / Courriel de contact"),
        help_text=_("We'll send status updates to this address. / "
                    "Nous enverrons les mises à jour à cette adresse."),
    )
    contact_phone = forms.CharField(
        max_length=30,
        required=False,
        label=_("Phone number (optional) / Numéro de téléphone (facultatif)"),
    )
    consent_given = forms.BooleanField(
        required=True,
        label=_("I consent to the collection of this information for the purpose of processing my request. / "
                "Je consens à la collecte de ces renseignements aux fins du traitement de ma demande."),
        error_messages={
            "required": _("You must provide consent to submit a request. / "
                          "Vous devez donner votre consentement pour soumettre une demande."),
        },
    )

    def get_submission_data(self) -> dict:
        """Return cleaned form data as a dict for storage in submission_data JSON field."""
        data = self.cleaned_data.copy()
        data.pop("consent_given", None)  # Consent flag stored separately, not in payload
        return data


class StatusUpdateForm(forms.Form):
    """
    Staff-only form for updating a service request status.
    Used in the staff case management view.
    """
    new_status = forms.ChoiceField(
        choices=ServiceRequestStatus.choices,
        label=_("New status / Nouveau statut"),
    )
    public_note = forms.CharField(
        required=False,
        label=_("Note to citizen / Note au citoyen"),
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_("This will be shown to the citizen in their portal. Keep plain and non-technical. / "
                    "Ceci sera affiché au citoyen dans son portail. Utilisez un langage simple."),
        max_length=1000,
    )

    def clean_new_status(self) -> str:
        status = self.cleaned_data.get("new_status")
        if status not in ServiceRequestStatus.values:
            raise forms.ValidationError(_("Invalid status."))
        return status


class RequestCancelForm(forms.Form):
    """Citizen-initiated cancellation with optional reason."""
    reason = forms.CharField(
        required=False,
        max_length=500,
        label=_("Reason for cancellation (optional) / Raison de l'annulation (facultatif)"),
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    confirm = forms.BooleanField(
        required=True,
        label=_("I confirm I want to cancel this request. / Je confirme vouloir annuler cette demande."),
    )
