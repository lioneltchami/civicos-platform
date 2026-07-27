"""
GovStack Scheduler BB — Appointment service layer.

Appointment = GovStack concept for a citizen's reservation against one or more
Events. CivicOS backing model: Booking (one Booking per GovStack Event/Slot
that the appointment spans).

This module is a THIN adapter over the existing, fully-tested Appointments BB
booking lifecycle service at apps.appointments.services.booking — it does NOT
reimplement booking policy (suspension checks, frequency windows, capacity,
reschedule limits, audit logging, signal dispatch, etc). All of that is
delegated to booking.py's create_booking() / confirm_booking() /
cancel_booking() / reschedule_booking() / reject_booking() / complete_booking().

create_ vs modify_ shape conflation (IMPORTANT for serializer authors)
───────────────────────────────────────────────────────────────────────
The real GovStack OpenAPI spec uses TWO DIFFERENT schemas for the Appointment
API, not one:

  appointment_creation_details (POST /appointment/new body):
      exclusive, event_ids (PLURAL — an appointment can span multiple
      events/slots), participant_type, participant_id, participant_entity_id

  appointment_details (PUT /appointment/modifications body, and each item's
  ``details`` in the GET list response):
      exclusive, event_id (SINGULAR), participant_type, participant_id,
      status_id, participant_entity_id

An existing but never-reviewed Wave-A-era ``AppointmentDetailsSerializer`` in
govstack_serializers.py currently reuses ONE serializer for both create and
modify, with a singular ``event_id`` and no ``event_ids`` array — this is the
exact same create/modify shape-conflation bug that the Wave D review found
and fixed for the Event API (see the module docstring in govstack_event.py
for that postmortem). This module's function signatures deliberately match
the REAL spec shapes (``event_ids: list[str]`` on appointment_create,
singular ``event_id: str | None`` on appointment_modify) so that whoever
fixes the serializers next has an unambiguous target to match.

"exclusive" semantics
──────────────────────
GovStack's ``exclusive`` flag blocks the underlying Slot from further
bookings. This is implemented as Slot.status = "blocked" — NOT a mutation of
Slot.capacity, which would be destructive and irreversible. A "blocked" slot
is correctly rejected by create_booking()/reschedule_booking() in booking.py,
since both gate on ``slot.status not in ("available", "partial")``. Toggling
``exclusive`` back to False recomputes the slot status from
spaces_used/capacity (see _unlock_slot(), which reimplements the same 3-line
logic as booking.py's private _update_slot_status() locally, rather than
reaching across a module boundary to a private helper).

Actor resolution (see also the design note reproduced in each function's
docstring below) — booking.py's confirm_booking() / reschedule_booking() /
reject_booking() / mark_no_show() call ``actor.pk`` directly in their final
post-commit log line and are NOT None-safe; passing actor=None to any of
those four crashes AFTER the DB write has already committed. This module
never does that. cancel_booking() and complete_booking() ARE None-safe.

participant_type / participant_id
──────────────────────────────────
CivicOS Booking.citizen is a FK to AUTH_USER_MODEL with
limit_choices_to={"is_staff": False}. The GovStack spec's only documented
participant_type is "subscriber" — no other participant type is supported by
this platform for appointment booking. participant_id resolves to
User.pk (matches the Wave C subscriber_id = User.pk design decision — a
GovStackSubscriberProfile row is optional metadata, not a booking-eligibility
gate).

PIPEDA note: no PII (name, email) appears in any log statement or error
message raised by this module. Log statements and ValueError messages use
PKs only; participant_entity_id validation errors use a static message (no
raw input echoed back).
"""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_naive

from apps.appointments.models import Booking, Organization, Slot
from apps.appointments.services.booking import (
    cancel_booking,
    complete_booking,
    confirm_booking,
    create_booking,
    reject_booking,
    reschedule_booking,
)

logger = logging.getLogger("civicos.appointments.services.govstack_appointment")


class AppointmentOwnershipError(Exception):
    """
    Raised when a citizen-authenticated (JWT-bound) GovStack caller attempts to
    act on a participant_id / appointment that is not their own. Maps to HTTP 403
    at the view layer. Never include the mismatched IDs in the exception message
    or logs (PIPEDA — no cross-citizen identifier correlation in logs).
    """


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SUPPORTED_PARTICIPANT_TYPE = "subscriber"

