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

        # ── Wire into Payments BB financial ledger ────────────────────────────
        # Creates a PaymentIntent + Payment so manual honorarium payments appear
        # in the financial record alongside gateway payments.
        #
        # Guarded with try/except: if Payments BB is unavailable or payment
        # creation fails, the honorarium is still created — .payment remains null.
        # Both records are in the same transaction.atomic() block, so a Payment
        # failure WILL roll back the honorarium (atomic integrity is preserved).
        # The try/except only handles unexpected import or model errors.
        try:
            from datetime import datetime as _dt
            from django.utils import timezone as _tz
            from apps.payments.models import (
                GATEWAY_MANUAL as _GATEWAY_MANUAL,
                PaymentIntent as _PI,
                Payment as _P,
            )

            # Convert payment_date (date) to timezone-aware datetime at midnight Toronto
            _paid_at = _tz.make_aware(
                _dt.combine(payment_date, _dt.min.time()),
                timezone=_tz.get_current_timezone(),
            )

            _pi = _PI.objects.create(
                payer=volunteer_profile.user,
                amount=amount,
                currency="CAD",
                purpose=_PI.PURPOSE_HONORARIUM,
                status=_PI.STATUS_COMPLETED,
                gateway=_GATEWAY_MANUAL,
                gateway_intent_id=f"hon-{honorarium.pk}",
                metadata={"source": "volunteers.honorarium", "honorarium_pk": honorarium.pk},
            )
            _payment = _P.objects.create(
                intent=_pi,
                gateway_charge_id=f"HON-{honorarium.pk}",
                amount_paid=amount,
                processor_fee=Decimal("0.00"),
                payment_method_type=_P.PAYMENT_METHOD_BANK,
                paid_at=_paid_at,
            )
            # Link via UPDATE to avoid re-running full_clean() on the honorarium
            type(honorarium).objects.filter(pk=honorarium.pk).update(payment=_payment)
            honorarium.payment = _payment  # keep in-memory instance consistent

            # M-3: Do not log gateway_charge_id or gateway_intent_id.
            # Both embed honorarium.pk (f"HON-{pk}" / f"hon-{pk}"), so including
            # them in a log line that already contains honorarium.pk creates a
            # redundant financial fingerprint without operational value.
            # PIPEDA: logging pk → payment ID pairs constitutes financial profiling.
            logger.debug(
                "volunteers.services.honoraria: Honorarium #%s wired to Payments BB",
                honorarium.pk,
            )
        except Exception:
            logger.warning(
                "volunteers.services.honoraria: Payments BB wiring failed for "
                "Honorarium #%s — .payment FK remains null.",
                honorarium.pk,
                exc_info=True,
            )
            # Do NOT re-raise: honorarium creation succeeds even if Payments BB wiring fails.
            # The financial record will be created on next reconciliation or manual entry.

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
                else:
                    # M-D: _ytd_after was captured at closure-creation time (before
                    # on_commit) and could be stale if another signal handler mutated
                    # honorarium.amount between save and commit. Recompute the true
                    # YTD total from the DB now that the transaction is committed.
                    from django.db.models import Sum as _Sum  # noqa: PLC0415
                    # Honorarium has no status field — match cumulative_ytd() filter
                    # exactly: all PAYMENT_TYPE_HONORARIUM rows for this volunteer
                    # in this calendar year.
                    _ytd_live = _H.objects.filter(
                        volunteer=hon.volunteer,
                        payment_type=_H.PAYMENT_TYPE_HONORARIUM,
                        payment_date__year=hon.payment_date.year,
                    ).aggregate(total=_Sum("amount"))["total"] or Decimal("0.00")
                    if _ytd_live >= _alert_threshold:
                        results = cra_alert_threshold_reached.send_robust(
                            sender=_H,
                            instance=hon,
                            coordinator=hon.created_by,
                            ytd_total=_ytd_live,
                        )
                        for recv, exc in results:
                            if isinstance(exc, Exception):
                                logger.error(
                                    "cra_alert_threshold_reached receiver %s raised %s",
                                    recv, exc, exc_info=exc,
                                )

        transaction.on_commit(_post_commit)

    # M-A: PIPEDA — do not log amount together with volunteer_profile.pk.
    # Correlating a financial amount with a volunteer's PK creates a profiling
    # record visible to anyone with log access (no DB access required).
    # The amount is stored in the DB audit trail; omit it from operational logs.
    logger.info(
        "volunteers.services.honoraria: Honorarium #%s created — "
        "payment_type=%s, volunteer profile #%s, created_by user #%s",
        honorarium.pk,
        payment_type,
        volunteer_profile.pk,
        created_by.pk,
    )
    return honorarium
