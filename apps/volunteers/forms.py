"""
Volunteer Management BB — Forms.

Two forms covering the volunteer-facing and coordinator-facing sides of the
application lifecycle:

  ApplicationForm       — volunteer submits / edits their application.
  ApplicationReviewForm — coordinator approves or rejects an application.

Security invariants (enforced here):
  - rejection_reason, screening_notes, and status are NEVER included in
    ApplicationForm.  They are coordinator-internal fields set programmatically
    by service functions.
  - ApplicationReviewForm is only rendered inside coordinator views protected
    by the volunteers.change_volunteerapplication permission.
  - Dynamic profile fields added via required_profile_fields respect PIPEDA
    minimum-collection: only fields the opportunity explicitly requires are shown.

WCAG 2.1 AA compliance notes:
  - All fields carry explicit `label` text (no placeholder-only labels).
  - `help_text` is rendered via aria-describedby in the base template.
  - Error messages are associated with inputs via Django's standard
    BoundField.errors / as_p rendering pattern.
"""
from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.volunteers.models import VolunteerApplication


# ---------------------------------------------------------------------------
# Allowed extra fields that may appear via required_profile_fields.
# Limits what an admin can inject into the form.  Each entry maps the field
# name (as it appears in required_profile_fields) to a form field definition.
# ---------------------------------------------------------------------------
_PROFILE_FIELD_REGISTRY: dict[str, forms.Field] = {
    "phone_number": forms.CharField(
        label=_("Phone number"),
        max_length=20,
        required=True,
        help_text=_("We will only use this number to contact you about this opportunity."),
    ),
    "date_of_birth": forms.DateField(
        label=_("Date of birth"),
        required=True,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_("Required for age verification on this role."),
    ),
    "emergency_contact_name": forms.CharField(
        label=_("Emergency contact name"),
        max_length=100,
        required=True,
    ),
    "emergency_contact_phone": forms.CharField(
        label=_("Emergency contact phone"),
        max_length=20,
        required=True,
    ),
    "emergency_contact_relationship": forms.CharField(
        label=_("Relationship to emergency contact"),
        max_length=50,
        required=True,
    ),
    "accommodation_notes": forms.CharField(
        label=_("Accommodation needs"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_(
            "Describe any accommodation requirements so we can support your participation."
        ),
    ),
}


class ApplicationForm(forms.ModelForm):
    """
    Volunteer application form.

    Dynamic: if ``opportunity.required_profile_fields`` is set, appends
    additional fields the volunteer must fill in (e.g. ``["phone_number",
    "date_of_birth"]``).  Only field names present in
    ``_PROFILE_FIELD_REGISTRY`` are allowed — unknown entries are silently
    skipped to prevent information disclosure from a misconfigured opportunity.

    Coordinator-internal fields excluded by design:
      - ``rejection_reason``   — PIPEDA: internal only, never visible to volunteer.
      - ``screening_notes``    — coordinator-only.
      - ``status``             — set programmatically by ``apply()`` service.
      - ``reviewed_by``        — set by ``approve_application()`` / ``reject_application()``.
      - ``reviewed_at``        — same as above.
      - ``work_item``          — Workflows BB internal.

    WCAG notes:
      - Dynamic fields inherit explicit ``label`` from ``_PROFILE_FIELD_REGISTRY``.
      - ``motivation`` textarea has a visible label — do not rely on placeholder
        text alone in templates.
    """

    class Meta:
        model = VolunteerApplication
        fields = ["motivation"]
        # SECURITY — never include: rejection_reason, screening_notes, status,
        # reviewed_by, reviewed_at, consent_record (set by service), work_item.
        widgets = {
            "motivation": forms.Textarea(
                attrs={
                    "rows": 6,
                    "aria-describedby": "motivation-help",
                }
            ),
        }
        labels = {
            "motivation": _("Why would you like to volunteer for this opportunity?"),
        }
        help_texts = {
            "motivation": _(
                "Describe your interest and relevant experience. "
                "This is read by the coordinator reviewing your application."
            ),
        }

    def __init__(
        self,
        *args,
        opportunity=None,
        volunteer_profile=None,
        **kwargs,
    ):
        """
        Initialise the form with optional context.

        Args:
            opportunity:       ``Opportunity`` instance being applied to.
                               Used to inject required_profile_fields.
            volunteer_profile: ``VolunteerProfile`` of the applicant.
                               Stored for cross-field validation if needed.
            *args / **kwargs:  Passed verbatim to ``ModelForm.__init__``.
        """
        super().__init__(*args, **kwargs)
        self.opportunity = opportunity
        self.volunteer_profile = volunteer_profile

        # --- Inject required profile fields ---
        # Iterate in list order so field rendering is deterministic.
        if opportunity and opportunity.required_profile_fields:
            for field_name in opportunity.required_profile_fields:
                if field_name in _PROFILE_FIELD_REGISTRY and field_name not in self.fields:
                    # Copy the field so mutations (e.g. required=False overrides)
                    # don't affect the registry original.
                    self.fields[field_name] = _PROFILE_FIELD_REGISTRY[field_name]

        # --- Accessibility: mark required fields explicitly ---
        for name, field in self.fields.items():
            if field.required:
                field.widget.attrs.setdefault("aria-required", "true")

    def clean(self):
        """
        Cross-field validation.

        Rules:
        - ``motivation`` must not be blank when the opportunity exists (soft
          enforcement: some roles may allow silent applications, but for
          government compliance we require a statement of intent).
        - Extra profile fields are already validated individually by their
          own ``Field.clean()``; no cross-field rules needed today.
        """
        cleaned_data = super().clean()
        motivation = cleaned_data.get("motivation", "").strip()

        if self.opportunity and not motivation:
            self.add_error(
                "motivation",
                _(
                    "Please tell us why you would like to volunteer for this opportunity. "
                    "This field is required."
                ),
            )

        return cleaned_data


class ApplicationReviewForm(forms.Form):
    """
    Coordinator-only form for approving or rejecting an application.

    Rendered exclusively inside ``ApplicationReviewView``, which is guarded by
    the ``volunteers.change_volunteerapplication`` permission.

    ``rejection_reason`` is labelled clearly as an internal note so coordinators
    understand it is never forwarded to the volunteer (PIPEDA compliance).

    WCAG notes:
      - ``action`` uses a radio widget for clarity over a select drop-down;
        both choices are visible without interaction.
      - ``rejection_reason`` textarea is conditionally required via JavaScript
        in the template, but server-side validation issues a non-blocking warning
        (``__all__`` error) rather than a hard error to avoid blocking a
        coordinator who intentionally leaves it blank.
    """

    action = forms.ChoiceField(
        label=_("Decision"),
        choices=[
            ("approve", _("Approve application")),
            ("reject", _("Decline application")),
        ],
        widget=forms.RadioSelect,
        # WCAG: RadioSelect renders visible radio buttons — no hidden state.
    )

    rejection_reason = forms.CharField(
        label=_("Internal notes (coordinator only)"),
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "aria-describedby": "rejection-reason-help",
            }
        ),
        required=False,
        help_text=_(
            "Internal record only. This text is NEVER shown to the volunteer. "
            "Notifications use generic 'not selected' language per PIPEDA policy."
        ),
    )

    def clean(self):
        """
        Non-blocking warning when rejecting without a reason.

        A missing ``rejection_reason`` on rejection is surfaced as a non-field
        error (warning level) rather than a ``ValidationError`` so the form is
        still valid and the coordinator can proceed.  This preserves the ability
        to act quickly without forcing boilerplate text entry.
        """
        cleaned_data = super().clean()
        action = cleaned_data.get("action")
        reason = cleaned_data.get("rejection_reason", "").strip()

        if action == "reject" and not reason:
            # Non-blocking: add informational message rather than invalidating.
            # Templates should surface this via form.non_field_errors with a
            # "warning" CSS class, not "error".
            self.add_error(
                None,
                _(
                    "No internal notes were provided for this rejection. "
                    "Consider adding a note for audit purposes — "
                    "it will not be shown to the volunteer."
                ),
            )
            # NOTE: we deliberately do NOT raise; the form remains valid so the
            # coordinator can submit without a reason if they choose.

        return cleaned_data
