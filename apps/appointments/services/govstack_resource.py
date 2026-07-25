"""
GovStack Scheduler BB — Resource service layer.

Resource = GovStack concept for any bookable resource (person, room, equipment).
CivicOS backing models: Resource (rooms/equipment) and StaffProfile (people).

The /resource/ GovStack API endpoints operate on a logical "resource" concept
that in CivicOS maps to two models:
  - Resource: for physical or virtual rooms and equipment
  - StaffProfile: for people (staff members acting as schedulable resources)

Wave B implementation stores GovStack-created resources in the Resource model only.
StaffProfile records are read-only via /resource/list_details and /resource/availability
(using a union approach via itertools.chain). Creating a StaffProfile via the GovStack
API is out of scope — staff are managed through CivicOS admin.

Location placeholder:
  Resource.location is a required FK. GovStack-created resources use a lazily-seeded
  "GovStack System Location" (slug="govstack-system-location") as a placeholder.

SSRF note:
  alert_url and status_poll_url are stored as-is in Wave B. Wave F alert dispatch
  MUST validate HTTPS-only and block private IP ranges before calling them.
"""
from __future__ import annotations

import itertools
import logging
from datetime import datetime

from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_naive

from apps.appointments.models import (
    GovStackAffiliation,
    Location,
    Organization,
    Resource,
    Slot,
    StaffProfile,
)

logger = logging.getLogger("civicos.appointments.services.govstack_resource")

# ---------------------------------------------------------------------------
# Alert preference validation
# ---------------------------------------------------------------------------

_VALID_ALERT_PREFS: frozenset[str] = frozenset({"push", "poll", "email", "sms", "none", ""})

# ---------------------------------------------------------------------------
# Category → resource_type mapping
# ---------------------------------------------------------------------------

_CATEGORY_MAP: dict[str, str] = {
    "room": "room",
    "meeting": "room",
    "virtual": "virtual",
    "video": "virtual",
    "phone": "phone_line",
    "equipment": "equipment",
}


def _map_category_to_resource_type(category: str) -> str:
    """
    Map a GovStack free-form category string to Resource.resource_type choice.

    Keywords checked in order (first match wins):
      room / meeting  → "room"
      virtual / video → "virtual"
      phone           → "phone_line"
      equipment       → "equipment"
      (no match)      → "other"
    """
    lower = (category or "").lower()
    for keyword, resource_type in _CATEGORY_MAP.items():
        if keyword in lower:
            return resource_type
    return "other"


# ---------------------------------------------------------------------------
# GovStack System Location placeholder
# ---------------------------------------------------------------------------

