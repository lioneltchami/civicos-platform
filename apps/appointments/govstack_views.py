"""
GovStack Scheduler BB — API Views.

Mounted at: /govstack/scheduler/  (via apps.appointments.govstack_urls)

Wave B (this module):
  Entity      (4 endpoints)   → EntityNewView, EntityModificationsView,
                                 EntityDeleteView, EntityListDetailsView
  Resource    (5 endpoints)   → ResourceNewView, ResourceModificationsView,
                                 ResourceDeleteView, ResourceListDetailsView,
                                 ResourceAvailabilityView
  Affiliation (4 endpoints)   → AffiliationNewView, AffiliationModificationsView,
                                 AffiliationDeleteView, AffiliationListDetailsView

Waves C–G: Subscriber, Event, Appointment, AlertSchedule, Message, Log
  (stubs remain in govstack_urls.py until each wave is implemented)

Request data convention (GovStack Scheduler BB):
  ALL parameters arrive as query parameters — not request body.
  Common params:
    requestor_id   (required — handled by GovStackSchedulerAuth)
    request_token  (required — handled by GovStackSchedulerAuth)
    qry            (JSON-encoded string for POST/PUT/GET list operations)
    entity_id      (required for PUT/DELETE entity)
    resource_id    (required for PUT/DELETE resource)
    affiliation_id (required for PUT/DELETE affiliation)

Response envelope:
  Success create/modify/delete → {"status": "success", "<entity>_id": "<pk>"}
  Success list                 → {"status": "success", "data": [...]}
  Error                        → {"status": "error", "code": "...", "message": "..."}

Actor roles:
  All Wave B views set request.META["_gs_actor_role"] = "admin" for entity/resource/
  affiliation CRUD. ResourceListDetailsView uses "organizer"; ResourceAvailabilityView
  uses "resource". This allows GovStackSchedulerRolePermission subclasses added in
  future waves to differentiate access levels correctly.

PIPEDA:
  - No PII in log messages. Organization names are not PII.
  - StaffProfile.user.email is never returned from resource list/availability.
  - Only gs_phone (explicitly set by staff for GovStack callbacks) is exposed.
"""
from __future__ import annotations

import json
import logging

from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.appointments.govstack_auth import (
    GovStackSchedulerAuth,
    GovStackSchedulerPermission,
)
from apps.appointments.govstack_serializers import (
    AffiliationCreateQrySerializer,
    AffiliationListQrySerializer,
    AffiliationModifySerializer,
    EntityCreateQrySerializer,
    EntityListQrySerializer,
    EntityModifySerializer,
    ResourceCreateQrySerializer,
    ResourceListQrySerializer,
    ResourceModifySerializer,
)
from apps.appointments.models import GovStackAffiliation, Organization, Resource
from apps.appointments.services.govstack_affiliation import (
    affiliation_create,
    affiliation_delete,
    affiliation_list,
    affiliation_modify,
)
from apps.appointments.services.govstack_entity import (
    entity_create,
    entity_delete,
    entity_list,
    entity_modify,
)
from apps.appointments.services.govstack_resource import (
    resource_create,
    resource_delete,
    resource_get_availability,
    resource_list,
    resource_modify,
)

logger = logging.getLogger("civicos.appointments.govstack.views")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _parse_qry(request) -> tuple[dict | None, Response | None]:
    """
    Parse the `qry` query parameter as JSON.

    Returns (data_dict, None) on success or (None, error_response) on failure.
    An absent or empty qry param returns ({}, None) — callers treat it as an
    empty object (valid for list operations with no filters).
    """
    qry_str = request.query_params.get("qry", "")
    if not qry_str:
        return {}, None
    try:
        return json.loads(qry_str), None
    except (json.JSONDecodeError, ValueError) as exc:
        return None, Response(
            {
                "status": "error",
                "code": "INVALID_QRY",
                "message": f"qry parameter must be valid JSON. Detail: {exc}",
            },
            status=400,
        )


def _validation_error(ser) -> Response:
    """Return a 400 response containing serializer validation errors."""
    return Response(
        {
            "status": "error",
            "code": "VALIDATION_ERROR",
            "message": str(ser.errors),
        },
        status=400,
    )


