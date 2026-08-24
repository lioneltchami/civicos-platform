#!/usr/bin/env bash
# Validate local evidence for the locked Payments RB-02 Level A scope only.
# This script performs no network, secret, staging, provider, or deployment action.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACT="$ROOT/artifacts/payments-rb02-option-b-scope-pure"
TEST_MODULE="$ROOT/apps/payments/tests/test_item02_rb02_batch_lease_live.py"
TEST_LOG="$ROOT/docs/evidence/civicos-payments-rb02-level-a-focused-test-isolated-20260823.log"
OUT="${1:-$ROOT/docs/evidence/civicos-payments-rb02-level-a-validation-20260823.log}"

EXPECTED_PAYLOAD="3fb5a4327e3ce5b0b9c076f3d2426d4e663ae178f7091ed6c19515c8c5b4ddca"
EXPECTED_MANIFEST="15b56e3bc3caf858522ce5196c70b61cc32efa476411455df367560a8d653ff9"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

payload_digest="$(shasum -a 256 "$ARTIFACT/canonical-payload.txt" | awk '{print $1}')"
manifest_digest="$(shasum -a 256 "$ARTIFACT/payload-file-sha256.txt" | awk '{print $1}')"
[ "$payload_digest" = "$EXPECTED_PAYLOAD" ] || fail "canonical payload digest mismatch"
[ "$manifest_digest" = "$EXPECTED_MANIFEST" ] || fail "manifest digest mismatch"

cmp -s "$ARTIFACT/expected-patch-paths.txt" "$ARTIFACT/patch-path-inventory.txt" || fail "artifact path inventory differs from expected allowlist"
[ ! -s "$ARTIFACT/patch-path-diff.txt" ] || fail "artifact patch-path diff is not empty"

grep -Fqx 'assembly=complete' "$ARTIFACT/ASSEMBLY_STATUS.txt" || fail "artifact assembly status is not complete"
grep -Fq 'forbidden_patch_paths:' "$ARTIFACT/exclusion-scan.txt" && grep -Fq 'patch_path_allowlist_difference:' "$ARTIFACT/exclusion-scan.txt" && grep -Fq 'mixed_anchor_in_canonical_payload:' "$ARTIFACT/exclusion-scan.txt" || fail "artifact exclusion scan structure is incomplete"
grep -Fq 'forbidden_patch_paths:' "$ARTIFACT/exclusion-scan.txt" && grep -A1 -F 'forbidden_patch_paths:' "$ARTIFACT/exclusion-scan.txt" | grep -Fqx 'none' || fail "artifact exclusion scan found forbidden paths"
grep -A1 -F 'patch_path_allowlist_difference:' "$ARTIFACT/exclusion-scan.txt" | grep -Fqx 'none' || fail "artifact exclusion scan found allowlist drift"
grep -A1 -F 'mixed_anchor_in_canonical_payload:' "$ARTIFACT/exclusion-scan.txt" | grep -Fqx 'none' || fail "artifact canonical payload contains mixed anchor"

for name in \
  test_fresh_acquire_and_heartbeat_is_fenced \
  test_expiry_takeover_advances_generation_once \
  test_stale_owner_cannot_write_any_side_effect \
  test_duplicate_finalization_creates_one_decision_audit_and_callback \
  test_two_live_workers_cross_expiry_and_stale_finalization; do
  grep -Fq "def $name" "$TEST_MODULE" || fail "missing required RB-02 test: $name"
  grep -Fq "$name" "$TEST_LOG" || fail "test log lacks required RB-02 result: $name"
done

grep -Fq 'Ran 12 tests' "$TEST_LOG" || fail "focused RB-02 test count evidence missing"
grep -Fxq 'OK' "$TEST_LOG" || fail "focused RB-02 test suite did not pass"
grep -Fqx 'TEST_EXIT=0' "$TEST_LOG" || fail "focused RB-02 test exit evidence missing"

# The canonical payload must not introduce secret, live-provider, deploy, or unrelated workstream contamination.
if grep -Ein '(sk_live_|pk_live_|whsec_|BEGIN (RSA|PRIVATE)|password=|authorization:|apps/appointments|apps/documents|docker-compose|config/settings/production)' "$ARTIFACT/canonical-payload.txt" >/dev/null; then
  fail "canonical payload contains prohibited Level A contamination"
fi

mkdir -p "$(dirname "$OUT")"
{
  printf 'LEVEL_A_VALIDATION=PASS\n'
  printf 'CANONICAL_PAYLOAD_SHA256=%s\n' "$payload_digest"
  printf 'MANIFEST_SHA256=%s\n' "$manifest_digest"
  printf 'ALLOWLIST_INVENTORY=PASS\n'
  printf 'EXCLUSION_SCAN=PASS\n'
  printf 'FOCUSED_RB02_TEST_LOG=PASS (12 tests)\n'
  printf 'NON_CLAIMS=LEVEL_A_ONLY; NO_STAGING; NO_OFFICIAL_SUITE; NO_CERTIFICATION; NO_SUBMISSION; NO_RELEASE; NO_PROD_AUTHORIZATION\n'
  printf 'NOTE=The focused test log was executed in the retained isolated Django environment because the connected desktop-local interpreter lacks Django; the RB-02 test module and core lease/task/policy/failure sources match that isolated environment except for the separately verified explicit BatchLease UUID schema-drift repair in govstack_models.py.\n'
} > "$OUT"

printf '%s\n' "PASS: Level A evidence validated; report: $OUT"
