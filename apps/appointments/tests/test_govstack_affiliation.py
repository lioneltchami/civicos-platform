"""
GovStack Scheduler BB — Affiliation endpoints test suite.

Covers 4 endpoints:
  POST   /govstack/scheduler/affiliation/new
  PUT    /govstack/scheduler/affiliation/modifications
  DELETE /govstack/scheduler/affiliation
  GET    /govstack/scheduler/affiliation/list_details

Written as part of the final certifiability review (FIX 5a): Affiliation was
one of 3 of 9 API groups (13 of 37 endpoints) with zero dedicated tests
before this file. Structural template and _AUTH/_qs/_qry_qs helper pattern
copied from test_govstack_message.py / test_govstack_alert_schedule.py;
auth/role enforcement pattern copied from test_govstack_log.py. Test
numbering: AFF1-AFFxx.

AFF1 below locks in FIX 1 (the wrong "details" inner key — real spec key is
"affiliation_details"). AFF20/AFF21 lock in FIX 2 (the wrong
"resource_category" filter/details_required field name — real spec key is
"category" — plus the previously entirely-missing from/to date-range filter).

Note: a separate, unrelated bug — this codebase double-wrapping the `qry`
query PARAMETER's JSON value under an extra outer "qry" key — was fixed
later. All payloads below use the correct single-nested shape, e.g.
{"affiliation_details": {...}} rather than {"qry": {"affiliation_details":
{...}}}.
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from django.test import TestCase, override_settings
from django.utils.dateparse import parse_datetime

from apps.appointments.models import GovStackAffiliation, GovStackBBCredential
from apps.appointments.services.govstack_affiliation import affiliation_create
from apps.appointments.services.govstack_entity import entity_create
from apps.appointments.services.govstack_resource import resource_create
from apps.payments.govstack_models import GovStackRegisteredBB

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEW_URL = "/govstack/scheduler/affiliation/new"
MODIFICATIONS_URL = "/govstack/scheduler/affiliation/modifications"
LIST_URL = "/govstack/scheduler/affiliation/list_details"
DELETE_URL = "/govstack/scheduler/affiliation"

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


def _create_entity(name="Test Entity"):
    return entity_create(name=name, category="health")


def _create_resource(name="Test Resource", category="room"):
    return resource_create(name=name, category=category)


def _create_affiliation(resource=None, entity=None, resource_category="nurse", work_days_hours=None):
    resource = resource or _create_resource()
    entity = entity or _create_entity()
    return affiliation_create(
        resource_id=resource.pk,
        entity_id=entity.pk,
        resource_category=resource_category,
        work_days_hours=work_days_hours,
    )


# ===========================================================================
# Base test case
# ===========================================================================

class AffiliationBaseTestCase(TestCase):
    """Shared HTTP helpers for all affiliation endpoint tests."""

    def _post(self, qry_dict):
        return self.client.post(NEW_URL + _qry_qs(qry_dict))

    def _put(self, qry_dict, affiliation_id=None):
        extra = {"affiliation_id": affiliation_id} if affiliation_id is not None else {}
        return self.client.put(MODIFICATIONS_URL + _qry_qs(qry_dict, **extra))

    def _delete(self, affiliation_id=None):
        extra = {"affiliation_id": affiliation_id} if affiliation_id is not None else {}
        return self.client.delete(DELETE_URL + _qs(**extra))

    def _get(self, qry_dict=None):
        if qry_dict is None:
            return self.client.get(LIST_URL + _qs())
        return self.client.get(LIST_URL + _qry_qs(qry_dict))


# ===========================================================================
# AFF1-AFF9: POST /affiliation/new
# ===========================================================================

class AffiliationNewTests(AffiliationBaseTestCase):
    """AFF1-AFF9: POST /affiliation/new"""

    def test_aff1_spec_literal_wire_format_succeeds(self):
        """AFF1 (locks in FIX 1): the real spec's affiliation_new_qry shape,
        {"affiliation_details": {...}} (single-nested — this is the JSON
        value of the `qry` query PARAMETER itself), must succeed."""
        resource = _create_resource()
        entity = _create_entity()
        qry = {"affiliation_details": {
            "resource_id": str(resource.pk),
            "entity_id": str(entity.pk),
            "resource_category": "nurse",
            "work_days_hours": {"monday": {"from": "09:00", "to": "17:00"}},
        }}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        aff = GovStackAffiliation.objects.get(pk=data["affiliation_id"])
        self.assertEqual(aff.resource_id, resource.pk)
        self.assertEqual(aff.entity_id, entity.pk)
        self.assertEqual(aff.resource_category, "nurse")

    def test_aff2_old_wrong_details_key_returns_400(self):
        """AFF2: the OLD (pre-FIX-1) wrapper key "details" is no longer
        recognised — the required "affiliation_details" inner field is
        missing, so validation fails with 400."""
        resource = _create_resource()
        entity = _create_entity()
        qry = {"details": {"resource_id": str(resource.pk), "entity_id": str(entity.pk)}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)

    def test_aff3_missing_resource_id_returns_400(self):
        entity = _create_entity()
        qry = {"affiliation_details": {"entity_id": str(entity.pk)}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "MISSING_RESOURCE_ID")

    def test_aff4_missing_entity_id_returns_400(self):
        resource = _create_resource()
        qry = {"affiliation_details": {"resource_id": str(resource.pk)}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "MISSING_ENTITY_ID")

    def test_aff5_nonexistent_resource_id_returns_404(self):
        entity = _create_entity()
        qry = {"affiliation_details": {"resource_id": "999999", "entity_id": str(entity.pk)}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "RESOURCE_NOT_FOUND")

    def test_aff6_nonexistent_entity_id_returns_404(self):
        resource = _create_resource()
        qry = {"affiliation_details": {"resource_id": str(resource.pk), "entity_id": "999999"}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "ENTITY_NOT_FOUND")

    def test_aff7_duplicate_pair_returns_409(self):
        resource = _create_resource()
        entity = _create_entity()
        _create_affiliation(resource=resource, entity=entity)
        qry = {"affiliation_details": {"resource_id": str(resource.pk), "entity_id": str(entity.pk)}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["code"], "DUPLICATE_AFFILIATION")

    def test_aff8_missing_qry_returns_400(self):
        resp = self.client.post(NEW_URL + _qs())
        self.assertEqual(resp.status_code, 400)

    def test_aff9_missing_auth_params_returns_401_or_403(self):
        resource = _create_resource()
        entity = _create_entity()
        qry = json.dumps({"affiliation_details": {
            "resource_id": str(resource.pk), "entity_id": str(entity.pk),
        }})
        resp = self.client.post(NEW_URL + f"?qry={qry}")
        self.assertIn(resp.status_code, (401, 403))

    # ---------------------------------------------------------------------
    # Bug 4a: resource_id "R-" prefix handling (MASTER_BB_CERTIFIABILITY_REPORT.md)
    # ---------------------------------------------------------------------

    def test_aff38_well_formed_r_prefixed_resource_id_succeeds(self):
        """
        AFF38 (Bug 4a regression-proof): a well-formed "R-<pk>" resource_id —
        the canonical externally-visible form used everywhere else in this
        BB (e.g. /resource/list_details, /resource/new) — must be accepted
        and correctly stripped/resolved, not misinterpreted as malformed.
        """
        resource = _create_resource()
        entity = _create_entity()
        qry = {"affiliation_details": {
            "resource_id": f"R-{resource.pk}", "entity_id": str(entity.pk),
        }}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        aff = GovStackAffiliation.objects.get(pk=data["affiliation_id"])
        self.assertEqual(aff.resource_id, resource.pk)

    def test_aff39_s_prefixed_resource_id_returns_400_not_409(self):
        """
        AFF39 (Bug 4a): an "S-" prefixed resource_id (StaffProfile id form) is
        rejected with a clean 400 — Affiliation.resource is a Resource FK and
        NEVER links to StaffProfile — never a leaked exception, never 409.
        """
        entity = _create_entity()
        qry = {"affiliation_details": {"resource_id": "S-1", "entity_id": str(entity.pk)}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertNotEqual(data.get("code"), "DUPLICATE_AFFILIATION")
        self.assertEqual(data["code"], "INVALID_RESOURCE_ID")

    def test_aff40_non_numeric_resource_id_returns_400_not_409(self):
        """
        AFF40 (Bug 4a): a non-numeric, non-"R-"-prefixed resource_id like
        "R-abc" must return a clean 400 (bad format) — never a raw Django
        ValueError string leaked into a 409 DUPLICATE_AFFILIATION body (the
        original bug: any ValueError from affiliation_create was
        unconditionally interpreted as a duplicate-pair signal).
        """
        entity = _create_entity()
        qry = {"affiliation_details": {"resource_id": "R-abc", "entity_id": str(entity.pk)}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertNotEqual(resp.status_code, 409)
        self.assertNotIn("expected a number", data.get("message", ""))

    def test_aff41_r_prefixed_nonexistent_resource_id_returns_404_not_409(self):
        """
        AFF41 (Bug 4a): a well-formed but nonexistent "R-<pk>" resource_id
        must return 404 RESOURCE_NOT_FOUND — never 409.
        """
        entity = _create_entity()
        qry = {"affiliation_details": {"resource_id": "R-999999", "entity_id": str(entity.pk)}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "RESOURCE_NOT_FOUND")

    def test_aff42_r_prefixed_duplicate_pair_still_returns_409(self):
        """
        AFF42 (Bug 4a): a well-formed, EXISTING, active "R-<pk>" resource_id
        that already has an affiliation with the given entity must still
        correctly return 409 DUPLICATE_AFFILIATION — the fix must not
        over-correct and break the genuine duplicate-pair signal.
        """
        resource = _create_resource()
        entity = _create_entity()
        _create_affiliation(resource=resource, entity=entity)
        qry = {"affiliation_details": {
            "resource_id": f"R-{resource.pk}", "entity_id": str(entity.pk),
        }}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["code"], "DUPLICATE_AFFILIATION")

    def test_aff43_bare_int_resource_id_still_succeeds(self):
        """
        AFF43 (Bug 4a — backward compat): a bare, non-prefixed integer
        resource_id must still be accepted (matching _parse_resource_only_pk's
        established backward-compatibility behaviour for the 3 Resource-only
        endpoints), not treated as malformed.
        """
        resource = _create_resource()
        entity = _create_entity()
        qry = {"affiliation_details": {
            "resource_id": str(resource.pk), "entity_id": str(entity.pk),
        }}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 200)


# ===========================================================================
# AFF10-AFF15: PUT /affiliation/modifications
# ===========================================================================

class AffiliationModificationsTests(AffiliationBaseTestCase):
    """AFF10-AFF15: PUT /affiliation/modifications (real spec key "details" — unchanged by FIX 1)"""

    def test_aff10_happy_path_updates_resource_category(self):
        aff = _create_affiliation(resource_category="old-cat")
        resp = self._put({"details": {"resource_category": "new-cat"}}, affiliation_id=aff.pk)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["affiliation_id"], str(aff.pk))
        aff.refresh_from_db()
        self.assertEqual(aff.resource_category, "new-cat")

    def test_aff11_updates_work_days_hours(self):
        aff = _create_affiliation()
        new_hours = {"tuesday": {"from": "08:00", "to": "16:00"}}
        resp = self._put({"details": {"work_days_hours": new_hours}}, affiliation_id=aff.pk)
        self.assertEqual(resp.status_code, 200)
        aff.refresh_from_db()
        self.assertEqual(aff.work_days_hours, new_hours)

    def test_aff12_missing_affiliation_id_returns_400(self):
        resp = self.client.put(MODIFICATIONS_URL + _qry_qs({"details": {"resource_category": "x"}}))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "MISSING_AFFILIATION_ID")

    def test_aff13_malformed_affiliation_id_returns_400(self):
        resp = self._put({"details": {"resource_category": "x"}}, affiliation_id="abc")
        self.assertEqual(resp.status_code, 400)

    def test_aff14_nonexistent_affiliation_id_returns_404(self):
        resp = self._put({"details": {"resource_category": "x"}}, affiliation_id=999999)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "AFFILIATION_NOT_FOUND")

    def test_aff15_missing_auth_params_returns_401_or_403(self):
        aff = _create_affiliation()
        params = urlencode({
            "affiliation_id": aff.pk,
            "qry": json.dumps({"details": {"resource_category": "x"}}),
        })
        resp = self.client.put(MODIFICATIONS_URL + "?" + params)
        self.assertIn(resp.status_code, (401, 403))


# ===========================================================================
# AFF16-AFF19: DELETE /affiliation
# ===========================================================================

class AffiliationDeleteTests(AffiliationBaseTestCase):
    """AFF16-AFF19: DELETE /affiliation"""

    def test_aff16_happy_path_hard_deletes_affiliation(self):
        aff = _create_affiliation()
        resp = self._delete(affiliation_id=aff.pk)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["affiliation_id"], str(aff.pk))
        self.assertFalse(GovStackAffiliation.objects.filter(pk=aff.pk).exists())

    def test_aff17_missing_affiliation_id_returns_400(self):
        resp = self._delete()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "MISSING_AFFILIATION_ID")

    def test_aff18_nonexistent_affiliation_id_returns_404(self):
        resp = self._delete(affiliation_id=999999)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "AFFILIATION_NOT_FOUND")

    def test_aff19_missing_auth_params_returns_401_or_403(self):
        aff = _create_affiliation()
        resp = self.client.delete(DELETE_URL + f"?affiliation_id={aff.pk}")
        self.assertIn(resp.status_code, (401, 403))


# ===========================================================================
# AFF20-AFF29: GET /affiliation/list_details (incl. FIX 2 proof)
# ===========================================================================

class AffiliationListDetailsTests(AffiliationBaseTestCase):
    """AFF20-AFF29: GET /affiliation/list_details"""

    def test_aff20_filter_by_category_field_name_works(self):
        """
        AFF20 (locks in FIX 2): the real spec's affiliation_filter field is
        literally "category" — NOT "resource_category". Before the fix,
        filtering by "category" was silently ignored (unknown key dropped by
        DRF); this proves it now actually filters.
        """
        aff = _create_affiliation(resource_category="unique-cat-20")
        _create_affiliation(resource_category="something-else")
        resp = self._get({"affiliation_filter": {"category": "unique-cat-20"}})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["affiliation_id"], str(aff.pk))
        # Response field itself is still "resource_category" (unchanged).
        self.assertEqual(data[0]["resource_category"], "unique-cat-20")

    def test_aff21_filter_by_from_to_datetime_range(self):
        """
        AFF21 (locks in FIX 2): affiliation_filter.from/to — entirely absent
        before the fix — must both include an in-range row AND exclude an
        out-of-range row.
        """
        in_range = _create_affiliation(resource_category="in-range-21")
        out_of_range = _create_affiliation(resource_category="out-of-range-21")
        GovStackAffiliation.objects.filter(pk=out_of_range.pk).update(
            created_at=parse_datetime("2020-01-01T00:00:00Z")
        )
        resp = self._get({"affiliation_filter": {
            "from": "2025-01-01T00:00:00Z", "to": "2099-12-31T00:00:00Z",
        }})
        self.assertEqual(resp.status_code, 200)
        ids = [item["affiliation_id"] for item in resp.json()]
        self.assertIn(str(in_range.pk), ids)
        self.assertNotIn(str(out_of_range.pk), ids)

    def test_aff22_details_required_category_flag_gates_resource_category_field(self):
        """AFF22: the "category" details_required flag (renamed from
        "resource_category" by FIX 2) still gates the response's
        "resource_category" field."""
        aff = _create_affiliation(resource_category="flag-test")
        resp = self._get({
            "affiliation_filter": {"affiliation_id": str(aff.pk)},
            "affiliation_details_required": {"category": False},
        })
        item = resp.json()[0]
        self.assertNotIn("resource_category", item)

    def test_aff23_happy_path_no_filter_returns_all(self):
        """
        AFF23: response body is a bare JSON array (Bug 2 fix, a sibling
        agent's change — see AffiliationListDetailsView docstring in
        govstack_views.py: the previous {"status": "success", "data": [...],
        "truncated": ...} wrapper was not part of the real GovStack spec and
        has been removed).
        """
        _create_affiliation()
        _create_affiliation()
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 2)

    def test_aff24_filter_by_affiliation_id(self):
        aff = _create_affiliation()
        _create_affiliation()
        resp = self._get({"affiliation_filter": {"affiliation_id": str(aff.pk)}})
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["affiliation_id"], str(aff.pk))

    def test_aff25_filter_by_entity_id(self):
        entity = _create_entity()
        aff = _create_affiliation(entity=entity)
        _create_affiliation()  # different entity
        resp = self._get({"affiliation_filter": {"entity_id": str(entity.pk)}})
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["affiliation_id"], str(aff.pk))

    def test_aff26_filter_by_resource_id(self):
        resource = _create_resource()
        aff = _create_affiliation(resource=resource)
        _create_affiliation()  # different resource
        resp = self._get({"affiliation_filter": {"resource_id": str(resource.pk)}})
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["affiliation_id"], str(aff.pk))

    def test_aff27_work_days_hours_excluded_by_default(self):
        aff = _create_affiliation(work_days_hours={"monday": {"from": "09:00", "to": "17:00"}})
        resp = self._get({"affiliation_filter": {"affiliation_id": str(aff.pk)}})
        item = resp.json()[0]
        self.assertNotIn("work_days_hours", item)

    def test_aff28_no_matches_returns_empty_list(self):
        resp = self._get({"affiliation_filter": {"category": "definitely-does-not-exist"}})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_aff29_missing_auth_params_returns_401_or_403(self):
        resp = self.client.get(LIST_URL)
        self.assertIn(resp.status_code, (401, 403))

    def test_aff44_resource_id_emitted_in_r_prefixed_form(self):
        """
        AFF44 (Bug 4b): affiliation_list()'s "resource_id" field must be
        emitted in the canonical "R-<pk>" externally-visible form — matching
        the exact pattern used by services.govstack_resource.resource_list()
        — not the bare FK integer. Round-tripping this value straight back
        into POST /affiliation/new's resource_id (which now expects "R-<pk>",
        see Bug 4a) must work without the caller needing to manually add the
        prefix themselves.
        """
        resource = _create_resource()
        aff = _create_affiliation(resource=resource)
        resp = self._get({"affiliation_filter": {"affiliation_id": str(aff.pk)}})
        self.assertEqual(resp.status_code, 200)
        item = resp.json()[0]
        self.assertEqual(item["resource_id"], f"R-{resource.pk}")


# ===========================================================================
# AFF30-AFF37: Auth / role enforcement
# ===========================================================================

@override_settings(GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True)
class AffiliationRoleEnforcementTests(AffiliationBaseTestCase):
    """
    AFF30-AFF37: all 4 Affiliation endpoints require gs_actor_role="admin" —
    a role="organizer" BB (below admin in the role hierarchy — subscriber <
    resource < organizer < admin) must be denied (403) on every endpoint; a
    role="admin" BB must be allowed through. Matches the exact
    role-enforcement pattern used in test_govstack_log.py.
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

    def test_aff30_organizer_role_denied_on_affiliation_new(self):
        self._make_role_bb("organizer")
        resource = _create_resource()
        entity = _create_entity()
        qry = {"affiliation_details": {
            "resource_id": str(resource.pk), "entity_id": str(entity.pk),
        }}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 403)

    def test_aff31_admin_role_allowed_on_affiliation_new(self):
        self._make_role_bb("admin")
        resource = _create_resource()
        entity = _create_entity()
        qry = {"affiliation_details": {
            "resource_id": str(resource.pk), "entity_id": str(entity.pk),
        }}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 200)

    def test_aff32_organizer_role_denied_on_affiliation_modifications(self):
        self._make_role_bb("organizer")
        aff = _create_affiliation()
        resp = self._put({"details": {"resource_category": "x"}}, affiliation_id=aff.pk)
        self.assertEqual(resp.status_code, 403)

    def test_aff33_admin_role_allowed_on_affiliation_modifications(self):
        self._make_role_bb("admin")
        aff = _create_affiliation()
        resp = self._put({"details": {"resource_category": "x"}}, affiliation_id=aff.pk)
        self.assertEqual(resp.status_code, 200)

    def test_aff34_organizer_role_denied_on_affiliation_delete(self):
        self._make_role_bb("organizer")
        aff = _create_affiliation()
        resp = self._delete(affiliation_id=aff.pk)
        self.assertEqual(resp.status_code, 403)

    def test_aff35_admin_role_allowed_on_affiliation_delete(self):
        self._make_role_bb("admin")
        aff = _create_affiliation()
        resp = self._delete(affiliation_id=aff.pk)
        self.assertEqual(resp.status_code, 200)

    def test_aff36_organizer_role_denied_on_affiliation_list(self):
        self._make_role_bb("organizer")
        resp = self._get()
        self.assertEqual(resp.status_code, 403)

    def test_aff37_admin_role_allowed_on_affiliation_list(self):
        self._make_role_bb("admin")
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
