#!/usr/bin/env python3
"""Generate or validate offline GovStack operation/artifact traceability.

The generated map intentionally records unknown official equivalence as
``UNVERIFIED``. It inventories every discoverable API operation or Payments API
artifact from the pinned, reviewed source tree without contacting a network.
"""

# ruff: noqa: E501  # Traceability values are intentionally preserved as complete evidence text.
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "govstack" / "traceability.json"
STATUS_VOCABULARY = ["IMPLEMENTED_LOCAL", "UNVERIFIED", "DEFERRED", "UNSUPPORTED"]
REQUIRED_ROW_FIELDS = {
    "id",
    "building_block",
    "official_artifacts",
    "official_operation",
    "method",
    "official_path",
    "request_schema_source",
    "response_schema_source",
    "identifiers",
    "authentication",
    "authorization",
    "states",
    "statuses_errors",
    "civicos_surface",
    "local_test_evidence",
    "status",
    "reason",
}

LOCAL_SURFACES = {
    "consent": {
        "artifacts": ["bb-consent/api/consent-openapi.yaml", "bb-consent/test/plan.md"],
        "surface": ["apps/consent/govstack_urls.py", "apps/consent/govstack_views.py"],
        "tests": ["apps/consent/tests/test_govstack_api.py"],
    },
    "payments": {
        "artifacts": ["bb-payments/test/openAPI/plan.md"],
        "surface": ["apps/payments/govstack_urls.py", "apps/payments/govstack_views.py"],
        "tests": [
            "apps/payments/tests/test_govstack_bulk_payment.py",
            "apps/payments/tests/test_govstack_auth.py",
            "apps/payments/tests/test_govstack_p2g.py",
        ],
    },
    "scheduler": {
        "artifacts": [
            "bb-scheduler/api/Govstack_scheduler_BB_APIs.json",
            "bb-scheduler/test/plan.md",
        ],
        "surface": ["apps/appointments/govstack_urls.py", "apps/appointments/govstack_views.py"],
        "tests": [
            "apps/appointments/tests/test_govstack_auth.py",
            "apps/appointments/tests/test_govstack_appointment.py",
        ],
    },
    "file-management": {
        "artifacts": ["bb-file-management/api/swagger.json", "bb-file-management/test/plan.md"],
        "surface": [
            "apps/documents/urls.py",
            "apps/documents/views/citizen.py",
            "apps/documents/views/staff.py",
        ],
        "tests": [
            "apps/documents/tests/test_views_citizen.py",
            "apps/documents/tests/test_views_staff.py",
            "apps/documents/tests/test_tasks.py",
        ],
    },
}


def yaml_operations(path: Path) -> list[tuple[str, str, str]]:
    """Extract path/method/operationId pairs from the pinned OpenAPI YAML subset."""
    rows: list[tuple[str, str, str]] = []
    current_path: str | None = None
    current_method: str | None = None
    for line in path.read_text().splitlines():
        path_match = re.match(r"^  (/[^:]+):\s*$", line)
        if path_match:
            current_path = path_match.group(1)
            current_method = None
            continue
        method_match = re.match(r"^    (get|post|put|patch|delete):\s*$", line)
        if method_match and current_path:
            current_method = method_match.group(1).upper()
            rows.append((current_path, current_method, f"{current_method} {current_path}"))
            continue
        operation_match = re.match(r'^      operationId:\s*["\']?([^"\']+)', line)
        if operation_match and rows and current_method:
            rows[-1] = (rows[-1][0], rows[-1][1], operation_match.group(1).strip())
    return rows


def json_operations(path: Path) -> list[tuple[str, str, str]]:
    document = json.loads(path.read_text())
    rows: list[tuple[str, str, str]] = []
    for api_path, methods in sorted(document.get("paths", {}).items()):
        if not isinstance(methods, dict):
            continue
        for method, details in sorted(methods.items()):
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation = details.get("operationId") if isinstance(details, dict) else None
            rows.append((api_path, method.upper(), operation or f"{method.upper()} {api_path}"))
    return rows


def payments_artifacts(source_root: Path) -> list[tuple[str | None, str | None, str, str]]:
    base = source_root / "bb-payments" / "api"
    rows = []
    for artifact in sorted(base.rglob("*.yml")) + sorted(base.rglob("*.yaml")):
        relative = artifact.relative_to(source_root / "bb-payments").as_posix()
        rows.append((None, None, artifact.stem, relative))
    return rows


