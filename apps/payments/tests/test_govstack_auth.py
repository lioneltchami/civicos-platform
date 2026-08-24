"""
test_govstack_auth.py

Unit tests for GovStack Payments BB authentication and permission classes.

Focus: IsTrustedSourceBB — the permission class that gates G2P endpoints
       behind the X-Registering-Institution-ID header and (in production mode)
       the GovStackRegisteredBB database whitelist.

Coverage matrix:
  AUTH-1: GOVSTACK_REQUIRE_REGISTERED_BB=True + active row + matching ID → grant
  AUTH-2: GOVSTACK_REQUIRE_REGISTERED_BB=True + no row in table → deny
  AUTH-3: GOVSTACK_REQUIRE_REGISTERED_BB=True + inactive row + matching ID → deny
  AUTH-4: missing header + GOVSTACK_REQUIRE_REGISTERED_BB=True (production mode) → deny
  AUTH-4b: missing header + GOVSTACK_REQUIRE_REGISTERED_BB=False (harness mode) → GRANT
           (P0 fix: the real harness never sends this header on any G2P endpoint —
           confirmed against the live g2p_*.js step definitions — so a missing
           header must degrade to AllowAnyBB-equivalent behaviour in harness mode,
           NOT deny as a stale prior version of this suite asserted.)
  AUTH-5: institution_id > 20 chars → deny (applies in both modes; only relevant
          when a header IS present — see AUTH-5b)
  AUTH-6: GOVSTACK_REQUIRE_REGISTERED_BB=False (harness mode) + header present →
          any valid (≤20-char) header value passes without a DB lookup
  AUTH-7: GOVSTACK_REQUIRE_REGISTERED_BB=True + active row + WRONG ID → deny
  AUTH-8: seed_govstack_vouchers creates GovStackRegisteredBB(bb_id="GS-HARNESS")

Also covers IsTrustedPayerFI and RequirePayerFI — the P2G caller-identity
permission classes added for Issue B (see SPEC_GOVSTACK_PAYMENTS_BB.md §24.2).
Both reuse the SAME GovStackRegisteredBB whitelist table as IsTrustedSourceBB
(no new model), gated by a separate settings flag,
GOVSTACK_REQUIRE_REGISTERED_PAYER_FI:

  PAYERFI-1:  GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=True + active row +
              matching ID → grant
  PAYERFI-2:  GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=True + no row in table
              → deny
  PAYERFI-3:  GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=True + inactive row +
              matching ID → deny
  PAYERFI-4:  missing header + GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=True
              (production mode) → deny
  PAYERFI-4b: missing header + GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=False
              (harness mode, the default) → GRANT (mode-gated, mirrors
              IsTrustedSourceBB's AUTH-4b)
  PAYERFI-5:  payer_fi_id > 20 chars → deny (matches the live spec's
              X-PayerFI-Id maxLength: 20)
  PAYERFI-6:  GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=False (harness mode) +
              header present → any valid (≤20-char) header value passes
              without a DB lookup
  PAYERFI-7:  GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=True + active row +
              WRONG ID → deny
  PAYERFI-ALT-HEADER: the PayerFI-Id variant (no "X-" prefix) is accepted,
              mirroring the upstream P2G YAMLs' inconsistent spelling

RequirePayerFI — the fail-closed variant used ONLY by MarkBillPaidView:

  PAYERFI-FC-1: missing header + harness mode (flag False, the default) →
              DENY (this is the whole point of the fail-closed variant — it
              does NOT degrade to AllowAnyBB-equivalent behaviour the way
              IsTrustedPayerFI/IsTrustedSourceBB do)
  PAYERFI-FC-2: missing header + production mode (flag True) → deny
  PAYERFI-FC-3: header present + oversized (>20 chars) → deny
  PAYERFI-FC-4: header present + production mode + whitelist hit → grant
  PAYERFI-FC-5: header present + production mode + whitelist miss → deny
  PAYERFI-FC-6: header present + production mode + inactive whitelist row
              → deny
  PAYERFI-FC-7: header present + harness mode (flag False) → grant without
              a DB lookup (only the "header absent" branch is fail-closed)

Test approach:
  AUTH-1 through AUTH-7 call IsTrustedSourceBB.has_permission() directly,
  bypassing the view/URL layer.  This is the correct unit test pattern for
  DRF permission classes — it tests the permission logic in isolation without
  coupling to any specific view's permission_classes configuration.

  DRF Request objects are created via APIRequestFactory (wraps a Django
  HttpRequest, exposes .headers via Django's HttpHeaders interface).
  The `view` argument to has_permission() is None — IsTrustedSourceBB never
  uses it.

  AUTH-8 is an integration test that actually invokes the management command.

Setting gate:
  GOVSTACK_REQUIRE_REGISTERED_BB follows the same two-mode pattern as
  GOVSTACK_VOUCHER_REQUIRE_JWT (GAP-3).  All DB-mode tests decorate with
  @override_settings(GOVSTACK_REQUIRE_REGISTERED_BB=True).
  Tests that run in harness mode (False, the default) need no decorator.
"""

