"""
GovStack Scheduler BB — AlertSchedule endpoints test suite (Wave F).

Covers 4 endpoints:
  POST   /govstack/scheduler/alert_schedule/new
  PUT    /govstack/scheduler/alert_schedule/modifications
  DELETE /govstack/scheduler/alert_schedule
  GET    /govstack/scheduler/alert_schedule/list_details

Plus dedicated coverage for:
  - apps.appointments.tasks._is_safe_outbound_url (the SSRF-safe URL validator)
  - apps.appointments.tasks.dispatch_alert_schedule (the Celery dispatch task)

Test numbering: AS1-ASxx for the endpoint suite, SSRF1-SSRFxx for the URL
validator, DISP1-DISPxx for the dispatch task — following the EV/AP
numbering convention used in test_govstack_event.py / test_govstack_appointment.py.

Celery isolation: every test that exercises the view layer's on_commit()
enqueue/reschedule path mocks dispatch_alert_schedule.apply_async and
AsyncResult.revoke — no test in this module ever hits a real broker. Tests
that need on_commit() callbacks to actually fire (Django's TestCase rolls
back its enclosing transaction, so on_commit callbacks registered during
the request are otherwise silently dropped) wrap the request in
self.captureOnCommitCallbacks(execute=True) — the established convention
already used across apps/payments/tests/test_donation_views.py etc.
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta
from unittest import mock
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.appointments.models import (
    AppointmentType,
    Booking,
    GovStackAlertSchedule,
    GovStackSubscriberProfile,
    Location,
    Organization,
    Resource,
    ServiceType,
    Slot,
    StaffProfile,
)
from apps.appointments.services.govstack_alert_schedule import alert_schedule_create
from apps.appointments.services.govstack_event import event_create
from apps.appointments.services.govstack_message import message_create
from apps.appointments.tasks import _is_safe_outbound_url, dispatch_alert_schedule
from apps.payments.govstack_models import GovStackRegisteredBB

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEW_URL = "/govstack/scheduler/alert_schedule/new"
MODIFICATIONS_URL = "/govstack/scheduler/alert_schedule/modifications"
LIST_URL = "/govstack/scheduler/alert_schedule/list_details"
DELETE_URL = "/govstack/scheduler/alert_schedule"

_AUTH = {"requestor_id": "test-bb", "request_token": "test-token"}

_FUTURE_DT = "2027-06-01T09:00:00Z"
_FUTURE_DT_2 = "2027-06-02T09:00:00Z"
_PAST_DT = "2020-01-01T09:00:00Z"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _qs(**extra):
    params = {**_AUTH, **extra}
    return "?" + urlencode(params)


def _qry_qs(qry_dict, **extra):
    params = {**_AUTH, "qry": json.dumps(qry_dict), **extra}
    return "?" + urlencode(params)


def _create_org(name="Test Org"):
    slug = f"test-org-{uuid.uuid4().hex[:10]}"
    return Organization.objects.create(
        slug=slug, name_en=name, name_fr=name, organization_type="other", is_active=True,
    )


def _create_message(entity_id=None, category="reminder", message_body="Hello"):
    if entity_id is None:
        entity_id = _create_org().pk
    return message_create(entity_id=entity_id, category=category, message_body=message_body)


def _create_event_slot(host_entity_id="", **kwargs):
    """
    Factory: create a bookable Event (AppointmentType + Slot) via the Event
    service. When host_entity_id is supplied, a non-empty venue dict must
    also be passed — services.govstack_event._resolve_location() only
    honours host_entity_id when the venue has at least one non-blank field;
    otherwise it silently falls back to the shared GovStack-system location
    (and thus the shared GovStack-system org), ignoring host_entity_id
    entirely. See _resolve_location's docstring in services/govstack_event.py.
    """
    venue = {"city": "Testville"} if host_entity_id else None
    slots = event_create(
        name=kwargs.get("name", "Test Event"),
        host_entity_id=host_entity_id,
        slots=[kwargs.get("slot", {"from": "2027-05-01T09:00:00Z", "to": "2027-05-01T10:00:00Z"})],
        venue=venue,
    )
    return slots[0]


def _create_full_slot(
    gs_alert_preference="",
    gs_alert_url="",
    resource_alert_preference="",
    resource_alert_url="",
    with_resource=False,
    org=None,
):
    """
    Factory: create a native AppointmentType + Slot with full control over
    StaffProfile.gs_alert_* and (optionally) an attached Resource with its
    own alert_* fields — needed for dispatch_alert_schedule tests, which
    event_create()'s shared GovStack-system staff cannot provide.
    """
    User = get_user_model()
    unique = uuid.uuid4().hex[:10]
    user = User.objects.create(email=f"as-staff-{unique}@civicos.internal", is_staff=True, is_active=True)
    staff = StaffProfile.objects.create(
        user=user,
        display_name_en="AS Staff",
        display_name_fr="Personnel AS",
        is_accepting_bookings=True,
        gs_alert_preference=gs_alert_preference,
        gs_alert_url=gs_alert_url,
    )
    org = org or _create_org()
    location = Location.objects.create(
        organization=org,
        slug=f"as-location-{unique}",
        name_en="AS Location",
        name_fr="Emplacement AS",
        is_virtual=True,
        timezone="UTC",
    )
    service_type, _ = ServiceType.objects.get_or_create(
        slug="as-native-service",
        defaults={
            "name_en": "AS Native Service", "name_fr": "Service Natif AS",
            "category": "government", "is_active": True,
        },
    )
    appt_type = AppointmentType.objects.create(
        service_type=service_type,
        slug=f"as-native-{unique}",
        name_en="AS Native Appt",
        name_fr="RDV Natif AS",
        duration_minutes=30,
        capacity_per_slot=5,
        mode="in_person",
        is_active=True,
        is_govstack_managed=False,
    )
    resource = None
    if with_resource:
        resource = Resource.objects.create(
            location=location,
            name_en="AS Resource",
            name_fr="Ressource AS",
            resource_type="room",
            capacity=5,
            alert_preference=resource_alert_preference,
            alert_url=resource_alert_url,
        )
    start_dt = parse_datetime("2027-05-10T09:00:00Z")
    end_dt = parse_datetime("2027-05-10T10:00:00Z")
    slot = Slot.objects.create(
        appointment_type=appt_type,
        staff=staff,
        location=location,
        resource=resource,
        start_datetime=start_dt,
        end_datetime=end_dt,
        effective_start=start_dt,
        effective_end=end_dt,
        capacity=5,
        spaces_used=0,
        status="available",
    )
    return slot, staff, resource, org


def _create_citizen(email=None):
    User = get_user_model()
    email = email or f"as-citizen-{uuid.uuid4().hex[:10]}@example.com"
    user = User.objects.create(email=email, is_staff=False, is_active=True)
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return user


def _create_subscriber_profile(user, alert_preference="push", alert_url="https://example.com/alert"):
    return GovStackSubscriberProfile.objects.create(
        user=user, alert_preference=alert_preference, alert_url=alert_url,
    )


def _create_booking(slot, citizen, status=Booking.STATUS_CONFIRMED):
    return Booking.objects.create(slot=slot, citizen=citizen, status=status)


# ===========================================================================
# Base test case
# ===========================================================================

class AlertScheduleBaseTestCase(TestCase):
    """Shared HTTP helpers for all alert_schedule endpoint tests."""

    def _post(self, qry_dict):
        return self.client.post(NEW_URL + _qry_qs(qry_dict))

    def _put(self, qry_dict, alert_schedule_id=None):
        extra = {"alert_schedule_id": alert_schedule_id} if alert_schedule_id is not None else {}
        return self.client.put(MODIFICATIONS_URL + _qry_qs(qry_dict, **extra))

    def _delete(self, alert_schedule_id=None):
        extra = {"alert_schedule_id": alert_schedule_id} if alert_schedule_id is not None else {}
        return self.client.delete(DELETE_URL + _qs(**extra))

    def _get(self, qry_dict=None):
        if qry_dict is None:
            return self.client.get(LIST_URL + _qs())
        return self.client.get(LIST_URL + _qry_qs(qry_dict))


# ===========================================================================
# AS1-AS10: POST /alert_schedule/new
# ===========================================================================

@mock.patch("apps.appointments.govstack_views.dispatch_alert_schedule.apply_async")
class AlertScheduleNewTests(AlertScheduleBaseTestCase):
    """AS1-AS10: POST /alert_schedule/new"""

    def test_as1_happy_path_creates_alert_schedule_and_enqueues_task(self, mock_apply_async):
        mock_apply_async.return_value = mock.Mock(id="celery-task-id-1")
        slot = _create_event_slot()
        msg = _create_message()
        qry = {"qry": {"details": {
            "event_id": str(slot.pk), "message_id": str(msg.pk),
            "target_category": "subscriber", "alert_datetime": _FUTURE_DT,
        }}}
        with self.captureOnCommitCallbacks(execute=True):
            resp = self._post(qry)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        alert_schedule = GovStackAlertSchedule.objects.get(pk=data["alert_schedule_id"])
        self.assertEqual(alert_schedule.slot_id, slot.pk)
        self.assertEqual(alert_schedule.message_id, msg.pk)
        self.assertEqual(alert_schedule.target_category, "subscriber")
        mock_apply_async.assert_called_once()
        _, call_kwargs = mock_apply_async.call_args
        self.assertEqual(call_kwargs["args"], [str(alert_schedule.pk)])
        self.assertEqual(call_kwargs["eta"], alert_schedule.alert_datetime)
        alert_schedule.refresh_from_db()
        self.assertEqual(alert_schedule.celery_task_id, "celery-task-id-1")

    def test_as2_missing_event_id_returns_404(self, mock_apply_async):
        msg = _create_message()
        qry = {"qry": {"details": {"message_id": str(msg.pk), "alert_datetime": _FUTURE_DT}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "EVENT_NOT_FOUND")

    def test_as3_nonexistent_event_id_returns_404(self, mock_apply_async):
        msg = _create_message()
        qry = {"qry": {"details": {
            "event_id": str(uuid.uuid4()), "message_id": str(msg.pk), "alert_datetime": _FUTURE_DT,
        }}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "EVENT_NOT_FOUND")

    def test_as4_malformed_event_id_returns_404(self, mock_apply_async):
        msg = _create_message()
        qry = {"qry": {"details": {
            "event_id": "not-a-uuid", "message_id": str(msg.pk), "alert_datetime": _FUTURE_DT,
        }}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "EVENT_NOT_FOUND")

    def test_as5_missing_message_id_returns_404(self, mock_apply_async):
        slot = _create_event_slot()
        qry = {"qry": {"details": {"event_id": str(slot.pk), "alert_datetime": _FUTURE_DT}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "MESSAGE_NOT_FOUND")

    def test_as6_nonexistent_message_id_returns_404(self, mock_apply_async):
        slot = _create_event_slot()
        qry = {"qry": {"details": {
            "event_id": str(slot.pk), "message_id": "999999", "alert_datetime": _FUTURE_DT,
        }}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "MESSAGE_NOT_FOUND")

    def test_as7_invalid_target_category_returns_400(self, mock_apply_async):
        slot = _create_event_slot()
        msg = _create_message()
        qry = {"qry": {"details": {
            "event_id": str(slot.pk), "message_id": str(msg.pk),
            "target_category": "not-a-real-category", "alert_datetime": _FUTURE_DT,
        }}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "ALERT_SCHEDULE_CREATE_FAILED")

    def test_as8_past_alert_datetime_returns_400(self, mock_apply_async):
        slot = _create_event_slot()
        msg = _create_message()
        qry = {"qry": {"details": {
            "event_id": str(slot.pk), "message_id": str(msg.pk), "alert_datetime": _PAST_DT,
        }}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "ALERT_SCHEDULE_CREATE_FAILED")

    def test_as9_missing_alert_datetime_returns_400(self, mock_apply_async):
        slot = _create_event_slot()
        msg = _create_message()
        qry = {"qry": {"details": {"event_id": str(slot.pk), "message_id": str(msg.pk)}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)

    def test_as10_naive_alert_datetime_returns_400(self, mock_apply_async):
        slot = _create_event_slot()
        msg = _create_message()
        qry = {"qry": {"details": {
            "event_id": str(slot.pk), "message_id": str(msg.pk),
            "alert_datetime": "2027-06-01T09:00:00",  # no tz offset
        }}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)


# ===========================================================================
# AS11-AS20: PUT /alert_schedule/modifications
# ===========================================================================

@mock.patch("apps.appointments.govstack_views.dispatch_alert_schedule.apply_async")
@mock.patch("celery.result.AsyncResult.revoke")
class AlertScheduleModificationsTests(AlertScheduleBaseTestCase):
    """AS11-AS20: PUT /alert_schedule/modifications"""

    def _create_alert_schedule(self, alert_datetime=_FUTURE_DT, target_category=""):
        slot = _create_event_slot()
        msg = _create_message()
        return alert_schedule_create(
            event_id=str(slot.pk), message_id=str(msg.pk),
            target_category=target_category, alert_datetime=alert_datetime,
        )

    def test_as11_happy_path_updates_target_category_no_reschedule(self, mock_revoke, mock_apply_async):
        alert_schedule = self._create_alert_schedule()
        with self.captureOnCommitCallbacks(execute=True):
            resp = self._put(
                {"details": {"target_category": "resource"}}, alert_schedule_id=str(alert_schedule.pk)
            )
        self.assertEqual(resp.status_code, 200)
        alert_schedule.refresh_from_db()
        self.assertEqual(alert_schedule.target_category, "resource")
        mock_apply_async.assert_not_called()
        mock_revoke.assert_not_called()

    def test_as12_alert_datetime_change_reschedules(self, mock_revoke, mock_apply_async):
        mock_apply_async.return_value = mock.Mock(id="new-task-id")
        alert_schedule = self._create_alert_schedule()
        alert_schedule.celery_task_id = "old-task-id"
        alert_schedule.save(update_fields=["celery_task_id"])
        with self.captureOnCommitCallbacks(execute=True):
            resp = self._put(
                {"details": {"alert_datetime": _FUTURE_DT_2}}, alert_schedule_id=str(alert_schedule.pk)
            )
        self.assertEqual(resp.status_code, 200)
        mock_revoke.assert_called_once()
        mock_apply_async.assert_called_once()
        alert_schedule.refresh_from_db()
        self.assertEqual(alert_schedule.celery_task_id, "new-task-id")
        self.assertEqual(alert_schedule.alert_datetime, parse_datetime(_FUTURE_DT_2))

    def test_as13_message_id_change_reschedules(self, mock_revoke, mock_apply_async):
        mock_apply_async.return_value = mock.Mock(id="new-task-id-2")
        alert_schedule = self._create_alert_schedule()
        new_msg = _create_message()
        with self.captureOnCommitCallbacks(execute=True):
            resp = self._put(
                {"details": {"message_id": str(new_msg.pk)}}, alert_schedule_id=str(alert_schedule.pk)
            )
        self.assertEqual(resp.status_code, 200)
        mock_apply_async.assert_called_once()
        alert_schedule.refresh_from_db()
        self.assertEqual(alert_schedule.message_id, new_msg.pk)

    def test_as14_missing_alert_schedule_id_returns_400(self, mock_revoke, mock_apply_async):
        resp = self.client.put(MODIFICATIONS_URL + _qry_qs({"details": {}}))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "MISSING_ALERT_SCHEDULE_ID")

    def test_as15_nonexistent_alert_schedule_id_returns_404(self, mock_revoke, mock_apply_async):
        resp = self._put({"details": {"target_category": "resource"}}, alert_schedule_id="999999")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "ALERT_SCHEDULE_NOT_FOUND")

    def test_as16_invalid_target_category_returns_400(self, mock_revoke, mock_apply_async):
        alert_schedule = self._create_alert_schedule()
        resp = self._put(
            {"details": {"target_category": "bogus"}}, alert_schedule_id=str(alert_schedule.pk)
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "ALERT_SCHEDULE_MODIFY_FAILED")

    def test_as17_past_alert_datetime_returns_400(self, mock_revoke, mock_apply_async):
        alert_schedule = self._create_alert_schedule()
        resp = self._put(
            {"details": {"alert_datetime": _PAST_DT}}, alert_schedule_id=str(alert_schedule.pk)
        )
        self.assertEqual(resp.status_code, 400)

    def test_as18_nonexistent_message_id_returns_404(self, mock_revoke, mock_apply_async):
        alert_schedule = self._create_alert_schedule()
        resp = self._put(
            {"details": {"message_id": "999999"}}, alert_schedule_id=str(alert_schedule.pk)
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "MESSAGE_NOT_FOUND")

    def test_as19_nonexistent_event_id_returns_404(self, mock_revoke, mock_apply_async):
        alert_schedule = self._create_alert_schedule()
        resp = self._put(
            {"details": {"event_id": str(uuid.uuid4())}}, alert_schedule_id=str(alert_schedule.pk)
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "EVENT_NOT_FOUND")

    def test_as20_rearm_already_dispatched_row_on_future_datetime_change(self, mock_revoke, mock_apply_async):
        """AS20: dispatched=True + alert_datetime changed to a new future value → re-armed."""
        mock_apply_async.return_value = mock.Mock(id="rearm-task-id")
        alert_schedule = self._create_alert_schedule()
        alert_schedule.dispatched = True
        alert_schedule.save(update_fields=["dispatched"])
        with self.captureOnCommitCallbacks(execute=True):
            resp = self._put(
                {"details": {"alert_datetime": _FUTURE_DT_2}}, alert_schedule_id=str(alert_schedule.pk)
            )
        self.assertEqual(resp.status_code, 200)
        alert_schedule.refresh_from_db()
        self.assertFalse(alert_schedule.dispatched)
        mock_apply_async.assert_called_once()


# ===========================================================================
# AS21-AS24: DELETE /alert_schedule
# ===========================================================================

class AlertScheduleDeleteTests(AlertScheduleBaseTestCase):
    """AS21-AS24: DELETE /alert_schedule"""

    @mock.patch("celery.current_app.control.revoke")
    def test_as21_happy_path_deletes_and_revokes(self, mock_revoke):
        slot = _create_event_slot()
        msg = _create_message()
        alert_schedule = alert_schedule_create(
            event_id=str(slot.pk), message_id=str(msg.pk), alert_datetime=_FUTURE_DT,
        )
        alert_schedule.celery_task_id = "task-to-revoke"
        alert_schedule.save(update_fields=["celery_task_id"])
        resp = self._delete(alert_schedule_id=str(alert_schedule.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(GovStackAlertSchedule.objects.filter(pk=alert_schedule.pk).exists())
        mock_revoke.assert_called_once()

    def test_as22_missing_alert_schedule_id_returns_400(self):
        resp = self._delete()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "MISSING_ALERT_SCHEDULE_ID")

    def test_as23_nonexistent_alert_schedule_id_returns_404(self):
        resp = self._delete(alert_schedule_id="999999")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "ALERT_SCHEDULE_NOT_FOUND")

    def test_as24_malformed_alert_schedule_id_returns_400(self):
        resp = self._delete(alert_schedule_id="not-a-number")
        self.assertEqual(resp.status_code, 400)


# ===========================================================================
# AS25-AS30: GET /alert_schedule/list_details
# ===========================================================================

class AlertScheduleListDetailsTests(AlertScheduleBaseTestCase):
    """AS25-AS30: GET /alert_schedule/list_details"""

    def _create_alert_schedule(self, entity_id=None, **kwargs):
        org = _create_org() if entity_id is None else Organization.objects.get(pk=entity_id)
        slot = _create_event_slot(host_entity_id=str(org.pk))
        msg = _create_message()
        alert_datetime = kwargs.pop("alert_datetime", _FUTURE_DT)
        return alert_schedule_create(
            event_id=str(slot.pk), message_id=str(msg.pk), alert_datetime=alert_datetime, **kwargs
        ), org

    def test_as25_happy_path_no_filter_returns_all(self):
        self._create_alert_schedule()
        self._create_alert_schedule()
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(len(data["data"]), 2)

    def test_as26_filter_by_alert_schedule_id(self):
        alert_schedule, _ = self._create_alert_schedule()
        self._create_alert_schedule()
        resp = self._get({"alert_schedule_filter": {"alert_schedule_id": str(alert_schedule.pk)}})
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["alert_schedule_id"], str(alert_schedule.pk))

    def test_as27_filter_by_entity_id_derived_field(self):
        alert_schedule, org = self._create_alert_schedule()
        self._create_alert_schedule()
        resp = self._get({"alert_schedule_filter": {"entity_id": str(org.pk)}})
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["details"]["entity_id"], str(org.pk))

    def test_as28_filter_by_from_to_datetime_range(self):
        alert_schedule, _ = self._create_alert_schedule(alert_datetime=_FUTURE_DT)
        resp = self._get({"alert_schedule_filter": {"from": "2027-05-01T00:00:00Z", "to": "2027-06-15T00:00:00Z"}})
        data = resp.json()["data"]
        ids = [item["alert_schedule_id"] for item in data]
        self.assertIn(str(alert_schedule.pk), ids)

    def test_as29_response_shape_and_target_category_always_present(self):
        alert_schedule, org = self._create_alert_schedule(target_category="subscriber")
        resp = self._get({"alert_schedule_filter": {"alert_schedule_id": str(alert_schedule.pk)}})
        item = resp.json()["data"][0]
        self.assertIn("alert_schedule_id", item)
        self.assertIn("details", item)
        self.assertEqual(item["details"]["entity_id"], str(org.pk))
        self.assertEqual(item["details"]["message_id"], str(alert_schedule.message_id))
        self.assertIn("alert_datetime", item["details"])
        self.assertEqual(item["details"]["target_category"], "subscriber")

    def test_as30_malformed_from_returns_400(self):
        resp = self._get({"alert_schedule_filter": {"from": "not-a-date"}})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "ALERT_SCHEDULE_LIST_FILTER_INVALID")


# ===========================================================================
# AS31-AS33: Auth / role enforcement
# ===========================================================================

@override_settings(GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True)
class AlertScheduleRoleEnforcementTests(AlertScheduleBaseTestCase):
    """AS31-AS33: alert_schedule endpoints require gs_actor_role="organizer" or higher."""

    def test_as31_resource_role_denied_on_alert_schedule_new(self):
        GovStackRegisteredBB.objects.create(bb_id="test-token", is_active=True, role="resource")
        slot = _create_event_slot()
        msg = _create_message()
        qry = {"qry": {"details": {
            "event_id": str(slot.pk), "message_id": str(msg.pk), "alert_datetime": _FUTURE_DT,
        }}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 403)

    @mock.patch("apps.appointments.govstack_views.dispatch_alert_schedule.apply_async")
    def test_as32_admin_role_allowed_on_alert_schedule_new(self, mock_apply_async):
        mock_apply_async.return_value = mock.Mock(id="tid")
        GovStackRegisteredBB.objects.create(bb_id="test-token", is_active=True, role="admin")
        slot = _create_event_slot()
        msg = _create_message()
        qry = {"qry": {"details": {
            "event_id": str(slot.pk), "message_id": str(msg.pk), "alert_datetime": _FUTURE_DT,
        }}}
        with self.captureOnCommitCallbacks(execute=True):
            resp = self._post(qry)
        self.assertEqual(resp.status_code, 201)

    def test_as33_organizer_role_allowed_on_alert_schedule_list(self):
        GovStackRegisteredBB.objects.create(bb_id="test-token", is_active=True, role="organizer")
        resp = self._get()
        self.assertEqual(resp.status_code, 200)


# ===========================================================================
# SSRF1-SSRF7: _is_safe_outbound_url
# ===========================================================================

class SafeOutboundUrlTests(TestCase):
    """SSRF1-SSRF7: apps.appointments.tasks._is_safe_outbound_url"""

    @mock.patch("apps.appointments.tasks.socket.getaddrinfo")
    def test_ssrf1_public_https_url_is_safe(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
        self.assertTrue(_is_safe_outbound_url("https://example.com/alert"))

    def test_ssrf2_http_scheme_rejected(self):
        self.assertFalse(_is_safe_outbound_url("http://example.com/alert"))

    @mock.patch("apps.appointments.tasks.socket.getaddrinfo")
    def test_ssrf3_loopback_ip_rejected(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("127.0.0.1", 0))]
        self.assertFalse(_is_safe_outbound_url("https://localhost/alert"))

    @mock.patch("apps.appointments.tasks.socket.getaddrinfo")
    def test_ssrf4_private_10_range_rejected(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("10.0.0.5", 0))]
        self.assertFalse(_is_safe_outbound_url("https://internal.example.com/alert"))

    @mock.patch("apps.appointments.tasks.socket.getaddrinfo")
    def test_ssrf5_private_192_168_range_rejected(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("192.168.1.1", 0))]
        self.assertFalse(_is_safe_outbound_url("https://router.example.com/alert"))

    @mock.patch("apps.appointments.tasks.socket.getaddrinfo")
    def test_ssrf6_link_local_169_254_range_rejected(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("169.254.169.254", 0))]
        self.assertFalse(_is_safe_outbound_url("https://metadata.example.com/alert"))

    @mock.patch("apps.appointments.tasks.socket.getaddrinfo")
    def test_ssrf7_dns_resolution_failure_rejected(self, mock_getaddrinfo):
        mock_getaddrinfo.side_effect = OSError("Name or service not known")
        self.assertFalse(_is_safe_outbound_url("https://does-not-resolve.invalid/alert"))

    def test_ssrf8_url_with_no_hostname_rejected(self):
        self.assertFalse(_is_safe_outbound_url("https:///no-host-here"))

    def test_ssrf9_blank_url_rejected(self):
        self.assertFalse(_is_safe_outbound_url(""))

    @mock.patch("apps.appointments.tasks.socket.getaddrinfo")
    def test_ssrf10_one_unsafe_ip_among_several_resolved_rejects_whole_url(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (2, 1, 6, "", ("93.184.216.34", 0)),
            (2, 1, 6, "", ("10.0.0.1", 0)),
        ]
        self.assertFalse(_is_safe_outbound_url("https://multi-homed.example.com/alert"))


# ===========================================================================
# DISP1-DISP8: dispatch_alert_schedule task
# ===========================================================================

class DispatchAlertScheduleTaskTests(TestCase):
    """DISP1-DISP8: apps.appointments.tasks.dispatch_alert_schedule"""

    def _make_alert_schedule(self, target_category="", **slot_kwargs):
        slot, staff, resource, org = _create_full_slot(**slot_kwargs)
        msg = _create_message(entity_id=org.pk, category="reminder", message_body="Your appt is soon.")
        alert_schedule = GovStackAlertSchedule.objects.create(
            slot=slot, message=msg, target_category=target_category,
            alert_datetime=timezone.now() + timedelta(hours=1),
        )
        return alert_schedule, slot, staff, resource

    @mock.patch("apps.appointments.tasks._is_safe_outbound_url", return_value=True)
    @mock.patch("apps.appointments.tasks.requests.post")
    def test_disp1_delivers_to_subscriber_with_push_preference(self, mock_post, mock_safe):
        mock_post.return_value = mock.Mock(status_code=200, raise_for_status=mock.Mock())
        alert_schedule, slot, staff, resource = self._make_alert_schedule(target_category="subscriber")
        citizen = _create_citizen()
        _create_subscriber_profile(citizen, alert_preference="push", alert_url="https://citizen.example.com/hook")
        _create_booking(slot, citizen)

        result = dispatch_alert_schedule(str(alert_schedule.pk))

        mock_post.assert_called_once()
        call_args, call_kwargs = mock_post.call_args
        self.assertEqual(call_args[0], "https://citizen.example.com/hook")
        self.assertEqual(call_kwargs["json"]["alert_schedule_id"], str(alert_schedule.pk))
        self.assertEqual(call_kwargs["json"]["message_body"], "Your appt is soon.")
        self.assertEqual(result["attempted"], 1)
        alert_schedule.refresh_from_db()
        self.assertTrue(alert_schedule.dispatched)

    @mock.patch("apps.appointments.tasks.requests.post")
    def test_disp2_skips_subscriber_with_non_push_preference(self, mock_post):
        alert_schedule, slot, staff, resource = self._make_alert_schedule(target_category="subscriber")
        citizen = _create_citizen()
        _create_subscriber_profile(citizen, alert_preference="poll", alert_url="https://citizen.example.com/hook")
        _create_booking(slot, citizen)

        dispatch_alert_schedule(str(alert_schedule.pk))

        mock_post.assert_not_called()
        alert_schedule.refresh_from_db()
        self.assertTrue(alert_schedule.dispatched)

    @mock.patch("apps.appointments.tasks.requests.post")
    def test_disp3_skips_cancelled_booking(self, mock_post):
        alert_schedule, slot, staff, resource = self._make_alert_schedule(target_category="subscriber")
        citizen = _create_citizen()
        _create_subscriber_profile(citizen, alert_preference="push", alert_url="https://citizen.example.com/hook")
        _create_booking(slot, citizen, status=Booking.STATUS_CANCELLED)

        dispatch_alert_schedule(str(alert_schedule.pk))

        mock_post.assert_not_called()

    @mock.patch("apps.appointments.tasks._is_safe_outbound_url", return_value=True)
    @mock.patch("apps.appointments.tasks.requests.post")
    def test_disp4_delivers_to_staff_resource(self, mock_post, mock_safe):
        mock_post.return_value = mock.Mock(status_code=200, raise_for_status=mock.Mock())
        alert_schedule, slot, staff, resource = self._make_alert_schedule(
            target_category="resource",
            gs_alert_preference="push",
            gs_alert_url="https://staff.example.com/hook",
        )

        result = dispatch_alert_schedule(str(alert_schedule.pk))

        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args[0][0], "https://staff.example.com/hook")
        self.assertEqual(result["attempted"], 1)

    @mock.patch("apps.appointments.tasks._is_safe_outbound_url", return_value=True)
    @mock.patch("apps.appointments.tasks.requests.post")
    def test_disp5_delivers_to_physical_resource(self, mock_post, mock_safe):
        mock_post.return_value = mock.Mock(status_code=200, raise_for_status=mock.Mock())
        alert_schedule, slot, staff, resource = self._make_alert_schedule(
            target_category="resource",
            with_resource=True,
            resource_alert_preference="push",
            resource_alert_url="https://room.example.com/hook",
        )

        result = dispatch_alert_schedule(str(alert_schedule.pk))

        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args[0][0], "https://room.example.com/hook")
        self.assertEqual(result["attempted"], 1)

    @mock.patch("apps.appointments.tasks._is_safe_outbound_url", return_value=False)
    @mock.patch("apps.appointments.tasks.requests.post")
    def test_disp6_unsafe_url_is_skipped_not_posted(self, mock_post, mock_safe):
        alert_schedule, slot, staff, resource = self._make_alert_schedule(
            target_category="resource",
            gs_alert_preference="push",
            gs_alert_url="https://169-254-169-254.example.com/hook",
        )

        result = dispatch_alert_schedule(str(alert_schedule.pk))

        mock_post.assert_not_called()
        self.assertEqual(result["skipped_unsafe"], 1)
        self.assertEqual(result["attempted"], 0)
        alert_schedule.refresh_from_db()
        self.assertTrue(alert_schedule.dispatched)  # still marked dispatched — best-effort

    @mock.patch("apps.appointments.tasks._is_safe_outbound_url", return_value=True)
    @mock.patch("apps.appointments.tasks.requests.post")
    def test_disp7_idempotent_noop_on_second_run(self, mock_post, mock_safe):
        mock_post.return_value = mock.Mock(status_code=200, raise_for_status=mock.Mock())
        alert_schedule, slot, staff, resource = self._make_alert_schedule(
            target_category="resource",
            gs_alert_preference="push",
            gs_alert_url="https://staff.example.com/hook",
        )

        first = dispatch_alert_schedule(str(alert_schedule.pk))
        self.assertEqual(first["attempted"], 1)
        mock_post.reset_mock()

        second = dispatch_alert_schedule(str(alert_schedule.pk))
        self.assertEqual(second.get("already_dispatched"), True)
        mock_post.assert_not_called()

    @mock.patch("apps.appointments.tasks.requests.post")
    def test_disp8_nonexistent_alert_schedule_pk_is_a_safe_noop(self, mock_post):
        result = dispatch_alert_schedule("999999")
        self.assertEqual(result, {"attempted": 0, "skipped_unsafe": 0})
        mock_post.assert_not_called()
