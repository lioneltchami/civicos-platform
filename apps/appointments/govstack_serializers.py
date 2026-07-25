"""
GovStack Scheduler BB — DRF serializers.

Implements the GovStack Scheduler BB OpenAPI schema conventions for all
9 entity types and their request/response envelope wrappers.

Request body conventions per operation type:
  POST /new           → { "qry": { "details": <entity_details> } }
  PUT  /modifications → { "details": <entity_details> }  (entity_id in query param)
  GET  /list_details  → { "<entity>_filter": {...}, "<entity>_details_required": {...} }

Response conventions:
  Success (create/modify/delete) → { "status": "success", "<entity>_id": "<id>" }
  Success (list)                 → { "status": "success", "data": [...] }
  Error                          → { "status": "error", "code": "...", "message": "..." }

Field typing:
  All GovStack Scheduler BB fields are typed as `string` in the OpenAPI spec.
  This module mirrors that loose typing with CharField(required=False, allow_blank=True)
  for all string fields. Complex validation belongs in the service layer.

  Exceptions to the all-string rule:
    - Appointment.exclusive:      BooleanField (explicitly boolean in OpenAPI)
    - Affiliation.work_days_hours: DictField   (object/dict in OpenAPI)
    - Event.slots:                ListField    (array in OpenAPI)
    - Event.venue:                VenueSerializer (nested object in OpenAPI)

Serializer sets per entity (9 entity types × 5 classes = 45 serializers):
  <Entity>DetailsSerializer         — core entity fields
  <Entity>FilterSerializer          — filter fields (all optional) for list queries
  <Entity>DetailsRequiredSerializer — boolean flags controlling list response fields
  <Entity>CreateQrySerializer       — POST /new body wrapper: { "qry": { "details": ... } }
  <Entity>ModifySerializer          — PUT /modifications body: { "details": ... }
  <Entity>ListQrySerializer         — GET /list_details body

Plus shared response envelopes (3):
  GovStackSuccessResponseSerializer
  GovStackErrorResponseSerializer
  GovStackListResponseSerializer

Module organisation:
  1.  Response envelope serializers (shared)
  2.  Entity (Organization)
  3.  Resource
  4.  Subscriber
  5.  Affiliation
  6.  Event (includes VenueSerializer)
  7.  Appointment
  8.  AlertSchedule
  9.  Message
  10. Log
"""
from __future__ import annotations

from rest_framework import serializers


# ---------------------------------------------------------------------------
# Shared response envelope serializers
# ---------------------------------------------------------------------------

class GovStackSuccessResponseSerializer(serializers.Serializer):
    """
    Generic success response for create / modify / delete operations.

    The entity_id field name varies by entity type (e.g. "entity_id",
    "resource_id", "appointment_id"). Build the response dict directly in
    the view and use this serializer for documentation purposes only, or
    subclass it and add the appropriate <entity>_id field.

    Example: { "status": "success", "entity_id": "42" }
    """

    status = serializers.CharField(default="success", read_only=True)


class GovStackErrorResponseSerializer(serializers.Serializer):
    """
    Error response body returned on validation failures, not-found, and other errors.

    Example: { "status": "error", "code": "ENTITY_NOT_FOUND", "message": "No entity with that ID." }
    """

    status = serializers.CharField(default="error", read_only=True)
    code = serializers.CharField(required=False, allow_blank=True)
    message = serializers.CharField(required=False, allow_blank=True)


class GovStackListResponseSerializer(serializers.Serializer):
    """
    List response returned by all /list_details endpoints.

    data contains serialized entity objects filtered and shaped according to
    the entity_details_required flags in the request.

    Example: { "status": "success", "data": [ {...}, {...} ] }
    """

    status = serializers.CharField(default="success", read_only=True)
    data = serializers.ListField(child=serializers.DictField(), read_only=True)


# ---------------------------------------------------------------------------
# 1. Entity (→ CivicOS Organization)
# ---------------------------------------------------------------------------

class EntityDetailsSerializer(serializers.Serializer):
    """GovStack Entity core fields — maps to CivicOS Organization."""

    category = serializers.CharField(required=False, allow_blank=True)
    name = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    email = serializers.CharField(required=False, allow_blank=True)
    website = serializers.CharField(required=False, allow_blank=True)


