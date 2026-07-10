"""Fairness and component-effect tests for all rigorous ablation modes."""

from __future__ import annotations

import unittest

from benchmarks.bench_ablation import (
    ABLATION_MODES,
    AblationRunner,
    benchmark_rows,
    deterministic_text_summary,
    prepare_facts,
)
from examples.incident_diagnosis_mock.scenarios import default_scenarios


class AblationModesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scenario = default_scenarios()[1]
        cls.facts = prepare_facts(cls.scenario, log_size_bytes=16 * 1024)

    def test_deterministic_summary_uses_same_fact_inputs(self) -> None:
        first = deterministic_text_summary(self.facts.log_blob, self.facts.config_text)
        second = deterministic_text_summary(self.facts.log_blob, self.facts.config_text)
        self.assertEqual(first, second)
        self.assertIn(self.scenario.family, first)
        self.assertIn(self.scenario.expected_root_cause, first)

    def test_all_modes_use_five_agents_and_produce_correct_root_cause(self) -> None:
        for mode in ABLATION_MODES:
            with self.subTest(mode=mode):
                row = AblationRunner(mode, self.facts, round_index=1).run()
                self.assertTrue(row["root_cause_correct"])
                self.assertEqual(row["agent_count"], 5)
                self.assertEqual(row["message_count"], 5)
                self.assertEqual(row["sent_bytes"], row["received_bytes"])

    def test_component_removals_have_expected_measured_effects(self) -> None:
        full = AblationRunner("structured_full", self.facts, 1).run()
        text = AblationRunner("text_full_context", self.facts, 1).run()
        no_shm = AblationRunner("structured_no_shm", self.facts, 1).run()
        no_patch = AblationRunner("structured_no_patch", self.facts, 1).run()
        no_memory = AblationRunner("structured_no_memory", self.facts, 1).run()
        no_embedding = AblationRunner("structured_no_embedding", self.facts, 1).run()
        no_capability = AblationRunner("structured_no_capability", self.facts, 1).run()

        self.assertLess(full["wire_bytes"], text["wire_bytes"])
        self.assertGreater(no_shm["wire_bytes"], full["wire_bytes"])
        self.assertEqual(no_shm["object_ref_count"], 0)
        self.assertGreater(no_patch["state_bytes"], full["state_bytes"])
        self.assertEqual(no_patch["state_patch_count"], 0)
        self.assertEqual(no_memory["memory_ref_count"], 0)
        self.assertEqual(no_memory["saved_steps"], 0)
        self.assertEqual(no_embedding["embedding_ref_count"], 0)
        self.assertEqual(no_capability["capability_discovery_count"], 0)

    def test_benchmark_adds_round_statistics(self) -> None:
        rows = benchmark_rows(
            scenarios=[self.scenario],
            rounds=2,
            warmup=0,
            modes=("structured_full",),
            log_size_bytes=8192,
        )
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertIn("p50_latency_ms", row)
            self.assertIn("p95_latency_ms", row)
            self.assertIn("p99_latency_ms", row)
            self.assertIn("latency_ci95_lower_ms", row)


if __name__ == "__main__":
    unittest.main(verbosity=2)
