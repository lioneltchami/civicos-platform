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
from django.db import transaction
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

    # --- Business rule: no duplicate active application (pre-check for clarity) ---
    # The DB unique_together on (opportunity, volunteer) provides the hard guarantee;
    # this pre-check surfaces a readable ValidationError before hitting the DB constraint.
    existing_active = VolunteerApplication.objects.filter(
        volunteer=volunteer_profile,
        opportunity=opportunity,
        status__in=[
            VolunteerApplication.STATUS_PENDING,
            VolunteerApplication.STATUS_IN_REVIEW,
            VolunteerApplication.STATUS_APPROVED,
            VolunteerApplication.STATUS_WAITLISTED,
        ],
    ).exists()
    if existing_active:
        raise ValidationError(
            {
                "volunteer": (
                    f"VolunteerProfile #{volunteer_profile.pk} already has an "
                    f"active application for Opportunity #{opportunity.pk}."
                )
            }
        )

    # --- Build and validate the application ---
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
    # full_clean() validates field constraints (max_length, choices, etc.) and
    # runs model-level clean(). Must be called before save() per project convention.
    application.full_clean()
    application.save()

    logger.info(
        "volunteers.services.applications: application #%s created — "
        "opportunity #%s, volunteer profile #%s.",
        application.pk,
        opportunity.pk,
        volunteer_profile.pk,
    )

    # --- Post-commit side-effects ---
    # Both the signal and work-item creation are deferred to on_commit() so they
    # only execute after the DB write succeeds. This is critical under
    # ATOMIC_REQUESTS=True — the transaction may still be rolled back after save().
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
    Volunteer withdraws their own pending application.

    Only STATUS_PENDING applications may be withdrawn. Once an application
    is in review, approved, or already withdrawn/rejected, it cannot be
    self-withdrawn (the coordinator must handle those states).

    Args:
        application: VolunteerApplication instance to withdraw.
        actor:       User instance — must be the volunteer's own User.

    Returns:
        Updated VolunteerApplication instance.

    Raises:
        PermissionDenied: actor is not the volunteer's User.
        ValidationError:  application is not in STATUS_PENDING.
    """
    from apps.volunteers.models import VolunteerApplication
    from apps.volunteers.signals import application_withdrawn

    # --- Permission check ---
    _assert_is_volunteer_actor(application, actor)

    # --- Business rule: only pending applications can be self-withdrawn ---
    if application.status != VolunteerApplication.STATUS_PENDING:
        raise ValidationError(
            {
                "status": (
                    f"Application #{application.pk} cannot be withdrawn: "
                    f"current status is '{application.get_status_display()}'. "
                    "Only pending applications may be withdrawn."
                )
            }
        )

    application.status = VolunteerApplication.STATUS_WITHDRAWN
    application.save(update_fields=["status", "updated_at"])

    logger.info(
        "volunteers.services.applications: application #%s withdrawn by "
        "volunteer profile #%s.",
        application.pk,
        application.volunteer_id,
    )

    transaction.on_commit(
        lambda: application_withdrawn.send_robust(
            sender=VolunteerApplication,
            instance=application,
            actor=actor,
        )
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
    _assert_coordinator_permission(actor)

    # --- Business rule: only pending applications can be approved ---
    if application.status != VolunteerApplication.STATUS_PENDING:
        raise ValidationError(
            {
                "status": (
                    f"Application #{application.pk} cannot be approved: "
                    f"current status is '{application.get_status_display()}'. "
                    "Only pending applications may be approved."
                )
            }
        )

    now = timezone.now()
    application.status = VolunteerApplication.STATUS_APPROVED
    application.reviewed_by = actor
    application.reviewed_at = now
    application.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])

    logger.info(
        "volunteers.services.applications: application #%s approved by "
        "user #%s — opportunity #%s, volunteer profile #%s.",
        application.pk,
        actor.pk,
        application.opportunity_id,
        application.volunteer_id,
    )

    transaction.on_commit(
        lambda: application_approved.send_robust(
            sender=VolunteerApplication,
            instance=application,
            actor=actor,
            reviewed_by=actor,
        )
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
    _assert_coordinator_permission(actor)

    # --- Business rule: only pending applications can be rejected ---
    if application.status != VolunteerApplication.STATUS_PENDING:
        raise ValidationError(
            {
                "status": (
                    f"Application #{application.pk} cannot be rejected: "
                    f"current status is '{application.get_status_display()}'. "
                    "Only pending applications may be rejected."
                )
            }
        )

    now = timezone.now()
    application.status = VolunteerApplication.STATUS_REJECTED
    application.rejection_reason = rejection_reason  # internal only — never send to volunteer
    application.reviewed_by = actor
    application.reviewed_at = now
    application.save(
        update_fields=[
            "status",
            "rejection_reason",
            "reviewed_by",
            "reviewed_at",
            "updated_at",
        ]
    )

    logger.info(
        "volunteers.services.applications: application #%s rejected by "
        "user #%s — opportunity #%s, volunteer profile #%s.",
        application.pk,
        actor.pk,
        application.opportunity_id,
        application.volunteer_id,
        # NOTE: rejection_reason deliberately omitted from log (PIPEDA data minimisation).
    )

    transaction.on_commit(
        lambda: application_rejected.send_robust(
            sender=VolunteerApplication,
            instance=application,
            actor=actor,
            reviewed_by=actor,
            # PIPEDA: rejection_reason is NOT passed in the signal kwargs so that
            # receivers cannot accidentally include it in volunteer-facing output.
        )
    )

    return application