class EntityFilterSerializer(serializers.Serializer):
    """Filter parameters for GET /entity/list_details."""

    entity_id = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    name = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    email = serializers.CharField(required=False, allow_blank=True)
    website = serializers.CharField(required=False, allow_blank=True)


class EntityDetailsRequiredSerializer(serializers.Serializer):
    """Boolean flags controlling which Entity fields appear in list responses."""

    entity_id = serializers.BooleanField(required=False, default=True)
    category = serializers.BooleanField(required=False, default=True)
    name = serializers.BooleanField(required=False, default=True)
    phone = serializers.BooleanField(required=False, default=False)
    email = serializers.BooleanField(required=False, default=False)
    website = serializers.BooleanField(required=False, default=False)


class _EntityQryDetailsSerializer(serializers.Serializer):
    """Inner { "details": ... } wrapper used by EntityCreateQrySerializer."""

    details = EntityDetailsSerializer(required=True)


class EntityCreateQrySerializer(serializers.Serializer):
    """POST /entity/new request body: { "qry": { "details": <entity_details> } }"""

    qry = _EntityQryDetailsSerializer(required=True)


class EntityModifySerializer(serializers.Serializer):
    """PUT /entity/modifications request body: { "details": <entity_details> }"""

    details = EntityDetailsSerializer(required=True)


class EntityListQrySerializer(serializers.Serializer):
    """GET /entity/list_details request body."""

    entity_filter = EntityFilterSerializer(required=False)
    entity_details_required = EntityDetailsRequiredSerializer(required=False)


# ---------------------------------------------------------------------------
# 2. Resource (→ CivicOS Resource / StaffProfile)
# ---------------------------------------------------------------------------

class ResourceDetailsSerializer(serializers.Serializer):
    """
    GovStack Resource core fields.

    Maps to CivicOS Resource (rooms/equipment) and StaffProfile (people).
    The category field distinguishes the resource type at the API layer.
    """

    name = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    email = serializers.CharField(required=False, allow_blank=True)
    alert_url = serializers.CharField(required=False, allow_blank=True)
    alert_preference = serializers.CharField(required=False, allow_blank=True)
    status_poll_url = serializers.CharField(required=False, allow_blank=True)


class ResourceFilterSerializer(serializers.Serializer):
    """Filter parameters for GET /resource/list_details."""

    resource_id = serializers.CharField(required=False, allow_blank=True)
    name = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    email = serializers.CharField(required=False, allow_blank=True)
    alert_url = serializers.CharField(required=False, allow_blank=True)
    alert_preference = serializers.CharField(required=False, allow_blank=True)
    status_poll_url = serializers.CharField(required=False, allow_blank=True)


class ResourceDetailsRequiredSerializer(serializers.Serializer):
    """Boolean flags controlling which Resource fields appear in list responses."""

    resource_id = serializers.BooleanField(required=False, default=True)
    name = serializers.BooleanField(required=False, default=True)
    category = serializers.BooleanField(required=False, default=True)
    phone = serializers.BooleanField(required=False, default=False)
    email = serializers.BooleanField(required=False, default=False)
    alert_url = serializers.BooleanField(required=False, default=False)
    alert_preference = serializers.BooleanField(required=False, default=False)
    status_poll_url = serializers.BooleanField(required=False, default=False)


class _ResourceQryDetailsSerializer(serializers.Serializer):
    """Inner { "details": ... } wrapper used by ResourceCreateQrySerializer."""

    details = ResourceDetailsSerializer(required=True)


class ResourceCreateQrySerializer(serializers.Serializer):
    """POST /resource/new request body: { "qry": { "details": <resource_details> } }"""

    qry = _ResourceQryDetailsSerializer(required=True)


class ResourceModifySerializer(serializers.Serializer):
    """PUT /resource/modifications request body: { "details": <resource_details> }"""

    details = ResourceDetailsSerializer(required=True)


class ResourceListQrySerializer(serializers.Serializer):
    """GET /resource/list_details request body."""

    resource_filter = ResourceFilterSerializer(required=False)
    resource_details_required = ResourceDetailsRequiredSerializer(required=False)


