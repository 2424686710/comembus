"""Tests for optional LLM adapter integration."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock
import urllib.error

from comembus.llm.adapter import LLMMessage, build_llm_client
from comembus.llm.agent import LLMReviewAgent
from comembus.llm.local_http_client import LocalHTTPChatClient
from comembus.llm.mock_client import MockLLMClient
from comembus.memory.unit import MemoryUnit
from comembus.state.task_state import TaskState
from examples.incident_diagnosis_mock.run_llm_agent_demo import run_llm_agent_demo


def build_state() -> TaskState:
    return TaskState(
        task_id="task-llm-1",
        version=3,
        goal="Diagnose a compressed database timeout incident",
        phase="review_ready",
        completed_steps=["planning", "log_analysis", "config_check"],
        pending_steps=["review"],
        facts={
            "log_error": "database timeout",
            "config_issue": "wrong database port",
            "log_summary": "database timeout repeated in checkout logs",
            "config_summary": "configuration uses the wrong database port",
        },
        errors=[],
        artifacts={},
    )


def build_memory() -> MemoryUnit:
    return MemoryUnit(
        memory_id="mem-1",
        task_id="task-prior",
        source_agent="review-agent",
        created_at=1.0,
        task_topic="database timeout",
        memory_type="summary",
        summary="wrong database port previously caused checkout timeout",
        content="root_cause=wrong database port caused database timeout",
        tags=["database", "timeout", "port"],
        confidence=0.95,
        metadata={},
    )


class LLMAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="comembus-llm-test-")
        self.db_path = os.path.join(self.tempdir.name, "llm.sqlite")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_mock_llm_client_returns_deterministic_result(self) -> None:
        client = MockLLMClient()
        response = client.generate(
            [
                LLMMessage(role="user", content="database timeout and wrong database port"),
            ]
        )

        self.assertEqual(response.provider, "mock")
        self.assertFalse(response.used_fallback)
        self.assertIn("wrong database port caused database timeout", response.content)

    def test_build_llm_client_mock_and_unknown_both_return_mock(self) -> None:
        self.assertIsInstance(build_llm_client("mock"), MockLLMClient)
        self.assertIsInstance(build_llm_client("unknown"), MockLLMClient)

    def test_llm_review_agent_generates_root_cause_from_facts(self) -> None:
        agent = LLMReviewAgent.from_provider(provider="mock")
        result = agent.review(
            task_state=build_state(),
            memories=[build_memory()],
            evidence=["database timeout repeated", "wrong database port configured"],
        )

        self.assertEqual(result["provider"], "mock")
        self.assertFalse(result["used_fallback"])
        self.assertEqual(
            result["root_cause"],
            "wrong database port caused database timeout",
        )

    def test_local_http_client_unreachable_endpoint_falls_back_without_raising(self) -> None:
        client = LocalHTTPChatClient(endpoint="http://127.0.0.1:9999/v1/chat/completions")
        with mock.patch(
            "comembus.llm.local_http_client.urllib.request.urlopen",
            side_effect=urllib.error.URLError("unreachable"),
        ):
            response = client.generate([LLMMessage(role="user", content="permission denied")])

        self.assertEqual(response.provider, "mock")
        self.assertTrue(response.used_fallback)
        self.assertIn("permission denied", response.content.lower())

    def test_run_llm_agent_demo_core_function_is_offline_testable(self) -> None:
        result = run_llm_agent_demo(provider="mock", db_path=self.db_path)

        self.assertEqual(result["provider"], "mock")
        self.assertFalse(result["used_fallback"])
        self.assertEqual(
            result["root_cause"],
            "wrong database port caused database timeout",
        )

    def test_run_llm_agent_demo_local_http_falls_back(self) -> None:
        with mock.patch(
            "comembus.llm.local_http_client.urllib.request.urlopen",
            side_effect=urllib.error.URLError("offline"),
        ):
            result = run_llm_agent_demo(
                provider="local_http",
                endpoint="http://127.0.0.1:9999/v1/chat/completions",
                db_path=self.db_path,
            )

        self.assertEqual(result["provider"], "mock")
        self.assertTrue(result["used_fallback"])
        self.assertIn("wrong database port", str(result["root_cause"]).lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
