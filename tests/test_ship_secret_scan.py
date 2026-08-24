"""Focused contract tests for the fail-closed ship-range secret scanner."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_ship_secret_scan.py"
MODULE_NAME = "civicos_ship_secret_scan_test_module"
SPEC = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
scanner = importlib.util.module_from_spec(SPEC)
sys.modules[MODULE_NAME] = scanner
SPEC.loader.exec_module(scanner)


def _record(index: int) -> dict[str, object]:
    return {
        "path": f"tests/fixture_{index}.py",
        "line": index,
        "sha256": f"{index:x}" * 64,
        "triage_id": f"SHIP-SEC-{index:03d}",
    }


def _write_allowlist(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "policy": "Exact path, line, and SHA-256 records only.",
                "approved_false_positives": records,
            }
        ),
        encoding="utf-8",
    )


def test_parse_allowlist_accepts_any_independently_reviewed_exact_record_count(
    tmp_path: Path,
) -> None:
    allowlist = tmp_path / "allowlist.json"
    expected = [_record(index) for index in range(1, 12)]
    _write_allowlist(allowlist, expected)

    records = scanner.parse_allowlist(allowlist)

    assert len(records) == len(expected)
    assert [(record.path, record.line, record.triage_id) for record in records] == [
        (item["path"], item["line"], item["triage_id"]) for item in expected
    ]


def test_parse_allowlist_rejects_duplicate_exact_records_even_when_cardinality_is_valid(
    tmp_path: Path,
) -> None:
    allowlist = tmp_path / "allowlist.json"
    duplicate = _record(1)
    _write_allowlist(allowlist, [duplicate, duplicate])

    with pytest.raises(scanner.ScanError, match="duplicate records"):
        scanner.parse_allowlist(allowlist)