# status_id (GovStack) -> booking.py transition function name, dispatched in
# appointment_modify(). Kept as a plain set here for up-front validation;
# the actual dispatch is an explicit if/elif chain (see appointment_modify)
# so each branch can apply its own actor-resolution rule from the design.
_SUPPORTED_STATUS_TRANSITIONS = frozenset({"confirmed", "rejected", "cancelled", "completed"})

_GOVSTACK_ORGANIZER_EMAIL = "govstack-system-organizer@civicos.internal"

_LIST_PAGE_CAP = 500


# ---------------------------------------------------------------------------
# Internal helpers — system seed actor
# ---------------------------------------------------------------------------


def _get_or_create_govstack_organizer_actor():
    """
    Lazily seed a system User (is_staff=True) to serve as the audit-trail
    actor for GovStack-driven staff/organizer actions (confirm, reject)
    where no specific CivicOS staff identity is resolvable from the
    GovStack requestor_id (see the platform-wide actor-role-resolution
    gap noted in the Wave D review — this is the same class of problem,
    solved locally the same way Wave D solved it for Slot.staff).

    Concurrency (FIX 6, Wave E adversarial review): this is called inside
    the outer transaction.atomic() block in appointment_modify(). On the
    very first concurrent confirmed/rejected calls — before this system
    User row exists — two transactions can both pass get_or_create()'s
    SELECT and then race on the INSERT, and the loser hits IntegrityError
    on the unique email constraint. Caught below and resolved with a plain
    re-fetch, in the same defensive try/except IntegrityError style already
    used by this service layer's other system-seed/creation helpers (see
    services.govstack_event._create_appointment_type's slug-collision
    retry for the identical try/except IntegrityError shape).

    PIPEDA: email is an internal system identifier, never returned in
    any API response.
    """
    User = get_user_model()
    try:
        user, created = User.objects.get_or_create(
            email=_GOVSTACK_ORGANIZER_EMAIL,
            defaults={"is_staff": True, "is_active": True},
        )
    except IntegrityError:
        # Lost the create race — the row now exists, re-fetch it.
        user = User.objects.get(email=_GOVSTACK_ORGANIZER_EMAIL)
        created = False
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    return user


# ---------------------------------------------------------------------------
# Internal helpers — validation / resolution
# ---------------------------------------------------------------------------


def _resolve_subscriber(participant_type: str, participant_id: str):
    """
    Resolve (participant_type, participant_id) to a citizen User.

    participant_type must be "subscriber" (case-sensitive) or blank — no
    other participant type is supported for appointment booking on this
    platform. participant_id resolves via User.pk, restricted to active,
    non-staff users (any citizen User, not gated on having a
    GovStackSubscriberProfile row — see Wave C design decision).

    Raises:
        ValueError: invalid participant_type, blank/malformed participant_id.
        get_user_model().DoesNotExist: participant_id not found / not an
            eligible citizen.
    """
    normalised_type = (participant_type or "").strip()
    if normalised_type and normalised_type != _SUPPORTED_PARTICIPANT_TYPE:
        raise ValueError(
            "participant_type must be 'subscriber' — no other participant "
            "types are supported for appointment booking."
        )

    if not participant_id or not str(participant_id).strip():
        raise ValueError("participant_id is required.")

    try:
        user_pk = int(participant_id)
    except (ValueError, TypeError) as exc:
        raise ValueError("participant_id must be an integer User primary key.") from exc

    User = get_user_model()
    return User.objects.get(pk=user_pk, is_staff=False, is_active=True)


def _validate_participant_entity_id(participant_entity_id: str | None) -> str:
    """
    Validate an (optional) participant_entity_id against Organization.exists().

    Returns the raw string to store on Booking.govstack_participant_entity_id
    ("" if blank/None — clears the field).

    Raises ValueError (never a raw Organization.DoesNotExist / ambiguous
    existence signal) for a non-blank value that is malformed or does not
    reference a known Organization — PIPEDA-safe: the error message is
    static and never echoes the raw input.
    """
    if not participant_entity_id or not str(participant_entity_id).strip():
        return ""

    raw = str(participant_entity_id).strip()
    try:
        org_pk = int(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            "participant_entity_id must be an integer Organization primary key."
        ) from exc

    if not Organization.objects.filter(pk=org_pk).exists():
        raise ValueError("participant_entity_id does not reference a known entity.")

    return raw


