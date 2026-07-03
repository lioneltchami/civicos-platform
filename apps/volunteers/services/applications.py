"""
Volunteer Management BB — Application service functions.

Business rules for the full application lifecycle:
  apply()              — volunteer submits application to an opportunity
  withdraw()           — volunteer withdraws a pending application
  approve_application() — coordinator approves a pending application
  reject_application()  — coordinator rejects a pending application

PIPEDA invariants enforced here:
  - rejection_reason is stored for internal records only; never surfaced to volunteer.
  - All signal dispatches use send_robust() so a failing receiver never rolls back
    the ATOMIC_REQUESTS transaction.
  - Celery-bound side-effects (work items, notifications) are scheduled inside
    transaction.on_commit() so they only fire after the DB write commits.

Permission model:
  - apply() / withdraw() — actor must be the volunteer's own User.
  - approve_application() / reject_application() — actor must hold the Django
    permission "volunteers.change_volunteerapplication".
"""
from __future__ import annotations

import logging

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _assert_is_volunteer_actor(application, actor) -> None:
    """Raise PermissionDenied if actor is not the volunteer's User."""
    if application.volunteer.user_id != actor.pk:
        raise PermissionDenied(
            "Only the volunteer themselves may perform this action "
            f"(application #{application.pk})."
        )


def _assert_coordinator_permission(actor) -> None:
    """
    Raise PermissionDenied if actor lacks volunteers.change_volunteerapplication.

    Superusers always pass. Uses has_perm() which respects object-level
    backends and caches the result per request via _perm_cache.
    """
    if not actor.has_perm("volunteers.change_volunteerapplication"):
        raise PermissionDenied(
            f"User #{actor.pk} does not have 'volunteers.change_volunteerapplication'."
        )


# ---------------------------------------------------------------------------
# apply()
# ---------------------------------------------------------------------------

