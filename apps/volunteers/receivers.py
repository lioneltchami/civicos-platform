"""
Volunteer Management BB — Signal receivers.

Receivers are registered in VolunteersConfig.ready() in apps.py.
All receivers use send_robust() at dispatch time (in signals.py callers)
so a failing receiver never rolls back the originating transaction.

Wave 1: H-3 PIPEDA photo deletion on consent record withdrawal.
Wave 2: Notification receivers wired to the Notifications BB.

PIPEDA rules enforced across all receivers:
  - Volunteers are identified only by profile.pk in log messages.
  - rejection_reason MUST NOT appear in any notification context dict.
  - All receivers are wrapped in try/except — they must never raise, even
    when called outside of send_robust() (defensive depth-in-depth).

Import note:
  The volunteer-domain signals (application_submitted, etc.) are imported at
  the top of this module because Django's @receiver() decorator needs the live
  Signal object — not a string. VolunteerApplication and ScreeningRecord model
  imports are deferred inside each receiver body to avoid circular-import cycles
  at app-startup time (models → services → receivers → models is a common trap).
"""
import logging

from django.conf import settings
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format as django_date_format

# Signal objects — safe to import at module level because signals.py only
# imports django.dispatch.Signal and has no model references.
from apps.volunteers.signals import (
    application_approved,
    application_rejected,
    application_submitted,
    hours_approved,
    hours_rejected,
    milestone_achieved,
    screening_expiring,
    shift_booked,
    shift_booking_cancelled,
    shift_cancelled,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# H-3: PIPEDA photo deletion on consent record withdrawal
# ---------------------------------------------------------------------------

@receiver(pre_delete, sender="consent.ConsentRecord")
def delete_volunteer_photo_on_consent_withdrawal(sender, instance, **kwargs):
    """
    PIPEDA: When a ConsentRecord is deleted (consent withdrawn), delete the
    volunteer's photo if it was authorised by this consent record.

    We use pre_delete rather than post_delete because VolunteerProfile.photo_consent
    is declared with on_delete=SET_NULL. Django's SET_NULL runs as part of the
    delete cascade, which fires *after* the instance is deleted — so in
    post_delete the FK has already been NULLed and we can no longer identify
    which profiles were affected. In pre_delete the FK is still intact.
    """
    try:
        from apps.volunteers.models import VolunteerProfile
        profiles = VolunteerProfile.objects.filter(photo_consent=instance)
        for profile in profiles:
            if profile.photo:
                photo_name = profile.photo.name
                try:
                    profile.photo.delete(save=False)  # remove file from storage
                    profile.photo = None
                    profile.save(update_fields=["photo"])
                    logger.info(
                        "volunteers.receivers: deleted photo %s for profile %s "
                        "on consent record %s deletion (PIPEDA).",
                        photo_name,
                        profile.pk,
                        instance.pk,
                    )
                except Exception as exc:
                    logger.error(
                        "volunteers.receivers: failed to delete photo for profile %s "
                        "on consent withdrawal: %s",
                        profile.pk,
                        exc,
                    )
    except Exception as exc:
        logger.error(
            "volunteers.receivers: delete_volunteer_photo_on_consent_withdrawal "
            "error for consent_record %s: %s",
            instance.pk,
            exc,
        )


# ---------------------------------------------------------------------------
# Wave 2: Application lifecycle notification receivers
# ---------------------------------------------------------------------------

@receiver(application_submitted)
def notify_coordinator_on_application_submitted(sender, instance, actor=None, **kwargs):
    """
    Notify the opportunity coordinator when a new volunteer application arrives.

    Sender: VolunteerApplication (checked at dispatch via send_robust)
    Signal: application_submitted

    PIPEDA: context contains opportunity_title, application_pk, and
    volunteer_display (profile.pk only — never name or email).
    """
    try:
        from apps.notifications.services import send_email_notification

        # Re-fetch with select_related so opportunity→program→coordinator is
        # loaded in a single JOIN rather than N lazy queries.
        # volunteer_id is accessed directly (no .user needed here), so we only
        # need the forward FK chain on the opportunity side.
        instance = (
            instance.__class__.objects
            .select_related("opportunity__program__coordinator")
            .get(pk=instance.pk)
        )

        # Coordinator is on Program, not directly on Opportunity.
        coordinator = instance.opportunity.program.coordinator
        if coordinator is None:
            logger.warning(
                "volunteers.receivers: application #%s submitted for opportunity #%s "
                "but program coordinator is not set — skipping coordinator notification.",
                instance.pk,
                instance.opportunity_id,
            )
            return

        # Build coordinator review URL as an absolute URL for email clients.
        # reverse() returns a relative path; prepend SITE_URL so the link is
        # clickable from any email client. Degrade to "" on any error.
        try:
            review_url = settings.SITE_URL.rstrip("/") + reverse(
                "volunteers:application_review",
                kwargs={"pk": instance.pk},
            )
        except Exception:
            review_url = ""

        send_email_notification(
            recipient=coordinator,
            subject_key="volunteer_application_received",
            context={
                "opportunity_title": instance.opportunity.get_title(),
                "application_pk": instance.pk,
                # PIPEDA: volunteer identified by profile PK only — never name or email.
                "volunteer_display": f"Applicant #{instance.volunteer_id}",
                # M-8 fix: supply review_url so the email template's
                # {% if review_url %} block renders the direct-link-to-review button.
                "review_url": review_url,
            },
        )

    except Exception as exc:
        logger.error(
            "volunteers.receivers: notify_coordinator_on_application_submitted "
            "failed for application #%s, volunteer profile #%s: %s",
            getattr(instance, "pk", "?"),
            getattr(instance, "volunteer_id", "?"),
            exc,
        )


@receiver(application_approved)
def notify_volunteer_on_application_approved(sender, instance, actor=None, **kwargs):
    """
    Notify the volunteer that their application was approved.

    Sender: VolunteerApplication
    Signal: application_approved

    context includes opportunity_title and a portal URL so the volunteer
    can navigate directly to their opportunity detail page.
    """
    try:
        from apps.notifications.services import send_email_notification

        # Re-fetch with select_related so opportunity and volunteer→user are
        # loaded in a single JOIN rather than lazy queries on first attribute
        # access. prefetch_related_objects() is a no-op for forward FK chains.
        instance = (
            instance.__class__.objects
            .select_related("opportunity", "volunteer__user")
            .get(pk=instance.pk)
        )

        volunteer_user = instance.volunteer.user

        # Build an absolute portal URL for the email. reverse() returns a
        # relative path; prepend SITE_URL so the link is clickable from any
        # email client. Degrade gracefully to "" if the URL conf isn't wired.
        try:
            portal_url = settings.SITE_URL.rstrip("/") + reverse(
                "volunteers:opportunity_detail",
                kwargs={"pk": instance.opportunity_id},
            )
        except Exception:
            portal_url = ""

        send_email_notification(
            recipient=volunteer_user,
            subject_key="volunteer_application_approved",
            context={
                "opportunity_title": instance.opportunity.get_title(),
                "portal_url": portal_url,
            },
        )

    except Exception as exc:
        logger.error(
            "volunteers.receivers: notify_volunteer_on_application_approved "
            "failed for application #%s, volunteer profile #%s: %s",
            getattr(instance, "pk", "?"),
            getattr(instance, "volunteer_id", "?"),
            exc,
        )


@receiver(application_rejected)
def notify_volunteer_on_application_rejected(sender, instance, actor=None, **kwargs):
    """
    Notify the volunteer that their application was not successful.

    Sender: VolunteerApplication
    Signal: application_rejected

    PIPEDA: This notification deliberately contains no rejection_reason —
    only generic "not selected" language. The rejection_reason stored on
    the model is for coordinator internal records only and must NEVER appear
    in volunteer-facing communications.

    Note: rejection_reason is NOT available in **kwargs here because
    reject_application() deliberately omits it from the send_robust() call
    as a defence-in-depth PIPEDA guard.
    """
    try:
        from apps.notifications.services import send_email_notification

        # Re-fetch with select_related so opportunity and volunteer→user are
        # loaded in a single JOIN rather than lazy queries on first attribute
        # access. prefetch_related_objects() is a no-op for forward FK chains.
        instance = (
            instance.__class__.objects
            .select_related("opportunity", "volunteer__user")
            .get(pk=instance.pk)
        )

        volunteer_user = instance.volunteer.user

        send_email_notification(
            recipient=volunteer_user,
            subject_key="volunteer_application_rejected",
            context={
                # PIPEDA: only the opportunity title — no rejection_reason.
                "opportunity_title": instance.opportunity.get_title(),
            },
        )

    except Exception as exc:
        logger.error(
            "volunteers.receivers: notify_volunteer_on_application_rejected "
            "failed for application #%s, volunteer profile #%s: %s",
            getattr(instance, "pk", "?"),
            getattr(instance, "volunteer_id", "?"),
            exc,
        )


# ---------------------------------------------------------------------------
# Wave 2: Screening expiry notification receiver
# ---------------------------------------------------------------------------

@receiver(screening_expiring)
def notify_on_screening_expiring(sender, instance, **kwargs):
    """
    Notify both the volunteer and the opportunity coordinator when a screening
    record is expiring soon.

    Sender: ScreeningRecord
    Signal: screening_expiring

    This receiver is called by the Celery beat task that iterates over
    services.screening.check_expiring_soon() and dispatches screening_expiring
    for each expiring record.

    context exposes logistical fields only — check type, expiry date, days
    remaining. No criminal-record content is present in ScreeningRecord (by
    PIPEDA design), so there is nothing to accidentally include.
    """
    try:
        from apps.notifications.services import send_email_notification

        # Re-fetch with select_related so opportunity→program→coordinator and
        # volunteer→user are loaded in a single JOIN rather than lazy queries.
        # prefetch_related_objects() is a no-op for forward FK chains.
        # The Celery beat task may have used select_related on its queryset, but
        # receivers cannot rely on that — always re-fetch defensively.
        instance = (
            instance.__class__.objects
            .select_related(
                "opportunity__program__coordinator",
                "volunteer__user",
            )
            .get(pk=instance.pk)
        )

        today = timezone.localtime(timezone.now()).date()
        expires_date = instance.expires_date

        # days_remaining can be 0 (expires today) or negative (already expired,
        # which should not normally reach this receiver). Guard defensively.
        days_remaining = (expires_date - today).days if expires_date else None

        base_context = {
            "check_type_display": instance.get_check_type_display(),
            "expiry_date": expires_date,
            "days_remaining": days_remaining,
        }

        # --- Notify volunteer ---
        volunteer_user = instance.volunteer.user
        try:
            send_email_notification(
                recipient=volunteer_user,
                subject_key="volunteer_screening_expiring",
                context=base_context,
            )
        except Exception as exc:
            logger.error(
                "volunteers.receivers: notify_on_screening_expiring — failed to "
                "notify volunteer (profile #%s) for ScreeningRecord #%s: %s",
                instance.volunteer_id,
                instance.pk,
                exc,
            )

        # --- Notify coordinator (only when opportunity + program coordinator are set) ---
        # Coordinator is on Program, not directly on Opportunity.
        # opportunity__program__coordinator was prefetched above, so these
        # attribute accesses cost zero additional DB queries.
        coordinator = None
        if instance.opportunity_id:
            coordinator = instance.opportunity.program.coordinator

        if coordinator is not None:
            try:
                send_email_notification(
                    recipient=coordinator,
                    subject_key="volunteer_screening_expiring",
                    context={
                        **base_context,
                        # Coordinator view adds volunteer identifier and opportunity name
                        # for internal triage. Profile PK only (PIPEDA).
                        "volunteer_display": f"Volunteer #{instance.volunteer_id}",
                        "opportunity_title": instance.opportunity.get_title(),
                    },
                )
            except Exception as exc:
                logger.error(
                    "volunteers.receivers: notify_on_screening_expiring — failed to "
                    "notify coordinator (user #%s) for ScreeningRecord #%s: %s",
                    coordinator.pk,
                    instance.pk,
                    exc,
                )

    except Exception as exc:
        logger.error(
            "volunteers.receivers: notify_on_screening_expiring "
            "failed for ScreeningRecord #%s, volunteer profile #%s: %s",
            getattr(instance, "pk", "?"),
            getattr(instance, "volunteer_id", "?"),
            exc,
        )


# ---------------------------------------------------------------------------
# Wave 3: Shift booking, shift cancellation, hours, and milestone receivers
# ---------------------------------------------------------------------------

@receiver(shift_booked, sender="volunteers.ShiftBooking")
def notify_volunteer_on_shift_booked(sender, instance, shift, volunteer, **kwargs):
    """
    Email confirmation when a volunteer books (or joins waitlist for) a shift.

    Sender: ShiftBooking
    Signal: shift_booked

    context includes shift timing, location, waitlist status, and a portal URL
    so the volunteer can navigate to their shifts list.
    """
    try:
        from apps.notifications.services import send_email_notification
        from apps.volunteers.models import ShiftBooking

        # Re-fetch with select_related so shift→opportunity→program and
        # volunteer→user are loaded in a single JOIN.
        booking = (
            ShiftBooking.objects
            .select_related("shift__opportunity__program", "volunteer__user")
            .get(pk=instance.pk)
        )
        recipient = booking.volunteer.user

        local_start = timezone.localtime(booking.shift.start_datetime)
        local_end = timezone.localtime(booking.shift.end_datetime)

        try:
            portal_url = settings.SITE_URL + reverse("volunteers:my_shifts")
        except Exception:
            portal_url = ""

        opp = booking.shift.opportunity
        opp_title = opp.get_title() if hasattr(opp, "get_title") else opp.title_en

        context = {
            "recipient": recipient,
            "volunteer_display": booking.volunteer.display_name,
            "shift_title": booking.shift.title_en,
            "opportunity_title": opp_title,
            "shift_start": django_date_format(local_start, "DATETIME_FORMAT"),
            "shift_end": django_date_format(local_end, "DATETIME_FORMAT"),
            "shift_location": booking.shift.location_override or "",
            "is_waitlisted": booking.status == ShiftBooking.STATUS_WAITLISTED,
            "waitlist_position": booking.waitlist_position,
            "portal_url": portal_url,
        }
        send_email_notification(
            recipient=recipient,
            subject_key="volunteer_shift_booked",
            context=context,
        )
        logger.info(
            "volunteers.receivers: shift_booked notification sent booking=%s",
            booking.pk,
        )
    except Exception as exc:
        logger.error(
            "notify_volunteer_on_shift_booked failed for booking pk=%s: %s",
            instance.pk if instance else "unknown",
            exc,
            exc_info=True,
        )


@receiver(shift_cancelled, sender="volunteers.Shift")
def notify_volunteers_on_shift_cancelled(sender, instance, opportunity, reason, cancelled_by, **kwargs):
    """
    Notify all booked/waitlisted volunteers when a shift is cancelled.

    Sender: Shift
    Signal: shift_cancelled

    Iterates over all bookings that were cancelled as part of the shift
    cancellation (status=CANCELLED) and sends one email per affected volunteer.
    """
    try:
        from apps.notifications.services import send_email_notification
        from apps.volunteers.models import Shift, ShiftBooking

        # Re-fetch shift with opportunity and all affected bookings in minimal queries.
        shift = (
            Shift.objects
            .select_related("opportunity")
            .prefetch_related("bookings__volunteer__user")
            .get(pk=instance.pk)
        )

        local_start = timezone.localtime(shift.start_datetime)
        local_end = timezone.localtime(shift.end_datetime)

        # cancel_shift() sets affected bookings to STATUS_CANCELLED before firing
        # the signal, so we filter on that status to find every notifiable volunteer.
        affected_bookings = shift.bookings.filter(
            status=ShiftBooking.STATUS_CANCELLED
        ).select_related("volunteer__user")

        opp = shift.opportunity
        opp_title = opp.get_title() if hasattr(opp, "get_title") else opp.title_en

        for booking in affected_bookings:
            try:
                recipient = booking.volunteer.user
                try:
                    portal_url = settings.SITE_URL + reverse("volunteers:my_shifts")
                except Exception:
                    portal_url = ""
                context = {
                    "recipient": recipient,
                    "volunteer_display": booking.volunteer.display_name,
                    "shift_title": shift.title_en,
                    "opportunity_title": opp_title,
                    "shift_start": django_date_format(local_start, "DATETIME_FORMAT"),
                    "shift_end": django_date_format(local_end, "DATETIME_FORMAT"),
                    "cancellation_reason": reason or "",
                    "portal_url": portal_url,
                }
                send_email_notification(
                    recipient=recipient,
                    subject_key="volunteer_shift_cancelled",
                    context=context,
                )
            except Exception as exc:
                logger.error(
                    "Failed to notify volunteer pk=%s for shift cancellation: %s",
                    booking.volunteer_id, exc, exc_info=True,
                )
                continue

        logger.info(
            "volunteers.receivers: shift_cancelled notifications sent shift=%s affected=%s",
            shift.pk,
            affected_bookings.count(),
        )
    except Exception as exc:
        logger.error(
            "notify_volunteers_on_shift_cancelled failed for shift pk=%s: %s",
            instance.pk, exc, exc_info=True,
        )


@receiver(shift_booking_cancelled, sender="volunteers.ShiftBooking")
def notify_volunteer_on_booking_cancelled(sender, instance, shift, volunteer, reason="", **kwargs):
    """
    Notify a volunteer when their individual shift booking is cancelled by a coordinator.
    Distinct from shift_cancelled which covers whole-shift cancellations.
    """
    try:
        from apps.volunteers.models import ShiftBooking
        booking = ShiftBooking.objects.select_related(
            "volunteer__user", "shift__opportunity__program",
        ).get(pk=instance.pk)

        local_start = timezone.localtime(booking.shift.start_datetime)
        local_end = timezone.localtime(booking.shift.end_datetime)

        try:
            portal_url = settings.SITE_URL + reverse("volunteers:my_shifts")
        except Exception:
            portal_url = ""

        context = {
            "shift_title": booking.shift.opportunity.get_title(),
            "shift_start": django_date_format(local_start, "DATETIME_FORMAT"),
            "shift_end": django_date_format(local_end, "DATETIME_FORMAT"),
            "shift_location": booking.shift.location or "",
            "cancellation_reason": reason or "",
            "portal_url": portal_url,
            "volunteer_display": booking.volunteer.display_name,
        }

        from apps.notifications.services import send_email_notification
        send_email_notification(
            recipient=booking.volunteer.user,
            subject_key="volunteer_booking_cancelled",
            context=context,
        )
        logger.info(
            "Booking cancellation email sent to volunteer pk=%s for shift pk=%s",
            booking.volunteer_id,
            booking.shift_id,
        )
    except Exception as exc:
        logger.error(
            "notify_volunteer_on_booking_cancelled failed for booking pk=%s: %s",
            instance.pk,
            exc,
            exc_info=True,
        )


@receiver(hours_approved, sender="volunteers.HoursLog")
def notify_volunteer_on_hours_approved(sender, instance, approved_by, **kwargs):
    """
    Notify volunteer their hours submission was approved.

    Sender: HoursLog
    Signal: hours_approved

    PIPEDA: context deliberately excludes rejection_reason — this field is
    coordinator-internal and must never appear in volunteer-facing notifications.
    total_hours_approved is read from the denormalized VolunteerProfile field
    (recomputed by the service layer before the signal fires).
    """
    try:
        from apps.notifications.services import send_email_notification
        from apps.volunteers.models import HoursLog, VolunteerProfile

        # .only() enforces PIPEDA data-minimisation: rejection_reason is never
        # loaded into the Python object, eliminating any risk of accidental inclusion.
        log = (
            HoursLog.objects
            .only("pk", "hours", "date", "volunteer_id", "opportunity_id", "status")
            .select_related("volunteer__user", "opportunity")
            .get(pk=instance.pk)
        )
        volunteer = log.volunteer
        recipient = volunteer.user

        # Re-fetch denormalized total from VolunteerProfile — authoritative post-approval value.
        total = (
            VolunteerProfile.objects
            .values_list("total_hours_approved", flat=True)
            .get(pk=volunteer.pk)
        )

        opp = log.opportunity
        opp_title = (
            opp.get_title() if (opp and hasattr(opp, "get_title"))
            else (opp.title_en if opp else "")
        )

        try:
            portal_url = settings.SITE_URL + reverse("volunteers:my_hours")
        except Exception:
            portal_url = ""

        local_date = timezone.localtime(
            timezone.make_aware(
                timezone.datetime.combine(log.date, timezone.datetime.min.time())
            )
        ).date() if hasattr(log.date, 'year') else log.date

        context = {
            "recipient": recipient,
            "volunteer_display": volunteer.display_name,
            "hours": str(log.hours),
            "date": str(log.date),
            "opportunity_title": opp_title,
            "total_hours_approved": str(total),
            "portal_url": portal_url,
            # PIPEDA: rejection_reason deliberately omitted
        }
        send_email_notification(
            recipient=recipient,
            subject_key="volunteer_hours_approved",
            context=context,
        )
        logger.info(
            "volunteers.receivers: hours_approved notification sent log=%s volunteer=%s",
            log.pk,
            volunteer.pk,
        )
    except Exception as exc:
        logger.error(
            "volunteers.receivers: notify_volunteer_on_hours_approved "
            "failed for HoursLog #%s: %s",
            getattr(instance, "pk", "?"),
            exc,
            exc_info=True,
        )


@receiver(hours_rejected, sender="volunteers.HoursLog")
def notify_volunteer_on_hours_rejected(sender, instance, rejected_by, **kwargs):
    """
    Notify volunteer their hours submission was not approved. No reason given (PIPEDA).

    Sender: HoursLog
    Signal: hours_rejected

    PIPEDA: rejection_reason is coordinator-internal. It is deliberately excluded
    from this context using .only() (data-minimisation) and must never appear in
    volunteer-facing communications.
    """
    try:
        from apps.notifications.services import send_email_notification
        from apps.volunteers.models import HoursLog

        # .only() enforces PIPEDA data-minimisation: rejection_reason is never
        # loaded into the Python object, eliminating any risk of accidental inclusion.
        log = (
            HoursLog.objects
            .only("pk", "hours", "date", "volunteer_id", "opportunity_id", "status")
            .select_related("volunteer__user", "opportunity")
            .get(pk=instance.pk)
        )
        volunteer = log.volunteer
        recipient = volunteer.user

        opp = log.opportunity
        opp_title = (
            opp.get_title() if (opp and hasattr(opp, "get_title"))
            else (opp.title_en if opp else "")
        )

        try:
            portal_url = settings.SITE_URL + reverse("volunteers:my_hours")
        except Exception:
            portal_url = ""

        context = {
            "recipient": recipient,
            "volunteer_display": volunteer.display_name,
            "hours": str(log.hours),
            "date": str(log.date),
            "opportunity_title": opp_title,
            "portal_url": portal_url,
            # PIPEDA: rejection_reason deliberately omitted
        }
        send_email_notification(
            recipient=recipient,
            subject_key="volunteer_hours_rejected",
            context=context,
        )
        logger.info(
            "volunteers.receivers: hours_rejected notification sent log=%s volunteer=%s",
            log.pk,
            volunteer.pk,
        )
    except Exception as exc:
        logger.error(
            "volunteers.receivers: notify_volunteer_on_hours_rejected "
            "failed for HoursLog #%s: %s",
            getattr(instance, "pk", "?"),
            exc,
            exc_info=True,
        )


@receiver(milestone_achieved, sender="volunteers.RecognitionMilestone")
def notify_volunteer_on_milestone_achieved(sender, instance, volunteer, hours_threshold, **kwargs):
    """
    Celebrate a volunteer's cumulative hours milestone.

    Sender: RecognitionMilestone
    Signal: milestone_achieved

    Sets notification_sent=True BEFORE sending the email (at-most-once semantics)
    to prevent duplicate milestone emails on retry. The milestone flag persists
    even if the email send fails; failures are logged for manual follow-up.
    """
    try:
        from apps.notifications.services import send_email_notification
        from apps.volunteers.models import RecognitionMilestone, VolunteerProfile

        # Accept either a VolunteerProfile instance or a PK integer.
        volunteer_pk = volunteer.pk if hasattr(volunteer, "pk") else volunteer
        volunteer_obj = (
            VolunteerProfile.objects
            .select_related("user")
            .get(pk=volunteer_pk)
        )
        recipient = volunteer_obj.user
        total = volunteer_obj.total_hours_approved

        try:
            portal_url = settings.SITE_URL + reverse("volunteers:my_hours")
        except Exception:
            portal_url = ""

        context = {
            "recipient": recipient,
            "volunteer_display": volunteer_obj.display_name,
            "hours_threshold": str(hours_threshold),
            "total_hours_approved": str(total),
            "portal_url": portal_url,
        }

        # Set flag first (at-most-once) — avoids duplicate milestone emails on retry.
        RecognitionMilestone.objects.filter(pk=instance.pk).update(notification_sent=True)

        try:
            send_email_notification(
                recipient=recipient,
                subject_key="volunteer_milestone_achieved",
                context=context,
            )
        except Exception as exc:
            logger.error(
                "Failed to send milestone email for milestone pk=%s: %s",
                instance.pk, exc, exc_info=True,
            )
            # notification_sent=True already; won't re-send. Logged for manual follow-up.

        logger.info(
            "volunteers.receivers: milestone_achieved notification sent volunteer=%s threshold=%s",
            volunteer_obj.pk,
            hours_threshold,
        )
    except Exception as exc:
        logger.error(
            "volunteers.receivers: notify_volunteer_on_milestone_achieved "
            "failed for milestone #%s: %s",
            getattr(instance, "pk", "?"),
            exc,
            exc_info=True,
        )
