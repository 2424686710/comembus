"""SQLite-backed storage for shared blackboard memories."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, List

from .unit import MemoryUnit, compute_content_hash


class SQLiteMemoryStore:
    """Persist memory units and their embeddings in SQLite."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def put(self, memory: MemoryUnit, embedding: List[float]) -> MemoryUnit:
        existing = self.get_by_content_hash(memory.content_hash)
        if existing is not None:
            return existing
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
                embedding_json,
                content_hash,
                version,
                valid_from,
                expires_at,
                parent_memory_ids_json,
                superseded_by,
                provenance_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                memory.content_hash,
                memory.version,
                memory.valid_from,
                memory.expires_at,
                json.dumps(memory.parent_memory_ids, separators=(",", ":")),
                memory.superseded_by,
                json.dumps(memory.provenance, separators=(",", ":"), sort_keys=True),
            ),
        )
        self._conn.commit()
        return memory

    def get(self, memory_id: str) -> MemoryUnit | None:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
        if row is None:
            return None
        return self._memory_from_row(row)

    def get_by_content_hash(self, content_hash: str) -> MemoryUnit | None:
        if not isinstance(content_hash, str) or not content_hash:
            return None
        row = self._conn.execute(
            """
            SELECT * FROM memories WHERE content_hash = ?
            ORDER BY created_at ASC, memory_id ASC LIMIT 1
            """,
            (content_hash,),
        ).fetchone()
        return None if row is None else self._memory_from_row(row)

    def mark_superseded(self, memory_id: str, superseded_by: str) -> None:
        cursor = self._conn.execute(
            "UPDATE memories SET superseded_by = ? WHERE memory_id = ?",
            (superseded_by, memory_id),
        )
        if cursor.rowcount != 1:
            self._conn.rollback()
            raise KeyError(f"memory not found: {memory_id}")
        self._conn.commit()

    def list_by_task(self, task_id: str) -> List[MemoryUnit]:
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE task_id = ? ORDER BY created_at ASC, memory_id ASC",
            (task_id,),
        ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def list_all(
        self,
        active_only: bool = False,
        at_time: float | None = None,
    ) -> List[MemoryUnit]:
        rows = self._conn.execute(
            "SELECT * FROM memories ORDER BY created_at ASC, memory_id ASC"
        ).fetchall()
        memories = [self._memory_from_row(row) for row in rows]
        if active_only:
            current = time.time() if at_time is None else float(at_time)
            memories = [memory for memory in memories if memory.is_reusable(current)]
        return memories

    def list_all_records(
        self,
        active_only: bool = False,
        at_time: float | None = None,
    ) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM memories ORDER BY created_at ASC, memory_id ASC"
        ).fetchall()
        records = [self._record_from_row(row) for row in rows]
        if active_only:
            current = time.time() if at_time is None else float(at_time)
            records = [
                record
                for record in records
                if record["memory"].is_reusable(current)
            ]
        return records

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
                embedding_json TEXT NOT NULL,
                content_hash TEXT NOT NULL DEFAULT '',
                version INTEGER NOT NULL DEFAULT 1,
                valid_from REAL NOT NULL DEFAULT 0,
                expires_at REAL,
                parent_memory_ids_json TEXT NOT NULL DEFAULT '[]',
                superseded_by TEXT NOT NULL DEFAULT '',
                provenance_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        self._migrate_schema()
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_content_hash ON memories(content_hash)"
        )
        self._conn.commit()

    def _migrate_schema(self) -> None:
        existing = {
            str(row["name"])
            for row in self._conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        additions = {
            "content_hash": "TEXT NOT NULL DEFAULT ''",
            "version": "INTEGER NOT NULL DEFAULT 1",
            "valid_from": "REAL NOT NULL DEFAULT 0",
            "expires_at": "REAL",
            "parent_memory_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            "superseded_by": "TEXT NOT NULL DEFAULT ''",
            "provenance_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for column, declaration in additions.items():
            if column not in existing:
                self._conn.execute(
                    f"ALTER TABLE memories ADD COLUMN {column} {declaration}"
                )
        rows = self._conn.execute(
            "SELECT memory_id, content FROM memories WHERE content_hash = ''"
        ).fetchall()
        for row in rows:
            self._conn.execute(
                "UPDATE memories SET content_hash = ? WHERE memory_id = ?",
                (compute_content_hash(str(row["content"])), str(row["memory_id"])),
            )

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
                "content_hash": row["content_hash"],
                "version": row["version"],
                "valid_from": row["valid_from"] or row["created_at"],
                "expires_at": row["expires_at"],
                "parent_memory_ids": json.loads(row["parent_memory_ids_json"]),
                "superseded_by": row["superseded_by"],
                "provenance": json.loads(row["provenance_json"]),
            }
        )

    def _record_from_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "memory": self._memory_from_row(row),
            "embedding": json.loads(row["embedding_json"]),
        }
