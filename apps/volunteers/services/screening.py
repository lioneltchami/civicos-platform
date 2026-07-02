"""
Volunteer Management BB — Screening service functions.

Manages background checks and reference records (ScreeningRecord) through
their full lifecycle:
  record_check()        — coordinator initiates a new screening record
  complete_check()      — coordinator records the outcome (verified_clear)
  check_expiring_soon() — query helper for expiry-alert Celery beat task

PIPEDA / Criminal Records Act (Canada) invariants enforced here:
  - The actual criminal record check result is NEVER stored. Only the fact
    that the coordinator has verified the result as "clear" (verified_clear=True)
    is recorded. verified_clear=False triggers a workflow; the content of that
    finding is never persisted here.
  - All log entries reference volunteer by profile.pk only — no name or email.
  - ScreeningRecord.clean() enforces VSC note restrictions (max 150 chars,
    no prohibited keyword fragments). full_clean() must be called before every save.

Field mapping (actual model vs spec):
  completed_date  — the date the volunteer completed / obtained the check
                    (spec called this `completed_at`; the model uses DateField)
  expires_date    — expiry date (spec called this `expiry_date`)
  verified_by     — FK to the user who verified the result
                    (spec called this `completed_by`)
  verified_at     — timestamp when coordinator recorded the verification
"""
from __future__ import annotations

import datetime
import logging

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# Convenience set of all known check-type constants — evaluated lazily to
# avoid importing models at module load time.
_KNOWN_CHECK_TYPES: frozenset[str] | None = None


def _get_known_check_types() -> frozenset[str]:
    """Return a frozenset of all valid ScreeningRecord.CHECK_TYPE_* values."""
    global _KNOWN_CHECK_TYPES
    if _KNOWN_CHECK_TYPES is None:
        from apps.volunteers.models import ScreeningRecord
        _KNOWN_CHECK_TYPES = frozenset(
            value for value, _ in ScreeningRecord.CHECK_TYPE_CHOICES
        )
    return _KNOWN_CHECK_TYPES


# ---------------------------------------------------------------------------
# record_check()
# ---------------------------------------------------------------------------

def record_check(
    *,
    volunteer_profile,
    check_type: str,
    opportunity=None,
    requested_by,
    notes: str = "",
    expiry_date: datetime.date | None = None,
    completed_date: datetime.date | None = None,
) -> "ScreeningRecord":
    """
    Initiate a new screening record for a volunteer.

    Creates a ScreeningRecord in an unverified state (verified_clear=None).
    The coordinator later calls complete_check() once the volunteer presents
    the result.

    PIPEDA note: notes are stored as-is here. For VSC records, clean() enforces
    the 150-char cap and prohibited-keyword rules once verified_clear is set.

    Args:
        volunteer_profile: VolunteerProfile the check belongs to.
        check_type:        One of ScreeningRecord.CHECK_TYPE_* constants.
        opportunity:       Opportunity this check was obtained for, or None
                           for an organization-wide check.
        requested_by:      User (coordinator/staff) initiating the request.
                           Stored as verified_by since ScreeningRecord has no
                           requested_by FK; updated on complete_check().
        notes:             Logistical notes (submission date, agency reference).
                           Must not contain criminal-record detail.
        expiry_date:       Expiry date for the record (expires_date on model).
        completed_date:    Date the volunteer completed / obtained the check.
                           Defaults to today if not provided.

    Returns:
        Saved ScreeningRecord instance.

    Raises:
        ValueError:       check_type is not a recognised CHECK_TYPE_* constant.
        ValidationError:  full_clean() fails (e.g. notes violate VSC rules,
                          unique constraint on volunteer+check_type+opportunity).
    """
    # --- Service-layer defence-in-depth permission check ---
    # Callers (views) must also gate this action, but the service enforces the
    # minimum permission requirement independently so that non-HTTP entry points
    # (Celery tasks, management commands, shell scripts) cannot bypass the guard.
    # Superusers always pass (Django's has_perm() respects that invariant).
    #
    # Future scope check: if a tighter coordinator-scoping rule is needed (e.g.
    # "requested_by must coordinate at least one program this volunteer is
    # applying to"), insert it here after the has_perm() guard. For now a
    # broad Django permission is the required minimum; scoping is enforced at
    # the view layer via the opportunity FK on the screening record itself.
    if not requested_by.has_perm("volunteers.add_screeningrecord"):
        raise PermissionDenied(
            f"User #{requested_by.pk} does not have permission to record screening checks "
            "(requires 'volunteers.add_screeningrecord')."
        )

    from apps.volunteers.models import ScreeningRecord
    from django.core.exceptions import ValidationError

    # --- Validate check_type against known constants ---
    known = _get_known_check_types()
    if check_type not in known:
        raise ValueError(
            f"Unknown check_type '{check_type}'. "
            f"Valid values: {', '.join(sorted(known))}."
        )

    # completed_date is required on the model (no null=True). Default to today.
    effective_completed_date = completed_date or timezone.localtime(timezone.now()).date()

    record = ScreeningRecord(
        volunteer=volunteer_profile,
        check_type=check_type,
        opportunity=opportunity,
        completed_date=effective_completed_date,
        expires_date=expiry_date,
        notes=notes,
        # verified_clear intentionally left as None (pending)
        # The requesting coordinator is pre-populated as verified_by so there
        # is an audit trail. It will be overwritten on complete_check().
        verified_by=requested_by,
    )

    # A-4 fix: wrap full_clean() + save() in atomic() so that when this
    # function is called from a Celery task or management command (no outer
    # ATOMIC_REQUESTS transaction), any future on_commit() callbacks fire only
    # after the write is durable. Inside an HTTP request it becomes a savepoint.
    # full_clean() validates choices, max_length, unique constraints, and
    # the custom clean() VSC rules.
    with transaction.atomic():
        record.full_clean()
        record.save()

    logger.info(
        "volunteers.services.screening: ScreeningRecord #%s created — "
        "check_type=%s, opportunity=%s, volunteer profile #%s, requested_by user #%s.",
        record.pk,
        check_type,
        opportunity.pk if opportunity else "None",
        volunteer_profile.pk,
        requested_by.pk,
    )

    return record


