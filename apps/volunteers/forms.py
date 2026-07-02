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

import copy

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.volunteers.models import HoursLog, Shift, VolunteerApplication


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
                    # Deep-copy the field so mutations (e.g. required=False
                    # overrides, aria-describedby additions, widget.attrs
                    # updates) don't corrupt the shared registry object for
                    # subsequent requests.
                    self.fields[field_name] = copy.deepcopy(_PROFILE_FIELD_REGISTRY[field_name])

        # --- Accessibility: mark required fields explicitly ---
        for name, field in self.fields.items():
            if field.required:
                field.widget.attrs.setdefault("aria-required", "true")

        # WCAG 2.1 SC 1.3.1 fix: wire aria-describedby to BOTH the hint
        # paragraph (-hint) AND the error container (-errors) so screen readers
        # can navigate field → help text AND field → error message.
        # Direct assignment (not setdefault) ensures any stale value set in
        # Meta.widgets is overwritten.  AT tools gracefully ignore IDs that are
        # absent from the DOM, so referencing both is safe when only one exists.
        for visible in self.visible_fields():
            visible.field.widget.attrs["aria-describedby"] = (
                f"{visible.auto_id}-hint {visible.auto_id}-errors"
            )

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

        # E-5 fix: write the stripped value back so leading/trailing whitespace
        # is never persisted to the database.
        cleaned_data["motivation"] = motivation
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
        in the template. Server-side, ``clean()`` raises a hard field-level
        ``ValidationError`` on ``"rejection_reason"`` when ``action == "reject"``
        and ``rejection_reason`` is empty or whitespace-only (M-4 fix). The
        service layer (``reject_application()``) does not enforce this — the
        form is the gate.
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
            }
        ),
        required=False,
        help_text=_(
            "Internal record only. This text is NEVER shown to the volunteer. "
            "Notifications use generic 'not selected' language per PIPEDA policy."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # WCAG 2.1 SC 1.3.1 fix: wire aria-describedby to BOTH the hint
        # paragraph (-hint) AND the error container (-errors) so screen readers
        # can navigate field → help text AND field → error message.
        # Direct assignment (not setdefault) overwrites any stale value set on
        # widget attrs at class-definition time.  AT tools gracefully ignore IDs
        # absent from the DOM, so referencing both is safe when only one exists.
        for visible in self.visible_fields():
            visible.field.widget.attrs["aria-describedby"] = (
                f"{visible.auto_id}-hint {visible.auto_id}-errors"
            )

    def clean(self):
        """
        Hard validation: rejection decisions require an internal reason.

        Under PIPEDA's accountability principle and access-to-information
        obligations, every rejection must carry a documented internal rationale.
        A missing ``rejection_reason`` when ``action == "reject"`` is a hard
        ``ValidationError`` that prevents form submission.

        The ``rejection_reason`` field remains ``required=False`` at the field
        level so the HTML input is not unconditionally marked required — this
        cross-field rule enforces the conditional requirement server-side.

        Note: the rejection reason is an internal coordinator record and is
        NEVER surfaced to the volunteer.
        """
        cleaned_data = super().clean()
        action = cleaned_data.get("action")
        rejection_reason = cleaned_data.get("rejection_reason", "").strip()

        if action == "reject" and not rejection_reason:
            raise forms.ValidationError(
                {
                    "rejection_reason": _(
                        "A rejection reason is required for audit purposes. "
                        "This record is for internal use only and will not be "
                        "shared with the applicant."
                    )
                }
            )

        return cleaned_data


# ---------------------------------------------------------------------------
# ShiftForm
# ---------------------------------------------------------------------------

class ShiftForm(forms.ModelForm):
    """
    Create or edit a Shift under an Opportunity.

    Used by coordinator ShiftCreateView and ShiftEditView.  The opportunity
    is injected via __init__ rather than rendered as a form field.

    WCAG notes:
      - start_datetime / end_datetime use <input type="datetime-local"> with
        explicit <label> elements — not placeholder-only.
      - aria-describedby references both -hint and -errors IDs.
    """

    class Meta:
        model = Shift
        fields = [
            "title_en", "title_fr",
            "description_en", "description_fr",
            "start_datetime", "end_datetime",
            "location_override",
            "is_remote",
            "capacity",
            "waitlist_enabled",
            "waitlist_cap",
        ]
        widgets = {
            "start_datetime": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "end_datetime": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "description_en": forms.Textarea(attrs={"rows": 4}),
            "description_fr": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {
            "title_en": _("Shift title (English)"),
            "title_fr": _("Shift title (French)"),
            "description_en": _("Description (English)"),
            "description_fr": _("Description (French)"),
            "start_datetime": _("Start date and time"),
            "end_datetime": _("End date and time"),
            "location_override": _("Location (leave blank to use opportunity location)"),
            "is_remote": _("Remote shift"),
            "capacity": _("Volunteer capacity (leave blank to inherit from opportunity)"),
            "waitlist_enabled": _("Enable waitlist"),
            "waitlist_cap": _("Waitlist cap (leave blank for unlimited)"),
        }

    def __init__(self, *args, opportunity=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.opportunity = opportunity
        # WCAG: set aria-describedby on every visible field
        for visible in self.visible_fields():
            visible.field.widget.attrs["aria-describedby"] = (
                f"{visible.auto_id}-hint {visible.auto_id}-errors"
            )

    def clean(self):
        cleaned_data = super().clean()
        start = cleaned_data.get("start_datetime")
        end = cleaned_data.get("end_datetime")

        if start and end and end <= start:
            self.add_error("end_datetime", _("End date and time must be after the start."))

        # Validate waitlist_cap only makes sense when waitlist_enabled
        waitlist_enabled = cleaned_data.get("waitlist_enabled")
        waitlist_cap = cleaned_data.get("waitlist_cap")
        if waitlist_cap and not waitlist_enabled:
            self.add_error("waitlist_cap", _("Set a waitlist cap only when the waitlist is enabled."))

        return cleaned_data


# ---------------------------------------------------------------------------
# HoursLogForm
# ---------------------------------------------------------------------------

class HoursLogForm(forms.ModelForm):
    """
    Volunteer submits an hours log against an opportunity.

    The ``volunteer_profile`` and ``opportunity`` are injected via __init__
    and are NOT rendered as form fields.  ``shift`` is an optional
    ModelChoiceField filtered to the volunteer's confirmed bookings.

    PIPEDA: ``rejection_reason`` is never rendered.
    """

    class Meta:
        model = HoursLog
        fields = ["date", "hours", "description", "shift"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "date": _("Date of volunteering"),
            "hours": _("Hours volunteered"),
            "description": _("Brief description (optional)"),
            "shift": _("Associated shift (optional)"),
        }

    def __init__(self, *args, volunteer_profile=None, opportunity=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.volunteer_profile = volunteer_profile
        self.opportunity = opportunity
        # Filter shift choices to confirmed/completed bookings for this volunteer+opportunity
        if volunteer_profile is not None and opportunity is not None:
            from apps.volunteers.models import ShiftBooking
            self.fields["shift"].queryset = (
                Shift.objects.filter(
                    opportunity=opportunity,
                    bookings__volunteer=volunteer_profile,
                    bookings__status__in=[
                        ShiftBooking.STATUS_CONFIRMED,
                        ShiftBooking.STATUS_COMPLETED,
                    ],
                ).distinct()
            )
        else:
            self.fields["shift"].queryset = Shift.objects.none()
        self.fields["shift"].required = False
        # WCAG: aria-describedby wiring
        for visible in self.visible_fields():
            visible.field.widget.attrs["aria-describedby"] = (
                f"{visible.auto_id}-hint {visible.auto_id}-errors"
            )

    def clean_hours(self):
        hours = self.cleaned_data.get("hours")
        if hours is not None:
            if hours <= 0:
                raise forms.ValidationError(_("Hours must be greater than zero."))
            if hours > 24:
                raise forms.ValidationError(_("Cannot log more than 24 hours in a single entry."))
        return hours


# ---------------------------------------------------------------------------
# HoursRejectForm
# ---------------------------------------------------------------------------

class HoursRejectForm(forms.Form):
    """
    Coordinator provides a rejection reason when rejecting an hours log.

    Raises a hard field-level ValidationError on ``reason`` when reason is
    empty or whitespace-only.  The service layer (``reject_hours()``) also
    requires a non-empty reason — this form is the gate.
    """

    reason = forms.CharField(
        label=_("Reason for rejection"),
        max_length=300,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("This reason will not be shown to the volunteer. Keep it brief."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # WCAG: aria-describedby wiring
        for visible in self.visible_fields():
            visible.field.widget.attrs["aria-describedby"] = (
                f"{visible.auto_id}-hint {visible.auto_id}-errors"
            )

    def clean_reason(self):
        reason = self.cleaned_data.get("reason", "").strip()
        if not reason:
            raise forms.ValidationError(_("A rejection reason is required."))
        return reason
