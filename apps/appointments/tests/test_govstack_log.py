"""
GovStack Scheduler BB — Log endpoints test suite (Wave G — FINAL wave).

Covers 4 endpoints:
  POST   /govstack/scheduler/log/new
  PUT    /govstack/scheduler/log/modifications   (always 405 — audit immutability)
  DELETE /govstack/scheduler/log                 (always 405 — audit immutability)
  GET    /govstack/scheduler/log/list_details

Test numbering: LOG1-LOGxx, following the AS/MSG numbering convention used
in test_govstack_alert_schedule.py / test_govstack_message.py.
"""

from __future__ import annotations

import json
import uuid
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.appointments.models import Booking, BookingAuditLog, GovStackBBCredential, Organization
from apps.appointments.services.govstack_event import event_create
from apps.payments.govstack_models import GovStackRegisteredBB

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEW_URL = "/govstack/scheduler/log/new"
MODIFICATIONS_URL = "/govstack/scheduler/log/modifications"
LIST_URL = "/govstack/scheduler/log/list_details"
DELETE_URL = "/govstack/scheduler/log"

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


def _create_event_slot(host_entity_id="", **kwargs):
    """
    Factory: create a bookable Event (AppointmentType + Slot) via the Event
    service. When host_entity_id is supplied, a non-empty venue dict must
    also be passed — services.govstack_event._resolve_location() only
    honours host_entity_id when the venue has at least one non-blank field;
    otherwise it silently falls back to the shared GovStack-system location
    (and thus the shared GovStack-system org), ignoring host_entity_id
    entirely (identical precedent/rationale documented in
    test_govstack_alert_schedule.py's helper of the same name).
    """
    venue = {"city": "Testville"} if host_entity_id else None
    slots = event_create(
        name=kwargs.get("name", "Test Event"),
        host_entity_id=host_entity_id,
        slots=[kwargs.get("slot", {"from": "2027-05-01T09:00:00Z", "to": "2027-05-01T10:00:00Z"})],
        venue=venue,
    )
    return slots[0]


def _create_citizen(email=None):
    User = get_user_model()  # noqa: N806
    email = email or f"log-citizen-{uuid.uuid4().hex[:10]}@example.com"
    user = User.objects.create(email=email, is_staff=False, is_active=True)
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return user


def _create_booking(slot, citizen, status=Booking.STATUS_CONFIRMED):
    return Booking.objects.create(slot=slot, citizen=citizen, status=status)


def _log_data_for(slot, citizen, **extra_pairs):
    pairs = [f"event_id:{slot.pk}", f"subscriber_id:{citizen.pk}"]
    for key, value in extra_pairs.items():
        pairs.append(f"{key}:{value}")
    return ",".join(pairs)


# ===========================================================================
# Base test case
# ===========================================================================


class LogBaseTestCase(TestCase):
    """Shared HTTP helpers for all log endpoint tests."""

    def _post(self, qry_dict):
        return self.client.post(NEW_URL + _qry_qs(qry_dict))

    def _put(self):
        return self.client.put(MODIFICATIONS_URL + _qs())

    def _delete(self):
        return self.client.delete(DELETE_URL + _qs())

    def _get(self, qry_dict=None):
        if qry_dict is None:
            return self.client.get(LIST_URL + _qs())
        return self.client.get(LIST_URL + _qry_qs(qry_dict))

    def _make_booking(self, host_entity_id=""):
        org = _create_org() if not host_entity_id else Organization.objects.get(pk=host_entity_id)
        slot = _create_event_slot(host_entity_id=str(org.pk))
        citizen = _create_citizen()
        booking = _create_booking(slot, citizen)
        return booking, slot, citizen, org


# ===========================================================================
# LOG1-LOG14: POST /log/new
# ===========================================================================


