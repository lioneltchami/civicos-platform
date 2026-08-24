"""Regression guardrails for CivicOS GovStack evidence artifacts.

These checks validate declared evidence boundaries. They deliberately do not
claim official harness execution or wire-level conformance.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOVSTACK_DOCS = ROOT / "docs" / "govstack"
MANIFEST_TOOL = ROOT / "scripts" / "govstack_authority_manifest.py"
TRACEABILITY_TOOL = ROOT / "scripts" / "govstack_traceability.py"
ARCHIVE_TOOL = ROOT / "scripts" / "archive_govstack_run.py"

REQUIRED_FIELDS = {
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


class GovStackEvidenceArtifactTests(unittest.TestCase):
    def test_authority_manifest_has_the_pinned_explicit_building_blocks(self) -> None:
        manifest = json.loads((GOVSTACK_DOCS / "authority-manifest.json").read_text())
        self.assertEqual(manifest["schema_version"], 1)
        self.assertFalse(manifest["network_access"])
        by_name = {item["repository"]: item for item in manifest["repositories"]}
        self.assertEqual(
            set(by_name),
            {"bb-consent", "bb-payments", "bb-scheduler", "bb-file-management"},
        )
        for entry in by_name.values():
            self.assertEqual(len(entry["commit"]), 40)
            self.assertTrue(entry["artifacts"])
            for artifact in entry["artifacts"]:
                self.assertEqual(len(artifact["sha256"]), 64)

    def test_authority_manifest_tool_validates_the_committed_structure(self) -> None:
        completed = subprocess.run(  # noqa: S603
            [sys.executable, str(MANIFEST_TOOL), "--validate"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("authority manifest structure valid", completed.stdout)

    def test_traceability_rows_are_explicit_about_unverified_contracts(self) -> None:
        manifest = json.loads((GOVSTACK_DOCS / "authority-manifest.json").read_text())
        data = json.loads((GOVSTACK_DOCS / "traceability.json").read_text())
        allowed_statuses = set(data["status_vocabulary"])
        declared_artifacts = {
            f"{repository['repository']}/{artifact['path']}"
            for repository in manifest["repositories"]
            for artifact in repository["artifacts"]
        }
        mapped_artifacts = {
            artifact
            for mapping in data["maps"].values()
            for row in mapping["rows"]
            for artifact in row["official_artifacts"]
        }
        self.assertTrue(declared_artifacts.issubset(mapped_artifacts))
        self.assertEqual(
            set(data["maps"]),
            {"consent", "payments", "scheduler", "file-management"},
        )
        for building_block, mapping in data["maps"].items():
            self.assertTrue(mapping["rows"], building_block)
            for row in mapping["rows"]:
                self.assertTrue(REQUIRED_FIELDS.issubset(row), row["id"])
                self.assertIn(row["status"], allowed_statuses)
                self.assertTrue(row["official_artifacts"])
                self.assertTrue(row["civicos_surface"])
                self.assertTrue(row["local_test_evidence"])
                self.assertTrue(row["reason"])
        self.assertEqual(
            set(data["non_claimed_scope"]["local_modules"]),
            {"messaging", "workflow", "cms"},
        )
        self.assertIn("identity", data["non_claimed_scope"]["not_done_yet"])

    def test_traceability_tool_validates_the_committed_operation_inventory(self) -> None:
        completed = subprocess.run(  # noqa: S603
            [sys.executable, str(TRACEABILITY_TOOL), "--validate"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("traceability structure valid", completed.stdout)

    def test_archival_tool_refuses_to_run_without_explicit_reviewed_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run.json"
            completed = subprocess.run(  # noqa: S603
                [
                    sys.executable,
                    str(ARCHIVE_TOOL),
                    "--adapter-id",
                    "reviewed-local-adapter",
                    "--traceability-row",
                    "PAYMENTS-G2P-BULK",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("refusing to execute", completed.stderr)

    def test_archival_tool_records_adapter_configuration_and_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "reviewed-adapter.json"
            output = Path(directory) / "run.json"
            config.write_text('{"target":"non-production"}\n')
            completed = subprocess.run(  # noqa: S603
                [
                    sys.executable,
                    str(ARCHIVE_TOOL),
                    "--execute",
                    "--adapter-id",
                    "reviewed-local-adapter",
                    "--traceability-row",
                    "PAYMENTS-LOCAL-REPLAY-EVIDENCE",
                    "--adapter-config",
                    str(config),
                    "--dependency",
                    "python==local",
                    "--output",
                    str(output),
                    "--",
                    sys.executable,
                    "--version",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            record = json.loads(output.read_text())
        self.assertEqual(record["result"], "passed")
        self.assertEqual(record["dependencies"], ["python==local"])
        self.assertEqual(len(record["adapter_config_sha256"]), 64)

    def test_scope_document_does_not_make_a_certification_claim(self) -> None:
        scope = (GOVSTACK_DOCS / "SCOPE.md").read_text()
        overview = (ROOT / "docs" / "PROJECT_OVERVIEW_AND_STATUS.md").read_text()
        self.assertIn("not current GovStack Building Block claims", scope)
        self.assertIn("No document in this directory is a certification", scope)
        self.assertIn("single-government", scope)
        self.assertIn("not** a GovStack Building Block conformance", overview)

    def test_scheduler_rejects_an_unsupported_multi_government_scope(self) -> None:
        from django.test import override_settings
        from rest_framework.exceptions import AuthenticationFailed
        from rest_framework.request import Request
        from rest_framework.test import APIRequestFactory

        from apps.appointments.govstack_auth import GovStackSchedulerAuth

        request = Request(APIRequestFactory().get("/govstack/scheduler/"))
        with override_settings(GOVSTACK_SCHEDULER_DEPLOYMENT_SCOPE="multi-government"):
            with self.assertRaises(AuthenticationFailed):
                GovStackSchedulerAuth().authenticate(request)


if __name__ == "__main__":
    unittest.main()