from __future__ import annotations

from io import StringIO

from django.test import TestCase, override_settings
from rest_framework.request import Request as DRFRequest
from rest_framework.test import APIRequestFactory

from apps.payments.govstack_auth import (
    IsTrustedPayerFI,
    IsTrustedSourceBB,
    RequirePayerFI,
)
from apps.payments.govstack_models import GovStackRegisteredBB

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_ID = "GS-HARNESS"
_TOO_LONG_ID = "A" * 21  # 21 chars — exceeds the 20-char limit


def _make_request(institution_id: str | None = None) -> DRFRequest:
    """
    Build a DRF Request with an optional X-Registering-Institution-ID header.

    When institution_id is None, no header is sent (AUTH-4 scenario).
    Uses POST / — the HTTP method and path do not affect permission logic.
    """
    factory = APIRequestFactory()
    if institution_id is not None:
        raw = factory.post(
            "/",
            HTTP_X_REGISTERING_INSTITUTION_ID=institution_id,
        )
    else:
        raw = factory.post("/")
    return DRFRequest(raw)


def _make_payer_fi_request(payer_fi_id: str | None = None) -> DRFRequest:
    """
    Build a DRF Request with an optional X-PayerFI-Id header.

    When payer_fi_id is None, no header is sent (PAYERFI-4 / PAYERFI-FC-1
    scenarios). Uses POST / — the HTTP method and path do not affect
    permission logic.
    """
    factory = APIRequestFactory()
    if payer_fi_id is not None:
        raw = factory.post("/", HTTP_X_PAYERFI_ID=payer_fi_id)
    else:
        raw = factory.post("/")
    return DRFRequest(raw)


# ---------------------------------------------------------------------------
# IsTrustedSourceBB unit tests (AUTH-1 through AUTH-7)
# ---------------------------------------------------------------------------


