"""
GovStack Scheduler BB — Appointment endpoints test suite (Wave E).

Covers 4 endpoints:
  POST   /govstack/scheduler/appointment/new
  PUT    /govstack/scheduler/appointment/modifications
  DELETE /govstack/scheduler/appointment
  GET    /govstack/scheduler/appointment/list_details

Tests are numbered AP1-AP49, mirroring the numbering style used in
test_govstack_event.py.

Serializer note: POST /appointment/new wraps the creation payload as
{"qry": {"appointment_details": {...}}} — NOT {"qry": {"details": {...}}} as
every other */new endpoint uses. This was verified directly against the real
GovStack OpenAPI spec (components.schemas.appointment_new_qry) during Wave E
implementation; see govstack_serializers.py's
_AppointmentQryDetailsSerializer docstring for the full note.
"""
from __future__ import annotations

import json
import uuid
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils.dateparse import parse_datetime
from rest_framework_simplejwt.tokens import RefreshToken

from apps.appointments.models import (
    AppointmentType,
    Booking,
    Location,
    Organization,
    ServiceType,
    Slot,
    StaffProfile,
)
from apps.appointments.services.govstack_appointment import (
    AppointmentOwnershipError,
    appointment_create,
    appointment_delete,
    appointment_list,
    appointment_modify,
)
from apps.appointments.services.govstack_event import event_create
from apps.payments.govstack_models import GovStackRegisteredBB

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEW_URL = "/govstack/scheduler/appointment/new"
MODIFICATIONS_URL = "/govstack/scheduler/appointment/modifications"
LIST_URL = "/govstack/scheduler/appointment/list_details"
DELETE_URL = "/govstack/scheduler/appointment"

_AUTH = {"requestor_id": "test-bb", "request_token": "test-token"}

# Slot datetimes used across tests — far enough in the future to clear any
# reschedule_notice_hours / booking_frequency_days policy windows.
_SLOT_1 = {"from": "2027-03-01T09:00:00Z", "to": "2027-03-01T10:00:00Z"}
_SLOT_2 = {"from": "2027-03-02T09:00:00Z", "to": "2027-03-02T10:00:00Z"}
_SLOT_3 = {"from": "2027-03-03T09:00:00Z", "to": "2027-03-03T10:00:00Z"}


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


def _create_event(name="Test Appt Event", slots=None, status="available", subscriber_limit="", **kwargs):
    """Factory: create a bookable Event (AppointmentType + Slot) via the Event service. Returns list[Slot]."""
    if slots is None:
        slots = [_SLOT_1]
    return event_create(
        name=name,
        description=kwargs.get("description", ""),
        category=kwargs.get("category", ""),
        host_entity_id=kwargs.get("host_entity_id", ""),
        slots=slots,
        deadline=kwargs.get("deadline", ""),
        subscriber_limit=subscriber_limit,
        terms=kwargs.get("terms", ""),
        status=status,
        venue=kwargs.get("venue"),
    )


def _create_citizen(email=None):
    """Factory: create an active, non-staff citizen User for use as participant_id."""
    User = get_user_model()
    email = email or f"citizen-{uuid.uuid4().hex[:10]}@example.com"
    user = User.objects.create(email=email, is_staff=False, is_active=True)
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return user


def _create_org(name="Test Org"):
    """Factory: create an Organization for use as participant_entity_id."""
    slug = f"test-org-{uuid.uuid4().hex[:10]}"
    return Organization.objects.create(
        slug=slug,
        name_en=name,
        name_fr=name,
        organization_type="other",
        is_active=True,
    )


def _create_native_slot(
    mode="in_person",
    capacity=1,
    requires_staff_confirmation=True,
    status="available",
    start="2027-04-01T09:00:00Z",
    end="2027-04-01T10:00:00Z",
):
    """
    Factory: create a native CivicOS AppointmentType + Slot directly
    (bypassing the GovStack Event service), for tests that need to control
    AppointmentType.mode directly (e.g. AP49's "hybrid" mode test).
    """
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        email="ap-native-staff@civicos.internal",
        defaults={"is_staff": True, "is_active": True},
    )
    staff, _ = StaffProfile.objects.get_or_create(
        user=user,
        defaults={
            "display_name_en": "AP Native Staff",
            "display_name_fr": "Personnel Natif AP",
            "is_accepting_bookings": True,
        },
    )
    org, _ = Organization.objects.get_or_create(
        slug="ap-native-org",
        defaults={
            "name_en": "AP Native Org",
            "name_fr": "Org Native AP",
            "organization_type": "other",
            "is_active": True,
        },
    )
    location, _ = Location.objects.get_or_create(
        slug="ap-native-location",
        defaults={
            "organization": org,
            "name_en": "AP Native Location",
            "name_fr": "Emplacement Natif AP",
            "is_virtual": True,
            "timezone": "UTC",
        },
    )
    service_type, _ = ServiceType.objects.get_or_create(
        slug="ap-native-service",
        defaults={
            "name_en": "AP Native Service",
            "name_fr": "Service Natif AP",
            "category": "government",
            "is_active": True,
        },
    )
    appt_type = AppointmentType.objects.create(
        service_type=service_type,
        slug=f"ap-native-{uuid.uuid4().hex[:8]}",
        name_en="Native Appt",
        name_fr="RDV Natif",
        description_en="",
        description_fr="",
        duration_minutes=30,
        capacity_per_slot=capacity,
        mode=mode,
        requires_staff_confirmation=requires_staff_confirmation,
        is_active=True,
        is_govstack_managed=False,
    )
    start_dt = parse_datetime(start)
    end_dt = parse_datetime(end)
    slot = Slot.objects.create(
        appointment_type=appt_type,
        staff=staff,
        location=location,
        start_datetime=start_dt,
        end_datetime=end_dt,
        effective_start=start_dt,
        effective_end=end_dt,
        capacity=capacity,
        spaces_used=0,
        status=status,
    )
    return appt_type, slot


