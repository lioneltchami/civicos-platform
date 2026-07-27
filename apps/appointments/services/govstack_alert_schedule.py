"""
GovStack Scheduler BB — AlertSchedule service layer.

AlertSchedule = GovStack concept for scheduling a push notification (a
Message) to be dispatched to an Event's (Slot's) participants at a specific
future datetime. CivicOS backing model: GovStackAlertSchedule (Wave A).

CRUD operations:
  alert_schedule_create  → GovStackAlertSchedule.objects.create(...). Does
                            NOT touch Celery — the view layer enqueues the
                            dispatch task via transaction.on_commit() AFTER
                            this function returns and the row is committed
                            (see govstack_views.AlertScheduleNewView and the
                            module docstring in apps.appointments.tasks for
                            the full on_commit + celery_task_id write-back
                            sequencing rationale). Keeping Celery dispatch
                            out of this function keeps it synchronous and
                            unit-testable without a broker, matching the
                            payments-BB precedent (BulkPaymentView /
                            PrepaymentValidationView in
                            apps/payments/govstack_views.py).
  alert_schedule_modify   → partial update of event_id/target_category/
                            message_id/alert_datetime. Returns
                            (alert_schedule, reschedule_needed, old_task_id)
                            so the view layer can revoke the old Celery task
                            and enqueue a replacement via on_commit() when
                            reschedule_needed is True — see docstring below.
  alert_schedule_delete   → hard delete. GovStackAlertSchedule.delete() is
                            OVERRIDDEN on the model (see models.py) to revoke
                            celery_task_id before deleting the row — this
                            service function does not need to duplicate that
                            logic.
  alert_schedule_list     → filter + shape into
                            {"alert_schedule_id": ..., "details": {...}}.
                            entity_id is a DERIVED field (GovStackAlertSchedule
                            has no stored entity/entity_id column) — resolved
                            via alert_schedule.slot.location.organization_id,
                            the identical derivation already used for Event's
                            host_entity_id in
                            services.govstack_event._slot_to_event_dict().
                            select_related("slot__location") avoids N+1
                            queries across the result set.

Not-modelled fields:
  The real spec's alert_schedule_details_required has no flag for
  target_category or event_id, so this module's response shape only exposes
  the four documented flags (alert_schedule_id, entity_id, message_id,
  alert_datetime) plus target_category unconditionally (no flag exists to
  suppress it) — see alert_schedule_list()'s docstring for the full rationale.

PIPEDA note:
  No PII is logged at any level. Log statements use PKs only. message_body
  (owned by the referenced GovStackMessage, not this model) is never logged
  from this module.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_naive

from apps.appointments.models import GovStackAlertSchedule, GovStackMessage, Slot

logger = logging.getLogger("civicos.appointments.services.govstack_alert_schedule")

_LIST_PAGE_CAP = 500

# target_category: "" means "all participant categories" (subscribers AND
# resources); "subscriber" / "resource" narrow to one category. Matches
# GovStackAlertSchedule.target_category's help_text.
_VALID_TARGET_CATEGORIES: frozenset[str] = frozenset({"", "subscriber", "resource"})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate_target_category(target_category: str) -> None:
    """Raise ValueError if target_category is not '', 'subscriber', or 'resource'."""
    if target_category not in _VALID_TARGET_CATEGORIES:
        raise ValueError(
            f"Invalid target_category {target_category!r}. "
            "Must be one of: 'subscriber', 'resource', or blank (all)."
        )


def _parse_datetime_str(value: str):
    """
    Parse an ISO 8601 datetime string, requiring timezone awareness.

    Duplicated (rather than imported) from the near-identical helpers in
    services.govstack_event / services.govstack_appointment — this codebase's
    established convention is to keep small private helpers local to each
    service module rather than importing an underscore-prefixed function
    across a module boundary (see _unlock_slot's docstring in
    services.govstack_appointment for the precedent/rationale).

    Raises ValueError for unparseable or naive (no tz) strings.
    """
    if not value:
        raise ValueError("Datetime string must not be empty.")
    dt = parse_datetime(value)
    if dt is None:
        raise ValueError(f"Cannot parse datetime string: {value!r}")
    if is_naive(dt):
        raise ValueError(
            f"Datetime must include a timezone offset (e.g. '2026-08-01T09:00:00Z'): {value!r}"
        )
    return dt


def _validate_future_datetime(dt) -> None:
    """Raise ValueError if dt is not strictly in the future (relative to now)."""
    if dt <= timezone.now():
        raise ValueError("alert_datetime must be in the future.")


def _resolve_message(message_id: str | int) -> GovStackMessage:
    """
    Resolve message_id to a GovStackMessage.

    A blank or non-integer message_id is deliberately folded into the same
    GovStackMessage.DoesNotExist outcome as a genuinely missing PK (rather
    than letting Django's ORM raise a raw ValueError for a malformed pk
    lookup) — the caller (view layer) maps both to a single, static 404
    MESSAGE_NOT_FOUND response. Mirrors
    services.govstack_message._resolve_entity's identical rationale.
    """
    try:
        msg_pk = int(message_id)
    except (ValueError, TypeError) as exc:
        raise GovStackMessage.DoesNotExist("message_id does not reference a known message.") from exc
    return GovStackMessage.objects.get(pk=msg_pk)


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

def alert_schedule_create(
    event_id: str,
    message_id: str | int,
    target_category: str = "",
    alert_datetime: str = "",
) -> GovStackAlertSchedule:
    """
    Create a new GovStackAlertSchedule.

    Raises:
      Slot.DoesNotExist / django.core.exceptions.ValidationError
        — event_id missing, not found, or not a valid UUID.
      GovStackMessage.DoesNotExist
        — message_id missing or not found.
      ValueError
        — invalid target_category; alert_datetime missing, unparseable,
          timezone-naive, or not strictly in the future.
    """
    _validate_target_category(target_category or "")

    slot = Slot.objects.select_related("location").get(pk=event_id)
    message = _resolve_message(message_id)

    alert_dt = _parse_datetime_str(alert_datetime)
    _validate_future_datetime(alert_dt)

    alert_schedule = GovStackAlertSchedule.objects.create(
        slot=slot,
        message=message,
        target_category=target_category or "",
        alert_datetime=alert_dt,
    )
    logger.debug(
        "alert_schedule_create: created alert_schedule pk=%s event_id=%s message_id=%s",
        alert_schedule.pk, event_id, message_id,
    )
    return alert_schedule


def alert_schedule_modify(
    alert_schedule_id: str | int,
    event_id: str | None = None,
    target_category: str | None = None,
    message_id: str | int | None = None,
    alert_datetime: str | None = None,
) -> tuple[GovStackAlertSchedule, bool, str]:
    """
    Partially update an existing GovStackAlertSchedule.

    Only fields explicitly supplied (not None) are changed.

    Celery reschedule semantics
    ────────────────────────────
    - alert_datetime and/or message_id change AND dispatched=False:
        the caller (view) must revoke the OLD celery_task_id and enqueue a
        fresh dispatch task for the (possibly unchanged) alert_datetime.
        Even a message_id-only change re-enqueues a new task (the ETA is
        unchanged in that case) — a deliberate, spec-directed choice: the
        dispatch task itself always re-reads slot/message fresh from the DB
        at fire time, so this is not strictly required for correctness, but
        it keeps celery_task_id an accurate, current handle for the row at
        all times, which matters for the DELETE-time revoke path.
    - dispatched=True AND alert_datetime changes to a new future value
        ("re-arming"): dispatched is reset to False and a fresh dispatch is
        scheduled. This is a documented CivicOS convenience, not literally
        specified by GovStack, but a sensible interpretation of "you moved
        the alert to a new time, so it should fire again."
    - target_category-only changes (no alert_datetime/message_id change)
        never touch the Celery task.

    event_id changes (re-targeting which Slot's participants receive the
    alert) never require a Celery reschedule — the dispatch task always
    resolves participants from alert_schedule.slot fresh at fire time, so
    updating the FK here is sufficient.

    Locking — select_for_update() around the read-modify-write
    ─────────────────────────────────────────────────────────────
    The row is read with select_for_update() inside transaction.atomic(),
    matching the precedent in services.govstack_affiliation.affiliation_create
    and services.govstack_event.event_modify/event_delete. Without this, two
    concurrent PUT /alert_schedule/modifications calls on the same row could
    both read the same stale celery_task_id, both cause the view layer to
    revoke that task and enqueue a replacement, and race unlocked on the
    final celery_task_id write-back — the loser's newly-enqueued task would
    never be recorded on the row (neither revocable nor trackable) yet would
    still fire at its ETA, producing a stale/duplicate alert. The lock is
    held only for the duration of this function's DB read-modify-write, NOT
    across the actual Celery revoke/enqueue calls — those are network/broker
    calls made by the view layer via transaction.on_commit() AFTER this
    function returns and its transaction has committed, consistent with this
    module's (and apps.appointments.tasks.dispatch_alert_schedule's) "never
    hold a DB row lock across an outbound call" discipline.

    Returns:
      (alert_schedule, reschedule_needed, old_task_id)
        reschedule_needed — True if the view must revoke old_task_id (if
                             non-blank) and enqueue+persist a new Celery ETA
                             task for alert_schedule.alert_datetime.
        old_task_id        — the celery_task_id value BEFORE this call (for
                              revocation); "" if none was ever scheduled.

    Raises:
      GovStackAlertSchedule.DoesNotExist — no row with alert_schedule_id.
      Slot.DoesNotExist / DjangoValidationError — event_id supplied but invalid.
      GovStackMessage.DoesNotExist — message_id supplied but invalid.
      ValueError — invalid target_category; alert_datetime unparseable,
                   naive, or not in the future.
    """
    with transaction.atomic():
        alert_schedule = (
            GovStackAlertSchedule.objects.select_for_update()
            .select_related("slot", "message")
            .get(pk=alert_schedule_id)
        )

        old_task_id = alert_schedule.celery_task_id
        update_fields: list[str] = []
        alert_datetime_changed = False
        message_id_changed = False

        if event_id is not None:
            slot = Slot.objects.get(pk=event_id)
            if slot.pk != alert_schedule.slot_id:
                alert_schedule.slot = slot
                update_fields.append("slot")

        if target_category is not None:
            _validate_target_category(target_category)
            alert_schedule.target_category = target_category
            update_fields.append("target_category")

        if message_id is not None:
            message = _resolve_message(message_id)
            if message.pk != alert_schedule.message_id:
                alert_schedule.message = message
                update_fields.append("message")
                message_id_changed = True

        if alert_datetime is not None:
            new_dt = _parse_datetime_str(alert_datetime)
            _validate_future_datetime(new_dt)
            if new_dt != alert_schedule.alert_datetime:
                alert_schedule.alert_datetime = new_dt
                update_fields.append("alert_datetime")
                alert_datetime_changed = True

        reschedule_needed = False
        if alert_schedule.dispatched:
            # Re-arming: only a genuine alert_datetime change revives an
            # already-fired schedule. A message_id-only change on an already
            # dispatched row does NOT re-arm it (that would silently resurrect
            # alerts the operator believed were done).
            if alert_datetime_changed:
                alert_schedule.dispatched = False
                update_fields.append("dispatched")
                reschedule_needed = True
        else:
            if alert_datetime_changed or message_id_changed:
                reschedule_needed = True

        if update_fields:
            update_fields.append("updated_at")
            alert_schedule.save(update_fields=update_fields)
            logger.debug(
                "alert_schedule_modify: updated alert_schedule pk=%s fields=%r",
                alert_schedule.pk, update_fields,
            )
        else:
            logger.debug(
                "alert_schedule_modify: no fields changed for alert_schedule pk=%s",
                alert_schedule.pk,
            )

    return alert_schedule, reschedule_needed, old_task_id


def alert_schedule_delete(alert_schedule_id: str | int) -> None:
    """
    Hard-delete the alert schedule.

    GovStackAlertSchedule.delete() is overridden on the model to revoke
    celery_task_id (best-effort, non-fatal) before the row is removed — see
    models.py's GovStackAlertSchedule.delete(). This function does not
    duplicate that logic.

    Raises GovStackAlertSchedule.DoesNotExist if no row with alert_schedule_id exists.
    """
    alert_schedule = GovStackAlertSchedule.objects.get(pk=alert_schedule_id)
    alert_schedule.delete()
    logger.debug("alert_schedule_delete: hard-deleted alert_schedule pk=%s", alert_schedule_id)


def alert_schedule_list(
    alert_schedule_filter: dict | None = None,
    alert_schedule_details_required: dict | None = None,
) -> list[dict]:
    """
    Return a GovStack AlertSchedule list, capped at 500 results, ordered by
    alert_datetime — matches GovStackAlertSchedule.Meta.ordering.

    alert_schedule_filter keys
    ────────────────────────────
    alert_schedule_id — exact match on PK
    entity_id         — exact match against the DERIVED owning Organization
                         (slot.location.organization_id)
    target_category   — exact match
    message_id        — exact match on message FK
    from / to         — filter on alert_datetime range (caller passes the
                         real spec's "from"/"to" keys; the view layer remaps
                         them from the wire-format "from" key to "from_" via
                         AlertScheduleFilterSerializer and remaps back to
                         "from" before calling this function — identical
                         convention to appointment_list/event_list)

    alert_schedule_details_required keys (all booleans; defaults noted)
    ──────────────────────────────────────────────────────────────────────
    alert_schedule_id (True), entity_id (True), message_id (True),
    alert_datetime (True)

    Response shape: [{"alert_schedule_id": "<pk>", "details": {...}}, ...]
    "details" always additionally includes target_category (no
    alert_schedule_details_required flag exists for it in the real spec —
    see module docstring "Not-modelled fields"). event_id is NOT included in
    "details" — there is likewise no flag for it, and the given spec text
    for this endpoint documents only the four fields above; a caller that
    needs to know which event an alert targets should use
    GET /event/list_details with the event_id echoed by GET
    /alert_schedule/list_details's sibling POST /alert_schedule/new response
    at creation time, or track it externally.
    """
    qs = GovStackAlertSchedule.objects.select_related("slot", "slot__location", "message")

    filter_data = alert_schedule_filter or {}

    # Finding #4 fix: alert_schedule_id and message_id are array-typed in the
    # real spec (alert_schedule_id[], message_id[]). AlertScheduleFilterSerializer's
    # StringOrListField always normalizes these to a list (even a single-value
    # caller becomes a 1-element list), so pk__in=/message_id__in= is always
    # the correct application — no special-casing needed for the old
    # single-value shape.
    if filter_data.get("alert_schedule_id"):
        qs = qs.filter(pk__in=filter_data["alert_schedule_id"])

    if filter_data.get("target_category"):
        qs = qs.filter(target_category=filter_data["target_category"])

    if filter_data.get("message_id"):
        qs = qs.filter(message_id__in=filter_data["message_id"])

    if filter_data.get("entity_id"):
        qs = qs.filter(slot__location__organization_id=filter_data["entity_id"])

    if filter_data.get("from"):
        dt = _parse_datetime_str(filter_data["from"])
        qs = qs.filter(alert_datetime__gte=dt)

    if filter_data.get("to"):
        dt = _parse_datetime_str(filter_data["to"])
        qs = qs.filter(alert_datetime__lte=dt)

    required = alert_schedule_details_required or {}
    results: list[dict] = []

    for alert_schedule in qs.order_by("alert_datetime")[:_LIST_PAGE_CAP]:
        item: dict = {"alert_schedule_id": str(alert_schedule.pk)}
        details: dict = {}

        if required.get("alert_schedule_id", True):
            details["alert_schedule_id"] = str(alert_schedule.pk)

        if required.get("entity_id", True):
            org_id = alert_schedule.slot.location.organization_id if alert_schedule.slot_id else None
            details["entity_id"] = str(org_id) if org_id else ""

        if required.get("message_id", True):
            details["message_id"] = str(alert_schedule.message_id)

        if required.get("alert_datetime", True):
            details["alert_datetime"] = alert_schedule.alert_datetime.isoformat()

        # No details_required flag exists for target_category — always included.
        details["target_category"] = alert_schedule.target_category

        item["details"] = details
        results.append(item)

    return results
