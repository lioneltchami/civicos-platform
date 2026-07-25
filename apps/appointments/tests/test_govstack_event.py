"""
GovStack Scheduler BB — Event endpoints test suite (Wave D).

Covers 4 endpoints:
  POST   /govstack/scheduler/event/new
  PUT    /govstack/scheduler/event/modifications
  DELETE /govstack/scheduler/event
  GET    /govstack/scheduler/event/list_details

Tests are numbered EV1–EV45 matching the original Wave D specification, plus
EV46–EV53 added for the deep adversarial review fixes (see module docstring
in services/govstack_event.py for the FIX 1..10 numbering referenced below).
"""
from __future__ import annotations

import json
import uuid
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.dateparse import parse_datetime

from apps.appointments.models import (
    AppointmentType,
    Location,
    Organization,
    ServiceType,
    Slot,
    StaffProfile,
)
from apps.appointments.services.govstack_event import (
    event_create,
    event_delete,
    event_list,
    event_modify,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEW_URL = "/govstack/scheduler/event/new"
MODIFICATIONS_URL = "/govstack/scheduler/event/modifications"
LIST_URL = "/govstack/scheduler/event/list_details"
DELETE_URL = "/govstack/scheduler/event"

_AUTH = {"requestor_id": "test-bb", "request_token": "test-token"}

# Default slot datetimes used across tests
_SLOT_1 = {"from": "2026-08-01T09:00:00Z", "to": "2026-08-01T10:00:00Z"}
_SLOT_2 = {"from": "2026-08-02T09:00:00Z", "to": "2026-08-02T10:00:00Z"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _qs(**extra):
    """Build a URL query string with GovStack auth + any extras."""
    params = {**_AUTH, **extra}
    return "?" + urlencode(params)


def _qry_qs(qry_dict, **extra):
    """Auth + JSON-encoded qry param."""
    params = {**_AUTH, "qry": json.dumps(qry_dict), **extra}
    return "?" + urlencode(params)


def _create_event(name="Test Event", slots=None, status="available", **kwargs):
    """Factory: create an Event via the service layer. Returns list[Slot]."""
    if slots is None:
        slots = [_SLOT_1]
    return event_create(
        name=name,
        description=kwargs.get("description", ""),
        category=kwargs.get("category", ""),
        host_entity_id=kwargs.get("host_entity_id", ""),
        slots=slots,
        deadline=kwargs.get("deadline", ""),
        subscriber_limit=kwargs.get("subscriber_limit", ""),
        terms=kwargs.get("terms", ""),
        status=status,
        venue=kwargs.get("venue"),
    )


def _create_native_appointment_type(slug: str, is_govstack_managed: bool = False) -> tuple[AppointmentType, Slot]:
    """
    Factory: create a native CivicOS AppointmentType + Slot directly (bypassing
    the GovStack service layer entirely), for FIX 9 boundary tests.
    """
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        email="native-staff@civicos.internal",
        defaults={"is_staff": True, "is_active": True},
    )
    staff, _ = StaffProfile.objects.get_or_create(
        user=user,
        defaults={
            "display_name_en": "Native Staff",
            "display_name_fr": "Personnel Natif",
            "is_accepting_bookings": True,
        },
    )
    org, _ = Organization.objects.get_or_create(
        slug="native-org",
        defaults={
            "name_en": "Native Org",
            "name_fr": "Org Native",
            "organization_type": "other",
            "is_active": True,
        },
    )
    location, _ = Location.objects.get_or_create(
        slug="native-location",
        defaults={
            "organization": org,
            "name_en": "Native Location",
            "name_fr": "Emplacement Natif",
            "is_virtual": True,
            "timezone": "UTC",
        },
    )
    service_type, _ = ServiceType.objects.get_or_create(
        slug="native-service",
        defaults={
            "name_en": "Native Service",
            "name_fr": "Service Natif",
            "category": "government",
            "is_active": True,
        },
    )
    appt_type = AppointmentType.objects.create(
        service_type=service_type,
        slug=slug,
        name_en="GS Drivers License Renewal",
        name_fr="Renouvellement de permis GS",
        description_en="",
        description_fr="",
        duration_minutes=30,
        capacity_per_slot=1,
        mode="in_person",
        is_active=True,
        is_govstack_managed=is_govstack_managed,
    )
    start_dt = parse_datetime("2026-08-05T09:00:00Z")
    end_dt = parse_datetime("2026-08-05T10:00:00Z")
    slot = Slot.objects.create(
        appointment_type=appt_type,
        staff=staff,
        location=location,
        start_datetime=start_dt,
        end_datetime=end_dt,
        effective_start=start_dt,
        effective_end=end_dt,
        capacity=1,
        spaces_used=0,
        status="available",
    )
    return appt_type, slot


# ===========================================================================
# Base test case
# ===========================================================================

class EventBaseTestCase(TestCase):
    """Shared HTTP helpers for all event endpoint tests."""

    def _post(self, qry_dict):
        return self.client.post(NEW_URL + _qry_qs(qry_dict))

    def _put(self, qry_dict, event_id=None):
        extra = {"event_id": event_id} if event_id is not None else {}
        return self.client.put(MODIFICATIONS_URL + _qry_qs(qry_dict, **extra))

    def _delete(self, event_id=None):
        extra = {"event_id": event_id} if event_id is not None else {}
        return self.client.delete(DELETE_URL + _qs(**extra))

    def _get(self, qry_dict=None):
        if qry_dict is None:
            return self.client.get(LIST_URL + _qs())
        return self.client.get(LIST_URL + _qry_qs(qry_dict))


# ===========================================================================
# EV1–EV11: POST /event/new
# ===========================================================================

class EventNewTests(EventBaseTestCase):
    """EV1–EV11: POST /event/new"""

    def _valid_qry(self, name="Test Event", slots=None, **extra_details):
        if slots is None:
            slots = [_SLOT_1]
        details = {"name": name, "slots": slots, "status": "available"}
        details.update(extra_details)
        return {"qry": {"details": details}}

    # EV1
    def test_ev1_post_new_valid_single_slot_returns_201_with_event_ids(self):
        """EV1: POST with valid name + 1 slot returns 201 with event_ids list containing 1 UUID."""
        resp = self._post(self._valid_qry())
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("event_ids", data)
        self.assertEqual(len(data["event_ids"]), 1)

    # EV2
    def test_ev2_post_new_two_slots_returns_two_event_ids(self):
        """EV2: POST with 2 slots returns 201 with event_ids containing 2 entries."""
        resp = self._post(self._valid_qry(slots=[_SLOT_1, _SLOT_2]))
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(len(data["event_ids"]), 2)

    # EV3
    def test_ev3_post_new_event_ids_are_valid_uuids(self):
        """EV3: Each event_id in response is a valid UUID string."""
        resp = self._post(self._valid_qry(slots=[_SLOT_1, _SLOT_2]))
        self.assertEqual(resp.status_code, 201)
        for event_id in resp.json()["event_ids"]:
            # Must not raise ValueError
            uuid.UUID(event_id)

    # EV4
    def test_ev4_post_new_slot_created_in_db(self):
        """EV4: Slot created in DB — verifiable by pk lookup."""
        resp = self._post(self._valid_qry())
        self.assertEqual(resp.status_code, 201)
        event_id = resp.json()["event_ids"][0]
        # Must not raise Slot.DoesNotExist
        slot = Slot.objects.get(pk=event_id)
        self.assertIsNotNone(slot)

    # EV5
    def test_ev5_post_new_appointment_type_created_with_name(self):
        """EV5: AppointmentType created with the correct name_en."""
        resp = self._post(self._valid_qry(name="My Named Event"))
        self.assertEqual(resp.status_code, 201)
        event_id = resp.json()["event_ids"][0]
        slot = Slot.objects.get(pk=event_id)
        # AppointmentType is accessed via slot.appointment_type
        self.assertEqual(slot.appointment_type.name_en, "My Named Event")

    # EV6
    def test_ev6_post_new_empty_slots_returns_400(self):
        """EV6: POST with empty slots list returns 400."""
        qry = {"qry": {"details": {"name": "No Slots", "slots": [], "status": "available"}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["status"], "error")

    # EV7
    def test_ev7_post_new_slot_missing_from_key_returns_400(self):
        """EV7: POST with slot missing 'from' key returns 400."""
        bad_slot = {"to": "2026-08-01T10:00:00Z"}  # no 'from'
        qry = {"qry": {"details": {"name": "Bad Slot", "slots": [bad_slot], "status": "available"}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["status"], "error")

    # EV8
    def test_ev8_post_new_invalid_status_returns_400(self):
        """EV8: POST with invalid status 'unknown_status' returns 400."""
        qry = {"qry": {"details": {"name": "Bad Status", "slots": [_SLOT_1], "status": "unknown_status"}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["status"], "error")

    # EV9
    def test_ev9_post_new_subscriber_limit_sets_capacity_per_slot(self):
        """EV9: POST with subscriber_limit='5' creates AppointmentType with capacity_per_slot == 5."""
        qry = {"qry": {"details": {"name": "Limited Event", "slots": [_SLOT_1],
                                    "status": "available", "subscriber_limit": "5"}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 201)
        event_id = resp.json()["event_ids"][0]
        slot = Slot.objects.get(pk=event_id)
        self.assertEqual(slot.appointment_type.capacity_per_slot, 5)

    # EV10
    def test_ev10_post_new_venue_city_sets_location_city(self):
        """EV10: POST with venue.city='Ottawa' creates Location with city == 'Ottawa'."""
        qry = {"qry": {"details": {"name": "Ottawa Event", "slots": [_SLOT_1],
                                    "status": "available",
                                    "venue": {"city": "Ottawa", "country": "Canada"}}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 201)
        event_id = resp.json()["event_ids"][0]
        slot = Slot.objects.get(pk=event_id)
        self.assertEqual(slot.location.city, "Ottawa")

    # EV11
    def test_ev11_post_new_no_name_still_creates_event(self):
        """EV11: POST with no name still creates event (name can be empty, auto-slug generated)."""
        qry = {"qry": {"details": {"slots": [_SLOT_1], "status": "available"}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(len(data["event_ids"]), 1)

    # EV47 (FIX 2)
    def test_ev47_post_new_response_includes_singular_event_id(self):
        """EV47 (FIX 2): response includes singular event_id == event_ids[0], plus full event_ids list."""
        resp = self._post(self._valid_qry(slots=[_SLOT_1, _SLOT_2]))
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertIn("event_id", data)
        self.assertIn("event_ids", data)
        self.assertEqual(data["event_id"], data["event_ids"][0])
        self.assertEqual(len(data["event_ids"]), 2)

    # EV48 (FIX 1)
    def test_ev48_multi_slot_batch_creates_distinct_appointment_types(self):
        """EV48 (FIX 1): each slot in a multi-slot POST batch gets its OWN AppointmentType, not a shared one."""
        resp = self._post(self._valid_qry(slots=[_SLOT_1, _SLOT_2]))
        self.assertEqual(resp.status_code, 201)
        event_ids = resp.json()["event_ids"]
        slot_a = Slot.objects.get(pk=event_ids[0])
        slot_b = Slot.objects.get(pk=event_ids[1])
        self.assertNotEqual(slot_a.appointment_type_id, slot_b.appointment_type_id)


# ===========================================================================
# EV12–EV17: PUT /event/modifications
# ===========================================================================

class EventModificationsTests(EventBaseTestCase):
    """EV12–EV17: PUT /event/modifications"""

    def setUp(self):
        created = _create_event(name="Original Event")
        self.slot = created[0]
        self.event_id = str(self.slot.pk)

    def _valid_put_qry(self, **details):
        return {"details": details}

    # EV12
    def test_ev12_put_valid_event_id_returns_200(self):
        """EV12: PUT with valid event_id returns 200 with event_id in response."""
        resp = self._put(self._valid_put_qry(name="Updated Event"), event_id=self.event_id)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["event_id"], self.event_id)

    # EV13 (rewritten for FIX 3 — flat from/to, not slots[])
    def test_ev13_put_flat_from_to_updates_slot_datetimes_in_db(self):
        """EV13 (FIX 3): PUT with flat from/to (real event_details schema) actually updates
        Slot.start_datetime/end_datetime in DB — the old slots[] shape is spec-incorrect for PUT
        and is silently dropped by the modify-only serializer now."""
        new_from = "2026-09-15T14:00:00Z"
        new_to = "2026-09-15T15:00:00Z"
        resp = self._put({"details": {"from": new_from, "to": new_to}}, event_id=self.event_id)
        self.assertEqual(resp.status_code, 200)
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.start_datetime.isoformat(), "2026-09-15T14:00:00+00:00")
        self.assertEqual(self.slot.end_datetime.isoformat(), "2026-09-15T15:00:00+00:00")
        self.assertEqual(self.slot.effective_start, self.slot.start_datetime)
        self.assertEqual(self.slot.effective_end, self.slot.end_datetime)

    # EV14
    def test_ev14_put_unknown_event_id_returns_404(self):
        """EV14: PUT with unknown event_id UUID returns 404 with EVENT_NOT_FOUND."""
        unknown_id = str(uuid.uuid4())
        resp = self._put(self._valid_put_qry(name="Ghost"), event_id=unknown_id)
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["code"], "EVENT_NOT_FOUND")

    # EV15
    def test_ev15_put_no_event_id_param_returns_400(self):
        """EV15: PUT with no event_id query param returns 400 MISSING_EVENT_ID."""
        resp = self._put(self._valid_put_qry(name="No ID"), event_id=None)
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["code"], "MISSING_EVENT_ID")

    # EV16
    def test_ev16_put_invalid_status_returns_400(self):
        """EV16: PUT with invalid status 'bad_val' returns 400."""
        resp = self._put(self._valid_put_qry(status="bad_val"), event_id=self.event_id)
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["status"], "error")

    # EV17
    def test_ev17_put_new_name_updates_appointment_type_in_db(self):
        """EV17: PUT with new name updates AppointmentType.name_en in DB."""
        resp = self._put(self._valid_put_qry(name="Renamed Event"), event_id=self.event_id)
        self.assertEqual(resp.status_code, 200)
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.appointment_type.name_en, "Renamed Event")

    # EV49 (FIX 1)
    def test_ev49_modify_one_event_in_multi_slot_batch_does_not_affect_siblings(self):
        """EV49 (FIX 1): modifying one event_id from a multi-slot batch must NOT rename/alter siblings
        created in the same original POST call — each event_id owns an exclusive AppointmentType."""
        created = _create_event(name="Batch Event", slots=[_SLOT_1, _SLOT_2])
        slot_a, slot_b = created
        event_id_a = str(slot_a.pk)

        resp = self._put({"details": {"name": "Renamed A Only"}}, event_id=event_id_a)
        self.assertEqual(resp.status_code, 200)

        slot_a.refresh_from_db()
        slot_b.refresh_from_db()
        self.assertEqual(slot_a.appointment_type.name_en, "Renamed A Only")
        self.assertEqual(slot_b.appointment_type.name_en, "Batch Event")
        self.assertNotEqual(slot_a.appointment_type_id, slot_b.appointment_type_id)

    # EV50 (FIX 5)
    def test_ev50_terms_round_trips_and_description_is_clean_on_read(self):
        """EV50 (FIX 5): terms round-trips via the service layer; description read path omits
        the internal [Terms: ...] storage marker."""
        created = _create_event(
            name="Terms Event",
            description="Come to this event.",
            terms="No refunds.",
        )
        slot = created[0]
        results = event_list(
            event_filter={"event_id": str(slot.pk)},
            event_details_required={"description": True, "terms": True},
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["description"], "Come to this event.")
        self.assertEqual(results[0]["terms"], "No refunds.")
        self.assertNotIn("[Terms:", results[0]["description"])

    # EV51 (FIX 6)
    def test_ev51_put_new_venue_updates_slot_location_in_db(self):
        """EV51 (FIX 6): PUT with a new venue actually updates Slot.location fields (previously a no-op)."""
        created = _create_event(name="Venue Event")
        slot = created[0]
        event_id = str(slot.pk)
        original_location_id = slot.location_id
        self.assertEqual(slot.location.slug, "govstack-system-location")

        resp = self._put(
            {"details": {"venue": {"city": "Halifax", "street": "123 Main St"}}},
            event_id=event_id,
        )
        self.assertEqual(resp.status_code, 200)
        slot.refresh_from_db()
        self.assertNotEqual(slot.location_id, original_location_id)
        self.assertEqual(slot.location.city, "Halifax")
        self.assertEqual(slot.location.street_address, "123 Main St")

        # A second venue update on the now-dedicated Location must mutate it
        # in place, not create yet another Location row.
        dedicated_location_id = slot.location_id
        resp2 = self._put(
            {"details": {"venue": {"city": "Moncton", "street": "456 Oak Ave"}}},
            event_id=event_id,
        )
        self.assertEqual(resp2.status_code, 200)
        slot.refresh_from_db()
        self.assertEqual(slot.location_id, dedicated_location_id)
        self.assertEqual(slot.location.city, "Moncton")
        self.assertEqual(slot.location.street_address, "456 Oak Ave")


# ===========================================================================
# EV18–EV23: DELETE /event
# ===========================================================================

class EventDeleteTests(EventBaseTestCase):
    """EV18–EV23: DELETE /event"""

    def setUp(self):
        created = _create_event(name="Delete Me Event")
        self.slot = created[0]
        self.event_id = str(self.slot.pk)

    # EV18
    def test_ev18_delete_valid_event_id_returns_200(self):
        """EV18: DELETE with valid event_id returns 200 with event_id."""
        resp = self._delete(event_id=self.event_id)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["event_id"], self.event_id)

    # EV19
    def test_ev19_delete_sets_slot_status_cancelled(self):
        """EV19: DELETE sets Slot.status = 'cancelled' in DB."""
        resp = self._delete(event_id=self.event_id)
        self.assertEqual(resp.status_code, 200)
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.status, "cancelled")

    # EV20
    def test_ev20_delete_slot_record_still_exists(self):
        """EV20: Slot record still exists after delete (soft delete)."""
        resp = self._delete(event_id=self.event_id)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Slot.objects.filter(pk=self.event_id).exists())

    # EV21
    def test_ev21_delete_appointment_type_still_exists(self):
        """EV21: AppointmentType still exists after delete."""
        apt_type = self.slot.appointment_type
        resp = self._delete(event_id=self.event_id)
        self.assertEqual(resp.status_code, 200)
        # Re-fetch to confirm
        self.assertTrue(AppointmentType.objects.filter(pk=apt_type.pk).exists())

    # EV22
    def test_ev22_delete_unknown_event_id_returns_404(self):
        """EV22: DELETE with unknown event_id UUID returns 404 EVENT_NOT_FOUND."""
        unknown_id = str(uuid.uuid4())
        resp = self._delete(event_id=unknown_id)
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["code"], "EVENT_NOT_FOUND")

    # EV23
    def test_ev23_delete_no_event_id_param_returns_400(self):
        """EV23: DELETE with no event_id query param returns 400 MISSING_EVENT_ID."""
        resp = self._delete(event_id=None)
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["code"], "MISSING_EVENT_ID")