def apply(
    *,
    volunteer_profile,
    opportunity,
    motivation: str = "",
    availability_json: dict | None = None,
    consent_record=None,
    actor,
) -> "VolunteerApplication":
    """
    Submit a volunteer application to an opportunity.

    Business rules validated:
    - The opportunity must be accepting applications (published + within close date).
    - The volunteer must not already have an active (pending/approved) application
      for the same opportunity. A pre-check is performed for a better error message;
      the DB unique_together constraint is the authoritative guard.
    - actor must be the volunteer's own User.

    Side-effects (all inside transaction.on_commit()):
    - Fires application_submitted signal via send_robust().
    - Creates a Workflows BB work item for coordinator review.

    Args:
        volunteer_profile: VolunteerProfile instance for the applying volunteer.
        opportunity:        Opportunity instance being applied to.
        motivation:         Volunteer's cover-letter / motivation text.
        availability_json:  Optional JSON availability snapshot at application time.
        consent_record:     ConsentRecord (signed volunteer agreement) or None.
        actor:              User instance — must be volunteer_profile.user.

    Returns:
        Saved VolunteerApplication instance.

    Raises:
        PermissionDenied:  actor is not the volunteer's User.
        ValidationError:   opportunity is not accepting applications, or a duplicate
                           active application already exists.
    """
    # Lazy imports to avoid circular-import cycles at module load time.
    from apps.volunteers.models import VolunteerApplication
    from apps.volunteers.signals import application_submitted
    from apps.workflows.services import create_work_item
    from apps.workflows.models import WorkItemPriority

    # --- Permission check ---
    if volunteer_profile.user_id != actor.pk:
        raise PermissionDenied(
            f"User #{actor.pk} cannot apply on behalf of "
            f"VolunteerProfile #{volunteer_profile.pk}."
        )

    # --- Business rule: opportunity must be accepting applications ---
    if not opportunity.is_accepting_applications:
        raise ValidationError(
            {
                "opportunity": (
                    f"Opportunity #{opportunity.pk} is not currently accepting "
                    "applications (status: %(status)s)."
                    % {"status": opportunity.get_status_display()}
                )
            }
        )

    # --- Build the application object (not yet saved) ---
    # Note: availability_json is accepted as a parameter for forward-compatibility
    # but the VolunteerApplication model does not currently have this field.
    # It is ignored here; callers can store it in a separate model if needed.
    application = VolunteerApplication(
        volunteer=volunteer_profile,
        opportunity=opportunity,
        motivation=motivation,
        consent_record=consent_record,
        status=VolunteerApplication.STATUS_PENDING,
    )

    # --- Business rule: no duplicate active application ---
    # select_for_update() serializes concurrent apply() calls for the same
    # (opportunity, volunteer) pair — eliminates the race condition window
    # between the existence check and the INSERT.  transaction.atomic() here
    # creates a savepoint inside the outer ATOMIC_REQUESTS transaction.
    #
    # Re-application design: unique_together = [("opportunity", "volunteer")]
    # means there is at most ONE row per pair regardless of status. When a
    # previously withdrawn volunteer re-applies we UPDATE that withdrawn row
    # back to pending instead of trying to INSERT a new row (which would
    # violate the constraint and permanently lock out the volunteer).
    with transaction.atomic():
        # Lock ALL rows for this (opportunity, volunteer) pair — there can only
        # ever be one due to unique_together, but select_for_update() serialises
        # concurrent apply() calls so the check→write is atomic.
        existing = (
            VolunteerApplication.objects
            .select_for_update()
            .filter(
                opportunity=opportunity,
                volunteer=volunteer_profile,
            )
        )

        # Check for an already-active (non-withdrawn) application first.
        active = existing.exclude(status=VolunteerApplication.STATUS_WITHDRAWN)
        if active.exists():
            raise ValidationError(
                {
                    "opportunity": (
                        "You already have an active application for this opportunity."
                    )
                }
            )

        # Check for a previously withdrawn application — reuse the existing row
        # rather than inserting a new one (unique_together blocks the INSERT).
        withdrawn = existing.filter(
            status=VolunteerApplication.STATUS_WITHDRAWN
        ).first()

        if withdrawn:
            # Reactivate: reset the withdrawn row to pending for the new cycle.
            # Fields intentionally NOT reset:
            #   created_at       — immutable (auto_now_add); preserves original date.
            #   declares_no_relevant_criminal_history — persisted from previous
            #                      submission; coordinator screening notes remain too.
            #   screening_notes  — coordinator notes persist across cycles (audit trail).
            #   work_item        — replaced by the new on_commit work-item creation.
            withdrawn.status = VolunteerApplication.STATUS_PENDING
            withdrawn.motivation = motivation
            withdrawn.consent_record = consent_record
            withdrawn.reviewed_by = None
            withdrawn.reviewed_at = None
            withdrawn.rejection_reason = ""
            withdrawn.full_clean()
            withdrawn.save(update_fields=[
                "status",
                "motivation",
                "consent_record",
                "reviewed_by",
                "reviewed_at",
                "rejection_reason",
                "updated_at",
            ])
            # Reassign so the _post_commit closure below captures the correct instance.
            application = withdrawn
        else:
            # No prior application — create new.
            try:
                # full_clean() validates field constraints (max_length, choices, etc.)
                # and runs model-level clean(). Must be called before save() per
                # project convention.
                application.full_clean()
                application.save()
            except IntegrityError:
                # Lost the race — another concurrent request saved first.
                raise ValidationError(
                    {
                        "opportunity": (
                            "You already have an active application for this opportunity."
                        )
                    }
                )

        # --- Post-commit side-effects ---
        # Both the signal and work-item creation are deferred to on_commit() so they
        # only execute after the DB write succeeds. This is critical under
        # ATOMIC_REQUESTS=True — the transaction may still be rolled back after save().
        #
        # Registering on_commit() INSIDE atomic() is the correct pattern: inside an
        # HTTP request it fires on the outer ATOMIC_REQUESTS commit; in a Celery task
        # or management command (no outer transaction) it fires on the inner savepoint
        # commit — giving correct semantics in both contexts.  Registering it OUTSIDE
        # would cause it to fire immediately and synchronously in the Celery/command
        # case (Django fires on_commit() at once when called outside any atomic block).
        #
        # H-5 fix: moved on_commit registration inside atomic() to match the pattern
        # used by withdraw(), approve_application(), and reject_application().
        #
        # create_work_item() wraps itself in atomic(), which is fine as a savepoint
        # inside the outer request transaction. Its own work_item_created signal fires
        # on commit of that inner savepoint (i.e. effectively on outer commit).
        def _post_commit():
            application_submitted.send_robust(
                sender=VolunteerApplication,
                instance=application,
                actor=actor,
                opportunity=opportunity,
                volunteer=volunteer_profile,
            )
            try:
                create_work_item(
                    application,
                    title=f"Review application: {opportunity.get_title()}",
                    actor=None,  # system-initiated
                    priority=WorkItemPriority.NORMAL,
                )
            except Exception as exc:
                # Work-item creation failure must not cause a 500 — the application
                # was already saved. The coordinator can manually create a work item.
                logger.error(
                    "volunteers.services.applications: failed to create work item "
                    "for application #%s: %s",
                    application.pk,
                    exc,
                )

        transaction.on_commit(_post_commit)

    logger.info(
        "volunteers.services.applications: application #%s created — "
        "opportunity #%s, volunteer profile #%s.",
        application.pk,
        opportunity.pk,
        volunteer_profile.pk,
    )

    return application


