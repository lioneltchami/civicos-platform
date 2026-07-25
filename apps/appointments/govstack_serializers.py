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
    """
    Inner wrapper used by ResourceCreateQrySerializer.

    The real GovStack resource_new_qry schema wraps the create payload under
    the entity-specific key "resource_details" (verified directly against
    the fetched OpenAPI spec's components.schemas.resource_new_qry) — NOT
    the generic "details" key. This mirrors the exact bug class Wave F's
    review found and fixed for Message/AlertSchedule's create wrapper keys;
    Resource (a Wave B group, never revisited until this final certifiability
    pass) had the same bug from initial implementation. See
    _MessageQryDetailsSerializer's docstring for the precedent.
    """

    resource_details = ResourceDetailsSerializer(required=True)


class ResourceCreateQrySerializer(serializers.Serializer):
    """POST /resource/new request body: { "qry": { "resource_details": <resource_details> } }"""

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

    name = serializers.CharField(required=False, allow_blank=True, max_length=301)
    category = serializers.CharField(required=False, allow_blank=True, max_length=50)
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
    """
    Inner wrapper used by SubscriberCreateQrySerializer.

    The real GovStack subscriber_new_qry schema wraps the create payload
    under the entity-specific key "subscriber_details" (verified directly
    against the fetched OpenAPI spec's components.schemas.subscriber_new_qry)
    — NOT the generic "details" key. This mirrors the exact bug class Wave
    F's review found and fixed for Message/AlertSchedule's create wrapper
    keys; Subscriber (a Wave C group, never revisited until this final
    certifiability pass) had the same bug from initial implementation. See
    _MessageQryDetailsSerializer's docstring for the precedent.
    """

    subscriber_details = SubscriberDetailsSerializer(required=True)


class SubscriberCreateQrySerializer(serializers.Serializer):
    """POST /subscriber/new request body: { "qry": { "subscriber_details": <subscriber_details> } }"""

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
    """
    Filter parameters for GET /affiliation/list_details.

    BUG FIX (verified directly against the fetched real GovStack OpenAPI
    spec's components.schemas.affiliation_filter): the filter field is
    literally named "category" — NOT "resource_category" (that name is only
    used on the separate affiliation_details create/response schema, which
    is unaffected and unchanged — see AffiliationDetailsSerializer above).
    Unlike Log's two documented Spec Quirks (a real inconsistency *within*
    the upstream spec itself, deliberately preserved), this is a
    straightforward field-name bug in this codebase with no such
    counter-indication in the spec, so it is renamed here rather than kept.
    The renamed "category" flag/filter still reads from and gates the
    model's/response's "resource_category" value — see
    AffiliationDetailsRequiredSerializer below and
    services.govstack_affiliation.affiliation_list for the read side.

    from_/to are the real spec's affiliation_filter.from/affiliation_filter.to
    date-range window fields — entirely absent before this fix. "from" is a
    Python reserved word and cannot be a class attribute; it is remapped to
    "from_" in to_internal_value() — identical technique to
    EventFilterSerializer/AppointmentFilterSerializer/LogFilterSerializer
    above (see those docstrings for the precedent this replicates). The
    range filters GovStackAffiliation.created_at — see
    services.govstack_affiliation.affiliation_list's docstring for why
    created_at (not updated_at) was chosen as the filtered timestamp field.
    """

    affiliation_id = serializers.CharField(required=False, allow_blank=True)
    resource_id = serializers.CharField(required=False, allow_blank=True)
    entity_id = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    from_ = serializers.CharField(required=False, allow_blank=True)
    to = serializers.CharField(required=False, allow_blank=True)

    def to_internal_value(self, data):
        data = dict(data)
        if "from" in data and "from_" not in data:
            data["from_"] = data.pop("from")
        return super().to_internal_value(data)


