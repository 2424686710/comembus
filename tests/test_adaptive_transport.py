"""Adaptive transport policy tests for CoMemBus."""

from __future__ import annotations

import unittest

from comembus.transport.adaptive import AdaptiveTransportPolicy


class AdaptiveTransportPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = AdaptiveTransportPolicy()

    def test_small_single_receiver_uses_direct_uds(self) -> None:
        self.assertEqual(self.policy.choose_mode(1024, 1), "direct_uds")

    def test_threshold_size_uses_shm_ref(self) -> None:
        self.assertEqual(self.policy.choose_mode(64 * 1024, 1), "shm_ref")

    def test_large_single_receiver_uses_shm_ref(self) -> None:
        self.assertEqual(self.policy.choose_mode(1024 * 1024, 1), "shm_ref")

    def test_small_multi_receiver_uses_shm_ref(self) -> None:
        self.assertEqual(self.policy.choose_mode(1024, 4), "shm_ref")


if __name__ == "__main__":
    unittest.main(verbosity=2)