# ===========================================================================
# Entity views (4 endpoints)
# ===========================================================================

class EntityNewView(APIView):
    """
    POST /govstack/scheduler/entity/new

    Create a new Entity (Organization). All request data arrives as query
    parameters; the entity details are embedded in the `qry` JSON parameter.

    Expected qry shape:
      {"qry": {"details": {"name": "...", "category": "...", "phone": "...",
                           "email": "...", "website": "..."}}}

    Returns:
      201 {"status": "success", "entity_id": "<pk>"}
      400 on validation or creation failure
    """

    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def post(self, request):
        request.META["_gs_actor_role"] = "admin"

        qry_data, err = _parse_qry(request)
        if err:
            return err

        ser = EntityCreateQrySerializer(data=qry_data)
        if not ser.is_valid():
            return _validation_error(ser)

        details = ser.validated_data["qry"]["details"]
        try:
            org = entity_create(
                name=details.get("name", ""),
                category=details.get("category", ""),
                phone=details.get("phone", ""),
                email=details.get("email", ""),
                website=details.get("website", ""),
            )
        except Exception as exc:
            logger.exception("entity_create failed")
            return Response(
                {
                    "status": "error",
                    "code": "CREATE_FAILED",
                    "message": str(exc),
                },
                status=400,
            )

        return Response({"status": "success", "entity_id": str(org.pk)}, status=201)


class EntityModificationsView(APIView):
    """
    PUT /govstack/scheduler/entity/modifications

    Modify an existing Entity. Requires `entity_id` and `qry` query parameters.

    Expected qry shape:
      {"details": {"name": "...", "category": "...", ...}}  (all fields optional)

    Returns:
      200 {"status": "success", "entity_id": "<pk>"}
      400 on missing/invalid params or modification failure
      404 if no active entity with the given entity_id exists
    """

    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def put(self, request):
        request.META["_gs_actor_role"] = "admin"

        entity_id = request.query_params.get("entity_id", "").strip()
        if not entity_id:
            return Response(
                {
                    "status": "error",
                    "code": "MISSING_ENTITY_ID",
                    "message": "entity_id query parameter is required.",
                },
                status=400,
            )

        qry_data, err = _parse_qry(request)
        if err:
            return err

        ser = EntityModifySerializer(data=qry_data)
        if not ser.is_valid():
            return _validation_error(ser)

        details = ser.validated_data["details"]
        try:
            org = entity_modify(
                entity_id=entity_id,
                name=details.get("name"),
                category=details.get("category"),
                phone=details.get("phone"),
                email=details.get("email"),
                website=details.get("website"),
            )
        except Organization.DoesNotExist:
            return Response(
                {
                    "status": "error",
                    "code": "ENTITY_NOT_FOUND",
                    "message": f"No active entity with id={entity_id}.",
                },
                status=404,
            )
        except Exception as exc:
            logger.exception("entity_modify failed for entity_id=%s", entity_id)
            return Response(
                {
                    "status": "error",
                    "code": "MODIFY_FAILED",
                    "message": str(exc),
                },
                status=400,
            )

        return Response({"status": "success", "entity_id": str(org.pk)}, status=200)


class EntityDeleteView(APIView):
    """
    DELETE /govstack/scheduler/entity

    Soft-delete an Entity. Requires `entity_id` query parameter.
    Sets Organization.is_active=False (does NOT hard-delete).

    Returns:
      200 {"status": "success", "entity_id": "<entity_id>"}
      400 if entity_id is missing
      404 if no active entity with the given entity_id exists
    """

    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def delete(self, request):
        request.META["_gs_actor_role"] = "admin"

        entity_id = request.query_params.get("entity_id", "").strip()
        if not entity_id:
            return Response(
                {
                    "status": "error",
                    "code": "MISSING_ENTITY_ID",
                    "message": "entity_id query parameter is required.",
                },
                status=400,
            )

        try:
            entity_delete(entity_id)
        except Organization.DoesNotExist:
            return Response(
                {
                    "status": "error",
                    "code": "ENTITY_NOT_FOUND",
                    "message": f"No active entity with id={entity_id}.",
                },
                status=404,
            )
        except Exception as exc:
            logger.exception("entity_delete failed for entity_id=%s", entity_id)
            return Response(
                {
                    "status": "error",
                    "code": "DELETE_FAILED",
                    "message": str(exc),
                },
                status=400,
            )

        return Response({"status": "success", "entity_id": str(entity_id)}, status=200)


