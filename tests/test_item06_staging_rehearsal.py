import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_item06_staging_preflight.py"
MANIFEST = ROOT / "docs/staging/rehearsal-manifest.example.json"
CONTROLS = ROOT / "docs/staging/CONTROL_TEMPLATES_AND_REGRESSION_MATRIX.md"


def _module():
    spec = importlib.util.spec_from_file_location("item06_validator", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_example_manifest_fails_closed_until_authorised_fields_are_completed():
    errors = _module().validate(MANIFEST)
    assert errors
    assert any("unresolved" in error for error in errors)


def test_repository_safe_controls_preserve_non_execution_boundary():
    text = CONTROLS.read_text(encoding="utf-8")
    for marker in ("NOT RUN", "BLOCKED", "Items 1–5 regression matrix", "Do not retain secrets"):
        assert marker in text
