"""
GovStack Scheduler BB — Event service layer.

Event = GovStack concept for a scheduled occurrence that citizens can subscribe to.
CivicOS backing models: AppointmentType (event definition) + Slot (event occurrence).

Each GovStack Event maps to one Slot (UUID PK). The event_id is str(slot.pk).

Multi-slot create batches: POST /event/new's ``slots`` array is a batch-convenience
for creating several *sibling* Events in one call — each slot entry becomes its own
independent GovStack event_id / Slot / AppointmentType. AppointmentType is NOT shared
across the batch (see Wave D review FIX 1): each event_id must be independently
modifiable via PUT /event/modifications without side effects on siblings created in
the same original POST call.

GovStack discriminator:
  All GovStack-managed AppointmentTypes carry AppointmentType.is_govstack_managed=True.
  This boolean field (not string-sniffing) is the discriminator used by event_list to
  exclude native CivicOS appointment types. AppointmentType slugs are also prefixed
  with "gs-" as a secondary/cosmetic naming convention for debugging — this prefix is
  NOT the security/scoping boundary.

Not-stored fields:
  ``deadline`` (GovStack booking cutoff) has no equivalent CivicOS field and is silently
  ignored on write. On event_list, deadline_from/deadline_to are mapped to
  Slot.start_datetime range filters as a best-effort approximation. The spec-compliant
  event_filter.from/to window filters on the Slot's actual occurrence time instead
  (Slot.start_datetime / Slot.end_datetime) and is the primary time-window filter.

PIPEDA note:
  No PII is logged at any log level. Log statements use slot/type PKs only.
"""
from __future__ import annotations

import logging
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify
from django.utils.timezone import is_naive

from apps.appointments.models import (
    AppointmentType,
    Location,
    Organization,
    ServiceType,
    Slot,
    StaffProfile,
)

logger = logging.getLogger("civicos.appointments.services.govstack_event")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GS_SLUG_PREFIX = "gs-"
_GOVSTACK_SYSTEM_EMAIL = "govstack-system-staff@civicos.internal"
_GOVSTACK_SYSTEM_LOCATION_SLUG = "govstack-system-location"
_MAX_SLUG_RETRIES = 9   # suffix counters 2..9 (8 retries beyond first attempt)
_MAX_CAPACITY = 100
_MIN_CAPACITY = 1
_MIN_DURATION = 5
_MAX_DURATION = 480

_VALID_STATUSES: frozenset[str] = frozenset(
    {"available", "partial", "full", "blocked", "cancelled", "completed"}
)

# Terms are appended to AppointmentType.description_en on write as either
# "{description}\n\n[Terms: {terms}]" (description non-empty) or
# "[Terms: {terms}]" (description empty). _split_description_terms() reverses
# both forms on read.
_TERMS_PREFIX = "[Terms: "

# Valid ServiceType.category DB choices (see apps.appointments.models.ServiceType).
_CATEGORY_CHOICES: frozenset[str] = frozenset(
    {
        "government", "health", "legal", "employment", "housing",
        "settlement", "food", "mental_health", "other",
    }
)

# Best-effort keyword mapping from free-form GovStack category strings to a
# valid ServiceType.category choice, checked in this order (first match wins).
_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("health", ("health", "medical", "clinic", "hospital", "wellness")),
    ("legal", ("legal", "law", "attorney", "court", "justice")),
    ("employment", ("employment", "job", "work", "career", "hiring")),
    ("housing", ("housing", "shelter", "rent", "tenant", "eviction")),
    ("settlement", ("settlement", "immigra", "newcomer", "refugee")),
    ("food", ("food", "nutrition", "meal", "grocery")),
    ("mental_health", ("mental", "addiction", "counsel", "therapy")),
    ("government", ("government", "gov", "municipal", "public")),
)

# ---------------------------------------------------------------------------
# Internal helpers — system seeds
# ---------------------------------------------------------------------------


def _get_or_create_govstack_org() -> Organization:
    """
    Get or create the GovStack System Organization.

    Used as the owning org for system-created Locations when no
    matching CivicOS org is found for a given host_entity_id.
    """
    org, _ = Organization.objects.get_or_create(
        slug="govstack-system",
        defaults={
            "name_en": "GovStack System",
            "name_fr": "Système GovStack",
            "organization_type": "other",
            "is_active": True,
        },
    )
    return org


