"""Tests for rich incident scenario helpers."""

from __future__ import annotations

from pathlib import Path
import unittest

from examples.incident_diagnosis_mock.scenarios import (
    default_scenarios,
    load_scenarios,
    scenario_to_config_text,
    scenario_to_log_bytes,
)


class ScenarioTests(unittest.TestCase):
    def test_default_scenarios_cover_three_families(self) -> None:
        scenarios = default_scenarios()

        self.assertEqual(len(scenarios), 12)
        families = {scenario.family for scenario in scenarios}
        self.assertEqual(families, {"database_timeout", "permission_denied", "storage_full"})
        for family in families:
            self.assertGreaterEqual(
                sum(1 for scenario in scenarios if scenario.family == family),
                4,
            )

    def test_load_scenarios_matches_default_file(self) -> None:
        scenario_path = Path("examples/incident_diagnosis_mock/scenarios.jsonl")

        loaded = load_scenarios(str(scenario_path))
        defaults = default_scenarios()

        self.assertEqual([item.to_dict() for item in loaded], [item.to_dict() for item in defaults])

    def test_scenario_materialization_preserves_pattern_and_size(self) -> None:
        scenario = default_scenarios()[0]

        log_blob = scenario_to_log_bytes(scenario, size_bytes=1024)
        config_text = scenario_to_config_text(scenario)

        self.assertEqual(len(log_blob), 1024)
        self.assertIn(scenario.log_pattern.encode("utf-8"), log_blob)
        self.assertIn(scenario.config_issue, config_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
