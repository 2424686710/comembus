"""Conflict-aware rebasing for stale CoMemBus state patches."""

from __future__ import annotations

from typing import List, Set

from .patch import StatePatch, apply_patch
from .task_state import TaskState


class PatchRebaseError(Exception):
    """Base patch rebase error."""


class PatchConflictError(PatchRebaseError):
    """Raised when a stale patch overlaps changes already in the latest state."""

    def __init__(self, conflict_paths: List[str]) -> None:
        self.conflict_paths = tuple(sorted(conflict_paths))
        super().__init__(f"patch conflicts at: {', '.join(self.conflict_paths)}")


class PatchRebaser:
    """Rebase non-overlapping StatePatch operations onto a newer version."""

    def rebase(
        self,
        stale_patch: StatePatch,
        base_state: TaskState,
        latest_state: TaskState,
    ) -> StatePatch:
        self._validate_inputs(stale_patch, base_state, latest_state)
        conflicts = self.find_conflicts(stale_patch, base_state, latest_state)
        if conflicts:
            raise PatchConflictError(conflicts)
        payload = stale_patch.to_dict()
        payload["expected_version"] = latest_state.version
        return StatePatch.from_dict(payload)

    def can_rebase(
        self,
        stale_patch: StatePatch,
        base_state: TaskState,
        latest_state: TaskState,
    ) -> bool:
        self._validate_inputs(stale_patch, base_state, latest_state)
        return not self.find_conflicts(stale_patch, base_state, latest_state)

    def rebase_and_apply(
        self,
        stale_patch: StatePatch,
        base_state: TaskState,
        latest_state: TaskState,
    ) -> TaskState:
        return apply_patch(
            latest_state, self.rebase(stale_patch, base_state, latest_state)
        )

    def find_conflicts(
        self,
        stale_patch: StatePatch,
        base_state: TaskState,
        latest_state: TaskState,
    ) -> List[str]:
        conflicts: Set[str] = set()
        for field_name in stale_patch.set_fields:
            if getattr(base_state, field_name) != getattr(latest_state, field_name):
                conflicts.add(field_name)

        # Appending to an existing list composes with other appends by definition.
        for field_name, values in stale_patch.merge_dict_fields.items():
            base_mapping = getattr(base_state, field_name)
            latest_mapping = getattr(latest_state, field_name)
            for key in values:
                base_has_key = key in base_mapping
                latest_has_key = key in latest_mapping
                if base_has_key != latest_has_key:
                    conflicts.add(f"{field_name}.{key}")
                elif base_has_key and base_mapping[key] != latest_mapping[key]:
                    conflicts.add(f"{field_name}.{key}")
        return sorted(conflicts)

    @staticmethod
    def _validate_inputs(
        stale_patch: StatePatch,
        base_state: TaskState,
        latest_state: TaskState,
    ) -> None:
        if not isinstance(stale_patch, StatePatch):
            raise TypeError("stale_patch must be a StatePatch")
        if not isinstance(base_state, TaskState) or not isinstance(
            latest_state, TaskState
        ):
            raise TypeError("base_state and latest_state must be TaskState objects")
        task_ids = {stale_patch.task_id, base_state.task_id, latest_state.task_id}
        if len(task_ids) != 1:
            raise PatchRebaseError("patch and states must have the same task_id")
        if stale_patch.expected_version != base_state.version:
            raise PatchRebaseError(
                "stale patch expected_version must match base_state version"
            )
        if latest_state.version < base_state.version:
            raise PatchRebaseError("latest_state cannot be older than base_state")
