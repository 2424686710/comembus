"""Tests for the shared blackboard memory module."""

from __future__ import annotations

import os
import tempfile
import unittest

from comembus.memory.blackboard import SharedBlackboard
from comembus.memory.embedding import HashEmbeddingEncoder, cosine_similarity
from comembus.memory.sqlite_store import SQLiteMemoryStore
from comembus.memory.unit import MemoryUnit


class MemoryBlackboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="comembus-memory-test-")
        self.db_path = os.path.join(self.tempdir.name, "memory.sqlite")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_memory_unit_round_trip(self) -> None:
        memory = MemoryUnit(
            memory_id="mem-1",
            task_id="task-1",
            source_agent="log-agent",
            created_at=123.5,
            task_topic="database timeout",
            memory_type="fact",
            summary="database timeout observed",
            content="timeout observed while connecting to database",
            tags=["database", "timeout"],
            confidence=0.9,
            metadata={"line_count": 10},
        )

        restored = MemoryUnit.from_dict(memory.to_dict())

        self.assertEqual(restored.to_dict(), memory.to_dict())
        self.assertEqual(restored.to_json_bytes(), memory.to_json_bytes())

    def test_hash_embedding_encoder_similarity(self) -> None:
        encoder = HashEmbeddingEncoder(dim=64)
        a = encoder.encode("database timeout wrong port")
        b = encoder.encode("wrong database port timeout")
        c = encoder.encode("filesystem cache miss")

        self.assertGreater(cosine_similarity(a, b), 0.5)
        self.assertLess(cosine_similarity(a, c), cosine_similarity(a, b))
        self.assertEqual(encoder.encode(""), [0.0] * 64)

    def test_sqlite_store_put_get_and_list_by_task(self) -> None:
        store = SQLiteMemoryStore(self.db_path)
        try:
            memory = MemoryUnit(
                memory_id="mem-1",
                task_id="task-1",
                source_agent="log-agent",
                created_at=1.0,
                task_topic="database timeout",
                memory_type="evidence",
                summary="database timeout evidence",
                content="found timeout in logs",
                tags=["database", "timeout"],
                confidence=1.0,
                metadata={"line": 42},
            )
            store.put(memory, [0.1, 0.2, 0.3])

            restored = store.get("mem-1")
            self.assertIsNotNone(restored)
            self.assertEqual(restored.to_dict(), memory.to_dict())
            self.assertEqual(len(store.list_by_task("task-1")), 1)
            self.assertEqual(len(store.list_all()), 1)
        finally:
            store.close()

    def test_shared_blackboard_write_and_search(self) -> None:
        board = SharedBlackboard(self.db_path, embedding_dim=64)
        try:
            first = board.write_memory(
                task_id="task-1",
                source_agent="log-agent",
                task_topic="database connection timeout",
                memory_type="evidence",
                summary="database timeout from wrong port",
                content="database timeout happened because the service used the wrong port",
                tags=["database", "timeout", "port"],
                confidence=0.95,
                metadata={"source": "logs"},
            )
            board.write_memory(
                task_id="task-2",
                source_agent="config-agent",
                task_topic="cache issue",
                memory_type="summary",
                summary="cache miss after restart",
                content="cache warmup is incomplete after restart",
                tags=["cache"],
                confidence=0.8,
                metadata={"source": "config"},
            )

            self.assertIsNotNone(board.get_memory(first.memory_id))
            self.assertEqual(len(board.list_task_memories("task-1")), 1)

            keyword_results = board.search_by_keyword("wrong port timeout", top_k=3)
            self.assertTrue(keyword_results)
            self.assertEqual(keyword_results[0].memory.memory_id, first.memory_id)

            tag_results = board.search_by_tag(["port"], top_k=3)
            self.assertTrue(tag_results)
            self.assertEqual(tag_results[0].memory.memory_id, first.memory_id)

            semantic_results = board.search_semantic("database timeout wrong port", top_k=3)
            self.assertTrue(semantic_results)
            self.assertEqual(semantic_results[0].memory.memory_id, first.memory_id)

            combined_results = board.search("database timeout wrong port", tags=["database"])
            self.assertTrue(combined_results)
            self.assertEqual(combined_results[0].memory.memory_id, first.memory_id)
            self.assertIn("semantic", combined_results[0].reason)
        finally:
            board.close()

    def test_empty_blackboard_search_and_close(self) -> None:
        board = SharedBlackboard(self.db_path, embedding_dim=32)
        try:
            self.assertEqual(board.search_by_keyword("anything"), [])
            self.assertEqual(board.search_by_tag(["database"]), [])
            self.assertEqual(board.search_semantic("database timeout"), [])
            self.assertEqual(board.search("database timeout"), [])
        finally:
            board.close()

        reopened = SQLiteMemoryStore(self.db_path)
        reopened.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

