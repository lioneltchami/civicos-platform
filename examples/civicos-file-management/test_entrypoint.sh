#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../_common/candidate_common.sh
source "$SCRIPT_DIR/../_common/candidate_common.sh"

candidate_main "$SCRIPT_DIR" "civicos-file-management-testing" "3003" "$@"