# ===========================================================================
# Base test case
# ===========================================================================

class AppointmentBaseTestCase(TestCase):
    """Shared HTTP helpers for all appointment endpoint tests."""

    def _post(self, qry_dict):
        return self.client.post(NEW_URL + _qry_qs(qry_dict))

    def _put(self, qry_dict, appointment_id=None):
        extra = {"appointment_id": appointment_id} if appointment_id is not None else {}
        return self.client.put(MODIFICATIONS_URL + _qry_qs(qry_dict, **extra))

    def _delete(self, appointment_id=None):
        extra = {"appointment_id": appointment_id} if appointment_id is not None else {}
        return self.client.delete(DELETE_URL + _qs(**extra))

    def _get(self, qry_dict=None):
        if qry_dict is None:
            return self.client.get(LIST_URL + _qs())
        return self.client.get(LIST_URL + _qry_qs(qry_dict))


# ===========================================================================
# AP1-AP15: POST /appointment/new
# ===========================================================================

class AppointmentNewTests(AppointmentBaseTestCase):
    """AP1-AP15: POST /appointment/new"""

    def setUp(self):
        self.citizen = _create_citizen()

    def _valid_qry(self, event_ids, **extra_details):
        details = {
            "event_ids": event_ids,
            "participant_type": "subscriber",
            "participant_id": str(self.citizen.pk),
        }
        details.update(extra_details)
        return {"qry": {"appointment_details": details}}

    # AP1
    def test_ap1_post_new_valid_single_event_id_returns_201(self):
        """AP1: POST with valid participant + 1 event_id returns 201 with appointment_id + appointment_ids."""
        slots = _create_event(slots=[_SLOT_1])
        resp = self._post(self._valid_qry(event_ids=[str(slots[0].pk)]))
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("appointment_id", data)
        self.assertIn("appointment_ids", data)
        self.assertEqual(len(data["appointment_ids"]), 1)
        self.assertEqual(data["appointment_id"], data["appointment_ids"][0])

    # AP2
    def test_ap2_two_event_ids_creates_two_bookings_same_citizen(self):
        """AP2: POST with 2 event_ids creates 2 Booking rows, both linked to the same citizen."""
        slots1 = _create_event(name="E1", slots=[_SLOT_1])
        slots2 = _create_event(name="E2", slots=[_SLOT_2])
        resp = self._post(
            self._valid_qry(event_ids=[str(slots1[0].pk), str(slots2[0].pk)])
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(len(data["appointment_ids"]), 2)
        bookings = Booking.objects.filter(pk__in=data["appointment_ids"])
        self.assertEqual(bookings.count(), 2)
        for booking in bookings:
            self.assertEqual(booking.citizen_id, self.citizen.pk)

    # AP3
    def test_ap3_appointment_id_is_valid_uuid(self):
        """AP3: appointment_id in response is a valid UUID string."""
        slots = _create_event(slots=[_SLOT_1])
        resp = self._post(self._valid_qry(event_ids=[str(slots[0].pk)]))
        self.assertEqual(resp.status_code, 201)
        uuid.UUID(resp.json()["appointment_id"])  # must not raise

    # AP4
    def test_ap4_booking_created_in_db_with_correct_slot_id(self):
        """AP4: Booking created in DB with correct slot_id."""
        slots = _create_event(slots=[_SLOT_1])
        slot = slots[0]
        resp = self._post(self._valid_qry(event_ids=[str(slot.pk)]))
        self.assertEqual(resp.status_code, 201)
        booking = Booking.objects.get(pk=resp.json()["appointment_id"])
        self.assertEqual(booking.slot_id, slot.pk)

    # AP5
    def test_ap5_empty_event_ids_returns_400(self):
        """AP5: empty event_ids array returns 400."""
        details = {
            "event_ids": [],
            "participant_type": "subscriber",
            "participant_id": str(self.citizen.pk),
        }
        resp = self._post({"qry": {"appointment_details": details}})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["status"], "error")

    # AP6
    def test_ap6_unknown_participant_id_returns_404(self):
        """AP6: unknown participant_id returns 404 PARTICIPANT_NOT_FOUND."""
        slots = _create_event(slots=[_SLOT_1])
        details = {
            "event_ids": [str(slots[0].pk)],
            "participant_type": "subscriber",
            "participant_id": "9999999",
        }
        resp = self._post({"qry": {"appointment_details": details}})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "PARTICIPANT_NOT_FOUND")

    # AP7
    def test_ap7_unknown_event_id_returns_404(self):
        """AP7: unknown (but well-formed UUID) event_id returns 404 EVENT_NOT_FOUND."""
        unknown_id = str(uuid.uuid4())
        resp = self._post(self._valid_qry(event_ids=[unknown_id]))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "EVENT_NOT_FOUND")

    # AP8
    def test_ap8_malformed_event_id_returns_404(self):
        """AP8: malformed (non-UUID) event_id returns 404 (DjangoValidationError path)."""
        resp = self._post(self._valid_qry(event_ids=["not-a-valid-uuid"]))
        self.assertEqual(resp.status_code, 404)

    # AP9
    def test_ap9_booking_full_slot_returns_400_slot_full(self):
        """AP9: booking an already-full slot returns 400 SLOT_FULL."""
        slots = _create_event(slots=[_SLOT_1], subscriber_limit="1")
        slot = slots[0]
        citizen_a = _create_citizen()
        appointment_create(
            event_ids=[str(slot.pk)],
            participant_type="subscriber",
            participant_id=str(citizen_a.pk),
        )
        resp = self._post(self._valid_qry(event_ids=[str(slot.pk)]))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "SLOT_FULL")

    # AP10
    def test_ap10_exclusive_true_blocks_slot(self):
        """AP10: exclusive=true results in Slot.status == 'blocked' in DB."""
        slots = _create_event(slots=[_SLOT_1])
        slot = slots[0]
        resp = self._post(self._valid_qry(event_ids=[str(slot.pk)], exclusive=True))
        self.assertEqual(resp.status_code, 201)
        slot.refresh_from_db()
        self.assertEqual(slot.status, "blocked")

    # AP11
    def test_ap11_exclusive_false_default_does_not_block_slot(self):
        """AP11: exclusive=false (default) — Slot.status is NOT 'blocked'."""
        slots = _create_event(slots=[_SLOT_1])
        slot = slots[0]
        resp = self._post(self._valid_qry(event_ids=[str(slot.pk)]))
        self.assertEqual(resp.status_code, 201)
        slot.refresh_from_db()
        self.assertNotEqual(slot.status, "blocked")

    # AP12
    def test_ap12_unsupported_participant_type_returns_400(self):
        """AP12: participant_type='resource' (unsupported) returns 400."""
        slots = _create_event(slots=[_SLOT_1])
        resp = self._post(
            self._valid_qry(event_ids=[str(slots[0].pk)], participant_type="resource")
        )
        self.assertEqual(resp.status_code, 400)

    # AP13
    def test_ap13_participant_entity_id_valid_org_stored_in_db(self):
        """AP13: participant_entity_id pointing at a real Organization is stored on Booking."""
        slots = _create_event(slots=[_SLOT_1])
        org = _create_org()
        resp = self._post(
            self._valid_qry(
                event_ids=[str(slots[0].pk)], participant_entity_id=str(org.pk)
            )
        )
        self.assertEqual(resp.status_code, 201)
        booking = Booking.objects.get(pk=resp.json()["appointment_id"])
        self.assertEqual(booking.govstack_participant_entity_id, str(org.pk))

    # AP14
    def test_ap14_participant_entity_id_unknown_org_returns_400(self):
        """AP14: participant_entity_id pointing at a non-existent Organization pk returns 400."""
        slots = _create_event(slots=[_SLOT_1])
        resp = self._post(
            self._valid_qry(event_ids=[str(slots[0].pk)], participant_entity_id="999999")
        )
        self.assertEqual(resp.status_code, 400)

    # AP15
    def test_ap15_second_event_id_full_rolls_back_first_booking(self):
        """AP15: two event_ids where the SECOND is full — the FIRST booking is also rolled back."""
        slots1 = _create_event(name="Roll1", slots=[_SLOT_1])
        slot1 = slots1[0]
        slots2 = _create_event(name="Roll2", slots=[_SLOT_2], subscriber_limit="1")
        slot2 = slots2[0]
        other_citizen = _create_citizen()
        appointment_create(
            event_ids=[str(slot2.pk)],
            participant_type="subscriber",
            participant_id=str(other_citizen.pk),
        )
        resp = self._post(
            self._valid_qry(event_ids=[str(slot1.pk), str(slot2.pk)])
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Booking.objects.filter(citizen=self.citizen).count(), 0)