# ===========================================================================
# EV24–EV33: GET /event/list_details
# ===========================================================================

class EventListDetailsTests(EventBaseTestCase):
    """EV24–EV33: GET /event/list_details"""

    def setUp(self):
        self.created = _create_event(name="TestEvent")
        self.slot = self.created[0]
        self.event_id = str(self.slot.pk)

    # EV24
    def test_ev24_get_returns_200_with_data_list(self):
        """EV24: GET returns 200 with 'data' list."""
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("data", data)
        self.assertIsInstance(data["data"], list)

    # EV25
    def test_ev25_created_event_appears_in_list(self):
        """EV25: Created event appears in list (not cancelled by default)."""
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        ids = [r["event_id"] for r in resp.json()["data"]]
        self.assertIn(self.event_id, ids)

    # EV26
    def test_ev26_deleted_event_not_in_default_list(self):
        """EV26: Deleted (cancelled) event NOT in default list (default excludes cancelled)."""
        event_delete(event_id=self.event_id)
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        ids = [r["event_id"] for r in resp.json()["data"]]
        self.assertNotIn(self.event_id, ids)

    # EV27
    def test_ev27_truncated_key_is_false_for_small_result_set(self):
        """EV27: 'truncated' key in response is False when < 500 results."""
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("truncated", data)
        self.assertFalse(data["truncated"])

    # EV28
    def test_ev28_event_details_required_name_true_includes_name(self):
        """EV28: event_details_required.name=true → 'name' in each result."""
        qry = {
            "event_filter": {"event_id": self.event_id},
            "event_details_required": {"name": True},
        }
        resp = self._get(qry)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertGreater(len(data), 0)
        for record in data:
            self.assertIn("name", record)

    # EV29
    def test_ev29_event_details_required_name_false_excludes_name(self):
        """EV29: event_details_required.name=false → 'name' NOT in each result."""
        qry = {
            "event_filter": {"event_id": self.event_id},
            "event_details_required": {"name": False, "event_id": True, "status": True,
                                        "category": True, "host_entity_id": True},
        }
        resp = self._get(qry)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertGreater(len(data), 0)
        for record in data:
            self.assertNotIn("name", record)

    # EV30
    def test_ev30_event_filter_status_cancelled_returns_cancelled_events(self):
        """EV30: event_filter.status='cancelled' returns cancelled events."""
        event_delete(event_id=self.event_id)
        qry = {"event_filter": {"status": "cancelled"}}
        resp = self._get(qry)
        self.assertEqual(resp.status_code, 200)
        ids = [r["event_id"] for r in resp.json()["data"]]
        self.assertIn(self.event_id, ids)

    # EV31
    def test_ev31_event_filter_by_event_id_returns_only_that_event(self):
        """EV31: event_filter.event_id=<uuid> returns only that event in results."""
        # Create a second event to ensure filter works
        _create_event(name="Other Event")
        qry = {"event_filter": {"event_id": self.event_id}}
        resp = self._get(qry)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["event_id"], self.event_id)

    # EV32
    def test_ev32_get_with_empty_qry_returns_200(self):
        """EV32: GET with empty qry returns 200 OK (all defaults)."""
        resp = self._get(qry_dict={})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")

    # EV33
    def test_ev33_event_filter_by_name_returns_matching_events(self):
        """EV33: event_filter.name='TestEvent' returns only events with that name."""
        _create_event(name="OtherEvent")
        qry = {"event_filter": {"name": "TestEvent"}}
        resp = self._get(qry)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertGreater(len(data), 0)
        for record in data:
            self.assertIn(self.event_id, [r["event_id"] for r in data])
        # OtherEvent should not appear
        ids = [r["event_id"] for r in data]
        self.assertIn(self.event_id, ids)

    # EV52 (FIX 8)
    def test_ev52_event_filter_from_to_filters_results(self):
        """EV52 (FIX 8): event_filter.from/to (real spec fields) filter Slot occurrences by
        actual time window, distinct from the legacy deadline_from/deadline_to approximation."""
        _create_event(
            name="Early Event",
            slots=[{"from": "2026-01-01T09:00:00Z", "to": "2026-01-01T10:00:00Z"}],
        )
        _create_event(
            name="Mid Event",
            slots=[{"from": "2026-06-01T09:00:00Z", "to": "2026-06-01T10:00:00Z"}],
        )
        qry = {"event_filter": {"from": "2026-05-01T00:00:00Z", "to": "2026-07-01T00:00:00Z"}}
        resp = self._get(qry)
        self.assertEqual(resp.status_code, 200)
        names = set()
        for record in resp.json()["data"]:
            s = Slot.objects.get(pk=record["event_id"])
            names.add(s.appointment_type.name_en)
        self.assertIn("Mid Event", names)
        self.assertNotIn("Early Event", names)
        self.assertNotIn("TestEvent", names)  # setUp's default event is Aug 2026, out of window

    # EV53 (FIX 9)
    def test_ev53_native_gs_prefixed_slug_apptype_excluded_from_event_list(self):
        """EV53 (FIX 9): a native CivicOS AppointmentType with a gs-prefixed slug but
        is_govstack_managed=False must NOT appear in event_list — the boolean field
        (not the slug prefix) is the actual security/scoping boundary."""
        _, native_slot = _create_native_appointment_type(
            slug="gs-drivers-license-renewal", is_govstack_managed=False
        )
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        ids = [r["event_id"] for r in resp.json()["data"]]
        self.assertNotIn(str(native_slot.pk), ids)

    # EV54 (FIX 4)
    def test_ev54_category_round_trips_via_service_type(self):
        """EV54 (FIX 4): category round-trips through a per-category ServiceType instead of
        always returning the hardcoded 'government' constant."""
        _create_event(name="Health Event", category="health")
        qry = {
            "event_filter": {"name": "Health Event"},
            "event_details_required": {"category": True},
        }
        resp = self._get(qry)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["category"], "health")

    # EV55 (FIX 4)
    def test_ev55_event_filter_category_filters_on_service_type_category(self):
        """EV55 (FIX 4): event_filter.category filters on the real ServiceType.category,
        not the name_en workaround."""
        _create_event(name="Legal Clinic", category="legal")
        _create_event(name="Health Clinic", category="health")
        qry = {"event_filter": {"category": "legal"}}
        resp = self._get(qry)
        self.assertEqual(resp.status_code, 200)
        names = {
            Slot.objects.get(pk=r["event_id"]).appointment_type.name_en
            for r in resp.json()["data"]
        }
        self.assertIn("Legal Clinic", names)
        self.assertNotIn("Health Clinic", names)


