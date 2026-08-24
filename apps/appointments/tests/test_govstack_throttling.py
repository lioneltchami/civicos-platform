"""
test_govstack_throttling.py (apps.appointments)

Unit tests for GovStackBBIdentityThrottle (apps/appointments/govstack_throttling.py),
the Bug 7 fix from the GovStack Appointments/Scheduler BB audit
(MASTER_BB_CERTIFIABILITY_REPORT.md, "Appointments/Scheduler BB"):

  Bug 7: all 37 GovStack Scheduler views used DRF's stock ScopedRateThrottle,
  which falls back to client-IP-based throttling whenever request.user is
  falsy — which it always is for GovStack BB-to-BB calls, since
  GovStackSchedulerAuth.authenticate() never links a Django User. Two
  different registered BBs behind the same NAT/gateway therefore shared one
  rate-limit bucket, and a single BB switching IP address reset its own
  limit history.

Coverage:
  GT-1: get_cache_key() produces DIFFERENT cache keys for two requests that
        carry different request.META["_gs_requestor_id"] values, even from
        the exact same client IP — proves two BBs behind a shared
        NAT/gateway no longer collide into one bucket.
  GT-2: get_cache_key() produces the SAME cache key for two requests that
        carry the SAME request.META["_gs_requestor_id"], even from two
        DIFFERENT client IPs — proves a single BB's bucket survives an IP
        change (failover / redeploy / IP rotation).
  GT-3: allow_request() records history under two INDEPENDENT cache entries
        for two different requestor_ids sharing one IP — a concrete,
        cache-level proof that one BB's request volume never counts against
        another BB's quota, not just that the keys differ in isolation.
  GT-4: get_cache_key() falls back to the stock IP-based behaviour, without
        raising, when request.META has no "_gs_requestor_id" at all (e.g.
        GovStack auth did not run for this request / non-GovStack traffic
        reusing this throttle class) — the resulting key matches exactly
        what the real DRF ScopedRateThrottle would have produced for the
        same anonymous request.
  GT-5: allow_request() with no resolved BB identity still returns True
        (does not error) for a fresh cache — i.e. the fallback path is fully
        functional, not just non-crashing at the get_cache_key layer.

Test approach:
  Direct unit tests against GovStackBBIdentityThrottle, using APIRequestFactory
  + a DRF Request wrapper — the same minimal pattern already used for
  GovStackSchedulerAuth/GovStackCitizenAuth unit tests in
  test_govstack_auth.py's `_make_drf_request` helper. No HTTP client / URL
  routing is needed since this is testing the throttle class directly, not
  view wiring.
"""

from __future__ import annotations

from django.core.cache import cache
from django.test import TestCase
from rest_framework.request import Request as DRFRequest
from rest_framework.test import APIRequestFactory
from rest_framework.throttling import ScopedRateThrottle

from apps.appointments.govstack_throttling import GovStackBBIdentityThrottle

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCOPE = "govstack_bb"


class _FakeView:
    """Minimal stand-in for a GovStack Scheduler view: only throttle_scope matters."""

    throttle_scope = _SCOPE


def _make_request(
    requestor_id: str | None = None,
    remote_addr: str = "10.0.0.1",
) -> DRFRequest:
    """
    Build a DRF Request against an arbitrary GovStack Scheduler-style path,
    optionally stamping request.META["_gs_requestor_id"] the way
    GovStackSchedulerAuth.authenticate() does on successful authentication.
    """
    factory = APIRequestFactory()
    raw = factory.get("/govstack/scheduler/entity/list", REMOTE_ADDR=remote_addr)
    if requestor_id is not None:
        raw.META["_gs_requestor_id"] = requestor_id
    return DRFRequest(raw)


# ---------------------------------------------------------------------------
# GT-1 / GT-2: get_cache_key() keys on BB identity, not IP
# ---------------------------------------------------------------------------


