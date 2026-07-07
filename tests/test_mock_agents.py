"""Tests for the mock multi-agent incident diagnosis helpers."""

from __future__ import annotations

import unittest

from comembus.state.patch import apply_patch
from examples.incident_diagnosis_mock.agents import (
    DEFAULT_LOG_SIZE_BYTES,
    analyze_config_text,
    analyze_log_blob,
    build_config_state_patch,
    build_initial_task_state,
    build_log_state_patch,
    build_mock_config_text,
    build_mock_log_blob,
    build_review_report_from_state,
    summarize_incident,
)


class MockAgentHelperTests(unittest.TestCase):
    def test_build_mock_log_blob_has_required_size(self) -> None:
        blob = build_mock_log_blob(DEFAULT_LOG_SIZE_BYTES)
        self.assertEqual(len(blob), DEFAULT_LOG_SIZE_BYTES)
        self.assertIn(b"DatabaseTimeout", blob)
        self.assertIn(b"ConnectionPoolExhausted", blob)

    def test_analyze_log_blob_detects_database_pool_signal(self) -> None:
        blob = build_mock_log_blob(DEFAULT_LOG_SIZE_BYTES)
        facts = analyze_log_blob(blob, "INC-1")
        self.assertEqual(facts["incident_id"], "INC-1")
        self.assertEqual(facts["suspected_component"], "database_pool")
        self.assertGreater(facts["database_timeout_count"], 0)
        self.assertGreater(facts["pool_exhausted_count"], 0)

    def test_analyze_config_text_extracts_risky_pool_size(self) -> None:
        facts = analyze_config_text(build_mock_config_text(), "INC-2")
        self.assertEqual(facts["incident_id"], "INC-2")
        self.assertEqual(facts["service_name"], "checkout-api")
        self.assertEqual(facts["pool_size"], 4)
        self.assertEqual(facts["timeout_ms"], 250)
        self.assertFalse(facts["retry_enabled"])
        self.assertEqual(facts["config_risk"], "database_pool_too_small")

    def test_summarize_incident_returns_expected_root_cause(self) -> None:
        report = summarize_incident(
            "INC-3",
            build_mock_log_blob(DEFAULT_LOG_SIZE_BYTES),
            build_mock_config_text(),
        )
        self.assertEqual(report["incident_id"], "INC-3")
        self.assertEqual(report["confidence"], "high")
        self.assertIn("database connection pool saturation", report["root_cause"].lower())
        self.assertIn("Increase database.pool_size", report["recommended_action"])

    def test_log_patch_applies_to_initial_task_state(self) -> None:
        state = build_initial_task_state(
            task_id="INC-4",
            goal="Diagnose checkout failures",
            log_ref_dict={"object_id": "obj-1", "shm_name": "shm-1", "size": 8388608},
        )
        patch = build_log_state_patch(
            state,
            analyze_log_blob(build_mock_log_blob(DEFAULT_LOG_SIZE_BYTES), "INC-4"),
        )

        updated = apply_patch(state, patch)

        self.assertEqual(patch.expected_version, 1)
        self.assertEqual(updated.version, 2)
        self.assertEqual(updated.phase, "log_analysis_complete")
        self.assertIn("log_analysis", updated.completed_steps)
        self.assertEqual(updated.facts["log_error"], "database timeout")

    def test_config_patch_applies_after_log_patch(self) -> None:
        state = build_initial_task_state(
            task_id="INC-5",
            goal="Diagnose checkout failures",
            log_ref_dict={"object_id": "obj-1", "shm_name": "shm-1", "size": 8388608},
        )
        log_state = apply_patch(
            state,
            build_log_state_patch(
                state,
                analyze_log_blob(build_mock_log_blob(DEFAULT_LOG_SIZE_BYTES), "INC-5"),
            ),
        )
        config_patch = build_config_state_patch(
            log_state,
            analyze_config_text(build_mock_config_text(), "INC-5"),
        )

        final_state = apply_patch(log_state, config_patch)
        report = build_review_report_from_state(final_state)

        self.assertEqual(config_patch.expected_version, 2)
        self.assertEqual(final_state.version, 3)
        self.assertEqual(final_state.phase, "review_ready")
        self.assertIn("config_check", final_state.completed_steps)
        self.assertEqual(final_state.facts["config_issue"], "database pool too small")
        self.assertIn("database connection pool saturation", report["root_cause"].lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
