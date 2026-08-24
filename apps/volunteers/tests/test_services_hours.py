"""
Wave 3 — Volunteer Management BB: hours service test suite.

Covers:
  log_hours()      — happy path, wrong actor, zero hours, duplicate shift,
                     shift mismatch, signal dispatch
  approve_hours()  — happy path, permission, non-pending guard, total update,
                     milestone creation, signal dispatch
  reject_hours()   — happy path, empty reason, non-pending guard, PIPEDA signal
                     invariant (rejection_reason never in kwargs)
  Milestones       — created at 25h threshold, idempotent get_or_create,
                     not triggered below threshold

Conventions:
  - captureOnCommitCallbacks(execute=True) to trigger on_commit paths.
  - PIPEDA invariant: rejection_reason must never appear in signal kwargs.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.volunteers.models import (
    HoursLog,
    Opportunity,
    Program,
    RecognitionMilestone,
    Shift,
    VolunteerApplication,
    VolunteerProfile,
)
from apps.volunteers.services.hours import approve_hours, log_hours, reject_hours

User = get_user_model()

# ---------------------------------------------------------------------------
# Shared factory helpers
# ---------------------------------------------------------------------------

_counter = [0]


def _uid():
    _counter[0] += 1
    return _counter[0]


def _make_user(email=None, password="testpass!", **kwargs):
    _counter[0] += 1
    email = email or f"h{_counter[0]}@example.gc.ca"
    return User.objects.create_user(email=email, password=password, **kwargs)


def _make_program(**kwargs):
    n = _uid()
    defaults = {
        "name_en": f"Program {n}",
        "name_fr": f"Programme {n}",
        "slug": f"hprog-{n}",
        "cra_category": "welfare",
    }
    defaults.update(kwargs)
    return Program.objects.create(**defaults)


def _make_opportunity(program, *, slug=None, status="published", **kwargs):
    n = _uid()
    defaults = {
        "title_en": f"Opportunity {n}",
        "title_fr": f"Opportunité {n}",
        "slug": slug or f"hopp-{n}",
        "description_en": "Description",
        "description_fr": "Description FR",
        "program": program,
        "status": status,
    }
    defaults.update(kwargs)
    return Opportunity.objects.create(**defaults)


def _make_profile(user):
    return VolunteerProfile.objects.create(user=user)


def _make_past_shift(opportunity, *, hours_ago_start=4, duration_hours=2):
    now = timezone.now()
    start = now - datetime.timedelta(hours=hours_ago_start)
    end = start + datetime.timedelta(hours=duration_hours)
    return Shift.objects.create(
        opportunity=opportunity,
        start_datetime=start,
        end_datetime=end,
        capacity=10,
        waitlist_enabled=True,
    )


def _approve_application(volunteer_profile, opportunity):
    return VolunteerApplication.objects.create(
        volunteer=volunteer_profile,
        opportunity=opportunity,
        status=VolunteerApplication.STATUS_APPROVED,
    )


def _grant_perm(user, codename):
    perm = Permission.objects.get(
        content_type__app_label="volunteers",
        codename=codename,
    )
    user.user_permissions.add(perm)
    for attr in ("_perm_cache", "_user_perm_cache"):
        if hasattr(user, attr):
            delattr(user, attr)
    return User.objects.get(pk=user.pk)


# ---------------------------------------------------------------------------
# Shared base setup
# ---------------------------------------------------------------------------


class HoursBaseTestCase(TestCase):
    """
    Shared fixtures for all hours service tests.

    Attributes:
        coordinator        — User with volunteers.change_hourslog
        volunteer_user     — regular volunteer User
        volunteer_profile  — VolunteerProfile for volunteer_user
        program, opportunity
        approved_application
        past_shift         — Shift whose start and end are in the past
    """

    def setUp(self):
        coord = _make_user("hcoord@example.gc.ca", is_staff=True)
        self.coordinator = _grant_perm(coord, "change_hourslog")

        self.volunteer_user = _make_user("hvol@example.gc.ca")
        self.volunteer_profile = _make_profile(self.volunteer_user)

        self.program = _make_program(slug="hours-base-prog")
        self.opportunity = _make_opportunity(self.program, slug="hours-base-opp")
        self.approved_application = _approve_application(self.volunteer_profile, self.opportunity)

        self.past_shift = _make_past_shift(self.opportunity, hours_ago_start=4, duration_hours=2)

    # ---- helpers ----

    def _pending_log(self, hours=Decimal("4.0"), shift=None):
        """Create a pending HoursLog for self.volunteer_profile."""
        return log_hours(
            volunteer_profile=self.volunteer_profile,
            opportunity=self.opportunity,
            hours=hours,
            date=datetime.date.today() - datetime.timedelta(days=1),
            actor=self.volunteer_user,
            shift=shift,
        )

    def _approve_n_hours(self, total_hours):
        """
        Approve enough HoursLog entries to bring the volunteer's approved
        total to `total_hours` (created in 5-hour batches, using distinct dates
        so we can reuse the same opportunity without a shift clash).
        """
        batch = Decimal("5.0")
        n = int(total_hours / batch)
        remainder = Decimal(str(total_hours)) - (batch * n)
        logs = []
        base_date = datetime.date.today() - datetime.timedelta(days=365)
        for i in range(n):
            date = base_date + datetime.timedelta(days=i)
            log = HoursLog.objects.create(
                volunteer=self.volunteer_profile,
                opportunity=self.opportunity,
                date=date,
                hours=batch,
                status=HoursLog.STATUS_PENDING,
            )
            logs.append(log)
        if remainder > 0:
            date = base_date + datetime.timedelta(days=n)
            log = HoursLog.objects.create(
                volunteer=self.volunteer_profile,
                opportunity=self.opportunity,
                date=date,
                hours=remainder,
                status=HoursLog.STATUS_PENDING,
            )
            logs.append(log)
        for log in logs:
            with self.captureOnCommitCallbacks(execute=True):
                approve_hours(hours_log=log, actor=self.coordinator)


# ===========================================================================
# log_hours() tests
# ===========================================================================


class LogHoursTests(HoursBaseTestCase):
    def test_log_hours_creates_pending_log(self):
        """Happy path: log_hours() creates a HoursLog with STATUS_PENDING."""
        log = log_hours(
            volunteer_profile=self.volunteer_profile,
            opportunity=self.opportunity,
            hours=Decimal("4.0"),
            date=datetime.date.today() - datetime.timedelta(days=1),
            actor=self.volunteer_user,
        )
        self.assertIsNotNone(log.pk)
        self.assertEqual(log.status, HoursLog.STATUS_PENDING)
        self.assertEqual(log.hours, Decimal("4.0"))
        self.assertEqual(log.volunteer, self.volunteer_profile)
        self.assertEqual(log.opportunity, self.opportunity)

    def test_log_hours_wrong_actor_raises(self):
        """log_hours() raises PermissionDenied when actor is not the volunteer's User."""
        with self.assertRaises(PermissionDenied):
            log_hours(
                volunteer_profile=self.volunteer_profile,
                opportunity=self.opportunity,
                hours=Decimal("2.0"),
                date=datetime.date.today() - datetime.timedelta(days=1),
                actor=self.coordinator,  # not the volunteer
            )

    def test_log_hours_zero_raises(self):
        """log_hours() raises ValidationError when hours <= 0 (MinValueValidator)."""
        with self.assertRaises(ValidationError):
            log_hours(
                volunteer_profile=self.volunteer_profile,
                opportunity=self.opportunity,
                hours=Decimal("0"),
                date=datetime.date.today() - datetime.timedelta(days=1),
                actor=self.volunteer_user,
            )

    def test_log_hours_above_24_raises(self):
        """log_hours() raises ValidationError when hours > 24 (MaxValueValidator)."""
        with self.assertRaises(ValidationError):
            log_hours(
                volunteer_profile=self.volunteer_profile,
                opportunity=self.opportunity,
                hours=Decimal("25.0"),
                date=datetime.date.today() - datetime.timedelta(days=1),
                actor=self.volunteer_user,
            )

    def test_log_hours_with_shift(self):
        """log_hours() associates a shift when one is provided."""
        log = log_hours(
            volunteer_profile=self.volunteer_profile,
            opportunity=self.opportunity,
            hours=Decimal("2.0"),
            date=datetime.date.today() - datetime.timedelta(days=1),
            actor=self.volunteer_user,
            shift=self.past_shift,
        )
        self.assertEqual(log.shift, self.past_shift)

    def test_log_hours_shift_wrong_opportunity_raises(self):
        """log_hours() raises ValidationError when shift belongs to a different opportunity."""
        other_opp = _make_opportunity(self.program, slug="other-opp-hours")
        other_shift = _make_past_shift(other_opp, hours_ago_start=5)
        with self.assertRaises(ValidationError) as ctx:
            log_hours(
                volunteer_profile=self.volunteer_profile,
                opportunity=self.opportunity,
                hours=Decimal("2.0"),
                date=datetime.date.today() - datetime.timedelta(days=1),
                actor=self.volunteer_user,
                shift=other_shift,
            )
        self.assertIn("shift", ctx.exception.message_dict)

    def test_log_hours_duplicate_shift_raises(self):
        """log_hours() raises ValidationError when a log already exists for this shift."""
        log_hours(
            volunteer_profile=self.volunteer_profile,
            opportunity=self.opportunity,
            hours=Decimal("2.0"),
            date=datetime.date.today() - datetime.timedelta(days=1),
            actor=self.volunteer_user,
            shift=self.past_shift,
        )
        with self.assertRaises(ValidationError) as ctx:
            log_hours(
                volunteer_profile=self.volunteer_profile,
                opportunity=self.opportunity,
                hours=Decimal("2.0"),
                date=datetime.date.today() - datetime.timedelta(days=1),
                actor=self.volunteer_user,
                shift=self.past_shift,
            )
        self.assertIn("shift", ctx.exception.message_dict)

    def test_log_hours_future_date_raises_validation_error(self):
        """log_hours() raises ValidationError when date is in the future."""
        future_date = (timezone.localtime(timezone.now()) + datetime.timedelta(days=1)).date()

        with self.assertRaises(ValidationError) as ctx:
            log_hours(
                volunteer_profile=self.volunteer_profile,
                opportunity=self.opportunity,
                hours=Decimal("2.0"),
                date=future_date,
                actor=self.volunteer_user,
            )

        self.assertIn("date", ctx.exception.message_dict)

    def test_log_hours_stores_description(self):
        """Description passed to log_hours() is persisted on the HoursLog."""
        log = log_hours(
            volunteer_profile=self.volunteer_profile,
            opportunity=self.opportunity,
            hours=Decimal("1.5"),
            date=datetime.date.today() - datetime.timedelta(days=1),
            description="Helped with setup",
            actor=self.volunteer_user,
        )
        log.refresh_from_db()
        self.assertEqual(log.description, "Helped with setup")

    def test_log_hours_fires_signal_on_commit(self):
        """log_hours() fires hours_logged signal after the DB commit."""
        from apps.volunteers.signals import hours_logged

        received = []

        def handler(sender, **kwargs):
            received.append(kwargs)

        hours_logged.connect(handler, weak=False)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                log_hours(
                    volunteer_profile=self.volunteer_profile,
                    opportunity=self.opportunity,
                    hours=Decimal("3.0"),
                    date=datetime.date.today() - datetime.timedelta(days=1),
                    actor=self.volunteer_user,
                )
        finally:
            hours_logged.disconnect(handler)

        self.assertEqual(len(received), 1)
        self.assertIn("volunteer", received[0])
        self.assertEqual(received[0]["volunteer"], self.volunteer_profile)


