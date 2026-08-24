"""
GovStack Scheduler BB — Entity endpoints test suite.

Covers 4 endpoints:
  POST   /govstack/scheduler/entity/new
  PUT    /govstack/scheduler/entity/modifications
  DELETE /govstack/scheduler/entity
  GET    /govstack/scheduler/entity/list_details

Written as part of the final certifiability review (FIX 5a): Entity was one
of 3 of 9 API groups (13 of 37 endpoints) with zero dedicated tests before
this file. Structural template and _AUTH/_qs/_qry_qs helper pattern copied
from test_govstack_message.py / test_govstack_alert_schedule.py; auth/role
enforcement pattern copied from test_govstack_log.py (the group with the
strongest existing auth coverage). Test numbering: E1-Exx.

Entity /new correctly uses the generic "details" qry wrapper key already
(verified directly against the fetched real GovStack OpenAPI spec's
entity_new_qry schema — Entity was NOT one of the 3 groups affected by
FIX 1's wrong-wrapper-key bug; that bug was specific to Resource, Subscriber,
and Affiliation). E1 below pins this correct, pre-existing contract.
"""

from __future__ import annotations

import json
from urllib.parse import urlencode

from django.test import TestCase, override_settings

from apps.appointments.models import GovStackBBCredential, Organization
from apps.appointments.services.govstack_entity import entity_create
from apps.payments.govstack_models import GovStackRegisteredBB

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEW_URL = "/govstack/scheduler/entity/new"
MODIFICATIONS_URL = "/govstack/scheduler/entity/modifications"
LIST_URL = "/govstack/scheduler/entity/list_details"
DELETE_URL = "/govstack/scheduler/entity"

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


def _create_entity(name="Test Entity", category="health", phone="", email="", website=""):
    return entity_create(name=name, category=category, phone=phone, email=email, website=website)


# ===========================================================================
# Base test case
# ===========================================================================


class EntityBaseTestCase(TestCase):
    """Shared HTTP helpers for all entity endpoint tests."""

    def _post(self, qry_dict):
        return self.client.post(NEW_URL + _qry_qs(qry_dict))

    def _put(self, qry_dict, entity_id=None):
        extra = {"entity_id": entity_id} if entity_id is not None else {}
        return self.client.put(MODIFICATIONS_URL + _qry_qs(qry_dict, **extra))

    def _delete(self, entity_id=None):
        extra = {"entity_id": entity_id} if entity_id is not None else {}
        return self.client.delete(DELETE_URL + _qs(**extra))

    def _get(self, qry_dict=None):
        if qry_dict is None:
            return self.client.get(LIST_URL + _qs())
        return self.client.get(LIST_URL + _qry_qs(qry_dict))


# ===========================================================================
# E1-E7: POST /entity/new
# ===========================================================================


