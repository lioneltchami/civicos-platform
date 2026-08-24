#!/usr/bin/env bash
# Shared CivicOS candidate launcher for local GovStack API-suite preparation.
# It intentionally starts only a disposable local Docker Compose candidate. It
# does not create external accounts, contact a testing website, or make a
# conformance claim.

set -euo pipefail

candidate_main() {
  local candidate_dir="$1"
  local candidate_id="$2"
  local expected_port="$3"
  shift 3

  if [[ $# -ne 2 || "$1" != "--config" || "$2" != "api-suite" ]]; then
    echo "Usage: test_entrypoint.sh --config api-suite" >&2
    exit 64
  fi

  if [[ "${GOVSTACK_TEST_TARGET:-local}" != "local" ]]; then
    echo "Refusing non-local target: GOVSTACK_TEST_TARGET must be 'local'." >&2
    exit 65
  fi

  local root_dir
  root_dir="$(cd "$candidate_dir/../.." && pwd)"
  local result_dir="$candidate_dir/result"
  local web_port="${GOVSTACK_WEB_PORT:-$expected_port}"
  local health_port="${GOVSTACK_CANDIDATE_HEALTH_PORT:-$web_port}"
  local temp_env
  temp_env="$(mktemp)"
  trap 'rm -f "${temp_env:-}"' EXIT

  if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose v2 is required to start this local candidate." >&2
    exit 69
  fi

  local secret_key
  secret_key="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"
  cat >"$temp_env" <<EOF
DJANGO_SECRET_KEY=$secret_key
CIVICOS_WEB_PORT=$web_port
GOVSTACK_TEST_PORT=$expected_port
GOVSTACK_TEST_TARGET=local
EOF

  mkdir -p "$result_dir"
  python3 - "$candidate_dir/candidate-manifest.json" "$result_dir/preflight.json" <<'PY'
import json
import pathlib
import subprocess
import sys
from datetime import UTC, datetime

manifest_path = pathlib.Path(sys.argv[1])
output_path = pathlib.Path(sys.argv[2])
manifest = json.loads(manifest_path.read_text())
source_root = manifest_path.parents[2]
git_revision = "archive-no-git"
try:
    inside_work_tree = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "--is-inside-work-tree"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0
    if inside_work_tree:
        git_revision = subprocess.check_output(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            text=True,
        ).strip()
except (FileNotFoundError, subprocess.CalledProcessError):
    pass

record = {
    "candidate": manifest,
    "created_at_utc": datetime.now(UTC).isoformat(),
    "git_revision": git_revision,
    "target": "local",
    "official_suite_executed": False,
    "claim": "preparation only; not an API compliance result",
}
output_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
PY

  local services
  case "$candidate_id" in
    civicos-consent-testing)
      services="db redis web consent-adapter"
      ;;
    civicos-payments-testing|civicos-scheduler-testing)
      services="db redis web"
      ;;
    civicos-file-management-testing)
      services="db redis clamav web"
      ;;
    *)
      echo "Unknown candidate building block: $candidate_id" >&2
      exit 64
      ;;
  esac

  if [[ -n "${GOVSTACK_CANDIDATE_SERVICES:-}" && "${GOVSTACK_CANDIDATE_SERVICES}" != "$services" ]]; then
    echo "Refusing unallowlisted GOVSTACK_CANDIDATE_SERVICES for $candidate_id." >&2
    exit 64
  fi

  local seed_command=""
  case "$candidate_id" in
    civicos-consent-testing)
      seed_command="seed_govstack_consent_candidate"
      ;;
  esac
  if [[ -n "${GOVSTACK_CANDIDATE_SEED_COMMAND:-}" && "${GOVSTACK_CANDIDATE_SEED_COMMAND}" != "$seed_command" ]]; then
    echo "Refusing unallowlisted GOVSTACK_CANDIDATE_SEED_COMMAND for $candidate_id." >&2
    exit 64
  fi

  echo "Starting $candidate_id as a local-only candidate on 127.0.0.1:$expected_port."
  # Service names are selected from the fixed per-building-block allowlist above.
  # shellcheck disable=SC2086
  docker compose \
    --project-name "$candidate_id" \
    --env-file "$temp_env" \
    -f "$root_dir/docker-compose.yml" \
    -f "$candidate_dir/docker-compose.yml" \
    up -d --build $services

  local attempt
  for attempt in $(seq 1 90); do
    if curl --fail --silent --show-error "http://127.0.0.1:$health_port/health/" >/dev/null; then
      if [[ -n "$seed_command" ]]; then
        docker compose \
          --project-name "$candidate_id" \
          --env-file "$temp_env" \
          -f "$root_dir/docker-compose.yml" \
          -f "$candidate_dir/docker-compose.yml" \
          exec -T web python manage.py "$seed_command"
      fi
      echo "Candidate is ready at http://127.0.0.1:$expected_port/."
      echo "Run the pinned official suite separately; preserve its raw result in $result_dir/."
      return 0
    fi
    sleep 2
  done

  echo "Candidate did not become healthy. Inspect with:" >&2
  echo "  docker compose --project-name $candidate_id -f $root_dir/docker-compose.yml -f $candidate_dir/docker-compose.yml logs web" >&2
  exit 70
}
