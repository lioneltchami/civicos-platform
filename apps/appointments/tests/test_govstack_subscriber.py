"""
GovStack Scheduler BB — Subscriber endpoints test suite (Wave C).

Covers 4 endpoints:
  POST   /govstack/scheduler/subscriber/new
  PUT    /govstack/scheduler/subscriber/modifications
  DELETE /govstack/scheduler/subscriber
  GET    /govstack/scheduler/subscriber/list_details

Tests are numbered S1–S29 matching the Wave C specification.
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.appointments.models import GovStackSubscriberProfile
from apps.appointments.services.govstack_subscriber import (
    subscriber_create,
    subscriber_delete,
    subscriber_list,
    subscriber_modify,
)

User = get_user_model()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEW_URL = "/govstack/scheduler/subscriber/new"
MODIFICATIONS_URL = "/govstack/scheduler/subscriber/modifications"
LIST_URL = "/govstack/scheduler/subscriber/list_details"
DELETE_URL = "/govstack/scheduler/subscriber"

_AUTH = {"requestor_id": "test-bb", "request_token": "test-token"}


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


def _create_subscriber(
    email="alice@example.com",
    name="Alice Smith",
    category="individual",
    phone="+15005550001",
    alert_preference="email",
):
    """Factory: create a Subscriber via the service layer."""
    return subscriber_create(
        name=name,
        category=category,
        phone=phone,
        email=email,
        alert_url="",
        alert_preference=alert_preference,
        status_poll_url="",
    )


# ===========================================================================
# Base test case
# ===========================================================================

class SubscriberBaseTestCase(TestCase):
    """Shared setup for all subscriber HTTP endpoint tests."""

    def _post(self, qry_dict):
        return self.client.post(NEW_URL + _qry_qs(qry_dict))

    def _put(self, qry_dict, subscriber_id=None):
        extra = {"subscriber_id": subscriber_id} if subscriber_id is not None else {}
        return self.client.put(MODIFICATIONS_URL + _qry_qs(qry_dict, **extra))

    def _delete(self, subscriber_id=None):
        extra = {"subscriber_id": subscriber_id} if subscriber_id is not None else {}
        return self.client.delete(DELETE_URL + _qs(**extra))

    def _get(self, qry_dict=None):
        if qry_dict is None:
            return self.client.get(LIST_URL + _qs())
        return self.client.get(LIST_URL + _qry_qs(qry_dict))


# ===========================================================================
# S1–S7: POST /subscriber/new
# ===========================================================================

class SubscriberNewTests(SubscriberBaseTestCase):
    """S1–S7: POST /subscriber/new"""

    # S1
    def test_s1_post_new_returns_201_with_subscriber_id(self):
        """S1: POST /subscriber/new returns 201 with subscriber_id."""
        qry = {"qry": {"details": {"name": "Bob Jones", "email": "bob@example.com",
                                    "category": "individual", "phone": "+15005550002",
                                    "alert_preference": "email"}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("subscriber_id", data)
        self.assertTrue(data["subscriber_id"].isdigit())

    # S2
    def test_s2_post_new_missing_email_returns_400(self):
        """S2: POST /subscriber/new with missing email returns 400."""
        qry = {"qry": {"details": {"name": "No Email", "category": "individual"}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data.get("code"), "SUBSCRIBER_CREATE_FAILED")

    # S3
    def test_s3_post_new_duplicate_email_returns_400(self):
        """S3: POST /subscriber/new with duplicate email returns 400."""
        email = "dupe@example.com"
        subscriber_create(email=email, name="First User")
        qry = {"qry": {"details": {"name": "Second User", "email": email}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["code"], "SUBSCRIBER_CREATE_FAILED")
        self.assertNotIn(email, resp.content.decode())

    # S4
    def test_s4_post_new_invalid_alert_preference_returns_400(self):
        """S4: POST /subscriber/new with invalid alert_preference returns 400."""
        qry = {"qry": {"details": {"email": "ap@example.com",
                                    "alert_preference": "carrier_pigeon"}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["code"], "SUBSCRIBER_CREATE_FAILED")

    # S5
    def test_s5_post_new_creates_user_and_profile(self):
        """S5: POST /subscriber/new creates both User and GovStackSubscriberProfile."""
        qry = {"qry": {"details": {"name": "Carol Doe", "email": "carol@example.com",
                                    "category": "individual", "alert_preference": "sms"}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 201)
        subscriber_id = int(resp.json()["subscriber_id"])

        user = User.objects.get(pk=subscriber_id)
        self.assertEqual(user.first_name, "Carol")
        self.assertEqual(user.last_name, "Doe")
        self.assertTrue(user.is_active)

        profile = GovStackSubscriberProfile.objects.get(user_id=subscriber_id)
        self.assertEqual(profile.category, "individual")
        self.assertEqual(profile.alert_preference, "sms")

    # S6
    def test_s6_post_new_without_auth_returns_4xx(self):
        """S6: POST /subscriber/new without auth params returns 401 or 403.

        DRF raises NotAuthenticated (401) when authenticators are configured
        but none succeed (request.successful_authenticator is None), rather
        than PermissionDenied (403). Either code is a valid "not allowed"
        response — the test accepts both.
        """
        qry = json.dumps({"qry": {"details": {"email": "noauth@example.com"}}})
        resp = self.client.post(NEW_URL + f"?qry={qry}")
        self.assertIn(resp.status_code, (401, 403))

    # S7
    def test_s7_post_new_invalid_json_qry_returns_400(self):
        """S7: POST /subscriber/new with invalid JSON qry returns 400."""
        params = urlencode({**_AUTH, "qry": "{not valid json"})
        resp = self.client.post(NEW_URL + "?" + params)
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["code"], "INVALID_QRY")

    # S31
    def test_s31_post_new_invalid_email_format_returns_400(self):
        """S31: Non-email string in email field returns 400."""
        resp = self._post({"qry": {"details": {"name": "Alice", "email": "not-an-email"}}})
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["status"], "error")

    # S32
    def test_s32_post_new_phone_too_long_returns_400(self):
        """S32: Phone number exceeding 20 chars returns 400 instead of silent truncation."""
        resp = self._post({"qry": {"details": {"email": "phone_test@example.com", "phone": "1" * 21}}})
        self.assertEqual(resp.status_code, 400)

    # S33
    def test_s33_post_new_http_alert_url_returns_400(self):
        """S33: http:// alert_url (not https) returns 400."""
        resp = self._post({"qry": {"details": {"email": "httptest@example.com", "alert_url": "http://example.com/callback"}}})
        self.assertEqual(resp.status_code, 400)

    # S34
    def test_s34_post_new_error_message_does_not_contain_email(self):
        """S34: PIPEDA — duplicate-email 400 response does not echo the email back."""
        _create_subscriber(email="existing@example.com")
        resp = self._post({"qry": {"details": {"email": "existing@example.com"}}})
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn("existing@example.com", resp.content.decode())

    # S35
    def test_s35_post_new_user_has_unusable_password(self):
        """S35: Subscribers are created with unusable Django passwords."""
        profile = subscriber_create(
            email="nopwd@example.com",
            name="No Password",
            category="",
            phone="",
            alert_url="",
            alert_preference="",
            status_poll_url="",
        )
        self.assertFalse(profile.user.has_usable_password())

    # S38
    def test_s38_post_new_name_too_long_returns_400(self):
        """S38 — name field exceeding 150 chars per part returns 400."""
        long_name = "A" * 151 + " B"
        qry = {"qry": {"details": {"email": "longname@example.com", "name": long_name}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)

    # S39
    def test_s39_post_new_category_too_long_returns_400(self):
        """S39 — category exceeding 50 chars returns 400."""
        qry = {"qry": {"details": {"email": "longcat@example.com", "category": "X" * 51}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)

    # S40
    def test_s40_post_new_whitespace_phone_stores_empty(self):
        """S40 — Phone with only whitespace is stripped and stored as empty string."""
        profile = subscriber_create(
            name="Whitespace Phone",
            email="wsphone@example.com",
            category="",
            phone="   ",
            alert_url="",
            alert_preference="",
            status_poll_url="",
        )
        self.assertEqual(profile.user.phone_number, "")

    # S41
    def test_s41_post_new_status_poll_url_http_returns_400(self):
        """S41 — http:// status_poll_url returns 400."""
        qry = {"qry": {"details": {"email": "spoll@example.com",
                                    "status_poll_url": "http://example.com/poll"}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 400)


# ===========================================================================
# S8–S13: PUT /subscriber/modifications
# ===========================================================================

class SubscriberModificationsTests(SubscriberBaseTestCase):
    """S8–S13: PUT /subscriber/modifications"""

    def setUp(self):
        self.profile = _create_subscriber(email="modify@example.com", name="Original Name")
        self.subscriber_id = self.profile.user_id

    # S8
    def test_s8_put_modifications_updates_name(self):
        """S8: PUT /subscriber/modifications updates name correctly."""
        qry = {"details": {"name": "Updated Name"}}
        resp = self._put(qry, subscriber_id=self.subscriber_id)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["subscriber_id"], str(self.subscriber_id))

        user = User.objects.get(pk=self.subscriber_id)
        self.assertEqual(user.first_name, "Updated")
        self.assertEqual(user.last_name, "Name")

    # S9
    def test_s9_put_modifications_updates_alert_preference(self):
        """S9: PUT /subscriber/modifications updates alert_preference."""
        qry = {"details": {"alert_preference": "push"}}
        resp = self._put(qry, subscriber_id=self.subscriber_id)
        self.assertEqual(resp.status_code, 200)
        profile = GovStackSubscriberProfile.objects.get(user_id=self.subscriber_id)
        self.assertEqual(profile.alert_preference, "push")

    # S10
    def test_s10_put_modifications_missing_subscriber_id_returns_400(self):
        """S10: PUT /subscriber/modifications with missing subscriber_id returns 400."""
        qry = {"details": {"name": "No ID"}}
        resp = self._put(qry, subscriber_id=None)
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["code"], "MISSING_SUBSCRIBER_ID")

    # S11
    def test_s11_put_modifications_non_integer_subscriber_id_returns_400(self):
        """S11: PUT /subscriber/modifications with non-integer subscriber_id returns 400."""
        qry = {"details": {"name": "Bad ID"}}
        resp = self._put(qry, subscriber_id="not-an-int")
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["code"], "INVALID_SUBSCRIBER_ID")

    # S12
    def test_s12_put_modifications_nonexistent_subscriber_id_returns_404(self):
        """S12: PUT /subscriber/modifications with non-existent subscriber_id returns 404."""
        qry = {"details": {"name": "Ghost"}}
        resp = self._put(qry, subscriber_id=99999999)
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertEqual(data["code"], "SUBSCRIBER_NOT_FOUND")

    # S13
    def test_s13_put_modifications_soft_deleted_subscriber_returns_404(self):
        """S13: PUT /subscriber/modifications on soft-deleted subscriber returns 404."""
        subscriber_delete(subscriber_id=self.subscriber_id)
        qry = {"details": {"name": "Should Fail"}}
        resp = self._put(qry, subscriber_id=self.subscriber_id)
        self.assertEqual(resp.status_code, 404)

    # S30
    def test_s30_modify_duplicate_email_returns_400(self):
        """S30: Changing to an already-registered email returns 400, not 500."""
        sub1 = _create_subscriber(email="first@example.com")
        sub2 = _create_subscriber(email="second@example.com")
        resp = self._put(
            {"details": {"email": "first@example.com"}},
            subscriber_id=sub2.user_id,
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["status"], "error")
        # Must not contain the email in the response body
        self.assertNotIn("first@example.com", str(data))


