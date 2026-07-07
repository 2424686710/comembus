"""Tests for SVG result figure generation."""

from __future__ import annotations

import csv
import json
import os
import tempfile
import unittest

from scripts.generate_result_figures import FIGURE_FILENAMES, generate_result_figures


class ResultFigureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="comembus-figures-test-")
        self.results_dir = self.tempdir.name

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_generate_result_figures_creates_svg_outputs(self) -> None:
        self._write_csv(
            "transport_bench.csv",
            ["mode", "latency_ms", "checksum_ok"],
            [
                {"mode": "direct_uds", "latency_ms": "2.0", "checksum_ok": "true"},
                {"mode": "shm_ref", "latency_ms": "1.0", "checksum_ok": "true"},
            ],
        )
        self._write_csv(
            "state_patch_bench.csv",
            ["mode", "state_size", "full_state_bytes", "patch_bytes", "reduction_ratio", "version_ok"],
            [
                {
                    "mode": "patch",
                    "state_size": "small",
                    "full_state_bytes": "100",
                    "patch_bytes": "20",
                    "reduction_ratio": "0.2",
                    "version_ok": "true",
                }
            ],
        )
        self._write_csv(
            "memory_reuse_bench.csv",
            ["task_index", "memory_hit", "saved_steps"],
            [{"task_index": "1", "memory_hit": "true", "saved_steps": "2"}],
        )
        self._write_csv(
            "collaboration_bench.csv",
            [
                "mode",
                "approx_tokens",
                "total_latency_ms",
                "memory_hit",
                "saved_steps",
                "embedding_state_count",
                "capability_discovery_count",
            ],
            [
                {
                    "mode": "text_mode",
                    "approx_tokens": "100",
                    "total_latency_ms": "10",
                    "memory_hit": "false",
                    "saved_steps": "0",
                    "embedding_state_count": "0",
                    "capability_discovery_count": "0",
                },
                {
                    "mode": "structured_mode",
                    "approx_tokens": "40",
                    "total_latency_ms": "5",
                    "memory_hit": "true",
                    "saved_steps": "2",
                    "embedding_state_count": "1",
                    "capability_discovery_count": "3",
                },
            ],
        )
        with open(os.path.join(self.results_dir, "summary_metrics.json"), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "transport": {"by_mode": {"direct_uds": {"avg_latency_ms": 2.0}, "shm_ref": {"avg_latency_ms": 1.0}}},
                    "state_patch": {"by_state_size": {"small": {"full_state_bytes": 100, "patch_bytes": 20}}},
                    "memory_reuse": {"memory_hit_rate": 1.0, "total_saved_steps": 2},
                    "collaboration": {
                        "text_mode_total_tokens": 100,
                        "structured_mode_total_tokens": 40,
                        "text_mode_total_latency_ms": 10.0,
                        "structured_mode_total_latency_ms": 5.0,
                        "embedding_state_count": 1,
                        "capability_discovery_count": 3,
                    },
                },
                handle,
            )

        result = generate_result_figures(results_dir=self.results_dir)

        self.assertFalse(result["warnings"])
        for filename in FIGURE_FILENAMES:
            self.assertTrue(os.path.exists(os.path.join(self.results_dir, "figures", filename)))

    def test_generate_result_figures_handles_missing_files(self) -> None:
        result = generate_result_figures(results_dir=self.results_dir)

        self.assertTrue(result["warnings"])
        for filename in FIGURE_FILENAMES:
            self.assertTrue(os.path.exists(os.path.join(self.results_dir, "figures", filename)))

    def _write_csv(
        self,
        filename: str,
        fieldnames: list[str],
        rows: list[dict[str, str]],
    ) -> None:
        path = os.path.join(self.results_dir, filename)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)


if __name__ == "__main__":
    unittest.main(verbosity=2)