# ===========================================================================
# approve_hours() tests
# ===========================================================================


class ApproveHoursTests(HoursBaseTestCase):
    def test_approve_hours_sets_approved_status(self):
        """Happy path: approve_hours() transitions status to approved."""
        log = self._pending_log()
        result = approve_hours(hours_log=log, actor=self.coordinator)
        result.refresh_from_db()
        self.assertEqual(result.status, HoursLog.STATUS_APPROVED)

    def test_approve_hours_sets_approved_by(self):
        """approve_hours() records the approving coordinator."""
        log = self._pending_log()
        approve_hours(hours_log=log, actor=self.coordinator)
        log.refresh_from_db()
        self.assertEqual(log.approved_by, self.coordinator)

    def test_approve_hours_sets_approved_at(self):
        """approve_hours() records the approval timestamp."""
        log = self._pending_log()
        before = timezone.now()
        approve_hours(hours_log=log, actor=self.coordinator)
        after = timezone.now()
        log.refresh_from_db()
        self.assertIsNotNone(log.approved_at)
        self.assertGreaterEqual(log.approved_at, before)
        self.assertLessEqual(log.approved_at, after)

    def test_approve_hours_requires_perm(self):
        """approve_hours() raises PermissionDenied for unprivileged users."""
        log = self._pending_log()
        with self.assertRaises(PermissionDenied):
            approve_hours(hours_log=log, actor=self.volunteer_user)

    def test_approve_hours_non_pending_raises(self):
        """approve_hours() raises ValidationError when log is not STATUS_PENDING."""
        log = self._pending_log()
        approve_hours(hours_log=log, actor=self.coordinator)
        log.refresh_from_db()
        with self.assertRaises(ValidationError) as ctx:
            approve_hours(hours_log=log, actor=self.coordinator)
        self.assertIn("status", ctx.exception.message_dict)

    def test_approve_rejected_log_raises(self):
        """approve_hours() raises ValidationError on a rejected log."""
        log = self._pending_log()
        reject_hours(hours_log=log, actor=self.coordinator, reason="Wrong date")
        log.refresh_from_db()
        with self.assertRaises(ValidationError):
            approve_hours(hours_log=log, actor=self.coordinator)

    def test_approve_hours_updates_total_on_commit(self):
        """approve_hours() recomputes VolunteerProfile.total_hours_approved."""
        log = self._pending_log(hours=Decimal("6.0"))
        initial_total = self.volunteer_profile.total_hours_approved

        with self.captureOnCommitCallbacks(execute=True):
            approve_hours(hours_log=log, actor=self.coordinator)

        self.volunteer_profile.refresh_from_db()
        self.assertEqual(
            self.volunteer_profile.total_hours_approved,
            initial_total + Decimal("6.0"),
        )

    def test_approve_hours_fires_signal_on_commit(self):
        """approve_hours() fires hours_approved signal after the DB commit."""
        from apps.volunteers.signals import hours_approved

        log = self._pending_log()
        received = []

        def handler(sender, **kwargs):
            received.append(kwargs)

        hours_approved.connect(handler, weak=False)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                approve_hours(hours_log=log, actor=self.coordinator)
        finally:
            hours_approved.disconnect(handler)

        self.assertEqual(len(received), 1)
        self.assertIn("approved_by", received[0])
        self.assertEqual(received[0]["approved_by"], self.coordinator)

    def test_approve_hours_superuser_can_approve(self):
        """Django superusers have all permissions and can approve hours."""
        superuser = _make_user("hsuper@example.gc.ca", is_staff=True, is_superuser=True)
        log = self._pending_log()
        result = approve_hours(hours_log=log, actor=superuser)
        result.refresh_from_db()
        self.assertEqual(result.status, HoursLog.STATUS_APPROVED)

    # ------------------------------------------------------------------
    # T2 — approve_hours() on an already-approved log raises
    # ------------------------------------------------------------------

    def test_approve_hours_already_approved_raises_validation_error(self):
        """
        T2: approve_hours() on a HoursLog that is already STATUS_APPROVED
        must raise ValidationError — the non-pending guard (H2) prevents
        double-approval from silently overwriting the record.

        This test explicitly verifies the guard path, distinct from the
        existing test_approve_hours_non_pending_raises which also tests
        rejection → approve. Here the sequence is approve → approve.
        """
        log = self._pending_log()
        # First approval must succeed.
        approve_hours(hours_log=log, actor=self.coordinator)
        log.refresh_from_db()
        self.assertEqual(log.status, HoursLog.STATUS_APPROVED)

        # Second approval must raise ValidationError with 'status' in message_dict.
        with self.assertRaises(ValidationError) as ctx:
            approve_hours(hours_log=log, actor=self.coordinator)
        self.assertIn(
            "status",
            ctx.exception.message_dict,
            "approve_hours() must identify 'status' as the invalid field.",
        )


