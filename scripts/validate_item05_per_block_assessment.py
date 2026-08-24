#!/usr/bin/env python3
"""Validate Item 05 per-block assessment evidence without external services."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/item-05-per-block-requirements-assessments-gap-analysis-20260818.md"
EVIDENCE = ROOT / "docs/item-05-per-block-requirements-evidence-20260818.json"
BLOCKS = {"Consent", "Payments", "Scheduler", "File Management"}
DIMENSIONS = {
    "functional",
    "api",
    "data_audit",
    "error_failure",
    "security_cross_cutting",
    "testability",
    "dependency",
}
STATES = {"implemented", "locally_tested", "staging_tested", "officially_mapped", "not_evidenced"}
PRIORITIES = {"Must-fix", "Should-fix", "Nice-to-have"}
DEPENDENCIES = {"source-observed", "required-contract", "not-evidenced"}
SCOPES = {"local", "combined-rehearsal", "authorized-staging", "official-submission"}


def validate() -> dict:
    data = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert set(data["blocks"]) == BLOCKS
    assert set(data["dimensions"]) == DIMENSIONS
    records = data["records"]
    assert len(records) == len(BLOCKS) * len(DIMENSIONS)
    seen = set()
    for record in records:
        assert record["block"] in BLOCKS
        assert record["dimension"] in DIMENSIONS
        assert record["evidence_state"] in STATES
        assert record["priority"] in PRIORITIES
        assert record["dependency_class"] in DEPENDENCIES
        assert record["claim_scope"] in SCOPES and record["gate_scope"] in SCOPES
        assert record["official_authority"].startswith("https://github.com/GovStackWorkingGroup/")
        assert (ROOT / record["civicos_path"]).exists(), record["civicos_path"]
        assert (record["block"], record["dimension"]) not in seen
        seen.add((record["block"], record["dimension"]))
    text = DOC.read_text(encoding="utf-8")
    for marker in (
        "## Inherited Item 01–04 status",  # noqa: RUF001
        "## Scope-sensitive priority legend",
        "## Evidence-control matrix",
        "## Auditable Items 6–7 gate",  # noqa: RUF001
        "**Status at Stage 3:**",
    ):
        assert marker in text, marker
    return {"records": len(records), "blocks": len(BLOCKS)}


if __name__ == "__main__":
    result = validate()
    print(f"Item 05 evidence valid: {result['records']} records; {result['blocks']} blocks")
