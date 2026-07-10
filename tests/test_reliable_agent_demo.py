"""End-to-end tests for the v1.6 reliable multi-agent demo."""

from __future__ import annotations

import os
import subprocess
import unittest

from examples.incident_diagnosis_mock.run_reliable_agent_demo import (
    run_reliable_agent_demo,
)


class ReliableAgentDemoTests(unittest.TestCase):
    def test_reliable_demo_integrates_delivery_state_and_object_recovery(self) -> None:
        result = run_reliable_agent_demo(visibility_timeout=0.03)

        for field in (
            "message_requeued",
            "duplicate_suppressed",
            "state_recovered",
            "patch_rebased",
            "object_reclaimed",
            "root_cause_correct",
        ):
            self.assertTrue(result[field], field)
        self.assertEqual(result["delivery_attempts"], 2)
        self.assertEqual(result["business_execution_count"], 1)
        self.assertEqual(result["state_version"], 3)
        self.assertEqual(result["shm_residue_count"], 0)

    def test_reliable_demo_script_emits_release_audit_flags(self) -> None:
        completed = subprocess.run(
            ["bash", "scripts/run_reliable_agent_demo.sh"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        for field in (
            "message_requeued",
            "duplicate_suppressed",
            "state_recovered",
            "patch_rebased",
            "object_reclaimed",
            "root_cause_correct",
        ):
            self.assertIn(f"{field}=true", completed.stdout)
        self.assertIn(
            "OK: reliable multi-agent demo completed",
            completed.stdout,
        )
        self.assertFalse(
            any(name.startswith("comembus_") for name in os.listdir("/dev/shm"))
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
