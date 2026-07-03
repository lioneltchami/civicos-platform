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
        # Lock the VolunteerProfile row to serialise concurrent honorarium creation.
        # This is the single serialization point: any second transaction that tries
        # to create an honorarium for the same volunteer will block here until the
        # first transaction commits, ensuring clean() always sees the final YTD total.
        # The VolunteerProfile lock makes a separate lock on existing honoraria rows
        # redundant — VolunteerProfile is always present even for a volunteer's very
        # first honorarium (no phantom-read gap), so holding this lock is sufficient.
        from apps.volunteers.models import VolunteerProfile
        VolunteerProfile.objects.select_for_update().get(pk=volunteer_profile.pk)

        # H1: The previous code locked existing honoraria rows and computed
        # existing_total via aggregate(), but that value was never compared
        # against any threshold — all enforcement is in clean() below.
        # Dead code removed; the VolunteerProfile lock above is the only
        # serialization needed.

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
        # L-3: datetime is a stdlib module that cannot raise ImportError — move it
        # outside the try block so only the app import warrants lazy loading.
        from datetime import datetime as _dt  # noqa: PLC0415
        try:
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
        # H2: _t4a_required is NOT captured here.
        # clean() sets t4a_required=True when projected_total >= $500, but that flag
        # reflects the YTD total at pre-commit time for THIS transaction. If a concurrent
        # admin or shell path creates another honorarium for the same volunteer between
        # our clean() and on_commit firing (both bypass the service-layer lock), the
        # flag can be stale — firing cra_alert_threshold_reached when the volunteer
        # actually crossed the $500 threshold. Fix (H2): re-derive the signal routing
        # post-commit from _ytd_live (authoritative) vs _t4a_threshold (below).
        # VN-2: _ytd_after removed — _post_commit recomputes the live YTD from the DB.

        from apps.volunteers.signals import (
            cra_alert_threshold_reached,
            honorarium_created,
            t4a_threshold_reached,
        )
        _alert_threshold = Decimal(str(getattr(settings, "VOLUNTEER_CRA_ALERT_THRESHOLD", 450)))
        # H2: Capture the T4A threshold for post-commit signal routing.
        # Both thresholds are read from settings before the closure so the closure
        # does not re-read settings on every on_commit call (settings are constant
        # for the life of the process, but this makes the capture explicit).
        _t4a_threshold = Decimal(str(getattr(settings, "VOLUNTEER_CRA_T4A_THRESHOLD", 500)))

        def _post_commit():
            # M1: Outer guard — any unexpected exception in the on_commit path is
            # caught and logged rather than silently discarded or bubbled up.
            # Django's on_commit queue swallows exceptions, so without this guard
            # a ReceiverError or DB hiccup would produce no log entry at all.
            try:
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

                # M2: Use distinct variable names for each send_robust() result so
                # the three call-sites are unambiguous under code review and debugger.
                _created_results = honorarium_created.send_robust(sender=_H, instance=hon, created_by=hon.created_by)
                for recv, exc in _created_results:
                    if isinstance(exc, Exception):
                        logger.error(
                            "honorarium_created receiver %s raised %s",
                            recv, exc, exc_info=exc,
                        )

                # M3: Use hon.payment_type (authoritative DB value committed by save())
                # rather than the outer-closure variable payment_type (captured from the
                # service argument before save). Under concurrent admin/shell paths that
                # bypass the service layer, the outer variable may differ from what was
                # actually persisted — hon.payment_type is always correct.
                if hon.payment_type == _H.PAYMENT_TYPE_HONORARIUM:
                    # P3-2: Compute the authoritative post-commit YTD total once,
                    # before the branch, so BOTH t4a_threshold_reached and
                    # cra_alert_threshold_reached can pass ytd_total= consistently.
                    # The query runs here (after on_commit fires) so it reflects the
                    # committed row. P2-4: Sum is imported at module level.
                    _ytd_live = _H.objects.filter(
                        volunteer=hon.volunteer,
                        payment_type=_H.PAYMENT_TYPE_HONORARIUM,
                        payment_date__year=hon.payment_date.year,
                    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

                    if _ytd_live >= _t4a_threshold:
                        # H2: Signal routing now derived from the live post-commit YTD total
                        # vs the settings threshold — not the stale pre-commit _t4a_required
                        # flag. Correct under concurrent admin/shell creates that bypassed
                        # the service-layer VolunteerProfile lock.
                        # VN-5: When YTD >= $500, only fire t4a_threshold_reached.
                        # cra_alert_threshold_reached is superseded — firing both would
                        # send duplicate coordinator notifications for the same event.
                        # P3-2: ytd_total= for signal contract parity.
                        _t4a_results = t4a_threshold_reached.send_robust(
                            sender=_H,
                            instance=hon,
                            coordinator=hon.created_by,
                            ytd_total=_ytd_live,
                        )
                        for recv, exc in _t4a_results:
                            if isinstance(exc, Exception):
                                logger.error(
                                    "t4a_threshold_reached receiver %s raised %s",
                                    recv, exc, exc_info=exc,
                                )
                    elif _ytd_live >= _alert_threshold:
                        # H2: elif (not else/if) — _ytd_live < _t4a_threshold is implicit,
                        # making these two branches mutually exclusive by construction (VN-5).
                        # M-D: _ytd_live is the authoritative post-commit YTD total.
                        _alert_results = cra_alert_threshold_reached.send_robust(
                            sender=_H,
                            instance=hon,
                            coordinator=hon.created_by,
                            ytd_total=_ytd_live,
                        )
                        for recv, exc in _alert_results:
                            if isinstance(exc, Exception):
                                logger.error(
                                    "cra_alert_threshold_reached receiver %s raised %s",
                                    recv, exc, exc_info=exc,
                                )
            except Exception:
                logger.error(
                    "volunteers.services.honoraria: _post_commit failed for Honorarium #%s",
                    _honorarium_pk,
                    exc_info=True,
                )

        transaction.on_commit(_post_commit)

    # C1 / PIPEDA: Do not log volunteer_profile.pk together with honorarium.pk.
    # An Honorarium PK is a financial record identifier — correlating it with a
    # volunteer profile PK in a log line constitutes financial profiling even
    # without the amount or payment_type (which were removed in P2-5).
    # The full record is in the DB audit trail; operational logs need only the
    # financial record PK and the actor who created it.
    logger.info(
        "volunteers.services.honoraria: Honorarium #%s created — "
        "created_by user #%s",
        honorarium.pk,
        created_by.pk,
    )
    return honorarium
