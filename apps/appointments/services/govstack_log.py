"""
GovStack Scheduler BB — Log service layer.

Log = GovStack concept for the append-only audit trail of booking lifecycle
events. CivicOS backing model: BookingAuditLog (see
apps.appointments.models.BookingAuditLog for field definitions and the
immutability/no-PII invariants documented there — save() raises ValueError
if the PK already exists, delete() always raises ValueError).

Only two operations are implemented in this module: log_create and log_list.
log_modify / log_delete are deliberately NOT implemented here — per
SPEC_APPOINTMENTS_BB_GOVSTACK.md §5.9, PUT /log/modifications and DELETE /log
return HTTP 405 unconditionally at the view layer
(apps.appointments.govstack_views.LogModificationsView /
LogDeleteView) because BookingAuditLog.save()/delete() already make "modify"
or "delete" a hard model-level impossibility, not merely a policy choice —
there is no code path here for a service layer to implement.

Architecture decision — resolving a target Booking from log_data
──────────────────────────────────────────────────────────────────
BookingAuditLog.booking is a REQUIRED FK (on_delete=CASCADE, not nullable),
but the real GovStack log_details schema has no field that directly names a
specific Booking — only entity_id, an Organization-level identifier that
cannot disambiguate one specific Booking. This is a genuine spec/model
mismatch. The resolution used here: PARSE event_id and subscriber_id out of
log_data itself, using the exact comma-separated key:value format the real
spec's OWN example already uses for log_details.log_data:

    "event_id:12345,subscriber_id:1,token:a2s3x2fer,status:attended"

(this is literally the spec's example value, not a format invented for this
wave). See _parse_log_data_refs() below. "event_id" is the GovStack term
used consistently throughout this whole implementation for a CivicOS Slot
UUID (see services.govstack_alert_schedule / services.govstack_event);
"subscriber_id" is the GovStack term used consistently for a citizen
User.pk (see services.govstack_appointment._resolve_subscriber /
services.govstack_subscriber). The resolved Booking is

    Booking.objects.get(slot_id=event_id, citizen_id=subscriber_id)

— the unique Booking for that citizen on that slot. Both halves of this
compound key MUST be present and non-blank in log_data, or creation fails
with LogDataParseError (mapped to 400 at the view layer).

entity_id, if ALSO supplied in log_details on create, is NEVER used to
resolve the Booking — it is validated as an optional CONSISTENCY CHECK
against the resolved booking's actual owning Organization
(booking.slot.location.organization_id), mirroring the "entity_id is a
DERIVED field, never stored" precedent already established for AlertSchedule
in Wave F (see services.govstack_alert_schedule's module docstring). If
entity_id is not supplied, the check is skipped entirely — the real spec
does not mark it required.

Field mapping (log_create)
────────────────────────────
  logger_role  → BookingAuditLog.actor_role — VALIDATED against the model's
                 real choices {"admin", "organizer", "resource", "subscriber",
                 "system"}; ValueError on any other value. Mirrors the
                 precedent set by Wave F's target_category validation
                 (services.govstack_alert_schedule._validate_target_category),
                 accepted in that wave's deep review as reasonable because it
                 maps to a real, meaningful internal enum.
  logger_id    → BookingAuditLog.actor_id — stored as-is (a string, NOT
                 resolved via FK — "PII safety, not a FK" per the model
                 field's own help_text). Blank is allowed (matches the
                 model's blank=True).
  log_category → BookingAuditLog.action — deliberately NOT validated against
                 ACTION_CHOICES; accepted as free text. The real spec's
                 log_category is an unconstrained `type: string` with a
                 domain-specific example ("attendance") that does not even
                 appear in CivicOS's own ACTION_CHOICES enum — Wave F's deep
                 review specifically flagged over-constraining a
                 GovStack-supplied enum-like field as a certifiability risk
                 when the real spec does not itself constrain it. Django's
                 choices= on the model field is a UI/admin-form hint only —
                 it is not enforced by .objects.create().
  datetime     → BookingAuditLog.timestamp — ACCEPTED BUT ALWAYS IGNORED.
                 timestamp has auto_now_add=True, so Django silently
                 discards any explicitly supplied value on create and the DB
                 always stores real wall-clock creation time. This is NOT a
                 limitation to work around (no manual .save(update_fields=)
                 trick is used here): allowing a caller to claim an
                 arbitrary — potentially backdated — timestamp for an audit
                 entry would undermine the audit trail's tamper resistance,
                 so accepting-but-ignoring the GovStack `datetime` input on
                 create is a deliberate, security-positive design choice,
                 not an oversight. (On log_list / read, the REAL stored
                 timestamp is always what is returned as "datetime" — this
                 note only concerns the write/create direction.)
  log_data     → after being parsed for event_id/subscriber_id (booking
                 resolution, above), the raw string itself is not stored
                 verbatim in any dedicated free-text column (none exists) —
                 it is stored inside BookingAuditLog.detail (a JSONField) as
                 {"log_data": log_data}. This assumes log_data cannot
                 contain PII under THIS endpoint's contract: GovStack callers
                 here are BB-to-BB/admin-tier, and the spec's own example
                 value only ever carries operational correlation info (IDs,
                 a token, a status) — consistent with `detail`'s "slugs and
                 UUIDs only" invariant documented on the model. This is an
                 explicit, documented ASSUMPTION about caller behaviour, not
                 a guarantee enforced by code.
  entity_id    → validation-only, as described above; never stored — there
                 is no entity/organization FK on BookingAuditLog, exactly
                 like AlertSchedule's Wave F entity_id derivation precedent.

log_list
────────
See log_list()'s own docstring below for the full filter/shape contract,
including Spec Quirk #1 (log_filter's field is "category", not
"log_category") and Spec Quirk #2 (log_details_required's field is
"logger_category", but it gates the response's "logger_role" field) — both
verified directly against the real fetched GovStack OpenAPI spec JSON and
preserved here exactly as specified, not "fixed".

Known, deliberately-deferred cross-wave limitation:
  log_filter.log_id is an array in the real spec, but — matching the SAME
  established, documented convention already used for every other `*_id[]`
  array filter in this codebase (Message/AlertSchedule/Event all implement
  these as single-value exact-match, NOT `__in`; see Wave F's deep-review
  commit message for the rationale) — log_id is implemented here as a
  single-value exact-match filter for consistency. Fixing this only for Log
  while leaving the identical limitation everywhere else would make the
  codebase MORE inconsistent, not less.

PIPEDA:
  No PII is logged at any level in this module. Log statements use PKs
  only. `detail` / `log_data` contents are never written to a Python logger
  — they may contain operational correlation data but this module makes no
  guarantee they are PII-free, so they are treated as sensitive for logging
  purposes even though they ARE returned in the API response (see log_data
  handling above).
"""
from __future__ import annotations

