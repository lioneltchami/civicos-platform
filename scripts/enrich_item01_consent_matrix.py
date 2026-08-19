"""Enrich the Item 01 Consent evidence matrix from a supplied pinned OpenAPI artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs/item-01-consent-v23q4-operation-matrix.json"
RENDERED_MATRIX = ROOT / "docs/item-01-consent-v23q4-operation-matrix.md"


def schema_name(schema: Any) -> str:
    """Return a conservative descriptive schema reference without resolving it."""
    if not schema:
        return "none"
    if isinstance(schema, dict) and "$ref" in schema:
        return str(schema["$ref"])
    if isinstance(schema, dict) and "type" in schema:
        return str(schema["type"])
    return "inline schema"


def operation_metadata(path: str, method: str, operation: dict[str, Any]) -> dict[str, Any]:
    """Extract non-speculative matrix metadata from one official OpenAPI operation."""
    parameters = operation.get("parameters", [])
    path_parameters = [
        parameter.get("name", "unresolved parameter")
        for parameter in parameters
        if isinstance(parameter, dict) and parameter.get("in") == "path"
    ]
    request_body = operation.get("requestBody", {})
    request_schema = "none"
    if isinstance(request_body, dict):
        for media in request_body.get("content", {}).values():
            if isinstance(media, dict):
                request_schema = schema_name(media.get("schema"))
                break

    response_schemas: dict[str, list[str]] = {}
    error_definitions: list[str] = []
    for status, response in operation.get("responses", {}).items():
        schemas: list[str] = []
        if isinstance(response, dict):
            for media in response.get("content", {}).values():
                if isinstance(media, dict):
                    schemas.append(schema_name(media.get("schema")))
        response_schemas[str(status)] = schemas or ["none"]
        if str(status) != "default" and not str(status).startswith("2"):
            error_definitions.append(str(status))

    security = sorted(
        {
            scheme
            for requirement in operation.get("security", [])
            if isinstance(requirement, dict)
            for scheme in requirement
        }
    )
    return {
        "operationId": operation["operationId"],
        "method": method.upper(),
        "path": path,
        "pathParameters": path_parameters,
        "requestSchema": request_schema,
        "responseSchemas": response_schemas,
        "statusCodes": sorted(str(status) for status in operation.get("responses", {})),
        "errorDefinitions": error_definitions,
        "security": security or ["no operation-level security requirement"],
    }


def render_matrix(matrix: dict[str, Any]) -> str:
    """Render a concise Markdown view directly from the machine-readable evidence."""
    authority = matrix["authority"]
    lines = [
        "# Item 01 Consent v23Q4 Operation Matrix",
        "",
        f"Baseline: `{authority['baseline']}`; official OpenAPI SHA-256: `{authority['sha256']}`.",
        (
            "All rows remain **partial** because metadata alignment is not local "
            "wire-conformance or testing-site evidence."
        ),
        "",
        "| Group | Method | Path | Operation ID | Status | Security | "
        "Local evidence | Disposition |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in matrix["operations"]:
        status = ", ".join(row["statusCodes"])
        security = ", ".join(row["security"])
        lines.append(
            "| {group} | `{method}` | `{path}` | `{operation_id}` | {status} | {security} | "
            "documented; wire-unverified | **{disposition}** |".format(
                group=row["group"],
                method=row["method"],
                path=row["path"],
                operation_id=row["operationId"],
                status=status,
                security=security,
                disposition=row["disposition"],
            )
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    """Update the local evidence matrix from a supplied published-v23Q4 OpenAPI file."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--openapi", required=True, type=Path)
    args = parser.parse_args()

    spec = yaml.safe_load(args.openapi.read_text())
    official: dict[str, dict[str, Any]] = {}
    for path, path_item in spec["paths"].items():
        for method, operation in path_item.items():
            if method.lower() in {"get", "post", "put", "delete", "patch"}:
                official[operation["operationId"]] = operation_metadata(path, method, operation)

    matrix = json.loads(MATRIX.read_text())
    rows = {row["operationId"]: row for row in matrix["operations"]}
    if set(rows) != set(official):
        missing = sorted(set(official) - set(rows))
        unexpected = sorted(set(rows) - set(official))
        raise SystemExit(f"operation identity mismatch; missing={missing}, unexpected={unexpected}")

    for operation_id, metadata in official.items():
        row = rows[operation_id]
        row.update(metadata)
        row["civicosRoute"] = (
            "apps/consent/govstack_urls.py and apps/consent/govstack_views.py require "
            "row-level route/serializer/status proof; wire conformance unverified"
        )
        row["test"] = (
            "tests/govstack/test_item01_consent_matrix.py; "
            "targeted operation test not yet linked"
        )
        row["versionDisposition"] = (
            "v23Q4 acceptance baseline; current main delta tracked separately"
        )
        row["externalGate"] = "official wire-conformance/staging and testing-site evidence"
        row["disposition"] = "partial"

    matrix["schemaVersion"] = 3
    matrix["authority"] = {
        "artifact": "Supplied official published-v23Q4 Consent OpenAPI",
        "sha256": hashlib.sha256(args.openapi.read_bytes()).hexdigest(),
        "baseline": "published v23Q4",
        "mainDelta": "Current main is tracked separately and is not an acceptance baseline",
    }
    MATRIX.write_text(json.dumps(matrix, indent=2, ensure_ascii=False) + "\n")
    RENDERED_MATRIX.write_text(render_matrix(matrix))
    print(f"enriched {len(official)} v23Q4 operations from {args.openapi}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