def _get_or_create_govstack_location() -> Location:
    """
    Get or create the GovStack System Location used for resources created via the GovStack API.

    Resources created through the /resource/new endpoint don't have a CivicOS location —
    the GovStack concept of affiliation (resource ↔ entity) is used instead.
    This virtual location is a placeholder to satisfy the non-nullable FK constraint.
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
    location, _ = Location.objects.get_or_create(
        slug="govstack-system-location",
        defaults={
            "organization": org,
            "name_en": "GovStack System Location",
            "name_fr": "Emplacement Système GovStack",
            "is_virtual": True,
            "timezone": "UTC",
        },
    )
    return location


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

def resource_create(
    name: str = "",
    category: str = "",
    phone: str = "",
    email: str = "",
    alert_url: str = "",
    alert_preference: str = "",
    status_poll_url: str = "",
) -> Resource:
    """
    Create a new Resource from GovStack Resource details.

    - Gets or creates the GovStack system location (placeholder for required FK).
    - Maps the GovStack category string to a Resource.resource_type choice.
    - Creates Resource with name_en = name_fr = name (bilingual stub).
    - Returns the newly created Resource instance.

    SSRF note: alert_url and status_poll_url are stored as-is. Wave F alert
    dispatch MUST validate HTTPS-only and block private IP ranges.
    # TODO (Wave F): validate alert_url/status_poll_url: HTTPS-only + private IP block.
    """
    if alert_preference and alert_preference not in _VALID_ALERT_PREFS:
        raise ValueError(
            f"Invalid alert_preference {alert_preference!r}. "
            f"Must be one of: push, poll, email, sms, none."
        )

    location = _get_or_create_govstack_location()
    resource_type = _map_category_to_resource_type(category)

    resource = Resource.objects.create(
        location=location,
        name_en=name or "",
        name_fr=name or "",
        resource_type=resource_type,
        phone=phone or "",
        email=email or "",
        alert_url=alert_url or "",
        alert_preference=alert_preference or "",
        status_poll_url=status_poll_url or "",
        is_active=True,
    )
    logger.debug("resource_create: created resource pk=%d", resource.pk)
    return resource


def resource_modify(
    resource_id: str | int,
    name: str | None = None,
    category: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    alert_url: str | None = None,
    alert_preference: str | None = None,
    status_poll_url: str | None = None,
) -> Resource:
    """
    Modify an existing active Resource.

    Only updates fields that are explicitly supplied (not None). Blank strings
    are treated as intentional clears for all fields.

    If name changes, updates both name_en and name_fr (bilingual stub).

    Raises Resource.DoesNotExist if no active Resource with resource_id exists.
    """
    resource = Resource.objects.get(pk=resource_id, is_active=True)

    update_fields: list[str] = []

    if name is not None:
        resource.name_en = name
        resource.name_fr = name
        update_fields.extend(["name_en", "name_fr"])

    if category is not None:
        resource.resource_type = _map_category_to_resource_type(category)
        update_fields.append("resource_type")

    if phone is not None:
        resource.phone = phone
        update_fields.append("phone")

    if email is not None:
        resource.email = email
        update_fields.append("email")

    if alert_url is not None:
        # TODO (Wave F): validate alert_url: HTTPS-only + private IP block before storing.
        resource.alert_url = alert_url
        update_fields.append("alert_url")

    if alert_preference is not None:
        if alert_preference and alert_preference not in _VALID_ALERT_PREFS:
            raise ValueError(
                f"Invalid alert_preference {alert_preference!r}. "
                f"Must be one of: push, poll, email, sms, none."
            )
        resource.alert_preference = alert_preference
        update_fields.append("alert_preference")

    if status_poll_url is not None:
        # TODO (Wave F): validate status_poll_url: HTTPS-only + private IP block before storing.
        resource.status_poll_url = status_poll_url
        update_fields.append("status_poll_url")

    if update_fields:
        update_fields.append("updated_at")
        resource.save(update_fields=update_fields)
        logger.debug(
            "resource_modify: updated resource pk=%d fields=%r", resource.pk, update_fields
        )
    else:
        logger.debug("resource_modify: no fields changed for resource pk=%d", resource.pk)

    return resource


def resource_delete(resource_id: str | int) -> None:
    """
    Soft-delete a Resource by setting is_active=False.

    Does NOT call resource.delete() — Slot and GovStackAffiliation FK dependents
    must remain intact for audit trail compliance.

    Raises Resource.DoesNotExist if no active Resource with resource_id exists.
    """
    resource = Resource.objects.get(pk=resource_id, is_active=True)
    resource.is_active = False
    resource.save(update_fields=["is_active", "updated_at"])
    logger.debug("resource_delete: soft-deleted resource pk=%d", resource.pk)


def resource_list(
    resource_filter: dict,
    resource_details_required: dict,
) -> list[dict]:
    """
    Return union of active Resource + StaffProfile records shaped as GovStack resource dicts.

    Resource records:
      - resource_id = f"R-{resource.pk}"  (prefix to avoid collision with StaffProfile IDs)
      - name = resource.name_en
      - category = resource.resource_type
      - phone, email, alert_url, alert_preference, status_poll_url from Resource fields

    StaffProfile records:
      - resource_id = f"S-{staffprofile.pk}"
      - name = staffprofile.display_name_en or f"Staff-{staffprofile.pk}"
      - category = "staff"
      - phone = staffprofile.gs_phone
      - email = "" (PIPEDA: StaffProfile.user.email must NEVER appear in API responses)
      - alert_url = staffprofile.gs_alert_url
      - alert_preference = staffprofile.gs_alert_preference
      - status_poll_url = staffprofile.gs_status_poll_url

    Filter by:
      - resource_id: "R-<int>" → Resources only; "S-<int>" → StaffProfiles only; else both
      - category: icontains on resource_type for Resources; "staff" substring for StaffProfiles
      - name: icontains on name_en / display_name_en
      - phone: icontains on phone / gs_phone
      - email: icontains on email for Resources; PIPEDA — skip for StaffProfiles

    Apply resource_details_required boolean flags; resource_id is always included.

    NOTE: No PII in this output — StaffProfile.user.email must NEVER appear here (PIPEDA).
    Only gs_phone (which staff explicitly set for GovStack callbacks) is returned.
    """
    rf = resource_filter or {}
    dr = resource_details_required or {}

    rid_filter = rf.get("resource_id", "")
    category_filter = rf.get("category", "")
    name_filter = rf.get("name", "")
    phone_filter = rf.get("phone", "")
    email_filter = rf.get("email", "")

    # Determine which models to query based on resource_id prefix.
    query_resources = True
    query_staff = True
    resource_id_int_filter: str | None = None
    staff_id_int_filter: str | None = None

    if rid_filter:
        if rid_filter.startswith("R-"):
            query_staff = False
            resource_id_int_filter = rid_filter[2:]
        elif rid_filter.startswith("S-"):
            query_resources = False
            staff_id_int_filter = rid_filter[2:]
        else:
            # No prefix — try matching as bare integer against both models.
            resource_id_int_filter = rid_filter
            staff_id_int_filter = rid_filter

    # -- Resource queryset --
    resource_records: list[dict] = []
    if query_resources:
        qs = Resource.objects.filter(is_active=True)

        if resource_id_int_filter:
            try:
                qs = qs.filter(pk=int(resource_id_int_filter))
            except (ValueError, TypeError):
                qs = qs.none()

        if category_filter:
            qs = qs.filter(resource_type__icontains=category_filter)

        if name_filter:
            qs = qs.filter(name_en__icontains=name_filter)

        if phone_filter:
            qs = qs.filter(phone__icontains=phone_filter)

        if email_filter:
            qs = qs.filter(email__icontains=email_filter)

        for resource in qs:
            record: dict = {"resource_id": f"R-{resource.pk}"}

            if dr.get("name", True):
                record["name"] = resource.name_en
            if dr.get("category", True):
                record["category"] = resource.resource_type
            if dr.get("phone", False):
                record["phone"] = resource.phone
            if dr.get("email", False):
                record["email"] = resource.email
            if dr.get("alert_url", False):
                record["alert_url"] = resource.alert_url
            if dr.get("alert_preference", False):
                record["alert_preference"] = resource.alert_preference
            if dr.get("status_poll_url", False):
                record["status_poll_url"] = resource.status_poll_url

            resource_records.append(record)

    # -- StaffProfile queryset --
    staff_records: list[dict] = []
    if query_staff:
        # Only include StaffProfiles if category filter is absent or explicitly
        # requests "staff". Resources with other category filters (room, equipment,
        # etc.) should not surface staff records.
        include_staff = True
        if category_filter and "staff" not in category_filter.lower():
            include_staff = False

        if include_staff:
            sqs = StaffProfile.objects.filter(is_accepting_bookings=True)

            if staff_id_int_filter:
                try:
                    sqs = sqs.filter(pk=int(staff_id_int_filter))
                except (ValueError, TypeError):
                    sqs = sqs.none()

            if name_filter:
                sqs = sqs.filter(display_name_en__icontains=name_filter)

            if phone_filter:
                sqs = sqs.filter(gs_phone__icontains=phone_filter)

            # email_filter: PIPEDA — StaffProfile.user.email must NEVER be
            # exposed via the GovStack API. Skip email filtering for StaffProfiles.

            for staff in sqs:
                record = {"resource_id": f"S-{staff.pk}"}

                if dr.get("name", True):
                    record["name"] = staff.display_name_en or f"Staff-{staff.pk}"
                if dr.get("category", True):
                    record["category"] = "staff"
                if dr.get("phone", False):
                    record["phone"] = staff.gs_phone
                if dr.get("email", False):
                    # PIPEDA: never expose user.email — return empty string.
                    # gs_phone is the only GovStack-visible contact field for staff.
                    record["email"] = ""
                if dr.get("alert_url", False):
                    record["alert_url"] = staff.gs_alert_url
                if dr.get("alert_preference", False):
                    record["alert_preference"] = staff.gs_alert_preference
                if dr.get("status_poll_url", False):
                    record["status_poll_url"] = staff.gs_status_poll_url

                staff_records.append(record)

    return list(itertools.chain(resource_records, staff_records))


def resource_get_availability(resource_filter: dict) -> list[dict]:
    """
    Return free slots for resources matching the filter.

    Reads free_resource_filter dict with keys:
      - resource_id (optional, accepts "R-<int>", "S-<int>", or bare int)
      - Entity_id   (optional, uppercase E per GovStack spec)
      - from        (optional, ISO 8601 datetime string — inclusive lower bound)
      - to          (optional, ISO 8601 datetime string — inclusive upper bound)
      - category    (optional, free-form string)

    Queries Slot model for status in ["available", "partial"] and applies all
    supplied filters. Returns list of slot dicts.

    Raises ValueError for invalid datetime strings in from/to — callers should
    convert this to a 400 response.
    """
    rf = resource_filter or {}

    rid_filter = rf.get("resource_id", "")
    entity_id = rf.get("Entity_id", "")
    # Accept both normalised keys (from_dt/to_dt — set by ResourceAvailabilityFilterSerializer)
    # and the raw spec keys (from/to) for backward-compatibility when calling the service directly.
    from_str = rf.get("from_dt", "") or rf.get("from", "")
    to_str = rf.get("to_dt", "") or rf.get("to", "")
    category_filter = rf.get("category", "")

    # Track query intent so the output resource_id label matches what the caller queried.
    staff_targeted = rid_filter.startswith("S-") if rid_filter else False
    resource_targeted = rid_filter.startswith("R-") if rid_filter else False

    qs = Slot.objects.filter(status__in=["available", "partial"])

    # -- Datetime range filters --
    if from_str:
        try:
            dt_from = parse_datetime(from_str)
            if dt_from is None:
                dt_from = datetime.fromisoformat(from_str)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid 'from' datetime: {from_str!r}") from exc
        if dt_from is not None and is_naive(dt_from):
            raise ValueError(
                f"'from' datetime must include a timezone offset (e.g. '2026-08-01T09:00:00Z' "
                f"or '2026-08-01T09:00:00+00:00'): {from_str!r}"
            )
        qs = qs.filter(start_datetime__gte=dt_from)

    if to_str:
        try:
            dt_to = parse_datetime(to_str)
            if dt_to is None:
                dt_to = datetime.fromisoformat(to_str)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid 'to' datetime: {to_str!r}") from exc
        if dt_to is not None and is_naive(dt_to):
            raise ValueError(
                f"'to' datetime must include a timezone offset (e.g. '2026-08-02T17:00:00Z'): {to_str!r}"
            )
        qs = qs.filter(end_datetime__lte=dt_to)

    # -- Resource / staff ID filter --
    if rid_filter:
        if rid_filter.startswith("R-"):
            try:
                qs = qs.filter(resource_id=int(rid_filter[2:]))
            except (ValueError, TypeError):
                qs = qs.none()
        elif rid_filter.startswith("S-"):
            try:
                qs = qs.filter(staff_id=int(rid_filter[2:]))
            except (ValueError, TypeError):
                qs = qs.none()
        else:
            # No prefix — treat as Resource PK (bare integer).
            try:
                qs = qs.filter(resource_id=int(rid_filter))
            except (ValueError, TypeError):
                qs = qs.none()

    # -- Entity (Organization) filter --
    # Slots are linked to an Entity via GovStackAffiliation on the slot's resource.
    # Staff-only slots (resource is NULL) are excluded when Entity_id is set
    # because the staff→entity join via location is too complex for Wave B.
    if entity_id:
        try:
            entity_pk = int(entity_id)
        except (ValueError, TypeError):
            entity_pk = None

        if entity_pk is not None:
            resource_ids_in_entity = list(
                GovStackAffiliation.objects.filter(entity_id=entity_pk).values_list(
                    "resource_id", flat=True
                )
            )
            qs = qs.filter(resource_id__in=resource_ids_in_entity)
        else:
            qs = qs.none()

    # -- Category filter --
    if category_filter:
        if "staff" in category_filter.lower():
            # Staff slots: no resource assigned (staff is the bookable resource).
            qs = qs.filter(resource__isnull=True)
        else:
            resource_type = _map_category_to_resource_type(category_filter)
            qs = qs.filter(resource__resource_type=resource_type)

    # -- Shape results --
    # Use the query intent to determine the correct resource_id label on each slot.
    # If the caller queried S-N (staff), emit S-N even if the slot has a room attached.
    # If the caller queried R-N (resource room), emit R-N.
    # If no filter, use whichever field is set on the slot (resource first, then staff).
    results: list[dict] = []
    for slot in qs.select_related("resource", "staff"):
        if staff_targeted:
            resource_id_out = f"S-{slot.staff_id}"
        elif resource_targeted:
            resource_id_out = f"R-{slot.resource_id}"
        else:
            # No prefix filter — use resource if set, else staff.
            if slot.resource_id is not None:
                resource_id_out = f"R-{slot.resource_id}"
            else:
                resource_id_out = f"S-{slot.staff_id}"

        results.append(
            {
                "slot_id": str(slot.id),
                "resource_id": resource_id_out,
                "from": slot.start_datetime.isoformat(),
                "to": slot.end_datetime.isoformat(),
                "capacity": slot.capacity,
                "spaces_used": slot.spaces_used,
                "status": slot.status,
            }
        )

    return results
