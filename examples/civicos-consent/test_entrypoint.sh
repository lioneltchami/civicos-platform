#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../_common/candidate_common.sh
source "$SCRIPT_DIR/../_common/candidate_common.sh"

export GOVSTACK_CANDIDATE_SERVICES="db redis web consent-adapter"
export GOVSTACK_WEB_PORT="18000"
export GOVSTACK_CANDIDATE_HEALTH_PORT="18000"
export GOVSTACK_CANDIDATE_SEED_COMMAND="seed_govstack_consent_candidate"

candidate_main "$SCRIPT_DIR" "civicos-consent-testing" "8888" "$@"
