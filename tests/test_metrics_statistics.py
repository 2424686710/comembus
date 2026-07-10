"""Tests for standard-library metrics, statistics, and exact byte recording."""

from __future__ import annotations

import math
import socket
import unittest

from comembus.metrics.process_metrics import ProcessMetrics
from comembus.metrics.recorder import MetricsRecorder
from comembus.metrics.statistics import (
    ci95,
    maximum,
    mean,
    median,
    minimum,
    p50,
    percentile,
    standard_deviation,
    summarize,
)
from comembus.protocol import encode_frame
from comembus.transport.uds import recv_frame, send_frame


class StatisticsTests(unittest.TestCase):
    def test_mean_median_and_p50(self) -> None:
        values = [4, 1, 3, 2]
        self.assertEqual(mean(values), 2.5)
        self.assertEqual(median(values), 2.5)
        self.assertEqual(p50(values), 2.5)

    def test_percentiles_use_linear_interpolation(self) -> None:
        values = [1, 2, 3, 4]
        self.assertAlmostEqual(percentile(values, 95), 3.85)
        self.assertAlmostEqual(percentile(values, 99), 3.97)
        self.assertEqual(percentile(values, 0), 1)
        self.assertEqual(percentile(values, 100), 4)

    def test_standard_deviation_and_ci95(self) -> None:
        values = [1, 2, 3, 4]
        self.assertAlmostEqual(standard_deviation(values), math.sqrt(5.0 / 3.0))
        lower, upper = ci95(values)
        self.assertLess(lower, mean(values))
        self.assertGreater(upper, mean(values))
        self.assertEqual(ci95([7]), (7.0, 7.0))

    def test_min_max_and_summary(self) -> None:
        values = [3, 1, 9]
        self.assertEqual(minimum(values), 1)
        self.assertEqual(maximum(values), 9)
        result = summarize(values)
        self.assertEqual(result["min"], 1)
        self.assertEqual(result["max"], 9)
        self.assertIn("p95", result)
        self.assertIn("ci95_lower", result)

    def test_empty_and_non_finite_data_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            mean([])
        with self.assertRaises(ValueError):
            percentile([1, 2], 101)
        with self.assertRaises(ValueError):
            mean([1, float("nan")])


class RecorderAndProcessMetricsTests(unittest.TestCase):
    def test_uds_recorder_counts_exact_frames_including_header(self) -> None:
        recorder = MetricsRecorder()
        left, right = socket.socketpair()
        message = {"kind": "metric-test", "value": "payload"}
        try:
            send_frame(left, message, recorder)
            self.assertEqual(recv_frame(right, recorder), message)
        finally:
            left.close()
            right.close()
        expected = len(encode_frame(message))
        snapshot = recorder.snapshot()
        self.assertEqual(snapshot.sent_bytes, expected)
        self.assertEqual(snapshot.received_bytes, expected)
        self.assertEqual(snapshot.message_count, 1)
        self.assertEqual(snapshot.wire_bytes, expected)

    def test_process_metrics_reports_cpu_and_peak_rss(self) -> None:
        process = ProcessMetrics().start()
        sum(index * index for index in range(1000))
        usage = process.stop()
        self.assertGreaterEqual(usage.cpu_time_ms, 0.0)
        self.assertGreater(usage.peak_rss_kb, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