class ResourceAvailabilityFilterSerializer(serializers.Serializer):
    """
    Filter parameters for GET /resource/availability.

    GovStack spec key mapping:
      spec "from"       → validated_data key "from_dt"   (Python keyword avoidance)
      spec "to"         → validated_data key "to_dt"
      spec "Entity_id"  → field Entity_id (capital E per spec)

    to_internal_value() accepts either the spec keys ("from"/"to") or the safe
    internal keys ("from_dt"/"to_dt") and normalises them to "from_dt"/"to_dt".
    """

    resource_id = serializers.CharField(required=False, allow_blank=True)
    # GovStack spec capitalises: Entity_id
    Entity_id   = serializers.CharField(required=False, allow_blank=True)
    from_dt     = serializers.CharField(
        required=False, allow_blank=True,
        help_text="ISO 8601 datetime with tz offset (spec key: 'from')",
    )
    to_dt       = serializers.CharField(
        required=False, allow_blank=True,
        help_text="ISO 8601 datetime with tz offset (spec key: 'to')",
    )
    category    = serializers.CharField(required=False, allow_blank=True)

    def to_internal_value(self, data):
        """Normalise incoming data — accept 'from'/'to' spec keys alongside 'from_dt'/'to_dt'."""
        data = dict(data)
        # "from" is a Python keyword; rename to the safe internal key before DRF processes fields.
        if "from" in data and "from_dt" not in data:
            data["from_dt"] = data.pop("from")
        elif "from" in data:
            data.pop("from")
        if "to" in data and "to_dt" not in data:
            data["to_dt"] = data.pop("to")
        elif "to" in data:
            data.pop("to")
        return super().to_internal_value(data)


# ---------------------------------------------------------------------------
# 3. Subscriber (→ CivicOS User + GovStackSubscriberProfile)
# ---------------------------------------------------------------------------

class SubscriberDetailsSerializer(serializers.Serializer):
    """
    GovStack Subscriber core fields.

    Maps to a Django User plus GovStackSubscriberProfile (created in Wave A
    migration 0013_govstack_wave_a.py).
    """

    name = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    email = serializers.CharField(required=False, allow_blank=True)
    alert_url = serializers.CharField(required=False, allow_blank=True)
    alert_preference = serializers.CharField(required=False, allow_blank=True)
    status_poll_url = serializers.CharField(required=False, allow_blank=True)


class SubscriberFilterSerializer(serializers.Serializer):
    """Filter parameters for GET /subscriber/list_details."""

    subscriber_id = serializers.CharField(required=False, allow_blank=True)
    name = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    email = serializers.CharField(required=False, allow_blank=True)
    alert_url = serializers.CharField(required=False, allow_blank=True)
    alert_preference = serializers.CharField(required=False, allow_blank=True)
    status_poll_url = serializers.CharField(required=False, allow_blank=True)


class SubscriberDetailsRequiredSerializer(serializers.Serializer):
    """Boolean flags controlling which Subscriber fields appear in list responses."""

    subscriber_id = serializers.BooleanField(required=False, default=True)
    name = serializers.BooleanField(required=False, default=False)
    category = serializers.BooleanField(required=False, default=False)
    phone = serializers.BooleanField(required=False, default=False)
    email = serializers.BooleanField(required=False, default=False)
    alert_url = serializers.BooleanField(required=False, default=False)
    alert_preference = serializers.BooleanField(required=False, default=False)
    status_poll_url = serializers.BooleanField(required=False, default=False)


class _SubscriberQryDetailsSerializer(serializers.Serializer):
    """Inner { "details": ... } wrapper used by SubscriberCreateQrySerializer."""

    details = SubscriberDetailsSerializer(required=True)


class SubscriberCreateQrySerializer(serializers.Serializer):
    """POST /subscriber/new request body: { "qry": { "details": <subscriber_details> } }"""

    qry = _SubscriberQryDetailsSerializer(required=True)


class SubscriberModifySerializer(serializers.Serializer):
    """PUT /subscriber/modifications request body: { "details": <subscriber_details> }"""

    details = SubscriberDetailsSerializer(required=True)


class SubscriberListQrySerializer(serializers.Serializer):
    """GET /subscriber/list_details request body."""

    subscriber_filter = SubscriberFilterSerializer(required=False)
    subscriber_details_required = SubscriberDetailsRequiredSerializer(required=False)


# ---------------------------------------------------------------------------
# 4. Affiliation (→ CivicOS GovStackAffiliation)
# ---------------------------------------------------------------------------

