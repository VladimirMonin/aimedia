#!/usr/bin/env python3
"""Bounded negative controls and reproduction/rollback checks in temporary dirs."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_runtime import build_runtime
from validate_package import validate_package


class RuntimePackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="infographic-package-tests-")
        cls.temp_root = Path(cls.workspace.name)
        cls.candidate = cls.temp_root / "candidate"
        build_runtime(ROOT, cls.candidate, verify_hashes=True)

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def candidate_copy(self) -> Path:
        path = self.temp_root / self._testMethodName
        shutil.copytree(self.candidate, path)
        return path

    def test_missing_required_contract_is_reported_without_traceback(self):
        root = self.candidate_copy()
        (root / "contracts/semantic-spec.schema.json").unlink()
        result = validate_package(root, "runtime")
        self.assertFalse(result["valid"])
        self.assertIn("missing required file: contracts/semantic-spec.schema.json", result["errors"])

    def test_malformed_manifest_is_rejected(self):
        root = self.candidate_copy()
        (root / "manifest.yaml").write_text("distributions: [broken", encoding="utf-8")
        result = validate_package(root, "runtime")
        self.assertFalse(result["valid"])
        self.assertTrue(result["errors"][0].startswith("invalid package manifest:"))

    def test_nonmapping_manifest_is_rejected(self):
        root = self.candidate_copy()
        (root / "manifest.yaml").write_text("- unexpected\n", encoding="utf-8")
        self.assertFalse(validate_package(root, "runtime")["valid"])

    def test_duplicate_manifest_keys_are_rejected(self):
        root = self.candidate_copy()
        target = root / "manifest.yaml"
        target.write_text(target.read_text(encoding="utf-8") + "\nversion: another\n", encoding="utf-8")
        result = validate_package(root, "runtime")
        self.assertFalse(result["valid"])
        self.assertIn("duplicate YAML mapping key", result["errors"][0])

    def test_traversal_allowlist_is_rejected(self):
        root = self.candidate_copy()
        manifest = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
        manifest["distributions"]["runtime"]["files"].append("../outside.txt")
        (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
        result = validate_package(root, "runtime")
        self.assertFalse(result["valid"])
        self.assertIn("invalid relative package path", result["errors"][0])

    def test_hash_tampering_is_rejected(self):
        root = self.candidate_copy()
        target = root / "templates/job-brief.md"
        target.write_text(target.read_text(encoding="utf-8") + "\nEdited after export.\n", encoding="utf-8")
        self.assertTrue(validate_package(root, "runtime")["valid"])
        result = validate_package(root, "runtime", verify_hashes=True)
        self.assertFalse(result["valid"])
        self.assertIn("hash mismatch: templates/job-brief.md", result["errors"])

    def test_missing_receipt_is_optional_unless_requested(self):
        root = self.candidate_copy()
        (root / "runtime-hashes.json").unlink()
        self.assertTrue(validate_package(root, "runtime")["valid"])
        self.assertFalse(validate_package(root, "runtime", verify_hashes=True)["valid"])

    def test_unexpected_historical_file_is_rejected(self):
        root = self.candidate_copy()
        (root / "evals").mkdir()
        (root / "evals/old.md").write_text("Historical result", encoding="utf-8")
        result = validate_package(root, "runtime")
        self.assertFalse(result["valid"])
        self.assertTrue(any("unexpected files" in error for error in result["errors"]))

    def test_cannot_override_profile_to_skip_development_checks(self):
        self.assertFalse(validate_package(ROOT, "runtime")["valid"])

    def test_builder_refuses_existing_destination(self):
        before = (self.candidate / "runtime-hashes.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "already exists"):
            build_runtime(ROOT, self.candidate)
        self.assertEqual(before, (self.candidate / "runtime-hashes.json").read_bytes())

    def test_unchanged_sources_produce_identical_receipts(self):
        other = self.temp_root / "second-export"
        build_runtime(ROOT, other, verify_hashes=True)
        self.assertEqual((self.candidate / "runtime-hashes.json").read_bytes(), (other / "runtime-hashes.json").read_bytes())
        receipt = json.loads((other / "runtime-hashes.json").read_text(encoding="utf-8"))
        self.assertNotIn("source_root", receipt)
        self.assertTrue(all(not Path(name).is_absolute() and "\\" not in name for name in receipt["files"]))

    def test_candidate_rollback_copy_preserves_every_package_byte(self):
        # Exercise backup -> new copy -> exact restoration in isolated temp paths.
        backup = self.temp_root / "rollback-backup"
        restored = self.temp_root / "rollback-restored"
        shutil.copytree(self.candidate, backup)
        shutil.copytree(backup, restored)
        def inventory(path):
            return {file.relative_to(path).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
                    for file in path.rglob("*") if file.is_file() and "__pycache__" not in file.parts}
        self.assertEqual(inventory(backup), inventory(restored))
        self.assertTrue(validate_package(restored, "runtime")["valid"])


if __name__ == "__main__":
    unittest.main()
