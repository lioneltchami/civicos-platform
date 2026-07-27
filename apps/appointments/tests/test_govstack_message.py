"""
GovStack Scheduler BB — Message endpoints test suite (Wave F).

Covers 4 endpoints:
  POST   /govstack/scheduler/message/new
  PUT    /govstack/scheduler/message/modifications
  DELETE /govstack/scheduler/message
  GET    /govstack/scheduler/message/list_details

Test numbering: MSG1-MSGxx, following the EV/AP/SUB numbering convention
used in test_govstack_event.py / test_govstack_appointment.py.
"""
from __future__ import annotations

import json
import uuid
from urllib.parse import urlencode

from django.test import TestCase, override_settings

from apps.appointments.models import GovStackAlertSchedule, GovStackBBCredential, GovStackMessage, Organization
from apps.appointments.services.govstack_alert_schedule import alert_schedule_create
from apps.appointments.services.govstack_event import event_create
from apps.appointments.services.govstack_message import message_create
from apps.payments.govstack_models import GovStackRegisteredBB

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEW_URL = "/govstack/scheduler/message/new"
MODIFICATIONS_URL = "/govstack/scheduler/message/modifications"
LIST_URL = "/govstack/scheduler/message/list_details"
DELETE_URL = "/govstack/scheduler/message"

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


def _create_org(name="Test Org"):
    slug = f"test-org-{uuid.uuid4().hex[:10]}"
    return Organization.objects.create(
        slug=slug,
        name_en=name,
        name_fr=name,
        organization_type="other",
        is_active=True,
    )


def _create_message(entity_id=None, category="reminder", message_body="Hello"):
    if entity_id is None:
        entity_id = _create_org().pk
    return message_create(entity_id=entity_id, category=category, message_body=message_body)


# ===========================================================================
# Base test case
# ===========================================================================

class MessageBaseTestCase(TestCase):
    """Shared HTTP helpers for all message endpoint tests."""

    def _post(self, qry_dict):
        return self.client.post(NEW_URL + _qry_qs(qry_dict))

    def _put(self, qry_dict, message_id=None):
        extra = {"message_id": message_id} if message_id is not None else {}
        return self.client.put(MODIFICATIONS_URL + _qry_qs(qry_dict, **extra))

    def _delete(self, message_id=None):
        extra = {"message_id": message_id} if message_id is not None else {}
        return self.client.delete(DELETE_URL + _qs(**extra))

    def _get(self, qry_dict=None):
        if qry_dict is None:
            return self.client.get(LIST_URL + _qs())
        return self.client.get(LIST_URL + _qry_qs(qry_dict))


# ===========================================================================
# MSG1-MSG8: POST /message/new
# ===========================================================================

class MessageNewTests(MessageBaseTestCase):
    """MSG1-MSG8: POST /message/new"""

    def test_msg1_happy_path_creates_message(self):
        org = _create_org()
        qry = {"message_details": {
            "entity_id": str(org.pk), "category": "reminder", "message_body": "Your appointment is tomorrow.",
        }}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(GovStackMessage.objects.filter(pk=data["message_id"]).exists())
        msg = GovStackMessage.objects.get(pk=data["message_id"])
        self.assertEqual(msg.entity_id, org.pk)
        self.assertEqual(msg.category, "reminder")
        self.assertEqual(msg.message_body, "Your appointment is tomorrow.")

    def test_msg2_missing_entity_id_returns_404(self):
        qry = {"message_details": {"category": "reminder", "message_body": "Hi"}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "ENTITY_NOT_FOUND")

    def test_msg3_nonexistent_entity_id_returns_404(self):
        qry = {"message_details": {"entity_id": "999999", "category": "reminder"}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "ENTITY_NOT_FOUND")

    def test_msg4_malformed_entity_id_returns_404(self):
        qry = {"message_details": {"entity_id": "not-a-number", "category": "reminder"}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "ENTITY_NOT_FOUND")

    def test_msg5_inactive_entity_returns_404(self):
        org = _create_org()
        org.is_active = False
        org.save(update_fields=["is_active"])
        qry = {"message_details": {"entity_id": str(org.pk)}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 404)

    def test_msg6_category_too_long_returns_400(self):
        org = _create_org()
        qry = {"message_details": {"entity_id": str(org.pk), "category": "x" * 51}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)

    def test_msg7_missing_qry_returns_400(self):
        resp = self.client.post(NEW_URL + _qs())
        self.assertEqual(resp.status_code, 400)

    def test_msg8_blank_category_and_body_allowed(self):
        org = _create_org()
        qry = {"message_details": {"entity_id": str(org.pk)}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 201)


# ===========================================================================
# MSG9-MSG15: PUT /message/modifications
# ===========================================================================

