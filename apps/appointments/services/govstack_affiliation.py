"""
GovStack Scheduler BB — Affiliation service layer.

Affiliation = GovStack concept linking a Resource to an Entity.
CivicOS backing model: GovStackAffiliation.

CRUD operations:
  affiliation_create  → GovStackAffiliation.objects.create(...)
  affiliation_modify  → update resource_category and/or work_days_hours
  affiliation_delete  → hard delete (no downstream FKs on Affiliation; soft-delete not needed)
  affiliation_list    → filter + return as GovStack dicts

Filter field naming (final certifiability review FIX 2): the real GovStack
OpenAPI spec's affiliation_filter/affiliation_details_required schemas name
this field "category" — NOT "resource_category" (that name is only used on
the affiliation_details create/response schema itself, which is unaffected).
affiliation_list() below reads the "category" key from affiliation_filter
and affiliation_details_required accordingly; see
govstack_serializers.AffiliationFilterSerializer /
AffiliationDetailsRequiredSerializer for the serializer-side fix and full
rationale.
"""
from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_naive

from apps.appointments.models import BookingAuditLog, GovStackAffiliation, Organization, Resource
from apps.appointments.services.govstack_log import record_admin_audit_event

logger = logging.getLogger("civicos.appointments.services.govstack_affiliation")

# Hard cap on /affiliation/list_details result size — matches the identical
# convention/value already established in govstack_appointment.py,
# govstack_alert_schedule.py, govstack_message.py, and govstack_log.py.
_LIST_PAGE_CAP = 500