# ===========================================================================
# reject_hours() tests
# ===========================================================================


class RejectHoursTests(HoursBaseTestCase):
    def test_reject_hours_sets_rejected_status(self):
        """Happy path: reject_hours() transitions log status to rejected."""
        log = self._pending_log()
        result = reject_hours(hours_log=log, actor=self.coordinator, reason="Wrong opportunity")
        result.refresh_from_db()
        self.assertEqual(result.status, HoursLog.STATUS_REJECTED)

    def test_reject_hours_stores_reason(self):
        """rejection_reason is persisted in the DB for coordinator records."""
        log = self._pending_log()
        reason = "Hours claimed exceed shift duration"
        reject_hours(hours_log=log, actor=self.coordinator, reason=reason)
        log.refresh_from_db()
        self.assertEqual(log.rejection_reason, reason)

    def test_reject_hours_requires_reason(self):
        """reject_hours() raises ValidationError when reason is blank."""
        log = self._pending_log()
        with self.assertRaises(ValidationError) as ctx:
            reject_hours(hours_log=log, actor=self.coordinator, reason="")
        self.assertIn("reason", ctx.exception.message_dict)

    def test_reject_hours_whitespace_only_reason_raises(self):
        """reject_hours() raises ValidationError when reason is whitespace only."""
        log = self._pending_log()
        with self.assertRaises(ValidationError) as ctx:
            reject_hours(hours_log=log, actor=self.coordinator, reason="   ")
        self.assertIn("reason", ctx.exception.message_dict)

    def test_reject_hours_requires_perm(self):
        """reject_hours() raises PermissionDenied for unprivileged users."""
        log = self._pending_log()
        with self.assertRaises(PermissionDenied):
            reject_hours(hours_log=log, actor=self.volunteer_user, reason="Not allowed")

    def test_reject_hours_non_pending_raises(self):
        """reject_hours() raises ValidationError when log is not STATUS_PENDING."""
        log = self._pending_log()
        reject_hours(hours_log=log, actor=self.coordinator, reason="First rejection")
        log.refresh_from_db()
        with self.assertRaises(ValidationError) as ctx:
            reject_hours(hours_log=log, actor=self.coordinator, reason="Second attempt")
        self.assertIn("status", ctx.exception.message_dict)

    def test_reject_approved_log_raises(self):
        """reject_hours() raises ValidationError on an already-approved log."""
        log = self._pending_log()
        approve_hours(hours_log=log, actor=self.coordinator)
        log.refresh_from_db()
        with self.assertRaises(ValidationError):
            reject_hours(hours_log=log, actor=self.coordinator, reason="Changed mind")

    def test_reject_hours_reason_not_in_signal_kwargs(self):
        """
        PIPEDA invariant: rejection_reason must never appear in hours_rejected
        signal kwargs, preventing receivers from accidentally forwarding it to
        the volunteer.
        """
        from apps.volunteers.signals import hours_rejected

        log = self._pending_log()
        received_kwargs: dict = {}

        def receiver(sender, **kwargs):
            received_kwargs.update(kwargs)

        hours_rejected.connect(receiver, weak=False)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                reject_hours(
                    hours_log=log,
                    actor=self.coordinator,
                    reason="INTERNAL: date discrepancy in payroll system",
                )
        finally:
            hours_rejected.disconnect(receiver)

        self.assertNotIn("rejection_reason", received_kwargs)
        # The actual reason text must also not bleed through any key.
        all_values = str(received_kwargs)
        self.assertNotIn("INTERNAL: date discrepancy", all_values)

    def test_reject_hours_fires_signal_on_commit(self):
        """reject_hours() fires hours_rejected signal after the DB commit."""
        from apps.volunteers.signals import hours_rejected

        log = self._pending_log()
        received = []

        def handler(sender, **kwargs):
            received.append(kwargs)

        hours_rejected.connect(handler, weak=False)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                reject_hours(hours_log=log, actor=self.coordinator, reason="Insufficient evidence")
        finally:
            hours_rejected.disconnect(handler)

        self.assertEqual(len(received), 1)
        self.assertIn("rejected_by", received[0])

    def test_reject_hours_does_not_set_approved_by(self):
        """Rejected logs must not set approved_by (rejection is not an approval)."""
        log = self._pending_log()
        reject_hours(hours_log=log, actor=self.coordinator, reason="Wrong opportunity")
        log.refresh_from_db()
        self.assertIsNone(log.approved_by)