# ===========================================================================
# AP16-AP27: PUT /appointment/modifications
# ===========================================================================

class AppointmentModificationsTests(AppointmentBaseTestCase):
    """AP16-AP27: PUT /appointment/modifications"""

    def setUp(self):
        self.citizen = _create_citizen()
        slots = _create_event(name="Mod Event", slots=[_SLOT_1])
        self.slot = slots[0]
        bookings = appointment_create(
            event_ids=[str(self.slot.pk)],
            participant_type="subscriber",
            participant_id=str(self.citizen.pk),
        )
        self.booking = bookings[0]
        self.appointment_id = str(self.booking.pk)

    # AP16
    def test_ap16_confirm_pending_booking(self):
        """AP16: status_id='confirmed' on a pending booking returns 200 and updates status."""
        resp = self._put({"details": {"status_id": "confirmed"}}, appointment_id=self.appointment_id)
        self.assertEqual(resp.status_code, 200)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "confirmed")

    # AP17
    def test_ap17_cancel_booking_decrements_slot_spaces_used(self):
        """AP17: status_id='cancelled' returns 200, status becomes cancelled, slot spaces_used decremented."""
        self.slot.refresh_from_db()
        before = self.slot.spaces_used
        resp = self._put({"details": {"status_id": "cancelled"}}, appointment_id=self.appointment_id)
        self.assertEqual(resp.status_code, 200)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "cancelled")
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.spaces_used, before - 1)

    # AP18
    def test_ap18_reject_pending_booking(self):
        """AP18: status_id='rejected' on a pending booking returns 200 with status rejected."""
        resp = self._put({"details": {"status_id": "rejected"}}, appointment_id=self.appointment_id)
        self.assertEqual(resp.status_code, 200)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "rejected")

    # AP19
    def test_ap19_complete_confirmed_booking(self):
        """AP19: status_id='completed' on a confirmed booking returns 200 with status completed."""
        resp1 = self._put({"details": {"status_id": "confirmed"}}, appointment_id=self.appointment_id)
        self.assertEqual(resp1.status_code, 200)
        resp2 = self._put({"details": {"status_id": "completed"}}, appointment_id=self.appointment_id)
        self.assertEqual(resp2.status_code, 200)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "completed")

    # AP20
    def test_ap20_unsupported_status_id_returns_400(self):
        """AP20: unsupported status_id ('bogus') returns 400."""
        resp = self._put({"details": {"status_id": "bogus"}}, appointment_id=self.appointment_id)
        self.assertEqual(resp.status_code, 400)

    # AP21
    def test_ap21_unknown_appointment_id_returns_404(self):
        """AP21: unknown appointment_id returns 404 APPOINTMENT_NOT_FOUND."""
        unknown_id = str(uuid.uuid4())
        resp = self._put({"details": {"status_id": "confirmed"}}, appointment_id=unknown_id)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "APPOINTMENT_NOT_FOUND")

    # AP22
    def test_ap22_missing_appointment_id_returns_400(self):
        """AP22: missing appointment_id query param returns 400 MISSING_APPOINTMENT_ID."""
        resp = self._put({"details": {"status_id": "confirmed"}}, appointment_id=None)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "MISSING_APPOINTMENT_ID")

    # AP23
    def test_ap23_reschedule_to_new_event_id(self):
        """AP23: event_id targeting a different available slot reschedules — new appointment_id differs,
        old booking cancelled+rescheduled=True, new booking confirmed."""
        self._put({"details": {"status_id": "confirmed"}}, appointment_id=self.appointment_id)
        new_slots = _create_event(name="Reschedule Target", slots=[_SLOT_2])
        new_slot = new_slots[0]
        resp = self._put({"details": {"event_id": str(new_slot.pk)}}, appointment_id=self.appointment_id)
        self.assertEqual(resp.status_code, 200)
        new_appointment_id = resp.json()["appointment_id"]
        self.assertNotEqual(new_appointment_id, self.appointment_id)

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "cancelled")
        self.assertTrue(self.booking.rescheduled)

        new_booking = Booking.objects.get(pk=new_appointment_id)
        self.assertEqual(new_booking.status, "confirmed")
        self.assertEqual(new_booking.slot_id, new_slot.pk)

    # AP24
    def test_ap24_reschedule_carries_forward_exclusive_and_entity_id(self):
        """AP24: reschedule carries forward govstack_exclusive/govstack_participant_entity_id onto the
        new booking, actually re-locks the new slot (FIX 1), and clears the stale flag on the old,
        now-cancelled booking (FIX 3)."""
        self._put({"details": {"status_id": "confirmed"}}, appointment_id=self.appointment_id)
        org = _create_org()
        appointment_modify(
            appointment_id=self.appointment_id,
            exclusive=True,
            participant_entity_id=str(org.pk),
        )
        new_slots = _create_event(name="Reschedule Target 2", slots=[_SLOT_2])
        new_slot = new_slots[0]
        resp = self._put({"details": {"event_id": str(new_slot.pk)}}, appointment_id=self.appointment_id)
        self.assertEqual(resp.status_code, 200)
        new_booking = Booking.objects.get(pk=resp.json()["appointment_id"])
        self.assertTrue(new_booking.govstack_exclusive)
        self.assertEqual(new_booking.govstack_participant_entity_id, str(org.pk))

        # FIX 1: the new slot must actually be locked, not just flagged exclusive=True.
        new_slot.refresh_from_db()
        self.assertEqual(new_slot.status, "blocked")

        # FIX 3: the OLD (now-cancelled) booking's stale exclusive flag is cleared.
        self.booking.refresh_from_db()
        self.assertFalse(self.booking.govstack_exclusive)

    # AP50
    def test_ap50_reschedule_non_exclusive_does_not_block_new_slot(self):
        """AP50: rescheduling a NON-exclusive appointment does not leave the new slot 'blocked'."""
        self._put({"details": {"status_id": "confirmed"}}, appointment_id=self.appointment_id)
        new_slots = _create_event(name="Reschedule Target Non-Exclusive", slots=[_SLOT_2])
        new_slot = new_slots[0]
        resp = self._put({"details": {"event_id": str(new_slot.pk)}}, appointment_id=self.appointment_id)
        self.assertEqual(resp.status_code, 200)
        new_booking = Booking.objects.get(pk=resp.json()["appointment_id"])
        self.assertFalse(new_booking.govstack_exclusive)
        new_slot.refresh_from_db()
        self.assertNotEqual(new_slot.status, "blocked")

    # AP51
    def test_ap51_cancel_and_exclusive_in_same_call_does_not_orphan_blocked_slot(self):
        """AP51: status_id='cancelled' + exclusive=true in ONE call returns 200; the cancellation
        frees the slot and the exclusive lock is silently skipped — no orphaned 'blocked' slot."""
        self._put({"details": {"status_id": "confirmed"}}, appointment_id=self.appointment_id)
        resp = self._put(
            {"details": {"status_id": "cancelled", "exclusive": True}},
            appointment_id=self.appointment_id,
        )
        self.assertEqual(resp.status_code, 200)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "cancelled")
        self.assertTrue(self.booking.govstack_exclusive)
        self.slot.refresh_from_db()
        self.assertNotEqual(self.slot.status, "blocked")

    # AP25
    def test_ap25_exclusive_true_blocks_slot(self):
        """AP25: exclusive=true on an existing confirmed booking returns 200 and blocks the slot."""
        self._put({"details": {"status_id": "confirmed"}}, appointment_id=self.appointment_id)
        resp = self._put({"details": {"exclusive": True}}, appointment_id=self.appointment_id)
        self.assertEqual(resp.status_code, 200)
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.status, "blocked")

    # AP26
    def test_ap26_exclusive_false_unblocks_slot(self):
        """AP26: exclusive=false on a previously-exclusive booking recomputes Slot.status (not 'blocked')."""
        self._put({"details": {"status_id": "confirmed"}}, appointment_id=self.appointment_id)
        self._put({"details": {"exclusive": True}}, appointment_id=self.appointment_id)
        resp = self._put({"details": {"exclusive": False}}, appointment_id=self.appointment_id)
        self.assertEqual(resp.status_code, 200)
        self.slot.refresh_from_db()
        self.assertNotEqual(self.slot.status, "blocked")

    # AP27
    def test_ap27_confirm_already_confirmed_returns_400(self):
        """AP27: confirming an already-confirmed booking returns 400 INVALID_STATUS_TRANSITION."""
        self._put({"details": {"status_id": "confirmed"}}, appointment_id=self.appointment_id)
        resp = self._put({"details": {"status_id": "confirmed"}}, appointment_id=self.appointment_id)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "INVALID_STATUS_TRANSITION")


