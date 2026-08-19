"""Regression checks for CivicOS GovStack testing-site preparation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREPARATION_TOOL = ROOT / "scripts" / "govstack_testing_preparation.py"
CANDIDATES = {
    "consent": "civicos-consent",
    "payments": "civicos-payments",
    "scheduler": "civicos-scheduler",
    "file-management": "civicos-file-management",
}


class GovStackTestingPreparationTests(unittest.TestCase):
    def test_preparation_validator_matches_committed_authority_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "preparation-evidence.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(PREPARATION_TOOL),
                    "--validate",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            evidence = json.loads(output.read_text())
        self.assertEqual(evidence["errors"], [])
        self.assertEqual(len(evidence["candidates"]), 4)
        self.assertFalse(
            evidence["official_suite_executed"] if "official_suite_executed" in evidence else False
        )
        self.assertIn("not an API compliance result", evidence["claim"])

    def test_candidate_manifests_are_local_only_and_non_claiming(self):
        for key, directory in CANDIDATES.items():
            manifest = json.loads(
                (ROOT / "examples" / directory / "candidate-manifest.json").read_text()
            )
            self.assertEqual(manifest["target_policy"], "local-only", key)
            self.assertIn("not an API compliance result", manifest["claim_boundary"], key)
            self.assertTrue(manifest["civicos_surface"], key)

    def test_candidate_entrypoints_reject_non_local_execution_without_docker(self):
        for key, directory in CANDIDATES.items():
            environment = os.environ | {"GOVSTACK_TEST_TARGET": "staging"}
            completed = subprocess.run(
                [
                    str(ROOT / "examples" / directory / "test_entrypoint.sh"),
                    "--config",
                    "api-suite",
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 65, key)
            self.assertIn("Refusing non-local target", completed.stderr, key)

    def test_testing_preparation_document_links_to_official_workflow(self):
        document = (
            ROOT / "docs" / "govstack" / "testing" / "TESTING_SITE_PREPARATION.md"
        ).read_text()
        self.assertIn("testing.govstack.global/requirements", document)
        self.assertIn("test_entrypoint.sh --config api-suite", document)
        self.assertIn("not an API Compliance result", document)


if __name__ == "__main__":
    unittest.main()
