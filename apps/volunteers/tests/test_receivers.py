"""
Volunteer Management BB — Signal receiver tests.

Tests the notification receivers wired in apps/volunteers/receivers.py.

Key invariants:
  PIPEDA compliance:
    - rejection_reason must NEVER appear in any email notification context
      sent to volunteers, not as a key and not as a value.
    - screening_notes must NEVER appear in any email notification context.
  E-8 fix:
    - review_url and portal_url in notification contexts must be absolute URLs
      (start with 'http'), not relative paths.

Strategy:
  - Mock ``apps.volunteers.receivers.send_email_notification`` at its exact
    import path in receivers.py so the notification service is never called.
  - Fire signals directly using Signal.send() so receivers run synchronously.
  - Inspect the captured call_args to verify context dict contents.

Receivers under test:
  notify_coordinator_on_application_submitted  (application_submitted signal)
  notify_volunteer_on_application_approved     (application_approved signal)
  notify_volunteer_on_application_rejected     (application_rejected signal)
"""
from __future__ import annotations

from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings

from apps.volunteers.models import Opportunity, Program, VolunteerApplication, VolunteerProfile
from apps.volunteers.signals import (
    application_approved,
    application_rejected,
    application_submitted,
)

User = get_user_model()

_counter = [0]

# Path at which send_email_notification is imported inside receivers.py.
# Must match the exact import: ``from apps.notifications.services import send_email_notification``
_SEND_NOTIFICATION_PATH = "apps.volunteers.receivers.send_email_notification"


# ---------------------------------------------------------------------------
# Shared factories
# ---------------------------------------------------------------------------

def _make_user(email=None, **kwargs):
    _counter[0] += 1
    email = email or f"recv{_counter[0]}@example.gc.ca"
    return User.objects.create_user(email=email, password="testpass!", **kwargs)


def _make_program(slug=None, coordinator=None):
    _counter[0] += 1
    program = Program.objects.create(
        name_en="Receiver Test Program",
        name_fr="Programme test récepteur",
        slug=slug or f"recv-prog-{_counter[0]}",
        cra_category="welfare",
    )
    if coordinator is not None:
        program.coordinator = coordinator
        program.save(update_fields=["coordinator"])
    return program


def _make_opportunity(program, *, slug=None, status="published", **kwargs):
    _counter[0] += 1
    defaults = dict(
        title_en="Receiver Opportunity",
        title_fr="Opportunité récepteur",
        slug=slug or f"recv-opp-{_counter[0]}",
        description_en="Test opportunity for receiver tests.",
        description_fr="Opportunité test pour tests récepteurs.",
        program=program,
        status=status,
    )
    defaults.update(kwargs)
    return Opportunity.objects.create(**defaults)


def _make_profile(user):
    return VolunteerProfile.objects.create(user=user)


def _grant_coordinator_permission(user):
    perm = Permission.objects.get(
        content_type__app_label="volunteers",
        codename="change_volunteerapplication",
    )
    user.user_permissions.add(perm)
    if hasattr(user, "_perm_cache"):
        del user._perm_cache
    if hasattr(user, "_user_perm_cache"):
        del user._user_perm_cache
    return User.objects.get(pk=user.pk)


def _extract_context_from_mock(mock_send):
    """
    Extract the ``context`` dict from a mocked send_email_notification call.

    send_email_notification is called as:
        send_email_notification(recipient=..., subject_key=..., context={...})
    All arguments are keyword arguments.
    """
    assert mock_send.called, "send_email_notification was not called."
    call_kwargs = mock_send.call_args[1]  # keyword arguments
    assert "context" in call_kwargs, (
        f"'context' not found in call kwargs. Got: {list(call_kwargs.keys())}"
    )
    return call_kwargs["context"]


# ---------------------------------------------------------------------------
# Base test case
# ---------------------------------------------------------------------------

