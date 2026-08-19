import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "validate_item05_per_block_assessment.py"


def _validator_module():
    spec = importlib.util.spec_from_file_location("item05_validator", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_item05_evidence_index_is_valid():
    result = _validator_module().validate()
    assert result == {"records": 28, "blocks": 4}


def test_item05_evidence_controls_cover_required_vocabularies():
    module = _validator_module()
    assert module.BLOCKS == {"Consent", "Payments", "Scheduler", "File Management"}
    assert len(module.DIMENSIONS) == 7
    assert "not_evidenced" in module.STATES