class IsTrustedSourceBBTest(TestCase):
    """
    Unit tests for IsTrustedSourceBB.has_permission().

    Each test creates (or omits) a GovStackRegisteredBB row, constructs a
    DRF Request with the appropriate header, and asserts the permission result.
    """

    def setUp(self) -> None:
        self.perm = IsTrustedSourceBB()

    # ── AUTH-1 ────────────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_REQUIRE_REGISTERED_BB=True)
    def test_auth1_active_row_matching_id_grants_access(self) -> None:
        """
        GOVSTACK_REQUIRE_REGISTERED_BB=True: an active GovStackRegisteredBB row
        whose bb_id matches the header value must grant access.

        This is the primary production-path positive test.
        """
        GovStackRegisteredBB.objects.create(bb_id=_VALID_ID, is_active=True)
        request = _make_request(_VALID_ID)
        self.assertTrue(
            self.perm.has_permission(request, None),
            "Expected True: active row with matching bb_id must grant access.",
        )

    # ── AUTH-2 ────────────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_REQUIRE_REGISTERED_BB=True)
    def test_auth2_no_row_in_table_denies_access(self) -> None:
        """
        GOVSTACK_REQUIRE_REGISTERED_BB=True: when the GovStackRegisteredBB table
        has no matching row for the institution_id, access must be denied.

        This is the primary production-path negative test — ensures that a
        BB that has not been whitelisted cannot call the G2P endpoints.
        """
        # Deliberately do NOT create any GovStackRegisteredBB row.
        request = _make_request(_VALID_ID)
        self.assertFalse(
            self.perm.has_permission(request, None),
            "Expected False: no matching row in the whitelist must deny access.",
        )

    # ── AUTH-3 ────────────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_REQUIRE_REGISTERED_BB=True)
    def test_auth3_inactive_row_denies_access(self) -> None:
        """
        GOVSTACK_REQUIRE_REGISTERED_BB=True: a GovStackRegisteredBB row with
        is_active=False must be rejected even though the bb_id matches.

        This supports temporary suspension of a BB's access without deletion.
        """
        GovStackRegisteredBB.objects.create(bb_id=_VALID_ID, is_active=False)
        request = _make_request(_VALID_ID)
        self.assertFalse(
            self.perm.has_permission(request, None),
            "Expected False: inactive row must deny access regardless of bb_id match.",
        )

    # ── AUTH-4 ────────────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_REQUIRE_REGISTERED_BB=True)
    def test_auth4_missing_header_denies_access_in_production_mode(self) -> None:
        """
        Missing X-Registering-Institution-ID header must deny access when
        GOVSTACK_REQUIRE_REGISTERED_BB=True (production mode) — production must
        not allow anonymous callers.

        Contrast with AUTH-4b: in harness mode (=False), a missing header is
        tolerated — see that test for the real, verified harness contract.
        """
        request = _make_request(institution_id=None)
        self.assertFalse(
            self.perm.has_permission(request, None),
            "Expected False: missing header must deny access in production mode.",
        )

    # ── AUTH-5 ────────────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_REQUIRE_REGISTERED_BB=True)
    def test_auth5_institution_id_too_long_denies_access(self) -> None:
        """
        institution_id > 20 chars must always deny access, regardless of mode.

        The length check runs before the DB lookup, so no GovStackRegisteredBB
        row is needed (or consulted) in this test.
        """
        # Ensure no accidental match even if the long string were somehow stored.
        request = _make_request(_TOO_LONG_ID)
        self.assertFalse(
            self.perm.has_permission(request, None),
            f"Expected False: {len(_TOO_LONG_ID)}-char institution_id must be rejected.",
        )

    # ── AUTH-6 ────────────────────────────────────────────────────────────────

    def test_auth6_harness_mode_any_valid_header_passes(self) -> None:
        """
        GOVSTACK_REQUIRE_REGISTERED_BB=False (default, harness mode):
        any non-empty, ≤ 20-char header value passes without a DB lookup.

        This confirms backward compatibility — the harness and existing test
        infrastructure do NOT need to seed GovStackRegisteredBB rows.
        No @override_settings decorator needed since False is the test default.
        """
        # Deliberately leave GovStackRegisteredBB table empty.
        request = _make_request("ANY-VALID-ID")
        self.assertTrue(
            self.perm.has_permission(request, None),
            "Expected True: harness mode must pass any valid header without DB lookup.",
        )

    # ── AUTH-7 ────────────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_REQUIRE_REGISTERED_BB=True)
    def test_auth7_wrong_id_with_different_active_row_denies_access(self) -> None:
        """
        GOVSTACK_REQUIRE_REGISTERED_BB=True: the whitelist table has an active row
        for a DIFFERENT bb_id — the request carries a non-matching ID.

        This distinguishes AUTH-2 (empty table) from a non-empty table whose
        entries don't match the caller — both must deny, but through different
        code paths (`.exists()` with a filter vs. empty queryset).
        """
        GovStackRegisteredBB.objects.create(bb_id="OTHER-BB", is_active=True)
        request = _make_request(_VALID_ID)  # "GS-HARNESS" ≠ "OTHER-BB"
        self.assertFalse(
            self.perm.has_permission(request, None),
            "Expected False: non-matching bb_id must be denied even when table is non-empty.",
        )

    # ── AUTH-4b — harness mode (P0 regression test) ─────────────────────────

    def test_auth4b_missing_header_grants_access_in_harness_mode(self) -> None:
        """
        P0 regression test: missing header must GRANT access in harness mode
        (GOVSTACK_REQUIRE_REGISTERED_BB=False, the test/harness default).

        This is the exact scenario the real GovStack Cucumber harness sends on
        every one of the 4 G2P endpoints (confirmed directly against the live
        g2p_register_beneficiary.js / g2p_update_beneficiary_details.js /
        g2p_bulk_payment.js / g2p_prepayment_validation.js step-definition
        files — none of them ever set X-Registering-Institution-ID).

        Before the fix, IsTrustedSourceBB.has_permission() denied this
        unconditionally ("Header presence ... is ALWAYS applied regardless of
        mode"), which meant RegisterBeneficiaryView / UpdateBeneficiaryView
        would have rejected every real harness scenario, including the smoke
        tests. This test is the direct regression guard for that bug.
        """
        request = _make_request(institution_id=None)
        self.assertTrue(
            self.perm.has_permission(request, None),
            "Expected True: missing header must be tolerated in harness mode, "
            "matching real (headerless) harness behaviour.",
        )

    # ── AUTH-5b — harness mode ─────────────────────────────────────────────

    def test_auth5b_too_long_id_denies_in_harness_mode(self) -> None:
        """
        institution_id > 20 chars is rejected in harness mode
        (GOVSTACK_REQUIRE_REGISTERED_BB=False) as well — the length check is
        mandatory in both modes.
        """
        request = _make_request(_TOO_LONG_ID)
        self.assertFalse(
            self.perm.has_permission(request, None),
            f"Expected False: {len(_TOO_LONG_ID)}-char institution_id rejected in harness mode.",
        )

    # ── AUTH — exact 20-char boundary ─────────────────────────────────────

    @override_settings(GOVSTACK_REQUIRE_REGISTERED_BB=True)
    def test_auth_boundary_exactly_20_chars_allowed(self) -> None:
        """
        institution_id of exactly 20 chars (max allowed) must pass the length check.
        Complements AUTH-5 which uses 21 chars (just over the boundary).
        """
        exactly_20 = "B" * 20
        GovStackRegisteredBB.objects.create(bb_id=exactly_20, is_active=True)
        request = _make_request(exactly_20)
        self.assertTrue(
            self.perm.has_permission(request, None),
            "Expected True: exactly-20-char institution_id must be accepted.",
        )