class LogNewTests(LogBaseTestCase):
    """LOG1-LOG14: POST /log/new"""

    def test_log1_happy_path_creates_audit_log_entry(self):
        booking, slot, citizen, org = self._make_booking()
        log_data = _log_data_for(slot, citizen, token="abc", status="attended")
        qry = {
            "log_details": {
                "logger_role": "organizer",
                "logger_id": "42",
                "log_category": "attendance",
                "datetime": "2020-01-01T00:00:00Z",
                "log_data": log_data,
            }
        }
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")

        entry = BookingAuditLog.objects.get(pk=data["log_id"])
        self.assertEqual(entry.booking_id, booking.pk)
        self.assertEqual(entry.actor_role, "organizer")
        self.assertEqual(entry.actor_id, "42")
        self.assertEqual(entry.action, "attendance")
        self.assertEqual(entry.detail, {"log_data": log_data})

    def test_log2_spec_literal_wire_format_succeeds(self):
        """
        LOG2: a POST /log/new body built EXACTLY per the real GovStack
        OpenAPI spec's log_new_qry schema — i.e. the `qry` query
        PARAMETER's JSON value is {"log_details": {...}}, single-nested,
        with no additional outer wrapper key — must succeed. This test
        intentionally does NOT reuse any shared payload-building helper: it
        hardcodes the wire-format dict inline so a future accidental
        regression — e.g. re-introducing this codebase's own past bug of
        double-wrapping the `qry` parameter's JSON value under an extra
        outer "qry" key — fails loudly here, independent of any other test
        in this module.
        """
        booking, slot, citizen, org = self._make_booking()
        spec_literal_qry = {
            "log_details": {
                "logger_role": "admin",
                "logger_id": "1",
                "log_category": "attendance",
                "datetime": "2026-07-25T09:00:00Z",
                "log_data": f"event_id:{slot.pk},subscriber_id:{citizen.pk},token:a2s3x2fer,status:attended",  # noqa: E501
            }
        }
        resp = self._post(spec_literal_qry)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(BookingAuditLog.objects.filter(pk=data["log_id"]).exists())

    def test_log3_blank_log_data_returns_400(self):
        qry = {
            "log_details": {
                "logger_role": "admin",
                "log_category": "attendance",
                "log_data": "",
            }
        }
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "LOG_DATA_INVALID")

    def test_log4_log_data_missing_subscriber_id_returns_400(self):
        booking, slot, citizen, org = self._make_booking()
        qry = {
            "log_details": {
                "logger_role": "admin",
                "log_category": "attendance",
                "log_data": f"event_id:{slot.pk},token:abc",
            }
        }
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "LOG_DATA_INVALID")

    def test_log5_log_data_missing_event_id_returns_400(self):
        booking, slot, citizen, org = self._make_booking()
        qry = {
            "log_details": {
                "logger_role": "admin",
                "log_category": "attendance",
                "log_data": f"subscriber_id:{citizen.pk},token:abc",
            }
        }
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "LOG_DATA_INVALID")

    def test_log6_unparseable_log_data_returns_400(self):
        qry = {
            "log_details": {
                "logger_role": "admin",
                "log_category": "attendance",
                "log_data": "not a key value string at all",
            }
        }
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "LOG_DATA_INVALID")

    def test_log7_nonexistent_booking_returns_404(self):
        qry = {
            "log_details": {
                "logger_role": "admin",
                "log_category": "attendance",
                "log_data": f"event_id:{uuid.uuid4()},subscriber_id:1",
            }
        }
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "BOOKING_NOT_FOUND")

    def test_log8_malformed_event_id_uuid_returns_404(self):
        booking, slot, citizen, org = self._make_booking()
        qry = {
            "log_details": {
                "logger_role": "admin",
                "log_category": "attendance",
                "log_data": f"event_id:not-a-uuid,subscriber_id:{citizen.pk}",
            }
        }
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "BOOKING_NOT_FOUND")

    def test_log9_non_integer_subscriber_id_returns_400(self):
        booking, slot, citizen, org = self._make_booking()
        qry = {
            "log_details": {
                "logger_role": "admin",
                "log_category": "attendance",
                "log_data": f"event_id:{slot.pk},subscriber_id:not-an-int",
            }
        }
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "LOG_CREATE_FAILED")

    def test_log10_invalid_logger_role_returns_400(self):
        booking, slot, citizen, org = self._make_booking()
        qry = {
            "log_details": {
                "logger_role": "not-a-real-role",
                "log_category": "attendance",
                "log_data": _log_data_for(slot, citizen),
            }
        }
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "LOG_CREATE_FAILED")

    def test_log11_missing_log_category_returns_400(self):
        booking, slot, citizen, org = self._make_booking()
        qry = {
            "log_details": {
                "logger_role": "admin",
                "log_data": _log_data_for(slot, citizen),
            }
        }
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "LOG_CREATE_FAILED")

    def test_log12_mismatched_entity_id_returns_400(self):
        booking, slot, citizen, org = self._make_booking()
        other_org = _create_org("Other Org")
        qry = {
            "log_details": {
                "logger_role": "admin",
                "log_category": "attendance",
                "entity_id": str(other_org.pk),
                "log_data": _log_data_for(slot, citizen),
            }
        }
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "ENTITY_MISMATCH")

    def test_log13_matching_entity_id_succeeds(self):
        booking, slot, citizen, org = self._make_booking()
        qry = {
            "log_details": {
                "logger_role": "admin",
                "log_category": "attendance",
                "entity_id": str(org.pk),
                "log_data": _log_data_for(slot, citizen),
            }
        }
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 200)

    def test_log14_caller_supplied_datetime_is_not_stored(self):
        """
        LOG14: BookingAuditLog.timestamp has auto_now_add=True — the
        caller-supplied `datetime` value (a deliberately backdated value
        here) must never be what ends up stored; the real stored timestamp
        must be close to "now" instead.
        """
        booking, slot, citizen, org = self._make_booking()
        backdated = "2001-01-01T00:00:00Z"
        qry = {
            "log_details": {
                "logger_role": "admin",
                "log_category": "attendance",
                "datetime": backdated,
                "log_data": _log_data_for(slot, citizen),
            }
        }
        before = timezone.now()
        resp = self._post(qry)
        after = timezone.now()
        self.assertEqual(resp.status_code, 200)
        entry = BookingAuditLog.objects.get(pk=resp.json()["log_id"])
        self.assertNotEqual(entry.timestamp, parse_datetime(backdated))
        self.assertGreaterEqual(entry.timestamp, before)
        self.assertLessEqual(entry.timestamp, after)


