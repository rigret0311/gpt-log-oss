from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

import gptlog_core


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_REPO = "https://github.com/rigret0311/gpt-log-oss"


class ReleaseMetadataTests(unittest.TestCase):
    def test_version_and_repository_metadata_are_canonical(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        metadata = pyproject["project"]
        self.assertEqual(pyproject["build-system"]["requires"], ["setuptools>=83"])
        self.assertEqual(metadata["version"], "0.1.0")
        self.assertEqual(gptlog_core.__version__, "0.1.0")
        self.assertEqual(metadata["urls"]["Homepage"], EXPECTED_REPO)
        self.assertEqual(metadata["urls"]["Repository"], EXPECTED_REPO)
        self.assertEqual(metadata["urls"]["Issues"], f"{EXPECTED_REPO}/issues")
        self.assertEqual(metadata["license"], "MIT")
        self.assertEqual(metadata["license-files"], ["LICENSE"])

    def test_license_is_standard_mit_and_uses_non_personal_holder(self):
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertTrue(license_text.startswith("MIT License\n"))
        self.assertIn("Copyright (c) 2026 GPT-Log contributors", license_text)
        self.assertIn("Permission is hereby granted, free of charge", license_text)
        self.assertIn('THE SOFTWARE IS PROVIDED "AS IS"', license_text)

    def test_readme_matches_release_identity_and_boundaries(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for required in (
            "# GPT-Log",
            "Current source version: `0.1.0`",
            EXPECTED_REPO,
            "Direct ZIP import is not supported",
            "one explicitly selected local directory",
            "Private Vulnerability Reporting",
            "MIT License",
        ):
            self.assertIn(required, readme)
        for stale in (
            "NOT SELECTED",
            "0.1.0-spike",
            "unapproved spike assumptions",
            "PENDING_REPO_CREATION",
            "Hosted Windows/Linux CI execution remains pending",
            "private predecessor",
        ):
            self.assertNotIn(stale, readme)

    def test_security_policy_uses_private_reporting_without_public_email(self):
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("Do not open a public GitHub Issue", security)
        self.assertIn("Private Vulnerability Reporting", security)
        self.assertIn("Only the latest released version", security)
        self.assertIn("No public security email is provided", security)
        self.assertIn("does not claim that Private Vulnerability Reporting is currently enabled", security)
        self.assertNotIn("repository and its Private Vulnerability Reporting setting do not exist yet", security)
        self.assertNotIn("mailto:", security)

    def test_ci_defines_both_platforms_and_release_path(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for required in (
            "ubuntu-latest",
            "windows-latest",
            'python-version: "3.11"',
            "python -m unittest discover -s tests -v",
            "python -m build",
            "pip', 'install', '--force-reinstall', '--no-deps'",
            "gptlog-core import",
            "gptlog-core search",
            "gptlog-core verify",
            '"pip>=26.2"',
        ):
            self.assertIn(required, workflow)
        for forbidden in ("secrets.", "/Users/", "C:\\Users\\"):
            self.assertNotIn(forbidden, workflow)

    def test_manifest_carries_release_source_evidence(self):
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        for required in (
            "include LICENSE",
            "include README.md",
            "include SECURITY.md",
            "include PUBLIC_REPO_SEED_MANIFEST.md",
            "include .github/workflows/ci.yml",
            "recursive-include tests *.py *.json",
        ):
            self.assertIn(required, manifest)

    def test_public_seed_manifest_has_fail_closed_boundaries(self):
        seed = (ROOT / "PUBLIC_REPO_SEED_MANIFEST.md").read_text(encoding="utf-8")
        for section in ("## INCLUDE", "## EXCLUDE", "## GENERATED", "## REVIEW_REQUIRED"):
            self.assertIn(section, seed)
        self.assertIn("gptlog_core/*.py", seed)
        self.assertIn("tests/fixtures/**/*.json", seed)
        self.assertIn("any other repository's Git history", seed)
        self.assertIn("real user exports", seed)
        for private_marker in (
            "legacy `",
            "private predecessor",
            "private Drive paths",
        ):
            self.assertNotIn(private_marker, seed)
        runbook = (ROOT / "docs" / "minimal-runbook.md").read_text(encoding="utf-8")
        self.assertIn("changed-payload conflict rejection", runbook)
        self.assertNotIn("UPSERT", runbook)


if __name__ == "__main__":
    unittest.main()