class AffiliationDetailsSerializer(serializers.Serializer):
    """
    GovStack Affiliation core fields.

    Maps to GovStackAffiliation model (created in Wave A migration).
    work_days_hours is a free-form JSON object in the GovStack spec.
    """

    resource_id = serializers.CharField(required=False, allow_blank=True)
    entity_id = serializers.CharField(required=False, allow_blank=True)
    resource_category = serializers.CharField(required=False, allow_blank=True)
    work_days_hours = serializers.DictField(
        # No child= : values are nested dicts ({"from": "09:00", "to": "17:00"}),
        # not strings. A bare DictField accepts any JSON-typed values.
        required=False,
        allow_empty=True,
        help_text=(
            "Free-form days/hours object. Structure is caller-defined per GovStack spec. "
            'Example: {"monday": {"from": "09:00", "to": "17:00"}}'
        ),
    )


class AffiliationFilterSerializer(serializers.Serializer):
    """Filter parameters for GET /affiliation/list_details."""

    affiliation_id = serializers.CharField(required=False, allow_blank=True)
    resource_id = serializers.CharField(required=False, allow_blank=True)
    entity_id = serializers.CharField(required=False, allow_blank=True)
    resource_category = serializers.CharField(required=False, allow_blank=True)


class AffiliationDetailsRequiredSerializer(serializers.Serializer):
    """Boolean flags controlling which Affiliation fields appear in list responses."""

    affiliation_id = serializers.BooleanField(required=False, default=True)
    resource_id = serializers.BooleanField(required=False, default=True)
    entity_id = serializers.BooleanField(required=False, default=True)
    resource_category = serializers.BooleanField(required=False, default=True)
    work_days_hours = serializers.BooleanField(required=False, default=False)


class _AffiliationQryDetailsSerializer(serializers.Serializer):
    """Inner { "details": ... } wrapper used by AffiliationCreateQrySerializer."""

    details = AffiliationDetailsSerializer(required=True)


class AffiliationCreateQrySerializer(serializers.Serializer):
    """POST /affiliation/new request body: { "qry": { "details": <affiliation_details> } }"""

    qry = _AffiliationQryDetailsSerializer(required=True)


class AffiliationModifySerializer(serializers.Serializer):
    """PUT /affiliation/modifications request body: { "details": <affiliation_details> }"""

    details = AffiliationDetailsSerializer(required=True)


class AffiliationListQrySerializer(serializers.Serializer):
    """GET /affiliation/list_details request body."""

    affiliation_filter = AffiliationFilterSerializer(required=False)
    affiliation_details_required = AffiliationDetailsRequiredSerializer(required=False)


# ---------------------------------------------------------------------------
# 5. Event (→ CivicOS AppointmentType + Slot + Location)
# ---------------------------------------------------------------------------

class VenueSerializer(serializers.Serializer):
    """
    GovStack Event venue — maps to CivicOS Location address fields.

    All fields are optional strings per the GovStack OpenAPI spec.
    lat and long are typed as strings (not floats) to match the spec's
    loose typing — the service layer converts to float for storage/queries.
    """

    building = serializers.CharField(required=False, allow_blank=True)
    street = serializers.CharField(required=False, allow_blank=True)
    area = serializers.CharField(required=False, allow_blank=True)
    city = serializers.CharField(required=False, allow_blank=True)
    state = serializers.CharField(required=False, allow_blank=True)
    country = serializers.CharField(required=False, allow_blank=True)
    lat = serializers.CharField(required=False, allow_blank=True)
    long = serializers.CharField(required=False, allow_blank=True)