# ===========================================================================
# LOG15-LOG16: PUT /log/modifications — always 405
# ===========================================================================


class LogModificationsTests(LogBaseTestCase):
    """LOG15-LOG16: PUT /log/modifications always returns 405 for an admin caller."""

    def test_log15_admin_caller_receives_405(self):
        resp = self._put()
        self.assertEqual(resp.status_code, 405)
        data = resp.json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["code"], "METHOD_NOT_ALLOWED")

    def test_log16_no_audit_log_row_is_ever_created_or_altered(self):
        before_count = BookingAuditLog.objects.count()
        self._put()
        self.assertEqual(BookingAuditLog.objects.count(), before_count)

    def test_log41_missing_auth_returns_401_or_403_not_405(self):
        """
        LOG41: a caller supplying NO requestor_id/request_token at all must
        be rejected with 401/403 before ever reaching the always-405
        handler. test_log33 only proves an authenticated-but-under-
        privileged (organizer role) caller is denied with 403; this proves
        a fully credential-less caller is also denied — not somehow routed
        through to the 405 response. Matches the test_log39/test_log40
        no-auth-params pattern (direct client call, no _AUTH params).
        """
        resp = self.client.put(MODIFICATIONS_URL)
        self.assertIn(resp.status_code, (401, 403))


# ===========================================================================
# LOG17-LOG18: DELETE /log — always 405
# ===========================================================================


class LogDeleteTests(LogBaseTestCase):
    """LOG17-LOG18: DELETE /log always returns 405 for an admin caller."""

    def test_log17_admin_caller_receives_405(self):
        resp = self._delete()
        self.assertEqual(resp.status_code, 405)
        data = resp.json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["code"], "METHOD_NOT_ALLOWED")

    def test_log18_no_audit_log_row_is_ever_deleted(self):
        booking, slot, citizen, org = self._make_booking()
        entry = BookingAuditLog.objects.create(
            booking=booking,
            action="created",
            actor_id="system",
            actor_role="system",
        )
        self._delete()
        self.assertTrue(BookingAuditLog.objects.filter(pk=entry.pk).exists())

    def test_log42_missing_auth_returns_401_or_403_not_405(self):
        """
        LOG42: same as LOG41 but for DELETE /log — a caller supplying NO
        requestor_id/request_token at all must be rejected with 401/403
        before ever reaching the always-405 handler, not just an
        authenticated-but-under-privileged (organizer role) caller
        (test_log35). Matches the test_log39/test_log40 no-auth-params
        pattern (direct client call, no _AUTH params).
        """
        resp = self.client.delete(DELETE_URL)
        self.assertIn(resp.status_code, (401, 403))


# ===========================================================================
# LOG19-LOG30: GET /log/list_details
# ===========================================================================


