"""Validate Item 01 Consent evidence with optional pinned-official OpenAPI comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs/item-01-consent-v23q4-operation-matrix.json"
REQUIRED = {
    "operationId",
    "group",
    "method",
    "path",
    "pathParameters",
    "requestSchema",
    "responseSchemas",
    "statusCodes",
    "errorDefinitions",
    "security",
    "civicosRoute",
    "test",
    "versionDisposition",
    "externalGate",
    "disposition",
}


def sha256(path: Path) -> str:
    """Return the SHA-256 digest of a supplied official artifact."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def official_ops(openapi: Path) -> dict[str, dict[str, object]]:
    """Load exact operation identity/status data from the supplied OpenAPI YAML."""
    spec: dict[str, Any] = yaml.safe_load(openapi.read_text())
    operations: dict[str, dict[str, object]] = {}
    for path, path_item in spec["paths"].items():
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "delete", "patch"}:
                continue
            operations[operation["operationId"]] = {
                "path": path,
                "method": method.upper(),
                "responses": sorted(str(status) for status in operation.get("responses", {})),
            }
    return operations


def validate(data: dict[str, object], operations: dict[str, dict[str, object]] | None) -> list[str]:
    """Validate matrix structure and optionally compare it to a supplied OpenAPI artifact."""
    errors: list[str] = []
    rows = data.get("operations", [])
    if not isinstance(rows, list):
        return ["operations must be a list"]

    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            errors.append("operation row must be an object")
            continue
        operation_id = row.get("operationId")
        if not isinstance(operation_id, str) or not operation_id:
            errors.append("operation row has no operationId")
            continue
        if operation_id in seen:
            errors.append(f"duplicate operationId {operation_id}")
        seen.add(operation_id)
        missing = REQUIRED - row.keys()
        if missing:
            errors.append(f"{operation_id}: missing {sorted(missing)}")
        if row.get("disposition") not in {"match", "partial", "missing", "constrained_adapter"}:
            errors.append(f"{operation_id}: invalid disposition")
        if row.get("disposition") == "match" and row.get("externalGate") != "none":
            errors.append(f"{operation_id}: cannot be match with an external gate")

        if operations is None:
            continue
        official = operations.get(operation_id)
        if official is None:
            errors.append(f"{operation_id}: absent from supplied OpenAPI")
            continue
        for key in ("method", "path"):
            if row.get(key) != official[key]:
                errors.append(f"{operation_id}: {key} differs from supplied OpenAPI")
        if sorted(row.get("statusCodes", [])) != official["responses"]:
            errors.append(f"{operation_id}: statusCodes differ from supplied OpenAPI")

    if operations is not None:
        if len(rows) != len(operations):
            errors.append(f"row count {len(rows)} != OpenAPI operation count {len(operations)}")
        for operation_id in set(operations) - seen:
            errors.append(f"missing OpenAPI operation {operation_id}")

    return errors


def main() -> int:
    """Run structural validation and, when supplied, exact OpenAPI identity validation."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", action="store_true")
    parser.add_argument(
        "--openapi",
        type=Path,
        default=os.environ.get("GOVSTACK_CONSENT_OPENAPI"),
        help="Path to the pinned official Consent v23Q4 OpenAPI artifact.",
    )
    args = parser.parse_args()

    data = json.loads(MATRIX.read_text())
    openapi = args.openapi
    operations = official_ops(openapi) if openapi else None
    errors = validate(data, operations)
    if errors:
        print("Item 01 Consent matrix invalid:")
        print("\n".join(f"- {error}" for error in errors))
        return 1

    mode = "official comparison" if openapi else "offline structural validation"
    print(f"Item 01 Consent matrix valid: {len(data['operations'])} rows ({mode})")
    if openapi:
        print(f"pinned OpenAPI sha256: {sha256(openapi)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
