"""Tests for CoMemBus task state patching."""

from __future__ import annotations

import json
import unittest

from comembus.state.manager import InMemoryStateManager
from comembus.state.patch import StatePatch, VersionConflictError, apply_patch
from comembus.state.task_state import TaskState


def build_state(fact_count: int = 10) -> TaskState:
    facts = {f"fact_{index}": f"value_{index}" for index in range(fact_count)}
    return TaskState(
        task_id="task-001",
        version=1,
        goal="Diagnose checkout failures",
        phase="collecting",
        completed_steps=["open_incident"],
        pending_steps=["analyze_logs", "review_config"],
        facts=facts,
        errors=[],
        artifacts={
            "log_bundle": {
                "kind": "object_ref",
                "size_bytes": 8 * 1024 * 1024,
            }
        },
    )


class TaskStatePatchTests(unittest.TestCase):
    def test_task_state_round_trip_serialization(self) -> None:
        state = build_state(fact_count=3)
        state_dict = state.to_dict()
        restored = TaskState.from_dict(state_dict)

        self.assertEqual(restored.to_dict(), state_dict)
        self.assertEqual(
            json.loads(restored.to_json_bytes().decode("utf-8")),
            state_dict,
        )

    def test_apply_patch_updates_state_fields(self) -> None:
        state = build_state(fact_count=5)
        patch = StatePatch(
            task_id=state.task_id,
            expected_version=state.version,
            set_fields={"phase": "reviewing"},
            append_fields={"completed_steps": ["analyze_logs"]},
            merge_dict_fields={"facts": {"latest_signal": "database_pool_warn"}},
        )

        updated = apply_patch(state, patch)

        self.assertEqual(updated.phase, "reviewing")
        self.assertEqual(updated.completed_steps[-1], "analyze_logs")
        self.assertEqual(updated.facts["latest_signal"], "database_pool_warn")
        self.assertEqual(updated.pending_steps, state.pending_steps)

    def test_version_conflict_raises(self) -> None:
        state = build_state(fact_count=2)
        patch = StatePatch(
            task_id=state.task_id,
            expected_version=state.version + 1,
            set_fields={"phase": "reviewing"},
        )

        with self.assertRaises(VersionConflictError):
            apply_patch(state, patch)

    def test_patch_increments_version_and_manager_snapshot(self) -> None:
        manager = InMemoryStateManager()
        original = manager.create_state(build_state(fact_count=8))
        patch = StatePatch(
            task_id=original.task_id,
            expected_version=original.version,
            set_fields={"phase": "reviewing"},
            append_fields={"completed_steps": ["analyze_logs"]},
            merge_dict_fields={"facts": {"latest_signal": "database_pool_warn"}},
        )

        updated = manager.apply_patch(patch)
        snapshot = manager.snapshot(original.task_id)

        self.assertEqual(updated.version, original.version + 1)
        self.assertEqual(snapshot.version, original.version + 1)
        self.assertEqual(snapshot.phase, "reviewing")

    def test_patch_bytes_are_smaller_than_full_state_bytes(self) -> None:
        state = build_state(fact_count=1000)
        patch = StatePatch(
            task_id=state.task_id,
            expected_version=state.version,
            set_fields={"phase": "reviewing"},
            append_fields={"completed_steps": ["analyze_logs"]},
            merge_dict_fields={"facts": {"latest_signal": "database_pool_warn"}},
        )

        updated = apply_patch(state, patch)
        self.assertLess(len(patch.to_json_bytes()), len(updated.to_json_bytes()))


if __name__ == "__main__":
    unittest.main(verbosity=2)

