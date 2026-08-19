import json
from pathlib import Path
from scripts.validate_item01_consent import validate_contract_map

ROOT = Path(__file__).resolve().parents[2]
def test_operation_contract_map_is_one_to_one_and_partial():
    matrix = json.loads((ROOT / "docs/item-01-consent-v23q4-operation-matrix.json").read_text())
    assert validate_contract_map(matrix) == []
    contract = json.loads((ROOT / "docs/item-01-consent-v23q4-operation-contract-map.json").read_text())
    assert len(contract["operations"]) == 42
    assert all(item["disposition"] == "partial" for item in contract["operations"])

def test_candidate_manifest_is_not_ready():
    manifest = json.loads((ROOT / "examples/civicos-consent/candidate-manifest.json").read_text())
    assert manifest["readiness"] == "NOT_READY"
    assert "never present" in manifest["submission_guard"]