# ---------------------------------------------------------------------------
# withdraw()
# ---------------------------------------------------------------------------

def withdraw(
    *,
    application,
    actor,
) -> "VolunteerApplication":
    """
    Volunteer withdraws their own pending or approved application.

    STATUS_PENDING and STATUS_APPROVED applications may be withdrawn.
    STATUS_WITHDRAWN and STATUS_REJECTED are terminal — re-withdrawing or
    withdrawing a rejected application makes no sense.

    Args:
        application: VolunteerApplication instance to withdraw.
        actor:       User instance — must be the volunteer's own User.

    Returns:
        Updated VolunteerApplication instance.

    Raises:
        PermissionDenied: actor is not the volunteer's User.
        ValidationError:  application is not in STATUS_PENDING or STATUS_APPROVED.
    """
    from apps.volunteers.models import VolunteerApplication
    from apps.volunteers.signals import application_withdrawn

    # --- Permission check ---
    # Stays outside atomic() — validation only, no state mutation, and keeping
    # it here avoids holding the DB row lock any longer than necessary.
    _assert_is_volunteer_actor(application, actor)

    # H-5 fix: wrap save + on_commit registration in atomic() so that when this
    # function is called from a Celery task or management command (no outer
    # ATOMIC_REQUESTS transaction), on_commit fires only after the write is durable.
    # Inside an HTTP request it becomes a savepoint — behaviour is identical.
    with transaction.atomic():
        # Re-fetch with a row-level lock so the status check and save are atomic
        # at the DB layer.  This prevents a coordinator from approving the
        # application while this withdraw is in-flight (or vice-versa): the
        # second writer will block on the lock, then re-read the already-changed
        # status and raise ValidationError rather than silently overwriting.
        application = (
            VolunteerApplication.objects
            .select_for_update()
            .get(pk=application.pk)
        )

        # --- Business rule: only pending or approved applications can be withdrawn ---
        # H7 fix: volunteers should also be able to cancel after being approved
        # (e.g. change of availability).  Terminal states (withdrawn, rejected)
        # raise immediately.
        _WITHDRAWABLE = {
            VolunteerApplication.STATUS_PENDING,
            VolunteerApplication.STATUS_APPROVED,
        }
        if application.status not in _WITHDRAWABLE:
            raise ValidationError(
                {
                    "status": (
                        "This application cannot be withdrawn in its current state."
                    )
                }
            )

        application.status = VolunteerApplication.STATUS_WITHDRAWN
        application.full_clean()
        application.save(update_fields=["status", "updated_at"])
        transaction.on_commit(
            lambda: application_withdrawn.send_robust(
                sender=VolunteerApplication,
                instance=application,
                actor=actor,
            )
        )

    logger.info(
        "volunteers.services.applications: application #%s withdrawn by "
        "volunteer profile #%s.",
        application.pk,
        application.volunteer_id,
    )

    return application


# ---------------------------------------------------------------------------
# approve_application()
# ---------------------------------------------------------------------------

