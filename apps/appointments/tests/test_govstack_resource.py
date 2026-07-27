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
POST /resource/new works with the real spec's {"resource_details": {...}}
wire format (FIX 1) — exactly the shape a spec-compliant GovStack caller
sends (as the JSON value of the `qry` query PARAMETER) and exactly what
would have caught the wrong-inner-key bug this codebase shipped with from
initial (Wave B) implementation. (Note: an unrelated, separate bug — this
codebase additionally double-wrapping that JSON value in an extra outer
"qry" key — was fixed later; these tests use the correct, single-nested
shape throughout.)
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from django.test import TestCase, override_settings

from apps.appointments.models import GovStackBBCredential, Resource
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
        {"resource_details": {...}}, must succeed — this is exactly the wire
        format a spec-compliant GovStack caller would send as the `qry` query
        PARAMETER's JSON value (single-nested; the double-nested
        {"qry": {"resource_details": {...}}} shape was this codebase's own
        bug, now fixed)."""
        qry = {"resource_details": {
            "name": "Exam Room 1", "category": "room",
            "phone": "+15005550001", "email": "room1@example.gov",
            "alert_url": "https://example.gov/alerts/room1",
            "alert_preference": "push",
            "status_poll_url": "https://example.gov/poll/room1",
        }}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        # Finding #3 fix: resource_id is "R-<pk>"-prefixed (matches
        # list_details/availability) — strip the prefix to look up the row.
        self.assertTrue(data["resource_id"].startswith("R-"))
        resource = Resource.objects.get(pk=data["resource_id"][2:])
        self.assertEqual(resource.name_en, "Exam Room 1")
        self.assertEqual(resource.resource_type, "room")
        self.assertEqual(resource.alert_preference, "push")
        self.assertTrue(resource.is_active)

    def test_r1b_creates_admin_audit_event(self):
        """
        Round 2 certifiability re-audit fix (MEDIUM): POST /resource/new must
        leave a real, queryable, tamper-evident BookingAuditLog entry
        (booking=None) recording who created the resource and when.
        """
        from apps.appointments.models import BookingAuditLog

        qry = {"resource_details": {"name": "Audit Trail Room", "category": "room"}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 200)
        resource_pk = resp.json()["resource_id"][2:]  # strip "R-" prefix

        event = BookingAuditLog.objects.filter(
            action=BookingAuditLog.ACTION_ADMIN_RESOURCE_MUTATED,
            detail__resource_pk=resource_pk,
        ).first()
        self.assertIsNotNone(event)
        self.assertIsNone(event.booking)
        self.assertEqual(event.detail["operation"], "create")
        # _AUTH["requestor_id"] == "test-bb" (see module constants above).
        self.assertEqual(event.actor_id, "test-bb")

    def test_r2_old_wrong_details_key_no_longer_creates_a_resource_from_it(self):
        """R2: the OLD (pre-FIX-1) wrapper key "details" is no longer
        recognised — DRF silently drops unknown keys, so the inner
        resource_details serializer field is missing and required=True fails
        validation, returning 400 rather than silently succeeding with blank
        fields."""
        qry = {"details": {"name": "Should Not Work", "category": "room"}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)

    def test_r3_missing_qry_returns_400(self):
        resp = self.client.post(NEW_URL + _qs())
        self.assertEqual(resp.status_code, 400)

    def test_r4_invalid_alert_preference_returns_400(self):
        qry = {"resource_details": {"name": "Bad Pref", "alert_preference": "carrier_pigeon"}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "CREATE_FAILED")

    def test_r5_category_maps_to_resource_type(self):
        qry = {"resource_details": {"name": "Video Suite", "category": "video"}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 200)
        # Finding #3 fix: resource_id is now "R-<pk>"-prefixed (matches
        # list_details/availability) — strip the prefix to look up the row.
        resource_id_str = resp.json()["resource_id"]
        self.assertTrue(resource_id_str.startswith("R-"))
        resource = Resource.objects.get(pk=resource_id_str[2:])
        self.assertEqual(resource.resource_type, "virtual")

    def test_r6_blank_details_still_creates_resource(self):
        resp = self._post({"resource_details": {}})
        self.assertEqual(resp.status_code, 200)

    def test_r7_invalid_json_qry_returns_400(self):
        params = urlencode({**_AUTH, "qry": "{not valid json"})
        resp = self.client.post(NEW_URL + "?" + params)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "INVALID_QRY")

    def test_r8_missing_auth_params_returns_401_or_403(self):
        qry = json.dumps({"resource_details": {"name": "No Auth"}})
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
        # Finding #3 fix: resource_id is now "R-<pk>"-prefixed.
        self.assertEqual(resp.json()["resource_id"], f"R-{resource.pk}")
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
        # Finding #3 fix: resource_id is now "R-<pk>"-prefixed.
        self.assertEqual(resp.json()["resource_id"], f"R-{resource.pk}")
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
        """
        Bug 2 fix: the response body is now a bare JSON array (matches the
        real GovStack OpenAPI spec's resource_list schema exactly) — no more
        {"status": "success", "data": [...], "truncated": ...} wrapper.
        """
        _create_resource(name="Room A")
        _create_resource(name="Room B")
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 2)

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
        data = resp.json()
        names = [r["name"] for r in data]
        self.assertIn("Video Room", names)
        self.assertNotIn("Phone Booth", names)

    def test_r24_filter_by_resource_id(self):
        resource = _create_resource()
        _create_resource()
        resp = self._get({"resource_filter": {"resource_id": f"R-{resource.pk}"}})
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["resource_id"], f"R-{resource.pk}")

    def test_r24b_filter_by_resource_id_array_matches_multiple(self):
        """
        Bug 1 fix: resource_id is array-typed per the real GovStack spec — a
        JSON array of 2+ (R-prefixed) ids returns all matching records.
        """
        r1 = _create_resource(name="Array Match 1")
        r2 = _create_resource(name="Array Match 2")
        _create_resource(name="Not Matched")
        resp = self._get({"resource_filter": {"resource_id": [f"R-{r1.pk}", f"R-{r2.pk}"]}})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        returned_ids = {item["resource_id"] for item in data}
        self.assertEqual(returned_ids, {f"R-{r1.pk}", f"R-{r2.pk}"})

    def test_r24c_filter_by_resource_id_single_string_still_works(self):
        """Bug 1 fix: backward compatibility — a single bare-string resource_id still works."""
        resource = _create_resource(name="Single String Filter")
        _create_resource(name="Other")
        resp = self._get({"resource_filter": {"resource_id": f"R-{resource.pk}"}})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)

    def test_r24d_filter_by_resource_id_invalid_entry_is_skipped(self):
        """
        Bug 1 fix: an invalid/non-numeric entry in the resource_id array is
        silently skipped (matches this function's pre-existing per-id
        "malformed -> no match" convention) rather than raising — the other,
        valid entries in the array still match.
        """
        resource = _create_resource(name="Valid Entry")
        resp = self._get({"resource_filter": {"resource_id": [f"R-{resource.pk}", "R-not-a-number"]}})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["resource_id"], f"R-{resource.pk}")

    def test_r25_response_excludes_email_and_alert_fields_by_default(self):
        resource = _create_resource(email="hide@example.gov", alert_url="https://hide.example.gov")
        resp = self._get({"resource_filter": {"resource_id": f"R-{resource.pk}"}})
        item = resp.json()[0]
        self.assertNotIn("email", item)
        self.assertNotIn("alert_url", item)

    def test_r26_email_included_when_required_flag_true(self):
        resource = _create_resource(email="show@example.gov")
        resp = self._get({
            "resource_filter": {"resource_id": f"R-{resource.pk}"},
            "resource_details_required": {"email": True},
        })
        item = resp.json()[0]
        self.assertEqual(item["email"], "show@example.gov")

    def test_r27_no_matches_returns_empty_list(self):
        resp = self._get({"resource_filter": {"category": "definitely-does-not-exist"}})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_r28_missing_auth_params_returns_401_or_403(self):
        resp = self.client.get(LIST_URL)
        self.assertIn(resp.status_code, (401, 403))


# ===========================================================================
# R29-R35: GET /resource/availability
# ===========================================================================

class ResourceAvailabilityTests(ResourceBaseTestCase):
    """R29-R35: GET /resource/availability"""

    def test_r29_happy_path_returns_available_slot(self):
        """
        Bug 2 fix: the response body is now a bare JSON array — no more
        {"status": "success", "data": [...], "truncated": ...} wrapper.
        """
        resource = _create_resource()
        slot = _create_slot_for_resource(resource)
        resp = self._availability({"free_resource_filter": {"resource_id": f"R-{resource.pk}"}})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        slot_ids = [item["slot_id"] for item in data]
        self.assertIn(str(slot.id), slot_ids)

    def test_r30_filter_by_entity_id_via_affiliation(self):
        org = entity_create(name="Avail Org")
        resource = _create_resource()
        affiliation_create(resource_id=resource.pk, entity_id=org.pk)
        slot = _create_slot_for_resource(resource)
        resp = self._availability({"free_resource_filter": {"Entity_id": str(org.pk)}})
        data = resp.json()
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
        slot_ids = [item["slot_id"] for item in resp.json()]
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
        self.assertEqual(resp.json(), [])

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

    def _make_role_bb(self, role):
        """
        Create a GovStackRegisteredBB (identity, bb_id=_AUTH['requestor_id']) PLUS a
        real GovStackBBCredential for it, and point _AUTH['request_token'] at the
        correct plaintext secret. Finding #1 fix: request_token must never equal
        bb_id — it must verify against a separate hashed secret.
        """
        bb = GovStackRegisteredBB.objects.create(bb_id=_AUTH["requestor_id"], is_active=True, role=role)
        token = GovStackBBCredential.generate_plaintext_token()
        credential = GovStackBBCredential(bb=bb)
        credential.set_token(token)
        credential.save()
        _AUTH["request_token"] = token
        self.addCleanup(lambda: _AUTH.update(request_token="test-token"))
        return bb

    def test_r36_organizer_role_denied_on_resource_new(self):
        self._make_role_bb("organizer")
        resp = self._post({"resource_details": {"name": "X"}})
        self.assertEqual(resp.status_code, 403)

    def test_r37_admin_role_allowed_on_resource_new(self):
        self._make_role_bb("admin")
        resp = self._post({"resource_details": {"name": "X"}})
        self.assertEqual(resp.status_code, 200)

    def test_r38_organizer_role_denied_on_resource_modifications(self):
        self._make_role_bb("organizer")
        resource = _create_resource()
        resp = self._put({"details": {"name": "X"}}, resource_id=resource.pk)
        self.assertEqual(resp.status_code, 403)

    def test_r39_organizer_role_denied_on_resource_delete(self):
        self._make_role_bb("organizer")
        resource = _create_resource()
        resp = self._delete(resource_id=resource.pk)
        self.assertEqual(resp.status_code, 403)

    def test_r40_admin_role_allowed_on_resource_delete(self):
        self._make_role_bb("admin")
        resource = _create_resource()
        resp = self._delete(resource_id=resource.pk)
        self.assertEqual(resp.status_code, 200)

    def test_r41_resource_role_denied_on_resource_list(self):
        self._make_role_bb("resource")
        resp = self._get()
        self.assertEqual(resp.status_code, 403)

    def test_r42_organizer_role_allowed_on_resource_list(self):
        self._make_role_bb("organizer")
        resp = self._get()
        self.assertEqual(resp.status_code, 200)

    def test_r43_admin_role_allowed_on_resource_list(self):
        self._make_role_bb("admin")
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
        self._make_role_bb("subscriber")
        resp = self._availability()
        self.assertEqual(resp.status_code, 403)

    def test_r45_resource_role_allowed_on_resource_availability(self):
        self._make_role_bb("resource")
        resp = self._availability()
        self.assertEqual(resp.status_code, 200)


# ===========================================================================
# R46-R50: Finding #3 — cross-endpoint resource_id format round-trips
# ===========================================================================

class ResourceIdRoundTripTests(ResourceBaseTestCase):
    """
    R46-R50: all 5 Resource endpoints must emit/accept the SAME resource_id
    format ("R-<pk>"), so a value taken from any one endpoint's response can
    be used directly against any other endpoint with no manual translation.
    Direct regression coverage for Finding #3 (resource_id format
    inconsistency across the 5 Resource endpoints).
    """

    def test_r46_create_response_resource_id_round_trips_into_list_details_filter(self):
        """The resource_id returned by POST /new can be used verbatim as the
        resource_id filter on GET /list_details."""
        resp = self._post({"resource_details": {"name": "Round Trip Room"}})
        self.assertEqual(resp.status_code, 200)
        resource_id = resp.json()["resource_id"]
        self.assertTrue(resource_id.startswith("R-"))

        list_resp = self._get({"resource_filter": {"resource_id": resource_id}})
        self.assertEqual(list_resp.status_code, 200)
        data = list_resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["resource_id"], resource_id)

    def test_r47_create_response_resource_id_round_trips_into_modifications(self):
        """The resource_id returned by POST /new can be used verbatim as the
        resource_id query param on PUT /modifications."""
        resp = self._post({"resource_details": {"name": "Modify Round Trip"}})
        resource_id = resp.json()["resource_id"]

        put_resp = self._put({"details": {"name": "Renamed"}}, resource_id=resource_id)
        self.assertEqual(put_resp.status_code, 200)
        self.assertEqual(put_resp.json()["resource_id"], resource_id)

    def test_r48_modifications_response_resource_id_round_trips_into_delete(self):
        """The resource_id returned by PUT /modifications can be used verbatim
        on DELETE /resource."""
        resp = self._post({"resource_details": {"name": "Delete Round Trip"}})
        created_id = resp.json()["resource_id"]

        put_resp = self._put({"details": {"name": "Renamed Again"}}, resource_id=created_id)
        modified_id = put_resp.json()["resource_id"]
        self.assertEqual(modified_id, created_id)

        delete_resp = self._delete(resource_id=modified_id)
        self.assertEqual(delete_resp.status_code, 200)
        self.assertEqual(delete_resp.json()["resource_id"], modified_id)

    def test_r49_bare_integer_resource_id_still_accepted_for_backward_compatibility(self):
        """modifications/delete also accept a bare integer (no 'R-' prefix),
        for backward compatibility with callers that predate this fix."""
        resource = _create_resource(name="Bare Int Compat")

        put_resp = self._put({"details": {"name": "Still Works"}}, resource_id=resource.pk)
        self.assertEqual(put_resp.status_code, 200)
        self.assertEqual(put_resp.json()["resource_id"], f"R-{resource.pk}")

    def test_r50_staff_prefixed_resource_id_rejected_on_modifications_and_delete(self):
        """An 'S-<pk>' (StaffProfile) resource_id is rejected with 400 on the
        Resource-only modify/delete endpoints — StaffProfile records cannot
        be modified or deleted via the GovStack Resource API."""
        put_resp = self._put({"details": {"name": "X"}}, resource_id="S-1")
        self.assertEqual(put_resp.status_code, 400)
        self.assertEqual(put_resp.json()["code"], "INVALID_RESOURCE_ID")

        delete_resp = self._delete(resource_id="S-1")
        self.assertEqual(delete_resp.status_code, 400)
        self.assertEqual(delete_resp.json()["code"], "INVALID_RESOURCE_ID")


# ===========================================================================
# R51-R56: Finding #6 — SSRF hardening on alert_url / status_poll_url
# ===========================================================================

class ResourceSsrfHardeningTests(ResourceBaseTestCase):
    """
    R51-R56: registration-time HTTPS-only validation of alert_url and
    status_poll_url on POST /resource/new and PUT /resource/modifications.
    Direct regression coverage for Finding #6 (the 3 SSRF TODOs — resource_create's
    single combined TODO plus resource_modify's 2 separate alert_url/status_poll_url
    TODOs — previously left both fields completely unvalidated at registration
    time). Mirrors the identical precedent already covered for Subscriber's
    alert_url/status_poll_url fields.

    Note: this is layer 1 of the two-layer SSRF defense (coarse HTTPS-only
    check at registration time). Layer 2 (DNS-resolution + private/loopback/
    link-local/reserved/multicast/CGNAT IP blocking at dispatch time) lives in
    apps/appointments/tasks.py's _is_safe_outbound_url() and was already wired
    to resource.alert_url before this fix — these tests only need to prove
    layer 1 is now closed.
    """

    def test_r51_plain_http_alert_url_rejected_on_create(self):
        qry = {"resource_details": {"name": "Insecure Room", "alert_url": "http://insecure.example.gov/hook"}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "CREATE_FAILED")
        self.assertFalse(Resource.objects.filter(name_en="Insecure Room").exists())

    def test_r52_plain_http_status_poll_url_rejected_on_create(self):
        qry = {"resource_details": {"name": "Insecure Poll", "status_poll_url": "http://insecure.example.gov/poll"}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "CREATE_FAILED")

    def test_r53_valid_https_urls_still_succeed_on_create(self):
        qry = {"resource_details": {
            "name": "Secure Room",
            "alert_url": "https://secure.example.gov/hook",
            "status_poll_url": "https://secure.example.gov/poll",
        }}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 200)
        resource_id = resp.json()["resource_id"]
        resource = Resource.objects.get(pk=resource_id[2:])
        self.assertEqual(resource.alert_url, "https://secure.example.gov/hook")
        self.assertEqual(resource.status_poll_url, "https://secure.example.gov/poll")

    def test_r54_plain_http_alert_url_rejected_on_modifications(self):
        resource = _create_resource()
        resp = self._put({"details": {"alert_url": "http://insecure.example.gov/hook"}}, resource_id=resource.pk)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "MODIFY_FAILED")
        resource.refresh_from_db()
        self.assertEqual(resource.alert_url, "")

    def test_r55_plain_http_status_poll_url_rejected_on_modifications(self):
        resource = _create_resource()
        resp = self._put({"details": {"status_poll_url": "http://insecure.example.gov/poll"}}, resource_id=resource.pk)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "MODIFY_FAILED")
        resource.refresh_from_db()
        self.assertEqual(resource.status_poll_url, "")

    def test_r56_valid_https_url_still_succeeds_on_modifications(self):
        resource = _create_resource()
        resp = self._put(
            {"details": {"alert_url": "https://secure.example.gov/hook-updated"}},
            resource_id=resource.pk,
        )
        self.assertEqual(resp.status_code, 200)
        resource.refresh_from_db()
        self.assertEqual(resource.alert_url, "https://secure.example.gov/hook-updated")