# Duplicated (rather than imported) from the near-identical helper in
# services.govstack_event / services.govstack_log — matches this codebase's
# established convention of keeping small private helpers local to each
# service module (see services.govstack_log._parse_datetime_str's docstring
# for the precedent/rationale).
def _parse_datetime_str(value: str):
    """
    Parse an ISO 8601 datetime string, requiring timezone awareness.

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


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

def affiliation_create(
    resource_id: str | int,
    entity_id: str | int,
    resource_category: str = "",
    work_days_hours: dict | None = None,
    actor_id: str = "",
    actor_role: str = "",
) -> GovStackAffiliation:
    """
    Create a new GovStackAffiliation linking a Resource to an Organization.

    Validates that both the resource and entity resolve to active records
    before attempting the insert. The UniqueConstraint on (resource, entity)
    is enforced at the DB level; IntegrityError is caught and re-raised as a
    ValueError with a descriptive message so the view can return 409.

    Round 2 certifiability re-audit fix (MEDIUM): writes an admin audit
    event (BookingAuditLog, booking=None) recording who created this
    Affiliation and when — see
    services.govstack_log.record_admin_audit_event and
    services.govstack_entity.entity_create's docstring for the full design
    rationale (identical pattern applied here). Deliberately written OUTSIDE
    the atomic() block below (record_admin_audit_event never raises), so an
    audit-write failure can never roll back a successful affiliation create.

    Raises:
      Resource.DoesNotExist      — resource_id not found or inactive.
      Organization.DoesNotExist  — entity_id not found or inactive.
      ValueError                 — duplicate (resource, entity) pair.
    """
    with transaction.atomic():
        resource = Resource.objects.select_for_update().get(pk=resource_id, is_active=True)
        org = Organization.objects.select_for_update().get(pk=entity_id, is_active=True)
        try:
            aff = GovStackAffiliation.objects.create(
                resource=resource,
                entity=org,
                resource_category=resource_category or "",
                work_days_hours=work_days_hours if work_days_hours is not None else {},
            )
        except IntegrityError:
            logger.debug(
                "affiliation_create: duplicate affiliation resource_id=%s entity_id=%s",
                resource_id,
                entity_id,
            )
            raise ValueError(
                f"An affiliation between resource_id={resource_id} and entity_id={entity_id} already exists."
            )

    logger.debug(
        "affiliation_create: created affiliation pk=%d resource_id=%s entity_id=%s",
        aff.pk,
        resource_id,
        entity_id,
    )
    record_admin_audit_event(
        action=BookingAuditLog.ACTION_ADMIN_AFFILIATION_MUTATED,
        resource_pk=aff.pk,
        operation="create",
        actor_id=actor_id,
        actor_role=actor_role,
    )
    return aff


def affiliation_modify(
    affiliation_id: str | int,
    resource_category: str | None = None,
    work_days_hours: dict | None = None,
    actor_id: str = "",
    actor_role: str = "",
) -> GovStackAffiliation:
    """
    Modify resource_category and/or work_days_hours of an existing affiliation.

    Only updates fields that are explicitly supplied (not None). Blank string
    for resource_category is treated as an intentional clear.

    Round 2 certifiability re-audit fix (MEDIUM): writes an admin audit
    event when any field actually changed — see affiliation_create()'s
    docstring for the full rationale.

    Raises GovStackAffiliation.DoesNotExist if no affiliation with affiliation_id exists.
    """
    aff = GovStackAffiliation.objects.get(pk=affiliation_id)

    update_fields: list[str] = []

    if resource_category is not None:
        aff.resource_category = resource_category
        update_fields.append("resource_category")

    if work_days_hours is not None:
        aff.work_days_hours = work_days_hours
        update_fields.append("work_days_hours")

    if update_fields:
        update_fields.append("updated_at")
        aff.save(update_fields=update_fields)
        logger.debug(
            "affiliation_modify: updated affiliation pk=%d fields=%r", aff.pk, update_fields
        )
        record_admin_audit_event(
            action=BookingAuditLog.ACTION_ADMIN_AFFILIATION_MUTATED,
            resource_pk=aff.pk,
            operation="update",
            actor_id=actor_id,
            actor_role=actor_role,
        )
    else:
        logger.debug("affiliation_modify: no fields changed for affiliation pk=%d", aff.pk)

    return aff


def affiliation_delete(affiliation_id: str | int, actor_id: str = "", actor_role: str = "") -> None:
    """
    Hard-delete the affiliation.

    GovStackAffiliation has no is_active field and no downstream FK dependents,
    so a hard delete is appropriate. The UniqueConstraint is released immediately
    so a new affiliation with the same (resource, entity) pair can be created.

    Round 2 certifiability re-audit fix (MEDIUM): writes an admin audit
    event BEFORE the hard delete (the row must still exist at write time —
    BookingAuditLog.detail stores resource_pk as a plain string, not an FK,
    so the audit entry survives the affiliation's own deletion) — see
    affiliation_create()'s docstring for the full rationale.

    Raises GovStackAffiliation.DoesNotExist if no affiliation with affiliation_id exists.
    """
    aff = GovStackAffiliation.objects.get(pk=affiliation_id)
    record_admin_audit_event(
        action=BookingAuditLog.ACTION_ADMIN_AFFILIATION_MUTATED,
        resource_pk=aff.pk,
        operation="delete",
        actor_id=actor_id,
        actor_role=actor_role,
    )
    aff.delete()
    logger.debug("affiliation_delete: hard-deleted affiliation pk=%s", affiliation_id)


def affiliation_list(
    affiliation_filter: dict,
    affiliation_details_required: dict,
) -> list[dict]:
    """
    Return list of affiliations filtered and shaped by the request params.

    Applies optional filter parameters from affiliation_filter:
      affiliation_id — array-typed per the real GovStack spec
                       (affiliation_filter.affiliation_id[]); matches ANY of
                       the given ids (pk__in). Round 2 certifiability
                       re-audit fix — verified directly against the fetched
                       spec; AffiliationFilterSerializer's StringOrListField
                       normalizes a single bare string into a 1-element
                       list, so this is always list-shaped by the time it
                       reaches this function (identical precedent to
                       entity_id/resource_id/subscriber_id/event_id).
      resource_id    — exact match on resource FK
      entity_id      — exact match on entity FK
      category       — case-insensitive contains on resource_category (FIX 2:
                       the real spec's affiliation_filter field is literally
                       named "category" — NOT "resource_category"; see this
                       module's docstring and
                       govstack_serializers.AffiliationFilterSerializer for
                       the full rationale)
      from_ / to     — real spec's affiliation_filter.from/to date-range
                       window, filtered on GovStackAffiliation.created_at
                       (the only timestamp field on this model — chosen over
                       updated_at because "from"/"to" reads most naturally as
                       "when was this affiliation established", matching
                       created_at's semantics; there is no field on
                       GovStackAffiliation representing an occurrence window
                       the way Slot.start_datetime/end_datetime does for
                       Event/Log, so created_at is the most sensible
                       approximation. Documented here explicitly per the
                       final certifiability review's instruction to call out
                       this choice rather than silently pick a field).
                       NOTE: the wire-format key is literally "from" —
                       AffiliationFilterSerializer remaps it to the
                       Python-safe attribute name "from_"; the view layer
                       remaps it back to "from" before calling this function
                       (identical pattern to LogListDetailsView — see
                       govstack_views.py).

    Shapes each result using affiliation_details_required boolean flags.
    affiliation_id is always included in the output regardless of the flag.
    The "category" flag (FIX 2 — same rename as the filter field above)
    gates the response's "resource_category" field, which is unchanged (the
    real field name on affiliation_details itself).

    Returns a list of dicts ready for JSON serialisation, capped at
    _LIST_PAGE_CAP (500) results.
    """
    qs = GovStackAffiliation.objects.all()

    # --- apply filters ---
    filter_data = affiliation_filter or {}

    # Round 2 certifiability re-audit fix: affiliation_id is array-typed per
    # the real spec — pk__in= is always the correct application now that
    # StringOrListField guarantees a list shape (even for a single-value
    # caller). A malformed entry raises at queryset evaluation below, which
    # the view layer's generic except Exception clause maps to a 400.
    affiliation_id_filter = filter_data.get("affiliation_id") or []
    if affiliation_id_filter:
        qs = qs.filter(pk__in=affiliation_id_filter)

    resource_id_filter = filter_data.get("resource_id", "")
    if resource_id_filter:
        qs = qs.filter(resource_id=resource_id_filter)

    entity_id_filter = filter_data.get("entity_id", "")
    if entity_id_filter:
        qs = qs.filter(entity_id=entity_id_filter)

    category_filter = filter_data.get("category", "")
    if category_filter:
        qs = qs.filter(resource_category__icontains=category_filter)

    from_str = filter_data.get("from", "")
    if from_str:
        dt = _parse_datetime_str(from_str)
        qs = qs.filter(created_at__gte=dt)

    to_str = filter_data.get("to", "")
    if to_str:
        dt = _parse_datetime_str(to_str)
        qs = qs.filter(created_at__lte=dt)

    # --- shape results ---
    required = affiliation_details_required or {}
    results: list[dict] = []

    for aff in qs.order_by("-created_at")[:_LIST_PAGE_CAP]:
        record: dict = {"affiliation_id": str(aff.pk)}  # always included

        if required.get("resource_id", True):
            # FIX (Bug 4b): emit the canonical "R-<pk>" externally-visible form
            # — matching the exact f"R-{resource.pk}" pattern used throughout
            # services.govstack_resource.resource_list() — instead of the bare
            # FK integer, so round-tripping this id back into
            # AffiliationNewView.post's resource_id (which expects "R-<pk>")
            # works correctly.
            record["resource_id"] = f"R-{aff.resource_id}"

        if required.get("entity_id", True):
            record["entity_id"] = str(aff.entity_id)

        if required.get("category", True):
            record["resource_category"] = aff.resource_category

        if required.get("work_days_hours", False):
            record["work_days_hours"] = aff.work_days_hours

        results.append(record)

    return results
