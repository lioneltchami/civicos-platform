import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_item01_matrix_is_structurally_valid() -> None:
    result = subprocess.run(  # noqa: S603 -- fixed local Python executable and repository script.
        [sys.executable, str(ROOT / "scripts/validate_item01_consent.py"), "--validate"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_item01_rows_do_not_claim_unproven_match() -> None:
    data = json.loads((ROOT / "docs/item-01-consent-v23q4-operation-matrix.json").read_text())
    assert len(data["operations"]) == 42
    assert all(row["disposition"] != "match" for row in data["operations"])
    assert all(row["externalGate"] != "none" for row in data["operations"])


def test_consent_lifecycle_and_audit_inventory_is_executable() -> None:
    from apps.consent.models import ConsentAuditEntry, ConsentRecord, ConsentRevision

    assert hasattr(ConsentRecord, "withdraw") or hasattr(ConsentRecord, "status")
    assert any("hash" in field.name.lower() for field in ConsentRevision._meta.fields)
    assert any("action" in field.name.lower() for field in ConsentAuditEntry._meta.fields)