def approve_application(
    *,
    application,
    actor,
) -> "VolunteerApplication":
    """
    Coordinator approves a pending volunteer application.

    Sets status to STATUS_APPROVED and records the reviewer. The volunteer
    is notified via the application_approved signal receiver.

    Args:
        application: VolunteerApplication instance to approve.
        actor:       User instance — must hold volunteers.change_volunteerapplication.

    Returns:
        Updated VolunteerApplication instance.

    Raises:
        PermissionDenied: actor lacks the required Django permission.
        ValidationError:  application is not in STATUS_PENDING.
    """
    from apps.volunteers.models import VolunteerApplication
    from apps.volunteers.signals import application_approved

    # --- Permission check ---
    # Stays outside atomic() — validation only, no state mutation, and keeping
    # it here avoids holding the DB row lock any longer than necessary.
    _assert_coordinator_permission(actor)

    # H-1 fix: broaden guard from STATUS_PENDING-only to the full set of non-terminal
    # statuses that a coordinator may still act on.  STATUS_APPROVED, STATUS_REJECTED,
    # and STATUS_WITHDRAWN are terminal — they must not be re-transitioned here.
    _approvable = frozenset({
        VolunteerApplication.STATUS_PENDING,
        VolunteerApplication.STATUS_IN_REVIEW,
        VolunteerApplication.STATUS_WAITLISTED,
    })

    # H-5 fix: wrap save + on_commit registration in atomic() so that when this
    # function is called from a Celery task or management command (no outer
    # ATOMIC_REQUESTS transaction), on_commit fires only after the write is durable.
    # Inside an HTTP request it becomes a savepoint — behaviour is identical.
    with transaction.atomic():
        # Re-fetch with a row-level lock so the status check and save are atomic
        # at the DB layer.  This prevents a volunteer's withdraw() from racing
        # with this approval (or two coordinators approving simultaneously): the
        # second writer blocks on the lock, re-reads the changed status, and
        # raises ValidationError rather than silently overwriting.
        application = (
            VolunteerApplication.objects
            .select_for_update()
            .select_related("opportunity__program")
            .get(pk=application.pk)
        )

        # Program-scope ownership check: re-evaluated on the freshly-locked
        # instance so a concurrent coordinator reassignment cannot race past
        # this guard (TOCTOU fix — checking the stale caller-supplied instance
        # before the lock would allow a window between the check and the save).
        if not actor.is_superuser and application.opportunity.program.coordinator_id != actor.pk:
            raise PermissionDenied(
                f"User #{actor.pk} does not coordinate the programme for "
                f"application #{application.pk}."
            )

        # --- Business rule: only pending/in-review/waitlisted applications can be approved ---
        if application.status not in _approvable:
            raise ValidationError(
                {
                    "status": (
                        "This application cannot be approved from its current state."
                    )
                }
            )

        now = timezone.now()
        application.status = VolunteerApplication.STATUS_APPROVED
        application.reviewed_by = actor
        application.reviewed_at = now
        application.full_clean()
        application.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
        transaction.on_commit(
            lambda: application_approved.send_robust(
                sender=VolunteerApplication,
                instance=application,
                actor=actor,
                reviewed_by=actor,
            )
        )

    logger.info(
        "volunteers.services.applications: application #%s approved by "
        "user #%s — opportunity #%s, volunteer profile #%s.",
        application.pk,
        actor.pk,
        application.opportunity_id,
        application.volunteer_id,
    )

    return application


# ---------------------------------------------------------------------------
# reject_application()
# ---------------------------------------------------------------------------

