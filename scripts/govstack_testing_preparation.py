#!/usr/bin/env python3
"""Validate CivicOS local-only GovStack API-test candidate preparation.

This tool validates local candidate metadata against the committed authority
manifest and can generate a preparation evidence package. It never launches
Docker, contacts a testing site, invokes an official suite, or claims
compliance.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTHORITY_MANIFEST = ROOT / "docs" / "govstack" / "authority-manifest.json"
CANDIDATES = {
    "consent": {
        "directory": "civicos-consent",
        "repository": "bb-consent",
        "port": 8888,
    },
    "payments": {
        "directory": "civicos-payments",
        "repository": "bb-payments",
        "port": 3333,
    },
    "scheduler": {
        "directory": "civicos-scheduler",
        "repository": "bb-scheduler",
        "port": 3333,
    },
    "file-management": {
        "directory": "civicos-file-management",
        "repository": "bb-file-management",
        "port": 3003,
    },
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def git_revision() -> str | None:
    try:
        return subprocess.check_output(["git", "-C", ROOT, "rev-parse", "HEAD"], text=True).strip()  # noqa: S603, S607
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


def working_tree_status() -> list[str] | None:
    try:
        output = subprocess.check_output(["git", "-C", ROOT, "status", "--porcelain"], text=True)  # noqa: S603, S607
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return output.splitlines()


def validate() -> dict:
    authority = load_json(AUTHORITY_MANIFEST)
    revisions = {entry["repository"]: entry["commit"] for entry in authority["repositories"]}
    manifests = []
    errors = []

    shared_launcher = ROOT / "examples" / "_common" / "candidate_common.sh"
    if not shared_launcher.is_file() or "GOVSTACK_TEST_TARGET" not in shared_launcher.read_text():
        errors.append("shared candidate launcher is missing the local-target guard")

    for key, expectation in CANDIDATES.items():
        directory = ROOT / "examples" / expectation["directory"]
        manifest_path = directory / "candidate-manifest.json"
        entrypoint = directory / "test_entrypoint.sh"
        compose = directory / "docker-compose.yml"
        if not manifest_path.is_file():
            errors.append(f"{key}: missing candidate-manifest.json")
            continue
        manifest = load_json(manifest_path)
        manifests.append(manifest)
        required = {
            "schema_version",
            "candidate_id",
            "building_block",
            "official_repository",
            "official_revision",
            "official_suite",
            "local_candidate_url",
            "civicos_surface",
            "target_policy",
            "claim_boundary",
        }
        missing = sorted(required - set(manifest))
        if missing:
            errors.append(f"{key}: missing manifest fields {', '.join(missing)}")
        if (
            manifest.get("official_repository")
            != f"GovStackWorkingGroup/{expectation['repository']}"
        ):
            errors.append(f"{key}: unexpected official repository")
        if manifest.get("official_revision") != revisions.get(expectation["repository"]):
            errors.append(f"{key}: revision does not match authority manifest")
        if manifest.get("target_policy") != "local-only":
            errors.append(f"{key}: target policy must be local-only")
        if f":{expectation['port']}/" not in manifest.get("local_candidate_url", ""):
            errors.append(f"{key}: unexpected local harness port")
        if not entrypoint.is_file() or not entrypoint.stat().st_mode & 0o111:
            errors.append(f"{key}: entrypoint is missing or not executable")
        if not compose.is_file() or "GOVSTACK_TEST_TARGET" not in compose.read_text():
            errors.append(f"{key}: compose overlay lacks local test-target configuration")

    working_tree = working_tree_status()
    return {
        "schema_version": 1,
        "validated_at_utc": datetime.now(UTC).isoformat(),
        "git_revision": git_revision(),
        "working_tree": {
            "available": working_tree is not None,
            "clean": not working_tree if working_tree is not None else None,
            "status": working_tree or [],
        },
        "authority_manifest": str(AUTHORITY_MANIFEST.relative_to(ROOT)),
        "candidates": manifests,
        "errors": errors,
        "claim": (
            "structural candidate-preparation validation only; not an API "
            "compliance result or a claim about whether a separate official "
            "suite or submission has occurred"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--validate", action="store_true", help="Validate local candidate preparation."
    )
    parser.add_argument("--output", type=Path, help="Write a preparation evidence JSON package.")
    arguments = parser.parse_args()

    if not arguments.validate and arguments.output is None:
        parser.error("select --validate and/or --output")
    report = validate()
    if arguments.output is not None:
        output = arguments.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(output)
    if arguments.validate:
        if report["errors"]:
            for error in report["errors"]:
                print(error)
            return 1
        print("GovStack testing preparation is structurally valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