class EventDetailsSerializer(serializers.Serializer):
    """
    GovStack Event core fields.

    Maps primarily to CivicOS Slot + AppointmentType + Location.

    slots is a list of slot objects per the OpenAPI spec. Each slot is expected
    to contain "from" and "to" datetime strings. The service layer generates
    CivicOS Slot records from this array.

    venue is a nested VenueSerializer mapping to CivicOS Location address fields.
    """

    name = serializers.CharField(required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    host_entity_id = serializers.CharField(required=False, allow_blank=True)
    slots = serializers.ListField(
        # child DictField without child= to allow nested dict values (from/to datetimes)
        child=serializers.DictField(),
        required=False,
        allow_empty=True,
        help_text=(
            'Array of slot objects. Each slot should contain "from" and "to" '
            'datetime strings, e.g. [{"from": "2026-08-01T09:00:00Z", "to": "2026-08-01T10:00:00Z"}].'
        ),
    )
    deadline = serializers.CharField(required=False, allow_blank=True)
    subscriber_limit = serializers.CharField(required=False, allow_blank=True)
    terms = serializers.CharField(required=False, allow_blank=True)
    status = serializers.CharField(required=False, allow_blank=True)
    venue = VenueSerializer(required=False)


class EventFilterSerializer(serializers.Serializer):
    """Filter parameters for GET /event/list_details."""

    event_id = serializers.CharField(required=False, allow_blank=True)
    name = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    host_entity_id = serializers.CharField(required=False, allow_blank=True)
    status = serializers.CharField(required=False, allow_blank=True)
    deadline_from = serializers.CharField(required=False, allow_blank=True)
    deadline_to = serializers.CharField(required=False, allow_blank=True)


class EventDetailsRequiredSerializer(serializers.Serializer):
    """Boolean flags controlling which Event fields appear in list responses."""

    event_id = serializers.BooleanField(required=False, default=True)
    name = serializers.BooleanField(required=False, default=True)
    description = serializers.BooleanField(required=False, default=False)
    category = serializers.BooleanField(required=False, default=True)
    host_entity_id = serializers.BooleanField(required=False, default=True)
    slots = serializers.BooleanField(required=False, default=False)
    deadline = serializers.BooleanField(required=False, default=False)
    subscriber_limit = serializers.BooleanField(required=False, default=False)
    terms = serializers.BooleanField(required=False, default=False)
    status = serializers.BooleanField(required=False, default=True)
    venue = serializers.BooleanField(required=False, default=False)


class _EventQryDetailsSerializer(serializers.Serializer):
    """Inner { "details": ... } wrapper used by EventCreateQrySerializer."""

    details = EventDetailsSerializer(required=True)


class EventCreateQrySerializer(serializers.Serializer):
    """POST /event/new request body: { "qry": { "details": <event_details> } }"""

    qry = _EventQryDetailsSerializer(required=True)


class EventModifySerializer(serializers.Serializer):
    """PUT /event/modifications request body: { "details": <event_details> }"""

    details = EventDetailsSerializer(required=True)


class EventListQrySerializer(serializers.Serializer):
    """GET /event/list_details request body."""

    event_filter = EventFilterSerializer(required=False)
    event_details_required = EventDetailsRequiredSerializer(required=False)


# ---------------------------------------------------------------------------
# 6. Appointment (→ CivicOS Booking)
# ---------------------------------------------------------------------------

class AppointmentDetailsSerializer(serializers.Serializer):
    """
    GovStack Appointment core fields — maps to CivicOS Booking.

    exclusive is explicitly boolean in the GovStack OpenAPI spec.
    It corresponds to Slot.capacity == 1 in CivicOS (a single-occupancy booking).
    All other fields are strings per the spec's loose typing.
    """

    exclusive = serializers.BooleanField(
        required=False,
        default=False,
        help_text=(
            "True if this appointment reserves the slot exclusively (capacity=1). "
            "When True, the service layer enforces single-booking on the target slot."
        ),
    )
    event_id = serializers.CharField(required=False, allow_blank=True)
    participant_type = serializers.CharField(required=False, allow_blank=True)
    participant_id = serializers.CharField(required=False, allow_blank=True)
    status_id = serializers.CharField(required=False, allow_blank=True)
    participant_entity_id = serializers.CharField(required=False, allow_blank=True)


class AppointmentFilterSerializer(serializers.Serializer):
    """Filter parameters for GET /appointment/list_details."""

    appointment_id = serializers.CharField(required=False, allow_blank=True)
    event_id = serializers.CharField(required=False, allow_blank=True)
    participant_type = serializers.CharField(required=False, allow_blank=True)
    participant_id = serializers.CharField(required=False, allow_blank=True)
    status_id = serializers.CharField(required=False, allow_blank=True)
    participant_entity_id = serializers.CharField(required=False, allow_blank=True)
    exclusive = serializers.BooleanField(required=False)


class AppointmentDetailsRequiredSerializer(serializers.Serializer):
    """Boolean flags controlling which Appointment fields appear in list responses."""

    appointment_id = serializers.BooleanField(required=False, default=True)
    exclusive = serializers.BooleanField(required=False, default=False)
    event_id = serializers.BooleanField(required=False, default=True)
    participant_type = serializers.BooleanField(required=False, default=True)
    participant_id = serializers.BooleanField(required=False, default=True)
    status_id = serializers.BooleanField(required=False, default=True)
    participant_entity_id = serializers.BooleanField(required=False, default=False)


class _AppointmentQryDetailsSerializer(serializers.Serializer):
    """Inner { "details": ... } wrapper used by AppointmentCreateQrySerializer."""

    details = AppointmentDetailsSerializer(required=True)


class AppointmentCreateQrySerializer(serializers.Serializer):
    """POST /appointment/new request body: { "qry": { "details": <appointment_details> } }"""

    qry = _AppointmentQryDetailsSerializer(required=True)


class AppointmentModifySerializer(serializers.Serializer):
    """PUT /appointment/modifications request body: { "details": <appointment_details> }"""

    details = AppointmentDetailsSerializer(required=True)


class AppointmentListQrySerializer(serializers.Serializer):
    """GET /appointment/list_details request body."""

    appointment_filter = AppointmentFilterSerializer(required=False)
    appointment_details_required = AppointmentDetailsRequiredSerializer(required=False)


# ---------------------------------------------------------------------------
# 7. AlertSchedule (→ CivicOS GovStackAlertSchedule)
# ---------------------------------------------------------------------------

class AlertScheduleDetailsSerializer(serializers.Serializer):
    """
    GovStack AlertSchedule core fields.

    Maps to GovStackAlertSchedule model (created in Wave A migration).
    alert_datetime is a string per GovStack spec; the service layer parses
    it to a datetime and stores it on GovStackAlertSchedule.alert_datetime.
    """

    event_id = serializers.CharField(required=False, allow_blank=True)
    target_category = serializers.CharField(required=False, allow_blank=True)
    message_id = serializers.CharField(required=False, allow_blank=True)
    alert_datetime = serializers.CharField(required=False, allow_blank=True)


class AlertScheduleFilterSerializer(serializers.Serializer):
    """Filter parameters for GET /alert_schedule/list_details."""

    alert_schedule_id = serializers.CharField(required=False, allow_blank=True)
    event_id = serializers.CharField(required=False, allow_blank=True)
    target_category = serializers.CharField(required=False, allow_blank=True)
    message_id = serializers.CharField(required=False, allow_blank=True)
    alert_datetime_from = serializers.CharField(required=False, allow_blank=True)
    alert_datetime_to = serializers.CharField(required=False, allow_blank=True)


class AlertScheduleDetailsRequiredSerializer(serializers.Serializer):
    """Boolean flags controlling which AlertSchedule fields appear in list responses."""

    alert_schedule_id = serializers.BooleanField(required=False, default=True)
    event_id = serializers.BooleanField(required=False, default=True)
    target_category = serializers.BooleanField(required=False, default=True)
    message_id = serializers.BooleanField(required=False, default=True)
    alert_datetime = serializers.BooleanField(required=False, default=True)


class _AlertScheduleQryDetailsSerializer(serializers.Serializer):
    """Inner { "details": ... } wrapper used by AlertScheduleCreateQrySerializer."""

    details = AlertScheduleDetailsSerializer(required=True)


class AlertScheduleCreateQrySerializer(serializers.Serializer):
    """POST /alert_schedule/new request body: { "qry": { "details": <alert_schedule_details> } }"""

    qry = _AlertScheduleQryDetailsSerializer(required=True)


class AlertScheduleModifySerializer(serializers.Serializer):
    """PUT /alert_schedule/modifications request body: { "details": <alert_schedule_details> }"""

    details = AlertScheduleDetailsSerializer(required=True)


class AlertScheduleListQrySerializer(serializers.Serializer):
    """GET /alert_schedule/list_details request body."""

    alert_schedule_filter = AlertScheduleFilterSerializer(required=False)
    alert_schedule_details_required = AlertScheduleDetailsRequiredSerializer(required=False)


# ---------------------------------------------------------------------------
# 8. Message (→ CivicOS GovStackMessage)
# ---------------------------------------------------------------------------

class MessageDetailsSerializer(serializers.Serializer):
    """
    GovStack Message core fields — notification template.

    Maps to GovStackMessage model (created in Wave A migration).
    entity_id references the owning Organization (GovStack Entity).
    """

    entity_id = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    message_body = serializers.CharField(required=False, allow_blank=True)


class MessageFilterSerializer(serializers.Serializer):
    """Filter parameters for GET /message/list_details."""

    message_id = serializers.CharField(required=False, allow_blank=True)
    entity_id = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)