# ===========================================================================
# AP28-AP32: DELETE /appointment
# ===========================================================================

class AppointmentDeleteTests(AppointmentBaseTestCase):
    """AP28-AP32: DELETE /appointment"""

    def setUp(self):
        self.citizen = _create_citizen()
        slots = _create_event(name="Delete Event", slots=[_SLOT_1])
        self.slot = slots[0]
        bookings = appointment_create(
            event_ids=[str(self.slot.pk)],
            participant_type="subscriber",
            participant_id=str(self.citizen.pk),
        )
        self.booking = bookings[0]
        self.appointment_id = str(self.booking.pk)
        appointment_modify(appointment_id=self.appointment_id, status_id="confirmed")
        self.booking.refresh_from_db()

    # AP28
    def test_ap28_delete_confirmed_booking_returns_200_cancelled(self):
        """AP28: valid appointment_id on a confirmed booking returns 200 and sets status to cancelled."""
        resp = self._delete(appointment_id=self.appointment_id)
        self.assertEqual(resp.status_code, 200)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "cancelled")

    # AP29
    def test_ap29_delete_decrements_slot_spaces_used(self):
        """AP29: Slot.spaces_used decremented after delete."""
        self.slot.refresh_from_db()
        before = self.slot.spaces_used
        resp = self._delete(appointment_id=self.appointment_id)
        self.assertEqual(resp.status_code, 200)
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.spaces_used, before - 1)

    # AP30
    def test_ap30_delete_unknown_appointment_id_returns_404(self):
        """AP30: unknown appointment_id returns 404."""
        resp = self._delete(appointment_id=str(uuid.uuid4()))
        self.assertEqual(resp.status_code, 404)

    # AP31
    def test_ap31_delete_missing_appointment_id_returns_400(self):
        """AP31: missing appointment_id returns 400 MISSING_APPOINTMENT_ID."""
        resp = self._delete(appointment_id=None)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "MISSING_APPOINTMENT_ID")

    # AP32
    def test_ap32_delete_already_cancelled_returns_400(self):
        """AP32: deleting an already-cancelled booking returns 400 INVALID_STATUS_TRANSITION."""
        self._delete(appointment_id=self.appointment_id)
        resp = self._delete(appointment_id=self.appointment_id)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "INVALID_STATUS_TRANSITION")