def official_rows(source_root: Path, building_block: str) -> list[dict]:
    config = LOCAL_SURFACES[building_block]
    source_file = {
        "consent": source_root / "bb-consent" / "api" / "consent-openapi.yaml",
        "scheduler": source_root / "bb-scheduler" / "api" / "Govstack_scheduler_BB_APIs.json",
        "file-management": source_root / "bb-file-management" / "api" / "swagger.yaml",
    }
    if building_block == "payments":
        discovered = payments_artifacts(source_root)
    elif building_block == "consent":
        discovered = [
            (*row, "api/consent-openapi.yaml")
            for row in yaml_operations(source_file[building_block])
        ]
    elif building_block == "file-management":
        # The pinned official swagger artifacts are empty in this revision. Record
        # that fact as a deferred artifact rather than inventing API operations.
        discovered = [
            (
                None,
                None,
                "No operation discoverable: pinned swagger artifact is empty",
                "api/swagger.yaml",
            )
        ]
    else:
        artifact = (
            source_file[building_block].relative_to(source_root / f"bb-{building_block}").as_posix()
        )
        discovered = [(*row, artifact) for row in json_operations(source_file[building_block])]

    manifest = json.loads((ROOT / "docs" / "govstack" / "authority-manifest.json").read_text())
    declared_artifacts = {
        item["path"]
        for repository in manifest["repositories"]
        if repository["repository"] == f"bb-{building_block}"
        for item in repository["artifacts"]
    }
    discovered_artifacts = {artifact for _, _, _, artifact in discovered}
    for artifact in sorted(declared_artifacts - discovered_artifacts):
        discovered.append(
            (
                None,
                None,
                f"Artifact inventory only: {artifact}",
                artifact,
            )
        )

    rows = []
    for index, (api_path, method, operation, artifact) in enumerate(discovered, start=1):
        rows.append(
            {
                "id": f"{building_block.upper().replace('-', '_')}-OFFICIAL-{index:03d}",
                "building_block": building_block,
                "official_artifacts": [f"bb-{building_block}/{artifact}"],
                "official_operation": operation,
                "method": method,
                "official_path": api_path,
                "request_schema_source": "Pinned official artifact; CivicOS equivalence unverified.",
                "response_schema_source": "Pinned official artifact; CivicOS equivalence unverified.",
                "identifiers": [],
                "authentication": "Official requirement must be mapped before a conformance claim.",
                "authorization": "Official requirement must be mapped before a conformance claim.",
                "states": [],
                "statuses_errors": "Official status/error equivalence is unverified.",
                "civicos_surface": config["surface"],
                "local_test_evidence": config["tests"],
                "status": "UNVERIFIED",
                "reason": "Official source is inventoried; no endpoint/schema/status equivalence is asserted.",
            }
        )
    return rows


def local_evidence_rows() -> dict[str, list[dict]]:
    return {
        "consent": [
            {
                "id": "CONSENT-LOCAL-SIGNATURE-UPDATE",
                "building_block": "consent",
                "official_artifacts": [
                    "bb-consent/api/consent-openapi.yaml",
                    "bb-consent/test/plan.md",
                ],
                "official_operation": "Local signature update evidence; official acceptance unverified",
                "method": None,
                "official_path": None,
                "request_schema_source": "Local serializer/service behavior.",
                "response_schema_source": "Local API behavior; official equivalence unverified.",
                "identifiers": ["ConsentRecord", "ConsentSignature", "ConsentRevision"],
                "authentication": "CivicOS consent actor authentication.",
                "authorization": "CivicOS ownership and local policy rules.",
                "states": ["granted", "signature_updated"],
                "statuses_errors": "Local behavior only; official mapping unverified.",
                "civicos_surface": ["apps/consent/services.py: ConsentService.update_signature"],
                "local_test_evidence": [
                    "apps/consent/tests/test_govstack_api.py: test_update_signature_creates_audit_entry",
                    "apps/consent/tests/test_govstack_api.py: test_update_signature_creates_revision",
                ],
                "status": "IMPLEMENTED_LOCAL",
                "reason": "Local action remains granted with details.trigger=signature_updated; official acceptance is unverified.",
            }
        ],
        "payments": [
            {
                "id": "PAYMENTS-LOCAL-REPLAY-EVIDENCE",
                "building_block": "payments",
                "official_artifacts": ["bb-payments/test/openAPI/plan.md"],
                "official_operation": "Local duplicate/replay evidence; official callback semantics unverified",
                "method": None,
                "official_path": None,
                "request_schema_source": "Local GovStack serializers and services.",
                "response_schema_source": "Local error translation; official mapping unverified.",
                "identifiers": ["requestId", "X-CorrelationID", "platform_tenant_id"],
                "authentication": "apps/payments/govstack_auth.py",
                "authorization": "Local registered-BB and tenant allow-list behavior.",
                "states": ["local success", "local failure", "local duplicate handling"],
                "statuses_errors": "Do not infer official replay/callback statuses from local tests.",
                "civicos_surface": [
                    "apps/payments/govstack_views.py",
                    "apps/payments/govstack_services.py",
                ],
                "local_test_evidence": [
                    "apps/payments/tests/test_govstack_p2g.py",
                    "apps/payments/tests/test_govstack_beneficiary.py",
                    "apps/payments/tests/test_govstack_auth.py",
                ],
                "status": "IMPLEMENTED_LOCAL",
                "reason": "Local idempotency, correlation and authorization evidence is preserved without an official equivalence claim.",
            }
        ],
        "scheduler": [
            {
                "id": "SCHEDULER-SINGLE-GOVERNMENT-GUARD",
                "building_block": "scheduler",
                "official_artifacts": [
                    "bb-scheduler/spec/8-service-apis.md",
                    "bb-scheduler/test/plan.md",
                ],
                "official_operation": "Local deployment boundary; multi-government role isolation deferred",
                "method": None,
                "official_path": None,
                "request_schema_source": "Not applicable to local deployment guard.",
                "response_schema_source": "Authentication failure for unsupported deployment scope.",
                "identifiers": ["requestor_id", "registered BB identity"],
                "authentication": "apps/appointments/govstack_auth.py",
                "authorization": "Local role resolution limited to a single-government deployment.",
                "states": ["single-government supported", "multi-government rejected"],
                "statuses_errors": "Unsupported scope raises local AuthenticationFailed; official equivalence unverified.",
                "civicos_surface": ["apps/appointments/govstack_auth.py"],
                "local_test_evidence": ["tests/govstack/test_evidence_artifacts.py"],
                "status": "IMPLEMENTED_LOCAL",
                "reason": "Multi-government isolation is not implemented and is deliberately rejected at the auth boundary.",
            }
        ],
        "file-management": [
            {
                "id": "FILE-MANAGEMENT-LOCAL-SECURITY-EVIDENCE",
                "building_block": "file-management",
                "official_artifacts": ["bb-file-management/test/plan.md"],
                "official_operation": "Local document access, scan and quarantine evidence; official lifecycle equivalence unverified",
                "method": None,
                "official_path": None,
                "request_schema_source": "Local document views/models.",
                "response_schema_source": "Local security/lifecycle responses; official equivalence unverified.",
                "identifiers": ["document UUID", "owner identity", "access token"],
                "authentication": "CivicOS citizen/staff authentication.",
                "authorization": "Local ownership, staff permission and access-token controls.",
                "states": ["pending_upload", "scanning", "active", "quarantined", "deleted"],
                "statuses_errors": "Local negative-case behavior only; official mapping unverified.",
                "civicos_surface": ["apps/documents/urls.py", "apps/documents/tasks.py"],
                "local_test_evidence": [
                    "apps/documents/tests/test_views_citizen.py",
                    "apps/documents/tests/test_views_staff.py",
                    "apps/documents/tests/test_tasks.py",
                ],
                "status": "IMPLEMENTED_LOCAL",
                "reason": "Local security controls remain behind a future contract-preserving adapter.",
            }
        ],
    }