class EntityListDetailsView(APIView):
    """
    GET /govstack/scheduler/entity/list_details

    List active Entities matching the supplied filters. All parameters arrive
    as query params; the filter and field-selection objects are embedded in `qry`.

    Expected qry shape:
      {
        "entity_filter": {"category": "health", "name": "Dept"},
        "entity_details_required": {"entity_id": true, "name": true, "phone": false}
      }

    Returns:
      200 {"status": "success", "data": [...]}
      400 on missing/invalid params
    """

    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def get(self, request):
        request.META["_gs_actor_role"] = "admin"

        qry_data, err = _parse_qry(request)
        if err:
            return err

        ser = EntityListQrySerializer(data=qry_data)
        if not ser.is_valid():
            return _validation_error(ser)

        entity_filter = ser.validated_data.get("entity_filter", {})
        entity_details_required = ser.validated_data.get("entity_details_required", {})

        try:
            data = entity_list(entity_filter, entity_details_required)
        except Exception as exc:
            logger.exception("entity_list failed")
            return Response(
                {
                    "status": "error",
                    "code": "LIST_FAILED",
                    "message": str(exc),
                },
                status=400,
            )

        return Response({"status": "success", "data": data}, status=200)


# ===========================================================================
# Resource views (5 endpoints)
# ===========================================================================

class ResourceNewView(APIView):
    """
    POST /govstack/scheduler/resource/new

    Creates a new GovStack Resource backed by a CivicOS Resource model instance.
    All request data arrives via query parameters:
      - requestor_id, request_token (auth)
      - qry: JSON-encoded { "qry": { "details": { name, category, phone, email,
              alert_url, alert_preference, status_poll_url } } }

    Resource.location is satisfied by a lazily-seeded "GovStack System Location"
    placeholder (the GovStack spec has no location concept at resource creation;
    location association happens via Affiliation).

    Returns:
      201 {"status": "success", "resource_id": "<pk>"}
      400 on validation or creation failure
    """

    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def post(self, request):
        request.META["_gs_actor_role"] = "admin"

        qry_data, err = _parse_qry(request)
        if err:
            return err

        ser = ResourceCreateQrySerializer(data=qry_data)
        if not ser.is_valid():
            return _validation_error(ser)

        details = ser.validated_data["qry"]["details"]
        try:
            resource = resource_create(**details)
        except Exception as exc:
            logger.exception("resource_create failed")
            return Response(
                {
                    "status": "error",
                    "code": "CREATE_FAILED",
                    "message": str(exc),
                },
                status=400,
            )

        return Response({"status": "success", "resource_id": str(resource.pk)}, status=201)


class ResourceModificationsView(APIView):
    """
    PUT /govstack/scheduler/resource/modifications

    Modifies an existing active Resource. All request data via query parameters:
      - requestor_id, request_token (auth)
      - resource_id: identifies the resource to modify
      - qry: JSON-encoded { "details": { <partial resource fields> } }

    Only fields present in qry.details are updated; absent fields are left unchanged.

    Returns:
      200 {"status": "success", "resource_id": "<pk>"}
      400 on missing/invalid params
      404 if no active resource with the given resource_id exists
    """

    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def put(self, request):
        request.META["_gs_actor_role"] = "admin"

        resource_id = request.query_params.get("resource_id", "").strip()
        if not resource_id:
            return Response(
                {
                    "status": "error",
                    "code": "MISSING_RESOURCE_ID",
                    "message": "resource_id query parameter is required.",
                },
                status=400,
            )

        qry_data, err = _parse_qry(request)
        if err:
            return err

        ser = ResourceModifySerializer(data=qry_data)
        if not ser.is_valid():
            return _validation_error(ser)

        details = ser.validated_data["details"]
        try:
            resource = resource_modify(resource_id=resource_id, **details)
        except Resource.DoesNotExist:
            return Response(
                {
                    "status": "error",
                    "code": "RESOURCE_NOT_FOUND",
                    "message": f"No active resource with id={resource_id}.",
                },
                status=404,
            )
        except Exception as exc:
            logger.exception("resource_modify failed for resource_id=%s", resource_id)
            return Response(
                {
                    "status": "error",
                    "code": "MODIFY_FAILED",
                    "message": str(exc),
                },
                status=400,
            )

        return Response({"status": "success", "resource_id": str(resource.pk)}, status=200)