def _get_or_create_govstack_location() -> Location:
    """
    Get or create the GovStack System Location placeholder.

    Slots created via the GovStack Event API use this virtual Location to
    satisfy the non-nullable FK on Slot.location when no real venue is provided.

    This Location is SHARED across every GovStack event with no venue — it must
    never be mutated in place by event_modify() (see _split_description_terms
    and the venue-update branch of event_modify() for the corresponding guard).
    """
    org = _get_or_create_govstack_org()
    location, _ = Location.objects.get_or_create(
        slug=_GOVSTACK_SYSTEM_LOCATION_SLUG,
        defaults={
            "organization": org,
            "name_en": "GovStack System Location",
            "name_fr": "Emplacement Système GovStack",
            "is_virtual": True,
            "timezone": "UTC",
        },
    )
    return location


def _get_or_create_govstack_service_type() -> ServiceType:
    """
    Get or create the GovStack System ServiceType (category="government").

    Used as the fallback ServiceType for blank/unrecognised event categories.
    Distinct, keyword-mapped categories get their own dedicated ServiceType —
    see _get_or_create_service_type_for_category().
    """
    service_type, _ = ServiceType.objects.get_or_create(
        slug="govstack-system",
        defaults={
            "name_en": "GovStack System",
            "name_fr": "Système GovStack",
            "category": "government",
            "is_active": True,
        },
    )
    return service_type


def _get_or_create_govstack_staff() -> StaffProfile:
    """
    Get or create the GovStack System StaffProfile.

    The GovStack Event API creates Slots that require a StaffProfile FK.
    A dedicated system staff user fills this role without occupying a real
    staff seat or exposing any PII.

    PIPEDA note: the email address is an internal system identifier, never
    returned in any API response.
    """
    User = get_user_model()
    user, created = User.objects.get_or_create(
        email=_GOVSTACK_SYSTEM_EMAIL,
        defaults={
            "is_staff": True,
            "is_active": True,
        },
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])

    staff, _ = StaffProfile.objects.get_or_create(
        user=user,
        defaults={
            "display_name_en": "GovStack System Staff",
            "display_name_fr": "Personnel Système GovStack",
            "is_accepting_bookings": True,
        },
    )
    return staff


# ---------------------------------------------------------------------------
# Internal helpers — input parsing and validation
# ---------------------------------------------------------------------------


