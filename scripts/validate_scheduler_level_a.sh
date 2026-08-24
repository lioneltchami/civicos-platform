#!/usr/bin/env bash
# Validate Scheduler Level A evidence for SCH-01 + SCH-02.1 only.
# No network, secret, staging, official-suite, provider, deployment, release, or
# submission action occurs. SCH-02.2 is explicitly out of scope.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_LOG="$ROOT/docs/evidence/civicos-scheduler-level-a-sqlite-test-20260823.log"
OUT="${1:-$ROOT/docs/evidence/civicos-scheduler-level-a-validation-20260823.log}"
ADMISSION_TESTS="$ROOT/apps/appointments/tests/test_scheduler_admission.py"
LIFECYCLE_TESTS="$ROOT/apps/appointments/tests/test_scheduler_lifecycle.py"
RUNTIME_TESTS="$ROOT/apps/appointments/tests/test_scheduler_runtime.py"
AUTH_TESTS="$ROOT/apps/appointments/tests/test_govstack_auth.py"
RUNTIME="$ROOT/apps/appointments/services/scheduler_runtime.py"
MODELS="$ROOT/apps/appointments/models.py"
TASKS="$ROOT/apps/appointments/scheduler_tasks.py"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

for f in "$TEST_LOG" "$ADMISSION_TESTS" "$LIFECYCLE_TESTS" "$RUNTIME_TESTS" "$AUTH_TESTS" "$RUNTIME" "$MODELS" "$TASKS"; do
  [ -r "$f" ] || fail "required Scheduler Level A evidence/source missing: $f"
done

grep -Fq 'Ran 50 tests' "$TEST_LOG" || fail "Scheduler Level A test count evidence missing"
grep -Fq 'OK (skipped=10)' "$TEST_LOG" || fail "Scheduler Level A SQLite suite did not pass with documented PostgreSQL skips"
grep -Fqx 'TEST_EXIT=0' "$TEST_LOG" || fail "Scheduler Level A test exit evidence missing"
! grep -Eq 'request_token = [A-Za-z0-9_-]{20,}' "$TEST_LOG" || fail "unredacted test credential token present in retained evidence"

# SCH-01 durable admission/lifecycle source and executed test evidence.
for marker in \
  test_zero_recipient_admission_is_deterministic_and_rowless \
  test_cancel_schedule_fences_non_terminal_work_and_blocks_admission \
  test_modify_delivery_content_invalidates_old_generation_before_new_admission \
  test_delete_schedule_cannot_leave_recreatable_admission_work \
  test_rearm_advances_generation_and_admits_exactly_once; do
  grep -Fq "def $marker" "$ADMISSION_TESTS" "$LIFECYCLE_TESTS" || fail "missing SCH-01 source test: $marker"
  grep -Fq "$marker" "$TEST_LOG" || fail "Scheduler test log lacks SCH-01 result: $marker"
done

# SCH-02.1 locally inspectable bounded publisher contract and fail-closed task classification.
for marker in 'PENDING = "pending"' 'CLAIMED = "claimed"' 'LOCAL_FAILURE = "local_failure"' 'UNKNOWN_HANDOFF = "unknown_handoff"' 'PUBLISHED = "published"' 'EXHAUSTED = "exhausted"' 'CANCELLED = "cancelled"'; do
  grep -Fq "$marker" "$MODELS" || fail "missing SCH-02.1 durable publisher state: $marker"
done
for marker in publisher_token publisher_generation; do
  grep -Fq "$marker" "$RUNTIME" || fail "missing SCH-02.1 publisher fencing marker: $marker"
done
grep -Fq 'TimeoutError' "$TASKS" || fail "missing publisher timeout classification"
grep -Fq 'mark_outbox_unknown_handoff' "$TASKS" || fail "missing unknown-handoff fail-closed path"
for marker in \
  test_claim_outbox_consumes_attempt_and_sets_publisher_lease \
  test_published_and_failure_require_current_publisher_token_and_generation \
  test_retry_policy_is_bounded_and_not_an_unbounded_hot_loop \
  test_replay_rejects_published_and_cancelled_rows \
  test_attempt_budget_transitions_to_terminal_publisher_failure; do
  grep -Fq "def $marker" "$RUNTIME_TESTS" || fail "missing SCH-02.1 source test: $marker"
  grep -Fq "$marker" "$TEST_LOG" || fail "SQLite test log lacks SCH-02.1 PostgreSQL skip record: $marker"
done

grep -Fq 'SchedulerPublisherRecoveryPostgresTests' "$RUNTIME_TESTS" || fail "dedicated SCH-02.1 PostgreSQL test class missing"
grep -Fq 'publisher evidence requires PostgreSQL' "$TEST_LOG" || fail "PostgreSQL-only SCH-02.1 skip classification missing"
grep -Fq 'SCH-02.1 publisher evidence requires PostgreSQL' "$TEST_LOG" || fail "PostgreSQL-only SCH-02.1 boundary is not explicit"

# Minimal locally tested guard surface must remain present.
grep -Eq '^ *def test_' "$AUTH_TESTS" || fail "GovStack auth guard test surface missing"

mkdir -p "$(dirname "$OUT")"
{
  printf 'SCHEDULER_LEVEL_A_VALIDATION=PASS\n'
  printf 'SCH_01_SQLITE_SUITE=PASS (50 tests, 10 dedicated SCH-02.1 PostgreSQL skips)\n'
  printf 'SCH_02_1_LOCAL_SOURCE_SAFETY=PASS\n'
  printf 'SCH_02_1_POSTGRES_EVIDENCE=BLOCKED-EXTERNAL (SQLite environment; no PostgreSQL proof claimed in this record)\n'
  printf 'SCH_02_2=NOT_READY_OUT_OF_SCOPE\n'
  printf 'NON_CLAIMS=LEVEL_A_SCH_01_AND_SCH_02_1_ONLY; NOT_FULL_SCHEDULER_BB; NO_STAGING; NO_OFFICIAL_SUITE; NO_CERTIFICATION; NO_SUBMISSION; NO_RELEASE; NO_PRODUCTION_AUTHORIZATION; NO_SECRETS\n'
  printf 'NOTE=The test output was produced in the retained isolated Django environment synchronized with the current Scheduler source. Test-generated credential values were redacted before evidence retention.\n'
} > "$OUT"

printf '%s\n' "PASS: Scheduler Level A evidence validated; report: $OUT"