# ===========================================================================
# S14–S17: DELETE /subscriber
# ===========================================================================

class SubscriberDeleteTests(SubscriberBaseTestCase):
    """S14–S17: DELETE /subscriber"""

    def setUp(self):
        self.profile = _create_subscriber(email="delete@example.com", name="Delete Me")
        self.subscriber_id = self.profile.user_id

    # S14
    def test_s14_delete_soft_deletes_user(self):
        """S14: DELETE /subscriber soft-deletes user (is_active=False)."""
        resp = self._delete(subscriber_id=self.subscriber_id)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["subscriber_id"], str(self.subscriber_id))

        user = User.objects.get(pk=self.subscriber_id)
        self.assertFalse(user.is_active)

    # S15
    def test_s15_delete_missing_subscriber_id_returns_400(self):
        """S15: DELETE /subscriber with missing subscriber_id returns 400."""
        resp = self._delete(subscriber_id=None)
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["code"], "MISSING_SUBSCRIBER_ID")

    # S16
    def test_s16_delete_nonexistent_subscriber_id_returns_404(self):
        """S16: DELETE /subscriber with non-existent subscriber_id returns 404."""
        resp = self._delete(subscriber_id=99999999)
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertEqual(data["code"], "SUBSCRIBER_NOT_FOUND")

    # S17
    def test_s17_delete_profile_still_exists_after_soft_delete(self):
        """S17: DELETE /subscriber — GovStackSubscriberProfile still exists for audit trail."""
        resp = self._delete(subscriber_id=self.subscriber_id)
        self.assertEqual(resp.status_code, 200)
        # Profile must still exist (soft-delete only deactivates the User)
        self.assertTrue(
            GovStackSubscriberProfile.objects.filter(user_id=self.subscriber_id).exists()
        )