def _parse_datetime_str(value: str) -> object:
    """
    Parse an ISO 8601 datetime string, requiring timezone awareness.

    Raises ValueError for unparseable or naive (no tz) strings.
    Callers should catch ValueError and return 400 to the API consumer.
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


def _map_status(raw: str | None) -> str:
    """
    Map a GovStack status string to a CivicOS Slot.status choice.

    GovStack status   → CivicOS status
    ──────────────────────────────────
    "" or None        → "available"
    "available"       → "available"
    "open"            → "available"  (real spec's own documented example value)
    "cancelled"       → "cancelled"
    "full"            → "full"
    "blocked"         → "blocked"
    "completed"       → "completed"

    Raises ValueError for any unrecognised non-empty value.
    """
    if not raw:
        return "available"
    normalised = raw.strip().lower()
    if normalised == "open":
        return "available"
    if normalised not in _VALID_STATUSES:
        raise ValueError(
            f"Invalid status value {raw!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_STATUSES))}, or 'open'."
        )
    return normalised


def _clamp_capacity(raw: str) -> int:
    """
    Parse subscriber_limit to an int clamped to [1..100].

    Blank/empty string → default 1.
    Non-integer strings raise ValueError.
    """
    if not raw or raw.strip() == "":
        return 1
    try:
        value = int(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"subscriber_limit must be an integer, got {raw!r}."
        ) from exc
    return max(_MIN_CAPACITY, min(_MAX_CAPACITY, value))


def _compute_duration(start, end) -> int:
    """
    Compute appointment duration in minutes from two aware datetimes.

    Clamped to [5..480] minutes regardless of the actual delta.
    """
    seconds = (end - start).total_seconds()
    minutes = int(seconds // 60)
    return max(_MIN_DURATION, min(_MAX_DURATION, minutes))


def _make_appt_type_slug(name: str) -> str:
    """
    Build the base AppointmentType slug for a GovStack event.

    GovStack event AppointmentType slugs are prefixed with "gs-" as a
    cosmetic/debugging naming convention (NOT the security boundary — see
    AppointmentType.is_govstack_managed).

    - If name is provided: ``"gs-" + slugify(name)[:77]``
    - Otherwise: ``"gs-event-{uuid4().hex[:8]}"``
    """
    if name and name.strip():
        return ("gs-" + slugify(name)[:77])
    return f"gs-event-{uuid4().hex[:8]}"


def _map_category(raw: str | None) -> str:
    """
    Map a free-form GovStack ``category`` string to a valid ServiceType.category
    DB choice.

    - Blank/None            → "government" (shared system default)
    - Exact choice match    → that choice (case-insensitive)
    - Keyword match         → best-effort mapped choice (first match wins)
    - No match at all       → "other"
    """
    if not raw or not raw.strip():
        return "government"
    normalised = raw.strip().lower()
    if normalised in _CATEGORY_CHOICES:
        return normalised
    for choice, keywords in _CATEGORY_KEYWORDS:
        if any(kw in normalised for kw in keywords):
            return choice
    return "other"


def _get_or_create_service_type_for_category(category: str | None) -> ServiceType:
    """
    Get or create a ServiceType for the given GovStack event ``category``.

    Blank/unrecognised categories fall back to the shared GovStack System
    ServiceType (category="government") to avoid proliferating near-duplicate
    rows for the common case. Recognised/keyword-mapped categories get their
    own dedicated ServiceType (slug ``govstack-svc-{category}``), so that
    AppointmentType.service_type.category round-trips the caller's actual
    categorisation on read instead of always returning a hardcoded constant.
    """
    if not category or not category.strip():
        return _get_or_create_govstack_service_type()

    mapped = _map_category(category)
    if mapped == "government":
        return _get_or_create_govstack_service_type()

    slug = f"govstack-svc-{slugify(mapped)}"[:80]
    service_type, _ = ServiceType.objects.get_or_create(
        slug=slug,
        defaults={
            "name_en": f"GovStack — {mapped.replace('_', ' ').title()}",
            "name_fr": f"GovStack — {mapped.replace('_', ' ').title()}",
            "category": mapped,
            "is_active": True,
        },
    )
    return service_type


def _split_description_terms(description_en: str) -> tuple[str, str]:
    """
    Split AppointmentType.description_en into (clean_description, terms).

    On write, terms (if supplied) are appended to description_en using the
    _TERMS_PREFIX marker (see _create_appointment_type / event_modify). This
    reverses that on read so GovStack callers see a clean ``description`` and
    a separate ``terms`` value, matching the real event_details schema
    instead of leaking the internal storage format.

    Returns (description_en, "") unchanged if no terms marker is present.
    """
    if not description_en:
        return description_en, ""
    idx = description_en.rfind(_TERMS_PREFIX)
    if idx == -1 or not description_en.endswith("]"):
        return description_en, ""
    terms_blob = description_en[idx + len(_TERMS_PREFIX):-1]
    clean = description_en[:idx]
    # Strip the "\n\n" separator that precedes the marker when both a
    # description and terms were present at write time.
    if clean.endswith("\n\n"):
        clean = clean[:-2]
    return clean, terms_blob


def _resolve_location(venue: dict | None, appt_type_slug: str, host_entity_id: str) -> Location:
    """
    Resolve the CivicOS Location for a GovStack Event.

    If ``venue`` contains at least one non-empty string value, a named event
    location is created (or retrieved) using slug ``govstack-ev-loc-{appt_type_slug}``
    truncated to 80 characters. The display name is derived from the first
    available of: city, building, or the fallback "GovStack Event Location".

    The owning Organization is looked up by ``host_entity_id`` (treated as
    Organization PK); if not found or not supplied, the GovStack system org
    is used.

    If ``venue`` is absent or contains no non-empty string values, returns the
    shared GovStack system location placeholder.

    Create-oriented: since FIX 1 each Slot has its own AppointmentType (and
    thus its own unique appt_type_slug), this naturally gives each event its
    own dedicated Location row when a real venue is supplied — never a row
    shared with a sibling. For updating an EXISTING event's venue in place,
    see the dedicated venue-update branch in event_modify() instead — calling
    this function again on modify would have no effect on an already-existing
    row's fields since get_or_create()'s defaults only apply on first creation.
    """
    has_venue = venue and any(
        isinstance(v, str) and v.strip() for v in venue.values()
    )
    if not has_venue:
        return _get_or_create_govstack_location()

    # Determine owning organization.
    org: Organization | None = None
    if host_entity_id:
        try:
            org = Organization.objects.filter(pk=int(host_entity_id), is_active=True).first()
        except (ValueError, TypeError):
            org = None
    if org is None:
        org = _get_or_create_govstack_org()

    # Build location slug — must be ≤80 chars.
    loc_slug = f"govstack-ev-loc-{appt_type_slug}"[:80]

    # Build a human-readable name from available venue fields.
    loc_name = (
        (venue or {}).get("city")
        or (venue or {}).get("building")
        or "GovStack Event Location"
    )

    location, _ = Location.objects.get_or_create(
        slug=loc_slug,
        defaults={
            "organization": org,
            "name_en": loc_name,
            "name_fr": loc_name,
            "street_address": (venue or {}).get("street", ""),
            "city": (venue or {}).get("city", ""),
            "province": (venue or {}).get("state", ""),
            "timezone": "UTC",
            "is_virtual": False,
        },
    )
    return location


def _create_appointment_type(
    name: str,
    description: str,
    terms: str,
    capacity_per_slot: int,
    mode: str,
    service_type: ServiceType,
    duration_minutes: int,
) -> AppointmentType:
    """
    Create an AppointmentType for ONE GovStack Event Slot, retrying on slug collision.

    FIX 1: called once PER SLOT (not once per POST /event/new batch) so that
    every GovStack event_id owns an exclusive AppointmentType — modifying one
    event can never affect a sibling created in the same batch.

    Slug strategy:
      - Base: ``_make_appt_type_slug(name)``
      - On IntegrityError (unique slug conflict): append ``-{n}`` for n = 2..9.
      - If all retries fail, raises RuntimeError.
      - Because every slot in a multi-slot batch shares the same ``name``, this
        retry mechanism is what gives each sibling a distinct slug (base, -2,
        -3, ...) automatically.

    Terms are appended to description_en as "[Terms: ...]" if non-empty.
    Always sets is_govstack_managed=True (FIX 9 — the actual event_list
    scoping boundary; the "gs-" slug prefix is cosmetic only).
    """
    base_slug = _make_appt_type_slug(name)

    desc_en = description or ""
    if terms and terms.strip():
        desc_en = f"{desc_en}\n\n{_TERMS_PREFIX}{terms}]" if desc_en else f"{_TERMS_PREFIX}{terms}]"

    def _try_create(slug: str) -> AppointmentType:
        return AppointmentType.objects.create(
            service_type=service_type,
            slug=slug,
            name_en=name or slug,
            name_fr=name or slug,
            description_en=desc_en,
            description_fr=description or "",
            duration_minutes=duration_minutes,
            capacity_per_slot=capacity_per_slot,
            mode=mode,
            is_active=True,
            is_govstack_managed=True,
        )

    # First attempt.
    try:
        with transaction.atomic():
            return _try_create(base_slug)
    except IntegrityError:
        pass

    # Retry with counter suffix 2..9.
    for n in range(2, _MAX_SLUG_RETRIES + 1):
        candidate = f"{base_slug}-{n}"[:80]
        try:
            with transaction.atomic():
                return _try_create(candidate)
        except IntegrityError:
            continue

    raise RuntimeError(
        f"Could not create AppointmentType: slug {base_slug!r} collides after "
        f"{_MAX_SLUG_RETRIES} retries. Consider a more unique event name."
    )


def _map_mode(category: str) -> str:
    """
    Map GovStack event category string to AppointmentType.mode choice.

    Keywords checked (first match wins):
      virtual / video / online  → "virtual"
      phone / call              → "phone"
      hybrid                    → "hybrid"
      (no match)                → "in_person"
    """
    lower = (category or "").lower()
    if any(kw in lower for kw in ("virtual", "video", "online")):
        return "virtual"
    if any(kw in lower for kw in ("phone", "call")):
        return "phone"
    if "hybrid" in lower:
        return "hybrid"
    return "in_person"


# ---------------------------------------------------------------------------
# Response shaping
# ---------------------------------------------------------------------------


def _slot_to_event_dict(slot: Slot, details_req: dict) -> dict:
    """
    Shape a Slot instance into a GovStack Event response dict.

    Only fields explicitly flagged True in ``details_req`` are included,
    except for ``event_id``, ``name``, ``category``, ``host_entity_id``, and
    ``status`` which default to True when absent from ``details_req``.

    No PII appears in the output.
    """
    result: dict = {}

    if details_req.get("event_id", True):
        result["event_id"] = str(slot.pk)

    if details_req.get("name", True):
        result["name"] = slot.appointment_type.name_en

    want_description = details_req.get("description", False)
    want_terms = details_req.get("terms", False)
    if want_description or want_terms:
        # FIX 5: split the internally-appended "[Terms: ...]" blob back out
        # so description is clean and terms is independently readable.
        clean_description, terms_value = _split_description_terms(
            slot.appointment_type.description_en
        )
        if want_description:
            result["description"] = clean_description
        if want_terms:
            result["terms"] = terms_value

    if details_req.get("category", True):
        result["category"] = (
            slot.appointment_type.service_type.category
            if slot.appointment_type.service_type_id
            else ""
        )

    if details_req.get("host_entity_id", True):
        org = slot.location.organization if slot.location_id else None
        result["host_entity_id"] = str(org.pk) if org else ""

    if details_req.get("slots", False):
        result["slots"] = [
            {
                "from": slot.start_datetime.isoformat(),
                "to": slot.end_datetime.isoformat(),
            }
        ]

    if details_req.get("status", True):
        result["status"] = slot.status

    if details_req.get("subscriber_limit", False):
        result["subscriber_limit"] = str(slot.appointment_type.capacity_per_slot)

    if details_req.get("venue", False):
        loc = slot.location
        result["venue"] = {
            "building": "",
            "street": loc.street_address if loc else "",
            "area": "",
            "city": loc.city if loc else "",
            "state": loc.province if loc else "",
            "country": "",
            "lat": "",
            "long": "",
        }

    return result


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------


def event_create(
    name: str = "",
    description: str = "",
    category: str = "",
    host_entity_id: str = "",
    slots: list[dict] | None = None,
    deadline: str = "",
    subscriber_limit: str = "",
    terms: str = "",
    status: str = "",
    venue: dict | None = None,
) -> list[Slot]:
    """
    Create a GovStack Event batch → N independent (AppointmentType, Slot) pairs.

    One Slot AND one dedicated AppointmentType are created per entry in the
    ``slots`` list (FIX 1 — each GovStack event_id owns an exclusive
    AppointmentType, so modifying one sibling can never corrupt another
    created in the same POST call). A single-entry ``slots`` list is the
    common case; multi-entry is a batch-convenience for creating several
    sibling Events in one call.

    Field mappings (applied identically to every sibling in the batch)
    ──────────────────────────────────────────────────────────────────
    name              → AppointmentType.name_en / name_fr
    description       → AppointmentType.description_en (terms appended if set)
    category          → AppointmentType.mode (via keyword mapping); also
                        resolves/creates AppointmentType.service_type with a
                        best-effort-mapped ServiceType.category (FIX 4)
    host_entity_id    → Location.organization (looked up by Organization PK)
    slots[].from/to   → Slot.start_datetime / end_datetime / effective_start /
                        effective_end (per-entry); duration derived from delta
                        and stored on that entry's own AppointmentType
    subscriber_limit  → AppointmentType.capacity_per_slot (int, clamped 1..100)
    terms             → appended to AppointmentType.description_en as
                        "[Terms: {terms}]" when non-empty (split back out on
                        read — see _split_description_terms)
    status            → Slot.status (mapped via _map_status)
    venue             → Location slug / name / address fields (per-entry,
                        since each entry gets its own AppointmentType slug)

    Not stored
    ──────────
    deadline: GovStack booking cutoff — no direct CivicOS field. Silently
    ignored. Wave D+ TODO: store in a dedicated JSONField or SchedulingPolicy
    if the business requirement emerges.

    Raises ValueError on invalid status, subscriber_limit, or slot entries.
    Returns list of created Slot instances (one per slot entry in 'slots'),
    in the same order as the input.
    """
    # --- Validate inputs ---
    civicos_status = _map_status(status)
    capacity = _clamp_capacity(subscriber_limit)
    mode = _map_mode(category)

    slot_entries = slots or []
    if not slot_entries:
        raise ValueError(
            "At least one slot entry with 'from' and 'to' datetime strings is required."
        )

    # Pre-parse and validate every entry before creating any DB rows.
    parsed_entries: list[tuple[object, object]] = []
    for entry in slot_entries:
        from_str = entry.get("from", "") or entry.get("start", "")
        to_str = entry.get("to", "") or entry.get("end", "")

        start_dt = _parse_datetime_str(from_str) if from_str else None
        end_dt = _parse_datetime_str(to_str) if to_str else None

        if start_dt is None or end_dt is None:
            raise ValueError(
                "Each slot entry must supply 'from' and 'to' ISO 8601 datetime strings."
            )
        if end_dt <= start_dt:
            raise ValueError(
                f"Slot 'to' ({to_str!r}) must be after 'from' ({from_str!r})."
            )
        parsed_entries.append((start_dt, end_dt))

    # --- Seed system records (shared taxonomy/staff, not owned per-event) ---
    service_type = _get_or_create_service_type_for_category(category)
    staff = _get_or_create_govstack_staff()

    created_slots: list[Slot] = []
    with transaction.atomic():
        for start_dt, end_dt in parsed_entries:
            duration = _compute_duration(start_dt, end_dt)

            # FIX 1: dedicated AppointmentType per slot entry.
            appt_type = _create_appointment_type(
                name=name,
                description=description,
                terms=terms,
                capacity_per_slot=capacity,
                mode=mode,
                service_type=service_type,
                duration_minutes=duration,
            )
            logger.debug(
                "event_create: created AppointmentType pk=%d slug=%r",
                appt_type.pk,
                appt_type.slug,
            )

            # Dedicated Location per slot entry when a real venue is supplied
            # (keyed by this slot's own unique appt_type.slug — see
            # _resolve_location docstring).
            location = _resolve_location(
                venue=venue,
                appt_type_slug=appt_type.slug,
                host_entity_id=host_entity_id,
            )

            slot = Slot.objects.create(
                appointment_type=appt_type,
                staff=staff,
                location=location,
                start_datetime=start_dt,
                end_datetime=end_dt,
                effective_start=start_dt,
                effective_end=end_dt,
                capacity=capacity,
                spaces_used=0,
                status=civicos_status,
            )
            created_slots.append(slot)
            logger.debug("event_create: created slot pk=%s", slot.pk)

    logger.debug(
        "event_create: batch complete slot_count=%d",
        len(created_slots),
    )
    return created_slots


def event_modify(
    event_id: str,
    name: str | None = None,
    description: str | None = None,
    category: str | None = None,
    host_entity_id: str | None = None,
    slots: list[dict] | None = None,
    from_dt: str | None = None,
    to_dt: str | None = None,
    deadline: str | None = None,
    subscriber_limit: str | None = None,
    terms: str | None = None,
    status: str | None = None,
    venue: dict | None = None,
) -> Slot:
    """
    Modify an existing GovStack Event by Slot UUID.

    None = field not supplied (no update). Only explicitly passed fields
    are written. Uses SELECT FOR UPDATE + transaction.atomic() for safety.

    Slot timing (FIX 3): the primary, spec-compliant path is the flat
    ``from_dt``/``to_dt`` parameters, matching the real event_details schema
    used by PUT /event/modifications (flat singular from/to — NOT the
    ``slots`` array, which only exists on event_creation_details/POST).
    Backward-compat: if from_dt/to_dt are both omitted and a legacy ``slots``
    list is supplied, the first entry's from/to values are used instead.

    Since FIX 1, every event_id owns an exclusive AppointmentType — mutating
    AppointmentType fields (name, description, terms, subscriber_limit,
    category, mode) here can never affect a sibling Slot/event_id, even one
    created in the same original batch POST.

    Not stored
    ──────────
    deadline: silently ignored (see event_create docstring).

    Raises:
      Slot.DoesNotExist  — if event_id is not found.
      ValueError         — if event_id is not a valid UUID, or if status or
                           subscriber_limit have invalid values.
    """
    # Let UUID parsing fail naturally for invalid format.
    with transaction.atomic():
        slot = Slot.objects.select_for_update().select_related(
            "appointment_type", "location"
        ).get(pk=event_id)

        appt_type = slot.appointment_type
        slot_update_fields: list[str] = []
        appt_update_fields: list[str] = []

        # -- Slot.status --
        if status is not None:
            slot.status = _map_status(status)
            slot_update_fields.append("status")

        # -- Slot timing --
        resolved_from = from_dt
        resolved_to = to_dt
        if resolved_from is None and resolved_to is None and slots:
            # Legacy backward-compat path (pre-FIX-3 callers sending slots[0]).
            entry = slots[0]
            resolved_from = entry.get("from") or entry.get("start")
            resolved_to = entry.get("to") or entry.get("end")

        if resolved_from:
            start_dt = _parse_datetime_str(resolved_from)
            slot.start_datetime = start_dt
            slot.effective_start = start_dt
            slot_update_fields.extend(["start_datetime", "effective_start"])
        if resolved_to:
            end_dt = _parse_datetime_str(resolved_to)
            slot.end_datetime = end_dt
            slot.effective_end = end_dt
            slot_update_fields.extend(["end_datetime", "effective_end"])
        # Recompute AppointmentType.duration_minutes if we have both bounds.
        # duration_minutes lives on AppointmentType, not on Slot. Since FIX 1
        # this AppointmentType is exclusively owned by this event_id, so
        # mutating it here is always safe — it can never affect a sibling.
        if (resolved_from or resolved_to) and slot.end_datetime > slot.start_datetime:
            appt_type.duration_minutes = _compute_duration(
                slot.start_datetime, slot.end_datetime
            )
            if "duration_minutes" not in appt_update_fields:
                appt_update_fields.append("duration_minutes")

        # -- AppointmentType.name --
        if name is not None:
            appt_type.name_en = name
            appt_type.name_fr = name
            appt_update_fields.extend(["name_en", "name_fr"])

        # -- AppointmentType.description / terms --
        if description is not None or terms is not None:
            # Always start from a clean split of the currently-stored value so
            # an update to only ONE of description/terms preserves the other
            # (FIX 5 — terms must actually round-trip, including across modify).
            current_desc, existing_terms = _split_description_terms(appt_type.description_en)
            if description is not None:
                current_desc = description
            current_terms = terms if terms is not None else existing_terms

            desc_en = current_desc
            if current_terms and current_terms.strip():
                desc_en = (
                    f"{current_desc}\n\n{_TERMS_PREFIX}{current_terms}]"
                    if current_desc
                    else f"{_TERMS_PREFIX}{current_terms}]"
                )
            appt_type.description_en = desc_en
            if description is not None:
                appt_type.description_fr = description
                appt_update_fields.append("description_fr")
            appt_update_fields.append("description_en")

        # -- AppointmentType.capacity_per_slot --
        if subscriber_limit is not None:
            appt_type.capacity_per_slot = _clamp_capacity(subscriber_limit)
            slot.capacity = appt_type.capacity_per_slot
            appt_update_fields.append("capacity_per_slot")
            slot_update_fields.append("capacity")

        # -- AppointmentType.mode / service_type (from category) --
        if category is not None:
            appt_type.mode = _map_mode(category)
            appt_type.service_type = _get_or_create_service_type_for_category(category)
            appt_update_fields.extend(["mode", "service_type"])

        # -- Location (venue / host_entity_id) --
        if venue is not None or host_entity_id is not None:
            has_new_venue = bool(venue) and any(
                isinstance(v, str) and v.strip() for v in venue.values()
            )
            if has_new_venue:
                current_location = slot.location
                is_shared_placeholder = (
                    current_location is None
                    or current_location.slug == _GOVSTACK_SYSTEM_LOCATION_SLUG
                )
                if not is_shared_placeholder:
                    # FIX 6: this Location already belongs exclusively to this
                    # event (either created dedicated at event_create time, or
                    # by a prior modify) — safe to update its address fields
                    # in place. Siblings from the same original batch each
                    # have their own Location row since FIX 1.
                    current_location.street_address = venue.get("street", "")
                    current_location.city = venue.get("city", "")
                    current_location.province = venue.get("state", "")
                    current_location.save(
                        update_fields=["street_address", "city", "province", "updated_at"]
                    )
                else:
                    # Currently on the shared system placeholder — mutating it
                    # would corrupt every other GovStack event without a real
                    # venue, so create a NEW dedicated Location instead and
                    # reassign the FK.
                    location = _resolve_location(
                        venue=venue,
                        appt_type_slug=appt_type.slug,
                        host_entity_id=host_entity_id if host_entity_id is not None else "",
                    )
                    slot.location = location
                    slot_update_fields.append("location")
            elif venue is not None:
                # venue supplied but entirely blank → reset to shared placeholder.
                slot.location = _get_or_create_govstack_location()
                slot_update_fields.append("location")
            else:
                # host_entity_id changed with no venue payload at all —
                # preserve prior behaviour for entity-only reassignment.
                location = _resolve_location(
                    venue=None,
                    appt_type_slug=appt_type.slug,
                    host_entity_id=host_entity_id,
                )
                slot.location = location
                slot_update_fields.append("location")

        # -- Persist changes --
        if appt_update_fields:
            appt_update_fields.append("updated_at")
            appt_type.save(update_fields=appt_update_fields)
            logger.debug(
                "event_modify: updated AppointmentType pk=%d fields=%r",
                appt_type.pk,
                appt_update_fields,
            )

        if slot_update_fields:
            slot_update_fields.append("updated_at")
            slot.save(update_fields=slot_update_fields)
            logger.debug(
                "event_modify: updated slot pk=%s fields=%r",
                slot.pk,
                slot_update_fields,
            )
        else:
            logger.debug("event_modify: no fields changed for slot pk=%s", slot.pk)

    return slot


def event_delete(event_id: str) -> None:
    """
    Soft-cancel a GovStack Event (Slot.status → 'cancelled').

    Does NOT hard-delete the Slot — existing Booking FK dependents and the
    audit trail must remain intact.

    Uses SELECT FOR UPDATE (FIX 10), matching the locking pattern used in
    event_modify(), to prevent a concurrent modify/delete race on the same
    Slot row.

    Raises:
      Slot.DoesNotExist — if event_id is not found.
      ValueError        — if event_id is not a valid UUID string.
    """
    with transaction.atomic():
        # Let UUID parsing fail naturally for invalid format.
        slot = Slot.objects.select_for_update().get(pk=event_id)
        slot.status = "cancelled"
        slot.save(update_fields=["status", "updated_at"])
        logger.debug("event_delete: soft-cancelled slot pk=%s", slot.pk)


def event_list(
    event_filter: dict | None = None,
    event_details_required: dict | None = None,
) -> list[dict]:
    """
    Return GovStack Event list from Slots, capped at 500 results.

    Only returns Slots whose AppointmentType.is_govstack_managed is True
    (FIX 9 — the actual security/scoping boundary; the "gs-" slug prefix is a
    cosmetic naming convention only and is never used for access control).

    Filter keys (event_filter)
    ──────────────────────────
    event_id         — exact UUID match
    name             — case-insensitive substring on AppointmentType.name_en
    category         — case-insensitive substring/exact match on
                       AppointmentType.service_type.category (FIX 4)
    host_entity_id   — exact match on Location.organization PK
    status           — exact match on Slot.status; when absent, cancelled slots
                       are excluded by default
    from_ / to       — real spec's event_filter.from/to date-range window
                       (FIX 8): filters on Slot.start_datetime ≥ from_ and
                       Slot.end_datetime ≤ to. NOTE: the wire-format key is
                       literally "from" — EventFilterSerializer remaps it to
                       the Python-safe attribute name "from_" (see
                       govstack_serializers.EventFilterSerializer).
    deadline_from    — legacy best-effort approximation: maps to
                       start_datetime ≥ value (kept for backward compat;
                       deadline has no CivicOS field)
    deadline_to      — legacy best-effort approximation: maps to
                       start_datetime ≤ value

    Ordering: start_datetime ASC. Hard cap: 500 rows.

    event_details_required controls which fields appear in each result dict.
    Missing keys default to the field-level default (True for event_id, name,
    category, host_entity_id, status; False for description, slots, venue,
    subscriber_limit, terms).
    """
    event_filter = event_filter or {}
    event_details_required = event_details_required or {}

    qs = (
        Slot.objects.filter(appointment_type__is_govstack_managed=True)
        .select_related(
            "appointment_type",
            "appointment_type__service_type",
            "location",
            "location__organization",
        )
    )

    # -- Filters --
    if event_filter.get("event_id"):
        qs = qs.filter(pk=event_filter["event_id"])

    if event_filter.get("name"):
        qs = qs.filter(appointment_type__name_en__icontains=event_filter["name"])

    if event_filter.get("category"):
        qs = qs.filter(
            appointment_type__service_type__category__icontains=event_filter["category"]
        )

    if event_filter.get("host_entity_id"):
        try:
            org_pk = int(event_filter["host_entity_id"])
            qs = qs.filter(location__organization__pk=org_pk)
        except (ValueError, TypeError):
            qs = qs.none()

    if event_filter.get("status"):
        qs = qs.filter(status=event_filter["status"])
    else:
        # Default: exclude cancelled slots so stale events don't pollute results.
        qs = qs.exclude(status="cancelled")

    # FIX 8: spec-compliant from/to date-range window (primary path).
    if event_filter.get("from_"):
        dt = _parse_datetime_str(event_filter["from_"])
        qs = qs.filter(start_datetime__gte=dt)

    if event_filter.get("to"):
        dt = _parse_datetime_str(event_filter["to"])
        qs = qs.filter(end_datetime__lte=dt)

    # Legacy best-effort approximation (kept for backward compat).
    if event_filter.get("deadline_from"):
        dt = _parse_datetime_str(event_filter["deadline_from"])
        qs = qs.filter(start_datetime__gte=dt)

    if event_filter.get("deadline_to"):
        dt = _parse_datetime_str(event_filter["deadline_to"])
        qs = qs.filter(start_datetime__lte=dt)

    slots_list = list(qs.order_by("start_datetime")[:500])

    return [_slot_to_event_dict(slot, event_details_required) for slot in slots_list]
