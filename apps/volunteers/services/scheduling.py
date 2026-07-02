"""
Volunteer Management BB — Shift scheduling service functions.

Business rules for the full shift-booking lifecycle:
  book_shift()       — volunteer books a confirmed slot (or joins waitlist) on a shift
  cancel_booking()   — volunteer or coordinator cancels a booking; promotes waitlist
  mark_no_show()     — coordinator marks a confirmed volunteer as no-show post-shift
  complete_booking() — coordinator marks a booking as completed; auto-creates HoursLog
  cancel_shift()     — coordinator cancels an entire shift; bulk-cancels all bookings

Concurrency safety:
  All mutating operations acquire SELECT FOR UPDATE on the relevant row(s) inside
  transaction.atomic() before any status checks or capacity decisions.  This
  prevents overbooking and double-cancel races under PostgreSQL's default READ
  COMMITTED isolation.

PIPEDA invariants enforced here:
  - Logs contain only PKs — never volunteer names, emails, or other PII.
  - Signals dispatched via send_robust() inside transaction.on_commit() so that
    a failing receiver cannot roll back the originating transaction, and
    side-effects (emails, Celery tasks) are only triggered after the DB write
    is durable.

Permission model:
  - book_shift()    — actor must be the volunteer's own User.
  - cancel_booking() — actor must be the volunteer's own User OR hold
                       volunteers.change_shiftbooking.
  - mark_no_show()   — actor must hold volunteers.change_shiftbooking.
  - complete_booking() — actor must hold volunteers.change_shiftbooking.
  - cancel_shift()   — actor must hold volunteers.change_shift.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Booking statuses from which a cancellation is permitted.
# Using string literals here keeps this module importable without the ORM
# being fully initialised (e.g. during migrations or early test collection).
# The service functions reference ShiftBooking.STATUS_* constants at call-time.
_CANCELLABLE = frozenset({"confirmed", "waitlisted"})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _assert_is_volunteer_actor(
    *,
    booking,  # may be None (book_shift pre-check)
    actor,
    volunteer_profile,
) -> None:
    """
    Raise PermissionDenied if actor is not the volunteer's own User.

    Accepts booking=None so it can be used in book_shift() before a booking
    object exists.
    """
    if actor.pk != volunteer_profile.user_id:
        raise PermissionDenied(
            f"User #{actor.pk} cannot act on behalf of "
            f"VolunteerProfile #{volunteer_profile.pk}."
        )


# ---------------------------------------------------------------------------
# book_shift()
# ---------------------------------------------------------------------------

def book_shift(
    *,
    shift,
    volunteer_profile,
    actor,
) -> "ShiftBooking":
    """
    Volunteer books a confirmed slot (or joins the waitlist) on a shift.

    Overbooking prevention:
    The Shift row is locked with SELECT FOR UPDATE before the confirmed-count
    vs. capacity comparison, so concurrent book_shift() calls for the same
    shift are serialised at the DB layer.

    Args:
        shift:             Shift instance to book.
        volunteer_profile: VolunteerProfile instance of the booking volunteer.
        actor:             User instance — must be volunteer_profile.user.

    Returns:
        Saved ShiftBooking instance (status = confirmed or waitlisted).

    Raises:
        PermissionDenied: actor is not the volunteer's User.
        ValidationError:  shift is cancelled, has already started, volunteer
                          has no approved application, shift is full with no
                          waitlist (or waitlist is full), or booking already
                          exists.
    """
    # Lazy model imports — avoid circular imports at module load time.
    from apps.volunteers.models import Shift, ShiftBooking, VolunteerApplication

    # -----------------------------------------------------------------------
    # Pre-checks (OUTSIDE atomic) — permission and cheap state validation.
    # Keeping these outside the atomic block avoids holding the row lock any
    # longer than necessary.
    # -----------------------------------------------------------------------

    _assert_is_volunteer_actor(
        booking=None,
        actor=actor,
        volunteer_profile=volunteer_profile,
    )

    if shift.is_cancelled:
        raise ValidationError(
            {"shift": _("This shift has been cancelled.")}
        )

    if shift.start_datetime <= timezone.now():
        raise ValidationError(
            {"shift": _("You cannot book a shift that has already started.")}
        )

    # Volunteer must hold an approved application for the parent opportunity.
    has_approved = VolunteerApplication.objects.filter(
        volunteer=volunteer_profile,
        opportunity=shift.opportunity,
        status=VolunteerApplication.STATUS_APPROVED,
    ).exists()
    if not has_approved:
        raise ValidationError(
            {
                "volunteer": _(
                    "You must have an approved application for this opportunity "
                    "to book a shift."
                )
            }
        )

    # -----------------------------------------------------------------------
    # Mutating work inside an atomic block.
    # -----------------------------------------------------------------------
    with transaction.atomic():
        # Re-fetch the Shift with a row-level lock so capacity checks and the
        # subsequent INSERT are atomic at the DB layer.
        shift_locked = Shift.objects.select_for_update().get(pk=shift.pk)

        # Re-validate cancellation status (another request may have cancelled
        # the shift between the pre-check above and acquiring the lock).
        if shift_locked.is_cancelled:
            raise ValidationError(
                {"shift": _("This shift has been cancelled.")}
            )

        # Count confirmed bookings to determine if capacity remains.
        confirmed_count = ShiftBooking.objects.filter(
            shift=shift_locked,
            status=ShiftBooking.STATUS_CONFIRMED,
        ).count()

        capacity = shift_locked.effective_capacity  # None = unlimited

        # ---- Capacity / waitlist decision ----
        if capacity is None or confirmed_count < capacity:
            # Slot available — book as confirmed.
            booking = ShiftBooking(
                shift=shift_locked,
                volunteer=volunteer_profile,
                status=ShiftBooking.STATUS_CONFIRMED,
            )
        elif shift_locked.waitlist_enabled:
            # Shift is full but has a waitlist.
            waitlisted_qs = ShiftBooking.objects.filter(
                shift=shift_locked,
                status=ShiftBooking.STATUS_WAITLISTED,
            )

            # Check waitlist cap if set.
            if (
                shift_locked.waitlist_cap is not None
                and waitlisted_qs.count() >= shift_locked.waitlist_cap
            ):
                raise ValidationError(
                    {"shift": _("The waitlist for this shift is full.")}
                )

            # Assign the next sequential waitlist position.
            next_pos = (
                waitlisted_qs.aggregate(
                    Max("waitlist_position")
                )["waitlist_position__max"]
                or 0
            ) + 1

            booking = ShiftBooking(
                shift=shift_locked,
                volunteer=volunteer_profile,
                status=ShiftBooking.STATUS_WAITLISTED,
                waitlist_position=next_pos,
            )
        else:
            # Full and no waitlist.
            raise ValidationError(
                {"shift": _("This shift is fully booked.")}
            )

        try:
            booking.full_clean()
            booking.save()
        except IntegrityError:
            # Lost the race — another concurrent request already booked this
            # (shift, volunteer) pair.
            raise ValidationError(
                {"shift": _("You are already booked for this shift.")}
            )

        # Register post-commit signal.  Capture the PK so the closure does
        # not hold a reference to the booking model instance (avoids stale-
        # state issues if the instance is mutated later in the request).
        _booking_pk = booking.pk

        def _post_commit_booked():
            from apps.volunteers.models import ShiftBooking as _SB
            from apps.volunteers.signals import shift_booked
            b = _SB.objects.select_related(
                "shift__opportunity", "volunteer__user"
            ).get(pk=_booking_pk)
            shift_booked.send_robust(
                sender=_SB,
                instance=b,
                shift=b.shift,
                volunteer=b.volunteer,
            )

        transaction.on_commit(_post_commit_booked)

    logger.info(
        "volunteers.scheduling: shift booked pk=%s volunteer=%s status=%s",
        booking.pk,
        volunteer_profile.pk,
        booking.status,
    )

    return booking


# ---------------------------------------------------------------------------
# cancel_booking()
# ---------------------------------------------------------------------------

def cancel_booking(
    *,
    booking,
    actor,
    reason: str = "",
) -> "ShiftBooking":
    """
    Volunteer cancels their own booking, OR a coordinator cancels it on their
    behalf.  If the cancelled booking was confirmed and the shift has a
    waitlist, the highest-priority waitlisted volunteer is automatically
    promoted to confirmed.

    Args:
        booking: ShiftBooking instance to cancel.
        actor:   User instance — must be booking.volunteer.user OR hold
                 volunteers.change_shiftbooking.
        reason:  Optional free-text cancellation reason (stored, max 300 chars).

    Returns:
        Updated ShiftBooking instance (status = cancelled).

    Raises:
        PermissionDenied: actor is neither the volunteer nor a coordinator.
        ValidationError:  booking is already cancelled / completed / no_show.
    """
    from apps.volunteers.models import ShiftBooking

    # -----------------------------------------------------------------------
    # Pre-checks (OUTSIDE atomic).
    # -----------------------------------------------------------------------

    is_self = actor.pk == booking.volunteer.user_id
    is_coordinator = actor.has_perm("volunteers.change_shiftbooking")

    if not (is_self or is_coordinator):
        raise PermissionDenied(
            f"User #{actor.pk} may not cancel booking #{booking.pk}."
        )

    if booking.status not in _CANCELLABLE:
        raise ValidationError(
            {"status": _("This booking cannot be cancelled.")}
        )

    # -----------------------------------------------------------------------
    # Mutating work.
    # -----------------------------------------------------------------------
    with transaction.atomic():
        # Lock the Shift row first to prevent a concurrent cancel_shift() from
        # setting is_cancelled=True between our pre-check and the waitlist
        # promotion below.
        from apps.volunteers.models import Shift
        shift_locked = Shift.objects.select_for_update().get(pk=booking.shift_id)
        if shift_locked.is_cancelled:
            raise ValidationError(
                _("Cannot modify a booking on a shift that has already been cancelled.")
            )

        # Re-fetch with lock + related objects needed for signal / promotion.
        booking = (
            ShiftBooking.objects
            .select_for_update()
            .select_related("shift", "volunteer__user")
            .get(pk=booking.pk)
        )

        # Re-validate status after acquiring lock (race-condition guard).
        if booking.status not in _CANCELLABLE:
            raise ValidationError(
                {"status": _("This booking cannot be cancelled.")}
            )

        was_confirmed = booking.status == ShiftBooking.STATUS_CONFIRMED

        booking.status = ShiftBooking.STATUS_CANCELLED
        booking.cancelled_at = timezone.now()
        booking.cancellation_reason = reason[:300]
        booking.full_clean()
        booking.save(
            update_fields=[
                "status",
                "cancelled_at",
                "cancellation_reason",
                "updated_at",
            ]
        )

        # ---- Waitlist promotion ----
        if was_confirmed and shift_locked.waitlist_enabled:
            next_up = (
                ShiftBooking.objects
                .select_for_update()
                .filter(
                    shift=booking.shift,
                    status=ShiftBooking.STATUS_WAITLISTED,
                )
                .order_by("waitlist_position")
                .first()
            )

            if next_up is not None:
                next_up.status = ShiftBooking.STATUS_CONFIRMED
                next_up.waitlist_position = None
                next_up.full_clean()
                next_up.save(
                    update_fields=["status", "waitlist_position", "updated_at"]
                )

                _promoted_pk = next_up.pk

                def _post_commit_promoted():
                    from apps.volunteers.models import ShiftBooking as _SB
                    from apps.volunteers.signals import shift_booked
                    b = _SB.objects.select_related(
                        "shift__opportunity", "volunteer__user"
                    ).get(pk=_promoted_pk)
                    shift_booked.send_robust(
                        sender=_SB,
                        instance=b,
                        shift=b.shift,
                        volunteer=b.volunteer,
                    )

                transaction.on_commit(_post_commit_promoted)

        # Signal for the cancelled booking.
        _cancelled_pk = booking.pk
        _reason = reason

        def _post_commit_cancelled():
            from apps.volunteers.models import ShiftBooking as _SB
            from apps.volunteers.signals import shift_booking_cancelled
            b = _SB.objects.select_related(
                "shift__opportunity", "volunteer__user"
            ).get(pk=_cancelled_pk)
            shift_booking_cancelled.send_robust(
                sender=_SB,
                instance=b,
                shift=b.shift,
                volunteer=b.volunteer,
                reason=_reason,
            )

        transaction.on_commit(_post_commit_cancelled)

    logger.info(
        "volunteers.scheduling: booking cancelled pk=%s volunteer=%s actor=%s",
        booking.pk,
        booking.volunteer_id,
        actor.pk,
    )

    return booking


# ---------------------------------------------------------------------------
# mark_no_show()
# ---------------------------------------------------------------------------

def mark_no_show(
    *,
    booking,
    actor,
) -> "ShiftBooking":
    """
    Coordinator marks a confirmed volunteer as a no-show after the shift
    start time has passed.

    No signal is fired for no-show (none is declared in signals.py).

    Args:
        booking: ShiftBooking instance — must be STATUS_CONFIRMED.
        actor:   User instance — must hold volunteers.change_shiftbooking.

    Returns:
        Updated ShiftBooking instance (status = no_show).

    Raises:
        PermissionDenied: actor lacks the required Django permission.
        ValidationError:  booking is not confirmed, or shift has not started yet.
    """
    from apps.volunteers.models import ShiftBooking

    # -----------------------------------------------------------------------
    # Pre-checks (OUTSIDE atomic).
    # -----------------------------------------------------------------------

    if not actor.has_perm("volunteers.change_shiftbooking"):
        raise PermissionDenied(
            f"User #{actor.pk} does not have 'volunteers.change_shiftbooking'."
        )

    if booking.status != ShiftBooking.STATUS_CONFIRMED:
        raise ValidationError(
            {
                "status": _(
                    "Only confirmed bookings can be marked as no-show."
                )
            }
        )

    if booking.shift.start_datetime > timezone.now():
        raise ValidationError(
            {
                "shift": _(
                    "Cannot mark a no-show before the shift starts."
                )
            }
        )

    # -----------------------------------------------------------------------
    # Mutating work.
    # -----------------------------------------------------------------------
    with transaction.atomic():
        booking = (
            ShiftBooking.objects
            .select_for_update()
            .select_related("shift")
            .get(pk=booking.pk)
        )

        # Re-validate after lock (race-condition guard).
        if booking.status != ShiftBooking.STATUS_CONFIRMED:
            raise ValidationError(
                {
                    "status": _(
                        "Only confirmed bookings can be marked as no-show."
                    )
                }
            )

        booking.status = ShiftBooking.STATUS_NO_SHOW
        booking.full_clean()
        booking.save(update_fields=["status", "updated_at"])

    logger.info(
        "volunteers.scheduling: no-show marked pk=%s volunteer=%s actor=%s",
        booking.pk,
        booking.volunteer_id,
        actor.pk,
    )

    return booking


# ---------------------------------------------------------------------------
# complete_booking()
# ---------------------------------------------------------------------------

def complete_booking(
    *,
    booking,
    actor,
) -> "ShiftBooking":
    """
    Coordinator marks a confirmed booking as completed after the shift ends.
    If no HoursLog row exists for this (volunteer, shift) pair an HoursLog in
    STATUS_PENDING is automatically created so coordinators can later approve
    the hours.

    No signal is declared for booking completion (see signals.py).

    Args:
        booking: ShiftBooking instance — must be STATUS_CONFIRMED.
        actor:   User instance — must hold volunteers.change_shiftbooking.

    Returns:
        Updated ShiftBooking instance (status = completed).

    Raises:
        PermissionDenied: actor lacks the required Django permission.
        ValidationError:  booking is not confirmed, or shift has not ended yet.
    """
    from apps.volunteers.models import ShiftBooking

    # -----------------------------------------------------------------------
    # Pre-checks (OUTSIDE atomic).
    # -----------------------------------------------------------------------

    if not actor.has_perm("volunteers.change_shiftbooking"):
        raise PermissionDenied(
            f"User #{actor.pk} does not have 'volunteers.change_shiftbooking'."
        )

    if booking.status != ShiftBooking.STATUS_CONFIRMED:
        raise ValidationError(
            {
                "status": _(
                    "Only confirmed bookings can be marked as completed."
                )
            }
        )

    if booking.shift.end_datetime > timezone.now():
        raise ValidationError(
            {
                "shift": _(
                    "Cannot mark complete before the shift ends."
                )
            }
        )

    # -----------------------------------------------------------------------
    # Mutating work.
    # -----------------------------------------------------------------------
    with transaction.atomic():
        booking = (
            ShiftBooking.objects
            .select_for_update()
            .select_related("shift__opportunity", "volunteer")
            .get(pk=booking.pk)
        )

        # Re-validate after lock.
        if booking.status != ShiftBooking.STATUS_CONFIRMED:
            raise ValidationError(
                {
                    "status": _(
                        "Only confirmed bookings can be marked as completed."
                    )
                }
            )

        booking.status = ShiftBooking.STATUS_COMPLETED
        booking.full_clean()
        booking.save(update_fields=["status", "updated_at"])

        # ---- Auto-create HoursLog (pending coordinator approval) ----
        from apps.volunteers.models import HoursLog

        raw_hours = booking.shift.duration_hours  # float or None; property — never stored
        if not raw_hours or raw_hours <= 0:
            # Zero-duration shift — skip HoursLog creation entirely
            pass
        else:
            hours = Decimal(str(round(min(raw_hours, 24), 2)))
            HoursLog.objects.get_or_create(
                volunteer=booking.volunteer,
                shift=booking.shift,
                defaults={
                    "opportunity": booking.shift.opportunity,
                    "hours": hours,
                    "date": timezone.localtime(booking.shift.start_datetime).date(),
                    "status": HoursLog.STATUS_PENDING,
                    "description": "",
                },
            )

    logger.info(
        "volunteers.scheduling: booking completed pk=%s volunteer=%s actor=%s",
        booking.pk,
        booking.volunteer_id,
        actor.pk,
    )

    return booking


# ---------------------------------------------------------------------------
# cancel_shift()
# ---------------------------------------------------------------------------

def cancel_shift(
    *,
    shift,
    actor,
    reason: str,
) -> "Shift":
    """
    Coordinator cancels an entire shift.

    All confirmed and waitlisted bookings are bulk-updated to cancelled.  The
    shift_cancelled signal is fired post-commit so notification receivers can
    contact affected volunteers without risking transaction rollback.

    Args:
        shift:  Shift instance to cancel.
        actor:  User instance — must hold volunteers.change_shift.
        reason: Required cancellation reason (non-empty string).

    Returns:
        Updated Shift instance (is_cancelled = True).

    Raises:
        PermissionDenied: actor lacks the required Django permission.
        ValidationError:  shift is already cancelled, or reason is blank.
    """
    from apps.volunteers.models import Shift, ShiftBooking

    # -----------------------------------------------------------------------
    # Pre-checks (OUTSIDE atomic).
    # -----------------------------------------------------------------------

    if not actor.has_perm("volunteers.change_shift"):
        raise PermissionDenied(
            f"User #{actor.pk} does not have 'volunteers.change_shift'."
        )

    if shift.is_cancelled:
        raise ValidationError(
            {"shift": _("This shift is already cancelled.")}
        )

    if not reason or not reason.strip():
        raise ValidationError(
            {"reason": _("A cancellation reason is required.")}
        )

    # -----------------------------------------------------------------------
    # Mutating work.
    # -----------------------------------------------------------------------
    with transaction.atomic():
        shift = Shift.objects.select_for_update().get(pk=shift.pk)

        # Re-validate cancellation (race-condition guard).
        if shift.is_cancelled:
            raise ValidationError(
                {"shift": _("This shift is already cancelled.")}
            )

        shift.is_cancelled = True
        shift.cancelled_at = timezone.now()
        shift.cancelled_by = actor
        shift.cancellation_reason = reason.strip()
        shift.full_clean()
        shift.save(
            update_fields=[
                "is_cancelled",
                "cancelled_at",
                "cancelled_by",
                "cancellation_reason",
                "updated_at",
            ]
        )

        # Bulk-cancel all active bookings.  .update() bypasses model-level
        # clean() — the status transition from confirmed/waitlisted → cancelled
        # is straightforward and requires no model validation.
        ShiftBooking.objects.filter(
            shift=shift,
            status__in=[
                ShiftBooking.STATUS_CONFIRMED,
                ShiftBooking.STATUS_WAITLISTED,
            ],
        ).update(
            status=ShiftBooking.STATUS_CANCELLED,
            cancelled_at=timezone.now(),
            cancellation_reason=f"Shift cancelled: {reason.strip()[:250]}",
            updated_at=timezone.now(),
        )

        # Post-commit signal — capture PKs only to avoid holding model
        # instances in the closure.
        _shift_pk = shift.pk
        _actor_pk = actor.pk
        _reason_stripped = reason.strip()

        def _post_commit_shift_cancelled():
            from apps.volunteers.models import Shift as _Shift
            from apps.volunteers.signals import shift_cancelled as _sig
            from django.contrib.auth import get_user_model

            s = _Shift.objects.select_related(
                "opportunity__program"
            ).get(pk=_shift_pk)
            a = get_user_model().objects.get(pk=_actor_pk)
            _sig.send_robust(
                sender=_Shift,
                instance=s,
                opportunity=s.opportunity,
                reason=_reason_stripped,
                cancelled_by=a,
            )

        transaction.on_commit(_post_commit_shift_cancelled)

    logger.info(
        "volunteers.scheduling: shift cancelled pk=%s actor=%s",
        shift.pk,
        actor.pk,
    )

    return shift
