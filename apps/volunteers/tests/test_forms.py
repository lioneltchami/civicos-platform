"""
Volunteer Management BB — Form unit tests.

Tests ApplicationForm and ApplicationReviewForm in isolation (no HTTP, no views —
direct form instantiation only).

Covers:
  ApplicationForm:
    - motivation required when opportunity is set (E-5 fix)
    - whitespace-only motivation rejected
    - motivation stripped of whitespace before storage (E-5 fix)
    - valid motivation passes
    - motivation not required when no opportunity is provided

  ApplicationReviewForm:
    - M-4 fix: reject without reason raises ValidationError
    - M-4 fix: whitespace-only reason also fails
    - reject with reason is valid
    - approve without reason is valid
    - invalid action fails validation
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.volunteers.forms import ApplicationForm, ApplicationReviewForm
from apps.volunteers.models import Opportunity, Program, VolunteerProfile

User = get_user_model()

_counter = [0]


def _make_user(email=None, **kwargs):
    _counter[0] += 1
    email = email or f"formtest{_counter[0]}@example.gc.ca"
    return User.objects.create_user(email=email, password="testpass!", **kwargs)


def _make_program(slug=None):
    _counter[0] += 1
    return Program.objects.create(
        name_en="Test Program",
        name_fr="Programme test",
        slug=slug or f"prog-{_counter[0]}",
        cra_category="welfare",
    )


def _make_opportunity(program, *, slug=None, status="published", **kwargs):
    _counter[0] += 1
    defaults = {
        "title_en": "Community Event Helper",
        "title_fr": "Aide aux événements communautaires",
        "slug": slug or f"opp-{_counter[0]}",
        "description_en": "Help with community events.",
        "description_fr": "Aidez lors des événements communautaires.",
        "program": program,
        "status": status,
    }
    defaults.update(kwargs)
    return Opportunity.objects.create(**defaults)


def _make_profile(user):
    return VolunteerProfile.objects.create(user=user)


# ===========================================================================
# ApplicationForm tests
# ===========================================================================


class ApplicationFormTests(TestCase):
    """Unit tests for ApplicationForm — direct instantiation, no HTTP."""

    def setUp(self):
        self.user = _make_user("alice@forms.gc.ca")
        self.profile = _make_profile(self.user)
        self.program = _make_program(slug="forms-prog")
        self.opportunity = _make_opportunity(self.program, slug="forms-opp")

    # -----------------------------------------------------------------------
    # Motivation required when opportunity is provided
    # -----------------------------------------------------------------------

    def test_motivation_required_when_opportunity_set(self):
        """Empty motivation fails validation when opportunity is provided."""
        form = ApplicationForm(
            data={"motivation": ""},
            opportunity=self.opportunity,
            volunteer_profile=self.profile,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("motivation", form.errors)

    def test_whitespace_only_motivation_rejected(self):
        """Motivation consisting only of spaces/tabs/newlines fails validation."""
        form = ApplicationForm(
            data={"motivation": "   \t\n   "},
            opportunity=self.opportunity,
            volunteer_profile=self.profile,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("motivation", form.errors)

    def test_stripped_motivation_written_back_to_cleaned_data(self):
        """E-5 fix: form.cleaned_data['motivation'] is stripped of leading/trailing whitespace."""
        form = ApplicationForm(
            data={"motivation": "  I love volunteering  "},
            opportunity=self.opportunity,
            volunteer_profile=self.profile,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["motivation"], "I love volunteering")

    def test_leading_newlines_stripped(self):
        """Motivation with leading/trailing newlines is stripped before returning cleaned_data."""
        form = ApplicationForm(
            data={"motivation": "\n\nI want to help the community.\n"},
            opportunity=self.opportunity,
            volunteer_profile=self.profile,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["motivation"], "I want to help the community.")

    def test_valid_motivation_passes(self):
        """A normal, non-empty motivation passes validation without errors."""
        form = ApplicationForm(
            data={"motivation": "I am passionate about helping elderly citizens."},
            opportunity=self.opportunity,
            volunteer_profile=self.profile,
        )
        self.assertTrue(form.is_valid(), form.errors)

    # -----------------------------------------------------------------------
    # Motivation not required without opportunity
    # -----------------------------------------------------------------------

    def test_motivation_not_required_without_opportunity(self):
        """When no opportunity is passed, blank motivation does not trigger a validation error."""
        form = ApplicationForm(
            data={"motivation": ""},
            opportunity=None,
            volunteer_profile=self.profile,
        )
        # Without an opportunity the cross-field rule does not fire.
        self.assertTrue(form.is_valid(), form.errors)

    def test_motivation_not_required_when_opportunity_omitted_entirely(self):
        """Omitting the opportunity kwarg entirely leaves motivation optional."""
        form = ApplicationForm(data={"motivation": ""})
        self.assertTrue(form.is_valid(), form.errors)

    # -----------------------------------------------------------------------
    # Dynamic profile fields (sanity check — field injection does not break base)
    # -----------------------------------------------------------------------

    def test_form_fields_include_motivation_by_default(self):
        """ApplicationForm always exposes the motivation field."""
        form = ApplicationForm(opportunity=self.opportunity, volunteer_profile=self.profile)
        self.assertIn("motivation", form.fields)

    def test_required_profile_fields_injected(self):
        """When opportunity.required_profile_fields lists a registry key, it is injected."""
        self.opportunity.required_profile_fields = ["phone_number"]
        self.opportunity.save(update_fields=["required_profile_fields"])

        form = ApplicationForm(
            opportunity=self.opportunity,
            volunteer_profile=self.profile,
        )
        self.assertIn("phone_number", form.fields)

    def test_unknown_profile_field_skipped_silently(self):
        """An unrecognised field name in required_profile_fields is silently ignored."""
        self.opportunity.required_profile_fields = ["unknown_field_xyz"]
        self.opportunity.save(update_fields=["required_profile_fields"])

        form = ApplicationForm(
            opportunity=self.opportunity,
            volunteer_profile=self.profile,
        )
        self.assertNotIn("unknown_field_xyz", form.fields)

    # -----------------------------------------------------------------------
    # Security: excluded coordinator-only fields
    # -----------------------------------------------------------------------

    def test_rejection_reason_not_in_form_fields(self):
        """PIPEDA: rejection_reason must never appear in ApplicationForm fields."""
        form = ApplicationForm(opportunity=self.opportunity, volunteer_profile=self.profile)
        self.assertNotIn("rejection_reason", form.fields)

    def test_screening_notes_not_in_form_fields(self):
        """PIPEDA: screening_notes must never appear in ApplicationForm fields."""
        form = ApplicationForm(opportunity=self.opportunity, volunteer_profile=self.profile)
        self.assertNotIn("screening_notes", form.fields)

    def test_status_not_in_form_fields(self):
        """status must never appear in ApplicationForm — it is set programmatically."""
        form = ApplicationForm(opportunity=self.opportunity, volunteer_profile=self.profile)
        self.assertNotIn("status", form.fields)


# ===========================================================================
# ApplicationReviewForm tests
# ===========================================================================


class ApplicationReviewFormTests(TestCase):
    """Unit tests for ApplicationReviewForm — especially M-4 enforcement."""

    # -----------------------------------------------------------------------
    # Reject without reason must fail (M-4 fix)
    # -----------------------------------------------------------------------

    def test_reject_without_reason_raises_validation_error(self):
        """M-4 fix: action=reject with empty rejection_reason must fail validation."""
        form = ApplicationReviewForm(data={"action": "reject", "rejection_reason": ""})
        self.assertFalse(form.is_valid())
        self.assertIn("rejection_reason", form.errors)

    def test_reject_with_whitespace_only_reason_raises_validation_error(self):
        """Whitespace-only rejection_reason must also fail when action=reject."""
        form = ApplicationReviewForm(data={"action": "reject", "rejection_reason": "   "})
        self.assertFalse(form.is_valid())
        self.assertIn("rejection_reason", form.errors)

    def test_reject_with_tabs_and_newlines_only_raises_validation_error(self):
        """Tab and newline-only rejection_reason is also rejected for action=reject."""
        form = ApplicationReviewForm(data={"action": "reject", "rejection_reason": "\t\n\t"})
        self.assertFalse(form.is_valid())
        self.assertIn("rejection_reason", form.errors)

    # -----------------------------------------------------------------------
    # Reject with a valid reason must pass
    # -----------------------------------------------------------------------

    def test_reject_with_reason_is_valid(self):
        """action=reject with a non-empty rejection_reason passes validation."""
        form = ApplicationReviewForm(
            data={
                "action": "reject",
                "rejection_reason": "Does not meet the experience requirements.",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_reject_with_minimal_reason_is_valid(self):
        """A single-character rejection_reason is sufficient for action=reject."""
        form = ApplicationReviewForm(data={"action": "reject", "rejection_reason": "N"})
        self.assertTrue(form.is_valid(), form.errors)

    # -----------------------------------------------------------------------
    # Approve does not require rejection_reason
    # -----------------------------------------------------------------------

    def test_approve_without_reason_is_valid(self):
        """action=approve does NOT require rejection_reason — it must pass with an empty string."""
        form = ApplicationReviewForm(data={"action": "approve", "rejection_reason": ""})
        self.assertTrue(form.is_valid(), form.errors)

    def test_approve_with_reason_is_still_valid(self):
        """action=approve with a rejection_reason present is also valid (coordinator may add notes)."""  # noqa: E501
        form = ApplicationReviewForm(data={"action": "approve", "rejection_reason": "Extra notes"})
        self.assertTrue(form.is_valid(), form.errors)

    # -----------------------------------------------------------------------
    # Invalid action choices
    # -----------------------------------------------------------------------

    def test_invalid_action_raises_validation_error(self):
        """An action not in ('approve', 'reject') must fail validation."""
        form = ApplicationReviewForm(data={"action": "delete", "rejection_reason": ""})
        self.assertFalse(form.is_valid())

    def test_empty_action_raises_validation_error(self):
        """A completely empty action field must fail validation (choice required)."""
        form = ApplicationReviewForm(data={"action": "", "rejection_reason": ""})
        self.assertFalse(form.is_valid())
        self.assertIn("action", form.errors)

    def test_missing_action_raises_validation_error(self):
        """Omitting the action field entirely must fail validation."""
        form = ApplicationReviewForm(data={"rejection_reason": "Some reason"})
        self.assertFalse(form.is_valid())
        self.assertIn("action", form.errors)

    # -----------------------------------------------------------------------
    # Field presence
    # -----------------------------------------------------------------------

    def test_form_has_action_field(self):
        """ApplicationReviewForm must expose an action field."""
        form = ApplicationReviewForm()
        self.assertIn("action", form.fields)

    def test_form_has_rejection_reason_field(self):
        """ApplicationReviewForm must expose a rejection_reason field."""
        form = ApplicationReviewForm()
        self.assertIn("rejection_reason", form.fields)

    def test_rejection_reason_not_required_at_field_level(self):
        """
        rejection_reason.required is False at the field level — the conditional
        requirement is enforced via clean() cross-field validation only.
        This allows approve submissions without rejection_reason.
        """
        form = ApplicationReviewForm()
        self.assertFalse(form.fields["rejection_reason"].required)