# ===========================================================================
# AP33-AP42: GET /appointment/list_details
# ===========================================================================

class AppointmentListDetailsTests(AppointmentBaseTestCase):
    """AP33-AP42: GET /appointment/list_details"""

    def setUp(self):
        self.citizen = _create_citizen()
        slots = _create_event(name="List Event", slots=[_SLOT_1])
        self.slot = slots[0]
        bookings = appointment_create(
            event_ids=[str(self.slot.pk)],
            participant_type="subscriber",
            participant_id=str(self.citizen.pk),
        )
        self.booking = bookings[0]
        self.appointment_id = str(self.booking.pk)

    # AP33
    def test_ap33_list_returns_200_with_data_list(self):
        """AP33: returns 200, data is a list, truncated: false."""
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIsInstance(data["data"], list)
        self.assertFalse(data["truncated"])

    # AP34
    def test_ap34_created_appointment_appears_in_list(self):
        """AP34: created appointment appears in the list with correct appointment_id."""
        resp = self._get()
        ids = [r["appointment_id"] for r in resp.json()["data"]]
        self.assertIn(self.appointment_id, ids)

    # AP35
    def test_ap35_participant_id_filters_correctly(self):
        """AP35: appointment_filter.participant_id filters correctly."""
        other_citizen = _create_citizen()
        other_slots = _create_event(name="Other Event", slots=[_SLOT_2])
        appointment_create(
            event_ids=[str(other_slots[0].pk)],
            participant_type="subscriber",
            participant_id=str(other_citizen.pk),
        )
        qry = {"appointment_filter": {"participant_id": str(self.citizen.pk)}}
        resp = self._get(qry)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["appointment_id"], self.appointment_id)

    # AP36
    def test_ap36_status_filters_correctly(self):
        """AP36: appointment_filter.status filters correctly."""
        appointment_modify(appointment_id=self.appointment_id, status_id="confirmed")
        qry = {"appointment_filter": {"status": "confirmed"}}
        resp = self._get(qry)
        data = resp.json()["data"]
        ids = [r["appointment_id"] for r in data]
        self.assertIn(self.appointment_id, ids)
        for record in data:
            self.assertEqual(record["status_id"], "confirmed")

    # AP37
    def test_ap37_appointment_id_filter_returns_exactly_one_match(self):
        """AP37: appointment_filter.appointment_id returns exactly one match."""
        other_slots = _create_event(name="Second Event", slots=[_SLOT_2])
        appointment_create(
            event_ids=[str(other_slots[0].pk)],
            participant_type="subscriber",
            participant_id=str(self.citizen.pk),
        )
        qry = {"appointment_filter": {"appointment_id": self.appointment_id}}
        resp = self._get(qry)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["appointment_id"], self.appointment_id)

    # AP38
    def test_ap38_event_details_true_includes_nested_event_details(self):
        """AP38: appointment_details_required.event_details=true includes event_details with nested event_id/name/status."""
        qry = {
            "appointment_filter": {"appointment_id": self.appointment_id},
            "appointment_details_required": {"event_details": True},
        }
        resp = self._get(qry)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertIn("event_details", data[0])
        self.assertIn("event_id", data[0]["event_details"])
        self.assertIn("name", data[0]["event_details"])
        self.assertIn("status", data[0]["event_details"])

    # AP39
    def test_ap39_exclusive_default_false_excludes_exclusive_key(self):
        """AP39: appointment_details_required.exclusive=false (default) — 'exclusive' key absent from results."""
        qry = {"appointment_filter": {"appointment_id": self.appointment_id}}
        resp = self._get(qry)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertNotIn("exclusive", data[0])

    # AP40
    def test_ap40_participant_type_resource_returns_empty_list(self):
        """AP40: appointment_filter.participant_type='resource' (unsupported) returns empty list, not an error."""
        qry = {"appointment_filter": {"participant_type": "resource"}}
        resp = self._get(qry)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"], [])

    # AP41
    def test_ap41_from_to_filters_by_slot_date_range(self):
        """AP41: appointment_filter.from/to date-range filters correctly."""
        far_slots = _create_event(
            name="Far Event",
            slots=[{"from": "2028-01-01T09:00:00Z", "to": "2028-01-01T10:00:00Z"}],
        )
        far_bookings = appointment_create(
            event_ids=[str(far_slots[0].pk)],
            participant_type="subscriber",
            participant_id=str(self.citizen.pk),
        )
        qry = {
            "appointment_filter": {
                "from": "2026-12-01T00:00:00Z",
                "to": "2027-06-01T00:00:00Z",
            }
        }
        resp = self._get(qry)
        ids = [r["appointment_id"] for r in resp.json()["data"]]
        self.assertIn(self.appointment_id, ids)
        self.assertNotIn(str(far_bookings[0].pk), ids)

    # AP52
    def test_ap52_details_required_status_false_suppresses_status_id(self):
        """AP52 (FIX 5 regression): appointment_details_required={"status": False} suppresses
        'status_id' from the response — the real spec's required-flag key is "status", not
        "status_id"; previously this key was silently dropped by DRF and status_id always appeared."""
        qry = {
            "appointment_filter": {"appointment_id": self.appointment_id},
            "appointment_details_required": {"status": False},
        }
        resp = self._get(qry)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertNotIn("status_id", data[0])

    # AP42
    def test_ap42_exclusive_filter_returns_only_exclusive(self):
        """AP42: appointment_filter.exclusive=true returns only exclusive appointments."""
        appointment_modify(appointment_id=self.appointment_id, exclusive=True)
        other_slots = _create_event(name="Non Exclusive Event", slots=[_SLOT_2])
        other_bookings = appointment_create(
            event_ids=[str(other_slots[0].pk)],
            participant_type="subscriber",
            participant_id=str(self.citizen.pk),
        )
        qry = {"appointment_filter": {"exclusive": True}}
        resp = self._get(qry)
        ids = [r["appointment_id"] for r in resp.json()["data"]]
        self.assertIn(self.appointment_id, ids)
        self.assertNotIn(str(other_bookings[0].pk), ids)


