"""End-to-end checks for the v1.4 failure injection matrix."""

from __future__ import annotations

import unittest

from benchmarks.bench_failure_recovery import run_benchmark
from comembus.reliability.failure_injector import FailureInjector, InjectedFailure


class FailureInjectionTests(unittest.TestCase):
    def test_failure_injector_fails_exact_configured_count(self) -> None:
        injector = FailureInjector({"commit": 2})
        with self.assertRaises(InjectedFailure):
            injector.trigger("commit")
        with self.assertRaises(InjectedFailure):
            injector.trigger("commit")
        injector.trigger("commit")
        self.assertEqual(injector.remaining("commit"), 0)

    def test_all_required_failure_scenarios_recover(self) -> None:
        rows = run_benchmark()
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(row.success for row in rows))
        by_name = {row.scenario: row for row in rows}
        self.assertTrue(
            by_name["duplicate_message_suppression"].duplicate_suppressed
        )
        self.assertTrue(by_name["consumer_crash_redelivery"].message_requeued)
        self.assertEqual(
            by_name["consumer_crash_redelivery"].delivery_attempts, 2
        )
        self.assertTrue(
            by_name["coordinator_crash_state_recovery"].state_recovered
        )
        self.assertTrue(by_name["object_lease_crash_reclaim"].object_reclaimed)
        self.assertTrue(all(row.shm_residue_count == 0 for row in rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