class MessageModificationsTests(MessageBaseTestCase):
    """MSG9-MSG15: PUT /message/modifications"""

    def test_msg9_happy_path_updates_category(self):
        msg = _create_message(category="old")
        resp = self._put({"details": {"category": "new"}}, message_id=str(msg.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["message_id"], str(msg.pk))
        msg.refresh_from_db()
        self.assertEqual(msg.category, "new")

    def test_msg10_happy_path_updates_message_body(self):
        msg = _create_message(message_body="old body")
        resp = self._put({"details": {"message_body": "new body"}}, message_id=str(msg.pk))
        self.assertEqual(resp.status_code, 200)
        msg.refresh_from_db()
        self.assertEqual(msg.message_body, "new body")

    def test_msg11_missing_message_id_returns_400(self):
        resp = self.client.put(MODIFICATIONS_URL + _qry_qs({"details": {"category": "x"}}))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "MISSING_MESSAGE_ID")

    def test_msg12_malformed_message_id_returns_400(self):
        resp = self._put({"details": {"category": "x"}}, message_id="abc")
        self.assertEqual(resp.status_code, 400)

    def test_msg13_nonexistent_message_id_returns_404(self):
        resp = self._put({"details": {"category": "x"}}, message_id="999999")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "MESSAGE_NOT_FOUND")

    def test_msg14_category_too_long_returns_400(self):
        msg = _create_message()
        resp = self._put({"details": {"category": "x" * 51}}, message_id=str(msg.pk))
        self.assertEqual(resp.status_code, 400)

    def test_msg15_no_fields_supplied_is_a_noop_success(self):
        msg = _create_message(category="unchanged")
        resp = self._put({"details": {}}, message_id=str(msg.pk))
        self.assertEqual(resp.status_code, 200)
        msg.refresh_from_db()
        self.assertEqual(msg.category, "unchanged")


# ===========================================================================
# MSG16-MSG21: DELETE /message
# ===========================================================================

class MessageDeleteTests(MessageBaseTestCase):
    """MSG16-MSG21: DELETE /message"""

    def test_msg16_happy_path_deletes_message(self):
        msg = _create_message()
        resp = self._delete(message_id=str(msg.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["message_id"], str(msg.pk))
        self.assertFalse(GovStackMessage.objects.filter(pk=msg.pk).exists())

    def test_msg17_missing_message_id_returns_400(self):
        resp = self._delete()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "MISSING_MESSAGE_ID")

    def test_msg18_nonexistent_message_id_returns_404(self):
        resp = self._delete(message_id="999999")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "MESSAGE_NOT_FOUND")

    def test_msg19_malformed_message_id_returns_400(self):
        resp = self._delete(message_id="not-a-number")
        self.assertEqual(resp.status_code, 400)

    def test_msg20_protected_error_when_referenced_by_alert_schedule(self):
        """MSG20: deleting a message still referenced by an AlertSchedule → 400 MESSAGE_IN_USE."""
        msg = _create_message()
        slots = event_create(name="Test Event", slots=[
            {"from": "2027-01-01T09:00:00Z", "to": "2027-01-01T10:00:00Z"}
        ])
        alert_schedule_create(
            event_id=str(slots[0].pk),
            message_id=str(msg.pk),
            alert_datetime="2026-12-01T09:00:00Z",
        )
        resp = self._delete(message_id=str(msg.pk))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "MESSAGE_IN_USE")
        # Message must NOT have been deleted.
        self.assertTrue(GovStackMessage.objects.filter(pk=msg.pk).exists())

    def test_msg21_delete_succeeds_after_referencing_alert_schedule_removed(self):
        msg = _create_message()
        slots = event_create(name="Test Event 2", slots=[
            {"from": "2027-01-02T09:00:00Z", "to": "2027-01-02T10:00:00Z"}
        ])
        alert_schedule = alert_schedule_create(
            event_id=str(slots[0].pk),
            message_id=str(msg.pk),
            alert_datetime="2026-12-02T09:00:00Z",
        )
        GovStackAlertSchedule.objects.filter(pk=alert_schedule.pk).delete()
        resp = self._delete(message_id=str(msg.pk))
        self.assertEqual(resp.status_code, 200)


# ===========================================================================
# MSG22-MSG28: GET /message/list_details
# ===========================================================================