class AffiliationDetailsRequiredSerializer(serializers.Serializer):
    """
    Boolean flags controlling which Affiliation fields appear in list responses.

    BUG FIX (verified directly against the fetched real GovStack OpenAPI
    spec's components.schemas.affiliation_details_required): the flag is
    literally named "category" — NOT "resource_category". As with
    AffiliationFilterSerializer above, this is a genuine field-name bug (not
    a spec quirk to preserve), so it is renamed here. The flag still gates
    the response's "resource_category" field (the real field name on
    affiliation_details itself, which is unchanged) — only the
    REQUIRED-flags lookup key changes, matching the flag-name-vs-
    response-field-name distinction established by Log's Spec Quirk #2
    (services.govstack_log.log_list / LogDetailsRequiredSerializer's
    "logger_category" flag gating the response's "logger_role" field).
    """

    affiliation_id = serializers.BooleanField(required=False, default=True)
    resource_id = serializers.BooleanField(required=False, default=True)
    entity_id = serializers.BooleanField(required=False, default=True)
    category = serializers.BooleanField(required=False, default=True)
    work_days_hours = serializers.BooleanField(required=False, default=False)


class _AffiliationQryDetailsSerializer(serializers.Serializer):
    """
    Inner wrapper used by AffiliationCreateQrySerializer.

    The real GovStack affiliation_new_qry schema wraps the create payload
    under the entity-specific key "affiliation_details" (verified directly
    against the fetched OpenAPI spec's components.schemas.affiliation_new_qry)
    — NOT the generic "details" key. This mirrors the exact bug class Wave
    F's review found and fixed for Message/AlertSchedule's create wrapper
    keys; Affiliation (a Wave B group, never revisited until this final
    certifiability pass) had the same bug from initial implementation. See
    _MessageQryDetailsSerializer's docstring for the precedent.
    """

    affiliation_details = AffiliationDetailsSerializer(required=True)


class AffiliationCreateQrySerializer(serializers.Serializer):
    """POST /affiliation/new request body: { "qry": { "affiliation_details": <affiliation_details> } }"""

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
    GovStack Event fields for POST /event/new (event_creation_details schema).

    Maps primarily to CivicOS Slot + AppointmentType + Location.

    slots is a list of slot objects per the OpenAPI spec. Each slot is expected
    to contain "from" and "to" datetime strings. The service layer generates
    CivicOS Slot records from this array — one independent (AppointmentType,
    Slot) pair per entry (see services.govstack_event.event_create FIX 1).

    venue is a nested VenueSerializer mapping to CivicOS Location address fields.

    NOTE: this serializer is used ONLY by EventCreateQrySerializer (POST). The
    real spec's PUT /event/modifications body uses a DIFFERENT schema
    (event_details) with flat singular from/to fields and no slots array — see
    EventModifyDetailsSerializer.
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


