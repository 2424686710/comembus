"""SQLite-backed storage for shared blackboard memories."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List

from .unit import MemoryUnit


class SQLiteMemoryStore:
    """Persist memory units and their embeddings in SQLite."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def put(self, memory: MemoryUnit, embedding: List[float]) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO memories (
                memory_id,
                task_id,
                source_agent,
                created_at,
                task_topic,
                memory_type,
                summary,
                content,
                tags_json,
                confidence,
                metadata_json,
                embedding_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory.memory_id,
                memory.task_id,
                memory.source_agent,
                memory.created_at,
                memory.task_topic,
                memory.memory_type,
                memory.summary,
                memory.content,
                json.dumps(memory.tags, separators=(",", ":"), sort_keys=True),
                memory.confidence,
                json.dumps(memory.metadata, separators=(",", ":"), sort_keys=True),
                json.dumps(embedding, separators=(",", ":")),
            ),
        )
        self._conn.commit()

    def get(self, memory_id: str) -> MemoryUnit | None:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
        if row is None:
            return None
        return self._memory_from_row(row)

    def list_by_task(self, task_id: str) -> List[MemoryUnit]:
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE task_id = ? ORDER BY created_at ASC, memory_id ASC",
            (task_id,),
        ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def list_all(self) -> List[MemoryUnit]:
        rows = self._conn.execute(
            "SELECT * FROM memories ORDER BY created_at ASC, memory_id ASC"
        ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def list_all_records(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM memories ORDER BY created_at ASC, memory_id ASC"
        ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    def _ensure_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                source_agent TEXT NOT NULL,
                created_at REAL NOT NULL,
                task_topic TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                summary TEXT NOT NULL,
                content TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                confidence REAL NOT NULL,
                metadata_json TEXT NOT NULL,
                embedding_json TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def _memory_from_row(self, row: sqlite3.Row) -> MemoryUnit:
        return MemoryUnit.from_dict(
            {
                "memory_id": row["memory_id"],
                "task_id": row["task_id"],
                "source_agent": row["source_agent"],
                "created_at": row["created_at"],
                "task_topic": row["task_topic"],
                "memory_type": row["memory_type"],
                "summary": row["summary"],
                "content": row["content"],
                "tags": json.loads(row["tags_json"]),
                "confidence": row["confidence"],
                "metadata": json.loads(row["metadata_json"]),
            }
        )

    def _record_from_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "memory": self._memory_from_row(row),
            "embedding": json.loads(row["embedding_json"]),
        }

