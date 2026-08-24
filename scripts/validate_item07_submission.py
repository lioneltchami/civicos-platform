#!/usr/bin/env python3
"""Offline-only validation for Item 07 submission preparation; never posts externally."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "docs/item-07-submissions/evidence-index.json"
LEDGER = ROOT / "docs/item-07-submissions/PORTAL_CHECKLIST_AND_RECEIPT_LEDGER.md"
EXPECTED = {"consent", "payments", "scheduler", "file-management"}
FORBIDDEN = {"SUBMITTED", "OFFICIAL_PASS", "STAGING_PASS", "CERTIFIED"}


def validate():  # noqa: ANN201
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    errors = []
    if data.get("schema_version") != 1 or data.get("release_state") != "NON_RELEASE_COMMIT":
        errors.append("unsafe release state")
    if (
        data.get("portal_state") != "NOT_SUBMITTED"
        or data.get("external_execution") != "NOT_PERFORMED"
    ):
        errors.append("unsafe portal/external state")
    candidates = data.get("candidates", [])
    if {x.get("id") for x in candidates} != EXPECTED:
        errors.append("candidate set is incomplete")
    for candidate in candidates:
        if candidate.get("decision") != "NOT_READY":
            errors.append("candidate decision must remain NOT_READY")
        for path in (candidate.get("dossier"), candidate.get("candidate_manifest")):
            if not path or not (ROOT / path).is_file():
                errors.append("candidate artifact missing")
    ledger = LEDGER.read_text(encoding="utf-8")
    if "NOT SUBMITTED" not in ledger or any(
        token in ledger for token in ("Submission Date: 20", "Jira: GS-", "Receipt: http")
    ):
        errors.append("unsafe receipt ledger")
    if any(candidate.get("decision") in FORBIDDEN for candidate in candidates):
        errors.append("forbidden candidate decision")
    if data.get("portal_state") in FORBIDDEN or data.get("external_execution") in FORBIDDEN:
        errors.append("forbidden top-level claim")
    return errors


if __name__ == "__main__":
    errors = validate()
    if errors:
        print("Item 07 dossier BLOCKED: " + "; ".join(sorted(set(errors))))
        raise SystemExit(1)
    print("Item 07 dossier valid: preparation only; no portal or external action performed")
