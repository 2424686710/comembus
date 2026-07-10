"""SQLite/WAL state transactions, recovery, compaction, retry, and rebase tests."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
import unittest

from comembus.state.patch import StatePatch, VersionConflictError
from comembus.state.patch_rebase import PatchConflictError, PatchRebaser
from comembus.state.sqlite_manager import SQLiteStateManager
from comembus.state.task_state import TaskState


def _state(task_id: str = "sqlite-task") -> TaskState:
    return TaskState(
        task_id=task_id,
        version=1,
        goal="recover state",
        phase="started",
        completed_steps=[],
        pending_steps=["log", "config"],
        facts={"base": "value"},
        errors=[],
        artifacts={},
    )


class SQLiteStateRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="comembus-sqlite-state-")
        self.path = os.path.join(self.directory.name, "state.sqlite")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_wal_patch_log_and_process_restart_recovery(self) -> None:
        manager = SQLiteStateManager(self.path)
        original = manager.create_state(_state())
        updated = manager.apply_patch(
            StatePatch(
                task_id=original.task_id,
                expected_version=original.version,
                set_fields={"phase": "review"},
                merge_dict_fields={"facts": {"signal": "timeout"}},
            )
        )
        self.assertEqual(manager.get_journal_mode(), "wal")
        self.assertEqual(manager.get_patch_count(original.task_id), 1)
        manager.close()

        restarted = SQLiteStateManager(self.path)
        try:
            recovered = restarted.recover(original.task_id)
            self.assertEqual(recovered.to_dict(), updated.to_dict())
        finally:
            restarted.close()

    def test_compact_keeps_snapshot_and_removes_old_patch_rows(self) -> None:
        manager = SQLiteStateManager(self.path)
        try:
            current = manager.create_state(_state())
            for index in range(3):
                current = manager.apply_patch(
                    StatePatch(
                        task_id=current.task_id,
                        expected_version=current.version,
                        merge_dict_fields={"facts": {f"fact_{index}": str(index)}},
                    )
                )
            self.assertEqual(manager.get_patch_count(current.task_id), 3)
            compacted = manager.compact(current.task_id)
            self.assertEqual(compacted.to_dict(), current.to_dict())
            self.assertEqual(manager.get_patch_count(current.task_id), 0)
            self.assertEqual(manager.recover(current.task_id).to_dict(), current.to_dict())
        finally:
            manager.close()

    def test_rebase_allows_append_and_different_fact_key(self) -> None:
        manager = SQLiteStateManager(self.path)
        try:
            base = manager.create_state(_state())
            first = StatePatch(
                task_id=base.task_id,
                expected_version=base.version,
                set_fields={"phase": "logs_done"},
                merge_dict_fields={"facts": {"log_signal": "timeout"}},
            )
            stale = StatePatch(
                task_id=base.task_id,
                expected_version=base.version,
                append_fields={"completed_steps": ["config"]},
                merge_dict_fields={"facts": {"config_issue": "wrong port"}},
            )
            latest = manager.apply_patch(first)
            with self.assertRaises(VersionConflictError):
                manager.apply_patch(stale)
            rebased = PatchRebaser().rebase(stale, base, latest)
            final = manager.apply_patch(rebased)
            self.assertEqual(final.facts["log_signal"], "timeout")
            self.assertEqual(final.facts["config_issue"], "wrong port")
            self.assertIn("config", final.completed_steps)
        finally:
            manager.close()

    def test_rebase_rejects_same_scalar_and_fact_key_conflicts(self) -> None:
        rebaser = PatchRebaser()
        base = _state()
        scalar_first = StatePatch(
            base.task_id, base.version, set_fields={"phase": "logs_done"}
        )
        from comembus.state.patch import apply_patch

        latest = apply_patch(base, scalar_first)
        scalar_stale = StatePatch(
            base.task_id, base.version, set_fields={"phase": "config_done"}
        )
        with self.assertRaises(PatchConflictError):
            rebaser.rebase(scalar_stale, base, latest)

        fact_first = StatePatch(
            base.task_id,
            base.version,
            merge_dict_fields={"facts": {"base": "new"}},
        )
        fact_latest = apply_patch(base, fact_first)
        fact_stale = StatePatch(
            base.task_id,
            base.version,
            merge_dict_fields={"facts": {"base": "other"}},
        )
        with self.assertRaises(PatchConflictError):
            rebaser.rebase(fact_stale, base, fact_latest)

    def test_locked_database_retries_then_commits(self) -> None:
        manager = SQLiteStateManager(
            self.path, max_retries=20, retry_delay_seconds=0.01
        )
        base = manager.create_state(_state())
        blocker = sqlite3.connect(
            self.path, timeout=0.0, isolation_level=None, check_same_thread=False
        )
        blocker.execute("BEGIN IMMEDIATE")

        def release() -> None:
            time.sleep(0.05)
            blocker.execute("ROLLBACK")

        thread = threading.Thread(target=release)
        thread.start()
        try:
            updated = manager.apply_patch(
                StatePatch(
                    base.task_id,
                    base.version,
                    merge_dict_fields={"facts": {"retried": "true"}},
                )
            )
            self.assertGreater(manager.last_retry_count, 0)
            self.assertEqual(updated.facts["retried"], "true")
        finally:
            thread.join()
            if blocker.in_transaction:
                blocker.execute("ROLLBACK")
            blocker.close()
            manager.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