class ResourceDeleteView(APIView):
    """
    DELETE /govstack/scheduler/resource

    Soft-deletes a Resource (sets is_active=False). All request data via query params:
      - requestor_id, request_token (auth)
      - resource_id: identifies the resource to soft-delete

    Does NOT hard-delete — FK dependents (Slots, GovStackAffiliation) remain intact
    for audit trail compliance.

    Returns:
      200 {"status": "success", "resource_id": "<resource_id>"}
      400 if resource_id is missing
      404 if no active resource with the given resource_id exists
    """

    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def delete(self, request):
        request.META["_gs_actor_role"] = "admin"

        resource_id = request.query_params.get("resource_id", "").strip()
        if not resource_id:
            return Response(
                {
                    "status": "error",
                    "code": "MISSING_RESOURCE_ID",
                    "message": "resource_id query parameter is required.",
                },
                status=400,
            )

        try:
            resource_delete(resource_id=resource_id)
        except Resource.DoesNotExist:
            return Response(
                {
                    "status": "error",
                    "code": "RESOURCE_NOT_FOUND",
                    "message": f"No active resource with id={resource_id}.",
                },
                status=404,
            )
        except Exception as exc:
            logger.exception("resource_delete failed for resource_id=%s", resource_id)
            return Response(
                {
                    "status": "error",
                    "code": "DELETE_FAILED",
                    "message": str(exc),
                },
                status=400,
            )

        return Response({"status": "success", "resource_id": str(resource_id)}, status=200)


class ResourceListDetailsView(APIView):
    """
    GET /govstack/scheduler/resource/list_details

    Returns a filtered union of active Resource and StaffProfile records shaped as
    GovStack resource dicts. All request data via query parameters:
      - requestor_id, request_token (auth)
      - qry: JSON-encoded {
            "resource_filter": { resource_id, name, category, phone, email, ... },
            "resource_details_required": { resource_id, name, category, phone, ... }
        }

    PIPEDA: StaffProfile.user.email is never returned. Only gs_phone (explicitly
    set by staff for GovStack callbacks) is exposed.

    Returns:
      200 {"status": "success", "data": [...]}
      400 on invalid params
    """

    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def get(self, request):
        request.META["_gs_actor_role"] = "organizer"

        qry_data, err = _parse_qry(request)
        if err:
            return err

        ser = ResourceListQrySerializer(data=qry_data)
        if not ser.is_valid():
            return _validation_error(ser)

        resource_filter = ser.validated_data.get("resource_filter", {})
        resource_details_required = ser.validated_data.get("resource_details_required", {})

        try:
            data = resource_list(
                resource_filter=resource_filter,
                resource_details_required=resource_details_required,
            )
        except Exception as exc:
            logger.exception("resource_list failed")
            return Response(
                {
                    "status": "error",
                    "code": "LIST_FAILED",
                    "message": str(exc),
                },
                status=400,
            )

        return Response({"status": "success", "data": data}, status=200)


class ResourceAvailabilityView(APIView):
    """
    GET /govstack/scheduler/resource/availability

    Returns free (available or partial) Slot records for resources matching the filter.
    All request data via query parameters:
      - requestor_id, request_token (auth)
      - qry: JSON-encoded {
            "free_resource_filter": {
                resource_id, Entity_id, from, to, category
            }
        }

    Note: the GovStack spec uses "Entity_id" (capital E) in the availability filter.

    Datetime strings (from/to) must be ISO 8601 — invalid strings return 400.

    Returns:
      200 {"status": "success", "data": [...]}
      400 on invalid params or datetime format error
    """

    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def get(self, request):
        request.META["_gs_actor_role"] = "resource"

        qry_data, err = _parse_qry(request)
        if err:
            return err

        free_resource_filter = qry_data.get("free_resource_filter", {})

        try:
            slots = resource_get_availability(free_resource_filter)
        except ValueError as exc:
            # Invalid datetime in from/to filter — surface as 400 without a stack trace.
            return Response(
                {
                    "status": "error",
                    "code": "INVALID_DATETIME",
                    "message": str(exc),
                },
                status=400,
            )
        except Exception as exc:
            logger.exception("resource_get_availability failed")
            return Response(
                {
                    "status": "error",
                    "code": "AVAILABILITY_ERROR",
                    "message": str(exc),
                },
                status=400,
            )

        return Response({"status": "success", "data": slots}, status=200)