# ===========================================================================
# AP43-AP46: Wrong HTTP method tests
# ===========================================================================

class AppointmentViewMethodTests(TestCase):
    """AP43-AP46: Wrong HTTP methods return 405."""

    # AP43
    def test_ap43_get_on_appointment_new_returns_405(self):
        """AP43: GET on POST-only /appointment/new returns 405."""
        resp = self.client.get(NEW_URL + _qs())
        self.assertEqual(resp.status_code, 405)

    # AP44
    def test_ap44_post_on_appointment_modifications_returns_405(self):
        """AP44: POST on PUT-only /appointment/modifications returns 405."""
        resp = self.client.post(MODIFICATIONS_URL + _qs())
        self.assertEqual(resp.status_code, 405)

    # AP45
    def test_ap45_get_on_appointment_delete_returns_405(self):
        """AP45: GET on DELETE-only /appointment returns 405."""
        resp = self.client.get(DELETE_URL + _qs())
        self.assertEqual(resp.status_code, 405)

    # AP46
    def test_ap46_post_on_appointment_list_details_returns_405(self):
        """AP46: POST on GET-only /appointment/list_details returns 405."""
        resp = self.client.post(LIST_URL + _qs())
        self.assertEqual(resp.status_code, 405)


# ===========================================================================
# AP47-AP49: Service-level tests (direct service imports)
# ===========================================================================

class AppointmentServiceTests(TestCase):
    """AP47-AP49: Direct service layer tests."""

    # AP47
    def test_ap47_appointment_create_empty_event_ids_raises_value_error(self):
        """AP47: appointment_create(event_ids=[]) raises ValueError."""
        with self.assertRaises(ValueError):
            appointment_create(event_ids=[])

    # AP48
    def test_ap48_appointment_create_unsupported_participant_type_raises_value_error(self):
        """AP48: appointment_create(participant_type='bogus', ...) raises ValueError."""
        with self.assertRaises(ValueError):
            appointment_create(
                event_ids=[str(uuid.uuid4())],
                participant_type="bogus",
                participant_id="1",
            )

    # AP49
    def test_ap49_derive_appointment_mode_hybrid_maps_to_in_person(self):
        """AP49: _derive_appointment_mode maps AppointmentType.mode='hybrid' to Booking.appointment_mode='in_person'."""
        citizen = _create_citizen()
        _, slot = _create_native_slot(mode="hybrid")
        bookings = appointment_create(
            event_ids=[str(slot.pk)],
            participant_type="subscriber",
            participant_id=str(citizen.pk),
        )
        self.assertEqual(bookings[0].appointment_mode, "in_person")