def build_traceability(source_root: Path) -> dict:
    local_rows = local_evidence_rows()
    maps = {}
    for building_block in LOCAL_SURFACES:
        maps[building_block] = {
            "classification": "explicit CivicOS implementation; official wire conformance unverified",
            "rows": official_rows(source_root, building_block) + local_rows[building_block],
        }
    return {
        "schema_version": 2,
        "status_vocabulary": STATUS_VOCABULARY,
        "required_row_fields": sorted(REQUIRED_ROW_FIELDS),
        "maps": maps,
        "non_claimed_scope": {
            "local_modules": ["messaging", "workflow", "cms"],
            "not_done_yet": [
                "cloud-infrastructure-hosting",
                "digital-registries",
                "emarketplace",
                "esignature",
                "gis",
                "identity",
                "im-connector",
                "information-mediator",
                "registration",
                "template",
                "ux",
                "wallet",
            ],
        },
    }


def validate(data: dict) -> None:
    if data.get("schema_version") != 2:
        raise ValueError("traceability schema version is invalid")
    if data.get("status_vocabulary") != STATUS_VOCABULARY:
        raise ValueError("traceability status vocabulary is invalid")
    for building_block in LOCAL_SURFACES:
        rows = data.get("maps", {}).get(building_block, {}).get("rows", [])
        if not rows or not any(row["id"].endswith("-OFFICIAL-001") for row in rows):
            raise ValueError(f"missing official inventory rows for {building_block}")
        for row in rows:
            if not REQUIRED_ROW_FIELDS.issubset(row):
                raise ValueError(f"incomplete traceability row: {row.get('id')}")
            if row["status"] not in STATUS_VOCABULARY:
                raise ValueError(f"invalid traceability status: {row.get('id')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, help="Local root containing bb-* source directories."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--validate", action="store_true")
    arguments = parser.parse_args()
    if sum((arguments.generate, arguments.check, arguments.validate)) != 1:
        parser.error("choose exactly one of --generate, --check, or --validate")
    if arguments.validate:
        validate(json.loads(arguments.output.read_text()))
        print("traceability structure valid")
        return 0
    if arguments.source is None:
        parser.error("--source is required for --generate and --check")
    expected = build_traceability(arguments.source)
    if arguments.generate:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n")
        print(arguments.output)
        return 0
    if not arguments.output.is_file() or json.loads(arguments.output.read_text()) != expected:
        print("traceability drift detected")
        return 1
    print("traceability inventory valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