class EntityNewTests(EntityBaseTestCase):
    """E1-E7: POST /entity/new"""

    def test_e1_spec_literal_wire_format_succeeds(self):
        """E1: {"details": {...}} — the real spec's entity_new_qry shape (the
        `qry` query PARAMETER's JSON value, single-nested) — succeeds. The
        double-nested {"qry": {"details": {...}}} shape used before was this
        codebase's own bug, now fixed."""
        qry = {
            "details": {
                "name": "Ministry of Health",
                "category": "health",
                "phone": "+15005550001",
                "email": "moh@example.gov",
                "website": "https://moh.example.gov",
            }
        }
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        org = Organization.objects.get(pk=data["entity_id"])
        self.assertEqual(org.name_en, "Ministry of Health")
        self.assertEqual(org.phone, "+15005550001")
        self.assertEqual(org.email, "moh@example.gov")
        self.assertEqual(org.website, "https://moh.example.gov")
        self.assertTrue(org.is_active)

    def test_e1b_creates_admin_audit_event(self):
        """
        Round 2 certifiability re-audit fix (MEDIUM): POST /entity/new must
        leave a real, queryable, tamper-evident BookingAuditLog entry
        (booking=None) recording who created the entity and when.
        """
        from apps.appointments.models import BookingAuditLog

        qry = {"details": {"name": "Audit Trail Ministry", "category": "health"}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 200)
        entity_id = resp.json()["entity_id"]

        event = BookingAuditLog.objects.filter(
            action=BookingAuditLog.ACTION_ADMIN_ENTITY_MUTATED,
            detail__resource_pk=entity_id,
        ).first()
        self.assertIsNotNone(event)
        self.assertIsNone(event.booking)
        self.assertEqual(event.detail["operation"], "create")
        # _AUTH["requestor_id"] == "test-bb" (see module constants above).
        self.assertEqual(event.actor_id, "test-bb")

    def test_e2_missing_qry_returns_400(self):
        resp = self.client.post(NEW_URL + _qs())
        self.assertEqual(resp.status_code, 400)

    def test_e3_invalid_json_qry_returns_400(self):
        params = urlencode({**_AUTH, "qry": "{not valid json"})
        resp = self.client.post(NEW_URL + "?" + params)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "INVALID_QRY")

    def test_e4_blank_details_still_creates_entity(self):
        """E4: all fields optional per the loose GovStack string typing — blank name is allowed."""
        resp = self._post({"details": {}})
        self.assertEqual(resp.status_code, 200)

    def test_e5_category_maps_to_organization_type(self):
        resp = self._post({"details": {"name": "Federal Dept", "category": "federal"}})
        self.assertEqual(resp.status_code, 200)
        org = Organization.objects.get(pk=resp.json()["entity_id"])
        self.assertEqual(org.organization_type, "government_federal")

    def test_e6_duplicate_name_still_succeeds_via_slug_suffix(self):
        """E6: entity_create() generates a unique slug even for a duplicate name (no 400)."""
        self._post({"details": {"name": "Duplicate Org"}})
        resp = self._post({"details": {"name": "Duplicate Org"}})
        self.assertEqual(resp.status_code, 200)

    def test_e7_missing_auth_params_returns_401_or_403(self):
        qry = json.dumps({"details": {"name": "No Auth"}})
        resp = self.client.post(NEW_URL + f"?qry={qry}")
        self.assertIn(resp.status_code, (401, 403))


# ===========================================================================
# E8-E14: PUT /entity/modifications
# ===========================================================================


class EntityModificationsTests(EntityBaseTestCase):
    """E8-E14: PUT /entity/modifications"""

    def test_e8_happy_path_updates_name(self):
        org = _create_entity(name="Old Name")
        resp = self._put({"details": {"name": "New Name"}}, entity_id=org.pk)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["entity_id"], str(org.pk))
        org.refresh_from_db()
        self.assertEqual(org.name_en, "New Name")

    def test_e9_updates_phone_email_website(self):
        org = _create_entity()
        resp = self._put(
            {
                "details": {
                    "phone": "+15005559999",
                    "email": "new@example.gov",
                    "website": "https://new.example.gov",
                }
            },
            entity_id=org.pk,
        )
        self.assertEqual(resp.status_code, 200)
        org.refresh_from_db()
        self.assertEqual(org.phone, "+15005559999")
        self.assertEqual(org.email, "new@example.gov")
        self.assertEqual(org.website, "https://new.example.gov")

    def test_e10_missing_entity_id_returns_400(self):
        resp = self.client.put(MODIFICATIONS_URL + _qry_qs({"details": {"name": "X"}}))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "MISSING_ENTITY_ID")

    def test_e11_malformed_entity_id_returns_400(self):
        resp = self._put({"details": {"name": "X"}}, entity_id="abc")
        self.assertEqual(resp.status_code, 400)

    def test_e12_nonexistent_entity_id_returns_404(self):
        resp = self._put({"details": {"name": "X"}}, entity_id=999999)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "ENTITY_NOT_FOUND")

    def test_e13_no_fields_supplied_is_a_noop_success(self):
        org = _create_entity(name="Unchanged")
        resp = self._put({"details": {}}, entity_id=org.pk)
        self.assertEqual(resp.status_code, 200)
        org.refresh_from_db()
        self.assertEqual(org.name_en, "Unchanged")

    def test_e14_missing_auth_params_returns_401_or_403(self):
        org = _create_entity()
        params = urlencode({"entity_id": org.pk, "qry": json.dumps({"details": {"name": "X"}})})
        resp = self.client.put(MODIFICATIONS_URL + "?" + params)
        self.assertIn(resp.status_code, (401, 403))


