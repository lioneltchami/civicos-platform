#!/usr/bin/env bash
# Validate the internal/local CivicOS Level A portfolio only. This script has no
# network, staging, secret, official-harness, deployment, release, or submission action.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/docs/evidence/civicos-level-a-portfolio-validation-20260823.log}"
READY_DOCS=(
  "$ROOT/docs/civicos-payments-rb02-canonical-validation-READY-20260823.md"
  "$ROOT/docs/civicos-consent-canonical-validation-READY-20260823.md"
  "$ROOT/docs/civicos-scheduler-sch01-sch02-1-canonical-validation-READY-20260823.md"
  "$ROOT/docs/civicos-file-management-canonical-validation-READY-20260823.md"
)
VALIDATORS=(
  "$ROOT/scripts/validate_payments_rb02_level_a.sh"
  "$ROOT/scripts/validate_consent_level_a.sh"
  "$ROOT/scripts/validate_scheduler_level_a.sh"
  "$ROOT/scripts/validate_file_management_level_a.sh"
)
ROLLUP="$ROOT/docs/civicos-level-a-portfolio-READY-20260823.md"
SHIP="$ROOT/docs/civicos-ship-readiness-checklist-20260823.md"
CLOSEOUT="$ROOT/docs/civicos-govstack-campaign-closeout-record-20260822.md"
PRODUCT_BRIEF="$ROOT/docs/civicos-product-prod-path-brief-20260822.md"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

for f in "${READY_DOCS[@]}" "${VALIDATORS[@]}" "$ROLLUP" "$SHIP" "$CLOSEOUT" "$PRODUCT_BRIEF"; do
  [ -r "$f" ] || fail "required portfolio file missing: $f"
done

if find "$ROOT" -maxdepth 3 -type f -name 'Canonical validation Blind Re-Review — MISSED*.md' -print -quit | grep -q .; then
  fail "canonical validation MISSED file remains in repository"
fi

# Run four existing fail-closed validators against current local evidence.
for validator in "${VALIDATORS[@]}"; do
  bash "$validator" >/dev/null || fail "individual Level A validator failed: $(basename "$validator")"
done

# Rollup must state all locked scopes, complete test outcomes, no external claims, and open boundaries.
for marker in \
  'Payments' 'Consent' 'Scheduler' 'File Management' \
  '12 focused live-path tests passed' '383 focused tests passed' '50 isolated SQLite tests passed' '1,159 all-layer tests passed' \
  'External claim allowed: No' 'SCH-02.2 remains open / out of scope' 'CLOSED OUT / PARKED'; do
  grep -Fq "$marker" "$ROLLUP" || fail "portfolio rollup lacks required marker: $marker"
done

# Checklist must remain planning only and reflect the observed pre-rollup divergence without authorizing execution.
for marker in \
  'ORIGIN_MAIN_AHEAD' '294' 'ORIGIN_MAIN_BEHIND' '0' \
  'Planning-only checklist' 'not authorize a push' 'Money/provider rails remain **off by default**' \
  'Not authorized by this document' 'SCH-02.2 remains open/out of scope'; do
  grep -Fq "$marker" "$SHIP" || fail "ship-readiness checklist lacks required marker: $marker"
done

grep -Fq 'closed out as parked' "$CLOSEOUT" || fail "parked campaign status not retained"
grep -Fq 'separate' "$PRODUCT_BRIEF" || fail "separate product-path boundary not retained"

# Both new documents must retain the hard Level A non-claim boundary.
for f in "$ROLLUP" "$SHIP"; do
  grep -Eqi 'no deployment' "$f" || fail "deployment non-claim missing: $f"
  grep -Eqi 'no release' "$f" || fail "release non-claim missing: $f"
  grep -Eqi 'no submission' "$f" || fail "submission non-claim missing: $f"
done

mkdir -p "$(dirname "$OUT")"
{
  printf 'LEVEL_A_PORTFOLIO_VALIDATION=PASS\n'
  printf 'PAYMENTS_RB02=READY_INTERNAL_LOCAL_ONLY\n'
  printf 'CONSENT=READY_INTERNAL_LOCAL_ONLY\n'
  printf 'SCHEDULER_SCH01_SCH02_1=READY_INTERNAL_LOCAL_ONLY; SCH_02_2=OPEN_OUT_OF_SCOPE\n'
  printf 'FILE_MANAGEMENT=READY_INTERNAL_LOCAL_ONLY; CMS_EQUIVALENCE=NOT_CLAIMED\n'
  printf 'FOUR_VALIDATORS=PASS\n'
  printf 'MISSED_FILES=NONE\n'
  printf 'GOVSTACK_STAGING_CAMPAIGN=CLOSED_OUT_PARKED\n'
  printf 'SHIP_READINESS=PLANNING_ONLY; PUSH_PR_DEPLOY=NOT_AUTHORIZED\n'
  printf 'NON_CLAIMS=NO_EXTERNAL_CLAIMS; NO_STAGING; NO_OFFICIAL_SUITE; NO_CERTIFICATION; NO_CONFORMANCE; NO_RELEASE; NO_SUBMISSION; NO_PRODUCTION_AUTHORIZATION; NO_SECRETS\n'
} > "$OUT"

printf '%s\n' "PASS: Level A portfolio validated; report: $OUT"
