"""
Volunteer Management BB — Honorarium service layer.

CRA PC-025 Compliance Rules
============================
Canadian Revenue Agency (CRA) protocol PC-025 governs the tax reporting
obligations for volunteer honoraria:

  - $450 CAD YTD (VOLUNTEER_CRA_ALERT_THRESHOLD): Non-blocking advisory.
    Coordinator receives an alert that the volunteer is approaching the
    mandatory T4A reporting threshold. No action is required at this stage,
    but advance notice allows coordinators to plan ahead.

  - $500 CAD YTD (VOLUNTEER_CRA_T4A_THRESHOLD): T4A slip required.
    When cumulative honoraria (excluding expense reimbursements) in a
    calendar year meet or exceed this amount, the ``t4a_required`` flag is
    automatically set on the Honorarium record by ``Model.clean()``.
    A T4A information return must be filed with the CRA.

  - $1,000 CAD YTD (VOLUNTEER_CRA_HARD_BLOCK): Hard block.
    ``Model.clean()`` raises ``ValidationError`` preventing the record from
    being saved if this limit would be exceeded. This is a system-enforced
    ceiling that requires coordinator-level override (not implemented here).

Only ``PAYMENT_TYPE_HONORARIUM`` rows count toward CRA thresholds.
Expense reimbursements (``PAYMENT_TYPE_EXPENSE``) are excluded.

PIPEDA note: volunteers are referenced by profile PK only in log output.
"""
import logging
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Sum

logger = logging.getLogger(__name__)


def cumulative_ytd(volunteer_profile, *, year: int) -> Decimal:
    """
    Return the sum of all honorarium (not expense reimbursement) payments
    for ``volunteer_profile`` in the given calendar ``year``.

    Expense reimbursements are excluded because they do not count toward
    CRA T4A reporting thresholds under PC-025.

    Args:
        volunteer_profile: A ``VolunteerProfile`` instance.
        year: The calendar year (integer, e.g. 2026).

    Returns:
        ``Decimal`` total in the volunteer's payment currency; ``Decimal("0")``
        when no honorarium records exist for that year.
    """
    from apps.volunteers.models import Honorarium

    total = (
        Honorarium.objects
        .filter(
            volunteer=volunteer_profile,
            payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
            payment_date__year=year,
        )
        .aggregate(total=Sum("amount"))["total"]
    )
    return total or Decimal("0")


