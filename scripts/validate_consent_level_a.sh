#!/usr/bin/env bash
# Validate local Consent Level A evidence only. No network, secrets, staging,
# official-suite, provider, deployment, release, or submission action occurs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_LOG="$ROOT/docs/evidence/civicos-consent-level-a-focused-test-isolated-20260823.log"
BOUNDARY_LOG="$ROOT/docs/evidence/civicos-consent-level-a-integration-boundary-isolated-20260823.log"
OUT="${1:-$ROOT/docs/evidence/civicos-consent-level-a-validation-20260823.log}"
GOVSTACK_TESTS="$ROOT/apps/consent/tests/test_govstack_api.py"
BOUNDARY_TESTS="$ROOT/apps/consent/tests/test_integration_boundary.py"
API_TESTS="$ROOT/apps/consent/tests/test_api.py"
SERVICE_TESTS="$ROOT/apps/consent/tests/test_services.py"
MODEL_TESTS="$ROOT/apps/consent/tests/test_models.py"
TASK_TESTS="$ROOT/apps/consent/tests/test_tasks.py"
VIEW_TESTS="$ROOT/apps/consent/tests/test_views.py"
BOUNDARY="$ROOT/apps/consent/integration_boundary.py"
GOVSTACK_VIEWS="$ROOT/apps/consent/govstack_views.py"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

for f in "$TEST_LOG" "$BOUNDARY_LOG" "$GOVSTACK_TESTS" "$BOUNDARY_TESTS" "$API_TESTS" "$SERVICE_TESTS" "$MODEL_TESTS" "$TASK_TESTS" "$VIEW_TESTS" "$BOUNDARY" "$GOVSTACK_VIEWS"; do
  [ -r "$f" ] || fail "required Level A evidence/source missing: $f"
done

grep -Fq 'Ran 383 tests' "$TEST_LOG" || fail "focused Consent test count evidence missing"
grep -Fq 'OK (skipped=2)' "$TEST_LOG" || fail "focused Consent suite did not pass with documented optional backend skips"
grep -Fqx 'TEST_EXIT=0' "$TEST_LOG" || fail "focused Consent test exit evidence missing"

# Config/service/audit/current-record safe auth, exact allowlist, and non-mutation coverage.
for marker in \
  test_unauthenticated_gets_401 \
  test_citizen_cannot_list_audit_consent_records \
  test_current_record_get_requires_authentication \
  test_current_record_get_matches_exact_existing_serializer_allowlist \
  test_current_record_get_is_read_only_and_never_calls_external_boundary; do
  grep -Fq "def $marker" "$GOVSTACK_TESTS" || fail "missing GovStack safety coverage: $marker"
  grep -Fq "$marker" "$TEST_LOG" || fail "focused log lacks GovStack safety result: $marker"
done

# External integration must fail closed rather than invent transport success.
for marker in \
  test_missing_external_configuration_fails_closed_without_network_call \
  test_message_requires_subject_and_idempotency_key \
  test_configured_stub_does_not_claim_transport_support; do
  grep -Fq "def $marker" "$BOUNDARY_TESTS" || fail "missing boundary coverage: $marker"
  grep -Fq "$marker" "$BOUNDARY_TESTS" || fail "boundary test source lacks required test: $marker"
done
grep -Fq '3 passed' "$BOUNDARY_LOG" || fail "module-level integration-boundary suite did not pass"
grep -Fqx 'TEST_EXIT=0' "$BOUNDARY_LOG" || fail "module-level integration-boundary test exit evidence missing"
grep -Fq 'NotImplementedError' "$BOUNDARY" || fail "integration boundary lacks explicit unsupported transport failure"
grep -Fq 'ConsentIntegrationUnavailableError' "$BOUNDARY" || fail "integration boundary lacks unavailable configuration failure"

# The full focused suite includes API/services/models/tasks/views lifecycle coverage.
for f in "$API_TESTS" "$SERVICE_TESTS" "$MODEL_TESTS" "$TASK_TESTS" "$VIEW_TESTS"; do
  grep -Eq '^ *def test_' "$f" || fail "missing lifecycle test definitions in $f"
done
for marker in grant withdraw revision signature audit webhook; do
  grep -Eqi "test_.*${marker}" "$SERVICE_TESTS" "$MODEL_TESTS" "$TASK_TESTS" "$VIEW_TESTS" "$GOVSTACK_TESTS" || fail "missing lifecycle coverage marker: $marker"
done

grep -Fq 'permission_classes' "$GOVSTACK_VIEWS" || fail "GovStack views lack declared permission handling"

mkdir -p "$(dirname "$OUT")"
{
  printf 'CONSENT_LEVEL_A_VALIDATION=PASS\n'
  printf 'FOCUSED_CONSENT_SUITE=PASS (383 Django tests, 2 documented SQLite concurrency skips; 3 pytest integration-boundary tests)\n'
  printf 'AUTH_SERIALIZER_NON_MUTATION=PASS\n'
  printf 'FAIL_CLOSED_INTEGRATION_BOUNDARY=PASS\n'
  printf 'LIFECYCLE_CORE_COVERAGE=PASS\n'
  printf 'NON_CLAIMS=LEVEL_A_ONLY; NO_STAGING; NO_OFFICIAL_SUITE; NO_CERTIFICATION; NO_SUBMISSION; NO_RELEASE; NO_PRODUCTION_AUTHORIZATION; NO_SECRETS; NO_REAL_EXTERNAL_MEDIATOR\n'
  printf 'NOTE=Focused suite ran in the retained isolated Django environment after synchronizing the current Consent source and required templates; this is a local test-environment action only.\n'
} > "$OUT"

printf '%s\n' "PASS: Consent Level A evidence validated; report: $OUT"
