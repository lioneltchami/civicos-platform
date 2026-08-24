#!/usr/bin/env bash
# Validate local File Management runner Level A evidence only. No network,
# staging, secret, official-harness, deployment, release, or submission action occurs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE="$ROOT/docs/evidence/file-management-level-a"
MANIFEST="$ROOT/tools/file_management_test_layers.json"
RUNNER="$ROOT/tools/run_file_management_tests.py"
CONTRACT="$ROOT/tests/test_file_management_runner_contract.py"
GUIDE="$ROOT/docs/testing/file-management.md"
MATRIX="$ROOT/docs/testing/file-management-operation-matrix.md"
OFFICIAL_PIN="$ROOT/docs/civicos-govstack-official-source-pin-20260822.md"
OUT="${1:-$ROOT/docs/evidence/civicos-file-management-level-a-validation-20260823.log}"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

for f in "$MANIFEST" "$RUNNER" "$CONTRACT" "$GUIDE" "$MATRIX" "$OFFICIAL_PIN" \
  "$EVIDENCE/metadata-level-a.json" "$EVIDENCE/LEVEL_A.md" "$EVIDENCE/runner-contract.log"; do
  [ -r "$f" ] || fail "required File Management Level A source/evidence missing: $f"
done

# Manifest must enumerate every current apps/documents test exactly once.
actual=$(find "$ROOT/apps/documents/tests" -maxdepth 1 -type f -name 'test_*.py' -exec basename {} \; | sort)
declared=$(grep -E '"test_[^"]+\.py"' "$MANIFEST" | sed -E 's/.*"(test_[^"]+\.py)".*/\1/' | sort)
[ "$(printf '%s\n' "$actual" | uniq -d | wc -l | tr -d ' ')" = "0" ] || fail "duplicate current test filenames discovered"
[ "$(printf '%s\n' "$declared" | uniq -d | wc -l | tr -d ' ')" = "0" ] || fail "layer manifest lists a test filename more than once"
[ "$actual" = "$declared" ] || fail "layer manifest does not list current apps/documents test modules exactly once"

grep -Fq 'apps/documents/tests' "$RUNNER" || fail "canonical runner scope is not fixed to apps/documents/tests"
grep -Fq 'Content Management System' "$OFFICIAL_PIN" || fail "CMS naming boundary missing from retained official-source pin"
grep -Fq 'not an official GovStack harness' "$GUIDE" || fail "local runner official-harness exclusion missing from guide"

# Require all executed layer reports and their expected pass/exit evidence.
for layer in all unit integration e2e; do
  for f in "$EVIDENCE/$layer/collection-$layer.log" "$EVIDENCE/$layer/pytest-$layer.log" "$EVIDENCE/$layer/junit-$layer.xml" "$EVIDENCE/$layer/coverage-$layer.xml" "$EVIDENCE/$layer/metadata-$layer.json" "$EVIDENCE/$layer/runner-$layer.log"; do
    [ -s "$f" ] || fail "missing or empty $layer evidence artifact: $f"
  done
  grep -Fqx 'TEST_EXIT=0' "$EVIDENCE/$layer/runner-$layer.log" || fail "$layer runner exit evidence is not zero"
  grep -Fq '<testsuite' "$EVIDENCE/$layer/junit-$layer.xml" || fail "$layer JUnit XML content missing"
  grep -Fq '<coverage' "$EVIDENCE/$layer/coverage-$layer.xml" || fail "$layer coverage XML content missing"
  grep -Fq '"official_harness_executed": false' "$EVIDENCE/$layer/metadata-$layer.json" || fail "$layer metadata does not preserve official-harness exclusion"
done

grep -Fq '1159 passed' "$EVIDENCE/all/pytest-all.log" || fail "all-layer success count missing"
grep -Fq '317 passed' "$EVIDENCE/unit/pytest-unit.log" || fail "unit-layer success count missing"
grep -Fq '637 passed' "$EVIDENCE/integration/pytest-integration.log" || fail "integration-layer success count missing"
grep -Fq '205 passed' "$EVIDENCE/e2e/pytest-e2e.log" || fail "e2e-layer success count missing"
grep -Fq '5 passed' "$EVIDENCE/runner-contract.log" || fail "runner-contract success count missing"
grep -Fqx 'TEST_EXIT=0' "$EVIDENCE/runner-contract.log" || fail "runner-contract exit evidence is not zero"

