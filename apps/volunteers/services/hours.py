"""
Volunteer Management BB — Hours service functions.

Business rules for the full hours-logging lifecycle:
  log_hours()      — volunteer logs hours against an opportunity / shift
  approve_hours()  — coordinator approves a pending HoursLog
  reject_hours()   — coordinator rejects a pending HoursLog

Private helpers:
  _recompute_total_hours()  — recalculate VolunteerProfile.total_hours_approved
  _check_milestones()       — create RecognitionMilestone records for crossed thresholds

PIPEDA invariants enforced here:
  - No PII (volunteer name, email) in log messages — PKs only.
  - rejection_reason is coordinator-internal; never passed in signal kwargs.
  - All signal dispatches use send_robust() so a failing receiver never rolls
    back the ATOMIC_REQUESTS transaction.
  - Celery-bound side-effects are scheduled inside transaction.on_commit() so
    they only fire after the DB write commits.

Permission model:
  - log_hours()    — actor must be the volunteer's own User.
  - approve_hours() / reject_hours() — actor must hold the Django permission
    "volunteers.change_hourslog".
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

MILESTONE_THRESHOLDS = [
    Decimal("25"), Decimal("50"), Decimal("100"),
    Decimal("250"), Decimal("500"), Decimal("1000"),
]


# ---------------------------------------------------------------------------
# log_hours()
# ---------------------------------------------------------------------------

def log_hours(
    *,
    volunteer_profile,
    opportunity,
    hours,
    date,
    description: str = "",
    shift=None,
    actor,
):
    """
    Volunteer logs hours against an opportunity (and optionally a specific shift).

    Pre-checks (outside atomic):
    - actor must be the volunteer's own User.
    - If shift is provided, it must belong to this opportunity.
    - If shift is provided, no prior HoursLog for this (volunteer, shift) pair
      may exist (friendly pre-check; the DB UniqueConstraint is the final guard).

    Side-effects (inside transaction.on_commit()):
    - Fires hours_logged signal via send_robust().

    Args:
        volunteer_profile: VolunteerProfile instance for the logging volunteer.
        opportunity:       Opportunity instance hours are logged against.
        hours:             Decimal hours (0.01 – 24.00).
        date:              datetime.date of the volunteering.
        description:       Optional free-text description (max 500 chars).
        shift:             Optional Shift instance (must belong to opportunity).
        actor:             User instance — must be volunteer_profile.user.

    Returns:
        Saved HoursLog instance.

    Raises:
        PermissionDenied:  actor is not the volunteer's User.
        ValidationError:   shift mismatch, duplicate shift entry, or field
                           constraint violation (hours out of range, etc.).
    """
    # --- Permission check (OUTSIDE atomic) ---
    if actor.pk != volunteer_profile.user_id:
        raise PermissionDenied(
            f"User #{actor.pk} cannot log hours on behalf of "
            f"VolunteerProfile #{volunteer_profile.pk}."
        )

    # --- Future-date check (OUTSIDE atomic) ---
    today = timezone.localtime(timezone.now()).date()
    if date > today:
        raise ValidationError(
            {"date": _("Hours cannot be logged for a future date.")}
        )

    # --- Shift ownership check (OUTSIDE atomic) ---
    if shift is not None:
        if shift.opportunity_id != opportunity.pk:
            raise ValidationError(
                {"shift": _("Shift does not belong to this opportunity.")}
            )
        # Friendly pre-check; DB UniqueConstraint is the authoritative guard.
        from apps.volunteers.models import HoursLog as _HoursLog
        if _HoursLog.objects.filter(
            volunteer=volunteer_profile, shift=shift
        ).exists():
            raise ValidationError(
                {"shift": _("You have already logged hours for this shift.")}
            )

    with transaction.atomic():
        from apps.volunteers.models import HoursLog

        log = HoursLog(
            volunteer=volunteer_profile,
            opportunity=opportunity,
            shift=shift,
            date=date,
            hours=hours,
            description=description,
            status=HoursLog.STATUS_PENDING,
        )
        log.full_clean()
        try:
            log.save()
        except IntegrityError:
            raise ValidationError(
                {"shift": _("Hours already logged for this shift.")}
            )

        # Capture PK before the closure — avoid holding a reference to the
        # full model instance inside the on_commit closure (memory / GC).
        _pk = log.pk

        def _post_commit():
            from apps.volunteers.signals import hours_logged
            from apps.volunteers.models import HoursLog as _HL
            l = _HL.objects.select_related(
                "volunteer__user", "opportunity"
            ).get(pk=_pk)
            hours_logged.send_robust(
                sender=_HL,
                instance=l,
                volunteer=l.volunteer,
                opportunity=l.opportunity,
            )

        transaction.on_commit(_post_commit)

        logger.info(
            "volunteers.hours: log created pk=%s volunteer=%s hours=%s",
            log.pk,
            volunteer_profile.pk,
            hours,
        )

    return log


# ---------------------------------------------------------------------------
# approve_hours()
# ---------------------------------------------------------------------------

def approve_hours(
    *,
    hours_log,
    actor,
):
    """
    Coordinator approves a pending HoursLog.

    Recomputes the volunteer's total_hours_approved and checks for newly
    crossed recognition milestones (both deferred to on_commit so they are
    never fired on a rolled-back transaction).

    Pre-checks (outside atomic):
    - actor must hold volunteers.change_hourslog.
    - hours_log must be STATUS_PENDING (fast-path; re-checked inside atomic).

    Side-effects (inside transaction.on_commit()):
    - Fires hours_approved signal via send_robust().
    - Calls _recompute_total_hours() to update VolunteerProfile.total_hours_approved.
    - Calls _check_milestones() to create RecognitionMilestone records and fire
      milestone_achieved signals.

    Args:
        hours_log: HoursLog instance to approve.
        actor:     User instance — must hold volunteers.change_hourslog.

    Returns:
        Updated HoursLog instance.

    Raises:
        PermissionDenied: actor lacks the required Django permission.
        ValidationError:  hours_log is not in STATUS_PENDING.
    """
    from apps.volunteers.models import HoursLog

    # --- Permission check (OUTSIDE atomic) ---
    if not actor.has_perm("volunteers.change_hourslog"):
        raise PermissionDenied(
            f"User #{actor.pk} does not have 'volunteers.change_hourslog'."
        )

    # Fast-path status check (OUTSIDE atomic) — avoids acquiring a row lock
    # when the state is clearly wrong.
    if hours_log.status != HoursLog.STATUS_PENDING:
        raise ValidationError(
            {"status": _("Only pending hours logs can be approved.")}
        )

    with transaction.atomic():
        # Re-fetch with row-level lock to serialise concurrent approvals /
        # rejections.  The second writer will block here, re-read the updated
        # status, and raise ValidationError rather than silently overwriting.
        hours_log = (
            HoursLog.objects
            .select_for_update()
            .select_related("volunteer")
            .get(pk=hours_log.pk)
        )

        # Re-validate status (race guard — someone else may have acted first).
        if hours_log.status != HoursLog.STATUS_PENDING:
            raise ValidationError(
                {"status": _("Only pending hours logs can be approved.")}
            )

        hours_log.status = HoursLog.STATUS_APPROVED
        hours_log.approved_by = actor
        hours_log.approved_at = timezone.now()
        hours_log.full_clean()
        hours_log.save(
            update_fields=["status", "approved_by", "approved_at", "updated_at"]
        )

        # Read old total INSIDE the transaction (before on_commit) so that the
        # milestone comparison uses the pre-approval value.
        from apps.volunteers.models import VolunteerProfile
        old_total = (
            VolunteerProfile.objects
            .values_list("total_hours_approved", flat=True)
            .get(pk=hours_log.volunteer_id)
        )

        _log_pk = hours_log.pk
        _volunteer_pk = hours_log.volunteer_id
        _old_total = old_total
        _actor_pk = actor.pk

        def _post_commit():
            from apps.volunteers.signals import hours_approved
            from apps.volunteers.models import HoursLog as _HL
            l = _HL.objects.select_related(
                "volunteer__user", "opportunity", "approved_by"
            ).get(pk=_log_pk)
            hours_approved.send_robust(
                sender=_HL,
                instance=l,
                approved_by=l.approved_by,
            )
            # Recompute total and check milestones.
            new_total = _recompute_total_hours(volunteer_profile_pk=_volunteer_pk)
            _check_milestones(
                volunteer_profile_pk=_volunteer_pk,
                old_total=_old_total,
                new_total=new_total,
            )

        transaction.on_commit(_post_commit)

        logger.info(
            "volunteers.hours: log approved pk=%s volunteer=%s actor=%s",
            hours_log.pk,
            hours_log.volunteer_id,
            actor.pk,
        )

    return hours_log


# ---------------------------------------------------------------------------
# reject_hours()
# ---------------------------------------------------------------------------

def reject_hours(
    *,
    hours_log,
    actor,
    reason: str,
):
    """
    Coordinator rejects a pending HoursLog.

    PIPEDA: reason is stored for the coordinator's internal records only.
    It is NOT passed in the hours_rejected signal kwargs and must never be
    included in any volunteer-facing notification.

    Pre-checks (outside atomic):
    - actor must hold volunteers.change_hourslog.
    - reason must be non-empty after stripping whitespace.
    - hours_log must be STATUS_PENDING (fast-path; re-checked inside atomic).

    Side-effects (inside transaction.on_commit()):
    - Fires hours_rejected signal via send_robust() with a .only()-fetched
      instance that does NOT include rejection_reason (PIPEDA defence-in-depth).

    Args:
        hours_log: HoursLog instance to reject.
        actor:     User instance — must hold volunteers.change_hourslog.
        reason:    Coordinator-internal rejection note.

    Returns:
        Updated HoursLog instance.

    Raises:
        PermissionDenied: actor lacks the required Django permission.
        ValidationError:  reason is blank, or hours_log is not STATUS_PENDING.
    """
    from apps.volunteers.models import HoursLog

    # --- Permission check (OUTSIDE atomic) ---
    if not actor.has_perm("volunteers.change_hourslog"):
        raise PermissionDenied(
            f"User #{actor.pk} does not have 'volunteers.change_hourslog'."
        )

    # --- Reason check (OUTSIDE atomic) ---
    if not reason or not reason.strip():
        raise ValidationError(
            {"reason": _("A rejection reason is required.")}
        )

    # Fast-path status check (OUTSIDE atomic).
    if hours_log.status != HoursLog.STATUS_PENDING:
        raise ValidationError(
            {"status": _("Only pending hours logs can be rejected.")}
        )

    with transaction.atomic():
        # Re-fetch with row-level lock.
        hours_log = (
            HoursLog.objects
            .select_for_update()
            .get(pk=hours_log.pk)
        )

        # Re-validate status (race guard).
        if hours_log.status != HoursLog.STATUS_PENDING:
            raise ValidationError(
                {"status": _("Only pending hours logs can be rejected.")}
            )

        hours_log.status = HoursLog.STATUS_REJECTED
        # Truncate to field max_length (CharField max_length=300) — mirrors the
        # pattern used in applications.py for defensive string truncation.
        hours_log.rejection_reason = reason.strip()[:300]
        # approved_by is intentionally NOT set on rejection.
        hours_log.full_clean()
        hours_log.save(
            update_fields=["status", "rejection_reason", "updated_at"]
        )

        _pk = hours_log.pk
        _actor_pk = actor.pk

        def _post_commit():
            from apps.volunteers.signals import hours_rejected
            from apps.volunteers.models import HoursLog as _HL
            from django.contrib.auth import get_user_model
            User = get_user_model()
            # email included for coordinator-audit notification only — never send to volunteer-facing context
            _actor = User.objects.only("pk", "email").get(pk=_actor_pk)
            # PIPEDA: fetch with .only() so rejection_reason is deferred and
            # inaccessible to signal receivers without an explicit extra query.
            l = _HL.objects.only(
                "pk", "status", "volunteer_id", "opportunity_id", "hours", "date"
            ).get(pk=_pk)
            hours_rejected.send_robust(
                sender=_HL,
                instance=l,
                rejected_by=_actor,
                # PIPEDA: rejection_reason deliberately omitted from signal kwargs
                # so receivers cannot accidentally include it in volunteer-facing output.
            )

        transaction.on_commit(_post_commit)

        logger.info(
            "volunteers.hours: log rejected pk=%s volunteer=%s actor=%s",
            hours_log.pk,
            hours_log.volunteer_id,
            actor.pk,
            # NOTE: rejection_reason deliberately omitted (PIPEDA data minimisation).
        )

    return hours_log


# ---------------------------------------------------------------------------
# _recompute_total_hours()  (private)
# ---------------------------------------------------------------------------

def _recompute_total_hours(*, volunteer_profile_pk) -> Decimal:
    """
    Recalculate and atomically update VolunteerProfile.total_hours_approved.

    Uses a bulk .update() (bypassing full_clean()/save()) for performance.
    The DB-level CheckConstraint (total_hours_approved >= 0) is the final
    guard against a negative total.

    Called from approve_hours() inside on_commit() — runs after the approving
    transaction commits, so the newly-approved HoursLog is visible to the
    aggregate query.

    Args:
        volunteer_profile_pk: PK of the VolunteerProfile to update.

    Returns:
        Recomputed Decimal total hours.
    """
    from django.db.models import Sum
    from apps.volunteers.models import HoursLog, VolunteerProfile

    # H2 fix: wrap the SUM + UPDATE in atomic() and acquire a row-level lock on
    # the VolunteerProfile before computing the aggregate.  Two concurrent
    # on_commit callbacks both try to recompute the total; without the lock the
    # second UPDATE overwrites the first using a stale partial aggregate.
    with transaction.atomic():
        # Serialise concurrent callers — the second caller blocks here until the
        # first has committed its UPDATE, then proceeds with the fully-updated set
        # of HoursLog rows visible.
        VolunteerProfile.objects.select_for_update().get(pk=volunteer_profile_pk)

        total = (
            HoursLog.objects
            .filter(
                volunteer_id=volunteer_profile_pk,
                status=HoursLog.STATUS_APPROVED,
            )
            .aggregate(total=Sum("hours"))["total"]
        ) or Decimal("0")

        VolunteerProfile.objects.filter(pk=volunteer_profile_pk).update(
            total_hours_approved=total
        )

    logger.info(
        "volunteers.hours: recomputed total volunteer=%s total=%s",
        volunteer_profile_pk,
        total,
    )

    return total


# ---------------------------------------------------------------------------
# _check_milestones()  (private)
# ---------------------------------------------------------------------------

def _check_milestones(*, volunteer_profile_pk, old_total, new_total) -> list:
    """
    Create RecognitionMilestone records for any MILESTONE_THRESHOLDS crossed.

    Idempotent via get_or_create — safe for Celery retries.  The
    RecognitionMilestone.unique_together = [("volunteer", "hours_threshold")]
    DB constraint is the final safeguard against duplicate milestones.

    Args:
        volunteer_profile_pk: PK of the VolunteerProfile being checked.
        old_total:            Total hours before the latest approval (Decimal).
        new_total:            Total hours after the latest approval (Decimal).

    Returns:
        List of newly-created RecognitionMilestone instances (may be empty).
    """
    from apps.volunteers.models import RecognitionMilestone, VolunteerProfile
    from apps.volunteers.signals import milestone_achieved

    newly_achieved = []

    for threshold in MILESTONE_THRESHOLDS:
        if old_total < threshold <= new_total:
            milestone, created = RecognitionMilestone.objects.get_or_create(
                volunteer_id=volunteer_profile_pk,
                hours_threshold=threshold,
                defaults={"notification_sent": False},
            )
            if created:
                newly_achieved.append(milestone)
                volunteer = (
                    VolunteerProfile.objects
                    .select_related("user")
                    .get(pk=volunteer_profile_pk)
                )
                results = milestone_achieved.send_robust(
                    sender=RecognitionMilestone,
                    instance=milestone,
                    volunteer=volunteer,
                    hours_threshold=threshold,
                )
                for receiver_fn, exc in results:
                    if isinstance(exc, Exception):
                        logger.error(
                            "_check_milestones: receiver %s raised %s",
                            receiver_fn,
                            exc,
                            exc_info=exc,
                        )
                logger.info(
                    "volunteers.hours: milestone achieved volunteer=%s threshold=%s",
                    volunteer_profile_pk,
                    threshold,
                )

    return newly_achieved