# ---------------------------------------------------------------------------
# IsTrustedPayerFI unit tests (PAYERFI-1 through PAYERFI-7) — Issue B
# ---------------------------------------------------------------------------

_PAYER_FI_VALID_ID = "FI-HARNESS"
_PAYER_FI_TOO_LONG_ID = "C" * 21  # 21 chars — exceeds the 20-char limit


class IsTrustedPayerFITest(TestCase):
    """
    Unit tests for IsTrustedPayerFI.has_permission() — the P2G caller-identity
    permission class added for Issue B. Mirrors IsTrustedSourceBBTest's
    structure/approach exactly, but against the X-PayerFI-Id header and the
    GOVSTACK_REQUIRE_REGISTERED_PAYER_FI settings flag.

    Reuses the SAME GovStackRegisteredBB whitelist table as IsTrustedSourceBB
    (no separate model/migration) — see SPEC_GOVSTACK_PAYMENTS_BB.md §24.2.
    """

    def setUp(self) -> None:
        self.perm = IsTrustedPayerFI()

    # ── PAYERFI-1 ────────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=True)
    def test_payerfi1_active_row_matching_id_grants_access(self) -> None:
        GovStackRegisteredBB.objects.create(bb_id=_PAYER_FI_VALID_ID, is_active=True)
        request = _make_payer_fi_request(_PAYER_FI_VALID_ID)
        self.assertTrue(
            self.perm.has_permission(request, None),
            "Expected True: active row with matching bb_id must grant access.",
        )

    # ── PAYERFI-2 ────────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=True)
    def test_payerfi2_no_row_in_table_denies_access(self) -> None:
        # Deliberately do NOT create any GovStackRegisteredBB row.
        request = _make_payer_fi_request(_PAYER_FI_VALID_ID)
        self.assertFalse(
            self.perm.has_permission(request, None),
            "Expected False: no matching row in the whitelist must deny access.",
        )

    # ── PAYERFI-3 ────────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=True)
    def test_payerfi3_inactive_row_denies_access(self) -> None:
        GovStackRegisteredBB.objects.create(bb_id=_PAYER_FI_VALID_ID, is_active=False)
        request = _make_payer_fi_request(_PAYER_FI_VALID_ID)
        self.assertFalse(
            self.perm.has_permission(request, None),
            "Expected False: inactive row must deny access regardless of bb_id match.",
        )

    # ── PAYERFI-4 ────────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=True)
    def test_payerfi4_missing_header_denies_access_in_production_mode(self) -> None:
        request = _make_payer_fi_request(payer_fi_id=None)
        self.assertFalse(
            self.perm.has_permission(request, None),
            "Expected False: missing header must deny access in production mode.",
        )

    # ── PAYERFI-5 ────────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=True)
    def test_payerfi5_payer_fi_id_too_long_denies_access(self) -> None:
        # Length check runs before the DB lookup — no whitelist row needed.
        request = _make_payer_fi_request(_PAYER_FI_TOO_LONG_ID)
        self.assertFalse(
            self.perm.has_permission(request, None),
            f"Expected False: {len(_PAYER_FI_TOO_LONG_ID)}-char payer_fi_id must be rejected.",
        )

    # ── PAYERFI-6 ────────────────────────────────────────────────────────────

    def test_payerfi6_harness_mode_any_valid_header_passes(self) -> None:
        """
        GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=False (default, harness mode):
        any non-empty, ≤ 20-char header value passes without a DB lookup.
        """
        # Deliberately leave GovStackRegisteredBB table empty.
        request = _make_payer_fi_request("ANY-VALID-FI")
        self.assertTrue(
            self.perm.has_permission(request, None),
            "Expected True: harness mode must pass any valid header without DB lookup.",
        )

    # ── PAYERFI-7 ────────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=True)
    def test_payerfi7_wrong_id_with_different_active_row_denies_access(self) -> None:
        GovStackRegisteredBB.objects.create(bb_id="OTHER-FI", is_active=True)
        request = _make_payer_fi_request(_PAYER_FI_VALID_ID)  # "FI-HARNESS" ≠ "OTHER-FI"
        self.assertFalse(
            self.perm.has_permission(request, None),
            "Expected False: non-matching bb_id must be denied even when table is non-empty.",
        )

    # ── PAYERFI-4b — harness mode ────────────────────────────────────────────

    def test_payerfi4b_missing_header_grants_access_in_harness_mode(self) -> None:
        """
        Missing header must GRANT access in harness mode
        (GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=False, the test/harness default) —
        IsTrustedPayerFI degrades to AllowAnyBB-equivalent behaviour here,
        exactly like IsTrustedSourceBB's AUTH-4b. Contrast with
        RequirePayerFITest's PAYERFI-FC-1, which asserts the opposite for the
        fail-closed variant used by MarkBillPaidView.
        """
        request = _make_payer_fi_request(payer_fi_id=None)
        self.assertTrue(
            self.perm.has_permission(request, None),
            "Expected True: missing header must be tolerated in harness mode.",
        )

    # ── PAYERFI-5b — harness mode ─────────────────────────────────────────────

    def test_payerfi5b_too_long_id_denies_in_harness_mode(self) -> None:
        request = _make_payer_fi_request(_PAYER_FI_TOO_LONG_ID)
        self.assertFalse(
            self.perm.has_permission(request, None),
            f"Expected False: {len(_PAYER_FI_TOO_LONG_ID)}-char payer_fi_id rejected in harness mode.",  # noqa: E501
        )

    # ── PAYERFI — exact 20-char boundary ───────────────────────────────────

    @override_settings(GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=True)
    def test_payerfi_boundary_exactly_20_chars_allowed(self) -> None:
        exactly_20 = "D" * 20
        GovStackRegisteredBB.objects.create(bb_id=exactly_20, is_active=True)
        request = _make_payer_fi_request(exactly_20)
        self.assertTrue(
            self.perm.has_permission(request, None),
            "Expected True: exactly-20-char payer_fi_id must be accepted.",
        )

    # ── PAYERFI-ALT-HEADER ──────────────────────────────────────────────────

    def test_payerfi_alt_header_name_payerfi_id_without_x_prefix_accepted(self) -> None:
        """
        The upstream P2G YAMLs spell the header inconsistently — 'PayerFI-Id'
        (no "X-" prefix) in one file, alongside the more common 'X-PayerFI-Id'.
        IsTrustedPayerFI must accept both.
        """
        factory = APIRequestFactory()
        raw = factory.post("/", HTTP_PAYERFI_ID="ANY-VALID-FI")
        request = DRFRequest(raw)
        self.assertTrue(
            self.perm.has_permission(request, None),
            "Expected True: the 'PayerFI-Id' header variant (no X- prefix) must be accepted.",
        )


