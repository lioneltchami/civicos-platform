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
from rest_framework.decorators import api_view
from rest_framework.response import Response

app_name = "govstack_scheduler"


@api_view(["GET", "POST", "PUT", "DELETE"])
def govstack_not_implemented(request, *args, **kwargs):
    """
    Wave A stub view — returns HTTP 501 Not Implemented for all endpoints.

    The @api_view decorator ensures DRF runs its authentication and permission
    pipeline before the stub responds. This means requestor_id + request_token
    validation via GovStackSchedulerAuth is exercised even before Wave B views
    exist, allowing early integration testing with the GovStack harness.

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
    path("event/new", govstack_not_implemented, name="event_new"),
    path("event/modifications", govstack_not_implemented, name="event_modifications"),
    path("event/list_details", govstack_not_implemented, name="event_list_details"),
    path("event", govstack_not_implemented, name="event_delete"),

    # ── Entity (Wave B) ───────────────────────────────────────────────────────
    path("entity/new", govstack_not_implemented, name="entity_new"),
    path("entity/modifications", govstack_not_implemented, name="entity_modifications"),
    path("entity/list_details", govstack_not_implemented, name="entity_list_details"),
    path("entity", govstack_not_implemented, name="entity_delete"),

    # ── AlertSchedule (Wave F) ────────────────────────────────────────────────
    path("alert_schedule/new", govstack_not_implemented, name="alert_schedule_new"),
    path("alert_schedule/modifications", govstack_not_implemented, name="alert_schedule_modifications"),
    path("alert_schedule/list_details", govstack_not_implemented, name="alert_schedule_list_details"),
    path("alert_schedule", govstack_not_implemented, name="alert_schedule_delete"),

    # ── Message (Wave F) ──────────────────────────────────────────────────────
    path("message/new", govstack_not_implemented, name="message_new"),
    path("message/modifications", govstack_not_implemented, name="message_modifications"),
    path("message/list_details", govstack_not_implemented, name="message_list_details"),
    path("message", govstack_not_implemented, name="message_delete"),

    # ── Resource (Wave B) — sub-paths listed before bare DELETE path ──────────
    path("resource/new", govstack_not_implemented, name="resource_new"),
    path("resource/modifications", govstack_not_implemented, name="resource_modifications"),
    path("resource/availability", govstack_not_implemented, name="resource_availability"),
    path("resource/list_details", govstack_not_implemented, name="resource_list_details"),
    path("resource", govstack_not_implemented, name="resource_delete"),

    # ── Subscriber (Wave C) ───────────────────────────────────────────────────
    path("subscriber/new", govstack_not_implemented, name="subscriber_new"),
    path("subscriber/modifications", govstack_not_implemented, name="subscriber_modifications"),
    path("subscriber/list_details", govstack_not_implemented, name="subscriber_list_details"),
    path("subscriber", govstack_not_implemented, name="subscriber_delete"),

    # ── Affiliation (Wave B) ──────────────────────────────────────────────────
    path("affiliation/new", govstack_not_implemented, name="affiliation_new"),
    path("affiliation/modifications", govstack_not_implemented, name="affiliation_modifications"),
    path("affiliation/list_details", govstack_not_implemented, name="affiliation_list_details"),
    path("affiliation", govstack_not_implemented, name="affiliation_delete"),

    # ── Appointment (Wave E) ──────────────────────────────────────────────────
    path("appointment/new", govstack_not_implemented, name="appointment_new"),
    path("appointment/modifications", govstack_not_implemented, name="appointment_modifications"),
    path("appointment/list_details", govstack_not_implemented, name="appointment_list_details"),
    path("appointment", govstack_not_implemented, name="appointment_delete"),

    # ── Log (Wave G) — PUT /log/modifications and DELETE /log will return 405 ─
    # in the Wave G implementation to preserve BookingAuditLog immutability.
    # The stubs return 501 until Wave G replaces them.
    path("log/new", govstack_not_implemented, name="log_new"),
    path("log/modifications", govstack_not_implemented, name="log_modifications"),
    path("log/list_details", govstack_not_implemented, name="log_list_details"),
    path("log", govstack_not_implemented, name="log_delete"),
]