class MessageListDetailsTests(MessageBaseTestCase):
    """MSG22-MSG28: GET /message/list_details"""

    def test_msg22_happy_path_no_filter_returns_all(self):
        _create_message(category="a")
        _create_message(category="b")
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(len(data["data"]), 2)

    def test_msg23_filter_by_entity_id(self):
        org = _create_org()
        msg = _create_message(entity_id=org.pk)
        _create_message()  # different org
        resp = self._get({"message_filter": {"entity_id": str(org.pk)}})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["message_id"], str(msg.pk))

    def test_msg24_filter_by_category(self):
        _create_message(category="unique-cat-24")
        resp = self._get({"message_filter": {"category": "unique-cat-24"}})
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["details"]["category"], "unique-cat-24")

    def test_msg25_filter_by_message_id(self):
        msg = _create_message()
        _create_message()
        resp = self._get({"message_filter": {"message_id": str(msg.pk)}})
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["message_id"], str(msg.pk))

    def test_msg25b_filter_by_message_id_array_matches_multiple(self):
        """
        Finding #4 fix: message_id is array-typed per the real spec
        (message_id[]). A JSON array of ids in the qry filter must match ANY
        of them (pk__in), not just a single exact value.
        """
        msg1 = _create_message()
        msg2 = _create_message()
        _create_message()  # not included in the filter — must be excluded
        resp = self._get({"message_filter": {"message_id": [str(msg1.pk), str(msg2.pk)]}})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(
            {item["message_id"] for item in data},
            {str(msg1.pk), str(msg2.pk)},
        )

    def test_msg26_response_shape_message_id_and_details(self):
        msg = _create_message(category="shape-test", message_body="body text")
        resp = self._get({"message_filter": {"message_id": str(msg.pk)}})
        item = resp.json()["data"][0]
        self.assertIn("message_id", item)
        self.assertIn("details", item)
        self.assertEqual(item["details"]["entity_id"], str(msg.entity_id))
        self.assertEqual(item["details"]["category"], "shape-test")
        # message_body defaults to NOT required — excluded unless explicitly requested.
        self.assertNotIn("message_body", item["details"])

    def test_msg27_message_body_included_when_required_flag_true(self):
        msg = _create_message(message_body="secret template text")
        resp = self._get({
            "message_filter": {"message_id": str(msg.pk)},
            "message_details_required": {"message_body": True},
        })
        item = resp.json()["data"][0]
        self.assertEqual(item["details"]["message_body"], "secret template text")

    def test_msg28_no_matches_returns_empty_list(self):
        resp = self._get({"message_filter": {"category": "definitely-does-not-exist"}})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"], [])


# ===========================================================================
# MSG29-MSG31: Auth / role enforcement
# ===========================================================================

@override_settings(GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True)
class MessageRoleEnforcementTests(MessageBaseTestCase):
    """
    MSG29-MSG34: message endpoints require gs_actor_role="organizer" or
    higher. A role="resource" BB (below organizer in the role hierarchy —
    subscriber < resource < organizer < admin) must be denied (403); a
    role="admin" BB (above organizer) must be allowed through.

    MSG32/MSG33 close a coverage gap identified by the Wave F adversarial
    review: PUT /message/modifications and DELETE /message previously had
    no test proving role enforcement applies to them at all (only POST /new
    and GET /list_details were covered).
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

    def test_msg29_resource_role_denied_on_message_new(self):
        self._make_role_bb("resource")
        org = _create_org()
        qry = {"message_details": {"entity_id": str(org.pk)}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 403)

    def test_msg30_admin_role_allowed_on_message_new(self):
        self._make_role_bb("admin")
        org = _create_org()
        qry = {"message_details": {"entity_id": str(org.pk)}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 201)

    def test_msg31_organizer_role_allowed_on_message_list(self):
        self._make_role_bb("organizer")
        resp = self._get()
        self.assertEqual(resp.status_code, 200)

    def test_msg32_resource_role_denied_on_message_modifications(self):
        self._make_role_bb("resource")
        msg = _create_message(category="old")
        resp = self._put({"details": {"category": "new"}}, message_id=str(msg.pk))
        self.assertEqual(resp.status_code, 403)

    def test_msg33_resource_role_denied_on_message_delete(self):
        self._make_role_bb("resource")
        msg = _create_message()
        resp = self._delete(message_id=str(msg.pk))
        self.assertEqual(resp.status_code, 403)


# ===========================================================================
# MSG34: spec-literal wire format (the `qry` query PARAMETER's JSON value,
# single-nested — no additional outer wrapper key)
# ===========================================================================

class MessageSpecWireFormatTests(MessageBaseTestCase):
    """
    MSG34: a POST /message/new body built EXACTLY per the real GovStack
    OpenAPI spec's message_new_qry schema — i.e. the `qry` query
    PARAMETER's JSON value is {"message_details": {...}}, single-nested,
    with no additional outer wrapper key — must succeed.

    This test intentionally does NOT reuse any shared payload-building
    helper: it hardcodes the wire-format dict inline so a future accidental
    regression — e.g. re-introducing this codebase's own past bug of
    double-wrapping the `qry` parameter's JSON value under an extra outer
    "qry" key — fails loudly here, independent of any other test in this
    module.
    """

    def test_msg34_spec_literal_message_new_payload_succeeds(self):
        org = _create_org()
        spec_literal_qry = {
            "message_details": {
                "entity_id": str(org.pk),
                "category": "reminder",
                "message_body": "Your appointment is tomorrow.",
            }
        }
        resp = self._post(spec_literal_qry)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(GovStackMessage.objects.filter(pk=data["message_id"]).exists())
