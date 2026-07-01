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

from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone

# Signal objects — safe to import at module level because signals.py only
# imports django.dispatch.Signal and has no model references.
from apps.volunteers.signals import (
    application_approved,
    application_rejected,
    application_submitted,
    screening_expiring,
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

        # Coordinator is on Program, not directly on Opportunity.
        # opportunity.program is accessible via FK (lazy-loaded here; batch
        # callers should select_related("opportunity__program")).
        coordinator = instance.opportunity.program.coordinator
        if coordinator is None:
            logger.warning(
                "volunteers.receivers: application #%s submitted for opportunity #%s "
                "but program coordinator is not set — skipping coordinator notification.",
                instance.pk,
                instance.opportunity_id,
            )
            return

        send_email_notification(
            recipient=coordinator,
            subject_key="volunteer_application_received",
            context={
                "opportunity_title": instance.opportunity.get_title(),
                "application_pk": instance.pk,
                # PIPEDA: volunteer identified by profile PK only — never name or email.
                "volunteer_display": f"Applicant #{instance.volunteer_id}",
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

        volunteer_user = instance.volunteer.user

        # Build an absolute portal URL path. The notification template is
        # responsible for prepending the scheme + domain. If the URL conf
        # for volunteers isn't wired yet, we degrade gracefully to "".
        try:
            portal_url = reverse(
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
        coordinator = None
        if instance.opportunity_id:
            # opportunity and opportunity.program are select_related by
            # check_expiring_soon() queryset, so these accesses don't hit the DB.
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