# ---------------------------------------------------------------------------
# RequirePayerFI unit tests (PAYERFI-FC-1 through PAYERFI-FC-7) — Issue B
# ---------------------------------------------------------------------------


class RequirePayerFITest(TestCase):
    """
    Unit tests for RequirePayerFI.has_permission() — the fail-closed variant
    of IsTrustedPayerFI used ONLY by MarkBillPaidView.

    Unlike every other permission class in this module, a missing/empty
    header is ALWAYS denied here, in every settings mode. When a header IS
    present, behaviour is identical to IsTrustedPayerFI (length check, and in
    production mode, the whitelist check).
    """

    def setUp(self) -> None:
        self.perm = RequirePayerFI()

    # ── PAYERFI-FC-1 — the defining behaviour of this class ─────────────────

    def test_payerfi_fc1_missing_header_denies_in_harness_mode(self) -> None:
        """
        Missing header must DENY access even in harness mode
        (GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=False, the default) — this is
        the entire point of the fail-closed variant. Contrast with
        IsTrustedPayerFITest.test_payerfi4b_missing_header_grants_access_in_harness_mode.
        """
        request = _make_payer_fi_request(payer_fi_id=None)
        self.assertFalse(
            self.perm.has_permission(request, None),
            "Expected False: RequirePayerFI must deny a missing header in every mode.",
        )

    # ── PAYERFI-FC-2 ─────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=True)
    def test_payerfi_fc2_missing_header_denies_in_production_mode(self) -> None:
        request = _make_payer_fi_request(payer_fi_id=None)
        self.assertFalse(
            self.perm.has_permission(request, None),
            "Expected False: missing header must deny access in production mode.",
        )

    # ── PAYERFI-FC-3 ─────────────────────────────────────────────────────────

    def test_payerfi_fc3_oversized_header_denies_access(self) -> None:
        request = _make_payer_fi_request(_PAYER_FI_TOO_LONG_ID)
        self.assertFalse(
            self.perm.has_permission(request, None),
            f"Expected False: {len(_PAYER_FI_TOO_LONG_ID)}-char payer_fi_id must be rejected.",
        )

    # ── PAYERFI-FC-4 ─────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=True)
    def test_payerfi_fc4_whitelist_hit_grants_access(self) -> None:
        GovStackRegisteredBB.objects.create(bb_id=_PAYER_FI_VALID_ID, is_active=True)
        request = _make_payer_fi_request(_PAYER_FI_VALID_ID)
        self.assertTrue(
            self.perm.has_permission(request, None),
            "Expected True: active whitelisted row must grant access.",
        )

    # ── PAYERFI-FC-5 ─────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=True)
    def test_payerfi_fc5_whitelist_miss_denies_access(self) -> None:
        # Deliberately do NOT create any GovStackRegisteredBB row.
        request = _make_payer_fi_request(_PAYER_FI_VALID_ID)
        self.assertFalse(
            self.perm.has_permission(request, None),
            "Expected False: unwhitelisted payer_fi_id must be denied in production mode.",
        )

    # ── PAYERFI-FC-6 ─────────────────────────────────────────────────────────

    @override_settings(GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=True)
    def test_payerfi_fc6_inactive_whitelist_row_denies_access(self) -> None:
        GovStackRegisteredBB.objects.create(bb_id=_PAYER_FI_VALID_ID, is_active=False)
        request = _make_payer_fi_request(_PAYER_FI_VALID_ID)
        self.assertFalse(
            self.perm.has_permission(request, None),
            "Expected False: inactive row must deny access regardless of bb_id match.",
        )

    # ── PAYERFI-FC-7 ─────────────────────────────────────────────────────────

    def test_payerfi_fc7_header_present_in_harness_mode_grants_without_db_lookup(
        self,
    ) -> None:
        """
        Only the "header absent" branch is fail-closed. When a header IS
        present and GOVSTACK_REQUIRE_REGISTERED_PAYER_FI=False (harness
        mode), RequirePayerFI behaves exactly like IsTrustedPayerFI: any
        valid, ≤20-char header passes without a DB lookup.
        """
        request = _make_payer_fi_request("ANY-VALID-FI")
        self.assertTrue(
            self.perm.has_permission(request, None),
            "Expected True: a present header in harness mode must still pass "
            "(only an absent header is fail-closed).",
        )