# ===========================================================================
# AP53-AP59: Citizen JWT self-service ownership enforcement (IDOR fix)
# ===========================================================================

def _jwt_header(user) -> dict:
    """Build a Django test-client kwargs dict carrying a Bearer JWT for `user`."""
    token = str(RefreshToken.for_user(user).access_token)
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


class AppointmentCitizenOwnershipTests(TestCase):
    """
    AP53-AP59: a citizen JWT-authenticated caller may only act on their OWN
    appointments — GovStackCitizenAuth + GovStackSchedulerRolePermission +
    the service-layer caller_citizen_id enforcement added to
    appointment_create/appointment_delete/appointment_list.

    GOVSTACK_SCHEDULER_REQUIRE_TOKEN is left at its test-settings default
    (False) — the citizen JWT path does not depend on BB whitelist mode.
    """

    def setUp(self):
        self.citizen = _create_citizen()
        self.other_citizen = _create_citizen()
        slots = _create_event(name="Ownership Event", slots=[_SLOT_1])
        self.slot = slots[0]

    # AP53
    def test_ap53_citizen_jwt_create_without_participant_id_defaults_to_self(self):
        """AP53: omitting participant_id on a citizen JWT call books for the caller themselves."""
        details = {"event_ids": [str(self.slot.pk)]}
        qry = {"qry": {"appointment_details": details}}
        resp = self.client.post(
            NEW_URL + _qry_qs(qry), **_jwt_header(self.citizen)
        )
        self.assertEqual(resp.status_code, 201)
        booking = Booking.objects.get(pk=resp.json()["appointment_id"])
        self.assertEqual(booking.citizen_id, self.citizen.pk)

    # AP54
    def test_ap54_citizen_jwt_create_with_own_participant_id_allowed(self):
        """AP54: explicitly setting participant_id to the caller's own pk is allowed."""
        details = {
            "event_ids": [str(self.slot.pk)],
            "participant_type": "subscriber",
            "participant_id": str(self.citizen.pk),
        }
        qry = {"qry": {"appointment_details": details}}
        resp = self.client.post(
            NEW_URL + _qry_qs(qry), **_jwt_header(self.citizen)
        )
        self.assertEqual(resp.status_code, 201)

    # AP55
    def test_ap55_citizen_jwt_create_with_other_participant_id_returns_403(self):
        """AP55: setting participant_id to a DIFFERENT citizen returns 403 and creates no Booking."""
        details = {
            "event_ids": [str(self.slot.pk)],
            "participant_type": "subscriber",
            "participant_id": str(self.other_citizen.pk),
        }
        qry = {"qry": {"appointment_details": details}}
        resp = self.client.post(
            NEW_URL + _qry_qs(qry), **_jwt_header(self.citizen)
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Booking.objects.filter(slot=self.slot).exists())

    # AP56
    def test_ap56_citizen_jwt_delete_own_appointment_allowed(self):
        """AP56: a citizen can cancel their own appointment via a JWT call."""
        bookings = appointment_create(
            event_ids=[str(self.slot.pk)],
            participant_type="subscriber",
            participant_id=str(self.citizen.pk),
        )
        appointment_id = str(bookings[0].pk)
        resp = self.client.delete(
            DELETE_URL + _qs(appointment_id=appointment_id), **_jwt_header(self.citizen)
        )
        self.assertEqual(resp.status_code, 200)
        bookings[0].refresh_from_db()
        self.assertEqual(bookings[0].status, "cancelled")

    # AP57
    def test_ap57_citizen_jwt_delete_other_citizens_appointment_returns_403(self):
        """AP57: a citizen cannot cancel another citizen's appointment — 403, DB state unchanged."""
        bookings = appointment_create(
            event_ids=[str(self.slot.pk)],
            participant_type="subscriber",
            participant_id=str(self.other_citizen.pk),
        )
        appointment_id = str(bookings[0].pk)
        resp = self.client.delete(
            DELETE_URL + _qs(appointment_id=appointment_id), **_jwt_header(self.citizen)
        )
        self.assertEqual(resp.status_code, 403)
        bookings[0].refresh_from_db()
        self.assertNotEqual(bookings[0].status, "cancelled")

    # AP58
    def test_ap58_citizen_jwt_list_only_returns_own_appointments(self):
        """AP58: listing with no participant_id filter still only returns the caller's own appointments."""
        appointment_create(
            event_ids=[str(self.slot.pk)],
            participant_type="subscriber",
            participant_id=str(self.citizen.pk),
        )
        other_slots = _create_event(name="Other Ownership Event", slots=[_SLOT_2])
        appointment_create(
            event_ids=[str(other_slots[0].pk)],
            participant_type="subscriber",
            participant_id=str(self.other_citizen.pk),
        )
        resp = self.client.get(LIST_URL + _qs(), **_jwt_header(self.citizen))
        self.assertEqual(resp.status_code, 200)
        participant_ids = {r["participant_id"] for r in resp.json()["data"]}
        self.assertEqual(participant_ids, {str(self.citizen.pk)})

    # AP59
    def test_ap59_citizen_jwt_list_ignores_other_participant_id_filter(self):
        """AP59: passing someone else's participant_id as a filter does not widen the result set."""
        appointment_create(
            event_ids=[str(self.slot.pk)],
            participant_type="subscriber",
            participant_id=str(self.citizen.pk),
        )
        other_slots = _create_event(name="Other Ownership Event 2", slots=[_SLOT_2])
        appointment_create(
            event_ids=[str(other_slots[0].pk)],
            participant_type="subscriber",
            participant_id=str(self.other_citizen.pk),
        )
        qry = {"appointment_filter": {"participant_id": str(self.other_citizen.pk)}}
        resp = self.client.get(LIST_URL + _qry_qs(qry), **_jwt_header(self.citizen))
        self.assertEqual(resp.status_code, 200)
        participant_ids = {r["participant_id"] for r in resp.json()["data"]}
        self.assertEqual(participant_ids, {str(self.citizen.pk)})

    # AP66 (service-level)
    def test_ap66_service_appointment_create_ownership_mismatch_raises(self):
        """AP66: appointment_create() itself raises AppointmentOwnershipError on a mismatched caller."""
        with self.assertRaises(AppointmentOwnershipError):
            appointment_create(
                event_ids=[str(self.slot.pk)],
                participant_type="subscriber",
                participant_id=str(self.other_citizen.pk),
                caller_citizen_id=self.citizen.pk,
            )


