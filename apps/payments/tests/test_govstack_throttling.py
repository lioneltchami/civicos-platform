"""
test_govstack_throttling.py (apps.payments)

Unit tests for GovStackPaymentsIdentityThrottle (apps/payments/govstack_throttling.py),
the Finding 3 fix from the Payments certifiability audit:

  Finding 3: all 13 GovStack Payments views used DRF's stock ScopedRateThrottle,
  which falls back to client-IP-based throttling whenever request.user is
  falsy — which it always is for GovStack Payments calls, since Payments'
  header-whitelist auth classes (IsTrustedSourceBB, IsTrustedPayerFI,
  RequirePayerFI, IsTrustedBiller) never link a Django User. Two different
  callers behind the same NAT/gateway therefore shared one rate-limit bucket,
  and a single caller switching IP address reset its own limit history.

  Mirrors apps.appointments.govstack_throttling.GovStackBBIdentityThrottle /
  apps.appointments.tests.test_govstack_throttling exactly, keyed on
  request.META["_gs_payer_identity"] instead of "_gs_requestor_id".

Coverage:
  PT-1: get_cache_key() produces DIFFERENT cache keys for two requests that
        carry different request.META["_gs_payer_identity"] values, even from
        the exact same client IP — proves two callers behind a shared
        NAT/gateway no longer collide into one bucket.
  PT-2: get_cache_key() produces the SAME cache key for two requests that
        carry the SAME request.META["_gs_payer_identity"], even from two
        DIFFERENT client IPs — proves a single caller's bucket survives an IP
        change (failover / redeploy / IP rotation).
  PT-3: allow_request() records history under two INDEPENDENT cache entries
        for two different payer identities sharing one IP — a concrete,
        cache-level proof that one caller's request volume never counts
        against another caller's quota, not just that the keys differ in
        isolation.
  PT-4: get_cache_key() falls back to the stock IP-based behaviour, without
        raising, when request.META has no "_gs_payer_identity" at all (e.g.
        GovStack auth did not run for this request, or an AllowAnyBB/
        HasVoucherJWT-gated endpoint that never stashes this key) — the
        resulting key matches exactly what the real DRF ScopedRateThrottle
        would have produced for the same anonymous request.
  PT-5: allow_request() with no resolved payer identity still returns True
        (does not error) for a fresh cache — i.e. the fallback path is fully
        functional, not just non-crashing at the get_cache_key layer.
  PT-6: GovStackAPIView.throttle_classes actually wires in
        GovStackPaymentsIdentityThrottle (not the stock ScopedRateThrottle) —
        a concrete wiring-level check, not just a unit test of the throttle
        class in isolation.

Test approach:
  Direct unit tests against GovStackPaymentsIdentityThrottle, using
  APIRequestFactory + a DRF Request wrapper — the same minimal pattern
  already used for the Appointments sibling
  (apps/appointments/tests/test_govstack_throttling.py). No HTTP client / URL
  routing is needed since this is testing the throttle class directly, not
  view wiring (except PT-6, which checks the class attribute).
"""
from __future__ import annotations

from django.core.cache import cache
from django.test import TestCase
from rest_framework.request import Request as DRFRequest
from rest_framework.test import APIRequestFactory
from rest_framework.throttling import ScopedRateThrottle

from apps.payments.govstack_throttling import GovStackPaymentsIdentityThrottle
from apps.payments.govstack_views import GovStackAPIView

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCOPE = "govstack_bb"


class _FakeView:
    """Minimal stand-in for a GovStack Payments view: only throttle_scope matters."""

    throttle_scope = _SCOPE


def _make_request(
    payer_identity: str | None = None,
    remote_addr: str = "10.0.0.1",
) -> DRFRequest:
    """
    Build a DRF Request against an arbitrary GovStack Payments-style path,
    optionally stamping request.META["_gs_payer_identity"] the way
    apps.payments.govstack_auth._HeaderWhitelistBBPermission.has_permission()
    does on successful authentication.
    """
    factory = APIRequestFactory()
    raw = factory.get("/govstack/payments/bulk-payment", REMOTE_ADDR=remote_addr)
    if payer_identity is not None:
        raw.META["_gs_payer_identity"] = payer_identity
    return DRFRequest(raw)


# ---------------------------------------------------------------------------
# PT-1 / PT-2: get_cache_key() keys on payer identity, not IP
# ---------------------------------------------------------------------------

