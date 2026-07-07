"""Tests for text_mode versus structured_mode collaboration experiments."""

from __future__ import annotations

import os
import tempfile
import unittest

from comembus.collab.metrics import estimate_tokens
from comembus.collab.protocol import AgentCapability, StructuredMessage, TextMessage
from comembus.collab.structured_mode import StructuredCollaborationRunner
from comembus.collab.text_mode import TextCollaborationRunner
from comembus.memory.blackboard import SharedBlackboard
from benchmarks.bench_collaboration_modes import benchmark_rows


class CollaborationModesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="comembus-collab-test-")
        self.db_path = os.path.join(self.tempdir.name, "collaboration.sqlite")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_estimate_tokens_handles_empty_and_normal_text(self) -> None:
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("abcd"), 1)
        self.assertEqual(estimate_tokens("abcde"), 2)

    def test_text_and_structured_message_round_trip(self) -> None:
        capability = AgentCapability(
            agent_id="log-agent",
            role="log_analysis",
            actions=["analyze_logs"],
            input_types=["object_ref"],
            output_types=["state_patch"],
            description="reads logs",
        )
        structured = StructuredMessage(
            message_id="msg-1",
            task_id="task-1",
            source_agent="planner-agent",
            target_agent="log-agent",
            action_type="analyze_logs",
            params={"task_topic": "database timeout"},
            result={"summary": "ok"},
            capability=capability.to_dict(),
            object_refs=[{"shm_name": "comembus_obj", "size": 8}],
            state_patch={"expected_version": 1},
            memory_refs=["mem-1"],
            created_at=1.0,
        )
        text = TextMessage(
            message_id="msg-2",
            task_id="task-1",
            source_agent="planner-agent",
            target_agent="review-agent",
            text="full natural language context",
            created_at=2.0,
        )

        self.assertEqual(AgentCapability.from_dict(capability.to_dict()).to_dict(), capability.to_dict())
        self.assertEqual(
            StructuredMessage.from_dict(structured.to_dict()).to_dict(),
            structured.to_dict(),
        )
        self.assertEqual(TextMessage.from_dict(text.to_dict()).to_dict(), text.to_dict())

    def test_text_runner_returns_correct_root_cause(self) -> None:
        metrics = TextCollaborationRunner(
            task_index=1,
            task_topic="database timeout",
            text_context_bytes=8192,
        ).run()
        self.assertTrue(metrics.root_cause_correct)

    def test_structured_runner_returns_correct_root_cause_and_refs(self) -> None:
        metrics = StructuredCollaborationRunner(
            task_index=1,
            task_topic="database timeout",
            db_path=self.db_path,
        ).run()
        self.assertTrue(metrics.root_cause_correct)
        self.assertGreaterEqual(metrics.object_ref_count, 1)
        self.assertGreaterEqual(metrics.state_patch_count, 1)
        self.assertEqual(metrics.capability_count, 5)
        self.assertGreaterEqual(metrics.capability_discovery_count, 3)
        self.assertGreaterEqual(metrics.embedding_state_count, 1)

    def test_structured_runner_uses_preloaded_memory(self) -> None:
        board = SharedBlackboard(self.db_path)
        try:
            board.write_memory(
                task_id="prior-task",
                source_agent="review-agent",
                task_topic="database timeout",
                memory_type="strategy",
                summary="reuse wrong port diagnosis",
                content="database timeout wrong port skip full_log_scan",
                tags=["database", "port", "strategy"],
                confidence=0.95,
                metadata={},
            )
            metrics = StructuredCollaborationRunner(
                task_index=2,
                task_topic="similar database connection failure",
                db_path=self.db_path,
                blackboard=board,
            ).run()
            self.assertTrue(metrics.memory_hit)
        finally:
            board.close()

    def test_structured_mode_uses_fewer_tokens_than_text_mode(self) -> None:
        text_metrics = TextCollaborationRunner(
            task_index=1,
            task_topic="database timeout",
            text_context_bytes=16384,
        ).run()
        structured_metrics = StructuredCollaborationRunner(
            task_index=1,
            task_topic="database timeout",
            db_path=self.db_path,
        ).run()
        self.assertLess(structured_metrics.approx_tokens, text_metrics.approx_tokens)

    def test_benchmark_fields_are_complete(self) -> None:
        rows = benchmark_rows(task_count=2, text_context_bytes=8192, db_path=self.db_path)
        self.assertEqual(len(rows), 4)
        expected_fields = {
            "mode",
            "task_index",
            "task_topic",
            "message_count",
            "text_chars",
            "approx_tokens",
            "protocol_bytes",
            "object_ref_count",
            "state_patch_count",
            "memory_ref_count",
            "non_text_state_bytes",
            "shared_object_bytes",
            "memory_hit",
            "reused_memory_id",
            "baseline_steps",
            "actual_steps",
            "saved_steps",
            "total_latency_ms",
            "root_cause_correct",
            "scenario_family",
            "capability_count",
            "capability_discovery_count",
            "embedding_state_count",
            "embedding_state_bytes",
        }
        self.assertEqual(set(rows[0].to_dict().keys()), expected_fields)


if __name__ == "__main__":
    unittest.main(verbosity=2)