def _derive_appointment_mode(slot: Slot) -> str:
    """
    Derive Booking.appointment_mode (in_person/virtual/phone — no "hybrid")
    from slot.appointment_type.mode (in_person/virtual/phone/hybrid).

    hybrid -> in_person: a safe, documented default (hybrid means "client
    choice"; in-person is the always-valid fallback for a single-mode field).
    The other three modes map 1:1.
    """
    mode = slot.appointment_type.mode
    if mode == "hybrid":
        return "in_person"
    return mode


def _parse_datetime_str(value: str):
    """
    Parse an ISO 8601 datetime string used by appointment_filter's from/to
    keys, requiring timezone awareness. Mirrors govstack_event's identical
    rule for consistency across the Scheduler BB's filter surface.

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


def _lock_slot(slot_pk) -> None:
    """
    Set Slot.status = "blocked" under select_for_update(), implementing the
    GovStack "exclusive" flag. Must be called inside an enclosing
    transaction.atomic() block. Reversible via _unlock_slot() — capacity is
    never touched.
    """
    slot = Slot.objects.select_for_update().get(pk=slot_pk)
    slot.status = "blocked"
    slot.save(update_fields=["status", "updated_at"])


def _unlock_slot(slot_pk) -> None:
    """
    Recompute Slot.status from spaces_used/capacity under select_for_update(),
    reversing a prior _lock_slot() call. Reimplements the identical 3-line
    logic used by booking.py's private _update_slot_status() locally, rather
    than importing a private underscore-prefixed helper across a module
    boundary. Must be called inside an enclosing transaction.atomic() block.
    """
    slot = Slot.objects.select_for_update().get(pk=slot_pk)
    if slot.spaces_used == 0:
        new_status = "available"
    elif slot.spaces_used < slot.capacity:
        new_status = "partial"
    else:
        new_status = "full"
    slot.status = new_status
    slot.save(update_fields=["status", "updated_at"])


def _booking_to_dict(booking: Booking, details_req: dict) -> dict:
    """
    Shape a Booking instance into a flat GovStack Appointment response dict.

    The real appointment_list schema nests fields under a ``details`` key,
    but this codebase's established convention (verified correct across
    Waves B/C/D) is a flat dict per item — followed here for consistency.

    Only fields explicitly flagged True in ``details_req`` are included,
    except appointment_id, event_id, participant_type, participant_id,
    status, participant_entity_id, and event_details, which default to
    True when absent from ``details_req`` (exclusive defaults to False).

    NOTE (FIX 5, Wave E adversarial review): the REQUIRED-flags lookup key
    for the response's ``status_id`` field is ``"status"`` — NOT
    ``"status_id"`` — matching the real GovStack OpenAPI spec's
    appointment_details_required schema (verified directly against the
    spec JSON). The *output* dict key stays "status_id" (that IS the real
    response field name per appointment_details); only the flag used to
    decide whether to include it is called "status". Getting this wrong
    previously meant a spec-compliant caller sending
    {"appointment_details_required": {"status": false}} had that key
    silently dropped by DRF (unknown keys ignored, not rejected) — the
    caller's explicit suppression request was silently ignored.
    """
    from apps.appointments.services.govstack_event import get_event_summary

    result: dict = {}

    if details_req.get("appointment_id", True):
        result["appointment_id"] = str(booking.pk)

    if details_req.get("exclusive", False):
        result["exclusive"] = booking.govstack_exclusive

    if details_req.get("event_id", True):
        result["event_id"] = str(booking.slot_id)

    if details_req.get("event_details", True):
        result["event_details"] = get_event_summary(booking.slot)

    if details_req.get("participant_type", True):
        result["participant_type"] = _SUPPORTED_PARTICIPANT_TYPE

    if details_req.get("participant_id", True):
        result["participant_id"] = str(booking.citizen_id)

    if details_req.get("status", True):
        result["status_id"] = booking.status

    if details_req.get("participant_entity_id", True):
        result["participant_entity_id"] = booking.govstack_participant_entity_id

    return result


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------


def appointment_create(
    *,
    event_ids: list[str] | None,
    participant_type: str = "",
    participant_id: str = "",
    participant_entity_id: str = "",
    exclusive: bool = False,
    caller_citizen_id: int | None = None,
) -> list[Booking]:
    """
    Create one or more Bookings (one per event_id) as a single atomic
    GovStack Appointment. Returns the list of created Booking instances
    (len == len(event_ids)).

    Ownership enforcement (caller_citizen_id): when the caller is a
    JWT-authenticated citizen (caller_citizen_id is not None):
      - a blank participant_id defaults to the caller's own pk (a citizen
        booking for themselves shouldn't have to pass their own ID);
      - an explicitly-supplied participant_id that resolves to a DIFFERENT
        citizen raises AppointmentOwnershipError (mapped to HTTP 403 by the
        view layer) — a citizen may never book an appointment on behalf of
        someone else.
    When caller_citizen_id is None (BB-to-BB organizer+ call), behaviour is
    unchanged — participant_id may reference any citizen.

    Actor resolution: the resolved citizen is used as ``actor`` for each
    create_booking() call — the citizen booking their own appointment is the
    natural audit-trail actor (create_booking()'s own steps already expect a
    real actor).

    All-or-nothing: the entire loop runs inside ONE outer transaction.atomic()
    block. create_booking() opens its own internal atomic() block per call;
    nesting is fine (savepoints). If ANY event_id fails for any reason, the
    whole batch rolls back — no partial appointment creation.

    Lock ordering (FIX 4, Wave E adversarial review): event_ids are processed
    in ascending string-sorted order, NOT the caller-supplied order — so the
    returned list is in sorted-pk order rather than strictly mirroring the
    input array's order. create_booking() locks each Slot internally via
    select_for_update(); two concurrent multi-event appointment_create()
    calls targeting an overlapping set of slots in different orders could
    otherwise deadlock (classic lock-ordering problem). Sorting first
    guarantees a globally consistent acquisition order, mirroring the
    identical precedent in booking.py's reschedule_booking(), which
    explicitly sorts slot PKs before locking (see its "Dual-slot lock
    ordering" comment).

    Raises:
        ValueError: invalid participant_type, bad participant_id format,
            bad participant_entity_id format, empty/None event_ids.
        get_user_model().DoesNotExist: participant_id not found / not an
            eligible citizen.
        Slot.DoesNotExist / django.core.exceptions.ValidationError: an
            event_id does not exist or is malformed (UUID parsing is allowed
            to fail naturally, matching govstack_event's convention).
        BookingError (and subclasses SlotFullError, CitizenSuspendedError,
            FrequencyWindowError, MaxActiveBookingsError): propagated as-is
            from booking.py.
        AppointmentOwnershipError: caller_citizen_id is set (JWT-authenticated
            citizen) and an explicitly-supplied participant_id resolves to a
            different citizen.
    """
    if not event_ids:
        raise ValueError("At least one event_id is required.")

    # Ownership enforcement (Tier 2 / citizen JWT callers only — see docstring).
    # A citizen with no participant_id supplied books for themselves by default;
    # an explicit participant_id must match their own pk.
    if caller_citizen_id is not None and not participant_id:
        participant_id = str(caller_citizen_id)

    citizen = _resolve_subscriber(participant_type, participant_id)

    if caller_citizen_id is not None and citizen.pk != caller_citizen_id:
        raise AppointmentOwnershipError(
            "Caller may not create an appointment for a different citizen."
        )

    entity_id_to_store = _validate_participant_entity_id(participant_entity_id)

    # FIX 4: sort by string value before locking — see the lock-ordering note
    # in this function's docstring.
    sorted_event_ids = sorted(event_ids, key=str)

    created_bookings: list[Booking] = []
    with transaction.atomic():
        for event_id in sorted_event_ids:
            slot = Slot.objects.select_related("appointment_type").get(pk=event_id)
            appointment_mode = _derive_appointment_mode(slot)

            booking = create_booking(
                slot=slot,
                citizen=citizen,
                appointment_mode=appointment_mode,
                form_responses={},
                actor=citizen,
                booking_channel=Booking.CHANNEL_API,
            )
            created_bookings.append(booking)

        # Apply GovStack-specific fields + exclusive locking only after every
        # Booking in the batch has been successfully created (still inside
        # the outer atomic() — any failure above rolls all of this back too).
        for booking in created_bookings:
            booking.govstack_exclusive = exclusive
            booking.govstack_participant_entity_id = entity_id_to_store
            booking.save(
                update_fields=[
                    "govstack_exclusive", "govstack_participant_entity_id", "updated_at",
                ]
            )
            if exclusive:
                _lock_slot(booking.slot_id)

    logger.info(
        "appointment_create: created %d booking(s) citizen_id=%s exclusive=%s",
        len(created_bookings), citizen.pk, exclusive,
    )
    return created_bookings


def appointment_modify(
    *,
    appointment_id: str,
    event_id: str | None = None,
    status_id: str | None = None,
    exclusive: bool | None = None,
    participant_entity_id: str | None = None,
) -> Booking:
    """
    Modify an existing Appointment (Booking).

    - event_id supplied and differs from the booking's current slot ->
      reschedule via booking.py:reschedule_booking() (actor=booking.citizen
      — its final log line uses actor.pk directly, so a real, non-null actor
      is required; booking.citizen is always a real User). NOTE:
      reschedule_booking() creates a NEW Booking (old one cancelled) — the
      returned Booking is NOT the same row looked up by appointment_id.
      govstack_exclusive/govstack_participant_entity_id are carried forward
      onto the NEW booking (reschedule_booking() has no knowledge of
      GovStack-specific fields), then overridden by any exclusive/
      participant_entity_id values also supplied in this same call.

      FIX 1 (Wave E adversarial review): reschedule_booking() only knows
      about CivicOS Slot capacity/status — it has no concept of GovStack's
      "exclusive" lock. If the OLD booking was exclusive, its slot was
      previously blocked via _lock_slot(); reschedule_booking() correctly
      frees that old slot on its own (it recomputes old_slot.status via its
      internal _update_slot_status() after decrementing spaces_used — see
      booking.py's reschedule_booking(), which never knows or needs to know
      the slot was GovStack-"blocked" in the first place). But the NEW slot
      is never automatically re-locked — without an explicit _lock_slot()
      call here, a rescheduled appointment would report
      exclusive=True in every API response while its actual underlying slot
      remained open to other bookings. This function re-establishes the
      lock on the new slot immediately after the carry-forward when
      carried_exclusive is True. Any `exclusive` value ALSO supplied in
      this same call is applied afterwards by step 3 below, which
      independently locks/unlocks as needed — so this is safe even when the
      caller overrides exclusive in the same PUT.

      FIX 3 (Wave E adversarial review): the OLD (now-cancelled) booking
      also has its own govstack_exclusive flag explicitly reset to False —
      its slot is no longer actually locked (see above), so leaving
      govstack_exclusive=True on the old row would misrepresent
      history/audit views (a cancelled booking claiming to still be
      "exclusive"). govstack_participant_entity_id on the old booking is
      left untouched — it is metadata about who the appointment was for,
      not lock state.
    - status_id supplied -> mapped to the appropriate booking.py transition,
      applied to whichever Booking is now "current" (i.e. after any
      reschedule from the same call):
          "confirmed" -> confirm_booking()  (actor = GovStack System Organizer)
          "rejected"  -> reject_booking()   (actor = GovStack System Organizer)
          "cancelled" -> cancel_booking()   (actor = booking.citizen)
          "completed" -> complete_booking() (actor = None — None-safe, mirrors
                          automated/system completions)
          blank/None  -> no status transition attempted.
      Confirm/reject are inherently staff/organizer actions in this domain
      (a citizen cannot confirm/reject their own pending booking); no
      specific CivicOS staff identity is resolvable from a GovStack
      requestor_id, so a lazily-seeded system organizer User stands in.
    - exclusive supplied (independent of the above) -> lock/unlock the
      CURRENT slot (Slot.status = "blocked" / recomputed), and persists the
      new govstack_exclusive value on the current Booking.

      FIX 2 (Wave E adversarial review): if THIS SAME call also transitions
      the booking to a terminal status (cancelled/rejected/completed) via
      status_id above, locking the slot for `exclusive=True` is silently
      skipped — the status transition's own cancel_booking()/etc. already
      freed the slot via its internal _update_slot_status(), and locking it
      again here would forcibly re-block a slot with no active booking
      behind it (orphaned, with no automatic path back to "available").
      Silently skipping (rather than raising) was chosen because it is less
      likely to break legitimate callers who send `exclusive` as an
      unrelated/leftover field alongside a status change unrelated to it;
      it is also the only outcome that leaves no orphaned "blocked" slot.
    - participant_entity_id supplied (independent of the above; None means
      "not supplied", "" means "clear") -> updates
      govstack_participant_entity_id on the current Booking, validated via
      the same Organization.exists() check used by appointment_create.

    All validation (status_id, participant_entity_id) happens BEFORE any DB
    write. Returns the final Booking instance (post-reschedule if
    applicable, post-status-transition if applicable).

    Raises:
        Booking.DoesNotExist / django.core.exceptions.ValidationError:
            appointment_id not found or malformed.
        ValueError: invalid status_id, invalid participant_entity_id.
        Slot.DoesNotExist / django.core.exceptions.ValidationError:
            event_id (for reschedule) not found or malformed.
        BookingError subclasses: propagated as-is (e.g. RescheduleCountError,
            RescheduleWindowError, SlotFullError, InvalidStatusTransitionError).
    """
    normalised_status = (status_id or "").strip().lower()
    if normalised_status and normalised_status not in _SUPPORTED_STATUS_TRANSITIONS:
        raise ValueError(
            f"Unsupported status_id. Must be one of: "
            f"{', '.join(sorted(_SUPPORTED_STATUS_TRANSITIONS))}, or blank."
        )

    entity_id_supplied = participant_entity_id is not None
    entity_id_to_set = (
        _validate_participant_entity_id(participant_entity_id) if entity_id_supplied else ""
    )

    with transaction.atomic():
        booking = Booking.objects.get(pk=appointment_id)

        carried_exclusive = booking.govstack_exclusive
        carried_entity_id = booking.govstack_participant_entity_id

        # -- 1. Reschedule (event_id changed) --
        if event_id and str(event_id) != str(booking.slot_id):
            new_slot = Slot.objects.get(pk=event_id)
            # FIX 3: keep a reference to the OLD booking row before `booking`
            # is reassigned to reschedule_booking()'s return value (the NEW
            # row) below.
            old_booking = booking
            booking = reschedule_booking(
                booking=booking, new_slot=new_slot, actor=booking.citizen,
            )
            # Carry forward GovStack-specific fields onto the NEW row.
            booking.govstack_exclusive = carried_exclusive
            booking.govstack_participant_entity_id = carried_entity_id
            booking.save(
                update_fields=[
                    "govstack_exclusive", "govstack_participant_entity_id", "updated_at",
                ]
            )

            # FIX 1: re-establish the exclusive lock on the NEW slot —
            # reschedule_booking() has no knowledge of GovStack's exclusive
            # concept, so carrying the boolean forward onto the new row (above)
            # is not enough on its own; the slot itself must also be blocked.
            # A same-call `exclusive` override (if supplied) is re-applied by
            # step 3 below, so acting on carried_exclusive here is safe.
            if carried_exclusive:
                _lock_slot(booking.slot_id)

            # FIX 3: the OLD booking's own slot is no longer locked (freed by
            # reschedule_booking()'s internal _update_slot_status() call) —
            # clear its stale govstack_exclusive flag so audit/history views
            # don't show a cancelled booking as still "exclusive".
            if old_booking.govstack_exclusive:
                old_booking.govstack_exclusive = False
                old_booking.save(update_fields=["govstack_exclusive", "updated_at"])

        # -- 2. Status transition (applied to whichever Booking is current) --
        if normalised_status == "confirmed":
            organizer = _get_or_create_govstack_organizer_actor()
            booking = confirm_booking(booking=booking, actor=organizer)
        elif normalised_status == "rejected":
            organizer = _get_or_create_govstack_organizer_actor()
            booking = reject_booking(booking=booking, actor=organizer)
        elif normalised_status == "cancelled":
            booking = cancel_booking(booking=booking, actor=booking.citizen)
        elif normalised_status == "completed":
            booking = complete_booking(booking=booking, actor=None)

        # -- 3. exclusive toggle (independent) --
        if exclusive is not None:
            booking.govstack_exclusive = exclusive
            booking.save(update_fields=["govstack_exclusive", "updated_at"])
            if exclusive:
                # FIX 2: if step 2 above just transitioned this booking to a
                # terminal status in this SAME call, the booking is no longer
                # active — its slot was already freed by that transition's own
                # cancel_booking()/reject_booking()/complete_booking() call.
                # Locking it now would orphan it (blocked, with nothing
                # keeping it that way and no automatic path back to
                # "available"). Silently skip the lock in that case — see the
                # docstring above for why "skip" was chosen over raising.
                if booking.status not in (
                    Booking.STATUS_CANCELLED,
                    Booking.STATUS_REJECTED,
                    Booking.STATUS_COMPLETED,
                ):
                    _lock_slot(booking.slot_id)
            else:
                _unlock_slot(booking.slot_id)

        # -- 4. participant_entity_id update (independent) --
        if entity_id_supplied:
            booking.govstack_participant_entity_id = entity_id_to_set
            booking.save(update_fields=["govstack_participant_entity_id", "updated_at"])

    logger.info(
        "appointment_modify: appointment_id=%s -> booking_id=%s status=%s",
        appointment_id, booking.pk, booking.status,
    )
    return booking


def appointment_delete(*, appointment_id: str, caller_citizen_id: int | None = None) -> Booking:
    """
    Cancel an Appointment (Booking) — soft cancel via
    booking.py:cancel_booking(), actor=booking.citizen (citizen-initiated
    cancellation is the expected common case for this endpoint per the
    internal spec's permission model — subscribers cancel their own
    appointments; cancel_booking() is also None-safe if that ever changes).

    Ownership enforcement (caller_citizen_id): when the caller is a
    JWT-authenticated citizen (caller_citizen_id is not None), the booking
    must belong to that citizen — otherwise AppointmentOwnershipError is
    raised. When caller_citizen_id is None (BB-to-BB organizer+ call),
    behaviour is unchanged — any appointment may be cancelled.

    Returns the now-cancelled Booking.

    Raises:
        Booking.DoesNotExist / django.core.exceptions.ValidationError:
            appointment_id not found or malformed.
        InvalidStatusTransitionError: booking already in a terminal status.
        AppointmentOwnershipError: caller_citizen_id is set and does not
            match the booking's citizen.
    """
    booking = Booking.objects.get(pk=appointment_id)

    if caller_citizen_id is not None and booking.citizen_id != caller_citizen_id:
        raise AppointmentOwnershipError("Caller does not own this appointment.")

    booking = cancel_booking(booking=booking, actor=booking.citizen)
    logger.info("appointment_delete: appointment_id=%s cancelled", appointment_id)
    return booking


def appointment_list(
    appointment_filter: dict | None = None,
    appointment_details_required: dict | None = None,
    caller_citizen_id: int | None = None,
) -> list[dict]:
    """
    Return a GovStack Appointment list from Booking, capped at 500 results,
    ordered by -created_at (matches Booking's own Meta.ordering).

    Ownership enforcement (caller_citizen_id): when the caller is a
    JWT-authenticated citizen (caller_citizen_id is not None), the result
    set is unconditionally restricted to that citizen's own appointments —
    this OVERRIDES any participant_id supplied in appointment_filter (a
    citizen can never use the filter to see another citizen's appointments).
    When caller_citizen_id is None (BB-to-BB organizer+ call), behaviour is
    unchanged — appointment_filter.participant_id (if any) applies as-is.

    appointment_filter keys
    ────────────────────────
    appointment_id         — array-typed (Bug 1 bonus fix — a robustness/
                              consistency enhancement, not a literal spec
                              conformance fix: the real GovStack spec types
                              appointment_filter.appointment_id as a plain
                              string, not an array, unlike entity_id/
                              resource_id/subscriber_id/log_id, which the
                              spec does type as arrays). AppointmentFilterSerializer's
                              StringOrListField normalizes a single bare
                              string into a 1-element list, so this is always
                              list-shaped by the time it reaches this
                              function; matches ANY of the given ids (pk__in).
    participant_type       — if supplied and not "subscriber" (case-sensitive),
                              returns [] immediately (no other participant
                              types exist in this system)
    participant_id          — array-typed, same Bug 1 bonus-fix rationale as
                              appointment_id above (matches ANY of the given
                              ids, citizen_id__in). A non-numeric entry
                              causes an empty result set to be returned —
                              preserves this function's pre-existing
                              single-value convention of returning [] on a
                              malformed participant_id rather than raising.
    participant_entity_id   — exact match (Booking.govstack_participant_entity_id)
    status                  — exact match against Booking.STATUS_CHOICES values
                              (already align 1:1 with the GovStack status
                              vocabulary — no alias needed, unlike Slot/Event)
    exclusive                — exact match against govstack_exclusive
    from / to                — filter on Slot.start_datetime range
                              (slot__start_datetime__gte / __lte)

    appointment_details_required keys (all booleans; defaults noted)
    ──────────────────────────────────────────────────────────────────
    appointment_id (True), exclusive (False), event_id (True),
    participant_type (True), participant_id (True), status (True —
    controls whether the response's status_id field is included; see FIX 5,
    Wave E adversarial review — this flag's own key is "status", matching
    the real GovStack spec, even though the response field it controls is
    named "status_id"), participant_entity_id (True), event_details (True —
    nests a compact per-slot projection via govstack_event.get_event_summary()).
    """
    appointment_filter = appointment_filter or {}
    appointment_details_required = appointment_details_required or {}

    participant_type = appointment_filter.get("participant_type")
    if participant_type and participant_type != _SUPPORTED_PARTICIPANT_TYPE:
        return []

    qs = Booking.objects.select_related(
        "slot",
        "slot__appointment_type",
        "slot__appointment_type__service_type",
        "slot__location",
        "slot__location__organization",
        "citizen",
    )

    # Ownership enforcement (Tier 2 / citizen JWT callers only — see docstring).
    # Applied unconditionally and BEFORE the caller-supplied participant_id
    # filter below is consulted, so a citizen can never widen or redirect
    # their own result set via the filter — any conflicting participant_id
    # they supply is simply ignored.
    if caller_citizen_id is not None:
        qs = qs.filter(citizen_id=caller_citizen_id)
        appointment_filter = {k: v for k, v in appointment_filter.items() if k != "participant_id"}

    if appointment_filter.get("appointment_id"):
        qs = qs.filter(pk__in=appointment_filter["appointment_id"])

    if appointment_filter.get("participant_id"):
        try:
            citizen_pks = [int(pid) for pid in appointment_filter["participant_id"]]
        except (ValueError, TypeError):
            return []
        qs = qs.filter(citizen_id__in=citizen_pks)

    if appointment_filter.get("participant_entity_id"):
        qs = qs.filter(
            govstack_participant_entity_id=str(appointment_filter["participant_entity_id"])
        )

    if appointment_filter.get("status"):
        qs = qs.filter(status=appointment_filter["status"])

    if appointment_filter.get("exclusive") is not None:
        qs = qs.filter(govstack_exclusive=bool(appointment_filter["exclusive"]))

    if appointment_filter.get("from"):
        dt = _parse_datetime_str(appointment_filter["from"])
        qs = qs.filter(slot__start_datetime__gte=dt)

    if appointment_filter.get("to"):
        dt = _parse_datetime_str(appointment_filter["to"])
        qs = qs.filter(slot__start_datetime__lte=dt)

    bookings = list(qs.order_by("-created_at")[:_LIST_PAGE_CAP])

    return [_booking_to_dict(booking, appointment_details_required) for booking in bookings]