# ===========================================================================
# S18–S26: GET /subscriber/list_details
# ===========================================================================

class SubscriberListDetailsTests(SubscriberBaseTestCase):
    """S18–S26: GET /subscriber/list_details"""

    def setUp(self):
        self.p1 = _create_subscriber(email="alice@list.com", name="Alice Smith", category="individual")
        self.p2 = _create_subscriber(email="bob@list.com", name="Bob Jones", category="group")

    # S18
    def test_s18_list_returns_all_active_subscribers(self):
        """S18: GET /subscriber/list_details returns all active subscribers."""
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        ids = [r["subscriber_id"] for r in data["data"]]
        self.assertIn(str(self.p1.user_id), ids)
        self.assertIn(str(self.p2.user_id), ids)

    # S19
    def test_s19_list_with_subscriber_id_filter_returns_single_result(self):
        """S19: GET /subscriber/list_details with subscriber_id filter returns one result."""
        qry = {"subscriber_filter": {"subscriber_id": str(self.p1.user_id)}}
        resp = self._get(qry)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["subscriber_id"], str(self.p1.user_id))

    # S20
    def test_s20_list_with_email_filter_returns_matching(self):
        """S20: GET /subscriber/list_details with email filter returns matching subscriber."""
        qry = {
            "subscriber_filter": {"email": "alice@list.com"},
            "subscriber_details_required": {"email": True},
        }
        resp = self._get(qry)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["subscriber_id"], str(self.p1.user_id))

    # S21
    def test_s21_list_excludes_soft_deleted_subscribers(self):
        """S21: GET /subscriber/list_details excludes soft-deleted subscribers."""
        subscriber_delete(subscriber_id=self.p1.user_id)
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        ids = [r["subscriber_id"] for r in resp.json()["data"]]
        self.assertNotIn(str(self.p1.user_id), ids)
        self.assertIn(str(self.p2.user_id), ids)

    # S22
    def test_s22_list_with_email_details_required_returns_email(self):
        """S22: GET /subscriber/list_details with subscriber_details_required email=true returns email."""
        qry = {
            "subscriber_filter": {"subscriber_id": str(self.p1.user_id)},
            "subscriber_details_required": {"email": True},
        }
        resp = self._get(qry)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertIn("email", data[0])
        self.assertEqual(data[0]["email"], "alice@list.com")

    # S23
    def test_s23_list_always_includes_subscriber_id(self):
        """S23: GET /subscriber/list_details always includes subscriber_id even with empty details_required."""
        qry = {"subscriber_details_required": {}}
        resp = self._get(qry)
        self.assertEqual(resp.status_code, 200)
        for record in resp.json()["data"]:
            self.assertIn("subscriber_id", record)

    # S24
    def test_s24_list_with_empty_qry_returns_all(self):
        """S24: GET /subscriber/list_details with empty qry does not return 400."""
        resp = self._get(qry_dict={})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")

    # S25 — PIPEDA
    def test_s25_pipeda_list_without_email_flag_omits_email(self):
        """S25: PIPEDA — email omitted when subscriber_details_required.email is not true."""
        qry = {
            "subscriber_filter": {"subscriber_id": str(self.p1.user_id)},
            "subscriber_details_required": {"email": False},
        }
        resp = self._get(qry)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertNotIn("email", data[0])

    # S26 — PIPEDA
    def test_s26_pipeda_post_new_does_not_return_email_in_response(self):
        """S26: PIPEDA — POST /subscriber/new response body does not contain email."""
        qry = {"qry": {"details": {"email": "pipeda@example.com", "name": "Privacy User"}}}
        resp = self._post(qry)
        self.assertEqual(resp.status_code, 201)
        resp_data = resp.json()
        # Only subscriber_id and status should be present — no PII
        self.assertNotIn("email", resp_data)
        self.assertNotIn("name", resp_data)
        self.assertNotIn("phone", resp_data)

    # S36 — PIPEDA
    def test_s36_list_default_response_excludes_pii(self):
        """S36: PIPEDA — default list (no qry) excludes name, email, and phone."""
        _create_subscriber(email="pii_test@example.com", name="PII Test User")
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        for record in resp.json()["data"]:
            self.assertNotIn("email", record)
            self.assertNotIn("phone", record)
            self.assertNotIn("name", record)

    # S37
    def test_s37_list_noninteger_subscriber_id_filter_returns_400(self):
        """S37: Non-integer subscriber_id filter in subscriber_filter returns 400 not 500."""
        resp = self._get({"subscriber_filter": {"subscriber_id": "abc"}})
        self.assertEqual(resp.status_code, 400)

    # S42
    def test_s42_list_nonexistent_alert_url_filter_returns_empty(self):
        """S42 — alert_url filter actually filters (not silently ignored)."""
        _create_subscriber(email="al1@example.com")  # no alert_url
        resp = self._get({"subscriber_filter": {"alert_url": "https://notregistered.example.com/hook"}})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["data"]), 0)  # filter actually applied