# ===========================================================================
# Milestone tests
# ===========================================================================


class MilestoneTests(HoursBaseTestCase):
    def test_milestone_created_at_25h(self):
        """Approving 25 hours of logs creates a RecognitionMilestone at the 25h threshold."""
        self._approve_n_hours(25)
        milestones = RecognitionMilestone.objects.filter(
            volunteer=self.volunteer_profile,
            hours_threshold=Decimal("25"),
        )
        self.assertEqual(milestones.count(), 1)

    def test_no_milestone_below_threshold(self):
        """Approving 24 hours should not create any milestones."""
        self._approve_n_hours(24)
        count = RecognitionMilestone.objects.filter(
            volunteer=self.volunteer_profile,
        ).count()
        self.assertEqual(count, 0)

    def test_milestone_idempotent_get_or_create(self):
        """
        Re-running _check_milestones() at the same threshold level creates
        exactly one RecognitionMilestone — not a duplicate.
        """
        from apps.volunteers.services.hours import _check_milestones

        self._approve_n_hours(25)
        # Manually invoke _check_milestones again with the same totals.
        _check_milestones(
            volunteer_profile_pk=self.volunteer_profile.pk,
            old_total=Decimal("0"),
            new_total=Decimal("25"),
        )

        count = RecognitionMilestone.objects.filter(
            volunteer=self.volunteer_profile,
            hours_threshold=Decimal("25"),
        ).count()
        self.assertEqual(count, 1, "Milestone must be idempotent — only one row allowed.")

    def test_milestone_created_at_50h(self):
        """Approving 50 hours creates milestones at both 25h and 50h thresholds."""
        self._approve_n_hours(50)
        milestones = RecognitionMilestone.objects.filter(
            volunteer=self.volunteer_profile,
        )
        thresholds = set(milestones.values_list("hours_threshold", flat=True))
        self.assertIn(Decimal("25"), thresholds)
        self.assertIn(Decimal("50"), thresholds)

    def test_milestone_achieved_signal_fired(self):
        """_check_milestones() fires the milestone_achieved signal for each new threshold."""
        from apps.volunteers.signals import milestone_achieved

        received = []

        def handler(sender, **kwargs):
            received.append(kwargs)

        milestone_achieved.connect(handler, weak=False)
        try:
            self._approve_n_hours(25)
        finally:
            milestone_achieved.disconnect(handler)

        self.assertEqual(len(received), 1)
        thresholds = [r["hours_threshold"] for r in received]
        self.assertIn(Decimal("25"), thresholds)

    def test_milestone_not_created_for_existing_threshold(self):
        """
        If a milestone at 25h already exists before approval, it must not be
        duplicated (get_or_create idempotency).
        """
        # Pre-create the milestone.
        RecognitionMilestone.objects.create(
            volunteer=self.volunteer_profile,
            hours_threshold=Decimal("25"),
            notification_sent=False,
        )
        # Now approve to 25h — should not raise or create a second row.
        self._approve_n_hours(25)
        count = RecognitionMilestone.objects.filter(
            volunteer=self.volunteer_profile,
            hours_threshold=Decimal("25"),
        ).count()
        self.assertEqual(count, 1)

    def test_milestone_notification_sent_flag_set_to_true(self):
        """RecognitionMilestone.notification_sent is True after milestone receiver fires."""
        from unittest.mock import patch

        with patch("apps.notifications.services.send_email_notification") as mock_send:
            mock_send.return_value = None
            # _approve_n_hours already wraps each approval in captureOnCommitCallbacks
            # so the on_commit signal path (and the receiver) fires synchronously.
            self._approve_n_hours(25)

        milestone = RecognitionMilestone.objects.get(
            volunteer=self.volunteer_profile,
            hours_threshold=Decimal("25"),
        )
        self.assertTrue(milestone.notification_sent)
        # Approving 5 hours × 5 times fires notify_volunteer_on_hours_approved  # noqa: RUF003
        # each time, plus one milestone notification — check the milestone call
        # landed rather than asserting a fixed total call count.
        milestone_calls = [
            c
            for c in mock_send.call_args_list
            if c.kwargs.get("subject_key") == "volunteer_milestone_achieved"
        ]
        self.assertEqual(len(milestone_calls), 1, "Expected exactly one milestone email")


