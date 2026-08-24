#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${SCHEDULER_API_BASE:=http://127.0.0.1:3333/}"
export SCHEDULER_API_BASE
python3 - "$SCHEDULER_API_BASE" <<'PY'
import sys
from urllib.parse import urlparse
u = urlparse(sys.argv[1])
if u.scheme != "http" or u.hostname not in {"127.0.0.1", "localhost", "::1"}:
    raise SystemExit("SCHEDULER_API_BASE must be an HTTP loopback URL in local-only mode")
PY
SCHEDULER_PORT="${SCHEDULER_PORT:-3333}"
case "$SCHEDULER_PORT" in (*[!0-9]*|'') echo "SCHEDULER_PORT must be numeric" >&2; exit 2;; esac
# shellcheck source=../_common/candidate_common.sh
source "$SCRIPT_DIR/../_common/candidate_common.sh"

candidate_main "$SCRIPT_DIR" "civicos-scheduler-testing" "$SCHEDULER_PORT" "$@"