class MessageDetailsRequiredSerializer(serializers.Serializer):
    """Boolean flags controlling which Message fields appear in list responses."""

    message_id = serializers.BooleanField(required=False, default=True)
    entity_id = serializers.BooleanField(required=False, default=True)
    category = serializers.BooleanField(required=False, default=True)
    message_body = serializers.BooleanField(required=False, default=False)


class _MessageQryDetailsSerializer(serializers.Serializer):
    """Inner { "details": ... } wrapper used by MessageCreateQrySerializer."""

    details = MessageDetailsSerializer(required=True)


class MessageCreateQrySerializer(serializers.Serializer):
    """POST /message/new request body: { "qry": { "details": <message_details> } }"""

    qry = _MessageQryDetailsSerializer(required=True)


class MessageModifySerializer(serializers.Serializer):
    """PUT /message/modifications request body: { "details": <message_details> }"""

    details = MessageDetailsSerializer(required=True)


class MessageListQrySerializer(serializers.Serializer):
    """GET /message/list_details request body."""

    message_filter = MessageFilterSerializer(required=False)
    message_details_required = MessageDetailsRequiredSerializer(required=False)


# ---------------------------------------------------------------------------
# 9. Log (→ CivicOS BookingAuditLog)
# ---------------------------------------------------------------------------

