"""
GovStack Scheduler BB — Entity (Organization) service layer.

Entity = GovStack concept for a service-delivery organisation.
CivicOS backing model: Organization (apps/appointments/models.py).

GovStack field mapping:
  GovStack `name`     → Organization.name_en / name_fr (same value, bilingual stub)
  GovStack `category` → Organization.organization_type (best-effort match; defaults to "other")
  GovStack `phone`    → Organization.phone
  GovStack `email`    → Organization.email
  GovStack `website`  → Organization.website

Soft-delete: entity_delete() sets is_active=False rather than hard-deleting.
Organizations may have FK dependents (Locations, GovStackMessages). Hard-delete
would break those chains and violate audit requirements.

PIPEDA: Organization contains no PII — name is an org name, not a person.
No redaction required.
"""
from __future__ import annotations

import logging
import random
import string

from django.db import IntegrityError
from django.utils.text import slugify

from apps.appointments.models import Organization

logger = logging.getLogger("civicos.appointments.services.govstack_entity")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_CATEGORY_MAP: dict[str, str] = {
    "health": "health",
    "federal": "government_federal",
    "provincial": "government_provincial",
    "municipal": "government_municipal",
    "ngo": "ngo",
    "government": "government_federal",  # best guess when no more specific keyword
}


def _map_category_to_org_type(category: str) -> str:
    """
    Map a GovStack free-form category string to the closest Organization.organization_type
    choice.

    Iterates over keyword → org_type pairs. First match wins. Defaults to "other" when
    no keyword matches.
    """
    lower = (category or "").lower()
    for keyword, org_type in _CATEGORY_MAP.items():
        if keyword in lower:
            return org_type
    return "other"


def _safe_slug(name: str, max_retries: int = 5) -> str:
    """
    Generate a unique slug from the organization name.

    Uses django.utils.text.slugify to produce a URL-safe identifier.
    If the resulting slug already exists in the database, appends a random
    4-character suffix and retries up to max_retries times.

    Raises RuntimeError if a unique slug cannot be found within max_retries
    attempts (extremely unlikely in practice).
    """
    base = slugify(name)[:76]  # leave room for "-xxxx" suffix within max_length=80
    candidate = base
    for attempt in range(max_retries):
        if not Organization.objects.filter(slug=candidate).exists():
            return candidate
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
        candidate = f"{base}-{suffix}"
    raise RuntimeError(
        f"Could not generate a unique slug for name={name!r} after {max_retries} attempts."
    )


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

def entity_create(
    name: str = "",
    category: str = "",
    phone: str = "",
    email: str = "",
    website: str = "",
) -> Organization:
    """
    Create a new Organization from GovStack Entity details.

    - Generates a unique slug from name.
    - Maps GovStack category to the closest organization_type choice.
    - Stores name in both name_en and name_fr (bilingual stub — caller provides
      a single name; localized display handled elsewhere).

    Returns the newly created Organization instance.
    """
    slug = _safe_slug(name or "entity")
    org_type = _map_category_to_org_type(category)

    try:
        org = Organization.objects.create(
            name_en=name,
            name_fr=name,
            slug=slug,
            organization_type=org_type,
            phone=phone or "",
            email=email or "",
            website=website or "",
            is_active=True,
        )
    except IntegrityError as exc:
        raise ValueError(
            "An entity with this name already exists. Please choose a different name."
        ) from exc
    logger.debug("entity_create: created org pk=%d slug=%r", org.pk, org.slug)
    return org


def entity_modify(
    entity_id: str | int,
    name: str | None = None,
    category: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    website: str | None = None,
) -> Organization:
    """
    Modify an existing active Organization.

    Only updates fields that are explicitly supplied (not None). Blank strings
    are treated as intentional clears for phone/email/website; for name they
    update both bilingual columns.

    Raises Organization.DoesNotExist if no active Organization with entity_id exists.
    """
    org = Organization.objects.get(pk=entity_id, is_active=True)

    update_fields: list[str] = []

    if name is not None:
        org.name_en = name
        org.name_fr = name
        update_fields.extend(["name_en", "name_fr"])

    if category is not None:
        org.organization_type = _map_category_to_org_type(category)
        update_fields.append("organization_type")

    if phone is not None:
        org.phone = phone
        update_fields.append("phone")

    if email is not None:
        org.email = email
        update_fields.append("email")

    if website is not None:
        org.website = website
        update_fields.append("website")

    if update_fields:
        update_fields.append("updated_at")
        org.save(update_fields=update_fields)
        logger.debug("entity_modify: updated org pk=%d fields=%r", org.pk, update_fields)
    else:
        logger.debug("entity_modify: no fields changed for org pk=%d", org.pk)

    return org


def entity_delete(entity_id: str | int) -> None:
    """
    Soft-delete an Organization by setting is_active=False.

    Does NOT call org.delete() — FK dependents (Locations, GovStackMessages)
    must remain intact for audit trail compliance.

    Raises Organization.DoesNotExist if no active Organization with entity_id exists.
    """
    org = Organization.objects.get(pk=entity_id, is_active=True)
    org.is_active = False
    org.save(update_fields=["is_active", "updated_at"])
    logger.debug("entity_delete: soft-deleted org pk=%d", org.pk)


def entity_list(
    entity_filter: dict,
    entity_details_required: dict,
) -> list[dict]:
    """
    Return filtered list of active Organization records as GovStack Entity dicts.

    Applies optional filter parameters from entity_filter:
      entity_id   — exact match on PK
      category    — case-insensitive contains on organization_type
      name        — case-insensitive contains on name_en
      phone       — case-insensitive contains on phone
      email       — case-insensitive contains on email
      website     — case-insensitive contains on website

    Shapes each result using entity_details_required boolean flags.
    entity_id is always included in the output regardless of the flag value.

    Returns a list of dicts ready for JSON serialisation.
    """
    qs = Organization.objects.filter(is_active=True)

    # --- apply filters ---
    entity_id_filter = (entity_filter or {}).get("entity_id", "")
    if entity_id_filter:
        qs = qs.filter(pk=entity_id_filter)

    category_filter = (entity_filter or {}).get("category", "")
    if category_filter:
        qs = qs.filter(organization_type__icontains=category_filter)

    name_filter = (entity_filter or {}).get("name", "")
    if name_filter:
        qs = qs.filter(name_en__icontains=name_filter)

    phone_filter = (entity_filter or {}).get("phone", "")
    if phone_filter:
        qs = qs.filter(phone__icontains=phone_filter)

    email_filter = (entity_filter or {}).get("email", "")
    if email_filter:
        qs = qs.filter(email__icontains=email_filter)

    website_filter = (entity_filter or {}).get("website", "")
    if website_filter:
        qs = qs.filter(website__icontains=website_filter)

    # --- shape results ---
    required = entity_details_required or {}
    results: list[dict] = []

    for org in qs:
        record: dict = {"entity_id": str(org.pk)}  # always included

        if required.get("name", True):
            record["name"] = org.name_en

        if required.get("category", True):
            record["category"] = org.organization_type

        if required.get("phone", False):
            record["phone"] = org.phone

        if required.get("email", False):
            record["email"] = org.email

        if required.get("website", False):
            record["website"] = org.website

        results.append(record)

    return results
