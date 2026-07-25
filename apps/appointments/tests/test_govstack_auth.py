"""
test_govstack_auth.py (apps.appointments)

Unit/integration tests for the GovStack Scheduler BB's two-tier caller
resolution:

  Tier 1 (BB/organizer/admin/resource role) — GovStackSchedulerAuth resolves
    request.META["_gs_resolved_role"] from the calling GovStackRegisteredBB
    row's `role` field (GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True) or defaults to
    "admin" as a dev/harness bootstrapping convenience (False, the default).
    GovStackSchedulerRolePermission enforces the endpoint's minimum required
    role against this resolved value — never against a view's own
    self-declared gs_actor_role (that attribute states the MINIMUM required
    to reach the view, it is not evidence of who is calling).

  Tier 2 (citizen/subscriber identity) — GovStackCitizenAuth validates an
    optional citizen JWT on top of the same BB-to-BB trust gate.

Coverage matrix:
  GA-1: GOVSTACK_SCHEDULER_REQUIRE_TOKEN=False (default) — any endpoint
        reachable with any non-empty requestor_id/request_token, regardless
        of "role" (resolved_role defaults to "admin" in this mode) —
        regression guard for pre-existing harness/test behaviour.
  GA-2: GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True + GovStackRegisteredBB(role=
        "resource") — an admin-tier endpoint (EntityNewView) is denied (403).
  GA-3: GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True + role="resource" — a
        resource-tier endpoint (ResourceAvailabilityView) passes auth (200).
  GA-4: GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True + role="admin" — the
        admin-tier endpoint (EntityNewView) succeeds (201).
  GA-5 through GA-9: GovStackCitizenAuth.authenticate() unit tests — valid
        citizen JWT resolves (user, "govstack_scheduler_subscriber"); no
        Authorization header falls back to BB-only trust; invalid/expired
        JWT raises AuthenticationFailed (never silently ignored); a staff
        user's JWT is rejected; both BB creds and Authorization header
        absent falls through (returns None).

Test approach:
  GA-1..GA-4 exercise the real view/URL layer via the Django test client,
  following the exact helper pattern already used in
  test_govstack_appointment.py / test_govstack_event.py (query-param auth,
  JSON-encoded `qry`).

  GA-5..GA-9 call GovStackCitizenAuth().authenticate() directly against a
  DRF Request built via APIRequestFactory — the correct unit test pattern
  for a DRF authentication class, matching the precedent set by
  apps/payments/tests/test_govstack_auth.py's direct
  IsTrustedSourceBB.has_permission() calls.
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request as DRFRequest
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.tokens import RefreshToken

from apps.appointments.govstack_auth import GovStackCitizenAuth
from apps.payments.govstack_models import GovStackRegisteredBB

# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

ENTITY_NEW_URL = "/govstack/scheduler/entity/new"
RESOURCE_AVAILABILITY_URL = "/govstack/scheduler/resource/availability"

_BB_ID = "test-registered-bb"


def _auth_params(bb_id: str = _BB_ID) -> dict:
    return {"requestor_id": "some-requestor", "request_token": bb_id}


def _qs(**extra) -> str:
    return "?" + urlencode({**_auth_params(), **extra})


def _entity_new_qry() -> str:
    """Minimal valid { "qry": { "details": {...} } } payload for /entity/new."""
    return json.dumps({"qry": {"details": {}}})


def _make_citizen(is_staff: bool = False):
    """Factory: create an active User, optionally staff, for JWT tests."""
    import uuid

    User = get_user_model()
    email = f"gs-auth-{uuid.uuid4().hex[:10]}@example.com"
    user = User.objects.create(email=email, is_staff=is_staff, is_active=True)
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return user


def _jwt_for(user) -> str:
    """Return a JWT access token string for the given user."""
    return str(RefreshToken.for_user(user).access_token)


def _make_drf_request(query: dict, auth_header: str | None = None) -> DRFRequest:
    """Build a DRF Request with query params and an optional Authorization header."""
    factory = APIRequestFactory()
    kwargs = {}
    if auth_header is not None:
        kwargs["HTTP_AUTHORIZATION"] = auth_header
    raw = factory.get("/govstack/scheduler/appointment/new", data=query, **kwargs)
    return DRFRequest(raw)


# ---------------------------------------------------------------------------
# GA-1: harness mode (GOVSTACK_SCHEDULER_REQUIRE_TOKEN=False, the default)
# ---------------------------------------------------------------------------

class SchedulerRoleHarnessModeTest(TestCase):
    """
    GA-1: GOVSTACK_SCHEDULER_REQUIRE_TOKEN=False (default) — regression guard.

    No @override_settings decorator: False is the test-settings default, so
    this reproduces the exact behaviour every other GovStack Scheduler test
    in this codebase already relies on.
    """

    def test_ga1_admin_tier_endpoint_reachable_with_any_credentials_in_harness_mode(self):
        """Any non-empty requestor_id/request_token reaches an admin-tier endpoint."""
        resp = self.client.post(ENTITY_NEW_URL + _qs(qry=_entity_new_qry()))
        self.assertEqual(resp.status_code, 201)

    def test_ga1_resource_tier_endpoint_reachable_with_any_credentials_in_harness_mode(self):
        """Any non-empty requestor_id/request_token reaches a resource-tier endpoint too."""
        resp = self.client.get(RESOURCE_AVAILABILITY_URL + _qs(qry=json.dumps({})))
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# GA-2..GA-4: production mode (GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True)
# ---------------------------------------------------------------------------

@override_settings(GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True)
class SchedulerRoleEnforcementTest(TestCase):
    """GA-2, GA-3, GA-4: role enforcement against a real GovStackRegisteredBB row."""

    def test_ga2_resource_role_bb_denied_admin_tier_endpoint(self):
        """GA-2: a role='resource' BB is denied (403) on an admin-tier endpoint."""
        GovStackRegisteredBB.objects.create(bb_id=_BB_ID, is_active=True, role="resource")
        resp = self.client.post(ENTITY_NEW_URL + _qs(qry=_entity_new_qry()))
        self.assertEqual(resp.status_code, 403)

    def test_ga3_resource_role_bb_passes_auth_on_resource_tier_endpoint(self):
        """GA-3: the SAME role='resource' BB passes auth on a resource-tier endpoint."""
        GovStackRegisteredBB.objects.create(bb_id=_BB_ID, is_active=True, role="resource")
        resp = self.client.get(RESOURCE_AVAILABILITY_URL + _qs(qry=json.dumps({})))
        self.assertEqual(resp.status_code, 200)

    def test_ga4_admin_role_bb_succeeds_on_admin_tier_endpoint(self):
        """GA-4: a role='admin' BB succeeds (201) on the admin-tier endpoint."""
        GovStackRegisteredBB.objects.create(bb_id=_BB_ID, is_active=True, role="admin")
        resp = self.client.post(ENTITY_NEW_URL + _qs(qry=_entity_new_qry()))
        self.assertEqual(resp.status_code, 201)

    def test_ga4b_organizer_role_bb_denied_admin_tier_endpoint(self):
        """Complements GA-2/GA-4: role='organizer' (below 'admin') is also denied (403)."""
        GovStackRegisteredBB.objects.create(bb_id=_BB_ID, is_active=True, role="organizer")
        resp = self.client.post(ENTITY_NEW_URL + _qs(qry=_entity_new_qry()))
        self.assertEqual(resp.status_code, 403)

    def test_ga4c_unregistered_bb_id_denied_before_role_check(self):
        """No matching GovStackRegisteredBB row at all → 401 (whitelist check runs first)."""
        resp = self.client.post(ENTITY_NEW_URL + _qs(qry=_entity_new_qry()))
        self.assertEqual(resp.status_code, 401)


# ---------------------------------------------------------------------------
# GA-5..GA-9: GovStackCitizenAuth unit tests
# ---------------------------------------------------------------------------

class GovStackCitizenAuthTest(TestCase):
    """Unit tests for GovStackCitizenAuth.authenticate()."""

    def test_ga5_valid_citizen_jwt_resolves_subscriber_identity(self):
        """GA-5: BB creds + a valid non-staff citizen JWT resolves (user, 'govstack_scheduler_subscriber')."""
        citizen = _make_citizen(is_staff=False)
        token = _jwt_for(citizen)
        request = _make_drf_request(_auth_params(), auth_header=f"Bearer {token}")

        result = GovStackCitizenAuth().authenticate(request)

        self.assertIsNotNone(result)
        user, auth = result
        self.assertEqual(user.pk, citizen.pk)
        self.assertEqual(auth, "govstack_scheduler_subscriber")
        self.assertEqual(request.META["_gs_resolved_role"], "subscriber")

    def test_ga6_no_authorization_header_falls_back_to_bb_only_trust(self):
        """GA-6: BB creds present, no Authorization header → identical outcome to GovStackSchedulerAuth."""
        request = _make_drf_request(_auth_params(), auth_header=None)

        result = GovStackCitizenAuth().authenticate(request)

        self.assertIsNotNone(result)
        user, auth = result
        self.assertIsNone(user)
        self.assertEqual(auth, "govstack_scheduler")
        self.assertEqual(request.META["_gs_resolved_role"], "admin")  # harness-mode default

    def test_ga7_invalid_jwt_raises_authentication_failed(self):
        """GA-7: an invalid/malformed JWT is rejected outright — never silently ignored."""
        request = _make_drf_request(_auth_params(), auth_header="Bearer not-a-real-jwt-token")

        with self.assertRaises(AuthenticationFailed):
            GovStackCitizenAuth().authenticate(request)

    def test_ga8_staff_user_jwt_raises_authentication_failed(self):
        """GA-8: a staff user's JWT is rejected — this path is for citizens only."""
        staff = _make_citizen(is_staff=True)
        token = _jwt_for(staff)
        request = _make_drf_request(_auth_params(), auth_header=f"Bearer {token}")

        with self.assertRaises(AuthenticationFailed):
            GovStackCitizenAuth().authenticate(request)

    def test_ga9_no_bb_creds_and_no_auth_header_returns_none(self):
        """GA-9: both BB credentials and Authorization header absent → falls through (None)."""
        request = _make_drf_request({}, auth_header=None)

        result = GovStackCitizenAuth().authenticate(request)

        self.assertIsNone(result)
