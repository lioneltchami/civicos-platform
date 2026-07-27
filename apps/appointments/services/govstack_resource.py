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

SSRF note (Finding #6, fixed):
  alert_url and status_poll_url are validated HTTPS-only + well-formed at
  registration time (_validate_url(), below) before ever being persisted.
  The deeper DNS-resolution + private-IP-block check happens at dispatch
  time in apps/appointments/tasks.py's _is_safe_outbound_url(), already
  applied to resource.alert_url since Wave F. See _validate_url()'s
  docstring for the full two-layer rationale.
"""
from __future__ import annotations

import itertools
import logging
from datetime import datetime

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator as _URLValidator
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_naive

from apps.appointments.models import (
    BookingAuditLog,
    GovStackAffiliation,
    Location,
    Organization,
    Resource,
    Slot,
    StaffProfile,
)
from apps.appointments.services.govstack_log import record_admin_audit_event

logger = logging.getLogger("civicos.appointments.services.govstack_resource")

# Hard cap on /resource/list_details and /resource/availability result size
# — matches the identical convention/value already established in
# govstack_appointment.py, govstack_alert_schedule.py, govstack_message.py,
# and govstack_log.py. Applied independently to the Resource and StaffProfile
# querysets in resource_list() (a chained union of the two).
_LIST_PAGE_CAP = 500

# ---------------------------------------------------------------------------
# Alert preference validation
# ---------------------------------------------------------------------------

_VALID_ALERT_PREFS: frozenset[str] = frozenset({"push", "poll", "email", "sms", "none", ""})

# ---------------------------------------------------------------------------
# SSRF hardening — registration-time URL validation (Finding #6 fix)
# ---------------------------------------------------------------------------
#
# Two-layer defense, matching the identical convention already established
# in services/govstack_subscriber.py's _validate_url:
#   1. HERE (registration time): coarse, cheap validation — scheme must be
#      HTTPS and the URL must be well-formed. Rejects the obviously-wrong
#      case (http://, javascript:, malformed strings) immediately with a
#      clear 400 at create/modify time, before anything is ever persisted.
#   2. apps/appointments/tasks.py's _is_safe_outbound_url (dispatch time,
#      already fully implemented since Wave F): the TOCTOU-safe layer —
#      resolves the hostname via DNS and rejects private/loopback/
#      link-local/reserved/multicast/unspecified/CGNAT/IETF-reserved IPs
#      immediately before every outbound POST to resource.alert_url (see
#      tasks.py's dispatch_alert_schedule, which already reads
#      resource.alert_url and gates it through _is_safe_outbound_url before
#      sending — this layer was already closed for Resource in Wave F).
#
# This function only ever needs to close layer 1 — layer 2 was already
# closed. Prior to this fix, resource_create()/resource_modify() stored
# alert_url/status_poll_url completely unvalidated (not even an HTTPS-only
# check), the only one of the "has an alert_url/status_poll_url field"
# entity groups (Subscriber, StaffProfile, Resource) missing even this
# coarse check.
_https_validator = _URLValidator(schemes=["https"])


def _validate_url(url: str, field_name: str) -> None:
    """Raise ValueError if url is non-empty and is not a valid HTTPS URL."""
    if url:
        try:
            _https_validator(url)
        except DjangoValidationError:
            raise ValueError(
                f"{field_name} must be a valid HTTPS URL. "
                f"Plain HTTP and malformed URLs are not permitted."
            )

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
    actor_id: str = "",
    actor_role: str = "",
) -> Resource:
    """
    Create a new Resource from GovStack Resource details.

    Round 2 certifiability re-audit fix (MEDIUM): writes an admin audit
    event (BookingAuditLog, booking=None) recording who created this
    Resource and when — see services.govstack_log.record_admin_audit_event
    and services.govstack_entity.entity_create's docstring for the full
    design rationale (identical pattern applied here).

    - Gets or creates the GovStack system location (placeholder for required FK).
    - Maps the GovStack category string to a Resource.resource_type choice.
    - Creates Resource with name_en = name_fr = name (bilingual stub).
    - Returns the newly created Resource instance.

    SSRF note (Finding #6, fixed): alert_url/status_poll_url must be
    HTTPS-only and well-formed — enforced here via _validate_url() before
    the row is ever persisted. The deeper DNS-resolution + private-IP-block
    check happens at dispatch time in tasks.py's _is_safe_outbound_url(),
    already applied to resource.alert_url since Wave F. See this module's
    "SSRF hardening" section docstring for the full two-layer rationale.
    """
    if alert_preference and alert_preference not in _VALID_ALERT_PREFS:
        raise ValueError(
            f"Invalid alert_preference {alert_preference!r}. "
            f"Must be one of: push, poll, email, sms, none."
        )

    _validate_url(alert_url, "alert_url")
    _validate_url(status_poll_url, "status_poll_url")

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

    record_admin_audit_event(
        action=BookingAuditLog.ACTION_ADMIN_RESOURCE_MUTATED,
        resource_pk=resource.pk,
        operation="create",
        actor_id=actor_id,
        actor_role=actor_role,
    )
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
    actor_id: str = "",
    actor_role: str = "",
) -> Resource:
    """
    Modify an existing active Resource.

    Only updates fields that are explicitly supplied (not None). Blank strings
    are treated as intentional clears for all fields.

    If name changes, updates both name_en and name_fr (bilingual stub).

    Round 2 certifiability re-audit fix (MEDIUM): writes an admin audit
    event when any field actually changed — see resource_create()'s
    docstring for the full rationale.

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
        # Finding #6 fix: see resource_create()'s docstring / this module's
        # "SSRF hardening" section for the two-layer rationale.
        _validate_url(alert_url, "alert_url")
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
        # Finding #6 fix: see resource_create()'s docstring / this module's
        # "SSRF hardening" section for the two-layer rationale.
        _validate_url(status_poll_url, "status_poll_url")
        resource.status_poll_url = status_poll_url
        update_fields.append("status_poll_url")

    if update_fields:
        update_fields.append("updated_at")
        resource.save(update_fields=update_fields)
        logger.debug(
            "resource_modify: updated resource pk=%d fields=%r", resource.pk, update_fields
        )
        record_admin_audit_event(
            action=BookingAuditLog.ACTION_ADMIN_RESOURCE_MUTATED,
            resource_pk=resource.pk,
            operation="update",
            actor_id=actor_id,
            actor_role=actor_role,
        )
    else:
        logger.debug("resource_modify: no fields changed for resource pk=%d", resource.pk)

    return resource


def resource_delete(resource_id: str | int, actor_id: str = "", actor_role: str = "") -> None:
    """
    Soft-delete a Resource by setting is_active=False.

    Does NOT call resource.delete() — Slot and GovStackAffiliation FK dependents
    must remain intact for audit trail compliance.

    Round 2 certifiability re-audit fix (MEDIUM): writes an admin audit
    event — see resource_create()'s docstring for the full rationale.

    Raises Resource.DoesNotExist if no active Resource with resource_id exists.
    """
    resource = Resource.objects.get(pk=resource_id, is_active=True)
    resource.is_active = False
    resource.save(update_fields=["is_active", "updated_at"])
    logger.debug("resource_delete: soft-deleted resource pk=%d", resource.pk)

    record_admin_audit_event(
        action=BookingAuditLog.ACTION_ADMIN_RESOURCE_MUTATED,
        resource_pk=resource.pk,
        operation="delete",
        actor_id=actor_id,
        actor_role=actor_role,
    )


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
      - resource_id: array-typed per the real GovStack spec
        (resource_filter.resource_id[]) — Bug 1 fix. Each element may
        independently carry the "R-"/"S-" prefix (or none): "R-<int>" routes
        to Resources only, "S-<int>" routes to StaffProfiles only, and an
        unprefixed bare int is tried against both models — exactly like the
        pre-existing single-value semantics, generalised per-element so a
        single call may mix R- and S- prefixed ids. ResourceFilterSerializer's
        StringOrListField normalizes a single bare string into a 1-element
        list, so this is always list-shaped by the time it reaches this
        function. An element that fails to parse to an int after its prefix
        is stripped is silently skipped (matches this function's pre-existing
        "malformed -> no match" convention — see below).
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

    # Bug 1 fix: resource_id is array-typed per the real GovStack spec
    # (resource_filter.resource_id[]) — ResourceFilterSerializer's
    # StringOrListField normalizes a single bare string into a 1-element
    # list, so rid_filter is always list-shaped (or falsy/empty) here.
    rid_filter = rf.get("resource_id") or []
    category_filter = rf.get("category", "")
    name_filter = rf.get("name", "")
    phone_filter = rf.get("phone", "")
    email_filter = rf.get("email", "")

    # Determine which models to query and which pks to filter by, based on
    # each resource_id element's "R-"/"S-" prefix (or lack thereof). An array
    # may legitimately mix R- and S- prefixed values, so each element is
    # parsed independently and routed into its own model's pk__in list — the
    # array-aware generalisation of the previous single-value branch. An
    # element that doesn't parse to an int after its prefix is stripped is
    # silently skipped (not raised as an error): the pre-existing
    # single-value convention was to fall back to qs.none() on a malformed
    # id; skipping the element here has the identical net effect, since
    # filter(pk__in=[]) (or a list missing that entry) matches nothing for it.
    query_resources = True
    query_staff = True
    resource_pks: list[int] = []
    staff_pks: list[int] = []

    if rid_filter:
        resource_targeted = False
        staff_targeted = False
        for rid in rid_filter:
            if rid.startswith("R-"):
                resource_targeted = True
                try:
                    resource_pks.append(int(rid[2:]))
                except (ValueError, TypeError):
                    pass
            elif rid.startswith("S-"):
                staff_targeted = True
                try:
                    staff_pks.append(int(rid[2:]))
                except (ValueError, TypeError):
                    pass
            else:
                # No prefix — try matching as bare integer against both models.
                resource_targeted = True
                staff_targeted = True
                try:
                    bare_pk = int(rid)
                except (ValueError, TypeError):
                    continue
                resource_pks.append(bare_pk)
                staff_pks.append(bare_pk)
        query_resources = resource_targeted
        query_staff = staff_targeted

    # -- Resource queryset --
    resource_records: list[dict] = []
    if query_resources:
        qs = Resource.objects.filter(is_active=True)

        if rid_filter:
            qs = qs.filter(pk__in=resource_pks)

        if category_filter:
            qs = qs.filter(resource_type__icontains=category_filter)

        if name_filter:
            qs = qs.filter(name_en__icontains=name_filter)

        if phone_filter:
            qs = qs.filter(phone__icontains=phone_filter)

        if email_filter:
            qs = qs.filter(email__icontains=email_filter)

        for resource in qs[:_LIST_PAGE_CAP]:
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

            if rid_filter:
                sqs = sqs.filter(pk__in=staff_pks)

            if name_filter:
                sqs = sqs.filter(display_name_en__icontains=name_filter)

            if phone_filter:
                sqs = sqs.filter(gs_phone__icontains=phone_filter)

            # email_filter: PIPEDA — StaffProfile.user.email must NEVER be
            # exposed via the GovStack API. Skip email filtering for StaffProfiles.

            for staff in sqs[:_LIST_PAGE_CAP]:
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
    supplied filters. Returns list of slot dicts, ordered by start_datetime
    and capped at _LIST_PAGE_CAP (500) results.

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
    for slot in qs.select_related("resource", "staff").order_by("start_datetime")[:_LIST_PAGE_CAP]:
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
