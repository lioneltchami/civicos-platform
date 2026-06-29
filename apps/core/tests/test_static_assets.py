"""Smoke tests for the public shell assets."""

from pathlib import Path

from django.test import SimpleTestCase


class StaticAssetPresenceTest(SimpleTestCase):
    def test_public_shell_assets_exist(self):
        repo_root = Path(__file__).resolve().parents[3]
        expected_paths = [
            repo_root / "static" / "js" / "htmx.min.js",
            repo_root / "static" / "js" / "alpine.min.js",
            repo_root / "static" / "images" / "favicon.svg",
        ]

        for path in expected_paths:
            self.assertTrue(path.is_file(), f"Missing static asset: {path}")
