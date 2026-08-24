#!/usr/bin/env python3
"""Archive a reviewed non-production official GovStack harness execution.

This tool refuses to execute by default. It never discovers a target, calls a
production-looking command, or marks an unexecuted run as passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

FORBIDDEN_TARGET_MARKERS = ("production", "prod", "live")


def is_production_looking(command: list[str]) -> bool:
    text = " ".join(command).lower()
    return any(marker in text for marker in FORBIDDEN_TARGET_MARKERS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--adapter-id", required=True, help="Reviewed non-production adapter identifier."
    )
    parser.add_argument(
        "--traceability-row",
        action="append",
        required=True,
        help="Traceability row ID covered by this run.",
    )
    parser.add_argument("--output", type=Path, required=True, help="JSON archive record path.")
    parser.add_argument(
        "--adapter-config",
        type=Path,
        help="Reviewed non-production adapter configuration file to fingerprint when executing.",
    )
    parser.add_argument(
        "--dependency",
        action="append",
        default=[],
        help="Pinned runtime/dependency identifier to archive when executing; may be repeated.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute only the explicit reviewed command after --.",
    )
    parser.add_argument(
        "command", nargs=argparse.REMAINDER, help="Command to execute, prefixed by --."
    )
    arguments = parser.parse_args()

    if not arguments.execute:
        parser.error(
            "refusing to execute: pass --execute only after reviewing a non-production adapter"
        )
    command = arguments.command[1:] if arguments.command[:1] == ["--"] else arguments.command
    if not command:
        parser.error("missing reviewed command after --; no official run was executed")
    if arguments.adapter_config is None or not arguments.adapter_config.is_file():
        parser.error("--adapter-config must reference a reviewed local configuration file")
    if not arguments.dependency:
        parser.error("supply at least one --dependency identifier for the execution record")
    if is_production_looking(command):
        parser.error("refusing production-looking command; no official run was executed")
    if shutil.which(command[0]) is None:
        parser.error(f"required executable is unavailable: {command[0]}")

    process = subprocess.run(command, text=True, capture_output=True, check=False)  # noqa: S603
    record = {
        "schema_version": 1,
        "started_at_utc": datetime.now(UTC).isoformat(),
        "result": "passed" if process.returncode == 0 else "failed",
        "returncode": process.returncode,
        "adapter_id": arguments.adapter_id,
        "adapter_config_sha256": hashlib.sha256(arguments.adapter_config.read_bytes()).hexdigest(),
        "dependencies": sorted(arguments.dependency),
        "traceability_rows": arguments.traceability_row,
        "command": command,
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "git": shutil.which("git"),
        },
        "stdout": process.stdout,
        "stderr": process.stderr,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(arguments.output)
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
