import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools" / "run_file_management_tests.py"
MANIFEST = ROOT / "tools" / "file_management_test_layers.json"
GUIDE = ROOT / "docs/testing/file-management.md"
MATRIX = ROOT / "docs/testing/file-management-operation-matrix.md"
MAKEFILE = ROOT / "Makefile"


def test_runner_is_scoped_and_emits_safe_reports() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert 'DEFAULT_SCOPE = "apps/documents/tests"' in text
    assert 'LAYER_MANIFEST = ROOT / "tools" / "file_management_test_layers.json"' in text
    assert "junit-{args.layer}.xml" in text
    assert "pytest-{args.layer}.log" in text
    assert "coverage-{args.layer}.xml" in text
    assert "metadata-{args.layer}.json" in text
    assert 'official_harness_executed": False' in text
    assert "zero tests collected" in text
    assert "--collect-only" in text
    assert "SENSITIVE_VALUE" in text
    assert "FILE_MANAGEMENT_REPORT_DIR" in text
    assert "--cov=apps.documents" in text
    assert "--cov-fail-under=0" in text


def test_layer_manifest_is_complete_and_references_exact_test_modules() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["scope_root"] == "apps/documents/tests"
    assert set(payload["layers"]) == {"unit", "integration", "e2e"}
    selected = []
    for layer, files in payload["layers"].items():
        assert files, layer
        for file_name in files:
            path = ROOT / payload["scope_root"] / file_name
            assert path.is_file(), path
            selected.append(file_name)
    expected = sorted(path.name for path in (ROOT / payload["scope_root"]).glob("test_*.py"))
    assert sorted(selected) == expected
    assert len(selected) == len(set(selected))


def test_documentation_defines_layers_fixture_policy_reports_and_boundary() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    for phrase in (
        "local/CI candidate runner",
        "synthetic files",
        "--layer unit",
        "official-harness",
        "JUnit XML",
        "layer manifest",
        "zero tests",
        "redact",
    ):
        assert phrase in text


def test_operation_matrix_covers_required_operations_and_paths() -> None:
    text = MATRIX.read_text(encoding="utf-8")
    for operation in (
        "Upload/create",
        "Read/metadata",
        "Download/presign",
        "Version",
        "Delete/soft-delete",
        "Scan/promotion",
        "Quarantine/unavailable",
        "Retention/expiry",
        "Audit",
    ):
        assert operation in text
    assert "Happy-path node(s)" in text
    assert "Failure/rejection node(s)" in text


def test_makefile_exposes_canonical_runner_targets() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "test-file-management:" in text
    assert "test-file-management-collect:" in text
    assert "tools/run_file_management_tests.py" in text
