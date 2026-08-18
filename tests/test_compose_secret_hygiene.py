"""Focused guardrails for the development Compose and settings secret contract."""

import importlib.util
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = (ROOT / "docker-compose.yml").read_text()
ENV_EXAMPLE = (ROOT / ".env.example").read_text()
README = (ROOT / "README.md").read_text()
DEVELOPMENT_SETTINGS = ROOT / "config/settings/development.py"
REQUIRED_INTERPOLATION = (
    "DJANGO_SECRET_KEY: "
    "${DJANGO_SECRET_KEY:?Set DJANGO_SECRET_KEY in .env before starting the development stack}"
)


class ComposeSecretHygieneTests(unittest.TestCase):
    def _service_body(self, service: str) -> str:
        match = re.search(
            rf"^  {re.escape(service)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:|\Z)",
            COMPOSE,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, service)
        return match.group("body")

    def test_each_development_app_service_requires_the_environment_secret(self):
        for service in ("web", "worker-webhooks", "worker-receipts", "beat"):
            with self.subTest(service=service):
                block = self._service_body(service)
                self.assertIn(REQUIRED_INTERPOLATION, block)
                self.assertNotRegex(block, r"DJANGO_SECRET_KEY:\s*\$\{[^}]*:-")

        self.assertNotIn("dev-secret-key-change-in-production", COMPOSE)
        self.assertEqual(COMPOSE.count("DJANGO_SECRET_KEY: ${DJANGO_SECRET_KEY:?"), 4)

    def test_environment_template_and_readme_require_a_generated_local_secret(self):
        self.assertIn("DJANGO_SECRET_KEY=\n", ENV_EXAMPLE)
        self.assertNotIn("REPLACE_WITH_GENERATED_LOCAL_SECRET", ENV_EXAMPLE)
        self.assertIn("get_random_secret_key", ENV_EXAMPLE)
        self.assertIn("get_random_secret_key", README)
        self.assertIn("refuses to configure when it is missing or empty", README)
        self.assertIn("docker compose config", README)
        self.assertIn("docker compose config --environment", README)

    def test_development_settings_have_no_secret_default(self):
        development = DEVELOPMENT_SETTINGS.read_text()
        self.assertIn('SECRET_KEY = env("DJANGO_SECRET_KEY")', development)
        self.assertNotIn('default="django-insecure-', development)

    @unittest.skipUnless(importlib.util.find_spec("django"), "Django is not installed")
    def test_direct_development_settings_require_a_process_local_secret(self):
        code = "import django; django.setup()"
        environment = os.environ.copy()
        environment.pop("DJANGO_SECRET_KEY", None)
        environment["DJANGO_SETTINGS_MODULE"] = "config.settings.development"

        missing = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY", missing.stderr + missing.stdout)

        environment["DJANGO_SECRET_KEY"] = "review-only-process-local-secret"
        loaded = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(loaded.returncode, 0, loaded.stderr + loaded.stdout)


if __name__ == "__main__":
    unittest.main()
