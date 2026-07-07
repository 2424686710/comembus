"""Tests for the result summary script."""

from __future__ import annotations

import csv
import json
import os
import tempfile
import unittest

from scripts.summarize_all_results import summarize_result_files


class ResultSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="comembus-summary-test-")
        self.results_dir = self.tempdir.name
        self.report_path = os.path.join(self.results_dir, "summary_report.md")
        self.metrics_path = os.path.join(self.results_dir, "summary_metrics.json")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_summary_generation_reads_csvs_and_writes_outputs(self) -> None:
        self._write_csv(
            "transport_bench.csv",
            ["mode", "latency_ms", "checksum_ok"],
            [
                {"mode": "direct_uds", "latency_ms": "1.0", "checksum_ok": "true"},
                {"mode": "shm_ref", "latency_ms": "0.5", "checksum_ok": "true"},
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
            ["memory_hit", "saved_steps"],
            [{"memory_hit": "true", "saved_steps": "2"}],
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
                "scenario_family",
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
                    "scenario_family": "database_timeout",
                },
                {
                    "mode": "structured_mode",
                    "approx_tokens": "40",
                    "total_latency_ms": "5",
                    "memory_hit": "true",
                    "saved_steps": "2",
                    "embedding_state_count": "1",
                    "capability_discovery_count": "3",
                    "scenario_family": "database_timeout",
                },
            ],
        )

        metrics = summarize_result_files(
            results_dir=self.results_dir,
            report_path=self.report_path,
            metrics_path=self.metrics_path,
        )

        self.assertTrue(os.path.exists(self.report_path))
        self.assertTrue(os.path.exists(self.metrics_path))
        self.assertEqual(metrics["memory_reuse"]["memory_hit_count"], 1)
        self.assertEqual(metrics["collaboration"]["embedding_state_count"], 1)
        with open(self.report_path, encoding="utf-8") as handle:
            self.assertIn("Transport Benchmark Summary", handle.read())
        with open(self.metrics_path, encoding="utf-8") as handle:
            parsed = json.loads(handle.read())
        self.assertIn("transport", parsed)

    def test_missing_csv_is_reported_but_does_not_crash(self) -> None:
        self._write_csv(
            "transport_bench.csv",
            ["mode", "latency_ms", "checksum_ok"],
            [{"mode": "direct_uds", "latency_ms": "1.0", "checksum_ok": "true"}],
        )

        metrics = summarize_result_files(
            results_dir=self.results_dir,
            report_path=self.report_path,
            metrics_path=self.metrics_path,
        )

        self.assertTrue(metrics["warnings"])
        self.assertTrue(os.path.exists(self.report_path))
        self.assertTrue(os.path.exists(self.metrics_path))

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