class LogDetailsSerializer(serializers.Serializer):
    """
    GovStack Log core fields — maps to CivicOS BookingAuditLog.

    IMPORTANT: The GovStack spec defines PUT and DELETE operations on logs, but
    CivicOS intentionally returns HTTP 405 (Method Not Allowed) for both to
    preserve audit log immutability. See Wave G notes in the spec document.

    datetime is a string per GovStack spec; the service layer parses it.
    log_data is a free-form string (plain text or serialised JSON).

    PII handling: log_data must NEVER contain citizen name, email, or other PII.
    Log only booking identifiers (UUID), slot IDs, and actor roles.
    """

    logger_role = serializers.CharField(required=False, allow_blank=True)
    logger_id = serializers.CharField(required=False, allow_blank=True)
    entity_id = serializers.CharField(required=False, allow_blank=True)
    log_category = serializers.CharField(required=False, allow_blank=True)
    datetime = serializers.CharField(required=False, allow_blank=True)
    log_data = serializers.CharField(required=False, allow_blank=True)


class LogFilterSerializer(serializers.Serializer):
    """Filter parameters for GET /log/list_details."""

    log_id = serializers.CharField(required=False, allow_blank=True)
    logger_role = serializers.CharField(required=False, allow_blank=True)
    logger_id = serializers.CharField(required=False, allow_blank=True)
    entity_id = serializers.CharField(required=False, allow_blank=True)
    log_category = serializers.CharField(required=False, allow_blank=True)
    datetime_from = serializers.CharField(required=False, allow_blank=True)
    datetime_to = serializers.CharField(required=False, allow_blank=True)


class LogDetailsRequiredSerializer(serializers.Serializer):
    """Boolean flags controlling which Log fields appear in list responses."""

    log_id = serializers.BooleanField(required=False, default=True)
    logger_role = serializers.BooleanField(required=False, default=True)
    logger_id = serializers.BooleanField(required=False, default=True)
    entity_id = serializers.BooleanField(required=False, default=True)
    log_category = serializers.BooleanField(required=False, default=True)
    datetime = serializers.BooleanField(required=False, default=True)
    log_data = serializers.BooleanField(required=False, default=False)


class _LogQryDetailsSerializer(serializers.Serializer):
    """Inner { "details": ... } wrapper used by LogCreateQrySerializer."""

    details = LogDetailsSerializer(required=True)


class LogCreateQrySerializer(serializers.Serializer):
    """POST /log/new request body: { "qry": { "details": <log_details> } }"""

    qry = _LogQryDetailsSerializer(required=True)


class LogModifySerializer(serializers.Serializer):
    """
    PUT /log/modifications request body.

    Defined for completeness. The view intentionally returns HTTP 405
    (Method Not Allowed) to preserve audit log immutability.
    """

    details = LogDetailsSerializer(required=True)


class LogListQrySerializer(serializers.Serializer):
    """GET /log/list_details request body."""

    log_filter = LogFilterSerializer(required=False)
    log_details_required = LogDetailsRequiredSerializer(required=False)