# ---------------------------------------------------------------------------
# complete_check()
# ---------------------------------------------------------------------------

def complete_check(
    *,
    screening_record,
    verified_clear: bool,
    completed_by,
    notes: str = "",
) -> "ScreeningRecord":
    """
    Record the outcome of a background check.

    The coordinator calls this after the volunteer presents their result.

    PIPEDA: Only the verified_clear flag is stored — not the content of the
    check. For VSC records, notes are restricted to logistical content only
    (enforced by ScreeningRecord.clean() called via full_clean() below).

    Args:
        screening_record: ScreeningRecord instance to complete.
        verified_clear:   True = coordinator confirmed result is clear.
                          False = result not clear (triggers workflow externally).
        completed_by:     User (coordinator/staff) recording the outcome.
        notes:            Logistical notes. For VSC with a result: max 150 chars,
                          no criminal-record content keywords.

    Returns:
        Updated ScreeningRecord instance.

    Raises:
        ValidationError: full_clean() fails (e.g. VSC note restrictions violated).
    """
    # --- Service-layer defence-in-depth permission check ---
    # Mirrors the guard in record_check(). completed_by must hold the Django
    # change permission for ScreeningRecord. Superusers always pass.
    #
    # Future scope check: if coordinator-scoping is added to record_check(),
    # mirror it here — completed_by should coordinate the same program scope
    # as the volunteer's opportunity.
    if not completed_by.has_perm("volunteers.change_screeningrecord"):
        raise PermissionDenied(
            f"User #{completed_by.pk} does not have permission to complete screening checks "
            "(requires 'volunteers.change_screeningrecord')."
        )

    # Set fields on the instance before full_clean() so the VSC validation
    # in ScreeningRecord.clean() can see the final state.
    screening_record.verified_clear = verified_clear
    screening_record.verified_at = timezone.now()
    screening_record.verified_by = completed_by
    screening_record.notes = notes

    # A-4 fix: wrap full_clean() + save() in atomic() so that when this
    # function is called from a Celery task or management command (no outer
    # ATOMIC_REQUESTS transaction), any future on_commit() callbacks fire only
    # after the write is durable. Inside an HTTP request it becomes a savepoint.
    # full_clean() runs ScreeningRecord.clean() which enforces VSC note restrictions
    # when verified_clear is not None. This MUST happen before save().
    with transaction.atomic():
        screening_record.full_clean()
        screening_record.save(
            update_fields=["verified_clear", "verified_at", "verified_by", "notes", "updated_at"]
        )

    logger.info(
        "volunteers.services.screening: ScreeningRecord #%s completed — "
        "check_type=%s, verified_clear=%s, volunteer profile #%s, completed_by user #%s.",
        screening_record.pk,
        screening_record.check_type,
        verified_clear,
        screening_record.volunteer_id,
        completed_by.pk,
    )

    return screening_record


# ---------------------------------------------------------------------------
# check_expiring_soon()
# ---------------------------------------------------------------------------

def check_expiring_soon(
    *,
    days_ahead: int = 30,
) -> "QuerySet[ScreeningRecord]":
    """
    Return ScreeningRecords that will expire within ``days_ahead`` days.

    Used by the Celery beat task that dispatches ``screening_expiring`` signals.
    Only returns records that have been verified clear — records with
    verified_clear=False or None are excluded because expiry is irrelevant
    until the check is cleared.

    Excludes already-expired records (expires_date < today) so coordinators
    only see upcoming expirations, not historical ones.

    Args:
        days_ahead: Number of calendar days from today to look ahead (default 30).

    Returns:
        QuerySet of ScreeningRecord ordered by expires_date ASC.
    """
    from apps.volunteers.models import ScreeningRecord

    today = timezone.localtime(timezone.now()).date()
    cutoff = today + datetime.timedelta(days=days_ahead)

    return (
        ScreeningRecord.objects.filter(
            verified_clear=True,
            expires_date__gte=today,    # not already expired
            expires_date__lte=cutoff,   # expires within the window
        )
        .select_related("volunteer", "volunteer__user", "opportunity", "opportunity__program")
        .order_by("expires_date")
    )