class EventModifyDetailsSerializer(serializers.Serializer):
    """
    GovStack Event fields for PUT /event/modifications (real event_details schema).

    Unlike EventDetailsSerializer (POST /event/new, modeled on
    event_creation_details with a ``slots`` array), this matches the real
    spec's ``event_details`` schema — used both as the PUT request body AND
    as the nested item shape in GET /event/list_details responses: flat
    singular ``from``/``to`` date-time strings, no ``slots`` array.

    "from" is a Python reserved word and cannot be declared as a literal
    class attribute name on a plain (non-Model) Serializer. The wire-format
    key the GovStack caller sends is literally "from"; it is remapped here to
    the Python-safe attribute name "from_" in to_internal_value() before
    validation runs. "to" is not a reserved word and needs no remapping.
    """

    name = serializers.CharField(required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    host_entity_id = serializers.CharField(required=False, allow_blank=True)
    from_ = serializers.CharField(required=False, allow_blank=True)
    to = serializers.CharField(required=False, allow_blank=True)
    deadline = serializers.CharField(required=False, allow_blank=True)
    subscriber_limit = serializers.CharField(required=False, allow_blank=True)
    terms = serializers.CharField(required=False, allow_blank=True)
    status = serializers.CharField(required=False, allow_blank=True)
    venue = VenueSerializer(required=False)

    def to_internal_value(self, data):
        data = dict(data)
        if "from" in data and "from_" not in data:
            data["from_"] = data.pop("from")
        return super().to_internal_value(data)


class EventFilterSerializer(serializers.Serializer):
    """
    Filter parameters for GET /event/list_details.

    from_/to are the real spec's event_filter.from/event_filter.to
    date-range window fields. As with EventModifyDetailsSerializer, "from" is
    remapped from the wire-format key to the Python-safe attribute name
    "from_" in to_internal_value(). deadline_from/deadline_to are kept as a
    non-spec CivicOS extension for backward compatibility.

    description/subscriber_limit/terms (verified directly against the
    fetched real GovStack OpenAPI spec's components.schemas.event_filter):
    these three filterable string fields were entirely missing before this
    fix. See services.govstack_event.event_list for how each is applied.

    venue GAP (deliberate, documented — not fixed here): the real spec's
    event_filter.venue field is a nested object (building/street/area/city/
    state/country/lat/long), not a flat filterable value. Query-string-based
    filtering on a nested object is out of scope/impractical for this
    endpoint's filter model (there is no established convention in this
    codebase for nested-object query filters), so venue filtering remains
    unimplemented. Flagged here for certification-review visibility rather
    than silently omitted.
    """

    event_id = serializers.CharField(required=False, allow_blank=True)
    name = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    host_entity_id = serializers.CharField(required=False, allow_blank=True)
    status = serializers.CharField(required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    subscriber_limit = serializers.CharField(required=False, allow_blank=True)
    terms = serializers.CharField(required=False, allow_blank=True)
    deadline_from = serializers.CharField(required=False, allow_blank=True)
    deadline_to = serializers.CharField(required=False, allow_blank=True)
    from_ = serializers.CharField(required=False, allow_blank=True)
    to = serializers.CharField(required=False, allow_blank=True)

    def to_internal_value(self, data):
        data = dict(data)
        if "from" in data and "from_" not in data:
            data["from_"] = data.pop("from")
        return super().to_internal_value(data)


class EventDetailsRequiredSerializer(serializers.Serializer):
    """
    Boolean flags controlling which Event fields appear in list responses.

    BUG FIX (verified directly against the fetched real GovStack OpenAPI
    spec's components.schemas.event_details_required): the real flag is
    literally named "period" — a boolean gate that does not exist in this
    serializer before this fix. The serializer previously declared an
    undocumented "slots" flag instead, which is not present anywhere in the
    real spec's event_details_required schema. Renamed here to "period" to
    match the spec.

    The renamed "period" flag still gates the response's "slots" field (the
    established CivicOS response key for the per-event from/to occurrence
    window) — only the REQUIRED-flags lookup key changes, matching the
    flag-name-vs-response-field-name distinction established by Log's Spec
    Quirk #2 (services.govstack_log.log_list / LogDetailsRequiredSerializer's
    "logger_category" flag gating the response's "logger_role" field). See
    services.govstack_event.event_list's _slot_to_event_dict for the read
    side of this gate.
    """

    event_id = serializers.BooleanField(required=False, default=True)
    name = serializers.BooleanField(required=False, default=True)
    description = serializers.BooleanField(required=False, default=False)
    category = serializers.BooleanField(required=False, default=True)
    host_entity_id = serializers.BooleanField(required=False, default=True)
    period = serializers.BooleanField(required=False, default=False)
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
    """
    PUT /event/modifications request body: { "details": <event_details> }

    Uses EventModifyDetailsSerializer (flat from/to, no slots array) — NOT
    EventDetailsSerializer, which models the POST-only event_creation_details
    schema. See EventModifyDetailsSerializer docstring for details (FIX 3).
    """

    details = EventModifyDetailsSerializer(required=True)


class EventListQrySerializer(serializers.Serializer):
    """GET /event/list_details request body."""

    event_filter = EventFilterSerializer(required=False)
    event_details_required = EventDetailsRequiredSerializer(required=False)


# ---------------------------------------------------------------------------
# 6. Appointment (→ CivicOS Booking)
# ---------------------------------------------------------------------------

class AppointmentCreateDetailsSerializer(serializers.Serializer):
    """
    GovStack appointment_creation_details — POST /appointment/new body shape.

    Distinct from AppointmentDetailsSerializer (used for modify/list) because
    the real GovStack OpenAPI spec uses TWO different schemas: this one has
    event_ids (plural array — an appointment can span multiple events/slots
    created atomically as one GovStack Appointment), the other has a singular
    event_id. Conflating these was a Wave-A-era bug (see the postmortem in
    govstack_event.py's module docstring for the analogous Event bug found
    and fixed by the Wave D review) — corrected here before Wave E ships.

    Verified against the real GovStack OpenAPI spec
    (components.schemas.appointment_creation_details, fetched directly from
    https://raw.githubusercontent.com/GovStackWorkingGroup/bb-scheduler/main/api/Govstack_scheduler_BB_APIs.json):
    exclusive, event_ids, participant_type, participant_id, participant_entity_id.
    """

    exclusive = serializers.BooleanField(
        required=False,
        default=False,
        help_text=(
            "True to block the underlying Slot(s) from further bookings "
            "after this appointment is created (Slot.status = 'blocked')."
        ),
    )
    event_ids = serializers.ListField(
        child=serializers.CharField(),
        required=True,
        allow_empty=False,
        help_text="Array of GovStack event_id strings (Slot UUIDs) this appointment books.",
    )
    participant_type = serializers.CharField(required=False, allow_blank=True)
    participant_id = serializers.CharField(required=False, allow_blank=True)
    participant_entity_id = serializers.CharField(required=False, allow_blank=True)


class AppointmentDetailsSerializer(serializers.Serializer):
    """
    GovStack appointment_details — used for PUT /appointment/modifications
    body AND each item's nested response shape conceptually (this codebase's
    own convention returns a flat dict, not nested — see govstack_appointment.py).

    event_id is SINGULAR here (unlike creation's event_ids array) — modifying
    an existing appointment targets one current event/slot at a time
    (rescheduling replaces it, doesn't add to it).

    Verified against the real GovStack OpenAPI spec
    (components.schemas.appointment_details): exclusive, event_id,
    participant_type, participant_id, status_id, participant_entity_id.
    """

    exclusive = serializers.BooleanField(required=False)
    event_id = serializers.CharField(required=False, allow_blank=True)
    participant_type = serializers.CharField(required=False, allow_blank=True)
    participant_id = serializers.CharField(required=False, allow_blank=True)
    status_id = serializers.CharField(required=False, allow_blank=True)
    participant_entity_id = serializers.CharField(required=False, allow_blank=True)


class AppointmentFilterSerializer(serializers.Serializer):
    """
    Filter parameters for GET /appointment/list_details.

    from/to use a to_internal_value() remap to from_/to attribute names
    because "from" is a Python reserved word and cannot be a class attribute
    — this mirrors the identical technique already used and tested in
    EventFilterSerializer (see govstack_serializers.py's Event section and
    test_govstack_event.py's EV52 for the pattern this replicates).
    """

    appointment_id = serializers.CharField(required=False, allow_blank=True)
    participant_type = serializers.CharField(required=False, allow_blank=True)
    participant_id = serializers.CharField(required=False, allow_blank=True)
    participant_entity_id = serializers.CharField(required=False, allow_blank=True)
    status = serializers.CharField(required=False, allow_blank=True)
    exclusive = serializers.BooleanField(required=False)
    from_ = serializers.CharField(required=False, allow_blank=True)
    to = serializers.CharField(required=False, allow_blank=True)

    def to_internal_value(self, data):
        data = dict(data)
        if "from" in data and "from_" not in data:
            data["from_"] = data.pop("from")
        return super().to_internal_value(data)


class AppointmentDetailsRequiredSerializer(serializers.Serializer):
    """
    Boolean flags controlling which Appointment fields appear in list responses.

    FIX 5 (Wave E adversarial review): the real GovStack OpenAPI spec's
    appointment_details_required schema uses the key "status" — NOT
    "status_id" — verified directly against the spec JSON. This field
    controls whether the response's "status_id" field (the real field name
    on appointment_details itself — that one is NOT renamed) is included;
    only the REQUIRED-flags lookup key changes here. Previously this
    serializer declared "status_id", so a spec-compliant caller sending
    {"appointment_details_required": {"status": false}} had that key
    silently dropped by DRF (unknown keys are ignored, not rejected) — the
    caller's explicit suppression request was silently ignored and the
    response included status_id anyway (same bug class the Wave D review
    found for a different field on the Event API).

    ``event_id`` is NOT part of the real spec's appointment_details_required
    schema (only ``event_details``, which nests the full per-slot
    projection, is) — kept here as an explicitly-documented CivicOS
    convenience so callers can suppress the flat event_id string
    independently of the nested event_details object.
    """

    appointment_id = serializers.BooleanField(required=False, default=True)
    exclusive = serializers.BooleanField(required=False, default=False)
    event_id = serializers.BooleanField(required=False, default=True)
    event_details = serializers.BooleanField(required=False, default=True)
    participant_type = serializers.BooleanField(required=False, default=True)
    participant_id = serializers.BooleanField(required=False, default=True)
    status = serializers.BooleanField(required=False, default=True)
    participant_entity_id = serializers.BooleanField(required=False, default=True)


class _AppointmentQryDetailsSerializer(serializers.Serializer):
    """
    Inner { "appointment_details": ... } wrapper used by AppointmentCreateQrySerializer.

    NOTE: the real spec's appointment_new_qry wraps the field as
    "appointment_details", NOT "details" (unlike event_new_qry, which does use
    "details") — confirmed directly from the OpenAPI schema
    (components.schemas.appointment_new_qry.properties). Do not copy the Event
    wrapper key name here.
    """

    appointment_details = AppointmentCreateDetailsSerializer(required=True)


class AppointmentCreateQrySerializer(serializers.Serializer):
    """POST /appointment/new request body: { "qry": { "appointment_details": <appointment_creation_details> } }"""

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
    """
    Filter parameters for GET /alert_schedule/list_details.

    Verified against the real GovStack OpenAPI spec
    (components.schemas.alert_schedule_filter): alert_schedule_id, entity_id,
    target_category, message_id, from, to. NOTE this uses entity_id — NOT
    event_id — unlike alert_schedule_details (the create/modify payload
    schema), because entity_id here is a DERIVED filter field (see
    services.govstack_alert_schedule.alert_schedule_list's docstring for the
    slot.location.organization_id derivation); there is no direct event_id
    filter in the real spec's alert_schedule_filter schema.

    from/to are the real spec's date-range window fields on alert_datetime.
    "from" is a Python reserved word and cannot be a class attribute; it is
    remapped to "from_" in to_internal_value() — identical technique to
    EventFilterSerializer/AppointmentFilterSerializer (see those docstrings
    for the precedent this replicates).
    """

    alert_schedule_id = serializers.CharField(required=False, allow_blank=True)
    entity_id = serializers.CharField(required=False, allow_blank=True)
    target_category = serializers.CharField(required=False, allow_blank=True)
    message_id = serializers.CharField(required=False, allow_blank=True)
    from_ = serializers.CharField(required=False, allow_blank=True)
    to = serializers.CharField(required=False, allow_blank=True)

    def to_internal_value(self, data):
        data = dict(data)
        if "from" in data and "from_" not in data:
            data["from_"] = data.pop("from")
        return super().to_internal_value(data)


class AlertScheduleDetailsRequiredSerializer(serializers.Serializer):
    """
    Boolean flags controlling which AlertSchedule fields appear in list responses.

    Verified against the real GovStack OpenAPI spec
    (components.schemas.alert_schedule_details_required): alert_schedule_id,
    entity_id, message_id, alert_datetime. There is no target_category flag
    in the real spec — target_category is always included in list responses
    regardless (see services.govstack_alert_schedule.alert_schedule_list).
    """

    alert_schedule_id = serializers.BooleanField(required=False, default=True)
    entity_id = serializers.BooleanField(required=False, default=True)
    message_id = serializers.BooleanField(required=False, default=True)
    alert_datetime = serializers.BooleanField(required=False, default=True)


class _AlertScheduleQryDetailsSerializer(serializers.Serializer):
    """
    Inner { "alert_schedule_details": ... } wrapper used by AlertScheduleCreateQrySerializer.

    NOTE: the real spec's alert_schedule_new_qry wraps the field as
    "alert_schedule_details", NOT "details" (unlike event_new_qry, which does
    use "details") — confirmed directly from the OpenAPI schema
    (components.schemas.alert_schedule_new_qry.properties). Do not copy the
    Event wrapper key name here (see AppointmentCreateQrySerializer's
    docstring above for the precedent this bug class was already caught and
    fixed for).
    """

    alert_schedule_details = AlertScheduleDetailsSerializer(required=True)


class AlertScheduleCreateQrySerializer(serializers.Serializer):
    """POST /alert_schedule/new request body: { "qry": { "alert_schedule_details": <alert_schedule_details> } }"""

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

    category's max_length=50 mirrors GovStackMessage.category's DB column
    (models.py) exactly — an over-length category is REJECTED with a 400 at
    the serializer layer rather than silently truncated at the DB layer.
    """

    entity_id = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True, max_length=50)
    message_body = serializers.CharField(required=False, allow_blank=True)


class MessageFilterSerializer(serializers.Serializer):
    """
    Filter parameters for GET /message/list_details.

    Verified against the real GovStack OpenAPI spec
    (components.schemas.message_filter): message_id, entity_id, category,
    message_body.
    """

    message_id = serializers.CharField(required=False, allow_blank=True)
    entity_id = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    message_body = serializers.CharField(required=False, allow_blank=True)


class MessageDetailsRequiredSerializer(serializers.Serializer):
    """Boolean flags controlling which Message fields appear in list responses."""

    message_id = serializers.BooleanField(required=False, default=True)
    entity_id = serializers.BooleanField(required=False, default=True)
    category = serializers.BooleanField(required=False, default=True)
    message_body = serializers.BooleanField(required=False, default=False)


class _MessageQryDetailsSerializer(serializers.Serializer):
    """
    Inner { "message_details": ... } wrapper used by MessageCreateQrySerializer.

    NOTE: the real spec's message_new_qry wraps the field as
    "message_details", NOT "details" (unlike event_new_qry, which does use
    "details") — confirmed directly from the OpenAPI schema
    (components.schemas.message_new_qry.properties). Do not copy the Event
    wrapper key name here (see AppointmentCreateQrySerializer's docstring
    above for the precedent this bug class was already caught and fixed for).
    """

    message_details = MessageDetailsSerializer(required=True)


class MessageCreateQrySerializer(serializers.Serializer):
    """POST /message/new request body: { "qry": { "message_details": <message_details> } }"""

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
    GovStack Log core fields — maps to CivicOS BookingAuditLog via
    services.govstack_log.log_create(). See that module's docstring for the
    full field-mapping rationale (booking resolution via log_data parsing,
    datetime accepted-but-ignored due to auto_now_add, logger_role validated
    against a real enum while log_category is deliberately unconstrained,
    etc).

    All fields are DRF-optional (required=False, allow_blank=True) — the
    SERVICE layer enforces which fields are effectively required
    (logger_role, log_category, log_data), matching the established
    DRF-vs-service required-ness split used throughout this module (e.g.
    MessageDetailsSerializer / AlertScheduleDetailsSerializer above).

    IMPORTANT: The GovStack spec defines PUT and DELETE operations on logs,
    but CivicOS intentionally returns HTTP 405 (Method Not Allowed) for both
    to preserve audit log immutability (BookingAuditLog.save()/delete() are
    hard model invariants — see models.py). See SPEC_APPOINTMENTS_BB_GOVSTACK.md
    §5.9.

    PII handling: log_data must NEVER contain citizen name, email, or other
    PII — see services.govstack_log module docstring's "log_data" entry for
    the full documented assumption behind storing it in BookingAuditLog.detail.
    """

    logger_role = serializers.CharField(required=False, allow_blank=True)
    logger_id = serializers.CharField(required=False, allow_blank=True)
    entity_id = serializers.CharField(required=False, allow_blank=True)
    log_category = serializers.CharField(required=False, allow_blank=True)
    datetime = serializers.CharField(required=False, allow_blank=True)
    log_data = serializers.CharField(required=False, allow_blank=True)


class LogFilterSerializer(serializers.Serializer):
    """
    Filter parameters for GET /log/list_details.

    SPEC QUIRK #1 (verified directly against the real GovStack OpenAPI spec
    JSON, not a typo to "fix"): the filter field is literally named
    "category" — NOT "log_category" (which is what log_details /
    log_details_required use for the same concept). See
    services.govstack_log's module docstring for the full rationale;
    implemented here exactly as the real spec defines it.

    from/to use the identical to_internal_value() remap technique as
    EventFilterSerializer / AppointmentFilterSerializer /
    AlertScheduleFilterSerializer above — "from" is a Python reserved word
    and cannot be a class attribute.
    """

    log_id = serializers.CharField(required=False, allow_blank=True)
    entity_id = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    from_ = serializers.CharField(required=False, allow_blank=True)
    to = serializers.CharField(required=False, allow_blank=True)

    def to_internal_value(self, data):
        data = dict(data)
        if "from" in data and "from_" not in data:
            data["from_"] = data.pop("from")
        return super().to_internal_value(data)


class LogDetailsRequiredSerializer(serializers.Serializer):
    """
    Boolean flags controlling which Log fields appear in list responses.

    SPEC QUIRK #2 (verified directly against the real GovStack OpenAPI spec
    JSON, not a typo to "fix"): this object's field is literally named
    "logger_category" — NOT "logger_role" (which is what log_details itself
    uses for the same concept). The flag gates inclusion of the response's
    "logger_role" field — this is a real spec inconsistency (the flag is
    spelled differently from the value it controls), preserved here exactly
    rather than renamed for consistency. See services.govstack_log.log_list's
    docstring for the full rationale.

    log_data defaults to False (the "heaviest"/most-detail field) — matches
    MessageDetailsRequiredSerializer.message_body's identical default-False
    precedent above.
    """

    log_id = serializers.BooleanField(required=False, default=True)
    logger_category = serializers.BooleanField(required=False, default=True)
    logger_id = serializers.BooleanField(required=False, default=True)
    entity_id = serializers.BooleanField(required=False, default=True)
    log_category = serializers.BooleanField(required=False, default=True)
    datetime = serializers.BooleanField(required=False, default=True)
    log_data = serializers.BooleanField(required=False, default=False)


class _LogQryDetailsSerializer(serializers.Serializer):
    """
    Inner { "log_details": ... } wrapper used by LogCreateQrySerializer.

    NOTE: the real spec's log_new_qry wraps the field as "log_details" — NOT
    "details" (unlike event_new_qry, which does use "details") — confirmed
    directly from the OpenAPI schema (components.schemas.log_new_qry.properties).
    Do not copy the Event wrapper key name here. This is the SAME bug class
    that shipped in Wave F and was caught and fixed in Wave F's deep review
    (MessageCreateQrySerializer / AlertScheduleCreateQrySerializer initially
    wrapped under "details" instead of "message_details" /
    "alert_schedule_details" — see those classes' docstrings above for the
    precedent this bug class was already caught and fixed for).
    """

    log_details = LogDetailsSerializer(required=True)


class LogCreateQrySerializer(serializers.Serializer):
    """POST /log/new request body: { "qry": { "log_details": <log_details> } }"""

    qry = _LogQryDetailsSerializer(required=True)


class LogModifySerializer(serializers.Serializer):
    """
    PUT /log/modifications request body: { "details": <log_details> }

    Per the real spec's log_modify_qry schema, modify DOES use the generic
    "details" wrapper key (unlike log_new_qry, which uses "log_details" —
    see _LogQryDetailsSerializer's docstring above). Defined here for
    completeness/reference only — LogModificationsView intentionally returns
    HTTP 405 (Method Not Allowed) unconditionally to preserve audit log
    immutability, so this serializer is never actually invoked.
    """

    details = LogDetailsSerializer(required=True)


class LogListQrySerializer(serializers.Serializer):
    """GET /log/list_details request body."""

    log_filter = LogFilterSerializer(required=False)
    log_details_required = LogDetailsRequiredSerializer(required=False)
