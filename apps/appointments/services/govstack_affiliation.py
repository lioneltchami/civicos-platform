"""
GovStack Scheduler BB — Affiliation service layer.

Affiliation = GovStack concept linking a Resource to an Entity.
CivicOS backing model: GovStackAffiliation.

CRUD operations:
  affiliation_create  → GovStackAffiliation.objects.create(...)
  affiliation_modify  → update resource_category and/or work_days_hours
  affiliation_delete  → hard delete (no downstream FKs on Affiliation; soft-delete not needed)
  affiliation_list    → filter + return as GovStack dicts
"""
from __future__ import annotations

import logging

from django.db import IntegrityError

from apps.appointments.models import GovStackAffiliation, Organization, Resource

logger = logging.getLogger("civicos.appointments.services.govstack_affiliation")


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

def affiliation_create(
    resource_id: str | int,
    entity_id: str | int,
    resource_category: str = "",
    work_days_hours: dict | None = None,
) -> GovStackAffiliation:
    """
    Create a new GovStackAffiliation linking a Resource to an Organization.

    Validates that both the resource and entity resolve to active records
    before attempting the insert. The UniqueConstraint on (resource, entity)
    is enforced at the DB level; IntegrityError is caught and re-raised as a
    ValueError with a descriptive message so the view can return 409.

    Raises:
      Resource.DoesNotExist      — resource_id not found or inactive.
      Organization.DoesNotExist  — entity_id not found or inactive.
      ValueError                 — duplicate (resource, entity) pair.
    """
    # Validate FK existence before inserting.
    Resource.objects.get(pk=resource_id, is_active=True)
    Organization.objects.get(pk=entity_id, is_active=True)

    try:
        aff = GovStackAffiliation.objects.create(
            resource_id=resource_id,
            entity_id=entity_id,
            resource_category=resource_category or "",
            work_days_hours=work_days_hours if work_days_hours is not None else {},
        )
    except IntegrityError as exc:
        logger.debug(
            "affiliation_create: duplicate affiliation resource_id=%s entity_id=%s",
            resource_id,
            entity_id,
        )
        raise ValueError(
            f"An affiliation between resource_id={resource_id} and entity_id={entity_id} already exists."
        ) from exc

    logger.debug(
        "affiliation_create: created affiliation pk=%d resource_id=%s entity_id=%s",
        aff.pk,
        resource_id,
        entity_id,
    )
    return aff


def affiliation_modify(
    affiliation_id: str | int,
    resource_category: str | None = None,
    work_days_hours: dict | None = None,
) -> GovStackAffiliation:
    """
    Modify resource_category and/or work_days_hours of an existing affiliation.

    Only updates fields that are explicitly supplied (not None). Blank string
    for resource_category is treated as an intentional clear.

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
    else:
        logger.debug("affiliation_modify: no fields changed for affiliation pk=%d", aff.pk)

    return aff


def affiliation_delete(affiliation_id: str | int) -> None:
    """
    Hard-delete the affiliation.

    GovStackAffiliation has no is_active field and no downstream FK dependents,
    so a hard delete is appropriate. The UniqueConstraint is released immediately
    so a new affiliation with the same (resource, entity) pair can be created.

    Raises GovStackAffiliation.DoesNotExist if no affiliation with affiliation_id exists.
    """
    aff = GovStackAffiliation.objects.get(pk=affiliation_id)
    aff.delete()
    logger.debug("affiliation_delete: hard-deleted affiliation pk=%s", affiliation_id)


def affiliation_list(
    affiliation_filter: dict,
    affiliation_details_required: dict,
) -> list[dict]:
    """
    Return list of affiliations filtered and shaped by the request params.

    Applies optional filter parameters from affiliation_filter:
      affiliation_id    — exact match on PK
      resource_id       — exact match on resource FK
      entity_id         — exact match on entity FK
      resource_category — case-insensitive contains

    Shapes each result using affiliation_details_required boolean flags.
    affiliation_id is always included in the output regardless of the flag.

    Returns a list of dicts ready for JSON serialisation.
    """
    qs = GovStackAffiliation.objects.all()

    # --- apply filters ---
    filter_data = affiliation_filter or {}

    affiliation_id_filter = filter_data.get("affiliation_id", "")
    if affiliation_id_filter:
        qs = qs.filter(pk=affiliation_id_filter)

    resource_id_filter = filter_data.get("resource_id", "")
    if resource_id_filter:
        qs = qs.filter(resource_id=resource_id_filter)

    entity_id_filter = filter_data.get("entity_id", "")
    if entity_id_filter:
        qs = qs.filter(entity_id=entity_id_filter)

    resource_category_filter = filter_data.get("resource_category", "")
    if resource_category_filter:
        qs = qs.filter(resource_category__icontains=resource_category_filter)

    # --- shape results ---
    required = affiliation_details_required or {}
    results: list[dict] = []

    for aff in qs:
        record: dict = {"affiliation_id": str(aff.pk)}  # always included

        if required.get("resource_id", True):
            record["resource_id"] = str(aff.resource_id)

        if required.get("entity_id", True):
            record["entity_id"] = str(aff.entity_id)

        if required.get("resource_category", True):
            record["resource_category"] = aff.resource_category

        if required.get("work_days_hours", False):
            record["work_days_hours"] = aff.work_days_hours

        results.append(record)

    return results
