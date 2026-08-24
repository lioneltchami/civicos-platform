#!/usr/bin/env python3
"""Offline-only validation of a proposed Item 06 staging rehearsal manifest."""

import json
import sys
from pathlib import Path

REQUIRED = {
    "schema_version",
    "rehearsal_id",
    "authorization",
    "target",
    "release",
    "ownership",
    "controls",
    "outcome",
}
SECRET_TOKENS = ("secret", "password", "token", "apikey", "api_key", "private_key")


def validate(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    errors = []
    if set(data) != REQUIRED or data.get("schema_version") != 1:
        errors.append("invalid manifest schema")
    auth = data.get("authorization", {})
    target = data.get("target", {})
    release = data.get("release", {})
    ownership = data.get("ownership", {})
    controls = data.get("controls", {})
    if auth.get("authorized") is not False:
        errors.append("offline validator accepts only authorised=false")
    if (
        target.get("network_action_permitted") is not False
        or target.get("synthetic_data_only") is not True
    ):
        errors.append("manifest must prohibit network actions and require synthetic data")
    for value in (
        data.get("rehearsal_id"),
        auth.get("approval_reference"),
        target.get("alias"),
        release.get("commit_sha"),
        release.get("image_digest"),
        release.get("migration_set"),
        release.get("configuration_fingerprint"),
        ownership.get("deployment_owner"),
        ownership.get("rollback_owner"),
        ownership.get("evidence_custodian"),
        controls.get("rollback_plan_reference"),
        controls.get("evidence_location"),
    ):
        if not isinstance(value, str) or not value or "UNSET" in value or "REQUIRED" in value:
            errors.append("unresolved required field")
            break
    if (
        not isinstance(controls.get("abort_criteria"), list)
        or not controls["abort_criteria"]
        or "UNSET" in controls["abort_criteria"]
    ):
        errors.append("unresolved abort criteria")
    if data.get("outcome") not in {"NOT_RUN", "BLOCKED"}:
        errors.append("offline manifests may only record NOT_RUN or BLOCKED")

    def walk(node) -> None:  # noqa: ANN001
        if isinstance(node, dict):
            for key, value in node.items():
                if any(token in key.lower() for token in SECRET_TOKENS):
                    errors.append("secret-like key is prohibited")
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return errors


if __name__ == "__main__":
    manifest = (
        Path(sys.argv[1])
        if len(sys.argv) == 2
        else Path("docs/staging/rehearsal-manifest.example.json")
    )
    issues = validate(manifest)
    if issues:
        print("Item 06 preflight BLOCKED: " + "; ".join(sorted(set(issues))))
        raise SystemExit(1)
    print("Item 06 preflight valid: offline-only manifest; no remote action performed")
