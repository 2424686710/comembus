"""Tests for measured transport calibration and profile-backed policy."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from comembus.transport.adaptive import AdaptiveTransportPolicy
from comembus.transport.calibrator import (
    AdaptiveTransportCalibrator,
    DEFAULT_RECEIVERS,
    DEFAULT_ROUNDS,
    DEFAULT_SIZES,
    DEFAULT_WARMUP,
    measure_transport_once,
)


class TransportCalibratorTests(unittest.TestCase):
    def test_required_default_matrix(self) -> None:
        calibrator = AdaptiveTransportCalibrator()
        self.assertEqual(calibrator.sizes, DEFAULT_SIZES)
        self.assertEqual(calibrator.receivers, DEFAULT_RECEIVERS)
        self.assertEqual(calibrator.warmup, 3)
        self.assertEqual(calibrator.rounds, 20)
        self.assertEqual(DEFAULT_WARMUP, 3)
        self.assertEqual(DEFAULT_ROUNDS, 20)

    def test_policy_loads_receiver_specific_thresholds(self) -> None:
        with tempfile.TemporaryDirectory(prefix="comembus-profile-test-") as directory:
            path = Path(directory) / "profile.json"
            path.write_text(
                json.dumps({"thresholds_by_receivers": {"1": 4096, "4": 1024}}),
                encoding="utf-8",
            )
            policy = AdaptiveTransportPolicy.from_profile(path)
            self.assertEqual(policy.choose_mode(1024, 1), "direct_uds")
            self.assertEqual(policy.choose_mode(4096, 1), "shm_ref")
            self.assertEqual(policy.choose_mode(1024, 4), "shm_ref")

    def test_missing_profile_uses_fixed_64k_fallback(self) -> None:
        policy = AdaptiveTransportPolicy.from_profile("/definitely/missing/profile.json")
        self.assertEqual(policy.choose_mode(1024, 1), "direct_uds")
        self.assertEqual(policy.choose_mode(64 * 1024, 1), "shm_ref")
        self.assertEqual(policy.choose_mode(1024, 4), "shm_ref")

    def test_measurement_separates_wire_and_shared_memory_bytes(self) -> None:
        data = b"z" * 4096
        direct = measure_transport_once("direct_uds", data, 2, 1)
        shared = measure_transport_once("shm_ref", data, 2, 1)
        self.assertTrue(direct.checksum_ok)
        self.assertTrue(shared.checksum_ok)
        self.assertEqual(direct.shm_bytes_written, 0)
        self.assertEqual(shared.shm_bytes_written, len(data))
        self.assertEqual(shared.shm_bytes_read, len(data) * 2)
        self.assertEqual(direct.message_count, 2)
        self.assertEqual(shared.message_count, 2)
        self.assertLess(shared.wire_bytes, direct.wire_bytes)

    def test_small_calibration_writes_loadable_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="comembus-calibration-test-") as directory:
            path = Path(directory) / "transport_profile.json"
            profile = AdaptiveTransportCalibrator(
                sizes=(1024,), receivers=(1,), warmup=0, rounds=2
            ).calibrate(path)
            self.assertTrue(path.exists())
            self.assertIn("1", profile["thresholds_by_receivers"])
            policy = AdaptiveTransportPolicy.from_profile(path, fallback_on_error=False)
            self.assertIn(policy.choose_mode(1024, 1), {"direct_uds", "shm_ref"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