class GetCacheKeyIdentityTest(TestCase):
    def test_gt1_different_requestor_ids_same_ip_get_different_cache_keys(self):
        """Two different BBs behind the same NAT/gateway must not share a bucket."""
        throttle = GovStackBBIdentityThrottle()
        throttle.scope = _SCOPE

        request_a = _make_request(requestor_id="bb-alpha", remote_addr="203.0.113.5")
        request_b = _make_request(requestor_id="bb-beta", remote_addr="203.0.113.5")

        key_a = throttle.get_cache_key(request_a, _FakeView())
        key_b = throttle.get_cache_key(request_b, _FakeView())

        self.assertIsNotNone(key_a)
        self.assertIsNotNone(key_b)
        self.assertNotEqual(key_a, key_b)
        self.assertIn("bb-alpha", key_a)
        self.assertIn("bb-beta", key_b)

    def test_gt2_same_requestor_id_different_ip_gets_same_cache_key(self):
        """A single BB's bucket must survive an IP change (failover / redeploy)."""
        throttle = GovStackBBIdentityThrottle()
        throttle.scope = _SCOPE

        request_old_ip = _make_request(requestor_id="bb-gamma", remote_addr="198.51.100.1")
        request_new_ip = _make_request(requestor_id="bb-gamma", remote_addr="198.51.100.99")

        key_old = throttle.get_cache_key(request_old_ip, _FakeView())
        key_new = throttle.get_cache_key(request_new_ip, _FakeView())

        self.assertEqual(key_old, key_new)


# ---------------------------------------------------------------------------
# GT-3: allow_request() records history under independent cache entries
# ---------------------------------------------------------------------------


class AllowRequestBucketIsolationTest(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_gt3_two_bbs_same_ip_do_not_share_throttle_history(self):
        """
        Concrete cache-level proof: BB-A's requests are recorded under BB-A's
        key only, BB-B's requests under BB-B's key only, even though both
        share the exact same client IP. Under the OLD (stock IP-based)
        behaviour these two would have collapsed into a single bucket.
        """
        view = _FakeView()
        shared_ip = "192.0.2.10"

        throttle_a = GovStackBBIdentityThrottle()
        request_a = _make_request(requestor_id="bb-shared-ip-a", remote_addr=shared_ip)
        for _ in range(3):
            allowed = throttle_a.allow_request(request_a, view)
            self.assertTrue(allowed)

        throttle_b = GovStackBBIdentityThrottle()
        request_b = _make_request(requestor_id="bb-shared-ip-b", remote_addr=shared_ip)
        allowed = throttle_b.allow_request(request_b, view)
        self.assertTrue(allowed)

        key_a = throttle_a.get_cache_key(request_a, view)
        key_b = throttle_b.get_cache_key(request_b, view)
        self.assertNotEqual(key_a, key_b)

        history_a = cache.get(key_a, [])
        history_b = cache.get(key_b, [])
        self.assertEqual(len(history_a), 3)
        self.assertEqual(len(history_b), 1)


# ---------------------------------------------------------------------------
# GT-4 / GT-5: fallback to stock IP-based behaviour when no BB identity
# ---------------------------------------------------------------------------


class FallbackToIpBasedThrottlingTest(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_gt4_no_requestor_id_falls_back_to_stock_ip_based_key(self):
        """
        With no resolved BB identity on the request (auth didn't run / non-
        GovStack traffic), the cache key must match exactly what the real
        DRF ScopedRateThrottle would produce for the same request — the
        fallback must be the genuine stock behaviour, not a reimplementation.
        """
        request = _make_request(requestor_id=None, remote_addr="203.0.113.77")
        view = _FakeView()

        gs_throttle = GovStackBBIdentityThrottle()
        gs_throttle.scope = _SCOPE
        gs_key = gs_throttle.get_cache_key(request, view)

        stock_throttle = ScopedRateThrottle()
        stock_throttle.scope = _SCOPE
        stock_key = stock_throttle.get_cache_key(request, view)

        self.assertIsNotNone(gs_key)
        self.assertEqual(gs_key, stock_key)

    def test_gt5_no_requestor_id_allow_request_does_not_error_and_allows(self):
        """The fallback path is fully functional end-to-end, not just non-crashing
        at the get_cache_key layer: allow_request() must return True on a fresh
        cache and must not raise."""
        request = _make_request(requestor_id=None, remote_addr="203.0.113.88")
        view = _FakeView()

        throttle = GovStackBBIdentityThrottle()
        allowed = throttle.allow_request(request, view)
        self.assertTrue(allowed)
