"""SQLite/WAL state snapshots, patch audit log, and crash recovery."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Callable, List, Optional, TypeVar

from .manager import StateAlreadyExistsError, StateNotFoundError
from .patch import StatePatch, apply_patch
from .task_state import TaskState


class SQLiteStateError(Exception):
    """Base persistent state manager error."""


class SQLiteBusyError(SQLiteStateError):
    """Raised after exhausting retries for a locked/busy database."""


T = TypeVar("T")


class SQLiteStateManager:
    """Persist latest TaskState and every successfully applied patch atomically."""

    def __init__(
        self,
        db_path: str,
        max_retries: int = 8,
        retry_delay_seconds: float = 0.02,
    ) -> None:
        if not isinstance(db_path, str) or not db_path:
            raise ValueError("db_path must be a non-empty string")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must be non-negative")
        self.db_path = db_path
        self.max_retries = max_retries
        self.retry_delay_seconds = float(retry_delay_seconds)
        self._lock = threading.RLock()
        self._closed = False
        self.last_retry_count = 0
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            db_path,
            timeout=0.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        try:
            self._configure()
            self._create_schema()
        except Exception:
            self._connection.close()
            self._closed = True
            raise

    def create_state(self, state: TaskState) -> TaskState:
        if not isinstance(state, TaskState):
            raise TypeError("state must be a TaskState")

        def operation(connection: sqlite3.Connection) -> TaskState:
            try:
                connection.execute(
                    """
                    INSERT INTO states(
                        task_id, version, snapshot_json, compacted_version, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        state.task_id,
                        state.version,
                        _state_json(state),
                        state.version,
                        time.time(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise StateAlreadyExistsError(
                    f"state already exists: {state.task_id}"
                ) from exc
            return _clone_state(state)

        return self._write_transaction(operation)

    def get_state(self, task_id: str) -> TaskState:
        _validate_task_id(task_id)
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                "SELECT snapshot_json FROM states WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise StateNotFoundError(f"state not found: {task_id}")
        return _state_from_json(str(row["snapshot_json"]))

    def snapshot(self, task_id: str) -> TaskState:
        return self.get_state(task_id)

    def apply_patch(self, patch: StatePatch) -> TaskState:
        if not isinstance(patch, StatePatch):
            raise TypeError("patch must be a StatePatch")

        def operation(connection: sqlite3.Connection) -> TaskState:
            row = connection.execute(
                "SELECT snapshot_json FROM states WHERE task_id = ?", (patch.task_id,)
            ).fetchone()
            if row is None:
                raise StateNotFoundError(f"state not found: {patch.task_id}")
            current = _state_from_json(str(row["snapshot_json"]))
            updated = apply_patch(current, patch)
            now = time.time()
            connection.execute(
                """
                INSERT INTO patches(
                    task_id, expected_version, resulting_version, patch_json, applied_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    patch.task_id,
                    patch.expected_version,
                    updated.version,
                    _patch_json(patch),
                    now,
                ),
            )
            cursor = connection.execute(
                """
                UPDATE states
                   SET version = ?, snapshot_json = ?, updated_at = ?
                 WHERE task_id = ? AND version = ?
                """,
                (
                    updated.version,
                    _state_json(updated),
                    now,
                    patch.task_id,
                    patch.expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise SQLiteStateError(
                    "state version changed during transactional patch application"
                )
            return _clone_state(updated)

        return self._write_transaction(operation)

    def recover(self, task_id: str) -> TaskState:
        """Recover and validate the durable latest snapshot after process restart."""

        state = self.get_state(task_id)
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                """
                SELECT MAX(resulting_version) AS max_version
                  FROM patches WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
        max_patch_version = row["max_version"] if row is not None else None
        if max_patch_version is not None and int(max_patch_version) > state.version:
            raise SQLiteStateError(
                "patch log is ahead of the durable snapshot; recovery refused"
            )
        return state

    def compact(self, task_id: str) -> TaskState:
        _validate_task_id(task_id)

        def operation(connection: sqlite3.Connection) -> TaskState:
            row = connection.execute(
                "SELECT snapshot_json, version FROM states WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise StateNotFoundError(f"state not found: {task_id}")
            state = _state_from_json(str(row["snapshot_json"]))
            connection.execute(
                """
                UPDATE states
                   SET snapshot_json = ?, compacted_version = ?, updated_at = ?
                 WHERE task_id = ?
                """,
                (_state_json(state), state.version, time.time(), task_id),
            )
            connection.execute(
                "DELETE FROM patches WHERE task_id = ? AND resulting_version <= ?",
                (task_id, state.version),
            )
            return _clone_state(state)

        return self._write_transaction(operation)

    def list_patches(self, task_id: str) -> List[StatePatch]:
        _validate_task_id(task_id)
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                """
                SELECT patch_json FROM patches
                 WHERE task_id = ? ORDER BY patch_id
                """,
                (task_id,),
            ).fetchall()
        return [StatePatch.from_dict(json.loads(str(row["patch_json"]))) for row in rows]

    def get_patch_count(self, task_id: str) -> int:
        _validate_task_id(task_id)
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM patches WHERE task_id = ?", (task_id,)
            ).fetchone()
        return int(row["count"])

    def get_journal_mode(self) -> str:
        with self._lock:
            self._ensure_open()
            row = self._connection.execute("PRAGMA journal_mode").fetchone()
        return str(row[0]).lower()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def __enter__(self) -> "SQLiteStateManager":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _configure(self) -> None:
        with self._lock:
            mode_row = self._connection.execute("PRAGMA journal_mode=WAL").fetchone()
            mode = str(mode_row[0]).lower()
            if self.db_path != ":memory:" and mode != "wal":
                raise SQLiteStateError(f"failed to enable WAL journal mode: {mode}")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA busy_timeout=0")

    def _create_schema(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS states (
                    task_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    compacted_version INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS patches (
                    patch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    expected_version INTEGER NOT NULL,
                    resulting_version INTEGER NOT NULL,
                    patch_json TEXT NOT NULL,
                    applied_at REAL NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES states(task_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_patches_task_version
                    ON patches(task_id, resulting_version);
                """
            )

    def _write_transaction(
        self, operation: Callable[[sqlite3.Connection], T]
    ) -> T:
        last_error: Optional[sqlite3.OperationalError] = None
        for attempt in range(self.max_retries + 1):
            with self._lock:
                self._ensure_open()
                try:
                    self._connection.execute("BEGIN IMMEDIATE")
                    result = operation(self._connection)
                    self._connection.execute("COMMIT")
                    self.last_retry_count = attempt
                    return result
                except sqlite3.OperationalError as exc:
                    self._rollback_if_needed()
                    if not _is_locked_error(exc):
                        raise
                    last_error = exc
                except Exception:
                    self._rollback_if_needed()
                    raise
            if attempt < self.max_retries:
                time.sleep(self.retry_delay_seconds * (attempt + 1))
        self.last_retry_count = self.max_retries
        raise SQLiteBusyError(
            f"database remained locked after {self.max_retries + 1} attempts"
        ) from last_error

    def _rollback_if_needed(self) -> None:
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")

    def _ensure_open(self) -> None:
        if self._closed:
            raise SQLiteStateError("SQLiteStateManager is closed")


def _is_locked_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def _validate_task_id(task_id: str) -> None:
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task_id must be a non-empty string")


def _state_json(state: TaskState) -> str:
    return state.to_json_bytes().decode("utf-8")


def _patch_json(patch: StatePatch) -> str:
    return patch.to_json_bytes().decode("utf-8")


def _state_from_json(payload: str) -> TaskState:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SQLiteStateError("invalid persisted state JSON") from exc
    return TaskState.from_dict(value)


def _clone_state(state: TaskState) -> TaskState:
    return TaskState.from_dict(state.to_dict())