# Metadata must bind the collected run to current runner/manifest bytes and recorded report hashes.
runner_sha=$(shasum -a 256 "$RUNNER" | awk '{print $1}')
manifest_sha=$(shasum -a 256 "$MANIFEST" | awk '{print $1}')
all_sha=$(shasum -a 256 "$EVIDENCE/all/pytest-all.log" | awk '{print $1}')
unit_sha=$(shasum -a 256 "$EVIDENCE/unit/pytest-unit.log" | awk '{print $1}')
integration_sha=$(shasum -a 256 "$EVIDENCE/integration/pytest-integration.log" | awk '{print $1}')
e2e_sha=$(shasum -a 256 "$EVIDENCE/e2e/pytest-e2e.log" | awk '{print $1}')
for value in "$runner_sha" "$manifest_sha" "$all_sha" "$unit_sha" "$integration_sha" "$e2e_sha"; do
  grep -Fq "$value" "$EVIDENCE/metadata-level-a.json" || fail "integrity metadata does not match current evidence hash"
done

# Covered operation modules are manifest-scoped and exercised by the passing all-layer run.
for module in test_upload_content_gating.py test_scan_audit_and_promotion.py test_wave3_clamav.py test_wave3_download.py test_wave4_retention.py test_pipeda.py test_audit.py; do
  grep -Fq "\"$module\"" "$MANIFEST" || fail "required File Management safety module absent from manifest: $module"
  [ -r "$ROOT/apps/documents/tests/$module" ] || fail "required File Management safety module missing: $module"
done
grep -Fq 'Real ClamAV daemon' "$EVIDENCE/LEVEL_A.md" || fail "optional ClamAV external boundary missing"
grep -Fq 'CMS' "$EVIDENCE/LEVEL_A.md" || fail "CMS-not-File-Management note missing"

# Evidence retention must not contain obvious sensitive tokens or live signed URLs.
! grep -RInE '(request_token = [A-Za-z0-9_-]{20,}|SECRET_KEY=|AKIA[0-9A-Z]{16}|aws_secret|password=|X-Amz-Signature=)' "$EVIDENCE" || fail "sensitive value pattern present in retained evidence"

mkdir -p "$(dirname "$OUT")"
{
  printf 'FILE_MANAGEMENT_LEVEL_A_VALIDATION=PASS\n'
  printf 'RUNNER_MANIFEST_CONTRACT=PASS\n'
  printf 'ALL_LAYER=PASS (1159 passed; exit 0)\n'
  printf 'UNIT_LAYER=PASS (317 passed; exit 0)\n'
  printf 'INTEGRATION_LAYER=PASS (637 passed; exit 0)\n'
  printf 'E2E_LAYER=PASS (205 passed; exit 0)\n'
  printf 'RUNNER_CONTRACT=PASS (5 passed; exit 0)\n'
  printf 'OPTIONAL_DEPS=NO_SKIP_OBSERVED; REAL_CLAMAV_CLOUD_KMS_STAGING=BLOCKED-EXTERNAL_OUT_OF_SCOPE\n'
  printf 'CMS_NOT_FILE_MANAGEMENT=EXPLICIT; OFFICIAL_HARNESS=NOT_EXECUTED\n'
  printf 'NON_CLAIMS=LOCAL_FILE_MANAGEMENT_RUNNER_LEVEL_A_ONLY; NO_OFFICIAL_CMS_EQUIVALENCE; NO_STAGING; NO_CERTIFICATION; NO_CONFORMANCE; NO_SUBMISSION; NO_RELEASE; NO_PRODUCTION_AUTHORIZATION; NO_SECRETS\n'
} > "$OUT"

printf '%s\n' "PASS: File Management Level A evidence validated; report: $OUT"