class BaseReceiverTestCase(TestCase):
    """
    Shared fixture for receiver tests.

    Sets SITE_URL to a known value so absolute URL assertions are reliable.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    def setUp(self):
        self.coordinator = _make_user("coord@receivers.gc.ca", is_staff=True)
        _grant_coordinator_permission(self.coordinator)

        self.program = _make_program(slug="recv-base-prog", coordinator=self.coordinator)
        self.opportunity = _make_opportunity(self.program, slug="recv-base-opp")

        self.volunteer_user = _make_user("vol@receivers.gc.ca")
        self.profile = _make_profile(self.volunteer_user)

        self.application = VolunteerApplication.objects.create(
            volunteer=self.profile,
            opportunity=self.opportunity,
            status=VolunteerApplication.STATUS_PENDING,
            rejection_reason="INTERNAL: Do not expose — coordinator eyes only.",
            screening_notes="SCREENING: Do not expose.",
        )


# ===========================================================================
# ApplicationSubmittedReceiverTests
# ===========================================================================

@override_settings(SITE_URL="https://civicos.example.gc.ca")
class ApplicationSubmittedReceiverTests(BaseReceiverTestCase):
    """Tests for notify_coordinator_on_application_submitted receiver."""

    def _fire(self):
        """Fire application_submitted and return the mock."""
        with mock.patch(_SEND_NOTIFICATION_PATH) as mock_send:
            application_submitted.send(
                sender=VolunteerApplication,
                instance=self.application,
                actor=self.volunteer_user,
                opportunity=self.opportunity,
                volunteer=self.profile,
            )
        return mock_send

    def test_coordinator_notified_on_submission(self):
        """Coordinator receives a notification when an application is submitted."""
        mock_send = self._fire()
        mock_send.assert_called_once()

    def test_coordinator_is_the_recipient(self):
        """The notification is addressed to the program coordinator, not the volunteer."""
        mock_send = self._fire()
        call_kwargs = mock_send.call_args[1]
        self.assertEqual(call_kwargs["recipient"], self.coordinator)

    def test_coordinator_email_context_excludes_rejection_reason(self):
        """PIPEDA: rejection_reason must NEVER appear as a key in coordinator notification context."""
        mock_send = self._fire()
        context = _extract_context_from_mock(mock_send)
        self.assertNotIn(
            "rejection_reason",
            context,
            "PIPEDA violation: rejection_reason found as a key in coordinator submission context.",
        )

    def test_coordinator_email_context_excludes_rejection_reason_as_value(self):
        """PIPEDA: the actual rejection_reason text must not leak as any context value."""
        mock_send = self._fire()
        context = _extract_context_from_mock(mock_send)
        for key, value in context.items():
            if isinstance(value, str):
                self.assertNotIn(
                    self.application.rejection_reason,
                    value,
                    f"PIPEDA violation: rejection_reason found in context['{key}'].",
                )

    def test_coordinator_email_context_excludes_screening_notes(self):
        """PIPEDA: screening_notes must NEVER appear in coordinator notification context."""
        mock_send = self._fire()
        context = _extract_context_from_mock(mock_send)
        self.assertNotIn("screening_notes", context)

    def test_coordinator_email_contains_absolute_review_url(self):
        """E-8 fix: review_url in context must be an absolute URL (starts with http/https)."""
        mock_send = self._fire()
        context = _extract_context_from_mock(mock_send)
        self.assertIn(
            "review_url",
            context,
            "review_url must be present in coordinator submission context.",
        )
        review_url = context["review_url"]
        # Allow empty string only if SITE_URL was not set — but with override_settings
        # it is set, so the URL must be absolute.
        if review_url:
            self.assertTrue(
                review_url.startswith("http"),
                f"E-8 fix: review_url must be absolute, got: {review_url!r}",
            )

    def test_coordinator_email_context_contains_opportunity_title(self):
        """The coordinator context includes the opportunity title for identification."""
        mock_send = self._fire()
        context = _extract_context_from_mock(mock_send)
        self.assertIn("opportunity_title", context)

    def test_coordinator_email_context_contains_application_pk(self):
        """The coordinator context includes the application PK for reference."""
        mock_send = self._fire()
        context = _extract_context_from_mock(mock_send)
        self.assertIn("application_pk", context)
        self.assertEqual(context["application_pk"], self.application.pk)

    def test_no_notification_when_coordinator_not_set(self):
        """
        When program has no coordinator (coordinator=None), no notification is sent.
        This prevents a crash on the .email attribute lookup.
        """
        self.program.coordinator = None
        self.program.save(update_fields=["coordinator"])

        with mock.patch(_SEND_NOTIFICATION_PATH) as mock_send:
            application_submitted.send(
                sender=VolunteerApplication,
                instance=self.application,
                actor=self.volunteer_user,
                opportunity=self.opportunity,
                volunteer=self.profile,
            )
        mock_send.assert_not_called()


# ===========================================================================
# ApplicationApprovedReceiverTests
# ===========================================================================

@override_settings(SITE_URL="https://civicos.example.gc.ca")
class ApplicationApprovedReceiverTests(BaseReceiverTestCase):
    """Tests for notify_volunteer_on_application_approved receiver."""

    def setUp(self):
        super().setUp()
        self.application.status = VolunteerApplication.STATUS_APPROVED
        self.application.reviewed_by = self.coordinator
        self.application.save(update_fields=["status", "reviewed_by"])

    def _fire(self):
        with mock.patch(_SEND_NOTIFICATION_PATH) as mock_send:
            application_approved.send(
                sender=VolunteerApplication,
                instance=self.application,
                actor=self.coordinator,
                reviewed_by=self.coordinator,
            )
        return mock_send

    def test_volunteer_notified_on_approval(self):
        """Volunteer receives a notification when their application is approved."""
        mock_send = self._fire()
        mock_send.assert_called_once()

    def test_volunteer_is_the_recipient(self):
        """The approval notification is sent to the volunteer user, not the coordinator."""
        mock_send = self._fire()
        call_kwargs = mock_send.call_args[1]
        self.assertEqual(call_kwargs["recipient"], self.volunteer_user)

    def test_approved_email_context_excludes_rejection_reason(self):
        """PIPEDA: rejection_reason must never appear as a key in approval email context."""
        mock_send = self._fire()
        context = _extract_context_from_mock(mock_send)
        self.assertNotIn(
            "rejection_reason",
            context,
            "PIPEDA violation: rejection_reason found in approval notification context.",
        )

    def test_approved_email_context_excludes_rejection_reason_as_value(self):
        """PIPEDA: the actual rejection_reason text must not leak as any context value."""
        mock_send = self._fire()
        context = _extract_context_from_mock(mock_send)
        for key, value in context.items():
            if isinstance(value, str):
                self.assertNotIn(
                    self.application.rejection_reason,
                    value,
                    f"PIPEDA violation: rejection_reason value found in context['{key}'].",
                )

    def test_approved_email_context_excludes_screening_notes(self):
        """PIPEDA: screening_notes must never appear in approval email context."""
        mock_send = self._fire()
        context = _extract_context_from_mock(mock_send)
        self.assertNotIn(
            "screening_notes",
            context,
            "PIPEDA violation: screening_notes found in approval notification context.",
        )

    def test_approved_email_context_excludes_screening_notes_as_value(self):
        """PIPEDA: the actual screening_notes text must not leak as any context value."""
        mock_send = self._fire()
        context = _extract_context_from_mock(mock_send)
        for key, value in context.items():
            if isinstance(value, str):
                self.assertNotIn(
                    self.application.screening_notes,
                    value,
                    f"PIPEDA violation: screening_notes value found in context['{key}'].",
                )

    def test_approved_email_portal_url_is_absolute(self):
        """E-8 fix: portal_url in approval context must be an absolute URL (starts with http)."""
        mock_send = self._fire()
        context = _extract_context_from_mock(mock_send)
        self.assertIn("portal_url", context)
        portal_url = context["portal_url"]
        if portal_url:
            self.assertTrue(
                portal_url.startswith("http"),
                f"E-8 fix: portal_url must be absolute, got: {portal_url!r}",
            )

    def test_approved_email_context_contains_opportunity_title(self):
        """The approval context includes the opportunity title."""
        mock_send = self._fire()
        context = _extract_context_from_mock(mock_send)
        self.assertIn("opportunity_title", context)


# ===========================================================================
# ApplicationRejectedReceiverTests
# ===========================================================================

@override_settings(SITE_URL="https://civicos.example.gc.ca")
class ApplicationRejectedReceiverTests(BaseReceiverTestCase):
    """
    PIPEDA CRITICAL: rejection notification must never expose rejection_reason
    or screening_notes to the volunteer.

    The receiver deliberately omits rejection_reason from the notification
    context (generic 'not selected' language per PIPEDA policy).
    """

    def setUp(self):
        super().setUp()
        self.application.status = VolunteerApplication.STATUS_REJECTED
        self.application.reviewed_by = self.coordinator
        # Ensure the rejection_reason is non-empty and specific so the test is meaningful.
        self.application.rejection_reason = (
            "INTERNAL: Background screening outcome CF-2025-099. Do not disclose."
        )
        self.application.screening_notes = (
            "SCREENING: Reference check returned concerns. Coordinator file #SC-2025-001."
        )
        self.application.save(
            update_fields=["status", "reviewed_by", "rejection_reason", "screening_notes"]
        )

    def _fire(self):
        with mock.patch(_SEND_NOTIFICATION_PATH) as mock_send:
            application_rejected.send(
                sender=VolunteerApplication,
                instance=self.application,
                actor=self.coordinator,
                reviewed_by=self.coordinator,
            )
        return mock_send

    def test_volunteer_notified_on_rejection(self):
        """Volunteer receives a notification when their application is not successful."""
        mock_send = self._fire()
        mock_send.assert_called_once()

    def test_volunteer_is_the_recipient(self):
        """The rejection notification is sent to the volunteer user, not the coordinator."""
        mock_send = self._fire()
        call_kwargs = mock_send.call_args[1]
        self.assertEqual(call_kwargs["recipient"], self.volunteer_user)

    def test_rejected_email_context_excludes_rejection_reason_key(self):
        """
        PIPEDA CRITICAL: rejection_reason must NEVER appear as a key in the
        volunteer-facing rejection email context.

        The rejection_reason is for coordinator internal records only.
        Volunteers receive only generic 'not selected' language per PIPEDA policy.
        """
        mock_send = self._fire()
        context = _extract_context_from_mock(mock_send)
        self.assertNotIn(
            "rejection_reason",
            context,
            "PIPEDA CRITICAL violation: rejection_reason found as a key "
            "in volunteer-facing rejection notification context.",
        )

    def test_rejected_email_context_excludes_rejection_reason_as_value(self):
        """
        PIPEDA CRITICAL: the actual rejection_reason text must not leak
        as any context value — even under a different key name.
        """
        # Ensure rejection_reason is actually set (sanity guard).
        self.assertTrue(
            self.application.rejection_reason,
            "Test setup error: rejection_reason must be non-empty for this test to be meaningful.",
        )

        mock_send = self._fire()
        context = _extract_context_from_mock(mock_send)

        for key, value in context.items():
            if isinstance(value, str):
                self.assertNotEqual(
                    value,
                    self.application.rejection_reason,
                    f"PIPEDA CRITICAL: rejection_reason value found verbatim in context['{key}'].",
                )
                self.assertNotIn(
                    self.application.rejection_reason,
                    value,
                    f"PIPEDA CRITICAL: rejection_reason substring found in context['{key}'].",
                )

    def test_rejected_email_context_excludes_internal_reference(self):
        """
        PIPEDA CRITICAL: internal coordinator reference (e.g. CF-2025-099) must
        not appear in any context value sent to the volunteer.
        """
        mock_send = self._fire()
        context = _extract_context_from_mock(mock_send)

        for key, value in context.items():
            if isinstance(value, str):
                self.assertNotIn(
                    "CF-2025-099",
                    value,
                    f"PIPEDA CRITICAL: internal coordinator reference leaked to volunteer "
                    f"via context['{key}'].",
                )

    def test_rejected_email_context_excludes_screening_notes_key(self):
        """PIPEDA: screening_notes must NEVER appear as a key in the rejection email context."""
        mock_send = self._fire()
        context = _extract_context_from_mock(mock_send)
        self.assertNotIn(
            "screening_notes",
            context,
            "PIPEDA violation: screening_notes found as a key in rejection notification context.",
        )

    def test_rejected_email_context_excludes_screening_notes_as_value(self):
        """
        PIPEDA: the actual screening_notes text must not leak as any context value.
        """
        self.assertTrue(
            self.application.screening_notes,
            "Test setup error: screening_notes must be non-empty for this test to be meaningful.",
        )

        mock_send = self._fire()
        context = _extract_context_from_mock(mock_send)

        for key, value in context.items():
            if isinstance(value, str):
                self.assertNotIn(
                    self.application.screening_notes,
                    value,
                    f"PIPEDA violation: screening_notes value found in context['{key}'].",
                )

    def test_rejected_email_context_contains_opportunity_title(self):
        """The rejection context includes the opportunity title (generic context for the volunteer)."""
        mock_send = self._fire()
        context = _extract_context_from_mock(mock_send)
        self.assertIn("opportunity_title", context)

    def test_rejected_email_context_does_not_contain_coordinator_name(self):
        """
        PIPEDA: the coordinator's identity must not appear in volunteer-facing
        rejection notification context.
        """
        mock_send = self._fire()
        context = _extract_context_from_mock(mock_send)
        self.assertNotIn("reviewed_by", context)
        self.assertNotIn("coordinator", context)

    def test_receiver_does_not_raise_on_exception(self):
        """
        Defensive depth-in-depth: even if send_email_notification raises, the
        receiver must swallow the exception and log it (never propagate to the signal sender).
        """
        with mock.patch(
            _SEND_NOTIFICATION_PATH,
            side_effect=Exception("Notifications BB unavailable"),
        ):
            # Must not raise — receivers wrap their body in try/except.
            try:
                application_rejected.send(
                    sender=VolunteerApplication,
                    instance=self.application,
                    actor=self.coordinator,
                    reviewed_by=self.coordinator,
                )
            except Exception as exc:
                self.fail(
                    f"Receiver must not propagate exceptions to the caller, but raised: {exc}"
                )