import json
import logging

from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_naive

from apps.appointments.models import Booking, BookingAuditLog

logger = logging.getLogger("civicos.appointments.services.govstack_log")

_LIST_PAGE_CAP = 500

# Mirrors BookingAuditLog.actor_role's real choices set exactly (models.py).
_VALID_LOGGER_ROLES: frozenset[str] = frozenset(
    {"admin", "organizer", "resource", "subscriber", "system"}
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class LogDataParseError(ValueError):
    """
    Raised when log_data is blank or does not contain both event_id and
    subscriber_id key:value pairs identifying the target booking.

    Subclasses ValueError so a caller that only distinguishes ValueError
    generically (rather than this specific subclass) still catches it — but
    the view layer catches this subclass FIRST to surface its fully static,
    non-PII message directly (see govstack_views.LogNewView).
    """


class LogEntityMismatchError(ValueError):
    """
    Raised when a supplied entity_id does not match the resolved booking's
    actual owning Organization (booking.slot.location.organization_id).

    Subclasses ValueError for the same reason as LogDataParseError.
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate_logger_role(logger_role: str) -> None:
    """Raise ValueError if logger_role is not one of BookingAuditLog.actor_role's real choices."""
    if logger_role not in _VALID_LOGGER_ROLES:
        raise ValueError(
            f"Invalid logger_role {logger_role!r}. Must be one of: {sorted(_VALID_LOGGER_ROLES)!r}."
        )


def _parse_datetime_str(value: str):
    """
    Parse an ISO 8601 datetime string, requiring timezone awareness.

    Duplicated (rather than imported) from the near-identical helpers in
    services.govstack_event / services.govstack_appointment /
    services.govstack_alert_schedule — this codebase's established
    convention keeps small private helpers local to each service module
    rather than importing an underscore-prefixed function across a module
    boundary (see services.govstack_appointment._unlock_slot's docstring
    for the precedent/rationale).

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


def _parse_log_data_refs(log_data: str) -> tuple[str, str]:
    """
    Parse event_id and subscriber_id out of a comma-separated key:value
    log_data string, e.g.:

        "event_id:12345,subscriber_id:1,token:a2s3x2fer,status:attended"

    This is literally the real GovStack OpenAPI spec's own example value for
    log_details.log_data — not a format invented for this wave. Keys are
    matched case-sensitively (exactly "event_id" / "subscriber_id", matching
    the spec example's own casing). Extra key:value pairs (e.g. "token",
    "status") are ignored by this parser — they remain part of the raw
    string stored in BookingAuditLog.detail.

    Raises LogDataParseError if log_data is blank, or if either key is
    missing or has a blank value.
    """
    _required_msg = (
        "log_data must include event_id and subscriber_id key:value pairs "
        "identifying the target booking."
    )
    if not log_data or not log_data.strip():
        raise LogDataParseError(_required_msg)

    refs: dict[str, str] = {}
    for pair in log_data.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        key, _, value = pair.partition(":")
        refs[key.strip()] = value.strip()

    event_id = refs.get("event_id", "")
    subscriber_id = refs.get("subscriber_id", "")
    if not event_id or not subscriber_id:
        raise LogDataParseError(_required_msg)

    return event_id, subscriber_id


def _resolve_booking(event_id: str, subscriber_id: str) -> Booking:
    """
    Resolve (event_id, subscriber_id) — as parsed from log_data — to a
    specific Booking. event_id is a Slot UUID; subscriber_id is a citizen
    User pk.

    Raises:
      ValueError — subscriber_id is not an integer (mirrors
        services.govstack_appointment._resolve_subscriber's identical
        malformed-participant_id-as-ValueError convention).
      django.core.exceptions.ValidationError — event_id is not a valid UUID
        (raised by the underlying UUIDField lookup, matching
        Slot.objects.get(pk=...)'s identical behaviour elsewhere in this
        codebase).
      Booking.DoesNotExist — no Booking matches the given (slot, citizen) pair.
    """
    try:
        citizen_pk = int(subscriber_id)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            "subscriber_id parsed from log_data must be an integer User primary key."
        ) from exc

    return Booking.objects.select_related("slot", "slot__location").get(
        slot_id=event_id, citizen_id=citizen_pk
    )


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

def log_create(
    logger_role: str,
    logger_id: str,
    log_category: str,
    log_data: str,
    entity_id: str = "",
    datetime_value: str = "",
) -> BookingAuditLog:
    """
    Create a new (immutable) BookingAuditLog row from GovStack log_details.

    See module docstring for the full field-mapping rationale, including WHY
    `datetime_value` (GovStack's `datetime` field) is accepted but always
    ignored (BookingAuditLog.timestamp has auto_now_add=True — see module
    docstring's "datetime" entry).

    Raises:
      ValueError — invalid logger_role; blank log_category; subscriber_id
        (parsed from log_data) is not an integer.
      LogDataParseError — log_data missing/blank or does not contain both
        event_id and subscriber_id key:value pairs (subclasses ValueError).
      django.core.exceptions.ValidationError — event_id (parsed from
        log_data) is not a valid Slot UUID.
      Booking.DoesNotExist — no Booking matches the resolved (event_id,
        subscriber_id) pair.
      LogEntityMismatchError — entity_id supplied and does not match the
        resolved booking's organization (subclasses ValueError).
    """
    _validate_logger_role(logger_role)

    if not log_category or not log_category.strip():
        raise ValueError("log_category is required.")

    event_id, subscriber_id = _parse_log_data_refs(log_data)
    booking = _resolve_booking(event_id, subscriber_id)

    if entity_id:
        try:
            supplied_entity_pk = int(entity_id)
        except (ValueError, TypeError) as exc:
            raise LogEntityMismatchError(
                "entity_id does not match the resolved booking's organization."
            ) from exc
        actual_org_id = booking.slot.location.organization_id
        if supplied_entity_pk != actual_org_id:
            raise LogEntityMismatchError(
                "entity_id does not match the resolved booking's organization."
            )

    # datetime_value is intentionally never passed through — see module
    # docstring's "datetime" entry. BookingAuditLog.timestamp is
    # auto_now_add=True and always stores real wall-clock creation time.
    entry = BookingAuditLog.objects.create(
        booking=booking,
        action=log_category,
        actor_id=logger_id or "",
        previous_status="",
        new_status="",
        detail={"log_data": log_data},
        actor_role=logger_role,
    )
    logger.debug(
        "log_create: created BookingAuditLog pk=%s booking_id=%s", entry.pk, booking.pk,
    )
    return entry


def log_list(
    log_filter: dict | None = None,
    log_details_required: dict | None = None,
) -> list[dict]:
    """
    Return a GovStack Log list from BookingAuditLog, capped at 500 results,
    ordered by -timestamp — matches BookingAuditLog.Meta.ordering.

    log_filter keys
    ────────────────
    log_id     — exact match on PK. Single-value exact-match, NOT an array
                 lookup — see module docstring "Known, deliberately-deferred
                 cross-wave limitation".
    entity_id  — exact match against the DERIVED owning Organization
                 (booking.slot.location.organization_id) — BookingAuditLog
                 has no stored entity/organization column of its own.
    category   — exact match on BookingAuditLog.action. NOTE: this is the
                 real spec's actual log_filter field name — NOT
                 "log_category" (Spec Quirk #1, see module docstring).
    from / to  — filter on BookingAuditLog.timestamp range. Caller passes
                 the real spec's "from"/"to" keys; the view layer remaps
                 them from the wire-format "from" key to "from_" via
                 LogFilterSerializer and remaps back to "from" before
                 calling this function — identical convention to
                 alert_schedule_list/event_list/appointment_list.

    log_details_required keys (all booleans; defaults noted)
    ──────────────────────────────────────────────────────────
    log_id (True), logger_category (True — Spec Quirk #2: this real-spec
    flag NAME gates the response's "logger_role" field, see module
    docstring), logger_id (True), entity_id (True), log_category (True),
    datetime (True), log_data (False — heaviest field; matches
    MessageDetailsRequiredSerializer.message_body's identical
    default-False precedent).

    Response shape: [{"log_id": "<pk>", "details": {...}}, ...]

    "details"."log_data", when included, is NEVER the raw stored string
    verbatim — it is reconstructed from safe fields only (detail,
    previous_status, new_status) as a JSON string. `actor_ip` is
    deliberately EXCLUDED from this reconstruction: while IP addresses are
    not classic PII, they are excluded here as an extra-cautious
    PIPEDA-consistent choice matching this codebase's general "log/expose
    PK-and-safe-fields only" discipline.
    """
    qs = BookingAuditLog.objects.select_related(
        "booking", "booking__slot", "booking__slot__location"
    )

    filter_data = log_filter or {}

    if filter_data.get("log_id"):
        qs = qs.filter(pk=filter_data["log_id"])

    if filter_data.get("category"):
        qs = qs.filter(action=filter_data["category"])

    if filter_data.get("entity_id"):
        qs = qs.filter(booking__slot__location__organization_id=filter_data["entity_id"])

    if filter_data.get("from"):
        dt = _parse_datetime_str(filter_data["from"])
        qs = qs.filter(timestamp__gte=dt)

    if filter_data.get("to"):
        dt = _parse_datetime_str(filter_data["to"])
        qs = qs.filter(timestamp__lte=dt)

    required = log_details_required or {}
    results: list[dict] = []

    for entry in qs.order_by("-timestamp")[:_LIST_PAGE_CAP]:
        item: dict = {"log_id": str(entry.pk)}
        details: dict = {}

        if required.get("log_id", True):
            details["log_id"] = str(entry.pk)

        if required.get("logger_category", True):
            # Spec Quirk #2 — the incoming flag is named "logger_category"
            # but it gates the response's "logger_role" field (see module
            # docstring).
            details["logger_role"] = entry.actor_role

        if required.get("logger_id", True):
            details["logger_id"] = entry.actor_id

        if required.get("entity_id", True):
            # Defensive per-row derivation — booking.slot.location should
            # always exist given the model's FK non-nullability, but guarded
            # so one malformed row can't crash the whole list (matches the
            # defensive-list-building convention used elsewhere, e.g.
            # services.govstack_alert_schedule.alert_schedule_list).
            try:
                org_id = entry.booking.slot.location.organization_id
                details["entity_id"] = str(org_id) if org_id else ""
            except AttributeError:
                details["entity_id"] = ""

        if required.get("log_category", True):
            details["log_category"] = entry.action

        if required.get("datetime", True):
            details["datetime"] = entry.timestamp.isoformat()

        if required.get("log_data", False):
            # Reconstructed from safe fields only — the raw stored string is
            # never echoed verbatim, and actor_ip is deliberately excluded
            # (see docstring above).
            details["log_data"] = json.dumps(
                {
                    "detail": entry.detail,
                    "previous_status": entry.previous_status,
                    "new_status": entry.new_status,
                }
            )

        item["details"] = details
        results.append(item)

    return results