def reject_application(
    *,
    application,
    rejection_reason: str = "",
    actor,
) -> "VolunteerApplication":
    """
    Coordinator rejects a pending volunteer application.

    PIPEDA: rejection_reason is stored solely for the coordinator's internal
    records (audit trail, pattern analysis). It MUST NOT appear in any
    notification sent to the volunteer. The notification receiver for
    application_rejected uses generic "not selected" language only.

    Args:
        application:      VolunteerApplication instance to reject.
        rejection_reason: Internal coordinator note. Never exposed to volunteer.
        actor:            User instance — must hold volunteers.change_volunteerapplication.

    Returns:
        Updated VolunteerApplication instance.

    Raises:
        PermissionDenied: actor lacks the required Django permission.
        ValidationError:  application is not in STATUS_PENDING.
    """
    from apps.volunteers.models import VolunteerApplication
    from apps.volunteers.signals import application_rejected

    # --- Permission check ---
    # Stays outside atomic() — validation only, no state mutation, and keeping
    # it here avoids holding the DB row lock any longer than necessary.
    _assert_coordinator_permission(actor)

    # H-1 fix: broaden guard from STATUS_PENDING-only to the full set of non-terminal
    # statuses that a coordinator may still act on.  Mirrors approve_application().
    _rejectable = frozenset({
        VolunteerApplication.STATUS_PENDING,
        VolunteerApplication.STATUS_IN_REVIEW,
        VolunteerApplication.STATUS_WAITLISTED,
    })

    def _reject_post_commit():
        # Re-fetch a sanitised instance using .only() to whitelist safe fields.
        # Django defers ALL fields not in this list — they are inaccessible without
        # an explicit additional SELECT.  This means rejection_reason AND
        # screening_notes are both deferred: signal receivers cannot read either
        # field via the instance reference without triggering a new query, which
        # is an auditable, traceable access (PIPEDA defence-in-depth).
        safe_instance = (
            VolunteerApplication.objects
            .only(
                "pk", "status", "opportunity_id", "volunteer_id",
                "reviewed_by_id", "reviewed_at",
            )
            .get(pk=application.pk)
        )
        application_rejected.send_robust(
            sender=VolunteerApplication,
            instance=safe_instance,
            actor=actor,
            reviewed_by=actor,
            # PIPEDA: rejection_reason is NOT passed in the signal kwargs so that
            # receivers cannot accidentally include it in volunteer-facing output.
            # The safe_instance fetched above also omits rejection_reason so that
            # receivers cannot access it via instance.rejection_reason either.
        )

    # H-5 fix: wrap save + on_commit registration in atomic() so that when this
    # function is called from a Celery task or management command (no outer
    # ATOMIC_REQUESTS transaction), on_commit fires only after the write is durable.
    # Inside an HTTP request it becomes a savepoint — behaviour is identical.
    with transaction.atomic():
        # Re-fetch with a row-level lock so the status check and save are atomic
        # at the DB layer.  This prevents a concurrent approve_application() or
        # withdraw() from racing with this rejection: the second writer blocks on
        # the lock, re-reads the changed status, and raises ValidationError rather
        # than silently overwriting.
        application = (
            VolunteerApplication.objects
            .select_for_update()
            .select_related("opportunity__program")
            .get(pk=application.pk)
        )

        # Program-scope ownership check: re-evaluated on the freshly-locked
        # instance so a concurrent coordinator reassignment cannot race past
        # this guard (TOCTOU fix — mirrors approve_application()).
        if not actor.is_superuser and application.opportunity.program.coordinator_id != actor.pk:
            raise PermissionDenied(
                f"User #{actor.pk} does not coordinate the programme for "
                f"application #{application.pk}."
            )

        # --- Business rule: only pending/in-review/waitlisted applications can be rejected ---
        if application.status not in _rejectable:
            raise ValidationError(
                {
                    "status": (
                        "This application cannot be rejected from its current state."
                    )
                }
            )

        now = timezone.now()
        application.status = VolunteerApplication.STATUS_REJECTED
        application.rejection_reason = rejection_reason  # internal only — never send to volunteer
        application.reviewed_by = actor
        application.reviewed_at = now
        application.full_clean()
        application.save(
            update_fields=[
                "status",
                "rejection_reason",
                "reviewed_by",
                "reviewed_at",
                "updated_at",
            ]
        )
        transaction.on_commit(_reject_post_commit)

    logger.info(
        "volunteers.services.applications: application #%s rejected by "
        "user #%s — opportunity #%s, volunteer profile #%s.",
        application.pk,
        actor.pk,
        application.opportunity_id,
        application.volunteer_id,
        # NOTE: rejection_reason deliberately omitted from log (PIPEDA data minimisation).
    )

    return application