class GetCacheKeyIdentityTest(TestCase):
    def test_pt1_different_payer_identities_same_ip_get_different_cache_keys(self):
        """Two different callers behind the same NAT/gateway must not share a bucket."""
        throttle = GovStackPaymentsIdentityThrottle()
        throttle.scope = _SCOPE

        request_a = _make_request(payer_identity="payer-alpha", remote_addr="203.0.113.5")
        request_b = _make_request(payer_identity="payer-beta", remote_addr="203.0.113.5")

        key_a = throttle.get_cache_key(request_a, _FakeView())
        key_b = throttle.get_cache_key(request_b, _FakeView())

        self.assertIsNotNone(key_a)
        self.assertIsNotNone(key_b)
        self.assertNotEqual(key_a, key_b)
        self.assertIn("payer-alpha", key_a)
        self.assertIn("payer-beta", key_b)

    def test_pt2_same_payer_identity_different_ip_gets_same_cache_key(self):
        """A single caller's bucket must survive an IP change (failover / redeploy)."""
        throttle = GovStackPaymentsIdentityThrottle()
        throttle.scope = _SCOPE

        request_old_ip = _make_request(payer_identity="payer-gamma", remote_addr="198.51.100.1")
        request_new_ip = _make_request(payer_identity="payer-gamma", remote_addr="198.51.100.99")

        key_old = throttle.get_cache_key(request_old_ip, _FakeView())
        key_new = throttle.get_cache_key(request_new_ip, _FakeView())

        self.assertEqual(key_old, key_new)


# ---------------------------------------------------------------------------
# PT-3: allow_request() records history under independent cache entries
# ---------------------------------------------------------------------------

class AllowRequestBucketIsolationTest(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_pt3_two_payers_same_ip_do_not_share_throttle_history(self):
        """
        Concrete cache-level proof: payer-A's requests are recorded under
        payer-A's key only, payer-B's requests under payer-B's key only, even
        though both share the exact same client IP. Under the OLD (stock
        IP-based) behaviour these two would have collapsed into a single
        bucket.
        """
        view = _FakeView()
        shared_ip = "192.0.2.10"

        throttle_a = GovStackPaymentsIdentityThrottle()
        request_a = _make_request(payer_identity="payer-shared-ip-a", remote_addr=shared_ip)
        for _ in range(3):
            allowed = throttle_a.allow_request(request_a, view)
            self.assertTrue(allowed)

        throttle_b = GovStackPaymentsIdentityThrottle()
        request_b = _make_request(payer_identity="payer-shared-ip-b", remote_addr=shared_ip)
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
# PT-4 / PT-5: fallback to stock IP-based behaviour when no payer identity
# ---------------------------------------------------------------------------

class FallbackToIpBasedThrottlingTest(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_pt4_no_payer_identity_falls_back_to_stock_ip_based_key(self):
        """
        With no resolved payer identity on the request (auth didn't run /
        AllowAnyBB / HasVoucherJWT-gated endpoint), the cache key must match
        exactly what the real DRF ScopedRateThrottle would produce for the
        same request — the fallback must be the genuine stock behaviour, not
        a reimplementation.
        """
        request = _make_request(payer_identity=None, remote_addr="203.0.113.77")
        view = _FakeView()

        gs_throttle = GovStackPaymentsIdentityThrottle()
        gs_throttle.scope = _SCOPE
        gs_key = gs_throttle.get_cache_key(request, view)

        stock_throttle = ScopedRateThrottle()
        stock_throttle.scope = _SCOPE
        stock_key = stock_throttle.get_cache_key(request, view)

        self.assertIsNotNone(gs_key)
        self.assertEqual(gs_key, stock_key)

    def test_pt5_no_payer_identity_allow_request_does_not_error_and_allows(self):
        """The fallback path is fully functional end-to-end, not just non-crashing
        at the get_cache_key layer: allow_request() must return True on a fresh
        cache and must not raise."""
        request = _make_request(payer_identity=None, remote_addr="203.0.113.88")
        view = _FakeView()

        throttle = GovStackPaymentsIdentityThrottle()
        allowed = throttle.allow_request(request, view)
        self.assertTrue(allowed)


# ---------------------------------------------------------------------------
# PT-6: GovStackAPIView actually wires in GovStackPaymentsIdentityThrottle
# ---------------------------------------------------------------------------

class ThrottleWiringTest(TestCase):
    def test_pt6_govstack_api_view_uses_identity_throttle(self):
        """
        GovStackAPIView.throttle_classes must use GovStackPaymentsIdentityThrottle
        (not the stock ScopedRateThrottle) so every concrete Payments view that
        inherits from it gets identity-aware throttling automatically.
        """
        self.assertEqual(
            list(GovStackAPIView.throttle_classes),
            [GovStackPaymentsIdentityThrottle],
        )
        self.assertEqual(GovStackAPIView.throttle_scope, "govstack_bb")