# ===========================================================================
# E15-E20: DELETE /entity
# ===========================================================================


class EntityDeleteTests(EntityBaseTestCase):
    """E15-E20: DELETE /entity"""

    def test_e15_happy_path_soft_deletes_entity(self):
        org = _create_entity()
        resp = self._delete(entity_id=org.pk)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["entity_id"], str(org.pk))
        org.refresh_from_db()
        self.assertFalse(org.is_active)

    def test_e16_missing_entity_id_returns_400(self):
        resp = self._delete()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "MISSING_ENTITY_ID")

    def test_e17_nonexistent_entity_id_returns_404(self):
        resp = self._delete(entity_id=999999)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "ENTITY_NOT_FOUND")

    def test_e18_malformed_entity_id_returns_400(self):
        resp = self._delete(entity_id="not-a-number")
        self.assertEqual(resp.status_code, 400)

    def test_e19_row_still_exists_after_soft_delete(self):
        org = _create_entity()
        self._delete(entity_id=org.pk)
        self.assertTrue(Organization.objects.filter(pk=org.pk).exists())

    def test_e20_missing_auth_params_returns_401_or_403(self):
        org = _create_entity()
        resp = self.client.delete(DELETE_URL + f"?entity_id={org.pk}")
        self.assertIn(resp.status_code, (401, 403))


# ===========================================================================
# E21-E28: GET /entity/list_details
# ===========================================================================


class EntityListDetailsTests(EntityBaseTestCase):
    """E21-E28: GET /entity/list_details"""

    def test_e21_happy_path_no_filter_returns_all(self):
        """
        Bug 2 fix: the response body is now a bare JSON array (matches the
        real GovStack OpenAPI spec's entity_list schema exactly) — no more
        {"status": "success", "data": [...], "truncated": ...} wrapper.
        """
        _create_entity(name="A")
        _create_entity(name="B")
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 2)

    def test_e22_filter_by_category(self):
        _create_entity(name="Health Dept", category="health")
        _create_entity(name="Legal Dept", category="legal")
        resp = self._get({"entity_filter": {"category": "health"}})
        data = resp.json()
        names = [r["name"] for r in data]
        self.assertIn("Health Dept", names)
        self.assertNotIn("Legal Dept", names)

    def test_e23_filter_by_entity_id(self):
        org = _create_entity()
        _create_entity()
        resp = self._get({"entity_filter": {"entity_id": str(org.pk)}})
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["entity_id"], str(org.pk))

    def test_e23b_filter_by_entity_id_array_matches_multiple(self):
        """
        Bug 1 fix: entity_id is array-typed per the real GovStack spec — a
        JSON array of 2+ ids returns all matching records (pk__in).
        """
        org1 = _create_entity(name="Array Match 1")
        org2 = _create_entity(name="Array Match 2")
        _create_entity(name="Not Matched")
        resp = self._get({"entity_filter": {"entity_id": [str(org1.pk), str(org2.pk)]}})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        returned_ids = {item["entity_id"] for item in data}
        self.assertEqual(returned_ids, {str(org1.pk), str(org2.pk)})

    def test_e23c_filter_by_entity_id_single_string_still_works(self):
        """Bug 1 fix: backward compatibility — a single bare-string entity_id still works."""
        org = _create_entity(name="Single String Filter")
        _create_entity(name="Other")
        resp = self._get({"entity_filter": {"entity_id": str(org.pk)}})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["entity_id"], str(org.pk))

    def test_e23d_filter_by_entity_id_invalid_entry_returns_400(self):
        """
        Bug 1 fix: an invalid/non-numeric id in the array is handled the same
        way the reference implementation (alert_schedule_id/message_id) does
        — the malformed pk__in lookup raises, and the view's generic
        exception handler maps it to a 400.
        """
        org = _create_entity()
        resp = self._get({"entity_filter": {"entity_id": [str(org.pk), "not-a-number"]}})
        self.assertEqual(resp.status_code, 400)

    def test_e24_filter_by_name(self):
        _create_entity(name="Unique Name 24")
        resp = self._get({"entity_filter": {"name": "Unique Name 24"}})
        data = resp.json()
        self.assertEqual(len(data), 1)

    def test_e25_response_excludes_phone_email_website_by_default(self):
        org = _create_entity(phone="+15005550000", email="hide@example.gov")
        resp = self._get({"entity_filter": {"entity_id": str(org.pk)}})
        item = resp.json()[0]
        self.assertNotIn("phone", item)
        self.assertNotIn("email", item)
        self.assertNotIn("website", item)

    def test_e26_phone_included_when_required_flag_true(self):
        org = _create_entity(phone="+15005550042")
        resp = self._get(
            {
                "entity_filter": {"entity_id": str(org.pk)},
                "entity_details_required": {"phone": True},
            }
        )
        item = resp.json()[0]
        self.assertEqual(item["phone"], "+15005550042")

    def test_e27_no_matches_returns_empty_list(self):
        resp = self._get({"entity_filter": {"category": "definitely-does-not-exist"}})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_e28_missing_auth_params_returns_401_or_403(self):
        resp = self.client.get(LIST_URL)
        self.assertIn(resp.status_code, (401, 403))