# ===========================================================================
# Affiliation views (4 endpoints)
# ===========================================================================

class AffiliationNewView(APIView):
    """
    POST /govstack/scheduler/affiliation/new

    Create a new Affiliation linking a Resource to an Entity. All request data
    arrives as query parameters; affiliation details are embedded in `qry`.

    Expected qry shape:
      {"qry": {"details": {"resource_id": "...", "entity_id": "...",
                           "resource_category": "...", "work_days_hours": {...}}}}

    Returns:
      201 {"status": "success", "affiliation_id": "<pk>"}
      400 on validation or creation failure
      404 if resource_id or entity_id do not resolve to active records
      409 on duplicate (resource, entity) pair
    """

    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def post(self, request):
        request.META["_gs_actor_role"] = "admin"

        qry_data, err = _parse_qry(request)
        if err:
            return err

        ser = AffiliationCreateQrySerializer(data=qry_data)
        if not ser.is_valid():
            return _validation_error(ser)

        details = ser.validated_data["qry"]["details"]
        resource_id = details.get("resource_id", "")
        entity_id = details.get("entity_id", "")

        if not resource_id:
            return Response(
                {
                    "status": "error",
                    "code": "MISSING_RESOURCE_ID",
                    "message": "details.resource_id is required.",
                },
                status=400,
            )
        if not entity_id:
            return Response(
                {
                    "status": "error",
                    "code": "MISSING_ENTITY_ID",
                    "message": "details.entity_id is required.",
                },
                status=400,
            )

        try:
            aff = affiliation_create(
                resource_id=resource_id,
                entity_id=entity_id,
                resource_category=details.get("resource_category", ""),
                work_days_hours=details.get("work_days_hours"),
            )
        except Resource.DoesNotExist:
            return Response(
                {
                    "status": "error",
                    "code": "RESOURCE_NOT_FOUND",
                    "message": f"No active resource with id={resource_id}.",
                },
                status=404,
            )
        except Organization.DoesNotExist:
            return Response(
                {
                    "status": "error",
                    "code": "ENTITY_NOT_FOUND",
                    "message": f"No active entity with id={entity_id}.",
                },
                status=404,
            )
        except ValueError as exc:
            # Duplicate (resource, entity) pair — service raises ValueError wrapping IntegrityError.
            return Response(
                {
                    "status": "error",
                    "code": "DUPLICATE_AFFILIATION",
                    "message": str(exc),
                },
                status=409,
            )
        except Exception as exc:
            logger.exception("affiliation_create failed")
            return Response(
                {
                    "status": "error",
                    "code": "CREATE_FAILED",
                    "message": str(exc),
                },
                status=400,
            )

        return Response(
            {"status": "success", "affiliation_id": str(aff.pk)},
            status=201,
        )


