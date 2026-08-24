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
        "resource") + a real GovStackBBCredential — an admin-tier endpoint
        (EntityNewView) is denied (403).
  GA-3: GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True + role="resource" — a
        resource-tier endpoint (ResourceAvailabilityView) passes auth (200).
  GA-4: GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True + role="admin" — the
        admin-tier endpoint (EntityNewView) succeeds (200; Bug 2 fix — this
        endpoint previously returned 201).
  GA-4d..GA-4g (Finding #1 regression guards — bb_id is NOT a credential):
        a caller who knows a registered BB's PUBLIC bb_id but supplies it
        (or any other value that isn't the real provisioned secret) as
        request_token is rejected (401), even when requestor_id correctly
        names that BB; a BB with no provisioned GovStackBBCredential at all
        cannot authenticate with any token; the correct plaintext secret
        DOES authenticate successfully. These are the direct verification
        of this session's "definition of done": a caller who only knows a
        public bb_id can no longer authenticate as that BB.
  GA-5 through GA-9: GovStackCitizenAuth.authenticate() unit tests — valid
        citizen JWT resolves (user, "govstack_scheduler_subscriber"); no
        Authorization header falls back to BB-only trust; invalid/expired
        JWT raises AuthenticationFailed (never silently ignored); a staff
        user's JWT is rejected; both BB creds and Authorization header
        absent falls through (returns None).

Test approach:
  GA-1..GA-4g exercise the real view/URL layer via the Django test client,
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

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request as DRFRequest
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.tokens import RefreshToken

from apps.appointments.govstack_auth import GovStackCitizenAuth
from apps.appointments.models import GovStackBBCredential
from apps.payments.govstack_models import GovStackRegisteredBB

# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

ENTITY_NEW_URL = "/govstack/scheduler/entity/new"
RESOURCE_AVAILABILITY_URL = "/govstack/scheduler/resource/availability"

_BB_ID = "test-registered-bb"


def _make_bb_with_credential(
    bb_id: str = _BB_ID, role: str = "admin"
) -> tuple[GovStackRegisteredBB, str]:
    """
    Create a GovStackRegisteredBB row AND a real GovStackBBCredential for it.

    Returns (bb, plaintext_token) — the plaintext is only ever available at
    creation time (mirrors govstack_generate_bb_credential's one-shot print).
    """
    bb = GovStackRegisteredBB.objects.create(bb_id=bb_id, is_active=True, role=role)
    plaintext = GovStackBBCredential.generate_plaintext_token()
    credential = GovStackBBCredential(bb=bb)
    credential.set_token(plaintext)
    credential.save()
    return bb, plaintext


def _auth_params(requestor_id: str = "some-requestor", request_token: str = _BB_ID) -> dict:
    """
    Default params for GOVSTACK_SCHEDULER_REQUIRE_TOKEN=False (harness mode)
    tests, where any non-empty pair passes regardless of DB state — these
    values are intentionally NOT a valid (requestor_id, real secret) pair
    for production mode, since harness-mode tests never reach the DB lookup.
    """
    return {"requestor_id": requestor_id, "request_token": request_token}


def _qs(**extra) -> str:
    return "?" + urlencode({**_auth_params(), **extra})


def _qs_with(requestor_id: str, request_token: str, **extra) -> str:
    """Build a query string with an explicit (requestor_id, request_token) pair."""
    return "?" + urlencode({"requestor_id": requestor_id, "request_token": request_token, **extra})


def _entity_new_qry() -> str:
    """Minimal valid { "details": {...} } payload for /entity/new (the `qry`
    query PARAMETER's JSON value, single-nested — no additional outer "qry"
    wrapper key)."""
    return json.dumps({"details": {}})


def _make_citizen(is_staff: bool = False):
    """Factory: create an active User, optionally staff, for JWT tests."""
    import uuid

    User = get_user_model()  # noqa: N806
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
        self.assertEqual(resp.status_code, 200)

    def test_ga1_resource_tier_endpoint_reachable_with_any_credentials_in_harness_mode(self):
        """Any non-empty requestor_id/request_token reaches a resource-tier endpoint too."""
        resp = self.client.get(RESOURCE_AVAILABILITY_URL + _qs(qry=json.dumps({})))
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# GA-2..GA-4: production mode (GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True)
# ---------------------------------------------------------------------------


@override_settings(GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True)
class SchedulerRoleEnforcementTest(TestCase):
    """
    GA-2, GA-3, GA-4: role enforcement against a real GovStackRegisteredBB row
    PLUS a real, hashed GovStackBBCredential (Finding #1 fix) — requestor_id
    is now the bb_id identity lookup and request_token is verified against
    the credential's hash, never against bb_id itself.
    """

    def test_ga2_resource_role_bb_denied_admin_tier_endpoint(self):
        """GA-2: a role='resource' BB is denied (403) on an admin-tier endpoint."""
        _bb, token = _make_bb_with_credential(role="resource")
        resp = self.client.post(ENTITY_NEW_URL + _qs_with(_BB_ID, token, qry=_entity_new_qry()))
        self.assertEqual(resp.status_code, 403)

    def test_ga3_resource_role_bb_passes_auth_on_resource_tier_endpoint(self):
        """GA-3: the SAME role='resource' BB passes auth on a resource-tier endpoint."""
        _bb, token = _make_bb_with_credential(role="resource")
        resp = self.client.get(
            RESOURCE_AVAILABILITY_URL + _qs_with(_BB_ID, token, qry=json.dumps({}))
        )
        self.assertEqual(resp.status_code, 200)

    def test_ga4_admin_role_bb_succeeds_on_admin_tier_endpoint(self):
        """GA-4: a role='admin' BB succeeds (200; Bug 2 fix — was 201) on the admin-tier endpoint."""  # noqa: E501
        _bb, token = _make_bb_with_credential(role="admin")
        resp = self.client.post(ENTITY_NEW_URL + _qs_with(_BB_ID, token, qry=_entity_new_qry()))
        self.assertEqual(resp.status_code, 200)

    def test_ga4b_organizer_role_bb_denied_admin_tier_endpoint(self):
        """Complements GA-2/GA-4: role='organizer' (below 'admin') is also denied (403)."""
        _bb, token = _make_bb_with_credential(role="organizer")
        resp = self.client.post(ENTITY_NEW_URL + _qs_with(_BB_ID, token, qry=_entity_new_qry()))
        self.assertEqual(resp.status_code, 403)

    def test_ga4c_unregistered_bb_id_denied_before_role_check(self):
        """No matching GovStackRegisteredBB row at all → 401 (identity lookup runs first)."""
        resp = self.client.post(
            ENTITY_NEW_URL + _qs_with("no-such-bb", "irrelevant-token", qry=_entity_new_qry())
        )
        self.assertEqual(resp.status_code, 401)

    # -----------------------------------------------------------------------
    # Finding #1 regression guards — bb_id must NEVER work as a bearer credential
    # -----------------------------------------------------------------------

    def test_ga4d_bb_id_used_as_request_token_is_rejected(self):
        """
        GA-4d (Finding #1 core verification): a caller who knows a registered
        BB's PUBLIC bb_id and supplies THAT SAME VALUE as request_token
        (the exact pre-fix vulnerability: request_token == bb_id) is now
        rejected. Direct proof that "a caller who only knows a public bb_id
        can no longer authenticate as that BB."
        """
        _make_bb_with_credential(role="admin")  # real credential exists but is never used
        resp = self.client.post(
            ENTITY_NEW_URL
            + _qs_with(_BB_ID, _BB_ID, qry=_entity_new_qry())  # request_token = bb_id itself
        )
        self.assertEqual(resp.status_code, 401)

    def test_ga4e_correct_requestor_id_wrong_token_is_rejected(self):
        """GA-4e: valid requestor_id (real bb_id) + an arbitrary wrong token → 401."""
        _make_bb_with_credential(role="admin")
        resp = self.client.post(
            ENTITY_NEW_URL + _qs_with(_BB_ID, "totally-made-up-guess", qry=_entity_new_qry())
        )
        self.assertEqual(resp.status_code, 401)

    def test_ga4f_registered_bb_with_no_provisioned_credential_is_rejected(self):
        """GA-4f: a GovStackRegisteredBB row with NO GovStackBBCredential row rejects any token."""
        GovStackRegisteredBB.objects.create(bb_id=_BB_ID, is_active=True, role="admin")
        resp = self.client.post(
            ENTITY_NEW_URL + _qs_with(_BB_ID, "any-value-at-all", qry=_entity_new_qry())
        )
        self.assertEqual(resp.status_code, 401)

    def test_ga4g_correct_plaintext_secret_authenticates_successfully(self):
        """GA-4g: the real provisioned secret (not bb_id) authenticates correctly (200; Bug 2 fix — was 201)."""  # noqa: E501
        _bb, token = _make_bb_with_credential(role="admin")
        self.assertNotEqual(token, _BB_ID)  # sanity: the secret is not the identifier
        resp = self.client.post(ENTITY_NEW_URL + _qs_with(_BB_ID, token, qry=_entity_new_qry()))
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# GA-10..GA-12: Bug 5 fix — GOVSTACK_SCHEDULER_REQUIRE_TOKEN /
# GOVSTACK_REQUIRE_REGISTERED_BB safe-default regression guards
# (MASTER_BB_CERTIFIABILITY_REPORT.md "Appointments/Scheduler BB")
# ---------------------------------------------------------------------------


class GovStackSchedulerAuthSafeDefaultsTest(TestCase):
    """
    Bug 5 fix: before this fix, GOVSTACK_SCHEDULER_REQUIRE_TOKEN and
    GOVSTACK_REQUIRE_REGISTERED_BB were defined ONLY in config/settings/
    production.py. base.py, development.py, and config/settings/test.py
    (which this test suite runs under) never defined either setting at all,
    so getattr(settings, "GOVSTACK_SCHEDULER_REQUIRE_TOKEN", False) in
    GovStackSchedulerAuth.authenticate() silently resolved to False
    (fail-open, admin-role-for-any-non-empty-token) in every non-production
    settings module purely because the attribute was UNDEFINED — not because
    of any deliberate per-environment safe-default choice.

    These tests assert config.settings.test (which inherits from base.py)
    now resolves an EXPLICIT, DEFINED boolean for both flags, proving the
    fix closes the "undefined attribute" gap rather than continuing to rely
    on getattr()'s fallback masking the absence.
    """

    def test_ga10_scheduler_require_token_is_explicitly_defined_boolean_in_test_settings(self):
        """
        GA-10 (Bug 5): GOVSTACK_SCHEDULER_REQUIRE_TOKEN is a real, defined
        Django setting under config.settings.test — not an undefined
        attribute silently defaulting via getattr() — and resolves to the
        safe-for-dev default of False (base.py's
        env.bool("GOVSTACK_SCHEDULER_REQUIRE_TOKEN", default=False)).
        """
        self.assertTrue(hasattr(settings, "GOVSTACK_SCHEDULER_REQUIRE_TOKEN"))
        self.assertIsInstance(settings.GOVSTACK_SCHEDULER_REQUIRE_TOKEN, bool)
        self.assertFalse(settings.GOVSTACK_SCHEDULER_REQUIRE_TOKEN)

    def test_ga11_require_registered_bb_is_explicitly_defined_boolean_in_test_settings(self):
        """
        GA-11 (Bug 5): GOVSTACK_REQUIRE_REGISTERED_BB is likewise now
        explicitly defined in base.py (previously production.py-only) and
        resolves to the safe-for-dev default of False under
        config.settings.test.
        """
        self.assertTrue(hasattr(settings, "GOVSTACK_REQUIRE_REGISTERED_BB"))
        self.assertIsInstance(settings.GOVSTACK_REQUIRE_REGISTERED_BB, bool)
        self.assertFalse(settings.GOVSTACK_REQUIRE_REGISTERED_BB)

    @override_settings(GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True)
    def test_ga12_forced_true_without_valid_token_is_rejected(self):
        """
        GA-12 (Bug 5): with GOVSTACK_SCHEDULER_REQUIRE_TOKEN forced True (as
        production.py's own default already does), a request with no
        matching GovStackRegisteredBB row at all — i.e. no valid token
        relationship — is correctly rejected/downgraded rather than silently
        authenticating as admin. This complements the pre-existing
        GA-4c/GA-4d/GA-4e/GA-4f coverage above (already exercising this
        production-mode rejection path) by anchoring an explicit assertion to
        the Bug 5 fix itself.
        """
        resp = self.client.post(
            ENTITY_NEW_URL + _qs_with("unknown-bb-ga12", "not-a-real-token", qry=_entity_new_qry())
        )
        self.assertIn(resp.status_code, (401, 403))


# ---------------------------------------------------------------------------
# GA-5..GA-9: GovStackCitizenAuth unit tests
# ---------------------------------------------------------------------------


class GovStackCitizenAuthTest(TestCase):
    """Unit tests for GovStackCitizenAuth.authenticate()."""

    def test_ga5_valid_citizen_jwt_resolves_subscriber_identity(self):
        """GA-5: BB creds + a valid non-staff citizen JWT resolves (user, 'govstack_scheduler_subscriber')."""  # noqa: E501
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
        """GA-6: BB creds present, no Authorization header → identical outcome to GovStackSchedulerAuth."""  # noqa: E501
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


# ---------------------------------------------------------------------------
# Round 2 certifiability re-audit fix (MEDIUM): audit trail for BB-credential
# creation/rotation via govstack_generate_bb_credential
# ---------------------------------------------------------------------------


class GovStackGenerateBBCredentialAuditTest(TestCase):
    """
    FIX 4: the govstack_generate_bb_credential management command must write
    a real, queryable, tamper-evident BookingAuditLog entry for both the
    initial credential creation AND every --rotate call.
    """

    def test_create_writes_admin_credential_mutated_audit_event(self):
        from django.core.management import call_command

        from apps.appointments.models import BookingAuditLog

        bb = GovStackRegisteredBB.objects.create(
            bb_id="audit-test-bb", is_active=True, role="admin"
        )

        call_command("govstack_generate_bb_credential", "--bb-id", bb.bb_id)

        event = (
            BookingAuditLog.objects.filter(
                action=BookingAuditLog.ACTION_ADMIN_CREDENTIAL_MUTATED,
            )
            .order_by("-timestamp")
            .first()
        )
        self.assertIsNotNone(event)
        self.assertIsNone(event.booking)
        self.assertEqual(event.detail["operation"], "create")
        self.assertEqual(event.detail["resource_pk"], bb.bb_id)

    def test_rotate_writes_admin_credential_mutated_audit_event_with_rotate_operation(self):
        from django.core.management import call_command

        from apps.appointments.models import BookingAuditLog

        bb = GovStackRegisteredBB.objects.create(
            bb_id="audit-test-bb-rotate", is_active=True, role="admin"
        )
        call_command("govstack_generate_bb_credential", "--bb-id", bb.bb_id)
        call_command("govstack_generate_bb_credential", "--bb-id", bb.bb_id, "--rotate")

        rotate_events = BookingAuditLog.objects.filter(
            action=BookingAuditLog.ACTION_ADMIN_CREDENTIAL_MUTATED,
            detail__operation="rotate",
        )
        self.assertEqual(rotate_events.count(), 1)
        self.assertEqual(rotate_events.first().detail["resource_pk"], bb.bb_id)