# ===========================================================================
# Hours race-condition tests  (requires TransactionTestCase / PostgreSQL)
# ===========================================================================

import unittest as _unittest  # noqa: E402

from django.db import connection as _connection  # noqa: E402


@_unittest.skipIf(
    _connection.vendor == "sqlite",
    "select_for_update() requires PostgreSQL row-level locking",
)
class HoursRaceConditionTests(TransactionTestCase):
    """
    Verify that approve_hours() raises on an already-approved HoursLog.
    Sequential (not threaded) — tests the state-machine guard, not true DB locking.
    """

    def setUp(self):
        # Build the minimum fixture: user, volunteer profile, coordinator,
        # program, opportunity, approved application, submitted HoursLog.
        # Mirror the factory pattern used in the rest of this test module.
        coord = _make_user("race_hcoord@example.gc.ca", is_staff=True)
        self.coordinator = _grant_perm(coord, "change_hourslog")

        self.volunteer_user = _make_user("race_hvol@example.gc.ca")
        self.volunteer_profile = _make_profile(self.volunteer_user)

        self.program = _make_program(slug="race-hours-prog")
        self.opportunity = _make_opportunity(self.program, slug="race-hours-opp")
        _approve_application(self.volunteer_profile, self.opportunity)

        # Create a pending HoursLog directly (bypass log_hours() to avoid the
        # actor permission check — we only need the record to exist in STATUS_PENDING).
        self.hours_log = HoursLog.objects.create(
            volunteer=self.volunteer_profile,
            opportunity=self.opportunity,
            date=datetime.date.today() - datetime.timedelta(days=1),
            hours=Decimal("3.0"),
            status=HoursLog.STATUS_PENDING,
        )

    def test_approve_hours_raises_if_already_approved(self):
        from apps.volunteers.services.hours import approve_hours

        # Approve once — must succeed.
        result = approve_hours(hours_log=self.hours_log, actor=self.coordinator)
        result.refresh_from_db()
        self.assertEqual(result.status, HoursLog.STATUS_APPROVED)

        # Second approval must raise (state-machine guard).
        with self.assertRaises(Exception):  # noqa: B017
            approve_hours(hours_log=result, actor=self.coordinator)