# ---------------------------------------------------------------------------
# AUTH-8: seed_govstack_vouchers creates GovStackRegisteredBB(bb_id="GS-HARNESS")
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Admin correctness tests
# ---------------------------------------------------------------------------


class GovStackRegisteredBBAdminTest(TestCase):
    """
    Tests for GovStackRegisteredBBAdmin form field behaviour.

    Critical invariant:
      - ADD form: bb_id must be EDITABLE (operator needs to set it)
      - CHANGE form: bb_id must be READ-ONLY (immutable lookup key)

    A class-level `readonly_fields = ["bb_id", ...]` would break the ADD form
    by hiding the bb_id input, making it impossible to create new entries via admin.
    get_readonly_fields(obj=None) must NOT include bb_id.
    """

    def setUp(self) -> None:
        from django.contrib.admin.sites import AdminSite

        from apps.payments.admin import GovStackRegisteredBBAdmin

        self.admin_obj = GovStackRegisteredBBAdmin(GovStackRegisteredBB, AdminSite())

    def test_admin_add_form_includes_bb_id_field(self) -> None:
        """
        On the ADD form (obj=None), bb_id must be a form field so operators
        can type in the BB identifier when creating a new whitelist entry.
        """
        from django.test import RequestFactory

        factory = RequestFactory()
        request = factory.get("/admin/payments/govstackregisteredbb/add/")

        Form = self.admin_obj.get_form(request, obj=None)  # noqa: N806
        self.assertIn(
            "bb_id",
            Form.base_fields,
            "bb_id must be an editable field on the ADD form so operators can set it.",
        )

    def test_admin_change_form_bb_id_is_readonly(self) -> None:
        """
        On the CHANGE form (obj is a saved instance), bb_id must be in
        readonly_fields so operators cannot rename a registered BB.
        """
        existing = GovStackRegisteredBB.objects.create(bb_id="SOME-BB", is_active=True)
        readonly = self.admin_obj.get_readonly_fields(request=None, obj=existing)
        self.assertIn(
            "bb_id",
            readonly,
            "bb_id must be read-only on the CHANGE form to prevent renaming.",
        )

    def test_admin_add_form_bb_id_not_readonly(self) -> None:
        """
        On the ADD form (obj=None), bb_id must NOT be in readonly_fields.
        """
        readonly = self.admin_obj.get_readonly_fields(request=None, obj=None)
        self.assertNotIn(
            "bb_id",
            readonly,
            "bb_id must NOT be read-only on the ADD form.",
        )

    def test_admin_delete_blocked(self) -> None:
        """
        has_delete_permission must return False — operators use is_active=False
        to suspend a BB, not delete its record.
        """
        self.assertFalse(
            self.admin_obj.has_delete_permission(request=None),
            "Delete must be blocked on GovStackRegisteredBBAdmin.",
        )


