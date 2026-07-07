"""In-memory versioned task state manager."""

from __future__ import annotations

import threading
from typing import Dict

from .patch import StatePatch, apply_patch
from .task_state import TaskState


class StateAlreadyExistsError(Exception):
    """Raised when creating a state with a duplicate task_id."""


class StateNotFoundError(Exception):
    """Raised when a task state does not exist."""


class InMemoryStateManager:
    """Manage versioned task state snapshots in memory."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: Dict[str, TaskState] = {}

    def create_state(self, state: TaskState) -> TaskState:
        with self._lock:
            if state.task_id in self._states:
                raise StateAlreadyExistsError(f"state already exists: {state.task_id}")
            self._states[state.task_id] = _clone_state(state)
            return _clone_state(self._states[state.task_id])

    def get_state(self, task_id: str) -> TaskState:
        with self._lock:
            state = self._states.get(task_id)
            if state is None:
                raise StateNotFoundError(f"state not found: {task_id}")
            return _clone_state(state)

    def apply_patch(self, patch: StatePatch) -> TaskState:
        with self._lock:
            state = self._states.get(patch.task_id)
            if state is None:
                raise StateNotFoundError(f"state not found: {patch.task_id}")
            updated = apply_patch(state, patch)
            self._states[patch.task_id] = updated
            return _clone_state(updated)

    def snapshot(self, task_id: str) -> TaskState:
        return self.get_state(task_id)


def _clone_state(state: TaskState) -> TaskState:
    return TaskState.from_dict(state.to_dict())

