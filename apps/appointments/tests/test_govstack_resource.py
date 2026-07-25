"""
GovStack Scheduler BB — Resource endpoints test suite.

Covers 5 endpoints:
  POST   /govstack/scheduler/resource/new
  PUT    /govstack/scheduler/resource/modifications
  DELETE /govstack/scheduler/resource
  GET    /govstack/scheduler/resource/list_details
  GET    /govstack/scheduler/resource/availability

Written as part of the final certifiability review (FIX 5a): Resource was
one of 3 of 9 API groups (13 of 37 endpoints) with zero dedicated tests
before this file. Structural template and _AUTH/_qs/_qry_qs helper pattern
copied from test_govstack_message.py / test_govstack_alert_schedule.py;
auth/role enforcement pattern copied from test_govstack_log.py. Test
numbering: R1-Rxx.

R1 below is the single most important test in this file: it proves
POST /resource/new works with the real spec's {"qry": {"resource_details":
{...}}} wire format (FIX 1) — exactly the shape a spec-compliant GovStack
caller sends and exactly what would have caught the wrong-wrapper-key bug
this codebase shipped with from initial (Wave B) implementation.
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from django.test import TestCase, override_settings

from apps.appointments.models import Resource
from apps.appointments.services.govstack_affiliation import affiliation_create
from apps.appointments.services.govstack_entity import entity_create
from apps.appointments.services.govstack_event import event_create
from apps.appointments.services.govstack_resource import resource_create
from apps.payments.govstack_models import GovStackRegisteredBB

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEW_URL = "/govstack/scheduler/resource/new"
MODIFICATIONS_URL = "/govstack/scheduler/resource/modifications"
LIST_URL = "/govstack/scheduler/resource/list_details"
DELETE_URL = "/govstack/scheduler/resource"
AVAILABILITY_URL = "/govstack/scheduler/resource/availability"

_AUTH = {"requestor_id": "test-bb", "request_token": "test-token"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _qs(**extra):
    params = {**_AUTH, **extra}
    return "?" + urlencode(params)


def _qry_qs(qry_dict, **extra):
    params = {**_AUTH, "qry": json.dumps(qry_dict), **extra}
    return "?" + urlencode(params)


def _create_resource(name="Test Room", category="room", **kwargs):
    return resource_create(
        name=name,
        category=category,
        phone=kwargs.get("phone", ""),
        email=kwargs.get("email", ""),
        alert_url=kwargs.get("alert_url", ""),
        alert_preference=kwargs.get("alert_preference", ""),
        status_poll_url=kwargs.get("status_poll_url", ""),
    )


def _create_slot_for_resource(resource, start="2027-06-01T09:00:00Z", end="2027-06-01T10:00:00Z"):
    """Factory: create a Slot (via event_create) and attach it to `resource`."""
    slots = event_create(name="Resource Availability Test Event", slots=[{"from": start, "to": end}])
    slot = slots[0]
    slot.resource = resource
    slot.save(update_fields=["resource"])
    return slot


# ===========================================================================
# Base test case
# ===========================================================================

class ResourceBaseTestCase(TestCase):
    """Shared HTTP helpers for all resource endpoint tests."""

    def _post(self, qry_dict):
        return self.client.post(NEW_URL + _qry_qs(qry_dict))

    def _put(self, qry_dict, resource_id=None):
        extra = {"resource_id": resource_id} if resource_id is not None else {}
        return self.client.put(MODIFICATIONS_URL + _qry_qs(qry_dict, **extra))

    def _delete(self, resource_id=None):
        extra = {"resource_id": resource_id} if resource_id is not None else {}
        return self.client.delete(DELETE_URL + _qs(**extra))

    def _get(self, qry_dict=None):
        if qry_dict is None:
            return self.client.get(LIST_URL + _qs())
        return self.client.get(LIST_URL + _qry_qs(qry_dict))

    def _availability(self, qry_dict=None):
        if qry_dict is None:
            return self.client.get(AVAILABILITY_URL + _qs())
        return self.client.get(AVAILABILITY_URL + _qry_qs(qry_dict))


# ===========================================================================
# R1-R8: POST /resource/new
# ===========================================================================

class ResourceNewTests(ResourceBaseTestCase):
    """R1-R8: POST /resource/new"""

    def test_r1_spec_literal_wire_format_succeeds(self):
        """R1 (locks in FIX 1): the real spec's resource_new_qry shape,
        {"qry": {"resource_details": {...}}}, must succeed — this is exactly
        the wire format a spec-compliant GovStack caller would send."""
        qry = {"qry": {"resource_details": {
            "name": "Exam Room 1", "category": "room",
            "phone": "+15005550001", "email": "room1@example.gov",
            "alert_url": "https://example.gov/alerts/room1",
            "alert_preference": "push",
            "status_poll_url": "https://example.gov/poll/room1",
        }}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        resource = Resource.objects.get(pk=data["resource_id"])
        self.assertEqual(resource.name_en, "Exam Room 1")
        self.assertEqual(resource.resource_type, "room")
        self.assertEqual(resource.alert_preference, "push")
        self.assertTrue(resource.is_active)

    def test_r2_old_wrong_details_key_no_longer_creates_a_resource_from_it(self):
        """R2: the OLD (pre-FIX-1) wrapper key "details" is no longer
        recognised — DRF silently drops unknown keys, so the inner
        resource_details serializer field is missing and required=True fails
        validation, returning 400 rather than silently succeeding with blank
        fields."""
        qry = {"qry": {"details": {"name": "Should Not Work", "category": "room"}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)

    def test_r3_missing_qry_returns_400(self):
        resp = self.client.post(NEW_URL + _qs())
        self.assertEqual(resp.status_code, 400)

    def test_r4_invalid_alert_preference_returns_400(self):
        qry = {"qry": {"resource_details": {"name": "Bad Pref", "alert_preference": "carrier_pigeon"}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "CREATE_FAILED")

    def test_r5_category_maps_to_resource_type(self):
        qry = {"qry": {"resource_details": {"name": "Video Suite", "category": "video"}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 201)
        resource = Resource.objects.get(pk=resp.json()["resource_id"])
        self.assertEqual(resource.resource_type, "virtual")

    def test_r6_blank_details_still_creates_resource(self):
        resp = self._post({"qry": {"resource_details": {}}})
        self.assertEqual(resp.status_code, 201)

    def test_r7_invalid_json_qry_returns_400(self):
        params = urlencode({**_AUTH, "qry": "{not valid json"})
        resp = self.client.post(NEW_URL + "?" + params)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "INVALID_QRY")

    def test_r8_missing_auth_params_returns_401_or_403(self):
        qry = json.dumps({"qry": {"resource_details": {"name": "No Auth"}}})
        resp = self.client.post(NEW_URL + f"?qry={qry}")
        self.assertIn(resp.status_code, (401, 403))


# ===========================================================================
# R9-R15: PUT /resource/modifications
# ===========================================================================

class ResourceModificationsTests(ResourceBaseTestCase):
    """R9-R15: PUT /resource/modifications (real spec key "details" — unchanged by FIX 1)"""

    def test_r9_happy_path_updates_name(self):
        resource = _create_resource(name="Old Name")
        resp = self._put({"details": {"name": "New Name"}}, resource_id=resource.pk)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["resource_id"], str(resource.pk))
        resource.refresh_from_db()
        self.assertEqual(resource.name_en, "New Name")

    def test_r10_updates_alert_url_and_preference(self):
        resource = _create_resource()
        resp = self._put(
            {"details": {"alert_url": "https://new.example.gov/hook", "alert_preference": "poll"}},
            resource_id=resource.pk,
        )
        self.assertEqual(resp.status_code, 200)
        resource.refresh_from_db()
        self.assertEqual(resource.alert_url, "https://new.example.gov/hook")
        self.assertEqual(resource.alert_preference, "poll")

    def test_r11_missing_resource_id_returns_400(self):
        resp = self.client.put(MODIFICATIONS_URL + _qry_qs({"details": {"name": "X"}}))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "MISSING_RESOURCE_ID")

    def test_r12_malformed_resource_id_returns_400(self):
        resp = self._put({"details": {"name": "X"}}, resource_id="abc")
        self.assertEqual(resp.status_code, 400)

    def test_r13_nonexistent_resource_id_returns_404(self):
        resp = self._put({"details": {"name": "X"}}, resource_id=999999)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "RESOURCE_NOT_FOUND")

    def test_r14_invalid_alert_preference_returns_400(self):
        resource = _create_resource()
        resp = self._put({"details": {"alert_preference": "smoke_signal"}}, resource_id=resource.pk)
        self.assertEqual(resp.status_code, 400)

    def test_r15_missing_auth_params_returns_401_or_403(self):
        resource = _create_resource()
        params = urlencode({"resource_id": resource.pk, "qry": json.dumps({"details": {"name": "X"}})})
        resp = self.client.put(MODIFICATIONS_URL + "?" + params)
        self.assertIn(resp.status_code, (401, 403))


# ===========================================================================
# R16-R21: DELETE /resource
# ===========================================================================

class ResourceDeleteTests(ResourceBaseTestCase):
    """R16-R21: DELETE /resource"""

    def test_r16_happy_path_soft_deletes_resource(self):
        resource = _create_resource()
        resp = self._delete(resource_id=resource.pk)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["resource_id"], str(resource.pk))
        resource.refresh_from_db()
        self.assertFalse(resource.is_active)

    def test_r17_missing_resource_id_returns_400(self):
        resp = self._delete()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "MISSING_RESOURCE_ID")

    def test_r18_nonexistent_resource_id_returns_404(self):
        resp = self._delete(resource_id=999999)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "RESOURCE_NOT_FOUND")

    def test_r19_malformed_resource_id_returns_400(self):
        resp = self._delete(resource_id="not-a-number")
        self.assertEqual(resp.status_code, 400)

    def test_r20_row_still_exists_after_soft_delete(self):
        resource = _create_resource()
        self._delete(resource_id=resource.pk)
        self.assertTrue(Resource.objects.filter(pk=resource.pk).exists())

    def test_r21_missing_auth_params_returns_401_or_403(self):
        resource = _create_resource()
        resp = self.client.delete(DELETE_URL + f"?resource_id={resource.pk}")
        self.assertIn(resp.status_code, (401, 403))


# ===========================================================================
# R22-R28: GET /resource/list_details
# ===========================================================================

class ResourceListDetailsTests(ResourceBaseTestCase):
    """R22-R28: GET /resource/list_details"""

    def test_r22_happy_path_no_filter_returns_all(self):
        _create_resource(name="Room A")
        _create_resource(name="Room B")
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(len(data["data"]), 2)
        self.assertIn("truncated", data)

    def test_r23_filter_by_category(self):
        """
        R23: resource_list()'s category filter matches against the STORED
        Resource.resource_type (icontains) — not the raw input category
        string used at creation time. A "video" category resource is mapped
        to resource_type="virtual" at creation (see
        services.govstack_resource._map_category_to_resource_type), so the
        filter value that actually matches is "virtual".
        """
        _create_resource(name="Video Room", category="video")
        _create_resource(name="Phone Booth", category="phone")
        resp = self._get({"resource_filter": {"category": "virtual"}})
        data = resp.json()["data"]
        names = [r["name"] for r in data]
        self.assertIn("Video Room", names)
        self.assertNotIn("Phone Booth", names)

    def test_r24_filter_by_resource_id(self):
        resource = _create_resource()
        _create_resource()
        resp = self._get({"resource_filter": {"resource_id": f"R-{resource.pk}"}})
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["resource_id"], f"R-{resource.pk}")

    def test_r25_response_excludes_email_and_alert_fields_by_default(self):
        resource = _create_resource(email="hide@example.gov", alert_url="https://hide.example.gov")
        resp = self._get({"resource_filter": {"resource_id": f"R-{resource.pk}"}})
        item = resp.json()["data"][0]
        self.assertNotIn("email", item)
        self.assertNotIn("alert_url", item)

    def test_r26_email_included_when_required_flag_true(self):
        resource = _create_resource(email="show@example.gov")
        resp = self._get({
            "resource_filter": {"resource_id": f"R-{resource.pk}"},
            "resource_details_required": {"email": True},
        })
        item = resp.json()["data"][0]
        self.assertEqual(item["email"], "show@example.gov")

    def test_r27_no_matches_returns_empty_list(self):
        resp = self._get({"resource_filter": {"category": "definitely-does-not-exist"}})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"], [])

    def test_r28_missing_auth_params_returns_401_or_403(self):
        resp = self.client.get(LIST_URL)
        self.assertIn(resp.status_code, (401, 403))


# ===========================================================================
# R29-R35: GET /resource/availability
# ===========================================================================

class ResourceAvailabilityTests(ResourceBaseTestCase):
    """R29-R35: GET /resource/availability"""

    def test_r29_happy_path_returns_available_slot(self):
        resource = _create_resource()
        slot = _create_slot_for_resource(resource)
        resp = self._availability({"free_resource_filter": {"resource_id": f"R-{resource.pk}"}})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("truncated", data)
        slot_ids = [item["slot_id"] for item in data["data"]]
        self.assertIn(str(slot.id), slot_ids)

    def test_r30_filter_by_entity_id_via_affiliation(self):
        org = entity_create(name="Avail Org")
        resource = _create_resource()
        affiliation_create(resource_id=resource.pk, entity_id=org.pk)
        slot = _create_slot_for_resource(resource)
        resp = self._availability({"free_resource_filter": {"Entity_id": str(org.pk)}})
        data = resp.json()["data"]
        slot_ids = [item["slot_id"] for item in data]
        self.assertIn(str(slot.id), slot_ids)

    def test_r31_filter_by_from_to_datetime_range(self):
        resource = _create_resource()
        in_range = _create_slot_for_resource(
            resource, start="2027-06-15T09:00:00Z", end="2027-06-15T10:00:00Z"
        )
        out_of_range = _create_slot_for_resource(
            resource, start="2020-01-01T09:00:00Z", end="2020-01-01T10:00:00Z"
        )
        resp = self._availability({"free_resource_filter": {
            "resource_id": f"R-{resource.pk}",
            "from": "2027-01-01T00:00:00Z", "to": "2027-12-31T00:00:00Z",
        }})
        slot_ids = [item["slot_id"] for item in resp.json()["data"]]
        self.assertIn(str(in_range.id), slot_ids)
        self.assertNotIn(str(out_of_range.id), slot_ids)

    def test_r32_malformed_from_returns_400(self):
        resp = self._availability({"free_resource_filter": {"from": "not-a-date"}})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "INVALID_DATETIME")

    def test_r33_naive_datetime_returns_400(self):
        resp = self._availability({"free_resource_filter": {"from": "2027-06-01T09:00:00"}})
        self.assertEqual(resp.status_code, 400)

    def test_r34_no_matches_returns_empty_list(self):
        resp = self._availability({"free_resource_filter": {"resource_id": "R-999999"}})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"], [])

    def test_r35_missing_auth_params_returns_401_or_403(self):
        resp = self.client.get(AVAILABILITY_URL)
        self.assertIn(resp.status_code, (401, 403))


# ===========================================================================
# R36-R45: Auth / role enforcement
# ===========================================================================

@override_settings(GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True)
class ResourceRoleEnforcementTests(ResourceBaseTestCase):
    """
    R36-R45: role enforcement per endpoint —
      ResourceNewView / ResourceModificationsView / ResourceDeleteView:
        gs_actor_role="admin"
      ResourceListDetailsView:
        gs_actor_role="organizer"
      ResourceAvailabilityView:
        gs_actor_role="resource"
    Role hierarchy (lowest to highest): subscriber < resource < organizer <
    admin. Matches the exact role-enforcement pattern used in
    test_govstack_log.py.
    """

    def test_r36_organizer_role_denied_on_resource_new(self):
        GovStackRegisteredBB.objects.create(bb_id="test-token", is_active=True, role="organizer")
        resp = self._post({"qry": {"resource_details": {"name": "X"}}})
        self.assertEqual(resp.status_code, 403)

    def test_r37_admin_role_allowed_on_resource_new(self):
        GovStackRegisteredBB.objects.create(bb_id="test-token", is_active=True, role="admin")
        resp = self._post({"qry": {"resource_details": {"name": "X"}}})
        self.assertEqual(resp.status_code, 201)

    def test_r38_organizer_role_denied_on_resource_modifications(self):
        GovStackRegisteredBB.objects.create(bb_id="test-token", is_active=True, role="organizer")
        resource = _create_resource()
        resp = self._put({"details": {"name": "X"}}, resource_id=resource.pk)
        self.assertEqual(resp.status_code, 403)

    def test_r39_organizer_role_denied_on_resource_delete(self):
        GovStackRegisteredBB.objects.create(bb_id="test-token", is_active=True, role="organizer")
        resource = _create_resource()
        resp = self._delete(resource_id=resource.pk)
        self.assertEqual(resp.status_code, 403)

    def test_r40_admin_role_allowed_on_resource_delete(self):
        GovStackRegisteredBB.objects.create(bb_id="test-token", is_active=True, role="admin")
        resource = _create_resource()
        resp = self._delete(resource_id=resource.pk)
        self.assertEqual(resp.status_code, 200)

    def test_r41_resource_role_denied_on_resource_list(self):
        GovStackRegisteredBB.objects.create(bb_id="test-token", is_active=True, role="resource")
        resp = self._get()
        self.assertEqual(resp.status_code, 403)

    def test_r42_organizer_role_allowed_on_resource_list(self):
        GovStackRegisteredBB.objects.create(bb_id="test-token", is_active=True, role="organizer")
        resp = self._get()
        self.assertEqual(resp.status_code, 200)

    def test_r43_admin_role_allowed_on_resource_list(self):
        GovStackRegisteredBB.objects.create(bb_id="test-token", is_active=True, role="admin")
        resp = self._get()
        self.assertEqual(resp.status_code, 200)

    def test_r44_subscriber_role_denied_on_resource_availability(self):
        """
        R44: "subscriber" is not a valid GovStackRegisteredBB.role choice
        (only resource/organizer/admin are), but Django does not enforce
        model field `choices` at the ORM .create()/.save() layer (only via
        full_clean()) — used here deliberately to exercise the permission
        rank comparison below the endpoint's "resource" minimum, since there
        is no lower VALID BB role to construct this denial with otherwise.
        """
        GovStackRegisteredBB.objects.create(bb_id="test-token", is_active=True, role="subscriber")
        resp = self._availability()
        self.assertEqual(resp.status_code, 403)

    def test_r45_resource_role_allowed_on_resource_availability(self):
        GovStackRegisteredBB.objects.create(bb_id="test-token", is_active=True, role="resource")
        resp = self._availability()
        self.assertEqual(resp.status_code, 200)