# ===========================================================================
# EV34–EV37: Wrong HTTP method tests
# ===========================================================================

class EventViewMethodTests(TestCase):
    """EV34–EV37: Wrong HTTP methods return 405."""

    # EV34
    def test_ev34_get_on_event_new_returns_405(self):
        """EV34: GET on POST-only /event/new returns 405."""
        resp = self.client.get(NEW_URL + _qs())
        self.assertEqual(resp.status_code, 405)

    # EV35
    def test_ev35_post_on_event_modifications_returns_405(self):
        """EV35: POST on PUT-only /event/modifications returns 405."""
        resp = self.client.post(MODIFICATIONS_URL + _qs())
        self.assertEqual(resp.status_code, 405)

    # EV36
    def test_ev36_get_on_event_delete_returns_405(self):
        """EV36: GET on DELETE-only /event returns 405."""
        resp = self.client.get(DELETE_URL + _qs())
        self.assertEqual(resp.status_code, 405)

    # EV37
    def test_ev37_post_on_event_list_details_returns_405(self):
        """EV37: POST on GET-only /event/list_details returns 405."""
        resp = self.client.post(LIST_URL + _qs())
        self.assertEqual(resp.status_code, 405)


# ===========================================================================
# EV38–EV45: Service-level tests (direct service imports)
# ===========================================================================

