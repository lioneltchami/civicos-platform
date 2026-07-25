"""
GovStack Scheduler BB — URL routing.

Mounted at: /govstack/scheduler/  (configured in config/urls.py)
Namespace:  govstack_scheduler

All 37 endpoints implement the GovStack Scheduler BB OpenAPI path convention:
  /<entity>/<operation>

Wave A stubs: every endpoint returns HTTP 501 Not Implemented via the
govstack_not_implemented view defined below. This avoids importing from the
not-yet-existing govstack_views module. Concrete implementations replace these
stubs in Waves B–G.

Entity groups and endpoint counts:
  event          → 4 endpoints  (Wave D)
  entity         → 4 endpoints  (Wave B)
  alert_schedule → 4 endpoints  (Wave F)
  message        → 4 endpoints  (Wave F)
  resource       → 5 endpoints  (Wave B — includes /resource/availability)
  subscriber     → 4 endpoints  (Wave C)
  affiliation    → 4 endpoints  (Wave B)
  appointment    → 4 endpoints  (Wave E)
  log            → 4 endpoints  (Wave G — PUT/DELETE return 405 for audit immutability)
  TOTAL: 37 endpoints

Ordering note for resource paths:
  resource/availability and resource/list_details are listed before the bare
  resource DELETE path. Django's path() resolver does not use regex and <str:>
  converters never match slashes, so there is no ambiguity here — the ordering
  is purely for human readability and to make intent explicit.
"""
from django.urls import path
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from apps.appointments.govstack_auth import GovStackSchedulerAuth, GovStackSchedulerPermission
from apps.appointments.govstack_views import (
    # Wave B — Entity
    EntityNewView,
    EntityModificationsView,
    EntityDeleteView,
    EntityListDetailsView,
    # Wave B — Resource
    ResourceNewView,
    ResourceModificationsView,
    ResourceDeleteView,
    ResourceListDetailsView,
    ResourceAvailabilityView,
    # Wave B — Affiliation
    AffiliationNewView,
    AffiliationModificationsView,
    AffiliationDeleteView,
    AffiliationListDetailsView,
    # Wave C — Subscriber
    SubscriberNewView,
    SubscriberModificationsView,
    SubscriberDeleteView,
    SubscriberListDetailsView,
    # Wave D — Event
    EventNewView,
    EventModificationsView,
    EventDeleteView,
    EventListDetailsView,
    # Wave E — Appointment
    AppointmentNewView,
    AppointmentModificationsView,
    AppointmentDeleteView,
    AppointmentListDetailsView,
    # Wave F — AlertSchedule
    AlertScheduleNewView,
    AlertScheduleModificationsView,
    AlertScheduleDeleteView,
    AlertScheduleListDetailsView,
    # Wave F — Message
    MessageNewView,
    MessageModificationsView,
    MessageDeleteView,
    MessageListDetailsView,
)

app_name = "govstack_scheduler"


@api_view(["GET", "POST", "PUT", "DELETE"])
@authentication_classes([GovStackSchedulerAuth])
@permission_classes([GovStackSchedulerPermission])
def govstack_not_implemented(request, *args, **kwargs):
    """
    Wave A stub view — returns HTTP 501 Not Implemented for all endpoints.

    GovStackSchedulerAuth and GovStackSchedulerPermission are applied explicitly
    so that requestor_id + request_token validation is exercised even before the
    real Wave B–G views exist. Unauthenticated callers receive 403, not 501.
    Authenticated GovStack BB callers receive 501 — the expected harness response
    for unimplemented-but-reachable endpoints.

    This stub will be replaced with real APIView classes in Waves B–G.
    """
    return Response(
        {
            "status": "error",
            "code": "NOT_IMPLEMENTED",
            "message": "This endpoint is scheduled for a future implementation wave.",
        },
        status=501,
    )


