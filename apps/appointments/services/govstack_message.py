"""
GovStack Scheduler BB — Message service layer.

Message = GovStack concept for a reusable notification template, owned by an
Entity (Organization). CivicOS backing model: GovStackMessage (Wave A).

CRUD operations:
  message_create  → GovStackMessage.objects.create(...)
  message_modify  → partial update of category/message_body (entity_id is
                     immutable post-create — the real GovStack spec has no
                     "re-parent a message to a different entity" operation;
                     delete + recreate is the documented path, matching the
                     Affiliation precedent's resource_id/entity_id immutability)
  message_delete  → hard delete; GovStackMessage.entity/on_delete is PROTECT
                     and GovStackAlertSchedule.message/on_delete is ALSO
                     PROTECT (see models.py), so deleting a message still
                     referenced by an AlertSchedule raises
                     django.db.models.deletion.ProtectedError — intentionally
                     NOT caught here; the view layer catches it and returns
                     400 MESSAGE_IN_USE (mirrors the ProtectedError-handling
                     precedent in apps/payments/admin.py's
                     GovStackBillAdmin.delete_view(), adapted for a service
                     layer instead of Django admin).
  message_list    → filter + shape into {"message_id": ..., "details": {...}}

PIPEDA note:
  message_body may describe appointment-specific details but is not
  classified PII on its own (it is caller-authored template text, not
  personal data about a specific citizen). Regardless, no message content is
  ever logged here — only PKs.
"""
from __future__ import annotations

import logging

from apps.appointments.models import GovStackMessage, Organization

logger = logging.getLogger("civicos.appointments.services.govstack_message")

# Mirrors GovStackMessage.category's max_length=50 (models.py) — the
# serializer/service layer must reject (not silently truncate) an
# over-length category, so this constant is used for validation, not slicing.
_CATEGORY_MAX_LENGTH = 50

_LIST_PAGE_CAP = 500


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate_category_length(category: str) -> None:
    """Raise ValueError if category exceeds GovStackMessage.category's max_length."""
    if category and len(category) > _CATEGORY_MAX_LENGTH:
        raise ValueError(
            f"category must be {_CATEGORY_MAX_LENGTH} characters or fewer "
            f"(received {len(category)})."
        )


def _resolve_entity(entity_id: str | int) -> Organization:
    """
    Resolve entity_id to an active Organization.

    A non-integer entity_id is deliberately folded into the same
    Organization.DoesNotExist outcome as a genuinely missing PK — the caller
    (view layer) maps both to a single, static 404 ENTITY_NOT_FOUND response.
    This avoids leaking format details in the error message (PIPEDA-safe,
    static client-facing errors) and avoids the latent ambiguity in the
    Affiliation service precedent, where a malformed id string instead
    raises a raw ValueError that the view mis-attributes to a different
    failure mode (duplicate affiliation).
    """
    try:
        org_pk = int(entity_id)
    except (ValueError, TypeError) as exc:
        raise Organization.DoesNotExist("entity_id does not reference a known entity.") from exc
    return Organization.objects.get(pk=org_pk, is_active=True)


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

def message_create(
    entity_id: str | int,
    category: str = "",
    message_body: str = "",
) -> GovStackMessage:
    """
    Create a new GovStackMessage owned by the given Organization.

    Raises:
      Organization.DoesNotExist — entity_id not found, inactive, or malformed.
      ValueError                — category exceeds max_length.
    """
    org = _resolve_entity(entity_id)
    _validate_category_length(category or "")

    message = GovStackMessage.objects.create(
        entity=org,
        category=category or "",
        message_body=message_body or "",
    )
    logger.debug(
        "message_create: created message pk=%s entity_id=%s", message.pk, entity_id
    )
    return message


def message_modify(
    message_id: str | int,
    category: str | None = None,
    message_body: str | None = None,
) -> GovStackMessage:
    """
    Partially update category and/or message_body of an existing message.

    Only fields explicitly supplied (not None) are changed. entity_id is not
    modifiable here — see module docstring.

    Raises:
      GovStackMessage.DoesNotExist — no message with message_id exists.
      ValueError                   — category exceeds max_length.
    """
    message = GovStackMessage.objects.get(pk=message_id)

    update_fields: list[str] = []

    if category is not None:
        _validate_category_length(category)
        message.category = category
        update_fields.append("category")

    if message_body is not None:
        message.message_body = message_body
        update_fields.append("message_body")

    if update_fields:
        update_fields.append("updated_at")
        message.save(update_fields=update_fields)
        logger.debug(
            "message_modify: updated message pk=%s fields=%r", message.pk, update_fields
        )
    else:
        logger.debug("message_modify: no fields changed for message pk=%s", message.pk)

    return message


def message_delete(message_id: str | int) -> None:
    """
    Hard-delete the message.

    Raises:
      GovStackMessage.DoesNotExist                  — no message with message_id exists.
      django.db.models.deletion.ProtectedError       — still referenced by an
                                                        active GovStackAlertSchedule
                                                        row (message FK is on_delete=PROTECT).
                                                        NOT caught here — propagated
                                                        to the view layer.
    """
    message = GovStackMessage.objects.get(pk=message_id)
    message.delete()
    logger.debug("message_delete: hard-deleted message pk=%s", message_id)


def message_list(
    message_filter: dict | None = None,
    message_details_required: dict | None = None,
) -> list[dict]:
    """
    Return a GovStack Message list, capped at 500 results, ordered by
    (entity, category) — matches GovStackMessage.Meta.ordering.

    message_filter keys
    ────────────────────
    message_id    — exact match on PK
    entity_id     — exact match on entity FK
    category      — exact match
    message_body  — case-insensitive substring match

    message_details_required keys (all booleans; defaults noted)
    ──────────────────────────────────────────────────────────────
    message_id (True), entity_id (True), category (True), message_body (False)

    Response shape: [{"message_id": "<pk>", "details": {...}}, ...]
    The top-level "message_id" key is always present (item identifier,
    matching the "affiliation_id always included" convention already used by
    affiliation_list) — message_details_required.message_id independently
    controls whether message_id is ALSO echoed inside "details".
    """
    qs = GovStackMessage.objects.select_related("entity")

    filter_data = message_filter or {}

    if filter_data.get("message_id"):
        qs = qs.filter(pk=filter_data["message_id"])

    if filter_data.get("entity_id"):
        qs = qs.filter(entity_id=filter_data["entity_id"])

    if filter_data.get("category"):
        qs = qs.filter(category=filter_data["category"])

    if filter_data.get("message_body"):
        qs = qs.filter(message_body__icontains=filter_data["message_body"])

    required = message_details_required or {}
    results: list[dict] = []

    for message in qs.order_by("entity_id", "category")[:_LIST_PAGE_CAP]:
        item: dict = {"message_id": str(message.pk)}
        details: dict = {}

        if required.get("message_id", True):
            details["message_id"] = str(message.pk)

        if required.get("entity_id", True):
            details["entity_id"] = str(message.entity_id)

        if required.get("category", True):
            details["category"] = message.category

        if required.get("message_body", False):
            details["message_body"] = message.message_body

        item["details"] = details
        results.append(item)

    return results
