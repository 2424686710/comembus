"""Memory provenance, version, TTL, supersession, migration, and dedup tests."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest

from comembus.memory.blackboard import SharedBlackboard
from comembus.memory.provenance import MemoryProvenance, build_provenance
from comembus.memory.sqlite_store import SQLiteMemoryStore
from comembus.memory.unit import MemoryUnit, compute_content_hash


class MemoryProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="comembus-provenance-")
        self.path = os.path.join(self.directory.name, "memory.sqlite")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_provenance_and_memory_unit_round_trip(self) -> None:
        provenance = build_provenance(
            "task-1",
            "review-agent",
            evidence_memory_ids=["evidence-1"],
            derivation="reviewed",
            recorded_at=12.0,
        )
        parsed = MemoryProvenance.from_dict(provenance)
        self.assertEqual(parsed.source_task_id, "task-1")
        memory = MemoryUnit(
            memory_id="mem-1",
            task_id="task-1",
            source_agent="review-agent",
            created_at=10.0,
            task_topic="topic",
            memory_type="strategy",
            summary="summary",
            content="stable content",
            tags=["tag"],
            confidence=0.9,
            metadata={},
            version=2,
            valid_from=10.0,
            expires_at=20.0,
            parent_memory_ids=["parent-1"],
            provenance=provenance,
        )
        restored = MemoryUnit.from_dict(memory.to_dict())
        self.assertEqual(restored.to_dict(), memory.to_dict())
        self.assertEqual(memory.content_hash, compute_content_hash("stable content"))
        self.assertTrue(memory.is_reusable(at_time=15.0))
        self.assertFalse(memory.is_reusable(at_time=20.0))
        payload = memory.to_dict()
        payload["content_hash"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "content_hash"):
            MemoryUnit.from_dict(payload)

    def test_blackboard_deduplicates_content_hash_and_filters_ttl(self) -> None:
        board = SharedBlackboard(self.path)
        try:
            first = board.write_memory(
                "task-1",
                "review-agent",
                "topic",
                "strategy",
                "first summary",
                "identical content",
                tags=["root"],
                valid_from=100.0,
            )
            duplicate = board.write_memory(
                "task-2",
                "other-agent",
                "other topic",
                "strategy",
                "different summary",
                "identical content",
                tags=["other"],
                valid_from=100.0,
            )
            expired = board.write_memory(
                "task-3",
                "review-agent",
                "expired",
                "strategy",
                "expired root strategy",
                "expired unique content",
                tags=["root"],
                valid_from=100.0,
                expires_at=150.0,
            )
            self.assertEqual(first.memory_id, duplicate.memory_id)
            self.assertEqual(len(board.list_memories()), 2)
            results = board.search("root strategy", tags=["root"], at_time=200.0)
            self.assertNotIn(expired.memory_id, [item.memory.memory_id for item in results])
        finally:
            board.close()

    def test_superseded_memory_is_excluded_and_version_lineage_persisted(self) -> None:
        board = SharedBlackboard(self.path)
        try:
            old = board.write_memory(
                "task-old",
                "review-agent",
                "database",
                "strategy",
                "old wrong diagnosis",
                "old content",
                tags=["database"],
                version=1,
            )
            new = board.write_memory(
                "task-new",
                "review-agent",
                "database",
                "strategy",
                "new corrected diagnosis",
                "new content",
                tags=["database"],
                version=2,
                supersedes_memory_id=old.memory_id,
            )
            old_reloaded = board.get_memory(old.memory_id)
            self.assertEqual(old_reloaded.superseded_by, new.memory_id)
            self.assertIn(old.memory_id, new.parent_memory_ids)
            results = board.search("diagnosis", tags=["database"])
            self.assertEqual(results[0].memory.memory_id, new.memory_id)
            self.assertNotIn(old.memory_id, [result.memory.memory_id for result in results])
        finally:
            board.close()

    def test_old_sqlite_schema_is_migrated_without_data_loss(self) -> None:
        connection = sqlite3.connect(self.path)
        connection.execute(
            """
            CREATE TABLE memories (
                memory_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                source_agent TEXT NOT NULL, created_at REAL NOT NULL,
                task_topic TEXT NOT NULL, memory_type TEXT NOT NULL,
                summary TEXT NOT NULL, content TEXT NOT NULL,
                tags_json TEXT NOT NULL, confidence REAL NOT NULL,
                metadata_json TEXT NOT NULL, embedding_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy",
                "task",
                "agent",
                10.0,
                "topic",
                "fact",
                "summary",
                "legacy content",
                "[]",
                1.0,
                "{}",
                "[0.0]",
            ),
        )
        connection.commit()
        connection.close()
        store = SQLiteMemoryStore(self.path)
        try:
            memory = store.get("legacy")
            self.assertEqual(memory.content_hash, compute_content_hash("legacy content"))
            self.assertEqual(memory.version, 1)
            self.assertEqual(memory.valid_from, 10.0)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
