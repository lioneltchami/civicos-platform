"""
GovStack Scheduler BB — URL routing.

Mounted at: /govstack/scheduler/  (configured in config/urls.py)
Namespace:  govstack_scheduler

All 37 endpoints implement the GovStack Scheduler BB OpenAPI path convention:
  /<entity>/<operation>

Status: FINISHED — all 37 endpoints across all 9 API groups are live,
concrete APIView classes (Waves B–G). There are no remaining stub/placeholder
routes in this module.

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
  TOTAL: 37 endpoints (all implemented as of Wave G)

Ordering note for resource paths:
  resource/availability and resource/list_details are listed before the bare
  resource DELETE path. Django's path() resolver does not use regex and <str:>
  converters never match slashes, so there is no ambiguity here — the ordering
  is purely for human readability and to make intent explicit.
"""  # noqa: RUF002

from django.urls import path

from apps.appointments.govstack_views import (
    AffiliationDeleteView,
    AffiliationListDetailsView,
    AffiliationModificationsView,
    # Wave B — Affiliation
    AffiliationNewView,
    AlertScheduleDeleteView,
    AlertScheduleListDetailsView,
    AlertScheduleModificationsView,
    # Wave F — AlertSchedule
    AlertScheduleNewView,
    AppointmentDeleteView,
    AppointmentListDetailsView,
    AppointmentModificationsView,
    # Wave E — Appointment
    AppointmentNewView,
    EntityDeleteView,
    EntityListDetailsView,
    EntityModificationsView,
    # Wave B — Entity
    EntityNewView,
    EventDeleteView,
    EventListDetailsView,
    EventModificationsView,
    # Wave D — Event
    EventNewView,
    LogDeleteView,
    LogListDetailsView,
    LogModificationsView,
    # Wave G — Log
    LogNewView,
    MessageDeleteView,
    MessageListDetailsView,
    MessageModificationsView,
    # Wave F — Message
    MessageNewView,
    ResourceAvailabilityView,
    ResourceDeleteView,
    ResourceListDetailsView,
    ResourceModificationsView,
    # Wave B — Resource
    ResourceNewView,
    SubscriberDeleteView,
    SubscriberListDetailsView,
    SubscriberModificationsView,
    # Wave C — Subscriber
    SubscriberNewView,
)

app_name = "govstack_scheduler"


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
    path(
        "resource/modifications", ResourceModificationsView.as_view(), name="resource_modifications"
    ),
    path("resource/availability", ResourceAvailabilityView.as_view(), name="resource_availability"),
    path("resource/list_details", ResourceListDetailsView.as_view(), name="resource_list_details"),
    path("resource", ResourceDeleteView.as_view(), name="resource_delete"),
    # ── Subscriber (Wave C) ───────────────────────────────────────────────────
    path("subscriber/new", SubscriberNewView.as_view(), name="subscriber_new"),
    path(
        "subscriber/modifications",
        SubscriberModificationsView.as_view(),
        name="subscriber_modifications",
    ),
    path(
        "subscriber/list_details",
        SubscriberListDetailsView.as_view(),
        name="subscriber_list_details",
    ),
    path("subscriber", SubscriberDeleteView.as_view(), name="subscriber_delete"),
    # ── Affiliation (Wave B) ──────────────────────────────────────────────────
    path("affiliation/new", AffiliationNewView.as_view(), name="affiliation_new"),
    path(
        "affiliation/modifications",
        AffiliationModificationsView.as_view(),
        name="affiliation_modifications",
    ),
    path(
        "affiliation/list_details",
        AffiliationListDetailsView.as_view(),
        name="affiliation_list_details",
    ),
    path("affiliation", AffiliationDeleteView.as_view(), name="affiliation_delete"),
    # ── Appointment (Wave E) ──────────────────────────────────────────────────
    path("appointment/new", AppointmentNewView.as_view(), name="appointment_new"),
    path(
        "appointment/modifications",
        AppointmentModificationsView.as_view(),
        name="appointment_modifications",
    ),
    path(
        "appointment/list_details",
        AppointmentListDetailsView.as_view(),
        name="appointment_list_details",
    ),
    path("appointment", AppointmentDeleteView.as_view(), name="appointment_delete"),
    # ── Log (Wave G) — sub-paths listed before bare DELETE path. PUT
    # /log/modifications and DELETE /log return 405 unconditionally
    # (BookingAuditLog immutability — see govstack_views.py's Log views
    # section docstring and services.govstack_log's module docstring). ─────
    path("log/new", LogNewView.as_view(), name="log_new"),
    path("log/modifications", LogModificationsView.as_view(), name="log_modifications"),
    path("log/list_details", LogListDetailsView.as_view(), name="log_list_details"),
    path("log", LogDeleteView.as_view(), name="log_delete"),
]
