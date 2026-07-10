#!/usr/bin/env python3
"""Create a deterministic CoMemBus release audit manifest."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import unittest
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]


def build_release_manifest(root: Path = ROOT) -> Dict[str, Any]:
    results_dir = root / "results"
    return {
        "git_commit": _git_commit(root),
        "python_version": platform.python_version(),
        "os_release": _os_release(),
        "test_count": _test_count(root),
        "result_file_sha256": _result_hashes(results_dir),
        "shm_residue_count": _shm_residue_count(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_release_manifest(path: Path, manifest: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _git_commit(root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(root),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _test_count(root: Path) -> int:
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    suite = unittest.TestLoader().discover(str(root / "tests"))
    return suite.countTestCases()


def _result_hashes(results_dir: Path) -> Dict[str, str]:
    hashes: Dict[str, str] = {}
    if not results_dir.exists():
        return hashes
    for path in sorted(results_dir.rglob("*")):
        if not path.is_file() or path.name == "release_manifest.json":
            continue
        relative = path.relative_to(results_dir.parent).as_posix()
        digest = sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        hashes[relative] = digest.hexdigest()
    return hashes


def _os_release() -> str:
    open_euler_path = Path("/etc/openEuler-release")
    if open_euler_path.exists():
        return open_euler_path.read_text(encoding="utf-8", errors="replace").strip()
    os_release_path = Path("/etc/os-release")
    if os_release_path.exists():
        values: Dict[str, str] = {}
        for raw_line in os_release_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            if "=" not in raw_line or raw_line.lstrip().startswith("#"):
                continue
            key, value = raw_line.split("=", 1)
            values[key] = value.strip().strip('"')
        return values.get("PRETTY_NAME", values.get("NAME", "unknown"))
    return "unknown"


def _shm_residue_count() -> int:
    shm_path = Path("/dev/shm")
    if not shm_path.is_dir():
        return 0
    return sum(1 for path in shm_path.iterdir() if path.name.startswith("comembus_"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="results/release_manifest.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    manifest = build_release_manifest(ROOT)
    write_release_manifest(output, manifest)
    print(f"wrote release manifest to {output.relative_to(ROOT)}")
    print(f"test_count={manifest['test_count']}")
    print(f"shm_residue_count={manifest['shm_residue_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