class LogListDetailsTests(LogBaseTestCase):
    """LOG19-LOG30: GET /log/list_details"""

    def _create_log_entry(self, host_entity_id="", logger_role="organizer", **overrides):
        booking, slot, citizen, org = self._make_booking(host_entity_id=host_entity_id)
        details = {
            "logger_role": logger_role,
            "logger_id": "7",
            "log_category": "attendance",
            "log_data": _log_data_for(slot, citizen, token="abc"),
        }
        details.update(overrides)
        resp = self._post({"log_details": details})
        assert resp.status_code == 200, resp.content
        entry = BookingAuditLog.objects.get(pk=resp.json()["log_id"])
        return entry, org

    def _create_log_entry_at(self, dt, **kwargs):
        entry, org = self._create_log_entry(**kwargs)
        BookingAuditLog.objects.filter(pk=entry.pk).update(timestamp=dt)
        entry.refresh_from_db()
        return entry, org

    def test_log19_happy_path_no_filter_returns_all(self):
        """
        Bug 2 fix: the response body is now a bare JSON array (matches the
        real GovStack OpenAPI spec's log_list schema exactly) — no more
        {"status": "success", "data": [...], "truncated": ...} wrapper.
        """
        self._create_log_entry()
        self._create_log_entry()
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 2)

    def test_log20_filter_by_log_id(self):
        entry, _ = self._create_log_entry()
        self._create_log_entry()
        resp = self._get({"log_filter": {"log_id": str(entry.pk)}})
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["log_id"], str(entry.pk))

    def test_log20b_filter_by_log_id_array_matches_multiple(self):
        """
        Bug 1 fix: log_id is array-typed per the real GovStack spec — a
        JSON array of 2+ ids returns all matching records (pk__in).
        """
        entry1, _ = self._create_log_entry()
        entry2, _ = self._create_log_entry()
        resp = self._get({"log_filter": {"log_id": [str(entry1.pk), str(entry2.pk)]}})
        self.assertEqual(resp.status_code, 200)
        returned_ids = {item["log_id"] for item in resp.json()}
        self.assertEqual(returned_ids, {str(entry1.pk), str(entry2.pk)})

    def test_log20c_filter_by_log_id_invalid_entry_returns_400(self):
        """
        Bug 1 fix: an invalid/non-numeric id in the array is handled the same
        way the reference implementation (alert_schedule_id/message_id) does
        — the malformed pk__in lookup raises, and the view's generic
        exception handler maps it to a 400.
        """
        entry, _ = self._create_log_entry()
        resp = self._get({"log_filter": {"log_id": [str(entry.pk), "not-a-number"]}})
        self.assertEqual(resp.status_code, 400)

    def test_log21_filter_by_category(self):
        entry, _ = self._create_log_entry(log_category="unique-cat-21")
        self._create_log_entry(log_category="something-else")
        resp = self._get({"log_filter": {"category": "unique-cat-21"}})
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["details"]["log_category"], "unique-cat-21")

    def test_log22_filter_by_entity_id(self):
        entry, org = self._create_log_entry()
        self._create_log_entry()  # different org
        resp = self._get({"log_filter": {"entity_id": str(org.pk)}})
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["details"]["entity_id"], str(org.pk))

    def test_log23_filter_by_from_to_datetime_range(self):
        """
        LOG23: the from/to window must both include an in-range row AND
        exclude an out-of-range row (proving the filter isn't a no-op) —
        matching the Wave F adversarial review's inclusion+exclusion
        requirement (see AlertScheduleListDetailsTests.test_as28's identical
        precedent).
        """
        in_range, _ = self._create_log_entry_at(parse_datetime("2027-05-15T00:00:00Z"))
        out_of_range, _ = self._create_log_entry_at(parse_datetime("2020-01-01T00:00:00Z"))
        resp = self._get(
            {
                "log_filter": {
                    "from": "2027-01-01T00:00:00Z",
                    "to": "2027-12-31T00:00:00Z",
                }
            }
        )
        data = resp.json()
        ids = [item["log_id"] for item in data]
        self.assertIn(str(in_range.pk), ids)
        self.assertNotIn(str(out_of_range.pk), ids)

    def test_log24_response_shape_log_id_and_details(self):
        entry, org = self._create_log_entry(logger_role="resource")
        resp = self._get({"log_filter": {"log_id": str(entry.pk)}})
        item = resp.json()[0]
        self.assertIn("log_id", item)
        self.assertIn("details", item)
        self.assertEqual(item["details"]["logger_role"], "resource")
        self.assertEqual(item["details"]["logger_id"], "7")
        self.assertEqual(item["details"]["entity_id"], str(org.pk))
        self.assertEqual(item["details"]["log_category"], "attendance")
        self.assertIn("datetime", item["details"])

    def test_log25_logger_role_excluded_when_logger_category_flag_false(self):
        """LOG25: Spec Quirk #2 — the "logger_category" flag gates the response's "logger_role" field."""  # noqa: E501
        entry, _ = self._create_log_entry()
        resp = self._get(
            {
                "log_filter": {"log_id": str(entry.pk)},
                "log_details_required": {"logger_category": False},
            }
        )
        item = resp.json()[0]
        self.assertNotIn("logger_role", item["details"])

    def test_log26_log_data_excluded_by_default(self):
        entry, _ = self._create_log_entry()
        resp = self._get({"log_filter": {"log_id": str(entry.pk)}})
        item = resp.json()[0]
        self.assertNotIn("log_data", item["details"])

    def test_log27_log_data_included_when_required_flag_true(self):
        entry, _ = self._create_log_entry()
        resp = self._get(
            {
                "log_filter": {"log_id": str(entry.pk)},
                "log_details_required": {"log_data": True},
            }
        )
        item = resp.json()[0]
        self.assertIn("log_data", item["details"])
        parsed = json.loads(item["details"]["log_data"])
        self.assertIn("detail", parsed)
        self.assertIn("previous_status", parsed)
        self.assertIn("new_status", parsed)
        # actor_ip must NEVER be reconstructed into log_data (PIPEDA-cautious exclusion).
        self.assertNotIn("actor_ip", parsed)

    def test_log28_no_matches_returns_empty_list(self):
        resp = self._get({"log_filter": {"category": "definitely-does-not-exist"}})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_log29_malformed_from_returns_400(self):
        resp = self._get({"log_filter": {"from": "not-a-date"}})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "LOG_LIST_FILTER_INVALID")

    def test_log30_entity_id_and_log_id_both_present_at_top_level(self):
        entry, _ = self._create_log_entry()
        resp = self._get({"log_filter": {"log_id": str(entry.pk)}})
        item = resp.json()[0]
        self.assertEqual(item["log_id"], str(entry.pk))
        self.assertEqual(item["details"]["log_id"], str(entry.pk))


