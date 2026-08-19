#!/usr/bin/env bash
# Regression tests for source-controlled local GovStack candidate launch settings.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMMON="$ROOT/examples/_common/candidate_common.sh"

# Fixed selections must remain source controlled and legible to review.
grep -Fq 'civicos-consent-testing)' "$COMMON"
grep -Fq 'services="db redis web consent-adapter"' "$COMMON"
grep -Fq 'civicos-payments-testing|civicos-scheduler-testing)' "$COMMON"
grep -Fq 'services="db redis web"' "$COMMON"
grep -Fq 'civicos-file-management-testing)' "$COMMON"
grep -Fq 'services="db redis clamav web"' "$COMMON"
grep -Fq 'seed_command="seed_govstack_consent_candidate"' "$COMMON"

fake_bin="$(mktemp -d)"
trap 'rm -rf "$fake_bin"' EXIT
cat >"$fake_bin/docker" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "compose" && "${2:-}" == "version" ]]; then
  exit 0
fi
exit 0
EOF
chmod +x "$fake_bin/docker"

set +e
output="$(PATH="$fake_bin:$PATH" GOVSTACK_CANDIDATE_SERVICES='web invalid-service' \
  "$ROOT/examples/civicos-payments/test_entrypoint.sh" --config api-suite 2>&1)"
status=$?
set -e
[[ "$status" -eq 64 ]]
grep -Fq 'Refusing unallowlisted GOVSTACK_CANDIDATE_SERVICES' <<<"$output"

set +e
output="$(PATH="$fake_bin:$PATH" GOVSTACK_CANDIDATE_SEED_COMMAND='arbitrary-command' \
  "$ROOT/examples/civicos-scheduler/test_entrypoint.sh" --config api-suite 2>&1)"
status=$?
set -e
[[ "$status" -eq 64 ]]
grep -Fq 'Refusing unallowlisted GOVSTACK_CANDIDATE_SEED_COMMAND' <<<"$output"

echo 'candidate launcher allowlist tests passed'