# ===========================================================================
# E29-E36: Auth / role enforcement
# ===========================================================================


@override_settings(GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True)
class EntityRoleEnforcementTests(EntityBaseTestCase):
    """
    E29-E36: all 4 Entity endpoints require gs_actor_role="admin" — a
    role="organizer" BB (below admin in the role hierarchy — subscriber <
    resource < organizer < admin) must be denied (403) on every endpoint; a
    role="admin" BB must be allowed through. Matches the exact pattern used
    in test_govstack_log.py's LogRoleEnforcementTests (Log is also
    admin-only across all 4 endpoints).
    """

    def _make_role_bb(self, role):
        """
        Create a GovStackRegisteredBB (identity, bb_id=_AUTH['requestor_id']) PLUS a
        real GovStackBBCredential for it, and point _AUTH['request_token'] at the
        correct plaintext secret. Finding #1 fix: request_token must never equal
        bb_id — it must verify against a separate hashed secret.
        """
        bb = GovStackRegisteredBB.objects.create(
            bb_id=_AUTH["requestor_id"], is_active=True, role=role
        )
        token = GovStackBBCredential.generate_plaintext_token()
        credential = GovStackBBCredential(bb=bb)
        credential.set_token(token)
        credential.save()
        _AUTH["request_token"] = token
        self.addCleanup(lambda: _AUTH.update(request_token="test-token"))
        return bb

    def test_e29_organizer_role_denied_on_entity_new(self):
        self._make_role_bb("organizer")
        resp = self._post({"details": {"name": "Denied Org"}})
        self.assertEqual(resp.status_code, 403)

    def test_e30_admin_role_allowed_on_entity_new(self):
        self._make_role_bb("admin")
        resp = self._post({"details": {"name": "Allowed Org"}})
        self.assertEqual(resp.status_code, 200)

    def test_e31_organizer_role_denied_on_entity_modifications(self):
        self._make_role_bb("organizer")
        org = _create_entity()
        resp = self._put({"details": {"name": "X"}}, entity_id=org.pk)
        self.assertEqual(resp.status_code, 403)

    def test_e32_admin_role_allowed_on_entity_modifications(self):
        self._make_role_bb("admin")
        org = _create_entity()
        resp = self._put({"details": {"name": "X"}}, entity_id=org.pk)
        self.assertEqual(resp.status_code, 200)

    def test_e33_organizer_role_denied_on_entity_delete(self):
        self._make_role_bb("organizer")
        org = _create_entity()
        resp = self._delete(entity_id=org.pk)
        self.assertEqual(resp.status_code, 403)

    def test_e34_admin_role_allowed_on_entity_delete(self):
        self._make_role_bb("admin")
        org = _create_entity()
        resp = self._delete(entity_id=org.pk)
        self.assertEqual(resp.status_code, 200)

    def test_e35_organizer_role_denied_on_entity_list(self):
        self._make_role_bb("organizer")
        resp = self._get()
        self.assertEqual(resp.status_code, 403)

    def test_e36_admin_role_allowed_on_entity_list(self):
        self._make_role_bb("admin")
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
