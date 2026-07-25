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
  All Wave B views declare a class-level gs_actor_role attribute so the role is
  visible to GovStackSchedulerRolePermission during DRF's check_permissions() call
  (which runs BEFORE the handler method). The handler also sets
  request.META["_gs_actor_role"] for logging middleware and downstream code that
  reads it from META (belt-and-suspenders).

  Entity / Resource CRUD / Affiliation → "admin"
  ResourceListDetailsView              → "organizer"
  ResourceAvailabilityView             → "resource"

PIPEDA:
  - No PII in log messages. Organization names are not PII.
  - StaffProfile.user.email is never returned from resource list/availability.
  - Only gs_phone (explicitly set by staff for GovStack callbacks) is exposed.
"""
from __future__ import annotations

import json
import logging

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError

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
    AppointmentCreateQrySerializer,
    AppointmentListQrySerializer,
    AppointmentModifySerializer,
    EntityCreateQrySerializer,
    EntityListQrySerializer,
    EntityModifySerializer,
    EventCreateQrySerializer,
    EventListQrySerializer,
    EventModifySerializer,
    ResourceAvailabilityFilterSerializer,
    ResourceCreateQrySerializer,
    ResourceListQrySerializer,
    ResourceModifySerializer,
    SubscriberCreateQrySerializer,
    SubscriberListQrySerializer,
    SubscriberModifySerializer,
)
from apps.appointments.models import (
    Booking,
    GovStackAffiliation,
    GovStackSubscriberProfile,
    Organization,
    Resource,
    Slot,
)
from apps.appointments.services.govstack_affiliation import (
    affiliation_create,
    affiliation_delete,
    affiliation_list,
    affiliation_modify,
)
from apps.appointments.services.govstack_appointment import (
    appointment_create,
    appointment_delete,
    appointment_list,
    appointment_modify,
)
from apps.appointments.services.booking import (
    BookingError,
    CitizenSuspendedError,
    FrequencyWindowError,
    InvalidStatusTransitionError,
    MaxActiveBookingsError,
    RescheduleCountError,
    RescheduleWindowError,
    SlotFullError,
)
from apps.appointments.services.govstack_entity import (
    entity_create,
    entity_delete,
    entity_list,
    entity_modify,
)
from apps.appointments.services.govstack_event import (
    event_create,
    event_delete,
    event_list,
    event_modify,
)
from apps.appointments.services.govstack_subscriber import (
    subscriber_create,
    subscriber_delete,
    subscriber_list,
    subscriber_modify,
)
from apps.appointments.services.govstack_resource import (
    resource_create,
    resource_delete,
    resource_get_availability,
    resource_list,
    resource_modify,
)

logger = logging.getLogger("civicos.appointments.services.govstack_views")


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


def _require_int_id(value: str, param_name: str) -> tuple[int | None, Response | None]:
    """
    Coerce a query-param ID string to int.
    Returns (int_value, None) on success or (None, 400 Response) on failure.
    """
    try:
        return int(value), None
    except (ValueError, TypeError):
        return None, Response(
            {
                "status": "error",
                "code": f"INVALID_{param_name.upper()}",
                "message": f"{param_name} must be a positive integer.",
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

    gs_actor_role = "admin"                           # visible during check_permissions()
    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def post(self, request):
        request.META["_gs_actor_role"] = "admin"     # belt-and-suspenders for middleware

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
        except RuntimeError:
            logger.exception("entity_create: slug generation failed")
            return Response(
                {
                    "status": "error",
                    "code": "CREATE_FAILED",
                    "message": (
                        "Unable to generate a unique identifier for this organization. "
                        "Please try again."
                    ),
                },
                status=400,
            )
        except Exception:
            logger.exception("entity_create failed")
            return Response(
                {
                    "status": "error",
                    "code": "CREATE_FAILED",
                    "message": "Entity creation failed. Please check your input and try again.",
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

    gs_actor_role = "admin"
    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def put(self, request):
        request.META["_gs_actor_role"] = "admin"

        entity_id_str = request.query_params.get("entity_id", "").strip()
        if not entity_id_str:
            return Response(
                {
                    "status": "error",
                    "code": "MISSING_ENTITY_ID",
                    "message": "entity_id query parameter is required.",
                },
                status=400,
            )
        entity_id, err = _require_int_id(entity_id_str, "entity_id")
        if err:
            return err

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
                    "message": f"No active entity with id={entity_id_str}.",
                },
                status=404,
            )
        except Exception:
            logger.exception("entity_modify failed for entity_id=%s", entity_id_str)
            return Response(
                {
                    "status": "error",
                    "code": "MODIFY_FAILED",
                    "message": "Operation failed. Please check your input and try again.",
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

    gs_actor_role = "admin"
    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def delete(self, request):
        request.META["_gs_actor_role"] = "admin"

        entity_id_str = request.query_params.get("entity_id", "").strip()
        if not entity_id_str:
            return Response(
                {
                    "status": "error",
                    "code": "MISSING_ENTITY_ID",
                    "message": "entity_id query parameter is required.",
                },
                status=400,
            )
        entity_id, err = _require_int_id(entity_id_str, "entity_id")
        if err:
            return err

        try:
            entity_delete(entity_id)
        except Organization.DoesNotExist:
            return Response(
                {
                    "status": "error",
                    "code": "ENTITY_NOT_FOUND",
                    "message": f"No active entity with id={entity_id_str}.",
                },
                status=404,
            )
        except Exception:
            logger.exception("entity_delete failed for entity_id=%s", entity_id_str)
            return Response(
                {
                    "status": "error",
                    "code": "DELETE_FAILED",
                    "message": "Operation failed. Please check your input and try again.",
                },
                status=400,
            )

        return Response({"status": "success", "entity_id": str(entity_id_str)}, status=200)


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

    gs_actor_role = "admin"
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
        except Exception:
            logger.exception("entity_list failed")
            return Response(
                {
                    "status": "error",
                    "code": "LIST_FAILED",
                    "message": "Operation failed. Please check your input and try again.",
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

    gs_actor_role = "admin"
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
        except ValueError as exc:
            # Service-layer validation error (e.g. invalid alert_preference) — safe to surface.
            return Response(
                {
                    "status": "error",
                    "code": "CREATE_FAILED",
                    "message": str(exc),
                },
                status=400,
            )
        except Exception:
            logger.exception("resource_create failed")
            return Response(
                {
                    "status": "error",
                    "code": "CREATE_FAILED",
                    "message": "Operation failed. Please check your input and try again.",
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

    gs_actor_role = "admin"
    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def put(self, request):
        request.META["_gs_actor_role"] = "admin"

        resource_id_str = request.query_params.get("resource_id", "").strip()
        if not resource_id_str:
            return Response(
                {
                    "status": "error",
                    "code": "MISSING_RESOURCE_ID",
                    "message": "resource_id query parameter is required.",
                },
                status=400,
            )
        resource_id, err = _require_int_id(resource_id_str, "resource_id")
        if err:
            return err

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
                    "message": f"No active resource with id={resource_id_str}.",
                },
                status=404,
            )
        except ValueError as exc:
            # Service-layer validation error (e.g. invalid alert_preference) — safe to surface.
            return Response(
                {
                    "status": "error",
                    "code": "MODIFY_FAILED",
                    "message": str(exc),
                },
                status=400,
            )
        except Exception:
            logger.exception("resource_modify failed for resource_id=%s", resource_id_str)
            return Response(
                {
                    "status": "error",
                    "code": "MODIFY_FAILED",
                    "message": "Operation failed. Please check your input and try again.",
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

    gs_actor_role = "admin"
    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def delete(self, request):
        request.META["_gs_actor_role"] = "admin"

        resource_id_str = request.query_params.get("resource_id", "").strip()
        if not resource_id_str:
            return Response(
                {
                    "status": "error",
                    "code": "MISSING_RESOURCE_ID",
                    "message": "resource_id query parameter is required.",
                },
                status=400,
            )
        resource_id, err = _require_int_id(resource_id_str, "resource_id")
        if err:
            return err

        try:
            resource_delete(resource_id=resource_id)
        except Resource.DoesNotExist:
            return Response(
                {
                    "status": "error",
                    "code": "RESOURCE_NOT_FOUND",
                    "message": f"No active resource with id={resource_id_str}.",
                },
                status=404,
            )
        except Exception:
            logger.exception("resource_delete failed for resource_id=%s", resource_id_str)
            return Response(
                {
                    "status": "error",
                    "code": "DELETE_FAILED",
                    "message": "Operation failed. Please check your input and try again.",
                },
                status=400,
            )

        return Response({"status": "success", "resource_id": str(resource_id_str)}, status=200)


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

    gs_actor_role = "organizer"
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
        except Exception:
            logger.exception("resource_list failed")
            return Response(
                {
                    "status": "error",
                    "code": "LIST_FAILED",
                    "message": "Operation failed. Please check your input and try again.",
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

    Datetime strings (from/to) must be ISO 8601 with timezone offset — naive or
    invalid strings return 400.

    Returns:
      200 {"status": "success", "data": [...]}
      400 on invalid params or datetime format error
    """

    gs_actor_role = "resource"
    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def get(self, request):
        request.META["_gs_actor_role"] = "resource"

        qry_data, err = _parse_qry(request)
        if err:
            return err

        # Normalise the filter through the serializer (renames from→from_dt, to→to_dt).
        raw_filter = qry_data.get("free_resource_filter", {})
        avail_ser = ResourceAvailabilityFilterSerializer(data=raw_filter)
        if not avail_ser.is_valid():
            return _validation_error(avail_ser)

        free_resource_filter = avail_ser.validated_data

        try:
            slots = resource_get_availability(free_resource_filter)
        except ValueError as exc:
            # Invalid or naive datetime in from/to filter — surface as 400.
            return Response(
                {
                    "status": "error",
                    "code": "INVALID_DATETIME",
                    "message": str(exc),
                },
                status=400,
            )
        except Exception:
            logger.exception("resource_get_availability failed")
            return Response(
                {
                    "status": "error",
                    "code": "AVAILABILITY_ERROR",
                    "message": "Availability query failed. Please check your filter parameters.",
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

    gs_actor_role = "admin"
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
        except Exception:
            logger.exception("affiliation_create failed")
            return Response(
                {
                    "status": "error",
                    "code": "CREATE_FAILED",
                    "message": "Operation failed. Please check your input and try again.",
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

    gs_actor_role = "admin"
    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def put(self, request):
        request.META["_gs_actor_role"] = "admin"

        affiliation_id_str = request.query_params.get("affiliation_id", "").strip()
        if not affiliation_id_str:
            return Response(
                {
                    "status": "error",
                    "code": "MISSING_AFFILIATION_ID",
                    "message": "affiliation_id query parameter is required.",
                },
                status=400,
            )
        affiliation_id, err = _require_int_id(affiliation_id_str, "affiliation_id")
        if err:
            return err

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
                    "message": f"No affiliation with id={affiliation_id_str}.",
                },
                status=404,
            )
        except Exception:
            logger.exception("affiliation_modify failed for affiliation_id=%s", affiliation_id_str)
            return Response(
                {
                    "status": "error",
                    "code": "MODIFY_FAILED",
                    "message": "Operation failed. Please check your input and try again.",
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

    gs_actor_role = "admin"
    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def delete(self, request):
        request.META["_gs_actor_role"] = "admin"

        affiliation_id_str = request.query_params.get("affiliation_id", "").strip()
        if not affiliation_id_str:
            return Response(
                {
                    "status": "error",
                    "code": "MISSING_AFFILIATION_ID",
                    "message": "affiliation_id query parameter is required.",
                },
                status=400,
            )
        affiliation_id, err = _require_int_id(affiliation_id_str, "affiliation_id")
        if err:
            return err

        try:
            affiliation_delete(affiliation_id)
        except GovStackAffiliation.DoesNotExist:
            return Response(
                {
                    "status": "error",
                    "code": "AFFILIATION_NOT_FOUND",
                    "message": f"No affiliation with id={affiliation_id_str}.",
                },
                status=404,
            )
        except Exception:
            logger.exception("affiliation_delete failed for affiliation_id=%s", affiliation_id_str)
            return Response(
                {
                    "status": "error",
                    "code": "DELETE_FAILED",
                    "message": "Operation failed. Please check your input and try again.",
                },
                status=400,
            )

        return Response(
            {"status": "success", "affiliation_id": str(affiliation_id_str)},
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

    gs_actor_role = "admin"
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
        except Exception:
            logger.exception("affiliation_list failed")
            return Response(
                {
                    "status": "error",
                    "code": "LIST_FAILED",
                    "message": "Operation failed. Please check your input and try again.",
                },
                status=400,
            )

        return Response({"status": "success", "data": data}, status=200)


# ===========================================================================
# Subscriber views (4 endpoints)  — Wave C
# ===========================================================================

class SubscriberNewView(APIView):
    """
    POST /govstack/scheduler/subscriber/new

    Create a new Subscriber (User + GovStackSubscriberProfile). All request data
    arrives as query parameters; subscriber details are embedded in `qry`.

    Expected qry shape:
      {"qry": {"details": {"name": "...", "category": "...", "phone": "...",
                           "email": "...", "alert_url": "...",
                           "alert_preference": "...", "status_poll_url": "..."}}}

    Returns:
      201 {"status": "success", "subscriber_id": "<user_pk>"}
      400 on validation or creation failure

    PIPEDA: subscriber PII (name, email, phone) must NOT appear in log messages.
    """

    gs_actor_role = "admin"
    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def post(self, request):
        request.META["_gs_actor_role"] = "admin"

        qry_data, err = _parse_qry(request)
        if err:
            return err

        ser = SubscriberCreateQrySerializer(data=qry_data)
        if not ser.is_valid():
            return _validation_error(ser)

        details = ser.validated_data["qry"]["details"]
        try:
            profile = subscriber_create(
                name=details.get("name", ""),
                category=details.get("category", ""),
                phone=details.get("phone", ""),
                email=details.get("email", ""),
                alert_url=details.get("alert_url", ""),
                alert_preference=details.get("alert_preference", ""),
                status_poll_url=details.get("status_poll_url", ""),
            )
        except ValueError:
            # Do not log or surface the exception message — it may contain PII (email).
            return Response(
                {
                    "status": "error",
                    "code": "SUBSCRIBER_CREATE_FAILED",
                    "message": "Subscriber creation failed. Please check the supplied details.",
                },
                status=400,
            )
        except Exception:
            logger.exception("subscriber_create failed")
            return Response(
                {
                    "status": "error",
                    "code": "SUBSCRIBER_CREATE_FAILED",
                    "message": "Operation failed. Please check your input and try again.",
                },
                status=400,
            )

        return Response({"status": "success", "subscriber_id": str(profile.user_id)}, status=201)


class SubscriberModificationsView(APIView):
    """
    PUT /govstack/scheduler/subscriber/modifications

    Modify an existing Subscriber. Requires `subscriber_id` and `qry` query params.

    Expected qry shape:
      {"details": {"name": "...", "category": "...", ...}}  (all fields optional)

    Returns:
      200 {"status": "success", "subscriber_id": "<user_pk>"}
      400 on missing/invalid params or modification failure
      404 if no subscriber with the given subscriber_id exists

    PIPEDA: subscriber PII must NOT appear in log messages.
    """

    gs_actor_role = "admin"
    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def put(self, request):
        request.META["_gs_actor_role"] = "admin"

        subscriber_id_str = request.query_params.get("subscriber_id", "").strip()
        if not subscriber_id_str:
            return Response(
                {
                    "status": "error",
                    "code": "MISSING_SUBSCRIBER_ID",
                    "message": "subscriber_id query parameter is required.",
                },
                status=400,
            )
        int_id, err = _require_int_id(subscriber_id_str, "subscriber_id")
        if err:
            return err

        qry_data, err = _parse_qry(request)
        if err:
            return err

        ser = SubscriberModifySerializer(data=qry_data)
        if not ser.is_valid():
            return _validation_error(ser)

        details = ser.validated_data["details"]
        try:
            profile = subscriber_modify(
                subscriber_id=int_id,
                name=details.get("name"),
                category=details.get("category"),
                phone=details.get("phone"),
                email=details.get("email"),
                alert_url=details.get("alert_url"),
                alert_preference=details.get("alert_preference"),
                status_poll_url=details.get("status_poll_url"),
            )
        except GovStackSubscriberProfile.DoesNotExist:
            return Response(
                {
                    "status": "error",
                    "code": "SUBSCRIBER_NOT_FOUND",
                    "message": f"No active subscriber with id={subscriber_id_str}.",
                },
                status=404,
            )
        except ValueError:
            # Do not log or surface the exception message — it may contain PII (email).
            return Response(
                {
                    "status": "error",
                    "code": "SUBSCRIBER_MODIFY_FAILED",
                    "message": "Subscriber update failed. Please check the supplied details.",
                },
                status=400,
            )
        except Exception:
            logger.exception("subscriber_modify failed for subscriber_id=%s", subscriber_id_str)
            return Response(
                {
                    "status": "error",
                    "code": "MODIFY_FAILED",
                    "message": "Operation failed. Please check your input and try again.",
                },
                status=400,
            )

        return Response({"status": "success", "subscriber_id": str(profile.user_id)}, status=200)


class SubscriberDeleteView(APIView):
    """
    DELETE /govstack/scheduler/subscriber

    Soft-delete a Subscriber (sets User.is_active=False). Requires `subscriber_id`
    query parameter.

    GovStackSubscriberProfile is retained for audit trail compliance.

    Returns:
      200 {"status": "success", "subscriber_id": "<subscriber_id>"}
      400 if subscriber_id is missing
      404 if no subscriber with the given subscriber_id exists

    PIPEDA: subscriber PII must NOT appear in log messages.
    """

    gs_actor_role = "admin"
    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def delete(self, request):
        request.META["_gs_actor_role"] = "admin"

        subscriber_id_str = request.query_params.get("subscriber_id", "").strip()
        if not subscriber_id_str:
            return Response(
                {
                    "status": "error",
                    "code": "MISSING_SUBSCRIBER_ID",
                    "message": "subscriber_id query parameter is required.",
                },
                status=400,
            )
        int_id, err = _require_int_id(subscriber_id_str, "subscriber_id")
        if err:
            return err

        try:
            subscriber_delete(subscriber_id=int_id)
        except GovStackSubscriberProfile.DoesNotExist:
            return Response(
                {
                    "status": "error",
                    "code": "SUBSCRIBER_NOT_FOUND",
                    "message": f"No active subscriber with id={subscriber_id_str}.",
                },
                status=404,
            )
        except Exception:
            logger.exception("subscriber_delete failed for subscriber_id=%s", subscriber_id_str)
            return Response(
                {
                    "status": "error",
                    "code": "DELETE_FAILED",
                    "message": "Operation failed. Please check your input and try again.",
                },
                status=400,
            )

        return Response({"status": "success", "subscriber_id": str(int_id)}, status=200)


class SubscriberListDetailsView(APIView):
    """
    GET /govstack/scheduler/subscriber/list_details

    List Subscribers matching the supplied filters. All parameters arrive as
    query params; filter and field-selection objects are embedded in `qry`.

    Expected qry shape:
      {
        "subscriber_filter": {"subscriber_id": "5", "name": "Alice"},
        "subscriber_details_required": {"subscriber_id": true, "name": true, "email": false}
      }

    Returns:
      200 {"status": "success", "data": [...]}
      400 on invalid params

    PIPEDA: email, phone, and name are only returned when explicitly requested via
    subscriber_details_required. subscriber_id is always included.
    """

    gs_actor_role = "organizer"
    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def get(self, request):
        request.META["_gs_actor_role"] = "organizer"

        qry_data, err = _parse_qry(request)
        if err:
            return err

        ser = SubscriberListQrySerializer(data=qry_data)
        if not ser.is_valid():
            return _validation_error(ser)

        subscriber_filter = ser.validated_data.get("subscriber_filter", {})
        # Use serializer defaults when caller omits subscriber_details_required entirely.
        subscriber_details_required = ser.validated_data.get("subscriber_details_required") or {}

        try:
            results = subscriber_list(subscriber_filter, subscriber_details_required)
        except ValueError:
            return Response(
                {
                    "status": "error",
                    "code": "LIST_FILTER_INVALID",
                    "message": "Invalid filter parameters. Please check subscriber_filter values.",
                },
                status=400,
            )
        except Exception:
            logger.exception("subscriber_list failed")
            return Response(
                {
                    "status": "error",
                    "code": "LIST_FAILED",
                    "message": "Operation failed. Please check your input and try again.",
                },
                status=400,
            )

        return Response(
            {
                "status": "success",
                "data": results,
                "truncated": len(results) == 500,
            },
            status=200,
        )


# ===========================================================================
# Event views (4 endpoints) — Wave D
# ===========================================================================

class EventNewView(APIView):
    """
    POST /govstack/scheduler/event/new

    Create a new Event batch (one dedicated AppointmentType + one Slot per
    entry in slots[] — see services.govstack_event.event_create FIX 1). All
    request data arrives as query parameters; event details are embedded in `qry`.

    Expected qry shape:
      {"qry": {"details": {"name": "...", "slots": [{"from": "...", "to": "..."}],
                           "status": "available", ...}}}

    Returns:
      201 {"status": "success", "event_id": "<first-slot-pk>", "event_ids": ["<slot_pk>", ...]}
      400 on validation or creation failure

    Response field convention (dual-field, per Wave D review FIX 2):
      "event_id" (singular) is the spec-compliant primary field — every other
      */new endpoint in the module (and in the real GovStack OpenAPI spec)
      returns a singular "<entity>_id", never a plural array. It is set to the
      FIRST created slot's PK, so a single-slot POST (the common case) is
      fully spec-compliant on its own.
      "event_ids" (plural) is an ADDITIONAL, documented CivicOS extension
      field containing every created event_id in the batch, so multi-slot
      batch-create callers can still retrieve all of them from one response
      without a follow-up list call.
    """

    gs_actor_role = "organizer"
    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def post(self, request):
        request.META["_gs_actor_role"] = "organizer"

        qry_data, err = _parse_qry(request)
        if err:
            return err

        ser = EventCreateQrySerializer(data=qry_data)
        if not ser.is_valid():
            return _validation_error(ser)

        details = ser.validated_data["qry"]["details"]
        try:
            created_slots = event_create(
                name=details.get("name", ""),
                description=details.get("description", ""),
                category=details.get("category", ""),
                host_entity_id=details.get("host_entity_id", ""),
                slots=details.get("slots", []),
                deadline=details.get("deadline", ""),
                subscriber_limit=details.get("subscriber_limit", ""),
                terms=details.get("terms", ""),
                status=details.get("status", ""),
                venue=details.get("venue"),
            )
        except ValueError:
            return Response(
                {
                    "status": "error",
                    "code": "EVENT_CREATE_FAILED",
                    "message": "Event creation failed. Please check the supplied details.",
                },
                status=400,
            )
        except Exception:
            logger.exception("event_create failed")
            return Response(
                {
                    "status": "error",
                    "code": "CREATE_FAILED",
                    "message": "Operation failed.",
                },
                status=400,
            )

        event_ids = [str(s.pk) for s in created_slots]
        return Response(
            {"status": "success", "event_id": event_ids[0], "event_ids": event_ids},
            status=201,
        )


class EventModificationsView(APIView):
    """
    PUT /govstack/scheduler/event/modifications

    Modify an existing Event (Slot + AppointmentType). Requires `event_id` and
    `qry` query parameters.

    Expected qry shape (real event_details schema — flat from/to, no slots
    array; see govstack_serializers.EventModifyDetailsSerializer FIX 3):
      {"details": {"name": "...", "from": "...", "to": "...", "status": "...", ...}}  (all optional)

    Returns:
      200 {"status": "success", "event_id": "<event_id>"}
      400 on missing/invalid params or modification failure
      404 if no event with the given event_id exists
    """

    gs_actor_role = "organizer"
    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def put(self, request):
        request.META["_gs_actor_role"] = "organizer"

        event_id_str = request.query_params.get("event_id", "").strip()
        if not event_id_str:
            return Response(
                {
                    "status": "error",
                    "code": "MISSING_EVENT_ID",
                    "message": "event_id is required.",
                },
                status=400,
            )

        qry_data, err = _parse_qry(request)
        if err:
            return err

        ser = EventModifySerializer(data=qry_data)
        if not ser.is_valid():
            return _validation_error(ser)

        details = ser.validated_data["details"]
        try:
            event_modify(
                event_id=event_id_str,
                name=details.get("name"),
                description=details.get("description"),
                category=details.get("category"),
                host_entity_id=details.get("host_entity_id"),
                from_dt=details.get("from_"),
                to_dt=details.get("to"),
                deadline=details.get("deadline"),
                subscriber_limit=details.get("subscriber_limit"),
                terms=details.get("terms"),
                status=details.get("status"),
                venue=details.get("venue"),
            )
        except (Slot.DoesNotExist, DjangoValidationError):
            return Response(
                {
                    "status": "error",
                    "code": "EVENT_NOT_FOUND",
                    "message": f"No event with id={event_id_str}.",
                },
                status=404,
            )
        except ValueError:
            return Response(
                {
                    "status": "error",
                    "code": "EVENT_MODIFY_FAILED",
                    "message": "Event modification failed. Please check the supplied details.",
                },
                status=400,
            )
        except Exception:
            logger.exception("event_modify failed for event_id=%s", event_id_str)
            return Response(
                {
                    "status": "error",
                    "code": "MODIFY_FAILED",
                    "message": "Operation failed.",
                },
                status=400,
            )

        return Response({"status": "success", "event_id": event_id_str}, status=200)


class EventDeleteView(APIView):
    """
    DELETE /govstack/scheduler/event

    Soft-delete an Event (sets Slot.status = "cancelled"). Requires `event_id`
    query parameter.

    Returns:
      200 {"status": "success", "event_id": "<event_id>"}
      400 if event_id is missing
      404 if no event with the given event_id exists
    """

    gs_actor_role = "organizer"
    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def delete(self, request):
        request.META["_gs_actor_role"] = "organizer"

        event_id_str = request.query_params.get("event_id", "").strip()
        if not event_id_str:
            return Response(
                {
                    "status": "error",
                    "code": "MISSING_EVENT_ID",
                    "message": "event_id is required.",
                },
                status=400,
            )

        try:
            event_delete(event_id=event_id_str)
        except (Slot.DoesNotExist, DjangoValidationError):
            return Response(
                {
                    "status": "error",
                    "code": "EVENT_NOT_FOUND",
                    "message": f"No event with id={event_id_str}.",
                },
                status=404,
            )
        except Exception:
            logger.exception("event_delete failed for event_id=%s", event_id_str)
            return Response(
                {
                    "status": "error",
                    "code": "DELETE_FAILED",
                    "message": "Operation failed.",
                },
                status=400,
            )

        return Response({"status": "success", "event_id": event_id_str}, status=200)


class EventListDetailsView(APIView):
    """
    GET /govstack/scheduler/event/list_details

    List Events matching the supplied filters. All parameters arrive as query
    params; filter and field-selection objects are embedded in `qry`.

    Expected qry shape:
      {
        "event_filter": {"status": "available", "name": "..."},
        "event_details_required": {"event_id": true, "name": true, "slots": false}
      }

    Returns:
      200 {"status": "success", "data": [...], "truncated": <bool>}
      400 on invalid params
    """

    gs_actor_role = "organizer"
    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def get(self, request):
        request.META["_gs_actor_role"] = "organizer"

        qry_data, err = _parse_qry(request)
        if err:
            return err

        ser = EventListQrySerializer(data=qry_data)
        if not ser.is_valid():
            return _validation_error(ser)

        event_filter = ser.validated_data.get("event_filter", {})
        # Use serializer defaults when caller omits event_details_required entirely.
        event_details_required = ser.validated_data.get("event_details_required") or {}

        try:
            results = event_list(
                event_filter=event_filter,
                event_details_required=event_details_required,
            )
        except ValueError:
            return Response(
                {
                    "status": "error",
                    "code": "EVENT_LIST_FILTER_INVALID",
                    "message": "Invalid filter parameters.",
                },
                status=400,
            )
        except Exception:
            logger.exception("event_list failed")
            return Response(
                {
                    "status": "error",
                    "code": "LIST_FAILED",
                    "message": "Operation failed.",
                },
                status=400,
            )

        return Response(
            {
                "status": "success",
                "data": results,
                "truncated": len(results) == 500,
            },
            status=200,
        )


# ===========================================================================
# Appointment views (4 endpoints) — Wave E
# ===========================================================================

class AppointmentNewView(APIView):
    """
    POST /govstack/scheduler/appointment/new

    Create a new Appointment (one or more CivicOS Bookings — one per
    event_id) as a single atomic batch. All request data arrives as query
    parameters; appointment details are embedded in `qry`.

    Expected qry shape (real spec key is "appointment_details", NOT
    "details" — verified against the GovStack OpenAPI spec; see
    govstack_serializers._AppointmentQryDetailsSerializer):
      {"qry": {"appointment_details": {"event_ids": ["..."], "exclusive": false,
                                        "participant_type": "subscriber",
                                        "participant_id": "...",
                                        "participant_entity_id": "..."}}}

    Returns:
      201 {"status": "success", "appointment_id": "<first-booking-pk>",
           "appointment_ids": ["<booking_pk>", ...]}
      400 on validation or creation failure
      404 if participant_id or any event_id does not resolve

    Response field convention (dual-field, matching EventNewView's
    event_id/event_ids precedent from the Wave D review): "appointment_id"
    (singular) is the spec-compliant primary field, set to the FIRST created
    Booking's PK. "appointment_ids" (plural) is an additional CivicOS
    extension field listing every Booking created in the batch.
    """

    gs_actor_role = "subscriber"
    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def post(self, request):
        request.META["_gs_actor_role"] = "subscriber"

        qry_data, err = _parse_qry(request)
        if err:
            return err

        ser = AppointmentCreateQrySerializer(data=qry_data)
        if not ser.is_valid():
            return _validation_error(ser)

        details = ser.validated_data["qry"]["appointment_details"]
        try:
            bookings = appointment_create(
                event_ids=details.get("event_ids"),
                participant_type=details.get("participant_type", ""),
                participant_id=details.get("participant_id", ""),
                participant_entity_id=details.get("participant_entity_id", ""),
                exclusive=details.get("exclusive", False),
            )
        except get_user_model().DoesNotExist:
            return Response(
                {
                    "status": "error",
                    "code": "PARTICIPANT_NOT_FOUND",
                    "message": "No active subscriber with the given participant_id.",
                },
                status=404,
            )
        except (Slot.DoesNotExist, DjangoValidationError):
            return Response(
                {
                    "status": "error",
                    "code": "EVENT_NOT_FOUND",
                    "message": "One or more event_ids were not found.",
                },
                status=404,
            )
        except SlotFullError:
            return Response(
                {
                    "status": "error",
                    "code": "SLOT_FULL",
                    "message": "One or more requested slots are full.",
                },
                status=400,
            )
        except CitizenSuspendedError:
            return Response(
                {
                    "status": "error",
                    "code": "PARTICIPANT_SUSPENDED",
                    "message": "This subscriber is currently suspended from self-booking.",
                },
                status=400,
            )
        except FrequencyWindowError:
            return Response(
                {
                    "status": "error",
                    "code": "FREQUENCY_WINDOW",
                    "message": (
                        "This subscriber has a recent booking within the service's "
                        "frequency window."
                    ),
                },
                status=400,
            )
        except MaxActiveBookingsError:
            return Response(
                {
                    "status": "error",
                    "code": "MAX_ACTIVE_BOOKINGS",
                    "message": "This subscriber has reached the maximum number of active bookings.",
                },
                status=400,
            )
        except BookingError:
            return Response(
                {
                    "status": "error",
                    "code": "APPOINTMENT_CREATE_FAILED",
                    "message": "Appointment creation failed.",
                },
                status=400,
            )
        except ValueError:
            return Response(
                {
                    "status": "error",
                    "code": "APPOINTMENT_CREATE_FAILED",
                    "message": "Appointment creation failed. Please check the supplied details.",
                },
                status=400,
            )
        except Exception:
            logger.exception("appointment_create failed")
            return Response(
                {
                    "status": "error",
                    "code": "CREATE_FAILED",
                    "message": "Operation failed.",
                },
                status=400,
            )

        appointment_ids = [str(b.pk) for b in bookings]
        return Response(
            {
                "status": "success",
                "appointment_id": appointment_ids[0],
                "appointment_ids": appointment_ids,
            },
            status=201,
        )


class AppointmentModificationsView(APIView):
    """
    PUT /govstack/scheduler/appointment/modifications

    Modify an existing Appointment (Booking) — reschedule (event_id), status
    transition (status_id), exclusive toggle, and/or participant_entity_id
    update. Requires `appointment_id` and `qry` query parameters.

    Expected qry shape:
      {"details": {"event_id": "...", "status_id": "confirmed", "exclusive": true,
                   "participant_entity_id": "..."}}  (all fields optional)

    Confirm/reject transitions are inherently staff/organizer actions in this
    domain (see govstack_appointment.py's actor-resolution design), so this
    endpoint declares gs_actor_role="organizer" — the correct minimum here,
    unlike create/delete which are subscriber-accessible.

    Returns:
      200 {"status": "success", "appointment_id": "<booking-pk>"}
      400 on missing/invalid params or modification failure
      404 if no appointment or target event_id is found

    NOTE: if a reschedule occurred (event_id differs from the appointment's
    current slot), the returned appointment_id is a NEW UUID, distinct from
    the appointment_id passed in the query string — this is correct and
    expected per appointment_modify()'s documented reschedule semantics.
    """

    gs_actor_role = "organizer"
    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def put(self, request):
        request.META["_gs_actor_role"] = "organizer"

        appointment_id_str = request.query_params.get("appointment_id", "").strip()
        if not appointment_id_str:
            return Response(
                {
                    "status": "error",
                    "code": "MISSING_APPOINTMENT_ID",
                    "message": "appointment_id is required.",
                },
                status=400,
            )

        qry_data, err = _parse_qry(request)
        if err:
            return err

        ser = AppointmentModifySerializer(data=qry_data)
        if not ser.is_valid():
            return _validation_error(ser)

        details = ser.validated_data["details"]
        try:
            booking = appointment_modify(
                appointment_id=appointment_id_str,
                event_id=details.get("event_id") or None,
                status_id=details.get("status_id") or None,
                exclusive=details.get("exclusive"),
                participant_entity_id=details.get("participant_entity_id"),
            )
        except (Booking.DoesNotExist, DjangoValidationError):
            return Response(
                {
                    "status": "error",
                    "code": "APPOINTMENT_NOT_FOUND",
                    "message": f"No appointment with id={appointment_id_str}.",
                },
                status=404,
            )
        except Slot.DoesNotExist:
            return Response(
                {
                    "status": "error",
                    "code": "EVENT_NOT_FOUND",
                    "message": "The target event_id was not found.",
                },
                status=404,
            )
        except RescheduleCountError:
            return Response(
                {
                    "status": "error",
                    "code": "RESCHEDULE_COUNT_EXCEEDED",
                    "message": "This appointment has reached its maximum reschedule count.",
                },
                status=400,
            )
        except RescheduleWindowError:
            return Response(
                {
                    "status": "error",
                    "code": "RESCHEDULE_WINDOW_TOO_CLOSE",
                    "message": "Too close to the appointment start time to reschedule.",
                },
                status=400,
            )
        except SlotFullError:
            return Response(
                {
                    "status": "error",
                    "code": "SLOT_FULL",
                    "message": "The target event is full.",
                },
                status=400,
            )
        except InvalidStatusTransitionError:
            return Response(
                {
                    "status": "error",
                    "code": "INVALID_STATUS_TRANSITION",
                    "message": (
                        "This status transition is not allowed from the appointment's "
                        "current state."
                    ),
                },
                status=400,
            )
        except BookingError:
            return Response(
                {
                    "status": "error",
                    "code": "APPOINTMENT_MODIFY_FAILED",
                    "message": "Appointment modification failed.",
                },
                status=400,
            )
        except ValueError:
            return Response(
                {
                    "status": "error",
                    "code": "APPOINTMENT_MODIFY_FAILED",
                    "message": "Appointment modification failed. Please check the supplied details.",
                },
                status=400,
            )
        except Exception:
            logger.exception(
                "appointment_modify failed for appointment_id=%s", appointment_id_str
            )
            return Response(
                {
                    "status": "error",
                    "code": "MODIFY_FAILED",
                    "message": "Operation failed.",
                },
                status=400,
            )

        return Response({"status": "success", "appointment_id": str(booking.pk)}, status=200)


class AppointmentDeleteView(APIView):
    """
    DELETE /govstack/scheduler/appointment

    Cancel an Appointment (Booking). Requires `appointment_id` query parameter.

    Returns:
      200 {"status": "success", "appointment_id": "<appointment_id>"}
      400 if appointment_id is missing, or the booking cannot be cancelled
          from its current status
      404 if no appointment with the given appointment_id exists
    """

    gs_actor_role = "subscriber"
    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def delete(self, request):
        request.META["_gs_actor_role"] = "subscriber"

        appointment_id_str = request.query_params.get("appointment_id", "").strip()
        if not appointment_id_str:
            return Response(
                {
                    "status": "error",
                    "code": "MISSING_APPOINTMENT_ID",
                    "message": "appointment_id is required.",
                },
                status=400,
            )

        try:
            appointment_delete(appointment_id=appointment_id_str)
        except (Booking.DoesNotExist, DjangoValidationError):
            return Response(
                {
                    "status": "error",
                    "code": "APPOINTMENT_NOT_FOUND",
                    "message": f"No appointment with id={appointment_id_str}.",
                },
                status=404,
            )
        except InvalidStatusTransitionError:
            return Response(
                {
                    "status": "error",
                    "code": "INVALID_STATUS_TRANSITION",
                    "message": "This appointment cannot be cancelled from its current status.",
                },
                status=400,
            )
        except Exception:
            logger.exception(
                "appointment_delete failed for appointment_id=%s", appointment_id_str
            )
            return Response(
                {
                    "status": "error",
                    "code": "DELETE_FAILED",
                    "message": "Operation failed.",
                },
                status=400,
            )

        return Response(
            {"status": "success", "appointment_id": appointment_id_str}, status=200
        )


class AppointmentListDetailsView(APIView):
    """
    GET /govstack/scheduler/appointment/list_details

    List Appointments matching the supplied filters. All parameters arrive as
    query params; filter and field-selection objects are embedded in `qry`.

    Expected qry shape:
      {
        "appointment_filter": {"participant_id": "5", "status": "confirmed"},
        "appointment_details_required": {"appointment_id": true, "event_details": true}
      }

    Returns:
      200 {"status": "success", "data": [...], "truncated": <bool>}
      400 on invalid filter parameters (e.g. malformed from/to datetimes)
    """

    gs_actor_role = "subscriber"
    authentication_classes = [GovStackSchedulerAuth]
    permission_classes = [GovStackSchedulerPermission]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "govstack_bb"

    def get(self, request):
        request.META["_gs_actor_role"] = "subscriber"

        qry_data, err = _parse_qry(request)
        if err:
            return err

        ser = AppointmentListQrySerializer(data=qry_data)
        if not ser.is_valid():
            return _validation_error(ser)

        appointment_filter = ser.validated_data.get("appointment_filter", {})
        appointment_details_required = ser.validated_data.get("appointment_details_required") or {}

        # AppointmentFilterSerializer's to_internal_value() remaps the wire-format
        # "from" key to "from_" (Python reserved word workaround — see
        # EventListDetailsView / EventFilterSerializer for the identical pattern).
        # appointment_list() expects the raw "from" key, so remap back here.
        if "from_" in appointment_filter:
            appointment_filter["from"] = appointment_filter.pop("from_")

        try:
            results = appointment_list(
                appointment_filter=appointment_filter,
                appointment_details_required=appointment_details_required,
            )
        except ValueError:
            return Response(
                {
                    "status": "error",
                    "code": "APPOINTMENT_LIST_FILTER_INVALID",
                    "message": "Invalid filter parameters.",
                },
                status=400,
            )
        except Exception:
            logger.exception("appointment_list failed")
            return Response(
                {
                    "status": "error",
                    "code": "LIST_FAILED",
                    "message": "Operation failed.",
                },
                status=400,
            )

        return Response(
            {
                "status": "success",
                "data": results,
                "truncated": len(results) == 500,
            },
            status=200,
        )