def create_honorarium(
    *,
    volunteer_profile,
    payment_type: str,
    amount: Decimal,
    description: str,
    payment_date,
    created_by,
):
    """
    Create an ``Honorarium`` record with full CRA threshold enforcement.

    Enforces CRA PC-025 rules via ``Model.clean()`` (hard block at $1,000 YTD)
    and emits post-commit signals to notify coordinators when thresholds are
    crossed.

    A row-level lock (``select_for_update``) is acquired on existing honoraria
    for the same volunteer and year before the new record is validated. This
    prevents two concurrent coordinators from simultaneously passing the $1,000
    hard block by serialising access at the DB level.

    Args:
        volunteer_profile: ``VolunteerProfile`` instance for the payee.
        payment_type: One of ``Honorarium.PAYMENT_TYPE_CHOICES[*][0]``.
        amount: ``Decimal`` payment amount (positive).
        description: Short description of the payment (max 300 chars).
        payment_date: ``datetime.date`` of the payment.
        created_by: ``User`` instance initiating the action.

    Returns:
        The saved ``Honorarium`` instance.

    Raises:
        PermissionDenied: If ``created_by`` lacks ``volunteers.add_honorarium``.
        ValueError: If ``payment_type`` is not a recognised choice.
        ValidationError: If CRA hard block ($1,000 YTD) would be exceeded,
            or any other model-level validation fails.
    """
    from django.utils.translation import gettext_lazy as _

    if not created_by.has_perm("volunteers.add_honorarium"):
        raise PermissionDenied(_("You do not have permission to create honoraria."))

    from apps.volunteers.models import Honorarium
    valid_types = {c[0] for c in Honorarium.PAYMENT_TYPE_CHOICES}
    if payment_type not in valid_types:
        raise ValueError(f"Invalid payment_type '{payment_type}'")

    with transaction.atomic():
        # Lock volunteer profile row to serialize concurrent honorarium creation.
        # When two transactions concurrently create the *first* honorarium for a
        # volunteer, the existing-honorarium select_for_update() locks no rows
        # (empty queryset) and both proceed simultaneously — a phantom-read race.
        # Locking the VolunteerProfile row here forces serialization regardless of
        # whether any honorarium rows exist yet.
        from apps.volunteers.models import VolunteerProfile
        VolunteerProfile.objects.select_for_update().get(pk=volunteer_profile.pk)

        # Acquire row-level lock on all existing honoraria for this volunteer+year
        # to serialise concurrent creates. This prevents a race condition where two
        # coordinators simultaneously read the YTD total, both see it under the
        # $1,000 hard block, and both proceed — collectively exceeding the limit.
        # Model.clean() also enforces this guard, but we lock here first for
        # service-level clarity and defence in depth.
        _qs = Honorarium.objects.select_for_update().filter(
            volunteer=volunteer_profile,
            payment_date__year=payment_date.year,
            payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
        )
        existing_total = _qs.aggregate(total=Sum("amount"))["total"] or Decimal("0")

        # Build the instance and run full model validation (CRA rules live here).
        honorarium = Honorarium(
            volunteer=volunteer_profile,
            payment_type=payment_type,
            amount=amount,
            description=description,
            payment_date=payment_date,
            created_by=created_by,
        )
        honorarium.full_clean()  # raises ValidationError on hard-block violation

        # skip_clean=True because full_clean() was already called above.
        honorarium.save(skip_clean=True)

        # Capture post-clean state in local variables for use inside the closure.
        # We must not close over the ORM instance directly — it may be mutated
        # by the time on_commit fires.
        _honorarium_pk = honorarium.pk
        _t4a_required = honorarium.t4a_required
        _ytd_after = existing_total + honorarium.amount

        from apps.volunteers.signals import (
            cra_alert_threshold_reached,
            honorarium_created,
            t4a_threshold_reached,
        )
        _alert_threshold = Decimal(str(getattr(settings, "VOLUNTEER_CRA_ALERT_THRESHOLD", 450)))

        def _post_commit():
            from apps.volunteers.models import Honorarium as _H
            try:
                hon = _H.objects.select_related("volunteer", "created_by").get(pk=_honorarium_pk)
            except _H.DoesNotExist:
                logger.warning(
                    "volunteers.services.honoraria: Honorarium #%s not found in "
                    "on_commit callback — skipping signal dispatch.",
                    _honorarium_pk,
                )
                return

            results = honorarium_created.send_robust(sender=_H, instance=hon, created_by=hon.created_by)
            for recv, exc in results:
                if isinstance(exc, Exception):
                    logger.error(
                        "honorarium_created receiver %s raised %s",
                        recv, exc, exc_info=exc,
                    )

            if payment_type == _H.PAYMENT_TYPE_HONORARIUM:
                if _t4a_required:
                    results = t4a_threshold_reached.send_robust(
                        sender=_H, instance=hon, coordinator=hon.created_by
                    )
                    for recv, exc in results:
                        if isinstance(exc, Exception):
                            logger.error(
                                "t4a_threshold_reached receiver %s raised %s",
                                recv, exc, exc_info=exc,
                            )
                elif _ytd_after >= _alert_threshold:
                    results = cra_alert_threshold_reached.send_robust(
                        sender=_H,
                        instance=hon,
                        coordinator=hon.created_by,
                        ytd_total=_ytd_after,
                    )
                    for recv, exc in results:
                        if isinstance(exc, Exception):
                            logger.error(
                                "cra_alert_threshold_reached receiver %s raised %s",
                                recv, exc, exc_info=exc,
                            )

        transaction.on_commit(_post_commit)

    logger.info(
        "volunteers.services.honoraria: Honorarium #%s created — "
        "payment_type=%s, amount=%s, volunteer profile #%s, created_by user #%s",
        honorarium.pk,
        payment_type,
        amount,
        volunteer_profile.pk,
        created_by.pk,
    )
    return honorarium