# ===========================================================================
# LOG31-LOG40: Auth / role enforcement
# ===========================================================================


@override_settings(GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True)
class LogRoleEnforcementTests(LogBaseTestCase):
    """
    LOG31-LOG38: all 4 log endpoints require gs_actor_role="admin" — a
    role="organizer" BB (below admin in the role hierarchy — subscriber <
    resource < organizer < admin) must be denied (403) on every one of the
    4 endpoints; a role="admin" BB must be allowed through. Covers all 4
    endpoints from the start (Wave F's review found 4-of-8 endpoints with no
    role-enforcement coverage at all — do not repeat that gap here).
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

    def test_log31_organizer_role_denied_on_log_new(self):
        self._make_role_bb("organizer")
        booking, slot, citizen, org = self._make_booking()
        qry = {
            "log_details": {
                "logger_role": "admin",
                "log_category": "attendance",
                "log_data": _log_data_for(slot, citizen),
            }
        }
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 403)

    def test_log32_admin_role_allowed_on_log_new(self):
        self._make_role_bb("admin")
        booking, slot, citizen, org = self._make_booking()
        qry = {
            "log_details": {
                "logger_role": "admin",
                "log_category": "attendance",
                "log_data": _log_data_for(slot, citizen),
            }
        }
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 200)

    def test_log33_organizer_role_denied_on_log_modifications(self):
        self._make_role_bb("organizer")
        resp = self._put()
        self.assertEqual(resp.status_code, 403)

    def test_log34_admin_role_receives_405_not_403_on_log_modifications(self):
        self._make_role_bb("admin")
        resp = self._put()
        self.assertEqual(resp.status_code, 405)

    def test_log35_organizer_role_denied_on_log_delete(self):
        self._make_role_bb("organizer")
        resp = self._delete()
        self.assertEqual(resp.status_code, 403)

    def test_log36_admin_role_receives_405_not_403_on_log_delete(self):
        self._make_role_bb("admin")
        resp = self._delete()
        self.assertEqual(resp.status_code, 405)

    def test_log37_organizer_role_denied_on_log_list(self):
        self._make_role_bb("organizer")
        resp = self._get()
        self.assertEqual(resp.status_code, 403)

    def test_log38_admin_role_allowed_on_log_list(self):
        self._make_role_bb("admin")
        resp = self._get()
        self.assertEqual(resp.status_code, 200)

    def test_log39_missing_auth_params_returns_401_or_403_on_log_new(self):
        """
        LOG39: a request with no requestor_id/request_token at all fails
        authentication. DRF raises NotAuthenticated (401) when authenticators
        are configured but none succeed — matches the identical pattern in
        test_govstack_subscriber.py's test_s6.
        """
        qry = json.dumps({"log_details": {"logger_role": "admin"}})
        resp = self.client.post(NEW_URL + f"?qry={qry}")
        self.assertIn(resp.status_code, (401, 403))

    def test_log40_missing_auth_params_returns_401_or_403_on_log_list(self):
        resp = self.client.get(LIST_URL)
        self.assertIn(resp.status_code, (401, 403))