class AffiliationModificationsView(APIView):
    """
    PUT /govstack/scheduler/affiliation/modifications

    Modify an existing Affiliation. Requires `affiliation_id` and `qry` query params.

    Modifiable fields: resource_category, work_days_hours.
    resource_id and entity_id cannot be changed (delete + recreate instead).

    Expected qry shape:
      {"details": {"resource_category": "...", "work_days_hours": {...}}}

    Returns:
      200 {"status": "success", "affiliation_id": "<pk>"}
      400 on missing/invalid params
      404 if no affiliation with the given affiliation_id exists
    """

    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def put(self, request):
        request.META["_gs_actor_role"] = "admin"

        affiliation_id = request.query_params.get("affiliation_id", "").strip()
        if not affiliation_id:
            return Response(
                {
                    "status": "error",
                    "code": "MISSING_AFFILIATION_ID",
                    "message": "affiliation_id query parameter is required.",
                },
                status=400,
            )

        qry_data, err = _parse_qry(request)
        if err:
            return err

        ser = AffiliationModifySerializer(data=qry_data)
        if not ser.is_valid():
            return _validation_error(ser)

        details = ser.validated_data["details"]
        try:
            aff = affiliation_modify(
                affiliation_id=affiliation_id,
                resource_category=details.get("resource_category"),
                work_days_hours=details.get("work_days_hours"),
            )
        except GovStackAffiliation.DoesNotExist:
            return Response(
                {
                    "status": "error",
                    "code": "AFFILIATION_NOT_FOUND",
                    "message": f"No affiliation with id={affiliation_id}.",
                },
                status=404,
            )
        except Exception as exc:
            logger.exception("affiliation_modify failed for affiliation_id=%s", affiliation_id)
            return Response(
                {
                    "status": "error",
                    "code": "MODIFY_FAILED",
                    "message": str(exc),
                },
                status=400,
            )

        return Response(
            {"status": "success", "affiliation_id": str(aff.pk)},
            status=200,
        )


class AffiliationDeleteView(APIView):
    """
    DELETE /govstack/scheduler/affiliation

    Hard-delete an Affiliation. Requires `affiliation_id` query parameter.

    GovStackAffiliation has no is_active field and no downstream FK dependents,
    so a hard delete is performed (unlike Entity which uses soft-delete).

    Returns:
      200 {"status": "success", "affiliation_id": "<affiliation_id>"}
      400 if affiliation_id is missing
      404 if no affiliation with the given affiliation_id exists
    """

    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def delete(self, request):
        request.META["_gs_actor_role"] = "admin"

        affiliation_id = request.query_params.get("affiliation_id", "").strip()
        if not affiliation_id:
            return Response(
                {
                    "status": "error",
                    "code": "MISSING_AFFILIATION_ID",
                    "message": "affiliation_id query parameter is required.",
                },
                status=400,
            )

        try:
            affiliation_delete(affiliation_id)
        except GovStackAffiliation.DoesNotExist:
            return Response(
                {
                    "status": "error",
                    "code": "AFFILIATION_NOT_FOUND",
                    "message": f"No affiliation with id={affiliation_id}.",
                },
                status=404,
            )
        except Exception as exc:
            logger.exception("affiliation_delete failed for affiliation_id=%s", affiliation_id)
            return Response(
                {
                    "status": "error",
                    "code": "DELETE_FAILED",
                    "message": str(exc),
                },
                status=400,
            )

        return Response(
            {"status": "success", "affiliation_id": str(affiliation_id)},
            status=200,
        )


class AffiliationListDetailsView(APIView):
    """
    GET /govstack/scheduler/affiliation/list_details

    List Affiliations matching the supplied filters. All parameters arrive as
    query params; filter and field-selection objects are embedded in `qry`.

    Expected qry shape:
      {
        "affiliation_filter": {"entity_id": "5", "resource_category": "nurse"},
        "affiliation_details_required": {"affiliation_id": true, "resource_id": true,
                                         "entity_id": true, "work_days_hours": false}
      }

    Returns:
      200 {"status": "success", "data": [...]}
      400 on missing/invalid params
    """

    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def get(self, request):
        request.META["_gs_actor_role"] = "admin"

        qry_data, err = _parse_qry(request)
        if err:
            return err

        ser = AffiliationListQrySerializer(data=qry_data)
        if not ser.is_valid():
            return _validation_error(ser)

        affiliation_filter = ser.validated_data.get("affiliation_filter", {})
        affiliation_details_required = ser.validated_data.get("affiliation_details_required", {})

        try:
            data = affiliation_list(affiliation_filter, affiliation_details_required)
        except Exception as exc:
            logger.exception("affiliation_list failed")
            return Response(
                {
                    "status": "error",
                    "code": "LIST_FAILED",
                    "message": str(exc),
                },
                status=400,
            )

        return Response({"status": "success", "data": data}, status=200)