# ===========================================================================
# AP60-AP65: BB-to-BB role gating on the citizen-capable endpoints
# ===========================================================================

@override_settings(GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True)
class AppointmentBBRoleGatingTests(TestCase):
    """
    AP60-AP65: a bare BB-to-BB caller (no citizen JWT) must have a resolved
    role of "organizer" or higher to act on an arbitrary citizen's
    appointment via AppointmentNewView / AppointmentDeleteView /
    AppointmentListDetailsView — the existing staff/case-worker workflow,
    preserved but now gated by GovStackRegisteredBB.role instead of being
    unconditionally allowed.

    request_token in _AUTH is "test-token" — the GovStackRegisteredBB row
    created in each test uses that same bb_id.
    """

    def setUp(self):
        self.citizen = _create_citizen()
        slots = _create_event(name="BB Role Event", slots=[_SLOT_1])
        self.slot = slots[0]

    # AP60
    def test_ap60_organizer_role_bb_can_create_for_arbitrary_participant(self):
        """AP60: role='organizer' BB may create an appointment for any participant_id."""
        GovStackRegisteredBB.objects.create(bb_id="test-token", is_active=True, role="organizer")
        details = {
            "event_ids": [str(self.slot.pk)],
            "participant_type": "subscriber",
            "participant_id": str(self.citizen.pk),
        }
        qry = {"qry": {"appointment_details": details}}
        resp = self.client.post(NEW_URL + _qry_qs(qry))
        self.assertEqual(resp.status_code, 201)

    # AP61
    def test_ap61_organizer_role_bb_can_delete_arbitrary_appointment(self):
        """AP61: role='organizer' BB may cancel any citizen's appointment."""
        GovStackRegisteredBB.objects.create(bb_id="test-token", is_active=True, role="organizer")
        bookings = appointment_create(
            event_ids=[str(self.slot.pk)],
            participant_type="subscriber",
            participant_id=str(self.citizen.pk),
        )
        resp = self.client.delete(DELETE_URL + _qs(appointment_id=str(bookings[0].pk)))
        self.assertEqual(resp.status_code, 200)

    # AP62
    def test_ap62_organizer_role_bb_can_list_arbitrary_participant(self):
        """AP62: role='organizer' BB may list appointments filtered to any participant_id."""
        GovStackRegisteredBB.objects.create(bb_id="test-token", is_active=True, role="organizer")
        appointment_create(
            event_ids=[str(self.slot.pk)],
            participant_type="subscriber",
            participant_id=str(self.citizen.pk),
        )
        qry = {"appointment_filter": {"participant_id": str(self.citizen.pk)}}
        resp = self.client.get(LIST_URL + _qry_qs(qry))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["data"]), 1)

    # AP63
    def test_ap63_resource_role_bb_denied_create(self):
        """AP63: role='resource' (below 'organizer') is denied (403) on create."""
        GovStackRegisteredBB.objects.create(bb_id="test-token", is_active=True, role="resource")
        details = {
            "event_ids": [str(self.slot.pk)],
            "participant_type": "subscriber",
            "participant_id": str(self.citizen.pk),
        }
        qry = {"qry": {"appointment_details": details}}
        resp = self.client.post(NEW_URL + _qry_qs(qry))
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Booking.objects.filter(slot=self.slot).exists())

    # AP64
    def test_ap64_resource_role_bb_denied_delete(self):
        """AP64: role='resource' is denied (403) on delete; the booking is left untouched."""
        GovStackRegisteredBB.objects.create(bb_id="test-token", is_active=True, role="resource")
        bookings = appointment_create(
            event_ids=[str(self.slot.pk)],
            participant_type="subscriber",
            participant_id=str(self.citizen.pk),
        )
        resp = self.client.delete(DELETE_URL + _qs(appointment_id=str(bookings[0].pk)))
        self.assertEqual(resp.status_code, 403)
        bookings[0].refresh_from_db()
        self.assertNotEqual(bookings[0].status, "cancelled")

    # AP65
    def test_ap65_resource_role_bb_denied_list(self):
        """AP65: role='resource' is denied (403) on list_details."""
        GovStackRegisteredBB.objects.create(bb_id="test-token", is_active=True, role="resource")
        resp = self.client.get(LIST_URL + _qs())
        self.assertEqual(resp.status_code, 403)
