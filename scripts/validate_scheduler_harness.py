#!/usr/bin/env python3
"""Validate Item 03 Scheduler harness traceability and local-only topology."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

REQUIRED_KEYS = {"id", "path", "method", "status", "source", "tests"}
STATUSES = {"full", "partial", "missing"}


def validate(manifest_path: Path, matrix_path: Path) -> list[str]:
    errors: list[str] = []
    manifest = json.loads(manifest_path.read_text())
    matrix = json.loads(matrix_path.read_text())
    if manifest.get("building_block") != "Scheduler":
        errors.append("manifest building_block must be Scheduler")
    if manifest.get("official_revision") != matrix.get("official_revision"):
        errors.append("official revision mismatch")
    if manifest.get("target_policy") != "local-only":
        errors.append("target_policy must be local-only")
    if manifest.get("adapter", {}).get("enabled") is not True:
        errors.append("adapter must be enabled for local harness preparation")
    url = manifest.get("local_candidate_url", "")
    # The manifest intentionally documents an injectable value; validation uses its safe local default.  # noqa: E501
    url = re.sub(r"^\$\{SCHEDULER_API_BASE:-([^}]+)\}$", r"\1", url)
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        errors.append("local_candidate_url must be an HTTP loopback URL")
    ops = matrix.get("operations")
    if not isinstance(ops, list) or len(ops) != 37:
        errors.append("operation matrix must contain all 37 Scheduler routes")
    seen = set()
    for op in ops or []:
        missing = REQUIRED_KEYS - op.keys()
        if missing:
            errors.append(f"{op.get('id', '<unknown>')}: missing keys {sorted(missing)}")
        key = (op.get("method"), op.get("path"))
        if key in seen:
            errors.append(f"duplicate operation {key}")
        seen.add(key)
        if op.get("status") not in STATUSES:
            errors.append(f"{op.get('id')}: invalid status")
        for field in ("source", "tests"):
            if not op.get(field):
                errors.append(f"{op.get('id')}: {field} trace is required")
        if op.get("source") and not Path(op["source"].split(":", 1)[0]).exists():
            errors.append(f"{op.get('id')}: source file missing")
        if op.get("tests") and not Path(op["tests"].split(":", 1)[0]).exists():
            errors.append(f"{op.get('id')}: test file missing")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest", type=Path, default=Path("examples/civicos-scheduler/candidate-manifest.json")
    )
    parser.add_argument(
        "--matrix", type=Path, default=Path("examples/civicos-scheduler/operation-matrix.json")
    )
    args = parser.parse_args()
    errors = validate(args.manifest, args.matrix)
    if errors:
        print("Scheduler harness validation: FAIL")
        print("\n".join(f"- {e}" for e in errors))
        return 1
    print(
        f"Scheduler harness validation: PASS ({len(json.loads(args.matrix.read_text())['operations'])} operations; local-only target)"  # noqa: E501
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