class SeedGovStackVouchersRegisteredBBTest(TestCase):
    """
    Integration test: seed_govstack_vouchers management command must create
    a GovStackRegisteredBB row with bb_id="GS-HARNESS" and is_active=True.

    This ensures that after running the seed command, the harness institution
    ID passes IsTrustedSourceBB even when GOVSTACK_REQUIRE_REGISTERED_BB=True.
    """

    def test_auth8_seed_creates_gs_harness_registered_bb(self) -> None:
        """
        Running seed_govstack_vouchers once creates GovStackRegisteredBB(bb_id="GS-HARNESS").
        Running it a second time is idempotent (get_or_create — no duplicate row error).
        """
        from django.core.management import call_command

        self.assertEqual(
            GovStackRegisteredBB.objects.filter(bb_id="GS-HARNESS").count(),
            0,
            "Pre-condition: table must be empty before seed command runs.",
        )

        # First run — should create the row.
        out = StringIO()
        call_command("seed_govstack_vouchers", verbosity=2, stdout=out)

        self.assertEqual(
            GovStackRegisteredBB.objects.filter(bb_id="GS-HARNESS", is_active=True).count(),
            1,
            "Post-condition: exactly one active GS-HARNESS row must exist after seeding.",
        )

        # Second run — must be idempotent (no IntegrityError or duplicate row).
        call_command("seed_govstack_vouchers", verbosity=0)

        self.assertEqual(
            GovStackRegisteredBB.objects.filter(bb_id="GS-HARNESS").count(),
            1,
            "Second seed run must not create a duplicate row.",
        )

    @override_settings(GOVSTACK_REQUIRE_REGISTERED_BB=True)
    def test_auth8b_seeded_gs_harness_passes_permission(self) -> None:
        """
        After running seed_govstack_vouchers, the harness ID passes
        IsTrustedSourceBB.has_permission() even when
        GOVSTACK_REQUIRE_REGISTERED_BB=True.

        This is the full end-to-end proof that the seed command and the
        permission class work together correctly for production deployment.
        """
        from django.core.management import call_command

        call_command("seed_govstack_vouchers", verbosity=0)

        perm = IsTrustedSourceBB()
        request = _make_request("GS-HARNESS")
        self.assertTrue(
            perm.has_permission(request, None),
            "Expected True: seeded GS-HARNESS row must pass IsTrustedSourceBB in production mode.",
        )
