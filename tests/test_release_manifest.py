"""Release manifest generation tests."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.create_release_manifest import (
    ROOT,
    build_release_manifest,
    write_release_manifest,
)


class ReleaseManifestTests(unittest.TestCase):
    def test_manifest_contains_release_audit_fields_without_credentials(self) -> None:
        manifest = build_release_manifest(ROOT)
        required = {
            "git_commit",
            "python_version",
            "os_release",
            "test_count",
            "result_file_sha256",
            "shm_residue_count",
            "generated_at",
        }
        self.assertTrue(required <= set(manifest))
        self.assertGreaterEqual(int(manifest["test_count"]), 1)
        self.assertEqual(len(str(manifest["git_commit"])), 40)
        serialized = json.dumps(manifest).lower()
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("authorization", serialized)

    def test_manifest_write_is_valid_json(self) -> None:
        with tempfile.TemporaryDirectory(prefix="comembus-manifest-") as directory:
            output = Path(directory) / "release_manifest.json"
            manifest = build_release_manifest(ROOT)
            write_release_manifest(output, manifest)
            restored = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(restored["git_commit"], manifest["git_commit"])
        self.assertEqual(restored["test_count"], manifest["test_count"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
