"""Tests for the minimal CodeAct sandbox."""

from __future__ import annotations

import tempfile
import unittest

from comembus.codeact.sandbox import run_code_sandbox
from comembus.codeact.tool_agent import CodeActToolAgent
from comembus.memory.blackboard import SharedBlackboard
from comembus.state.task_state import TaskState
from examples.incident_diagnosis_mock.run_codeact_demo import run_codeact_demo


class CodeActSandboxTests(unittest.TestCase):
    def test_safe_code_runs(self) -> None:
        result = run_code_sandbox(
            "numbers = [1, 2, 3]\nresult = {'value': sum(numbers)}",
            inputs={},
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["timeout"])
        self.assertEqual(result["result"], {"value": 6})

    def test_import_os_is_blocked(self) -> None:
        result = run_code_sandbox(
            "import os\nresult = {'value': 1}",
            inputs={},
        )

        self.assertFalse(result["ok"])
        self.assertIn("Import", str(result["error"]))

    def test_open_is_blocked(self) -> None:
        result = run_code_sandbox(
            "result = open('forbidden.txt')",
            inputs={},
        )

        self.assertFalse(result["ok"])
        self.assertIn("open is not allowed", str(result["error"]))

    def test_timeout_code_returns_timeout(self) -> None:
        result = run_code_sandbox(
            "\n".join(
                [
                    "result = 0",
                    "for i in range(10 ** 9):",
                    "    result = result + i",
                ]
            ),
            inputs={},
            timeout_sec=0.2,
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["timeout"])
        self.assertIn("timed out", str(result["error"]))

    def test_codeact_tool_agent_returns_root_cause(self) -> None:
        agent = CodeActToolAgent()
        task_state = TaskState(
            task_id="codeact-test-task",
            version=1,
            goal="derive a root cause from compressed facts",
            phase="codeact_ready",
            completed_steps=[],
            pending_steps=["codeact_review"],
            facts={
                "log_error": "database timeout",
                "config_port": "wrong database port",
            },
            errors=[],
            artifacts={},
        )

        result = agent.run(task_state=task_state)

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["root_cause"],
            "wrong database port caused database timeout",
        )

    def test_run_codeact_demo_core_logic_is_testable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="comembus-codeact-") as tempdir:
            db_path = f"{tempdir}/codeact_demo.sqlite"
            result = run_codeact_demo(db_path=db_path, timeout_sec=1.0)

            self.assertTrue(result["ok"])
            self.assertEqual(
                result["root_cause"],
                "wrong database port caused database timeout",
            )
            board = SharedBlackboard(db_path)
            try:
                memories = board.list_task_memories(result["task_id"])
            finally:
                board.close()
            self.assertEqual(len(memories), 1)
            self.assertEqual(memories[0].memory_id, result["memory_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
