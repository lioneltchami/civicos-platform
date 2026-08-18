#!/usr/bin/env python3
"""Generate or validate the pinned offline GovStack authority manifest.

This utility never contacts a network service. Hash comparison is available only
when a reviewed local checkout of the pinned official sources is supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "govstack" / "authority-manifest.json"

AUTHORITIES = {
    "bb-consent": {
        "commit": "7af4b62a1c0b0d7073d42b37c71b0f08bea63dda",
        "comparison_status": (
            "explicit CivicOS implementation; official wire conformance unverified"
        ),
        "artifacts": [
            "README.md",
            "api/consent-openapi.yaml",
            "spec/8-service-apis.md",
            "test/plan.md",
        ],
    },
    "bb-payments": {
        "commit": "4b63a6b5efbb20123c442e0b09fb44ec2d7e6b6a",
        "comparison_status": (
            "explicit CivicOS implementation; official wire conformance unverified"
        ),
        "artifacts": [
            "README.md",
            "api/G2P API YAMLs/BulkPayment.yml",
            "spec/.gitbook/assets/Mobile_Money_API_v1.1.2-Specification_Definition (1) (1).yaml",
            "test/openAPI/plan.md",
            "test/openAPI/test_entrypoint.sh",
        ],
    },
    "bb-scheduler": {
        "commit": "d425be5cc0d6c606f351e5bf89be6d5c6c83c468",
        "comparison_status": (
            "explicit CivicOS implementation through Appointments; "
            "official wire conformance unverified"
        ),
        "artifacts": [
            "README.md",
            "api/Govstack_scheduler_BB_APIs.json",
            "spec/8-service-apis.md",
            "test/plan.md",
            "test/openAPI/test_entrypoint.sh",
        ],
    },
    "bb-file-management": {
        "commit": "cf50bf4952491bd1ede775aa3c90a228319c3977",
        "comparison_status": (
            "explicit CivicOS implementation through Documents; "
            "official wire conformance unverified"
        ),
        "artifacts": [
            "README.md",
            "api/swagger.yaml",
            "spec/8-service-apis.md",
            "test/plan.md",
            "test/openAPI/test_entrypoint.sh",
        ],
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_manifest(source_root: Path) -> dict:
    repositories = []
    for repository, metadata in AUTHORITIES.items():
        artifacts = []
        for relative_path in metadata["artifacts"]:
            artifact = source_root / repository / relative_path
            if not artifact.is_file():
                raise FileNotFoundError(f"Missing pinned artifact: {artifact}")
            artifacts.append({"path": relative_path, "sha256": sha256(artifact)})
        repositories.append(
            {
                "repository": repository,
                "commit": metadata["commit"],
                "comparison_status": metadata["comparison_status"],
                "artifacts": artifacts,
            }
        )
    return {
        "schema_version": 1,
        "source": "reviewed offline GovStack source snapshots",
        "network_access": False,
        "repositories": repositories,
    }


def validate_structure(manifest: dict) -> None:
    if manifest.get("schema_version") != 1 or manifest.get("network_access") is not False:
        raise ValueError("manifest schema/version or network contract is invalid")
    repositories = manifest.get("repositories")
    if not isinstance(repositories, list) or len(repositories) != len(AUTHORITIES):
        raise ValueError("manifest repository inventory is incomplete")
    by_name = {entry.get("repository"): entry for entry in repositories}
    if set(by_name) != set(AUTHORITIES):
        raise ValueError("manifest repository names do not match the approved authority set")
    for repository, metadata in AUTHORITIES.items():
        entry = by_name[repository]
        if entry.get("commit") != metadata["commit"]:
            raise ValueError(f"unexpected commit for {repository}")
        artifacts = entry.get("artifacts")
        if not isinstance(artifacts, list) or {item.get("path") for item in artifacts} != set(
            metadata["artifacts"]
        ):
            raise ValueError(f"artifact inventory is incomplete for {repository}")
        for artifact in artifacts:
            value = artifact.get("sha256", "")
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError(f"invalid SHA-256 for {repository}/{artifact.get('path')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, help="Local root containing bb-* source directories."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--generate", action="store_true", help="Generate a manifest from --source."
    )
    parser.add_argument("--check", action="store_true", help="Compare --output with --source.")
    parser.add_argument(
        "--validate", action="store_true", help="Validate only the committed manifest structure."
    )
    arguments = parser.parse_args()

    if sum((arguments.generate, arguments.check, arguments.validate)) != 1:
        parser.error("choose exactly one of --generate, --check, or --validate")

    if arguments.validate:
        validate_structure(json.loads(arguments.output.read_text()))
        print("authority manifest structure valid")
        return 0

    if arguments.source is None:
        parser.error("--source is required for --generate and --check")
    expected = build_manifest(arguments.source)
    if arguments.generate:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n")
        print(arguments.output)
        return 0

    if not arguments.output.is_file() or json.loads(arguments.output.read_text()) != expected:
        print("authority manifest drift detected")
        return 1
    print("authority manifest hashes valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