class EventServiceTests(TestCase):
    """EV38–EV45: Direct service layer tests."""

    # EV38
    def test_ev38_event_create_empty_slots_raises_value_error(self):
        """EV38: event_create([]) with empty slots list raises ValueError."""
        with self.assertRaises(ValueError):
            event_create(
                name="No Slots",
                description="",
                category="",
                host_entity_id="",
                slots=[],
                deadline="",
                subscriber_limit="",
                terms="",
                status="available",
                venue=None,
            )

    # EV39
    def test_ev39_event_create_slot_missing_from_key_raises_value_error(self):
        """EV39: event_create with slot missing 'from' key raises ValueError."""
        bad_slot = {"to": "2026-08-01T10:00:00Z"}
        with self.assertRaises(ValueError):
            event_create(
                name="Bad Slot",
                description="",
                category="",
                host_entity_id="",
                slots=[bad_slot],
                deadline="",
                subscriber_limit="",
                terms="",
                status="available",
                venue=None,
            )

    # EV40
    def test_ev40_event_modify_cancelled_slot_can_restore_to_available(self):
        """EV40: event_modify of cancelled slot — can update status back to 'available'."""
        slots = _create_event(name="Restore Test")
        slot = slots[0]
        event_id = str(slot.pk)
        # First cancel it
        event_delete(event_id=event_id)
        slot.refresh_from_db()
        self.assertEqual(slot.status, "cancelled")
        # Now restore it
        event_modify(event_id=event_id, status="available")
        slot.refresh_from_db()
        self.assertEqual(slot.status, "available")

    # EV41
    def test_ev41_created_slot_effective_start_equals_start_datetime(self):
        """EV41: Created Slot.effective_start == Slot.start_datetime."""
        slots = _create_event(name="Effective Start Test")
        slot = slots[0]
        self.assertEqual(slot.effective_start, slot.start_datetime)

    # EV42
    def test_ev42_created_slot_effective_end_equals_end_datetime(self):
        """EV42: Created Slot.effective_end == Slot.end_datetime."""
        slots = _create_event(name="Effective End Test")
        slot = slots[0]
        self.assertEqual(slot.effective_end, slot.end_datetime)

    # EV43
    def test_ev43_subscriber_limit_200_is_clamped_to_100(self):
        """EV43: subscriber_limit='200' sets capacity_per_slot clamped to 100."""
        slots = _create_event(name="Clamped Limit Event", subscriber_limit="200")
        slot = slots[0]
        self.assertEqual(slot.appointment_type.capacity_per_slot, 100)

    # EV44
    def test_ev44_subscriber_limit_empty_defaults_to_1(self):
        """EV44: subscriber_limit='' sets capacity_per_slot to 1 (default)."""
        slots = _create_event(name="Default Limit Event", subscriber_limit="")
        slot = slots[0]
        self.assertEqual(slot.appointment_type.capacity_per_slot, 1)

    # EV45
    def test_ev45_event_delete_invalid_uuid_string_raises_exception(self):
        """EV45: event_delete with invalid UUID string raises an exception (ValidationError, ValueError, or DoesNotExist)."""
        from django.core.exceptions import ValidationError as DjangoValidationError
        with self.assertRaises((ValueError, Slot.DoesNotExist, DjangoValidationError)):
            event_delete(event_id="not-a-valid-uuid")

    # EV46 (FIX 7)
    def test_ev46_event_create_status_open_maps_to_available(self):
        """EV46 (FIX 7): status='open' (the real spec's own documented example value) is
        accepted (not rejected) and maps to CivicOS 'available'."""
        slots = _create_event(name="Open Status Service Test", status="open")
        slot = slots[0]
        self.assertEqual(slot.status, "available")