# ===========================================================================
# S27–S29: Service-level tests (direct imports)
# ===========================================================================

class SubscriberServiceTests(TestCase):
    """S27–S29: Direct service layer tests."""

    # S27
    def test_s27_subscriber_create_creates_user_and_profile(self):
        """S27: subscriber_create creates correct User and GovStackSubscriberProfile."""
        profile = subscriber_create(
            name="Jane Doe",
            category="individual",
            phone="+15005550099",
            email="jane@example.com",
            alert_url="",
            alert_preference="email",
            status_poll_url="",
        )
        self.assertIsInstance(profile, GovStackSubscriberProfile)
        user = profile.user
        self.assertEqual(user.first_name, "Jane")
        self.assertEqual(user.last_name, "Doe")
        self.assertEqual(user.email, "jane@example.com")
        self.assertEqual(user.phone_number, "+15005550099")
        self.assertTrue(user.is_active)
        self.assertEqual(profile.category, "individual")
        self.assertEqual(profile.alert_preference, "email")

    # S28
    def test_s28_subscriber_list_name_filter_matches_first_or_last_name(self):
        """S28: subscriber_list with name filter matches first_name or last_name."""
        subscriber_create(name="Alice Wonder", email="alice_w@example.com")
        subscriber_create(name="Bob Alice", email="bob_a@example.com")
        subscriber_create(name="Charlie Brown", email="charlie@example.com")

        results = subscriber_list({"name": "Alice"}, {})
        returned_ids = {r["subscriber_id"] for r in results}

        alice_profile = GovStackSubscriberProfile.objects.get(user__email="alice_w@example.com")
        bob_profile = GovStackSubscriberProfile.objects.get(user__email="bob_a@example.com")
        charlie_profile = GovStackSubscriberProfile.objects.get(user__email="charlie@example.com")

        self.assertIn(str(alice_profile.user_id), returned_ids)   # first_name matches
        self.assertIn(str(bob_profile.user_id), returned_ids)     # last_name matches
        self.assertNotIn(str(charlie_profile.user_id), returned_ids)

    # S29
    def test_s29_subscriber_delete_does_not_hard_delete_profile(self):
        """S29: subscriber_delete does not hard-delete GovStackSubscriberProfile."""
        profile = subscriber_create(name="To Delete", email="todelete@example.com")
        user_id = profile.user_id

        subscriber_delete(subscriber_id=user_id)

        # User should still exist in DB (soft-delete only)
        self.assertTrue(User.objects.filter(pk=user_id).exists())
        # Profile should still exist for audit trail
        self.assertTrue(GovStackSubscriberProfile.objects.filter(user_id=user_id).exists())
        # User should be inactive
        self.assertFalse(User.objects.get(pk=user_id).is_active)


# ===========================================================================
# S43–S46: Wrong HTTP method tests
# ===========================================================================

class SubscriberViewMethodTests(TestCase):
    """S43–S46 — Wrong HTTP methods return 405."""

    def test_s43_get_on_new_returns_405(self):
        """S43: GET on POST-only /subscriber/new returns 405."""
        resp = self.client.get(NEW_URL + _qs())
        self.assertEqual(resp.status_code, 405)

    def test_s44_post_on_modifications_returns_405(self):
        """S44: POST on PUT-only /subscriber/modifications returns 405."""
        resp = self.client.post(MODIFICATIONS_URL + _qs())
        self.assertEqual(resp.status_code, 405)

    def test_s45_post_on_subscriber_delete_returns_405(self):
        """S45: POST on DELETE-only /subscriber returns 405."""
        resp = self.client.post(DELETE_URL + _qs())
        self.assertEqual(resp.status_code, 405)

    def test_s46_post_on_list_details_returns_405(self):
        """S46: POST on GET-only /subscriber/list_details returns 405."""
        resp = self.client.post(LIST_URL + _qs())
        self.assertEqual(resp.status_code, 405)