urlpatterns = [
    # ── Event (Wave D) ────────────────────────────────────────────────────────
    path("event/new", EventNewView.as_view(), name="event_new"),
    path("event/modifications", EventModificationsView.as_view(), name="event_modifications"),
    path("event/list_details", EventListDetailsView.as_view(), name="event_list_details"),
    path("event", EventDeleteView.as_view(), name="event_delete"),

    # ── Entity (Wave B) ───────────────────────────────────────────────────────
    path("entity/new", EntityNewView.as_view(), name="entity_new"),
    path("entity/modifications", EntityModificationsView.as_view(), name="entity_modifications"),
    path("entity/list_details", EntityListDetailsView.as_view(), name="entity_list_details"),
    path("entity", EntityDeleteView.as_view(), name="entity_delete"),

    # ── AlertSchedule (Wave F) — sub-paths listed before bare DELETE path ──────
    path("alert_schedule/new", AlertScheduleNewView.as_view(), name="alert_schedule_new"),
    path(
        "alert_schedule/modifications",
        AlertScheduleModificationsView.as_view(),
        name="alert_schedule_modifications",
    ),
    path(
        "alert_schedule/list_details",
        AlertScheduleListDetailsView.as_view(),
        name="alert_schedule_list_details",
    ),
    path("alert_schedule", AlertScheduleDeleteView.as_view(), name="alert_schedule_delete"),

    # ── Message (Wave F) — sub-paths listed before bare DELETE path ────────────
    path("message/new", MessageNewView.as_view(), name="message_new"),
    path("message/modifications", MessageModificationsView.as_view(), name="message_modifications"),
    path("message/list_details", MessageListDetailsView.as_view(), name="message_list_details"),
    path("message", MessageDeleteView.as_view(), name="message_delete"),

    # ── Resource (Wave B) — sub-paths listed before bare DELETE path ──────────
    path("resource/new", ResourceNewView.as_view(), name="resource_new"),
    path("resource/modifications", ResourceModificationsView.as_view(), name="resource_modifications"),
    path("resource/availability", ResourceAvailabilityView.as_view(), name="resource_availability"),
    path("resource/list_details", ResourceListDetailsView.as_view(), name="resource_list_details"),
    path("resource", ResourceDeleteView.as_view(), name="resource_delete"),

    # ── Subscriber (Wave C) ───────────────────────────────────────────────────
    path("subscriber/new", SubscriberNewView.as_view(), name="subscriber_new"),
    path("subscriber/modifications", SubscriberModificationsView.as_view(), name="subscriber_modifications"),
    path("subscriber/list_details", SubscriberListDetailsView.as_view(), name="subscriber_list_details"),
    path("subscriber", SubscriberDeleteView.as_view(), name="subscriber_delete"),

    # ── Affiliation (Wave B) ──────────────────────────────────────────────────
    path("affiliation/new", AffiliationNewView.as_view(), name="affiliation_new"),
    path("affiliation/modifications", AffiliationModificationsView.as_view(), name="affiliation_modifications"),
    path("affiliation/list_details", AffiliationListDetailsView.as_view(), name="affiliation_list_details"),
    path("affiliation", AffiliationDeleteView.as_view(), name="affiliation_delete"),

    # ── Appointment (Wave E) ──────────────────────────────────────────────────
    path("appointment/new", AppointmentNewView.as_view(), name="appointment_new"),
    path("appointment/modifications", AppointmentModificationsView.as_view(), name="appointment_modifications"),
    path("appointment/list_details", AppointmentListDetailsView.as_view(), name="appointment_list_details"),
    path("appointment", AppointmentDeleteView.as_view(), name="appointment_delete"),

    # ── Log (Wave G) — PUT /log/modifications and DELETE /log will return 405 ─
    # in the Wave G implementation to preserve BookingAuditLog immutability.
    # The stubs return 501 until Wave G replaces them.
    path("log/new", govstack_not_implemented, name="log_new"),
    path("log/modifications", govstack_not_implemented, name="log_modifications"),
    path("log/list_details", govstack_not_implemented, name="log_list_details"),
    path("log", govstack_not_implemented, name="log_delete"),
]
